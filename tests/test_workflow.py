import asyncio
import sys
import os
import uuid

# 把项目根目录加入 Python 搜索路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 导入你编译好的工作流图
from app.graph.workflow import workflow_app

import os
# # 强制注入 LangSmith 配置（防止 .env 文件未被加载）
# os.environ["LANGCHAIN_TRACING_V2"] = "true"
# os.environ["LANGCHAIN_PROJECT"] = "travel-planner-agent"


def run_full_workflow():
    print("===== 🚀 开始测试完整的 LangGraph 工作流 =====")

    # 1. 构造初始状态（模拟新用户进来）
    initial_state = {
        "user_query": "我想10月1号去杭州玩3天，预算4000块，喜欢美食和自然风光",  # 故意把预算设低一点，看看会不会触发重规划
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
        "human_approved": True,  # 初始状态先设为 False
        "human_feedback": "",
        "error": None,
        "tool_results": [],
        "next_node": None,
        "start_date": "",
    }

    # 2. 生成一个唯一的 thread_id（用于 LangGraph 检查点恢复）
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    print(f"▶ 使用 thread_id: {thread_id}")
    print("▶ 开始执行完整工作流...\n")

    # 3. 调用工作流，跑完整条链路
    try:
        # 因为我们在 workflow.py 里用了 add_conditional_edges，图会自己流转
        # 注意：这里如果 human_approved 一直是 False，且没有真正的"人工输入"打断，它可能会停在 human_review 或者一直循环
        for event in workflow_app.stream(initial_state, config):
            for node_name, output in event.items():
                print(f"🔹 节点 [{node_name}] 执行完毕")
                if node_name == "supervisor":
                    print(f"   └─ 路由决策: {output.get('next_node')}")
                elif node_name == "human_review":
                    print(f"   └─ 人工审核状态: {output.get('human_approved')}")
                    print(f"   └─ 最终方案: {output.get('final_plan')}")
                print("-" * 50)
    except Exception as e:
        print(f"\n❌ 工作流执行出错: {e}")

    print("\n===== ✅ 完整工作流测试结束 =====")

if __name__ == "__main__":
    run_full_workflow()