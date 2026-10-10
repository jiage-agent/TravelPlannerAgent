"""系统评估：支持综合测试 + 分开测试两种模式

用法：
    uv run python -m tests.eval_agent          # 综合测试（正 + 负）
    uv run python -m tests.eval_agent split    # 分开测试（正、负各跑一遍）
"""
import json
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agents.requirement_agent import requirement_agent


# ================================================================
# 初始化状态
# ================================================================
def build_base_state(query: str) -> dict:
    return {
        "user_query": query,
        "destination": "", "days": 0,
        "budget": 0.0,
        "preferences": [],
        "start_date": "",
        "weather_info": None,
        "route_info": None,
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


# ================================================================
# 单条评测
# ================================================================
def run_single(case):
    """跑一条用例，返回评测结果字典"""
    state = build_base_state(case["query"])
    try:
        result = requirement_agent(state)
        exp = case["expected"]

        dest_ok = result.get("destination") == exp["destination"]
        days_ok = result.get("days") == exp["days"]
        budget_ok = abs(result.get("budget", 0) - exp["budget"]) < 1

        exp_prefs = set(exp.get("preferences", []))
        got_prefs = set(result.get("preferences", []))
        pref_ok = len(exp_prefs & got_prefs) > 0 or (not exp_prefs and not got_prefs)

        return {
            "id": case["id"],
            "query": case["query"],
            "type": case.get("type", "unknown"),
            "valid": True,
            "dest_ok": dest_ok,
            "days_ok": days_ok,
            "budget_ok": budget_ok,
            "pref_ok": pref_ok,
            "extracted": result,
            "expected": exp,
        }
    except Exception as e:
        return {
            "id": case["id"],
            "query": case["query"],
            "type": case.get("type", "unknown"),
            "valid": False,
            "error": str(e),
        }


# ================================================================
# 打印单条结果
# ================================================================
def print_case(c):
    if not c["valid"]:
        print(f"💥 [{c['id']}] 报错: {c.get('error')}")
        return
    flag = "✅" if (c["dest_ok"] and c["days_ok"] and c["pref_ok"]) else "❌"
    print(f"{flag} [{c['id']}] {c['query'][:30]}...")
    print(f"    提取: dest={c['extracted'].get('destination')} days={c['extracted'].get('days')} "
          f"budget={c['extracted'].get('budget')} prefs={c['extracted'].get('preferences')}")
    print(f"    期望: dest={c['expected']['destination']} days={c['expected']['days']} "
          f"budget={c['expected']['budget']} prefs={c['expected'].get('preferences')}")


# ================================================================
# 统计 + 输出报告
# ================================================================
def summarize(results, label):
    total = len(results)
    if total == 0:
        print(f"\n📊 {label}：无用例")
        return

    valid = sum(1 for r in results if r["valid"])
    dest = sum(1 for r in results if r["valid"] and r["dest_ok"])
    days = sum(1 for r in results if r["valid"] and r["days_ok"])
    budget = sum(1 for r in results if r["valid"] and r["budget_ok"])
    pref = sum(1 for r in results if r["valid"] and r["pref_ok"])

    print(f"\n📊 {label}（{total} 条）")
    print("-" * 45)
    print(f"结构化输出合规率: {valid}/{total} = {valid / total * 100:.1f}%")
    print(f"目的地提取准确率: {dest}/{total} = {dest / total * 100:.1f}%")
    print(f"天数提取准确率:   {days}/{total} = {days / total * 100:.1f}%")
    print(f"预算提取准确率:   {budget}/{total} = {budget / total * 100:.1f}%")
    print(f"偏好命中率:       {pref}/{total} = {pref / total * 100:.1f}%")

    return {
        "total": total, "valid": valid, "dest": dest,
        "days": days, "budget": budget, "pref": pref,
    }


# ================================================================
# 模式 1：综合测试（正 + 负 混跑）
# ================================================================
def run_all(cases):
    print("=" * 55)
    print(f"📊 综合评测（正样例 + 负样例，共 {len(cases)} 条）")
    print("=" * 55)
    results = []
    for case in cases:
        c = run_single(case)
        print_case(c)
        results.append(c)

    pos = [r for r in results if r["type"] == "positive"]
    neg = [r for r in results if r["type"] == "negative"]

    summarize(results, "综合")
    if pos:
        summarize(pos, "正样例子集")
    if neg:
        summarize(neg, "负样例子集")


# ================================================================
# 模式 2：分开测试（正、负 各跑一遍）
# ================================================================
def run_split(cases):
    pos = [c for c in cases if c.get("type") == "positive"]
    neg = [c for c in cases if c.get("type") == "negative"]

    print("=" * 55)
    print("📊 分开评测模式")
    print("=" * 55)

    print(f"\n🟢 正样例（调参集，{len(pos)} 条）")
    print("-" * 45)
    pos_results = []
    for case in pos:
        c = run_single(case)
        print_case(c)
        pos_results.append(c)
    summarize(pos_results, "正样例报告")

    print(f"\n🔴 负样例（鲁棒性，{len(neg)} 条）")
    print("-" * 45)
    neg_results = []
    for case in neg:
        c = run_single(case)
        print_case(c)
        neg_results.append(c)
    summarize(neg_results, "负样例报告")


# ================================================================
# 入口
# ================================================================
def main():
    with open("tests/evaluation/requirement_collection/requirement_cases_50.json", "r", encoding="utf-8") as f:
        cases = json.load(f)

    mode = sys.argv[1] if len(sys.argv) > 1 else "all"

    if mode == "split":
        run_split(cases)
    else:
        run_all(cases)


if __name__ == "__main__":
    main()