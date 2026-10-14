"""节点级用例（pytest 版）

背景
----
本文件原先是一份"手写脚本"：8 个 `async def test_*`，靠文件末尾的
`asyncio.run(main())` 串行执行。作为 pytest 用例它有两处硬伤：

1. `app/agents/` 下所有节点函数都是**普通同步函数**，而原代码对其中 6 个做了
   `await`。同步函数被调用后先真的执行一遍、再抛
   `TypeError: object dict can't be used in 'await' expression` —— 也就是说
   "测试失败"之前，LLM 调用已经真实发生并计费了。
2. `test_requirement` 没有任何断言、异常还被 try/except 吞掉，永远"通过"。

改造原则
--------
* 不依赖 LLM / 外网的 → 改成真正的纯逻辑断言，默认运行、零成本；
* 必须真调 LLM 的 → `@pytest.mark.integration` + 默认 skip（写明原因）；
* `tests/conftest.py` 里装了外网熔断器：默认任何外网连接都会让用例失败，
  不存在"不小心跑出 API 账单"的可能。

运行
----
    uv run pytest -q                                  # 只跑零成本用例（默认）
    RUN_INTEGRATION_TESTS=1 uv run pytest -q -m integration   # 会调真实 LLM，产生费用
"""
import os
from typing import get_args, get_type_hints

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

# 注意：info_collection_agent 模块整体导入，方便 monkeypatch 掉它内部的 amap / Tavily
import app.agents.info_collection_agent as info_collection_module
from app.agents.budget_agent import budget_agent
from app.agents.human_review_agent import human_review_agent, should_continue_after_human
from app.agents.planner_agent import planner_agent
from app.agents.preference_agent import preference_agent
from app.agents.requirement_agent import requirement_agent
from app.agents.supervisor import supervisor_node
from app.config import settings
from app.graph.state import (
    MESSAGE_LIMIT,
    TOOL_RESULT_LIMIT,
    AgentState,
    append_bounded,
)

# ===============================================================
# 集成测试开关
# ===============================================================
# 刻意不用"有没有 API Key"作为判据：本仓库 .env 里已经配了真实的
# DEEPSEEK_API_KEY，用 Key 判断等于没拦。必须由人显式设环境变量才会真的联网。
LLM_TESTS_ENABLED = os.environ.get("RUN_INTEGRATION_TESTS") == "1"

requires_llm = pytest.mark.skipif(
    not LLM_TESTS_ENABLED,
    reason=(
        "需要调用真实 LLM（DeepSeek），会产生 API 费用：默认跳过。"
        "确需运行请设置 RUN_INTEGRATION_TESTS=1 并加 -m integration"
    ),
)


def get_base_state(**overrides) -> AgentState:
    """构造一个"刚进来的用户请求"状态；可用关键字参数覆盖任意字段。"""
    state: AgentState = {
        "user_query": "我想去杭州玩3天，预算5000块，喜欢美食和自然风光",
        "destination": "",
        "days": 0,
        "budget": 0.0,
        "preferences": [],
        "start_date": "",
        "weather_info": None,
        "route_info": None,
        "attractions_info": None,
        "search_info": None,
        "info_collected": False,
        "current_plan": [],
        "total_cost": 0.0,
        "budget_approved": False,
        "budget_feedback": "",
        "preference_approved": False,
        "preference_feedback": "",
        "iteration": 0,
        "max_iterations": 3,
        "turn": 1,
        "history_summary": "",
        "final_plan": None,
        "messages": [],
        "human_approved": False,
        "human_feedback": "",
        "error": None,
        "tool_results": [],
        "next_node": None,
    }
    state.update(overrides)
    return state


SAMPLE_PLAN = [
    {"day": 1, "activities": ["西湖", "灵隐寺"], "estimated_cost": 1200.0, "notes": "晴天"},
    {"day": 2, "activities": ["浙江省博物馆"], "estimated_cost": 1000.0, "notes": "雨天室内"},
    {"day": 3, "activities": ["宋城"], "estimated_cost": 1500.0, "notes": "多云"},
]


