"""S3 兼容原件版本、哈希和准入状态机集成测试。"""

from __future__ import annotations

import hashlib
import json
import unittest
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path

from brand_os.domain import Actor, ActorKind, CommandContext
from brand_os.object_evidence import (
    EvidenceAdmissionRequest,
    EvidenceAdmissionService,
    EvidenceIntegrityError,
    EvidencePermissionDenied,
    EvidenceRejectedError,
    EvidenceState,
    EvidenceStateError,
)
from brand_os.postgresql_evidence import PostgreSQLEvidenceRepository
from brand_os.postgresql_store import PostgreSQLCanonicalStore
from brand_os.s3_store import S3ObjectStore
from phase2.postgresql_test_runtime import TemporaryPostgreSQL
from phase2.s3_test_runtime import TemporaryS3


ROOT = Path(__file__).parents[2]
CONTRACT_PATH = ROOT / "contracts" / "phase2" / "object-evidence.json"
POSTGRESQL: TemporaryPostgreSQL | None = None
S3: TemporaryS3 | None = None


def setUpModule() -> None:
    """为本模块启动隔离 PostgreSQL 与 S3 兼容服务。"""

    global POSTGRESQL, S3
    try:
        POSTGRESQL = TemporaryPostgreSQL()
    except RuntimeError as error:
        raise unittest.SkipTest(str(error)) from error
    POSTGRESQL.start()
    S3 = TemporaryS3()
    S3.start()


def tearDownModule() -> None:
    """无论测试结果如何都停止两项临时服务。"""

    if S3 is not None:
        S3.stop()
    if POSTGRESQL is not None:
        POSTGRESQL.stop()


class ObjectEvidenceContractTest(unittest.TestCase):
    """验证 F2.3 机器契约保持人工确认和可恢复一致性边界。"""

    def test_contract_freezes_states_transitions_and_authority(self) -> None:
        contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))

        self.assertEqual(contract["schema_version"], "object-evidence.v1")
        self.assertTrue(contract["bucket_versioning_required"])
        self.assertFalse(contract["consistency"]["distributed_transaction"])
        self.assertTrue(contract["authority"]["active_only_for_formal_evidence"])
        self.assertFalse(contract["authority"]["temporary_objects_are_evidence"])
        self.assertFalse(contract["authority"]["agent_may_revoke"])
        self.assertFalse(contract["integrity"]["silent_overwrite"])
        self.assertEqual(
            {tuple(item) for item in contract["transitions"]},
            {
                ("UPLOADING", "QUARANTINED"),
                ("UPLOADING", "EXPIRED"),
                ("QUARANTINED", "VERIFIED"),
                ("QUARANTINED", "REJECTED"),
                ("VERIFIED", "ACTIVE"),
                ("ACTIVE", "REVOKED"),
            },
        )
        self.assertFalse(contract["migrates_hongri_data"])


