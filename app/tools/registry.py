"""把内部工具包装成 LangChain Tool，供大模型通过 Function Calling 自主调用。

架构取舍（这一点面试常问）：
- 核心链路（天气 / 景点 / 路线）走**确定性编排**：可单测、可复现、成本固定、演示不翻车；
- 补充信息（实时攻略、活动、门票）交给**大模型自主选工具**：这类需求事先无法穷举查询词，
  让模型根据用户偏好自己决定搜什么，比写死模板更灵活；
- 两者都受同一层「重试 + 缓存 + 降级」保护，模型选错工具也不会让链路崩掉。
"""
from typing import List

from langchain_core.tools import tool

from app.tools.amap import format_weather, search_attractions, weather_forecast
from app.tools.search import search_travel_guide


@tool
def search_attractions_tool(city: str, keywords: str = "景点") -> str:
    """按城市检索真实景点（高德 POI）。keywords 可换成「博物馆」「美食街」等具体类别。

    Args:
        city: 城市名，例如「杭州」
        keywords: 检索关键词，默认「景点」
    """
    pois = search_attractions(city, keywords=keywords, limit=8)
    if not pois:
        return f"未检索到 {city} 的「{keywords}」相关地点"
    return "\n".join(f"- {p['name']}（{p['type']}）{p['address']}" for p in pois)


@tool
def get_city_weather_tool(city: str) -> str:
    """查询城市未来 4 天天气预报（高德）。

    Args:
        city: 城市名，例如「杭州」
    """
    return format_weather(weather_forecast(city))


@tool
def search_travel_guide_tool(destination: str, days: int, focus: str = "") -> str:
    """联网搜索目的地的实时旅行信息（攻略、门票、交通、当地活动）。

    Args:
        destination: 目的地城市
        days: 出行天数
        focus: 需要重点了解的内容，例如「亲子」「门票预约」「夜间活动」
    """
    text = search_travel_guide(destination, days, [], extra=focus)
    return text or "未检索到相关实时信息"


# 暴露给大模型绑定的工具列表
LLM_TOOLS = [search_attractions_tool, get_city_weather_tool, search_travel_guide_tool]

# 工具名 → 可读标签（写进 tool_results，便于观测与前端展示）
TOOL_LABELS = {
    "search_attractions_tool": "景点检索",
    "get_city_weather_tool": "天气查询",
    "search_travel_guide_tool": "联网攻略搜索",
}


def tool_names() -> List[str]:
    return [t.name for t in LLM_TOOLS]
