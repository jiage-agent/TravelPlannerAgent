"""数据库初始化：建表"""
import sqlite3
from app.config import settings
from app.utils.logger import setup_logger

logger = setup_logger("travel_planner.db")


def init_db():
    """初始化业务表（会话表 + 行程表）"""
    conn = sqlite3.connect(settings.DB_PATH, check_same_thread=False)
    cursor = conn.cursor()

    # 会话表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            thread_id TEXT PRIMARY KEY,
            user_query TEXT,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 行程表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT,
            destination TEXT,
            days INTEGER,
            total_cost REAL,
            final_plan TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (thread_id) REFERENCES sessions(thread_id)
        )
    """)

    conn.commit()
    conn.close()
    logger.info("✅ 数据库初始化完成")