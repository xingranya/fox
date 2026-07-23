# 客户端与 AI 访问规划

> 员工客户端：FoxWork，基于 OpenWork 的公司定制版，只提供简体中文界面<br>
> 员工安装数量：1<br>
> 团队后端：Brand Project OS Service<br>
> AI 接口：stdio/远程 MCP、Skills 和版本化 CLI/API

## 产品入口

员工只使用一个客户端。OpenCode Runtime、Sidecar 和本机文件/终端/桌面桥接随安装包交付，在后台运行但不形成第二个产品。

```text
员工
  -> FoxWork（公司定制 OpenWork）
       -> Brand Project OS API -> 当前状态 / 证据 / Proposal / 人工确认
       -> 本机桥接 -> 经用户授权的文件 / 终端 / 桌面
       -> OpenCode Runtime -> AI 会话 / 工具请求 / Artifact

Codex / Claude / Dify / 其他 Agent
  -> MCP Gateway / Skills -> 同一应用服务
```

MCP 是 AI 访问协议，不是业务数据库。Desktop 的人工交互使用版本化业务 API；Agent 使用受限 MCP/Skills。两类入口最终调用同一领域用例，但人工批准路由只接受交互式员工身份。

## 入口职责

| 入口 | 使用者 | 能力 | 明确禁止 |
|:---|:---|:---|:---|
| FoxWork | 员工 | 状态、资料、会议、证据、Proposal、AI 工作、流程状态和诊断 | 直连 PostgreSQL/S3、从 Session 推导正式状态 |
| 本机桥接 | 当前设备上的员工 | 用户选择的文件、终端和桌面动作 | 服务器绕过授权、跨目录扫描、产生业务批准 |
| CLI | 运维、迁移和确定性诊断 | 初始化、导入、校验、备份、迁移和 doctor | Agent 非交互调用人工批准 |
| stdio MCP | Phase 1 本机 Agent | 当前状态、Task Packet、证据和 Proposal | 切换项目、批准、任意 SQL、任意文件 |
| 远程 MCP Gateway | Codex、Claude、Dify 等 | 按令牌 Scope 读取、回源和创建 Proposal | 冒充员工、跨项目、批准、数据库管理 |
| Skills 目录 | Desktop 与 Agent | 版本化工作法、工具顺序、Schema 和失败处理 | 保存实时事实、原文、密钥或私有聊天记忆 |
| Dify/外部工作流 | 受控自动化 | 调用 MCP/应用端口，执行流程并返回 Proposal | 成为正式状态源或绕过数据外发检查 |

## Desktop 信息架构

首屏是工作台，不是聊天首页，也不是营销页。桌面保持紧凑、可扫描，状态和证据优先。

| 入口 | 员工要解决的问题 | 核心内容 |
|:---|:---|:---|
| 当前 | 项目做到哪里，什么已经确认 | 阶段、目标、决定、约束、开放问题、行动、状态版本和最近变化 |
| 资料 | AI 依据了什么 | 原件、版本、SHA-256、来源角色、缺口和原文打开 |
| 会议 | 这次会议改变了什么 | 会议模式、原话、分类、重复、冲突和增量 Proposal |
| 待确认 | 哪些变化需要人判断 | 旧新差异、原话、证据、影响、冲突和批准/修改/驳回 |
| 策略/执行 | 当前是开放探索还是落地执行 | 模式、选择代价、已批准方向、执行规格和废案隔离 |
| AI 工作 | AI 读了什么、做了什么 | Task Packet、模型、工具请求、流式过程、Artifact 和采用结果 |
| 流程 | 自动化现在到哪一步 | Dify/FlowLong 等运行状态、失败、人工待办和重试 |
| 诊断 | 系统是否可信 | API、数据水位、同步、Runtime、备份、版本和最近错误 |

### 关键交互

- 每个重要结论都有“查看依据”，可以打开原始文件或会议片段。
- 已批准、工作中、待确认和历史废案使用文字标签与分区，不只靠颜色。
- 会议页默认显示相对当前版本的新增、冲突和建议修改；摘要不能覆盖状态。
- `TARGET_DATE` 显示日期性质，不把暂定、内部目标或评审点写成外部死线。
- 决定、约束和外部截止逐项确认，不提供默认批量批准。
- 工作模式始终可见。探索切换到执行需要员工显式动作和范围说明。
- 聊天属于 AI 工作页，不是项目状态源或唯一导航。
- 服务不可用时显示最后同步版本和时间，进入只读或草稿状态。

## 身份与两类确认

| 项目 | 业务确认 | Tool Permission |
|:---|:---|:---|
| 目的 | 确认事实、决定、约束、行动、时间性质和状态变化 | 允许本次 Agent 读目录、执行命令、访问网络或调用工具 |
| 身份 | OIDC 交互式员工身份 + 项目权限 | 当前员工或受限运行策略 |
| 处理位置 | Brand Project OS 人工确认用例 | OpenWork/OpenCode 运行层 |
| 结果 | 追加审批与领域事件，更新当前投影 | 只改变本次运行范围 |
| 审计 | 旧新值、证据、范围、理由、员工和状态版本 | 工具、参数摘要、路径/网络、时限和运行 ID |

