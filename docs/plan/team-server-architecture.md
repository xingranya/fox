# 团队服务器架构

可视化交付：[可编辑 Draw.io 源文件](../diagrams/team-server-architecture.drawio)；[嵌入源数据的 PNG](../diagrams/team-server-architecture.drawio.png)。

## 架构结论

生产形态采用“服务器权威、Web/PWA 优先、AI 多入口共用同一 API”。团队自托管指数据、账号、密钥和备份由团队控制，不要求所有基础设施都运行在同一台自建主机上。

~~~mermaid
flowchart TB
    HUMAN["团队成员"] --> EDGE["HTTPS 入口 / WAF / Caddy"]
    AI["Codex / Claude / 其他 AI"] --> EDGE
    CLI["CLI / 本地上传代理"] --> EDGE
    EDGE --> WEB["Web / PWA"]
    EDGE --> API["无状态 API"]
    EDGE --> MCP["远程 MCP 网关"]
    WEB --> API
    MCP --> API
    API --> CORE["领域核心与应用服务"]
    CORE --> PG[("PostgreSQL 权威库")]
    CORE --> S3[("S3 兼容对象存储")]
    PG --> WORKER["Outbox Worker"]
    WORKER --> SEARCH["PostgreSQL 检索 / Zvec"]
    WORKER --> AIWF["直接 Worker / Dify"]
    WORKER --> NOTEBOOK["Open Notebook"]
    WORKER --> FLOW["FlowLong"]
    AIWF --> API
    NOTEBOOK --> API
    FLOW --> API
~~~

## 组件职责

| 组件 | 保存内容 | 写入规则 | 故障时行为 |
|:---|:---|:---|:---|
| PostgreSQL | 身份映射、项目权限、领域事件、正式投影、审批、Outbox、审计 | 只经领域应用服务写入 | 核心写入停止，禁止伪成功 |
| 对象存储 | 原始文件、提交件、版本化导出和备份 | 临时区校验后转为不可变正式对象 | 新上传暂停，已登记状态保持可查 |
| Web/PWA | 展示和交互状态 | 只调 API；浏览器不保存正式数据库 | 可显示只读快照和故障状态 |
| API/MCP | 身份、权限、业务用例和协议适配 | 无状态；不得保存独立业务事实 | 任一副本可替换 |
| Worker | Outbox 消费、投影外派、通知和重建 | 至少一次投递，消费者幂等 | 积压后追平，不阻塞正式事务 |
| PostgreSQL 检索 | 首发关键词、过滤、权限回源 | 派生字段可重建 | 作为 Zvec 降级路径 |
| Zvec | 混合检索索引 | 单活动写 Worker；只存稳定 ID 和派生字段 | 降级到 PostgreSQL 检索 |
| Dify | AI 工作流运行、模型编排和运行日志 | 仅接收最小 Task Packet，返回 Proposal | 切换直接 Worker 或暂停 AI 任务 |
| Open Notebook | 研究工作台和内容处理 | 单向副本，结果只作为候选 | 主系统保持完整 |
| FlowLong | 复杂人工流程协调 | 回调需核心重新鉴权和提交 | 使用内置审批状态机 |
| Nubase | Auth/Storage/Gateway 平台 POC | 不直接写正式领域表 | 完全禁用不影响核心 |

## 部署档位

| 档位 | 拓扑 | 用途 | RPO / RTO | 结论 |
|:---|:---|:---|:---|:---|
| 开发与演示 | Docker Compose，PostgreSQL、对象存储和单 API 节点 | 本地开发、CI、隔离 POC | 不作为生产承诺 | 必须保留 |
| 小团队生产 | 1 个应用节点 + 托管 PostgreSQL + 托管对象存储 + 独立备份域 | 3-10 人首发 | 5 分钟 / 60 分钟 | 首选 |
| 高可用生产 | 2 个以上应用节点 + 负载均衡 + 多可用区 PostgreSQL + 版本化对象存储 | 稳定使用后 | 1 分钟 / 15-30 分钟 | 达到升级门后采用 |

不采用“两台自建 PostgreSQL 即高可用”的方案。缺少仲裁、自动故障转移和成熟备份体系时，它会增加脑裂风险。首发也不引入 Kubernetes；无状态容器、托管数据库和自动化发布已经能满足小团队需求。

## 网络与信任区

- 公网只开放 443，所有 HTTP 自动重定向到 HTTPS。
- PostgreSQL、对象存储管理端、Redis、Zvec、Dify 管理端和监控管理端不得直接暴露公网。
- 运维入口使用 VPN、Tailscale、堡垒机或云厂商私网控制面，并启用 MFA。
- API 到外部模型、Embedding、转录、网页抓取和插件的出口实行提供商白名单、任务级授权和审计。
- Dify、Open Notebook、Nubase 和 FlowLong 使用独立服务账号、独立数据库或 Schema，不共享数据库超级用户。

## 健康、监控与告警

- /livez 只确认进程仍可服务，避免依赖抖动触发重启风暴。
- /readyz 检查 PostgreSQL、Schema 版本和必要配置；Zvec、Dify、Notebook 或模型故障不得让核心 API 整体失去就绪状态。
- 指标至少覆盖可用率、P95 延迟、5xx、连接池、死锁、版本冲突、Outbox 延迟、Worker 心跳、备份年龄、WAL 归档、磁盘、证书和外部模型错误率。
- 严重告警：核心不可用超过 3 分钟、数据库不可用、WAL 归档停止、最近成功备份超过 24 小时、磁盘超过 90%、跨项目越权事件。
- 日志使用 request_id、correlation_id 和结构化字段；禁止记录 Token、密码、完整原文、未脱敏提示词或模型密钥。

## 服务目标

| 指标 | 首个生产目标 |
|:---|:---|
| 核心 API 月可用性 | 首发不低于 99.5%，高可用档不低于 99.9% |
| 读取接口 P95 | 不高于 500 ms |
| 非 AI 写入 P95 | 不高于 1 s |
| Outbox/检索追平 P95 | 不高于 5 s |
| PostgreSQL RPO | 不高于 5 分钟 |
| 核心服务 RTO | 不高于 60 分钟 |
| 初始并发 | 50 个在线会话、10 个并发写请求 |

所有目标都必须以压测、监控和独立恢复演练证明，不能只依据云服务商宣传。

## 发布与迁移

1. 镜像固定版本和摘要，生产禁用 latest。
2. 迁移采用 expand-migrate-contract，并通过 PostgreSQL advisory lock 保证只有一个迁移任务执行。
3. 发布前在脱敏恢复库运行迁移、金标回归、权限测试和关键查询。
4. 保留最近两个应用版本；应用可快速回滚，数据库默认向前修复。
5. 破坏性迁移至少延迟一个发布周期，并在执行前创建可验证恢复点。
6. 单应用节点允许明确维护窗口；升级为双节点后采用滚动或蓝绿发布。

## 扩容门

满足任一条件时评估双应用节点和更高数据库规格：连续 30 天正式使用、活跃成员超过 5 人、月可用性接近 99.5% 下限、CPU 或连接池连续一周超过 70%、核心请求 P95 连续超标。扩容后的服务目标提高到 99.9%，但不改变权威边界。
