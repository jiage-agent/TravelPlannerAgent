"""Tavily 联网搜索工具。

v2.0 里装了 `tavily-python` 却没有任何调用，这里把它真正接进链路：
InfoCollection 节点用搜索结果补充「实时攻略 / 门票 / 交通 / 当地活动」，
这些恰恰是静态知识容易过时、而大模型参数记忆不可靠的部分。
"""
from typing import Any, Dict, List

from app.config import settings
from app.tools import cache
from app.utils.logger import setup_logger
from app.utils.resilience import retry_call

logger = setup_logger("travel_planner.search")


def _client():
    from tavily import TavilyClient  # 延迟导入：未配置 key 时不影响其他功能

    if not settings.TAVILY_API_KEY:
        raise RuntimeError("TAVILY_API_KEY 未配置")
    return TavilyClient(api_key=settings.TAVILY_API_KEY)


def web_search(query: str, max_results: int = 3) -> List[Dict[str, Any]]:
    """原始搜索：返回 [{title, url, content}]，带缓存与重试。"""

    def _produce() -> List[Dict[str, Any]]:
        resp = retry_call(
            lambda: _client().search(query=query, max_results=max_results, search_depth="basic"),
            what=f"Tavily 搜索({query[:20]})",
        )
        results = resp.get("results") or []
        out = []
        for r in results[:max_results]:
            out.append(
                {
                    "title": r.get("title") or "",
                    "url": r.get("url") or "",
                    "content": (r.get("content") or "").strip(),
                    "score": r.get("score"),
                }
            )
        return out

    return cache.cached_call(
        "tavily.search", settings.CACHE_TTL_SEARCH, _produce, query=query, max_results=max_results
    )


def search_travel_guide(destination: str, days: int, preferences: List[str], extra: str = "") -> str:
    """把搜索结果的标题 + 摘要整理成提示词友好的文本块。"""
    if not destination:
        return ""

    pref_text = "、".join(preferences) if preferences else "无特别偏好"
    query = f"{destination} {days}天 行程攻略 必去景点 门票 交通 {pref_text} {extra}".strip()

    try:
        results = web_search(query, max_results=settings.SEARCH_MAX_RESULTS)
    except Exception as exc:
        logger.warning(f"⚠️ Tavily 搜索失败（降级为无搜索结果）：{exc}")
        return ""

    if not results:
        return ""

    lines: List[str] = []
    for i, r in enumerate(results, 1):
        snippet = r["content"][:220].replace("\n", " ")
        lines.append(f"{i}. {r['title']}：{snippet}")
    return "\n".join(lines)
