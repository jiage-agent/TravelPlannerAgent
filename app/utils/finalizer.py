"""最终方案格式化"""
from app.graph.state import AgentState


def format_final_plan(state: AgentState) -> str:
    """把 state 格式化成 Markdown 文本"""
    lines = [f"🌴 {state['destination']} {state['days']}日游方案\n"]
    for day_plan in state["current_plan"]:
        day = day_plan["day"] if isinstance(day_plan, dict) else day_plan.day
        activities = day_plan["activities"] if isinstance(day_plan, dict) else day_plan.activities
        cost = day_plan["estimated_cost"] if isinstance(day_plan, dict) else day_plan.estimated_cost

        lines.append(f"第{day}天:")
        for act in activities:
            lines.append(f"  - {act}")
        lines.append(f"  预估花费: ¥{cost}\n")

    lines.append(f"总花费: ¥{state['total_cost']}")
    budget_icon = "✅ 符合" if state.get("budget_approved") else "❌ 超出"
    pref_icon = "✅ 符合" if state.get("preference_approved") else "❌ 不符"
    lines.append(f"预算状态: {budget_icon}")
    lines.append(f"偏好状态: {pref_icon}")
    return "\n".join(lines)