Tool Permission 的“始终允许”不能映射到业务批准。AI 页面中的“采用产物”先创建 Proposal，再进入待确认页。

## Task Packet

Task Packet 分为 L0-L4：

- L0：角色、工作模式、目标、交付、非目标、输出 Schema 和质量基线。
- L1：当前阶段、批准事实/决定/约束、开放问题、行动、禁区和状态版本。
- L2：本任务相关证据、会议和关系。
- L3：按需打开的原始内容。
- L4：只在复盘、排重、冲突和风险检查时读取的历史与废案。

Desktop 显示模型实际领取的 Packet 版本、载入层、证据集合、缺口和外发范围。员工如需修改，应更新项目状态或规则并生成新 Packet，不能直接篡改已保存快照。

## MCP 与 Skills

Phase 1 已有 stdio MCP 白名单：

- `project_get_state`
- `task_get_packet`
- `evidence_get`
- `decision_list`、`open_question_list`
- `proposal_create`、`proposal_get`
- `system_doctor`、`project_verify`

Phase 3 的远程 MCP 复用同一 Schema，并增加 OIDC/OAuth、项目 Scope、令牌撤权、限流和审计。远程 MCP 不增加批准、直接 SQL、成员管理、原件硬删除、工作模式强制切换或任意本机文件工具。

Skills 只保存工作法。每个 Skill 有 ID、版本、兼容范围、签名、变更记录和回滚版本；运行时事实仍从 MCP/API 读取。

## Dify 与外部组件

| 组件 | 端口 | 接入条件 | 退出方式 |
|:---|:---|:---|:---|
| Dify | `AIWorkflowPort` | 外发清单、服务身份、回调幂等、超时和审计通过 | 切回直接 Worker/NoOp |
| Zvec | `SearchIndexPort` | 相对 PostgreSQL FTS 有可测收益，权限回源完整 | 删除索引并回退 FTS |
| Open Notebook | `ContentProcessingPort` | 解析质量、来源定位和失败隔离通过 | 停用适配器，保留原件与派生映射 |
| Nubase | `MemoryPort` | 记忆污染、删除和项目隔离通过 | 清空记忆并回退 Task Packet |
| FlowLong | `ApprovalWorkflowPort` | 许可证、回调、撤回和越权测试通过 | 使用内置待确认队列 |

这些组件都不能批准正式状态。F3.9-F3.12 可以得出“拒绝采用”；核心旅程必须在全部外部组件禁用时仍可完成。

## 离线与故障

- Desktop 缓存保存状态版本、服务器水位和同步时间，不能只显示无版本内容。
- 服务不可用时只允许查看缓存、编辑草稿和准备 Proposal；批准、驳回和正式提交禁用。
- 恢复联网后重新读取当前版本，草稿按新基线生成差异。冲突由员工处理，不自动合并决定。
- OpenCode/Sidecar 故障不影响状态和证据读取；AI 工作暂停并显示可诊断错误。
- Dify、Zvec、Open Notebook、Nubase 或 FlowLong 故障不影响核心 API 就绪状态。

## 客户端安全

- Electron 启用 Sandbox、`contextIsolation`、最小 preload 和 IPC 允许列表。
- Renderer 不直接获得 Node、数据库、对象存储或任意文件系统能力。
- 本机路径经过工作区允许列表、真实路径、符号链接和目录边界复核。
- OIDC 令牌和设备密钥进入系统钥匙串；不写入 URL、日志、Prompt、Skill、项目目录或 OpenWork SQLite。
- 外部导航、下载、深链、协议和网络出口使用允许列表。
- 服务地址、更新源、模型目录和外部提供商必须显式配置；没有配置时不回退上游公共服务。

## 验收

1. 员工安装一个应用即可登录、选项目、读状态/证据、运行 AI、处理 Proposal 和查看流程状态。
2. 两个客户端并发处理同一状态时，只有一个版本成功，另一端看到可理解的差异。
3. Codex、Claude 和 Dify 读取同一 Task Packet 时，正式事实、决定和证据一致。
4. 服务账号、MCP、Skill、Dify、FlowLong 和 Tool Permission 均无法批准正式状态。
5. 服务器不能绕过 Desktop 访问员工电脑；本机桥接每次按工具和路径授权。
6. 离线缓存不会冒充最新状态，恢复联网后草稿重新校验版本。
7. 删除 OpenWork/OpenCode 会话、缓存和全部外部组件派生数据，正式状态和证据仍完整。
8. 员工日常使用不出现数据库、RLS、Outbox、MCP Token 等开发术语。
9. 不存在第二个员工 Web/PWA 或桌面客户端。
