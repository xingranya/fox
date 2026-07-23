# OpenWork OW-L0 技术选型记录

> 结论：有条件通过  
> 核验日期：2026-07-22  
> 对应任务：F1.9 前置工作包，不单独计入 49 项任务完成数

> 2026-07-22 后续：公司已选定 OpenWork 作为唯一员工客户端。本页的“最终采用仍待决定”只记录 OW-L0 当时状态。正式决定见 [ADR-0004](../adr/0004-openwork-single-client.md)。

## 结论

OpenWork 可以继续作为 F1.9 的桌面壳基础，但不能把上游版本原样接入鸿日真实资料。

固定版本能够在本机完成社区工作区安装、桌面构建、OpenCode sidecar、OpenWork Server/Orchestrator 和 macOS Computer Use helper 打包。构建过程不要求 `ee/**`，也没有 Den 编译依赖。这证明它能作为本地客户端起点。

上游默认配置仍会连接 PostHog、OpenWork Den/Cloud、模型目录和 GitHub Releases；应用名称、AppID、协议、更新源和 macOS 网络权限也都是 OpenWork 默认值。F1.9 必须先完成默认离线补丁，之后才能接触鸿日原件、状态库或真实 Task Packet。

OW-L0 只证明上游基线可构建和可隔离，不证明公司客户端已经完成。OpenWork 已被选定为唯一客户端；后续门未通过时应阻断发布并修复，不再切换第二套员工界面。

## 固定基线

