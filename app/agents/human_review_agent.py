"""
人工审核节点
职责：暂停工作流，等待用户确认或提供修改意见
"""
from langgraph.types import interrupt
from app.graph.state import AgentState
from app.utils.finalizer import format_final_plan   # 复用格式化工具
from app.utils.logger import setup_logger
from app.utils.error_handler import safe_node

logger = setup_logger("travel_planner.human_review")

@safe_node("human_review")
def human_review_agent(state: AgentState) -> AgentState:
    """人工审核节点：interrupt 等待用户输入"""
    plan = state.get("final_plan") or format_final_plan(state)

    logger.info("▶ 等待人工审核...")
    user_feedback = interrupt({
        "message": "请审核以下方案，回复'通过'表示接受，或输入修改意见：",
        "plan": plan,
    })

    feedback_str = str(user_feedback).strip().lower()
    logger.info(f"✔ 收到人工反馈 | {feedback_str!r}")

    if any(kw in feedback_str for kw in ["通过", "yes", "ok", "确认", "approve", "accept"]):
        state["human_approved"] = True
        state["human_feedback"] = ""
        state["final_plan"] = format_final_plan(state)
        logger.info("✔ 用户通过")
    else:
        state["human_approved"] = False
        state["human_feedback"] = str(user_feedback)
        state["budget_feedback"] = f"用户反馈: {user_feedback}"
        state["iteration"] = 0   # 重置迭代计数，重新开始
        logger.info(f"✔ 用户拒绝，将重新规划 | feedback={user_feedback!r}")

    return state


def should_continue_after_human(state: AgentState) -> str:
    """人工审核后的路由：通过则结束，否则回 planner"""
    if state.get("human_approved", False):
        logger.info("→ END (人工通过)")
        return "end"
    logger.info("→ planner (人工拒绝，重新规划)")
    return "planner"


