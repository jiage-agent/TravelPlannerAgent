"""
偏好审核 Agent
职责：检查方案是否符合用户偏好
- 用 LLM 做智能审核
- LLM 解析失败时默认通过（保守策略：宁可放过，不要误杀）
"""
from langchain_core.messages import SystemMessage
from app.graph.state import AgentState
from app.models.llm_config import model
from app.utils.json_parser import parse_llm_json
from app.utils.logger import setup_logger
from app.utils.error_handler import safe_node

logger = setup_logger("travel_planner.preference")


SYSTEM_PROMPT = """你是一位旅行体验审核员。请审核以下行程方案是否符合用户的偏好。

## 用户偏好
{preferences}

## 行程详情
{plan_detail}

## 审核标准
1. 逐天检查活动是否符合用户偏好
2. 如果全部符合，返回 approved=true
3. 如果有不符合的，返回 approved=false，并指出具体哪一天、哪个活动不符合

## 输出格式
只返回一个 JSON 对象：
{{
  "approved": true 或 false,
  "feedback": "反馈内容，指出具体问题和调整建议"
}}
"""


def _build_plan_detail(state: AgentState) -> str:
    """把 current_plan 拼成可读文本"""
    lines = []
    for day_plan in state["current_plan"]:
        # 兼容 dict 和 Pydantic 对象
        day = day_plan["day"] if isinstance(day_plan, dict) else day_plan.day
        activities = day_plan["activities"] if isinstance(day_plan, dict) else day_plan.activities
        lines.append(f"第{day}天: {', '.join(activities)}")
    return "\n".join(lines)

@safe_node("preference")
def preference_agent(state: AgentState) -> AgentState:
    """偏好审核节点"""
    logger.info(
        f"▶ 开始 | preferences={state['preferences']}, "
        f"plan_days={len(state['current_plan'])}"
    )

    # 1) 构造 prompt 并调用 LLM
    plan_detail = _build_plan_detail(state)
    prompt = SYSTEM_PROMPT.format(
        preferences=", ".join(state["preferences"]),
        plan_detail=plan_detail,
    )
    response = model.invoke([SystemMessage(content=prompt)])
    logger.debug(f"LLM 原始输出: {response.content!r}")

    # 2) 解析 LLM 结果
    try:
        result = parse_llm_json(response.content)
        approved = result.get("approved", True)   # 默认 True（保守）
        feedback = result.get("feedback", "")
        logger.info(f"✔ LLM 审核完成 | approved={approved}")
    except Exception as e:
        logger.exception(f"✘ LLM 解析失败，默认通过: {e}")
        approved = True
        feedback = "偏好审核解析失败，默认通过"

    # 3) 回填 state
    state["preference_approved"] = approved
    state["preference_feedback"] = feedback

    logger.info(f"✔ 完成 | approved={approved}, feedback={feedback[:50]!r}")
    return state


