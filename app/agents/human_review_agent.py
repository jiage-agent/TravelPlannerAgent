"""人工审核 Agent：通过 interrupt() 暂停工作流，等待人工决策"""
from langgraph.types import interrupt              # 中断原语
from app.graph.state import AgentState             # 全局状态
from app.utils.logger import setup_logger          # 日志

logger = setup_logger("travel_planner.human_review")


def human_review_agent(state: AgentState) -> dict:
    """人工审核节点：通过 interrupt() 挂起，由 Command(resume=...) 恢复。"""
    current_plan = state.get("current_plan", [])
    logger.info("▶ [HumanReview] 挂起，等待人工审核")

    # interrupt 会把控制权交还给调用方，并保存当前状态
    # resume 时，传入的值会成为 user_decision
    user_decision = interrupt({
        "message": "请审核该行程方案是否通过？",
        "plan": str(current_plan),
    })

    # 约定：resume 值为 "通过" 则通过，其他字符串视为驳回意见
    if user_decision == "通过":
        logger.info("✅ [HumanReview] 人工通过")
        return {
            "final_plan": current_plan,
            "human_approved": True,
        }

    logger.warning(f"⚠️ [HumanReview] 人工驳回: {user_decision}")
    iteration = state.get("iteration", 0) + 1
    return {
        "iteration": iteration,
        "human_approved": False,
        "human_feedback": user_decision,
    }


def should_continue_after_human(state: AgentState) -> str:
    """人工审核后的条件路由：通过则结束，驳回则回到 supervisor。"""
    if state.get("human_approved", False):
        return "end"
    return "supervisor"