"""info_collection Agent：拉取天气（真实 API）和路线（Mock）"""
import httpx                                              # 同步 HTTP 客户端
from datetime import datetime, timedelta                  # 日期计算
from app.graph.state import AgentState                    # 全局状态
from app.utils.logger import setup_logger                 # 日志

logger = setup_logger("travel_planner.info_collection")


def info_collection_agent(state: AgentState) -> dict:
    """信息收集节点：写入 weather_info / route_info / info_collected。"""
    destination = state.get("destination", "")
    days = state.get("days", 0)
    start_date = state.get("start_date", "")

    logger.info(f"▶ [InfoCollection] 收集 {destination} {days}天 天气 (出发: {start_date or '未指定'})")

    # 计算用户请求的具体日期范围
    date_range = []
    if start_date:
        try:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
            date_range = [(start_dt + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)]
            logger.info(f"  目标日期: {date_range[0]} ~ {date_range[-1]}")
        except Exception:
            logger.warning(f"⚠️ 日期解析失败: {start_date}")

    # ---------- 天气：调 wttr.in（免费，无需 Key） ----------
    weather_info = "暂无天气信息"
    if destination:
        try:
            url = f"https://wttr.in/{destination}?format=j1"
            with httpx.Client(timeout=10.0) as client:
                response = client.get(url)
                response.raise_for_status()
                weather_data = response.json()

                weather_days = weather_data.get("weather", [])
                weather_lines = []

                if date_range:
                    # 用户指定了日期：按日期匹配预报，匹配不到则提示超范围
                    for target_date in date_range:
                        matched = False
                        for day in weather_days:
                            if day.get("date") == target_date:
                                desc = day.get("hourly", [{}])[0].get("weatherDesc", [{}])[0].get("value", "未知")
                                weather_lines.append(
                                    f"{target_date}: {desc}, {day.get('mintempC')}-{day.get('maxtempC')}℃"
                                )
                                matched = True
                                break
                        if not matched:
                            weather_lines.append(f"{target_date}: 超出免费 API 预报范围")
                else:
                    # 用户没给日期：展示未来 days 天
                    for i, day in enumerate(weather_days[:min(days, len(weather_days))]):
                        desc = day.get("hourly", [{}])[0].get("weatherDesc", [{}])[0].get("value", "未知")
                        weather_lines.append(
                            f"第{i+1}天({day.get('date')}): {desc}, {day.get('mintempC')}-{day.get('maxtempC')}℃"
                        )

                weather_info = "\n".join(weather_lines) if weather_lines else "无法获取天气详情"
                logger.info("✅ 天气获取成功")

        except Exception as e:
            # 网络失败不阻断流程，降级为提示信息
            logger.warning(f"⚠️ 天气 API 失败: {e}，降级为默认信息")
            weather_info = f"{destination} 第1-{days}天：天气未知，请随身携带雨具。"
    else:
        logger.warning("⚠️ 目的地为空，跳过天气查询")

    # ---------- 路线：暂用 Mock，后续接高德 API ----------
    # TODO: 换成高德/百度路线 API，按 date_range 计算每日通勤
    mock_route = (
        f"{destination}景点路线参考："
        f"西湖 -> 灵隐寺（约 8 公里，打车 20 分钟）；"
        f"灵隐寺 -> 宋城（约 12 公里，打车 30 分钟）"
    )

    logger.info("✅ [InfoCollection] 信息收集完成")

    return {
        "weather_info": weather_info,
        "route_info": mock_route,
        "info_collected": True,
    }