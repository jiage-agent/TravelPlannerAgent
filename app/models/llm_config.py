# app/models/llm_config.py
from langchain.chat_models import init_chat_model
from app.config import settings

model = init_chat_model(
    model=settings.MODEL_NAME,
    model_provider=settings.MODEL_PROVIDER,
    base_url=settings.MODEL_BASE_URL,
    api_key=settings.MODEL_API_KEY,
    temperature=settings.TEMPERATURE,
    timeout=settings.TIMEOUT
)