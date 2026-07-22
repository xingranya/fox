# Brand Project OS 单一客户端与团队服务 - 总进度

> 任务：交付基于 OpenWork 的唯一员工客户端，以及公司服务器上的 Brand Project OS Service，让员工和不同 Agent 使用同一项目状态、证据、人工确认和工作流能力。
> 开始日期：2026-07-13
> 最后更新：2026-07-23
> 模式：`LOCAL_ONLY`（只表示 SPEC 在本地 Markdown 追踪，不表示产品只能本地运行）
> 第一用户：Fox
> 第一验证项目：鸿日
> 需求基线：`20260713_品牌AI长期项目协作系统_场景化需求澄清与产品反馈_v0.1(1).md`

## 当前结论

- 当前活动方案为 5 阶段 49 项：Phase 0 和 Phase 1 已完成，F2.1-F2.5 已通过，当前执行 F2.6 幂等、乐观锁和冲突差异；MCP/Skills、工作流接入和团队试点仍按后续阶段过门。
- 旧 42 项服务器草案和 32 项本地价值验证方案均保留在 Git 历史中，不再作为当前依赖或完成度来源。
- 当前主线不是通用项目管理、企业知识库或完整 RAG，而是长期品牌项目的状态与品牌认知协作层。
- 公司定制版 OpenWork 是唯一员工客户端。Brand Project OS 是当前项目名，最终发行名可以另定；OpenCode Runtime、Sidecar 和本机桥接仍随同一安装包分发。
- Brand Project OS Service 部署在公司服务器。PostgreSQL 保存团队正式事件、审批和投影，对象存储保存原件版本；客户端不得直连存储。
- MCP Gateway、Skills 和 Dify/外部组件适配都调用同一应用服务。MCP 是 AI 接口，不是数据库；AI 和服务账号无人工批准权。
- Phase 1 SQLite 在迁移前继续承载本地验证；Phase 3 一次性切换后退出正式写入，不形成双主。
- 七项一票否决适用于所有模型/协议/版本；任何一项出现即阻断阶段门。
- BISHENG 评估与条件接入 SPEC 已进入当前 49 项之后的候选池；它不影响 F2.2-F4.9 完成度，只有 Phase 4 试点结论和 Fox 单独批准后才能正式 rescope。

## 参考文档

### 当前活动 SPEC

- [任务分解](../plan/task-breakdown.md)
- [依赖图](../plan/dependency-graph.md)
- [里程碑](../plan/milestones.md)
- [Phase 0：边界、协议与黄金测试](phase-0-boundary-and-bench.md)
- [Phase 1：单一客户端本地纵向切片](phase-1-hongri-local-prototype.md)
- [Phase 2：服务器权威基础](phase-2-server-authority-foundation.md)
- [Phase 3：客户端联网、MCP、Skills 与工作流](phase-3-connected-client-and-integrations.md)
- [Phase 4：团队试点与生产准入](phase-4-team-pilot-and-production-gate.md)

### 分析与架构依据

- [项目概览](../analysis/project-overview.md)
- [目标模块清单](../analysis/module-inventory.md)
- [风险评估](../analysis/risk-assessment.md)
- [部署拓扑评估](../analysis/deployment-topology-evaluation.md)
- [开源项目评估](../analysis/open-source-evaluation.md)
- [OpenWork 唯一客户端评估](../analysis/openwork-client-evaluation.md)
- [OpenWork 深度集成计划](../plan/openwork-deep-integration.md)
- [F2.2 PostgreSQL 权威事件、审批和投影](../phase2/postgresql-authority-store.md)
- [F2.3 S3 兼容原件版本、哈希和准入状态机](../phase2/object-evidence-store.md)
- [F2.4 OIDC 员工身份与服务器会话](../phase2/oidc-identity-and-sessions.md)
- [F2.5 项目 RBAC、保密级别与 RLS](../phase2/project-authorization-and-rls.md)
- [BISHENG 接入评估](../analysis/bisheng-integration-evaluation.md)
- [BISHENG 条件接入 SPEC](../plan/bisheng-integration-spec.md)
- [ADR-0005：单一客户端与服务器权威服务](../adr/0005-single-client-server-authority.md)

