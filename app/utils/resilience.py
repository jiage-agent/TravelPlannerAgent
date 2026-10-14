"""外部调用的弹性层：指数退避重试 + 失败降级。

对应简历里「工具调用失败自动重试（最多 3 次）」的实现。
设计原则：
- 重试只针对**可恢复**的外部故障（网络抖动、限流、5xx），业务错误不重试；
- 重试次数与退避基数由配置控制，方便演示与压测时调整；
- 重试耗尽后由调用方决定降级策略（返回兜底值而不是让整条链路崩掉）。
"""
import time
from typing import Any, Callable, Optional, Tuple, Type

from app.config import settings
from app.utils.logger import setup_logger

logger = setup_logger("travel_planner.resilience")


def retry_call(
    func: Callable[..., Any],
    *args: Any,
    retries: Optional[int] = None,
    base_delay: Optional[float] = None,
    max_delay: float = 4.0,
    exceptions: Tuple[Type[BaseException], ...] = (Exception,),
    what: str = "外部调用",
    **kwargs: Any,
) -> Any:
    """同步重试：指数退避 + 抖动。失败到上限后抛出最后一次异常。

    调用次数 = 1 次首发 + retries 次重试（默认共 4 次）。
    """
    retries = settings.TOOL_RETRY_TIMES if retries is None else retries
    base_delay = settings.TOOL_RETRY_BASE_DELAY if base_delay is None else base_delay

    attempt = 0
    last_exc: Optional[BaseException] = None
    while attempt <= retries:
        try:
            return func(*args, **kwargs)
        except exceptions as exc:  # noqa: PERF203 - 重试语义需要就地捕获
            last_exc = exc
            attempt += 1
            if attempt > retries:
                break
            delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
            logger.warning(f"⚠️ {what} 第 {attempt} 次失败（{type(exc).__name__}: {exc}），{delay:.1f}s 后重试")
            time.sleep(delay)

    logger.error(f"✘ {what} 重试 {retries} 次后仍失败：{last_exc}")
    assert last_exc is not None
    raise last_exc


def with_fallback(
    func: Callable[..., Any],
    fallback: Any,
    *args: Any,
    what: str = "外部调用",
    **kwargs: Any,
) -> Any:
    """重试耗尽后不抛异常，返回兜底值（降级而非中断）。"""
    try:
        return retry_call(func, *args, what=what, **kwargs)
    except Exception:
        logger.warning(f"⚠️ {what} 降级为兜底值")
        return fallback
