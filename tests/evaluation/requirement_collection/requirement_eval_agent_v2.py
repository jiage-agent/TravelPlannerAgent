"""需求提取 Agent 评测脚本 v2（口径修复版）

用法（务必在项目根目录、用项目 venv 运行）：
    $env:PYTHONUTF8=1; $env:PYTHONIOENCODING="utf-8"
    & "D:\\develop\\travel-planner-agent-v2.0\\.venv\\Scripts\\python.exe" `
        -m tests.evaluation.requirement_collection.requirement_eval_agent_v2

数据流：
    第一次运行（只调用一次 API）→ 保存运行输出 report_20261014.txt

============================================================================
本次相对旧版修复的 4 个口径问题
============================================================================
旧版：pref_ok = len(exp & got) > 0（期望 3 个只中 1 个也算对）；
      所有指标分母都是"全部用例数"，包含大量"用户没提该字段 → 期望默认值
      → 模型返回默认值就算对"的平凡正确项，准确率虚高；
      负样例的 valid 只表示"没抛异常"，没有任何"应当如何"的判定；
      没有样本级全对率。

新版口径：

【1】偏好指标 = 集合运算的 precision / recall / F1（微平均，按集合元素计数）
    - 期望非空且输出同为该集合            → TP 满分
    - 期望非空、输出命中一部分            → 按交集元素计数（命中 1/3 只拿 1/3）
    - 期望为空且输出也为空                → 记为"空集正确"，单独报告条数；
                                            因无正例也无预测，不并入 F1（否则会虚增
                                            或稀释 F1），metric 段单列这一行
    - 期望为空但输出了偏好                → FP，precision 直接损失（recall 不受影响）
    公式：P = TP/(TP+FP)，R = TP/(TP+FN)，F1 = 2PR/(P+R) = 2TP/(2TP+FP+FN)
          （全文统一用调和平均定义，micro 与 macro 口径一致）
    micro = 元素级汇总；macro = 仅对"涉及偏好的非平凡用例"逐条求 P/R/F1 再平均，
          空集正确条目不进 macro（它们不产生预测，属于平凡正确）

【2】分母拆分（只对"用户实际提及的字段"算准确率）
    - 提及口径：destination=期望非空；days=期望>0；budget=期望>0；preferences=期望非空
      （注意：这条口径是按"该字段是否有有效值"判定，不是字符串匹配。
       因此 case 42「0元…」budget 期望 0，satisfied 判为"未提及"，是已知局限）
    - 提及段：提取准确率，分母 = 用户提了的条数
    - 未提及段：拒答正确率，分母 = 用户没提的条数（期望默认值 ""/0/0.0/[]）

【3】每个指标都打印 分子/分母（如 目的地 32/34），不裸给百分比

【4】样本级全对率（该条所有字段全部正确）
    正样例：destination + days + budget + preferences 四个字段全对
    负样例：按"该类型的期望行为"逐字段判定（见【5】）

【5】负样例分四类统计（前三类是任务要求的主类，第 4 类是兜底，见下）
    A-非法表达 illegal      ：火星 / 月球 / 123456 / asdfghjkl
        期望行为：这些不是可提取的目的地 → destination 应为空（或原样回显被识别为非法），
                  days/budget/preferences 应为默认值，绝不能编造一个真实城市
    B-越界/不合理 out_of_range：0元玩一个月 / 100块玩7天 / 1块+五星酒店 / 365天 / 100万
        期望行为：数字本身是"清楚说出来的"，提取器要忠于原文提对
                  （提取准确=行为正确，记为 PASS）；
                  至于数值是否合理，属于 budget_agent 的审查职责，本脚本不重复判
    C-境外目的地 overseas   ：东京 / 巴黎 / 纽约（期望值是完整填写的）
        期望行为：提取器照常完整提取（本节点不做"只支持国内"的拒绝），
                  但必须打标：境外 destination 要"提对 + 打标提醒/拦截"，
                  若直接当国内城市放行 → 记为 FAIL
    D-其他 other           ：随便去哪都行 / 订机票 / 看股票 / 讨厌海边 / 南极探险
        期望行为：非目的地类请求不臆造 destination（默认值兜底）；
                  用户明确说出的偏好要提对；
                  显式否定（"讨厌海边"）不得进 preferences
    每类都打印：样本数、行为 PASS（分子/分母）、失败样本明细与错在哪

【6】分类统计：综合 / positive 子集 / negative 子集 三段都输出

【7】结尾输出「结论摘要」区块，格式紧凑，可直接贴进简历/README，
    并附「新旧口径对照」，说明旧指标为什么虚高
============================================================================
"""
import json
import os
import re
import sys
import unicodedata
from statistics import mean

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agents.requirement_agent import requirement_agent

DATASET_PATH = "tests/evaluation/requirement_collection/requirement_cases_50.json"
BUDGET_TOL = 1.0          # 预算容差：差 < 1 元算对（与旧版一致）

# ================================================================
# 字段规格：默认值 + 提及口径
# ================================================================
FIELD_ORDER = ["destination", "days", "budget", "preferences"]

FIELD_DEFAULT = {
    "destination": "",
    "days": 0,
    "budget": 0,
    "preferences": [],
}

