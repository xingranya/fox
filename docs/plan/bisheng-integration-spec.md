# BISHENG 条件接入 SPEC

> 依赖评估：[BISHENG 接入评估](../analysis/bisheng-integration-evaluation.md)
> 状态：`planned-candidate`
> 计划位置：当前 49 项完成后的候选池，不计入 Phase 2-4 完成度
> 不改变当前主线：Phase 2 服务器权威、Phase 3 联网闭环与 Phase 4 团队试点均不依赖 BISHENG

## 目标

在不改变 Brand Project OS 权威边界的前提下，验证 BISHENG 能否承担可视化 AI 工作流、人工介入、文档处理和派生知识检索，并让 OpenWork 成为统一的员工操作入口。

## 非目标

- 不把 BISHENG 变成项目真相数据库。
- 不让 BISHENG 的 HITL 代替业务批准。
- 不把 BISHENG 前端嵌成公司主客户端。
- 不在 OpenWork 桌面端启动 BISHENG 或其基础设施。
- 不同时深度接入 BISHENG、Dify 和 FlowLong。
- 不为 POC 导入全部鸿喜达资料。

## 系统职责

| 系统 | 负责 | 不负责 |
|:---|:---|:---|
| Brand Project OS | 当前状态、事件、证据关系、Task Packet、Proposal、人工确认、审计 | 可视化编排引擎 |
| OpenWork | 展示项目与运行状态、收集输入、展示 Artifact、发起人工确认 | 直接调用 BISHENG、保存正式状态 |
| BISHENG | 执行已登记工作流、等待流程输入、处理授权文档、产出工作稿 | 判断正式事实、批准决定、改变状态 |
| API Gateway/Service Mesh | 服务身份、网络隔离、限流、审计、TLS | 业务批准 |

## 接口设计

### `BishengWorkflowAdapter`

适配器实现主计划已经定义的 `AIWorkflowPort`，与 Dify 和直接 Worker 使用同一领域边界。若现有端口无法表达人工输入或事件续读，应先通过版本化 Schema 扩展端口，不能让领域核心直接导入 BISHENG SDK。

```text
invoke(request) -> run
get_run(run_id) -> run
stream_events(run_id, cursor) -> events
submit_input(run_id, input) -> run
cancel(run_id, reason) -> run
health() -> dependency_status
```

### 调用请求

`external-workflow-request.v1` 至少包含：

| 字段 | 说明 |
|:---|:---|
| `invocation_id` | Brand Project OS 生成的唯一调用 ID |
| `idempotency_key` | 防止重试产生第二次运行 |
| `project_id` | 所属项目 |
| `task_id` | 当前任务 |
| `workflow_key` | Brand Project OS 白名单中的稳定名称，不直接接受任意 UUID |
| `workflow_version` | 已验证的固定版本 |
| `task_packet_ref` | Task Packet 引用与摘要哈希 |
| `base_state_version` | 输出所依据的正式状态版本 |
| `evidence_refs` | 已经通过外发/自托管处理检查的证据引用 |
| `confidentiality_ceiling` | 本次允许的最高保密级别 |
| `input` | 通过版本化 Schema 校验的业务参数 |

BISHENG 原生接口没有 Brand Project OS 的幂等语义。适配器必须用本地调用台账保存 `idempotency_key -> session_id`，同一键只返回原运行。

### 事件映射

| BISHENG 事件 | 统一事件 | OpenWork 呈现 |
|:---|:---|:---|
| 运行开始 | `RUN_STARTED` | 已开始 |
| 节点输出/流式消息 | `RUN_PROGRESS` | 当前步骤和安全摘要 |
| 等待 Input 节点 | `INPUT_REQUIRED` | 待某人补充信息 |
| 工作流完成 | `RUN_SUCCEEDED` | 可查看 Artifact |
| 工作流失败 | `RUN_FAILED` | 失败节点、可重试性和追踪号 |
| 主动停止 | `RUN_CANCELLED` | 已停止及原因 |

每个事件必须有 `run_id`、单调递增 `sequence`、`occurred_at`、`trace_id`、`source_session_id` 和内容摘要。断线重连按 `sequence` 续读，不靠前端猜测。

### 结果契约

BISHENG 结果只能进入以下两类对象：

