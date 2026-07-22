# ADR-0002：条件性采用 OpenWork 作为本地客户端基础

- 状态：条件性候选；OW-L0 有条件通过
- 当前结论：从固定稳定版继续本地切片，先完成默认离线补丁；尚未批准为必选主客户端
- 日期：2026-07-13
- 最近复审：2026-07-22
- 影响范围：鸿日本地 MVP 界面、Agent 运行、本地文件访问和后续客户端演进
- 关联：[ADR-0003](0003-local-first-hongri-validation.md)、[OpenWork 深度集成计划](../plan/openwork-deep-integration.md)

## 背景

OpenWork 社区核心提供 Electron/React 桌面壳、工作区、会话流、流式任务、工具权限、Skills/MCP、模型连接和本地文件能力，可能缩短本地客户端开发时间。当前固定基线为 [`v0.17.36@ddf3e482`](https://github.com/different-ai/openwork/tree/ddf3e482d2fdf3a374d0fbf4e23e01467a3014fc)（2026-07-20）；持续变化的 `dev` 不作为 fork 基线。

但 OpenWork 的信息架构和运行层深度依赖 OpenCode，并带有 OpenWork Server、Orchestrator、远程 Workspace 和团队化演进路径。当前批准路线只是 Fox 在 `/Users/fox/work` 中验证鸿日，本地单用户、无需登录、无需常驻服务端。若为了采用 OpenWork 先建设团队服务器、OIDC、远程 Worker、签名分发和三平台更新，就会再次让技术方案反客为主。

OpenWork 根许可证约定 `ee/**` 之外采用 MIT，`ee/**` 采用 FSL-1.1-MIT；许可证不授予 OpenWork 或 Different AI 商标权。

2026-07-22 的 OW-L0 核验表明：社区切片能完成桌面、sidecar、Server/Orchestrator 和 helper 构建，不要求 `ee/**` 或 Den 编译依赖；桌面测试 80 项中 79 通过、1 跳过。上游默认仍连接遥测、Den/Cloud、模型目录和 GitHub 更新，并保留 OpenWork AppID、协议以及 `NSAllowsArbitraryLoads=true`。因此 OW-L0 只按“有条件通过”处理，详见 [OW-L0 技术选型记录](../phase1/openwork-ow-l0-evaluation.md)。

## 决策

1. OpenWork 仅作为本地客户端基础的条件性候选，不是鸿日 MVP 的产品架构、权威状态源或交付前提。
2. 先执行一个隔离纵向切片：本地打开鸿日项目、读取分层 Task Packet、启动一个 Agent、生成带证据的增量 Proposal，并由 Fox 在本地界面批准或驳回。切片通过后才能决定是否继续改造 OpenWork。
3. 本地 MVP 不要求部署 OpenWork Server、远程 Orchestrator、OIDC、团队账户、托管 Worker、轻量 Web 后备或私有更新服务。能以进程内适配、本地回环 API 或受控子进程完成的能力，不得为了“架构完整”改为服务端前置。
4. OpenWork 的本地 SQLite、JSON、会话、工作区、Tool Permission 和 OpenCode 状态均不承担鸿日正式项目状态。正式状态由 ADR-0003 定义的本地领域核心、SQLite 事件/投影和 Fox 人工批准记录形成。
5. 领域核心只认识版本化 `AgentRuntimePort`、Task Packet、Artifact、Tool Permission 和 Proposal Schema，不导入 `@opencode-ai/sdk` 类型。OpenCode 可作为首个适配器，但不能成为永久领域依赖。
6. Tool Permission 只控制本次运行能否读取目录、执行命令或调用工具；它不能批准 `DECISION`、`CONSTRAINT`、`ACTION`、正式日期或项目状态变更。
7. 如采用源码，只使用 `ee/**` 之外的 MIT 社区核心，保留许可证与版权声明，不依赖 Den、上游生产云、不可关闭遥测或上游更新源。内部名称、图标、协议和包标识不得造成官方来源混淆。
8. 若 OpenWork 切片不通过，退回更薄的本地界面或现有 Web 技术壳，不影响本地领域核心、SQLite、MCP、CLI、Task Packet 和运行时协议。
9. F1.9 的第一批补丁必须默认关闭 PostHog、Den、Cloud、上游模型目录和自动更新，更换内部名称/AppID/协议/更新源，并收紧 Electron 网络与沙箱配置。完成无上游请求验证后才能接真实鸿日资料。

## 采用门

OpenWork 只有同时满足以下条件，才升级为鸿日 MVP 客户端实现：

- 不启动团队服务即可完成冷启动、会议增量、证据回源、Proposal 和人工确认主旅程。
- Brand 业务页面能与 OpenCode 会话域清晰分离，不需要把项目状态塞入 OpenWork 会话或工作区配置。
- 本地构建图不包含 `ee/**`、Den、上游云端、未登记遥测或远程更新依赖。
- 关键路径可以通过稳定适配层实现，上游核心补丁规模可控且退出成本低。
- 从双击启动到看到鸿日当前状态的本地体验足够简单，不要求 Fox 理解服务器、Workspace Host 或部署术语。
- 相比薄本地界面，复用收益确实覆盖 Electron 安全、上游同步和 OpenCode 耦合成本。

## 后果

### 正面

- 在不锁定服务端架构的前提下复用成熟桌面交互与 Agent 控制能力。
- 通过端口隔离保留 Codex、Claude、OpenCode 和其他本地运行时互换能力。
- 若退出 OpenWork，本地权威数据、证据、状态和会议解释协议无需迁移。

### 代价

- 需要先做真实纵向切片，不能直接把 OpenWork 的功能清单当作适配完成证明。
- 即使只做本地 MVP，也要处理 Electron IPC、路径权限、外部导航、依赖供应链和许可证边界。
- OpenWork 原有会话中心界面可能需要调整，避免“聊天就是项目状态”的错误心智。

## 当前不采用

- 把 Brand OS 服务器化作为采用 OpenWork 的前置条件。
- 原样部署 OpenWork Den 或复制 `ee/**`。
- 让 OpenWork Server/OpenCode 接管正式事实、决定、证据或人工批准。
- 在鸿日 MVP 前完成三平台签名、灰度更新、团队设备撤权和完整企业管理面。
- 仅因 OpenWork 功能多，就放弃更薄、更适合单用户验证的客户端方案。

## 复审条件

- 本地纵向切片完成并有 Fox 的真实使用反馈。
- OpenWork 社区核心许可证、目录边界、Electron 运行时或 OpenCode 耦合发生变化。
- 本地改造连续出现必须部署服务端、必须使用 `ee/**` 或补丁无法审计的阻断。
- 鸿日试点后正式启动团队服务器评估；届时再单独审查私有 fork、品牌发行、签名更新、OIDC、远程 Worker 和兼容窗口。
