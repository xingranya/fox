# F3.1：SQLite 到 PostgreSQL/S3 的一次性迁移

**状态**：已完成，2026-07-23。真实鸿日资料没有迁移；本轮只使用临时 SQLite、PostgreSQL 和 Moto S3。

## 目标

F3.1 只把 SQLite v1-v6 的正式领域数据切到服务器权威层：项目、事件、来源、会议、Proposal、人工动作、当前投影及其关系。SQLite v7-v8 的运行态和切换守卫不进入 PostgreSQL；`runtime_tasks`、Task Packet、Agent 运行等仍属于本机运行边界。

切换成功后：

- PostgreSQL 是团队正式状态唯一可写源；
- S3 兼容对象存储保存开启版本控制的 ACTIVE 原件版本；
- SQLite 文件和应用层均为只读，不形成长期双写或双主；
- 每个来源版本都能回到同一 SHA-256 和明确的 S3 `VersionId`；
- AI、MCP、Skill、工作流和服务账号不能激活切换或批准正式状态。

## 机器契约

- [数据切换契约](../../contracts/phase3/data-cutover.json)
- [数据切换清单 Schema](../../schemas/phase3/data-cutover.schema.json)
- 实现：[data_cutover.py](../../src/brand_os/data_cutover.py)
- 测试：[test_data_cutover.py](../../tests/phase3/test_data_cutover.py)

SQLite 的 `authority_cutovers` 表记录 `PREPARING`、`ACTIVE`、`ABORTED` 三种状态。PostgreSQL v12 增加 `data_cutover_runs` 和 `data_cutover_source_evidence`，保存清单哈希、结果、失败原因和来源版本到对象版本的映射。

## 执行顺序

1. 先应用 PostgreSQL 迁移并检查目标库。除默认 `outbox` 消费者外，目标不能已有业务、身份、对象元数据或未完成切换记录。
2. 在 SQLite 上取得写锁，插入 `PREPARING` 守卫。随后应用层、运行态写入和普通 SQLite 连接都被阻断；切换失败时才恢复写入。
3. 导出 31 张 v1-v6 正式表到只读 JSONL 文件。每张表按规范 JSON 排序，记录列、行数和文件 SHA-256；再计算来源快照摘要和不可变 Manifest SHA-256。
4. 逐个校验本地内容寻址证据，使用现有 `EvidenceAdmissionService` 经过隔离、哈希、大小、MIME 和安全准入，写入版本化 S3，并保存明确 `VersionId`。
5. 在同一目标导入边界内插入正式表，保留项目 ID、事件 ID、事件位置、Proposal ID、人工动作和投影；历史事件不会重新投递为新的 Outbox 业务消息。
6. 对 PostgreSQL 表、项目版本、人工动作、当前投影、来源版本和 S3 ACTIVE 对象做双向对账；同时重新读取冻结来源，确认正式数据没有变化。
7. 先把 PostgreSQL 切换记录置为 `ACTIVE`，再把 SQLite 守卫置为 `ACTIVE` 并将数据库文件设为只读。任何中间异常都走回滚，不以最后写入覆盖冲突。

## 失败与重试

清单文件、表文件或来源哈希不一致时，切换立即失败。目标尚未激活前，服务会：

- 删除本次导入的目标行和对象元数据；
- 仅删除切换开始后新增的临时对象和内容对象版本，保留切换前版本；
- 写入 `ROLLED_BACK` 记录和失败原因；
- 把 SQLite 守卫改为 `ABORTED` 并恢复本地写权限。

PostgreSQL 已经 `ACTIVE` 后禁止使用普通回滚；应按 F2.10 的隔离恢复流程处理。若进程在 PostgreSQL 激活后、SQLite 激活前退出，SQLite 仍保持 `PREPARING` 只读，重新执行会完成最后激活，不会形成双主。

## 验收证据

`tests/phase3/test_data_cutover.py` 覆盖：

- 完整表导出、导入、ID/事件/审批/投影和来源哈希对账；
- 明确 S3 `VersionId`、原件 SHA-256、重跑幂等；
- 目标非空拒绝和 AI 操作人拒绝；
- Manifest 篡改、来源冻结后变化、对象上传完成后连接中断的失败回滚；
- 成功后 SQLite 写入失败、读取正常，PostgreSQL 成为唯一可写源。

F3.1 专项 7 项和 `uv run pytest` 完整回归均通过。测试没有启动常驻 Web、数据库或桌面应用，也没有写入真实鸿日资料。99.5% 可用性、PostgreSQL RPO 不高于 5 分钟、核心服务 RTO 不高于 60 分钟仍属于 Fox 已批准的内部目标，必须在 Phase 4 真实部署中测量。
