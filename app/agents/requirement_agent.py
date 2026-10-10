"""需求分析 Agent：从自然语言中提取目的地、天数、预算、偏好、日期"""
from langchain_core.messages import SystemMessage, HumanMessage   # 消息类型
from datetime import datetime                                     # 动态注入"今天"的日期
from app.graph.state import AgentState                            # 全局状态
from app.models.llm_config import get_llm                         # LLM 工厂函数
from app.models.schemas import RequirementOutput                  # 结构化输出模型
from app.utils.logger import setup_logger                         # 日志

logger = setup_logger("travel_planner.requirement")


def requirement_agent(state: AgentState) -> dict:
    """需求提取节点：写入 destination / days / budget / preferences / start_date。"""
    user_query = state.get("user_query", "")
    logger.info(f"▶ [Requirement] 分析需求: {user_query}")

    # 需求提取要稳定，温度调低
    llm = get_llm(temperature=0.1)
    structured_llm = llm.with_structured_output(RequirementOutput, method="json_mode")

    # 动态注入当前日期：LLM 本身没有时间概念，必须显式告知
    today_str = datetime.now().strftime("%Y-%m-%d")

    system_prompt = f"""你是一个专业的旅行需求分析助手。
今天是 {today_str}。

请从用户话语中提取以下字段：
- destination: 目的地城市，未提则为空字符串
- days: 旅行天数，未提则为 0
- budget: 预算金额，未提则为 0.0
- preferences: 偏好列表，未提则为空列表
- start_date: 出发日期，格式 YYYY-MM-DD。用户说"10月1号"这类相对日期时，结合今天 {today_str} 推算正确年份；未提则返回空字符串。

只输出 JSON，不要任何多余解释。
"""

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_query)
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
    }