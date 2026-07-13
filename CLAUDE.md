# Claude Code 项目说明

首先读取根目录 `AGENTS.md`，其内容是跨 Codex、Claude 和其他代理共用的权威协作规则。

- 查询项目状态、证据和任务时，优先使用服务器项目 MCP 的确定性读取工具，并保留返回的项目版本和证据引用。
- 只能通过 `state.propose_change` 提交变更候选，不得调用 PostgreSQL、对象存储、Open Notebook、Nubase Memory、FlowLong 或 Dify 绕过人工审批。
- MCP、CLI、Skill 或 Dify 返回的派生结果不得自行升级为正式状态；版本冲突时停止写入并请求人工处理。
- 不重复维护共享规则；跨代理规则统一写入 `AGENTS.md`。
- 当前没有获准的仓库内 Memory 文件，不得自行创建。
