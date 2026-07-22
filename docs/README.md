# Brand Project OS 规格文档

本目录保存 Brand Project OS 的产品、分析、架构、验证和执行 SPEC。2026-07-13 依据[场景化需求澄清与产品反馈](../20260713_品牌AI长期项目协作系统_场景化需求澄清与产品反馈_v0.1(1).md)完成范围重定：当前先用鸿日项目证明 Fox 本地单用户真实提效，再决定是否进入团队服务器化。

当前已有 Phase 1 本地核心。Phase 0 已完成 7/7；Phase 1 已完成本地工作空间、SQLite 权威状态和鸿喜达来源导入对账。现在进入 F1.4 的会议增量摄取。未经用户明确要求，不启动桌面应用、Web、数据库、Docker 或其他常驻服务。

## 当前结论

- **第一用户**：Fox。
- **第一验证项目**：鸿日。
- **当前产品形态**：本地优先、单用户、轻量结构化存储、统一本地 API/MCP/CLI、简单查看与确认界面。
- **当前验证目标**：正确理解品牌项目、避免非法状态升级、支持增量会议、统一多模型上下文，并通过真实工作和匿名品牌质量评审证明提效。
- **客户端候选**：可以基于 OpenWork MIT 社区核心改造本地 Brand OS Desktop，但 OpenWork Server、OpenCode 会话和 SQLite/JSON 运行态不是业务真相源，服务端化不是 MVP 前置。
- **远期候选**：团队服务器、PostgreSQL、S3/MinIO、OIDC、RLS、Outbox、高可用、灾备和完整团队分发，统一标记为 `future-candidate`、`not-approved-for-current-mvp`、`review-after-hongri-pilot`。
- **外部组件**：Zvec、Open Notebook、Nubase、FlowLong 和 Dify 只保留研究与端口设计，不进入当前 MVP 关键路径。

## 需求输入与产品边界

- [完整问题与技术需求说明](../AI品牌营销长期项目协作系统_完整问题与技术需求说明_20260713.md)
- [场景化需求澄清与产品反馈](../20260713_品牌AI长期项目协作系统_场景化需求澄清与产品反馈_v0.1(1).md)
- [两条需求线拆分说明](analysis/two-track-requirements.md)
- [鸿日本地 MVP 一页边界](analysis/hongri-local-mvp-boundary.md)
- [产品定义](../PRODUCT.md)

## 分析

- [项目概览](analysis/project-overview.md)
- [目标模块清单](analysis/module-inventory.md)
- [风险评估](analysis/risk-assessment.md)
- [部署拓扑评估](analysis/deployment-topology-evaluation.md)
- [六个开源项目总评估（含 OpenWork）](analysis/open-source-evaluation.md)
- [OpenWork 客户端采用评估](analysis/openwork-client-evaluation.md)

## 当前执行计划

- [任务分解](plan/task-breakdown.md)
- [任务依赖与并行 Lane](plan/dependency-graph.md)
- [里程碑与决策门](plan/milestones.md)
- [Phase 0 黄金测试集与验收方式](plan/phase-0-golden-test-set.md)
- [运行时品牌 Agent 与会议解释协议](plan/runtime-brand-agent-and-meeting-protocol.md)
- [安全与验收计划](plan/security-and-verification.md)
- [总进度](progress/MASTER.md)
- [Phase 0：边界、协议与黄金测试](progress/phase-0-boundary-and-bench.md)
- [Phase 1：鸿日本地单用户原型](progress/phase-1-hongri-local-prototype.md)
- [Phase 2：鸿日真实工作连续验证](progress/phase-2-real-work-validation.md)
- [Phase 3：团队服务器化决策门](progress/phase-3-team-server-decision.md)

## Phase 1 当前实现

- [F1.1 本地工作空间、只读证据与备份恢复](phase1/local-workspace.md)
- [F1.2 SQLite 权威事件、人工确认与当前投影](phase1/sqlite-authority-store.md)
- [F1.3 来源 Manifest、版本、旧 ID、缺口与去重对账](phase1/source-import-and-reconciliation.md)

## Phase 0 当前证据

