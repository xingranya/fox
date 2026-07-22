# ADR-0004：公司定制版 OpenWork 是唯一员工客户端

- 状态：已接受
- 日期：2026-07-22
- 决策人：Fox
- 影响范围：员工安装、桌面界面、Agent Runtime、MCP/Skills 接入、服务器边界和发布方式
- 取代：[ADR-0002](0002-openwork-primary-client.md) 的条件性候选结论
- 保留：[ADR-0003](0003-local-first-hongri-validation.md) 的人工确认和本地价值验证规则
- 配套：[ADR-0005](0005-single-client-server-authority.md) 的服务器权威与团队部署决策

## 决策

1. 员工唯一需要安装的软件是公司定制版 OpenWork。Brand Project OS 是当前项目名，公司可以更换最终发行名称、图标、AppID 和协议，但客户端代码基础和主要交互界面就是 OpenWork，不再另做一个 Web 或桌面客户端。
2. Brand Project OS 是 OpenWork 内的业务能力层，负责当前状态、证据、会议增量、Task Packet、Proposal 和人工确认。它不是员工需要单独安装或打开的第二个软件。
3. OpenCode Runtime、Sidecar、本机文件桥接、终端桥接和必要的本地服务随同一个 OpenWork 安装包分发。它们可以作为后台进程运行，但不能要求员工再安装、登录或管理另一个客户端。
4. MCP Gateway、Skills 目录和版本化 Brand Project OS API 可以部署在公司服务器。定制 OpenWork 连接这些服务；Codex、Claude 等其他 Agent 平台也可以连接同一组 MCP/Skills，但这些入口只用于辅助，不是员工完成日常工作的第二套主界面。
5. 必须访问员工电脑的能力留在 OpenWork 的受控本机桥接中，包括用户明确选择的文件、终端和桌面操作。服务器 MCP 不得绕过客户端权限读取员工电脑。
6. F1.9 已完成离线、安全、品牌和单安装包收口；当前由 F1.10 完成鸿日纵向切片。随后按 ADR-0005 建设服务器权威服务、团队身份、并发控制和恢复能力；本地与服务器不得形成双主。
7. OpenWork/OpenCode 会话、Tool Permission、运行 SQLite 和缓存仍是运行态。正式事实、决定、约束、负责人、日期和外部承诺只能由 Brand Project OS 的人工确认事件形成。
8. 上游 MIT 许可证与版权声明必须保留；公司发行版不得暗示获得 OpenWork 官方背书。

## 单一安装形态

```text
员工
  -> 安装并打开公司定制版 OpenWork
       -> 内置 OpenCode Runtime / Sidecar
       -> 受控访问本机文件、终端和桌面能力
       -> 连接公司 Brand Project OS API / MCP / Skills

Codex、Claude 等其他 Agent
  -> 可连接同一服务器 MCP / Skills
  -> 仅作为辅助入口，不要求员工另装第二个主客户端
```

## 验收标准

- 员工安装一个应用即可打开项目、查看状态和证据、发起 AI 工作、处理 Proposal 并查看流程状态。
- 安装说明不得要求员工另装 OpenCode、Codex、Claude Code 或 Brand Project OS 客户端。
- 所有后台运行时都由同一安装包提供，并能从 OpenWork 内查看健康状态和错误。
- 服务器 MCP 与本机工具使用不同权限边界；远程工具不能任意读取本机文件。
- OpenWork 被卸载或运行数据被清理时，正式项目状态和证据仍可从权威层恢复。
- MCP、Skills、CLI 和其他 Agent 输出不能绕过人工确认改变正式状态。

## 非目标

- 不再开发第二个员工 Web/PWA 或桌面客户端。
- 不把 Codex、Claude Code 或其他 Agent 平台作为员工必须安装的主产品。
- 不把 OpenWork Server、OpenCode Session 或 MCP 日志当作业务真相源。
- 不把 MCP Gateway 当作业务数据库、审批服务或本机权限桥接的替代品。
