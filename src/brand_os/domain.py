"""定义本地权威状态端口使用的领域命令与值对象。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Mapping


SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
INFORMATION_TYPES = {
    "FACT",
    "VIEW",
    "PREFERENCE",
    "HYPOTHESIS",
    "OPTION",
    "TENDENCY",
    "TARGET_DATE",
    "DECISION_CANDIDATE",
    "CONSTRAINT_CANDIDATE",
    "ACTION_CANDIDATE",
    "OPEN",
}
PROPOSAL_KINDS = {"create", "update", "supersede", "link", "flag_conflict"}
RELATION_TYPES = {
    "sourced_from",
    "raised_in",
    "supports",
    "opposes",
    "conflicts_with",
    "applies_to",
    "approved_by",
    "supersedes",
    "depends_on",
    "answers",
    "pending_confirmation",
}
CONFIDENTIALITY_LEVELS = {"P0", "P1", "P2", "P3"}


class ActorKind(StrEnum):
    """区分人、AI、工作流和系统操作。"""

    HUMAN = "HUMAN"
    AI = "AI"
    WORKFLOW = "WORKFLOW"
    SYSTEM = "SYSTEM"


class ReviewAction(StrEnum):
    """Fox 对 Proposal 可执行的最小动作。"""

    APPROVE = "approve"
    MODIFY_AND_APPROVE = "modify_and_approve"
    REJECT = "reject"


def _require_text(value: str, field: str) -> None:
    if not value.strip():
        raise ValueError(f"{field} 不能为空")


@dataclass(frozen=True, slots=True)
class Actor:
    """记录命令实际操作者，不复用模型或会话身份。"""

    kind: ActorKind
    actor_id: str

    def __post_init__(self) -> None:
        _require_text(self.actor_id, "actor_id")


@dataclass(frozen=True, slots=True)
class CommandContext:
    """为每次正式写请求携带幂等键和预期版本。"""

    project_id: str
    actor: Actor
    idempotency_key: str
    expected_version: int

    def __post_init__(self) -> None:
        _require_text(self.project_id, "project_id")
        _require_text(self.idempotency_key, "idempotency_key")
        if self.expected_version < 0:
            raise ValueError("expected_version 不能小于 0")


@dataclass(frozen=True, slots=True)
class SourceRecord:
    """只保存原件版本与定位元数据，不保存无来源正文。"""

    source_id: str
    sha256: str
    size: int
    relative_path: str
    source_role: str
    confidentiality: str
    status: str = "current"

    def __post_init__(self) -> None:
        _require_text(self.source_id, "source_id")
        _require_text(self.relative_path, "relative_path")
        _require_text(self.source_role, "source_role")
        _require_text(self.status, "status")
        if not SHA256_PATTERN.fullmatch(self.sha256):
            raise ValueError("sha256 必须是完整的小写 SHA-256")
        if self.size < 0:
            raise ValueError("size 不能小于 0")
        if self.confidentiality not in CONFIDENTIALITY_LEVELS:
            raise ValueError("confidentiality 必须是 P0-P3")
        relative = Path(self.relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("relative_path 必须位于工作空间内且不能包含上级跳转")


@dataclass(frozen=True, slots=True)
class ClassificationCandidate:
    """保存可回到原件位置的分类候选。"""

    candidate_id: str
    source_id: str
    source_sha256: str
    locator: str
    excerpt: str
    classification: str
    reasoning: str

    def __post_init__(self) -> None:
        for value, field in (
            (self.candidate_id, "candidate_id"),
            (self.source_id, "source_id"),
            (self.locator, "locator"),
            (self.excerpt, "excerpt"),
            (self.reasoning, "reasoning"),
        ):
            _require_text(value, field)
        if not SHA256_PATTERN.fullmatch(self.source_sha256):
            raise ValueError("source_sha256 必须是完整的小写 SHA-256")
        if self.classification not in INFORMATION_TYPES:
            raise ValueError("classification 不在 Phase 0 冻结词表中")


@dataclass(frozen=True, slots=True)
class ProposalDraft:
    """描述一个尚未生效的状态变化建议。"""

    proposal_id: str
    proposal_kind: str
    classification: str
    subject_id: str | None
    before: Mapping[str, object] | None
    after: Mapping[str, object]
    reason: str
    impact_scope: str
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        for value, field in (
            (self.proposal_id, "proposal_id"),
            (self.reason, "reason"),
            (self.impact_scope, "impact_scope"),
        ):
            _require_text(value, field)
        if self.proposal_kind not in PROPOSAL_KINDS:
            raise ValueError("proposal_kind 无效")
        if self.classification not in INFORMATION_TYPES:
            raise ValueError("classification 不在 Phase 0 冻结词表中")
        if not self.evidence_refs or any(not item.strip() for item in self.evidence_refs):
            raise ValueError("Proposal 必须至少有一个有效 evidence_ref")


@dataclass(frozen=True, slots=True)
class RelationDraft:
    """描述带证据的工作层关系。"""

    relation_id: str
    from_type: str
    from_id: str
    relation_type: str
    to_type: str
    to_id: str
    evidence_ref: str

    def __post_init__(self) -> None:
        for value, field in (
            (self.relation_id, "relation_id"),
            (self.from_type, "from_type"),
            (self.from_id, "from_id"),
            (self.to_type, "to_type"),
            (self.to_id, "to_id"),
            (self.evidence_ref, "evidence_ref"),
        ):
            _require_text(value, field)
        if self.relation_type not in RELATION_TYPES:
            raise ValueError("relation_type 不在冻结关系词表中")


@dataclass(frozen=True, slots=True)
class ProposalReview:
    """记录 Fox 的批准、修改后批准或驳回。"""

    proposal_id: str
    action: ReviewAction
    reason: str
    replacement_after: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        _require_text(self.proposal_id, "proposal_id")
        _require_text(self.reason, "reason")
        if self.action is ReviewAction.MODIFY_AND_APPROVE and self.replacement_after is None:
            raise ValueError("modify_and_approve 必须提供 replacement_after")
        if self.action is not ReviewAction.MODIFY_AND_APPROVE and self.replacement_after is not None:
            raise ValueError("只有 modify_and_approve 可以提供 replacement_after")


@dataclass(frozen=True, slots=True)
class CommandResult:
    """返回命令生效后的版本与事件。"""

    project_version: int
    event_id: str
    resource_id: str
    replayed: bool = False
