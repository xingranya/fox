"""SQLite 权威事件、幂等、人工确认和投影测试。"""

from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path


from brand_os.domain import (
    Actor,
    ActorKind,
    ClassificationCandidate,
    CommandContext,
    ProposalDraft,
    ProposalReview,
    RelationDraft,
    ReviewAction,
    SourceRecord,
)
from brand_os.sqlite_store import (
    BusinessPermissionDenied,
    ResourceConflict,
    SQLiteCanonicalStore,
    VersionConflict,
)


class FailingProjectionStore(SQLiteCanonicalStore):
    """用于证明事件与投影处于同一事务。"""

    def _apply_approval_projection(self, *args, **kwargs) -> None:
        raise RuntimeError("模拟投影写入失败")


class SQLiteCanonicalStoreTest(unittest.TestCase):
    """验证 AI 只能提案，Fox 确认后状态才变化。"""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "project.db"
        self.store = SQLiteCanonicalStore(self.database)
        self.fox = Actor(ActorKind.HUMAN, "Fox")
        self.ai = Actor(ActorKind.AI, "codex")
        self.store.create_project(self.context(self.fox, "project-create", 0), "鸿日")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def context(self, actor: Actor, key: str, version: int | None = None) -> CommandContext:
        return CommandContext(
            project_id="hongri",
            actor=actor,
            idempotency_key=key,
            expected_version=self.store.get_project_version("hongri") if version is None else version,
        )

    def proposal(self, proposal_id: str = "proposal-1") -> ProposalDraft:
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

    def test_idempotent_retry_returns_original_result_without_new_event(self) -> None:
        context = self.context(self.ai, "proposal-create")
        first = self.store.create_proposal(context, self.proposal())
        second = self.store.create_proposal(context, self.proposal())
        self.assertFalse(first.replayed)
        self.assertTrue(second.replayed)
        self.assertEqual(first.event_id, second.event_id)
        self.assertEqual(len(self.store.list_events("hongri")), 2)

    def test_same_idempotency_key_with_different_request_conflicts(self) -> None:
        context = self.context(self.ai, "proposal-create")
        self.store.create_proposal(context, self.proposal())
        with self.assertRaises(ResourceConflict):
            self.store.create_proposal(context, self.proposal("proposal-other"))

    def test_stale_expected_version_is_rejected_without_partial_event(self) -> None:
        before = len(self.store.list_events("hongri"))
        with self.assertRaises(VersionConflict):
            self.store.create_proposal(self.context(self.ai, "stale", 0), self.proposal())
        self.assertEqual(len(self.store.list_events("hongri")), before)
        self.assertEqual(self.store.get_project_version("hongri"), 1)

    def test_ai_can_propose_but_cannot_approve(self) -> None:
        created = self.store.create_proposal(self.context(self.ai, "proposal-create"), self.proposal())
        self.assertEqual(self.store.get_current_state("hongri"), [])
        review = ProposalReview("proposal-1", ReviewAction.APPROVE, "同意")
        with self.assertRaises(BusinessPermissionDenied):
            self.store.review_proposal(
                self.context(self.ai, "proposal-approve", created.project_version), review
            )
        self.assertEqual(self.store.get_current_state("hongri"), [])

    def test_unconfigured_human_cannot_approve(self) -> None:
        created = self.store.create_proposal(self.context(self.ai, "proposal-create"), self.proposal())
        other = Actor(ActorKind.HUMAN, "Other")
        with self.assertRaises(BusinessPermissionDenied):
            self.store.review_proposal(
                self.context(other, "proposal-approve", created.project_version),
                ProposalReview("proposal-1", ReviewAction.APPROVE, "同意"),
            )

    def test_fox_approval_updates_event_action_and_projection_atomically(self) -> None:
        created = self.store.create_proposal(self.context(self.ai, "proposal-create"), self.proposal())
        result = self.store.review_proposal(
            self.context(self.fox, "proposal-approve", created.project_version),
            ProposalReview("proposal-1", ReviewAction.APPROVE, "Fox 明确确认"),
        )
        state = self.store.get_current_state("hongri")
        self.assertEqual(result.project_version, 3)
        self.assertEqual(len(state), 1)
        self.assertEqual(state[0]["item_type"], "DECISION")
        self.assertEqual(state[0]["payload"]["statement"], "采用已确认方向")
        self.assertEqual(self.store.list_proposals("hongri")[0]["status"], "approved")
        self.assertEqual(self.store.list_human_actions("hongri")[0]["actor_id"], "Fox")
        events = self.store.list_events("hongri")
        self.assertEqual(events[-1]["event_type"], "PROPOSAL_APPROVED")
        self.assertEqual(events[-1]["actor_kind"], "HUMAN")

    def test_review_retry_is_idempotent(self) -> None:
        created = self.store.create_proposal(self.context(self.ai, "proposal-create"), self.proposal())
        context = self.context(self.fox, "proposal-approve", created.project_version)
        review = ProposalReview("proposal-1", ReviewAction.APPROVE, "Fox 明确确认")
        first = self.store.review_proposal(context, review)
        second = self.store.review_proposal(context, review)
        self.assertFalse(first.replayed)
        self.assertTrue(second.replayed)
        self.assertEqual(first.event_id, second.event_id)
        self.assertEqual(len(self.store.list_human_actions("hongri")), 1)

    def test_rejection_is_audited_without_changing_state(self) -> None:
        created = self.store.create_proposal(self.context(self.ai, "proposal-create"), self.proposal())
        self.store.review_proposal(
            self.context(self.fox, "proposal-reject", created.project_version),
            ProposalReview("proposal-1", ReviewAction.REJECT, "证据不足"),
        )
        self.assertEqual(self.store.get_current_state("hongri"), [])
        self.assertEqual(self.store.list_proposals("hongri")[0]["status"], "rejected")
        self.assertEqual(self.store.list_human_actions("hongri")[0]["action"], "reject")

    def test_modify_and_approve_uses_fox_replacement(self) -> None:
        created = self.store.create_proposal(self.context(self.ai, "proposal-create"), self.proposal())
        replacement = {"id": "decision-positioning", "statement": "采用 Fox 修改后的方向"}
        self.store.review_proposal(
            self.context(self.fox, "proposal-modify", created.project_version),
            ProposalReview(
                "proposal-1",
                ReviewAction.MODIFY_AND_APPROVE,
                "调整措辞后确认",
                replacement,
            ),
        )
        self.assertEqual(self.store.get_current_state("hongri")[0]["payload"], replacement)
        self.assertEqual(self.store.list_human_actions("hongri")[0]["action"], "modify_and_approve")

    def test_projection_failure_rolls_back_event_review_and_project_version(self) -> None:
        created = self.store.create_proposal(self.context(self.ai, "proposal-create"), self.proposal())
        failing = FailingProjectionStore(self.database)
        before_events = len(self.store.list_events("hongri"))
        with self.assertRaises(RuntimeError):
            failing.review_proposal(
                CommandContext("hongri", self.fox, "proposal-approve", created.project_version),
                ProposalReview("proposal-1", ReviewAction.APPROVE, "Fox 明确确认"),
            )
        self.assertEqual(len(self.store.list_events("hongri")), before_events)
        self.assertEqual(self.store.get_project_version("hongri"), created.project_version)
        self.assertEqual(self.store.list_proposals("hongri")[0]["status"], "proposed")
        self.assertEqual(self.store.get_current_state("hongri"), [])

    def test_projection_can_be_rebuilt_from_human_events(self) -> None:
        created = self.store.create_proposal(self.context(self.ai, "proposal-create"), self.proposal())
        self.store.review_proposal(
            self.context(self.fox, "proposal-approve", created.project_version),
            ProposalReview("proposal-1", ReviewAction.APPROVE, "Fox 明确确认"),
        )
        expected = self.store.get_current_state("hongri")
        with sqlite3.connect(self.database) as connection:
            connection.execute("DELETE FROM state_items")
        self.assertEqual(self.store.get_current_state("hongri"), [])
        self.assertEqual(self.store.rebuild_state_projection("hongri"), 1)
        self.assertEqual(self.store.get_current_state("hongri"), expected)

    def test_source_and_candidate_keep_hash_and_locator(self) -> None:
        digest = hashlib.sha256(b"source").hexdigest()
        source = SourceRecord("source-1", digest, 6, "materials/brief.md", "current_work", "P2")
        source_result = self.store.register_source(self.context(self.fox, "source-register"), source)
        candidate = ClassificationCandidate(
            "candidate-1",
            "source-1",
            digest,
            "line:12",
            "候选原话",
            "TENDENCY",
            "表达了方向但没有正式决定动词",
        )
        candidate_result = self.store.record_candidate(
            self.context(self.ai, "candidate-record", source_result.project_version), candidate
        )
        event = self.store.list_events("hongri")[-1]
        stored_source = self.store.get_source("hongri", "source-1")
        stored_candidate = self.store.list_candidates("hongri")[0]
        self.assertEqual(stored_source["sha256"], digest)
        self.assertEqual(stored_candidate["source_id"], "source-1")
        self.assertEqual(event["payload"]["source_sha256"], digest)
        self.assertEqual(event["payload"]["locator"], "line:12")
        self.assertEqual(candidate_result.project_version, 3)

    def test_candidate_with_wrong_source_hash_rolls_back(self) -> None:
        digest = hashlib.sha256(b"source").hexdigest()
        source = SourceRecord("source-1", digest, 6, "materials/brief.md", "current_work", "P2")
        source_result = self.store.register_source(self.context(self.fox, "source-register"), source)
        candidate = ClassificationCandidate(
            "candidate-1",
            "source-1",
            hashlib.sha256(b"other").hexdigest(),
            "line:12",
            "候选原话",
            "TENDENCY",
            "表达了方向",
        )
        before_events = len(self.store.list_events("hongri"))
        with self.assertRaises(ResourceConflict):
            self.store.record_candidate(
                self.context(self.ai, "candidate-record", source_result.project_version), candidate
            )
        self.assertEqual(len(self.store.list_events("hongri")), before_events)
        self.assertEqual(self.store.get_project_version("hongri"), source_result.project_version)

    def test_relation_is_working_state_and_does_not_change_current_projection(self) -> None:
        relation = RelationDraft(
            "relation-1",
            "proposal",
            "proposal-a",
            "conflicts_with",
            "decision",
            "decision-b",
            "evidence:meeting-1#18",
        )
        result = self.store.add_relation(self.context(self.ai, "relation-record"), relation)
        self.assertEqual(result.project_version, 2)
        self.assertEqual(self.store.get_current_state("hongri"), [])
        self.assertEqual(self.store.list_events("hongri")[-1]["event_type"], "RELATION_RECORDED")
        self.assertEqual(self.store.list_relations("hongri")[0]["relation_type"], "conflicts_with")

    def test_database_constraints_reject_illegal_business_types(self) -> None:
        self.store.create_proposal(self.context(self.ai, "proposal-create"), self.proposal())
        with sqlite3.connect(self.database) as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE proposals SET classification = 'DECISION' WHERE proposal_id = 'proposal-1'"
                )

    def test_database_constraint_keeps_candidate_bound_to_source_hash(self) -> None:
        digest = hashlib.sha256(b"source").hexdigest()
        source = SourceRecord("source-1", digest, 6, "materials/brief.md", "current_work", "P2")
        source_result = self.store.register_source(self.context(self.fox, "source-register"), source)
        candidate = ClassificationCandidate(
            "candidate-1",
            "source-1",
            digest,
            "line:12",
            "候选原话",
            "TENDENCY",
            "表达了方向",
        )
        self.store.record_candidate(
            self.context(self.ai, "candidate-record", source_result.project_version), candidate
        )
        with sqlite3.connect(self.database) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE classification_candidates SET source_sha256 = ? WHERE candidate_id = 'candidate-1'",
                    (hashlib.sha256(b"other").hexdigest(),),
                )


if __name__ == "__main__":
    unittest.main()
