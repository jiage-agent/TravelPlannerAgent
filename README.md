# 🌴 Travel Planner Agent

> 构建于 LangGraph 框架的多 Agent 协作旅行规划系统 —— 具备任务规划、工具调用、人工审核、数据库持久化、可视化调试

[![Python](https://img.shields.io/badge/Python-3.13-blue)](https://www.python.org/)
[![LangGraph](https://img.shields.io/badge/LangGraph-0.2+-green)](https://langchain-ai.github.io/langgraph/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-teal)](https://fastapi.tiangolo.com/)
[![License](https://img.shields.io/badge/License-MIT-yellow)](./LICENSE)

---

## 📖 项目简介

用户输入一句自然语言需求（如"我想去成都玩3天，预算3000，喜欢美食和自然风光"），系统通过 **5 个 Agent 协作**自动生成完整旅行方案。

**核心特性**：
- 🤖 **多 Agent 协作** —— 需求分析、行程规划、预算审核、偏好审核、人工审核
- 🔧 **工具调用** —— LLM 主动调用 Tavily 搜索真实景点和门票价格，避免模型幻觉
- ⏸️ **人工审核中断** —— 使用 LangGraph `interrupt` 机制，支持暂停和恢复
- 💾 **状态持久化** —— SQLite checkpointer + 业务数据表（users / sessions / plans）
- 🔐 **完整 API** —— 鉴权、限流、trace_id、全局异常处理
- 📊 **可视化调试** —— LangSmith Studio 展示完整调用链

---

## 🏗️ 架构设计

![img_1.png](img_1.png)

### Agent 工作流
                  [ __start__ ]
                        │
                        ▼
               +-------------------+
               |    requirement    |  ① 需求分析 Agent
               | (提取预算/偏好/时间) |     解析用户原始 Query
               +-------------------+
                        │
                        ▼
               +-------------------+
         +---->|      planner      |  ② 规划 Agent
         |     |  (生成初步行程方案)  |     根据需求与反馈生成方案
         |     +-------------------+
         |               │
         |               ▼
         |     +-------------------+
         |     |      budget       |  ③ 预算 Agent
         |     |  (审核是否超预算)   |     超支则给出建议并打回
         |     +-------------------+
         |               │
         |               ▼
         |     +-------------------+
         |     |    preference     |  ④ 偏好 Agent
         |     | (审核是否符合兴趣)  |     不符则给出建议并打回
         |     +-------------------+
         |               │
         |               ├──────────────┐
         |               │              │
         |     (审核不通过，打回)     (审核通过)
         |               │              │
         +───────────────┘              ▼
                              +-------------------+
                              |   human_review    |  ⑤ 人工审批 (Interrupt)
                              |  (用户确认最终方案) |     暂停等待用户点击确认
                              +-------------------+
                                        │
                                        ▼
                                   [ __end__ ]


### 技术亮点

| 亮点 | 实现 |
|------|------|
| **规则路由** | Supervisor 用规则而非 LLM 路由，可预测、零成本 |
| **工具调用** | `bind_tools` + 工具循环 + 调用记录，防止 LLM 无限调工具 |
| **中断恢复** | LangGraph `interrupt` + `Command(resume=...)` + checkpointer |
| **异常处理** | `@safe_node` 装饰器统一处理节点异常，放过 interrupt 异常 |
| **双实例** | `workflow_app`（FastAPI 用，带 checkpointer）+ `studio_app`（Studio 用，不带） |

---

## 🎬 运行演示

### 1. 完整工作流可视化

![img_2.png](img_2.png)
![img_3.png](img_3.png)
**Planner Agent 主动调用 9 次工具**（3 次搜景点 + 6 次查门票）LLM 自主决策。

### 2. 人工审核中断

![img_4.png](img_4.png)

流程停在 `human_review` 节点，返回草稿方案给用户。用户确认后通过 `/resume` 恢复。

### 3. 最终状态

![img_5.png](img_5.png)

`human_approved=true`，所有审核通过，`final_plan` 生成。

### 4. API 响应

| `/plan` | `/resume` |
|---------|-----------|
| ![img_6.png](img_6.png) | ![img_7.png](img_7.png)|

### 5. 鉴权与 API 文档

| 401 鉴权失败 | Swagger 文档 |
|-------------|-------------|
| ![img_8.png](img_8.png) | ![img_9.png](img_9.png) |

---

## 🚀 快速开始

### 环境要求

- Python 3.11+
- [uv](https://github.com/astral-sh/uv)（包管理器）
- DeepSeek API Key（或任何 OpenAI 兼容 API）
- Tavily API Key（搜索工具用）

### 安装

```bash
# 克隆项目
git clone https://github.com/your-username/travel-planner-agent.git
cd travel-planner-agent

# 安装依赖
uv sync

# 配置环境变量
cp .env.example .env
# 编辑 .env，填入以下 key：
#   DEEPSEEK_API_KEY=sk-xxx
#   TAVILY_API_KEY=tvly-xxx
#   API_KEY=your-secret-key


