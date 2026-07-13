# 前端与 AI 访问规划

> 当前范围：Fox 本地单用户鸿日 MVP<br>
> 客户端路线：先交付最薄可用本地界面；OpenWork 通过纵向切片后才条件性采用
> 团队 Web、OIDC、远程 MCP 与移动审批：远期候选，不是当前前置

## 统一本地访问原则

图形界面、CLI、MCP、Skills、Codex、Claude、OpenCode 和可选 OpenWork 都调用同一本地应用层。应用层统一装配当前状态、Task Packet、证据、Proposal 和人工确认；任何入口不得直接修改 SQLite 正式表或内容寻址证据区。

```text
Fox 本地界面 --------+
本地 CLI ------------+
本地 stdio MCP ------+--> 本地应用层 --> 领域核心 --> SQLite / 只读证据区
Skills / Agent -------+         |
可选 OpenWork --------+         +--> Task Packet / Proposal / Artifact
```

当前不要求登录、远程 HTTPS 或团队服务器。Fox 的本地人工确认与 AI Tool Permission 是两个独立动作：前者可以改变正式项目状态，后者只能允许本次运行读文件、执行命令或调用工具。

详细语义见[运行时品牌 Agent 与会议解释协议](runtime-brand-agent-and-meeting-protocol.md)，OpenWork 采用条件见[ADR-0002](../adr/0002-openwork-primary-client.md)。

## 客户端组合

| 入口 | 当前定位 | 当前能力 | 明确边界 |
|:---|:---|:---|:---|
| 本地查看与确认界面 | Fox 日常主入口 | 当前状态、资料、会议、证据、关系、待确认、AI 运行和诊断 | 不做通用项目管理平台；不直接写数据库 |
| 本地 CLI | 确定性维护与诊断入口 | 初始化、导入、状态、Task Packet、会议、Proposal、校验和备份 | 非交互 Agent 不能调用人工批准命令 |
| 本地 stdio MCP | Codex、Claude 等 AI 的统一上下文入口 | 状态、Task Packet、证据搜索/回源、会议解释和 Proposal | 不暴露批准、直接 SQL、硬删除或模式强制切换 |
| Skills | 可复用品牌工作法 | 读取顺序、角色、工作模式、输出 Schema 和失败处理 | 不保存事实、项目状态、密钥或旧聊天摘要 |
| Agent Runtime | 执行本轮任务 | 调用模型、流式事件、工具权限、Artifact 和 Proposal | 运行结果不是正式状态 |
| OpenWork | 条件性本地客户端/运行控制面 | 仅在切片通过后复用壳、会话、工具和本地文件交互 | 不能要求先部署 OpenWork Server 或团队基础设施 |

## 本地界面信息架构

首屏是鸿日工作台，不是纯聊天，也不是面向团队管理的仪表盘。界面保持紧凑、可扫描、证据常驻：

| 入口 | Fox 要解决的问题 | 核心内容 |
|:---|:---|:---|
| 当前 | 现在做到哪里，什么已经确认 | 当前阶段、目标、批准决定/约束、开放问题、行动、状态版本和最近变化 |
| 资料 | AI 到底依据了什么 | 原始资料、版本、哈希、来源角色、内容索引、缺失/损坏状态和原文打开 |
| 会议 | 这次会议真正改变了什么 | 会议模式、原话片段、VIEW/PREFERENCE/OPTION/TENDENCY 等分类、冲突和增量变化 |
| 待确认 | 哪些变化需要我判断 | Proposal 旧新差异、原话、证据、影响范围、冲突、批准/修改/驳回 |
| 策略探索 | 还有哪些真正不同的选择 | 矛盾、假设、战略领地、选择收益/代价、待验证问题和 Fox 选择闸门 |
| 执行 | 已批准方向如何落地 | 当前执行规格、事实/约束、文案/命名/物料任务、验收和废案隔离 |
| AI 工作 | 本轮 AI 读了什么、做了什么 | Task Packet 层级、角色、模式、模型、工具请求、Artifact、证据引用和采用结果 |
| 诊断 | 系统是否可信 | SQLite、证据哈希、索引水位、协议版本、备份、金标和最近错误 |

### 关键交互

