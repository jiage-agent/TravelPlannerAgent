# Changelog

## [v2.1.0] - 2026-10

### Added
- **真实外部数据接入**：高德地图 Web 服务（天气预报 / 景点 POI 检索 / 驾车路线 / 地理编码），替换 v2.0 中硬编码的路线 Mock（原先不管去哪都返回"西湖 → 灵隐寺"）
- **Tavily 联网搜索**：补充门票预约、限流、当地活动等实时信息（v2.0 装了依赖但零调用）
- **LLM 自主工具调用（Function Calling）**：模型通过 `bind_tools` 自行决定补充检索什么，调用次数受 `LLM_TOOL_MAX_CALLS` 约束；与确定性核心链路形成"可测编排 + 模型补长尾"的分工
- **工具层 `app/tools/`**：`amap`（外部数据）、`search`（联网）、`cache`（SQLite TTL 缓存 + 命中率统计）、`registry`（LangChain Tool 封装）
- **弹性层 `app/utils/resilience.py`**：指数退避重试（默认 3 次）+ 失败降级
- **上下文预算管理 `app/utils/context.py`**：token 保守估算、分块优先级截断、结构化历史摘要
- **多轮对话记忆**：结构化轮次摘要 + 有界消息历史 + 增量字段继承（"改成 5 天"能继承上一轮目的地）
- 新增接口：`GET /sessions/{thread_id}/context`（查看上下文里到底存了什么）、`DELETE /cache`（清理工具缓存）
- `/plan` 新增 `new_session` 参数，区分"开新会话"与"同一对话续接"

### Changed
- **评测口径改造**：分母拆分为"用户提及字段"与"未提及字段的拒答正确率"；偏好指标从集合交集（命中 1 个就算对，明显偏松）改为 precision / recall / F1；新增样本级全对率与负样例分类统计；每项指标打印分子/分母
- **State 有界化**：`messages` 与 `tool_results` 从 `operator.add` 改为 `append_bounded(N)`，长会话不再无限增长
- **单连接收敛**：`workflow.py` 原先创建两个 `SqliteSaver` 实例写同一个库文件，现改为全局唯一连接 + WAL + `busy_timeout`
- **需求提取支持多轮继承**：读 `previous_extraction` 作为继承基线（注意不能预填进 `destination`/`days`，否则 Supervisor 会跳过需求节点）
- README 补齐评估结果、上下文与可靠性设计说明

### Fixed
- 天气超出 4 天预报范围时不再只回"超范围"，而是附带最近 4 天趋势作为参考
- 静态演示页默认会话 ID 由固定值改为留空自动生成，避免多人共用同一会话

### Known Limitations
- FastAPI 端点内仍为同步 `workflow_app.stream()`，会阻塞事件循环（待升级 `AsyncSqliteSaver` + `astream`）
- 尚未提供 Dockerfile（README TODO 中）
- 评测数字为单次运行结果，受 LLM 采样波动影响

## [v2.0.0] - 2026-10

### Added
- 新增 `info_collection` 节点：接入真实天气 API
- 新增 Human-in-the-loop：`interrupt()` + `Command(resume=...)`
- 新增 FastAPI 接口：`/plan`、`/resume`、`/sessions/{thread_id}`
- 新增业务数据持久化：`sessions` 表 + `plans` 表
- 新增 API Key 鉴权中间件
- 新增 LangSmith 全链路追踪
- 新增请求追踪中间件（`trace_id`）与 `/metrics` 运行指标

### Changed
- 架构重构：从线性流水线升级为 Supervisor 中心化（Hub-and-Spoke）多 Agent 架构
- 状态管理：引入 Pydantic 强校验与 `with_structured_output`

## [v1.0.0] - 2026-06

### Added
- 初版线性流水线
- SQLite Checkpointer 状态持久化
