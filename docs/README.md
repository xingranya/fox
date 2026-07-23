# Brand Project OS 规格文档

本目录保存产品、架构、数据治理、测试和执行 SPEC。当前目标是交付 FoxWork：员工只使用这个基于 OpenWork 的公司客户端，服务器运行 Brand Project OS Service；AI 只能提出候选，正式变化必须由授权人员确认并留痕。FoxWork 的员工界面只使用简体中文，不提供语言切换或英文回退。

当前完成 28/49 项。Phase 0、Phase 1 和 Phase 2 已完成，F3.1 已完成，下一项是 F3.2。Fox 已批准小团队托管部署，以及 99.5% 月可用性、PostgreSQL RPO 不高于 5 分钟、核心服务 RTO 不高于 60 分钟的内部目标；这些目标仍需 Phase 4 实测。远程 MCP/Skills、Dify 和四个开源组件按 Phase 3 的顺序接入。

## 当前结论

- 第一用户：Fox；第一验证项目：鸿日。
- 员工唯一客户端：FoxWork，基于 OpenWork 的公司定制版。
- 员工界面：只使用简体中文，不提供语言切换；缺失文案不得回退为英文或内部键名。
- 单一安装包：OpenCode Runtime、Sidecar 和本机桥接随客户端交付。
- 团队后端：Brand Project OS Service，不是第二个员工软件。
- 团队权威：PostgreSQL 事件/审批/投影和对象存储原件版本。
- AI 接口：MCP Gateway、Skills 和 CLI/API。MCP 不是数据库或审批核心。
- AI、MCP、Skill、Dify、FlowLong 和服务账号只能读取或创建 Proposal。
- 不建设第二个 Web/PWA 或桌面客户端。
- FoxWork 全量文件同步、NAS、个人工作区和 PPT 合版仍是独立需求线。

## 权威决策

- [ADR-0003：Phase 0-1 本地验证与业务语义](adr/0003-local-first-hongri-validation.md)
- [ADR-0004：公司定制 OpenWork 是唯一员工客户端](adr/0004-openwork-single-client.md)
- [ADR-0005：单一客户端与服务器权威服务](adr/0005-single-client-server-authority.md)
- [ADR-0006：FoxWork 发行名与简体中文界面](adr/0006-foxwork-name-and-chinese-ui.md)
- [ADR-0001：旧服务器候选决策，已被 ADR-0005 取代](adr/0001-team-server-authority.md)
- [ADR-0002：旧 OpenWork 候选决策，已被 ADR-0004 取代](adr/0002-openwork-primary-client.md)

## 需求与分析

- [完整问题与技术需求说明](../AI品牌营销长期项目协作系统_完整问题与技术需求说明_20260713.md)
- [场景化需求澄清与产品反馈](../20260713_品牌AI长期项目协作系统_场景化需求澄清与产品反馈_v0.1(1).md)
- [两条需求线拆分说明](analysis/two-track-requirements.md)
- [Phase 1 鸿日本地纵切边界](analysis/hongri-local-mvp-boundary.md)
- [项目概览](analysis/project-overview.md)
- [目标模块清单](analysis/module-inventory.md)
- [风险评估](analysis/risk-assessment.md)
- [部署拓扑评估](analysis/deployment-topology-evaluation.md)
- [开源组件评估](analysis/open-source-evaluation.md)
- [OpenWork 唯一客户端评估](analysis/openwork-client-evaluation.md)
- [BISHENG 后续接入评估](analysis/bisheng-integration-evaluation.md)
- [F3.1 SQLite 到 PostgreSQL/S3 一次性迁移](phase3/data-cutover.md)
- [OpenWork OW-L0 技术选型记录](phase1/openwork-ow-l0-evaluation.md)

## 活动计划

- [任务分解](plan/task-breakdown.md)
- [依赖图](plan/dependency-graph.md)
- [里程碑](plan/milestones.md)
- [架构与接口契约](plan/architecture-and-contracts.md)
- [团队服务器架构](plan/team-server-architecture.md)
- [数据一致性与可靠性](plan/data-consistency-and-reliability.md)
- [客户端与 AI 访问](plan/frontend-and-ai-access.md)
- [OpenWork 深度集成](plan/openwork-deep-integration.md)
- [BISHENG 条件接入 SPEC（当前 49 项之后）](plan/bisheng-integration-spec.md)
- [安全与验收](plan/security-and-verification.md)
- [Phase 0 黄金测试集](plan/phase-0-golden-test-set.md)
- [运行时品牌 Agent 与会议解释协议](plan/runtime-brand-agent-and-meeting-protocol.md)

