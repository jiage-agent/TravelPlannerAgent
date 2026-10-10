"""LLM 工厂：支持多模型热切换（DeepSeek / DashScope）"""
from langchain.chat_models import init_chat_model    # 统一的 LLM 初始化入口
from app.config import settings                      # 模型配置
from app.utils.logger import setup_logger            # 日志

logger = setup_logger("travel_planner.llm")


# ========== 模型注册表 ==========
# 新增模型只需在这里加一行
MODEL_REGISTRY = {
    "deepseek": {
        "model": settings.DEEPSEEK_MODEL,
        "base_url": settings.DEEPSEEK_BASE_URL,
        "api_key": settings.DEEPSEEK_API_KEY or settings.MODEL_API_KEY,  # 兼容旧字段
        "provider": "openai",
    },
    "dashscope": {
        "model": settings.DASHSCOPE_MODEL,
        "base_url": settings.DASHSCOPE_BASE_URL,
        "api_key": settings.DASHSCOPE_API_KEY,
        "provider": "openai",
    },
}


def get_llm(temperature: float | int | None = None, provider: str | None = None):
    """
    工厂函数：按 provider 创建 LLM 实例。
    - temperature: 温度，None 则用配置默认值
    - provider: 模型标识（"deepseek" / "dashscope"），None 则用 DEFAULT_PROVIDER
    """
    provider = provider or settings.DEFAULT_PROVIDER

    if provider not in MODEL_REGISTRY:
        logger.warning(f"⚠️ 未知 provider={provider}，回落到 deepseek")
        provider = "deepseek"

    cfg = MODEL_REGISTRY[provider]

    if not cfg["api_key"]:
        logger.warning(f"⚠️ {provider} 的 API Key 为空，请检查 .env")

    logger.info(f"▶ 加载模型 provider={provider}, model={cfg['model']}")

    return init_chat_model(
        model=cfg["model"],
        model_provider=cfg["provider"],
        base_url=cfg["base_url"],
        api_key=cfg["api_key"],
        temperature=temperature if temperature is not None else settings.TEMPERATURE,
        timeout=settings.TIMEOUT,
    )


# 默认实例（兼容旧代码 from app.models.llm_config import model）
model = get_llm()