"""FastAPI 入口：旅行规划助手 REST 接口"""
import time
import uuid
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.responses import JSONResponse
from langgraph.types import Command

from app.models.schemas import PlanRequest, PlanStatus, SessionInfo, ResumeRequest
from app.graph.workflow import workflow_app, checkpointer
from app.graph.state import AgentState
from app.db.database import init_db
from app.db import repository as repo
from app.utils.auth import verify_api_key
from app.utils.logger import setup_logger
from app.utils.context import estimate_tokens, summarize_turn
from app.tools import cache
from app.config import settings

logger = setup_logger("travel_planner.api")

api = FastAPI(
    title="旅行规划助手 API",
    description="基于 LangGraph 的多 Agent 旅行规划系统",
    version="0.1.0",
)

# 启动时建表
init_db()

# 运行指标（进程内统计，重启归零）
METRICS = {
    "total_requests": 0,
    "total_errors": 0,
    "total_latency_ms": 0.0,
    "start_time": time.time(),
}


# ================================================================
# 中间件：请求追踪 + 耗时统计
# ================================================================
@api.middleware("http")
async def trace_middleware(request: Request, call_next):
    """给每个请求分配唯一 trace_id，统计耗时，注入响应头"""
    trace_id = str(uuid.uuid4())[:8]
    request.state.trace_id = trace_id

    METRICS["total_requests"] += 1
    start = time.time()

    logger.info(f"→ {request.method} {request.url.path} | trace_id={trace_id}")

    response = await call_next(request)

    latency_ms = (time.time() - start) * 1000
    METRICS["total_latency_ms"] += latency_ms

    response.headers["X-Trace-ID"] = trace_id
    if response.status_code >= 400:
        METRICS["total_errors"] += 1

    logger.info(
        f"← {request.method} {request.url.path} | status={response.status_code} "
        f"| 耗时={latency_ms:.0f}ms | trace_id={trace_id}"
    )
    return response


# ================================================================
# 全局异常处理器：报错也带 trace_id
# ================================================================
@api.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    trace_id = getattr(request.state, "trace_id", "unknown")
    logger.exception(f"✘ 未捕获异常 | path={request.url.path} | trace_id={trace_id}")
    return JSONResponse(
        status_code=500,
        content={"success": False, "error": "服务器内部错误", "trace_id": trace_id},
    )


# ================================================================
# 辅助函数
# ================================================================
def build_initial_state(
    user_query: str,
    turn: int = 1,
    history_summary: str = "",
    inherited: dict | None = None,
) -> AgentState:
    """构造一次请求的初始状态。

    v2.1 起支持多轮：同一 thread_id 再次请求时不擦除历史，而是
    - 轮次 +1；
    - 把**上一轮提取到的字段**放进 `previous_extraction`，交给需求提取节点做增量继承
      （例如用户说"改成 5 天"，目的地/偏好应当继承上一轮）；
    - 历史轮次摘要保留，供各节点按上下文预算引用。

    注意：**不要把继承值直接预填进 destination/days 等字段**——Supervisor 是按
    "目的地为空就去需求节点"来路由的，预填会让它跳过需求节点，用户的增量修改就被忽略了。
    """
    base: dict = {
        "user_query": user_query,
        "destination": "",
        "days": 0,
        "budget": 0.0,
        "preferences": [],
        "start_date": "",
        "weather_info": None,
        "attractions_info": None,
        "route_info": None,
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
        "turn": turn,
        "history_summary": history_summary,
        "previous_extraction": inherited or {},
        "final_plan": None,
        "human_approved": False,
        "human_feedback": "",
        "error": None,
        "tool_results": [],
        "next_node": None,
    }
    return base  # type: ignore[return-value]


# ================================================================
# 接口
# ================================================================
@api.get("/health")
async def health():
    return {"status": "healthy"}


@api.get("/metrics")
async def metrics():
    """运行指标：请求数、错误数、平均耗时、运行时长、工具缓存命中率。"""
    total = METRICS["total_requests"]
    avg_ms = METRICS["total_latency_ms"] / total if total else 0
    uptime = time.time() - METRICS["start_time"]
    return {
        "total_requests": total,
        "total_errors": METRICS["total_errors"],
        "avg_latency_ms": round(avg_ms, 1),
        "uptime_seconds": round(uptime, 1),
        "tool_cache": cache.stats(),
    }


