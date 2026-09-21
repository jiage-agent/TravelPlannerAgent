"""
异常处理装饰器
"""
import traceback
from functools import wraps
from app.utils.logger import setup_logger

logger = setup_logger("travel_planner.error_handler")


def safe_node(node_name: str):
    """节点异常处理装饰器：捕获异常、记日志、回写 state

    注意：会放过 LangGraph 内部控制流异常（interrupt / bubble up），
    否则 human_review 节点的 interrupt 机制会被吞掉。
    """
    def decorator(func):
        @wraps(func)
        def wrapper(state, *args, **kwargs):
            try:
                return func(state, *args, **kwargs)
            except Exception as e:
                # LangGraph 内部控制流异常（interrupt 等）必须向上抛，否则中断失效
                cls_name = type(e).__name__
                if "Interrupt" in cls_name or "BubbleUp" in cls_name:
                    raise

                logger.error(f"[{node_name}] 执行失败: {e}")
                logger.error(traceback.format_exc())

                if isinstance(state, dict):
                    state["error"] = f"{node_name} 节点失败: {str(e)}"
                return state
        return wrapper
    return decorator