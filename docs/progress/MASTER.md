# FoxWork、Den 与 Brand Project OS - 总进度

> 目标：交付 FoxWork，公司定制版 OpenWork 是员工唯一客户端；Den 提供统一账号和公司 AI 控制面；Brand Project OS Service 提供项目资料、正式状态、证据、Proposal、多媒体分析和流程能力。
> 开始日期：2026-07-13
> 最后更新：2026-07-24
> 模式：`LOCAL_ONLY`（只表示 SPEC 在本地 Markdown 追踪，不表示产品只能本地运行）
> 第一用户：Fox
> 第一验证项目：鸿日
> 需求基线：`20260713_品牌AI长期项目协作系统_场景化需求澄清与产品反馈_v0.1(1).md`

## 当前结论

- 当前活动方案为 5 阶段 56 项：Phase 0、Phase 1 和 Phase 2 已完成，F3.1/F3.2 已完成，当前进入 F3.3 Den 与远程 Worker 生产部署基线。Fox 已批准小团队托管部署档位，以及 99.5% 月可用性、PostgreSQL RPO 不高于 5 分钟、核心服务 RTO 不高于 60 分钟的内部目标；上述目标仍需 Phase 4 用真实部署验证，不代表已经达到生产 SLO。Den MySQL 与远程 Worker 的 SLO 尚未获 Fox 批准。
- 旧 42 项服务器草案和 32 项本地价值验证方案均保留在 Git 历史中，不再作为当前依赖或完成度来源。
- 当前主线不是通用项目管理、企业知识库或完整 RAG，而是长期品牌项目的状态与品牌认知协作层。
- FoxWork 是公司定制版 OpenWork 的固定发行名，也是唯一员工客户端；FoxWork、Den 员工页面和 Den 后台管理员网页只使用简体中文，不提供语言切换或英文回退。OpenCode Runtime、Sidecar 和本机桥接仍随同一安装包分发。
- Den Web/API/MySQL 采用 `FSL-1.1-MIT` 内部自托管，使用 `single_org` 并允许普通员工从公司入口自助注册，但拒绝第二组织；Den 统一账号、成员/团队、远程工作区/Worker、FoxWork 桌面交接、MCP、Skills、共享模型和桌面策略。它不是 OSI 开源组件，也不得作为对外竞争服务。
- Brand Project OS Service 部署在公司服务器。PostgreSQL 保存团队正式事件、审批和投影，对象存储保存原件版本；客户端不得直连存储。
- 员工只维护一套 Den 账号。普通员工自助注册后，Brand OS 使用 Den OAuth/OIDC 第一方短期令牌按 `(issuer, subject)` 首次建立内部身份映射，不复用原始 Den Session Token，也不保留第二套密码入口；首次建档不自动授予项目权限或业务审批权。
- Brand OS MCP 登记到 Den，由 Den 按成员或团队下发；Skills 和共享模型由 Den 管理。MCP 是 AI 接口，不是数据库；AI 和服务账号无人工批准权。
- Den 远程工作区/Worker 是 Phase 3 必验运行面，但其文件系统、Session 和缓存不是业务真相源；Brand OS Outbox/Worker 继续单独负责资料处理。
- 图片、视频、录音、PPT、Office 和 PDF 由 Brand OS 上传、准入和异步分析；每个结果必须回到原件版本和页码、幻灯片或时间码，只能形成 Artifact/Proposal。
- Phase 1 SQLite 在迁移前继续承载本地验证；Phase 3 一次性切换后退出正式写入，不形成双主。
- 七项一票否决适用于所有模型/协议/版本；任何一项出现即阻断阶段门。
- BISHENG 评估与条件接入 SPEC 位于当前 56 项之后的候选池；它不影响 F3.19/F4.10 完成度，只有 Phase 4 试点证明直接基线不足并由 Fox 单独批准后才能正式 rescope。

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
- [Den 内部自托管技术门](../phase3/openwork-den-self-host-gate.md)
- [F2.2 PostgreSQL 权威事件、审批和投影](../phase2/postgresql-authority-store.md)
- [F2.3 S3 兼容原件版本、哈希和准入状态机](../phase2/object-evidence-store.md)
- [F2.4 OIDC 员工身份与服务器会话](../phase2/oidc-identity-and-sessions.md)
- [F2.5 项目 RBAC、保密级别与 RLS](../phase2/project-authorization-and-rls.md)
- [F2.6 幂等、乐观锁和冲突差异](../phase2/write-consistency-and-conflicts.md)
- [F2.7 审计、Outbox/Inbox 和后台任务](../phase2/audit-outbox-inbox.md)
- [F2.8 HTTP API 与 OpenAPI](../phase2/http-api-and-openapi.md)
- [F2.9 可观测性、健康和告警](../phase2/observability-and-alerting.md)
- [F2.10 服务器恢复演练与阶段门](../phase2/server-recovery-and-gate.md)
- [BISHENG 接入评估](../analysis/bisheng-integration-evaluation.md)
- [BISHENG 条件接入 SPEC](../plan/bisheng-integration-spec.md)
- [ADR-0005：单一客户端与服务器权威服务](../adr/0005-single-client-server-authority.md)
- [ADR-0007：采用 OpenWork Den 作为公司 AI 控制面](../adr/0007-adopt-openwork-den-control-plane.md)
- [ADR-0008：单组织自助注册与 Den 远程工作区](../adr/0008-den-self-registration-and-remote-workspaces.md)

