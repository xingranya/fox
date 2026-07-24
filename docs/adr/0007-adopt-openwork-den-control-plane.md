# ADR-0007：采用 OpenWork Den 作为公司 AI 控制面

- 状态：已接受
- 日期：2026-07-24
- 决策人：Fox
- 影响范围：FoxWork 登录、组织与团队、MCP、Skills、共享模型、服务器部署、身份和许可证
- 替代：ADR-0002 中“不采用 `ee/**`/Den”的活动约束；ADR-0004、ADR-0005 中自行建设团队连接和 Skills/模型目录的部分
- 注册与远程工作区补充：[ADR-0008](0008-den-self-registration-and-remote-workspaces.md)

## 背景

FoxWork 的员工都是 AI 初学者。让员工分别配置模型、MCP、Skills、服务器地址和第二套 Brand OS 账号，会把系统变成只有开发人员能使用的工具。OpenWork Den 已经提供注册登录、组织、团队、桌面交接、共享模型、Skills 和 MCP 控制面，产品体验与“登录即用”的目标一致。

此前规划因为没有确认 Den 源码和许可证边界，选择只使用 `ee/**` 之外的 MIT 客户端并自建 OIDC、团队连接和能力目录。2026-07-24 的源码、构建、数据库和合成账户技术门证明：Den 可以在公司内部自托管和修改，核心链路不依赖缺失的私有源码。

## 决定

1. FoxWork 继续是员工唯一安装的软件，发行名和全中文要求不变。
2. 公司内部部署 Den Web、Den API 和独立 MySQL，使用 `single_org` 模式。
3. Den 统一负责员工账号、组织、团队、远程工作区/Worker、FoxWork 登录交接、MCP、Skills、共享模型和桌面策略。
4. 员工只注册和登录 Den。生产环境按 ADR-0008 允许公司入口自助注册并限制在唯一组织；原“团队连接”页并入 Den 公司工作区，不再提供第二套 Brand OS 账号入口。
5. Brand Project OS Service 继续使用 PostgreSQL 和版本化对象存储，负责项目资料、正式状态、证据、Proposal、人工确认和多媒体分析。
6. Brand OS MCP 登记到 Den 的公司 MCP 目录，按成员或团队下发。MCP 没有人工批准正式状态的权限。
7. Brand OS 通过 Den OAuth/OIDC 第一方令牌识别员工，按 `(issuer, subject)` 绑定。普通员工自助注册后可在首次访问时建立内部身份映射，但不自动获得项目权限或业务审批权；禁止跨服务复用原始 Den Session Token。
8. Den 的共享模型密钥由 Den 管理并只下发给获授权客户端；Brand OS 不读取或保存模型 API Key。
9. Den MySQL 是账号与控制面运行数据，Brand OS PostgreSQL/S3 是业务正式数据。两者不双写正式状态，也不做跨数据库分布式事务。
10. 采用范围仅限公司内部使用。不得把 FoxWork/Den 作为与 OpenWork 相同或相近的对外商业服务。

## 许可证依据

OpenWork 根许可证规定 `ee/**` 使用 `ee/LICENSE`，其余代码采用 MIT。`ee/LICENSE` 为 `FSL-1.1-MIT`：允许内部使用、复制、修改和派生；限制竞争性用途；每个版本在发布两周年后转为 MIT。内部发行必须保留相应许可证和版权，不得使用 OpenWork 商标暗示官方关系。

## 职责边界

| 能力 | 权威负责方 | 不能承担 |
|:---|:---|:---|
| 账号、组织、团队、角色 | Den | 品牌项目事实和审批 |
| MCP、Skills、共享模型 | Den 控制面 | 业务数据库和人工确认 |
| 项目、资料、证据、Proposal | Brand OS Service | 密码和模型密钥管理 |
| 正式事件、审批、投影 | Brand OS PostgreSQL | Den Session 或 Worker 文件系统 |
| 原件版本 | Brand OS S3 兼容对象存储 | Den MySQL 或 OpenWork 工作区 |
| 本机文件、终端、桌面 | FoxWork 受控本机桥接 | 服务器绕过员工授权 |

## 需要修改 Den 的部分

- 增加 Brand OS 第一方 OAuth 资源受众、组织声明和撤权联动；
- 固定 `single_org` 自助注册、公司入口和第二组织拒绝策略；
- 全量中文化 FoxWork、Den 员工页面和 Den 后台管理员网页；
- 固定 FoxWork 品牌、协议、更新源和允许版本；
- 预置 Brand OS MCP、公司 Skills 和共享模型策略；
- 增加适合公司部署的健康、审计、备份恢复和升级验收。

这些补丁必须保留在 OpenWork fork 的 `ee/**` 中，按 FSL 管理，不能误标为 MIT 自研代码。

## 后果

### 正面

- 员工只维护一个账号，登录后即可获得公司模型、MCP 和 Skills；
- 复用已存在并已验证的组织权限和桌面交接，减少重复建设；
- 模型、MCP 和 Skills 的授权与撤销可以在一个控制面完成；
- Brand OS 仍保持独立业务权威，不被 OpenWork 会话或 Den 数据结构锁死。

### 代价与风险

- 新增 Den Web、Den API 和 MySQL 的生产运维面；
- `ee/**` 受 FSL 限制，未来若把产品公开商业化必须重新做法律和架构评估；
- Den 员工页面和后台管理员网页当前仍有大量英文，需要完整中文化和产物扫描；
- Den 远程工作区/Worker 已成为 Phase 3 交付要求，但自托管路径尚未证明，必须在 F3.3/F3.19 用测试环境完成部署、隔离、撤权和故障验收；
- Den 与 Brand OS 的身份和撤权必须通过正式 OAuth/OIDC 契约衔接，不能靠共享 Token。

## 验收

详细证据见 [OpenWork Den 内部自托管技术门](../phase3/openwork-den-self-host-gate.md)。Phase 3 只有在生产 Den 基线、单点登录、全中文 FoxWork、资料分析、Brand OS MCP 和端到端撤权全部通过后才能关闭。
