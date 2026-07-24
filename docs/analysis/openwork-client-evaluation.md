# OpenWork / FoxWork 客户端与 Den 评估

> 固定基线：OpenWork `v0.17.36@ddf3e482d2fdf3a374d0fbf4e23e01467a3014fc`  
> 核验日期：2026-07-24  
> 当前结论：FoxWork 采用 OpenWork 客户端和 Den 控制面；Brand OS 保留业务权威

## 最终判断

OpenWork 能成为公司内部“登录即用”的员工客户端基础。Den 源码包含注册登录、组织/团队、远程工作区、桌面交接、MCP、Skills、共享模型和策略管理，F3.2 已用真实构建、数据库和合成账户证明账号与能力链可以内部自托管和修改；远程 Worker 仍待 F3.3 真实验证。

因此不再自建第二套账号、团队连接、MCP/Skill 管理和模型管理。员工只安装 FoxWork、只维护 Den 账号。Brand Project OS 通过 Den 的第一方 OAuth/OIDC 接入，并继续独立负责项目资料、正式状态、证据、Proposal 和人工确认。

## 已验证事实

| 能力 | F3.2 结果 | 当前含义 |
|:---|:---|:---|
| Den Web/API/MySQL | 固定源码可构建并在测试 Mac 运行 | 技术上可自托管；生产运维待 F3.3 |
| 单一组织 | 第一个用户成为所有者，员工加入同一组织，第二组织被拒绝 | 可满足小团队公司空间 |
| 注册、登录、桌面交接、退出 | 合成账户链路通过 | FoxWork 可复用原生流程，F3.4 负责产品合并和中文 |
| Skills | 员工 REST 和 Den Agent MCP 均可读取/执行共享 Skill | 不再自建独立 Skill 控制面 |
| 共享模型 | 按授权获得 Provider/模型/托管凭据，撤权后 403 | 可实现员工登录即用模型 |
| 公司 MCP | 列表、搜索、执行和撤权联动通过 | Brand OS MCP 可注册到 Den |
| 敏感管理动作 | 过期登录返回 `fresh_auth_required` | 保留新鲜登录，不绕过安全门 |
| 远程工作区/Worker | 上游有 `stub`、Render、Daytona provisioner，未完成公司自托管 E2E | 已进入 F3.3 必验范围，不能写成已完成 |

F3.2 验证不是依赖未提交的 Den 业务补丁：固定提交之后的已提交差异没有改动 `ee/`、Helm 和自托管文档。

## 许可结论

| 范围 | 许可证 | 当前处置 |
|:---|:---|:---|
| `ee/**` 外 | MIT | 保留版权与许可，可修改和内部分发 |
| `ee/**`，含 Den | FSL-1.1-MIT | 只按公司内部用途使用和修改，保留许可与版权 |

FSL 允许当前公司内部使用、复制和修改，但限制把 FoxWork/Den 做成与 OpenWork 相同或近似的对外竞争服务。每个版本在公开发布两周年后转为 MIT。Den 不能写成“完全开源”或“MIT 企业功能”；本文不替代法律意见。

## 可直接复用的能力

- React/Vite 应用与 Electron 桌面壳；
- Workspace、Session、流式消息、工具权限、终端、文件和 Artifact 交互；
- OpenCode/Sidecar 运行集成；
- Den 自助注册登录、组织、成员、团队、远程工作区和桌面交接；
- Den MCP、Skills、Skill Hub、Provider、共享模型和策略管理；
- 管理员与员工权限、敏感动作新鲜登录；
- 桌面更新与跨平台打包基础。

这些能力解决“员工不会配置 AI”的实际问题。管理员在 Den 配好公司能力，员工登录后直接获得可用模型、MCP 和 Skills。

## 必须由 Brand OS 提供

| 品牌业务能力 | 为什么 Den/OpenWork 不能替代 |
|:---|:---|
| 原件、版本、SHA-256 和 S3 VersionId | Den Worker/Workspace 不是版本化证据库 |
| 当前事实、决定、约束和开放问题 | Session/模型摘要不是人工批准事件 |
| 会议模式和增量解释 | 通用会话没有品牌语义状态机 |
| Proposal 与人工确认 | Tool Permission 只授权工具动作 |
| Task Packet 和模式切换 | 模型聊天不能作为稳定任务快照 |
| 项目 RBAC、保密级别和 RLS | Den 组织/团队不等于品牌项目权限 |
| 图片/视频/录音/PPT/Office/PDF 分析 | 需要原件准入、长任务、来源定位和失败对账 |

OpenWork 是唯一客户端，但不是业务真相源。清除 OpenWork/Den/OpenCode Session、SQLite/JSON、索引和缓存后，PostgreSQL/S3 中的业务状态与原件必须完整。

## 员工产品流程

```text
启动 FoxWork
  -> 注册或登录 Den 公司账号
  -> 返回 FoxWork，进入唯一公司组织
  -> 连接获授权远程工作区，同步项目、模型、MCP、Skills 和桌面策略
  -> 在项目中上传资料、询问 AI、查看依据和处理 Proposal
```

Den Web 只承担登录、授权和必要管理，不是第二个日常客户端。旧“团队连接”和第二套 Brand OS 登录应删除，相关状态并入“工作区”“公司能力”和“设置”。

## 必须修改的部分

### FoxWork

- FoxWork 全部员工界面、错误、安装和更新使用自然简体中文；
- 用公司工作区替代旧团队连接和上游云入口；
- Den 登录后自动取得 Brand OS 资源令牌和项目列表；
- 新增资料、会议、证据、Proposal、流程和业务诊断视图；
- Token 进入系统钥匙串，不由 Renderer `localStorage` 保存；
- 内网可显式允许 HTTP 公司入口，但不允许全局忽略安全校验；
- 无公司配置时不回退上游 Den/Cloud、遥测、模型目录或更新源。

