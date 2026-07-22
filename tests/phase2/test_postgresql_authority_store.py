"""PostgreSQL 权威事件、人工审批和投影集成测试。"""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import psycopg

from brand_os.domain import (
    Actor,
    ActorKind,
    ClassificationCandidate,
    CommandContext,
    ProposalDraft,
    ProposalReopen,
    ProposalReview,
    RelationDraft,
    ReviewAction,
    SourceRecord,
    legacy_source_version_id,
)
from brand_os.manifest_import import load_source_manifest
from brand_os.meeting_ingest import parse_meeting_ingest
from brand_os.postgresql_store import PostgreSQLCanonicalStore
from brand_os.sqlite_store import (
    BusinessPermissionDenied,
    ResourceConflict,
    SQLiteCanonicalStore,
    VersionConflict,
)
from phase2.postgresql_test_runtime import TemporaryPostgreSQL


ROOT = Path(__file__).parents[2]
CONTRACT_PATH = ROOT / "contracts" / "phase2" / "postgresql-authority.json"
POSTGRESQL: TemporaryPostgreSQL | None = None


def setUpModule() -> None:
    """整个测试模块只启动一个临时 PostgreSQL 进程。"""

    global POSTGRESQL
    try:
        POSTGRESQL = TemporaryPostgreSQL()
    except RuntimeError as error:
        raise unittest.SkipTest(str(error)) from error
    POSTGRESQL.start()


def tearDownModule() -> None:
    """无论测试结果如何都停止临时 PostgreSQL。"""

    if POSTGRESQL is not None:
        POSTGRESQL.stop()


class FailingProjectionStore(PostgreSQLCanonicalStore):
    """在投影步骤注入失败，用于验证整笔事务回滚。"""

    def _apply_approval_projection(self, *args, **kwargs) -> None:
        raise RuntimeError("模拟 PostgreSQL 投影写入失败")


