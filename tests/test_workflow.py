"""工作流层用例

本文件包含两部分，务必区分：

1. `test_workflow_topology_offline()` —— pytest 用例。只校验图的拓扑接线
   （节点是否齐全、入口、条件边去向），不连数据库、不调 LLM，默认跑。

2. `run_full_workflow()` —— **手动全链路脚本，不是 pytest 用例**。
   它会真的走完 supervisor → requirement → info_collection → planner → budget
   → preference → human_review，其中 requirement / planner / budget / preference
   都会调用真实 DeepSeek API，**会产生费用**。所以：
     * 函数名没有 test_ 前缀，pytest 不会收集它；
     * `workflow_app` 的导入放在函数内部，避免 pytest 收集本文件时就连数据库、编译图。

   需要跑全链路时手动执行：

       $env:PYTHONUTF8 = "1"
       uv run python tests/test_workflow.py
"""
import os
import sys
import uuid

# 把项目根目录加入 Python 搜索路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 期望出现在图里的 7 个业务节点
EXPECTED_NODES = {
    "supervisor",
    "requirement",
    "info_collection",
    "planner",
    "budget",
    "preference",
    "human_review",
}


def test_workflow_topology_offline():
    """离线校验图接线：7 个节点齐全，入口是 supervisor，子节点一律回到 supervisor。

    不需要 checkpointer（with_checkpointer=False），因此不碰 SQLite、不调 LLM。
    """
    from app.graph.workflow import build_workflow

    app = build_workflow(with_checkpointer=False)
    graph = app.get_graph()

    # 1. 节点齐全
    missing = EXPECTED_NODES - set(graph.nodes)
    assert not missing, f"工作流缺少节点: {missing}"

    # 2. 入口：__start__ → supervisor
    assert any(e.source == "__start__" and e.target == "supervisor" for e in graph.edges)

    # 3. Hub-and-Spoke：每个子节点执行完都回到 supervisor
    for node in EXPECTED_NODES - {"supervisor"}:
        if node == "human_review":
            continue  # human_review 走条件边，单独校验
        assert any(
            e.source == node and e.target == "supervisor" for e in graph.edges
        ), f"{node} 应该回到 supervisor"

    # 4. supervisor 的条件路由目标必须覆盖全部 6 个节点 + 结束
    supervisor_targets = {e.target for e in graph.edges if e.source == "supervisor" and e.conditional}
    assert supervisor_targets == (EXPECTED_NODES - {"supervisor"}) | {"__end__"}, (
        f"supervisor 条件路由不完整: {sorted(supervisor_targets)}"
    )

    # 5. 人工审核后的条件路由：通过→结束，驳回→回 supervisor 重规划
    human_targets = {e.target for e in graph.edges if e.source == "human_review" and e.conditional}
    assert human_targets == {"__end__", "supervisor"}, sorted(human_targets)


def run_full_workflow():
    """⚠️ 手动全链路脚本：会调用真实 LLM，产生 API 费用，pytest 不收集。"""
    from app.graph.workflow import workflow_app  # 延迟导入：避免 pytest 收集时连库/编译图

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