# ===============================================================
# 一、Supervisor 路由：纯逻辑，不碰 LLM（原 test_supervisor / 第 3 个用例）
# ===============================================================
async def test_supervisor():
    """全新请求（需求字段全空）→ 应该先去 requirement。

    注意：supervisor_node 是同步函数，调用时不要加 await（原代码这里是对的）。
    """
    result = supervisor_node(get_base_state())
    assert result["next_node"] == "requirement"


async def test_supervisor_routes_to_info_collection():
    """需求已提取（目的地 + 天数齐了）→ 应该去 info_collection。"""
    state = get_base_state(destination="杭州", days=3, budget=5000.0)
    state["info_collected"] = False

    result = supervisor_node(state)
    assert result["next_node"] == "info_collection"


@pytest.mark.parametrize(
    ("label", "overrides", "expected"),
    [
        ("空状态→先补需求", {}, "requirement"),
        ("有目的地但天数为0→仍要补需求", {"destination": "杭州", "days": 0}, "requirement"),
        ("需求齐但信息未收集→收信息", {"destination": "杭州", "days": 3}, "info_collection"),
        (
            "信息已收集但无行程→去规划",
            {"destination": "杭州", "days": 3, "info_collected": True},
            "planner",
        ),
        (
            "有行程但预算未审→审预算",
            {"destination": "杭州", "days": 3, "info_collected": True, "current_plan": SAMPLE_PLAN},
            "budget",
        ),
        (
            "预算已过但偏好未审→审偏好",
            {
                "destination": "杭州",
                "days": 3,
                "info_collected": True,
                "current_plan": SAMPLE_PLAN,
                "budget_approved": True,
            },
            "preference",
        ),
        (
            "全部就绪→送人工审核",
            {
                "destination": "杭州",
                "days": 3,
                "info_collected": True,
                "current_plan": SAMPLE_PLAN,
                "budget_approved": True,
                "preference_approved": True,
            },
            "human_review",
        ),
        ("迭代超上限且人工未介入→强制送审", {"iteration": 3, "max_iterations": 3}, "human_review"),
        (
            "迭代超上限且人工已介入→避免死循环直接结束",
            {"iteration": 3, "max_iterations": 3, "human_feedback": "预算太高了"},
            "end",
        ),
    ],
    ids=lambda v: v if isinstance(v, str) else "",
)
async def test_supervisor_routing_table(label, overrides, expected):
    """Supervisor 的完整优先级路由表 + 迭代上限兜底（防死循环）。"""
    state = get_base_state(**overrides)
    assert supervisor_node(state)["next_node"] == expected, label


# ===============================================================
# 二、Budget：超支走硬规则（纯逻辑）；未超支才走 LLM（集成）
# ===============================================================
async def test_budget():
    """场景2：严重超支（预算 5000 / 花费 8000）→ 硬规则直接驳回，不消耗 Token。

    budget_agent 的 `if budget > 0 and total_cost > budget` 分支在调用 LLM 之前
    就 return，所以这条用例是真正的零成本纯逻辑测试。
    """
    state = get_base_state(budget=5000.0, total_cost=8000.0, current_plan=SAMPLE_PLAN)

    result = budget_agent(state)

    assert result["budget_approved"] is False
    assert "超出预算" in result["budget_feedback"]
    assert "3000" in result["budget_feedback"]  # 超支金额 = 8000 - 5000


@pytest.mark.integration
@requires_llm
async def test_budget_llm_within_budget():
    """场景1：未超支（预算 5000 / 花费 3700）→ 会落到 LLM 做性价比评估。

    原 test_budget 把场景1和场景2写在同一个函数里，导致"跑超支硬规则"也必然
    先付出一次真实 LLM 调用；这里把它拆出来单独标记，默认不跑。
    """
    state = get_base_state(budget=5000.0, total_cost=3700.0, current_plan=SAMPLE_PLAN)

    result = budget_agent(state)

    assert result["budget_approved"] is True
    assert result["budget_feedback"]


