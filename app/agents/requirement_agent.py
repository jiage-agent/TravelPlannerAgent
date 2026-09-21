import json
from langchain_core.messages import HumanMessage, SystemMessage
from app.graph.state import AgentState
from app.models.llm_config import model
from app.utils.logger import setup_logger
from app.utils.error_handler import safe_node

logger = setup_logger("travel_planner.requirement")


# 系统提示词：抽取结构化需求
SYSTEM_PROMPT = """你是一个需求分析专家。请从用户输入中提取以下信息，以JSON格式返回。
{
  "destination": "目的地城市",
  "days": 天数(整数),
  "budget": 预算金额(数字),
  "preferences": ["偏好1", "偏好2"]
}
如果某项信息缺失，请根据常识推断一个合理值。
只返回JSON，不要有其他内容，不要解释文字。
"""

# JSON 解析失败时的兜底
FALLBACK = {
    "destination": "杭州",
    "days": 3,
    "budget": 3000,
    "preferences": ["自然风光", "安静"],
}


def _parse_json(content: str) -> dict:
    """从 LLM 输出里提取 JSON（兼容 markdown 代码块包裹）"""
    content = content.strip()
    if content.startswith("```"):
        content = content.split("```")[1]
        content = content[4:].strip() if content.startswith("json") else content.strip()
    return json.loads(content)


@safe_node("requirement")                          # ← 装饰器直接加在这里
def requirement_agent(state: AgentState) -> AgentState:
    """需求分析节点：解析用户原始query，输出结构化旅行需求"""
    user_query = state["user_query"]
    logger.info(f"▶ 开始 | query={user_query!r}")

    response = model.invoke([
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=f"用户输入: {user_query}"),
    ])
    content = response.content.strip()
    logger.debug(f"LLM 原始输出: {content!r}")

    try:
        requirements = _parse_json(content)
        logger.info(f"✔ 解析成功 | destination={requirements.get('destination')}")
    except Exception as e:
        logger.exception(f"✘ 解析失败，使用兜底: {e}")
        requirements = FALLBACK

    state["destination"] = requirements.get("destination", "杭州")
    state["days"] = int(requirements.get("days", 3))
    state["budget"] = float(requirements.get("budget", 3000))
    state["preferences"] = requirements.get("preferences", [])
    state["iteration"] = 0
    state["max_iterations"] = 3

    logger.info(f"✔ 完成 | destination={state['destination']}, days={state['days']}")
    return state