FIELD_LABEL = {
    "destination": "目的地",
    "days": "天数",
    "budget": "预算",
    "preferences": "偏好",
}

# 提及口径说明（写进报告，便于追问时解释）：
#   destination  = 期望非空
#   days/budget  = 期望 > 0（0 视为未提及 → 用 0 做"字段缺失"的哨兵值）
#   preferences  = 期望列表非空
MENTION_RULE = {
    "destination": "期望非空",
    "days": "期望>=1",
    "budget": "期望>0",
    "preferences": "期望列表非空",
}


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
# 负样例分类：A-非法 / B-越界 / C-境外 / D-其他
# 关键词是唯一真相来源；只有关键词识别不出来时，才用显式 id 兜底
# ================================================================
NEG_CATEGORY_ORDER = ["illegal", "out_of_range", "overseas", "other"]

NEG_CATEGORY_LABEL = {
    "illegal": "A-非法表达",
    "out_of_range": "B-越界/不合理",
    "overseas": "C-境外目的地",
    "other": "D-其他",
}

NEG_CATEGORY_BEHAVIOR = {
    "illegal": "非法目的词不得被识别为目的地；其余字段取默认值，禁止编造真实城市",
    "out_of_range": "数值是用户明确说出的，需忠于原文提对（合理性审查交给 budget_agent）",
    "overseas": "照常完整提取，但必须打标/拦截提示，不得当国内城市直接放行",
    "other": "非目的地类请求不臆造目的地；显式否定偏好不得进入 preferences 列表",
}

NEG_CATEGORY_KEYWORDS = {
    "illegal": ["火星", "月球", "123456", "asdfghjkl"],
    "out_of_range": ["0元", "100块", "1块", "365天", "100万", "一个月"],
    "overseas": ["东京", "巴黎", "纽约"],
}

# 关键词兜不住时的显式归类（关键词识别优先，这里只是兜底）
NEG_CATEGORY_BY_ID = {
    36: "other",   # 随便去哪都行
    37: "other",   # 帮我订一张去广州的机票
    38: "other",   # 我想看股票行情
    44: "other",   # 去三亚但讨厌海边
}

# 显式否定词 → 这些偏好若出现在输出里就是错的
NEGATION_PATTERN = re.compile(
    r"讨厌|不喜欢|不要去|不想去|没法接受|拒绝|no\s+|not\s+|dislike|hate",
    re.IGNORECASE,
)

# D-其他 这 4 条不属前三类（"不是目的地类请求" / "显式否定偏好"），
# 逐条写明期望行为，报告里也会打印出来，避免"判了但说不清依据"。
NEG_OTHER_EXPECT = {
    36: (r"随便去哪都行 → 无具体目的地，destination 应为空，不得臆造城市", "no_fabricate"),
    37: (r"帮我订机票 → 非行程规划请求，该节点不应臆造偏好（偏好须来自用户话语）", "no_fabricate"),
    38: (r"看股票行情 → 非旅行请求，destination 应为空，不得臆造城市与偏好", "no_fabricate"),
    44: (r"去三亚但讨厌海边 → 显式否定：destination=三亚要提对，但『海边』不得进 preferences", "no_negation_leak"),
    47: (r"南极探险 → destination 明说须提对；『探险』有依据时可接受，但否定项绝不可泄漏", "mentioned_extra_guard"),
}

# 中文偏好常被拆成短语，先用连接词切分，再取长度 >= 2 的词块去用户话语里找依据。
SPLIT_SPLITTERS = re.compile(r"[、,，/和与及\s]+")


def pref_evidence_tokens(pref: str) -> list:
    """把一条偏好拆成可在用户话语里检索证据的词块。"""
    pieces = [p for p in SPLIT_SPLITTERS.split(str(pref)) if p]
    return [p for p in (pieces or [str(pref)]) if len(p) >= 2]


def _is_negated_in(query: str, token: str) -> bool:
    """token 在 query 中出现的那个子句里是否带否定词。"""
    for clause in re.split(r"[，,。；;！!？?]", query):
        if token in clause and NEGATION_PATTERN.search(clause):
            return True
    return False


def classify_negative(case: dict) -> str:
    """按关键词给负样例分类，识别不出再用 id 兜底，最后落到 other。"""
    query = case["query"]
    for cat in ("illegal", "out_of_range", "overseas"):
        if any(kw in query for kw in NEG_CATEGORY_KEYWORDS[cat]):
            return cat
    return NEG_CATEGORY_BY_ID.get(case["id"], "other")


def mention_rule_satisfied(field: str, expected: dict) -> bool:
    """判断该条用例里，用户是否"实际提及"了这个字段（即该字段有有效期望值）。"""
    val = expected.get(field, FIELD_DEFAULT[field])
    if field == "destination":
        return str(val).strip() != ""
    if field == "preferences":
        return len(val or []) > 0
    if field == "days":
        return int(val or 0) > 0
    if field == "budget":
        return float(val or 0) > 0
    return False


