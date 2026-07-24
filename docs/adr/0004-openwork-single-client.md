# ADR-0004：公司定制版 OpenWork 是唯一员工客户端

- 状态：已接受
- 日期：2026-07-22
- 决策人：Fox
- 影响范围：员工安装、桌面界面、Agent Runtime、MCP/Skills 接入、服务器边界和发布方式
- 取代：[ADR-0002](0002-openwork-primary-client.md) 的条件性候选结论
- 保留：[ADR-0003](0003-local-first-hongri-validation.md) 的人工确认和本地价值验证规则
- 配套：[ADR-0005](0005-single-client-server-authority.md) 的服务器权威与团队部署决策
- 命名与语言：[ADR-0006](0006-foxwork-name-and-chinese-ui.md)
- 账号与控制面：[ADR-0007](0007-adopt-openwork-den-control-plane.md)

## 决策

1. 员工唯一需要安装的软件是公司定制版 OpenWork。2026-07-23 起发行名固定为 FoxWork，员工界面只使用简体中文；客户端代码基础和主要交互界面仍是 OpenWork，不再另做一个 Web 或桌面客户端。
2. Brand Project OS 是 OpenWork 内的业务能力层，负责当前状态、证据、会议增量、Task Packet、Proposal 和人工确认。它不是员工需要单独安装或打开的第二个软件。
3. OpenCode Runtime、Sidecar、本机文件桥接、终端桥接和必要的本地服务随同一个 OpenWork 安装包分发。它们可以作为后台进程运行，但不能要求员工再安装、登录或管理另一个客户端。
4. 公司服务器部署 Den Web/API/MySQL/远程 Worker 和 Brand Project OS Service/PostgreSQL/S3。Den 统一账号、组织、团队、远程工作区、MCP、Skills 和共享模型；Brand OS 提供版本化业务 API 和公司 MCP。Codex、Claude 等其他 Agent 平台可以连接同一组受控能力，但不是员工的第二套主界面。
5. 必须访问员工电脑的能力留在 OpenWork 的受控本机桥接中，包括用户明确选择的文件、终端和桌面操作。服务器 MCP 不得绕过客户端权限读取员工电脑。
6. F1.9-F1.10、Phase 2、F3.1 和 F3.2 已完成。当前按 ADR-0005、ADR-0007 和 ADR-0008 执行 F3.3 Den 与远程 Worker 生产部署基线；后续统一登录、项目工作区和联网业务闭环不得恢复双主或第二套账号。
7. OpenWork/OpenCode 会话、Tool Permission、运行 SQLite 和缓存仍是运行态。正式事实、决定、约束、负责人、日期和外部承诺只能由 Brand Project OS 的人工确认事件形成。
8. 上游 MIT 与 `FSL-1.1-MIT` 许可证和版权声明必须按目录保留；公司发行版不得暗示获得 OpenWork 官方背书，也不得把 Den 作为对外竞争服务。

## 单一安装形态

```text
员工
  -> 安装并打开 FoxWork
       -> 内置 OpenCode Runtime / Sidecar
       -> 受控访问本机文件、终端和桌面能力
       -> 连接公司 Den Web
            -> 注册、登录、组织、团队、MCP、Skills、共享模型
            -> 获取 Brand OS 第一方短期令牌
       -> 访问 Brand Project OS API

Codex、Claude 等其他 Agent
  -> 可通过 Den 授权连接同一 Brand OS MCP / Skills
  -> 仅作为辅助入口，不要求员工另装第二个主客户端
```

## 验收标准

- 员工安装一个应用即可打开项目、查看状态和证据、发起 AI 工作、处理 Proposal 并查看流程状态。
- 安装说明不得要求员工另装 OpenCode、Codex、Claude Code 或 Brand Project OS 客户端。
- 所有后台运行时都由同一安装包提供，并能从 OpenWork 内查看健康状态和错误。
- 服务器 MCP 与本机工具使用不同权限边界；远程工具不能任意读取本机文件。
- OpenWork 被卸载或运行数据被清理时，正式项目状态和证据仍可从权威层恢复。
- MCP、Skills、CLI 和其他 Agent 输出不能绕过人工确认改变正式状态。
- FoxWork、Den 员工页面和 Den 后台管理员网页只显示简体中文，没有语言选择或英文回退。

## 非目标

- 不再开发第二个员工 Web/PWA 或桌面客户端。
- 不把 Codex、Claude Code 或其他 Agent 平台作为员工必须安装的主产品。
- 不把 OpenWork Server、OpenCode Session 或 MCP 日志当作业务真相源。
- 不把 MCP Gateway 当作业务数据库、审批服务或本机权限桥接的替代品。