# ===============================================================
# 三、Preference：无偏好默认通过（纯逻辑）；有偏好才走 LLM（集成）
# ===============================================================
async def test_preference():
    """场景2：用户没提偏好 → 默认通过，不消耗 Token（纯逻辑）。"""
    state = get_base_state(preferences=[], current_plan=SAMPLE_PLAN)

    result = preference_agent(state)

    assert result["preference_approved"] is True
    assert "默认通过" in result["preference_feedback"]


@pytest.mark.integration
@requires_llm
async def test_preference_llm_with_preferences():
    """场景1：用户有偏好（美食/自然风光）→ 会落到 LLM 做匹配评估。

    原代码对这里的结果没有任何断言，等于白跑一次付费调用；现在补上真实断言。
    """
    state = get_base_state(
        preferences=["美食", "自然风光"],
        current_plan=[
            {"day": 1, "activities": ["西湖", "楼外楼吃杭帮菜"], "estimated_cost": 1200.0, "notes": ""},
            {"day": 2, "activities": ["浙江省博物馆"], "estimated_cost": 1000.0, "notes": ""},
        ],
    )

    result = preference_agent(state)

    assert isinstance(result["preference_approved"], bool)
    assert result["preference_feedback"]


# ===============================================================
# 四、InfoCollection：高德 / Tavily / LLM 三处外部依赖全换替身（纯逻辑）
# ===============================================================
# v2.1 起 info_collection_agent 不再用 httpx + wttr.in，而是调用：
#   * app.tools.amap   —— 高德天气、POI 景点检索、驾车路线（真实外部 API）
#   * app.tools.search —— Tavily 联网攻略（真实外部 API）
#   * ENABLE_LLM_TOOL_CALLING=True（默认值）时还会真调一次 LLM 做 Function Calling
# 所以离线测试要在三个接缝上打补丁：高德 / Tavily / LLM 开关。
# amap 是 `from app.tools import amap` 导入的模块对象，直接给它的属性打补丁即可。

# 高德 weather_forecast 的返回形状（未来 4 天预报）
FAKE_CASTS = [
    {"date": "2026-10-01", "week": "4", "day_weather": "晴", "night_weather": "多云",
     "day_temp": "26", "night_temp": "18", "wind": "东风1级"},
    {"date": "2026-10-02", "week": "5", "day_weather": "小雨", "night_weather": "阴",
     "day_temp": "24", "night_temp": "17", "wind": "东北风2级"},
    {"date": "2026-10-03", "week": "6", "day_weather": "多云", "night_weather": "晴",
     "day_temp": "25", "night_temp": "16", "wind": "北风1级"},
    {"date": "2026-10-04", "week": "7", "day_weather": "晴", "night_weather": "晴",
     "day_temp": "27", "night_temp": "18", "wind": "西风1级"},
]

# 高德 search_attractions 的返回形状，按关键词区分：
# "景点" 是节点默认的第一次检索，"美食" 模拟"按用户偏好追加一次检索"
FAKE_POIS = {
    "景点": [
        {"name": "西湖", "type": "风景名胜", "address": "杭州市西湖区", "location": "120.15,30.25", "tel": ""},
        {"name": "灵隐寺", "type": "风景名胜", "address": "杭州市西湖区", "location": "120.10,30.24", "tel": ""},
        {"name": "宋城", "type": "风景名胜", "address": "杭州市之江路", "location": "120.09,30.19", "tel": ""},
    ],
    "美食": [
        {"name": "河坊街", "type": "美食街", "address": "杭州市上城区", "location": "120.17,30.24", "tel": ""},
    ],
}