- [Example 脱敏样本登记](phase0/example-sample-register.md)
- [远程鸿喜达资料筛选结论](phase0/remote-hongxida-source-selection.md)
- [已知资料缺口](phase0/known-source-gaps.md)
- [BrandBench 匿名评审指引](phase0/brandbench-review-guide.md)
- [BrandBench 首轮人工基线结果](phase0/brandbench-baseline-result.md)
- [BrandBench 第二轮人工评审结果](phase0/brandbench-second-round-result.md)
- [技术实施就绪与 OpenWork 交接](phase0/technical-readiness-and-openwork-handoff.md)
- [阶段成果简报](phase0/leadership-progress-brief.md)

远程鸿喜达资料已经按现有 SSOT 和五态治理筛出当前控制层、执行层和产品二次包装工作源。完整路径、哈希、原文和匿名评审候选只保存在本地 `.work/`，不进入 Git。

当前执行顺序：

1. Phase 0：冻结边界、分类、运行时协议和 10-20 个黄金用例。
2. Phase 1：构建鸿日本地单用户原型。
3. Phase 2：在真实连续工作中验证提效与品牌质量。
4. Phase 3：基于证据决定维持本地、有限共享或进入团队服务器化。

## 架构与接口

- [架构与接口契约](plan/architecture-and-contracts.md)
- [前端与 AI 访问规划](plan/frontend-and-ai-access.md)
- [OpenWork 深度集成计划](plan/openwork-deep-integration.md)
- [数据一致性与可靠性计划](plan/data-consistency-and-reliability.md)

当前实现必须保持领域语义、端口和 Schema 可迁移，但不得为了远期部署提前建设完整服务器基础设施。

## 远期架构候选

以下文档保留上一轮服务器化研究，用于 Phase 3 复审，不代表当前批准实现：

- [团队服务器架构](plan/team-server-architecture.md)
- [团队服务器架构图](diagrams/team-server-architecture.drawio)与 [PNG 预览](diagrams/team-server-architecture.drawio.png)
- [ADR-0001：团队服务器权威运行面候选](adr/0001-team-server-authority.md)
- [ADR-0002：OpenWork 客户端条件采用](adr/0002-openwork-primary-client.md)
- [ADR-0003：本地优先的鸿日价值验证](adr/0003-local-first-hongri-validation.md)

## 治理

- [治理面解析](governance/surface-resolution.md)
- 开发协作规则：[`AGENTS.md`](../AGENTS.md)
- Claude Code 适配规则：[`CLAUDE.md`](../CLAUDE.md)
- 运行时品牌行为：[运行时品牌 Agent 与会议解释协议](plan/runtime-brand-agent-and-meeting-protocol.md)

开发仓库 `AGENTS.md` 与运行时品牌 Agent 规则是两个不同的治理面。业务事实不写入代理说明、Skills 或聊天记忆；当前没有获准的仓库内 Memory 文件。

## 外部组件定位

| 组件 | 可能角色 | 当前状态 | 不得承担 |
|:---|:---|:---|:---|
| OpenWork | 本地桌面壳与 Agent 工作入口 | 条件采用候选 | 项目真相、业务批准、MVP 服务端前置 |
| OpenCode | `AgentRuntimePort` 的候选适配器 | 可替换运行时 | 正式状态、业务批准 |
| Zvec | `SearchIndexPort` 可重建索引 | 试点后 POC 候选 | 当前有效性判断、权威事实 |
| Open Notebook | 内容处理与研究工作台 | 试点后 POC 候选 | 正式状态、原件或批准 |
| Nubase | 单项基础能力参考 | 试点后 POC 候选 | 生产权威平台 |
| FlowLong | 复杂人工流程协调 | 许可门后的远期候选 | AI 推理、最终批准 |
| Dify | `AIWorkflowPort` 编排 | 可选后置适配器 | 当前 MVP 前置、项目真相 |

## 当前一票否决

- 虚构事实。
- 把讨论升级成决定。
- 把暂定日期升级成死线。
- 把过期方案当作当前方向。
- 重要结论无法回源。
- 未经确认改变正式状态。
- 在探索模式下强行制造唯一答案。
