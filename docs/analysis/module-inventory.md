# 目标模块清单

当前没有实现代码。本清单描述团队服务器目标架构，文件数、行数和 S.U.P.E.R 分数均为规划值或预期值，不代表现有代码质量。

## 总览

| ID | 模块 | 单一责任 | 主要依赖 | 复杂度 | S.U.P.E.R 预期 |
|:---|:---|:---|:---|:---|:---|
| M01 | 领域模型与契约 | 定义项目对象、事件、端口和状态规则 | 无外部实现依赖 | 高 | `S绿 U绿 P绿 E绿 R绿` 10/10 |
| M02 | 原始文件与对象谱系 | 保存不可变原件、版本、哈希和对象键 | M01、ObjectStorePort | 高 | `S绿 U绿 P绿 E绿 R绿` 10/10 |
| M03 | 资料准入与版本谱系 | 解析、去重、来源登记和版本关系 | M01、M02、ParserPort | 高 | `S黄 U绿 P绿 E绿 R绿` 9/10 |
| M04 | 会议增量解析 | 分段、说话人、信息类型候选和上下文 | M01、M03、ModelPort | 高 | `S黄 U绿 P绿 E黄 R绿` 8/10 |
| M05 | 事件账本与状态投影 | 原子追加事件并重建任意时间点状态 | M01、CanonicalStorePort | 关键 | `S绿 U绿 P绿 E绿 R绿` 10/10 |
| M06 | 证据、主张与关系 | 管理支持、冲突、替代和不推导关系 | M01、M05 | 高 | `S绿 U绿 P绿 E绿 R绿` 10/10 |
| M07 | 人工审批与状态策略 | 阻止 AI 或外部流程越权升级状态 | M01、M05、M06、M16 | 关键 | `S绿 U绿 P绿 E绿 R绿` 10/10 |
| M08 | 混合检索与回源 | 全文、向量、过滤、关系扩展和权威复核 | M03、M06、SearchIndexPort | 高 | `S绿 U绿 P绿 E绿 R绿` 10/10 |
| M09 | 任务与轻量工作流 | 管理任务、日期性质、依赖和行动候选 | M05、M07、WorkflowPort | 中 | `S黄 U绿 P绿 E绿 R黄` 8/10 |
| M10 | 双轨 BrandSpec 与 Task Packet | 按工作模式生成最小任务上下文 | M05、M06、M08、M09 | 高 | `S绿 U绿 P绿 E绿 R绿` 10/10 |
| M11 | 模型路由与 BrandBench | 模型调用、隔离、比较和质量留痕 | M10、ModelGatewayPort | 高 | `S黄 U绿 P绿 E绿 R绿` 9/10 |
| M12 | 统一应用服务与访问接口 | 向 Web、API、远程 MCP、CLI 和 Skills 暴露同一用例 | M03-M11、M16 | 关键 | `S黄 U绿 P绿 E绿 R绿` 9/10 |
| M13 | Web/PWA 团队工作台 | 提供今日、审批、检索、会议、任务和系统健康界面 | M12 | 高 | `S黄 U绿 P绿 E绿 R绿` 9/10 |
| M14 | 审计、导出、备份与恢复 | 验证一致性、生成快照、恢复权威数据并重建派生层 | M02、M05、M08、M17 | 关键 | `S黄 U绿 P绿 E绿 R黄` 8/10 |
| M15 | 能力与依赖登记 | 管理模型、Skill、MCP、组件版本和适配器准入 | M01、M05 | 中 | `S绿 U绿 P绿 E绿 R绿` 10/10 |
| M16 | 身份、项目成员与授权 | 管理团队身份、角色、项目隔离和服务凭据 | M01、IdentityPort、CanonicalStorePort | 关键 | `S绿 U绿 P绿 E绿 R绿` 10/10 |
| M17 | Outbox、Worker 与外部协调 | 可靠派发索引、解析、通知和外部流程任务 | M05、OutboxPort、各外部端口 | 关键 | `S黄 U绿 P绿 E绿 R绿` 9/10 |

## 依赖方向

```text
Web / PWA / HTTPS API / 远程 MCP / CLI / Skills
                         ↓
统一应用服务：鉴权、幂等、并发控制、导入、检索、审批、任务包
                         ↓
领域核心：对象、状态策略、事件、权限规则和版本化端口
                         ↓
PostgreSQL / S3 / Search / Dify / FlowLong / Open Notebook / Nubase 适配器
                         ↓
                  外部实现与基础设施
```