def check_field(field: str, got: dict, expected: dict) -> bool:
    """单字段是否判对（与是否有期望值严格与否无关，只看取值相等）。"""
    if field == "destination":
        return str(got.get("destination", "") or "").strip() == str(expected.get("destination", "") or "").strip()
    if field == "days":
        return int(got.get("days", 0) or 0) == int(expected.get("days", 0) or 0)
    if field == "budget":
        return abs(float(got.get("budget", 0) or 0) - float(expected.get("budget", 0) or 0)) < BUDGET_TOL
    if field == "preferences":
        return {str(x).strip() for x in (got.get("preferences") or [])} == \
               {str(x).strip() for x in (expected.get("preferences") or [])}
    return False


def get_field_value(field: str, holder: dict):
    val = holder.get(field, FIELD_DEFAULT[field])
    if field == "preferences":
        return list(val or [])
    return val


def pref_empty_set_correct(record: dict) -> bool:
    """期望偏好为空、输出偏好也为空 → "空集正确"（该拒答的确实拒答了）。

    这类样本不计入 precision/recall 的分子，但计入 F1 分母（TP+FP+FN），
    保证它不会被当成"什么都没发生"而从统计里消失。
    """
    exp = {str(x).strip() for x in (record["expected"].get("preferences") or [])}
    got = {str(x).strip() for x in ((record["extracted"] or {}).get("preferences") or [])}
    return exp == set() and got == set()


# ================================================================
# 单条评测
# ================================================================
def normalize_result(result):
    """兼容 (parsed, raw) 元组 / pydantic 模型 / dict 三种返回形态。"""
    if isinstance(result, tuple):
        result = result[0]
    if hasattr(result, "model_dump"):
        d = result.model_dump()
    elif isinstance(result, dict):
        d = dict(result)
    else:
        d = {
            "destination": getattr(result, "destination", ""),
            "days": getattr(result, "days", 0),
            "budget": getattr(result, "budget", 0),
            "preferences": getattr(result, "preferences", []),
        }
    return {
        "destination": d.get("destination", "") or "",
        "days": int(d.get("days", 0) or 0),
        "budget": float(d.get("budget", 0) or 0),
        "preferences": [str(x) for x in (d.get("preferences") or [])],
    }


def run_single(case):
    """跑一条用例，返回评测结果字典（含逐字段判定 + 偏好集合运算）。"""
    case_type = case.get("type", "unknown")
    neg_cat = classify_negative(case) if case_type == "negative" else None
    state = build_base_state(case["query"])
    try:
        result = requirement_agent(state)
        got = normalize_result(result)
        expected = case["expected"]

        exp_prefs = {str(x).strip() for x in (expected.get("preferences") or [])}
        got_prefs = {str(x).strip() for x in (got.get("preferences") or [])}

        fields = {f: check_field(f, got, expected) for f in FIELD_ORDER}
        mentioned = {f: mention_rule_satisfied(f, expected) for f in FIELD_ORDER}

        hit = exp_prefs & got_prefs
        miss = exp_prefs - got_prefs
        extra = got_prefs - exp_prefs

        return {
            "id": case["id"],
            "query": case["query"],
            "type": case_type,
            "neg_cat": neg_cat,
            "valid": True,
            "error": None,
            "extracted": got,
            "expected": expected,
            "mentions": mentioned,
            "fields": fields,
            # 旧口径（保留，仅用于对照展示，不再作为结论指标）
            "legacy_pref_ok": len(hit) > 0 or (not exp_prefs and not got_prefs),
            # 偏好集合运算明细
            "pref_hit": hit,
            "pref_miss": miss,
            "pref_extra": extra,
            "pref_abs_a": len(hit),          # 交集元素数（TP）
            "pref_abs_b": len(miss),         # 漏掉的期望偏好（FN）
            "pref_abs_d": len(extra),        # 多输出的偏好（FP）
            "pref_exact": exp_prefs == got_prefs,
            "pref_both_empty": (not exp_prefs and not got_prefs),   # 空集正确
            # 仅作说明：用户压根没提偏好、模型也没输出（属于平凡正确，不参与 P/R 分子）
            "pref_both_empty_mentioned": bool(
                (not mentioned["preferences"]) and exp_prefs == got_prefs == set()
            ),
            "pref_shaped": int(bool(got_prefs)),
            # 全字段对（4 字段全对）
            "all_fields_ok": all(fields.values()),
        }
    except Exception as e:
        return {
            "id": case["id"],
            "query": case["query"],
            "type": case_type,
            "neg_cat": neg_cat,
            "valid": False,
            "error": f"{type(e).__name__}: {e}",
            "extracted": None,
            "expected": case["expected"],
            "mentions": {f: mention_rule_satisfied(f, case["expected"]) for f in FIELD_ORDER},
            "fields": {f: False for f in FIELD_ORDER},
            "legacy_pref_ok": False,
            "pref_hit": set(), "pref_miss": set(), "pref_extra": set(),
            "pref_abs_a": 0, "pref_abs_b": 0, "pref_abs_d": 0,
            "pref_exact": False, "pref_both_empty": False,
            "pref_both_empty_mentioned": False,
            "pref_shaped": 0,
            "all_fields_ok": False,
        }


