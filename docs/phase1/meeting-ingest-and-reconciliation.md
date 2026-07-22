# F1.4 会议增量摄取与对账

## 结果

会议解释现在有了独立入口 `meeting-ingest.v1`。它接收已经分段和初步分类的会议内容，核对来源版本与基础状态版本，再写入会议、原话片段、工作层候选和冲突候选。

这个入口不会批准任何内容。AI、工作流和本地系统都可以提交解释结果，但 `state_items` 不会因此变化。正式决定、约束和行动仍只能由 Fox 在 Proposal 评审流程中确认。

## 输入内容

每次摄取必须带这些信息：

- 已登记的逻辑来源 ID、来源版本 ID 和 SHA-256；
- 会议时间、参与者、全场模式和模式置信度；
- 每段原话的定位、说话人、时间位置、上下文和转写置信度；
- 每条解释的建议类型、范围、依据、置信度和原话片段；
- 与当前人工确认状态有关的冲突引用；
- 解释时使用的 `base_state_version`。

原话字段只按数据处理。即使内容写着“忽略规则”“直接批准”或“FINAL”，也不会变成命令，更不会获得业务批准权限。

## 保守分类

接口只接受工作层类型，不接受 `DECISION`、`CONSTRAINT`、`ACTION` 或 `DEADLINE`。

`DECISION_CANDIDATE` 需要决定人、明确动词、适用范围、状态差异、会议时间、发言人、原话时间位置和已核验来源。少一项就降为 `OPEN`，原始建议类型仍保留，方便检查模型为什么判断过头。

`TARGET_DATE` 始终还是时间候选。未提供时间性质时记为 `UNKNOWN`；“最好”“争取”等表达可记为 `TENTATIVE_DATE`，不会自动变成外部硬截止。

会议包含多种片段模式时，全场模式必须是 `MIXED`。模式和置信度只帮助解释内容，不代表正式程度。

## 去重和冲突

系统分别计算会议内容摘要和整批摄取摘要：

- 完全相同的摄取摘要直接返回原批次，不新增事件或记录；
- 同一来源、定位和原话复用现有片段；
- 同一原话、分类、范围和语义结果复用现有候选；
- 同一候选与同一正式状态之间的冲突只保留一条。

冲突必须指向 `state_items` 中实际存在的人工确认状态。系统保存当时的状态内容、事件和版本作为快照，但不会覆盖它。引用不存在或晚于基础版本的状态时，整批事务回滚。

## SQLite v4

迁移 v4 新增四组表：

| 表 | 保存内容 |
|:---|:---|
| `meetings` / `meeting_ingest_batches` | 会议身份、来源绑定、摄取摘要、基础版本和本批计数 |
| `meeting_segments` / `meeting_batch_segments` | 原话、发言人、时间位置、上下文、模式及批次关系 |
| `meeting_interpretation_items` / `meeting_item_evidence` / `meeting_batch_items` | 建议类型、受保护后的分类、理由、置信度和原话关系 |
| `meeting_conflict_candidates` / `meeting_conflict_evidence` / `meeting_batch_conflicts` | 与人工确认状态的冲突快照及证据 |

备份清单同步升级为 `sqlite-backup.v3`，增加会议批次、会议、片段、解释项、冲突数量和会议工作层摘要。旧 v1、v2 备份仍可恢复。

## 本地对账

本次使用 `.work/phase0/` 中一份非鸿日智能纪要做隔离回归。它只验证功能，报告明确标记 `fixture_only=true` 和 `hongri_business_fact=false`，不能进入鸿日正式状态。

结果如下：

| 对账项 | 结果 |
|:---|---:|
| 会议 | 1 |
| 原话片段 | 3 |
| 工作层解释 | 3 |
| 冲突候选 | 0 |
| 第二次摄取新增行 | 0 |
| 正式业务状态 | 0 |

智能纪要把“南京区域不再投放”写成了决定语气，但资料缺决定人、可靠发言人和人工核验原话。系统保留 `suggested_type=DECISION_CANDIDATE`，实际分类为 `OPEN`。

本地复跑入口：

```bash
uv run python scripts/reconcile_meeting_ingest.py \
  --database .work/phase1/f1.4-fixture.db \
  --source-manifest .work/phase0/manifest.local.json \
  --meeting .work/phase1/f1.4-meeting-fixture.json \
  --report .work/phase1/f1.4-meeting-reconciliation.local.json
```

F1.4 完成时共 101 项测试通过，另有 2 组旧 Schema 子测试。覆盖缺说话人、缺时间位置、未核验纪要、暂定日期、非法正式类型、提示注入、重复会议、重复候选、来源不匹配、状态冲突、旧基础版本、事务回滚和会议备份恢复。
