# Brand Project OS 规划文档

本目录保存 2026-07-13 形成并按团队服务器范围修订的 SPEC 规划。当前尚无应用代码，处于“规划完成、等待实施确认”阶段；未经用户明确要求，不启动 Web、数据库、Docker 或其他常驻服务。

## 快速结论

- 产品形态：团队控制、自托管优先的服务器产品，响应式 Web/PWA 为主客户端。
- 生产权威：PostgreSQL 是正式状态、权限、审批、事件、审计和 Outbox 的唯一可写数据库；S3 兼容对象存储保存按 SHA-256 寻址的不可变原件。
- 客户端：Web/PWA、HTTPS API、远程 MCP、CLI 和 Skills 连接同一应用服务；SQLite 仅限开发、测试、演示或只读快照。
- AI 边界：AI 可读取最小 Task Packet、检索回源、生成工作结果和创建 Proposal，不能批准正式事实、决定、约束、负责人、截止时间或提交版本。
- 外部组件：Zvec、Open Notebook、Nubase、FlowLong 和 Dify 只作为可禁用、可替换的派生层或协调层，必须逐项通过许可、隐私、稳定性和退出门。

## 分析

- [项目概览](analysis/project-overview.md)
- [目标模块清单](analysis/module-inventory.md)
- [风险评估](analysis/risk-assessment.md)
- [五个外部组件评估](analysis/open-source-evaluation.md)
- [部署拓扑评估](analysis/deployment-topology-evaluation.md)

## 核心计划

- [任务分解：6 阶段 42 任务](plan/task-breakdown.md)
- [任务依赖与并行 Lane](plan/dependency-graph.md)
- [里程碑与发布门](plan/milestones.md)
- [架构与接口契约](plan/architecture-and-contracts.md)
- [安全与验收](plan/security-and-verification.md)

## 团队服务器专题

- [团队服务器架构](plan/team-server-architecture.md)：部署档位、网络、监控、SLO、发布与扩容门。
- [团队服务器架构图](diagrams/team-server-architecture.drawio)：可编辑 Draw.io 源文件；[PNG 预览](diagrams/team-server-architecture.drawio.png)。
- [数据一致性与可靠性计划](plan/data-consistency-and-reliability.md)：事务、幂等、并发、Outbox、对象状态机、PITR 与恢复。
- [前端与 AI 访问规划](plan/frontend-and-ai-access.md)：Web/PWA、OIDC、远程 MCP、CLI、Skills、Dify 和离线边界。

## 进度与治理

- [总进度](progress/MASTER.md)
- [治理面解析](governance/surface-resolution.md)
- [ADR-0001：团队服务器作为唯一权威运行面](adr/0001-team-server-authority.md)

## 实施范围

| 范围 | 阶段 | 任务 | 结果 |
|:---|:---|---:|:---|
| 团队服务器试运行版 | Phase 1-4 | 29 | 权威数据、身份权限、统一 AI 访问、Web/PWA、监控部署和恢复验收 |
| 外部组件与生产准入 | Phase 5-6 | 13 | 五组件 ADR、选择性集成、BrandBench、真实试运行和第二项目隔离 |
| 合计 | Phase 1-6 | 42 | 完整 SPEC 规划 |

三条主 Lane 并行实施 Phase 1-4 预计 5-7 周；单人顺序完成 Phase 1-4 预计 12-16 周。完整 42 项还需增加五组件 POC、生产演练和 10-30 天自然试运行，必须在 POC 后根据实测重估，不能沿用 12-16 周或用降低验收标准压缩。

## 外部组件定位

| 组件 | 允许角色 | 首要门槛 | 不得承担 |
|:---|:---|:---|:---|
| Zvec | `SearchIndexPort` 可重建增强索引 | Linux、中文、单写多读、重建和 A/B | 权威事实、权限或批准事件 |
| Open Notebook | 内容处理与单向研究工作台 | 引用映射、数据出口、允许列表和退出 | 正式状态、原件角色或批准 |
| Nubase | Auth/Storage/Gateway/Memory 单项 POC | 项目隔离、导出、禁用、PITR/HA 缺口 | 生产权威库或全平台锁定 |
| FlowLong | 复杂人工流程协调 | 真实需求、书面许可、回调幂等和对账 | AI 推理或最终批准写入 |
| Dify | `AIWorkflowPort`、模型/Prompt 编排 | 单内部 Workspace、Schema、外发和许可 | 人工审批、项目真相或直接数据库写入 |

一句话边界：Dify 编排 AI 如何计算，FlowLong 编排人如何流转，Brand Project OS 决定什么正式生效。
