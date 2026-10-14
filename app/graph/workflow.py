"""LangGraph 工作流：Supervisor 中心化的多 Agent 拓扑"""
from langgraph.graph import StateGraph, END                      # 状态图 + 结束节点
from langgraph.checkpoint.sqlite import SqliteSaver              # 状态持久化（SQLite）
import sqlite3                                                    # 数据库连接

from app.graph.state import AgentState                            # 全局状态定义
from app.config import settings                                   # 配置（DB_PATH 等）
from app.utils.logger import setup_logger                         # 日志

# 所有子 Agent 节点
from app.agents.supervisor import supervisor_node                 # 中央调度
from app.agents.requirement_agent import requirement_agent        # requirement_collection
from app.agents.info_collection_agent import info_collection_agent  # 天气/路线收集
from app.agents.planner_agent import planner_agent                # 行程规划
from app.agents.budget_agent import budget_agent                  # 预算审核
from app.agents.preference_agent import preference_agent          # 偏好审核
from app.agents.human_review_agent import human_review_agent, should_continue_after_human

logger = setup_logger("travel_planner.workflow")


def create_connection() -> sqlite3.Connection:
    """创建 SQLite 连接（全项目唯一一个，避免多实例写同一个库文件）。

    三个 PRAGMA 都是针对真实并发问题的：
    - journal_mode=WAL    ：读写并发，读操作不再被写操作阻塞；
    - busy_timeout=5000   ：写锁冲突时等待 5s 再报错，而不是直接 database is locked；
    - synchronous=NORMAL  ：WAL 下的推荐值，兼顾安全与写入性能。
    """
    conn = sqlite3.connect(settings.DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


# 全局唯一的连接与检查点保存器：v2.0 曾出现"两个 SqliteSaver 实例写同一个库文件"，
# 高并发下会互相抢写锁，这里收敛成一个。
_conn = create_connection()
checkpointer = SqliteSaver(_conn)


def build_workflow(with_checkpointer: bool = True, saver: SqliteSaver | None = None):
    """构建并编译工作流。with_checkpointer=True 时用于 FastAPI（带状态持久化）。"""
    workflow = StateGraph(AgentState)

    # 注册所有节点
    workflow.add_node("supervisor", supervisor_node)
    workflow.add_node("requirement", requirement_agent)
    workflow.add_node("info_collection", info_collection_agent)
    workflow.add_node("planner", planner_agent)
    workflow.add_node("budget", budget_agent)
    workflow.add_node("preference", preference_agent)
    workflow.add_node("human_review", human_review_agent)

    # 入口：所有请求先交给 Supervisor 评估
    workflow.set_entry_point("supervisor")

    # Hub-and-Spoke 拓扑：子节点执行完一律回到 supervisor，由它决定下一步
    workflow.add_edge("requirement", "supervisor")
    workflow.add_edge("info_collection", "supervisor")
    workflow.add_edge("planner", "supervisor")
    workflow.add_edge("budget", "supervisor")
    workflow.add_edge("preference", "supervisor")

    # supervisor 条件路由：读取 state["next_node"] 决定去哪
    workflow.add_conditional_edges(
        "supervisor",
        lambda state: state["next_node"],
        {
            "requirement": "requirement",
            "info_collection": "info_collection",
            "planner": "planner",
            "budget": "budget",
            "preference": "preference",
            "human_review": "human_review",
            "end": END,
        }
    )

    # 人工审核后的路由：通过则结束，驳回则回到 supervisor 重新调度
    workflow.add_conditional_edges(
        "human_review",
        should_continue_after_human,
        {
            "end": END,
            "supervisor": "supervisor",
        }
    )

    # 编译
    if with_checkpointer:
        # interrupt_before：进入 human_review 前挂起，等待人工介入
        app = workflow.compile(
            checkpointer=saver or checkpointer,
            interrupt_before=["human_review"],
        )
    else:
        app = workflow.compile()

    return app


# FastAPI 用（带持久化 + 中断恢复）
workflow_app = build_workflow(with_checkpointer=True)

# LangGraph Studio 用（平台自管状态，不需要本地 checkpointer）
studio_app = build_workflow(with_checkpointer=False)