服务器架构、数据一致性、安全和 OpenWork/Den 集成文档均按 ADR-0005、ADR-0007 和 ADR-0008 执行。历史文档中的“不部署 Den”“只使用 `ee/**` 外 MIT 代码”和“双登录/自建团队连接”只描述旧方案，不再决定当前实施顺序。

## 阶段汇总

| 阶段 | 名称 | 任务 | 完成 | 进度 | 交付边界 |
|:---|:---|---:|---:|:---|:---|
| 0 | 边界、协议与黄金测试先行 | 7 | 7 | 100% | 一页边界、鸿日样本、分类标准、品牌 Agent 协议、10-20 黄金用例与 BrandBench |
| 1 | 单一客户端本地纵向切片 | 10 | 10 | 100% | 本地领域核心、CLI/MCP、OpenWork 单安装包和鸿日桌面闭环 |
| 2 | 服务器权威基础 | 10 | 10 | 100% | PostgreSQL、对象存储、OIDC/RBAC、一致性、API、审计、观测和恢复 |
| 3 | Den 统一控制面与联网业务闭环 | 19 | 2 | 11% | Den、自助注册、全中文管理面、远程工作区、项目、多媒体和公司 AI 能力 |
| 4 | 团队试点与生产准入 | 10 | 0 | 0% | 真实账号与资料、并发、全栈恢复、安全、SLO、签名分发和 Go/No-Go |
| **合计** |  | **56** | **29** | **52%** | Den 技术门已通过，当前执行 F3.3 |

## 阶段清单

- [x] Phase 0：边界、协议与黄金测试先行（7/7）- [详情](phase-0-boundary-and-bench.md)
- [x] Phase 1：单一客户端本地纵向切片（10/10）- [详情](phase-1-hongri-local-prototype.md)
- [x] Phase 2：服务器权威基础（10/10）- [详情](phase-2-server-authority-foundation.md)
- [ ] Phase 3：Den 统一控制面与联网业务闭环（2/19）- [详情](phase-3-connected-client-and-integrations.md)
- [ ] Phase 4：团队试点与生产准入（0/10）- [详情](phase-4-team-pilot-and-production-gate.md)

## 当前状态

**活动阶段**：Phase 3：Den 统一控制面与联网业务闭环
**活动任务**：F3.3：建立 Den 与远程 Worker 生产部署基线
**阻塞项**：无；远程 Worker 公司部署路径尚未验证，真实鸿日资料尚未迁移，Den MySQL/Worker 生产 SLO 尚待 Fox 在 Phase 4 前批准
**后续顺序**：F3.3 Den/Worker 部署 -> F3.4/F3.5 自助注册、全中文与身份 -> F3.6-F3.13 远程工作区、项目、资料、业务和能力目录 -> F3.14-F3.18 工作流/可选适配器 -> F3.19
**规划检查点**：Phase 0-2 和 F3.1/F3.2 已通过，当前完成 29/56；Den 员工页面和管理员后台尚未全量中文化，远程 Worker 尚未真实运行，内部 macOS 包尚未签名、公证，不能向员工分发或接入真实资料

## 治理状态

**共享指令面**：`AGENTS.md`
**Claude 指令面**：`CLAUDE.md`
**其他平台规则**：无
**架构决策面**：`docs/adr/`；ADR-0004 固定唯一员工客户端，ADR-0005 固定服务器权威服务，ADR-0007 固定 Den 公司控制面，ADR-0008 固定自助注册、远程工作区、中文管理面和 Wiki 阶段门
**Memory 面**：不可用，不写入
**仓库 Memory 回退路径**：无，未获用户批准
**需求范围权威**：新需求源文件 + `task-breakdown.md` + `milestones.md` + ADR-0004/0005/0006/0007/0008 + 本 MASTER
**本地原件权威**：获授权的鸿日原始文件、版本与 SHA-256，只读
**迁移前状态权威**：Phase 1 本地人工确认事件及可重建投影；模型输出、摘要、索引和聊天记忆不是事实
**账号与控制权威**：Den MySQL 保存账号、组织、团队、远程工作区引用、MCP、Skills、共享模型和策略；不是业务正式状态源
**Wiki 发布规则**：只在大阶段目标完成并通过对应阶段门后统一同步；Phase 3 在 F3.19 通过前不更新 Wiki
**团队状态权威**：PostgreSQL 事件/审批和对象存储原件形成业务权威；本地库只读，无双主

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
| 活动阶段 | Phase 3 |
| drift_score | 0 |
| strategy | Den 生产基线与身份先行；再做项目、多媒体、业务界面和公司能力目录；可选适配器后置 |
| threshold_annotate | 4 |
| threshold_replan | 8 |
| threshold_rescope | 12 |
| total_tasks | 19 |
| completed_tasks | 2 |
| last_updated | 2026-07-24 |

### 各阶段阈值

| 阶段 | 任务数 | 标注 | 重计划 | 重定范围 |
|:---|---:|---:|---:|---:|
| 0 | 7 | 2 | 3 | 5 |
| 1 | 10 | 2 | 4 | 6 |
| 2 | 10 | 2 | 4 | 6 |
| 3 | 19 | 4 | 8 | 12 |
| 4 | 10 | 2 | 4 | 6 |

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
| F2.6 | L | L | 0 | 10/10 | +1 | 0 | 0 |
| F2.7 | L | L | 0 | 10/10 | +1 | 0 | 0 |
| F2.8 | L | L | 0 | 10/10 | +1 | 1 | 1 |
| F2.9 | L（重计划后） | L | 0 | 10/10 | +1 | 1 | 1 |
| F2.10 | L | L | 0 | 10/10 | +1 | 0 | 0 |
| F3.1 | XL | XL | 0 | 10/10 | +1 | 0 | 0 |
| F3.2 | L | L | 0 | 10/10 | +1 | 0 | 0 |

## Rescope 追踪

| 版本 | 阶段/任务 | 状态 | 主线 | 处置 |
|:---|:---|:---|:---|:---|
| 初始本地草案 | 个人本地 29 项 | 未开始 | 本地知识与 AI 接入 | 被团队服务器方案替代 |
| 团队服务器方案 | 6 阶段 / 42 项 | 0/42，未开始 | OpenWork Electron + PostgreSQL/S3 团队服务器先行 | 被后续 rescope 替代；逐项映射保留在任务分解 |
| 本地价值验证方案 | 4 阶段 / 32 项 | 15/32 时 rescope | Fox/鸿日、本地单用户、黄金测试与真实工作先行 | Phase 0 和 F1.1-F1.8 保留；未开始的旧 Phase 2/3 被取代 |
| 单一客户端 + 服务器权威服务 | 5 阶段 / 49 项 | 28/49 时 rescope | OpenWork 唯一客户端、自建团队连接、公司服务器权威 | Phase 0-2 和 F3.1 保留；不部署 Den/双登录路线退出 |
| Den 统一控制面 + Brand OS 权威服务 | 5 阶段 / 56 项 | 29/56，执行中 | 单一 Den 账号、公司 AI 控制面、项目资料与多媒体、业务权威和团队试点 | 当前活动方案 |

Den 生产部署、身份联邦、项目映射、多媒体处理、业务视图、Dify 和四个开源组件已进入正式实施顺序。外部组件仍需逐项证明收益；“拒绝并使用 NoOp”是合法结果。

## 下一步