### Den

- 增加 Brand OS 第一方 OAuth 资源 audience、组织声明、PKCE 和撤权联动；
- 完成 Den Web 员工页面和后台管理员网页中文化；
- 固定单组织引导和普通员工注册策略；
- 完成远程工作区/Worker 的公司部署、生命周期、隔离、撤权和诊断；
- 为 Brand OS MCP、公司 Skills、共享模型和桌面策略建立公司目录；
- 补齐生产部署、MySQL 备份恢复、升级回滚和观测。

### Brand OS

- 按 Den `(issuer, subject)` 绑定员工，不建立第二套密码；
- 显式映射 Den 组织/团队/远程工作区和 Brand OS 项目权限；
- 每次 MCP/API 调用重新鉴权，不信任目录缓存；
- 上传与处理资料，输出可回源 Artifact/Proposal；
- 保持人工确认与 Tool Permission 分路。

## 身份边界

一次登录不等于一个万能 Token。FoxWork 先取得 Den 客户端会话，再通过 PKCE 取得面向 Brand OS audience 的短期令牌。Brand OS 令牌泄露不能调用 Den 管理 API；Den Session 泄露也不能直通 Brand OS 人工路由。

组织管理员不自动成为项目审批人。项目权限继续由 Brand OS RBAC/RLS 控制，Den 团队只作为映射和能力分发输入。

## OpenCode 与 Worker 耦合

OpenWork App/Server/Orchestrator 深度依赖 OpenCode SDK，短期内保留 OpenCode 是务实选择。Brand OS 领域对象不得导入 OpenCode 类型，Session 只保存 `task_id`、Packet 版本和运行引用。

上游当前 provisioner 只有 `stub`、Render 和 Daytona。F3.2 没有证明可在公司服务器自托管完整远程 Worker；由于远程工作区已是最终产品要求，F3.3 必须选定或实现可替换的公司部署路径：

- FoxWork 本地 OpenCode/Sidecar 处理员工电脑任务；
- Brand OS Worker 处理服务器资料；
- Den Worker 负责远程 Agent 运行，必须通过创建、连接、停止、恢复、撤权、清理和故障验收，但不能进入 Brand OS 领域核心或成为业务真相源。

## 当前安全缺口

- 上游历史实现存在 Renderer Token、Electron 沙箱、IPC/PTY/文件、上游更新和默认外联风险；F1.9 已收紧基础，F3.4/F4.9 继续验证联网与分发。
- Den 员工页面和后台管理员网页仍有英文和上游品牌，需要全量中文及产物扫描。
- Brand OS 专用 OAuth audience 尚未实现。
- 生产 MySQL 备份恢复、升级和 SLO 尚未验证。
- 远程工作区/Worker 的公司部署路径、隔离、撤权、恢复和诊断尚未验证。
- 内网 HTTP 必须限公司入口，不能扩展成任意 URL 或关闭重放保护。

## S.U.P.E.R 评估

| 模块 | S | U | P | E | R | 当前判断 |
|:---|:---:|:---:|:---:|:---:|:---:|:---|
| OpenWork App | 黄 | 黄 | 黄 | 黄 | 黄 | UI/运行体验成熟，但 OpenCode 与多职责耦合高 |
| Electron Desktop | 黄 | 黄 | 黄 | 黄 | 黄 | 能力完整，安全与分发面大 |
| OpenWork Server/Orchestrator | 黄 | 黄 | 黄 | 黄 | 红 | 适合运行控制，不适合业务核心；替换成本高 |
| Den Web/API/MySQL | 黄 | 黄 | 黄 | 黄 | 黄 | 功能满足控制面，生产化和 Brand OS federation 待完成 |
| Brand OS 适配层 | 绿 | 绿 | 黄 | 黄 | 绿 | 领域边界已稳定，F3 契约仍待实现 |

“采用 OpenWork/Den”是产品选择，不等于让领域核心依赖上游内部对象。可替换性目标是：换模型、解析器、Worker 或可选组件时不迁移业务数据；若未来替换 Den，也能通过稳定身份和能力端口迁移控制面，而不是把 Den 表当领域表。

## 决策门

| 门 | 状态 | 通过条件 |
|:---|:---|:---|
| OW-L0 社区客户端技术门 | 已通过 | 固定版可构建，默认外联可收口 |
| F1.10 FoxWork 本地业务门 | 已通过 | 单安装、真实业务纵切、fraimz 和黄金测试 |
| F3.2 Den 采用门 | 已通过 | 源码、许可、单组织、Skills、模型、MCP 和撤权 |
| F3.3 Den 生产门 | 进行中 | Web/API/MySQL/远程 Worker 可重复部署、恢复、升级回滚、观测和许可清单 |
| F3.4-F3.6 单账号门 | 待开始 | 自助注册、员工端/管理员后台全中文、无第二登录、独立 audience、远程工作区/项目映射和撤权 |
| F3.19 联网产品门 | 待开始 | 远程工作区、多媒体、业务闭环、MCP/Skills/模型、本机边界和 E2E |
| F4.10 生产准入 | 待开始 | 真实团队、安全、恢复、容量、签名更新和 Fox 明确批准 |

## 结论

- OpenWork 客户端可用，继续作为 FoxWork 唯一员工客户端。
- Den 可在许可证允许的公司内部范围自托管和修改，不需要重造账号/MCP/Skills/模型控制面。
- Brand OS 不能被 Den 替代，必须保留独立业务权威、证据、Proposal 和人工确认。
- 下一步不是继续研究是否采用，而是完成 F3.3-F3.6 的 Den/远程 Worker 生产部署、自助注册、全中文管理面和统一登录闭环。
