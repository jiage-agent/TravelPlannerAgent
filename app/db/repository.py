"""数据访问层：所有 SQL 操作集中在这里"""
from typing import Optional
from app.db.database import get_connection
from app.utils.logger import setup_logger

logger = setup_logger("travel_planner.db.repo")


# ========== 会话操作 ==========

def create_session(thread_id: str, user_query: str, user_id: Optional[int] = None) -> None:
    """创建会话记录"""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO sessions (id, user_id, user_query, status) VALUES (?, ?, ?, 'pending')",
            (thread_id, user_id, user_query),
        )
        conn.commit()
        logger.info(f"✔ 创建会话 | thread_id={thread_id}")
    finally:
        conn.close()


def update_session_status(thread_id: str, status: str) -> None:
    """更新会话状态（completed / failed）"""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE sessions SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, thread_id),
        )
        conn.commit()
        logger.info(f"✔ 更新会话状态 | thread_id={thread_id}, status={status}")
    finally:
        conn.close()


def get_session(thread_id: str) -> Optional[dict]:
    """查询单个会话"""
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (thread_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ========== 行程操作 ==========

def save_plan(thread_id: str, destination: str, days: int, total_cost: float, final_plan: str) -> None:
    """保存行程结果"""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO plans (session_id, destination, days, total_cost, final_plan) VALUES (?, ?, ?, ?, ?)",
            (thread_id, destination, days, total_cost, final_plan),
        )
        conn.commit()
        logger.info(f"✔ 保存行程 | thread_id={thread_id}, destination={destination}")
    finally:
        conn.close()


def get_plan_by_session(thread_id: str) -> Optional[dict]:
    """查询某会话的行程"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM plans WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (thread_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()