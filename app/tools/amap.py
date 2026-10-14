"""高德地图 Web 服务封装：天气、景点检索（POI）、驾车路线、地理编码。

这一层替换了 v2.0 里硬编码的路线 Mock（原实现不管去哪都返回"西湖→灵隐寺"）。
三个能力都是真实外部数据，全部走「缓存 + 重试 + 降级」：
    cached_call(...) → 命中直接返回
    retry_call(...)  → 失败指数退避重试
    调用方 with_fallback → 重试耗尽后降级为兜底文案，不阻断整条规划链路
"""
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import httpx

from app.config import settings
from app.tools import cache
from app.utils.logger import setup_logger
from app.utils.resilience import retry_call

logger = setup_logger("travel_planner.amap")

BASE_URL = "https://restapi.amap.com"
TIMEOUT = 10.0


def _request(path: str, params: Dict[str, Any], what: str) -> Dict[str, Any]:
    """统一请求：注入 key、超时、状态码校验，并由 retry_call 负责重试。"""
    if not settings.AMAP_API_KEY:
        raise RuntimeError("AMAP_API_KEY 未配置")

    query = {**params, "key": settings.AMAP_API_KEY}

    def _do() -> Dict[str, Any]:
        with httpx.Client(timeout=TIMEOUT) as client:
            resp = client.get(BASE_URL + path, params=query)
            resp.raise_for_status()
            data = resp.json()
        # 高德用 status=1 表示成功；infocode=10003 之类是配额/权限问题
        if str(data.get("status")) != "1":
            raise RuntimeError(f"高德接口错误 info={data.get('info')} infocode={data.get('infocode')}")
        return data

    return retry_call(_do, what=what)


# ================================================================
# 地理编码：地名 → 经纬度 + adcode（天气接口需要 adcode）
# ================================================================
def geocode(address: str) -> Optional[Dict[str, Any]]:
    if not address:
        return None

    def _produce() -> Optional[Dict[str, Any]]:
        data = _request("/v3/geocode/geo", {"address": address}, what=f"地理编码({address})")
        items = data.get("geocodes") or []
        if not items:
            return None
        top = items[0]
        return {
            "formatted_address": top.get("formatted_address"),
            "province": top.get("province"),
            "city": top.get("city") or top.get("province"),
            "adcode": top.get("adcode"),
            "location": top.get("location"),  # "经度,纬度"
            "level": top.get("level"),
        }

    return cache.cached_call("amap.geocode", settings.CACHE_TTL_GEOCODE, _produce, address=address)


# ================================================================
# 天气预报：adcode → 未来 4 天预报
# ================================================================
def weather_forecast(city: str) -> List[Dict[str, Any]]:
    """返回 [{date, day_weather, night_weather, day_temp, night_temp, wind}]，最多 4 天。"""
    if not city:
        return []

    adcode = city
    if not city.isdigit():  # 传的是城市名 → 先解析成 adcode
        geo = geocode(city)
        if geo and geo.get("adcode"):
            adcode = geo["adcode"]

    def _produce() -> List[Dict[str, Any]]:
        data = _request(
            "/v3/weather/weatherInfo",
            {"city": adcode, "extensions": "all"},
            what=f"天气({city})",
        )
        forecasts = data.get("forecasts") or []
        if not forecasts:
            return []
        casts = forecasts[0].get("casts") or []
        return [
            {
                "date": c.get("date"),
                "week": c.get("week"),
                "day_weather": c.get("dayweather"),
                "night_weather": c.get("nightweather"),
                "day_temp": c.get("daytemp"),
                "night_temp": c.get("nighttemp"),
                "wind": f"{c.get('daywind', '')}{c.get('daypower', '')}级",
            }
            for c in casts
        ]

    return cache.cached_call("amap.weather", settings.CACHE_TTL_WEATHER, _produce, city=city)


# ================================================================
# 景点检索：POI 关键字搜索
# ================================================================
def search_attractions(city: str, keywords: str = "景点", limit: int = 8) -> List[Dict[str, Any]]:
    """返回 [{name, type, address, location, tel}]。"""
    if not city:
        return []

    def _produce() -> List[Dict[str, Any]]:
        data = _request(
            "/v3/place/text",
            {
                "keywords": keywords,
                "city": city,
                "citylimit": "true",
                "offset": str(min(limit, 25)),
                "page": "1",
                "extensions": "base",
            },
            what=f"景点检索({city})",
        )
        pois = data.get("pois") or []
        out = []
        for p in pois[:limit]:
            loc = p.get("location") or ""
            if not loc:
                continue  # 没有坐标的 POI 无法参与路线计算，丢弃
            out.append(
                {
                    "name": p.get("name"),
                    "type": (p.get("type") or "").split(";")[-1],
                    "address": p.get("address") if isinstance(p.get("address"), str) else "",
                    "location": loc,
                    "tel": p.get("tel") if isinstance(p.get("tel"), str) else "",
                }
            )
        return out

    return cache.cached_call(
        "amap.poi", settings.CACHE_TTL_POI, _produce, city=city, keywords=keywords, limit=limit
    )


