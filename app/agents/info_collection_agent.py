"""InfoCollection Agent：真实外部信息收集（高德天气/景点/路线 + Tavily 联网补充）。

v2.0 的问题：天气是真的，但路线是**硬编码 Mock**——不管用户去哪都返回
"西湖 → 灵隐寺 → 宋城"，只要现场演示输入"去北京玩 3 天"就会当场露馅。

v2.1 改为三路真实数据：
    1) 天气  —— 高德天气预报（未来 4 天），按用户出发日期对齐
    2) 景点  —— 高德 POI 检索，拿到真实景点名称与坐标，并按用户偏好追加一次检索
    3) 路线  —— 用真实坐标计算驾车距离/时长，串成当日动线

三层保护：缓存（同城重复查询直接命中）→ 重试（指数退避）→ 降级（失败给兜底文案，
绝不让外部依赖失败掐断整条规划链路）。

此外，若开启 ENABLE_LLM_TOOL_CALLING，模型会通过 **Function Calling** 自主决定
还需要补充搜索什么（门票预约、当地活动、交通管制等），调用次数受配置上限约束：
这是"确定性编排 + LLM 自主选工具"的混合设计——核心链路可测可控，长尾需求交给模型。
"""
import time
from typing import Any, Dict, List, Tuple

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from app.config import settings
from app.graph.state import AgentState
from app.models.llm_config import get_llm
from app.tools import amap
from app.tools.registry import LLM_TOOLS, TOOL_LABELS
from app.tools.search import search_travel_guide
from app.utils.logger import setup_logger
from app.utils.resilience import with_fallback

logger = setup_logger("travel_planner.info_collection")


def _record(tool: str, ok: bool, detail: str = "") -> Dict[str, Any]:
    """统一的工具调用记录，写入 State.tool_results 供观测与前端展示。"""
    return {"tool": tool, "ok": ok, "detail": detail, "ts": round(time.time(), 3)}


# ================================================================
# 1. 天气
# ================================================================
def _collect_weather(destination: str, date_range: List[str], days: int) -> Tuple[str, Dict[str, Any]]:
    casts = with_fallback(amap.weather_forecast, [], destination, what=f"天气({destination})")
    if not casts:
        return (
            f"{destination}：天气获取失败，建议按季节常识准备衣物并携带雨具。",
            _record("高德天气", False, "降级为兜底文案"),
        )
    return amap.format_weather(casts, date_range or None), _record("高德天气", True, f"{len(casts)} 天预报")


# ================================================================
# 2. 景点（真实 POI）
# ================================================================
def _collect_attractions(
    destination: str, preferences: List[str]
) -> Tuple[List[Dict[str, Any]], str, List[Dict[str, Any]]]:
    records: List[Dict[str, Any]] = []

    pois = with_fallback(
        amap.search_attractions, [], destination, keywords="景点", limit=8, what=f"景点检索({destination})"
    )
    records.append(_record("高德景点检索", bool(pois), f"关键词=景点，命中 {len(pois)} 个"))

    # 用用户偏好追加一次检索（如"美食"→ 美食街、"博物馆"），合并去重
    if preferences:
        keyword = str(preferences[0])[:10]
        extra = with_fallback(
            amap.search_attractions, [], destination, keywords=keyword, limit=5, what=f"景点检索({keyword})"
        )
        records.append(_record("高德景点检索", bool(extra), f"关键词={keyword}，命中 {len(extra)} 个"))
        seen = {p["name"] for p in pois}
        pois += [p for p in extra if p["name"] not in seen]

    if not pois:
        return [], "未检索到候选景点（可让模型按通用知识规划）", records

    text = "\n".join(f"- {p['name']}（{p['type']}）{p['address']}".rstrip() for p in pois[:10])
    return pois, text, records


# ================================================================
# 3. 路线（真实驾车距离）
# ================================================================
def _collect_routes(attractions: List[Dict[str, Any]]) -> Tuple[str, Dict[str, Any]]:
    if len(attractions) < 2:
        return "候选景点不足 2 个，无法计算路线，建议按地图导航自行安排。", _record(
            "高德驾车路线", False, "景点不足 2 个"
        )

    text = amap.build_route_plan(attractions, max_legs=4)
    ok = "→" in text
    legs = text.count("\n") + 1 if ok else 0
    return text, _record("高德驾车路线", ok, f"计算 {legs} 段")


