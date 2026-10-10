import asyncio
import sys
import os

# 强制把项目根目录加入 Python 搜索路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.graph.state import AgentState
from app.agents.supervisor import supervisor_node
from app.agents.requirement_agent import requirement_agent
from app.agents.info_collection_agent import info_collection_agent
from app.agents.planner_agent import planner_agent
from app.agents.budget_agent import budget_agent
from app.agents.preference_agent import preference_agent
from app.agents.human_review_agent import human_review_agent, should_continue_after_human

# 1. 构造一个“空”的初始状态（模拟一个刚进来的用户请求）
def get_base_state() -> AgentState:
    return {
        "user_query": "我想去杭州玩3天，预算5000块，喜欢美食和自然风光",
        "destination": "",
        "days": 0,
        "budget": 0.0,
        "preferences": [],
        "info_collected": False,
        "current_plan": [],
        "total_cost": 0.0,
        "budget_approved": False,
        "budget_feedback": "",
        "preference_approved": False,
        "preference_feedback": "",
        "iteration": 0,
        "max_iterations": 3,
        "final_plan": None,
        "messages": [],
        "human_approved": False,
        "human_feedback": "",
        "error": None,
        "tool_results": [],
        "next_node": None,
    }

async def test_supervisor():
    print("===== 测试 Supervisor =====")
    state = get_base_state()
    # 注意：supervisor_node 是同步函数，不要加 await！
    result = supervisor_node(state)
    print(f"Supervisor 输出: {result}")
    assert result["next_node"] == "requirement", "❌ 路由错误，应该去 requirement"
    print("✅ Supervisor 测试通过\n")

async def test_requirement():
    print("===== 测试 Requirement Agent =====")
    state = get_base_state()
    try:
        # requirement_agent 是 async def，需要加 await
        result = await requirement_agent(state)
        print(f"提取结果: {result}")
    except Exception as e:
        print(f"❌ Requirement 运行报错: {e}")


async def test_supervisor_routes_to_info_collection():
    """测试 Supervisor 在需求提取完成后，是否路由到信息收集节点"""
    print("===== 测试 Supervisor (路由到 info_collection) =====")
    state = get_base_state()
    # 模拟需求已经提取完成
    state["destination"] = "杭州"
    state["days"] = 3
    state["budget"] = 5000.0
    state["info_collected"] = False  # 信息还没收集

    result = supervisor_node(state)
    print(f"Supervisor 输出: {result}")
    assert result["next_node"] == "info_collection", "❌ 路由错误，应该去 info_collection"
    print("✅ Supervisor 路由测试通过\n")


async def test_info_collection():
    """测试信息收集节点是否能正常返回数据并更新状态"""
    print("===== 测试 InfoCollection Agent =====")
    state = get_base_state()
    state["destination"] = "杭州"
    state["days"] = 3

    result = await info_collection_agent(state)
    print(f"信息收集结果: weather={result.get('weather_info')}, route={result.get('route_info')}")
    assert result["info_collected"] is True, "❌ 标记位没有更新"
    print("✅ InfoCollection 测试通过\n")


async def test_planner():
    """测试行程规划节点"""
    print("===== 测试 Planner Agent =====")
    state = get_base_state()
    # 模拟已经提取的需求
    state["destination"] = "杭州"
    state["days"] = 3
    state["budget"] = 5000.0
    state["preferences"] = ["美食", "自然风光"]
    # 模拟已经收集的信息
    state["info_collected"] = True
    state["weather_info"] = "杭州未来3天：第1天晴 25-32℃；第2天大雨 22-28℃；第3天多云 24-30℃"
    state["route_info"] = "西湖 -> 灵隐寺（约 8 公里）；灵隐寺 -> 宋城（约 12 公里）"

    result = await planner_agent(state)
    print(f"生成的行程: {result.get('current_plan')}")
    print(f"总花费: {result.get('total_cost')}")
    assert len(result.get("current_plan", [])) == 3, "❌ 生成的行程天数不对"
    assert result.get("total_cost", 0) > 0, "❌ 总花费计算错误"
    print("✅ Planner 测试通过\n")


