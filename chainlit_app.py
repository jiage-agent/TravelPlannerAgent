"""Chainlit 前端：对接 FastAPI 的旅行规划助手"""
import uuid
import httpx
import chainlit as cl

API_BASE = "http://127.0.0.1:8000"
API_KEY = "travel-planner-secret-2026"


# ================================================================
# 调用 FastAPI
# ================================================================
async def call_plan(user_query: str, thread_id: str):
    async with httpx.AsyncClient(timeout=180.0) as client:
        resp = await client.post(
            f"{API_BASE}/plan",
            headers={"X-API-Key": API_KEY, "Content-Type": "application/json"},
            json={"user_query": user_query, "thread_id": thread_id},
        )
        return resp.json()


async def call_resume(thread_id: str, feedback: str):
    async with httpx.AsyncClient(timeout=180.0) as client:
        resp = await client.post(
            f"{API_BASE}/resume",
            headers={"X-API-Key": API_KEY, "Content-Type": "application/json"},
            json={"thread_id": thread_id, "user_feedback": feedback},
        )
        return resp.json()


# ================================================================
# 渲染工具
# ================================================================
def format_plan_markdown(plan_text: str) -> str:
    """把 FastAPI 返回的 DayPlan 字符串格式化为 Markdown 卡片"""
    # plan_text 是 "[DayPlan(day=1, activities=['上午: ...', ...], estimated_cost=..., notes='...'), ...]"
    # 简单处理：按 DayPlan 拆分
    import re

    # 提取每一天的内容
    days = re.findall(
        r"DayPlan\(day=(\d+), activities=\[(.*?)\], estimated_cost=([\d.]+), notes='(.*?)'\)",
        plan_text,
    )

    if not days:
        return f"```\n{plan_text}\n```"

    md = []
    total = 0.0
    for day, activities_str, cost, notes in days:
        activities = re.findall(r"'([^']*)'", activities_str)
        cost_f = float(cost)
        total += cost_f

        md.append(f"### 第 {day} 天")
        for act in activities:
            md.append(f"- {act}")
        md.append(f"\n**预估花费：¥{cost_f:.0f}**")
        if notes:
            md.append(f"\n> 备注：{notes}")
        md.append("")

    md.append(f"---\n\n**总花费：¥{total:.0f}**")
    return "\n".join(md)


async def ask_user_review(thread_id: str, plan_text: str, round_num: int):
    """展示方案 + 弹窗询问审核意见"""
    plan_md = format_plan_markdown(plan_text)

    # 1. 展示方案
    await cl.Message(
        content=(
            f"## 📋 草稿方案（第 {round_num} 轮）\n\n"
            f"{plan_md}\n\n"
            f"---\n\n"
            f"**请审核方案：**\n\n"
            f"- 回复 **`通过`** 确认方案\n"
            f"- 或直接输入 **修改意见**（例如：预算太高了，请重新规划）"
        )
    ).send()

    # 2. 弹窗询问（这一步才是关键——不用 Action，用 AskUserMessage）
    res = await cl.AskUserMessage(
        content="请选择操作：输入 `通过` 确认，或填写修改意见：",
        timeout=600,
        raise_on_timeout=False,
    ).send()

    if not res or not res.get("output"):
        # 用户长时间未输入，退回等待普通消息
        return

    feedback = res["output"].strip()
    if not feedback:
        return

    await cl.Message(content="⏳ 正在处理你的审核意见...").send()

    try:
        result = await call_resume(thread_id, feedback)
    except Exception as e:
        await cl.Message(content=f"❌ 请求失败：{e}").send()
        return

    if result.get("status") == "completed":
        await cl.Message(
            content=f"# 🎉 最终方案\n\n{format_plan_markdown(result.get('final_plan', ''))}"
        ).send()
    elif result.get("status") == "waiting_for_review":
        cl.user_session.set("waiting_review", True)
        await ask_user_review(thread_id, result.get("plan", ""), round_num + 1)
    else:
        await cl.Message(content=f"⚠️ 状态异常：{result.get('status')}").send()


# ================================================================
# 会话开始
# ================================================================
@cl.on_chat_start
async def on_chat_start():
    thread_id = str(uuid.uuid4())[:8]
    cl.user_session.set("thread_id", thread_id)
    cl.user_session.set("waiting_review", False)
    cl.user_session.set("round", 0)

    await cl.Message(
        content=(
            f"# 🌴 智能旅行规划助手\n\n"
            f"你好，我是基于 **LangGraph** 的多 Agent 旅行规划系统。\n\n"
            f"请告诉我你的旅行需求，例如：\n\n"
            f"> 我想10月1号去杭州玩3天，预算4000块，喜欢美食和自然风光\n\n"
            f"**当前会话 ID**：`{thread_id}`"
        )
    ).send()


# ================================================================
# 处理用户普通消息（发起新规划）
# ================================================================
@cl.on_message
async def on_message(message: cl.Message):
    thread_id = cl.user_session.get("thread_id")
    user_text = message.content.strip()

    await cl.Message(content="⏳ 正在规划中，请稍等 20~40 秒...").send()

    try:
        result = await call_plan(user_text, thread_id)
    except Exception as e:
        await cl.Message(content=f"❌ 请求失败：{e}").send()
        return

    if result.get("status") == "waiting_for_review":
        cl.user_session.set("waiting_review", True)
        cl.user_session.set("round", 1)
        await ask_user_review(thread_id, result.get("plan", ""), 1)
    elif result.get("status") == "completed":
        await cl.Message(content=f"# 🎉 最终方案\n\n{format_plan_markdown(result.get('final_plan', ''))}").send()
    else:
        await cl.Message(content=f"⚠️ 状态异常：{result.get('status')}").send()