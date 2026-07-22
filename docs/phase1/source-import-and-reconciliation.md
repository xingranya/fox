# F1.3 来源导入与对账

## 结果

鸿喜达当前 9 份来源已经导入本地 SQLite。系统只登记来源角色、相对路径、SHA-256 和 Manifest 能证明的字段，不读取正文，也不把决策日志里的内容直接写成正式决定。

同一导入请求连续执行两次，第二次没有增加命令、事件、批次、来源、版本、缺口或状态记录。

| 对账项 | 结果 |
|:---|---:|
| Manifest 记录 | 9 |
| 逻辑来源 | 9 |
| 去重后的内容对象 | 9 |
| 来源版本 | 9 |
| 当前版本 | 9 |
| 缺口观察 | 5 |
| 第二次执行新增行 | 0 |
| 正式业务状态 | 0 |

完整路径、全部哈希、数据库和对账报告留在 `.work/phase1/`，不提交 Git。仓库只保留汇总结果和可复跑脚本。

## 导入规则

`source-import.v1` 把来源拆成四件事：

- 逻辑来源：同一份资料跨版本不换 ID。
- 内容对象：按 SHA-256 去重；相同内容只存一份内容身份。
- 来源版本：哈希变化时新增版本，旧版本不覆盖，并建立 `supersedes` 关系。
- 缺口：只记录缺什么、影响范围和依据，不创建假的来源记录。

Manifest 原始 SHA-256 和导入指纹分开保存。导入指纹还包含标准化记录与补充缺口，所以同一文件换了一组缺口时会形成新的对账批次；输入完全相同时直接返回原批次。

旧 ID、废弃保号和路径别名保存在 `source_aliases`。同一旧 ID 指向两个逻辑来源时整批回滚。V5 当前没有可核验 Manifest，因此本轮只登记 `GAP-V5-UNVERIFIED`，没有编造数量、旧 ID 或上下级关系。

## 两种现有 Manifest

本地样本 Manifest 有来源 ID、文件大小和保密级别。远程 Manifest 只有角色、相对路径和哈希。导入器兼容两种格式；远程清单缺少的大小、媒体类型和保密级别保持为空，不用 `0` 或默认等级代替未知值。

远程清单没有逻辑来源 ID 时，系统根据相对路径生成稳定 ID。后续同一路径内容变化会进入同一逻辑来源的新版本。

## SQLite v3

迁移 v3 新增以下表，不修改已经发布的 v1、v2：

| 表 | 内容 |
|:---|:---|
| `source_import_batches` | Manifest SHA、导入指纹、事件和本批差异 |
| `source_contents` | 按哈希去重的内容身份 |
| `logical_sources` | 跨版本稳定的来源身份 |
| `source_versions` | 不可变内容版本和当前版本标记 |
| `source_aliases` | 旧 ID、废弃保号和路径别名 |
| `source_version_relations` | 版本替代关系 |
| `source_gaps` | 每批次看到的缺口及证据 |

升级已有数据库时，原 `sources` 记录会复制进版本表，原事件和来源 ID 保持不变。在线备份清单升为 `sqlite-backup.v2`，增加来源批次、逻辑来源、版本、缺口数量和来源摘要；旧的 v1 备份仍可恢复。

## 验证

自动化测试覆盖重复导入、同清单换幂等键、坏行、路径越界、哈希变化、内容去重、旧 ID、废弃保号、别名冲突、缺失前序版本、整批回滚、v2 升级和备份恢复。

本地真实对账入口：

```bash
uv run python scripts/reconcile_source_manifest.py \
  --database .work/phase1/hongri.db \
  --manifest .work/phase0/remote-hongxida/source-manifest.local.json \
  --gap-file .work/phase1/hongri-known-gaps.local.json \
  --report .work/phase1/f1.3-source-reconciliation.local.json
```

本次全量检查为 79 项测试通过，`ruff` 和 Python 编译检查通过，SQLite `quick_check` 通过。
