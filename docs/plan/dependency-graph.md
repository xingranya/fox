# 任务依赖图

## 读取说明

- 节点与[任务分解](task-breakdown.md)中的 42 个任务一一对应；箭头表示“前置任务完成后才可开始”。
- Lane 表示可独立派发的工作流，不代表同一 Lane 内任务可忽略显式依赖。
- `G_FLOW_LICENSE` 是外部书面许可门，不计入 42 个实施任务。
- Phase 1-4 共 29 项，形成“团队服务器试运行版”；Phase 5-6 在试运行版通过后执行。

## 全量依赖与并行 Lane

```mermaid
flowchart TD
    subgraph P1["Phase 1：契约、身份与安全基线"]
        subgraph P1A["Lane A"]
            F1_1["F1.1 V5 黄金样本"]
            F1_3["F1.3 领域 Schema、事件、状态机与端口"]
        end
        subgraph P1B["Lane B"]
            F1_2["F1.2 服务器权威与拓扑 SLO ADR"]
            F1_4["F1.4 租户、角色、保密与威胁模型"]
        end
        subgraph P1C["Lane C"]
            F1_5["F1.5 OIDC、RLS、幂等与接口契约"]
            F1_6["F1.6 工程、测试、密钥与许可基线"]
        end
    end

    F1_1 --> F1_3
    F1_2 --> F1_3
    F1_2 --> F1_4
    F1_3 --> F1_5
    F1_4 --> F1_5
    F1_2 --> F1_6

    subgraph P2["Phase 2：权威数据与可靠性脊柱"]
        subgraph P2A["Lane A"]
            F2_1["F2.1 PostgreSQL、Alembic 与强制 RLS"]
            F2_2["F2.2 事务、事件、投影、并发与 Outbox"]
        end
        subgraph P2B["Lane B"]
            F2_3["F2.3 S3 准入、内容寻址与谱系"]
            F2_4["F2.4 V5 幂等导入与数量对账"]
        end
        subgraph P2C["Lane C"]
            F2_5["F2.5 PostgreSQL 检索与稳定回源"]
            F2_6["F2.6 Outbox Worker、Inbox 与索引世代"]
        end
        subgraph P2D["Lane D"]
            F2_7["F2.7 PITR、对象恢复与诊断"]
        end
    end

    F1_3 --> F2_1
    F1_4 --> F2_1
    F2_1 --> F2_2
    F1_5 --> F2_2
    F1_3 --> F2_3
    F1_4 --> F2_3
    F2_2 --> F2_4
    F2_3 --> F2_4
    F2_4 --> F2_5
    F2_2 --> F2_6
    F2_5 --> F2_6
    F2_3 --> F2_7
    F2_6 --> F2_7

    subgraph P3["Phase 3：团队治理与 AI 统一访问"]
        subgraph P3A["Lane A"]
            F3_1["F3.1 OIDC、MFA、会话与服务账号"]
            F3_2["F3.2 RBAC、Scope、保密与 RLS 上下文"]
        end
        subgraph P3B["Lane B"]
            F3_3["F3.3 Proposal 与正式状态迁移"]
            F3_4["F3.4 人工审批与并发控制"]
            F3_5["F3.5 当前状态、证据与 Task Packet"]
        end
        subgraph P3C["Lane C"]
            F3_6["F3.6 HTTP/OpenAPI、审计与错误模型"]
            F3_7["F3.7 远程 MCP 与 stdio 代理"]
        end
        subgraph P3D["Lane D"]
            F3_8["F3.8 CLI、钥匙串与 Skills"]
        end
    end

    F1_4 --> F3_1
    F2_1 --> F3_1
    F3_1 --> F3_2
    F2_1 --> F3_2
    F2_2 --> F3_3
    F3_2 --> F3_3
    F3_3 --> F3_4
    F2_5 --> F3_5
    F3_3 --> F3_5
    F3_2 --> F3_6
    F3_4 --> F3_6
    F3_5 --> F3_6
    F3_6 --> F3_7
    F3_6 --> F3_8

    subgraph P4["Phase 4：Web/PWA 与团队服务器试运行版"]
        subgraph P4A["Lane A"]
            F4_1["F4.1 会议候选与直接 Worker"]
        end
        subgraph P4B["Lane B"]
            F4_2["F4.2 Web/PWA 壳与无障碍"]
            F4_3["F4.3 今日、工作、知识与证据"]
            F4_4["F4.4 待确认与审批冲突"]
        end
        subgraph P4C["Lane C"]
            F4_5["F4.5 团队、审计、健康与 AI 连接"]
            F4_6["F4.6 离线只读、草稿与冲突"]
        end
        subgraph P4D["Lane D"]
            F4_7["F4.7 TLS、监控、告警与回滚"]
            F4_8["F4.8 租户、并发、恢复与金标验收"]
        end
    end

    F2_3 --> F4_1
    F2_6 --> F4_1
    F3_3 --> F4_1
    F3_6 --> F4_2
    F4_2 --> F4_3
    F3_5 --> F4_3
    F2_5 --> F4_3
    F4_1 --> F4_4
    F4_3 --> F4_4
    F3_4 --> F4_4
    F3_2 --> F4_5
    F4_2 --> F4_5
    F4_3 --> F4_6
    F4_4 --> F4_6
    F2_7 --> F4_7
    F3_6 --> F4_7
    F4_4 --> F4_8
    F4_5 --> F4_8
    F4_6 --> F4_8
    F4_7 --> F4_8

    subgraph P5["Phase 5：五个外部组件隔离 POC"]
        subgraph P5A["Lane A"]
            F5_1["F5.1 统一契约、基准与故障评分"]
            F5_7["F5.7 五组件采用 ADR"]
        end
        subgraph P5B["Lane B"]
            F5_2["F5.2 Zvec POC"]
        end
        subgraph P5C["Lane C"]
            F5_3["F5.3 Open Notebook/content-core POC"]
        end
        subgraph P5D["Lane D"]
            F5_4["F5.4 Nubase 单项能力 POC"]
        end
        subgraph P5E["Lane E"]
            F5_5["F5.5 FlowLong 许可与审批 POC"]
        end
        subgraph P5F["Lane F"]
            F5_6["F5.6 Dify 与直接 Worker A/B"]
        end
    end

    G_FLOW_LICENSE{"FlowLong 书面许可通过？"}
    F4_8 --> F5_1
    F5_1 --> F5_2
    F2_6 --> F5_2
    F5_1 --> F5_3
    F2_3 --> F5_3
    F5_1 --> F5_4
    F2_7 --> F5_4
    F3_2 --> F5_4
    F5_1 --> F5_5
    G_FLOW_LICENSE -- "是" --> F5_5
    F5_1 --> F5_6
    F4_1 --> F5_6
    F3_5 --> F5_6
    F5_2 --> F5_7
    F5_3 --> F5_7
    F5_4 --> F5_7
    F5_5 --> F5_7
    F5_6 --> F5_7

    subgraph P6["Phase 6：选择性集成与生产验证"]
        subgraph P6A["Lane A"]
            F6_1["F6.1 适配器、NoOp 与降级开关"]
        end
        subgraph P6B["Lane B"]
            F6_2["F6.2 ModelGateway、AIWorkflow 与留痕"]
            F6_3["F6.3 BrandBench 与攻击回归"]
        end
        subgraph P6C["Lane C"]
            F6_4["F6.4 负载、故障、PITR 与安全发布"]
        end
        subgraph P6D["Lane D"]
            F6_5["F6.5 鸿日团队试运行"]
            F6_6["F6.6 第二项目与生产准入"]
        end
    end

    F5_7 --> F6_1
    F5_7 --> F6_2
    F3_5 --> F6_2
    F6_1 --> F6_3
    F6_2 --> F6_3
    F6_1 --> F6_4
    F6_3 --> F6_4
    F6_4 --> F6_5
    F6_5 --> F6_6
```

