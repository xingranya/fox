# 外部组件评估

> 核验基线：2026-07-24  
> 评估原则：先解决明确问题；每项只接一个端口；可禁用、可替换、可退出；不得承担业务权威

## 当前排序

| 组件 | 当前定位 | 任务 | 状态 |
|:---|:---|:---|:---|
| OpenWork 客户端 | FoxWork 唯一员工客户端 | F1.9-F1.10、F3.4-F3.13 | 已采用 |
| OpenWork Den | 账号、组织、远程工作区和 AI 控制面 | F3.2-F3.6、F3.11-F3.13 | F3.2 采用门已通过，远程 Worker 待 F3.3 |
| Dify | `AIWorkflowPort` | F3.14 | 计划接入 |
| Zvec | `SearchIndexPort` | F3.15 | 评估后可拒绝 |
| Open Notebook | `ContentProcessingPort` | F3.16 | 评估后可部分采用或拒绝 |
| Nubase | `MemoryPort` | F3.17 | 评估后可拒绝 |
| FlowLong | `ApprovalWorkflowPort` | F3.18 | 许可与需求门后可拒绝 |
| BISHENG | `AIWorkflowPort` 候选 | Phase 4 后另行 rescope | 不计入当前 56 项 |

## 统一采用规则

1. 必须解决鸿日或团队试点中已经观测到的瓶颈。
2. 组件内部对象不能进入 Brand OS 领域核心。
3. 关闭组件后，原件、当前状态、Task Packet、Proposal 和审批历史完整。
4. 输出只形成 Artifact、Proposal 或流程状态，不能自动改变正式状态。
5. 授权、数据外发、日志、备份、升级和退出必须有自动化或演练证据。
6. 星数、功能列表和演示效果不能替代金标、BrandBench 和真实使用。

## OpenWork 与 Den

### 价值

- OpenWork 提供成熟的 Electron/React 客户端、Session、终端、文件、工具权限和 OpenCode 运行体验；
- Den 提供自助注册登录、单组织、成员/团队、远程工作区、桌面交接、MCP、Skills、共享模型和策略管理；
- 两者一起解决普通员工“不懂模型配置、MCP 和 CLI”的使用门槛。

### 许可

- `ee/**` 外是 MIT；
- Den 所在 `ee/**` 是 FSL-1.1-MIT，属于源码可见企业版；
- 当前允许公司内部使用、复制和修改，限制对外竞争服务；
- 内部分发也保留许可与版权；每个版本两周年后转 MIT。

### 采用边界

- 采用 FoxWork 客户端和 Den 控制面；
- 不采用 Den MySQL、Worker 文件系统或 OpenWork Session 作为品牌业务权威；
- 不复用 Den Session Token 直通 Brand OS；
- 不把 Den 组织管理员自动映射为项目审批人；
- 远程 Worker 是必验运行面，但必须经可替换端口接入；未证明 Daytona/Render 前不得虚报完成或把其内部数据作为业务权威。

详细证据见 [OpenWork/Den 评估](openwork-client-evaluation.md) 和 [F3.2 技术门](../phase3/openwork-den-self-host-gate.md)。

## Dify

### 解决的问题

提供可视化 Prompt、模型路由、工作流、运行日志和人工可维护的 AI 编排，让不熟悉代码的团队也能管理部分固定流程。

### 接入方式

- 只实现 `AIWorkflowPort`；
- 输入最小 Task Packet 或明确 Artifact；
- 使用独立服务身份，不持有员工会话；
- 回调带签名、幂等键、任务和项目范围；
- 输出经 Schema 校验后只形成 Artifact/Proposal；
- 超时、取消、重试、外发和 NoOp 回退可见。

### 风险

Dify 自托管仍包含自身数据库、Redis、Worker、插件和 Sandbox。外部模型、插件与 HTTP 节点仍属于数据外发。修改版 Apache-2.0 的多租户/品牌边界需要正式分发前复核。

## Zvec

### 解决的问题

提供中文 BM25/Jieba、稠密/稀疏向量、过滤和混合检索，可能改善大量资料中的召回。

### 当前基线

PostgreSQL FTS + 结构化关系 + 权限回源。Zvec 只存稳定 ID、派生字段、项目和索引水位，不保存正式状态。

### 采用门

在相同鸿日金标和 Task Packet 下，召回、准确率或延迟相对 PostgreSQL FTS 有可测提升，并保持项目过滤、撤权、回源和可重建。否则拒绝采用。

## Open Notebook

### 解决的问题

