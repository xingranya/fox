# Brand Project OS 团队服务器实施规划 - 总进度

> 任务：建设团队控制、自托管优先的工作操作系统，以服务器权威状态统一支撑 Web/PWA、远程 MCP、CLI、Skills 和可替换 AI 工作流。
> 开始日期：2026-07-13
> 最后更新：2026-07-13
> 模式：`LOCAL_ONLY`（仅表示 SPEC 任务在本地文档追踪，不表示产品只在本机部署）
> 部署目标：团队服务器为唯一正式写入入口；PostgreSQL、S3/MinIO、API、Worker 与 Web/PWA 组成首发运行面。

## 当前结论

- SPEC 范围重规划已完成，等待用户确认后进入实现。
- Phase 1-4 共 29 项，构成团队服务器试运行版；建议三条执行 Lane 并行，预计 5-7 周。
- 单人顺序执行 Phase 1-4 预计 12-16 周，不包含外部组件 POC 和 10-30 天团队试运行。
- Phase 5 对 Zvec、Open Notebook、Nubase、FlowLong、Dify 做隔离 POC；Phase 6 只集成 ADR 批准的组件。
- SQLite 仅用于开发、测试或单机演示，不承担团队生产权威状态。

## 参考文档

- [项目概览](../analysis/project-overview.md)
- [目标模块清单](../analysis/module-inventory.md)
- [风险评估](../analysis/risk-assessment.md)
- [部署拓扑评估](../analysis/deployment-topology-evaluation.md)
- [开源项目评估](../analysis/open-source-evaluation.md)
- [任务分解](../plan/task-breakdown.md)
- [依赖图](../plan/dependency-graph.md)
- [里程碑](../plan/milestones.md)
- [团队服务器架构](../plan/team-server-architecture.md)
- [数据一致性与可靠性](../plan/data-consistency-and-reliability.md)
- [前端与 AI 访问](../plan/frontend-and-ai-access.md)
- [架构与接口契约](../plan/architecture-and-contracts.md)
- [安全与验收](../plan/security-and-verification.md)
- [团队服务器权威 ADR](../adr/0001-team-server-authority.md)

## 阶段汇总

| 阶段 | 名称 | 任务 | 完成 | 进度 | 交付边界 |
|:---|:---|---:|---:|:---|:---|
| 1 | 契约、身份与安全基线 | 6 | 0 | 0% | 冻结服务器权威、租户、身份和接口契约 |
| 2 | 权威数据与可靠性脊柱 | 7 | 0 | 0% | PostgreSQL、对象存储、Outbox、检索与 PITR |
| 3 | 团队治理与 AI 统一访问 | 8 | 0 | 0% | OIDC、RBAC、审批、HTTP、MCP、CLI 与 Skills |
| 4 | Web/PWA 与团队服务器试运行版 | 8 | 0 | 0% | 团队前端、离线边界、部署运维和金标旅程 |
| 5 | 五个外部组件隔离 POC | 7 | 0 | 0% | Zvec、Open Notebook、Nubase、FlowLong、Dify 决策 |
| 6 | 选择性集成与生产验证 | 6 | 0 | 0% | 降级、模型治理、演练、团队试运行和生产准入 |
| **合计** |  | **42** | **0** | **0%** | Phase 1-4 为 29 项团队服务器试运行版 |

## 阶段清单

- [ ] Phase 1：契约、身份与安全基线（0/6）- [详情](phase-1-foundation.md)
- [ ] Phase 2：权威数据与可靠性脊柱（0/7）- [详情](phase-2-evidence-core.md)
- [ ] Phase 3：团队治理与 AI 统一访问（0/8）- [详情](phase-3-governed-ai.md)
- [ ] Phase 4：Web/PWA 与团队服务器试运行版（0/8）- [详情](phase-4-mvp.md)
- [ ] Phase 5：五个外部组件隔离 POC（0/7）- [详情](phase-5-open-source-poc.md)
- [ ] Phase 6：选择性集成与生产验证（0/6）- [详情](phase-6-pilot.md)

