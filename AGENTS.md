# Brand Project OS 项目协作规则

## 适用范围

本规则适用于整个开发仓库。开始任何实现前，先读取 `docs/progress/MASTER.md`、`docs/plan/task-breakdown.md` 和本文件。

本文件只规定软件开发、数据治理、测试和协作约束。运行中的品牌 AI 角色、会议解释、工作模式和品味规则由 `docs/plan/runtime-brand-agent-and-meeting-protocol.md` 定义，不得用开发仓库规则替代运行时品牌协议。

## 当前产品边界

- 当前第一用户是 Fox，第一验证项目是鸿日。
- 当前批准路线是“单一员工客户端 + 公司服务器权威服务”。本地纵向切片仍先完成，但服务器化不再是待决定候选。
- 员工唯一需要安装的软件是公司定制版 OpenWork。文档暂用 Brand Project OS 作为项目名，最终发行名可以另定；改名不得引出第二个 Web、PWA 或桌面客户端。
- OpenCode Runtime、Sidecar 和本机能力桥接随同一个安装包分发。它们是客户端后台运行时，不是第二个产品，也不是正式业务数据源。
- Brand Project OS Service 部署在公司服务器，承载正式业务 API、权威事件与审批、证据元数据、MCP Gateway、Skills 目录和工作流适配器。
- MCP 是 AI 访问协议，不是数据库或业务核心。定制 OpenWork 通过版本化业务 API完成员工交互；Codex、Claude、Dify 等通过受控 MCP/Skills 使用同一业务能力。
- FoxWork 团队文件协作仍是独立需求线。当前服务器只接入经批准的品牌项目资料，不自动接管团队全部文件同步、NAS 或个人工作区。

## 权威来源

- 原始项目文件及其 SHA-256：只证明原始内容和文件版本。
- 经人工确认的权威事件日志与审批记录：只证明当前正式状态如何形成。
- 当前状态投影：可从权威事件重建，不是独立真相源。
- OpenWork/OpenCode 会话、Zvec、FTS、Open Notebook、Nubase Memory、FlowLong、Dify、缓存和模型摘要：均为运行态、派生数据或流程协调数据，不得覆盖正式状态。
- Phase 1 本地 SQLite 是迁移前权威；团队阶段完成一次性校验切换后，PostgreSQL 是唯一可写正式状态源，S3 兼容对象存储保存原件版本。不得长期双写或形成双主。
- S3 原件准入必须使用开启版本控制的桶；临时对象不能进入证据链，只有 `ACTIVE` 版本可以作为正式证据。PostgreSQL 与对象存储不做分布式事务，上传失败、孤儿、撤销和删除必须通过状态机、延迟墓碑和可审计对账恢复。

## 核心规则

- AI 只能提出变更，不得批准事实、决定、约束、外部承诺、负责人、截止时间或提交版本。
- `VIEW`、`PREFERENCE`、`HYPOTHESIS`、`OPTION`、`TENDENCY`、`TARGET_DATE` 不得自动升级为 `DECISION`、`CONSTRAINT` 或 `DEADLINE`。
- 新资料和新会议只生成增量 Proposal；未经人工确认不得静默覆盖当前状态。
- 开始任务时按“当前状态 -> 当前阶段与任务 -> 已批准决定 -> 开放问题 -> 相关证据 -> 必要原文”装配上下文，历史废案默认不进入当前上下文。
- Task Packet 必须是不可变派生快照；任务角色和工作模式由 Fox 登记，AI 只能建议模式切换。每次 Agent 运行必须绑定 Packet 哈希、状态版本、任务版本、协议、运行时和模型版本。
- 本地 CLI、stdio MCP、远程 MCP 和桌面 API 必须调用同一版本化应用服务。非交互入口不得声明为人工身份；MCP 项目范围必须显式固定，输入 Schema 拒绝额外字段，只能开放白名单工具。
- Codex、Claude 等模型运行时自行管理提供商登录。Brand Project OS 不读取、转存或记录模型 API Key；模型切换必须复用已保存的 Task Packet，并受 Packet 模型允许列表约束。
- 探索协议与执行规格必须显式分离和切换；AI 不得在探索中强行收口，也不得在执行中重写已批准战略。
- 领域核心不得直接依赖 OpenWork、OpenCode、Zvec、Nubase、Open Notebook、FlowLong、Dify 或具体模型 SDK；必须通过版本化端口和 Schema 调用。
- 所有外部组件必须可禁用、可替换；检索索引必须可从权威数据完整重建。
- OpenWork/OpenCode 自有的会话、工具权限和本地 SQLite/JSON 运行态不得承担正式事实、审批、负责人、截止时间或外部承诺；这不包括 Phase 1 的 Brand Project OS 权威 SQLite。
- OpenCode Tool Permission 只允许本次 Agent 工具执行，不是 Brand Project OS 业务批准；两者必须使用不同 Schema、路由、权限和审计。
- Phase 1 默认在本机处理；团队切换后默认在公司服务器控制边界内处理。数据发送到公司控制边界之外前，必须检查保密级别、提供商、任务范围和人工授权；自托管 Dify/FlowLong 中的外部模型、插件和 HTTP 节点仍视为外发。
- 所有正式写请求必须有幂等键和预期版本。冲突返回差异并要求刷新确认，不得以最后写入覆盖。
- 原件上传必须先进入隔离状态并流式校验 SHA-256、大小、MIME 和安全检查；同名异内容按内容地址和版本号保存，不得静默覆盖。对象删除前必须有获授权员工撤销和延迟删除墓碑。
- 服务器审批必须绑定交互式员工身份、项目权限和审计事件。服务账号、MCP、Skill、工作流和 Agent 令牌没有批准正式状态的权限。
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
- 2026-07-22 完成第二次正式 `rescope`：保留已完成的本地领域验证，后续改为单一 OpenWork 客户端、服务器权威服务、Agent/工作流接入和团队试点四段实施。旧方案只作追溯。
- 每完成一个任务，先记录执行遥测，再更新对应阶段文件和 `docs/progress/MASTER.md`。
- 若任务产生稳定规则或架构不变量，更新本文件和相应 ADR；未经用户明确批准，不创建仓库内 Memory 文件。

## 验证入口

- `uv run pytest`
- `uv run brand-os doctor`
- `uv run brand-os verify hongri`
- OpenWork fork 的 App、Desktop、Server、Orchestrator 类型检查与测试
- OpenWork fork 的 Electron 打包扫描、桌面流程验收和安装烟测

本地核心、CLI 和 stdio MCP 已有可运行入口；员工界面只在公司定制版 OpenWork 中实现。不得用单独 Web 或另一个桌面壳替代该交付边界。