class PostgreSQLAuthorityStoreTest(unittest.TestCase):
    """验证 PostgreSQL 与 Phase 1 领域语义保持一致。"""

    def setUp(self) -> None:
        assert POSTGRESQL is not None
        self.database_name, self.dsn = POSTGRESQL.create_database()
        self.store = PostgreSQLCanonicalStore(self.dsn)
        self.fox = Actor(ActorKind.HUMAN, "Fox")
        self.ai = Actor(ActorKind.AI, "codex")
        self.store.create_project(self.context(self.fox, "project-create", 0), "鸿日")

    def tearDown(self) -> None:
        assert POSTGRESQL is not None
        POSTGRESQL.drop_database(self.database_name)

    def context(self, actor: Actor, key: str, version: int | None = None) -> CommandContext:
        return CommandContext(
            project_id="hongri",
            actor=actor,
            idempotency_key=key,
            expected_version=(
                self.store.get_project_version("hongri") if version is None else version
            ),
        )

    @staticmethod
    def proposal(proposal_id: str = "proposal-1") -> ProposalDraft:
        return ProposalDraft(
            proposal_id=proposal_id,
            proposal_kind="create",
            classification="DECISION_CANDIDATE",
            subject_id="decision-positioning",
            before=None,
            after={"id": "decision-positioning", "statement": "采用已确认方向"},
            reason="来自当前会议候选",
            impact_scope="本轮内容",
            evidence_refs=("evidence:meeting-1#12",),
        )

    def create_and_approve(self) -> None:
        created = self.store.create_proposal(
            self.context(self.ai, "proposal-create"), self.proposal()
        )
        self.store.review_proposal(
            self.context(self.fox, "proposal-approve", created.project_version),
            ProposalReview("proposal-1", ReviewAction.APPROVE, "Fox 明确确认"),
        )

    def test_versioned_migrations_are_repeatable_and_verified(self) -> None:
        self.assertEqual(self.store.schema_version, 7)
        self.assertTrue(self.store.quick_check())
        reopened = PostgreSQLCanonicalStore(self.dsn)
        self.assertEqual(reopened.schema_version, 7)

        with psycopg.connect(self.dsn, autocommit=True) as connection:
            connection.execute(
                "UPDATE schema_migrations SET checksum = %s WHERE version = 1",
                ("0" * 64,),
            )
        with self.assertRaisesRegex(RuntimeError, "校验和发生变化"):
            PostgreSQLCanonicalStore(self.dsn)

    def test_idempotency_and_expected_version_match_sqlite_semantics(self) -> None:
        context = self.context(self.ai, "proposal-create")
        first = self.store.create_proposal(context, self.proposal())
        replay = self.store.create_proposal(context, self.proposal())

        self.assertFalse(first.replayed)
        self.assertTrue(replay.replayed)
        self.assertEqual(first.event_id, replay.event_id)
        self.assertEqual(len(self.store.list_events("hongri")), 2)
        with self.assertRaises(ResourceConflict):
            self.store.create_proposal(context, self.proposal("proposal-other"))
        with self.assertRaises(VersionConflict):
            self.store.create_proposal(
                self.context(self.ai, "stale", 0), self.proposal("proposal-stale")
            )
        self.assertEqual(len(self.store.list_events("hongri")), 2)

    def test_only_configured_human_can_approve_formal_state(self) -> None:
        created = self.store.create_proposal(
            self.context(self.ai, "proposal-create"), self.proposal()
        )
        review = ProposalReview("proposal-1", ReviewAction.APPROVE, "同意")
        with self.assertRaises(BusinessPermissionDenied):
            self.store.review_proposal(
                self.context(self.ai, "ai-approve", created.project_version), review
            )
        with self.assertRaises(BusinessPermissionDenied):
            self.store.review_proposal(
                self.context(
                    Actor(ActorKind.HUMAN, "Other"),
                    "other-approve",
                    created.project_version,
                ),
                review,
            )
        self.assertEqual(self.store.get_current_state("hongri"), [])

    def test_event_approval_and_projection_commit_atomically(self) -> None:
        self.create_and_approve()
        state = self.store.get_current_state("hongri")
        action = self.store.list_human_actions("hongri")[0]
        event = self.store.list_events("hongri")[-1]

        self.assertEqual(self.store.get_project_version("hongri"), 3)
        self.assertEqual(state[0]["item_type"], "DECISION")
        self.assertEqual(state[0]["payload"]["statement"], "采用已确认方向")
        self.assertEqual(action["actor_id"], "Fox")
        self.assertEqual(event["event_type"], "PROPOSAL_APPROVED")
        self.assertEqual(event["actor_kind"], "HUMAN")

    def test_projection_failure_rolls_back_every_intermediate_write(self) -> None:
        created = self.store.create_proposal(
            self.context(self.ai, "proposal-create"), self.proposal()
        )
        failing = FailingProjectionStore(self.dsn)
        before_events = len(self.store.list_events("hongri"))
        with self.assertRaisesRegex(RuntimeError, "模拟 PostgreSQL 投影写入失败"):
            failing.review_proposal(
                CommandContext(
                    "hongri", self.fox, "proposal-approve", created.project_version
                ),
                ProposalReview("proposal-1", ReviewAction.APPROVE, "Fox 明确确认"),
            )

        self.assertEqual(len(self.store.list_events("hongri")), before_events)
        self.assertEqual(self.store.get_project_version("hongri"), created.project_version)
        self.assertEqual(self.store.list_proposals("hongri")[0]["status"], "proposed")
        self.assertEqual(self.store.list_human_actions("hongri"), [])
        self.assertEqual(self.store.get_current_state("hongri"), [])

    def test_state_and_proposal_lifecycle_rebuild_from_events(self) -> None:
        self.store.create_proposal(
            self.context(self.ai, "proposal-create"), self.proposal()
        )
        self.store.review_proposal(
            self.context(self.fox, "proposal-reject"),
            ProposalReview("proposal-1", ReviewAction.REJECT, "证据不足"),
        )
        self.store.reopen_proposal(
            self.context(self.fox, "proposal-reopen"),
            ProposalReopen("proposal-1", "补到新证据", ("evidence:new",)),
        )
        self.store.review_proposal(
            self.context(self.fox, "proposal-approve"),
            ProposalReview("proposal-1", ReviewAction.APPROVE, "Fox 明确确认"),
        )
        expected_state = self.store.get_current_state("hongri")
        expected_history = self.store.get_proposal_history("hongri", "proposal-1")

        with psycopg.connect(self.dsn, autocommit=True) as connection:
            connection.execute("DELETE FROM state_items")
            connection.execute("DELETE FROM proposal_lifecycle_actions")
            connection.execute("DELETE FROM proposal_lifecycle")

        self.assertEqual(self.store.rebuild_proposal_lifecycle("hongri"), 3)
        self.assertEqual(self.store.rebuild_state_projection("hongri"), 1)
        rebuilt_history = self.store.get_proposal_history("hongri", "proposal-1")
        self.assertEqual(self.store.get_current_state("hongri"), expected_state)
        self.assertEqual(
            rebuilt_history["lifecycle_actions"],
            expected_history["lifecycle_actions"],
        )
        self.assertEqual(rebuilt_history["proposal"]["status"], "approved")
        self.assertEqual(rebuilt_history["proposal"]["revision"], 1)

    def test_source_import_keeps_versions_and_explicit_supersession(self) -> None:
        first_content = b"source-v1"
        second_content = b"source-v2"
        first_sha256 = hashlib.sha256(first_content).hexdigest()
        second_sha256 = hashlib.sha256(second_content).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def manifest(name: str, record: dict):
                path = root / name
                path.write_text(
                    json.dumps(
                        {
                            "schema_version": "source-import.v1",
                            "snapshot_at": "2026-07-22",
                            "records": [record],
                            "gaps": [],
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                return load_source_manifest(path, origin_ref=name)

            first = {
                "logical_source_id": "SRC-1",
                "sha256": first_sha256,
                "relative_path": "资料/SRC-1-v1.md",
                "source_role": "working_source",
                "confidentiality": "P2",
                "size_bytes": len(first_content),
                "media_type": "text/markdown",
                "status": "current",
                "version_label": "v1",
            }
            second = {
                **first,
                "sha256": second_sha256,
                "relative_path": "资料/SRC-1-v2.md",
                "size_bytes": len(second_content),
                "version_label": "v2",
                "supersedes_sha256": [first_sha256],
            }
            self.store.import_source_batch(
                self.context(Actor(ActorKind.SYSTEM, "source-importer"), "import-v1"),
                manifest("v1.json", first),
            )
            result = self.store.import_source_batch(
                self.context(Actor(ActorKind.SYSTEM, "source-importer"), "import-v2"),
                manifest("v2.json", second),
            )

        versions = self.store.list_source_versions("hongri", "SRC-1")
        report = self.store.get_source_import_report("hongri", result.resource_id)
        self.assertEqual([version["is_current"] for version in versions], [0, 1])
        self.assertEqual(versions[-1]["sha256"], second_sha256)
        self.assertEqual(report["batch"]["new_supersession_count"], 1)
        self.assertEqual(self.store.get_current_state("hongri"), [])

    def test_source_meeting_working_layer_and_evidence_queries_share_one_store(self) -> None:
        source_sha256 = hashlib.sha256(b"meeting-source").hexdigest()
        source_id = "meeting-source"
        source_version_id = legacy_source_version_id(source_id, source_sha256)
        self.store.register_source(
            self.context(self.fox, "source-register"),
            SourceRecord(
                source_id,
                source_sha256,
                14,
                "meetings/meeting-1.md",
                "meeting_minutes",
                "P2",
            ),
        )
        self.store.record_candidate(
            self.context(self.ai, "candidate-record"),
            ClassificationCandidate(
                "candidate-1",
                source_id,
                source_sha256,
                "line:1",
                "月底前最好看到一版。",
                "TARGET_DATE",
                "最好表示暂定目标",
            ),
        )
        self.store.add_relation(
            self.context(self.ai, "relation-record"),
            RelationDraft(
                "relation-1",
                "classification_candidate",
                "candidate-1",
                "sourced_from",
                "source_version",
                source_version_id,
                f"source-version:{source_version_id}#line:1",
            ),
        )
        payload = {
            "schema_version": "meeting-ingest.v1",
            "source_is_data": True,
            "base_state_version": self.store.get_project_version("hongri"),
            "meeting": {
                "meeting_id": "meeting-1",
                "title": "增量讨论",
                "occurred_at": "2026-07-22T10:00:00+08:00",
                "participants": ["Fox"],
                "mode": "SYNC",
                "mode_confidence": 0.9,
                "source": {
                    "logical_source_id": source_id,
                    "source_version_id": source_version_id,
                    "sha256": source_sha256,
                    "verification": "verified",
                },
            },
            "segments": [
                {
                    "segment_id": "segment-1",
                    "locator": "00:01:00-00:01:06",
                    "quote": "月底前最好看到一版。",
                    "speaker": "Fox",
                    "spoken_at": "00:01:00",
                    "start_ms": 60000,
                    "end_ms": 66000,
                    "context": "同步下次看版时间",
                    "transcript_confidence": 0.95,
                    "mode": "SYNC",
                    "mode_confidence": 0.9,
                }
            ],
            "items": [
                {
                    "item_id": "item-date",
                    "type": "TARGET_DATE",
                    "summary": "希望月底前看到一版",
                    "scope": "内部看版",
                    "date_kind": "TENTATIVE_DATE",
                    "evidence_segment_ids": ["segment-1"],
                    "confidence": 0.9,
                    "reason": "最好表示暂定目标",
                    "requires_human_confirmation": True,
                }
            ],
            "conflicts": [],
        }
        result = self.store.ingest_meeting_batch(
            self.context(self.ai, "meeting-ingest"), parse_meeting_ingest(payload)
        )

        report = self.store.get_meeting_ingest_report("hongri", result.resource_id)
        evidence = self.store.resolve_evidence_ref(
            "hongri", f"source-version:{source_version_id}#line:1"
        )
        self.assertEqual(len(self.store.list_candidates("hongri")), 1)
        self.assertEqual(len(self.store.list_relations("hongri")), 1)
        self.assertEqual(report["inventory"]["meeting_count"], 1)
        self.assertEqual(report["items"][0]["classification"], "TARGET_DATE")
        self.assertEqual(evidence["verification"], "confirmed")
        self.assertEqual(evidence["source"]["sha256"], source_sha256)
        self.assertEqual(self.store.get_current_state("hongri"), [])

    def test_approved_state_matches_sqlite_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sqlite_store = SQLiteCanonicalStore(Path(directory) / "authority.db")
            sqlite_store.create_project(
                CommandContext("hongri", self.fox, "project-create", 0), "鸿日"
            )
            created = sqlite_store.create_proposal(
                CommandContext("hongri", self.ai, "proposal-create", 1),
                self.proposal(),
            )
            sqlite_store.review_proposal(
                CommandContext(
                    "hongri", self.fox, "proposal-approve", created.project_version
                ),
                ProposalReview("proposal-1", ReviewAction.APPROVE, "Fox 明确确认"),
            )

            self.create_and_approve()
            postgresql_state = self.store.get_current_state("hongri")
            sqlite_state = sqlite_store.get_current_state("hongri")
            for item in (*postgresql_state, *sqlite_state):
                item.pop("updated_event_id")
            self.assertEqual(
                postgresql_state,
                sqlite_state,
            )
            self.assertEqual(
                [event["event_type"] for event in self.store.list_events("hongri")],
                [event["event_type"] for event in sqlite_store.list_events("hongri")],
            )


class PostgreSQLAuthorityContractTest(unittest.TestCase):
    """验证 F2.2 机器契约没有扩大当前任务边界。"""

    def test_contract_keeps_single_authority_and_deferred_boundaries(self) -> None:
        contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        self.assertEqual(contract["schema_version"], "postgresql-authority.v2")
        self.assertEqual(contract["migration_versions"], [1, 2, 3, 4, 5, 6, 7])
        self.assertEqual(contract["object_evidence_contract"], "object-evidence.v1")
        self.assertEqual(contract["formal_write_store"], "postgresql")
        self.assertFalse(contract["dual_write"])
        self.assertFalse(contract["migrates_hongri_data"])
        self.assertEqual(
            set(contract["deferred"]),
            {"oidc_rbac_rls", "outbox", "http_api", "data_cutover"},
        )


if __name__ == "__main__":
    unittest.main()
