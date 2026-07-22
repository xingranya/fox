"""定义 Task Packet、品牌角色、工作模式和 Agent 运行请求。"""

from __future__ import annotations

from dataclasses import dataclass

from .domain import ActorKind


TASK_PACKET_SCHEMA_VERSION = "task-packet.v2"
TASK_PACKET_ASSEMBLY_VERSION = "task-packet-assembly.v1"
RUNTIME_RUN_SCHEMA_VERSION = "runtime-run.v1"
RUNTIME_MODE_SWITCH_SCHEMA_VERSION = "runtime-mode-switch.v2"
BRAND_AGENT_PROTOCOL_VERSION = "brand-agent-constitution.v1"
WORK_MODE_PROTOCOL_VERSION = "work-mode-protocol.v1"
TAXONOMY_VERSION = "brand-taxonomy.v1"
EVIDENCE_QUERY_VERSION = "evidence-query.v1"

WORK_MODES = {"EXPLORATION", "EVALUATION", "DECISION", "EXECUTION"}
BRAND_ROLES = {
    "BRAND_STRATEGIST",
    "BRAND_RESEARCHER",
    "CREATIVE_PARTNER",
    "EXECUTION_PARTNER",
}
NETWORK_POLICIES = {"deny", "local_only", "approved_external"}
EVIDENCE_PURPOSES = {"support", "oppose", "source", "conflict", "context"}
CONTEXT_ITEM_TYPES = {
    "FACT",
    "DECISION",
    "CONSTRAINT",
    "ACTION",
    "VIEW",
    "PREFERENCE",
    "HYPOTHESIS",
    "OPTION",
    "TENDENCY",
    "TARGET_DATE",
    "OPEN",
    "PROPOSAL",
}

ROLE_CONTRACTS = {
    "BRAND_STRATEGIST": {
        "label": "品牌策略",
        "must": ["围绕本轮问题给出有取舍的判断", "区分证据、假设和建议"],
        "must_not": ["批准项目事实", "替 Fox 决定工作模式"],
    },
    "BRAND_RESEARCHER": {
        "label": "品牌研究",
        "must": ["先核对来源再下结论", "把证据缺口写明"],
        "must_not": ["用模型记忆补齐资料", "把相关性当成因果"],
    },
    "CREATIVE_PARTNER": {
        "label": "创意协作",
        "must": ["使用已确认事实", "让表达适合真实受众"],
        "must_not": ["为了文案效果虚构产品事实", "把废案带回当前方向"],
    },
    "EXECUTION_PARTNER": {
        "label": "执行协作",
        "must": ["服从已批准方向和交付要求", "明确未满足项"],
        "must_not": ["重写已批准战略", "把暂定时间改成硬截止"],
    },
}

MODE_CONTRACTS = {
    "EXPLORATION": {
        "label": "探索",
        "must": ["保留真正不同的选择和代价", "标出待验证问题"],
        "must_not": ["强行收成唯一答案", "直接升级为正式决定"],
    },
    "EVALUATION": {
        "label": "评估",
        "must": ["按同一尺度比较选择", "分开记录偏好和事实"],
        "must_not": ["替 Fox 选择方案", "把审美偏好写成永久约束"],
    },
    "DECISION": {
        "label": "决策准备",
        "must": ["给出可审查的建议和依据", "保留选择代价"],
        "must_not": ["代替人工批准", "省略冲突和证据缺口"],
    },
    "EXECUTION": {
        "label": "执行",
        "must": ["严格使用已批准方向", "按交付标准完成产物"],
        "must_not": ["重新发明战略", "重新启用已关闭方案"],
    },
}

VETOES = (
    "虚构产品事实",
    "把讨论升级成决定",
    "把暂定日期写成死线",
    "把过期方案当成当前方向",
    "重要结论不能回到证据",
    "未经人确认自动改变项目状态",
    "在探索模式下强行制造唯一答案",
)


def _require_text(value: str, field: str) -> None:
    if not value.strip():
        raise ValueError(f"{field} 不能为空")


def _require_unique_texts(values: tuple[str, ...], field: str, *, allow_empty: bool) -> None:
    if not allow_empty and not values:
        raise ValueError(f"{field} 不能为空")
    if any(not value.strip() for value in values):
        raise ValueError(f"{field} 不能包含空值")
    if len(values) != len(set(values)):
        raise ValueError(f"{field} 不能重复")


@dataclass(frozen=True, slots=True)
class TaskContextRef:
    """指定本轮真正需要装配的状态对象。"""

    item_type: str
    item_id: str

    def __post_init__(self) -> None:
        normalized_type = self.item_type.upper()
        if normalized_type not in CONTEXT_ITEM_TYPES:
            raise ValueError("item_type 不在 Task Packet 支持范围内")
        _require_text(self.item_id, "item_id")
        object.__setattr__(self, "item_type", normalized_type)