# ================================================================
# 驾车路线：两点经纬度 → 距离/时长
# ================================================================
def driving_route(origin: str, destination: str) -> Optional[Dict[str, Any]]:
    if not origin or not destination:
        return None

    def _produce() -> Optional[Dict[str, Any]]:
        data = _request(
            "/v3/direction/driving",
            {"origin": origin, "destination": destination, "extensions": "base", "strategy": "0"},
            what="驾车路线",
        )
        paths = (data.get("route") or {}).get("paths") or []
        if not paths:
            return None
        p = paths[0]
        try:
            distance_m = int(p.get("distance") or 0)
            duration_s = int(p.get("duration") or 0)
        except (TypeError, ValueError):
            return None
        return {
            "distance_m": distance_m,
            "duration_s": duration_s,
            "tolls": p.get("tolls"),
            "taxi_cost": p.get("taxi_cost"),
        }

    return cache.cached_call(
        "amap.driving", settings.CACHE_TTL_ROUTE, _produce, origin=origin, destination=destination
    )


def build_route_plan(attractions: List[Dict[str, Any]], max_legs: int = 4) -> str:
    """把候选景点串成一条链，逐段算真实驾车距离/时长，生成可读文本。

    只算相邻段（N 个点算 N-1 段）并限制最大段数，避免 N² 次调用打爆配额。
    """
    if len(attractions) < 2:
        return "候选景点不足，无法计算路线"

    lines: List[str] = []
    legs = min(len(attractions) - 1, max_legs)
    for i in range(legs):
        a, b = attractions[i], attractions[i + 1]
        info = driving_route(a["location"], b["location"])
        if not info:
            continue
        km = info["distance_m"] / 1000
        minutes = max(1, round(info["duration_s"] / 60))
        taxi = info.get("taxi_cost")
        cost_hint = f"，打车约 {taxi} 元" if taxi and taxi != "0" else ""
        lines.append(f"{a['name']} → {b['name']}：约 {km:.1f} 公里，驾车 {minutes} 分钟{cost_hint}")

    return "\n".join(lines) if lines else "路线查询失败，请按地图导航自行安排"


def format_weather(casts: List[Dict[str, Any]], date_range: Optional[List[str]] = None) -> str:
    """把预报整理成提示词友好的文本；给了日期范围就按日期对齐。

    高德免费预报只有 4 天：用户问的日期超出范围时，不编造数据，而是明确标注
    「超出预报范围」并附带**最近 4 天的趋势**作为参考——比返回空值或瞎猜有用。
    """
    if not casts:
        return "暂无天气信息"

    by_date = {c["date"]: c for c in casts}
    lines: List[str] = []
    out_of_range = 0

    if date_range:
        for i, d in enumerate(date_range):
            c = by_date.get(d)
            if c:
                lines.append(f"第{i + 1}天({d}): {c['day_weather']}，{c['night_temp']}~{c['day_temp']}℃，{c['wind']}")
            else:
                out_of_range += 1
                lines.append(f"第{i + 1}天({d}): 超出 4 天预报范围")
        if out_of_range:
            trend = "；".join(
                f"{c['date']} {c['day_weather']} {c['night_temp']}~{c['day_temp']}℃" for c in casts
            )
            lines.append(f"【近期趋势参考（超出预报范围，请按季节常识准备）】{trend}")
    else:
        for i, c in enumerate(casts):
            lines.append(
                f"第{i + 1}天({c['date']} {c['week']}): {c['day_weather']}，{c['night_temp']}~{c['day_temp']}℃，{c['wind']}"
            )

    return "\n".join(lines)


def build_date_range(start_date: str, days: int) -> List[str]:
    """把出发日期 + 天数展开成日期列表；解析失败返回空列表。"""
    if not start_date or days <= 0:
        return []
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d")
    except (ValueError, TypeError):
        return []
    return [(start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)]
