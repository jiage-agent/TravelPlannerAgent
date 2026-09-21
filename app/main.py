"""
FastAPI 入口
"""
import os
import json
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from typing import AsyncGenerator
from langgraph.types import Command
from fastapi import Depends
from app.auth import verify_api_key
from fastapi import Request
from fastapi.responses import JSONResponse
import uuid
from fastapi import Request

# ================================================================
# 1. 关闭 LangSmith（必须在 import langchain 之前）
# ================================================================
os.environ["LANGSMITH_TRACING"] = "false"
os.environ.pop("LANGSMITH_API_KEY", None)
os.environ.pop("LANGSMITH_ENDPOINT", None)

# ================================================================
# 2. 导入
# ================================================================
from app.models.schemas import (
    PlanRequest, PlanResponse, PlanStatus,
    ResumeRequest, DayPlan, SessionInfo
)
from app.graph.workflow import workflow_app, checkpointer
from app.graph.state import AgentState
from app.db.database import init_db
from app.db import repository as repo
from app.utils.logger import setup_logger

logger = setup_logger("travel_planner.api")

# ================================================================
# 3. 创建 FastAPI 应用 + 初始化数据库
# ================================================================
api = FastAPI(
    title="旅行规划助手 API",
    description="基于 LangGraph 的多Agent旅行规划系统",
    version="1.0.0"
)

# 启动时初始化数据库表
init_db()


# ================================================================
# 4. 辅助函数
# ================================================================
def build_initial_state(user_query: str) -> AgentState:
    """构建初始状态"""
    return {
        "user_query": user_query,
        "destination": "",
        "days": 0,
        "budget": 0,
        "preferences": [],
        "current_plan": [],
        "total_cost": 0,
        "budget_approved": False,
        "budget_feedback": "",
        "preference_approved": False,
        "preference_feedback": "",
        "iteration": 0,
        "max_iterations": 3,
        "final_plan": None,
        "human_approved": False,
        "human_feedback": "",
        "messages": [],
        "tool_results": [],
    }


def _format_plan_text(state: dict) -> str:
    """格式化方案展示"""
    lines = [f"🌴 {state.get('destination', '')} {state.get('days', 0)}日游方案\n"]
    for day_plan in state.get("current_plan", []):
        lines.append(f"第{day_plan['day']}天: ")
        for act in day_plan["activities"]:
            lines.append(f"  - {act}")
        lines.append(f"  预估花费: ¥{day_plan['estimated_cost']}")
        lines.append("")
    lines.append(f"总花费: ¥{state.get('total_cost', 0)}")
    return "\n".join(lines)


# ================================================================
# 5. 健康检查
# ================================================================
@api.get("/")
async def root():
    return {"status": "ok", "message": "旅行规划助手服务运行中"}


@api.get("/health")
async def health():
    return {"status": "healthy"}


# ================================================================
# 6. /plan —— 同步规划接口（唯一版本）
# ================================================================
@api.post("/plan", response_model=PlanStatus,dependencies=[Depends(verify_api_key)])
async def plan(request: PlanRequest):
    """
    同步规划接口：
    - 如果跑到 human_review 会中断，返回 status="waiting_for_review" + 草稿方案
    - 如果直接完成，返回 status="completed" + 最终方案
    """
    # 记录会话
    repo.create_session(request.thread_id, request.user_query)

    try:
        config = {
            "configurable": {"thread_id": request.thread_id},
            "recursion_limit": 20
        }

        # 清空旧会话
        try:
            checkpointer.delete_thread(request.thread_id)
        except Exception:
            pass

        initial_state = build_initial_state(request.user_query)

        # 运行工作流（不关心中间事件，只等它跑到暂停或结束）
        for _ in workflow_app.stream(initial_state, config=config):
            pass

        # 检查是否暂停在 human_review
        snapshot = workflow_app.get_state(config)

        if snapshot.next and "human_review" in snapshot.next:
            # 工作流暂停，返回草稿方案
            plan_text = _format_plan_text(snapshot.values)
            logger.info(f"✔ /plan 中断 | thread_id={request.thread_id}")
            return PlanStatus(
                success=True,
                thread_id=request.thread_id,
                status="waiting_for_review",
                plan=plan_text,
            )

        # 工作流已完成
        final_state = snapshot.values
        final_plan = final_state.get("final_plan", "")

        # 更新数据库 + 保存行程
        repo.update_session_status(request.thread_id, "completed")
        repo.save_plan(
            thread_id=request.thread_id,
            destination=final_state.get("destination", ""),
            days=final_state.get("days", 0),
            total_cost=final_state.get("total_cost", 0),
            final_plan=final_plan,
        )
        logger.info(f"✔ /plan 完成 | thread_id={request.thread_id}")

        return PlanStatus(
            success=True,
            thread_id=request.thread_id,
            status="completed",
            final_plan=final_plan,
        )

    except Exception as e:
        logger.exception(f"✘ /plan 失败 | thread_id={request.thread_id}")
        repo.update_session_status(request.thread_id, "failed")
        return PlanStatus(
            success=False,
            thread_id=request.thread_id,
            status="error",
            error=str(e),
        )


