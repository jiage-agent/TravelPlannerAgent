"""行程规划 Agent：结合天气、路线和用户需求，生成结构化行程"""
from langchain_core.messages import SystemMessage, HumanMessage   # 消息类型
from app.graph.state import AgentState                            # 全局状态
from app.models.llm_config import get_llm                         # LLM 工厂函数
from app.models.schemas import DayPlan, ItineraryOutput           # 结构化输出模型
from app.utils.logger import setup_logger                         # 日志

logger = setup_logger("travel_planner.planner")


def planner_agent(state: AgentState) -> dict:
    """行程规划节点：写入 current_plan 和 total_cost。"""
    destination = state.get("destination", "")
    days = state.get("days", 0)
    budget = state.get("budget", 0.0)
    preferences = state.get("preferences", [])
    weather_info = state.get("weather_info", "暂无天气信息")
    route_info = state.get("route_info", "暂无路线信息")
    human_feedback = state.get("human_feedback", "")

    logger.info(f"▶ [Planner] 规划 {destination} {days}天 行程")

    # 温度稍高一点：规划需要一些创意
    llm = get_llm(temperature=0.5)
    # json_mode：兼容 DeepSeek，强制输出 JSON
    structured_llm = llm.with_structured_output(ItineraryOutput, method="json_mode")

    feedback_hint = ""
    if human_feedback:
        feedback_hint = (
            f"\n【人工审核反馈】\n"
            f"上一次方案未通过，用户的修改意见是：{human_feedback}\n"
            f"请务必针对该反馈做出明显调整，不要输出与上一版相似的方案。\n"
        )

    system_prompt = f"""你是一个专业的旅行规划师，请为以下需求规划 {days} 天的行程。

【用户需求】
- 目的地: {destination}
- 天数: {days}
- 总预算: {budget} 元
- 偏好: {preferences}
{feedback_hint}
【客观事实】
- 天气: {weather_info}
- 路线: {route_info}

【规划要求】
1. 遵守天气：雨天安排室内景点（博物馆、商场），不排户外徒步。
2. 参考路线：距离近的景点放同一天，避免来回奔波。
3. 预算约束：每日 estimated_cost 之和不超过总预算 {budget}。
4. 输出格式：JSON 对象，含 "plan" 键，值为数组，每项含：
   - day: 第几天（整数）
   - activities: 字符串数组，每个元素必须是纯文本字符串，例如 ["上午: 游览西湖", "下午: 乘船游湖"]，禁止使用对象
   - estimated_cost: 当天预估花费（数字）
   - notes: 备注（字符串）
"""

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"请生成 {destination} 的 {days} 天行程")
    ]

    result = structured_llm.invoke(messages)

    # FIXME: DeepSeek 在 json_mode 下会返回 (parsed, raw) 元组，需要取 [0]
    if isinstance(result, tuple):
        result = result[0]

    plan_list = result.plan
    total_cost = sum(day.estimated_cost for day in plan_list)

    logger.info(f"✅ [Planner] 完成 {len(plan_list)} 天，总花费 {total_cost} 元")

    return {
        "current_plan": plan_list,
        "total_cost": total_cost,
    }