"""数据库连接与初始化"""
import sqlite3
from pathlib import Path
from app.config import settings
from app.utils.logger import setup_logger

logger = setup_logger("travel_planner.db")

# 确保 db 目录存在
Path(settings.DB_PATH).parent.mkdir(parents=True, exist_ok=True)


def get_connection() -> sqlite3.Connection:
    """获取数据库连接（每次调用返回新连接，线程安全）"""
    conn = sqlite3.connect(settings.DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row   # 让查询结果支持 dict 风格访问
    return conn


def init_db() -> None:
    """初始化数据库：建表 + 索引"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.executescript("""
        -- 用户表
        CREATE TABLE IF NOT EXISTS users (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            username    TEXT UNIQUE NOT NULL,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- 会话表
        CREATE TABLE IF NOT EXISTS sessions (
            id          TEXT PRIMARY KEY,
            user_id     INTEGER,
            user_query  TEXT NOT NULL,
            status      TEXT DEFAULT 'pending',
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
        CREATE INDEX IF NOT EXISTS idx_sessions_status  ON sessions(status);

        -- 行程表
        CREATE TABLE IF NOT EXISTS plans (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id   TEXT NOT NULL,
            destination  TEXT,
            days         INTEGER,
            total_cost   REAL,
            final_plan   TEXT,
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        );
        CREATE INDEX IF NOT EXISTS idx_plans_session_id ON plans(session_id);
    """)

    conn.commit()
    conn.close()
    logger.info(f"✔ 数据库初始化完成 | path={settings.DB_PATH}")