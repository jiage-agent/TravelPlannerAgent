"""上下文预算管理：token 估算、分块截断、结构化历史摘要。

为什么需要（Agent 落地的核心工程问题）：
多轮对话里，消息历史 + 工具返回会随轮次线性膨胀，最终导致三个后果：
上下文撞上模型上限、token 成本失控、首字延迟上升。v2.0 的 State 里有
`messages` 字段却从未写入，等于完全没有上下文管理。

这里的策略（刻意选确定性方案，而不是"再调一次 LLM 去摘要"）：
1. **保守的 token 估算**：不引入 tiktoken 依赖，中文按 1 字 ≈ 1 token、
   英文按 4 字符 ≈ 1 token 估算，宁高不低；
2. **分块预算分配**：把提示词拆成若干块，按优先级从高到低分配预算，
   低优先级块先被截断（例如历史摘要可以先砍，用户需求和天气必须完整）；
3. **结构化历史摘要**：每轮压成一行结构化记录（目的地/天数/预算/结果/人工反馈），
   零 LLM 成本、可复现、可单测——比让模型自由发挥的摘要更可控。
"""
from typing import Dict, Iterable, List

# 块优先级：数字越小越重要，预算不足时最后被截断
BLOCK_PRIORITY = {
    "user_needs": 0,
    "weather": 1,
    "attractions": 2,
    "route": 3,
    "search": 4,
    "history": 5,
}


def estimate_tokens(text: str) -> int:
    """保守估算 token 数：CJK 按 1 token/字，其他按 1 token/4 字符。"""
    if not text:
        return 0
    cjk = sum(1 for ch in text if ord(ch) > 0x2E80)
    other = len(text) - cjk
    return cjk + (other + 3) // 4


def truncate_to_tokens(text: str, limit: int) -> str:
    """按 token 上限截断文本，保留头部并加省略标记。"""
    if limit <= 0:
        return ""
    if estimate_tokens(text) <= limit:
        return text

    cjk_budget = limit
    out_chars: List[str] = []
    used = 0
    for ch in text:
        cost = 1 if ord(ch) > 0x2E80 else 0.25
        if used + cost > cjk_budget:
            break
        out_chars.append(ch)
        used += cost
    return "".join(out_chars).rstrip() + " …（已按上下文预算截断）"


def assemble_context(blocks: Dict[str, str], budget: int) -> Dict[str, str]:
    """按优先级把预算分配给各块，返回截断后的块。

    budget 为总 token 上限；优先满足高优先级块，剩余预算逐级下传。
    """
    ordered = sorted(blocks.items(), key=lambda kv: BLOCK_PRIORITY.get(kv[0], 99))
    remaining = budget
    result: Dict[str, str] = {}
    for name, text in ordered:
        if not text:
            result[name] = ""
            continue
        size = estimate_tokens(text)
        if size <= remaining:
            result[name] = text
            remaining -= size
        else:
            result[name] = truncate_to_tokens(text, max(remaining, 0))
            remaining = 0
    return result


def summarize_turn(
    turn: int,
    user_query: str,
    destination: str = "",
    days: int = 0,
    budget: float = 0.0,
    plan_days: int = 0,
    total_cost: float = 0.0,
    human_feedback: str = "",
) -> str:
    """把一轮对话压成一行结构化记录（写进 State.history_summary）。"""
    parts = [f"第{turn}轮: 用户「{truncate_to_tokens(user_query, 40)}」"]
    if destination:
        parts.append(f"目的地={destination}")
    if days:
        parts.append(f"天数={days}")
    if budget:
        parts.append(f"预算={budget:g}元")
    if plan_days:
        parts.append(f"产出={plan_days}天行程")
    if total_cost:
        parts.append(f"预估花费={total_cost:g}元")
    if human_feedback:
        parts.append(f"人工反馈「{truncate_to_tokens(human_feedback, 40)}」")
    return " | ".join(parts)


def format_history(summary: str, recent_messages: Iterable[str], limit: int = 12) -> str:
    """组装历史块：结构化摘要 + 最近若干条消息（受行数上限约束）。"""
    lines: List[str] = []
    if summary:
        lines.append("【历史轮次摘要】")
        lines.extend([ln for ln in summary.splitlines() if ln.strip()][-6:])
    recent = [m for m in recent_messages if m and m.strip()]
    if recent:
        lines.append("【最近对话】")
        lines.extend(recent[-limit:])
    return "\n".join(lines)
