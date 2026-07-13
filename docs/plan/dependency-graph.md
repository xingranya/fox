# 任务依赖图

## 读取说明

- 实线表示必须完成的依赖；标有“仅 Go”的边表示必须先通过人工价值门。
- Phase 0-2 是当前批准的产品验证主线；Phase 3 只有 F2.8=Go 才启动。
- Phase 3 的 PostgreSQL、S3、OIDC、HA、OpenWork 深度团队化和五组件均是决策候选，不表示已批准实施。
- Lane 只表示可并行的所有权面；实际启动仍以每个任务的依赖为准。
- 旧 42 项的逐项去向见[任务分解](task-breakdown.md#旧-42-项追踪表)。

## 全量依赖与并行 Lane

```mermaid
flowchart TB
    subgraph P0["Phase 0：边界、协议与黄金测试"]
        subgraph P0A["Lane A：范围与就绪门"]
            F0_1["F0.1 两条需求线与本地 MVP 边界"]
            F0_7["F0.7 本地纵切、Schema/Port 与就绪门"]
        end
        subgraph P0B["Lane B：资料与黄金集"]
            F0_2["F0.2 鸿日资料与 V5 样本清单"]
            F0_5["F0.5 10-20 黄金用例与一票否决"]
        end
        subgraph P0C["Lane C：分类与运行协议"]
            F0_3["F0.3 会议/信息/时间分类标准"]
            F0_4["F0.4 品牌 Agent 宪法与模式协议"]
        end
        subgraph P0D["Lane D：品牌质量"]
            F0_6["F0.6 BrandBench 匿名评审基线"]
        end

        F0_1 --> F0_2
        F0_1 --> F0_3
        F0_3 --> F0_4
        F0_2 --> F0_5
        F0_3 --> F0_5
        F0_4 --> F0_5
        F0_4 --> F0_6
        F0_5 --> F0_6
        F0_1 --> F0_7
        F0_2 --> F0_7
        F0_3 --> F0_7
        F0_4 --> F0_7
        F0_5 --> F0_7
        F0_6 --> F0_7
    end

    subgraph P1["Phase 1：鸿日本地单用户原型"]
        subgraph P1A["Lane A：本地数据脊柱"]
            F1_1["F1.1 工作空间、只读原件与测试骨架"]
            F1_2["F1.2 SQLite、事件、投影与备份"]
            F1_3["F1.3 鸿日 V5 幂等导入与对账"]
        end
        subgraph P1B["Lane B：会议与状态"]
            F1_4["F1.4 会议增量解释与冲突候选"]
            F1_5["F1.5 当前状态、Proposal 与人工确认"]
        end
        subgraph P1C["Lane C：证据与 AI 入口"]
            F1_6["F1.6 关系查询、有效期与稳定回源"]
            F1_7["F1.7 Task Packet、角色/模式与运行留痕"]
            F1_8["F1.8 本地 CLI/MCP 与多模型适配"]
        end
        subgraph P1D["Lane D：人类界面"]
            F1_9["F1.9 本地查看与确认界面"]
        end
        subgraph P1E["Lane E：原型发布门"]
            F1_10["F1.10 黄金集与本地 E2E 验收"]
        end

        F1_1 --> F1_2
        F0_3 --> F1_2
        F1_2 --> F1_3
        F0_2 --> F1_3
        F1_3 --> F1_4
        F0_3 --> F1_4
        F1_2 --> F1_5
        F1_4 --> F1_5
        F1_3 --> F1_6
        F1_5 --> F1_6
        F1_5 --> F1_7
        F1_6 --> F1_7
        F0_4 --> F1_7
        F1_7 --> F1_8
        F1_5 --> F1_9
        F1_6 --> F1_9
        F1_8 --> F1_9
        F1_1 --> F1_10
        F1_2 --> F1_10
        F1_3 --> F1_10
        F1_4 --> F1_10
        F1_5 --> F1_10
        F1_6 --> F1_10
        F1_7 --> F1_10
        F1_8 --> F1_10
        F1_9 --> F1_10
    end

    subgraph P2["Phase 2：鸿日真实工作连续验证"]
        subgraph P2A["Lane A：真实增量与观测"]
            F2_1["F2.1 价值指标与观察基线"]
            F2_2["F2.2 连续新会议/资料增量"]
        end
        subgraph P2B["Lane B：理解与证据"]
            F2_3["F2.3 冷启动、状态与证据回归"]
        end
        subgraph P2C["Lane C：品牌工作闭环"]
            F2_4["F2.4 策略探索与选择代价"]
            H_SELECT{{"Fox 显式选择"}}
            F2_5["F2.5 已批准方向执行落地"]
        end
        subgraph P2D["Lane D：模型与品牌质量"]
            F2_6["F2.6 多模型一致性/成本/质量"]
            F2_7["F2.7 匿名评审与 BrandBench 更新"]
        end
        subgraph P2E["Lane E：价值门"]
            F2_8["F2.8 错误修订闭环与 Go/No-Go"]
        end

        F2_1 --> F2_2
        F2_1 --> F2_4
        F1_7 --> F2_4
        F2_4 --> H_SELECT
        H_SELECT --> F2_5
        F2_1 --> F2_6
        F1_8 --> F2_6
        F2_1 --> F2_3
        F2_2 --> F2_3
        F2_4 --> F2_7
        F2_5 --> F2_7
        F2_6 --> F2_7
        F2_2 --> F2_8
        F2_3 --> F2_8
        F2_4 --> F2_8
        F2_5 --> F2_8
        F2_6 --> F2_8
        F2_7 --> F2_8
    end

    subgraph P3["Phase 3：团队服务器化决策门"]
        F3_1{{"F3.1 团队需求与 FoxWork 合线门"}}
        subgraph P3A["Lane A：数据与存储候选"]
            F3_2["F3.2 SQLite/PostgreSQL/S3/同步候选"]
        end
        subgraph P3B["Lane B：身份与可靠性候选"]
            F3_3["F3.3 OIDC/RBAC/RLS/并发/HA 候选"]
        end
        subgraph P3C["Lane C：客户端与 Agent 候选"]
            F3_4["F3.4 OpenWork 深度团队化候选"]
        end
        subgraph P3D["Lane D：外部组件候选"]
            F3_5["F3.5 Zvec/Notebook/Nubase/FlowLong/Dify"]
        end
        subgraph P3E["Lane E：部署档位与结论"]
            F3_6["F3.6 成本、SLO、运维与迁移候选"]
            F3_7{{"F3.7 服务器化 Go/No-Go ADR + 新 SPEC"}}
        end

        F2_8 -->|"仅 Go"| F3_1
        F3_1 -->|"允许评估"| F3_2
        F3_1 -->|"允许评估"| F3_3
        F3_1 -->|"允许评估"| F3_4
        F3_1 -->|"允许评估"| F3_5
        F3_2 --> F3_6
        F3_3 --> F3_6
        F3_4 --> F3_6
        F3_5 --> F3_6
        F3_1 --> F3_7
        F3_2 --> F3_7
        F3_3 --> F3_7
        F3_4 --> F3_7
        F3_5 --> F3_7
        F3_6 --> F3_7
    end

    F0_7 ==> F1_1
    F1_10 ==> F2_1
    F2_8 -. "No-Go / 延长：保持本地并返回 Phase 0-2" .-> F0_5
```

## 当前本地数据流

```mermaid
flowchart LR
    RAW["鸿日只读原件 + Manifest/哈希"]
    INGEST["增量导入/分段"]
    CANDIDATE["事实、观点、假设、选项、倾向、行动候选"]
    REVIEW{{"Fox 确认/修改/驳回"}}
    EVENT["本地确认事件"]
    STATE["当前状态与关系"]
    PACKET["最小 Task Packet"]
    MODEL["Codex / Claude / 其他模型"]
    OUTPUT["Artifact / Proposal"]

    RAW --> INGEST --> CANDIDATE --> REVIEW
    REVIEW -->|"确认"| EVENT --> STATE --> PACKET --> MODEL --> OUTPUT
    OUTPUT --> REVIEW
    REVIEW -->|"驳回"| HISTORY["保留历史，不改当前状态"]
    STATE -. "稳定 ID 回源" .-> RAW
```

SQLite 保存单用户确认事件、当前投影和关系；FTS、摘要、模型输出和界面缓存是派生数据。Phase 1 不存在 PostgreSQL、S3、OIDC、Outbox、Dify 或其他服务器依赖。

## Phase 3 候选边界

```mermaid
flowchart LR
    VALUE{{"F2.8 本地价值 Go"}}
    TEAM{{"F3.1 真实团队需求成立"}}
    DATA["PostgreSQL / S3 / 同步"]
    AUTH["OIDC / RBAC / RLS / 审计"]
    OPS["并发 / HA / 灾备 / SLO"]
    CLIENT["OpenWork 深度团队化 / 远程 Agent"]
    EXT["Zvec / Open Notebook / Nubase / FlowLong / Dify"]
    ADR{{"F3.7 Go/No-Go + 独立实施 SPEC"}}

    VALUE -->|"Go"| TEAM
    TEAM --> DATA
    TEAM --> AUTH
    TEAM --> OPS
    TEAM --> CLIENT
    TEAM --> EXT
    DATA --> ADR
    AUTH --> ADR
    OPS --> ADR
    CLIENT --> ADR
    EXT --> ADR
```

从候选节点到 ADR 的连线表示“提供决策证据”，不是“自动采用”。F3.7=Go 后仍需新建实施 SPEC，旧 42 项不得直接恢复执行。

## 关键路径与并行窗口

主价值关键路径：

```text
F0.1 -> F0.3 -> F0.4 -> F0.5 -> F0.6 -> F0.7
-> F1.1 -> F1.2 -> F1.3 -> F1.4 -> F1.5 -> F1.6 -> F1.7 -> F1.8 -> F1.9 -> F1.10
-> F2.1 -> F2.2 -> F2.3 -> F2.8
```

品牌质量关键路径：`F0.4 -> F0.5 -> F0.6 -> F1.7 -> F1.10 -> F2.4 -> Fox 显式选择 -> F2.5 -> F2.7 -> F2.8`。

并行窗口：

- Phase 0：F0.2 与 F0.3/F0.4 并行；评分基线在黄金输入稳定后建立。
- Phase 1：F1.3 后可并行推进会议增量与只读证据关系；Task Packet 等状态/证据契约合并后推进。
- Phase 2：在同一确认状态版本上并行做冷启动/回查、策略探索和模型切换；执行任务必须等 Fox 选择。
- Phase 3：F3.1 放行后，数据、身份、客户端和外部组件四个候选面并行；F3.6 汇总，F3.7 决策。

## 阶段门与停止传播

| 门 | 通过条件 | 失败时 |
|:---|:---|:---|
| F0.7 实施就绪 | 边界、样本、分类、协议、黄金集和 BrandBench 均冻结 | 返回对应 Phase 0 任务，不写产品代码 |
| F1.10 原型门 | 八旅程可复核、七项一票否决为 0、本地恢复可用、Fox 可独立使用 | 保持 Phase 1，不进入真实工作主流程 |
| F2.8 价值门 | 连续真实工作、前后指标、匿名评审和错误修订支持 Go | 延长或 No-Go；不得用服务器建设补救价值缺口 |
| F3.1 团队需求门 | 真实多人场景、用户、共享动作与 FoxWork 合线结论明确 | 保持本地，不评估团队架构 |
| F3.7 服务器化门 | Fox 批准 ADR，收益、成本、风险、退出和迁移证据完整 | No-Go 或局部共享；不启动服务器实施 |

任一一票否决会使当前用例和阶段门失败，并阻断所有下游任务，直到完成 Fixture、修复和全量回归。
