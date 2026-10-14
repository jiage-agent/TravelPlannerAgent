"""需求分析 Agent：从自然语言中提取目的地、天数、预算、偏好、出发日期。

v2.1 起支持**多轮增量修改**（真正的对话记忆在这里体现）：
如果用户这一轮只改了部分信息（例如"改成 5 天，预算加到 8000"），
未提及的字段会**继承上一轮**的结果，而不是被清空成默认值。

这是单轮抽取 prompt 做不到的事——指代消解（"就按刚才那个"）与增量修改
（"天数改成 5 天"）都依赖对话历史，也是面试里最容易被追问的多轮场景。
"""
from datetime import datetime

from langchain_core.messages import HumanMessage, SystemMessage

from app.graph.state import AgentState
from app.models.llm_config import get_llm
from app.models.schemas import RequirementOutput
from app.utils.context import estimate_tokens, format_history, truncate_to_tokens
from app.utils.logger import setup_logger

logger = setup_logger("travel_planner.requirement")


def _previous_block(state: AgentState) -> str:
    """把上一轮已确认的字段整理成"继承基线"。

    优先读 `previous_extraction`（由 /plan 在多轮续接时注入）；没有时退回当前 State 字段
    （兼容单轮调用与评测脚本）。注意：**继承值不能预填进 State 的 destination/days**，
    否则 Supervisor 会认为需求已提取而跳过本节点，用户的增量修改就被忽略了。
    """
    prev = state.get("previous_extraction") or {}
    merged = {
        "destination": prev.get("destination") or state.get("destination") or "",
        "days": prev.get("days") or state.get("days") or 0,
        "budget": prev.get("budget") or state.get("budget") or 0.0,
        "preferences": prev.get("preferences") or state.get("preferences") or [],
        "start_date": prev.get("start_date") or state.get("start_date") or "",
    }
    if not any(merged.values()):
        return ""
    return (
        "\n【上一轮已确认的信息（本轮未提到时请沿用）】\n"
        f"- 目的地: {merged['destination'] or '（无）'}\n"
        f"- 天数: {merged['days'] or '（无）'}\n"
        f"- 预算: {merged['budget'] or '（无）'}\n"
        f"- 偏好: {merged['preferences'] or '（无）'}\n"
        f"- 出发日期: {merged['start_date'] or '（无）'}\n"
    )


def _recent_history(state: AgentState) -> str:
    """最近若干条对话（用于指代消解）。"""
    lines = []
    for item in (state.get("messages") or [])[-6:]:
        if isinstance(item, dict):
            content = str(item.get("content", "")).replace("\n", " ")
            if content:
                lines.append(f"{item.get('role', '?')}: {content[:120]}")
    summary = state.get("history_summary") or ""
    if not lines and not summary:
        return ""
    return format_history(summary, lines)


def requirement_agent(state: AgentState) -> dict:
    """需求提取节点：写入 destination / days / budget / preferences / start_date。"""
    user_query = state.get("user_query", "")
    turn = state.get("turn", 1)
    logger.info(f"▶ [Requirement] 第 {turn} 轮，分析需求：{user_query}")

    # 需求提取要稳定，温度调低
    llm = get_llm(temperature=0.1)
    structured_llm = llm.with_structured_output(RequirementOutput, method="json_mode")

    # 动态注入当前日期：LLM 本身没有时间概念，必须显式告知
    today_str = datetime.now().strftime("%Y-%m-%d")

    previous_block = _previous_block(state)
    history_block = _recent_history(state)
    history_section = f"\n【最近对话】\n{history_block}\n" if history_block else ""

    system_prompt = f"""你是一个专业的旅行需求分析助手。
今天是 {today_str}。
{history_section}{previous_block}
请从用户**本轮**话语中提取以下字段：
- destination: 目的地城市，未提则为空字符串
- days: 旅行天数，未提则为 0
- budget: 预算金额，未提则为 0.0
- preferences: 偏好列表，未提则为空列表
- start_date: 出发日期，格式 YYYY-MM-DD。用户说"10月1号"这类相对日期时，结合今天 {today_str} 推算正确年份；未提则返回空字符串。

提取规则：
1. 用户本轮**明确提到**的字段 → 用本轮的值；
2. 用户本轮**没有提到**的字段 → 沿用上面"上一轮已确认的信息"；
3. 上面两处都没有的字段 → 返回默认值（空字符串 / 0 / 0.0 / 空列表）；
4. "改成 5 天""预算加到 8000""就按刚才那个来"这类增量或指代表达，要结合历史理解；
5. 不要把"上一轮的信息"重新当成用户这轮新说的内容。

只输出 JSON，不要任何多余解释。
"""

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_query),
    ]

    result = structured_llm.invoke(messages)

    # FIXME: DeepSeek 在 json_mode 下会返回 (parsed, raw) 元组，需取 [0]
    if isinstance(result, tuple):
        result = result[0]

    logger.info(
        f"✅ [Requirement] 提取完成: destination={result.destination}, "
        f"days={result.days}, start_date={result.start_date}"
    )

    return {
        "destination": result.destination,
        "days": result.days,
        "budget": result.budget,
        "preferences": result.preferences,
        "start_date": result.start_date,
        "messages": [{"role": "user", "content": truncate_to_tokens(user_query, 60)}],
        "tool_results": [
            {
                "tool": "需求提取",
                "ok": True,
                "detail": (
                    f"第{turn}轮 | 上下文 {estimate_tokens(system_prompt)} tokens | "
                    f"目的地={result.destination or '-'} 天数={result.days} 预算={result.budget:g}"
                ),
                "ts": 0,
            }
        ],
    }
