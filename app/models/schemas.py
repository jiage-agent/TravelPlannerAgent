"""
FastAPI 请求/响应数据模型
"""
from pydantic import BaseModel, Field
from typing import Optional, List


class PlanRequest(BaseModel):
    """规划请求"""
    user_query: str = Field(description="用户需求描述，如'我想去杭州玩3天'")
    thread_id: str = Field(default="default", description="会话ID，用于多轮对话")


class DayPlan(BaseModel):
    """单天行程"""
    day: int
    activities: List[str]
    estimated_cost: float
    notes: str = ""


class PlanResponse(BaseModel):
    """规划响应"""
    success: bool
    thread_id: str
    destination: str = ""
    days: int = 0
    total_cost: float = 0
    plan: List[DayPlan] = []
    final_plan: Optional[str] = None
    error: Optional[str] = None


class SessionInfo(BaseModel):
    """会话信息"""
    thread_id: str
    exists: bool
    message: str = ""


class PlanStatus(BaseModel):
    """规划状态（用于 /plan 返回）"""
    success: bool
    thread_id: str
    status: str = "completed"              # "completed" 或 "waiting_for_review"
    plan: Optional[str] = None       # 当前方案（等待审核时返回）
    final_plan: Optional[str] = None # 最终方案（完成时返回）
    error: Optional[str] = None


class ResumeRequest(BaseModel):
    """恢复请求"""
    thread_id: str = Field(description="会话ID")
    user_feedback: str = Field(description="用户反馈：'通过' 或修改意见")