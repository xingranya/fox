# 治理面解析

## 指令面

| 指令面 | 状态 | 作用 | 明确边界 |
|:---|:---|:---|:---|
| `AGENTS.md` | 已解析 | 跨开发代理共享工程规则 | 不定义运行时品牌角色，不保存鸿日业务事实 |
| `CLAUDE.md` | 已解析 | Claude Code 专用适配 | 只补充 Claude 的读取和提案约束，不复制共享工程规则 |
| `docs/plan/runtime-brand-agent-and-meeting-protocol.md` | 当前规范 | 运行时品牌角色、会议解释与工作模式 | 不规定代码架构、部署和开发命令 |
| 鸿日项目级 Agent 规则 | Phase 0 待建立 | 当前目标、已批准方向、开放问题与本轮非目标 | 必须从权威状态生成或版本化维护，不得与开发规则混放 |
| `.cursor/rules/` | 不存在 | Cursor 规则 | 本轮不创建 |
| `.windsurf/` | 不存在 | Windsurf 规则 | 本轮不创建 |
| `.clinerules*` | 不存在 | Cline 规则 | 本轮不创建 |
| `.codex/` | 不存在 | Codex 项目配置 | 仅在 Phase 1 接口确定后创建无密钥配置示例 |

## Memory 面

| 字段 | 值 |
|:---|:---|
| 原生项目 Memory 可用 | 未确认 |
| 已解析 Memory 面 | 不可用，不写入 |
| 仓库回退 Memory 获批 | 否 |
| 说明 | 业务事实进入权威事件与状态层；SPEC 进度进入 `docs/progress/MASTER.md`；未经用户明确授权不创建仓库内 Memory 文件 |

聊天记忆、模型摘要、OpenWork/OpenCode 会话和第三方 Memory 只能辅助运行，不能替代 Task Packet、权威状态或证据。

## 当前真相面

| 内容 | 当前权威面 |
|:---|:---|
| 原始内容与文件版本 | 鸿日本地原始资料、不可变副本、SHA-256 和来源清单 |
| 正式事实、决定、约束和状态变化 | 经 Fox 人工确认的权威事件日志 |
| 当前状态 | 从权威事件重建的本地状态投影 |
| 开放观点、假设、选项与倾向 | 探索层记录，不得自动进入正式状态 |
| 新会议解释结果 | 增量 Proposal、冲突与待确认队列 |
| 项目执行进度 | `docs/progress/MASTER.md` 与阶段文件 |
| 技术决策 | `docs/adr/`；ADR-0003 约束当前路线，ADR-0001/0002 按其状态复审 |
| 检索索引、模型摘要、第三方工作流 | 可重建派生数据，不是权威面 |

当前可以用 SQLite 或其他轻量结构化存储实现以上语义，但“SQLite 文件”本身不是独立真相定义。领域事件、审批、版本和证据关系必须通过版本化 Schema 表达，以便未来迁移而不改变业务含义。

## 远期真相面候选

团队服务器、PostgreSQL、S3/MinIO、OIDC、RLS、Outbox 和灾备属于 `future-candidate`、`not-approved-for-current-mvp`、`review-after-hongri-pilot`。只有 Phase 2 证明个人试点有效并通过 Phase 3 决策门后，才允许替换当前部署适配器；替换不得创造第二真相源或改变人工批准边界。
