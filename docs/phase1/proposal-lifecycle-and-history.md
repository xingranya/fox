# F1.5 Proposal 生命周期与历史回放

## 结果

会议解释项现在可以进入 Proposal 流程，但不会直接改项目状态。Fox 可以逐项批准、修改后批准或驳回；被驳回的 Proposal 只有补到新证据后才能重开。

已经批准的内容不能原地覆盖。方向变了，需要新建一个 `supersede` Proposal，写清它替代哪一条、旧内容是什么、新内容是什么。Fox 批准后，旧内容退出当前状态，事件、旧值和替代关系都保留。

对应契约是：

- `contracts/phase1/proposal-lifecycle.json`
- `contracts/phase1/canonical-store.json`，版本 `canonical-store-port.v4`
- SQLite Schema v5

## 谁能做什么

| 动作 | AI/工作流 | Fox | 是否改变当前状态 |
|:---|:---:|:---:|:---:|
| 创建 Proposal | 可以 | 可以 | 否 |
| 批准或修改后批准 | 不可以 | 可以 | 是 |
| 驳回 | 不可以 | 可以 | 否 |
| 携新证据重开 | 不可以 | 可以 | 否 |
| 批准替代 Proposal | 不可以 | 可以 | 是 |

OpenWork、OpenCode Tool Permission、MCP 或模型身份都不能代替 Fox 的业务确认。

## 生命周期

| 当前状态 | Fox 动作 | 下一状态 | 附加条件 |
|:---|:---|:---|:---|
| `proposed` | `approve` | `approved` | 保留理由、证据、版本和事件 |
| `proposed` | `modify_and_approve` | `approved` | 保存 Fox 修改后的内容 |
| `proposed` | `reject` | `rejected` | 不改当前状态 |
| `rejected` | `reopen` | `proposed` | 至少补一条此前没有的新证据 |
| `approved` | 批准后继 `supersede` Proposal | `superseded` | 后继 Proposal 必须保存旧值并使用新的状态 ID |

同一个幂等键重试会返回第一次的结果。幂等键相同但请求内容不同、调用方版本过期、重复替代或用普通 Proposal 改写现有状态 ID，都会报冲突，不会追加半条事件。

## 会议解释如何进入 Proposal

Proposal 可以引用 `source_meeting_item_id`。一旦引用，系统会核对两件事：

1. Proposal 分类与会议解释项的保守分类一致；
2. Proposal 证据包含该解释项引用的全部会议原话片段。

例如，会议里“月底前最好看到一版”被识别为 `TARGET_DATE` 和 `TENTATIVE_DATE`。后续 Proposal 仍是时间候选，不能借转换流程把它写成 `DEADLINE`。

## 替代不是覆盖

批准替代 Proposal 时，一个事务内完成以下动作：

1. 写入后继 Proposal 的批准事件；
2. 从当前投影移除旧状态；
3. 将前序 Proposal 标记为 `superseded`；
4. 保存前后状态快照和替代关系；
5. 写入新状态并推进项目版本。

中间任何一步失败，整笔事务回滚。旧状态不会先消失，新状态也不会只写一半。

## 重放与备份

`rebuild_state_projection(project_id)` 根据 Fox 的批准事件重建当前状态。替代事件会先移除旧状态，再写入后继状态。

`rebuild_proposal_lifecycle(project_id)` 根据 Proposal 事件重建生命周期、重开记录和替代链。重放时会再次检查操作者和状态转换；事件顺序不合法时直接停止。

备份清单现为 `sqlite-backup.v4`。除原有事件、状态、来源和会议对账外，还会核对：

- Proposal 生命周期数量；
- 重开与替代动作数量；
- 会议解释项到 Proposal 的绑定数量；
- 替代链数量；
- Proposal、证据、人工动作、生命周期和替代关系的统一摘要。

旧的 v1、v2、v3 清单仍可恢复；真实 SQLite Schema v2、v3、v4 也有恢复测试。

## 验证

本任务完成时，114 项测试全部通过，另覆盖 3 组真实旧 Schema 恢复。测试包含未授权重开、新证据要求、幂等重试、过期版本、同 ID 静默覆盖、重复替代、替代事务回滚、会议原话绑定，以及状态和生命周期事件重放。

验证命令：

```bash
rtk uv run pytest
rtk uvx ruff check src tests scripts
rtk uv run python -m compileall -q src tests scripts
rtk git diff --check
```
