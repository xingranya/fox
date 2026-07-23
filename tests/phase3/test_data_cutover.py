"""SQLite 到 PostgreSQL/S3 一次性迁移和权威切换测试。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import psycopg

from brand_os.data_cutover import (
    DataCutoverIntegrityError,
    DataCutoverPermissionDenied,
    DataCutoverService,
    DataCutoverTargetNotEmpty,
)
from brand_os.domain import (
    Actor,
    ActorKind,
    CommandContext,
    ProposalDraft,
    ProposalReview,
    ReviewAction,
)
from brand_os.evidence import LocalEvidenceStore
from brand_os.manifest_import import load_source_manifest
from brand_os.object_evidence import EvidenceAdmissionService, EvidenceState
from brand_os.postgresql_evidence import PostgreSQLEvidenceRepository
from brand_os.postgresql_store import PostgreSQLCanonicalStore
from brand_os.s3_store import S3ObjectStore
from brand_os.sqlite_store import AuthorityCutoverReadOnly, SQLiteCanonicalStore
from brand_os.workspace import WorkspaceLayout
from phase2.postgresql_test_runtime import TemporaryPostgreSQL
from phase2.s3_test_runtime import TemporaryS3


ROOT = Path(__file__).parents[2]
CONTRACT_PATH = ROOT / "contracts" / "phase3" / "data-cutover.json"
SCHEMA_PATH = ROOT / "schemas" / "phase3" / "data-cutover.schema.json"
POSTGRESQL: TemporaryPostgreSQL | None = None
S3: TemporaryS3 | None = None


def setUpModule() -> None:
    """启动只监听回环地址且会自动退出的临时 PostgreSQL 和 Moto S3。"""

    global POSTGRESQL, S3
    try:
        POSTGRESQL = TemporaryPostgreSQL()
    except RuntimeError as error:
        raise unittest.SkipTest(str(error)) from error
    POSTGRESQL.start()
    S3 = TemporaryS3()
    S3.start()


def tearDownModule() -> None:
    """无论测试结果如何都停止临时依赖。"""

    if S3 is not None:
        S3.stop()
    if POSTGRESQL is not None:
        POSTGRESQL.stop()


class DataCutoverContractTest(unittest.TestCase):
    """验证机器契约固定一次性切换和人工权限边界。"""

    def test_contract_excludes_local_runtime_and_forbids_dual_primary(self) -> None:
        contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

        self.assertEqual(contract["schema_version"], "data-cutover.v1")
        self.assertEqual(contract["mode"], "one_time")
        self.assertTrue(contract["source"]["freeze_before_export"])
        self.assertTrue(contract["source"]["read_only_after_activation"])
        self.assertFalse(contract["target"]["dual_write"])
        self.assertFalse(contract["target"]["dual_primary"])
        self.assertFalse(contract["authority"]["agent_may_activate_cutover"])
        self.assertFalse(contract["authority"]["workflow_may_activate_cutover"])
        self.assertEqual(contract["included_sqlite_migrations"], [1, 2, 3, 4, 5, 6])
        self.assertEqual(
            set(contract["excluded_local_runtime_tables"]),
            {
                "runtime_commands",
                "runtime_tasks",
                "runtime_mode_switches",
                "task_packets",
                "agent_runs",
            },
        )
        self.assertFalse(contract["test_scope"]["migrates_hongri_data"])
        self.assertFalse(contract["test_scope"]["production_slo_confirmed"])
        self.assertFalse(schema["additionalProperties"])


class FailingObjectStore:
    """在指定上传次数失败，用于验证跨 PostgreSQL/S3 回滚。"""

    def __init__(self, delegate: S3ObjectStore, fail_on_put: int, *, fail_after: bool = False) -> None:
        self.delegate = delegate
        self.fail_on_put = fail_on_put
        self.fail_after = fail_after
        self.put_count = 0
        self.bucket = delegate.bucket

    def put_stream(self, *args, **kwargs):
        self.put_count += 1
        if self.put_count == self.fail_on_put:
            if not self.fail_after:
                raise RuntimeError("模拟对象上传失败")
            result = self.delegate.put_stream(*args, **kwargs)
            raise RuntimeError("模拟对象上传完成后连接中断")
        return self.delegate.put_stream(*args, **kwargs)

    def __getattr__(self, name: str):
        return getattr(self.delegate, name)


class DataCutoverIntegrationTest(unittest.TestCase):
    """用临时 SQLite、PostgreSQL 和 Moto S3 验证完整切换。"""

    def setUp(self) -> None:
        assert POSTGRESQL is not None
        assert S3 is not None
        self.temporary = tempfile.TemporaryDirectory(prefix="brand-os-cutover-")
        self.root = Path(self.temporary.name)
        self.layout = WorkspaceLayout.from_root(self.root)
        for path in (
            self.layout.control,
            self.layout.state,
            self.layout.evidence,
            self.layout.backups,
            self.layout.derived,
            self.layout.runtime,
        ):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.source_database = self.layout.state / "project.db"
        self.source_store = SQLiteCanonicalStore(self.source_database)
        self.local_evidence = LocalEvidenceStore(self.layout, (self.root,))
        self.fox = Actor(ActorKind.HUMAN, "Fox")
        self.system = Actor(ActorKind.SYSTEM, "source-importer")
        self.ai = Actor(ActorKind.AI, "codex")
        self.source_store.create_project(
            CommandContext("example", self.fox, "project-create", 0),
            "迁移测试项目",
        )
        self.contents = (b"source-version-one", b"source-version-two")
        self.content_hashes = tuple(self._snapshot(content, index) for index, content in enumerate(self.contents, 1))
        self._import_sources()
        created = self.source_store.create_proposal(
            self._context(self.ai, "proposal-create"),
            ProposalDraft(
                proposal_id="proposal-1",
                proposal_kind="create",
                classification="DECISION_CANDIDATE",
                subject_id="decision-1",
                before=None,
                after={"id": "decision-1", "statement": "采用人工确认的测试方向"},
                reason="验证审批迁移",
                impact_scope="测试",
                evidence_refs=("source-version:SRC-V2",),
            ),
        )
        self.source_store.review_proposal(
            self._context(self.fox, "proposal-approve", created.project_version),
            ProposalReview("proposal-1", ReviewAction.APPROVE, "Fox 明确确认"),
        )
        self.expected_version = self.source_store.get_project_version("example")
        self.expected_events = self.source_store.list_events("example")
        self.expected_actions = self.source_store.list_human_actions("example")
        self.expected_state = self.source_store.get_current_state("example")
        self.database_name, self.dsn = POSTGRESQL.create_database()
        self.repository = PostgreSQLEvidenceRepository(self.dsn)
        self.bucket, self.raw_s3 = S3.create_versioned_bucket()
        self.objects = S3ObjectStore(
            endpoint_url=S3.endpoint_url,
            bucket=self.bucket,
            access_key=S3.access_key,
            secret_key=S3.secret_key,
            region=S3.region,
        )
        self.admission = EvidenceAdmissionService(
            metadata=self.repository,
            objects=self.objects,
            allowed_revokers=("Fox",),
        )

    def tearDown(self) -> None:
        assert POSTGRESQL is not None
        POSTGRESQL.drop_database(self.database_name)
        self.temporary.cleanup()

    def _context(
        self,
        actor: Actor,
        key: str,
        version: int | None = None,
    ) -> CommandContext:
        return CommandContext(
            "example",
            actor,
            key,
            self.source_store.get_project_version("example") if version is None else version,
        )

    def _snapshot(self, content: bytes, index: int) -> str:
        source = self.root / f"source-v{index}.md"
        source.write_bytes(content)
        return self.local_evidence.snapshot(source).sha256

    def _import_sources(self) -> None:
        previous: str | None = None
        for index, digest in enumerate(self.content_hashes, 1):
            manifest_path = self.root / f"source-manifest-v{index}.json"
            record = {
                "logical_source_id": "SRC",
                "sha256": digest,
                "relative_path": f"资料/source-v{index}.md",
                "source_role": "working_source",
                "confidentiality": "P2",
                "size_bytes": len(self.contents[index - 1]),
                "media_type": "text/markdown",
                "status": "current",
                "version_label": f"v{index}",
                "supersedes_sha256": [previous] if previous else [],
            }
            manifest_path.write_text(
                json.dumps(
                    {
                        "schema_version": "source-import.v1",
                        "snapshot_at": f"2026-07-{20 + index:02d}",
                        "records": [record],
                        "gaps": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            self.source_store.import_source_batch(
                self._context(self.system, f"source-import-v{index}"),
                load_source_manifest(manifest_path, origin_ref=f"fixture-v{index}"),
            )
            previous = digest

    def _service(
        self,
        admission: EvidenceAdmissionService | None = None,
    ) -> DataCutoverService:
        return DataCutoverService(
            source_database=self.source_database,
            local_evidence=self.local_evidence,
            target_dsn=self.dsn,
            evidence_admission=admission or self.admission,
            export_root=self.root / "exports",
            allowed_operators=("Fox",),
        )

    def test_ai_cannot_freeze_or_activate_authority_cutover(self) -> None:
        with self.assertRaises(DataCutoverPermissionDenied):
            self._service().run("CUT-AI-DENIED-001", self.ai)
        self.assertIsNone(self._service().source_cutover_status("CUT-AI-DENIED-001"))
        self.assertEqual(self.source_store.get_project_version("example"), self.expected_version)

    def test_full_cutover_preserves_authority_and_makes_postgresql_only_writer(self) -> None:
        service = self._service()
        report = service.run("CUT-EXAMPLE-001", self.fox)

        target = PostgreSQLCanonicalStore(self.dsn)
        self.assertEqual(report.result, "activated")
        self.assertFalse(report.replayed)
        self.assertEqual(target.get_project_version("example"), self.expected_version)
        self.assertEqual(
            [event["event_id"] for event in target.list_events("example")],
            [event["event_id"] for event in self.expected_events],
        )
        self.assertEqual(target.list_human_actions("example"), self.expected_actions)
        self.assertEqual(target.get_current_state("example"), self.expected_state)
        self.assertEqual(report.human_action_count, 1)
        self.assertEqual(report.state_item_count, 1)
        versions = self.repository.list_all_versions()
        self.assertEqual(len(versions), 2)
        self.assertTrue(all(item.state is EvidenceState.ACTIVE for item in versions))
        self.assertEqual({item.sha256 for item in versions}, set(self.content_hashes))
        self.assertTrue(all(item.object_version_id for item in versions))
        self.assertEqual(
            [item.source_version_id for item in service.list_evidence_mappings("CUT-EXAMPLE-001")],
            [row["source_version_id"] for row in self.source_store.list_source_versions("example", "SRC")],
        )

        with self.assertRaises(AuthorityCutoverReadOnly):
            SQLiteCanonicalStore(self.source_database).create_proposal(
                CommandContext("example", self.ai, "local-write-after-cutover", self.expected_version),
                ProposalDraft(
                    proposal_id="local-after-cutover",
                    proposal_kind="create",
                    classification="VIEW",
                    subject_id="view-1",
                    before=None,
                    after={"id": "view-1", "statement": "本地不应继续写"},
                    reason="测试",
                    impact_scope="测试",
                    evidence_refs=("manual:local-read-only",),
                ),
            )
        self.assertEqual(
            SQLiteCanonicalStore(self.source_database).get_current_state("example"),
            self.expected_state,
        )

        appended = target.create_proposal(
            CommandContext("example", self.ai, "server-write-after-cutover", self.expected_version),
            ProposalDraft(
                proposal_id="server-after-cutover",
                proposal_kind="create",
                classification="VIEW",
                subject_id="view-1",
                before=None,
                after={"id": "view-1", "statement": "服务器继续写入"},
                reason="验证唯一写入源",
                impact_scope="测试",
                evidence_refs=("manual:server-write",),
            ),
        )
        self.assertEqual(appended.project_version, self.expected_version + 1)

        replay = service.run("CUT-EXAMPLE-001", self.fox)
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.manifest_sha256, report.manifest_sha256)
        self.assertEqual(len(self.repository.list_all_versions()), 2)

    def test_nonempty_target_is_rejected_before_source_freeze(self) -> None:
        target = PostgreSQLCanonicalStore(self.dsn)
        target.create_project(
            CommandContext("existing", self.fox, "existing", 0),
            "已有目标数据",
        )

        with self.assertRaises(DataCutoverTargetNotEmpty):
            self._service().run("CUT-NONEMPTY-001", self.fox)

        result = self.source_store.create_proposal(
            self._context(self.ai, "source-still-writable"),
            ProposalDraft(
                proposal_id="still-writable",
                proposal_kind="create",
                classification="VIEW",
                subject_id="view-write",
                before=None,
                after={"id": "view-write", "statement": "源库仍可写"},
                reason="目标拒绝发生在冻结前",
                impact_scope="测试",
                evidence_refs=("manual:target-not-empty",),
            ),
        )
        self.assertEqual(result.project_version, self.expected_version + 1)

    def test_tampered_export_is_rejected_and_source_is_unfrozen(self) -> None:
        service = self._service()
        manifest = service.prepare("CUT-TAMPER-001", self.fox)
        table_path = service.export_directory(manifest.cutover_id) / manifest.tables[0].file
        table_path.chmod(0o600)
        table_path.write_text(table_path.read_text(encoding="utf-8") + "{}\n", encoding="utf-8")

        with self.assertRaises(DataCutoverIntegrityError):
            service.execute(manifest.cutover_id, self.fox)

        self.assertEqual(service.source_cutover_status("CUT-TAMPER-001"), "ABORTED")
        self.source_store.create_proposal(
            self._context(self.ai, "write-after-tamper"),
            ProposalDraft(
                proposal_id="after-tamper",
                proposal_kind="create",
                classification="VIEW",
                subject_id="view-tamper",
                before=None,
                after={"id": "view-tamper", "statement": "篡改后已解除冻结"},
                reason="验证回滚",
                impact_scope="测试",
                evidence_refs=("manual:tamper",),
            ),
        )
        with psycopg.connect(self.dsn, autocommit=True) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0], 0)

    def test_object_failure_removes_imported_rows_and_created_versions(self) -> None:
        failing_objects = FailingObjectStore(self.objects, fail_on_put=2, fail_after=True)
        failing_admission = EvidenceAdmissionService(
            metadata=self.repository,
            objects=failing_objects,
            allowed_revokers=("Fox",),
        )

        with self.assertRaisesRegex(RuntimeError, "上传完成后连接中断"):
            self._service(failing_admission).run("CUT-ROLLBACK-001", self.fox)

        self.assertEqual(self._service().source_cutover_status("CUT-ROLLBACK-001"), "ABORTED")
        with psycopg.connect(self.dsn, autocommit=True) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0], 0)
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM evidence_object_versions").fetchone()[0],
                0,
            )
        listed = self.raw_s3.list_object_versions(Bucket=self.bucket)
        self.assertEqual(listed.get("Versions", []), [])

    def test_source_change_during_cutover_is_rejected_and_target_is_rolled_back(self) -> None:
        service = self._service()
        original_verify = service._verify_source_unchanged

        def mutate_then_verify(manifest):
            self.source_database.chmod(0o600)
            with sqlite3.connect(self.source_database) as connection:
                connection.execute(
                    "UPDATE projects SET name = ? WHERE project_id = ?",
                    ("被外部改写", "example"),
                )
            return original_verify(manifest)

        with patch.object(service, "_verify_source_unchanged", side_effect=mutate_then_verify):
            with self.assertRaisesRegex(DataCutoverIntegrityError, "冻结后发生变化"):
                service.run("CUT-SOURCE-CHANGE-001", self.fox)

        self.assertEqual(service.source_cutover_status("CUT-SOURCE-CHANGE-001"), "ABORTED")
        with psycopg.connect(self.dsn, autocommit=True) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
