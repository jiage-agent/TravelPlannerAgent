"""偏好审核 Agent：判断行程是否匹配用户的旅行偏好"""
from langchain_core.messages import SystemMessage, HumanMessage   # 消息类型
from app.graph.state import AgentState                            # 全局状态
from app.models.llm_config import get_llm                         # LLM 工厂函数
from app.models.schemas import PreferenceCheckOutput              # 结构化输出模型
from app.utils.logger import setup_logger                         # 日志

logger = setup_logger("travel_planner.preference")


def preference_agent(state: AgentState) -> dict:
    """偏好审核节点：写入 preference_approved / preference_feedback。"""
    preferences = state.get("preferences", [])
    current_plan = state.get("current_plan", [])

    logger.info(f"▶ [Preference] 审核 | 偏好={preferences}")

    # 用户没提偏好 → 默认通过，不消耗 Token
    if not preferences:
        logger.info("✅ 无偏好，默认通过")
        return {
            "preference_approved": True,
            "preference_feedback": "用户未指定偏好，默认通过。",
        }

    llm = get_llm(temperature=0.1)
    structured_llm = llm.with_structured_output(PreferenceCheckOutput, method="json_mode")

    plan_summary = "\n".join([
        f"第 {day.day} 天: {'、'.join(day.activities)} | 花费 {day.estimated_cost} 元"
        for day in current_plan
    ]) if current_plan else "暂无行程"

    system_prompt = f"""你是一个旅行偏好审核员。
用户偏好：{preferences}

【行程明细】
{plan_summary}

评估该行程是否满足用户偏好：
- 偏好"美食" → 行程需包含当地特色餐饮
- 偏好"自然风光" → 行程需有户外自然景点

返回 JSON：
- preference_approved: 布尔值
- feedback: 符合则返回 '偏好匹配'；否则给出具体增补建议。
"""

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content="请审核偏好匹配度。")
    ]

    result = structured_llm.invoke(messages)

    # FIXME: DeepSeek json_mode 返回 (parsed, raw) 元组
    if isinstance(result, tuple):
        result = result[0]

    logger.info(f"✅ [Preference] 通过={result.preference_approved} | {result.feedback}")

    return {
        "preference_approved": result.preference_approved,
        "preference_feedback": result.feedback,
    }