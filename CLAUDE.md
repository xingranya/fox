# Claude Code 项目说明

首先读取根目录 `AGENTS.md`，其内容是跨 Codex、Claude 和其他开发代理共用的权威协作规则。

- 当前产品形态是“FoxWork 唯一员工客户端 + 公司自托管 OpenWork Den 控制面 + Brand Project OS 权威业务服务”。Phase 0-2、F3.1 和 F3.2 已完成，当前任务是 F3.3 Den 与远程 Worker 生产部署基线，不得跳过阶段门。
- 员工只维护一套 Den 账号。Den 使用 `single_org`，允许普通员工在公司入口自助注册，但不能创建第二组织；Den 统一登录、组织、团队、远程工作区、MCP、Skills、共享模型和桌面策略，Brand Project OS 不再提供第二套账号或旧“团队连接”入口。
- 普通员工首次访问 Brand OS 时，可按可信 Den `(issuer, subject)`、唯一公司组织和有效成员关系建立内部身份映射；这不是第二套账号，也不自动授予项目权限或业务审批权。邮箱不能作为身份键自动合并或重新绑定账号。
- FoxWork、Den 员工页面和 Den 后台管理员网页全部使用简体中文，不提供语言切换或英文回退。
- Den 管理远程工作区和远程 Worker，但其文件系统、Session 和缓存不是业务真相源，也不能绕过 FoxWork 本机授权访问员工电脑。
- 开始品牌任务时，按“当前状态 -> 当前阶段与本轮任务 -> 已批准决定 -> 开放问题 -> 相关证据 -> 必要原文”读取，不依赖旧聊天记忆，也不从全量文件随机检索开始。
- 必须读取明确的工作模式；探索、评估、决策和执行不可自行切换。
- `VIEW`、`PREFERENCE`、`HYPOTHESIS`、`OPTION`、`TENDENCY` 和 `TARGET_DATE` 不得自动解释为 `DECISION`、`CONSTRAINT` 或 `DEADLINE`。
- 只能创建状态变化 Proposal，不得批准事实、决定、约束、负责人、截止时间或提交版本；证据不足时必须明确说“未确认”。
- 新会议只生成增量变化、冲突和待确认项，不重写完整项目历史。
- OpenWork/OpenCode 会话和 Tool Permission 仅属于 Agent Runtime，不是业务真相或正式批准。
- 当前可通过 `brand-os` CLI 或本地 stdio MCP 读取状态、Task Packet、证据和提交 Proposal；Phase 3 将 Brand OS MCP 注册到 Den，并由 Den 按成员或团队下发。任何 MCP 都不直接修改底层存储，也没有批准、任意 SQL、原件硬删除或模式切换工具。
- Phase 3 权威切换后，PostgreSQL 是唯一可写正式状态源。本地 SQLite 只读，不得形成双主或离线批准。
- Claude 的提供商登录由 Claude Code 自己管理，不得把密钥写入 Brand Project OS 配置、Task Packet、Skill 或日志。
- 不重复维护共享工程规则；跨代理规则统一写入 `AGENTS.md`，运行时品牌行为写入独立协议。
- 当前没有获准的仓库内 Memory 文件，不得自行创建。