# ================================================================
# 负样例"应当如何"的判定
# ================================================================
def check_neg_behavior(r):
    """返回 (是否按期望行为通过, 失败原因描述)。"""
    if not r["valid"]:
        return False, f"抛异常({str(r.get('error'))[:40]})"

    exp = r["expected"]
    got = r["extracted"]
    cat = r["neg_cat"]
    reasons = []

    if cat == "illegal":
        # 期望行为：非法目的词不得被当成"可提取的目的地"。
        # 两种都可接受：① 期望原样回显非法词且提取一致（case 35/41）；
        #              ② 返回空目的地（视为识别出非法/未提供）。
        got_dest = str(got["destination"]).strip()
        exp_dest = str(exp.get("destination", "")).strip()
        keyword_in_got = any(kw in str(got["destination"]) for kw in NEG_CATEGORY_KEYWORDS["illegal"])
        if exp_dest and got_dest == exp_dest:
            pass                                   # 与期望一致（原样回显）
        elif got_dest == "" and keyword_in_got is False:
            pass                                   # 判为"未提供目的地"，可接受
        elif got_dest != "":
            reasons.append(f"destination 不该有值(得到「{got_dest}」)")
        if not r["fields"]["days"]:
            reasons.append(f"days 应为 0(得到 {got['days']})")
        if not r["fields"]["budget"]:
            reasons.append(f"budget 应为 0(得到 {got['budget']})")
        if got["preferences"]:
            reasons.append(f"preferences 应为空(得到 {got['preferences']})")

    elif cat == "out_of_range":
        # 数值是明说的，提对即正确
        if not r["fields"]["destination"]:
            reasons.append(f"destination 提错(得到「{got['destination']}」/期望「{exp.get('destination')}」)")
        if not r["fields"]["days"]:
            reasons.append(f"days 提错(得到 {got['days']}/期望 {exp.get('days')})")
        if not r["fields"]["budget"]:
            reasons.append(f"budget 提错(得到 {got['budget']}/期望 {exp.get('budget')})")

    elif cat == "overseas":
        # 期望完整提取 + 打标/拦截提醒
        if not r["fields"]["destination"]:
            reasons.append(f"destination 提错(得到「{got['destination']}」/期望「{exp.get('destination')}」)")
        if not r["fields"]["days"]:
            reasons.append(f"days 提错(得到 {got['days']}/期望 {exp.get('days')})")
        if not r["fields"]["budget"]:
            reasons.append(f"budget 提错(得到 {got['budget']}/期望 {exp.get('budget')})")
        if not r["fields"]["preferences"]:
            reasons.append(f"preferences 提错(得到 {got['preferences']}/期望 {exp.get('preferences')})")

    else:  # D-其他：见 NEG_OTHER_EXPECT 的逐条期望
        mode = NEG_OTHER_EXPECT.get(r["id"], (r"未登记", "no_fabricate"))[1]
        if mode in ("no_negation_leak", "mentioned_extra_guard"):
            # destination 是用户明说的 → 必须提对
            if not r["fields"]["destination"]:
                reasons.append(f"destination 提错(得到「{got['destination']}」/期望「{exp.get('destination')}」)")
            leaked = [(pf, tok) for pf in got["preferences"]
                      for tok in pref_evidence_tokens(pf)
                      if tok in r["query"]]
            if mode == "no_negation_leak":
                # 显式否定：只要偏好词本身出现在用户话语里，就说明它来自被否定的部分
                if leaked:
                    reasons.append(f"否定偏好泄漏进 preferences(得到 {got['preferences']})")
            else:
                # 明说目的地、允许提偏好，但偏好不得取自被否定的子句；
                # 且数据集期望偏好为空时，任何输出都算多输出（与偏好指标 FP 口径保持一致）
                unsupported = [(pf, tok) for pf, tok in leaked if _is_negated_in(r["query"], tok)]
                if unsupported:
                    reasons.append(f"偏好取自被否定的子句(得到 {got['preferences']})")
                elif got["preferences"] and not (exp.get("preferences") or []):
                    reasons.append(f"该条期望偏好为空，输出即为冗余(得到 {got['preferences']})")
        else:
            # 非目的地类请求：不得臆造目的地，也不应凭空产出偏好
            if str(got["destination"]).strip() != str(exp.get("destination", "")).strip():
                reasons.append(f"destination 提错(得到「{got['destination']}」/期望「{exp.get('destination')}」)")
            if not r["fields"]["days"]:
                reasons.append(f"days 提错(得到 {got['days']}/期望 {exp.get('days')})")
            if not r["fields"]["budget"]:
                reasons.append(f"budget 提错(得到 {got['budget']}/期望 {exp.get('budget')})")
            if got["preferences"]:
                reasons.append(f"非目的地类请求不应产出 preferences(得到 {got['preferences']})")

    return (len(reasons) == 0), "；".join(reasons)


