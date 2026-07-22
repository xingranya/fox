# F2.8 版本化 HTTP API 与 OpenAPI 契约

## 当前结果

F2.8 已把服务器应用层发布为一个可嵌入的 ASGI 应用。它不是第二个客户端，也不会自行启动常驻进程；公司定制 OpenWork 通过 Employee API 访问，Codex、Claude、Dify 和其他受控 Agent 通过 Agent API 使用同一领域端口。

机器契约为 `contracts/phase2/http-api.json`，错误体为 `http-error.v1`，OpenAPI 文档由 `build_openapi_document()` 生成并由 `/openapi.json` 返回。实现位于 `src/brand_os/http_api.py`。

本轮没有连接公司 OIDC、PostgreSQL、S3 或鸿日/鸿喜达正式资料。测试只使用进程内 ASGI 客户端和合成项目数据。

## 入口边界

```text
OpenWork（唯一员工客户端）
  -> /api/v1/employee/**
       -> OIDC 会话 + 项目角色 + 领域应用服务

Codex / Claude / Dify / 其他 Agent
  -> /api/v1/agent/**
       -> 不透明服务凭据 + 项目 Scope + 领域应用服务

两条入口都不能直连 PostgreSQL 或对象存储。
```

Employee 和 Agent 使用不同的 Bearer 语义。员工会话由 F2.4 的 `OidcIdentityService` 解析，Agent 凭据由可替换的 `AgentCredentialVerifier` 解析；员工会话不能拿来调用 Agent 入口，服务身份也不能伪装成交互式员工。

Agent 只有读取、回源、Task Packet 和创建 Proposal 的路由，没有人工评审路由。`MCP` 命令身份仍留给 F3.6；当前 HTTP 层只接受已经能映射到领域 `AI`、`WORKFLOW` 或受控 `SYSTEM` Actor 的服务主体。

## 已发布路由

| 范围 | 能力 | 关键约束 |
|:---|:---|:---|
| `employee/auth` | 登录开始、OIDC 回调、刷新、撤销 | state/nonce/PKCE 和会话校验仍由 F2.4 负责 |
| `employee/me` | 当前员工身份 | 只返回非敏感身份摘要 |
| `employee/projects/{id}` | 项目摘要、当前状态、Proposal、证据、Task Packet | 先应用授权，再进入存储 |
| `employee/.../proposals/{id}/review` | 批准、修改后批准、驳回 | 必须是交互式员工、`Idempotency-Key` 和强 `If-Match` |
| `employee/.../evidence/uploads` | 创建上传会话、通过应用 API 传入内容 | 不返回 S3 直连地址；对象仍要经过准入状态机 |
| `agent/projects/{id}` | 当前状态、Proposal、证据回源、Task Packet | 服务 Scope 固定项目和动作 |
| `agent/.../proposals` | 创建 Proposal | 不能批准、驳回或改变正式状态 |
| `/livez`、`/readyz` | 存活和就绪 | 可选 Dify/Zvec/Open Notebook/Nubase/FlowLong 故障不阻断核心就绪 |

## 写入与冲突

HTTP 层不重新实现一致性，而是把请求转换成 F2.6/F2.7 已冻结的领域调用：

1. 解析 `Bearer`、项目、动作和保密级别。
2. 对正式写强制读取 `Idempotency-Key` 与 `If-Match`；请求体若带兼容字段，必须与 Header 一致。
3. 员工命令通过 `bind_human_command_context` 留下会话身份断言；Agent 命令使用受控非人工 Actor。
4. 调用 `WriteConsistencyService.execute`。
5. `COMMITTED` 返回 201，`REPLAYED` 返回 200，`CONFLICT` 映射为 409；未知数据库或投影错误不伪装成业务冲突。

409 的 `details` 保留 `write-conflict.v1` 的预期/当前版本、脱敏事件和正式状态差异。HTTP 层不做最后写入覆盖、不自动合并冲突，也不自动重试非幂等命令。

## 分页与兼容

- 列表接口使用带 HMAC 的不透明 `cursor.v1`，默认每页 50 条，最大 100 条。
- 游标绑定项目、资源、过滤条件和状态版本。状态版本变化时返回 409 `PAGINATION_CURSOR_STALE`，要求客户端重新读取第一页。
- URL 主版本当前为 `/api/v1`。新增可选字段可以在同一主版本发布；改变语义或删除字段必须增加主版本。
- 已退休主版本返回 410 `API_VERSION_RETIRED`。当前兼容窗口只记录规则，不把未批准日期写成上线死线。

## 错误与限流

所有应用错误都返回 `http-error.v1`：

```json
{
  "schema_version": "http-error.v1",
  "code": "VERSION_MISMATCH",
  "message": "预期版本已过期",
  "request_id": "req_...",
  "retryable": false,
  "details": {}
}
```

当前覆盖 400、401、403、404、409、422、429 和 503。每个响应带 `X-Request-ID`；未提供或格式不安全的请求 ID 不会被原样回显。默认限流器是进程内固定窗口实现，仅适合开发或单进程部署；多副本服务器必须在 F2.9/运维配置中注入共享实现，不能把本地计数当成团队级限流事实。

## 不在本任务内的内容

- 远程 MCP OAuth、MCP 命令身份和工具白名单：F3.6。
- OpenWork 登录界面、钥匙串和服务器项目选择：F3.2。
- 日志、指标、追踪、告警和共享限流存储：F2.9。
- SQLite 到 PostgreSQL/S3 的一次性切换：F3.1。
- BISHENG、Dify、FlowLong 和四个外部组件的正式接入：仍按当前 49 项之后候选门和各自 Phase 3 任务执行；BISHENG 不因本 API 发布而提前启动。

## 验证

- OpenAPI 3.1 文档通过 `openapi-spec-validator`。
- 覆盖 Employee/Agent 身份分离、项目授权、游标分页、游标过期、额外字段拒绝、前置版本、幂等键、409 冲突、429 限流、503 就绪和退休版本。
- 未启动常驻 Web、数据库、对象存储、Worker 或桌面应用；全量测试使用一次性/进程内夹具，并未读取正式项目资料。
