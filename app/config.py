"""
统一配置管理
所有配置从 .env 读取，集中管理
使用 pydantic-settings 提供强类型校验和安全输出
"""
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    """项目配置"""

    # ========== 旧字段 ==========
    MODEL_NAME: str = Field(default="deepseek-chat")
    MODEL_PROVIDER: str = Field(default="openai")
    MODEL_BASE_URL: str = Field(default="https://api.deepseek.com/v1")
    MODEL_API_KEY: str = Field(default="", alias="DEEPSEEK_API_KEY")
    TEMPERATURE: float = Field(default=0.3)
    TIMEOUT: int = Field(default=60)

    # ========== 多模型配置 ==========
    DEFAULT_PROVIDER: str = Field(default="deepseek")

    # DeepSeek
    DEEPSEEK_API_KEY: str = Field(default="")
    DEEPSEEK_BASE_URL: str = Field(default="https://api.deepseek.com/v1")
    DEEPSEEK_MODEL: str = Field(default="deepseek-chat")

    # 阿里云百炼（DashScope 兼容 OpenAI 协议）
    DASHSCOPE_API_KEY: str = Field(default="")
    DASHSCOPE_BASE_URL: str = Field(default="https://dashscope.aliyuncs.com/compatible-mode/v1")
    DASHSCOPE_MODEL: str = Field(default="qwen-plus")

    # ========== 数据库配置 ==========
    DB_PATH: str = Field(default="./app/db/travel_agent.db")

    # ========== 工作流配置 ==========
    MAX_ITERATIONS: int = Field(default=3)
    RECURSION_LIMIT: int = Field(default=20)

    # ========== 日志配置 ==========
    LOG_LEVEL: str = Field(default="INFO")
    LOG_DIR: str = Field(default="./logs")

    # ========== 鉴权配置 ==========
    API_KEY: str = Field(default="dev-key-12345")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()