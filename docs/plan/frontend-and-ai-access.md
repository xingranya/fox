# 前端与 AI 访问规划

## 统一访问原则

Web/PWA、CLI、MCP、Skills、自动化和后续桌面代理都调用同一应用服务，入口只负责协议和交互，不重复实现领域规则。

~~~text
Web/PWA ----+
CLI --------+--> 身份网关 --> Versioned API --> 领域核心 --> PostgreSQL
Remote MCP -+
Skills -----+
Dify -------+
~~~

浏览器、Skill、Dify 工作流和 MCP 客户端都不能持有数据库凭据。所有写请求携带幂等键和 expected_version；所有响应返回资源版本和 request_id。

## 首发前端

首发采用响应式 Web + PWA，不把 Tauri 作为上线前置条件。浏览器便于统一升级、远程协作和手机审批；Tauri 或本地同步代理只在需要受控目录监听、大文件断点续传、系统钥匙串和加密离线缓存时引入。

| 一级入口 | 主要内容 |
|:---|:---|
| 今日 | 当前阶段、目标、我的任务、开放问题、风险和最近变化 |
| 工作 | 列表/看板、负责人、依赖、时间性质、交付物和状态版本 |
| 知识 | 原始资料、会议、证据、决定、版本谱系和回源 |
| 待确认 | 状态变更、会议候选、任务候选、提交件和冲突处理 |
| AI 工作 | Task Packet、运行记录、候选对比、证据引用和采用结果 |
| 管理 | 团队、角色、能力登记、审计、备份和系统健康 |

桌面使用主从分栏，证据详情常驻右侧；手机首发保证今日、搜索、任务和审批四条高频路径。聊天是辅助入口，不是产品首页或唯一工作方式。

## 身份与角色

组织角色和项目角色分离：

| 角色 | 主要权限 |
|:---|:---|
| 组织所有者 | 身份、安全、组织配置、项目创建和基础设施管理 |
| 项目负责人 | 项目正式状态和对外承诺的最终批准 |
| 项目管理员 | 成员、资料准入和项目配置；默认没有最终批准权 |
| 审批人 | 只批准被授权的决定、任务或交付物范围 |
| 编辑者 | 上传、编辑、评论和创建 Proposal |
| 查看者 | 只读获授权内容 |
| 外部访客 | 限项目、限资源、限时间的只读或评论权限 |
| AI/服务账号 | 读取最小上下文、检索证据、创建 Proposal；永远不可批准 |

- Web 使用 OIDC 授权码流程、服务端安全 Cookie 和 MFA。
- CLI 使用设备授权或 PKCE 本机回调，刷新令牌进入系统钥匙串。
- 自动化账号使用短期、可撤销、最小权限凭据，禁止共享账号和永久全局 Token。
- 高风险外部承诺可开启双人复核和短时重新认证。
- 撤销成员或服务账号后，旧会话和令牌必须在目标时间内失效。

## 远程 MCP

服务器提供受保护的 Streamable HTTP MCP 入口。MCP 只公开确定性的读取、证据回源和 Proposal 工具；approve、reject、任意 SQL、成员管理、原始文件硬删除和工作流自动批准不进入工具表。

Codex 当前官方文档确认桌面端、CLI 和 IDE 共用 MCP 配置，并支持 Streamable HTTP、Bearer Token 和 OAuth。项目可提交不含密钥的 .codex/config.toml 示例：

~~~toml
[mcp_servers.brand_project_os]
url = "https://brand-os.example.com/mcp"
bearer_token_env_var = "BRAND_OS_MCP_TOKEN"
enabled_tools = ["project_get_state", "task_get_packet", "evidence_search", "evidence_get", "proposal_create"]
default_tools_approval_mode = "writes"
tool_timeout_sec = 60
~~~

生产优先使用 OAuth；Bearer 环境变量仅用于受控试点和服务账号。只支持 stdio 的客户端通过 brand-os mcp proxy 本地桥接 HTTPS，桥接器不缓存业务事实。

