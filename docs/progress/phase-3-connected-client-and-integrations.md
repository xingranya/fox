# Phase 3：客户端联网闭环、MCP、Skills 与工作流

**目标**：把唯一员工客户端切到服务器权威层，并逐项接入 Agent、Skills、Dify 和四个开源组件。
**状态**：进行中，1/13，当前任务 F3.2
**任务数**：13
**自适应阈值**：标注 3 / 重计划 6 / 重定范围 8

## 任务

- [x] **F3.1：实现 SQLite 到 PostgreSQL/S3 的一次性迁移和切换**
  - P0 / XL / Lane A；依赖 F2.10；S.U.P.E.R：U、P、E、R。
  - 验收：已完成。SQLite v1-v6 正式表导出、PostgreSQL v12 导入、ID/事件/审批/投影/来源和 SHA-256 对账；S3 原件经过准入并保存明确 VersionId；清单不可变，目标非空、篡改、来源变化和对象中断均可回滚；切换后 SQLite 文件和应用层只读，无长期双写或双主。专项 7 项及完整回归通过，未迁移真实鸿日资料。

- [ ] **F3.2：在 OpenWork 接入 OIDC、项目选择和服务器连接**
  - P0 / L / Lane B；依赖 F3.1；S.U.P.E.R：S、P、E。
  - 验收：一个客户端完成登录与项目进入；撤权、证书错误和离线状态可理解。

- [ ] **F3.3：接入服务器当前状态、证据和变化视图**
  - P0 / L / Lane B；依赖 F3.2；S.U.P.E.R：S、P、R。
  - 验收：业务状态来自 API，不从 Session 推导；缓存标明版本和水位。

- [ ] **F3.4：接入 Proposal 差异、人工确认和并发冲突处理**
  - P0 / L / Lane B；依赖 F3.3、F2.6；S.U.P.E.R：S、U、P。
  - 验收：批准、修改、驳回和 409 差异完整；业务确认与 Tool Permission 分路。

- [ ] **F3.5：接入本机文件/终端/桌面桥接和远程权限边界**
  - P0 / L / Lane C；依赖 F3.2；S.U.P.E.R：S、P、E。
  - 验收：每次按路径和工具授权；服务器不能绕过客户端读取员工电脑。

- [ ] **F3.6：实现服务器 MCP Gateway 和 Codex/Claude 接入**
  - P0 / L / Lane D；依赖 F2.8、F2.5；S.U.P.E.R：S、P、E、R。
  - 验收：项目 Scope、工具白名单和撤权通过；没有批准、任意 SQL 或任意本机文件工具。

- [ ] **F3.7：实现 Skills 目录、版本、签名和客户端分发**
  - P1 / L / Lane D；依赖 F3.6；S.U.P.E.R：S、P、R。
  - 验收：版本锁、签名、兼容和回滚通过；Skill 不保存实时事实。

- [ ] **F3.8：实现 Dify `AIWorkflowPort` 适配**
  - P1 / L / Lane E；依赖 F3.6；S.U.P.E.R：S、P、E、R。
  - 验收：外发、服务身份、超时、回调幂等和 NoOp 通过；Dify 只能读取或创建 Proposal。

- [ ] **F3.9：评估并实现 Zvec `SearchIndexPort` 适配**
  - P2 / M / Lane F；依赖 F2.3、F2.7；S.U.P.E.R：S、P、R。
  - 验收：索引可重建；相对 PostgreSQL FTS 无可测收益时拒绝采用。

- [ ] **F3.10：评估并实现 Open Notebook `ContentProcessingPort` 适配**
  - P2 / M / Lane F；依赖 F2.3；S.U.P.E.R：S、P、R。
  - 验收：解析保留来源定位，失败隔离；只产派生内容，不形成正式事实。

- [ ] **F3.11：评估并实现 Nubase `MemoryPort` 适配**
  - P2 / M / Lane F；依赖 F3.6；S.U.P.E.R：S、P、R。
  - 验收：记忆污染、删除、项目隔离和 NoOp 通过；没有明确增益时拒绝采用。

- [ ] **F3.12：评估并实现 FlowLong `ApprovalWorkflowPort` 适配**
  - P2 / M / Lane F；依赖 F2.5、F3.6；S.U.P.E.R：S、P、R。
  - 验收：先过许可门；只路由人的待办，最终批准仍由 Brand Project OS 完成。

- [ ] **F3.13：完成单安装包、服务器、MCP/Skills 和工作流端到端验收**
  - P0 / XL / Gate；依赖 F3.1-F3.12；S.U.P.E.R：全部。
  - 验收：核心旅程不依赖可选组件；每个外部组件有采用/试用/拒绝结论和 NoOp 回退。

## 完成检查

- [x] F3.1 先记录遥测并单独提交。
- [x] F3.1 完成后 SQLite 已退出正式写入，没有双主。
- [ ] 员工只使用一个 OpenWork 定制客户端。
- [ ] MCP/Skills/Dify 和四组件不能绕过人工确认或本机权限。
- [ ] 外部组件许可、外发和退出证据齐全。
- [ ] F3.13 通过后才向试点成员分发。

## 备注

本文件取代旧的 `phase-3-team-server-decision.md`。Fox 已批准服务器路线，因此不再重复做“是否服务器化”的候选决策。

Phase 2 已于 2026-07-23 关闭。F3.1 已用临时 SQLite、PostgreSQL 和 Moto S3 验证迁移、对账、写入冻结与回滚；真实 F1.10 基线只使用副本做只读核验，未写入真实资料。下一项是 F3.2 OpenWork 的 OIDC、项目选择和服务器连接。
