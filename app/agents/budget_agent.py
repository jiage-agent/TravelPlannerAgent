"""预算审核 Agent：判断行程总花费是否超出用户预算"""
from langchain_core.messages import SystemMessage, HumanMessage   # 消息类型
from app.graph.state import AgentState                            # 全局状态
from app.models.llm_config import get_llm                         # LLM 工厂函数
from app.models.schemas import BudgetCheckOutput                  # 结构化输出模型
from app.utils.logger import setup_logger                         # 日志

logger = setup_logger("travel_planner.budget")


def budget_agent(state: AgentState) -> dict:
    """预算审核节点：写入 budget_approved / budget_feedback。"""
    total_cost = state.get("total_cost", 0.0)
    budget = state.get("budget", 0.0)
    current_plan = state.get("current_plan", [])

    logger.info(f"▶ [Budget] 审核 | 花费={total_cost} 预算={budget}")

    # 硬规则优先：明显超支时直接驳回，不消耗 LLM Token
    if budget > 0 and total_cost > budget:
        logger.warning("⚠️ 严重超支，直接驳回")
        return {
            "budget_approved": False,
            "budget_feedback": (
                f"总花费 {total_cost} 元超出预算 {budget} 元，"
                f"超出 {total_cost - budget} 元。建议减少住宿或缩短一天行程。"
            ),
        }

    # 未超支时再让 LLM 评估性价比（是否太寒酸、路线安排是否浪费钱）
    llm = get_llm(temperature=0.1)
    structured_llm = llm.with_structured_output(BudgetCheckOutput, method="json_mode")

    # 从 Pydantic 对象里取字段（不是 dict，不能 .get()）
    plan_summary = "\n".join([
        f"第 {day.day} 天: {'、'.join(day.activities)} | 花费 {day.estimated_cost} 元"
        for day in current_plan
    ]) if current_plan else "暂无行程"

    system_prompt = f"""你是一个严格的旅行预算审核员。
用户预算 {budget} 元，当前行程总花费 {total_cost} 元。

【行程明细】
{plan_summary}

判断预算安排是否合理。返回 JSON：
- budget_approved: 布尔值
- feedback: 合理则返回 '预算合理'；否则给出具体修改建议。
"""

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content="请审核预算。")
    ]

    result = structured_llm.invoke(messages)

    # FIXME: DeepSeek json_mode 返回 (parsed, raw) 元组
    if isinstance(result, tuple):
        result = result[0]

    logger.info(f"✅ [Budget] 通过={result.budget_approved} | {result.feedback}")

    return {
        "budget_approved": result.budget_approved,
        "budget_feedback": result.feedback,
    }