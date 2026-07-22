# 开源组件评估

核验日期：2026-07-22。本文保留此前对 OpenWork、Zvec、Nubase、Open Notebook、FlowLong 和 Dify 的技术/许可研究，但依据最新产品范围重新排序。

## 当前决策

| 项目 | CURRENT 定位 | 当前状态 |
|:---|:---|:---|
| OpenWork | 唯一员工客户端；OpenCode 作 Agent 运行时 | 已采用，F1.9-F1.10 完成发布与业务纵切门 |
| Zvec | `SearchIndexPort` | Phase 3 独立评估/适配；可拒绝并回退 PostgreSQL FTS |
| Open Notebook | `ContentProcessingPort` | Phase 3 独立评估/适配；只产派生内容 |
| Nubase | `MemoryPort` | Phase 3 独立评估/适配；不得保存正式状态 |
| Dify | `AIWorkflowPort` | Phase 3 正式接入任务；只读或创建 Proposal |
| FlowLong | `ApprovalWorkflowPort` | Phase 3 许可门后评估；只路由人的待办 |

以下标签仅属于 2026-07-13 的旧范围，不再是当前任务状态：

- `not-approved-for-current-mvp`
- `review-after-hongri-pilot`

Phase 3 仍以直接实现和 NoOp 作为每个组件的对照。组件必须单独证明收益、许可、隐私、稳定性和退出；进入计划不等于必须采用。

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

### Phase 1-3 目标

- 复用 React/Electron 壳显示当前状态、待确认、证据、工作模式和 AI 任务；
- 复用 Session、Skills/MCP、终端、文件和模型选择体验；
- 让 Codex、Claude 或 OpenCode 通过同一 Task Packet 工作；
- 证明 OpenCode Tool Permission 与 Fox 的业务确认完全分开；
- 证明停用 OpenWork 后本地核心数据和 AI 入口仍然可用。

### 仍不采用

- 不部署 OpenWork Den/`ee/**`，不把 OpenWork Server 当作业务服务；
- OIDC、RBAC 和公司更新链按独立任务实现，不混入 OpenWork Session；
- 不把 OpenWork SQLite、Session 或 Permission 作为项目真相；
- 不先进行大规模品牌重构和三平台生产分发；
- 不承诺 OpenWork 替代所有 Agent 引擎，底层当前仍是 OpenCode。

详细结论见[OpenWork 唯一客户端评估](openwork-client-evaluation.md)和[OW-L0 技术选型记录](../phase1/openwork-ow-l0-evaluation.md)。

## Zvec

### 可用价值

- 中文 BM25/Jieba、稠密/稀疏向量、过滤和混合检索；
- 适合作为可重建索引，对比本地全文和结构化关系查询。

### 为什么放在 Phase 3

- 鸿日首要问题是内容有效性、证据和替代关系，不是单纯召回不足；
- CURRENT 单用户可以先用 SQLite/本地全文建立可解释基线；
- 进程内单写和部署差异只在服务器/多 Worker 阶段成为实际问题。

### 采用门

在相同鸿日金标和 Task Packet 下，显著提升召回且不降低当前有效性判断、证据回源率和可重建性。否则保留本地全文基线。

## Open Notebook

### 可用价值

- `content-core` 的 PDF、Office、网页、音视频和 OCR 处理；
- 来源、笔记、引用和研究式交互可作为参考。

### 为什么放在 Phase 3

- CURRENT 先验证鸿日真实资料的最小直接解析和回源；
- 完整应用会引入独立数据库、模型配置和研究状态；
- Notebook 的摘要、笔记和 Transformation 不能成为当前状态。

### 采用门

优先按 `ContentProcessingPort` 评估单项解析能力；完整 sidecar 只有在真实研究工作证明需要时才评估。任何 MCP 创建/更新/删除能力都需允许列表包装。

## Nubase

### 可用价值

- Auth、Storage、Memory、Gateway 等平台能力可按端口分别研究。

### 为什么放在 Phase 3

- CURRENT 不需要团队 Auth、远程 Storage 或平台网关；
- early-stage 和 PITR/HA 缺口与本地价值验证无关；
- 自动 Memory ADD/UPDATE/DELETE 与 Fox 人工状态确认冲突。

### 采用门

只做单能力 POC，不整体替换平台；必须证明项目隔离、数据导出、禁用和退出，Memory 永远无权改变正式状态。

## Dify

### 可用价值

- 可视化 Prompt、模型路由、Workflow、运行日志和结构化生成；
- 可用于会议候选、Task Packet 草案、内容分类和 BrandBench 批处理。

### 为什么放在 Phase 3

- CURRENT 可以用直接模型调用更快验证协议和品牌质量；
- Dify 自托管会带来自己的数据库、Redis、Worker、插件和 Sandbox；
- 平台能力可能掩盖 Task Packet、工作模式和状态闸门本身的问题。

### 许可与采用门

Dify 使用带附加条件的修改版 Apache-2.0；源码多租户和前端品牌存在许可边界。Phase 3 只通过 `AIWorkflowPort` 对比直接 Worker，输入最小 Task Packet，输出经 Schema 校验后只形成 Proposal。未经书面授权不做源码多租户或白标前端。

## FlowLong

### 可用价值

- 多人会签、或签、转办、委派和复杂组织路由。

### 为什么放在 Phase 3

- CURRENT 只有 Fox 一个确认人，本地状态机已经足够；
- Java/MySQL/设计器增加完全无关的运行面；
- AI 审批和超时自动通过与产品人工判断原则冲突；
- 附加许可需要独立书面结论。

### 采用门

F3.12 先确认真实多人流程和许可，再以 `ApprovalWorkflowPort` 隔离 POC。FlowLong 只路由人的任务，最终状态仍由核心根据具名人工结果写入。

## Dify、FlowLong 与 OpenWork 的分工

| 问题 | 组件 | 永久边界 |
|:---|:---|:---|
| Agent 会话、工具、文件和终端运行 | OpenWork/OpenCode | Tool Permission 只批准工具执行，不批准业务状态 |
| 模型/Prompt/AI 计算编排 | Dify | 输出只形成 Proposal |
| 多人任务路由 | FlowLong | 流程结果需回到核心复核 |
| 当前状态、证据、模式切换和最终确认 | Brand Project OS 核心 + 有权限员工 | 任何外部组件都不能替代 |

核心旅程只依赖 Brand Project OS Service 和唯一客户端；其余组件必须可以关闭。

## Phase 3 统一决策门

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

1. Phase 1：完成 OpenWork 唯一客户端、鸿日黄金用例和本地纵切。
2. Phase 2：完成服务器权威、身份权限、一致性和恢复。
3. Phase 3：Dify 正式适配；Zvec、Open Notebook、Nubase、FlowLong 分别评估，逐项采用或拒绝。
4. Phase 4：在真实团队负载下复核所有已采用组件的稳定性、成本和退出能力。

明确禁止把安装六个项目当作 MVP 进度，或让任何组件保存/批准鸿日当前状态。