## 当前状态

**活动阶段**：无，SPEC 范围重规划完成，等待用户确认执行  
**活动任务**：无；确认后从可并行的 F1.1 与 F1.2 开始  
**阻塞项**：当前工作区缺少鸿日 V5 数据副本，F1.1 无法建立真实黄金样本  
**后置门禁**：F5.5 在取得 FlowLong 书面许可结论前不得执行源码集成  
**规划检查点**：SPEC Phase 5a，尚未进入实现

## 治理状态

**共享指令面**：`AGENTS.md`  
**Claude 指令面**：`CLAUDE.md`  
**其他平台规则**：无  
**架构决策面**：`docs/adr/`  
**Memory 面**：不可用，不写入  
**仓库 Memory 回退路径**：无，未获用户批准  
**正式事实来源**：服务器 PostgreSQL 中的权威事件与人工审批记录；对象存储原件按 SHA-256 和版本证明内容  
**派生系统边界**：检索索引、模型摘要、Open Notebook、Nubase Memory、FlowLong 与 Dify 均不得覆盖正式状态

## 自适应控制状态

| 字段 | 当前值 |
|:---|:---|
| 活动阶段 | 待确认，下一阶段为 Phase 1 |
| drift_score | 0 |
| strategy | 团队服务器权威、自底向上契约优先、外部组件隔离 POC |
| threshold_annotate | 2 |
| threshold_replan | 3 |
| threshold_rescope | 4 |
| total_tasks | 6 |
| completed_tasks | 0 |
| last_updated | 2026-07-13 |

### 各阶段阈值

| 阶段 | 任务数 | 标注 | 重计划 | 重定范围 |
|:---|---:|---:|---:|---:|
| 1 | 6 | 2 | 3 | 4 |
| 2 | 7 | 2 | 3 | 5 |
| 3 | 8 | 2 | 4 | 5 |
| 4 | 8 | 2 | 4 | 5 |
| 5 | 7 | 2 | 3 | 5 |
| 6 | 6 | 2 | 3 | 4 |

### 任务遥测日志

每项任务完成后，先写入本表，再更新对应阶段文件和阶段计数。

| 任务 | 估算 | 实际 | 工期差 | SUPER 分数 | SUPER 变化 | 未计划依赖 | 任务漂移 |
|:---|:---|:---|---:|---:|---:|---:|---:|
| - | - | - | - | - | - | - | - |

## 下一步

1. 由用户确认 42 项团队服务器方案及 Phase 1-4 的试运行版边界。
2. 提供鸿日 `_system`、V5 索引和少量脱敏原始文件的可读副本，解除 F1.1 阻塞。
3. 确认后并行启动 F1.1 与 F1.2；在服务器权威 ADR 固定前不实现正式写入。
4. 依次完成 Phase 1-4 的 29 项任务并通过团队金标旅程，再启动五组件隔离 POC。
5. Dify 仅通过 `AIWorkflowPort` 参与可替换工作流，并与直接 Worker 做 A/B 验证。

## 会话日志

| 日期 | 会话 | 摘要 |
|:---|:---|:---|
| 2026-07-13 | 初始规划 | 读取完整需求并核验 Zvec、Nubase、Open Notebook、FlowLong，形成个人本地版 29 项初稿。 |
| 2026-07-13 | 团队服务器范围变更 | 将目标改为团队控制、自托管优先；服务器成为唯一正式写入入口，采用 PostgreSQL、S3/MinIO、Web/PWA、远程 MCP/API、CLI 与 Skills。 |
| 2026-07-13 | Dify 范围变更 | 将 Dify 纳入第五个隔离 POC，以 `AIWorkflowPort` 对接并与直接 Worker A/B；规划重构为 6 阶段、42 项，等待用户确认执行。 |
