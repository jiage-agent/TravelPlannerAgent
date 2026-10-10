# Changelog

## [v2.0.0] - 2026-10

### Added
- 新增 `info_collection` 节点：接入真实天气 API
- 新增 Human-in-the-loop：`interrupt()` + `Command(resume=...)`
- 新增 FastAPI 接口：`/plan`、`/resume`、`/sessions/{thread_id}`
- 新增业务数据持久化：`sessions` 表 + `plans` 表
- 新增 API Key 鉴权中间件
- 新增 LangSmith 全链路追踪

### Changed
- 架构重构：从线性流水线升级为 Supervisor 中心化（Hub-and-Spoke）多 Agent 架构
- 状态管理：引入 Pydantic 强校验与 `with_structured_output`

## [v1.0.0] - 2026-06

### Added
- 初版线性流水线
- SQLite Checkpointer 状态持久化