参考：[Codex MCP](https://learn.chatgpt.com/docs/extend/mcp)、[MCP Streamable HTTP 规范](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)。

## CLI 与 Skills

- CLI 提供登录、状态、证据、导入、Proposal、诊断和受控审批命令；审批必须显示差异、重新认证并要求明确确认。
- Repo Skill 存放在 .agents/skills/brand-project-os/SKILL.md，只描述可复用步骤、工具顺序、Schema 和失败处理。
- Skill 不保存项目事实、聊天摘要、服务器数据、API Key 或数据库连接串。
- Skill 的典型流程是“读取 Task Packet -> 检索并回源 -> 生成工作结果 -> 创建 Proposal -> 等待人工确认”。
- 团队分发时可将 Skills 和 MCP 配置打包为插件，但插件仍不获得批准权限。
- AGENTS.md 保存长期协作规则；Skills 保存可重复工作流；MCP 连接实时共享状态；CLI 服务人和自动化脚本，四者不互相替代。

参考：[Codex Skills](https://learn.chatgpt.com/docs/build-skills)。

## Dify 接入

Dify 通过 AIWorkflowPort 成为可替换的 AI 工作流执行器，适合会议候选抽取、Task Packet 结构化、多模型生成比较和内容转换。Brand Project OS 后端是唯一调用方，浏览器、CLI、MCP 和 Skill 不直接持有 Dify API Key。

~~~mermaid
sequenceDiagram
    participant U as 团队成员或 AI
    participant B as Brand Project OS
    participant D as Dify
    participant P as PostgreSQL
    U->>B: 发起 AI 工作任务
    B->>P: 校验身份、范围和数据级别
    B->>P: 写入运行记录与 Outbox
    B->>D: 后端 Bearer 调用，传最小 Task Packet
    D-->>B: 返回结构化候选和运行信息
    B->>P: 校验 Schema，保存为 Proposal
    B-->>U: 展示候选与证据，等待人工决定
~~~

Dify 官方 API 要求 API Key 只由后端持有，自托管实例使用自己的 API Base URL。接入必须固定工作流键、工作流导出版本、输入输出 Schema、模型和插件清单、超时、重试、数据级别和退出方案。

Dify Community Edition 使用基于 Apache 2.0 的附加条件许可：未经书面授权不得用源码运行多租户环境；使用其前端时不得移除或修改 Logo 和版权信息。因此首个 POC 只使用单内部 Workspace 和后端 API，任何多租户部署、白标或分发都必须先通过书面许可审查。

自托管 Dify 不代表数据不会外发。模型提供商、Embedding、插件、HTTP 节点和遥测必须逐项登记；P2/P3 数据只允许批准的本地模型或经过任务级人工授权的脱敏副本。

参考：[Dify API 入门](https://docs.dify.ai/en/api-reference/guides/get-started)、[Dify 运行工作流](https://docs.dify.ai/en/api-reference/workflow-runs/run-workflow)、[Dify 许可证](https://github.com/langgenius/dify/blob/main/LICENSE)。

## Dify 与 FlowLong 分工

| 组件 | 负责 | 不负责 |
|:---|:---|:---|
| Dify | 模型调用、提示链、AI 计算节点、派生候选 | 正式任务状态、权限、人工审批和事件账本 |
| FlowLong | 会签、转办、提醒、人工任务流程协调 | AI 推理、正式批准事件和领域真相 |
| Brand Project OS | 权限、状态机、证据、Proposal、审批复核和正式事件 | 具体模型或外部流程引擎内部实现 |

Dify 和 FlowLong 回调都先落入待处理外部结果，经过身份映射、幂等、Schema、版本和权限复核后，才能由领域用例产生 Proposal 或人工动作；任何回调都不能直接批准。

## 离线与本地文件

- PWA 可缓存允许离线查看的最近状态，并显示同步时间和过期警告。
- 离线只保存草稿和 Proposal，不允许批准、删除原始资料、修改成员或确认外部承诺。
- 恢复网络后按基础版本提交；服务器版本变化时进入人工冲突处理。
- P2/P3 资料默认不进入浏览器持久缓存；需要离线敏感数据时由后续 Tauri 客户端提供加密缓存。
- 浏览器不能任意读取成员电脑。目录监听通过显式安装、明确选择目录、最小权限的本地上传代理完成。
- 服务器故障时使用只读快照或签名导出包应急，不启动第二个可写数据库。

## 跨入口验收

1. 同一用户从 Web、MCP 和 CLI 查询同一项目，得到相同状态版本和证据链。
2. AI 服务账号从任一入口调用批准操作均被拒绝并留下审计。
3. 撤销成员后，Web 会话、CLI Token、MCP OAuth 和自动化凭据按目标时间失效。
4. PWA 断网后不能批准，联网提交草稿时能识别版本冲突。
5. Dify 停止、超时或返回错误 Schema 时，权威状态不变且核心查询可用。
6. Dify 和直接 Worker 对相同金标任务运行 A/B，只有通过证据、隐私、成本和稳定性门的实现可成为默认。
7. 所有外部调用可由 request_id 关联到操作者、Task Packet、数据级别、模型/工作流版本和最终 Proposal。