@api.get("/sessions/{thread_id}/context", dependencies=[Depends(verify_api_key)])
async def get_context(thread_id: str):
    """查看某个会话当前的真实上下文（演示与排查用）。

    回答的是面试里最常被追问的问题："你的多轮记忆里到底存了什么？"
    """
    config = {"configurable": {"thread_id": thread_id}}
    try:
        snapshot = workflow_app.get_state(config)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    if not snapshot or not snapshot.values:
        return {"thread_id": thread_id, "exists": False}

    values = dict(snapshot.values)
    messages = values.get("messages") or []
    history_summary = values.get("history_summary") or ""
    return {
        "thread_id": thread_id,
        "exists": True,
        "turn": values.get("turn", 1),
        "history_summary_lines": len([ln for ln in history_summary.splitlines() if ln.strip()]),
        "history_summary_tokens": estimate_tokens(history_summary),
        "recent_messages": len(messages),
        "recent_messages_tokens": estimate_tokens(str(messages)),
        "tool_calls_recorded": len(values.get("tool_results") or []),
        "inherited_fields": {
            "destination": values.get("destination"),
            "days": values.get("days"),
            "budget": values.get("budget"),
            "preferences": values.get("preferences"),
        },
    }


@api.delete("/cache", dependencies=[Depends(verify_api_key)])
async def clear_cache(namespace: str | None = None):
    """清理工具缓存（演示前想强制走真实外部请求时用）。"""
    removed = cache.clear(namespace)
    return {"success": True, "removed": removed, "namespace": namespace or "all"}


@api.post("/plan", response_model=PlanStatus, dependencies=[Depends(verify_api_key)])
async def plan(request: PlanRequest, req: Request):
    """发起规划：跑到人工审核前暂停，返回草稿方案。

    v2.1 起支持**多轮对话**：同一个 thread_id 再次调用表示"在同一段对话里继续提要求"，
    历史消息与轮次摘要保留，上一轮提取到的字段作为继承基线
    （例如用户接着说"改成 5 天"，目的地与偏好要继承上一轮）。
    需要开新会话时传 `new_session=true`，或直接换一个 thread_id。
    """
    trace_id = getattr(req.state, "trace_id", "unknown")
    thread_id = request.thread_id or str(uuid.uuid4())[:8]
    logger.info(f"▶ /plan | thread_id={thread_id} | new_session={request.new_session} | trace_id={trace_id}")

    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": settings.RECURSION_LIMIT}

    # ---------- 判断这是"新会话"还是"同一段对话的下一轮" ----------
    previous: dict = {}
    try:
        snapshot = workflow_app.get_state(config)
        previous = dict(snapshot.values) if snapshot and snapshot.values else {}
    except Exception:
        previous = {}

    turn, history_summary, inherited = 1, "", None

    if previous and not request.new_session:
        turn = int(previous.get("turn", 1)) + 1
        inherited = previous
        line = summarize_turn(
            turn=turn - 1,
            user_query=previous.get("user_query", ""),
            destination=previous.get("destination", ""),
            days=previous.get("days", 0),
            budget=previous.get("budget", 0.0),
            plan_days=len(previous.get("current_plan") or []),
            total_cost=previous.get("total_cost", 0.0),
            human_feedback=previous.get("human_feedback", ""),
        )
        history_summary = f"{(previous.get('history_summary') or '').strip()}\n{line}".strip()
        logger.info(f"↻ 续接第 {turn} 轮对话（继承 目的地={inherited.get('destination')}，历史 {len(history_summary)} 字）")
    else:
        try:
            checkpointer.delete_thread(thread_id)
        except Exception:
            pass
        logger.info("＋ 新会话：已清空该 thread 的检查点")

    repo.create_session(thread_id, request.user_query)
    initial_state = build_initial_state(
        request.user_query, turn=turn, history_summary=history_summary, inherited=inherited
    )

    try:
        for event in workflow_app.stream(initial_state, config=config):
            if "__interrupt__" in event:
                break
    except Exception as e:
        logger.exception(f"✘ /plan 失败 | thread_id={thread_id} | trace_id={trace_id}")
        repo.update_session_status(thread_id, "failed")
        return PlanStatus(success=False, thread_id=thread_id, status="error", error=str(e))

    snapshot = workflow_app.get_state(config)

    if snapshot.next and "human_review" in snapshot.next:
        current_plan = snapshot.values.get("current_plan", [])
        repo.update_session_status(thread_id, "waiting_for_review")
        logger.info(f"✔ /plan 等待人工审核 | thread_id={thread_id} | trace_id={trace_id}")
        return PlanStatus(
            success=True,
            thread_id=thread_id,
            status="waiting_for_review",
            plan=str(current_plan),
        )

    logger.info(f"✔ /plan 完成 | thread_id={thread_id} | trace_id={trace_id}")
    return PlanStatus(
        success=True,
        thread_id=thread_id,
        status="completed",
        final_plan=str(snapshot.values.get("final_plan", "")),
    )