# ================================================================
# 7. /plan/stream —— 流式规划接口
# ================================================================
@api.post("/plan/stream",dependencies=[Depends(verify_api_key)])
async def plan_stream(request: PlanRequest):
    """SSE 流式返回工作流进度"""

    async def event_generator() -> AsyncGenerator[str, None]:
        repo.create_session(request.thread_id, request.user_query)
        try:
            config = {
                "configurable": {"thread_id": request.thread_id},
                "recursion_limit": 20
            }

            try:
                checkpointer.delete_thread(request.thread_id)
            except Exception:
                pass

            initial_state = build_initial_state(request.user_query)

            for event in workflow_app.stream(initial_state, config=config):
                # 中断事件
                if "__interrupt__" in event:
                    interrupt_data = event["__interrupt__"][0].value
                    data = {
                        "type": "interrupt",
                        "message": interrupt_data.get("message", ""),
                        "plan": interrupt_data.get("plan", ""),
                    }
                    yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
                    continue

                # 节点事件
                for node_name, node_output in event.items():
                    data = {
                        "type": "node",
                        "node": node_name,
                        "iteration": node_output.get("iteration", 0) if isinstance(node_output, dict) else 0,
                    }
                    yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

            # 最终状态
            snapshot = workflow_app.get_state(config)
            final_plan = snapshot.values.get("final_plan", "")

            # 完成时更新数据库
            if final_plan:
                repo.update_session_status(request.thread_id, "completed")
                repo.save_plan(
                    thread_id=request.thread_id,
                    destination=snapshot.values.get("destination", ""),
                    days=snapshot.values.get("days", 0),
                    total_cost=snapshot.values.get("total_cost", 0),
                    final_plan=final_plan,
                )

            yield f"data: {json.dumps({'type': 'final', 'content': final_plan}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"

        except Exception as e:
            logger.exception(f"✘ /plan/stream 失败 | thread_id={request.thread_id}")
            repo.update_session_status(request.thread_id, "failed")
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


# ================================================================
# 8. /resume —— 恢复中断
# ================================================================
@api.post("/resume", response_model=PlanStatus,dependencies=[Depends(verify_api_key)])
async def resume(request: ResumeRequest):
    """用户审核后，恢复工作流"""
    try:
        config = {
            "configurable": {"thread_id": request.thread_id},
            "recursion_limit": 20
        }

        # 检查会话
        snapshot = workflow_app.get_state(config)

        if not snapshot or not snapshot.next:
            return PlanStatus(
                success=False,
                thread_id=request.thread_id,
                status="error",
                error="会话不存在或未处于暂停状态",
            )

        # 用 Command(resume=...) 恢复
        for _ in workflow_app.stream(
            Command(resume=request.user_feedback),
            config=config
        ):
            pass

        # 检查最新状态
        snapshot = workflow_app.get_state(config)

        # 又被暂停（用户不通过）
        if snapshot.next and "human_review" in snapshot.next:
            plan_text = _format_plan_text(snapshot.values)
            logger.info(f"✔ /resume 再次中断 | thread_id={request.thread_id}")
            return PlanStatus(
                success=True,
                thread_id=request.thread_id,
                status="waiting_for_review",
                plan=plan_text,
            )

        # 完成
        final_plan = snapshot.values.get("final_plan", "")
        repo.update_session_status(request.thread_id, "completed")
        repo.save_plan(
            thread_id=request.thread_id,
            destination=snapshot.values.get("destination", ""),
            days=snapshot.values.get("days", 0),
            total_cost=snapshot.values.get("total_cost", 0),
            final_plan=final_plan,
        )
        logger.info(f"✔ /resume 完成 | thread_id={request.thread_id}")

        return PlanStatus(
            success=True,
            thread_id=request.thread_id,
            status="completed",
            final_plan=final_plan,
        )

    except Exception as e:
        logger.exception(f"✘ /resume 失败 | thread_id={request.thread_id}")
        repo.update_session_status(request.thread_id, "failed")
        return PlanStatus(
            success=False,
            thread_id=request.thread_id,
            status="error",
            error=str(e),
        )


# ================================================================
# 9. 会话管理接口
# ================================================================
@api.get("/sessions/{thread_id}", response_model=SessionInfo,dependencies=[Depends(verify_api_key)])
async def get_session(thread_id: str):
    """查询会话是否存在（先查数据库，再查 checkpointer）"""
    # 优先查数据库
    db_session = repo.get_session(thread_id)
    if db_session:
        return SessionInfo(
            thread_id=thread_id,
            exists=True,
            message=f"会话状态：{db_session['status']}，查询：{db_session['user_query'][:30]}...",
        )

    # 兜底：查 checkpointer
    try:
        config = {"configurable": {"thread_id": thread_id}}
        snapshot = workflow_app.get_state(config)
        if snapshot and snapshot.values:
            return SessionInfo(
                thread_id=thread_id,
                exists=True,
                message=f"会话存在，包含 {len(snapshot.values.get('current_plan', []))} 天行程",
            )
    except Exception:
        pass

    return SessionInfo(thread_id=thread_id, exists=False, message="会话不存在")


@api.delete("/sessions/{thread_id}",dependencies=[Depends(verify_api_key)])
async def delete_session(thread_id: str):
    """清空会话（同时删 checkpointer + 数据库记录）"""
    try:
        checkpointer.delete_thread(thread_id)
        repo.update_session_status(thread_id, "deleted")
        return {"success": True, "message": f"已清空会话 {thread_id}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@api.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception(f"✘ 未捕获异常 | path={request.url.path}")
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": "服务器内部错误",
            "trace_id": getattr(request.state, "trace_id", "unknown"),
        }
    )


@api.middleware("http")
async def add_trace_id(request: Request, call_next):
    trace_id = str(uuid.uuid4())[:8]   # 短 ID，8 位
    request.state.trace_id = trace_id
    logger.info(f"→ {request.method} {request.url.path} | trace_id={trace_id}")
    response = await call_next(request)
    response.headers["X-Trace-ID"] = trace_id
    logger.info(f"← {request.method} {request.url.path} | status={response.status_code} | trace_id={trace_id}")
    return response


# ================================================================
# 10. 启动入口
# ================================================================
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:api",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )