# Brand Project OS 项目协作规则

## 适用范围

本规则适用于整个开发仓库。开始任何实现前，先读取 `docs/progress/MASTER.md`、`docs/plan/task-breakdown.md` 和本文件。

本文件只规定软件开发、数据治理、测试和协作约束。运行中的品牌 AI 角色、会议解释、工作模式和品味规则由 `docs/plan/runtime-brand-agent-and-meeting-protocol.md` 定义，不得用开发仓库规则替代运行时品牌协议。

## 当前产品边界

- 当前第一用户是 Fox，第一验证项目是鸿日。
- 当前批准路线是本地优先、单用户、先证明真实提效。
- FoxWork 团队文件协作是独立需求线，不得提前与品牌 AI 长期项目协作合并。
- 团队服务器、PostgreSQL、S3、OIDC、并发、高可用和灾备均为 `future-candidate`、`not-approved-for-current-mvp`、`review-after-hongri-pilot`。
- OpenWork 可作为本地客户端候选基础；OpenWork 服务端化不得成为 MVP 前置条件。

## 权威来源

- 原始项目文件及其 SHA-256：只证明原始内容和文件版本。
- 经人工确认的权威事件日志与审批记录：只证明当前正式状态如何形成。
- 当前状态投影：可从权威事件重建，不是独立真相源。
- OpenWork/OpenCode 会话、Zvec、FTS、Open Notebook、Nubase Memory、FlowLong、Dify、缓存和模型摘要：均为运行态、派生数据或流程协调数据，不得覆盖正式状态。
- 当前 MVP 可使用本地轻量结构化存储承载事件和投影；具体实现必须通过版本化端口隔离，未来服务器化不得要求重写领域语义。

## 核心规则

- AI 只能提出变更，不得批准事实、决定、约束、外部承诺、负责人、截止时间或提交版本。
- `VIEW`、`PREFERENCE`、`HYPOTHESIS`、`OPTION`、`TENDENCY`、`TARGET_DATE` 不得自动升级为 `DECISION`、`CONSTRAINT` 或 `DEADLINE`。
- 新资料和新会议只生成增量 Proposal；未经人工确认不得静默覆盖当前状态。
- 开始任务时按“当前状态 -> 当前阶段与任务 -> 已批准决定 -> 开放问题 -> 相关证据 -> 必要原文”装配上下文，历史废案默认不进入当前上下文。
- Task Packet 必须是不可变派生快照；任务角色和工作模式由 Fox 登记，AI 只能建议模式切换。每次 Agent 运行必须绑定 Packet 哈希、状态版本、任务版本、协议、运行时和模型版本。
- 探索协议与执行规格必须显式分离和切换；AI 不得在探索中强行收口，也不得在执行中重写已批准战略。
- 领域核心不得直接依赖 OpenWork、OpenCode、Zvec、Nubase、Open Notebook、FlowLong、Dify 或具体模型 SDK；必须通过版本化端口和 Schema 调用。
- 所有外部组件必须可禁用、可替换；检索索引必须可从权威数据完整重建。
- OpenWork/OpenCode 会话、工具权限、本地 SQLite/JSON 运行态不得承担正式事实、审批、负责人、截止时间或外部承诺。
- OpenCode Tool Permission 只允许本次 Agent 工具执行，不是 Brand Project OS 业务批准；两者必须使用不同 Schema、路由、权限和审计。
- 当前默认本地优先。数据外发前必须检查保密级别、提供商、任务范围和人工授权；自托管工作流中的外部模型、插件和 HTTP 节点仍视为外发。
- 本地正式写请求仍必须有幂等键和预期版本；冲突不得静默覆盖。未来适配服务器时沿用同一领域契约。
- 所有注释、文档和函数级说明使用简体中文；对外界面不得出现内部开发占位文案。
- 未经用户明确要求，不启动 Web、数据库、Docker、桌面应用或其他常驻服务。
- 新增行为、Schema、迁移、解析、权限、检索、缓存或持久化逻辑必须增加自动化测试。

## 一票否决

- 虚构产品事实。
- 把讨论升级为决定。
- 把暂定日期升级为死线。
- 把过期方案作为当前方向。
- 重要结论无法回到原始证据。
- 未经人工确认改变正式状态。
- 在探索模式下强行制造唯一答案。

## 规划与进度

- 当前采用 `LOCAL_ONLY` SPEC 追踪模式；它只描述任务追踪方式。
- 本轮属于正式 `rescope`：原团队服务器优先的 42 项任务均未开始，以新的四阶段鸿日本地验证计划取代，旧任务仅保留追溯记录。
- 每完成一个任务，先记录执行遥测，再更新对应阶段文件和 `docs/progress/MASTER.md`。
- 若任务产生稳定规则或架构不变量，更新本文件和相应 ADR；未经用户明确批准，不创建仓库内 Memory 文件。

## 规划中的验证入口

- `uv run pytest`
- `pnpm --dir apps/web test`
- `pnpm --dir apps/web exec playwright test`
- `uv run brand-os doctor`
- `uv run brand-os verify hongri`

这些是计划中的实现后入口，不表示当前规格仓库已有可运行应用。OpenWork 独立客户端 fork 的类型检查、桌面测试和发布校验只在 Phase 1 选型门通过后确定。
