# 开源组件评估

核验日期：2026-07-22。本文保留此前对 OpenWork、Zvec、Nubase、Open Notebook、FlowLong 和 Dify 的技术/许可研究，但依据最新产品范围重新排序。

## 当前决策

| 项目 | CURRENT 定位 | 当前状态 |
|:---|:---|:---|
| OpenWork | 本地桌面客户端候选；OpenCode 可作 Agent 运行辅助 | OW-L0 有条件通过；先做默认离线补丁，不是 MVP 前置条件 |
| Zvec | 未来 `SearchIndexPort` | `future-candidate` |
| Open Notebook | 未来 `ContentProcessingPort` / `ResearchWorkspacePort` | `future-candidate` |
| Nubase | 未来单项 `MemoryPort` / `ModelGatewayPort` 候选 | `future-candidate` |
| Dify | 未来 `AIWorkflowPort` | `future-candidate` |
| FlowLong | 未来 `ApprovalWorkflowPort` | `future-candidate` |

除 OpenWork 的本地界面候选外，五个项目全部是：

- `not-approved-for-current-mvp`
- `review-after-hongri-pilot`

CURRENT MVP 使用本地直接基线：轻量结构化存储、本地全文/关系查询、直接解析、直接模型调用、Fox 本地确认队列。不得以“以后可能需要”为由提前部署任何外部平台。

## 评估原则

1. 组件必须解决鸿日试点中已经观测到的问题，而不是只展示能力丰富。
2. 每个组件只实现一个版本化端口，不把内部对象泄漏进核心。
3. 关闭或删除组件后，原件、当前状态、Task Packet、Proposal 和 Fox 确认记录仍完整。
4. 模型、索引、Notebook、Memory 和工作流输出都只能成为候选，不能自动改变状态。
5. 采用必须同时通过质量、隐私、许可、稳定性、成本和退出对比。
6. BrandBench 和鸿日黄金用例优先于星数、演示效果和功能列表。

## OpenWork

### 现场事实

