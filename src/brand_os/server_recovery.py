"""服务器权威数据、投影和对象版本的联合恢复演练。"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol, Sequence

from .backup import BackupError
from .object_evidence import EvidenceAdmissionService
from .ports import ObjectStorePort
from .postgresql_backup import (
    PostgreSQLRecoverySnapshot,
    PostgreSQLRestoreResult,
)
from .postgresql_evidence import PostgreSQLEvidenceRepository
from .postgresql_store import PostgreSQLCanonicalStore


SERVER_RECOVERY_REPORT_SCHEMA_VERSION = "server-recovery-report.v1"


class RecoveryIntegrityError(BackupError):
    """恢复数据、事件重放或对象版本没有通过联合对账。"""


class RecoveryBackupPort(Protocol):
    """联合恢复演练依赖的数据库备份端口。"""

    def restore(self, backup_id: str, target_dsn: str) -> PostgreSQLRestoreResult: ...

    def snapshot(self, dsn: str) -> PostgreSQLRecoverySnapshot: ...

    def list_project_ids(self, dsn: str) -> tuple[str, ...]: ...


@dataclass(frozen=True, slots=True)
class ServerRecoveryDrillReport:
    """一次隔离恢复演练的可审计安全摘要。"""

    schema_version: str
    backup_id: str
    started_at: str
    completed_at: str
    database_restore_seconds: float
    total_drill_seconds: float
    recovery_point_age_seconds: float
    database_snapshot_sha256: str
    project_count: int
    event_count: int
    proposal_count: int
    human_action_count: int
    state_item_count: int
    evidence_version_count: int
    rebuilt_state_events: int
    rebuilt_lifecycle_events: int
    object_reconciliation_run_id: str
    object_issue_count: int
    measurement_scope: str
    production_pitr_verified: bool
    production_slo_confirmed: bool
    result: str

    def as_dict(self) -> dict[str, object]:
        """返回不包含 DSN、桶名、对象键和业务原文的演练报告。"""

        return {
            "schema_version": self.schema_version,
            "backup_id": self.backup_id,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "database_restore_seconds": self.database_restore_seconds,
            "total_drill_seconds": self.total_drill_seconds,
            "recovery_point_age_seconds": self.recovery_point_age_seconds,
            "database_snapshot_sha256": self.database_snapshot_sha256,
            "project_count": self.project_count,
            "event_count": self.event_count,
            "proposal_count": self.proposal_count,
            "human_action_count": self.human_action_count,
            "state_item_count": self.state_item_count,
            "evidence_version_count": self.evidence_version_count,
            "rebuilt_state_events": self.rebuilt_state_events,
            "rebuilt_lifecycle_events": self.rebuilt_lifecycle_events,
            "object_reconciliation_run_id": self.object_reconciliation_run_id,
            "object_issue_count": self.object_issue_count,
            "measurement_scope": self.measurement_scope,
            "production_pitr_verified": self.production_pitr_verified,
            "production_slo_confirmed": self.production_slo_confirmed,
            "result": self.result,
        }


class ServerRecoveryDrill:
    """在新数据库中恢复、重放投影并核对明确对象版本。"""

    def __init__(
        self,
        *,
        backup: RecoveryBackupPort,
        objects: ObjectStorePort,
        allowed_reviewers: Sequence[str] = ("Fox",),
    ) -> None:
        self.backup = backup
        self.objects = objects
        self.allowed_reviewers = tuple(allowed_reviewers)
        if not self.allowed_reviewers:
            raise ValueError("恢复演练至少需要一个已配置人工评审人")

    def run(
        self,
        backup_id: str,
        target_dsn: str,
        *,
        now: datetime | None = None,
        orphan_grace: timedelta = timedelta(hours=24),
    ) -> ServerRecoveryDrillReport:
        """执行隔离恢复；任何事件、投影或对象异常都会阻断通过。"""

        if orphan_grace < timedelta(0):
            raise ValueError("orphan_grace 不能小于 0")
        started_at = (now or datetime.now(UTC)).astimezone(UTC)
        started = time.perf_counter()
        restore = self.backup.restore(backup_id, target_dsn)
        canonical = PostgreSQLCanonicalStore(
            target_dsn,
            allowed_reviewers=self.allowed_reviewers,
        )
        evidence = PostgreSQLEvidenceRepository(
            target_dsn,
            allowed_reviewers=self.allowed_reviewers,
        )
        if not canonical.quick_check() or not evidence.quick_check():
            raise RecoveryIntegrityError("恢复库没有通过 Schema 和核心表检查")

        project_ids = self.backup.list_project_ids(target_dsn)
        rebuilt_lifecycle = 0
        rebuilt_state = 0
        try:
            for project_id in project_ids:
                rebuilt_lifecycle += canonical.rebuild_proposal_lifecycle(project_id)
                rebuilt_state += canonical.rebuild_state_projection(project_id)
        except Exception as error:
            raise RecoveryIntegrityError("恢复库无法从正式事件重建投影") from error

        rebuilt_snapshot = self.backup.snapshot(target_dsn)
        if rebuilt_snapshot != restore.snapshot:
            raise RecoveryIntegrityError("事件重放后的投影与备份一致快照不相同")

        reconciliation = EvidenceAdmissionService(
            metadata=evidence,
            objects=self.objects,
            allowed_revokers=self.allowed_reviewers,
        ).reconcile(
            now=started_at,
            orphan_grace=orphan_grace,
            cleanup=False,
        )
        if reconciliation.issues:
            issue_codes = ",".join(sorted({item.code for item in reconciliation.issues}))
            raise RecoveryIntegrityError(f"对象版本恢复对账失败：{issue_codes}")

        versions = tuple(
            version
            for version in evidence.list_all_versions()
            if version.object_deleted_at is None
        )
        wrong_bucket = [
            version.version_id
            for version in versions
            if version.bucket != self.objects.bucket
        ]
        if wrong_bucket:
            raise RecoveryIntegrityError("恢复库对象版本指向了非当前受控桶")

        completed_at = datetime.now(UTC)
        backup_created_at = datetime.fromisoformat(restore.backup_created_at)
        if backup_created_at.tzinfo is None:
            backup_created_at = backup_created_at.replace(tzinfo=UTC)
        snapshot = restore.snapshot
        return ServerRecoveryDrillReport(
            schema_version=SERVER_RECOVERY_REPORT_SCHEMA_VERSION,
            backup_id=backup_id,
            started_at=started_at.isoformat(),
            completed_at=completed_at.isoformat(),
            database_restore_seconds=restore.duration_seconds,
            total_drill_seconds=round(time.perf_counter() - started, 6),
            recovery_point_age_seconds=round(
                max(0.0, (started_at - backup_created_at.astimezone(UTC)).total_seconds()),
                6,
            ),
            database_snapshot_sha256=snapshot.snapshot_sha256,
            project_count=len(project_ids),
            event_count=snapshot.table("events").row_count,
            proposal_count=snapshot.table("proposals").row_count,
            human_action_count=snapshot.table("human_actions").row_count,
            state_item_count=snapshot.table("state_items").row_count,
            evidence_version_count=len(versions),
            rebuilt_state_events=rebuilt_state,
            rebuilt_lifecycle_events=rebuilt_lifecycle,
            object_reconciliation_run_id=reconciliation.run_id,
            object_issue_count=0,
            measurement_scope="isolated-local-fixture",
            production_pitr_verified=False,
            production_slo_confirmed=False,
            result="passed",
        )


__all__ = [
    "RecoveryBackupPort",
    "RecoveryIntegrityError",
    "SERVER_RECOVERY_REPORT_SCHEMA_VERSION",
    "ServerRecoveryDrill",
    "ServerRecoveryDrillReport",
]