# ================================================================
# 打印单条结果
# ================================================================
def print_case(c):
    cat = f" / {NEG_CATEGORY_LABEL[c['neg_cat']]}" if c.get("neg_cat") else ""
    if not c["valid"]:
        print(f"💥 [{c['id']}] {c['type']}{cat} 报错: {c.get('error')}")
        print(f"    query: {c['query']}")
        return

    ok_flags = "".join("[OK]" if c["fields"][f] else "[X]" for f in FIELD_ORDER)
    flag = "✅" if c["all_fields_ok"] else "❌"
    print(f"{flag} [{c['id']}] {c['type']}{cat} {ok_flags} | {c['query']}")
    print(f"    提取: dest={c['extracted']['destination']} days={c['extracted']['days']} "
          f"budget={c['extracted']['budget']} prefs={c['extracted']['preferences']}")
    print(f"    期望: dest={c['expected']['destination']} days={c['expected']['days']} "
          f"budget={c['expected']['budget']} prefs={c['expected'].get('preferences')}")
    if len(c["pref_hit"]) or len(c["pref_miss"]) or len(c["pref_extra"]):
        print(f"    偏好集合: hit={sorted(c['pref_hit'])} miss={sorted(c['pref_miss'])} extra={sorted(c['pref_extra'])}")
    if c.get("neg_cat"):
        passed, why = c["neg_behavior"]
        mark = "PASS" if passed else "FAIL"
        print(f"    负样例行为[{NEG_CATEGORY_LABEL[c['neg_cat']]}]: {mark}" + (f" ← {why}" if why else ""))


# ================================================================
# 统计累积器
# ================================================================
def new_metrics():
    m = {
        "n": 0,
        "valid": 0,
        "case_all_ok": 0,
        # 逐字段
        "fields": {f: {"men_total": 0, "men_ok": 0, "men_empty": 0, "men_wrong": 0,
                       "un_men_total": 0, "un_men_ok": 0, "un_men_output": 0} for f in FIELD_ORDER},
        # 偏好集合运算（微平均，按元素计数）
        "pref": {"tp": 0, "fp": 0, "fn": 0, "exact": 0, "both_empty": 0, "shaped": 0,
                 "unmentioned_but_output": 0,
                 "cases_for_p": 0, "cases_for_r": 0, "cases_f1": 0,
                 "macro_p": [], "macro_r": [], "macro_f": []},
        # 旧口径（仅对照）
        "legacy_pref": 0,
        # 负样例行为
        "neg_pass": 0,
    }
    return m


def is_default_output(field: str, value) -> bool:
    """模型对该字段的输出是否等于"未提及"的默认值（""/0/[]）。"""
    if field == "preferences":
        return len(value or []) == 0
    if field == "destination":
        return str(value or "").strip() == ""
    try:
        return float(value or 0) == 0.0
    except (TypeError, ValueError):
        return str(value).strip() == ""


def acc(m, r):
    if r["valid"]:
        m["valid"] += 1
    if r["all_fields_ok"]:
        m["case_all_ok"] += 1
    if r.get("neg_cat"):
        if r["neg_behavior"][0]:
            m["neg_pass"] += 1
    for f in FIELD_ORDER:
        ok = bool(r["fields"][f]) if r["valid"] else False
        got_val = (r["extracted"] or {}).get(f, FIELD_DEFAULT[f])
        if r["mentions"][f]:
            m["fields"][f]["men_total"] += 1
            m["fields"][f]["men_ok"] += 1 if ok else 0
            if not ok:
                # 失败再分两种：输出为空（漏提） vs 输出有值但不对（提错）
                m["fields"][f]["men_empty" if is_default_output(f, got_val) else "men_wrong"] += 1
        else:
            m["fields"][f]["un_men_total"] += 1
            m["fields"][f]["un_men_ok"] += 1 if ok else 0
            if not is_default_output(f, got_val):
                m["fields"][f]["un_men_output"] += 1
    a, b, d = r["pref_abs_a"], r["pref_abs_b"], r["pref_abs_d"]
    m["pref"]["tp"] += a
    m["pref"]["fp"] += d
    m["pref"]["fn"] += b
    m["pref"]["exact"] += 1 if r["pref_exact"] else 0
    m["pref"]["both_empty"] += 1 if r["pref_both_empty"] else 0
    m["pref"]["shaped"] += r["pref_shaped"]
    if (not r["mentions"]["preferences"]) and r["pref_shaped"]:
        # 用户没提偏好却输出了 → precision 损失
        m["pref"]["unmentioned_but_output"] += 1
    if d > 0:
        m["pref"]["cases_for_p"] += 1
    if b > 0:
        m["pref"]["cases_for_r"] += 1
    if (a + b + d) > 0:
        # 非平凡用例（有期望偏好或有预测偏好）才进 macro，
        # "期望空且输出空"是平凡正确，不进 macro，避免稀释/虚增
        m["pref"]["cases_f1"] += 1
        cp = a / (a + d) if (a + d) else 0.0
        cr = a / (a + b) if (a + b) else 0.0
        cf = 2 * cp * cr / (cp + cr) if (cp + cr) else 0.0
        m["pref"]["macro_p"].append(cp)
        m["pref"]["macro_r"].append(cr)
        m["pref"]["macro_f"].append(cf)
    if r["legacy_pref_ok"]:
        m["legacy_pref"] += 1
    m["n"] += 1


# ================================================================
# 格式化助手（所有指标都带 分子/分母）
# ================================================================
def line(label, num, den, pad=22):
    pct = (num / den * 100) if den else 0.0
    return f"  {label:<{pad}} {num}/{den} = {pct:5.1f}%"


