"""定义领域核心依赖的版本化存储端口。"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Protocol, Sequence

from .domain import (
    ClassificationCandidate,
    CommandContext,
    CommandResult,
    ProposalDraft,
    ProposalReview,
    RelationDraft,
    SourceRecord,
)


class CanonicalStorePort(Protocol):
    """正式事件、人工动作和当前投影的存储端口。"""

    def create_project(self, context: CommandContext, name: str) -> CommandResult: ...

    def register_source(self, context: CommandContext, source: SourceRecord) -> CommandResult: ...

    def record_candidate(
        self, context: CommandContext, candidate: ClassificationCandidate
    ) -> CommandResult: ...

    def create_proposal(self, context: CommandContext, proposal: ProposalDraft) -> CommandResult: ...

    def add_relation(self, context: CommandContext, relation: RelationDraft) -> CommandResult: ...

    def review_proposal(self, context: CommandContext, review: ProposalReview) -> CommandResult: ...

    def get_project_version(self, project_id: str) -> int: ...

    def get_current_state(self, project_id: str) -> Sequence[Mapping[str, object]]: ...

    def get_source(self, project_id: str, source_id: str) -> Mapping[str, object]: ...

    def list_candidates(self, project_id: str) -> Sequence[Mapping[str, object]]: ...

    def list_relations(self, project_id: str) -> Sequence[Mapping[str, object]]: ...

    def list_human_actions(self, project_id: str) -> Sequence[Mapping[str, object]]: ...

    def rebuild_state_projection(self, project_id: str) -> int: ...


class CanonicalBackupPort(Protocol):
    """正式状态在线备份与恢复端口。"""

    def create(self) -> str: ...

    def restore(self, backup_id: str, destination: Path) -> Path: ...
