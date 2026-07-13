# 五个外部开源组件评估

核验日期：2026-07-13。Zvec、Nubase、Open Notebook 和 FlowLong 的数据来自前轮 GitHub 仓库 README、项目结构、发行版和许可文件现场读取；Dify 追加核验了主仓库 README、最新发行版和 LICENSE。

## 总体结论

五个项目都可以进入架构，但都不能成为领域核心或第二真相源：

- [Zvec](https://github.com/alibaba/zvec)：作为可重建检索适配器 POC；生产基线先使用 PostgreSQL FTS，可选 pgvector。
- [Nubase](https://github.com/OtterMind/Nubase)：团队和远程需求已经成立，可提前验证 Auth、Storage、Gateway 等单项能力，但当前不承担生产权威库。
- [Open Notebook](https://github.com/lfnovo/open-notebook)：选择性复用 `content-core`、研究交互和 REST/MCP 设计，避免整仓 fork。
- [Dify](https://github.com/langgenius/dify)：作为可视化 AI 工作流、Prompt、模型路由和运行观测适配器；输出只形成候选。
- [FlowLong](https://github.com/aizuda/flowlong)：作为复杂人工审批协调适配器；许可和真实需求门通过前不集成。

推荐顺序不是“五套同时部署”，而是 PostgreSQL/S3 权威基线 -> Open Notebook 内容处理 POC -> PostgreSQL FTS/pgvector 与 Zvec A/B -> Dify AI 流程 POC -> Nubase 单能力 POC -> FlowLong 复杂审批 POC。

## 对比

| 项目 | 现场版本/活跃度 | 许可 | 适合解决 | 不能解决 | 决策 |
|:---|:---|:---|:---|:---|:---|
| Zvec | v0.5.1；约 1.48 万星；2026-07-13 仍有提交 | Apache-2.0 | CJK 全文、向量、混合检索、过滤和本地嵌入 | 权威事实、审批、版本谱系、跨实例写协调 | 条件采用 |
| Nubase | v0.1.4；2026-06 首次公开；约 486 星 | Apache-2.0 | PostgreSQL 平台、Auth、Storage、Memory、Gateway、MCP | 当前可靠 PITR/HA；受控业务状态语义 | 单能力 POC，权威库禁用 |
| Open Notebook | v1.12.0；约 3.55 万星；40 个发行版 | MIT | 多模态摄入、多模型、引用、研究聊天、REST/MCP | 决定审批、状态升级门、权威版本谱系 | 选择性复用 |
| Dify | v1.15.0，发布于 2026-06-25；主仓活跃 | 修改版 Apache-2.0，附加多租户与前端品牌条件 | AI Workflow、Agent、Prompt、模型管理、RAG、LLMOps、API | 正式人工审批、证据权威性、项目状态真相 | 条件采用，先过许可与数据出口门 |
| FlowLong | v1.2.5；41 个发行版；审批能力完整 | 双许可加附加限制 | 会签、或签、驳回、转办、提醒、复杂组织分支 | AI 计算编排、单人审批低维护实现、权威领域状态 | 首版不引入 |

## Zvec

### 适合融入的原因

- 进程内运行，无需独立数据库服务。
- 支持 BM25、Jieba CJK 分词、稠密/稀疏向量、结构过滤和混合检索。
- 支持 WAL，适合作为中文证据检索实验层。
- 索引天然可从 PostgreSQL 正式数据和对象原件重建，符合可替换原则。

### 团队服务器下的限制

- 进程内单写设计不适合每个 API 实例各自写索引。
- 多实例读取时必须明确索引文件共享、发布和切换方式；不能把网络文件系统当作未经验证的共享数据库。
- 首版由独立索引 Worker 串行写入，API 只读取已发布索引；若部署复杂度或 Linux POC 不通过，继续使用 PostgreSQL FTS/pgvector。

### 必须保持的边界

- 只保存可重建文本、向量、过滤字段、事件水位和稳定回源 ID。
- 不保存原始文件、批准事件、权限和当前正式状态。
- 查询结果回到 PostgreSQL 和对象原件复核后，才能进入回答和 Task Packet。
- 先用鸿日 V5 中文金标与 PostgreSQL FTS/pgvector A/B 测试，通过后才能设为默认。

## Nubase

### 团队场景带来的价值

- 团队协作、远程访问、项目隔离、Auth、对象存储和模型网关需求已经真实出现，不再需要等待“第二位用户”才研究。
- 可以按端口分别评估 `IdentityFederationPort`、`ModelGatewayPort` 和受限 `MemoryPort`，并研究其 Storage 行为，不一次采用整个平台；标准 PostgreSQL、S3 和正式成员权限仍由 Brand OS 自身掌握。

### 当前不能承担生产权威库的原因

- 项目仍处于 early-stage，现场版本为 v0.1.4。
- README 明确缺少 Realtime、备份/PITR 和 HA；这与团队生产 `RPO <= 5 分钟`、`RTO <= 1 小时` 的目标不匹配。
- 一次引入 Java、PostgreSQL、Redis、Node 和 Docker，会扩大故障面和升级责任。
- Mem0 式 Memory 会自动 ADD/UPDATE/DELETE，不能直接操作项目事实。
- 若同时采用数据库、Auth、Storage、Functions、Memory 和 Gateway，替换成本将从单端口迁移升级为整个平台迁移。

### 推荐接法

生产权威库直接使用标准 PostgreSQL，并建立自身 Alembic 迁移、PITR 和恢复演练。Nubase 只在隔离环境逐端口 POC；通过项目隔离、服务密钥、数据导出、组件禁用和退出演练后，才允许替换对应适配器。Nubase 自身能力不得被当作 PostgreSQL 备份和高可用方案的替代品。

## Open Notebook

### 最值得复用的能力

- `content-core`：PDF、Office、网页、音视频和 Docling OCR 内容处理。
- `Esperanto`：18+ 模型提供商和 OpenAI-compatible 接口。
- FastAPI、Next.js/React、REST API 和 MCP 接入模式。
- 来源选择、上下文控制、搜索、笔记和引用交互。

### 推荐接法

首选直接使用 `content-core` 实现 `ContentProcessingPort`，避免运行完整 SurrealDB 应用。若确需 NotebookLM 式研究工作台，再以独立 sidecar 部署，通过 Outbox 和 REST 单向同步：

```text
S3 原始文件 -> Brand OS 登记哈希与版本 -> Open Notebook 研究处理
                                              |
                                              v
                         解析文本、引用、笔记和摘要候选
                                              |
                                              v
                    Brand OS Proposal -> 稳定 ID 回源 -> 人工审批
```

Open Notebook 的 MCP 默认包含创建、更新和删除能力，不应直接完整暴露给主控 AI。主项目只提供允许列表包装器；删除研究对象也不得影响 S3 原件或 PostgreSQL 正式状态。

## Dify

### 适合融入的能力

- 可视化 Workflow 适合把“摄入上下文 -> 调用模型/工具 -> 结构化输出 -> 质量检查”做成可审阅流程。
- 支持多模型提供商、Agent、RAG、Prompt 调试、API 和运行日志，可降低 AI 流程试验门槛。
- 可以承载会议摘要候选、任务包草案、内容分类、BrandBench 批量评估和低风险通知等异步工作。

### 必须保持的边界

- Dify 只实现 `AIWorkflowPort` 或 `ModelGatewayPort`，不实现 `ApprovalWorkflowPort` 和 `CanonicalStorePort`。
- Dify API Key 只能调用受限应用 API，不持有 PostgreSQL、对象存储管理员密钥或批准能力。
- 输入携带项目、数据分级、任务范围、Schema 版本和请求 ID；输出必须通过 Schema 校验，只能生成 Proposal。
- Dify Workflow DSL、Prompt、模型版本和运行 ID 要留痕；Dify 不可用时，权威状态读取和人工审批仍可工作。
- 自建 Dify 会引入其自身 PostgreSQL、Redis、Worker、插件和 Sandbox 等运行面，应与 Brand OS 网络、凭据、数据库和备份明确隔离。

### 许可边界

Dify [LICENSE](https://github.com/langgenius/dify/blob/main/LICENSE) 是基于 Apache-2.0 修改的衍生许可证，并非无附加条件的 Apache-2.0：

1. 商业使用通常允许，但未经 Dify 书面授权，不得使用其源码运营多租户环境；许可证把一个 workspace 定义为一个 tenant。
2. 使用 Dify 前端时，不得移除或修改 Dify 控制台或应用中的 Logo 和版权信息；不使用其 `web/` 或 `web` 镜像前端时，该条不适用。
3. 团队内部单 workspace 自托管与对外多客户 workspace 服务不是同一许可风险；产品化前必须获得书面法律结论，不能凭技术隔离推定合规。

因此首选把 Dify 作为内部独立服务，通过后端 API 适配，不 fork 或白标其前端。需要多租户或自有品牌 Dify 前端时，先取得书面商业授权。

## FlowLong

### 适合融入的条件

- 多审批人、会签或签、转办委派、复杂分支、超时提醒和组织权限成为真实需求。

### 当前不采用的原因

- 核心有限状态机已能覆盖单级或少量审批人；不应为潜在需求提前引入第二套复杂审批平台。
- Java 17、Spring/Solon、MyBatis Plus 和 MySQL 形成第二技术栈和额外恢复链路。
- “AI 审批”和“超时自动通过”与人工闸门原则冲突，必须禁用。
- README 包含署名、禁止部分 SaaS/源码扩散/竞争性二开等附加条件；违反后切换 AGPL-3.0，不能按普通 Apache 项目处理。

### 后期接法

通过 `ApprovalWorkflowPort` 和独立包装服务接入。FlowLong 只协调人工任务，核心系统收到回调后重新验证身份、幂等键、流程版本和目标版本，再写入 PostgreSQL 权威审批事件。FlowLong 数据库丢失时，可以从权威状态重新发起或人工对账，但不能反向覆盖正式事件。

## Dify 与 FlowLong 的明确分工

| 场景 | Dify | FlowLong | Brand OS 核心 |
|:---|:---|:---|:---|
| 模型调用、Prompt、RAG、Agent 和结构化生成 | 主责 | 不参与 | 提供受限上下文和 Schema，验收候选输出 |
| 会议摘要、分类、任务草案、内容建议 | 生成 Proposal | 不参与 | 保存来源、版本和候选状态 |
| 多人会签、或签、转办、催办和组织路由 | 不负责 | 主责 | 发起流程并保存关联 ID |
| 批准决定、约束、负责人、截止时间和提交版本 | 无权 | 只返回人工任务结果，无最终写权 | 重新鉴权、并发校验并写权威事件 |
| 当前正式状态查询 | 可按任务获得只读最小上下文 | 可获得流程所需最小上下文 | 唯一权威来源 |
| 故障降级 | 停止 AI 自动化，保留人工流程 | 回退核心审批队列 | 必须持续提供权威读取和人工批准 |

一句话边界：**Dify 编排 AI 如何计算，FlowLong 编排人如何流转，Brand OS 决定什么正式生效。**

## 决策门

| 门 | 通过条件 | 未通过 |
|:---|:---|:---|
| G0 真相门 | PostgreSQL 单一权威写入、对象谱系和单向数据流确认 | 不开始实现 |
| G1 许可门 | 形成 SBOM；Dify 使用形态和 FlowLong 许可获得书面结论 | 对应组件仅限隔离研究或排除 |
| G2 隐私门 | 分级、出口白名单、凭据、提供商登记和审计完成 | 只允许不含真实数据的离线 POC |
| G3 权威服务门 | PostgreSQL 迁移、事务、PITR、恢复和 S3 对账通过 | 不接入任何会写回的外部组件 |
| G4 Zvec 门 | 中文召回显著优于 PostgreSQL 基线、回源 100%、可重建、服务器 POC 通过 | 保留 PostgreSQL FTS/pgvector |
| G5 Open Notebook 门 | 单向同步、引用映射、无越权和无未授权外发 | 仅使用 `content-core` 或自有解析器 |
| G6 Nubase 门 | 单项能力确有收益，项目隔离、导出、禁用和退出演练通过 | 使用标准 PostgreSQL/S3/独立身份方案 |
| G7 Dify 门 | AI 流程收益可测、Proposal 边界、数据出口和许可证通过 | 使用核心 Worker 与直接模型适配器 |
| G8 FlowLong 门 | 复杂人工审批需求成立，许可、回调幂等和对账通过 | 使用核心有限状态机 |

## 最终采用判断

- **首版必需**：标准 PostgreSQL、S3 兼容对象存储、统一应用服务、Web/PWA、远程 MCP、CLI、Skills、Outbox Worker。
- **首批 POC**：Open Notebook 内容处理、PostgreSQL FTS/pgvector 与 Zvec 检索对比、Dify AI 流程。
- **条件 POC**：Nubase 的单项平台能力、FlowLong 的复杂人工审批。
- **明确禁止**：让任何候选组件保存正式状态、让 AI 自动批准、让多个数据库并行接受业务写入、未经许可运营 Dify 源码多租户或白标前端、未经许可嵌入分发 FlowLong。
