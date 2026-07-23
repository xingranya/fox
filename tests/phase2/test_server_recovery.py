"""PostgreSQL、对象版本和投影联合恢复演练。"""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import psycopg

from brand_os.backup import BackupError
from brand_os.domain import (
    Actor,
    ActorKind,
    CommandContext,
    ProposalDraft,
    ProposalReview,
    ReviewAction,
)
from brand_os.object_evidence import EvidenceAdmissionRequest, EvidenceAdmissionService
from brand_os.postgresql_backup import PostgreSQLBackupService
from brand_os.postgresql_evidence import PostgreSQLEvidenceRepository
from brand_os.postgresql_store import PostgreSQLCanonicalStore
from brand_os.s3_store import S3ObjectStore
from brand_os.server_recovery import RecoveryIntegrityError, ServerRecoveryDrill
from phase2.postgresql_test_runtime import TemporaryPostgreSQL
from phase2.s3_test_runtime import TemporaryS3


ROOT = Path(__file__).parents[2]
CONTRACT_PATH = ROOT / "contracts" / "phase2" / "server-recovery.json"
SCHEMA_PATH = ROOT / "schemas" / "phase2" / "server-recovery.schema.json"
POSTGRESQL: TemporaryPostgreSQL | None = None
S3: TemporaryS3 | None = None


def setUpModule() -> None:
    """为恢复测试启动仅监听回环地址的临时 PostgreSQL 和 S3。"""

    global POSTGRESQL, S3
    try:
        POSTGRESQL = TemporaryPostgreSQL()
    except RuntimeError as error:
        raise unittest.SkipTest(str(error)) from error
    POSTGRESQL.start()
    S3 = TemporaryS3()
    S3.start()


def tearDownModule() -> None:
    """测试结束后停止并清理临时服务。"""

    if S3 is not None:
        S3.stop()
    if POSTGRESQL is not None:
        POSTGRESQL.stop()


class ServerRecoveryContractTest(unittest.TestCase):
    """验证恢复边界没有把逻辑备份或 AI 提升为生产权威。"""

    def test_contract_freezes_recovery_and_confirmation_boundaries(self) -> None:
        contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

        self.assertEqual(contract["schema_version"], "server-recovery.v1")
        self.assertTrue(contract["postgresql_backup"]["consistent_exported_snapshot"])
        self.assertFalse(contract["postgresql_backup"]["logical_backup_is_pitr"])
        self.assertTrue(contract["restore"]["target_must_be_empty"])
        self.assertFalse(contract["restore"]["restore_in_place"])
        self.assertTrue(contract["object_versions"]["explicit_version_id_required"])
        self.assertFalse(
            contract["object_versions"]["same_bucket_versioning_is_independent_backup"]
        )
        self.assertFalse(contract["authority"]["ai_may_confirm_slo"])
        self.assertTrue(contract["authority"]["fox_confirmation_required"])
        self.assertFalse(contract["measurement"]["local_fixture_is_production_evidence"])
        self.assertEqual(schema["properties"]["schema_version"]["const"], "server-recovery.v1")


