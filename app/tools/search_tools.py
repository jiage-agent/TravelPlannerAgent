"""搜索工具：查真实景点/门票信息"""
from langchain_core.tools import tool
from pydantic import BaseModel, Field
from tavily import TavilyClient
from app.utils.logger import setup_logger
from app.config import settings
import os

logger = setup_logger("travel_planner.tools.search")

_client = None


def _get_client():
    """懒加载 Tavily Client（避免启动时初始化）"""
    global _client
    if _client is None:
        api_key = os.getenv("TAVILY_API_KEY")
        if not api_key:
            raise RuntimeError("请设置 TAVILY_API_KEY")
        _client = TavilyClient(api_key=api_key)
    return _client


class SearchAttractionsInput(BaseModel):
    """工具入参（Pydantic 强约束，防 LLM 幻觉参数）"""
    city: str = Field(description="城市名，如：成都")
    keywords: str = Field(default="", description="关键词，如：安静 自然 免费")


@tool(args_schema=SearchAttractionsInput)
def search_attractions(city: str, keywords: str = "") -> str:
    """搜索指定城市的景点推荐信息。

    当需要了解某城市有哪些景点、有什么特色、是否适合特定偏好时使用。
    """
    query = f"{city} {keywords} 景点推荐 攻略".strip()
    logger.info(f"🔍 搜索工具调用 | query={query!r}")

    try:
        client = _get_client()
        response = client.search(query=query, max_results=5)
        results = response.get("results", [])

        if not results:
            logger.warning(f"搜索无结果 | query={query!r}")
            return f"未找到 {city} 的相关景点信息"

        snippets = []
        for r in results:
            title = r.get("title", "")
            content = r.get("content", "")[:200]
            snippets.append(f"- {title}: {content}")

        result_text = "\n".join(snippets)
        logger.info(f"✔ 搜索成功 | 结果数={len(results)}")
        return result_text

    except Exception as e:
        logger.exception(f"✘ 搜索失败 | query={query!r}")
        return f"搜索失败：{str(e)}（请基于常识规划）"


@tool
def get_ticket_price(attraction: str) -> str:
    """查询景点门票价格。

    需要知道某景点是否需要门票、价格多少时使用。
    """
    logger.info(f"🎫 查门票 | attraction={attraction!r}")

    try:
        client = _get_client()
        response = client.search(
            query=f"{attraction} 门票价格 2026",
            max_results=3,
        )
        results = response.get("results", [])
        if not results:
            return f"未找到 {attraction} 的门票信息"
        return results[0].get("content", "")[:200]

    except Exception as e:
        logger.exception(f"✘ 查门票失败 | attraction={attraction!r}")
        return f"门票查询失败：{str(e)}"


# 供 Agent 使用的工具列表
SEARCH_TOOLS = [search_attractions, get_ticket_price]