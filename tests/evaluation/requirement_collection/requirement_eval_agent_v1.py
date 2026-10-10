"""系统评估：跑评测集统计合规率与提取准确率"""
import json
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agents.requirement_agent import requirement_agent


def build_base_state(query: str) -> dict:
    """构造 Requirement Agent 需要的初始状态"""
    return {
        "user_query": query,
        "destination": "",
        "days": 0,
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


def evaluate():
    with open("tests/evaluation/requirement_collection/requirement_cases_50.json", "r", encoding="utf-8") as f:
        cases = json.load(f)

    total = len(cases)
    valid_output = 0
    dest_correct = 0
    days_correct = 0
    budget_correct = 0
    pref_hit = 0          # 至少有 1 个偏好词匹配
    pref_exact = 0        # 偏好集合完全一致（更严格）

    print(f"===== 🚀 开始评测 {total} 条用例 =====\n")

    for case in cases:
        state = build_base_state(case["query"])
        try:
            result = requirement_agent(state)
            valid_output += 1

            exp = case["expected"]
            dest_ok = result.get("destination") == exp["destination"]
            days_ok = result.get("days") == exp["days"]
            budget_ok = abs(result.get("budget", 0) - exp["budget"]) < 1

            exp_prefs = set(exp.get("preferences", []))
            got_prefs = set(result.get("preferences", []))
            hit_ok = len(exp_prefs & got_prefs) > 0 or (not exp_prefs and not got_prefs)
            exact_ok = exp_prefs == got_prefs

            if dest_ok: dest_correct += 1
            if days_ok: days_correct += 1
            if budget_ok: budget_correct += 1
            if hit_ok: pref_hit += 1
            if exact_ok: pref_exact += 1

            flag = "✅" if (dest_ok and days_ok and hit_ok) else "❌"
            print(f"{flag} [{case['id']}] {case['query'][:25]}...")
            print(f"    提取: dest={result.get('destination')} days={result.get('days')} budget={result.get('budget')} prefs={result.get('preferences')}")
            print(f"    期望: dest={exp['destination']} days={exp['days']} budget={exp['budget']} prefs={exp.get('preferences')}\n")

        except Exception as e:
            print(f"💥 [{case['id']}] 报错: {e}\n")

    print("=" * 55)
    print(f"📊 评测报告（共 {total} 条）")
    print("=" * 55)
    print(f"结构化输出合规率: {valid_output}/{total} = {valid_output/total*100:.1f}%")
    print(f"目的地提取准确率: {dest_correct}/{total} = {dest_correct/total*100:.1f}%")
    print(f"天数提取准确率:   {days_correct}/{total} = {days_correct/total*100:.1f}%")
    print(f"预算提取准确率:   {budget_correct}/{total} = {budget_correct/total*100:.1f}%")
    print(f"偏好命中率:       {pref_hit}/{total} = {pref_hit/total*100:.1f}%（至少 1 个偏好词匹配）")
    print(f"偏好完全匹配率:   {pref_exact}/{total} = {pref_exact/total*100:.1f}%（集合完全一致）")
    print("=" * 55)


if __name__ == "__main__":
    evaluate()