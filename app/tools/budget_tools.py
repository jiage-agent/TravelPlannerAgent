"""预算工具：精确计算"""
from langchain_core.tools import tool
from langchain_core.tools import tool
from pydantic import BaseModel, Field
from app.utils.logger import setup_logger

logger = setup_logger("travel_planner.tools.budget")


class CalculateCostInput(BaseModel):
    days: int = Field(description="旅行天数", gt=0)
    daily_cost: float = Field(description="每日预估花费（元）", ge=0)
    transport: float = Field(default=0, description="交通费用（元）")
    accommodation: float = Field(default=0, description="住宿费用（元）")


@tool(args_schema=CalculateCostInput)
def calculate_total_cost(days: int, daily_cost: float, transport: float = 0, accommodation: float = 0) -> str:
    """计算旅行总花费。

    需要根据天数、每日花费、交通、住宿计算总预算时使用。
    """
    total = days * daily_cost + transport + accommodation
    logger.info(f"💰 计算预算 | days={days}, daily={daily_cost}, total={total}")
    return f"总花费：{total:.0f} 元（{days}天 × {daily_cost}元/天 + 交通{transport} + 住宿{accommodation}）"


@tool
def check_budget_overflow(total_cost: float, budget: float) -> str:
    """检查是否超出预算。

    需要对比总花费和用户预算时使用。
    """
    diff = total_cost - budget
    if diff > 0:
        logger.warning(f"⚠ 超预算 | {diff:.0f}元")
        return f"超出预算 {diff:.0f} 元，建议削减 {diff / budget * 100:.1f}%"
    logger.info(f"✔ 预算充足 | 剩余={-diff:.0f}元")
    return f"预算充足，剩余 {-diff:.0f} 元"


BUDGET_TOOLS = [calculate_total_cost, check_budget_overflow]