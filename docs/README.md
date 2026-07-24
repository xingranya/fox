# FoxWork / Brand Project OS 规格文档

本目录是当前产品、架构、数据、安全、测试和实施进度的统一入口。当前活动方案为：员工只安装 FoxWork；公司服务器自托管 OpenWork Den 与 Brand Project OS Service；普通员工可在公司 Den 入口自助注册并登录一套账号，进入唯一公司组织和远程工作区，使用公司下发的模型、MCP、Skills 和获授权的品牌项目工作区。

当前完成 29/56 项。Phase 0、Phase 1、Phase 2 和 F3.1-F3.2 已完成，当前任务是 F3.3 Den 与远程 Worker 生产部署基线。Fox 已批准“小团队托管部署”档位，以及 99.5% 月可用性、PostgreSQL RPO 不高于 5 分钟、核心服务 RTO 不高于 60 分钟的内部目标；这些目标仍需 Phase 4 实测。Den MySQL 与远程 Worker 的恢复目标须在 F3.3/F4.8 单独确认，不能套用 PostgreSQL 的数据目标。

## 产品边界

| 部分 | 当前定义 | 不承担 |
|:---|:---|:---|
| FoxWork | 公司定制版 OpenWork，唯一员工客户端，全部员工文案使用自然简体中文 | 不保存正式项目状态，不形成第二套账号 |
| OpenWork Den | 单组织自助注册/登录、成员/团队、远程工作区/Worker、桌面交接、MCP、Skills、共享模型和桌面策略 | 不保存品牌项目原件，不批准业务状态 |
| Brand Project OS Service | 项目工作区、资料准入、多媒体处理、证据、Proposal、人工确认、Task Packet 和审计 | 不再提供独立员工登录或第二个客户端 |
| PostgreSQL / S3 | 正式事件、审批、投影、原件版本和 SHA-256 | 不保存模型聊天作为事实，不与本地 SQLite 长期双写 |
| OpenCode / Agent Runtime | AI 会话、工具执行和本机能力桥接 | Tool Permission 不等于业务批准 |
| Dify 与可选组件 | 工作流、解析、检索、记忆或人工待办的可替换适配器 | 不直写正式表，不形成第二套真相 |

Brand Project OS 是 FoxWork 背后的业务能力，不是第二个软件。Den Web 是 FoxWork 注册、登录、授权和管理链路的一部分，不是员工日常处理品牌工作的第二个入口。普通员工自助注册或登录后回到 FoxWork；首次访问 Brand OS 时按可信 Den 身份建立内部映射，不再要求管理员预建第二套账号，项目权限仍按成员或团队单独授予。Den 后台管理员网页也必须全部使用简体中文。

## 当前决策

- [ADR-0008：单组织自助注册与 Den 远程工作区](adr/0008-den-self-registration-and-remote-workspaces.md)
- [ADR-0007：采用 OpenWork Den 统一账号与 AI 控制面](adr/0007-adopt-openwork-den-control-plane.md)
- [ADR-0006：发行名固定为 FoxWork，员工端与管理员后台只用简体中文](adr/0006-foxwork-name-and-chinese-ui.md)
- [ADR-0005：单一客户端与服务器权威业务服务](adr/0005-single-client-server-authority.md)
- [ADR-0004：OpenWork 是唯一员工客户端](adr/0004-openwork-single-client.md)
- [ADR-0003：Phase 0-1 本地验证与业务语义](adr/0003-local-first-hongri-validation.md)
- [ADR-0001：旧服务器候选决策，已被 ADR-0005 取代](adr/0001-team-server-authority.md)
- [ADR-0002：旧 OpenWork 候选决策，已被 ADR-0004 和 ADR-0007 取代](adr/0002-openwork-primary-client.md)

发生冲突时，以 ADR-0008、ADR-0007、ADR-0006、ADR-0005 和当前任务分解为准。历史文档中的“不部署 Den”“只使用 `ee/**` 外代码”“自建团队连接”或“第二套 Brand OS 登录”不再是活动方案。

## 需求与分析

- [完整问题与技术需求说明](../AI品牌营销长期项目协作系统_完整问题与技术需求说明_20260713.md)
- [场景化需求澄清与产品反馈](../20260713_品牌AI长期项目协作系统_场景化需求澄清与产品反馈_v0.1(1).md)
- [两条需求线拆分说明](analysis/two-track-requirements.md)
- [项目概览](analysis/project-overview.md)
- [目标模块清单](analysis/module-inventory.md)
- [风险评估](analysis/risk-assessment.md)
- [部署拓扑评估](analysis/deployment-topology-evaluation.md)
- [开源组件评估](analysis/open-source-evaluation.md)
- [OpenWork/FoxWork 客户端评估](analysis/openwork-client-evaluation.md)
- [BISHENG 后续接入评估](analysis/bisheng-integration-evaluation.md)