- 固定稳定发布为 [`v0.17.36@ddf3e482`](https://github.com/different-ai/openwork/tree/ddf3e482d2fdf3a374d0fbf4e23e01467a3014fc)，2026-07-20 发布；`dev` 会继续变化，不进入采用基线。
- `ee/` 外代码为 MIT；`ee/` 使用 FSL-1.1-MIT。证据见 [根 LICENSE](https://github.com/different-ai/openwork/blob/ddf3e482d2fdf3a374d0fbf4e23e01467a3014fc/LICENSE) 和 [`ee/LICENSE`](https://github.com/different-ai/openwork/blob/ddf3e482d2fdf3a374d0fbf4e23e01467a3014fc/ee/LICENSE)。
- 客户端是 React 19 + Vite + Electron 35，并深度依赖 `@opencode-ai/sdk`。
- OpenWork Server 的 Token、SQLite、Session、内存/JSONL 状态适合 Agent 运行控制，不是品牌业务状态库。
- OW-L0 已完成本地构建、许可证和网络出口核验。社区切片不依赖 `ee/**`，但上游默认仍带 PostHog、Den/Cloud、模型目录、上游更新、OpenWork AppID/协议及宽松 ATS；必须先打补丁再接真实资料。

### CURRENT 可以验证

- 复用 React/Electron 壳显示当前状态、待确认、证据、工作模式和 AI 任务；
- 复用 Session、Skills/MCP、终端、文件和模型选择体验；
- 让 Codex、Claude 或 OpenCode 通过同一 Task Packet 工作；
- 证明 OpenCode Tool Permission 与 Fox 的业务确认完全分开；
- 证明停用 OpenWork 后本地核心数据和 AI 入口仍然可用。

### CURRENT 不做

- 不部署远程 OpenWork Server、Den/`ee/` 和团队控制面；
- 不实施 OIDC、多用户 RBAC、公司自动更新链和企业策略；
- 不把 OpenWork SQLite、Session 或 Permission 作为项目真相；
- 不先进行大规模品牌重构和三平台生产分发；
- 不承诺 OpenWork 替代所有 Agent 引擎，底层当前仍是 OpenCode。

详细结论见[OpenWork 本地客户端候选评估](openwork-client-evaluation.md)和[OW-L0 技术选型记录](../phase1/openwork-ow-l0-evaluation.md)。

## Zvec

### 未来价值

- 中文 BM25/Jieba、稠密/稀疏向量、过滤和混合检索；
- 适合作为可重建索引，对比本地全文和结构化关系查询。

### 当前后置原因

- 鸿日首要问题是内容有效性、证据和替代关系，不是单纯召回不足；
- CURRENT 单用户可以先用 SQLite/本地全文建立可解释基线；
- 进程内单写和部署差异只在服务器/多 Worker 阶段成为实际问题。

### 未来采用门

在相同鸿日金标和 Task Packet 下，显著提升召回且不降低当前有效性判断、证据回源率和可重建性。否则保留本地全文基线。

## Open Notebook

### 未来价值

- `content-core` 的 PDF、Office、网页、音视频和 OCR 处理；
- 来源、笔记、引用和研究式交互可作为参考。

### 当前后置原因

- CURRENT 先验证鸿日真实资料的最小直接解析和回源；
- 完整应用会引入独立数据库、模型配置和研究状态；
- Notebook 的摘要、笔记和 Transformation 不能成为当前状态。

### 未来采用门

优先按 `ContentProcessingPort` 评估单项解析能力；完整 sidecar 只有在真实研究工作证明需要时才评估。任何 MCP 创建/更新/删除能力都需允许列表包装。

## Nubase

### 未来价值

- Auth、Storage、Memory、Gateway 等平台能力可按端口分别研究。

### 当前后置原因

- CURRENT 不需要团队 Auth、远程 Storage 或平台网关；
- early-stage 和 PITR/HA 缺口与本地价值验证无关；
- 自动 Memory ADD/UPDATE/DELETE 与 Fox 人工状态确认冲突。

### 未来采用门

只做单能力 POC，不整体替换平台；必须证明项目隔离、数据导出、禁用和退出，Memory 永远无权改变正式状态。

## Dify

### 未来价值

- 可视化 Prompt、模型路由、Workflow、运行日志和结构化生成；
- 可用于会议候选、Task Packet 草案、内容分类和 BrandBench 批处理。

### 当前后置原因

- CURRENT 可以用直接模型调用更快验证协议和品牌质量；
- Dify 自托管会带来自己的数据库、Redis、Worker、插件和 Sandbox；
- 平台能力可能掩盖 Task Packet、工作模式和状态闸门本身的问题。

### 许可与采用门

Dify 使用带附加条件的修改版 Apache-2.0；源码多租户和前端品牌存在许可边界。未来只通过 `AIWorkflowPort` 对比直接 Worker，输入最小 Task Packet，输出经 Schema 校验后只形成 Proposal。未经书面授权不做源码多租户或白标前端。

## FlowLong

### 未来价值

- 多人会签、或签、转办、委派和复杂组织路由。

### 当前后置原因

- CURRENT 只有 Fox 一个确认人，本地状态机已经足够；
- Java/MySQL/设计器增加完全无关的运行面；
- AI 审批和超时自动通过与产品人工判断原则冲突；
- 附加许可需要独立书面结论。

### 未来采用门

只有真实团队复杂审批成立且许可通过后，才以 `ApprovalWorkflowPort` 隔离 POC。FlowLong 只路由人的任务，最终状态仍由核心根据具名人工结果写入。

## Dify、FlowLong 与 OpenWork 的分工

| 问题 | 未来组件 | 永久边界 |
|:---|:---|:---|
| Agent 会话、工具、文件和终端运行 | OpenWork/OpenCode | Tool Permission 只批准工具执行，不批准业务状态 |
| 模型/Prompt/AI 计算编排 | Dify | 输出只形成 Proposal |
| 多人任务路由 | FlowLong | 流程结果需回到核心复核 |
| 当前状态、证据、模式切换和最终确认 | Brand OS 核心 + Fox | 任何外部组件都不能替代 |

CURRENT 只需要最后一行，以及可选的本地 OpenWork 界面辅助。

## 鸿日试点后的统一决策门

| 门 | 通过条件 | 未通过时 |
|:---|:---|:---|
| 真实需求门 | 鸿日使用记录证明直接基线存在明确瓶颈 | 不做 POC |
| 质量门 | 相同金标、Task Packet 和数据下显著优于基线 | 保留直接实现 |
| 真相门 | 组件关闭后当前状态、证据和确认历史完整 | 拒绝采用 |
| 隐私门 | 数据范围、外发、凭据和日志可审计 | 只用脱敏样本或拒绝 |
| 许可门 | 内部使用、修改、分发和未来产品形态有书面结论 | 不集成/不分发 |
| 稳定门 | 故障、升级、导出、重建和取消可验证 | 继续隔离 POC |
| 退出门 | 能在限定时间内切回直接基线且无业务数据迁移 | 拒绝采用 |

## 最终排序

1. CURRENT：鸿日黄金用例、只读原件、轻量状态、增量会议、探索/执行协议、同一 Task Packet、Codex/Claude 和 Fox 本地确认。
2. CURRENT 候选：OpenWork MIT 社区壳的最小本地界面适配。
3. `review-after-hongri-pilot`：判断是否需要 Zvec、Open Notebook 或 Dify 单项 POC。
4. 只有团队需求成立后：判断 Nubase 平台能力、FlowLong 和服务器运行面。

明确禁止把安装六个项目当作 MVP 进度，或让任何组件保存/批准鸿日当前状态。