## 权威数据流与派生边界

```mermaid
flowchart LR
    D_CLIENTS["Web/PWA、CLI、MCP、Skills"] --> D_API["统一应用服务"]
    D_API --> D_CORE["领域核心"]
    D_CORE --> D_PG[("PostgreSQL 权威库")]
    D_API --> D_S3[("S3 不可变原件")]
    D_PG --> D_OUTBOX["Outbox Worker"]
    D_OUTBOX --> D_SEARCH["PostgreSQL 检索 / Zvec"]
    D_OUTBOX --> D_NOTEBOOK["Open Notebook"]
    D_OUTBOX --> D_DIFY["直接 Worker / Dify"]
    D_OUTBOX --> D_FLOW["核心审批 / FlowLong"]
    D_SEARCH -. "稳定 ID 回源" .-> D_API
    D_NOTEBOOK -. "派生候选" .-> D_API
    D_DIFY -. "结构化 Proposal" .-> D_API
    D_FLOW -. "人工动作待复核" .-> D_API
```

只有 `D_PG` 接受正式业务状态写入；`D_S3` 只保存不可变内容原件。所有搜索、研究、AI 和流程数据均为可重建派生数据或协调状态。

## 关键路径

主关键路径是：`F1.2 -> F1.4 -> F2.1 -> F2.2 -> F2.4 -> F2.5 -> F3.5 -> F3.6 -> F4.2 -> F4.3 -> F4.4 -> F4.8 -> F5.1 -> F5.6 -> F5.7 -> F6.2 -> F6.3 -> F6.4 -> F6.5 -> F6.6`。

恢复能力的独立关键路径是：`F1.3/F1.4 -> F2.3 -> F2.7 -> F4.7 -> F4.8`。F2.7 未通过时，不允许以“功能可用”替代团队服务器发布门。

外部组件依赖、故障和退出细节见[团队服务器架构](team-server-architecture.md)、[数据一致性与可靠性计划](data-consistency-and-reliability.md)和[前端与 AI 访问规划](frontend-and-ai-access.md)。