服务器架构、数据一致性、安全和 OpenWork 集成文档均按 ADR-0005 执行。历史分析中的 `future-candidate`、`not-approved-for-current-mvp` 和 `review-after-hongri-pilot` 只描述旧方案，不再决定当前实施顺序。

## 阶段汇总

| 阶段 | 名称 | 任务 | 完成 | 进度 | 交付边界 |
|:---|:---|---:|---:|:---|:---|
| 0 | 边界、协议与黄金测试先行 | 7 | 7 | 100% | 一页边界、鸿日样本、分类标准、品牌 Agent 协议、10-20 黄金用例与 BrandBench |
| 1 | 单一客户端本地纵向切片 | 10 | 10 | 100% | 本地领域核心、CLI/MCP、OpenWork 单安装包和鸿日桌面闭环 |
| 2 | 服务器权威基础 | 10 | 5 | 50% | PostgreSQL、对象存储、OIDC/RBAC、一致性、API、审计和恢复 |
| 3 | 客户端联网与集成 | 13 | 0 | 0% | Desktop 联网、MCP、Skills、Dify、Zvec、Open Notebook、Nubase、FlowLong |
| 4 | 团队试点与生产准入 | 9 | 0 | 0% | 真实团队工作、并发、故障恢复、安全、SLO、签名分发和 Go/No-Go |
| **合计** |  | **49** | **22** | **45%** | 当前执行 F2.6，后续阶段依次过门 |

## 阶段清单

- [x] Phase 0：边界、协议与黄金测试先行（7/7）- [详情](phase-0-boundary-and-bench.md)
- [x] Phase 1：单一客户端本地纵向切片（10/10）- [详情](phase-1-hongri-local-prototype.md)
- [ ] Phase 2：服务器权威基础（5/10）- [详情](phase-2-server-authority-foundation.md)
- [ ] Phase 3：客户端联网、MCP、Skills 与工作流（0/13）- [详情](phase-3-connected-client-and-integrations.md)
- [ ] Phase 4：团队试点与生产准入（0/9）- [详情](phase-4-team-pilot-and-production-gate.md)

## 当前状态

**活动阶段**：Phase 2：服务器权威基础
**活动任务**：F2.6：实现幂等、乐观锁和冲突差异
**阻塞项**：当前无业务阻塞；内部 macOS 包尚未签名、公证，仍不可向员工分发
**后续顺序**：F2.6-F2.10 -> Phase 3 联网与集成 -> Phase 4 团队试点
**规划检查点**：Phase 0、Phase 1 和 F2.1-F2.5 已通过，当前完成 22/49

## 治理状态

**共享指令面**：`AGENTS.md`
**Claude 指令面**：`CLAUDE.md`
**其他平台规则**：无
**架构决策面**：`docs/adr/`；ADR-0004 固定唯一员工客户端，ADR-0005 固定服务器权威服务
**Memory 面**：不可用，不写入
**仓库 Memory 回退路径**：无，未获用户批准
**需求范围权威**：新需求源文件 + `task-breakdown.md` + `milestones.md` + ADR-0004/0005 + 本 MASTER
**本地原件权威**：获授权的鸿日原始文件、版本与 SHA-256，只读
**迁移前状态权威**：Phase 1 本地人工确认事件及可重建投影；模型输出、摘要、索引和聊天记忆不是事实
**团队状态权威**：Phase 3 切换后由 PostgreSQL 事件/审批和对象存储原件形成；本地库只读，无双主

## 一票否决状态

