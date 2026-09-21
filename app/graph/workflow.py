"""构建 LangGraph 工作流"""
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver

from app.graph.state import AgentState
from app.agents.requirement_agent import requirement_agent
from app.agents.planner_agent import planner_agent
from app.agents.budget_agent import budget_agent
from app.agents.preference_agent import preference_agent
from app.agents.human_review_agent import human_review_agent, should_continue_after_human
from app.agents.supervisor import supervisor
from app.utils.logger import setup_logger
from app.config import settings

logger = setup_logger("travel_planner.workflow")


def build_workflow(with_checkpointer: bool = True):
    """构建并编译工作流"""
    workflow = StateGraph(AgentState)

    # ========== 1. 添加节点 ==========
    workflow.add_node("requirement", requirement_agent)
    workflow.add_node("planner", planner_agent)
    workflow.add_node("budget", budget_agent)
    workflow.add_node("preference", preference_agent)
    workflow.add_node("human_review", human_review_agent)
    # 注意：supervisor 不作为节点添加，它只作为路由函数使用

    # ========== 2. 入口 ==========
    workflow.set_entry_point("requirement")

    # ========== 3. 线性链 ==========
    workflow.add_edge("requirement", "planner")
    workflow.add_edge("planner", "budget")
    workflow.add_edge("budget", "preference")

    # ========== 4. supervisor 条件边（从 preference 出发） ==========
    workflow.add_conditional_edges(
        "preference",
        supervisor,                       # 路由函数，返回字符串
        {
            "human_review": "human_review",
            "planner": "planner",         # 审核未过 → 回 planner
        }
    )

    # ========== 5. human_review 条件边 ==========
    workflow.add_conditional_edges(
        "human_review",
        should_continue_after_human,      # 路由函数
        {
            "end": END,                   # 人工通过 → 结束
            "planner": "planner",         # 人工拒绝 → 回 planner
        }
    )

    # ========== 6. 编译 ==========
    if with_checkpointer:
        import sqlite3
        conn = sqlite3.connect(settings.DB_PATH, check_same_thread=False)
        checkpointer = SqliteSaver(conn)
        app = workflow.compile(checkpointer=checkpointer)
    else:
        app = workflow.compile()

    logger.info(f"✔ 工作流构建完成 (checkpointer={with_checkpointer})")
    return app


# ========== 双实例 ==========
# FastAPI 用（带 checkpointer，支持 interrupt 恢复）
workflow_app = build_workflow(with_checkpointer=True)

# LangGraph Studio 用（不带 checkpointer，平台自管）
studio_app = build_workflow(with_checkpointer=False)
checkpointer = workflow_app.checkpointer