- `Artifact`：工作稿、报告、表格、图片、解析结果或中间文件。
- `Proposal`：可能影响正式事实、决定、约束、行动、负责人、日期或对外承诺的建议变化。

结果必须携带 `base_state_version`、工作流版本、输入摘要、证据引用、模型与解析器版本。状态版本已经过期时仍可保存 Artifact，但 Proposal 必须标记 `stale_base`，不能直接确认。

## 知识同步

### 允许数据

- P0：允许。
- P1：允许，但只同步任务所需副本。
- P2：逐项目、逐用途批准，默认不允许。
- P3：禁止。

自托管不等于可以无条件复制。BISHENG、它调用的模型、解析服务和 MCP 工具都属于新的处理方。

### 同步规则

1. 原件继续保存在 Brand Project OS 登记的位置，BISHENG 只拿内容副本。
2. 每个副本保存 `source_id`、版本、SHA-256、保密级别和 `evidence_ref`。
3. 原件更新时创建新版本，不在 BISHENG 内静默覆盖旧内容。
4. 删除或撤销授权时，发出删除任务并验证 MySQL、MinIO、Milvus、Elasticsearch 和缓存中的派生数据已清除。
5. BISHENG 检索只返回候选证据；当前有效性仍由 Brand Project OS 根据五态和项目状态判断。

## 安全要求

- BISHENG API、MySQL、Redis、Milvus、Elasticsearch、MinIO、OpenFGA 不暴露公网。
- OpenWork 无 BISHENG 凭据，只调用 Brand Project OS。
- 在 BISHENG 前放置自有认证层，使用短期服务身份或 mTLS；不得依赖 `default_operator` 作为访问控制。
- 只允许固定 `workflow_key`，禁止客户端提交任意 `workflow_id`、任意节点 `override` 或任意 `user_id`。
- 所有默认密码、示例 HMAC Secret 和开放端口必须覆盖。
- 自定义代码节点、HTTP 工具、MCP、模型和网页抓取分别设白名单与网络出口策略。
- 日志不得记录原件全文、密钥、Authorization、P2/P3 内容或未经脱敏的模型输入。
- BISHENG 失效时，Brand Project OS 的状态查询、证据回查、Proposal 审批和 OpenWork 基本使用仍可运行。

## 第一个真实流程

第一条 POC 流程定为“新增会议资料整理”，因为它能同时测试文档处理、状态差异、人工输入和 Proposal，又不会要求 BISHENG 获得业务批准权。

### 输入

- 一份已授权会议记录或转写。
- 当前 Task Packet。
- 会议模式和分类 Schema。
- 相关项目证据引用。

### 流程

1. 解析并分段会议资料。
2. 提取 FACT、VIEW、OPTION、TENDENCY、候选行动和开放问题。
3. 与 Task Packet 的当前状态比较，只输出新增、冲突和可能失效项。
4. 证据不足时进入人工输入节点，不自行补全。
5. 生成会议工作稿 Artifact 和若干 State Proposal。

### 禁止结果

- 把讨论写成决定。
- 把目标日期写成截止时间。
- 覆盖当前状态。
- 丢失发言人、位置或原始证据。
- 在探索会议里给出唯一方向。

## OpenWork 使用规格

OpenWork 增加一个“流程运行”视图，但不复制 BISHENG 的画布编辑器。

### 列表

- 流程名称和固定版本。
- 所属项目与任务。
- 状态、开始时间、持续时间和发起人。
- 是否等待输入、是否有 Proposal、是否失败。

### 详情

- 当前步骤和经过脱敏的事件时间线。
- 输入所依据的 Task Packet 版本。
- 待输入表单。
- 产出 Artifact 与 Proposal。
- 取消、重试和打开追踪信息。

普通员工不需要打开 BISHENG。流程管理员通过单独管理入口编辑、测试和发布工作流，发布后再登记到 Brand Project OS。

## 实施任务

### BS0：固定候选与安全边界

| ID | 任务 | 依赖 | 验收 |
|:---|:---|:---|:---|
| BS0.1 | 固定 BISHENG tag、commit、镜像 digest、Apache-2.0 与商业功能清单 | 无 | 社区与商业能力逐项分开，无浮动 `latest` |
| BS0.2 | 审计 `/api/v2` 认证、默认操作人、代用户、WebSocket 和网络出口 | BS0.1 | 未认证路径全部位于隔离网段，自有认证方案通过攻击测试 |
| BS0.3 | 冻结请求、事件、Artifact、Proposal 和知识元数据 Schema | BS0.1 | JSON Schema 有正反例和版本兼容测试 |
| BS0.4 | 选择与 Dify/直接 Worker 对比的唯一流程 | BS0.1 | 同一输入、输出和评分口径固定 |