外部实现只能依赖端口和版本化 Schema，领域核心不得导入 PostgreSQL 驱动、S3 SDK、Zvec、Nubase、Open Notebook、FlowLong、Dify 或具体模型 SDK。任何客户端也不得绕过 M12 直连数据库或外部组件。

## 核心模块说明

### M01 领域模型与契约

- 规划路径：`packages/core/domain/`、`packages/core/ports/`、`schemas/`。
- 公共接口：Project、Source、Meeting、Segment、Evidence、Claim、Decision、Constraint、Action、Artifact、Proposal、Event、Actor、Membership。
- 转换要求：先冻结 JSON Schema、稳定 ID、项目边界、幂等键、版本字段和错误码，再实现数据库或 UI。
- S.U.P.E.R：零框架依赖；跨边界对象可序列化；可在无数据库和网络条件下单测。

### M02 原始文件与对象谱系

- 规划路径：`packages/core/sources/`、`packages/adapters/object_store/`。
- 公共接口：`stage_upload`、`verify_hash`、`commit_source`、`open_version`、`verify_object`、`list_lineage`。
- 生产实现：S3 兼容对象存储，开启版本控制、服务端加密和生命周期策略；数据库仅保存稳定对象键和完整元数据。
- 原子边界：先写临时对象并计算 SHA-256，再在 PostgreSQL 事务登记；提交失败后的孤立对象由清理任务回收。
- 降级实现：开发环境可使用本地文件系统适配器，但对象键和哈希契约保持一致。

### M05 事件账本与状态投影

- 规划路径：`packages/core/events/`、`packages/core/projections/`、`packages/adapters/postgres/`。
- 公共接口：`append_event`、`rebuild_projection`、`get_state_at`、`verify_projection`。
- 生产实现：标准 PostgreSQL 是唯一权威库；普通领域表、只追加事件表、审计记录和 Outbox 在同一事务提交。
- 并发规则：聚合携带 `version`；普通编辑使用乐观锁；批准、角色变更等关键操作使用版本校验，必要时取得行锁。
- SQLite 边界：只用于开发、测试、单机演示和导出的只读快照，不接受团队生产写入。
- 关键测试：重复事件、重复幂等键、并发审批、丢失更新、重放等价、投影删除后重建。

### M07 人工审批与状态策略

- 规划路径：`packages/core/approval/`。
- 公共接口：`propose`、`approve`、`reject`、`request_edit`、`keep_open`、`supersede`。
- 规则：只有经过身份确认且具备项目角色的人工入口可以批准；远程 MCP、Dify、Open Notebook、Nubase Memory 和模型输出只能提出候选。
- 外部流程回调：FlowLong 返回的人工任务结果仍需核验操作者、项目、流程版本、目标对象版本和幂等键，再由核心写入正式事件。
- 默认状态机：`PROPOSED -> APPROVED | REJECTED | NEEDS_EDIT | OPEN`。

### M08 混合检索与回源

- 规划路径：`packages/core/search/`、`packages/adapters/search/`。
- 公共接口：`upsert`、`delete`、`search`、`rebuild`、`health`、`watermark`。
- 生产基线：PostgreSQL FTS；可选 pgvector。Zvec 只有通过 V5 中文金标 A/B 测试后才启用。
- 一致性：索引由 M17 消费 Outbox 异步更新；查询响应显示 `indexed_at` 或事件水位，不宣称瞬时一致。
- 规则：搜索结果必须携带稳定领域 ID，并回到 PostgreSQL 和对象原件复核后才能进入回答或 Task Packet。

### M10 双轨 BrandSpec 与 Task Packet

- 规划路径：`packages/core/task_packet/`。
- 输入：工作模式、当前目标、已批准状态、开放问题、相关证据、输出要求。
- 输出：版本化 Task Packet，不包含无关历史，并记录生成时的权威事件水位。
- 规则：探索任务保留矛盾；执行任务不得重写已批准策略；过期 Task Packet 必须明确提示。

### M12 统一应用服务与访问接口

