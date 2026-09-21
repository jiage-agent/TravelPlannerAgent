"""API Key 鉴权"""
from fastapi import Header, HTTPException, status
from app.config import settings
from app.utils.logger import setup_logger

logger = setup_logger("travel_planner.auth")


async def verify_api_key(x_api_key: str = Header(..., alias="X-API-Key")) -> str:
    """验证请求头里的 X-API-Key

    Args:
        x_api_key: 请求头 X-API-Key 的值

    Returns:
        验证通过时返回 key

    Raises:
        HTTPException: key 不匹配时抛 401
    """
    if x_api_key != settings.API_KEY:
        logger.warning(f"✘ 鉴权失败 | 收到key={x_api_key[:8]}...")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API Key",
            headers={"WWW-Authenticate": "X-API-Key"},
        )
    logger.debug("✔ 鉴权通过")
    return x_api_key