# 高德 driving_route 的返回形状：8 公里 / 20 分钟 / 打车 25 元
FAKE_ROUTE = {"distance_m": 8000, "duration_s": 1200, "tolls": "0", "taxi_cost": "25"}

FAKE_SEARCH_TEXT = "1. 西湖需提前预约：官方公众号可约 3 日内门票"


@pytest.fixture
def offline_info_collection(monkeypatch):
    """把 info_collection 的外部依赖全部换成离线替身（不联网、不调 LLM）。

    返回 monkeypatch，方便各用例再对 amap.weather_forecast 做差异化打补丁。
    """
    # LLM：关掉 Function Calling 自主检索，避免真实模型调用
    monkeypatch.setattr(settings, "ENABLE_LLM_TOOL_CALLING", False)
    # Tavily：给个假 key 让兜底分支稳定生效，并把 search_travel_guide 换成固定文本
    monkeypatch.setattr(settings, "TAVILY_API_KEY", "test-tavily-key")
    monkeypatch.setattr(info_collection_module, "search_travel_guide", lambda *a, **k: FAKE_SEARCH_TEXT)
    # 重试退避调 0：降级用例不必白等 3.5s（默认 0.5s 起指数退避重试 3 次）
    monkeypatch.setattr(settings, "TOOL_RETRY_TIMES", 0)
    monkeypatch.setattr(settings, "TOOL_RETRY_BASE_DELAY", 0.0)
    # 高德：景点检索与驾车路线换成替身（返回副本，避免用例之间互相污染）
    monkeypatch.setattr(
        info_collection_module.amap,
        "search_attractions",
        lambda city, keywords="景点", limit=8: [dict(p) for p in FAKE_POIS.get(keywords, [])],
    )
    monkeypatch.setattr(
        info_collection_module.amap, "driving_route", lambda origin, destination: dict(FAKE_ROUTE)
    )
    return monkeypatch


async def test_info_collection(offline_info_collection):
    """正常路径：天气 / 景点 / 路线 / 联网信息全部就绪，且全部来自替身。

    原用例 `await info_collection_agent(state)` 会先真的请求外部接口再抛 TypeError；
    现在把高德、Tavily、LLM 三处依赖都换成替身，断言完全离线可复现。
    """
    offline_info_collection.setattr(
        info_collection_module.amap,
        "weather_forecast",
        lambda city: [dict(c) for c in FAKE_CASTS],
    )

    result = info_collection_module.info_collection_agent(
        get_base_state(destination="杭州", days=2, preferences=["美食"])
    )

    assert result["info_collected"] is True

    # 天气：无 start_date → 直接按预报顺序输出
    assert "第1天(2026-10-01 4): 晴，18~26℃，东风1级" in result["weather_info"]

    # 景点：默认检索 + 按偏好「美食」追加检索，结果被合并
    assert "西湖" in result["attractions_info"]
    assert "河坊街" in result["attractions_info"], "用户偏好应触发第二次检索并合并结果"

    # 路线：由真实 build_route_plan 组装，距离/时长来自替身
    assert "西湖 → 灵隐寺" in result["route_info"]
    assert "约 8.0 公里" in result["route_info"]
    assert "驾车 20 分钟" in result["route_info"]
    assert "打车约 25 元" in result["route_info"]

    # 联网补充信息走 Tavily 替身
    assert result["search_info"] == FAKE_SEARCH_TEXT

    # 工具调用记录：四类工具都成功，且 LLM 自主检索确实被关掉了（离线保证）
    labels = {r["tool"] for r in result["tool_results"]}
    assert labels == {"高德天气", "高德景点检索", "高德驾车路线", "Tavily 攻略搜索"}
    assert all(r["ok"] for r in result["tool_results"])

    # 节点会把本轮信息写进 messages，供多轮对话使用
    assert result["messages"] and "信息收集" in result["messages"][0]["content"]