1. 执行 F3.3：把已通过的 Den API/Web/MySQL 隔离栈扩展为可重复的公司部署基线，补远程工作区/Worker、公司内网 HTTP/HTTPS 入口、密钥、备份恢复、监控和升级回滚。
2. F3.4/F3.5 删除第二套登录，把 FoxWork、Den 和 Brand OS 接成自助注册、首次身份建档、一次登录、独立短期令牌和联动撤权；FoxWork、Den 员工页面和管理员后台只显示自然简体中文。
3. F3.7/F3.8 完成图片、视频、录音、PPT、Office 和 PDF 的上传、准入、处理状态与来源定位；安全门通过前不写真实鸿日资料。
4. Phase 4 实测已批准的 99.5%、PostgreSQL RPO<=5 分钟和核心 RTO<=60 分钟；Den MySQL 指标需另获 Fox 批准，F4.9 前不分发未签名包。

## 会话日志

| 日期 | 会话 | 摘要 |
|:---|:---|:---|
| 2026-07-24 | 自助注册、中文管理面与远程工作区范围校准 | Fox 更正普通员工可以自助注册，并明确 Den 后台管理员网页也必须全中文；Den 远程工作区/Worker 由“可选控制面”提升为 Phase 3 必验运行面。新增 ADR-0008，调整 F3.3-F3.6、F3.11、F3.19 的部署、注册、首次身份建档、映射、权限和验收口径；注册无需管理员预建第二套账号，但不自动授予项目权限。任务总数和完成度保持 56 项、29/56，当前仍为 F3.3。Wiki 只在大阶段通过阶段门后同步，Phase 3 在 F3.19 前不更新。 |
| 2026-07-24 | 第三次 rescope 后的全量 SPEC 收口 | 按 ADR-0007 和 56 项任务重写文档入口、OpenWork/Den 深度集成、架构契约、客户端、服务器、数据一致性、安全、项目/模块/风险/部署/外部组件分析、OIDC 与治理面；明确 FoxWork 唯一客户端、Den 单账号与 AI 控制面、Brand OS 项目业务权威、多媒体资料链、内网 HTTP 边界和 F3.3-F4.10 阶段门。历史 49 项与“不部署 Den”只保留在明确 rescope/旧 ADR 追溯。G-01-G-16 保留并新增 G-17-G-24 联网回归。最终治理审计补齐 ADR-0004/0005 的 Den 拓扑、当前任务和 `CLAUDE.md` 单账号规则；测试 Mac 上的临时 Den Web/API/隔离 MySQL 已停止，专用验收数据库、合成凭据、临时脚本和日志已删除，源码与打包产物保留。64 份 Markdown 相对链接、SPEC 计数和 `git diff --check` 通过；全量 `pytest` 为 308 项、22 组子测试通过，2 条第三方弃用警告。`doctor` 和正确语法的 `--project hongri verify` 因仓库根目录未初始化本地数据库而按设计拒绝；未执行初始化。此项不增加任务完成数，仍为 29/56、当前 F3.3。 |
| 2026-07-24 | F3.2 Den 内部自托管门与第三次 rescope | 固定 `v0.17.36@ddf3e482`，确认 `ee/**` 为 `FSL-1.1-MIT` 源码可见企业版，允许公司内部使用和修改但不得做对外竞争服务。测试 Mac 上 Den API/Web/MySQL、单组织自助注册、第二组织拒绝、桌面交接和登出撤销通过；普通员工通过 Den Agent MCP 使用共享 Skill 和公司 MCP，获得授权共享模型，模型/MCP 撤权立即生效。采用 ADR-0007，将活动方案改为 56 项：Den 统一账号、组织、MCP、Skills、模型；Brand OS 保留 PostgreSQL/S3 业务权威并新增多媒体资料流水线。F3.2 遥测 L/L、SUPER 10/10、漂移 0；当前 29/56，进入 F3.3。 |
| 2026-07-23 | FoxWork 发行名与中文界面约束 | Fox 将公司定制 OpenWork 的发行名固定为 FoxWork，并要求整个员工界面只使用简体中文，不需要语言切换或英文回退。当时 49 项方案把全量中文放在旧 F3.2/F3.13；2026-07-24 rescope 后，当前由 F3.4 完成全量中文和旧团队连接移除，F4.9 完成签名分发。 |
| 2026-07-23 | F2.7 审计、Outbox/Inbox 和后台任务 | 新增 `audit-outbox.v1`、PostgreSQL v10、追加式事件审计、按消费者 Outbox、租约领取、聚合顺序、Inbox 去重、重试、死信、重放和 Worker 最小权限。消费者不写正式表，正式状态在消费者停机或失败时仍可查询。专项 7 项、Phase 2 全量 101 项及 14 组子测试、完整回归 260 项及 19 组子测试通过；未迁移正式资料，临时 PostgreSQL 已退出。实际工作量 L，SUPER 10/10，未计划依赖 0，任务漂移 0，Phase 2 累计漂移保持 3，当前进入 F2.8。 |
| 2026-07-23 | F2.8 版本化 HTTP API 与 OpenAPI | 新增 `http-api.v1`、`http-error.v1`、OpenAPI 3.1 和可嵌入 ASGI 应用。Employee/Agent 路由分离，员工会话、项目授权、`Idempotency-Key`、`If-Match`、HMAC 游标、错误码、兼容窗口、限流和证据应用层回源均通过进程内契约测试；Agent 没有人工评审路由，客户端不直连 PostgreSQL/S3。专项 11 项、完整回归 271 项及 19 组子测试通过；未启动常驻 Web，未连接公司 OIDC 或正式资料。实际工作量 L，SUPER 10/10，未计划依赖 1，任务漂移 1，Phase 2 累计漂移达到 4，触发重计划提醒；F2.9 先补多副本可观测与共享限流边界。当前进入 F2.9。 |
| 2026-07-23 | F2.9 自适应重计划 | F2.8 后 `drift_score=4` 达到 Phase 2 重计划阈值。保持 49 项总范围不变，将 F2.9 拆为观测契约、HTTP/健康接入、PostgreSQL 共享限流、故障/告警验证四个波次；重计划段的漂移计数归零，F2.10 继续等待 F2.9-D 证据。 |
| 2026-07-23 | F2.9 可观测性、健康和告警 | 冻结 `observability.v1`、`trace-context.v1`、`metrics.v1`、`alert.v1`；HTTP 接入请求/关联/追踪 ID、业务定位字段、健康依赖和 Outbox 水位。PostgreSQL v11 共享限流只存 key 摘要；故障返回 503 `RATE_LIMIT_STORE_UNAVAILABLE`，不回退本地计数。专项观测 11 项、HTTP 15 项通过，编译检查通过；没有启动常驻服务或接入正式资料。实际工作量 L，SUPER 10/10，未计划依赖 1，重计划段 `drift_score` 由 0 变为 1，随后进入 F2.10。 |
| 2026-07-23 | F2.10 恢复技术演练 | 新增 `postgresql-backup.v1`、`server-recovery.v1` 和安全报告；一致快照逻辑备份可恢复到空库，逐表摘要、事件序列、Proposal 生命周期、当前投影和明确 S3 VersionId 对账一致。归档篡改、非空目标、不可重放批准事件和缺失 ACTIVE 对象均阻断。专项 6 项、Phase 2 的 133 项测试和 292 项完整回归通过；逻辑备份不是 PITR，本机夹具不是生产 SLO 证据。当次技术提交结束时人工阶段门尚未确认，阶段完成度暂为 26/49。 |
| 2026-07-23 | F2.10 人工阶段门与 Phase 2 关闭 | Fox 明确批准小团队托管部署档位：单应用节点、托管 PostgreSQL、版本化对象存储和独立备份域；批准 99.5% 月可用性、PostgreSQL RPO 不高于 5 分钟、核心服务 RTO 不高于 60 分钟作为内部目标。F2.10 遥测为 L/L、SUPER 10/10、变化 +1、未计划依赖 0、任务漂移 0；Phase 2 重计划段最终 `drift_score=1`，10/10 关闭，当前进入 F3.1。批准目标尚未经过生产验证，Phase 4 仍须实测。 |
| 2026-07-23 | F3.1 SQLite 到 PostgreSQL/S3 一次性迁移和权威切换 | 使用临时 SQLite、PostgreSQL 和 Moto S3 完成导出 Manifest、正式表导入、S3 明确 VersionId、全链路哈希/ID/事件/审批/投影/来源对账、写入冻结、对象中断回滚和成功重跑幂等。成功后 SQLite 文件和应用层只读，PostgreSQL 成为唯一可写正式状态源；真实鸿日资料未迁移。专项 7 项与完整回归通过。F3.1 遥测为 XL/XL、工期差 0、SUPER 10/10、变化 +1、未计划依赖 0、任务漂移 0；Phase 3 当前 1/13，下一项 F3.2。小团队托管部署及 99.5% 月可用性、RPO 不高于 5 分钟、RTO 不高于 60 分钟仍须 Phase 4 实测。 |
| 2026-07-23 | F3.2 FoxWork 与 Fox ASGI 临时契约验收 | 新增跨仓库回环测试：FoxWork 真实连接模块调用 Fox ASGI 的 OIDC 回调 303、一次性交接、员工摘要、项目列表、项目撤权和退出登录；临时监听自动退出，未读取鸿喜达资料。该测试证明 HTTP 字段和状态契约一致，但尚未证明测试 Mac 上的临时 OIDC/PostgreSQL/S3、HTTPS 证书和真实桌面窗口，因此 F3.2 仍保持未完成。 |
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
| 2026-07-22 | BISHENG 后续候选规划 | 纳入固定 `v2.6.0@779d8fb8` 的接入评估与 BS0-BS3 条件 SPEC，统一使用 `AIWorkflowPort` 与 Dify/直接 Worker 对比。当时不计入 49 项；当前也不计入 56 项或 F3-F4 依赖。最早在 Phase 4 试点形成结论且 Fox 单独批准 rescope 后启动。BISHENG 不保存正式状态、不承担人工批准，也不形成第二客户端。 |
| 2026-07-23 | F2.3 S3 原件版本与准入 | 新增 `object-evidence.v1`、PostgreSQL v7 对象元数据、boto3 S3 兼容适配器和可恢复准入编排。分片中断、幂等、同名异内容、SHA-256/大小/MIME/安全拒绝、内容地址复用、非法状态、孤儿清理、对象对账、人工撤销、延迟墓碑和删除/激活竞争均通过临时 PostgreSQL 17 与 Moto S3 HTTP 验证；Phase 2 共 43 项、全量 202 项及 9 组子测试通过。未读取或上传鸿日/鸿喜达正式资料，临时服务已退出。实际工作量 L，SUPER 10/10；boto3/Moto 记 1 项未计划依赖，任务漂移 1，Phase 2 累计漂移 2，已对 F2.4 添加标注级复杂度提醒。当前进入 F2.4。 |
| 2026-07-23 | F2.4 OIDC 员工身份与会话 | 新增 `oidc-identity.v1`、PostgreSQL v8、Authorization Code + S256 PKCE、Discovery/JWKS/claims 校验、预登记 issuer/subject 绑定、加密令牌、刷新轮换、本地优先撤销和 `IDENTITY_ASSERTED` 审计。Phase 2 共 74 项、全量 233 项及 16 组子测试通过；未连接公司 OIDC，未读取或迁移正式资料。实际工作量 XL，SUPER 10/10；PyJWT/cryptography 与补充安全语义记 1 项未计划依赖，任务漂移 1，Phase 2 累计漂移 3，当前进入 F2.5。 |
| 2026-07-23 | F2.5 项目授权与 RLS | 新增 `project-authorization.v1`、五角色九动作矩阵、P0-P3 上限、独立服务身份、PostgreSQL v9 授权表和强制 RLS。应用服务先判权，非所有者 `NOBYPASSRLS` 角色再按事务上下文过滤；服务身份不能获授或伪造人工评审。Phase 2 共 83 项、全量 242 项及 16 组子测试通过；未迁移正式资料。实际工作量 L，SUPER 10/10，未计划依赖 0、任务漂移 0，Phase 2 累计漂移保持 3，当前进入 F2.6。 |
| 2026-07-23 | BISHENG 未来规划确认 | Fox 确认 BISHENG 文档可以保留并作为后续规划参考。本次确认不启动部署或 BS0；当前 56 项仍不包含 BISHENG，需 Phase 4 试点结论和单独 rescope 才能进入实施。 |
| 2026-07-23 | F2.6 写一致性与冲突差异 | 新增 `write-consistency.v1`、`write-conflict.v1`、专用幂等键冲突、一致性应用服务和 PostgreSQL `REPEATABLE READ` 差异快照。100 次同请求并发重试只提交一次；双请求同版本竞争只有一个成功，另一端得到 409、当前版本、事件元数据和可复核正式状态差异。事件重建与当前投影不一致时阻断报告，未知事务异常保持回滚。Phase 2 共 93 项、全量 252 项及 19 组子测试通过；未迁移正式资料，临时 PostgreSQL 已退出。实际工作量 L，SUPER 10/10，未计划依赖 0、任务漂移 0，Phase 2 累计漂移保持 3，随后进入 F2.7。 |