| 一票否决 | 当前发生数 | 门 |
|:---|---:|:---|
| 虚构产品事实 | 0 | 必须保持 0 |
| 把讨论升级成决定 | 0 | 必须保持 0 |
| 把暂定日期写成死线 | 0 | 必须保持 0 |
| 把过期方案当成当前方向 | 0 | 必须保持 0 |
| 重要结论不能回到证据 | 0 | 必须保持 0 |
| 未经人确认自动改变项目状态 | 0 | 必须保持 0 |
| 在探索模式下强行制造唯一答案 | 0 | 必须保持 0 |

任一发生即阻断当前阶段；先保存复现证据、增加 Fixture、修复并完成全量回归，不能用平均分抵消。

## 自适应控制状态

| 字段 | 当前值 |
|:---|:---|
| 活动阶段 | Phase 2 |
| drift_score | 3 |
| strategy | 保留本地领域验证、完成唯一客户端纵切、建设服务器权威、接入 Agent/工作流、团队试点过门 |
| threshold_annotate | 2 |
| threshold_replan | 4 |
| threshold_rescope | 6 |
| total_tasks | 10 |
| completed_tasks | 5 |
| last_updated | 2026-07-23 |

### 各阶段阈值

| 阶段 | 任务数 | 标注 | 重计划 | 重定范围 |
|:---|---:|---:|---:|---:|
| 0 | 7 | 2 | 3 | 5 |
| 1 | 10 | 2 | 4 | 6 |
| 2 | 10 | 2 | 4 | 6 |
| 3 | 13 | 3 | 6 | 8 |
| 4 | 9 | 2 | 4 | 6 |

阈值按阶段任务数的 20%/40%/60% 向上取整。切换阶段时将活动状态切换为对应行，`drift_score` 从该阶段 0 开始累计。

### 任务遥测日志

每项任务完成后，先写入本表，再更新阶段文件和阶段计数。

| 任务 | 估算 | 实际 | 工期差 | SUPER 分数 | SUPER 变化 | 未计划依赖 | 任务漂移 |
|:---|:---|:---|---:|---:|---:|---:|---:|
| F0.1 | S | S | 0 | 10/10 | +1 | 0 | 0 |
| F0.2 | M | M | 0 | 9/10 | 0 | 0 | 0 |
| F0.3 | M | M | 0 | 10/10 | +1 | 0 | 0 |
| F0.4 | M | M | 0 | 10/10 | +1 | 0 | 0 |
| F0.5 | L | L | 0 | 10/10 | +1 | 0 | 0 |
| F0.6 | M | M | 0 | 4/10 | -6 | 0 | 1 |
| F0.7 | M | M | 0 | 10/10 | +1 | 0 | 0 |
| F1.1 | M | M | 0 | 10/10 | +1 | 1 | 1 |
| F1.2 | L | L | 0 | 10/10 | +1 | 0 | 0 |
| F1.3 | M | M | 0 | 10/10 | +1 | 0 | 0 |
| F1.4 | L | L | 0 | 10/10 | +1 | 1 | 1 |
| F1.5 | L | L | 0 | 10/10 | 0 | 0 | 0 |
| F1.6 | L | L | 0 | 10/10 | +1 | 0 | 0 |
| F1.7 | M | M | 0 | 10/10 | +1 | 0 | 0 |
| F1.8 | L | L | 0 | 10/10 | +1 | 0 | 0 |
| F1.9 | L | L | 0 | 10/10 | +1 | 1 | 1 |
| F1.10 | XL | XL | 0 | 10/10 | 0 | 2 | 1 |
| F2.1 | M | M | 0 | 10/10 | +1 | 0 | 0 |
| F2.2 | XL | XL | 0 | 10/10 | 0 | 1 | 1 |
| F2.3 | L | L | 0 | 10/10 | +1 | 1 | 1 |
| F2.4 | L | XL | +1 | 10/10 | 0 | 1 | 1 |
| F2.5 | L | L | 0 | 10/10 | +1 | 0 | 0 |

## Rescope 追踪

