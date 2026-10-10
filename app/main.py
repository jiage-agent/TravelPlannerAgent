"""FastAPI 入口：旅行规划助手 REST 接口"""
import uuid
from fastapi import FastAPI, HTTPException, Depends
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


@api.post("/plan", response_model=PlanStatus, dependencies=[Depends(verify_api_key)])
async def plan(request: PlanRequest):
    """发起规划：跑到 human_review 前暂停，返回草稿方案。"""
    thread_id = request.thread_id or str(uuid.uuid4())[:8]
    logger.info(f"▶ /plan | thread_id={thread_id}")

    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 20}

    # 清空旧会话状态（防止复用同一 thread_id 时残留上次数据）
    try:
        checkpointer.delete_thread(thread_id)
    except Exception:
        pass

    # 记录会话
    repo.create_session(thread_id, request.user_query)

    initial_state = build_initial_state(request.user_query)

    try:
        for event in workflow_app.stream(initial_state, config=config):
            if "__interrupt__" in event:
                break
    except Exception as e:
        logger.exception(f"✘ /plan 失败 | thread_id={thread_id}")
        repo.update_session_status(thread_id, "failed")
        return PlanStatus(success=False, thread_id=thread_id, status="error", error=str(e))

    snapshot = workflow_app.get_state(config)

    # 停在 human_review：返回草稿方案，等待人工审核
    if snapshot.next and "human_review" in snapshot.next:
        current_plan = snapshot.values.get("current_plan", [])
        repo.update_session_status(thread_id, "waiting_for_review")
        logger.info(f"✔ /plan 等待人工审核 | thread_id={thread_id}")
        return PlanStatus(
            success=True,
            thread_id=thread_id,
            status="waiting_for_review",
            plan=str(current_plan),
        )

    # 理论上不会走到这里（interrupt_before 一定会在 human_review 前停）
    logger.info(f"✔ /plan 完成 | thread_id={thread_id}")
    return PlanStatus(
        success=True,
        thread_id=thread_id,
        status="completed",
        final_plan=str(snapshot.values.get("final_plan", "")),
    )


@api.post("/resume", response_model=PlanStatus, dependencies=[Depends(verify_api_key)])
async def resume(request: ResumeRequest):
    """人工审核后恢复流程。user_feedback='通过' 则输出最终方案，否则打回重规划。"""
    thread_id = request.thread_id
    user_feedback = request.user_feedback
    logger.info(f"▶ /resume | thread_id={thread_id} | feedback={user_feedback}")

    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 20}

    try:
        for event in workflow_app.stream(Command(resume=user_feedback), config=config):
            if "__interrupt__" in event:
                break
    except Exception as e:
        logger.exception(f"✘ /resume 失败 | thread_id={thread_id}")
        repo.update_session_status(thread_id, "failed")
        return PlanStatus(success=False, thread_id=thread_id, status="error", error=str(e))

    snapshot = workflow_app.get_state(config)

    # 用户驳回后又回到 human_review
    if snapshot.next and "human_review" in snapshot.next:
        current_plan = snapshot.values.get("current_plan", [])
        logger.info(f"✔ /resume 再次等待审核 | thread_id={thread_id}")
        return PlanStatus(
            success=True, thread_id=thread_id,
            status="waiting_for_review",
            plan=str(current_plan),
        )

    # 通过：落库
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

    logger.info(f"✔ /resume 完成 | thread_id={thread_id}")
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