async def test_info_collection_degrades_when_weather_api_fails(offline_info_collection):
    """天气接口失败 → 降级为兜底文案（ok=False），但不阻断其余信息收集。"""

    def _boom(city):
        raise RuntimeError("高德天气接口不可用")

    offline_info_collection.setattr(info_collection_module.amap, "weather_forecast", _boom)

    result = info_collection_module.info_collection_agent(get_base_state(destination="杭州", days=3))

    assert result["info_collected"] is True
    assert result["weather_info"] == "杭州：天气获取失败，建议按季节常识准备衣物并携带雨具。"

    weather_record = next(r for r in result["tool_results"] if r["tool"] == "高德天气")
    assert weather_record["ok"] is False
    assert weather_record["detail"] == "降级为兜底文案"

    # 其余两路仍照常产出（降级而不是整条链路中断）
    assert result["attractions_info"]
    assert result["route_info"]


async def test_info_collection_matches_start_date(offline_info_collection):
    """指定出发日期时按日期对齐预报；超出 4 天预报范围要明确标注 + 给近期趋势。"""
    offline_info_collection.setattr(
        info_collection_module.amap,
        "weather_forecast",
        lambda city: [dict(c) for c in FAKE_CASTS],
    )

    # 场景1：出发日期落在 4 天预报范围内 → 逐日对齐
    in_range = info_collection_module.info_collection_agent(
        get_base_state(destination="杭州", days=2, start_date="2026-10-02")
    )
    assert "第1天(2026-10-02): 小雨，17~24℃，东北风2级" in in_range["weather_info"]
    assert "第2天(2026-10-03): 多云，16~25℃，北风1级" in in_range["weather_info"]
    assert "超出" not in in_range["weather_info"]

    # 场景2：出发日期超出预报范围 → 不编造数据，标注超出并附近期趋势参考
    out_of_range = info_collection_module.info_collection_agent(
        get_base_state(destination="杭州", days=2, start_date="2026-10-20")
    )
    assert "第1天(2026-10-20): 超出 4 天预报范围" in out_of_range["weather_info"]
    assert "第2天(2026-10-21): 超出 4 天预报范围" in out_of_range["weather_info"]
    assert "【近期趋势参考" in out_of_range["weather_info"]


# ===============================================================
# 五、HumanReview：真跑 LangGraph 的 interrupt / resume，但不碰 LLM
# ===============================================================
def _build_human_review_app():
    """把 human_review 单独编成一张最小图：真跑中断/恢复，不接数据库、不调 LLM。"""

    def supervisor_stub(state):
        return {"next_node": "supervisor"}

    graph = StateGraph(AgentState)
    graph.add_node("human_review", human_review_agent)
    graph.add_node("supervisor", supervisor_stub)
    graph.add_edge(START, "human_review")
    graph.add_conditional_edges(
        "human_review",
        should_continue_after_human,
        {"end": END, "supervisor": "supervisor"},  # 对齐 workflow.py 里的真实拓扑
    )
    graph.add_edge("supervisor", END)
    return graph.compile(checkpointer=InMemorySaver())


async def test_human_review():
    """场景1：节点会真的挂起，resume="通过" 后才写入 final_plan / human_approved。

    原用例直接 `await human_review_agent(state)` 并预先设 state["human_approved"]=True，
    这个前提是错的：human_review_agent 恒调用 interrupt()，人工结论只来自 resume 值
    （直接调用会抛 RuntimeError: Called get_config outside of a runnable context）。
    """
    app = _build_human_review_app()
    config = {"configurable": {"thread_id": "test-human-review-approve"}}
    current_plan = [{"day": 1, "activities": ["西湖"], "estimated_cost": 500.0, "notes": ""}]

    paused = await app.ainvoke(get_base_state(current_plan=current_plan), config)
    assert paused["__interrupt__"], "进入 human_review 后应该挂起等待人工输入"
    assert paused["final_plan"] is None, "挂起期间不应产生最终方案"

    resumed = await app.ainvoke(Command(resume="通过"), config)

    assert resumed["human_approved"] is True
    assert resumed["final_plan"] == current_plan


