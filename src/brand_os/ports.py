"""定义领域核心依赖的版本化存储端口。"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Protocol, Sequence

from .domain import (
    Actor,
    ClassificationCandidate,
    CommandContext,
    CommandResult,
    MeetingIngestBatch,
    ProposalDraft,
    ProposalReopen,
    ProposalReview,
    RelationDraft,
    SourceRecord,
    SourceImportBatch,
)
from .task_packets import AgentRunRequest, RuntimeTaskDefinition, WorkModeSwitch


class CanonicalStorePort(Protocol):
    """正式事件、人工动作和当前投影的存储端口。"""

    def create_project(self, context: CommandContext, name: str) -> CommandResult: ...

    def register_source(self, context: CommandContext, source: SourceRecord) -> CommandResult: ...

    def import_source_batch(
        self, context: CommandContext, batch: SourceImportBatch
    ) -> CommandResult: ...

    def ingest_meeting_batch(
        self, context: CommandContext, batch: MeetingIngestBatch
    ) -> CommandResult: ...

    def record_candidate(
        self, context: CommandContext, candidate: ClassificationCandidate
    ) -> CommandResult: ...

    def create_proposal(self, context: CommandContext, proposal: ProposalDraft) -> CommandResult: ...

    def add_relation(self, context: CommandContext, relation: RelationDraft) -> CommandResult: ...

    def review_proposal(self, context: CommandContext, review: ProposalReview) -> CommandResult: ...

    def reopen_proposal(self, context: CommandContext, reopen: ProposalReopen) -> CommandResult: ...

    def get_project_version(self, project_id: str) -> int: ...

    def get_current_state(self, project_id: str) -> Sequence[Mapping[str, object]]: ...

    def list_proposals(
        self, project_id: str, status: str | None = None
    ) -> Sequence[Mapping[str, object]]: ...

    def get_source(self, project_id: str, source_id: str) -> Mapping[str, object]: ...

    def get_source_import_report(
        self, project_id: str, batch_id: str
    ) -> Mapping[str, object]: ...

    def get_meeting_ingest_report(
        self, project_id: str, batch_id: str
    ) -> Mapping[str, object]: ...

    def list_source_versions(
        self, project_id: str, logical_source_id: str | None = None, *, current_only: bool = False
    ) -> Sequence[Mapping[str, object]]: ...

    def list_source_aliases(self, project_id: str) -> Sequence[Mapping[str, object]]: ...

    def list_source_gaps(self, project_id: str) -> Sequence[Mapping[str, object]]: ...

    def list_candidates(self, project_id: str) -> Sequence[Mapping[str, object]]: ...

    def list_relations(self, project_id: str) -> Sequence[Mapping[str, object]]: ...

    def list_human_actions(self, project_id: str) -> Sequence[Mapping[str, object]]: ...

    def get_proposal_history(
        self, project_id: str, proposal_id: str
    ) -> Mapping[str, object]: ...

    def list_proposal_supersessions(
        self, project_id: str
    ) -> Sequence[Mapping[str, object]]: ...

    def rebuild_state_projection(self, project_id: str) -> int: ...

    def rebuild_proposal_lifecycle(self, project_id: str) -> int: ...

    @property
    def schema_version(self) -> int: ...

    def quick_check(self) -> bool: ...


class CanonicalBackupPort(Protocol):
    """正式状态在线备份与恢复端口。"""

    def create(self) -> str: ...

    def restore(self, backup_id: str, destination: Path) -> Path: ...


class EvidenceQueryPort(Protocol):
    """当前决定、开放问题、关系与稳定证据链的只读端口。"""

    def list_decisions(
        self,
        project_id: str,
        *,
        as_of: str | None = None,
        include_inactive: bool = False,
    ) -> Sequence[Mapping[str, object]]: ...

    def list_open_questions(
        self,
        project_id: str,
        *,
        as_of: str | None = None,
        include_inactive: bool = False,
    ) -> Sequence[Mapping[str, object]]: ...

    def get_evidence_chain(
        self,
        project_id: str,
        item_type: str,
        item_id: str,
        *,
        as_of: str | None = None,
    ) -> Mapping[str, object]: ...

    def resolve_evidence_ref(
        self, project_id: str, evidence_ref: str
    ) -> Mapping[str, object]: ...

    def query_relations(
        self,
        project_id: str,
        *,
        subject_type: str | None = None,
        subject_id: str | None = None,
        relation_types: Sequence[str] | None = None,
        as_of: str | None = None,
        include_inactive: bool = False,
    ) -> Sequence[Mapping[str, object]]: ...


class TaskPacketPort(Protocol):
    """任务定义、分层上下文、人工模式切换和运行留痕端口。"""

    def register_runtime_task(
        self,
        project_id: str,
        actor: Actor,
        task: RuntimeTaskDefinition,
        *,
        idempotency_key: str,
    ) -> Mapping[str, object]: ...

    def switch_work_mode(
        self,
        project_id: str,
        actor: Actor,
        switch: WorkModeSwitch,
        *,
        idempotency_key: str,
    ) -> Mapping[str, object]: ...

    def build_task_packet(
        self,
        project_id: str,
        task_id: str,
        actor: Actor,
        *,
        expected_state_version: int | None = None,
    ) -> Mapping[str, object]: ...

    def get_task_packet(self, project_id: str, packet_id: str) -> Mapping[str, object]: ...

    def get_task_packet_layer(
        self, project_id: str, packet_id: str, layer: str
    ) -> Mapping[str, object]: ...

    def validate_task_packet(self, project_id: str, packet_id: str) -> Mapping[str, object]: ...

    def record_agent_run(
        self, project_id: str, actor: Actor, request: AgentRunRequest
    ) -> Mapping[str, object]: ...

    def get_agent_run(self, project_id: str, run_id: str) -> Mapping[str, object]: ...


class LocalAccessStorePort(CanonicalStorePort, EvidenceQueryPort, TaskPacketPort, Protocol):
    """本地 CLI/MCP 应用层所需的最小组合端口。"""