其内容处理能力覆盖 PDF、Office、网页、音视频、OCR、来源与引用，可作为多媒体解析方案参考或适配器。

### 接入方式

- 优先复用解析能力，不嵌入第二套 Notebook 前端；
- 通过 `ContentProcessingPort` 传入明确原件版本；
- 输出绑定页码、幻灯片、时间码、处理器版本和原件哈希；
- 自身数据库、笔记、摘要和 Transformation 只作派生数据。

### 采用门

与 Brand OS 直接解析基线比较支持格式、来源定位、失败隔离、成本、升级和导出。若完整 sidecar 增加的运行面大于解析收益，只复用单项能力或拒绝采用。

## Nubase

### 解决的问题

可评估其 Memory 能力是否能减少员工和 Agent 的重复偏好说明。Auth、Storage、Gateway 与当前已采用 Den/PostgreSQL/S3 重叠，不整体替换。

### 风险与采用门

自动 ADD/UPDATE/DELETE 可能把模型推断污染成项目事实。Memory 必须项目隔离、可查看、可删除、可导出、可回源，且永远不进入当前状态或人工审批。若相对 Task Packet 和显式偏好没有可测收益，拒绝采用。

## FlowLong

### 解决的问题

适合多人会签、或签、转办、委派和复杂组织路由。当前团队人数很少，Brand OS 内置待确认队列已能完成首版。

### 接入方式

- 只实现 `ApprovalWorkflowPort`，只路由人的待办；
- 回调由 Brand OS 重新鉴权、校验项目、状态版本和幂等键；
- 超时不能自动批准；服务身份不能最终化 Proposal；
- 最终正式事件仍由 Brand OS 应用用例提交。

### 采用门

先证明真实多人流程存在，再通过许可、部署、撤回、超时、重复回调和越权测试。否则保持内置队列。

## BISHENG

BISHENG 保留为 Phase 4 后候选，用于比较文档解析和确定性工作流。它不计入当前 56 项，不形成第二套前端、不直连 PostgreSQL/S3、不承担账号或人工批准。只有试点证明 F3.8/F3.14 的直接基线不足，且 Fox 单独批准 rescope 后才启动。

## 组件分工

| 问题 | 首选组件 | 永久边界 |
|:---|:---|:---|
| 员工客户端与本机 Agent | FoxWork/OpenCode | Session 和 Tool Permission 不是业务真相 |
| 账号、组织、远程工作区、MCP、Skills、模型 | OpenWork Den | 不保存原件或业务审批 |
| 项目状态、证据、Proposal | Brand OS + PostgreSQL/S3 | 只有具名员工可最终确认 |
| AI 工作流 | Dify 或直接 Worker | 输出只形成 Artifact/Proposal |
| 检索 | PostgreSQL FTS，可选 Zvec | 命中回权威层复核 |
| 内容处理 | Brand OS Worker，可选 Open Notebook | 派生结果绑定原件版本和定位 |
| 运行记忆 | 可选 Nubase | 不进入当前状态 |
| 人工待办路由 | Brand OS，可选 FlowLong | 最终批准仍由 Brand OS 完成 |

## 统一决策门

| 门 | 通过条件 | 未通过时 |
|:---|:---|:---|
| 真实需求 | 已观测到直接基线瓶颈 | 不做 POC |
| 质量 | 同一数据和金标下显著更好 | 保留基线 |
| 权威 | 关闭组件后正式数据完整 | 拒绝采用 |
| 权限 | 项目隔离、服务身份、撤权和审计通过 | 不接真实资料 |
| 隐私 | 外发、保留、密钥和日志可枚举 | 脱敏样本或拒绝 |
| 许可 | 内部使用、修改和分发边界清楚 | 不集成/不分发 |
| 稳定 | 超时、取消、重试、升级和恢复可验证 | 继续隔离 |
| 退出 | 可在限定时间切回基线，无业务迁移 | 拒绝采用 |

## 实施顺序

1. F3.3-F3.6：先完成 Den/远程 Worker 部署、FoxWork 自助注册与单登录、员工端/管理员后台中文、身份联邦和项目映射。
2. F3.7-F3.13：再完成多媒体业务闭环和公司 AI 能力目录。
3. F3.14：接入 Dify。
4. F3.15-F3.18：逐项评估 Zvec、Open Notebook、Nubase、FlowLong，可采用或拒绝。
5. F3.19/F4：端到端和真实团队复核所有已采用组件。

禁止把“安装多个开源项目”当作进度，也禁止为可选组件推迟单账号和品牌业务主线。
