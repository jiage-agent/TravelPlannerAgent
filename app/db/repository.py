"""业务数据访问层：会话与行程的读写"""
import sqlite3
from app.config import settings


def _conn():
    return sqlite3.connect(settings.DB_PATH, check_same_thread=False)


def create_session(thread_id: str, user_query: str):
    """创建会话"""
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO sessions (thread_id, user_query, status) VALUES (?, ?, 'pending')",
            (thread_id, user_query),
        )


def update_session_status(thread_id: str, status: str):
    """更新会话状态"""
    with _conn() as conn:
        conn.execute(
            "UPDATE sessions SET status = ? WHERE thread_id = ?",
            (status, thread_id),
        )


def save_plan(thread_id, destination, days, total_cost, final_plan):
    """保存最终行程"""
    with _conn() as conn:
        conn.execute(
            "INSERT INTO plans (thread_id, destination, days, total_cost, final_plan) "
            "VALUES (?, ?, ?, ?, ?)",
            (thread_id, destination, days, total_cost, final_plan),
        )


def get_session(thread_id: str):
    """查询会话"""
    with _conn() as conn:
        cursor = conn.execute(
            "SELECT thread_id, user_query, status FROM sessions WHERE thread_id = ?",
            (thread_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return {"thread_id": row[0], "user_query": row[1], "status": row[2]}


def get_plan(thread_id: str):
    """查询行程"""
    with _conn() as conn:
        cursor = conn.execute(
            "SELECT destination, days, total_cost, final_plan FROM plans WHERE thread_id = ? ORDER BY id DESC LIMIT 1",
            (thread_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return {"destination": row[0], "days": row[1], "total_cost": row[2], "final_plan": row[3]}