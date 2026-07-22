"""定义领域核心依赖的版本化存储端口。"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Protocol, Sequence

from .domain import (
    ClassificationCandidate,
    CommandContext,
    CommandResult,
    MeetingIngestBatch,
    ProposalDraft,
    ProposalReview,
    RelationDraft,
    SourceRecord,
    SourceImportBatch,
)


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

    def get_project_version(self, project_id: str) -> int: ...

    def get_current_state(self, project_id: str) -> Sequence[Mapping[str, object]]: ...

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

    def rebuild_state_projection(self, project_id: str) -> int: ...


class CanonicalBackupPort(Protocol):
    """正式状态在线备份与恢复端口。"""

    def create(self) -> str: ...

    def restore(self, backup_id: str, destination: Path) -> Path: ...
