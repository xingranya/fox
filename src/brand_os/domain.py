"""定义本地权威状态端口使用的领域命令与值对象。"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
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
SOURCE_ALIAS_KINDS = {"legacy_id", "reserved_id", "path"}
SOURCE_ALIAS_STATUSES = {"active", "deprecated", "reserved"}
SOURCE_GAP_STATUSES = {"KNOWN_SOURCE_GAP", "PARTIALLY_RESOLVED", "RESOLVED"}
MEETING_MODES = {"EXPLORATION", "EVALUATION", "DECISION", "SYNC", "MIXED", "UNKNOWN"}
MEETING_ITEM_STATUSES = {"working", "preferred", "tentative", "proposed", "verified"}
DATE_KINDS = {
    "EXTERNAL_DEADLINE",
    "INTERNAL_TARGET",
    "REVIEW_CHECKPOINT",
    "TENTATIVE_DATE",
    "UNKNOWN",
}
SOURCE_VERIFICATION_STATUSES = {"verified", "unverified", "fixture_only"}


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


def _parse_timestamp(value: str, field: str) -> datetime:
    """校验带时区的 ISO 8601 时间，避免有效期依赖本机时区。"""

    _require_text(value, field)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} 必须是 ISO 8601 时间") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} 必须包含时区")
    return parsed


def imported_source_version_id(logical_source_id: str, sha256: str) -> str:
    """根据逻辑来源和内容哈希生成稳定版本 ID。"""

    digest = hashlib.sha256(f"{logical_source_id}\0{sha256}".encode("utf-8")).hexdigest()
    return f"SV-{digest[:24].upper()}"


def legacy_source_version_id(source_id: str, sha256: str) -> str:
    """生成与 v3 数据迁移一致的单版本来源 ID。"""

    return f"LEGACY-{source_id}@{sha256[:16]}"


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
class SourceAliasRecord:
    """保留旧 ID、废弃保号或来源路径别名。"""

    alias_id: str
    alias_kind: str
    status: str

    def __post_init__(self) -> None:
        _require_text(self.alias_id, "alias_id")
        if self.alias_kind not in SOURCE_ALIAS_KINDS:
            raise ValueError("alias_kind 无效")
        if self.status not in SOURCE_ALIAS_STATUSES:
            raise ValueError("alias status 无效")


@dataclass(frozen=True, slots=True)
class SourceGapRecord:
    """记录已知但未取得、未核验或只完成部分核验的资料缺口。"""

    gap_id: str
    status: str
    description: str
    scope: str
    evidence_ref: str

    def __post_init__(self) -> None:
        for value, field in (
            (self.gap_id, "gap_id"),
            (self.description, "description"),
            (self.scope, "scope"),
            (self.evidence_ref, "evidence_ref"),
        ):
            _require_text(value, field)
        if self.status not in SOURCE_GAP_STATUSES:
            raise ValueError("gap status 无效")


@dataclass(frozen=True, slots=True)
class SourceImportRecord:
    """导入批次中的一个不可变来源版本。"""

    logical_source_id: str
    sha256: str
    relative_path: str
    source_role: str
    confidentiality: str | None = None
    size_bytes: int | None = None
    media_type: str | None = None
    status: str = "observed"
    version_label: str | None = None
    aliases: tuple[SourceAliasRecord, ...] = ()
    supersedes_sha256: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for value, field in (
            (self.logical_source_id, "logical_source_id"),
            (self.relative_path, "relative_path"),
            (self.source_role, "source_role"),
            (self.status, "status"),
        ):
            _require_text(value, field)
        if not SHA256_PATTERN.fullmatch(self.sha256):
            raise ValueError("sha256 必须是完整的小写 SHA-256")
        if self.confidentiality is not None and self.confidentiality not in CONFIDENTIALITY_LEVELS:
            raise ValueError("confidentiality 必须为空或 P0-P3")
        if self.size_bytes is not None and self.size_bytes < 0:
            raise ValueError("size_bytes 不能小于 0")
        relative = Path(self.relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("relative_path 必须是安全相对路径")
        if self.media_type is not None:
            _require_text(self.media_type, "media_type")
        if self.version_label is not None:
            _require_text(self.version_label, "version_label")
        if any(not SHA256_PATTERN.fullmatch(item) for item in self.supersedes_sha256):
            raise ValueError("supersedes_sha256 必须全部是完整的小写 SHA-256")


@dataclass(frozen=True, slots=True)
class SourceImportBatch:
    """一个已标准化、可幂等对账的来源 Manifest。"""

    manifest_sha256: str
    import_digest: str
    manifest_schema_version: str
    origin_ref: str
    records: tuple[SourceImportRecord, ...]
    gaps: tuple[SourceGapRecord, ...] = ()
    snapshot_at: str | None = None

    def __post_init__(self) -> None:
        if not SHA256_PATTERN.fullmatch(self.manifest_sha256):
            raise ValueError("manifest_sha256 必须是完整的小写 SHA-256")
        if not SHA256_PATTERN.fullmatch(self.import_digest):
            raise ValueError("import_digest 必须是完整的小写 SHA-256")
        for value, field in (
            (self.manifest_schema_version, "manifest_schema_version"),
            (self.origin_ref, "origin_ref"),
        ):
            _require_text(value, field)
        if self.snapshot_at is not None:
            _require_text(self.snapshot_at, "snapshot_at")

        versions_by_source: dict[str, str] = {}
        aliases: dict[str, str] = {}
        for record in self.records:
            previous_hash = versions_by_source.setdefault(record.logical_source_id, record.sha256)
            if previous_hash != record.sha256:
                raise ValueError("同一批次不能为同一逻辑来源提供两个不同内容版本")
            for alias in record.aliases:
                previous_source = aliases.setdefault(alias.alias_id, record.logical_source_id)
                if previous_source != record.logical_source_id:
                    raise ValueError("同一别名不能映射到两个逻辑来源")
        gap_ids = [gap.gap_id for gap in self.gaps]
        if len(gap_ids) != len(set(gap_ids)):
            raise ValueError("同一批次不能重复登记 gap_id")


@dataclass(frozen=True, slots=True)
class MeetingSegment:
    """保存会议原话、定位和转写质量，不把摘要当原话。"""

    segment_id: str
    locator: str
    quote: str
    mode: str
    mode_confidence: float
    speaker: str | None = None
    spoken_at: str | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    context: str | None = None
    transcript_confidence: float | None = None

    def __post_init__(self) -> None:
        for value, field in (
            (self.segment_id, "segment_id"),
            (self.locator, "locator"),
            (self.quote, "quote"),
        ):
            _require_text(value, field)
        if self.mode not in MEETING_MODES:
            raise ValueError("segment mode 不在冻结词表中")
        if not 0 <= self.mode_confidence <= 1:
            raise ValueError("mode_confidence 必须位于 0-1")
        for value, field in (
            (self.speaker, "speaker"),
            (self.spoken_at, "spoken_at"),
            (self.context, "context"),
        ):
            if value is not None:
                _require_text(value, field)
        if (self.start_ms is None) != (self.end_ms is None):
            raise ValueError("start_ms 与 end_ms 必须同时提供或同时省略")
        if self.start_ms is not None and (
            self.start_ms < 0 or self.end_ms is None or self.end_ms < self.start_ms
        ):
            raise ValueError("会议片段时间范围无效")
        if self.transcript_confidence is not None and not 0 <= self.transcript_confidence <= 1:
            raise ValueError("transcript_confidence 必须位于 0-1")

    @property
    def has_time_position(self) -> bool:
        """判断片段是否具有可复核的发言时间位置。"""

        return self.spoken_at is not None or self.start_ms is not None


@dataclass(frozen=True, slots=True)
class MeetingInterpretationItem:
    """保存模型建议类型和经过规则保护后的工作层分类。"""

    item_id: str
    suggested_type: str
    classification: str
    status: str
    summary: str
    scope: str
    evidence_segment_ids: tuple[str, ...]
    confidence: float
    reason: str
    date_kind: str | None = None
    decision_actor: str | None = None
    decision_verb: str | None = None
    state_difference: str | None = None
    normalization_reason: str | None = None
    requires_human_confirmation: bool = True

    def __post_init__(self) -> None:
        for value, field in (
            (self.item_id, "item_id"),
            (self.summary, "summary"),
            (self.scope, "scope"),
            (self.reason, "reason"),
        ):
            _require_text(value, field)
        if self.suggested_type not in INFORMATION_TYPES:
            raise ValueError("suggested_type 不在 Phase 0 冻结词表中")
        if self.classification not in INFORMATION_TYPES:
            raise ValueError("classification 不在 Phase 0 冻结词表中")
        if self.status not in MEETING_ITEM_STATUSES:
            raise ValueError("会议解释状态无效")
        if not self.evidence_segment_ids or any(
            not segment_id.strip() for segment_id in self.evidence_segment_ids
        ):
            raise ValueError("会议解释项必须引用至少一个原话片段")
        if len(self.evidence_segment_ids) != len(set(self.evidence_segment_ids)):
            raise ValueError("会议解释项不能重复引用同一片段")
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence 必须位于 0-1")
        if not self.requires_human_confirmation:
            raise ValueError("会议解释项必须等待人工确认")
        if self.classification == "TARGET_DATE" and self.date_kind not in DATE_KINDS:
            raise ValueError("TARGET_DATE 必须包含 date_kind")
        if self.date_kind is not None and self.date_kind not in DATE_KINDS:
            raise ValueError("date_kind 无效")
        if self.classification == "DECISION_CANDIDATE":
            if self.decision_actor is None or self.decision_verb is None:
                raise ValueError("DECISION_CANDIDATE 必须包含决定人和决定动词")
            if self.state_difference is None:
                raise ValueError("DECISION_CANDIDATE 必须说明与当前状态的差异")
        for value, field in (
            (self.decision_actor, "decision_actor"),
            (self.decision_verb, "decision_verb"),
            (self.state_difference, "state_difference"),
            (self.normalization_reason, "normalization_reason"),
        ):
            if value is not None:
                _require_text(value, field)


@dataclass(frozen=True, slots=True)
class MeetingConflictCandidate:
    """描述会议候选与某条人工确认状态之间的待确认冲突。"""

    conflict_id: str
    item_id: str
    state_item_type: str
    state_item_id: str
    reason: str
    evidence_segment_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        for value, field in (
            (self.conflict_id, "conflict_id"),
            (self.item_id, "item_id"),
            (self.state_item_type, "state_item_type"),
            (self.state_item_id, "state_item_id"),
            (self.reason, "reason"),
        ):
            _require_text(value, field)
        if not self.evidence_segment_ids or any(
            not segment_id.strip() for segment_id in self.evidence_segment_ids
        ):
            raise ValueError("冲突候选必须引用至少一个会议片段")


@dataclass(frozen=True, slots=True)
class MeetingIngestBatch:
    """一个与来源版本及基础状态绑定的会议增量批次。"""

    meeting_id: str
    title: str
    occurred_at: str | None
    participants: tuple[str, ...]
    logical_source_id: str
    source_version_id: str
    source_sha256: str
    source_verification: str
    base_state_version: int
    meeting_mode: str
    mode_confidence: float
    content_sha256: str
    ingest_digest: str
    segments: tuple[MeetingSegment, ...]
    items: tuple[MeetingInterpretationItem, ...]
    conflicts: tuple[MeetingConflictCandidate, ...] = ()

    def __post_init__(self) -> None:
        for value, field in (
            (self.meeting_id, "meeting_id"),
            (self.title, "title"),
            (self.logical_source_id, "logical_source_id"),
            (self.source_version_id, "source_version_id"),
        ):
            _require_text(value, field)
        if self.occurred_at is not None:
            _require_text(self.occurred_at, "occurred_at")
        if any(not participant.strip() for participant in self.participants):
            raise ValueError("participants 不能包含空值")
        if len(self.participants) != len(set(self.participants)):
            raise ValueError("participants 不能重复")
        if not SHA256_PATTERN.fullmatch(self.source_sha256):
            raise ValueError("source_sha256 必须是完整的小写 SHA-256")
        if not SHA256_PATTERN.fullmatch(self.content_sha256):
            raise ValueError("content_sha256 必须是完整的小写 SHA-256")
        if not SHA256_PATTERN.fullmatch(self.ingest_digest):
            raise ValueError("ingest_digest 必须是完整的小写 SHA-256")
        if self.source_verification not in SOURCE_VERIFICATION_STATUSES:
            raise ValueError("source_verification 无效")
        if self.base_state_version < 0:
            raise ValueError("base_state_version 不能小于 0")
        if self.meeting_mode not in MEETING_MODES:
            raise ValueError("meeting_mode 不在冻结词表中")
        if not 0 <= self.mode_confidence <= 1:
            raise ValueError("mode_confidence 必须位于 0-1")
        if not self.segments:
            raise ValueError("会议批次必须包含至少一个原话片段")

        segment_ids = {segment.segment_id for segment in self.segments}
        if len(segment_ids) != len(self.segments):
            raise ValueError("同一会议不能重复 segment_id")
        known_modes = {segment.mode for segment in self.segments if segment.mode != "UNKNOWN"}
        if len(known_modes) > 1 and self.meeting_mode != "MIXED":
            raise ValueError("包含多种片段模式的会议必须标记为 MIXED")
        if len(known_modes) == 1 and self.meeting_mode not in {*known_modes, "MIXED", "UNKNOWN"}:
            raise ValueError("会议模式与片段模式不一致")

        item_ids = {item.item_id for item in self.items}
        if len(item_ids) != len(self.items):
            raise ValueError("同一会议不能重复 item_id")
        for item in self.items:
            if not set(item.evidence_segment_ids).issubset(segment_ids):
                raise ValueError("会议解释项引用了不存在的片段")
        conflict_ids = {conflict.conflict_id for conflict in self.conflicts}
        if len(conflict_ids) != len(self.conflicts):
            raise ValueError("同一批次不能重复 conflict_id")
        for conflict in self.conflicts:
            if conflict.item_id not in item_ids:
                raise ValueError("冲突候选引用了不存在的会议解释项")
            if not set(conflict.evidence_segment_ids).issubset(segment_ids):
                raise ValueError("冲突候选引用了不存在的片段")


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
    supersedes_proposal_id: str | None = None
    source_meeting_item_id: str | None = None
    valid_from: str | None = None
    valid_until: str | None = None

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
        if self.proposal_kind == "supersede":
            if self.supersedes_proposal_id is None:
                raise ValueError("supersede Proposal 必须指定 supersedes_proposal_id")
            if self.before is None:
                raise ValueError("supersede Proposal 必须保存被替代状态的旧值")
        elif self.supersedes_proposal_id is not None:
            raise ValueError("只有 supersede Proposal 可以指定 supersedes_proposal_id")
        for value, field in (
            (self.supersedes_proposal_id, "supersedes_proposal_id"),
            (self.source_meeting_item_id, "source_meeting_item_id"),
        ):
            if value is not None:
                _require_text(value, field)
        valid_from = (
            _parse_timestamp(self.valid_from, "valid_from")
            if self.valid_from is not None
            else None
        )
        valid_until = (
            _parse_timestamp(self.valid_until, "valid_until")
            if self.valid_until is not None
            else None
        )
        if valid_from is not None and valid_until is not None and valid_until <= valid_from:
            raise ValueError("valid_until 必须晚于 valid_from")


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
class ProposalReopen:
    """记录 Fox 以新证据重开已驳回 Proposal 的动作。"""

    proposal_id: str
    reason: str
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_text(self.proposal_id, "proposal_id")
        _require_text(self.reason, "reason")
        if not self.evidence_refs or any(not item.strip() for item in self.evidence_refs):
            raise ValueError("重开 Proposal 必须提供新证据")


@dataclass(frozen=True, slots=True)
class CommandResult:
    """返回命令生效后的版本与事件。"""

    project_version: int
    event_id: str
    resource_id: str
    replayed: bool = False