- 规划路径：`apps/api/`、`apps/mcp/`、`apps/cli/`、`packages/application/`。
- 职责：统一鉴权、授权、幂等、并发控制、请求追踪、审计和用例调用。
- 远程 MCP 首批工具：状态、任务包、会议、证据检索与回源、决定、开放问题、行动和变更提案。
- 写权限：MCP 和 Skills 只能提交 Proposal；批准仅允许 Web/PWA 或明确要求交互确认且通过授权的管理 CLI。
- 网络边界：所有远程入口走 HTTPS；服务凭据按客户端、项目和能力最小授权，不共享管理员密钥。

### M13 Web/PWA 团队工作台

- 规划路径：`apps/web/`。
- 一级视图：今日、当前状态、待我确认、会议与变化、证据检索、任务与交付、研究工作台、系统健康与 AI 连接。
- 团队能力：项目切换、成员角色、在线冲突提示、版本差异、操作人和时间留痕。
- 离线边界：PWA 可缓存壳和只读快照；离线修改不直接视为成功，恢复网络后必须按版本重新提交。

### M14 审计、导出、备份与恢复

- 规划路径：`packages/operations/`、`apps/cli/commands/`、`deploy/`。
- 公共接口：`doctor`、`verify`、`snapshot_export`、`restore_drill`、`rebuild_derived`、`reconcile_external`。
- 恢复顺序：PostgreSQL PITR -> 对象版本校验 -> 状态投影重建 -> 索引重建 -> 外部协调层对账。
- 规则：备份与生产必须位于独立故障域；只有定期恢复演练成功才算具备恢复能力。

### M16 身份、项目成员与授权

- 规划路径：`packages/core/auth/`、`packages/adapters/identity/`。
- 最小角色：Owner、Approver、Contributor、Viewer；服务账号使用独立能力集合。
- 授权粒度：组织、项目、对象和动作；API 在应用层强制校验，数据库可用 RLS 作为第二道防线而非唯一防线。
- 关键测试：跨项目读取、对象枚举、服务密钥越权、成员移除、角色降级和审批自授权。

### M17 Outbox、Worker 与外部协调

- 规划路径：`packages/core/outbox/`、`apps/worker/`、`packages/adapters/workflows/`。
- 公共接口：`claim`、`dispatch`、`ack`、`retry`、`dead_letter`、`reconcile`。
- 规则：多个 Worker 使用 PostgreSQL `FOR UPDATE SKIP LOCKED` 竞争任务；处理器必须幂等；失败采用有限退避并进入死信与人工处理。
- 适配器分工：Dify 处理 AI 生成和模型编排；FlowLong 处理复杂人工任务路由；二者都不能在回调中直接改变正式状态。

## 五个外部组件适配器

| 项目 | 目标端口 | 允许保存 | 禁止成为唯一存储 | 替换成本 |
|:---|:---|:---|:---|:---|
| Zvec | `SearchIndexPort` | 文本、向量、过滤字段、回源 ID、索引水位 | 原文、批准事件、当前状态 | 低，删除后可从 Outbox/权威数据重建 |
| Open Notebook | `ContentProcessingPort`、`ResearchWorkspacePort` | 临时解析、外部 source ID、研究笔记和摘要候选 | 权威来源角色、事实、决定 | 中；完整 fork 为高 |
| Nubase | `MemoryPort`、`ModelGatewayPort`、可选 `IdentityFederationPort` | 派生 Memory、模型路由配置、外部身份映射 | PostgreSQL 正式状态、S3 原件、正式成员权限、自动 Memory 提取结果 | 中；全平台采用后为高 |
| Dify | `AIWorkflowPort`、`ModelGatewayPort` | Workflow DSL、Prompt、运行日志、候选输出 | 正式审批、负责人、截止时间、决定和约束 | 中；直接嵌入其前端或多租户模型后为高 |
| FlowLong | `ApprovalWorkflowPort` | 流程定义、实例、人工任务路由状态 | Evidence/Decision 正文、最终批准事件 | 中；直接承载业务状态后为高 |

## 规划态 S.U.P.E.R 结论

当前准备度仍为 `S黄 U黄 P红 E黄 R黄`：目标边界已经形成，但端口、Schema、数据库约束、部署和恢复演练尚未落地。最高优先级不是安装五个组件，而是冻结单一权威写入、版本化端口、幂等与并发协议、Outbox 和恢复顺序。完成基础阶段后，目标准备度应达到五项全绿。
