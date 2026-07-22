"""对象原件准入状态和不可变版本的 PostgreSQL 元数据适配器。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime
from uuid import uuid4

from .object_evidence import (
    ALLOWED_EVIDENCE_TRANSITIONS,
    EvidenceAdmissionRequest,
    EvidenceIntegrityError,
    EvidenceState,
    EvidenceStateError,
    EvidenceTombstone,
    EvidenceUpload,
    EvidenceVersion,
    ObjectInfo,
    ReconciliationReport,
)
from .postgresql_store import PostgreSQLConnection, PostgreSQLStoreBase


OBJECT_EVIDENCE_TABLES = frozenset(
    {
        "evidence_uploads",
        "evidence_object_versions",
        "evidence_state_transitions",
        "evidence_object_tombstones",
        "evidence_reconciliation_runs",
    }
)


class PostgreSQLEvidenceRepository(PostgreSQLStoreBase):
    """只管理对象准入元数据，不直接访问 S3 或解释业务事实。"""

    def quick_check(self) -> bool:
        """核对公共迁移和 F2.3 对象元数据表。"""

        if not super().quick_check():
            return False
        with self._connect() as connection:
            tables = {
                str(row[0])
                for row in connection.execute(
                    """
                    SELECT table_name FROM information_schema.tables
                    WHERE table_schema = current_schema()
                      AND table_name IN (?, ?, ?, ?, ?)
                    """,
                    tuple(sorted(OBJECT_EVIDENCE_TABLES)),
                )
            }
        return tables == OBJECT_EVIDENCE_TABLES

    def create_upload(
        self,
        request: EvidenceAdmissionRequest,
        *,
        upload_id: str,
        temporary_object_key: str,
        occurred_at: datetime,
        expires_at: datetime,
    ) -> EvidenceUpload:
        """按项目和幂等键创建或重放上传会话。"""

        request_hash = hashlib.sha256(
            json.dumps(
                asdict(request),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        occurred = _iso(occurred_at)
        expires = _iso(expires_at)
        with self._connect() as connection:
            connection.execute("BEGIN")
            try:
                lock_key = f"evidence-upload:{request.project_id}:{request.idempotency_key}"
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(?, 0))",
                    (lock_key,),
                )
                existing = connection.execute(
                    """
                    SELECT * FROM evidence_uploads
                    WHERE project_id = ? AND idempotency_key = ?
                    FOR UPDATE
                    """,
                    (request.project_id, request.idempotency_key),
                ).fetchone()
                if existing is not None:
                    if str(existing["request_hash"]) != request_hash:
                        raise EvidenceIntegrityError(
                            "同一上传幂等键不能绑定不同请求"
                        )
                    upload = _upload_from_row(existing)
                    connection.execute("COMMIT")
                    return upload
                project = connection.execute(
                    "SELECT 1 FROM projects WHERE project_id = ? FOR UPDATE",
                    (request.project_id,),
                ).fetchone()
                if project is None:
                    raise EvidenceIntegrityError("上传所属项目不存在")
                connection.execute(
                    """
                    INSERT INTO evidence_uploads(
                        upload_id, project_id, logical_source_id, original_filename,
                        expected_sha256, expected_size_bytes, expected_media_type,
                        confidentiality, idempotency_key, request_hash,
                        temporary_object_key, state, created_at, updated_at, expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'UPLOADING', ?, ?, ?)
                    """,
                    (
                        upload_id,
                        request.project_id,
                        request.logical_source_id,
                        request.original_filename,
                        request.expected_sha256,
                        request.expected_size_bytes,
                        request.expected_media_type,
                        request.confidentiality,
                        request.idempotency_key,
                        request_hash,
                        temporary_object_key,
                        occurred,
                        occurred,
                        expires,
                    ),
                )
                self._insert_transition(
                    connection,
                    upload_id,
                    None,
                    EvidenceState.UPLOADING,
                    "upload_started",
                    {},
                    occurred,
                )
                row = self._load_upload(connection, upload_id)
                connection.execute("COMMIT")
                return _upload_from_row(row)
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def mark_quarantined(
        self,
        upload_id: str,
        *,
        object_info: ObjectInfo,
        occurred_at: datetime,
    ) -> EvidenceUpload:
        """仅在完整临时对象可见后进入 QUARANTINED。"""

        occurred = _iso(occurred_at)
        with self._connect() as connection:
            connection.execute("BEGIN")
            try:
                row = self._load_upload(connection, upload_id, for_update=True)
                upload = _upload_from_row(row)
                if upload.state is EvidenceState.QUARANTINED:
                    if upload.temporary_object_version_id != object_info.version_id:
                        raise EvidenceIntegrityError("隔离对象版本发生变化")
                    connection.execute("COMMIT")
                    return upload
                self._assert_transition(upload.state, EvidenceState.QUARANTINED)
                connection.execute(
                    """
                    UPDATE evidence_uploads
                    SET state = 'QUARANTINED', temporary_object_version_id = ?, updated_at = ?
                    WHERE upload_id = ?
                    """,
                    (object_info.version_id, occurred, upload_id),
                )
                self._insert_transition(
                    connection,
                    upload_id,
                    upload.state,
                    EvidenceState.QUARANTINED,
                    "upload_completed",
                    {"size_bytes": object_info.size_bytes},
                    occurred,
                )
                updated = self._load_upload(connection, upload_id)
                connection.execute("COMMIT")
                return _upload_from_row(updated)
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def mark_verified(
        self,
        upload_id: str,
        *,
        actual_sha256: str,
        actual_size_bytes: int,
        detected_media_type: str,
        final_object_key: str,
        occurred_at: datetime,
    ) -> EvidenceUpload:
        """保存已验证内容元数据，但此时对象仍不能作为正式证据。"""

        occurred = _iso(occurred_at)
        with self._connect() as connection:
            connection.execute("BEGIN")
            try:
                upload = _upload_from_row(
                    self._load_upload(connection, upload_id, for_update=True)
                )
                if upload.state is EvidenceState.VERIFIED:
                    connection.execute("COMMIT")
                    return upload
                self._assert_transition(upload.state, EvidenceState.VERIFIED)
                connection.execute(
                    """
                    UPDATE evidence_uploads
                    SET state = 'VERIFIED', actual_sha256 = ?, actual_size_bytes = ?,
                        detected_media_type = ?, final_object_key = ?, updated_at = ?
                    WHERE upload_id = ?
                    """,
                    (
                        actual_sha256,
                        actual_size_bytes,
                        detected_media_type,
                        final_object_key,
                        occurred,
                        upload_id,
                    ),
                )
                self._insert_transition(
                    connection,
                    upload_id,
                    upload.state,
                    EvidenceState.VERIFIED,
                    "integrity_verified",
                    {"sha256": actual_sha256, "size_bytes": actual_size_bytes},
                    occurred,
                )
                updated = self._load_upload(connection, upload_id)
                connection.execute("COMMIT")
                return _upload_from_row(updated)
            except Exception:
                connection.execute("ROLLBACK")
                raise

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
    ) -> EvidenceUpload:
        """把未通过完整性或安全检查的隔离对象标为 REJECTED。"""

        occurred = _iso(occurred_at)
        with self._connect() as connection:
            connection.execute("BEGIN")
            try:
                upload = _upload_from_row(
                    self._load_upload(connection, upload_id, for_update=True)
                )
                if upload.state is EvidenceState.REJECTED:
                    connection.execute("COMMIT")
                    return upload
                self._assert_transition(upload.state, EvidenceState.REJECTED)
                connection.execute(
                    """
                    UPDATE evidence_uploads
                    SET state = 'REJECTED', actual_sha256 = ?, actual_size_bytes = ?,
                        detected_media_type = ?, rejection_code = ?, rejection_reason = ?,
                        updated_at = ?
                    WHERE upload_id = ?
                    """,
                    (
                        actual_sha256,
                        actual_size_bytes,
                        detected_media_type,
                        code,
                        reason,
                        occurred,
                        upload_id,
                    ),
                )
                self._insert_transition(
                    connection,
                    upload_id,
                    upload.state,
                    EvidenceState.REJECTED,
                    code,
                    {"reason": reason},
                    occurred,
                )
                updated = self._load_upload(connection, upload_id)
                connection.execute("COMMIT")
                return _upload_from_row(updated)
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def activate_upload(
        self,
        upload_id: str,
        *,
        bucket: str,
        object_info: ObjectInfo,
        occurred_at: datetime,
    ) -> EvidenceVersion:
        """在一笔事务中创建不可变版本并将 VERIFIED 推进为 ACTIVE。"""

        occurred = _iso(occurred_at)
        with self._connect() as connection:
            connection.execute("BEGIN")
            try:
                upload = _upload_from_row(
                    self._load_upload(connection, upload_id, for_update=True)
                )
                if upload.state is EvidenceState.ACTIVE:
                    version = self._load_version_for_upload(connection, upload_id)
                    connection.execute("COMMIT")
                    return _version_from_row(version)
                self._assert_transition(upload.state, EvidenceState.ACTIVE)
                if (
                    upload.actual_sha256 is None
                    or upload.actual_size_bytes is None
                    or upload.detected_media_type is None
                    or upload.final_object_key != object_info.key
                ):
                    raise EvidenceIntegrityError("VERIFIED 上传缺少生效所需元数据")
                source_lock = (
                    f"evidence-version:{upload.project_id}:{upload.logical_source_id}"
                )
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(?, 0))",
                    (source_lock,),
                )
                object_lock = (
                    f"evidence-object:{bucket}:{object_info.key}:{object_info.version_id}"
                )
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(?, 0))",
                    (object_lock,),
                )
                deletion_claim = connection.execute(
                    """
                    SELECT 1 FROM evidence_object_tombstones
                    WHERE bucket = ? AND object_key = ? AND object_version_id = ?
                      AND deleted_at IS NULL AND deletion_claim_id IS NOT NULL
                    LIMIT 1
                    """,
                    (bucket, object_info.key, object_info.version_id),
                ).fetchone()
                if deletion_claim is not None:
                    raise EvidenceStateError(
                        "内容对象版本正在延迟删除，请在清理完成后重试"
                    )
                row = connection.execute(
                    """
                    SELECT COALESCE(MAX(version_number), 0)
                    FROM evidence_object_versions
                    WHERE project_id = ? AND logical_source_id = ?
                    """,
                    (upload.project_id, upload.logical_source_id),
                ).fetchone()
                version_number = int(row[0]) + 1
                version_id = f"EV-{uuid4().hex.upper()}"
                connection.execute(
                    """
                    INSERT INTO evidence_object_versions(
                        version_id, project_id, logical_source_id, version_number,
                        upload_id, original_filename, sha256, size_bytes, media_type,
                        confidentiality, bucket, object_key, object_version_id,
                        state, activated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?)
                    """,
                    (
                        version_id,
                        upload.project_id,
                        upload.logical_source_id,
                        version_number,
                        upload.upload_id,
                        upload.original_filename,
                        upload.actual_sha256,
                        upload.actual_size_bytes,
                        upload.detected_media_type,
                        upload.confidentiality,
                        bucket,
                        object_info.key,
                        object_info.version_id,
                        occurred,
                    ),
                )
                connection.execute(
                    """
                    UPDATE evidence_uploads
                    SET state = 'ACTIVE', final_object_version_id = ?, updated_at = ?
                    WHERE upload_id = ?
                    """,
                    (object_info.version_id, occurred, upload_id),
                )
                self._insert_transition(
                    connection,
                    upload_id,
                    upload.state,
                    EvidenceState.ACTIVE,
                    "content_object_activated",
                    {"version_id": version_id, "version_number": version_number},
                    occurred,
                )
                version = self._load_version(connection, version_id)
                connection.execute("COMMIT")
                return _version_from_row(version)
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def get_upload(self, upload_id: str) -> EvidenceUpload:
        """读取一次上传会话。"""

        with self._connect() as connection:
            return _upload_from_row(self._load_upload(connection, upload_id))

    def list_uploads(self) -> tuple[EvidenceUpload, ...]:
        """列出全部上传会话，供对账作业使用。"""

        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM evidence_uploads ORDER BY created_at, upload_id"
            ).fetchall()
        return tuple(_upload_from_row(row) for row in rows)

    def get_version(self, version_id: str) -> EvidenceVersion:
        """读取一个对象版本。"""

        with self._connect() as connection:
            return _version_from_row(self._load_version(connection, version_id))

    def get_version_for_upload(self, upload_id: str) -> EvidenceVersion:
        """根据上传会话读取已经生效的对象版本。"""

        with self._connect() as connection:
            return _version_from_row(
                self._load_version_for_upload(connection, upload_id)
            )

    def list_versions(
        self,
        project_id: str,
        logical_source_id: str | None = None,
    ) -> tuple[EvidenceVersion, ...]:
        """按逻辑来源的稳定版本号列出项目对象版本。"""

        statement = "SELECT * FROM evidence_object_versions WHERE project_id = ?"
        parameters: tuple[object, ...] = (project_id,)
        if logical_source_id is not None:
            statement += " AND logical_source_id = ?"
            parameters += (logical_source_id,)
        statement += " ORDER BY logical_source_id, version_number"
        with self._connect() as connection:
            rows = connection.execute(statement, parameters).fetchall()
        return tuple(_version_from_row(row) for row in rows)

    def list_all_versions(self) -> tuple[EvidenceVersion, ...]:
        """列出所有项目的对象版本，供服务器后台对账。"""

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM evidence_object_versions
                ORDER BY project_id, logical_source_id, version_number
                """
            ).fetchall()
        return tuple(_version_from_row(row) for row in rows)

    def expire_due_uploads(self, occurred_at: datetime) -> tuple[EvidenceUpload, ...]:
        """把到期且仍在 UPLOADING 的会话显式推进为 EXPIRED。"""

        occurred = _iso(occurred_at)
        expired: list[EvidenceUpload] = []
        with self._connect() as connection:
            connection.execute("BEGIN")
            try:
                rows = connection.execute(
                    """
                    SELECT * FROM evidence_uploads
                    WHERE state = 'UPLOADING' AND expires_at <= ?
                    ORDER BY expires_at, upload_id
                    FOR UPDATE
                    """,
                    (occurred,),
                ).fetchall()
                for row in rows:
                    upload = _upload_from_row(row)
                    self._assert_transition(upload.state, EvidenceState.EXPIRED)
                    connection.execute(
                        """
                        UPDATE evidence_uploads
                        SET state = 'EXPIRED', updated_at = ? WHERE upload_id = ?
                        """,
                        (occurred, upload.upload_id),
                    )
                    self._insert_transition(
                        connection,
                        upload.upload_id,
                        upload.state,
                        EvidenceState.EXPIRED,
                        "upload_expired",
                        {},
                        occurred,
                    )
                    expired.append(
                        _upload_from_row(
                            self._load_upload(connection, upload.upload_id)
                        )
                    )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return tuple(expired)

    def revoke_version(
        self,
        version_id: str,
        *,
        actor_id: str,
        reason: str,
        occurred_at: datetime,
        earliest_delete_at: datetime,
    ) -> EvidenceVersion:
        """把 ACTIVE 版本撤销并原子创建延迟删除墓碑。"""

        occurred = _iso(occurred_at)
        earliest = _iso(earliest_delete_at)
        with self._connect() as connection:
            connection.execute("BEGIN")
            try:
                version = _version_from_row(
                    self._load_version(connection, version_id, for_update=True)
                )
                if version.state is EvidenceState.REVOKED:
                    connection.execute("COMMIT")
                    return version
                self._assert_transition(version.state, EvidenceState.REVOKED)
                connection.execute(
                    """
                    UPDATE evidence_object_versions
                    SET state = 'REVOKED', revoked_at = ?, revoked_by = ?,
                        revocation_reason = ?
                    WHERE version_id = ?
                    """,
                    (occurred, actor_id, reason, version_id),
                )
                connection.execute(
                    """
                    UPDATE evidence_uploads
                    SET state = 'REVOKED', updated_at = ? WHERE upload_id = ?
                    """,
                    (occurred, version.upload_id),
                )
                self._insert_transition(
                    connection,
                    version.upload_id,
                    version.state,
                    EvidenceState.REVOKED,
                    "human_revoked",
                    {"actor_id": actor_id, "reason": reason},
                    occurred,
                )
                connection.execute(
                    """
                    INSERT INTO evidence_object_tombstones(
                        tombstone_id, version_id, bucket, object_key,
                        object_version_id, reason, created_by, created_at,
                        earliest_delete_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"ET-{uuid4().hex.upper()}",
                        version_id,
                        version.bucket,
                        version.object_key,
                        version.object_version_id,
                        reason,
                        actor_id,
                        occurred,
                        earliest,
                    ),
                )
                updated = self._load_version(connection, version_id)
                connection.execute("COMMIT")
                return _version_from_row(updated)
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def list_due_tombstones(self, occurred_at: datetime) -> tuple[EvidenceTombstone, ...]:
        """列出已到最早删除时间且尚未处理的墓碑。"""

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM evidence_object_tombstones
                WHERE deleted_at IS NULL AND earliest_delete_at <= ?
                ORDER BY earliest_delete_at, tombstone_id
                """,
                (_iso(occurred_at),),
            ).fetchall()
        return tuple(_tombstone_from_row(row) for row in rows)

    def claim_object_deletion(
        self,
        tombstone: EvidenceTombstone,
        *,
        occurred_at: datetime,
    ) -> bool:
        """原子认领一个到期对象删除，阻止并发激活复用同一 S3 版本。"""

        occurred = _iso(occurred_at)
        identity = (
            tombstone.bucket,
            tombstone.object_key,
            tombstone.object_version_id,
        )
        with self._connect() as connection:
            connection.execute("BEGIN")
            try:
                object_lock = "evidence-object:" + ":".join(identity)
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(?, 0))",
                    (object_lock,),
                )
                versions = connection.execute(
                    """
                    SELECT version_id, state FROM evidence_object_versions
                    WHERE bucket = ? AND object_key = ? AND object_version_id = ?
                      AND object_deleted_at IS NULL
                    FOR UPDATE
                    """,
                    identity,
                ).fetchall()
                if not versions or any(
                    str(row["state"]) != EvidenceState.REVOKED.value
                    for row in versions
                ):
                    connection.execute("COMMIT")
                    return False
                tombstones = connection.execute(
                    """
                    SELECT version_id, earliest_delete_at, deletion_claim_id, deleted_at
                    FROM evidence_object_tombstones
                    WHERE bucket = ? AND object_key = ? AND object_version_id = ?
                    FOR UPDATE
                    """,
                    identity,
                ).fetchall()
                version_ids = {str(row["version_id"]) for row in versions}
                if {str(row["version_id"]) for row in tombstones} != version_ids:
                    connection.execute("COMMIT")
                    return False
                if any(
                    row["deleted_at"] is not None
                    or str(row["earliest_delete_at"]) > occurred
                    for row in tombstones
                ):
                    connection.execute("COMMIT")
                    return False
                claim_id = next(
                    (
                        str(row["deletion_claim_id"])
                        for row in tombstones
                        if row["deletion_claim_id"] is not None
                    ),
                    f"EDC-{uuid4().hex.upper()}",
                )
                connection.execute(
                    """
                    UPDATE evidence_object_tombstones
                    SET deletion_claim_id = ?,
                        deletion_claimed_at = COALESCE(deletion_claimed_at, ?)
                    WHERE bucket = ? AND object_key = ? AND object_version_id = ?
                      AND deleted_at IS NULL
                    """,
                    (claim_id, occurred, *identity),
                )
                connection.execute("COMMIT")
                return True
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def mark_object_deleted(
        self,
        bucket: str,
        object_key: str,
        object_version_id: str,
        *,
        occurred_at: datetime,
    ) -> None:
        """在物理删除成功后关闭全部对应墓碑。"""

        occurred = _iso(occurred_at)
        with self._connect() as connection:
            connection.execute("BEGIN")
            try:
                rows = connection.execute(
                    """
                    SELECT state FROM evidence_object_versions
                    WHERE bucket = ? AND object_key = ? AND object_version_id = ?
                      AND object_deleted_at IS NULL
                    FOR UPDATE
                    """,
                    (bucket, object_key, object_version_id),
                ).fetchall()
                if any(str(row["state"]) == EvidenceState.ACTIVE.value for row in rows):
                    raise EvidenceStateError("仍有 ACTIVE 引用，不能关闭对象墓碑")
                claims = connection.execute(
                    """
                    SELECT deletion_claim_id FROM evidence_object_tombstones
                    WHERE bucket = ? AND object_key = ? AND object_version_id = ?
                      AND deleted_at IS NULL
                    FOR UPDATE
                    """,
                    (bucket, object_key, object_version_id),
                ).fetchall()
                if not claims or any(row["deletion_claim_id"] is None for row in claims):
                    raise EvidenceStateError("对象墓碑尚未取得删除认领")
                connection.execute(
                    """
                    UPDATE evidence_object_versions
                    SET object_deleted_at = ?
                    WHERE bucket = ? AND object_key = ? AND object_version_id = ?
                      AND state = 'REVOKED' AND object_deleted_at IS NULL
                    """,
                    (occurred, bucket, object_key, object_version_id),
                )
                connection.execute(
                    """
                    UPDATE evidence_object_tombstones
                    SET deleted_at = ?
                    WHERE bucket = ? AND object_key = ? AND object_version_id = ?
                      AND deleted_at IS NULL
                    """,
                    (occurred, bucket, object_key, object_version_id),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def record_reconciliation(
        self,
        report: ReconciliationReport,
        *,
        started_at: datetime,
        completed_at: datetime,
        cleanup_enabled: bool,
    ) -> None:
        """保存对账摘要和问题清单，便于后续告警与审计。"""

        details = [asdict(issue) for issue in report.issues]
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO evidence_reconciliation_runs(
                    run_id, started_at, completed_at, cleanup_enabled,
                    expired_uploads, aborted_multipart_uploads,
                    deleted_orphan_objects, issue_count, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report.run_id,
                    _iso(started_at),
                    _iso(completed_at),
                    int(cleanup_enabled),
                    report.expired_uploads,
                    report.aborted_multipart_uploads,
                    report.deleted_orphan_objects,
                    len(report.issues),
                    json.dumps(details, ensure_ascii=False, sort_keys=True),
                ),
            )

    def list_reconciliation_runs(self) -> tuple[dict[str, object], ...]:
        """列出已保存的对象对账运行。"""

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM evidence_reconciliation_runs
                ORDER BY started_at, run_id
                """
            ).fetchall()
        return tuple(dict(row) for row in rows)

    @staticmethod
    def _assert_transition(current: EvidenceState, target: EvidenceState) -> None:
        if (current, target) not in ALLOWED_EVIDENCE_TRANSITIONS:
            raise EvidenceStateError(
                f"不允许从 {current.value} 转换到 {target.value}"
            )

    @staticmethod
    def _insert_transition(
        connection: PostgreSQLConnection,
        upload_id: str,
        from_state: EvidenceState | None,
        to_state: EvidenceState,
        reason_code: str,
        details: dict[str, object],
        occurred_at: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO evidence_state_transitions(
                transition_id, upload_id, from_state, to_state,
                reason_code, details_json, occurred_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"EST-{uuid4().hex.upper()}",
                upload_id,
                from_state.value if from_state else None,
                to_state.value,
                reason_code,
                json.dumps(details, ensure_ascii=False, sort_keys=True),
                occurred_at,
            ),
        )

    @staticmethod
    def _load_upload(
        connection: PostgreSQLConnection,
        upload_id: str,
        *,
        for_update: bool = False,
    ):
        suffix = " FOR UPDATE" if for_update else ""
        row = connection.execute(
            f"SELECT * FROM evidence_uploads WHERE upload_id = ?{suffix}",
            (upload_id,),
        ).fetchone()
        if row is None:
            raise EvidenceIntegrityError(f"上传会话不存在：{upload_id}")
        return row

    @staticmethod
    def _load_version(
        connection: PostgreSQLConnection,
        version_id: str,
        *,
        for_update: bool = False,
    ):
        suffix = " FOR UPDATE" if for_update else ""
        row = connection.execute(
            f"SELECT * FROM evidence_object_versions WHERE version_id = ?{suffix}",
            (version_id,),
        ).fetchone()
        if row is None:
            raise EvidenceIntegrityError(f"证据版本不存在：{version_id}")
        return row

    @staticmethod
    def _load_version_for_upload(
        connection: PostgreSQLConnection,
        upload_id: str,
    ):
        row = connection.execute(
            "SELECT * FROM evidence_object_versions WHERE upload_id = ?",
            (upload_id,),
        ).fetchone()
        if row is None:
            raise EvidenceIntegrityError(f"上传尚未形成 ACTIVE 版本：{upload_id}")
        return row


def _upload_from_row(row) -> EvidenceUpload:
    return EvidenceUpload(
        upload_id=str(row["upload_id"]),
        project_id=str(row["project_id"]),
        logical_source_id=str(row["logical_source_id"]),
        original_filename=str(row["original_filename"]),
        expected_sha256=str(row["expected_sha256"]),
        expected_size_bytes=int(row["expected_size_bytes"]),
        expected_media_type=str(row["expected_media_type"]),
        confidentiality=str(row["confidentiality"]),
        idempotency_key=str(row["idempotency_key"]),
        temporary_object_key=str(row["temporary_object_key"]),
        temporary_object_version_id=_optional_text(row["temporary_object_version_id"]),
        state=EvidenceState(str(row["state"])),
        actual_sha256=_optional_text(row["actual_sha256"]),
        actual_size_bytes=(
            int(row["actual_size_bytes"])
            if row["actual_size_bytes"] is not None
            else None
        ),
        detected_media_type=_optional_text(row["detected_media_type"]),
        final_object_key=_optional_text(row["final_object_key"]),
        final_object_version_id=_optional_text(row["final_object_version_id"]),
        rejection_code=_optional_text(row["rejection_code"]),
        rejection_reason=_optional_text(row["rejection_reason"]),
        created_at=_datetime(row["created_at"]),
        updated_at=_datetime(row["updated_at"]),
        expires_at=_datetime(row["expires_at"]),
    )


def _version_from_row(row) -> EvidenceVersion:
    return EvidenceVersion(
        version_id=str(row["version_id"]),
        project_id=str(row["project_id"]),
        logical_source_id=str(row["logical_source_id"]),
        version_number=int(row["version_number"]),
        upload_id=str(row["upload_id"]),
        original_filename=str(row["original_filename"]),
        sha256=str(row["sha256"]),
        size_bytes=int(row["size_bytes"]),
        media_type=str(row["media_type"]),
        confidentiality=str(row["confidentiality"]),
        bucket=str(row["bucket"]),
        object_key=str(row["object_key"]),
        object_version_id=str(row["object_version_id"]),
        state=EvidenceState(str(row["state"])),
        activated_at=_datetime(row["activated_at"]),
        revoked_at=_optional_datetime(row["revoked_at"]),
        revoked_by=_optional_text(row["revoked_by"]),
        revocation_reason=_optional_text(row["revocation_reason"]),
        object_deleted_at=_optional_datetime(row["object_deleted_at"]),
    )


def _tombstone_from_row(row) -> EvidenceTombstone:
    return EvidenceTombstone(
        tombstone_id=str(row["tombstone_id"]),
        version_id=str(row["version_id"]),
        bucket=str(row["bucket"]),
        object_key=str(row["object_key"]),
        object_version_id=str(row["object_version_id"]),
        earliest_delete_at=_datetime(row["earliest_delete_at"]),
    )


def _optional_text(value: object) -> str | None:
    return None if value is None else str(value)


def _datetime(value: object) -> datetime:
    return datetime.fromisoformat(str(value))


def _optional_datetime(value: object) -> datetime | None:
    return None if value is None else _datetime(value)


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("时间必须包含时区")
    return value.isoformat()


__all__ = ["OBJECT_EVIDENCE_TABLES", "PostgreSQLEvidenceRepository"]
