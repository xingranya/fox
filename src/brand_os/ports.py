"""定义领域核心依赖的版本化存储端口。"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, Iterator, Mapping, Protocol, Sequence

from .domain import (
    Actor,
    ActorKind,
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
from .object_evidence import (
    EvidenceAdmissionRequest,
    EvidenceTombstone,
    EvidenceUpload,
    EvidenceVersion,
    MultipartUploadInfo,
    ObjectInfo,
    ReconciliationReport,
)
from .identity import (
    AuthorizationTransaction,
    EmployeeSession,
    IdentityBinding,
    OidcTokenSet,
    SensitiveValue,
    VerifiedIdentity,
)
from .authorization import (
    AuthorizationDecision,
    ConfidentialityLevel,
    EmployeeProjectGrant,
    ProjectAction,
    ProjectPrincipal,
    ProjectRole,
    ServiceProjectGrant,
)

if TYPE_CHECKING:
    from .consistency import ConflictCode, ConflictReport


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

    def get_project(self, project_id: str) -> Mapping[str, object]: ...

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


class ConflictSnapshotPort(Protocol):
    """在一个一致读快照中生成正式写冲突差异。"""

    def capture_conflict(
        self,
        authorization: AuthorizationDecision,
        *,
        context: CommandContext,
        command_name: str,
        code: ConflictCode,
        reason: str,
        resource_type: str | None,
        resource_id: str | None,
        max_events: int,
    ) -> ConflictReport: ...


class CanonicalBackupPort(Protocol):
    """正式状态在线备份与恢复端口。"""

    def create(self) -> str: ...

    def restore(self, backup_id: str, destination: Path) -> Path: ...


class ObjectStorePort(Protocol):
    """S3 兼容对象操作端口，领域层不依赖具体 SDK。"""

    bucket: str

    def versioning_enabled(self) -> bool: ...

    def put_stream(
        self,
        key: str,
        source: BinaryIO,
        *,
        content_type: str,
        metadata: Mapping[str, str],
    ) -> ObjectInfo: ...

    def head(self, key: str, *, version_id: str | None = None) -> ObjectInfo | None: ...

    def iter_chunks(
        self,
        key: str,
        *,
        version_id: str | None = None,
        chunk_size: int = 1024 * 1024,
    ) -> Iterator[bytes]: ...

    def copy(
        self,
        source_key: str,
        destination_key: str,
        *,
        source_version_id: str | None,
        content_type: str,
        metadata: Mapping[str, str],
    ) -> ObjectInfo: ...

    def delete(self, key: str, *, version_id: str | None = None) -> None: ...

    def list_objects(self, prefix: str) -> Sequence[ObjectInfo]: ...

    def list_multipart_uploads(self, prefix: str) -> Sequence[MultipartUploadInfo]: ...

    def abort_multipart_upload(self, key: str, upload_id: str) -> None: ...


class EvidenceMetadataPort(Protocol):
    """对象准入状态、版本、墓碑和对账记录的持久化端口。"""

    def create_upload(
        self,
        request: EvidenceAdmissionRequest,
        *,
        upload_id: str,
        temporary_object_key: str,
        occurred_at: datetime,
        expires_at: datetime,
    ) -> EvidenceUpload: ...

    def mark_quarantined(
        self,
        upload_id: str,
        *,
        object_info: ObjectInfo,
        occurred_at: datetime,
    ) -> EvidenceUpload: ...

    def mark_verified(
        self,
        upload_id: str,
        *,
        actual_sha256: str,
        actual_size_bytes: int,
        detected_media_type: str,
        final_object_key: str,
        occurred_at: datetime,
    ) -> EvidenceUpload: ...

    def reject_upload(
        self,
        upload_id: str,
        *,
        code: str,
        reason: str,
        actual_sha256: str | None,
        actual_size_bytes: int | None,
        detected_media_type: str | None,
        occurred_at: datetime,
    ) -> EvidenceUpload: ...

    def activate_upload(
        self,
        upload_id: str,
        *,
        bucket: str,
        object_info: ObjectInfo,
        occurred_at: datetime,
    ) -> EvidenceVersion: ...

    def get_upload(self, upload_id: str) -> EvidenceUpload: ...

    def list_uploads(self) -> Sequence[EvidenceUpload]: ...

    def get_version(self, version_id: str) -> EvidenceVersion: ...

    def get_version_for_upload(self, upload_id: str) -> EvidenceVersion: ...

    def list_all_versions(self) -> Sequence[EvidenceVersion]: ...

    def expire_due_uploads(self, occurred_at: datetime) -> Sequence[EvidenceUpload]: ...

    def revoke_version(
        self,
        version_id: str,
        *,
        actor_id: str,
        reason: str,
        occurred_at: datetime,
        earliest_delete_at: datetime,
    ) -> EvidenceVersion: ...

    def list_due_tombstones(self, occurred_at: datetime) -> Sequence[EvidenceTombstone]: ...

    def claim_object_deletion(
        self,
        tombstone: EvidenceTombstone,
        *,
        occurred_at: datetime,
    ) -> bool: ...

    def mark_object_deleted(
        self,
        bucket: str,
        object_key: str,
        object_version_id: str,
        *,
        occurred_at: datetime,
    ) -> None: ...

    def record_reconciliation(
        self,
        report: ReconciliationReport,
        *,
        started_at: datetime,
        completed_at: datetime,
        cleanup_enabled: bool,
    ) -> None: ...


class OidcProviderPort(Protocol):
    """OIDC Discovery、Code + PKCE、令牌校验和撤销端口。"""

    def authorization_url(
        self,
        *,
        redirect_uri: str,
        state: str,
        nonce: str,
        code_challenge: str,
        scopes: Sequence[str],
    ) -> str: ...

    def exchange_code(
        self,
        *,
        code: SensitiveValue,
        code_verifier: SensitiveValue,
        redirect_uri: str,
        occurred_at: datetime,
    ) -> OidcTokenSet: ...

    def verify_id_token(
        self,
        id_token: SensitiveValue,
        *,
        expected_nonce_digest: str | None,
        access_token: SensitiveValue,
        occurred_at: datetime,
        clock_skew: timedelta,
    ) -> VerifiedIdentity: ...

    def refresh(
        self,
        refresh_token: SensitiveValue,
        *,
        occurred_at: datetime,
    ) -> OidcTokenSet: ...

    def revoke_token(self, token: SensitiveValue) -> None: ...


class IdentityRepositoryPort(Protocol):
    """预登记员工、OIDC 绑定、一次性授权事务和服务器会话端口。"""

    def create_authorization(self, transaction: AuthorizationTransaction) -> None: ...

    def claim_authorization(
        self,
        *,
        state_digest: str,
        authorization_code_digest: str,
        occurred_at: datetime,
    ) -> AuthorizationTransaction: ...

    def fail_authorization(
        self,
        transaction_id: str,
        *,
        reason_code: str,
        occurred_at: datetime,
    ) -> None: ...

    def resolve_binding(self, issuer: str, subject: str) -> IdentityBinding: ...

    def create_session(
        self,
        *,
        transaction_id: str,
        binding: IdentityBinding,
        session_id: str,
        session_secret_digest: str,
        token_set: OidcTokenSet,
        access_token_expires_at: datetime,
        session_expires_at: datetime,
        occurred_at: datetime,
    ) -> EmployeeSession: ...

    def get_session(self, session_id: str) -> EmployeeSession: ...

    def rotate_session_tokens(
        self,
        session_id: str,
        *,
        expected_token_version: int,
        token_set: OidcTokenSet,
        access_token_expires_at: datetime,
        occurred_at: datetime,
    ) -> EmployeeSession: ...

    def revoke_session(
        self,
        session_id: str,
        *,
        reason: str,
        actor_kind: ActorKind,
        actor_id: str,
        occurred_at: datetime,
    ) -> bool: ...

    def revoke_employee_sessions(
        self,
        employee_id: str,
        *,
        reason: str,
        actor_id: str,
        occurred_at: datetime,
    ) -> int: ...

    def expire_session(self, session_id: str, *, occurred_at: datetime) -> bool: ...

    def record_identity_assertion(
        self,
        session_id: str,
        *,
        project_id: str,
        command_name: str,
        idempotency_key: str,
        occurred_at: datetime,
    ) -> None: ...


class ProjectAuthorizationRepositoryPort(Protocol):
    """项目成员、服务身份和最小授权的持久化端口。"""

    def get_employee_grant(
        self, project_id: str, employee_id: str
    ) -> EmployeeProjectGrant | None: ...

    def get_service_grant(
        self, project_id: str, principal: ProjectPrincipal
    ) -> ServiceProjectGrant | None: ...

    def bootstrap_owner(
        self,
        *,
        project_id: str,
        employee_id: str,
        confidentiality_ceiling: ConfidentialityLevel,
        occurred_at: datetime,
    ) -> EmployeeProjectGrant: ...

    def upsert_employee_grant(
        self,
        *,
        project_id: str,
        employee_id: str,
        role: ProjectRole,
        confidentiality_ceiling: ConfidentialityLevel,
        granted_by: ProjectPrincipal,
        occurred_at: datetime,
    ) -> EmployeeProjectGrant: ...

    def register_service_principal(
        self,
        principal: ProjectPrincipal,
        *,
        display_name: str,
        registered_by: ProjectPrincipal,
        occurred_at: datetime,
    ) -> None: ...

    def upsert_service_grant(
        self,
        *,
        project_id: str,
        principal: ProjectPrincipal,
        actions: frozenset[ProjectAction],
        confidentiality_ceiling: ConfidentialityLevel,
        granted_by: ProjectPrincipal,
        occurred_at: datetime,
    ) -> ServiceProjectGrant: ...

    def revoke_employee_grant(
        self,
        *,
        project_id: str,
        employee_id: str,
        reason: str,
        revoked_by: ProjectPrincipal,
        occurred_at: datetime,
    ) -> None: ...

    def revoke_service_grant(
        self,
        *,
        project_id: str,
        principal: ProjectPrincipal,
        reason: str,
        revoked_by: ProjectPrincipal,
        occurred_at: datetime,
    ) -> None: ...

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

    def list_runtime_tasks(
        self, project_id: str
    ) -> Sequence[Mapping[str, object]]: ...

    def list_task_packets(
        self, project_id: str
    ) -> Sequence[Mapping[str, object]]: ...

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
