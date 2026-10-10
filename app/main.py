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
def build_initial_state(user_query: str) -> AgentState:
    """构造一次新会话的初始状态"""
    return {
        "user_query": user_query,
        "destination": "",
        "days": 0,
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
# 接口
# ================================================================
@api.get("/health")
async def health():
    return {"status": "healthy"}


@api.get("/metrics")
async def metrics():
    """运行指标：请求数、错误数、平均耗时、运行时长"""
    total = METRICS["total_requests"]
    avg_ms = METRICS["total_latency_ms"] / total if total else 0
    uptime = time.time() - METRICS["start_time"]
    return {
        "total_requests": total,
        "total_errors": METRICS["total_errors"],
        "avg_latency_ms": round(avg_ms, 1),
        "uptime_seconds": round(uptime, 1),
    }


@api.post("/plan", response_model=PlanStatus, dependencies=[Depends(verify_api_key)])
async def plan(request: PlanRequest, req: Request):
    """发起规划：跑到 human_review 前暂停，返回草稿方案。"""
    trace_id = getattr(req.state, "trace_id", "unknown")
    thread_id = request.thread_id or str(uuid.uuid4())[:8]
    logger.info(f"▶ /plan | thread_id={thread_id} | trace_id={trace_id}")

    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 20}

    try:
        checkpointer.delete_thread(thread_id)
    except Exception:
        pass

    repo.create_session(thread_id, request.user_query)
    initial_state = build_initial_state(request.user_query)

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