## 活动计划

- [任务分解：5 阶段 56 项](plan/task-breakdown.md)
- [任务依赖图](plan/dependency-graph.md)
- [里程碑与阶段门](plan/milestones.md)
- [架构与接口契约](plan/architecture-and-contracts.md)
- [团队服务器架构](plan/team-server-architecture.md)
- [数据一致性与可靠性](plan/data-consistency-and-reliability.md)
- [FoxWork 与 AI 访问](plan/frontend-and-ai-access.md)
- [OpenWork/Den 深度集成](plan/openwork-deep-integration.md)
- [安全与验收](plan/security-and-verification.md)
- [Phase 0 黄金测试集](plan/phase-0-golden-test-set.md)
- [运行时品牌 Agent 与会议解释协议](plan/runtime-brand-agent-and-meeting-protocol.md)
- [BISHENG 条件接入 SPEC：Phase 4 后候选](plan/bisheng-integration-spec.md)

## 实施证据

- [F3.1 SQLite 到 PostgreSQL/S3 一次性迁移](phase3/data-cutover.md)
- [F3.2 OpenWork Den 源码、许可与自托管技术门](phase3/openwork-den-self-host-gate.md)
- [F2.1-F2.10 服务器权威基础](progress/phase-2-server-authority-foundation.md)
- [F1.1-F1.10 本地纵向切片](progress/phase-1-hongri-local-prototype.md)
- [OW-L0 历史技术选型记录](phase1/openwork-ow-l0-evaluation.md)

## 当前任务顺序

1. F3.3：把 Den Web、Den API、独立 MySQL、远程工作区和 Worker 做成可重复部署、升级、备份和回滚的内部服务。
2. F3.4-F3.6：统一 FoxWork 自助注册/登录、公司组织、远程工作区、Brand OS 第一方 OAuth/OIDC 和项目工作区映射；删除第二套账号和旧团队连接，完成 Den 员工页和后台管理页中文化。
3. F3.7-F3.10：完成通用资料上传、图片/视频/录音/PPT/Office/PDF 处理、状态证据视图和人工确认。
4. F3.11-F3.13：完成本机与远程工作区权限边界、公司 MCP、Skills、共享模型和桌面策略下发。
5. F3.14-F3.18：接入 Dify，并逐项评估 Zvec、Open Notebook、Nubase 和 FlowLong。
6. F3.19：通过单账号自助注册、全中文管理面、远程工作区、多媒体、MCP/Skills/模型、权限和故障端到端验收；通过后再统一同步 Wiki。
7. F4.1-F4.10：小团队真实试点、生产稳定性、签名更新和 Go/No-Go。

## 外部组件

| 组件 | 端口或职责 | 当前任务 | 当前结论 |
|:---|:---|:---|:---|
| OpenWork Den | 账号与 AI 控制面 | F3.2-F3.6、F3.12-F3.13 | 已通过采用门，按 FSL 内部自托管和修改 |
| Dify | `AIWorkflowPort` | F3.14 | 计划接入，只读或创建 Proposal |
| Zvec | `SearchIndexPort` | F3.15 | 与 PostgreSQL FTS 对比后可拒绝 |
| Open Notebook | `ContentProcessingPort` | F3.16 | 只复用解析能力，不嵌入第二套 Notebook 前端 |
| Nubase | `MemoryPort` | F3.17 | 只保存可删除记忆，不进入当前状态 |
| FlowLong | `ApprovalWorkflowPort` | F3.18 | 只路由人的待办，最终确认仍由 Brand OS 完成 |
| BISHENG | `AIWorkflowPort` 候选 | Phase 4 后另行 rescope | 不计入当前 56 项 |

## 进度与治理

- [总进度与执行遥测](progress/MASTER.md)
- [Phase 0](progress/phase-0-boundary-and-bench.md)
- [Phase 1](progress/phase-1-hongri-local-prototype.md)
- [Phase 2](progress/phase-2-server-authority-foundation.md)
- [Phase 3](progress/phase-3-connected-client-and-integrations.md)
- [Phase 4](progress/phase-4-team-pilot-and-production-gate.md)
- [治理面解析](governance/surface-resolution.md)
- [开发协作规则](../AGENTS.md)

业务事实不写入 `AGENTS.md`、Skill、模型聊天或 Den/OpenWork Session。当前没有获准的仓库内 Memory 文件。

## 一票否决

1. 虚构事实或证据。
2. 把讨论升级成决定。
3. 把暂定日期升级成死线。
4. 把过期方案当作当前方向。
5. 重要结论无法回源。
6. 未经有权限员工确认改变正式状态。
7. 在探索模式下强行制造唯一答案。

另有独立技术发布阻断：出现两个可写正式状态源、静默覆盖并发写入，或把 Den/工作流运行态当作业务真相。它不改变已冻结的七项业务一票否决数量。
