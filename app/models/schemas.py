# app/models/schemas.py
"""
FastAPI 请求/响应数据模型 + LLM 结构化输出模型
"""
from pydantic import BaseModel, Field
from typing import Optional, List

# ==========================================
# 1. API 请求/响应模型 (用于 FastAPI)
# ==========================================
class PlanRequest(BaseModel):
    """规划请求"""
    user_query: str = Field(description="用户需求描述，如 '我想去杭州玩3天'")
    thread_id: str = Field(default="default", description="会话ID，用于多轮对话")

class DayPlan(BaseModel):
    """单天行程（同时也作为 LLM 结构化输出的 schema）"""
    day: int = Field(description="第几天，从1开始")
    activities: List[str] = Field(description="当天的活动列表，每个活动是一个字符串")
    estimated_cost: float = Field(description="预估花费（人民币）")
    notes: str = Field(default="", description="备注，如门票是否需要预约等")

class ItineraryOutput(BaseModel):
    """
    用于 LLM 结构化输出的包装类。
    因为 with_structured_output 只能接收 Class，不能接收 List[Class]。
    """
    plan: List[DayPlan] = Field(description="按天规划的行程列表，包含每一天的活动、费用和备注")

class PlanResponse(BaseModel):
    """规划响应"""
    success: bool
    thread_id: str
    destination: str = ""
    days: int = 0
    total_cost: float = 0.0
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
    status: str = "completed" # "completed" 或 "waiting_for_review"
    plan: Optional[str] = None
    final_plan: Optional[str] = None
    error: Optional[str] = None

class ResumeRequest(BaseModel):
    """恢复请求"""
    thread_id: str = Field(description="会话ID")
    user_feedback: str = Field(description="用户反馈: '通过' 或 修改意见")

# ==========================================
# 2. LLM 结构化输出模型 (这是最核心的部分！)
# ==========================================
class RequirementOutput(BaseModel):
    destination: str = Field(description="目的地，如 '杭州'，如果用户没提，则返回空字符串")
    days: int = Field(default=0, description="旅行天数，如果用户没提，返回0")
    budget: float = Field(default=0.0, description="预算金额，如果用户没提，返回0")
    preferences: List[str] = Field(default_factory=list, description="偏好列表")
    start_date: str = Field(default="", description="旅行开始日期，格式 YYYY-MM-DD。如果用户没提则返回空字符串。")

class BudgetCheckOutput(BaseModel):
    """budget_agent 的审查结果"""
    budget_approved: bool = Field(description="预算是否合理")
    feedback: str = Field(description="如果超预算，给出具体的削减建议；如果合理，返回 '预算合理'")

class PreferenceCheckOutput(BaseModel):
    """preference_agent 的偏好匹配结果"""
    preference_approved: bool = Field(description="行程是否符合用户偏好")
    feedback: str = Field(description="如果不符合，给出修改建议；符合则返回 '偏好匹配'")

class HumanReviewOutput(BaseModel):
    """human_review_agent 的判断"""
    human_approved: bool = Field(description="人类是否通过了这个方案")
    feedback: str = Field(description="人类的修改意见，若无则为空字符串")