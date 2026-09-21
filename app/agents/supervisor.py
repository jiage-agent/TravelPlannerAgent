"""Supervisor 路由节点：决定下一步去哪个 Agent"""
from app.graph.state import AgentState
from app.utils.logger import setup_logger


logger = setup_logger("travel_planner.supervisor")


def supervisor(state: AgentState) -> str:
    """路由决策：返回下一个节点名"""
    budget_ok = state.get("budget_approved", False)
    preference_ok = state.get("preference_approved", False)
    iteration = state.get("iteration", 0)
    max_iter = state.get("max_iterations", 3)

    logger.info(
        f"▶ 判断 | iteration={iteration}/{max_iter}, "
        f"budget_ok={budget_ok}, preference_ok={preference_ok}"
    )

    # 1) 两个审核都过 → 去人工审核
    if budget_ok and preference_ok:
        logger.info("→ human_review (审核全通过，等待人工确认)")
        return "human_review"

    # 2) 超迭代上限 → 强制进人工审核（避免死循环）
    if iteration >= max_iter:
        logger.warning(f"→ human_review (达到最大迭代 {max_iter}，强制结束)")
        return "human_review"

    # 3) 未通过 → 回 planner 重规划
    logger.info("→ planner (审核未通过，重新规划)")
    return "planner"