| 版本 | 阶段/任务 | 状态 | 主线 | 处置 |
|:---|:---|:---|:---|:---|
| 初始本地草案 | 个人本地 29 项 | 未开始 | 本地知识与 AI 接入 | 被团队服务器方案替代 |
| 团队服务器方案 | 6 阶段 / 42 项 | 0/42，未开始 | OpenWork Electron + PostgreSQL/S3 团队服务器先行 | 被本次 rescope 替代；逐项映射保留在任务分解 |
| 本地价值验证方案 | 4 阶段 / 32 项 | 15/32 时 rescope | Fox/鸿日、本地单用户、黄金测试与真实工作先行 | Phase 0 和 F1.1-F1.8 保留；未开始的旧 Phase 2/3 被取代 |
| 单一客户端 + 服务器权威服务 | 5 阶段 / 49 项 | 22/49，执行中 | OpenWork 唯一客户端、公司服务器权威、MCP/Skills/工作流和团队试点 | 当前活动方案 |

服务器、身份、一致性、恢复、Dify 和四个开源组件已进入正式实施顺序。外部组件仍需逐项证明收益；“拒绝并使用 NoOp”是合法结果。

## 下一步

1. 用 F2.6 把现有命令幂等、乐观版本和并发错误统一为服务器应用契约，并返回可复核冲突差异。
2. 保持 F2.5 的应用层授权和事务级 RLS 上下文；冲突重试不得绕过项目、动作或保密级别校验。
3. 保留 F1.10 的 SQLite、原件哈希和验收副本，供 F3.1 对账；在 F4.8 前不向员工分发未签名包。

## 会话日志

