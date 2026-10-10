"""
LangGraph 状态定义
所有 Agent 之间通过这个状态传递信息
"""
from typing import TypedDict, List, Dict, Any, Optional, Annotated
from operator import add
# 注意：为了强校验，我们将使用 Pydantic 模型来定义数据结构
from app.models.schemas import DayPlan

class AgentState(TypedDict):
    """
    多Agent系统的共享状态
    每个Agent读取和修改这个状态
    """
    # ========== 用户输入 ==========
    user_query: str  # 用户原始输入

    # ========== 需求分析结果 ==========
    destination: str  # 目的地
    days: int  # 天数
    budget: float  # 预算
    preferences: List[str]  # 偏好列表
    start_date: str  # 旅行开始日期（YYYY-MM-DD）

    # ========== 新增：外部信息收集（为 Planner 提供事实依据） ==========
    weather_info: Optional[str]  # 天气情况（如："第1天: 晴, 25-32℃; 第2天: 大雨..."）
    route_info: Optional[str]    # 路线信息（如："西湖到灵隐寺: 打车约20分钟，距离8公里"）
    info_collected: bool         # 标记信息是否已收集完毕（供 Supervisor 路由判断）

    # ========== 规划结果 ==========
    current_plan: List[DayPlan]  # 当前行程方案（已替换为强校验的 Pydantic 模型）
    total_cost: float  # 总花费

    # ========== 审核结果 ==========
    budget_approved: bool  # 预算是否通过
    budget_feedback: str  # 预算反馈
    preference_approved: bool  # 偏好是否通过
    preference_feedback: str  # 偏好反馈

    # ========== 循环控制 ==========
    iteration: int  # 当前迭代轮次
    max_iterations: int  # 最大迭代次数

    # ========== 最终输出 ==========
    final_plan: Optional[str]  # 最终方案
    messages: Annotated[List, add]  # 对话历史

    # ========== 人工审核 ==========
    human_approved: bool              # 用户是否通过
    human_feedback: str               # 用户的修改意见

    # ========== 错误信息 ==========
    error: Optional[str]

    # =========工具调用记录===========
    tool_results: Annotated[List[dict], add]

    # ========== 路由控制 ==========
    next_node: Optional[str]  # 由 supervisor 写入，指示下一步去哪个节点