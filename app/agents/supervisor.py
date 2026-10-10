"""Supervisor 节点：多 Agent 系统的中央调度大脑"""
from app.graph.state import AgentState              # 全局状态定义
from app.utils.logger import setup_logger           # 日志
from app.config import settings                     # 最大迭代次数等配置

logger = setup_logger("travel_planner.supervisor")


def supervisor_node(state: AgentState) -> dict:
    """评估 state，写入 next_node，由条件边决定下一步去哪个节点。"""
    iteration = state.get("iteration", 0)
    max_iterations = state.get("max_iterations", settings.MAX_ITERATIONS)

    # 当前状态标志位
    destination = state.get("destination", "")
    days = state.get("days", 0)
    info_collected = state.get("info_collected", False)
    current_plan = state.get("current_plan", [])
    budget_approved = state.get("budget_approved", False)
    preference_approved = state.get("preference_approved", False)

    logger.info(
        f"▶ [Supervisor] 迭代={iteration}/{max_iterations} | "
        f"需求={bool(destination)} | 信息={info_collected} | "
        f"计划={len(current_plan)}天 | 预算={budget_approved} | 偏好={preference_approved}"
    )

    # 安全兜底：超过最大迭代次数，避免死循环
    if iteration >= max_iterations:
        if state.get("human_feedback"):
            # 人工已经介入过，不能再打回，强制结束
            logger.warning("🛑 达到最大迭代且人工已介入，强制结束")
            return {"next_node": "end"}
        logger.warning(f"⚠️ 达到最大迭代 {max_iterations}，送人工审核")
        return {"next_node": "human_review"}

    # 按业务优先级顺序判断：缺什么补什么
    if not destination or days <= 0:
        return {"next_node": "requirement"}

    if not info_collected:
        return {"next_node": "info_collection"}

    if not current_plan:
        return {"next_node": "planner"}

    if not budget_approved:
        return {"next_node": "budget"}

    if not preference_approved:
        return {"next_node": "preference"}

    # 全部满足，进入人工审核
    logger.info("✅ 所有条件满足 → human_review")
    return {"next_node": "human_review"}