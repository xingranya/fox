# F2.9 可观测性、健康和告警

## 这项工作解决什么问题

F2.8 已经有 HTTP API，但多副本运行还缺几件日常排障必须知道的事：请求从哪里来、经过了哪一个项目和状态版本、依赖是不是已经失效、Outbox 是否积压，以及共享限流坏掉时能不能明确停下来。

F2.9 把这些信息放进一个可替换的运行时。它只保存日志、指标、追踪和告警等派生数据，不改变正式事件、审批或当前状态。实现位于 `src/brand_os/observability.py`，HTTP 接入位于 `src/brand_os/http_api.py`。

机器契约：

- `contracts/phase2/observability.json`
- `schemas/phase2/observability.schema.json`
- PostgreSQL v11 的 `rate_limit_buckets` 迁移

## 请求关联

每个请求都有四个关联字段：`request_id`、`correlation_id`、`trace_id` 和 `span_id`。客户端可以提供 `X-Request-ID`、`X-Correlation-ID` 和 W3C `traceparent`；格式不安全或过长的值会被替换，不会原样写入日志。

响应会返回 `X-Request-ID`、`X-Correlation-ID` 和服务端的 `traceparent`。HTTP 请求由 `Tracer` 包成一个 span。身份、项目、状态版本和事件 ID 在路由已经确认后才补进结束日志，路径中的项目 ID 不会成为指标标签。

## 日志

日志是 JSON Lines，字段和长度有上限。以下内容不会写入日志：

- Authorization、Cookie、Token、密码、密钥和 DSN；
- Prompt、请求体、Payload、原文内容和签名 URL；
- 未经限制的集合、深层嵌套对象和完整异常输入。

日志 Sink 是端口。测试使用有界 `InMemorySink`，部署时可以替换成公司的日志收集器；库代码不会自行打开文件、连接网络或启动后台进程。

## 指标

指标名称和标签在契约中固定，单个指标最多 256 条时间序列。未知的入口、方法、依赖、消费者或错误码统一折叠为 `other`，不把员工、项目、文件名或会话 ID放进标签。

当前指标覆盖：

- HTTP 请求总数、延迟直方图、错误和限流拒绝；
- PostgreSQL、Schema、对象存储、OIDC 及可选依赖的健康状态；
- Outbox pending 数、最老消息年龄、未解决死信和过期租约；
- 共享限流存储错误；
- 告警 firing/resolved 状态变化。

`MetricRegistry.prometheus_text()` 只导出上述安全摘要，不导出事件正文或 Outbox Payload。

## 健康检查和 Outbox

`/livez` 只证明进程能响应，`/readyz` 检查必需依赖并把结果写入 `dependency_status`。可选组件故障不会把核心状态查询误报成不可用。

PostgreSQL Outbox 提供只读的 `collect_outbox_metrics()`。它只读取消费者、状态、创建时间和租约时间，返回 pending 数、最老年龄、死信数和过期租约数，不返回 Payload。运行时按消费者写入固定指标，重复采样不会重复累计同一批过期租约。

## 共享限流

进程内限流器仍可用于开发和单进程测试。团队多副本必须注入 `PostgreSQLRateLimiter`：

1. 窗口行存放在 PostgreSQL v11 的 `rate_limit_buckets`；
2. 客户端 key 只以 SHA-256 摘要保存，数据库不保存原始 IP 或其他入口标识；
3. 同一 key 和 bucket 在事务内锁定，窗口过期后重新计数；
4. 后端不可用时返回 503 `RATE_LIMIT_STORE_UNAVAILABLE`，不退回本地计数。

这条规则是故意偏保守的。限流服务失效时，宁可暂时拒绝请求，也不让每个副本各自放行，造成团队边界被悄悄绕过。

## 告警

`AlertManager` 只在状态发生变化时发送事件，同一故障不会每次采样都重复告警。当前默认规则是：

- Outbox 最老消息超过 300 秒：`outbox_stale`；
- 共享限流存储出现错误：`rate_limit_store_unavailable`。

每条告警都有稳定指纹，并分别记录 `firing` 和 `resolved`。具体通知渠道不写进领域核心，后续可接公司的告警系统。

## 验证结果

新增 `tests/phase2/test_observability.py`，覆盖非法关联头替换、traceparent 继承、日志脱敏与截断、指标标签和基数上限、Prometheus 导出、成功/异常 span、告警去重与恢复、Outbox 统计、共享限流窗口和故障语义。HTTP 测试还覆盖响应头、健康指标、普通 503 与限流存储 503 的区分。

本轮只运行一次性测试和进程内夹具，不启动常驻 Web、数据库、Worker、Docker 或桌面应用，也没有接入鸿日或鸿喜达正式资料。

## 与未来 BISHENG 规划的关系

BISHENG 仍是 Phase 4 真实试点后的候选工作流实现，不计入当前 56 项，也不是 F2.9 的依赖。将来若 Fox 单独批准 BS0-BS3 POC，BISHENG 适配器必须复用同一套请求关联、脱敏、Outbox 观测和告警契约；BISHENG 的运行状态只能作为派生工作流状态回到 FoxWork，不能成为正式状态源或人工批准入口。

如果 BISHENG 停止，Brand Project OS 的状态查询、证据回查和人工确认仍应可用。没有满足隔离、服务身份、来源回溯和同场对比条件时，保持 NoOp，不启动部署。