| 项目 | 核验结果 |
|:---|:---|
| 稳定发布 | [`v0.17.36`](https://github.com/different-ai/openwork/releases/tag/v0.17.36)，发布于 2026-07-20 |
| 固定提交 | [`ddf3e482d2fdf3a374d0fbf4e23e01467a3014fc`](https://github.com/different-ai/openwork/tree/ddf3e482d2fdf3a374d0fbf4e23e01467a3014fc) |
| 上游开发分支 | 核验时已前进到 `dev@636bf2d`，不作为采用基线 |
| 工具链 | Node.js 24；仓库固定 `pnpm@11.4.0` |
| 本地评估副本 | `.work/vendor/openwork-v0.17.36`，已忽略，不是正式 fork |
| 构建产物 | `.work/vendor/openwork-v0.17.36/apps/desktop/dist-electron/mac-arm64/OpenWork.app` |

后续正式开发必须从上述稳定提交建立独立 fork 和集成分支。`.work/vendor` 只保存可删除的核验副本，不在这里积累公司补丁。

## 构建与测试

本次只安装桌面端、应用、Server、Orchestrator、共用类型及其社区依赖，共 5/27 个 workspace。没有安装或构建 `ee/**`。

| 检查 | 结果 |
|:---|:---|
| Electron 桌面构建 | 通过 |
| OpenCode 1.17.11 sidecar | 通过 |
| OpenWork Server/Orchestrator | 通过 |
| macOS Computer Use helper | 通过 |
| 未签名 macOS `.app` | 通过，约 690 MB |
| Desktop 测试 | 80 项中 79 通过、1 跳过、0 失败 |
| Electron bridge | 55 个 Renderer 方法通过 |
| App TypeScript | 通过 |
| Server TypeScript | 通过 |

本轮没有启动桌面应用或常驻服务。OW-L0 只回答能否构建和隔离，不把“产出 `.app`”当成真实使用验收。

## 许可证

| 范围 | 结论 | 处理 |
|:---|:---|:---|
| 根目录及 `ee/**` 外代码 | MIT | 保留版权和许可证声明 |
| `ee/**` | FSL-1.1-MIT | 当前不复制、不安装、不构建、不依赖 |
| `khroma@2.1.0` | 包元数据缺少 license 字段，包内 LICENSE 和上游仓库均为 MIT | 在第三方声明中补录，不能因为扫描器显示 `Unknown` 就省略 |
| OpenWork/Different AI 名称与商标 | 不由代码许可证授予 | 使用公司内部名称、图标、AppID 和协议，不宣称官方关系 |

许可证结论仅覆盖当前固定版本和这次社区工作区选择。新增依赖、升级上游或进入对外分发时要重新生成清单。

## 默认外联与安全缺口

| 现状 | 证据 | F1.9 处理 |
|:---|:---|:---|
| 生产构建默认启用 PostHog Key | `apps/app/src/app/lib/analytics-key.ts` | 源码默认关闭；构建和产物扫描都不能出现上游 Key |
| Den/Cloud 默认地址仍在应用和桌面端 | `app.openworklabs.com`、`api.openworklabs.com` | 删除默认连接和登录入口；未显式配置时不得探测 |
| 模型目录默认连接上游 | `models.openworklabs.com` | 改为关闭或使用已登记内部来源 |
| 自动更新指向上游仓库 | `different-ai/openwork` GitHub Releases | 原型期默认关闭；正式分发前只允许公司更新源 |
| 应用仍使用上游身份 | `OpenWork`、`com.differentai.openwork`、`openwork://` | 更换内部名称、Bundle ID、协议和辅助进程标识 |
| macOS 允许任意网络加载 | `NSAllowsArbitraryLoads=true` | 改为默认拒绝，只保留受控回环和明确允许的 HTTPS 目标 |
| 主窗口 `sandbox=false` | `apps/desktop/electron/main.mjs` | 核对 preload/IPC 依赖后启用沙箱；不能启用时列出最小例外和测试 |
| 桌面包约 690 MB，含多个运行面 | 构建产物 | 不把 Server/Orchestrator 全部能力暴露给 Brand OS 页面 |

本次评估构建通过显式设置空的 `VITE_OPENWORK_POSTHOG_KEY` 去掉了产物中的默认 Key。这只能证明开关有效，不能代替源码默认值修正。

## OW-L0 判定

| 门 | 结果 |
|:---|:---|
| 稳定 SHA 可固定 | 通过 |
| 社区代码可独立构建 | 通过 |
| 不要求 `ee/**` 或 Den 编译依赖 | 通过 |
| 默认外联可识别 | 通过 |
| 上游默认值可直接处理真实资料 | 不通过 |
| 在补丁后继续 OW-L1 的可行性 | 通过 |

因此 OW-L0 为“有条件通过”。条件是默认离线补丁先于任何真实资料接入，不能一边做业务页面，一边暂时保留上游外联。

## F1.9 第一批补丁

1. 从 `v0.17.36@ddf3e482` 建立正式 fork；记录上游 remote、集成分支和第三方许可证。
2. 默认关闭 PostHog、Den、Cloud、上游模型目录和自动更新；加入源码与产物字符串扫描测试。
3. 更换产品名、Bundle/App ID、深链协议、辅助进程标识、数据目录和更新配置。
4. 收紧 Electron 导航、IPC、preload、沙箱和 macOS ATS；默认网络策略只允许回环与人工登记的模型目标。
5. Brand OS 页面只调用本地 CLI/MCP 或受控应用端口。OpenWork Session、SQLite、Workspace 和 Tool Permission 不得写正式业务状态。
6. 完成无网络冷启动、无上游请求、删除 OpenWork 运行数据不丢业务状态的测试后，再进入 OW-L1。

## F1.9 后续实现状态

正式工作树位于 `brand-os/f1.9-offline-shell`，最终提交为 [`7cf9b229`](https://github.com/xingranya/openwork/commit/7cf9b229ed85f5e86c6c2b6324f4da775c647141)。已完成：

- 默认关闭遥测、Den/Cloud、上游模型目录和自动更新；未配置模型目录时向 OpenCode 注入 `OPENCODE_DISABLE_MODELS_FETCH=1`。
- 产品名、Bundle ID、深链、数据目录和安装产物切换到 Brand Project OS。
- Electron Sandbox、IPC、导航、外链、浏览器 Session、麦克风和网络权限使用允许列表或隔离。
- macOS ATS 默认拒绝任意外联，删除相机/蓝牙权限，打包时排除上游测试、文档和 Cloud 能力插件。
- 上游反馈、社区入口、默认下载地址和公共服务回退已移除；Cloud、Sidecar、模型目录和更新源必须显式配置。
- 欢迎页、Cloud 登录页和空会话建议卡改用安装包内的 Lucide 图标；离线资源测试禁止核心界面重新依赖外部图标 CDN。
- 内部 `.app` 已构建；尚未签名和公证，不可向员工分发。

`brand-os-offline-shell` 的 8 帧 fraimz 已全部通过，报告位于 OpenWork fork 的 `evals/results/2026-07-22T12-14-39-312Z/fraimz.html`。无公司服务时进入本地入口；遥测、Cloud、模型目录和更新均为显式开启；应用身份和数据目录一致；导航、IPC、网络和文件访问按允许列表工作；实际 `.app` 不含 PostHog Key，也未包含鸿日资料。旧上游地址字符串只用于识别迁移配置，不构成默认地址，未显式登记时会被网络策略拒绝。

最终回归为 App 370 项通过，Desktop 100 项通过、1 项平台条件跳过；App、Desktop、Server、Orchestrator 类型检查通过。F1.9 已完成。后续 F1.10 与 Phase 2 也已通过，当前总进度为 27/49，活动任务是 F3.1；内部包尚未签名和公证，不能向员工分发。
