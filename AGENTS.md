# Brand Project OS 项目协作规则

## 适用范围

本规则适用于整个仓库。开始任何实现前，先读取 `docs/progress/MASTER.md`、`docs/plan/task-breakdown.md` 和本文件。

## 权威来源

- 团队控制的 S3 兼容对象存储及其 SHA-256：只证明原始内容和文件版本。
- PostgreSQL 权威事件日志及人工审批记录：只证明当前正式状态如何形成。
- 当前状态投影：可从权威事件重建，不是独立真相源。
- Zvec、FTS、Open Notebook、Nubase Memory、FlowLong、Dify、缓存和模型摘要：均为派生数据或流程协调数据，不得覆盖正式状态。

## 核心规则

- AI 只能提出变更，不得批准事实、决定、约束、外部承诺、负责人、截止时间或提交版本。
- `VIEW`、`HYPOTHESIS`、`OPTION`、`OPEN` 不得自动升级为 `DECISION` 或 `CONSTRAINT`。
- 生产环境以团队服务器为唯一权威运行面；标准 PostgreSQL 是唯一正式可写数据库，SQLite 只用于开发测试或只读快照，不得形成双主。
- Web/PWA、CLI、MCP、Skills、自动化和桌面代理必须调用同一版本化应用 API，不得直接写 PostgreSQL 或对象存储正式区。
- 领域核心不得直接依赖 Zvec、Nubase、Open Notebook、FlowLong、Dify 或具体模型 SDK；必须通过版本化端口和 Schema 调用。
- 所有外部组件必须可禁用、可替换；检索索引必须可从权威数据完整重建。
- Dify 多租户或白标部署、FlowLong 源码集成必须先取得明确许可证结论；未通过许可门时只能做不分发的隔离 POC。
- 默认团队控制、自托管优先。数据外发前必须检查保密级别、提供商、任务范围和人工授权；自托管 AI 工作流中的外部模型、插件和 HTTP 节点仍视为外发。
- 正式写请求必须有幂等键和预期版本；事件、最小正式投影与 Outbox 在同一 PostgreSQL 事务提交，版本冲突不得静默覆盖。
- 搜索、Dify、Open Notebook、FlowLong、通知和模型任务通过 Outbox 异步派生；延迟必须可见，故障不得改变正式状态。
- 离线客户端只允许查看获准缓存和保存草稿或 Proposal，不得批准、删除原始资料、修改成员或确认外部承诺。
- 所有注释、文档和函数级说明使用简体中文；对外界面不得出现内部开发占位文案。
- 未经用户明确要求，不启动 Web、数据库、Docker 或其他常驻服务。
- 新增行为、Schema、迁移、解析、权限、检索、缓存或持久化逻辑必须增加自动化测试。

## 规划与进度

- 当前采用 `LOCAL_ONLY` SPEC 追踪模式；这只描述任务追踪方式，不表示产品运行在本地。
- 每完成一个任务，先记录执行遥测，再更新对应阶段文件和 `docs/progress/MASTER.md`。
- 若任务产生稳定规则或架构不变量，更新本文件和相应 ADR；未经用户明确批准，不创建仓库内 Memory 文件。

## 规划中的验证入口

- `uv run pytest`
- `pnpm --dir apps/web test`
- `pnpm --dir apps/web exec playwright test`
- `uv run brand-os doctor`
- `uv run brand-os verify hongri`