## 进度

- [总进度](progress/MASTER.md)
- [Phase 0：边界、协议与黄金测试](progress/phase-0-boundary-and-bench.md)
- [Phase 1：单一客户端本地纵向切片](progress/phase-1-hongri-local-prototype.md)
- [Phase 2：服务器权威基础](progress/phase-2-server-authority-foundation.md)
- [Phase 3：客户端联网、MCP、Skills 与工作流](progress/phase-3-connected-client-and-integrations.md)
- [Phase 4：团队试点与生产准入](progress/phase-4-team-pilot-and-production-gate.md)

## Phase 1 已实现

- [F1.1 本地工作空间、只读证据与备份恢复](phase1/local-workspace.md)
- [F1.2 SQLite 权威事件、人工确认与当前投影](phase1/sqlite-authority-store.md)
- [F1.3 来源 Manifest、版本、旧 ID、缺口与去重](phase1/source-import-and-reconciliation.md)
- [F1.4 会议增量摄取与冲突](phase1/meeting-ingest-and-reconciliation.md)
- [F1.5 Proposal 生命周期与历史](phase1/proposal-lifecycle-and-history.md)
- [F1.6 证据关系与回源](phase1/evidence-query-and-provenance.md)
- [F1.7 Task Packet 与 Agent 运行](phase1/task-packet-and-agent-run.md)
- [F1.8 CLI、stdio MCP 与模型切换](phase1/local-cli-mcp-and-model-switching.md)
- [F1.9 前置：OpenWork OW-L0](phase1/openwork-ow-l0-evaluation.md)

## Phase 2 已实现

- F2.1 服务器配置、组件职责和健康基线：见[架构与接口契约](plan/architecture-and-contracts.md#f21-服务器基线已完成)与[阶段进度](progress/phase-2-server-authority-foundation.md)。
- [F2.2 PostgreSQL 权威事件、审批和投影](phase2/postgresql-authority-store.md)
- [F2.3 S3 兼容原件版本、哈希和准入状态机](phase2/object-evidence-store.md)
- [F2.4 OIDC 员工身份与服务器会话](phase2/oidc-identity-and-sessions.md)
- [F2.5 项目 RBAC、保密级别与 RLS](phase2/project-authorization-and-rls.md)
- [F2.6 幂等、乐观锁和冲突差异](phase2/write-consistency-and-conflicts.md)
- [F2.7 审计、Outbox/Inbox 和后台任务边界](phase2/audit-outbox-inbox.md)
- [F2.8 版本化 HTTP API 与 OpenAPI](phase2/http-api-and-openapi.md)
- [F2.9 可观测性、健康和告警](phase2/observability-and-alerting.md)
- [F2.10 服务器恢复演练与阶段门](phase2/server-recovery-and-gate.md)

## 外部组件

| 组件 | 端口 | 当前任务 | 不得承担 |
|:---|:---|:---|:---|
| OpenWork/OpenCode | Desktop / `AgentRuntimePort` | F1.9-F1.10、F3.2-F3.5 | 正式状态、业务批准 |
| Dify | `AIWorkflowPort` | F3.8 | 项目真相、人工批准 |
| Zvec | `SearchIndexPort` | F3.9，可拒绝 | 当前有效性判断、权威事实 |
| Open Notebook | `ContentProcessingPort` | F3.10，可拒绝 | 原件、正式状态 |
| Nubase | `MemoryPort` | F3.11，可拒绝 | 当前状态、批准 |
| FlowLong | `ApprovalWorkflowPort` | F3.12，先过许可门 | 最终批准、AI 推理 |
| BISHENG | `AIWorkflowPort` 候选实现 | Phase 4 试点后单独评审 | 正式状态、人工批准、第二客户端 |

## 治理

- [治理面解析](governance/surface-resolution.md)
- 开发协作规则：[`AGENTS.md`](../AGENTS.md)
- Claude Code 适配规则：[`CLAUDE.md`](../CLAUDE.md)
- 运行时品牌行为：[品牌 Agent 与会议解释协议](plan/runtime-brand-agent-and-meeting-protocol.md)

业务事实不写入 AGENTS、Skills 或聊天记忆。当前没有获准的仓库内 Memory 文件。

## 一票否决

1. 虚构事实或证据。
2. 把讨论升级成决定。
3. 把暂定日期升级成死线。
4. 把过期方案当作当前方向。
5. 重要结论无法回源。
6. 未经有权限员工确认改变正式状态。
7. 在探索模式下强行制造唯一答案。
8. 出现两个可写正式状态源或静默覆盖并发写入。
