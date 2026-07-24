# Brand Project OS 项目协作规则

## 适用范围

本规则适用于整个开发仓库。开始任何实现前，先读取 `docs/progress/MASTER.md`、`docs/plan/task-breakdown.md` 和本文件。

本文件只规定软件开发、数据治理、测试和协作约束。运行中的品牌 AI 角色、会议解释、工作模式和品味规则由 `docs/plan/runtime-brand-agent-and-meeting-protocol.md` 定义，不得用开发仓库规则替代运行时品牌协议。

## 当前产品边界

- 当前第一用户是 Fox，第一验证项目是鸿日。
- 当前批准路线是“单一员工客户端 + 公司服务器权威服务”。本地纵向切片仍先完成，但服务器化不再是待决定候选。
- 员工唯一需要安装的软件是公司定制版 OpenWork，发行名固定为 FoxWork。Brand Project OS Service 是其背后的业务服务，不是第二个 Web、PWA 或桌面客户端。
- FoxWork、Den 员工页面和 Den 后台管理员网页的界面、提示、安装说明和错误信息只使用简体中文，不提供语言切换，也不得因翻译缺失回退显示英文或内部键名。
- OpenCode Runtime、Sidecar 和本机能力桥接随同一个安装包分发。它们是客户端后台运行时，不是第二个产品，也不是正式业务数据源。
- 公司内部部署 OpenWork Den Web、Den API 和独立 MySQL。Den 使用 `single_org`，允许普通员工在公司入口自助注册，但不能创建第二组织；Den 统一负责员工登录、单一公司组织、成员与团队、FoxWork 桌面交接、MCP、Skills、共享模型和桌面策略；员工不再使用第二套 Brand Project OS 登录或“团队连接”账号。
- Den 的 `ee/**` 使用 `FSL-1.1-MIT`，当前只允许按公司内部用途采用和修改。不得称为 OSI 开源组件，不得把 FoxWork/Den 作为与 OpenWork 相同或近似的对外竞争服务；内部发行也必须保留许可证与版权声明。
- Brand Project OS Service 部署在公司服务器，承载项目资料、正式业务 API、权威事件与审批、证据元数据、MCP Gateway、多媒体处理和工作流适配器。
- Brand OS MCP 作为公司 MCP 登记到 Den 并按成员或团队下发。MCP 是 AI 访问协议，不是数据库或业务核心；FoxWork 通过版本化业务 API 完成员工交互，Codex、Claude、Dify 等通过受控 MCP/Skills 使用同一业务能力。
- Den 负责远程工作区和远程 Worker 的生命周期、分配与运行策略；它们承载远程 Agent 运行，不保存项目正式状态或原件唯一副本。Brand OS Outbox/Worker 继续负责资料准入和多媒体派生任务。远程 Worker、服务器、MCP 和工作流都不得绕过 FoxWork 的本机授权访问员工电脑。
- FoxWork 团队文件协作仍是独立需求线。当前服务器只接入经批准的品牌项目资料，不自动接管团队全部文件同步、NAS 或个人工作区。

## 权威来源

- 原始项目文件及其 SHA-256：只证明原始内容和文件版本。
- 经人工确认的权威事件日志与审批记录：只证明当前正式状态如何形成。
- 当前状态投影：可从权威事件重建，不是独立真相源。
- Den MySQL、Den Worker 文件系统、OpenWork/OpenCode 会话、Zvec、FTS、Open Notebook、Nubase Memory、FlowLong、Dify、缓存和模型摘要：均为账号控制数据、运行态、派生数据或流程协调数据，不得覆盖正式状态。
- Phase 1 本地 SQLite 是迁移前权威；团队阶段完成一次性校验切换后，PostgreSQL 是唯一可写正式状态源，S3 兼容对象存储保存原件版本。不得长期双写或形成双主。
- S3 原件准入必须使用开启版本控制的桶；临时对象不能进入证据链，只有 `ACTIVE` 版本可以作为正式证据。PostgreSQL 与对象存储不做分布式事务，上传失败、孤儿、撤销和删除必须通过状态机、延迟墓碑和可审计对账恢复。
- 员工只维护一套 Den 账号。Brand OS 通过 Den OAuth/OIDC 第一方短期令牌按 `(issuer, subject)` 绑定员工；普通员工自助注册后，首次访问 Brand OS 可依据可信 Den 发行者、唯一公司组织和有效成员关系建立内部身份映射，但不得因此自动获得项目权限或业务审批权。邮箱不得作为身份键自动建号、合并或重新绑定账号，原始 Den Session Token 不得跨服务复用。交互式人工命令必须来自有效 Brand OS 服务器会话，撤销或绝对过期时清空令牌密文。

## 核心规则

