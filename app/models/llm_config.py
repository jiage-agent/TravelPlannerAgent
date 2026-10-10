"""LLM 工厂：按需创建模型实例，支持不同 Agent 使用不同温度"""
from langchain.chat_models import init_chat_model    # 统一的 LLM 初始化入口
from app.config import settings                      # 模型配置（name/base_url/key 等）


def get_llm(temperature: float | int | None = None, model_name: str | None = None):
    """工厂函数：不同 Agent 可按需指定温度和模型名"""
    return init_chat_model(
        model=model_name or settings.MODEL_NAME,
        model_provider=settings.MODEL_PROVIDER,
        base_url=settings.MODEL_BASE_URL,
        api_key=settings.MODEL_API_KEY,
        temperature=temperature if temperature is not None else settings.TEMPERATURE,
        timeout=settings.TIMEOUT,
    )


# 默认实例：兼容旧代码里直接 import model 的地方
model = get_llm()