class ServerRecoveryDrillTest(unittest.TestCase):
    """通过真实临时数据库和 S3 HTTP 服务完成恢复与故障注入。"""

    def setUp(self) -> None:
        assert POSTGRESQL is not None
        assert S3 is not None
        self.temporary = tempfile.TemporaryDirectory(prefix="brand-os-recovery-test-")
        self.backup_root = Path(self.temporary.name) / "backups"
        self.source_database, self.source_dsn = POSTGRESQL.create_database()
        self.target_databases: list[str] = []
        self.store = PostgreSQLCanonicalStore(self.source_dsn)
        self.fox = Actor(ActorKind.HUMAN, "Fox")
        self.ai = Actor(ActorKind.AI, "codex")
        self.store.create_project(
            CommandContext("hongri", self.fox, "project-create", 0),
            "恢复演练项目",
        )
        self.store.register_outbox_consumer("recovery-index")
        created = self.store.create_proposal(
            CommandContext("hongri", self.ai, "proposal-create", 1),
            self.proposal("proposal-1"),
        )
        self.store.review_proposal(
            CommandContext(
                "hongri",
                self.fox,
                "proposal-approve",
                created.project_version,
            ),
            ProposalReview("proposal-1", ReviewAction.APPROVE, "Fox 明确确认"),
        )

        self.repository = PostgreSQLEvidenceRepository(self.source_dsn)
        self.bucket, self.raw_s3 = S3.create_versioned_bucket()
        self.objects = S3ObjectStore(
            endpoint_url=S3.endpoint_url,
            bucket=self.bucket,
            access_key=S3.access_key,
            secret_key=S3.secret_key,
            region=S3.region,
        )
        self.evidence_service = EvidenceAdmissionService(
            metadata=self.repository,
            objects=self.objects,
            allowed_revokers=("Fox",),
        )
        self.content = b"authoritative evidence for recovery"
        self.evidence_version = self.admit_evidence(self.content)
        self.backups = PostgreSQLBackupService(
            self.source_dsn,
            self.backup_root,
            binary_directory=POSTGRESQL.binary_directory,
        )

    def tearDown(self) -> None:
        assert POSTGRESQL is not None
        for database_name in reversed(self.target_databases):
            POSTGRESQL.drop_database(database_name)
        POSTGRESQL.drop_database(self.source_database)
        self.temporary.cleanup()

    @staticmethod
    def proposal(proposal_id: str) -> ProposalDraft:
        return ProposalDraft(
            proposal_id=proposal_id,
            proposal_kind="create",
            classification="DECISION_CANDIDATE",
            subject_id=f"decision-{proposal_id}",
            before=None,
            after={"id": f"decision-{proposal_id}", "statement": "采用已确认方向"},
            reason="来自恢复演练 Fixture",
            impact_scope="恢复演练",
            evidence_refs=("evidence:fixture#1",),
        )

    def create_target(self) -> tuple[str, str]:
        assert POSTGRESQL is not None
        database_name, dsn = POSTGRESQL.create_database()
        self.target_databases.append(database_name)
        return database_name, dsn

    def admit_evidence(self, content: bytes):
        now = datetime.now(UTC)
        upload = self.evidence_service.begin_upload(
            EvidenceAdmissionRequest(
                project_id="hongri",
                logical_source_id="source-recovery",
                original_filename="恢复证据.md",
                expected_sha256=hashlib.sha256(content).hexdigest(),
                expected_size_bytes=len(content),
                expected_media_type="text/markdown",
                confidentiality="P2",
                idempotency_key="recovery-evidence",
            ),
            now=now,
        )
        self.evidence_service.upload_and_quarantine(
            upload.upload_id,
            BytesIO(content),
            now=now,
        )
        return self.evidence_service.verify_and_activate(
            upload.upload_id,
            detected_media_type="text/markdown",
            security_scan_passed=True,
            now=now,
        )

    def test_restore_rebuilds_projections_and_reads_explicit_object_version(self) -> None:
        backup_id = self.backups.create()
        manifest_text = (
            self.backup_root / backup_id / "manifest.json"
        ).read_text(encoding="utf-8")
        manifest = json.loads(manifest_text)
        self.assertEqual(manifest["schema_version"], "postgresql-backup.v1")
        self.assertEqual(manifest["snapshot"]["database_schema_version"], 11)
        self.assertNotIn(self.source_database, manifest_text)
        self.assertNotIn("恢复演练项目", manifest_text)
        self.assertNotIn("postgresql://", manifest_text)

        # 删除当前键只产生删除标记；恢复必须仍按数据库记录的明确 VersionId 读取旧正文。
        self.raw_s3.delete_object(
            Bucket=self.bucket,
            Key=self.evidence_version.object_key,
        )
        self.assertIsNone(self.objects.head(self.evidence_version.object_key))
        self.assertIsNotNone(
            self.objects.head(
                self.evidence_version.object_key,
                version_id=self.evidence_version.object_version_id,
            )
        )

        # 备份点之后的新事件不能混入恢复目标。
        self.store.create_proposal(
            CommandContext("hongri", self.ai, "late-proposal", 3),
            self.proposal("proposal-after-backup"),
        )
        _, target_dsn = self.create_target()
        report = ServerRecoveryDrill(
            backup=self.backups,
            objects=self.objects,
        ).run(backup_id, target_dsn)

        restored = PostgreSQLCanonicalStore(target_dsn)
        restored_evidence = PostgreSQLEvidenceRepository(target_dsn)
        restored_service = EvidenceAdmissionService(
            metadata=restored_evidence,
            objects=self.objects,
        )
        self.assertEqual(restored.get_project_version("hongri"), 3)
        self.assertEqual(
            [item["proposal_id"] for item in restored.list_proposals("hongri")],
            ["proposal-1"],
        )
        restored_version = restored_evidence.list_all_versions()[0]
        self.assertEqual(
            b"".join(restored_service.stream_active(restored_version.version_id)),
            self.content,
        )
        self.assertEqual(report.result, "passed")
        self.assertEqual(report.project_count, 1)
        self.assertEqual(report.human_action_count, 1)
        self.assertEqual(report.state_item_count, 1)
        self.assertEqual(report.evidence_version_count, 1)
        self.assertEqual(report.object_issue_count, 0)
        self.assertFalse(report.production_pitr_verified)
        self.assertFalse(report.production_slo_confirmed)

        # 归档恢复的序列必须允许继续追加事件，不能与旧 global_position 冲突。
        appended = restored.create_proposal(
            CommandContext("hongri", self.ai, "post-restore-write", 3),
            self.proposal("proposal-after-restore"),
        )
        self.assertEqual(appended.project_version, 4)
        self.assertEqual(len(restored.list_events("hongri")), 4)

    def test_tampered_archive_is_rejected_before_target_changes(self) -> None:
        backup_id = self.backups.create()
        archive_path = self.backup_root / backup_id / "database.dump"
        archive_path.write_bytes(archive_path.read_bytes() + b"tampered")
        _, target_dsn = self.create_target()

        with self.assertRaisesRegex(BackupError, "哈希或大小"):
            self.backups.restore(backup_id, target_dsn)

        with psycopg.connect(target_dsn, autocommit=True) as connection:
            table_count = connection.execute(
                """
                SELECT COUNT(*) FROM information_schema.tables
                WHERE table_schema = current_schema()
                """
            ).fetchone()[0]
        self.assertEqual(table_count, 0)

    def test_nonempty_target_is_rejected_instead_of_overwritten(self) -> None:
        backup_id = self.backups.create()
        _, target_dsn = self.create_target()
        target = PostgreSQLCanonicalStore(target_dsn)
        target.create_project(
            CommandContext("existing", self.fox, "existing-project", 0),
            "已有数据",
        )

        with self.assertRaisesRegex(BackupError, "空数据库"):
            self.backups.restore(backup_id, target_dsn)
        self.assertEqual(target.get_project_version("existing"), 1)

    def test_missing_active_object_version_blocks_recovery_gate(self) -> None:
        backup_id = self.backups.create()
        self.raw_s3.delete_object(
            Bucket=self.bucket,
            Key=self.evidence_version.object_key,
            VersionId=self.evidence_version.object_version_id,
        )
        _, target_dsn = self.create_target()

        with self.assertRaisesRegex(
            RecoveryIntegrityError,
            "active_object_missing",
        ):
            ServerRecoveryDrill(
                backup=self.backups,
                objects=self.objects,
            ).run(backup_id, target_dsn)

        restored_evidence = PostgreSQLEvidenceRepository(target_dsn)
        reconciliation = restored_evidence.list_reconciliation_runs()
        self.assertEqual(len(reconciliation), 1)
        self.assertEqual(reconciliation[0]["issue_count"], 1)
        self.assertIn("active_object_missing", reconciliation[0]["details_json"])

    def test_nonreplayable_approval_event_blocks_projection_rebuild(self) -> None:
        with psycopg.connect(self.source_dsn, autocommit=True) as connection:
            connection.execute(
                """
                UPDATE events SET actor_kind = 'AI', actor_id = 'tampered-runtime'
                WHERE event_type = 'PROPOSAL_APPROVED'
                """
            )
        backup_id = self.backups.create()
        _, target_dsn = self.create_target()

        with self.assertRaisesRegex(
            RecoveryIntegrityError,
            "无法从正式事件重建投影",
        ):
            ServerRecoveryDrill(
                backup=self.backups,
                objects=self.objects,
            ).run(backup_id, target_dsn)


if __name__ == "__main__":
    unittest.main()
