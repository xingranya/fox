# 依赖图

> 当前活动方案：Den 统一控制面 + Brand Project OS 权威业务服务。任务定义以 [任务分解](task-breakdown.md) 为准。

## 总体顺序

```mermaid
flowchart LR
    P0["Phase 0\n边界与黄金测试\n7/7"] --> P1["Phase 1\nFoxWork 本地纵切\n10/10"]
    P1 --> P2["Phase 2\n服务器权威基础\n10/10"]
    P2 --> P3["Phase 3\nDen 与联网业务闭环\n2/19"]
    P3 --> P4["Phase 4\n团队试点与生产准入\n0/10"]
```

Phase 0-2 和 F3.1 已完成。2026-07-24 的重定范围不重做这些成果，只替换旧的“双登录/不部署 Den”后续路径。

## Phase 0-2 已完成链路

```mermaid
flowchart LR
    F01["F0.1 边界"] --> F02["F0.2 样本"] --> F05["F0.5 黄金集"]
    F01 --> F03["F0.3 分类"] --> F04["F0.4 运行协议"] --> F05
    F05 --> F06["F0.6 BrandBench"] --> F07["F0.7 契约门"]

    F07 --> F11["F1.1 工作空间"] --> F12["F1.2 SQLite"] --> F13["F1.3 导入"]
    F13 --> F14["F1.4 会议增量"] --> F15["F1.5 Proposal"] --> F16["F1.6 证据"]
    F16 --> F17["F1.7 Task Packet"] --> F18["F1.8 CLI/MCP"] --> F19["F1.9 FoxWork"] --> F110["F1.10 本地门"]

    F110 --> F21["F2.1 服务器基线"]
    F21 --> F22["F2.2 PostgreSQL"]
    F21 --> F23["F2.3 S3"]
    F21 --> F24["F2.4 OIDC 协议"]
    F22 --> F25["F2.5 RBAC/RLS"]
    F24 --> F25
    F25 --> F26["F2.6 并发一致性"] --> F27["F2.7 审计/Outbox"]
    F23 --> F28["F2.8 HTTP API"]
    F27 --> F28 --> F29["F2.9 观测"] --> F210["F2.10 恢复门"]
```

## Phase 3

```mermaid
flowchart TD
    F210["F2.10 服务器基础门"] --> F31["F3.1 权威迁移"]
    F31 --> F32["F3.2 Den 自托管技术门"]

    subgraph A["Lane A：Den 与远程 Worker 生产基线"]
        F32 --> F33["F3.3 Den/Worker 生产部署"]
    end

    subgraph B["Lane B：FoxWork 员工界面"]
        F33 --> F34["F3.4 自助注册/登录/组织/全中文"]
        F39["F3.9 状态/资料/证据视图"] --> F310["F3.10 Proposal 与冲突"]
    end

    subgraph C["Lane C：身份与项目"]
        F33 --> F35["F3.5 Den -> Brand OS OAuth/OIDC"]
        F35 --> F36["F3.6 组织/团队/远程工作区/项目映射"]
    end

    subgraph D["Lane D：资料与多媒体"]
        F36 --> F37["F3.7 上传/准入/任务状态"]
        F37 --> F38["F3.8 图片/视频/录音/PPT/Office/PDF 分析"]
    end

    subgraph E["Lane E：本机与远程运行边界"]
        F34 --> F311["F3.11 本机桥接/远程 Worker"]
        F36 --> F311
    end

    subgraph F["Lane F：公司 AI 能力目录"]
        F36 --> F312["F3.12 Brand OS MCP 接入 Den"]
        F34 --> F313["F3.13 Skills/模型/桌面策略"]
        F36 --> F313
    end

    subgraph G["Lane G：工作流"]
        F38 --> F314["F3.14 Dify 适配"]
        F312 --> F314
    end

    subgraph H["Lane H：可选适配器"]
        F38 --> F315["F3.15 Zvec"]
        F38 --> F316["F3.16 Open Notebook"]
        F312 --> F317["F3.17 Nubase"]
        F310 --> F318["F3.18 FlowLong"]
        F312 --> F318
    end

    F34 --> F39
    F36 --> F39
    F38 --> F39

    F310 --> F319["F3.19 联网产品门"]
    F311 --> F319
    F312 --> F319
    F313 --> F319
    F314 --> F319
    F315 --> F319
    F316 --> F319
    F317 --> F319
    F318 --> F319
```

关键顺序：

1. Den Web/API/MySQL 与远程 Worker 生产基线先于员工登录改造；不能继续用测试 Mac 的 HTTP 和测试密钥做正式连接。
2. Den 到 Brand OS 的身份联邦先于项目、上传和 MCP；不能用共享服务令牌替代员工身份。
3. 原件上传和准入先于解析；解析结果先于状态/证据界面。
4. Brand OS MCP 和 Skills/共享模型在同一 Den 控制面下发，但业务 MCP 与模型密钥仍是不同权限面。
5. Den 远程工作区/Worker 是必验运行面；Zvec、Open Notebook、Nubase、FlowLong 可分别拒绝，NoOp 不阻断 F3.19。

## Phase 4

```mermaid
flowchart TD
    F319["F3.19 联网产品门"] --> F41["F4.1 试点范围"]
    F41 --> F42["F4.2 自助注册/账号生命周期"]
    F41 --> F43["F4.3 多客户端一致性"]
    F42 --> F44["F4.4 真实工作与多媒体"]
    F43 --> F44
    F44 --> F45["F4.5 多模型与 BrandBench"]
    F42 --> F46["F4.6 Den Worker/全栈恢复"]
    F43 --> F46
    F44 --> F46
    F45 --> F47["F4.7 安全/外发/供应链"]
    F46 --> F47
    F43 --> F48["F4.8 容量/成本/SLO"]
    F46 --> F48
    F47 --> F49["F4.9 签名/更新/回滚"]
    F45 --> F410["F4.10 Go/延长/No-Go/Wiki"]
    F48 --> F410
    F49 --> F410
```

## 权威数据流

```mermaid
flowchart LR
    USER["员工"] --> FOX["FoxWork"]
    FOX --> DEN["Den Web/API"]
    DEN --> DENDB[("Den MySQL\n账号/组织/控制面")]
    DEN --> OIDC["短期 OAuth/OIDC 令牌"]
    OIDC --> BAPI["Brand OS API"]
    FOX --> BAPI
    BAPI --> PG[("PostgreSQL\n事件/审批/投影")]
    BAPI --> S3[("版本化对象存储\n原件")]
    BAPI --> OUTBOX["Outbox / Worker"]
    OUTBOX --> ART["派生 Artifact"]
    DEN --> MCP["公司 MCP/Skills/模型目录"]
    MCP --> BMCP["Brand OS MCP"] --> BAPI
```

Den MySQL 与 Brand OS PostgreSQL/S3 没有跨库分布式事务。账号撤权通过令牌撤销、短期过期和可审计同步收敛；业务正式状态只在 Brand OS 事务中变化。

## 停止传播

- Den 不可用：禁止新登录和控制面变更；已有 Brand OS 短期会话按明确过期策略运行，不伪装成永久在线。
- Brand OS 不可用：FoxWork 可显示账号和 Den 控制面，但业务页只读降级或明确不可用，不写 Den MySQL 代替。
- 解析 Worker 不可用：原件仍可上传和回源，任务进入可重试状态，不生成无来源摘要。
- 可选组件不可用：回退 PostgreSQL FTS、内置解析或 NoOp，正式状态继续可读。
- 任一身份、权限、哈希、版本或中文发布门失败：停止进入 F3.19/F4.10。
