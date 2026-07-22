# F2.2 PostgreSQL 权威事件、审批和投影

## 当前结果

服务器侧已经有可运行的 `PostgreSQLCanonicalStore`。它实现 `canonical-store-port.v6` 的项目、来源、会议、候选、Proposal、关系、人工动作、事件、当前投影和稳定证据查询。F2.2 的领域语义仍位于 PostgreSQL v1-v6；F2.3 已把同一服务器 Schema 扩展到 v7，用于对象准入元数据。

这一步完成的是服务器权威存储适配器，不是鸿日数据切换。F3.1 完成前，Phase 1 SQLite 仍是鸿日正式权威；当前没有复制鸿日数据，也没有 SQLite/PostgreSQL 双写。

机器契约为 `contracts/phase2/postgresql-authority.json`，实现入口为 `src/brand_os/postgresql_store.py`，迁移入口为 `src/brand_os/postgresql_migrations.py`。

## 事务边界

每条写命令必须带 `idempotency_key` 和 `expected_version`。PostgreSQL 适配器在一笔事务中完成：

1. 按项目、操作者、命令和幂等键取得事务级锁；
2. 检查相同幂等键是否已经提交，并校验请求摘要；
3. 锁定项目版本行，拒绝过期版本；
4. 追加领域事件，写入 Proposal 或人工动作；
5. 在人工批准时同步更新当前投影；
6. 推进项目版本并保存幂等结果。

任一中间步骤失败，事件、Proposal 状态、人工动作、投影、项目版本和幂等结果全部回滚。AI、工作流和服务账号仍不能执行人工批准。

## 迁移

PostgreSQL 当前应用 v1-v7：

| 版本 | 内容 |
|:---|:---|
| v1-v2 | 项目、命令、事件、来源、候选、Proposal、人工动作、投影和读取索引 |
| v3 | 来源内容、逻辑来源、不可变版本、旧 ID、替代关系和资料缺口 |
| v4 | 会议、原话片段、解释候选和冲突快照 |
| v5 | Proposal 生命周期、重开、会议绑定和显式替代 |
| v6 | 状态与 Proposal 有效期 |
| v7 | F2.3 上传会话、对象版本、状态迁移、延迟墓碑和对账记录 |

每版迁移保存 SHA-256。已经登记的迁移内容变化时，适配器拒绝启动；单版迁移失败时整版回滚。迁移使用 PostgreSQL 会话锁串行执行，避免多个服务实例同时改 Schema。

SQLite v7 的 Task Packet 与 Agent 运行留痕仍留在 Phase 1 本地实现。SQLite 与 PostgreSQL 使用独立迁移序列；PostgreSQL v7 是 F2.3 对象元数据，不表示 Task Packet 已迁入服务器。本轮仍没有提前实现 F2.7 的审计和 Outbox。

## 领域语义复用

SQLite 和 PostgreSQL 共享 v1-v6 命令、查询、人工权限和事件重放逻辑。方言层只处理参数占位符、自增列、忽略冲突写入和行锁，不重新解释领域规则。

PostgreSQL 返回行同时支持字段名和数字位置，保证现有映射、JSON 解析和重放代码不因驱动差异改变结果。SQLite 继续使用 `BEGIN IMMEDIATE`；PostgreSQL 使用事务级幂等锁和项目行锁。

## 验证

集成测试会启动仅监听 `127.0.0.1` 的临时 PostgreSQL 17 集群，为每项测试创建独立数据库，并在模块结束后停止进程和删除数据目录。覆盖：

- v1-v7 迁移重跑和校验和篡改阻断；
- 幂等重放、同键异义和过期版本拒绝；
- Fox 人工批准权限，AI 和其他人员拒绝；
- 事件、人工动作和投影原子提交；
- 中间投影失败整笔回滚；
- 状态投影和 Proposal 生命周期从事件重建；
- 来源版本替代、会议增量、候选、关系和证据回源；
- SQLite/PostgreSQL 正式状态和事件类型对账。

本轮结果为 10 项 PostgreSQL 集成测试通过，完整回归 `189 passed, 5 subtests passed`。测试结束后没有遗留临时 PostgreSQL 进程。

## 后续边界

- F2.3 已增加 S3 兼容原件版本和准入状态机，详见[对象原件准入](object-evidence-store.md)。
- F2.4-F2.5 增加 OIDC、项目权限和 RLS；当前 `allowed_reviewers` 只是 F2.2 的领域权限基线。
- F2.6 增加 API 级并发冲突差异，不把数据库异常直接暴露给客户端。
- F2.7 增加审计、Outbox/Inbox 和后台任务。
- F2.10 完成 PostgreSQL 备份恢复和故障演练。
- F3.1 才执行 SQLite 到 PostgreSQL/S3 的一次性迁移和正式切换。
