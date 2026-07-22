"""对象原件的版本、完整性校验和可恢复准入编排。"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import PurePath
from typing import TYPE_CHECKING, BinaryIO, Iterable
from uuid import uuid4

from .domain import Actor, ActorKind

if TYPE_CHECKING:
    from .ports import EvidenceMetadataPort, ObjectStorePort


SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
TEMPORARY_OBJECT_PREFIX = "quarantine/"
CONTENT_OBJECT_PREFIX = "evidence/sha256/"


class EvidenceState(StrEnum):
    """对象原件在上传、校验和生效过程中的状态。"""

    UPLOADING = "UPLOADING"
    QUARANTINED = "QUARANTINED"
    VERIFIED = "VERIFIED"
    ACTIVE = "ACTIVE"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


ALLOWED_EVIDENCE_TRANSITIONS = frozenset(
    {
        (EvidenceState.UPLOADING, EvidenceState.QUARANTINED),
        (EvidenceState.UPLOADING, EvidenceState.EXPIRED),
        (EvidenceState.QUARANTINED, EvidenceState.VERIFIED),
        (EvidenceState.QUARANTINED, EvidenceState.REJECTED),
        (EvidenceState.VERIFIED, EvidenceState.ACTIVE),
        (EvidenceState.ACTIVE, EvidenceState.REVOKED),
    }
)


class EvidenceError(RuntimeError):
    """对象证据准入错误基类。"""


class EvidenceIntegrityError(EvidenceError):
    """对象、元数据或幂等请求不一致。"""


class EvidenceStateError(EvidenceError):
    """请求的状态迁移不在准入状态机内。"""


class EvidenceRejectedError(EvidenceError):
    """上传对象未通过完整性或安全检查。"""


class EvidencePermissionDenied(EvidenceError):
    """操作者无权撤销已经生效的证据版本。"""


@dataclass(frozen=True, slots=True)
class EvidenceAdmissionRequest:
    """开始上传前必须固定的来源与预期完整性元数据。"""

    project_id: str
    logical_source_id: str
    original_filename: str
    expected_sha256: str
    expected_size_bytes: int
    expected_media_type: str
    confidentiality: str
    idempotency_key: str

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.project_id, "project_id"),
            (self.logical_source_id, "logical_source_id"),
            (self.original_filename, "original_filename"),
            (self.expected_media_type, "expected_media_type"),
            (self.idempotency_key, "idempotency_key"),
        ):
            if not value.strip():
                raise ValueError(f"{field_name} 不能为空")
        if (
            PurePath(self.original_filename).name != self.original_filename
            or "/" in self.original_filename
            or "\\" in self.original_filename
        ):
            raise ValueError("original_filename 只能包含文件名，不能包含路径")
        if not SHA256_PATTERN.fullmatch(self.expected_sha256):
            raise ValueError("expected_sha256 必须是完整的小写 SHA-256")
        if self.expected_size_bytes < 0:
            raise ValueError("expected_size_bytes 不能小于 0")
        if self.confidentiality not in {"P0", "P1", "P2", "P3"}:
            raise ValueError("confidentiality 必须是 P0-P3")


@dataclass(frozen=True, slots=True)
class ObjectInfo:
    """对象存储中一个明确版本的安全元数据。"""

    key: str
    size_bytes: int
    content_type: str
    version_id: str
    last_modified: datetime
    metadata: dict[str, str]


@dataclass(frozen=True, slots=True)
class MultipartUploadInfo:
    """尚未完成的分片上传。"""

    key: str
    upload_id: str
    initiated_at: datetime


@dataclass(frozen=True, slots=True)
class EvidenceUpload:
    """PostgreSQL 中的一次对象准入会话。"""

    upload_id: str
    project_id: str
    logical_source_id: str
    original_filename: str
    expected_sha256: str
    expected_size_bytes: int
    expected_media_type: str
    confidentiality: str
    idempotency_key: str
    temporary_object_key: str
    temporary_object_version_id: str | None
    state: EvidenceState
    actual_sha256: str | None
    actual_size_bytes: int | None
    detected_media_type: str | None
    final_object_key: str | None
    final_object_version_id: str | None
    rejection_code: str | None
    rejection_reason: str | None
    created_at: datetime
    updated_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class EvidenceVersion:
    """已经生效或被显式撤销的不可变原件版本。"""

    version_id: str
    project_id: str
    logical_source_id: str
    version_number: int
    upload_id: str
    original_filename: str
    sha256: str
    size_bytes: int
    media_type: str
    confidentiality: str
    bucket: str
    object_key: str
    object_version_id: str
    state: EvidenceState
    activated_at: datetime
    revoked_at: datetime | None
    revoked_by: str | None
    revocation_reason: str | None
    object_deleted_at: datetime | None


@dataclass(frozen=True, slots=True)
class EvidenceTombstone:
    """延迟删除一个已撤销对象版本的墓碑。"""

    tombstone_id: str
    version_id: str
    bucket: str
    object_key: str
    object_version_id: str
    earliest_delete_at: datetime


@dataclass(frozen=True, slots=True)
class ReconciliationIssue:
    """对象存储和 PostgreSQL 对账发现的一项异常。"""

    code: str
    object_key: str
    detail: str


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    """一次对象对账和可选清理的可审计结果。"""

    run_id: str
    expired_uploads: int
    aborted_multipart_uploads: int
    deleted_orphan_objects: int
    issues: tuple[ReconciliationIssue, ...]


class EvidenceAdmissionService:
    """在不使用分布式事务的前提下协调元数据与对象存储。"""

    def __init__(
        self,
        *,
        metadata: EvidenceMetadataPort,
        objects: ObjectStorePort,
        allowed_revokers: Iterable[str] = ("Fox",),
    ) -> None:
        self.metadata = metadata
        self.objects = objects
        self.allowed_revokers = frozenset(allowed_revokers)
        if not self.objects.versioning_enabled():
            raise EvidenceIntegrityError("对象存储必须开启版本控制")

    def begin_upload(
        self,
        request: EvidenceAdmissionRequest,
        *,
        now: datetime | None = None,
        upload_ttl: timedelta = timedelta(hours=24),
    ) -> EvidenceUpload:
        """幂等创建上传会话，临时对象键不能用于正式证据引用。"""

        occurred_at = _utc(now)
        if upload_ttl <= timedelta(0):
            raise ValueError("upload_ttl 必须大于 0")
        upload_id = f"EU-{uuid4().hex.upper()}"
        temporary_key = f"{TEMPORARY_OBJECT_PREFIX}{upload_id}"
        return self.metadata.create_upload(
            request,
            upload_id=upload_id,
            temporary_object_key=temporary_key,
            occurred_at=occurred_at,
            expires_at=occurred_at + upload_ttl,
        )

    def upload_and_quarantine(
        self,
        upload_id: str,
        source: BinaryIO,
        *,
        now: datetime | None = None,
    ) -> EvidenceUpload:
        """上传临时对象并在对象完整存在后进入隔离态。"""

        upload = self.metadata.get_upload(upload_id)
        if upload.state in {
            EvidenceState.QUARANTINED,
            EvidenceState.VERIFIED,
            EvidenceState.ACTIVE,
        }:
            return upload
        self._require_state(upload, EvidenceState.UPLOADING)
        object_info = self.objects.put_stream(
            upload.temporary_object_key,
            source,
            content_type=upload.expected_media_type,
            metadata={
                "upload-id": upload.upload_id,
                "project-id": upload.project_id,
            },
        )
        return self.metadata.mark_quarantined(
            upload_id,
            object_info=object_info,
            occurred_at=_utc(now),
        )

    def verify_and_activate(
        self,
        upload_id: str,
        *,
        detected_media_type: str,
        security_scan_passed: bool,
        now: datetime | None = None,
    ) -> EvidenceVersion:
        """校验隔离对象，复制为内容地址对象，再用数据库事务生效。"""

        occurred_at = _utc(now)
        upload = self.metadata.get_upload(upload_id)
        if upload.state is EvidenceState.ACTIVE:
            return self.metadata.get_version_for_upload(upload_id)
        if upload.state is EvidenceState.QUARANTINED:
            upload = self._verify_quarantined(
                upload,
                detected_media_type=detected_media_type,
                security_scan_passed=security_scan_passed,
                occurred_at=occurred_at,
            )
        else:
            self._require_state(upload, EvidenceState.QUARANTINED, EvidenceState.VERIFIED)
        return self._activate_verified(upload, occurred_at=occurred_at)

    def stream_active(self, version_id: str):
        """只流式读取 ACTIVE 对象，隔离、拒绝和撤销对象均不可回源。"""

        version = self.metadata.get_version(version_id)
        if version.state is not EvidenceState.ACTIVE or version.object_deleted_at is not None:
            raise EvidenceStateError("只有 ACTIVE 对象可以作为正式证据读取")
        info = self.objects.head(version.object_key, version_id=version.object_version_id)
        if info is None:
            raise EvidenceIntegrityError("ACTIVE 对象不存在")
        yield from self.objects.iter_chunks(
            version.object_key,
            version_id=version.object_version_id,
        )

    def revoke(
        self,
        version_id: str,
        actor: Actor,
        *,
        reason: str,
        now: datetime | None = None,
        retention: timedelta = timedelta(days=30),
    ) -> EvidenceVersion:
        """由获授权的人撤销版本并创建延迟删除墓碑。"""

        if actor.kind is not ActorKind.HUMAN or actor.actor_id not in self.allowed_revokers:
            raise EvidencePermissionDenied("只有获授权的真实员工可以撤销证据版本")
        if not reason.strip():
            raise ValueError("撤销原因不能为空")
        if retention < timedelta(0):
            raise ValueError("retention 不能小于 0")
        occurred_at = _utc(now)
        return self.metadata.revoke_version(
            version_id,
            actor_id=actor.actor_id,
            reason=reason,
            occurred_at=occurred_at,
            earliest_delete_at=occurred_at + retention,
        )

    def cleanup_tombstones(self, *, now: datetime | None = None) -> int:
        """只删除已到期且没有 ACTIVE 引用的明确对象版本。"""

        occurred_at = _utc(now)
        deleted = 0
        for tombstone in self.metadata.list_due_tombstones(occurred_at):
            if not self.metadata.claim_object_deletion(
                tombstone,
                occurred_at=occurred_at,
            ):
                continue
            self.objects.delete(
                tombstone.object_key,
                version_id=tombstone.object_version_id,
            )
            self.metadata.mark_object_deleted(
                tombstone.bucket,
                tombstone.object_key,
                tombstone.object_version_id,
                occurred_at=occurred_at,
            )
            deleted += 1
        return deleted

    def reconcile(
        self,
        *,
        now: datetime | None = None,
        orphan_grace: timedelta = timedelta(hours=24),
        cleanup: bool = False,
    ) -> ReconciliationReport:
        """对账元数据、完整对象、孤儿对象和未完成分片上传。"""

        started_at = _utc(now)
        if orphan_grace < timedelta(0):
            raise ValueError("orphan_grace 不能小于 0")
        cutoff = started_at - orphan_grace
        expired = self.metadata.expire_due_uploads(started_at)
        for upload in expired:
            if upload.temporary_object_version_id:
                self.objects.delete(
                    upload.temporary_object_key,
                    version_id=upload.temporary_object_version_id,
                )

        aborted = 0
        for multipart in self.objects.list_multipart_uploads(TEMPORARY_OBJECT_PREFIX):
            if cleanup and multipart.initiated_at <= cutoff:
                self.objects.abort_multipart_upload(multipart.key, multipart.upload_id)
                aborted += 1

        uploads = self.metadata.list_uploads()
        versions = self.metadata.list_all_versions()
        issues: list[ReconciliationIssue] = []
        referenced_objects = {
            (version.object_key, version.object_version_id)
            for version in versions
            if version.object_deleted_at is None
        }
        pending_content_keys = {
            upload.final_object_key
            for upload in uploads
            if upload.state is EvidenceState.VERIFIED and upload.final_object_key
        }
        live_temporary_keys = {
            upload.temporary_object_key
            for upload in uploads
            if upload.state
            in {
                EvidenceState.UPLOADING,
                EvidenceState.QUARANTINED,
                EvidenceState.VERIFIED,
            }
        }

        checked: set[tuple[str, str]] = set()
        for version in versions:
            if version.object_deleted_at is not None:
                continue
            identity = (version.object_key, version.object_version_id)
            if identity in checked:
                continue
            checked.add(identity)
            info = self.objects.head(version.object_key, version_id=version.object_version_id)
            if info is None:
                code = (
                    "active_object_missing"
                    if version.state is EvidenceState.ACTIVE
                    else "retained_object_missing"
                )
                issues.append(ReconciliationIssue(code, version.object_key, version.version_id))
                continue
            actual_sha256, actual_size = self._object_digest(
                version.object_key,
                version_id=version.object_version_id,
            )
            if actual_sha256 != version.sha256 or actual_size != version.size_bytes:
                issues.append(
                    ReconciliationIssue(
                        "object_integrity_mismatch",
                        version.object_key,
                        version.version_id,
                    )
                )

        deleted_orphans = 0
        for prefix in (TEMPORARY_OBJECT_PREFIX, CONTENT_OBJECT_PREFIX):
            for info in self.objects.list_objects(prefix):
                is_referenced = (
                    info.key in live_temporary_keys
                    if prefix == TEMPORARY_OBJECT_PREFIX
                    else (
                        (info.key, info.version_id) in referenced_objects
                        or info.key in pending_content_keys
                    )
                )
                if is_referenced or info.last_modified > cutoff:
                    continue
                if cleanup:
                    self.objects.delete(info.key, version_id=info.version_id)
                    deleted_orphans += 1
                else:
                    issues.append(
                        ReconciliationIssue("orphan_object", info.key, info.version_id)
                    )

        report = ReconciliationReport(
            run_id=f"ER-{uuid4().hex.upper()}",
            expired_uploads=len(expired),
            aborted_multipart_uploads=aborted,
            deleted_orphan_objects=deleted_orphans,
            issues=tuple(issues),
        )
        self.metadata.record_reconciliation(
            report,
            started_at=started_at,
            completed_at=started_at,
            cleanup_enabled=cleanup,
        )
        return report

    def _verify_quarantined(
        self,
        upload: EvidenceUpload,
        *,
        detected_media_type: str,
        security_scan_passed: bool,
        occurred_at: datetime,
    ) -> EvidenceUpload:
        self._require_state(upload, EvidenceState.QUARANTINED)
        info = self.objects.head(
            upload.temporary_object_key,
            version_id=upload.temporary_object_version_id,
        )
        if info is None:
            self._reject(
                upload,
                "object_missing",
                "隔离对象不存在",
                actual_sha256=None,
                actual_size_bytes=None,
                detected_media_type=detected_media_type,
                occurred_at=occurred_at,
            )
        actual_sha256, actual_size = self._object_digest(
            upload.temporary_object_key,
            version_id=upload.temporary_object_version_id,
        )
        failures: list[tuple[str, str]] = []
        if actual_sha256 != upload.expected_sha256:
            failures.append(("sha256_mismatch", "SHA-256 与上传声明不一致"))
        if actual_size != upload.expected_size_bytes or actual_size != info.size_bytes:
            failures.append(("size_mismatch", "对象大小与上传声明不一致"))
        if detected_media_type != upload.expected_media_type:
            failures.append(("media_type_mismatch", "MIME 与上传声明不一致"))
        if not security_scan_passed:
            failures.append(("security_check_failed", "对象未通过安全检查"))
        if failures:
            code, reason = failures[0]
            self._reject(
                upload,
                code,
                reason,
                actual_sha256=actual_sha256,
                actual_size_bytes=actual_size,
                detected_media_type=detected_media_type,
                occurred_at=occurred_at,
            )
        final_key = f"{CONTENT_OBJECT_PREFIX}{actual_sha256[:2]}/{actual_sha256}"
        return self.metadata.mark_verified(
            upload.upload_id,
            actual_sha256=actual_sha256,
            actual_size_bytes=actual_size,
            detected_media_type=detected_media_type,
            final_object_key=final_key,
            occurred_at=occurred_at,
        )

    def _activate_verified(
        self,
        upload: EvidenceUpload,
        *,
        occurred_at: datetime,
    ) -> EvidenceVersion:
        self._require_state(upload, EvidenceState.VERIFIED)
        if not upload.final_object_key or not upload.actual_sha256:
            raise EvidenceIntegrityError("VERIFIED 元数据不完整")
        existing = self.objects.head(upload.final_object_key)
        if existing is None:
            existing = self.objects.copy(
                upload.temporary_object_key,
                upload.final_object_key,
                source_version_id=upload.temporary_object_version_id,
                content_type=upload.detected_media_type or upload.expected_media_type,
                metadata={"sha256": upload.actual_sha256},
            )
        actual_sha256, actual_size = self._object_digest(
            upload.final_object_key,
            version_id=existing.version_id,
        )
        if actual_sha256 != upload.actual_sha256 or actual_size != upload.actual_size_bytes:
            raise EvidenceIntegrityError("内容寻址对象与 VERIFIED 元数据不一致")
        version = self.metadata.activate_upload(
            upload.upload_id,
            bucket=self.objects.bucket,
            object_info=existing,
            occurred_at=occurred_at,
        )
        if upload.temporary_object_version_id:
            try:
                self.objects.delete(
                    upload.temporary_object_key,
                    version_id=upload.temporary_object_version_id,
                )
            except Exception as error:
                raise EvidenceIntegrityError(
                    "对象已经生效，但临时对象清理失败，需运行对账"
                ) from error
        return version

    def _reject(
        self,
        upload: EvidenceUpload,
        code: str,
        reason: str,
        *,
        actual_sha256: str | None,
        actual_size_bytes: int | None,
        detected_media_type: str | None,
        occurred_at: datetime,
    ) -> None:
        self.metadata.reject_upload(
            upload.upload_id,
            code=code,
            reason=reason,
            actual_sha256=actual_sha256,
            actual_size_bytes=actual_size_bytes,
            detected_media_type=detected_media_type,
            occurred_at=occurred_at,
        )
        if upload.temporary_object_version_id:
            try:
                self.objects.delete(
                    upload.temporary_object_key,
                    version_id=upload.temporary_object_version_id,
                )
            except Exception as error:
                raise EvidenceRejectedError(
                    f"{reason}；临时对象清理失败，需运行对账"
                ) from error
        raise EvidenceRejectedError(reason)

    def _object_digest(
        self,
        key: str,
        *,
        version_id: str | None,
    ) -> tuple[str, int]:
        digest = hashlib.sha256()
        size = 0
        for chunk in self.objects.iter_chunks(key, version_id=version_id):
            digest.update(chunk)
            size += len(chunk)
        return digest.hexdigest(), size

    @staticmethod
    def _require_state(upload: EvidenceUpload, *expected: EvidenceState) -> None:
        if upload.state not in expected:
            names = " 或 ".join(state.value for state in expected)
            raise EvidenceStateError(
                f"上传 {upload.upload_id} 必须处于 {names}，当前为 {upload.state.value}"
            )


def _utc(value: datetime | None) -> datetime:
    """规范为带时区的 UTC 时间。"""

    current = value or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("时间必须包含时区")
    return current.astimezone(UTC)


__all__ = [
    "ALLOWED_EVIDENCE_TRANSITIONS",
    "CONTENT_OBJECT_PREFIX",
    "TEMPORARY_OBJECT_PREFIX",
    "EvidenceAdmissionRequest",
    "EvidenceAdmissionService",
    "EvidenceError",
    "EvidenceIntegrityError",
    "EvidencePermissionDenied",
    "EvidenceRejectedError",
    "EvidenceState",
    "EvidenceStateError",
    "EvidenceTombstone",
    "EvidenceUpload",
    "EvidenceVersion",
    "MultipartUploadInfo",
    "ObjectInfo",
    "ReconciliationIssue",
    "ReconciliationReport",
]
