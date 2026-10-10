"""API Key 鉴权"""
from fastapi import Header, HTTPException
from app.config import settings
from app.utils.logger import setup_logger

logger = setup_logger("travel_planner.auth")


async def verify_api_key(x_api_key: str = Header(default="")):
    """校验请求头里的 X-API-Key 是否匹配 .env 里的 API_KEY"""
    if x_api_key != settings.API_KEY:
        logger.warning(f"⚠️ 鉴权失败: 收到 key={x_api_key[:6]}...")
        raise HTTPException(status_code=401, detail="Invalid API Key")
    return x_api_key