- AI 只能提出变更，不得批准事实、决定、约束、外部承诺、负责人、截止时间或提交版本。
- `VIEW`、`PREFERENCE`、`HYPOTHESIS`、`OPTION`、`TENDENCY`、`TARGET_DATE` 不得自动升级为 `DECISION`、`CONSTRAINT` 或 `DEADLINE`。
- 新资料和新会议只生成增量 Proposal；未经人工确认不得静默覆盖当前状态。
- 开始任务时按“当前状态 -> 当前阶段与任务 -> 已批准决定 -> 开放问题 -> 相关证据 -> 必要原文”装配上下文，历史废案默认不进入当前上下文。
- Task Packet 必须是不可变派生快照；任务角色和工作模式由 Fox 登记，AI 只能建议模式切换。每次 Agent 运行必须绑定 Packet 哈希、状态版本、任务版本、协议、运行时和模型版本。
- 本地 CLI、stdio MCP、远程 MCP 和桌面 API 必须调用同一版本化应用服务。非交互入口不得声明为人工身份；MCP 项目范围必须显式固定，输入 Schema 拒绝额外字段，只能开放白名单工具。
- 个人模型登录由 Codex、Claude 或本机运行时自行管理；公司共享 Provider、模型和密钥由 Den 管理并只下发给获授权 FoxWork。Brand Project OS 不读取、转存或记录模型 API Key。模型切换必须复用已保存的 Task Packet，并受 Packet 模型允许列表约束。
- 探索协议与执行规格必须显式分离和切换；AI 不得在探索中强行收口，也不得在执行中重写已批准战略。
- 领域核心不得直接依赖 OpenWork、OpenCode、Zvec、Nubase、Open Notebook、FlowLong、Dify 或具体模型 SDK；必须通过版本化端口和 Schema 调用。
- 所有外部组件必须可禁用、可替换；检索索引必须可从权威数据完整重建。
- OpenWork/OpenCode 自有的会话、工具权限和本地 SQLite/JSON 运行态不得承担正式事实、审批、负责人、截止时间或外部承诺；这不包括 Phase 1 的 Brand Project OS 权威 SQLite。
- OpenCode Tool Permission 只允许本次 Agent 工具执行，不是 Brand Project OS 业务批准；两者必须使用不同 Schema、路由、权限和审计。
- Phase 1 默认在本机处理；团队切换后默认在公司服务器控制边界内处理。数据发送到公司控制边界之外前，必须检查保密级别、提供商、任务范围和人工授权；自托管 Dify/FlowLong 中的外部模型、插件和 HTTP 节点仍视为外发。
- 所有正式写请求必须有幂等键和预期版本。冲突返回差异并要求刷新确认，不得以最后写入覆盖。
- 原件上传必须先进入隔离状态并流式校验 SHA-256、大小、MIME 和安全检查；同名异内容按内容地址和版本号保存，不得静默覆盖。对象删除前必须有获授权员工撤销和延迟删除墓碑。
- 视频、录音、图片、PPT、Office 和 PDF 的解析、OCR、转写、时间码、页码和模型分析都是派生 Artifact。每个结果必须保留原件 ID、版本、SHA-256 和定位信息；解析失败可重试或替换适配器，但不能产生无来源的正式事实。
- F2.10 的 PostgreSQL 逻辑备份只用于一致快照和隔离恢复验证，不得写成生产 PITR。恢复只能进入空数据库，完成后必须核对全表摘要、事件重建投影和明确 S3 VersionId。Fox 已批准 99.5% 月可用性、PostgreSQL RPO 不高于 5 分钟、核心服务 RTO 不高于 60 分钟及“小团队托管部署”档位；这些仍是待 Phase 4 验证的内部目标。每次生产恢复点选择和切换仍需获授权员工确认，并使用独立备份域验证。
- 服务器审批必须绑定交互式员工身份、项目权限和审计事件。服务账号、MCP、Skill、工作流和 Agent 令牌没有批准正式状态的权限。
- 项目访问必须先经过应用层角色、动作和保密级别授权；PostgreSQL RLS 只作纵深防御。运行时数据库角色不得拥有表、不得 `BYPASSRLS`，每个事务只用 `SET LOCAL` 注入当前主体、项目和动作。
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
- 2026-07-24 完成第三次正式 `rescope`：保留已完成的 Phase 0-2 和 F3.1，后续采用 Den 统一账号与 AI 控制面、Brand OS 权威业务服务、多媒体资料处理和团队试点。旧的“不部署 Den”和“双登录/自建团队连接”路线只作追溯。
- 2026-07-24 完成后续范围校准：普通员工可在公司 Den 入口自助注册，Den 后台管理员网页也必须全中文；Den 远程工作区/Worker 是 F3.3-F3.19 必验运行面。任务总数和完成度不变，当前仍为 F3.3、29/56。
- 每完成一个任务，先记录执行遥测，再更新对应阶段文件和 `docs/progress/MASTER.md`。
- GitHub Wiki 只在大阶段目标完成并通过对应阶段门后统一更新；普通任务、SPEC 校准和未通过阶段门的中间状态只提交仓库文档，不提前同步 Wiki。
- 若任务产生稳定规则或架构不变量，更新本文件和相应 ADR；未经用户明确批准，不创建仓库内 Memory 文件。

## 验证入口

- `uv run pytest`
- `uv run brand-os doctor`
- `uv run brand-os --project hongri verify`
- OpenWork fork 的 App、Desktop、Server、Orchestrator 类型检查与测试
- OpenWork fork 的 Den API、Den Web、Den DB、单组织账号、OAuth/OIDC、Skills、共享模型和 MCP 权限测试
- OpenWork fork 的 Electron 打包扫描、桌面流程验收和安装烟测

本地核心、CLI 和 stdio MCP 已有可运行入口；员工界面只在公司定制版 OpenWork 中实现。不得用单独 Web 或另一个桌面壳替代该交付边界。