- 任何重要结论旁都有“查看依据”，打开原始文件或会议时间点，而不是只显示摘要。
- 已批准层、工作层和历史/废案使用明确标签与分区，不能只靠颜色。
- 会议页默认显示“相对当前版本新增/冲突/建议修改”，完整摘要作为辅助，不覆盖状态。
- `TARGET_DATE` 同时显示日期性质；暂定、内部目标、评审节点和外部截止的文案不同。
- 待确认使用逐项差异。决定、约束和外部截止默认不能一键批量批准。
- 工作模式始终可见；从探索切换到执行必须由 Fox 明确操作并说明范围。
- 聊天只是 AI 工作中的一种呈现，不成为项目首页或唯一入口。

## 开发规则与运行时规则

本地客户端不得把仓库 `AGENTS.md` 直接当作品牌 Agent 系统提示。运行时按以下顺序装配：

1. 品牌 Agent 宪法：证据纪律、品牌判断、开放性、语言和人工最终判断权。
2. 工作模式协议：探索、评估、决策或执行的允许/禁止行为。
3. 鸿日项目规则：当前目标、批准方向、开放问题、反例和非目标。
4. 本轮 Task Packet：角色、任务、证据、输出 Schema、工具范围和验收。

开发者模式与品牌运行模式使用不同配置、日志标签和规则版本。界面不得向 Fox 展示数据库、RLS、Outbox 等开发术语作为品牌角色说明。

## Task Packet 体验

Task Packet 分为 L0-L4：

- **L0 任务头**：角色、工作模式、目标、交付、非目标、输出 Schema 和质量基线。
- **L1 当前状态**：当前阶段、批准事实/决定/约束、开放问题、行动、禁区和状态版本。
- **L2 相关证据**：本任务所需片段、会议、关系和来源定位。
- **L3 原始内容**：模型按需打开的 PDF、录音、Brief、提案或研究原文。
- **L4 历史与废案**：只在复盘、排重、冲突和风险检查时读取。

AI 工作页必须显示模型实际领取的 Packet 版本、载入层、证据集合、缺口和外发范围。Fox 可以打开同一 Packet 检查“模型为什么这样理解”，但不能直接编辑生成后的 Packet 来绕过当前状态；需要修改时应回到状态或项目规则形成显式变化。

## 本地身份与安全

- 当前操作者是本机 Fox，不建设 OIDC、团队账户、邀请、角色或设备撤权。
- 人工批准用例必须来自本地交互界面或明确进入“Fox 人工确认”的交互式 CLI；MCP/Agent 非交互调用被拒绝。
- 数据库、证据区和备份使用用户目录最小权限，并依赖 macOS 磁盘加密；运行日志不记录密钥、完整敏感原文或模型提供商凭据。
- 外部模型调用前显示或记录提供商、发送范围、任务目的和数据策略。敏感资料默认本地处理或使用经 Fox 明确授权的最小片段。
- 本地 API 如使用回环 HTTP，必须绑定 `127.0.0.1`/Unix Socket、随机短期令牌和严格路由，不监听局域网无认证端口。
- Agent 只访问本轮明确授权的目录；主进程或运行时适配器复核符号链接、路径穿越、外部命令和网络范围。

## Agent 运行与两类确认

| 项目 | Fox 业务确认 | Tool Permission |
|:---|:---|:---|
| 确认内容 | 事实、决定、约束、行动、时间性质和状态变化 | 本次 Agent 能否读目录、执行命令、访问网络或调用工具 |
| 执行位置 | 本地领域应用层 | AgentRuntime/OpenWork/OpenCode 运行层 |
| 结果 | 追加人工动作与领域事件，更新当前投影 | 只改变本次运行可执行范围 |
| 可执行主体 | Fox 的显式本地交互 | Fox 或受限预设运行策略 |
| 审计 | 旧新值、证据、范围、理由和状态版本 | 工具、参数摘要、路径/网络、时限和运行 ID |

Tool Permission 的“始终允许”也不能映射到业务批准。AI 页面中的“采用产物”先创建 Proposal，再跳转待确认，不得直接更新当前状态。

## 本地 MCP

本地 MCP 优先使用 stdio，不要求远程 OAuth。首批工具：

- `project_get_state`
- `task_get_packet`
- `meeting_list`、`meeting_get`、`meeting_interpret`
- `evidence_search`、`evidence_get`
- `decision_list`、`open_question_list`、`action_list`
- `proposal_create`、`proposal_get`
- `system_doctor`、`project_verify`

MCP 工具不包含批准、驳回、直接 SQL、成员管理、原件硬删除或工作模式强制切换。客户端只得到本轮项目和证据范围，不默认暴露 `/Users/fox/work` 的全部文件。