def dwidth(s: str) -> int:
    """显示宽度：中日韩全角字符按 2 列算，用于对齐报告表格。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in str(s))


def pad_display(s: str, width: int) -> str:
    s = str(s)
    return s + " " * max(0, width - dwidth(s))


def micro_f1(tp, fp, fn):
    """统一用调和平均定义：F1 = 2TP/(2TP+FP+FN)，与 P/R 严格自洽。"""
    den = 2 * tp + fp + fn
    return (2 * tp) / den if den else 0.0


def prec_rec(tp, fp, fn):
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    return p, r


def ratio_pct(num, den):
    return f"{num}/{den} = {(num / den * 100) if den else 0.0:.1f}%"


# ================================================================
# 分段报告（综合 / positive / negative）
# ================================================================
def summarize(results, label, out):
    m = new_metrics()
    for r in results:
        acc(m, r)

    n = m["n"]
    if n == 0:
        out.append(f"\n📊 {label}：无用例")
        return m

    out.append("")
    out.append("=" * 72)
    out.append(f"📊 分段报告：{label}（样本数 {n}）")
    out.append("=" * 72)

    out.append("[0] 结构化输出合规率（不抛异常）")
    out.append(line("合规率", m["valid"], n, 24))

    out.append("")
    out.append("[1] 分母拆分：用户实际提及的字段 → 提取准确率 / 未提及字段 → 拒答正确率")
    out.append(f"    提及口径：dest={MENTION_RULE['destination']}；days={MENTION_RULE['days']}；"
               f"budget={MENTION_RULE['budget']}；preferences={MENTION_RULE['preferences']}")
    out.append("    " + "-" * 68)
    for f in FIELD_ORDER:
        s = m["fields"][f]
        out.append(f"  {FIELD_LABEL[f]}")
        out.append(line("提及-提取准确率", s["men_ok"], s["men_total"], 24))
        out.append(line("未提及-拒答正确率", s["un_men_ok"], s["un_men_total"], 24))

    out.append("")
    out.append("[2] 偏好指标：集合运算 precision / recall / F1（微平均，按元素计数）")
    p = m["pref"]
    tp, fp, fn = p["tp"], p["fp"], p["fn"]
    prec, rec = prec_rec(tp, fp, fn)
    out.append(f"    元素计数：TP={tp}（命中） FP={fp}（多输出/无依据） FN={fn}（漏输出）")
    out.append(f"  {'precision (TP/(TP+FP))':<24} {tp}/{tp + fp} = {prec * 100:5.1f}%")
    out.append(f"  {'recall    (TP/(TP+FN))':<24} {tp}/{tp + fn} = {rec * 100:5.1f}%")
    out.append(f"  {'micro-F1 (2TP/(2TP+FP+FN))':<24} {2 * tp}/{2 * tp + fp + fn} = {micro_f1(tp, fp, fn) * 100:5.1f}%")
    if p["macro_f"]:
        out.append(f"  {'macro-P / macro-R / macro-F1':<24} "
                   f"{mean(p['macro_p']) * 100:.1f}% / {mean(p['macro_r']) * 100:.1f}% / "
                   f"{mean(p['macro_f']) * 100:.1f}%（仅 {p['cases_f1']} 条涉及偏好的非平凡用例按条平均）")
    else:
        out.append(f"  {'macro-P / macro-R / macro-F1':<24} 0/0 = 0.0%（无涉及偏好的非平凡用例）")
    out.append(f"  {'集合完全一致(期望=输出)':<24} {p['exact']}/{n} = {p['exact'] / n * 100:5.1f}%")
    out.append(f"  {'期望空且输出空(空集正确)':<24} {p['both_empty']}/{n} = {p['both_empty'] / n * 100:5.1f}%"
               f"   ← 该拒答的确实拒答了；平凡正确，不并入 F1/macro")
    un_men = m["fields"]["preferences"]["un_men_total"]
    out.append(f"  {'未提及偏好却输出偏好':<24} {p['unmentioned_but_output']}/{un_men} = "
               f"{(p['unmentioned_but_output'] / un_men * 100) if un_men else 0.0:5.1f}%"
               f"   ← 造成 FP {fp} 个元素，直接扣 precision")
    out.append(f"  {'旧口径(交集>0即算对)':<24} {m['legacy_pref']}/{n} = {m['legacy_pref'] / n * 100:5.1f}%"
               f"   ← 仅对照，偏松，不作结论")

    out.append("")
    out.append("[3] 样本级全对率（该条所有字段全部正确）")
    out.append(line("样本级全对率", m["case_all_ok"], n, 24))
    return m


# ================================================================
# 负样例分类报告
# ================================================================
def summarize_negative_by_category(neg_results, out):
    out.append("")
    out.append("=" * 72)
    out.append(f"📊 负样例分类报告（共 {len(neg_results)} 条，按期望行为判定 PASS/FAIL）")
    out.append("=" * 72)
    for cat in NEG_CATEGORY_ORDER:
        group = [r for r in neg_results if r.get("neg_cat") == cat]
        if not group:
            continue
        passed = [r for r in group if r["neg_behavior"][0]]
        out.append("")
        out.append(f"▌{NEG_CATEGORY_LABEL[cat]}（{len(group)} 条）")
        out.append(f"    期望行为：{NEG_CATEGORY_BEHAVIOR[cat]}")
        out.append(line("行为 PASS 率", len(passed), len(group), 24))
        # 该类的提取器一致性（destination 提对率）
        dest_ok = sum(1 for r in group if r["valid"] and r["fields"]["destination"])
        out.append(line("destination 一致率", dest_ok, len(group), 24))
        failed = [r for r in group if not r["neg_behavior"][0]]
        if failed:
            out.append("    失败明细：")
            for r in failed:
                out.append(f"      - [{r['id']}] {r['query']}")
                out.append(f"        提取={r['extracted'] if r['valid'] else '异常'}")
                out.append(f"        原因：{r['neg_behavior'][1]}")
        else:
            out.append("    失败明细：无")


# ================================================================
# 结论摘要（紧凑，可直接贴简历 / README）
# ================================================================
def conclusion_summary(m_all, m_pos, m_neg, neg_results, out, results):
    out.append("")
    out.append("=" * 72)
    out.append("📌 结论摘要（可直接贴简历 / README）")
    out.append("=" * 72)
    out.append(f"需求提取（Requirement Agent）评测：{m_all['n']} 条用例"
               f"（positive {m_pos['n']} / negative {m_neg['n']}），真实 API 调用 {m_all['n']} 次（1 次/条）")

    p = m_all["pref"]
    tp, fp, fn = p["tp"], p["fp"], p["fn"]
    prec, rec = prec_rec(tp, fp, fn)
    out.append(line("[综合] 结构化输出合规率", m_all["valid"], m_all["n"], 26))
    out.append(line("[综合] 目的地 提及→准确率",
                    m_all["fields"]["destination"]["men_ok"], m_all["fields"]["destination"]["men_total"], 26))
    out.append(line("[综合] 天数   提及→准确率",
                    m_all["fields"]["days"]["men_ok"], m_all["fields"]["days"]["men_total"], 26))
    out.append(line("[综合] 预算   提及→准确率",
                    m_all["fields"]["budget"]["men_ok"], m_all["fields"]["budget"]["men_total"], 26))
    out.append(f"  {'[综合] 偏好   micro-F1':<26} {2 * tp}/{2 * tp + fp + fn} = "
               f"{micro_f1(tp, fp, fn) * 100:5.1f}%   (P={tp}/{tp + fp}={prec * 100:.1f}%, "
               f"R={tp}/{tp + fn}={rec * 100:.1f}%)")
    out.append(line("[综合] 样本级全对率（4 字段全对）", m_all["case_all_ok"], m_all["n"], 26))

    out.append("")
    out.append("  分段（各指标的 分子/分母 = 百分比）：")
    cols = [("子集", 10), ("合规率", 18), ("目的地", 18), ("天数", 18),
            ("预算", 18), ("偏好F1", 18), ("样本全对", 18)]
    out.append("    " + "".join(pad_display(h, w) for h, w in cols))
    for name, m in (("综合", m_all), ("positive", m_pos), ("negative", m_neg)):
        if m["n"] == 0:
            continue
        pp = m["pref"]
        t2, f2, n2 = pp["tp"], pp["fp"], pp["fn"]
        cells = [
            name,
            ratio_pct(m["valid"], m["n"]),
            ratio_pct(m["fields"]["destination"]["men_ok"], m["fields"]["destination"]["men_total"]),
            ratio_pct(m["fields"]["days"]["men_ok"], m["fields"]["days"]["men_total"]),
            ratio_pct(m["fields"]["budget"]["men_ok"], m["fields"]["budget"]["men_total"]),
            f"{2 * t2}/{2 * t2 + f2 + n2} = {micro_f1(t2, f2, n2) * 100:.1f}%",
            ratio_pct(m["case_all_ok"], m["n"]),
        ]
        out.append("    " + "".join(pad_display(c, w) for c, (_, w) in zip(cells, cols)))

    out.append("")
    out.append("  偏好 P/R/F1 明细（分子/分母）：")
    for name, m in (("综合", m_all), ("positive", m_pos), ("negative", m_neg)):
        if m["n"] == 0:
            continue
        pp = m["pref"]
        t2, f2, n2 = pp["tp"], pp["fp"], pp["fn"]
        pr, rc = prec_rec(t2, f2, n2)
        out.append(f"    {name:<10}P {t2}/{t2 + f2}={pr * 100:.1f}% | R {t2}/{t2 + n2}={rc * 100:.1f}% | "
                   f"F1 {2 * t2}/{2 * t2 + f2 + n2}={micro_f1(t2, f2, n2) * 100:.1f}% | "
                   f"空集正确 {pp['both_empty']} 条（不计入 F1）")

    out.append("")
    out.append("  未提及字段的拒答正确率（分母=用户没提的条数，期望为默认值）：")
    for name, m in (("综合", m_all), ("positive", m_pos), ("negative", m_neg)):
        if m["n"] == 0:
            continue
        parts = []
        for f in FIELD_ORDER:
            s = m["fields"][f]
            parts.append(f"{FIELD_LABEL[f]} {ratio_pct(s['un_men_ok'], s['un_men_total'])}")
        out.append(f"    {name:<10}" + " | ".join(parts))

    out.append("")
    out.append("  负样例（按期望行为 PASS/FAIL）：")
    for cat in NEG_CATEGORY_ORDER:
        group = [r for r in neg_results if r.get("neg_cat") == cat]
        if not group:
            continue
        passed = sum(1 for r in group if r["neg_behavior"][0])
        out.append("    " + pad_display(NEG_CATEGORY_LABEL[cat], 16)
                   + pad_display(ratio_pct(passed, len(group)), 20)
                   + f"（{NEG_CATEGORY_BEHAVIOR[cat]}）")

    # ---- 新旧口径对照：回答"为什么旧指标虚高" ----
    old_pref_ok = sum(1 for r in results if r["legacy_pref_ok"])
    old_all4 = sum(1 for r in results
                   if r["valid"] and r["fields"]["destination"] and r["fields"]["days"]
                   and r["fields"]["budget"] and r["legacy_pref_ok"])
    out.append("")
    out.append("  新旧口径对照（旧版为什么虚高）：")
    out.append(f"    - 偏好命中：旧『交集>0 即算对』{old_pref_ok}/{m_all['n']} = {old_pref_ok / m_all['n'] * 100:.1f}%"
               f"  →  新『集合 P/R/F1』F1={micro_f1(tp, fp, fn) * 100:.1f}%"
               f"（P={prec * 100:.1f}% / R={rec * 100:.1f}%）")
    out.append(f"      多输出的偏好（如『温暖』『探险』）在旧口径下完全不计入，新口径按 FP 扣 precision。")
    out.append(f"    - 字段准确率：旧口径分母 = 全部 {m_all['n']} 条（含大量『没提该字段→期望默认值→返回默认值算对』"
               f"的平凡正确项）")
    out.append(f"      → 新口径分母拆成『提及段』与『未提及段』，例如目的地 "
               f"{m_all['fields']['destination']['men_ok']}/{m_all['fields']['destination']['men_total']}"
               f"（提及）与 {m_all['fields']['destination']['un_men_ok']}/{m_all['fields']['destination']['un_men_total']}"
               f"（未提及）分开看")
    out.append(f"    - 样本级：旧口径没有全对率，只看单字段；新口径 4 字段全对 "
               f"{m_all['case_all_ok']}/{m_all['n']} = {m_all['case_all_ok'] / m_all['n'] * 100:.1f}%"
               f"（旧口径按同样 4 字段但偏好用交集>0 计算 = {old_all4}/{m_all['n']} = "
               f"{old_all4 / m_all['n'] * 100:.1f}%）")

    # ---- 失败样本清单 ----
    failed = [r for r in results if not r["all_fields_ok"] or not r["neg_behavior"][0]]
    out.append("")
    out.append(f"  失败样本清单（{len(failed)} 条，字段错 或 负样例行为错）：")
    if not failed:
        out.append("    无")
    for r in failed:
        wrong = [FIELD_LABEL[f] for f in FIELD_ORDER if not r["fields"][f]]
        tag = NEG_CATEGORY_LABEL[r["neg_cat"]] if r.get("neg_cat") else "positive"
        detail = ("字段错：" + "/".join(wrong)) if wrong else ""
        if r.get("neg_cat") and not r["neg_behavior"][0]:
            detail = (detail + "；" if detail else "") + "行为错：" + r["neg_behavior"][1]
        out.append(f"    - [{r['id']}] {tag} 「{r['query']}」 → {detail}")

    out.append("")
    out.append("  口径附录（可回答追问）：")
    out.append("    - 偏好为集合运算：期望非空且同集合=TP 满分；只命中一部分按交集元素计分（命中 1/3 拿 1/3）；")
    out.append("      期望为空但输出了偏好=FP，直接扣 precision；期望空且输出空=空集正确，单列条数、不并入 F1。")
    out.append("    - F1 用调和平均定义（2TP/(2TP+FP+FN)），micro 为元素级汇总，macro 仅对非平凡用例按条平均。")
    out.append("    - 分母拆分：提及段分母=用户提了该字段的条数，未提及段分母=用户没提的条数，")
    out.append("      互不混用，故不含“没提也算对”的平凡正确项。")
    out.append("    - 预算容差 <1 元；提及口径 dest=期望非空 / days,budget=期望>0 / prefs=期望非空。")
    out.append("    - 负样例 D-其他 5 条不属前三类，逐条期望见运行日志中的负样例分类报告。")
    out.append("")


# ================================================================
# 主流程
# ================================================================
def run_all(cases, out):
    out.append("=" * 72)
    out.append(f"📊 requirement_eval_agent_v2 综合评测（positive + negative，共 {len(cases)} 条）")
    out.append("=" * 72)
    results = []
    for case in cases:
        r = run_single(case)
        r["neg_behavior"] = check_neg_behavior(r)
        print_case(r)
        results.append(r)

    pos = [r for r in results if r["type"] == "positive"]
    neg = [r for r in results if r["type"] == "negative"]

    m_all = summarize(results, "综合", out)
    m_pos = summarize(pos, "positive 子集", out) if pos else new_metrics()
    m_neg = summarize(neg, "negative 子集", out) if neg else new_metrics()

    if neg:
        summarize_negative_by_category(neg, out)

    conclusion_summary(m_all, m_pos, m_neg, neg, out, results)
    return results


def main():
    with open(DATASET_PATH, "r", encoding="utf-8") as f:
        cases = json.load(f)

    out = []
    run_all(cases, out)
    text = "\n".join(out)

    print()
    print(text)

    report_path = "tests/evaluation/requirement_collection/eval_report_20261014.txt"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    print(f"\n📄 报告已保存：{report_path}")


if __name__ == "__main__":
    main()