@api.post("/resume", response_model=PlanStatus, dependencies=[Depends(verify_api_key)])
async def resume(request: ResumeRequest, req: Request):
    """人工审核后恢复流程。"""
    trace_id = getattr(req.state, "trace_id", "unknown")
    thread_id = request.thread_id
    user_feedback = request.user_feedback
    logger.info(f"▶ /resume | thread_id={thread_id} | feedback={user_feedback} | trace_id={trace_id}")

    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 20}

    try:
        for event in workflow_app.stream(Command(resume=user_feedback), config=config):
            if "__interrupt__" in event:
                break
    except Exception as e:
        logger.exception(f"✘ /resume 失败 | thread_id={thread_id} | trace_id={trace_id}")
        repo.update_session_status(thread_id, "failed")
        return PlanStatus(success=False, thread_id=thread_id, status="error", error=str(e))

    snapshot = workflow_app.get_state(config)

    if snapshot.next and "human_review" in snapshot.next:
        current_plan = snapshot.values.get("current_plan", [])
        logger.info(f"✔ /resume 再次等待审核 | thread_id={thread_id} | trace_id={trace_id}")
        return PlanStatus(
            success=True, thread_id=thread_id,
            status="waiting_for_review",
            plan=str(current_plan),
        )

    final_state = snapshot.values
    final_plan = str(final_state.get("final_plan", ""))

    repo.update_session_status(thread_id, "completed")
    repo.save_plan(
        thread_id=thread_id,
        destination=final_state.get("destination", ""),
        days=final_state.get("days", 0),
        total_cost=final_state.get("total_cost", 0),
        final_plan=final_plan,
    )

    logger.info(f"✔ /resume 完成 | thread_id={thread_id} | trace_id={trace_id}")
    return PlanStatus(
        success=True, thread_id=thread_id, status="completed",
        final_plan=final_plan,
    )


@api.get("/sessions/{thread_id}", response_model=SessionInfo, dependencies=[Depends(verify_api_key)])
async def get_session(thread_id: str):
    """查询会话状态与最终方案"""
    session = repo.get_session(thread_id)
    if not session:
        return SessionInfo(thread_id=thread_id, exists=False, message="会话不存在")

    plan = repo.get_plan(thread_id)
    msg = f"状态: {session['status']}"
    if plan:
        msg += f" | {plan['destination']} {plan['days']}天 | 总花费 ¥{plan['total_cost']}"
    return SessionInfo(thread_id=thread_id, exists=True, message=msg)


@api.delete("/sessions/{thread_id}", dependencies=[Depends(verify_api_key)])
async def delete_session(thread_id: str):
    """清空会话（LangGraph 状态 + 业务库记录）"""
    try:
        checkpointer.delete_thread(thread_id)
        repo.update_session_status(thread_id, "deleted")
        return {"success": True, "message": f"已清空会话 {thread_id}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:api", host="0.0.0.0", port=8000, reload=True)