## CLI 与 Skills

- CLI 提供 `init`、`import`、`status`、`meeting ingest`、`meeting interpret`、`task packet`、`proposal list`、`verify`、`backup` 和 `doctor` 等确定性入口。
- 人工确认命令必须是单独的交互模式，显示差异和证据，拒绝从 stdin 脚本或 Agent 工具无提示调用。
- Skill 只描述品牌角色、工作模式、读取顺序、工具步骤、输出 Schema、证据要求和失败处理。
- 标准流程是“读取 Task Packet -> 按需回源 -> 生成 Artifact/Proposal -> 等待 Fox 确认”。
- AGENTS 管软件开发，运行时品牌规则管业务行为，Skill 管可重复工作法，MCP 读实时状态，CLI 做确定性操作；五者不能互相替代。

## OpenWork 条件性接入

OpenWork 不自动成为主客户端。先验证以下本地旅程：

```text
启动本地客户端
-> 打开鸿日
-> 读取当前状态和 Task Packet
-> 启动一个本地 Agent
-> 查看工具请求和流式结果
-> 生成带证据的增量 Proposal
-> Fox 在独立待确认界面批准/修改/驳回
```

接入原则：

- Brand 业务页面调用本地应用层；OpenWork `workspaceId/sessionId` 只作运行关联，不复用为项目/状态 ID。
- OpenWork Server、Orchestrator 和远程 Workspace 不是当前必需组件；优先进程内、本地子进程或回环适配。
- OpenCode 可以作为 `AgentRuntimePort` 首个实现，但业务页面不导入其 SDK 类型。
- OpenWork 本地 SQLite/JSON、会话和设置可删除；正式鸿日状态必须完整。
- 若为了完成主旅程需要团队服务器、OIDC、`ee/**`、Den 或大面积修改上游会话内核，停止接入并采用薄本地界面。

完整准入和退出门见[OpenWork 深度集成计划](openwork-deep-integration.md)。

## Dify、FlowLong 与其他组件

Dify、FlowLong、Zvec、Open Notebook 和 Nubase 不进入当前鸿日 MVP 必需链路：

| 组件 | 当前结论 | 未来可能角色 |
|:---|:---|:---|
| Dify | 不接入主链 | 可替换 AI 工作流执行器 |
| FlowLong | 不接入主链 | 团队复杂人工流程协调器 |
| Zvec | 不作为前置 | FTS5 不足时的可重建检索增强 |
| Open Notebook | 不作为前置 | 研究 sidecar 或内容处理适配器 |
| Nubase | 不作为前置 | 单项能力 POC，不承担权威状态 |

当前多模型优先通过轻量本地适配器完成，先证明 Task Packet 与运行时协议有效。

## 远期团队访问候选

鸿日试点后若 ADR-0001 通过复审，可在不改变业务协议的前提下增加：

- OIDC、团队账户、角色/Scope、设备撤权和审计。
- HTTPS 版本化 API、OAuth 远程 MCP、轻量 Web 和移动审批。
- PostgreSQL/S3 权威实现、Outbox Worker、托管 Agent Worker 和服务监控。
- 签名桌面分发、私有更新、兼容窗口和灾备。

这些能力必须复用当前 Schema、Task Packet、Proposal、人工批准和 AgentRuntime 端口；不能要求重新解释鸿日历史或建立第二套业务规则。

## 当前验收

1. Fox 启动本地界面后一分钟内找到鸿日当前阶段、决定、开放问题、最近会议和待确认项。
2. 新 AI 不读旧聊天也能取得正确 Task Packet，并从证据链接打开原始资料或会议片段。
3. 会议中的偏好、倾向和暂定日期不会出现在正式决定/约束/硬截止区。
4. 新会议页只显示相对当前版本的增量变化、重复和冲突，不静默覆盖历史。
5. 探索界面保留多个策略选择和代价；切换执行需要 Fox 明确确认。
6. Codex、Claude、OpenCode 使用相同 Packet 时，事实、决定、约束和证据一致。
7. AI/服务账号从 MCP、CLI、Skill、OpenWork 或 Tool Permission 调用人工批准均被拒绝。
8. 删除索引、缓存、模型会话和 OpenWork 状态后，本地界面仍能显示并重建正式状态与证据链。
9. OpenWork 未通过采用门时，薄本地界面仍可完成全部核心旅程。
10. 当前 MVP 无需启动 Web、数据库服务器、Docker 或远程服务即可运行。