| 日期 | 会话 | 摘要 |
|:---|:---|:---|
| 2026-07-13 | 初始规划 | 核验 Zvec、Nubase、Open Notebook、FlowLong，形成个人本地方案初稿。 |
| 2026-07-13 | 团队服务器范围变更 | 将目标改为 PostgreSQL/S3 团队服务器、远程 API/MCP、Web/PWA 和可靠性体系。 |
| 2026-07-13 | Dify 范围变更 | 将 Dify 纳入外部组件 POC，形成 6 阶段 42 项未开始方案。 |
| 2026-07-13 | OpenWork 客户端范围变更 | 核验 OpenWork，规划公司 Electron 主客户端、OpenCode Runtime 和轻量 Web 后备，42 项仍未开始。 |
| 2026-07-13 | 场景化需求 rescope | 读取品牌工作场景反馈，确认验证顺序错误；将活动 SPEC 重排为 4 阶段 32 项，BrandBench/黄金集提前到 Phase 0，服务器与所有深度团队能力后移到条件 Phase 3。 |
| 2026-07-16 | Phase 0 启动 | 用户确认继续执行，并指定 `Example` 为当前唯一可用材料；完成 F0.1，开始 F0.2/F0.3，完整鸿日/V5 数据保留为已知缺口。 |
| 2026-07-16 | Phase 0 样本与分类契约 | 完成当前授权 5 类样本的本地只读解析、脱敏登记和缺口清单；冻结会议解释 Schema 与人工确认不变量，11 项自动化测试通过。 |
| 2026-07-16 | Phase 0 运行协议与黄金集 | 冻结四种运行模式和人工切换 Schema；建立 16 个脱敏 Fixture、八旅程和七项一票否决静态发布门；BrandBench 保持待 Fox 人工评分。 |
| 2026-07-16 | Phase 0 技术就绪准备 | 完成本地核心端口、Task Packet、Proposal 与 OpenWork 可替换适配契约；18 项自动化测试和 21 项契约检查通过，F0.7 因人工基线未完成继续保持未通过。 |
| 2026-07-16 | BrandBench 待评包 | 基于当前唯一明确的鸿日规划材料生成本地匿名 A/B 评审包；候选、映射和评分表仅存 `.work/`，等待 Fox 完成六维人工评分。 |
| 2026-07-21 | 远程鸿喜达资料接入 | 获得远程完整资料根只读访问；按项目规则筛选并哈希核验 9 份当前 SSOT/工作源，纠正当前主线为 EVENT-0730，重建 BrandBench v2；全量物理对账和人工评分仍未完成。 |
| 2026-07-22 | BrandBench 首轮人工评分 | Fox 完成匿名评分：普通结构版 19/30，Task Packet 版 12/30；自然中文分别 2/5 和 1/5，反馈为两份都不像人话。记录失败基线并使用 humanizer-zh 重写，F0.7 继续阻断。 |
| 2026-07-22 | BrandBench 第二轮与 Phase 0 完成 | Fox 选择 B：23/30、中位数 4.0、自然中文 4/5，三个维度较首轮普通版提高，且无一票否决。F0.7 通过，开始 F1.1。 |
| 2026-07-22 | F1.1 本地工作空间 | 建立配置、`.fox` 分区、只读内容寻址证据和状态备份恢复；14 项新测试、34 项全量测试通过。统一测试环境补齐 Phase 0 解析依赖，任务漂移 1。 |
| 2026-07-22 | F1.2 SQLite 权威状态 | 完成 Schema v2、幂等与乐观版本、人工评审、事件/投影原子提交、重建和在线备份恢复；27 项相关新测试、61 项全量测试通过，任务漂移 0。 |
| 2026-07-22 | F1.3 来源导入与对账 | 完成 `source-import.v1`、SQLite v3、版本/旧 ID/缺口/去重和备份 v2；真实 9 条来源双跑新增 0 行，未把来源内容升级为正式状态；79 项全量测试通过，任务漂移 0。 |
| 2026-07-22 | F1.4 会议增量摄取 | 完成 `meeting-ingest.v1`、SQLite v4、来源/状态版本绑定、原话分段、模式约束、保守分类、去重和冲突快照；非鸿日 Fixture 双跑新增 0 行、正式状态 0；备份清单升级 v3，101 项全量测试和 2 组旧 Schema 子测试通过。任务漂移 1，Phase 1 累计漂移 2，已对 F1.5 添加复杂度提醒。 |
| 2026-07-22 | F1.5 Proposal 生命周期 | 完成 `proposal-lifecycle.v1`、SQLite v5、Fox 人工评审/驳回/重开、显式替代、历史查询及状态与生命周期事件重放；重开必须补新证据，同状态 ID 不得静默覆盖，替代失败整笔回滚。备份清单升级 v4，兼容 v1-v3；114 项测试和 3 组真实旧 Schema 恢复子测试通过。任务漂移 0。 |
| 2026-07-22 | F1.6 证据关系与稳定回源 | 完成 `evidence-query.v1`、SQLite v6、决定/开放问题当前过滤、11 种关系查询、有效期和会议原话全链回源；被替代、驳回、过期和待生效内容默认不进入当前任务，证据不全明确返回未确认。备份清单升级 v5，兼容 v1-v4；126 项测试和 4 组旧 Schema 子测试通过。真实 F1.5 数据库副本迁移通过，但正式决定、开放问题和关系仍为 0。任务漂移 0。 |
| 2026-07-22 | F1.7 Task Packet 与运行留痕 | 完成 `task-packet.v2`、`runtime-mode-switch.v2`、Fox 任务角色/模式登记、人工模式切换、L0-L4 分层、当前相关上下文裁剪、不可变 Packet 哈希和 `runtime-run.v1`；运行绑定状态、任务、协议、运行时与模型版本，AI 无模式切换权。SQLite 升级 v7，备份清单升级 v6 并兼容 v1-v5；133 项测试和 5 组旧 Schema 子测试通过。真实鸿日 v3 数据库副本迁移后项目版本与业务计数未变，运行表为空。任务漂移 0。 |
| 2026-07-22 | F1.8 本地 CLI/MCP 与模型切换 | 完成统一 `LocalAIService`、`brand-os` CLI、官方 stdio MCP、9 个封闭 Schema 白名单工具和 Codex/Claude 无密钥适配配置；模型切换复用既有 Task Packet，并受 Packet 模型允许列表约束。没有开放审批、模式切换、任意 SQL、硬删除、密钥或任意文件读取。148 项测试和 5 组旧备份 Schema 子测试通过；真实鸿日 v3 数据库副本迁移到 v7 后项目版本和业务计数未变。任务漂移 0。 |
| 2026-07-22 | OpenWork OW-L0 选型门 | 固定 `v0.17.36@ddf3e482` 并完成社区切片构建、许可证、`ee/**`、默认外联和 Electron 配置核验。桌面测试 79 通过、1 跳过；OW-L0 有条件通过。上游默认外联、品牌标识和宽松 ATS 必须先修复，F1.9 仍未完成，总进度保持 15/32。 |
| 2026-07-22 | OpenWork 单一客户端决策 | Fox 明确选择公司定制版 OpenWork 作为唯一员工客户端；Brand Project OS 不另做前端软件，OpenCode Runtime/Sidecar 随同一安装包分发。服务器 MCP/Skills 可供 OpenWork 与其他 Agent 共用，但服务器正式数据权威、身份和高可用仍按独立 SPEC 决策。 |
| 2026-07-22 | 单一客户端与服务器权威 rescope | Fox 说明命名可调整，核心是员工只使用公司定制 OpenWork，Brand Project OS 是同一产品的业务服务。正式批准公司服务器权威、MCP/Skills/Dify 与外部组件接入路线；保留已完成 Phase 0 和 F1.1-F1.8，重排为 5 阶段 49 项。 |
| 2026-07-22 | 命名边界澄清 | Fox 确认最终发行名可以调整；这不改变产品形态。SPEC 继续以 Brand Project OS 作为项目名，员工仍只安装一个基于 OpenWork 的公司客户端。 |
| 2026-07-22 | F1.9 单一客户端离线收口 | OpenWork fork 用 9 个独立提交完成默认关闭遥测、Cloud、模型目录和更新，统一公司工作名、Bundle ID、协议与数据目录，并收紧 Electron 导航、IPC、网络和打包边界。`Brand Project OS.app` 构建成功；8 帧 fraimz 全部通过，实际包无 PostHog Key、未含鸿日资料，未登记旧上游地址被默认配置和网络策略拒绝。App 370 项通过；Desktop 100 项通过、1 项平台条件跳过；App、Desktop、Server、Orchestrator 类型检查通过。最终提交 `7cf9b229` 已推送；包尚未签名和公证。任务漂移 1，Phase 1 累计漂移 3，F1.10 保持复杂度提醒。 |
| 2026-07-22 | F1.10 鸿日桌面纵切与 Phase 1 完成 | 公司定制 OpenWork 已接入当前状态、证据、Proposal、Task Packet 和 AI 任务入口。使用 Codex/ChatGPT 内置 macOS 控制检查真实 Electron 窗口，12 帧 fraimz、12 段旁白全部通过，0 失败、0 跳过；临时 Electron、Vite、Sidecar 和保活进程均已关闭。Fox 159 项及 5 组子测试、OpenWork App 370 项、共置契约 6 项、Desktop 105 项通过且 1 项平台条件跳过，两端类型检查通过。权威鸿日数据库 SHA-256 保持 `cd9ff29827e7bec2fb3db50c54958ada89c76705fc1df6ab7e549251ed7801e3`。实际工作量 XL，SUPER 10/10；初始草稿消费和加载态截图竞态记为 2 项未计划依赖，任务漂移 1，Phase 1 最终漂移 4。阶段已经通过，不返工已验收范围；F2.1 继续固化桥接契约和可观测加载状态。内部包尚未签名、公证。 |
| 2026-07-22 | F2.1 服务器边界与测试基线 | 新增独立服务器配置与健康模型，冻结 `server-boundary.v1`、`service-config.v1` 和 `service-health.v1`。配置采用显式参数、环境变量、非敏感文件、默认值的优先级，秘密不会进入配置文件、`repr` 或健康报告；OpenWork Runtime、MCP 和工作流没有人工批准权。179 项测试及 5 组子测试通过，未启动服务、数据库或迁移正式数据。实际工作量 M，SUPER 10/10，未计划依赖 0，任务漂移 0。当前进入 F2.2。 |
| 2026-07-22 | F2.2 PostgreSQL 权威事件、审批和投影 | 新增 PostgreSQL v1-v6 可校验迁移和 `PostgreSQLCanonicalStore`，复用 Phase 1 项目、来源、会议、候选、Proposal、关系、人工审批、事件、投影和证据查询语义。命令幂等锁、项目行锁、事件/审批/投影同事务、失败回滚及事件重建均通过真实 PostgreSQL 17 临时集群验证；10 项集成测试和 189 项完整回归通过，另有 5 组子测试。未迁移鸿日正式数据，未双写，临时数据库进程已退出。实际工作量 XL，SUPER 10/10；本机测试运行时补装记为 1 项未计划依赖，任务漂移 1，Phase 2 累计漂移 1。当前进入 F2.3。 |
| 2026-07-22 | BISHENG 后续候选规划 | 纳入固定 `v2.6.0@779d8fb8` 的接入评估与 BS0-BS3 条件 SPEC，统一使用 `AIWorkflowPort` 与 Dify/直接 Worker 对比。候选不计入当前 49 项，也不是 F2-F4 依赖；最早在 Phase 4 试点形成结论且 Fox 单独批准 rescope 后启动。BISHENG 不保存正式状态、不承担人工批准，也不形成第二客户端。 |
| 2026-07-23 | F2.3 S3 原件版本与准入 | 新增 `object-evidence.v1`、PostgreSQL v7 对象元数据、boto3 S3 兼容适配器和可恢复准入编排。分片中断、幂等、同名异内容、SHA-256/大小/MIME/安全拒绝、内容地址复用、非法状态、孤儿清理、对象对账、人工撤销、延迟墓碑和删除/激活竞争均通过临时 PostgreSQL 17 与 Moto S3 HTTP 验证；Phase 2 共 43 项、全量 202 项及 9 组子测试通过。未读取或上传鸿日/鸿喜达正式资料，临时服务已退出。实际工作量 L，SUPER 10/10；boto3/Moto 记 1 项未计划依赖，任务漂移 1，Phase 2 累计漂移 2，已对 F2.4 添加标注级复杂度提醒。当前进入 F2.4。 |
| 2026-07-23 | F2.4 OIDC 员工身份与会话 | 新增 `oidc-identity.v1`、PostgreSQL v8、Authorization Code + S256 PKCE、Discovery/JWKS/claims 校验、预登记 issuer/subject 绑定、加密令牌、刷新轮换、本地优先撤销和 `IDENTITY_ASSERTED` 审计。Phase 2 共 74 项、全量 233 项及 16 组子测试通过；未连接公司 OIDC，未读取或迁移正式资料。实际工作量 XL，SUPER 10/10；PyJWT/cryptography 与补充安全语义记 1 项未计划依赖，任务漂移 1，Phase 2 累计漂移 3，当前进入 F2.5。 |
| 2026-07-23 | F2.5 项目授权与 RLS | 新增 `project-authorization.v1`、五角色九动作矩阵、P0-P3 上限、独立服务身份、PostgreSQL v9 授权表和强制 RLS。应用服务先判权，非所有者 `NOBYPASSRLS` 角色再按事务上下文过滤；服务身份不能获授或伪造人工评审。Phase 2 共 83 项、全量 242 项及 16 组子测试通过；未迁移正式资料。实际工作量 L，SUPER 10/10，未计划依赖 0、任务漂移 0，Phase 2 累计漂移保持 3，当前进入 F2.6。 |