class ObjectEvidenceStoreTest(unittest.TestCase):
    """通过真实 PostgreSQL 与临时 S3 HTTP 服务验证对象准入。"""

    def setUp(self) -> None:
        assert POSTGRESQL is not None
        assert S3 is not None
        self.database_name, self.dsn = POSTGRESQL.create_database()
        canonical = PostgreSQLCanonicalStore(self.dsn)
        self.fox = Actor(ActorKind.HUMAN, "Fox")
        canonical.create_project(
            CommandContext("hongri", self.fox, "project-create", 0),
            "测试项目",
        )
        self.repository = PostgreSQLEvidenceRepository(self.dsn)
        self.bucket, self.raw_s3 = S3.create_versioned_bucket()
        self.objects = S3ObjectStore(
            endpoint_url=S3.endpoint_url,
            bucket=self.bucket,
            access_key=S3.access_key,
            secret_key=S3.secret_key,
            region=S3.region,
        )
        self.service = EvidenceAdmissionService(
            metadata=self.repository,
            objects=self.objects,
            allowed_revokers=("Fox",),
        )
        self.now = datetime.now(UTC)

    def tearDown(self) -> None:
        assert POSTGRESQL is not None
        POSTGRESQL.drop_database(self.database_name)

    @staticmethod
    def digest(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    def request(
        self,
        content: bytes,
        *,
        key: str,
        logical_source_id: str = "source-1",
        filename: str = "同名资料.md",
        sha256: str | None = None,
        size: int | None = None,
        media_type: str = "text/markdown",
    ) -> EvidenceAdmissionRequest:
        return EvidenceAdmissionRequest(
            project_id="hongri",
            logical_source_id=logical_source_id,
            original_filename=filename,
            expected_sha256=sha256 or self.digest(content),
            expected_size_bytes=len(content) if size is None else size,
            expected_media_type=media_type,
            confidentiality="P2",
            idempotency_key=key,
        )

    def admit(
        self,
        content: bytes,
        *,
        key: str,
        logical_source_id: str = "source-1",
        filename: str = "同名资料.md",
    ):
        upload = self.service.begin_upload(
            self.request(
                content,
                key=key,
                logical_source_id=logical_source_id,
                filename=filename,
            ),
            now=self.now,
        )
        self.service.upload_and_quarantine(upload.upload_id, BytesIO(content), now=self.now)
        return self.service.verify_and_activate(
            upload.upload_id,
            detected_media_type="text/markdown",
            security_scan_passed=True,
            now=self.now,
        )

    def test_schema_v7_is_repeatable_and_contains_object_tables(self) -> None:
        self.assertEqual(self.repository.schema_version, 9)
        self.assertTrue(self.repository.quick_check())
        reopened = PostgreSQLEvidenceRepository(self.dsn)
        self.assertEqual(reopened.schema_version, 9)
        self.assertTrue(reopened.quick_check())

    def test_bucket_without_versioning_is_rejected_before_upload(self) -> None:
        assert S3 is not None
        bucket = f"without-versioning-{self.database_name.replace('_', '-')}"
        self.raw_s3.create_bucket(Bucket=bucket)
        objects = S3ObjectStore(
            endpoint_url=S3.endpoint_url,
            bucket=bucket,
            access_key=S3.access_key,
            secret_key=S3.secret_key,
            region=S3.region,
        )

        with self.assertRaisesRegex(EvidenceIntegrityError, "版本控制"):
            EvidenceAdmissionService(metadata=self.repository, objects=objects)

    def test_begin_upload_is_idempotent_but_same_key_cannot_change_meaning(self) -> None:
        content = b"first"
        request = self.request(content, key="upload-idempotent")

        first = self.service.begin_upload(request, now=self.now)
        replay = self.service.begin_upload(request, now=self.now)

        self.assertEqual(first.upload_id, replay.upload_id)
        with self.assertRaisesRegex(EvidenceIntegrityError, "幂等键"):
            self.service.begin_upload(
                self.request(b"changed", key="upload-idempotent"),
                now=self.now,
            )

    def test_interrupted_multipart_upload_expires_and_is_aborted(self) -> None:
        content = b"partial"
        upload = self.service.begin_upload(
            self.request(content, key="multipart-interrupted"),
            now=self.now,
            upload_ttl=timedelta(minutes=5),
        )
        multipart = self.raw_s3.create_multipart_upload(
            Bucket=self.bucket,
            Key=upload.temporary_object_key,
            ContentType="text/markdown",
        )
        self.raw_s3.upload_part(
            Bucket=self.bucket,
            Key=upload.temporary_object_key,
            UploadId=multipart["UploadId"],
            PartNumber=1,
            Body=b"unfinished-part",
        )

        report = self.service.reconcile(
            now=self.now + timedelta(hours=1),
            orphan_grace=timedelta(0),
            cleanup=True,
        )

        current = self.repository.get_upload(upload.upload_id)
        remaining = self.raw_s3.list_multipart_uploads(Bucket=self.bucket)
        self.assertEqual(current.state, EvidenceState.EXPIRED)
        self.assertEqual(report.expired_uploads, 1)
        self.assertEqual(report.aborted_multipart_uploads, 1)
        self.assertNotIn("Uploads", remaining)
        self.assertEqual(self.repository.list_versions("hongri"), ())

    def test_same_filename_with_different_content_creates_distinct_versions(self) -> None:
        first = self.admit(b"version-one", key="version-1")
        second = self.admit(b"version-two", key="version-2")

        versions = self.repository.list_versions("hongri", "source-1")
        self.assertEqual([item.version_number for item in versions], [1, 2])
        self.assertEqual([item.original_filename for item in versions], ["同名资料.md"] * 2)
        self.assertNotEqual(first.sha256, second.sha256)
        self.assertNotEqual(first.object_key, second.object_key)
        self.assertEqual(b"".join(self.service.stream_active(first.version_id)), b"version-one")
        self.assertEqual(b"".join(self.service.stream_active(second.version_id)), b"version-two")

    def test_hash_size_media_type_and_security_failures_are_rejected(self) -> None:
        cases = (
            ("hash", {"sha256": "0" * 64}, "text/markdown", True),
            ("size", {"size": 99}, "text/markdown", True),
            ("mime", {}, "application/pdf", True),
            ("security", {}, "text/markdown", False),
        )
        for name, overrides, detected_media_type, security_scan_passed in cases:
            with self.subTest(name=name):
                content = f"failure-{name}".encode()
                upload = self.service.begin_upload(
                    self.request(content, key=f"reject-{name}", **overrides),
                    now=self.now,
                )
                self.service.upload_and_quarantine(
                    upload.upload_id,
                    BytesIO(content),
                    now=self.now,
                )
                with self.assertRaises(EvidenceRejectedError):
                    self.service.verify_and_activate(
                        upload.upload_id,
                        detected_media_type=detected_media_type,
                        security_scan_passed=security_scan_passed,
                        now=self.now,
                    )
                current = self.repository.get_upload(upload.upload_id)
                self.assertEqual(current.state, EvidenceState.REJECTED)
                self.assertIsNone(self.objects.head(upload.temporary_object_key))

        self.assertEqual(self.repository.list_versions("hongri"), ())

    def test_only_quarantined_upload_can_be_verified(self) -> None:
        upload = self.service.begin_upload(
            self.request(b"not-uploaded", key="illegal-transition"),
            now=self.now,
        )

        with self.assertRaisesRegex(EvidenceStateError, "QUARANTINED"):
            self.service.verify_and_activate(
                upload.upload_id,
                detected_media_type="text/markdown",
                security_scan_passed=True,
                now=self.now,
            )
        self.assertEqual(
            self.repository.get_upload(upload.upload_id).state,
            EvidenceState.UPLOADING,
        )

    def test_content_address_is_reused_without_creating_a_second_object_version(self) -> None:
        content = b"same-content"
        first = self.admit(content, key="same-content-1")
        second = self.admit(content, key="same-content-2")
        listed = self.raw_s3.list_object_versions(
            Bucket=self.bucket,
            Prefix=first.object_key,
        )

        self.assertEqual(first.object_key, second.object_key)
        self.assertEqual(first.object_version_id, second.object_version_id)
        self.assertEqual(len(listed.get("Versions", [])), 1)

    def test_reconciliation_cleans_orphan_but_preserves_and_checks_active_object(self) -> None:
        active = self.admit(b"active-content", key="active")
        orphan_key = "evidence/sha256/ff/" + "f" * 64
        self.raw_s3.put_object(
            Bucket=self.bucket,
            Key=orphan_key,
            Body=b"orphan",
            ContentType="application/octet-stream",
        )

        report = self.service.reconcile(
            now=datetime.now(UTC) + timedelta(minutes=1),
            orphan_grace=timedelta(0),
            cleanup=True,
        )

        self.assertEqual(report.deleted_orphan_objects, 1)
        self.assertIsNone(self.objects.head(orphan_key))
        self.assertIsNotNone(
            self.objects.head(active.object_key, version_id=active.object_version_id)
        )
        self.assertFalse(report.issues)
        self.assertEqual(len(self.repository.list_reconciliation_runs()), 1)

    def test_reconciliation_reports_missing_active_object_without_silent_state_change(self) -> None:
        active = self.admit(b"will-be-missing", key="missing")
        self.raw_s3.delete_object(
            Bucket=self.bucket,
            Key=active.object_key,
            VersionId=active.object_version_id,
        )

        report = self.service.reconcile(now=datetime.now(UTC), cleanup=False)

        self.assertIn("active_object_missing", {issue.code for issue in report.issues})
        self.assertEqual(
            self.repository.get_version(active.version_id).state,
            EvidenceState.ACTIVE,
        )

    def test_revocation_requires_human_and_delayed_tombstone_cleanup(self) -> None:
        active = self.admit(b"revoked-content", key="revoke")
        with self.assertRaises(EvidencePermissionDenied):
            self.service.revoke(
                active.version_id,
                Actor(ActorKind.AI, "codex"),
                reason="AI 不能撤销",
                now=self.now,
                retention=timedelta(days=1),
            )

        revoked = self.service.revoke(
            active.version_id,
            self.fox,
            reason="Fox 明确撤销",
            now=self.now,
            retention=timedelta(days=1),
        )

        self.assertEqual(revoked.state, EvidenceState.REVOKED)
        with self.assertRaises(EvidenceStateError):
            tuple(self.service.stream_active(active.version_id))
        self.assertEqual(
            self.service.cleanup_tombstones(now=self.now + timedelta(hours=12)),
            0,
        )
        self.assertIsNotNone(
            self.objects.head(active.object_key, version_id=active.object_version_id)
        )
        self.assertEqual(
            self.service.cleanup_tombstones(now=self.now + timedelta(days=2)),
            1,
        )
        self.assertIsNone(
            self.objects.head(active.object_key, version_id=active.object_version_id)
        )

    def test_claimed_deletion_blocks_reuse_until_old_object_is_removed(self) -> None:
        content = b"same-content-after-revocation"
        first = self.admit(content, key="claimed-delete-first")
        self.service.revoke(
            first.version_id,
            self.fox,
            reason="替换前撤销",
            now=self.now,
            retention=timedelta(0),
        )
        tombstone = self.repository.list_due_tombstones(self.now)[0]
        self.assertTrue(
            self.repository.claim_object_deletion(
                tombstone,
                occurred_at=self.now,
            )
        )

        upload = self.service.begin_upload(
            self.request(content, key="claimed-delete-second"),
            now=self.now,
        )
        self.service.upload_and_quarantine(upload.upload_id, BytesIO(content), now=self.now)
        with self.assertRaisesRegex(EvidenceStateError, "延迟删除"):
            self.service.verify_and_activate(
                upload.upload_id,
                detected_media_type="text/markdown",
                security_scan_passed=True,
                now=self.now,
            )

        self.assertEqual(self.service.cleanup_tombstones(now=self.now), 1)
        second = self.service.verify_and_activate(
            upload.upload_id,
            detected_media_type="text/markdown",
            security_scan_passed=True,
            now=self.now,
        )
        self.assertEqual(second.version_number, 2)
        self.assertNotEqual(first.object_version_id, second.object_version_id)
        self.assertEqual(b"".join(self.service.stream_active(second.version_id)), content)


if __name__ == "__main__":
    unittest.main()
