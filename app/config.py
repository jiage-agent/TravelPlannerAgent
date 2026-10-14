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

    # ========== 外部工具配置 ==========
    # 高德地图 Web 服务：天气 / 景点 POI / 驾车路线 / 地理编码
    AMAP_API_KEY: str = Field(default="")
    # Tavily 联网搜索：实时攻略、门票、交通
    TAVILY_API_KEY: str = Field(default="")
    SEARCH_MAX_RESULTS: int = Field(default=3)

    # ========== 工具弹性配置 ==========
    # 外部调用失败重试次数（1 次首发 + N 次重试）与退避基数
    TOOL_RETRY_TIMES: int = Field(default=3)
    TOOL_RETRY_BASE_DELAY: float = Field(default=0.5)

    # ========== 工具结果缓存 TTL（秒）==========
    CACHE_TTL_GEOCODE: int = Field(default=604800)  # 地名与坐标基本不变，缓存 7 天
    CACHE_TTL_WEATHER: int = Field(default=3600)    # 天气：小时级更新
    CACHE_TTL_POI: int = Field(default=86400)       # 景点：天级更新
    CACHE_TTL_ROUTE: int = Field(default=86400)     # 路线：天级更新
    CACHE_TTL_SEARCH: int = Field(default=21600)    # 联网搜索：6 小时

    # ========== 上下文预算（token）==========
    CONTEXT_MAX_TOKENS: int = Field(default=6000)
    CONTEXT_KEEP_TURNS: int = Field(default=6)

    # ========== LLM 自主工具调用 ==========
    # True：除确定性核心链路外，额外让模型自主决定补充搜索什么（Function Calling）
    ENABLE_LLM_TOOL_CALLING: bool = Field(default=True)
    LLM_TOOL_MAX_CALLS: int = Field(default=2)

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