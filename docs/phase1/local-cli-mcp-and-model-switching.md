# F1.8 本地 CLI、MCP 与模型切换

## 这一步解决什么

Codex 和 Claude 现在可以通过同一个本地 stdio MCP 读取鸿日状态、Task Packet 和证据，也可以创建等待 Fox 处理的 Proposal。CLI 使用同一个 `LocalAIService`，不会另做一套状态查询或写入逻辑。

这一步没有直接调用外部模型 API。Codex 和 Claude 仍由各自运行时管理登录和模型凭据；Brand Project OS 不读取、不转存这些凭据。这里完成的是统一上下文入口、运行版本登记和模型切换边界。

机器契约位于：

- `contracts/phase1/local-ai-access.json`
- `schemas/phase1/proposal-create-input.schema.json`

## CLI

安装当前项目后，入口为 `brand-os`。默认数据库是工作空间内的 `.fox/state/project.db`。

```bash
brand-os --workspace /Users/fox/work init --project-name 鸿日
brand-os --workspace /Users/fox/work status
brand-os --workspace /Users/fox/work task packet --packet-id TP-...
brand-os --workspace /Users/fox/work evidence get --ref 'meeting:会议ID#片段ID'
brand-os --workspace /Users/fox/work decision list
brand-os --workspace /Users/fox/work open-question list
brand-os --workspace /Users/fox/work doctor
brand-os --workspace /Users/fox/work verify
```

所有普通命令都输出 UTF-8 JSON。错误写到 stderr，并使用 `brand-os-error.v1`。读取命令在数据库不存在时直接失败，不会为了返回空结果偷偷新建数据库。

`proposal create` 从一个不超过 1 MiB 的普通 JSON 文件读取参数。它需要 `expected_version` 和 `idempotency_key`，只创建 `proposed` 记录，不提供批准、修改后批准、驳回或重开命令。

## stdio MCP

启动命令：

```bash
brand-os --workspace /Users/fox/work --project hongri mcp
```

MCP 使用官方 Python SDK 的低层 `Server`。每个工具的输入 Schema 都设置 `additionalProperties: false`，多余字段会在进入应用层前被拒绝。项目 ID 在进程启动时固定，工具参数不能临时切换到另一个项目。

当前开放 9 个工具：

| 工具 | 能做什么 | 是否改变当前正式状态 |
|:---|:---|:---|
| `project_get_state` | 读取当前状态和版本 | 否 |
| `task_get_packet` | 读取既有 Packet 全文或 L0-L4 | 否 |
| `evidence_get` | 按稳定引用回到来源版本或会议原话 | 否 |
| `decision_list` | 读取当前决定，可显式查看失效历史 | 否 |
| `open_question_list` | 读取当前开放问题 | 否 |
| `proposal_create` | 创建带证据的待确认 Proposal | 否 |
| `proposal_get` | 读取 Proposal 当前状态 | 否 |
| `system_doctor` | 检查 SQLite、协议和工具白名单 | 否 |
| `project_verify` | 检查本地结构和证据概况 | 否 |

没有开放 `proposal_approve`、`proposal_reject`、`proposal_reopen`、`task_switch_mode`、任意 SQL、原件硬删除、密钥读取或任意工作区文件读取。

工具默认 10 秒超时，最高可配 60 秒。客户端取消会传回正在执行的 MCP 调用。写请求如果在返回前超时，调用方必须使用原幂等键查询或重试，不能换键制造第二条 Proposal。

## Codex 与 Claude

以下命令生成对应客户端的本地 MCP 配置：

```bash
brand-os --workspace /Users/fox/work adapter show --runtime codex
brand-os --workspace /Users/fox/work adapter show --runtime claude
```

两份配置的客户端包裹格式不同，但底层 `command`、`args`、项目和数据库完全相同。配置中没有 `env`、API Key 或 Token。模型运行时登录失败应由 Codex 或 Claude 自己报告，不能把密钥交给 Brand Project OS 兜底保存。

模型切换不是重新生成项目背景。Fox 或受控流程先生成一个不可变 Task Packet，然后分别登记 Codex 和 Claude 运行：

```bash
brand-os --workspace /Users/fox/work run start \
  --packet-id TP-... --packet-hash ... \
  --runtime codex --runtime-version ... \
  --model-id codex --model-version ... \
  --run-id ... --idempotency-key ...
```

`model_id` 必须在 Packet 的 `model_allowlist` 中。两次运行如果使用同一 Packet，它们的事实、证据、模式、角色、状态版本和协议版本相同；运行时和模型版本可以不同。运行记录仍是派生数据，不进入正式项目事件流。

## 当前限制

- 当前 MCP 没有会议列表、会议解释、全文搜索或行动项工具；这些能力要等对应应用端口稳定后再加，不能用任意文件读取或 SQL 临时代替。
- 当前不执行 Codex/Claude 模型调用，只生成客户端接入配置并登记实际运行版本。
- 当前没有业务审批 CLI，也没有把审批伪装成 MCP Tool Permission。
- 当前没有远程 MCP、OAuth、团队账户或服务器依赖。
- `project_verify` 只做本地结构和证据检查，不代表 BrandBench 或 Fox 业务验收已经通过。

## 验证结果

2026-07-22 完成以下检查：

- 全量测试：148 项通过，另有 5 组旧备份 Schema 子测试通过。
- `ruff`、Python 编译、JSON 解析、`uv lock --check` 和 `git diff --check` 通过。
- 官方 MCP 客户端与 stdio 子进程完成真实往返：可列出 9 个工具、读取同一 Packet、创建待确认 Proposal，并拒绝未声明参数。
- 鸿日 v3 数据库副本迁移到 SQLite v7 后通过 `quick_check`。项目版本保持 2，事件 2 条；正式状态、Proposal、运行任务和 Agent 运行仍为 0，没有写入测试业务事实。
- Codex 与 Claude 配置的底层 `command` 和 `args` 一致，均不包含 `env`、API Key 或 Token。