async def test_budget():
    """测试预算审核节点"""
    print("===== 测试 Budget Agent =====")

    # 场景 1：预算充足（5000 预算，花费 3700）
    state = get_base_state()
    state["budget"] = 5000.0
    state["total_cost"] = 3700.0
    state["current_plan"] = [
        {"day": 1, "activities": ["西湖", "灵隐寺"], "estimated_cost": 1200.0, "notes": "晴天"},
        {"day": 2, "activities": ["浙江省博物馆"], "estimated_cost": 1000.0, "notes": "雨天室内"},
        {"day": 3, "activities": ["宋城"], "estimated_cost": 1500.0, "notes": "多云"},
    ]

    result = await budget_agent(state)
    print(f"审核结果: approved={result.get('budget_approved')}, feedback={result.get('budget_feedback')}")
    assert result.get("budget_approved") is True, "❌ 预算充足却被驳回"
    print("✅ 场景1（预算充足）测试通过\n")

    # 场景 2：预算严重超支（5000 预算，花费 8000）—— 测试硬逻辑兜底
    print("===== 测试 Budget Agent (超支场景) =====")
    state["total_cost"] = 8000.0
    result = await budget_agent(state)
    print(f"审核结果: approved={result.get('budget_approved')}, feedback={result.get('budget_feedback')}")
    assert result.get("budget_approved") is False, "❌ 超支却没被驳回"
    print("✅ 场景2（超支驳回）测试通过\n")


async def test_preference():
    """测试偏好审核节点"""
    print("===== 测试 Preference Agent =====")

    # 场景 1：偏好匹配
    state = get_base_state()
    state["preferences"] = ["美食", "自然风光"]
    state["current_plan"] = [
        {"day": 1, "activities": ["西湖", "楼外楼吃杭帮菜"], "estimated_cost": 1200.0, "notes": ""},
        {"day": 2, "activities": ["浙江省博物馆"], "estimated_cost": 1000.0, "notes": ""},
    ]

    result = await preference_agent(state)
    print(f"审核结果: approved={result.get('preference_approved')}, feedback={result.get('preference_feedback')}")
    print("✅ 场景1（偏好匹配）测试完成\n")

    # 场景 2：无偏好默认通过（不消耗 LLM）
    print("===== 测试 Preference Agent (无偏好) =====")
    state["preferences"] = []  # 用户没提偏好
    result = await preference_agent(state)
    print(f"审核结果: approved={result.get('preference_approved')}, feedback={result.get('preference_feedback')}")
    assert result.get("preference_approved") is True, "❌ 无偏好时应该默认通过"
    print("✅ 场景2（无偏好默认通过）测试通过\n")


async def test_human_review():
    """测试人工审核节点"""
    print("===== 测试 HumanReview Agent =====")

    # 场景 1：人工通过
    state = get_base_state()
    state["current_plan"] = [{"day": 1, "activities": ["西湖"], "estimated_cost": 500.0, "notes": ""}]
    state["human_approved"] = True

    result = await human_review_agent(state)
    print(f"审核结果: final_plan={result.get('final_plan')}")
    assert result.get("final_plan") is not None, "❌ 通过后没有输出最终方案"
    print("✅ 场景1（人工通过）测试通过\n")

    # 场景 2：人工驳回
    print("===== 测试 HumanReview Agent (人工驳回) =====")
    state["human_approved"] = False
    state["human_feedback"] = "预算太高了，重新规划"
    state["iteration"] = 0

    result = await human_review_agent(state)
    print(f"审核结果: iteration={result.get('iteration')}")
    assert result.get("iteration") == 1, "❌ 驳回后 iteration 应该 +1"

    # 验证路由逻辑
    route = should_continue_after_human({**state, **result})
    print(f"路由结果: {route}")
    assert route == "supervisor", "❌ 驳回后应该回 supervisor"
    print("✅ 场景2（人工驳回）测试通过\n")


async def main():
    # 按顺序执行单测
    await test_supervisor()
    await test_requirement()
    await test_supervisor_routes_to_info_collection() # 新增
    await test_info_collection()                      # 新增
    await test_planner()
    await test_budget()
    await test_preference()
    await test_human_review()

if __name__ == "__main__":
    asyncio.run(main())