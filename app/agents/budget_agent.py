"""
预算审核 Agent
职责：检查方案是否超预算，超出给反馈
- 用 LLM 做智能审核
- LLM 解析失败时用简单规则兜底
"""
from langchain_core.messages import SystemMessage
from app.graph.state import AgentState
from app.models.llm_config import model
from app.utils.json_parser import parse_llm_json
from app.utils.logger import setup_logger
from app.utils.error_handler import safe_node

logger = setup_logger("travel_planner.budget")


SYSTEM_PROMPT = """你是一位严格的预算审核员。请审核以下行程方案是否符合预算要求。

## 预算要求
- 用户预算: {budget} 元
- 方案总花费: {total_cost} 元

## 行程详情
{plan_detail}

## 审核标准
1. 如果总花费 <= 预算，返回 approved=true, feedback 写"预算符合要求"
2. 如果总花费 > 预算，返回 approved=false, feedback 写具体的超支金额和削减建议

## 输出格式
只返回一个 JSON 对象，不要加任何其他内容：
{{
  "approved": true 或 false,
  "feedback": "反馈内容"
}}
"""


def _build_plan_detail(state: AgentState) -> str:
    """把 current_plan 拼成可读文本"""
    lines = []
    for day_plan in state["current_plan"]:
        day = day_plan["day"] if isinstance(day_plan, dict) else day_plan.day
        activities = day_plan["activities"] if isinstance(day_plan, dict) else day_plan.activities
        cost = day_plan["estimated_cost"] if isinstance(day_plan, dict) else day_plan.estimated_cost
        lines.append(f"第{day}天: {', '.join(activities)}, 花费 {cost} 元")
    return "\n".join(lines)


def _rule_based_review(state: AgentState) -> tuple[bool, str]:
    """纯规则兜底：直接比大小"""
    approved = state["total_cost"] <= state["budget"]
    if approved:
        feedback = "预算符合要求"
    else:
        over = state["total_cost"] - state["budget"]
        feedback = f"超出预算 {over} 元，建议削减开支"
    return approved, feedback



@safe_node("budget")
def budget_agent(state: AgentState) -> AgentState:
    """预算审核节点"""
    total_cost = state["total_cost"]
    budget = state["budget"]
    logger.info(f"▶ 开始 | total_cost={total_cost}, budget={budget}")

    # 1) 构造 prompt 并调用 LLM
    plan_detail = _build_plan_detail(state)
    prompt = SYSTEM_PROMPT.format(
        budget=budget,
        total_cost=total_cost,
        plan_detail=plan_detail,
    )
    response = model.invoke([SystemMessage(content=prompt)])
    logger.debug(f"LLM 原始输出: {response.content!r}")

    # 2) 解析 LLM 结果，失败则规则兜底
    try:
        result = parse_llm_json(response.content)
        approved = result.get("approved", False)
        feedback = result.get("feedback", "")
        logger.info(f"✔ LLM 审核完成 | approved={approved}")
    except Exception as e:
        logger.exception(f"✘ LLM 解析失败，使用规则兜底: {e}")
        approved, feedback = _rule_based_review(state)

    # 3) 回填 state
    state["budget_approved"] = approved
    state["budget_feedback"] = feedback

    logger.info(f"✔ 完成 | approved={approved}, feedback={feedback[:50]!r}")
    return state

