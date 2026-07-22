# 依赖图

## 总体顺序

```mermaid
flowchart TD
    P0["Phase 0\n边界、协议、黄金测试\n7/7"]
    P1["Phase 1\n单一客户端本地纵切\n10/10"]
    G1{{"F1.10 本地纵切门"}}
    P2["Phase 2\n服务器权威基础\n2/10"]
    G2{{"F2.10 服务器基础门"}}
    P3["Phase 3\n客户端联网、MCP、Skills、工作流\n0/13"]
    G3{{"F3.13 联网产品门"}}
    P4["Phase 4\n团队试点与生产准入\n0/9"]
    G4{{"F4.9 Go / 延长 / No-Go"}}
    BS["后续候选池\nBISHENG BS0-BS3\n不计入当前49项"]

    P0 --> P1 --> G1 --> P2 --> G2 --> P3 --> G3 --> P4 --> G4
    G4 -. "试点通过且 Fox 单独批准" .-> BS
    G1 -. "失败" .-> P1
    G2 -. "失败" .-> P2
    G3 -. "失败" .-> P3
    G4 -. "延长" .-> P4
```

任何阶段门未通过时，下游任务保持未开始。F1.10 和 F2.1-F2.2 已通过，当前从 F2.3 继续建设服务器权威基础。BISHENG 候选池不属于当前阶段依赖。

## Phase 1

```mermaid
flowchart LR
    F11["F1.1 工作空间"] --> F12["F1.2 SQLite"] --> F13["F1.3 导入"]
    F13 --> F14["F1.4 会议增量"] --> F15["F1.5 Proposal"] --> F16["F1.6 证据回源"]
    F16 --> F17["F1.7 Task Packet"] --> F18["F1.8 CLI/MCP"] --> F19["F1.9 OpenWork 单安装包"]
    F11 --> F110["F1.10 鸿日纵切与 fraimz/E2E"]
    F12 --> F110
    F13 --> F110
    F14 --> F110
    F15 --> F110
    F16 --> F110
    F17 --> F110
    F18 --> F110
    F19 --> F110
```

F1.1-F1.10 已完成。F1.10 的真实鸿日桌面纵切、fraimz、黄金集和本地 E2E 已通过，Phase 1 阶段门关闭。

## Phase 2

```mermaid
flowchart TD
    F21["F2.1 服务边界与测试基线"]

    subgraph DATA["Lane A/B：权威数据与一致性"]
        F22["F2.2 PostgreSQL 事件/审批/投影"]
        F23["F2.3 对象存储/哈希/准入"]
        F26["F2.6 幂等/乐观锁/冲突"]
        F27["F2.7 审计/Outbox/Inbox"]
    end

    subgraph AUTH["Lane C：身份与权限"]
        F24["F2.4 OIDC"]
        F25["F2.5 RBAC/RLS"]
    end

    subgraph SURFACE["Lane D：访问与运维"]
        F28["F2.8 HTTP API/OpenAPI"]
        F29["F2.9 观测与告警"]
    end

    F210{{"F2.10 恢复与服务器门"}}

    F21 --> F22
    F21 --> F23
    F21 --> F24 --> F25
    F22 --> F25
    F22 --> F26
    F25 --> F26 --> F27
    F23 --> F28
    F25 --> F28
    F26 --> F28
    F27 --> F28 --> F29 --> F210
    F22 --> F210
    F23 --> F210
```

## Phase 3

```mermaid
flowchart TD
    F31["F3.1 一次性迁移与权威切换"] --> F32["F3.2 Desktop 登录/项目/连接"]
    F32 --> F33["F3.3 当前状态与证据"] --> F34["F3.4 Proposal 与冲突"]
    F32 --> F35["F3.5 本机权限桥接"]

    F36["F3.6 MCP Gateway"] --> F37["F3.7 Skills 目录"]
    F36 --> F38["F3.8 Dify 适配"]
    F36 --> F311["F3.11 Nubase 评估/适配"]
    F36 --> F312["F3.12 FlowLong 评估/适配"]
    F39["F3.9 Zvec 评估/适配"]
    F310["F3.10 Open Notebook 评估/适配"]

    G3{{"F3.13 联网产品门"}}
    F31 --> G3
    F34 --> G3
    F35 --> G3
    F37 --> G3
    F38 --> G3
    F39 --> G3
    F310 --> G3
    F311 --> G3
    F312 --> G3
```

F3.9-F3.12 彼此独立，可并行评估。采用不是完成的唯一答案；有证据地拒绝并验证 NoOp 回退也算通过。

## Phase 4

```mermaid
flowchart TD
    F41["F4.1 成员/角色/资料范围"]
    F41 --> F42["F4.2 并发一致性"]
    F41 --> F43["F4.3 真实工作"] --> F44["F4.4 多模型与 BrandBench"]
    F42 --> F45["F4.5 故障与恢复"]
    F41 --> F46["F4.6 安全与供应链"]
    F42 --> F47["F4.7 容量/成本/SLO"]
    F45 --> F47
    F46 --> F48["F4.8 签名/更新/回滚"]
    F49{{"F4.9 生产 Go/延长/No-Go"}}
    F43 --> F49
    F44 --> F49
    F45 --> F49
    F46 --> F49
    F47 --> F49
    F48 --> F49
```

## 权威数据流

```mermaid
flowchart LR
    RAW["原始资料"] --> HASH["SHA-256 / 来源版本"] --> OBJ[("对象存储")]
    HASH --> PROPOSAL["候选 / Proposal"]
    AI["Agent / MCP / Dify"] --> PROPOSAL
    PROPOSAL --> REVIEW{{"有权限员工确认"}}
    REVIEW -->|"确认"| TX["单事务：事件 + 审批 + 投影 + 审计 + Outbox"]
    TX --> PG[("PostgreSQL 唯一写入权威")]
    REVIEW -->|"驳回/暂缓"| HISTORY["保留历史，不改当前状态"]
    PG --> API["版本化 API / MCP"]
    API --> DESKTOP["唯一员工客户端"]
    API --> AGENTS["Codex / Claude / 工作流"]
```

## 停止传播

- F1.10 已通过；验收副本和来源 Manifest 保持只读，供后续迁移对账。
- F2.10 未通过恢复与一致性门：不得将本地 SQLite 降为缓存。
- F3.13 未通过：不得向团队分发或连接生产资料。
- 任一一票否决、跨项目越权、服务账号批准、双主或静默覆盖出现时，立即阻断当前阶段并补 Fixture、修复和全量回归。
- BISHENG 只有在 Phase 4 试点结论和 Fox 单独批准后才可进入正式任务图；在此之前不得连接公司生产资料。
