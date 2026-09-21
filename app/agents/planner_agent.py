"""
规划 Agent
职责：根据需求分析结果，生成初步行程方案
支持多轮迭代：读取 budget_feedback / preference_feedback 做调整
支持工具调用：搜索真实景点、查门票价格
"""
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from app.graph.state import AgentState, TravelPlan
from app.models.llm_config import model
from app.utils.json_parser import parse_llm_json
from app.utils.logger import setup_logger
from app.utils.error_handler import safe_node
from app.tools.search_tools import SEARCH_TOOLS

logger = setup_logger("travel_planner.planner")

# 工具名 → 工具函数 的映射（执行 tool_calls 时用）
_TOOL_MAP = {t.name: t for t in SEARCH_TOOLS}

# 绑定工具的 LLM（模块级只做一次）
_PLANNER_LLM = model.bind_tools(SEARCH_TOOLS)

# 工具调用最大轮数（防止 LLM 反复调工具）
_MAX_TOOL_ROUNDS = 3

SYSTEM_PROMPT = """你是一位资深旅行规划师。请根据以下需求，生成一份详细的行程方案。

## 用户需求
- 目的地: {destination}
- 天数: {days}天
- 预算: {budget}元
- 偏好: {preferences}

## 审核反馈(如果有，请据此调整方案)
- 预算反馈: {budget_feedback}
- 偏好反馈: {preference_feedback}

## 要求
1. 每天安排2-3个活动，活动要符合用户偏好
2. 每个活动给出预估花费
3. 总花费尽量不超过预算
4. 输出必须是一个合法的 JSON 对象
5. 如果信息不确定（如景点是否收费、有什么特色），可以调用工具查询

## 输出格式
返回以下 JSON 结构(不要加任何解释文字，不要用 markdown 代码块)：
{{
  "plan": [
    {{
      "day": 1,
      "activities": ["活动1", "活动2"],
      "estimated_cost": 850,
      "notes": "备注"
    }}
  ],
  "total_cost": 1950,
  "summary": "方案概述"
}}
"""


def _build_prompt(state: AgentState) -> str:
    """根据 state 构造规划 prompt"""
    return SYSTEM_PROMPT.format(
        destination=state["destination"],
        days=state["days"],
        budget=state["budget"],
        preferences=", ".join(state["preferences"]),
        budget_feedback=state.get("budget_feedback") or "无",
        preference_feedback=state.get("preference_feedback") or "无",
    )


def _to_travel_plans(plan_data: list[dict]) -> list[TravelPlan]:
    """把 LLM 返回的 plan 列表转成 TravelPlan 对象列表"""
    plans = []
    for item in plan_data:
        plans.append(TravelPlan(
            day=item.get("day", 0),
            activities=item.get("activities", []),
            estimated_cost=item.get("estimated_cost", 0),
            notes=item.get("notes", ""),
        ))
    return plans


def _fallback_plan(state: AgentState, error: Exception) -> list[TravelPlan]:
    """解析失败时的兜底：每天一个占位活动，均摊预算"""
    daily_budget = state["budget"] / state["days"] if state["days"] else 0
    return [
        TravelPlan(
            day=i + 1,
            activities=[f"第{i + 1}天: 自由活动"],
            estimated_cost=daily_budget,
            notes=f"解析失败: {str(error)[:50]}",
        )
        for i in range(state["days"])
    ]


def _invoke_with_tools(prompt: str, state: AgentState):
    """带工具调用的 LLM 调用循环

    流程：
    1. 首次调用 LLM → 它可能直接输出 JSON，也可能请求调用工具
    2. 如果请求调用工具 → 执行工具 → 把结果喂回 LLM → 继续
    3. 最多循环 _MAX_TOOL_ROUNDS 轮，防止死循环
    4. 最终返回 LLM 的正常输出（含 JSON）
    """
    messages = [SystemMessage(content=prompt)]
    response = _PLANNER_LLM.invoke(messages)

    tool_round = 0
    while response.tool_calls and tool_round < _MAX_TOOL_ROUNDS:
        tool_round += 1
        logger.info(f"🔧 第 {tool_round} 轮工具调用 | {len(response.tool_calls)} 个")

        tool_messages = []
        for tc in response.tool_calls:
            tool_name = tc["name"]
            tool_args = tc["args"]
            logger.info(f"  → 调用 {tool_name} | args={tool_args}")

            tool_fn = _TOOL_MAP.get(tool_name)
            if tool_fn is None:
                result = f"未知工具：{tool_name}"
                logger.warning(f"  ✘ 未知工具: {tool_name}")
            else:
                try:
                    result = tool_fn.invoke(tool_args)
                except Exception as e:
                    result = f"工具执行失败：{e}"
                    logger.exception(f"  ✘ 工具 {tool_name} 执行失败")

            # 记录到 state（供可观测/下游复用）
            state.setdefault("tool_results", []).append({
                "tool": tool_name,
                "args": tool_args,
                "result": str(result)[:300],
            })

            tool_messages.append(ToolMessage(content=str(result), tool_call_id=tc["id"]))

        # 把工具结果喂回 LLM
        messages.extend([response] + tool_messages)
        response = _PLANNER_LLM.invoke(messages)

    if tool_round >= _MAX_TOOL_ROUNDS and response.tool_calls:
        logger.warning(f"⚠ 工具调用达到上限 {_MAX_TOOL_ROUNDS} 轮，强制收尾")

    return response


@safe_node("planner")
def planner_agent(state: AgentState) -> AgentState:
    """规划节点：生成或调整行程方案"""
    logger.info(
        f"▶ 开始 | destination={state['destination']}, days={state['days']}, "
        f"budget={state['budget']}, iteration={state.get('iteration', 0)}"
    )

    # 1) 调用 LLM（可能带工具调用）
    prompt = _build_prompt(state)
    response = _invoke_with_tools(prompt, state)
    logger.debug(f"LLM 最终输出: {response.content[:200]!r}...")

    # 2) 解析 JSON
    try:
        result = parse_llm_json(response.content)
        current_plan = _to_travel_plans(result.get("plan", []))
        total_cost = result.get("total_cost", 0)
        logger.info(f"✔ 生成行程 | days={len(current_plan)}, total_cost={total_cost}")
    except Exception as e:
        logger.exception(f"✘ 解析失败，使用兜底: {e}")
        current_plan = _fallback_plan(state, e)
        total_cost = state["budget"]
        logger.warning(f"⚠ 兜底方案 | days={len(current_plan)}, total_cost={total_cost}")

    # 3) 回填 state
    state["current_plan"] = current_plan
    state["total_cost"] = total_cost
    state["iteration"] = state.get("iteration", 0) + 1

    logger.info(f"✔ 完成 | iteration={state['iteration']}")
    return state