async def test_human_review_reject():
    """场景2：resume 传驳回意见 → iteration+1、human_approved=False，并路由回 supervisor。"""
    app = _build_human_review_app()
    config = {"configurable": {"thread_id": "test-human-review-reject"}}
    feedback = "预算太高了，重新规划"

    await app.ainvoke(get_base_state(current_plan=SAMPLE_PLAN), config)
    resumed = await app.ainvoke(Command(resume=feedback), config)

    assert resumed["human_approved"] is False
    assert resumed["iteration"] == 1
    assert resumed["human_feedback"] == feedback

    # 路由规则（should_continue_after_human，纯函数）
    assert should_continue_after_human(resumed) == "supervisor"
    assert should_continue_after_human({**resumed, "human_approved": True}) == "end"
    assert should_continue_after_human({}) == "supervisor", "缺少标志位时应回 supervisor 重审"


# ===============================================================
# 六、需求提取 / 行程规划：只能真调 LLM，默认 skip
# ===============================================================
@pytest.mark.integration
@requires_llm
async def test_requirement():
    """需求提取：requirement_agent 全程依赖 LLM 结构化输出，无法离线改写。

    原用例没有任何断言、还把异常 try/except 吞掉 —— 无论 LLM 返回什么都是"通过"，
    因此这里补上断言，并默认跳过以免产生费用。
    """
    result = requirement_agent(get_base_state())

    assert "杭州" in result["destination"]
    assert result["days"] == 3
    assert result["budget"] == pytest.approx(5000, rel=0.01)
    assert any("美食" in p for p in result["preferences"])


@pytest.mark.integration
@requires_llm
async def test_planner():
    """行程规划：planner_agent 依赖 LLM 生成结构化 ItineraryOutput，无法离线改写。"""
    state = get_base_state(
        destination="杭州",
        days=3,
        budget=5000.0,
        preferences=["美食", "自然风光"],
        info_collected=True,
        weather_info="杭州未来3天：第1天晴 25-32℃；第2天大雨 22-28℃；第3天多云 24-30℃",
        route_info="西湖 -> 灵隐寺（约 8 公里）；灵隐寺 -> 宋城（约 12 公里）",
    )

    result = planner_agent(state)

    assert len(result["current_plan"]) == 3, "生成的行程天数不对"
    assert result["total_cost"] > 0, "总花费应该由各天 estimated_cost 汇总而来"


# ===============================================================
# 七、State 的有界 reducer：纯逻辑，长会话里防止上下文无限膨胀
# ===============================================================
def test_state_bounded_reducer():
    """append_bounded(N)：先拼接、只保留最近 N 条；messages/tool_results 都接了它。

    v2.0 用的是 operator.add（只拼接不设上限），多轮对话下 messages / tool_results
    会一路膨胀；这里既测 reducer 本身，也测 AgentState 的接线确实是"有界"的。
    """
    reducer = append_bounded(3)

    assert reducer([1, 2], [3, 4]) == [2, 3, 4], "应该只保留最近 limit 条"
    assert reducer([], [1, 2]) == [1, 2]
    assert reducer(None, [1]) == [1], "左值为空时不应报错"
    assert reducer([1, 2], None) == [1, 2], "右值为空时不应报错"
    assert reducer(None, None) == []
    assert len(reducer(list(range(100)), [999])) == 3

    # 接线断言：State 声明的确实是同一个有界 reducer，而不是裸 list / operator.add
    hints = get_type_hints(AgentState, include_extras=True)
    for field, limit in (("messages", MESSAGE_LIMIT), ("tool_results", TOOL_RESULT_LIMIT)):
        metadata = get_args(hints[field])[1]
        assert callable(metadata), f"{field} 必须接有界 reducer"
        assert len(metadata([], list(range(limit + 10)))) == limit, f"{field} 的上限应是 {limit}"