# ================================================================
# 4. LLM 自主补充检索（Function Calling）
# ================================================================
def _llm_supplementary_search(
    destination: str, days: int, preferences: List[str], known: str
) -> Tuple[str, List[Dict[str, Any]]]:
    """让模型自主决定补充搜索什么；调用次数受 LLM_TOOL_MAX_CALLS 限制。"""
    if not settings.ENABLE_LLM_TOOL_CALLING:
        return "", []

    try:
        llm_with_tools = get_llm(temperature=0.1).bind_tools(LLM_TOOLS)
    except Exception as exc:  # 部分兼容 OpenAI 协议的模型不支持工具绑定
        logger.warning(f"⚠️ 模型不支持工具绑定，跳过自主检索：{exc}")
        return "", [_record("LLM 工具调用", False, f"绑定失败：{str(exc)[:60]}")]

    system = f"""你是旅行信息收集助手。当前已掌握的信息：
{known}

请判断还需要补充哪些**实时**信息（例如门票是否需预约、近期天气变化、当地活动、
交通管制、季节限定景观）。规则：
1. 已有信息不要重复查询；
2. 最多调用 {settings.LLM_TOOL_MAX_CALLS} 次工具；
3. 若确实无需补充，直接回复"无需补充"，不要调用工具。"""

    messages: List[Any] = [
        SystemMessage(content=system),
        HumanMessage(content=f"目的地：{destination}；天数：{days}；用户偏好：{preferences or '无'}"),
    ]

    records: List[Dict[str, Any]] = []
    collected: List[str] = []
    tool_map = {t.name: t for t in LLM_TOOLS}

    for _ in range(settings.LLM_TOOL_MAX_CALLS):
        try:
            ai = llm_with_tools.invoke(messages)
        except Exception as exc:
            records.append(_record("LLM 工具调用", False, f"模型调用失败：{str(exc)[:60]}"))
            break

        calls = getattr(ai, "tool_calls", None) or []
        if not calls:
            logger.info("ℹ️ 模型判断无需补充检索")
            break

        messages.append(ai)
        for call in calls:
            name = call.get("name") or "unknown"
            args = call.get("args") or {}
            tool = tool_map.get(name)
            if tool is None:
                records.append(_record(name, False, "未知工具"))
                continue
            try:
                output = tool.invoke(args)
                ok = True
            except Exception as exc:
                output, ok = f"工具执行失败：{exc}", False
            label = TOOL_LABELS.get(name, name)
            records.append(_record(label, ok, f"参数 {str(args)[:70]}"))
            collected.append(f"[{label}] {str(output)[:400]}")
            messages.append(ToolMessage(content=str(output)[:1500], tool_call_id=call.get("id") or "call_0"))

    return "\n".join(collected), records


# ================================================================
# 节点入口
# ================================================================
def info_collection_agent(state: AgentState) -> dict:
    """收集外部信息：写入 weather_info / attractions_info / route_info / search_info / info_collected。"""
    destination = state.get("destination", "")
    days = state.get("days", 0)
    start_date = state.get("start_date", "")
    preferences = state.get("preferences", []) or []

    logger.info(f"▶ [InfoCollection] {destination} {days} 天（出发：{start_date or '未指定'}）")

    if not destination:
        logger.warning("⚠️ 目的地为空，跳过信息收集")
        return {
            "weather_info": "未指定目的地，无法查询天气",
            "attractions_info": "",
            "route_info": "",
            "search_info": "",
            "info_collected": True,
            "tool_results": [_record("信息收集", False, "目的地为空")],
        }

    date_range = amap.build_date_range(start_date, days)
    if date_range:
        logger.info(f"  目标日期：{date_range[0]} ~ {date_range[-1]}")

    records: List[Dict[str, Any]] = []

    weather_text, rec = _collect_weather(destination, date_range, days)
    records.append(rec)

    attractions, attractions_text, recs = _collect_attractions(destination, preferences)
    records.extend(recs)

    route_text, rec = _collect_routes(attractions)
    records.append(rec)

    # 交给模型判断还缺什么实时信息
    known = (
        f"天气：{weather_text[:300]}\n"
        f"候选景点：{attractions_text[:400]}\n"
        f"路线：{route_text[:300]}"
    )
    search_text, recs = _llm_supplementary_search(destination, days, preferences, known)
    records.extend(recs)

    # 兜底：模型未补充或调用失败时，做一次确定性的攻略检索，保证 search_info 有内容
    if not search_text and settings.TAVILY_API_KEY:
        search_text = with_fallback(
            search_travel_guide, "", destination, days, preferences, what="Tavily 攻略搜索"
        )
        records.append(_record("Tavily 攻略搜索", bool(search_text), "确定性兜底"))

    ok_count = sum(1 for r in records if r["ok"])
    logger.info(f"✅ [InfoCollection] 完成，工具调用 {ok_count}/{len(records)} 成功")

    return {
        "weather_info": weather_text,
        "attractions_info": attractions_text,
        "route_info": route_text,
        "search_info": search_text,
        "info_collected": True,
        "tool_results": records,
        "messages": [
            {"role": "assistant", "content": f"[信息收集] {destination} 天气/景点/路线/联网信息已就绪"}
        ],
    }
