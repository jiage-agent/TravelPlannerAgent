"""
统一配置管理
所有配置从 .env 读取，集中管理
"""
import os
import logging
from dotenv import load_dotenv

load_dotenv()


class Settings:
    """项目配置"""

    # ========== 模型配置 ==========
    MODEL_NAME: str = os.getenv("MODEL_NAME", "deepseek-chat")
    MODEL_PROVIDER: str = "openai"
    MODEL_BASE_URL: str = os.getenv("MODEL_BASE_URL", "https://api.deepseek.com/v1")   # ← 修
    MODEL_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")   # ← 只留 DeepSeek
    TEMPERATURE: float = float(os.getenv("TEMPERATURE", "0.3"))
    TIMEOUT: int = int(os.getenv("TIMEOUT", "60"))

    # ========== 数据库配置 ==========
    DB_PATH: str = os.getenv("DB_PATH", "./app/db/travel_agent.db")

    # ========== 工作流配置 ==========
    MAX_ITERATIONS: int = int(os.getenv("MAX_ITERATIONS", "3"))
    RECURSION_LIMIT: int = int(os.getenv("RECURSION_LIMIT", "20"))

    # ========== 日志配置 ==========
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_DIR: str = os.getenv("LOG_DIR", "./logs")

    # ========== 鉴权配置 ==========
    API_KEY: str = os.getenv("API_KEY", "dev-key-12345")


settings = Settings()