@dataclass(frozen=True, slots=True)
class TaskEvidenceRef:
    """指定本轮需要打开的证据及用途。"""

    evidence_ref: str
    purpose: str

    def __post_init__(self) -> None:
        _require_text(self.evidence_ref, "evidence_ref")
        if self.purpose not in EVIDENCE_PURPOSES:
            raise ValueError("purpose 不在证据用途词表中")


@dataclass(frozen=True, slots=True)
class RuntimeTaskDefinition:
    """由 Fox 登记的任务目标、角色、模式与上下文范围。"""

    task_id: str
    goal: str
    role: str
    work_mode: str
    deliverables: tuple[str, ...]
    non_goals: tuple[str, ...]
    context_refs: tuple[TaskContextRef, ...]
    evidence_refs: tuple[TaskEvidenceRef, ...]
    known_gap_ids: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    network: str
    model_allowlist: tuple[str, ...]
    output_schema_ref: str
    acceptance_criteria: tuple[str, ...]
    confidentiality_ceiling: str = "P2"
    max_evidence_items: int = 20

    def __post_init__(self) -> None:
        for value, field in (
            (self.task_id, "task_id"),
            (self.goal, "goal"),
            (self.output_schema_ref, "output_schema_ref"),
        ):
            _require_text(value, field)
        if self.role not in BRAND_ROLES:
            raise ValueError("role 不在品牌角色词表中")
        if self.work_mode not in WORK_MODES:
            raise ValueError("work_mode 不在工作模式词表中")
        if self.network not in NETWORK_POLICIES:
            raise ValueError("network 策略无效")
        if self.confidentiality_ceiling not in {"P0", "P1", "P2", "P3"}:
            raise ValueError("confidentiality_ceiling 必须是 P0-P3")
        if not 1 <= self.max_evidence_items <= 100:
            raise ValueError("max_evidence_items 必须位于 1-100")
        for values, field, allow_empty in (
            (self.deliverables, "deliverables", False),
            (self.non_goals, "non_goals", True),
            (self.known_gap_ids, "known_gap_ids", True),
            (self.allowed_tools, "allowed_tools", True),
            (self.model_allowlist, "model_allowlist", False),
            (self.acceptance_criteria, "acceptance_criteria", False),
        ):
            _require_unique_texts(values, field, allow_empty=allow_empty)
        context_keys = [(value.item_type, value.item_id) for value in self.context_refs]
        if len(context_keys) != len(set(context_keys)):
            raise ValueError("context_refs 不能重复")
        evidence_keys = [value.evidence_ref for value in self.evidence_refs]
        if len(evidence_keys) != len(set(evidence_keys)):
            raise ValueError("evidence_refs 不能重复")


@dataclass(frozen=True, slots=True)
class WorkModeSwitch:
    """描述一次只能由 Fox 执行的工作模式切换。"""

    task_id: str
    to_mode: str
    reason: str
    task_scope: str
    expected_task_revision: int
    suggested_by_runtime: str | None = None

    def __post_init__(self) -> None:
        for value, field in (
            (self.task_id, "task_id"),
            (self.reason, "reason"),
            (self.task_scope, "task_scope"),
        ):
            _require_text(value, field)
        if self.to_mode not in WORK_MODES:
            raise ValueError("to_mode 不在工作模式词表中")
        if self.expected_task_revision < 1:
            raise ValueError("expected_task_revision 必须大于 0")
        if self.suggested_by_runtime is not None:
            _require_text(self.suggested_by_runtime, "suggested_by_runtime")


@dataclass(frozen=True, slots=True)
class AgentRunRequest:
    """登记一次消费固定 Task Packet 的 Agent 运行。"""

    run_id: str
    packet_id: str
    expected_packet_hash: str
    runtime_id: str
    runtime_version: str
    model_id: str
    model_version: str
    idempotency_key: str

    def __post_init__(self) -> None:
        for value, field in (
            (self.run_id, "run_id"),
            (self.packet_id, "packet_id"),
            (self.runtime_id, "runtime_id"),
            (self.runtime_version, "runtime_version"),
            (self.model_id, "model_id"),
            (self.model_version, "model_version"),
            (self.idempotency_key, "idempotency_key"),
        ):
            _require_text(value, field)
        if len(self.expected_packet_hash) != 64 or any(
            character not in "0123456789abcdef" for character in self.expected_packet_hash
        ):
            raise ValueError("expected_packet_hash 必须是完整的小写 SHA-256")


ALLOWED_RUN_STARTER_KINDS = {
    ActorKind.HUMAN,
    ActorKind.WORKFLOW,
    ActorKind.SYSTEM,
}