### BS1：适配器与模拟测试

| ID | 任务 | 依赖 | 验收 |
|:---|:---|:---|:---|
| BS1.1 | 实现 `BishengWorkflowAdapter` 与调用台账 | BS0.2、BS0.3 | 幂等、超时、取消、重试、断线续读和禁用测试通过 |
| BS1.2 | 实现工作流白名单与版本映射 | BS0.3 | 未登记 ID、版本、override 和 user_id 全部拒绝 |
| BS1.3 | 实现 Artifact/Proposal 映射 | BS0.3 | 任何输出都不能直接改变正式状态 |
| BS1.4 | 实现 Fake BISHENG 契约服务 | BS0.3 | 无真实服务器即可运行全部端口契约测试 |

### BS2：隔离服务器 POC

| ID | 任务 | 依赖 | 验收 |
|:---|:---|:---|:---|
| BS2.1 | 在独立环境部署固定镜像 | BS0.1、BS0.2 | 默认凭据清除、端口隔离、备份与健康检查通过 |
| BS2.2 | 配置“新增会议资料整理”流程 | BS0.4、BS1.1 | 同一 Fixture 可重复执行，输出 Schema 稳定 |
| BS2.3 | 接入最小授权知识副本 | BS1.3、BS2.1 | 来源映射、版本、撤销和删除验证通过 |
| BS2.4 | 完成故障与恢复演练 | BS1.1、BS2.1 | BISHENG 停机不影响核心；重启后运行状态可对账 |

### BS3：OpenWork 与采用门

| ID | 任务 | 依赖 | 验收 |
|:---|:---|:---|:---|
| BS3.1 | 在 OpenWork 增加运行列表、详情和输入表单 | BS1.1、BS1.3 | 员工无需打开 BISHENG 即可完成 POC 流程 |
| BS3.2 | 与 Dify/直接 Worker 做同场对比 | BS2.2 | 质量、耗时、人工步骤、失败率、运维和补丁量可复算 |
| BS3.3 | 完成 Go/No-Go 评审 | BS2.3、BS2.4、BS3.1、BS3.2 | 明确采用、延长或淘汰；不以已有投入替代证据 |

## 验收门

### 必须通过

- 七项一票否决为 0。
- 同一请求重复提交只产生一个 BISHENG 运行。
- BISHENG 输出无法绕过 Brand Project OS 应用服务的人工确认用例。
- 100% 重要结论可回到 Brand Project OS 原件或会议位置。
- P2/P3 数据在无授权时无法进入请求。
- OpenWork、Brand Project OS 核心在 BISHENG 停机时仍可用。
- 社区版 POC 不依赖私有 Gateway。
- 与直接 Worker 或 Dify 相比，至少在可维护性、人工协作或文档质量中有一项明显收益，且总运维成本可接受。

### 立即停止

- 需要直连 BISHENG 数据库才能完成集成。
- 需要把正式状态或批准迁入 BISHENG。
- 无法在公司网络内安全隔离 `/api/v2`。
- 关键能力实际属于未获许可的商业组件。
- 为了接入 BISHENG 必须重写 Brand Project OS 领域语义。
- OpenWork 必须嵌入完整 BISHENG 前端才能完成日常流程。

## 决策记录

这份 SPEC 只把 BISHENG 加入后续候选池，不批准部署或产品代码。进入 BS0 的最早条件是：Phase 4 已形成真实团队试点结论，Fox 明确批准启动候选 POC，并同意用同一输入对比 BISHENG、Dify 或直接 Worker。届时应执行一次正式 rescope，把 BS0-BS3 作为独立任务段纳入活动进度；在此之前不得把它写成 F2.2-F4.9 的依赖或完成条件。

2026-07-23，Fox 确认本评估与 SPEC 可以保留在文档中，供后续规划参考。这次确认只批准“纳入未来候选”，不批准启动 BS0、部署 BISHENG 或接入公司资料。
