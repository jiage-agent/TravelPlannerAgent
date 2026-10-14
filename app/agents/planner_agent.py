"""行程规划 Agent：结合真实天气、景点、路线与联网信息，生成结构化行程。

v2.1 的两个变化：
1. **接入真实数据**：候选景点来自高德 POI、路线是真实驾车距离、联网补充来自 Tavily，
   不再让模型凭空编景点（这是"规划类 Agent 最大的幻觉来源"）。
2. **上下文预算管理**：提示词被拆成若干块（用户需求 / 天气 / 景点 / 路线 / 联网摘要 / 历史），
   用 `assemble_context` 按优先级分配 token 预算——硬信息必须完整，补充信息预算不够时先截断。
   这样多轮对话不会把上下文撑爆、也不会让 token 成本随轮次线性上升。
"""
from typing import Any, Dict, List

from langchain_core.messages import HumanMessage, SystemMessage

from app.config import settings
from app.graph.state import AgentState
from app.models.llm_config import get_llm
from app.models.schemas import ItineraryOutput
from app.utils.context import assemble_context, estimate_tokens, format_history
from app.utils.logger import setup_logger

logger = setup_logger("travel_planner.planner")


def _recent_message_lines(state: AgentState) -> List[str]:
    """取最近若干条对话消息（受 CONTEXT_KEEP_TURNS 约束），压缩成单行文本。"""
    raw = state.get("messages") or []
    keep = max(settings.CONTEXT_KEEP_TURNS, 2) * 2
    lines: List[str] = []
    for item in raw[-keep:]:
        if isinstance(item, dict):
            role = item.get("role", "?")
            content = str(item.get("content", ""))
        else:
            role, content = "message", str(item)
        content = content.replace("\n", " ").strip()
        if content:
            lines.append(f"{role}: {content[:120]}")
    return lines


def planner_agent(state: AgentState) -> dict:
    """行程规划节点：写入 current_plan 与 total_cost。"""
    destination = state.get("destination", "")
    days = state.get("days", 0)
    budget = state.get("budget", 0.0)
    preferences = state.get("preferences", []) or []
    weather_info = state.get("weather_info") or "暂无天气信息"
    attractions_info = state.get("attractions_info") or ""
    route_info = state.get("route_info") or "暂无路线信息"
    search_info = state.get("search_info") or ""
    human_feedback = state.get("human_feedback", "")

    logger.info(f"▶ [Planner] 规划 {destination} {days} 天行程")

    # ---------- 上下文预算分配 ----------
    history_block = format_history(state.get("history_summary", ""), _recent_message_lines(state))
    fitted = assemble_context(
        {
            "weather": weather_info,
            "attractions": attractions_info,
            "route": route_info,
            "search": search_info,
            "history": history_block,
        },
        budget=settings.CONTEXT_MAX_TOKENS,
    )
    used_tokens = sum(estimate_tokens(v) for v in fitted.values())
    logger.info(f"  上下文预算：{used_tokens}/{settings.CONTEXT_MAX_TOKENS} tokens")

    feedback_hint = ""
    if human_feedback:
        feedback_hint = (
            f"\n【人工审核反馈】\n"
            f"上一次方案未通过，用户的修改意见是：{human_feedback}\n"
            f"请务必针对该反馈做出明显调整，不要输出与上一版相似的方案。\n"
        )

    history_hint = f"\n【历史对话】\n{fitted['history']}\n" if fitted["history"] else ""

    system_prompt = f"""你是一个专业的旅行规划师，请为以下需求规划 {days} 天的行程。

【用户需求】
- 目的地: {destination}
- 天数: {days}
- 总预算: {budget} 元
- 偏好: {preferences}
{feedback_hint}{history_hint}
【客观事实】
- 天气: {fitted['weather']}
- 候选景点（来自地图真实检索，请优先从中选择）: {fitted['attractions'] or '未获取到'}
- 路线（真实驾车距离与时长）: {fitted['route']}
- 联网补充（实时信息）: {fitted['search'] or '无'}

【规划要求】
1. 遵守天气：雨天安排室内景点（博物馆、商场），不排户外徒步；天气超出预报范围时按季节常识保守安排。
2. 参考路线：距离近的景点放同一天，避免来回奔波；尽量使用上面"候选景点"里的真实地点。
3. 预算约束：每日 estimated_cost 之和不超过总预算 {budget}。
4. 联网补充里的预约/限流提醒（如需要提前预约）要写进当天的 notes。
5. 输出格式：JSON 对象，含 "plan" 键，值为数组，每项含：
   - day: 第几天（整数）
   - activities: 字符串数组，每个元素必须是纯文本字符串，例如 ["上午: 游览西湖", "下午: 乘船游湖"]，禁止使用对象
   - estimated_cost: 当天预估花费（数字）
   - notes: 备注（字符串）
"""

    # 温度稍高一点：规划需要一些创意
    structured_llm = get_llm(temperature=0.5).with_structured_output(ItineraryOutput, method="json_mode")

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"请生成 {destination} 的 {days} 天行程"),
    ]

    result: Any = structured_llm.invoke(messages)

    # FIXME: DeepSeek 在 json_mode 下会返回 (parsed, raw) 元组，需要取 [0]
    if isinstance(result, tuple):
        result = result[0]

    plan_list = result.plan
    total_cost = sum(day.estimated_cost for day in plan_list)

    logger.info(f"✅ [Planner] 完成 {len(plan_list)} 天，总花费 {total_cost} 元")

    summary: Dict[str, Any] = {
        "current_plan": plan_list,
        "total_cost": total_cost,
        "tool_results": [
            {
                "tool": "行程生成",
                "ok": True,
                "detail": f"{len(plan_list)} 天 / 预算占用 {total_cost:g} 元 / 上下文 {used_tokens} tokens",
                "ts": 0,
            }
        ],
        "messages": [
            {
                "role": "assistant",
                "content": f"[行程规划] 已生成 {destination} {len(plan_list)} 天行程，预估总花费 {total_cost:g} 元",
            }
        ],
    }
    return summary
