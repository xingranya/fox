# 治理面解析

> 当前解析日期：2026-07-24  
> 追踪模式：`LOCAL_ONLY`  
> 活动任务真源：`docs/plan/task-breakdown.md`（5 阶段 56 项）

## 指令面

| 指令面 | 状态 | 作用 | 明确边界 |
|:---|:---|:---|:---|
| `AGENTS.md` | 当前共享指令 | 架构、数据、测试、中文界面和协作规则 | 不保存品牌项目事实，不替代运行时品牌协议 |
| `CLAUDE.md` | Claude Code 专用适配 | 补充 Claude 的仓库读取与提案边界 | 不复制或覆盖共享指令 |
| `docs/plan/runtime-brand-agent-and-meeting-protocol.md` | 当前运行时规范 | 品牌角色、会议解释、信息类型和工作模式 | 不规定部署、代码结构或开发命令 |
| `docs/adr/` | 技术决定 | 记录架构选择、原因和替代关系 | 当前以 ADR-0008/0007/0006/0005 为优先 |
| `docs/progress/MASTER.md` | 进度入口 | 完成度、当前任务、漂移和遥测 | 不保存业务正式状态 |
| `docs/plan/task-breakdown.md` | 任务真源 | 当前 56 项、依赖、测试和阶段门 | 历史任务只能在 rescope 表中出现 |
| `.cursor/rules/`、`.windsurf/`、`.clinerules*`、项目 `.codex/` | 当前不存在 | 平台规则 | 未经用户要求不创建 |

## Memory 面

| 字段 | 当前值 |
|:---|:---|
| 原生项目 Memory | 未作为本项目权威面使用 |
| 仓库回退 Memory | 未获批准，不创建 |
| 跨会话进度 | `docs/progress/MASTER.md` 与阶段进度文件 |
| 业务事实 | PostgreSQL/S3 权威层；Phase 1 SQLite 只读归档 |

聊天记忆、模型摘要、OpenWork/Den/OpenCode Session、Nubase Memory 和 Skill 只辅助运行，不能替代 Task Packet、业务权威或证据。

## 当前真相面

| 内容 | 权威位置 |
|:---|:---|
| Den 账号、组织、团队、远程工作区和 AI 能力授权 | Den MySQL |
| 品牌项目员工绑定、项目角色和映射审计 | Brand OS PostgreSQL |
| 原始内容与文件版本 | S3 VersionId、SHA-256、来源元数据；Phase 1 本地证据只读归档 |
| 正式事实、决定、约束和状态变化 | 具名员工批准的 PostgreSQL 事件与人工动作 |
| 当前状态 | 从权威事件生成、可重建的 PostgreSQL 投影 |
| 观点、假设、选项、倾向和新会议解释 | 工作层 Artifact/Proposal，不自动正式化 |
| 技术决策 | `docs/adr/`，当前 ADR-0008/0007/0006/0005 优先 |
| SPEC 活动进度 | `docs/progress/MASTER.md` + 阶段文件 |
| 检索、摘要、转写、Notebook、Memory、工作流 | 可重建派生数据 |

Den 与 Brand OS 各自在自己的职责内权威，但不能成为双主：Den 不定义业务正式状态，Brand OS 不定义 Den 密码、组织或模型授权。

## 文档状态规则

- `docs/analysis/`：说明当前问题、技术事实、风险和组件边界；
- `docs/plan/`：当前活动架构、接口、任务、依赖和验收；
- `docs/progress/`：执行状态和遥测；
- `docs/phase*/`：已完成任务的实施证据，历史编号不覆盖活动计划；
- `docs/adr/`：稳定决策及其替代关系。

历史文档中的“不部署 Den”“自建团队连接”“当前 49 项”只作追溯。若文档同时描述历史与当前，必须显式标明日期和已被 ADR-0007/ADR-0008/56 项方案替代。

## 更新规则

1. 完成任务前先记录执行遥测，再更新阶段文件和 `MASTER.md`。
2. 新稳定架构规则同时更新对应 ADR 和 `AGENTS.md`；未经用户批准不创建仓库 Memory。
3. 代码、Schema 或权限行为变化必须更新相关 SPEC 和自动化测试。
4. OpenWork/Den 上游事实固定 tag/SHA、许可证和现场证据，不能引用漂移的 `dev` 作为当前基线。
5. 业务事实不写入开发指令、Skill 或模型记忆；它们通过权威状态和 Task Packet 提供。
6. GitHub Wiki 只在大阶段完成并通过阶段门后，从对应提交统一同步；普通任务和中间 SPEC 校准只更新仓库文档。
