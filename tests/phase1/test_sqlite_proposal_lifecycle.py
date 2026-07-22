"""SQLite Proposal 生命周期、替代和历史重放测试。"""

from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from brand_os.domain import (
    Actor,
    ActorKind,
    CommandContext,
    ProposalDraft,
    ProposalReopen,
    ProposalReview,
    ReviewAction,
    SourceRecord,
    legacy_source_version_id,
)
from brand_os.meeting_ingest import parse_meeting_ingest
from brand_os.sqlite_store import (
    BusinessPermissionDenied,
    ResourceConflict,
    SQLiteCanonicalStore,
    VersionConflict,
)


class FailingSupersessionStore(SQLiteCanonicalStore):
    """在替代事务写入新投影时制造失败。"""

    def _apply_approval_projection(self, *args, **kwargs) -> None:
        raise RuntimeError("模拟替代投影写入失败")


class SQLiteProposalLifecycleTest(unittest.TestCase):
    """验证只有 Fox 的显式动作能推进 Proposal 生命周期。"""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "project.db"
        self.store = SQLiteCanonicalStore(self.database)
        self.fox = Actor(ActorKind.HUMAN, "Fox")
        self.ai = Actor(ActorKind.AI, "codex")
        self.store.create_project(self.context(self.fox, "project", 0), "鸿日")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def context(
        self, actor: Actor, key: str, version: int | None = None
    ) -> CommandContext:
        return CommandContext(
            "hongri",
            actor,
            key,
            self.store.get_project_version("hongri") if version is None else version,
        )

    def proposal(
        self,
        proposal_id: str,
        subject_id: str,
        statement: str,
        *,
        proposal_kind: str = "create",
        before: dict[str, object] | None = None,
        supersedes_proposal_id: str | None = None,
    ) -> ProposalDraft:
        return ProposalDraft(
            proposal_id=proposal_id,
            proposal_kind=proposal_kind,
            classification="DECISION_CANDIDATE",
            subject_id=subject_id,
            before=before,
            after={"id": subject_id, "statement": statement},
            reason="来自会议候选",
            impact_scope="本轮方向",
            evidence_refs=(f"evidence:{proposal_id}",),
            supersedes_proposal_id=supersedes_proposal_id,
        )

    def approve(self, proposal_id: str, key: str) -> None:
        self.store.review_proposal(
            self.context(self.fox, key),
            ProposalReview(proposal_id, ReviewAction.APPROVE, "Fox 明确确认"),
        )

    def create_approved_predecessor(self) -> dict[str, object]:
        old_payload = {"id": "direction-old", "statement": "采用旧方向"}
        self.store.create_proposal(
            self.context(self.ai, "create-old"),
            self.proposal("proposal-old", "direction-old", "采用旧方向"),
        )
        self.approve("proposal-old", "approve-old")
        return old_payload

    def create_successor(self, old_payload: dict[str, object]) -> None:
        self.store.create_proposal(
            self.context(self.ai, "create-new"),
            self.proposal(
                "proposal-new",
                "direction-new",
                "采用新方向",
                proposal_kind="supersede",
                before=old_payload,
                supersedes_proposal_id="proposal-old",
            ),
        )

    def test_fox_can_reopen_rejected_proposal_with_new_evidence(self) -> None:
        self.store.create_proposal(
            self.context(self.ai, "create"),
            self.proposal("proposal-1", "direction-1", "采用方向一"),
        )
        self.store.review_proposal(
            self.context(self.fox, "reject"),
            ProposalReview("proposal-1", ReviewAction.REJECT, "证据不足"),
        )
        context = self.context(self.fox, "reopen")
        reopen = ProposalReopen("proposal-1", "补到客户确认原话", ("evidence:new",))
        first = self.store.reopen_proposal(context, reopen)
        second = self.store.reopen_proposal(context, reopen)

        proposal = self.store.list_proposals("hongri")[0]
        history = self.store.get_proposal_history("hongri", "proposal-1")
        self.assertFalse(first.replayed)
        self.assertTrue(second.replayed)
        self.assertEqual(proposal["status"], "proposed")
        self.assertEqual(proposal["revision"], 1)
        self.assertEqual(
            proposal["evidence_refs"], ["evidence:new", "evidence:proposal-1"]
        )
        self.assertEqual(history["lifecycle_actions"][0]["action"], "reopen")
        self.assertEqual(self.store.get_current_state("hongri"), [])

        expected_actions = history["lifecycle_actions"]
        with sqlite3.connect(self.database) as connection:
            connection.execute("DELETE FROM proposal_lifecycle_actions")
            connection.execute("DELETE FROM proposal_lifecycle")
        self.assertEqual(self.store.rebuild_proposal_lifecycle("hongri"), 2)
        rebuilt = self.store.get_proposal_history("hongri", "proposal-1")
        self.assertEqual(rebuilt["proposal"]["status"], "proposed")
        self.assertEqual(rebuilt["proposal"]["revision"], 1)
        self.assertEqual(rebuilt["lifecycle_actions"], expected_actions)

    def test_reopen_requires_fox_rejected_status_and_new_evidence(self) -> None:
        self.store.create_proposal(
            self.context(self.ai, "create"),
            self.proposal("proposal-1", "direction-1", "采用方向一"),
        )
        self.store.review_proposal(
            self.context(self.fox, "reject"),
            ProposalReview("proposal-1", ReviewAction.REJECT, "证据不足"),
        )
        with self.assertRaises(BusinessPermissionDenied):
            self.store.reopen_proposal(
                self.context(self.ai, "ai-reopen"),
                ProposalReopen("proposal-1", "AI 试图重开", ("evidence:new",)),
            )
        with self.assertRaises(BusinessPermissionDenied):
            self.store.reopen_proposal(
                self.context(Actor(ActorKind.HUMAN, "Other"), "other-reopen"),
                ProposalReopen("proposal-1", "未授权人员试图重开", ("evidence:new",)),
            )
        with self.assertRaises(ResourceConflict):
            self.store.reopen_proposal(
                self.context(self.fox, "old-evidence"),
                ProposalReopen(
                    "proposal-1",
                    "没有补充新材料",
                    ("evidence:proposal-1",),
                ),
            )
        self.assertEqual(self.store.list_proposals("hongri")[0]["status"], "rejected")

    def test_reopen_rejects_stale_project_version(self) -> None:
        self.store.create_proposal(
            self.context(self.ai, "create"),
            self.proposal("proposal-1", "direction-1", "采用方向一"),
        )
        self.store.review_proposal(
            self.context(self.fox, "reject"),
            ProposalReview("proposal-1", ReviewAction.REJECT, "证据不足"),
        )
        stale_version = self.store.get_project_version("hongri")
        self.store.create_proposal(
            self.context(self.ai, "other"),
            self.proposal("proposal-2", "direction-2", "采用方向二"),
        )
        with self.assertRaises(VersionConflict):
            self.store.reopen_proposal(
                self.context(self.fox, "stale-reopen", stale_version),
                ProposalReopen("proposal-1", "补到新材料", ("evidence:new",)),
            )

    def test_supersede_replaces_current_state_and_keeps_history(self) -> None:
        old_payload = self.create_approved_predecessor()
        self.create_successor(old_payload)
        self.approve("proposal-new", "approve-new")

        state = self.store.get_current_state("hongri")
        proposals = {
            proposal["proposal_id"]: proposal
            for proposal in self.store.list_proposals("hongri")
        }
        supersession = self.store.list_proposal_supersessions("hongri")[0]
        self.assertEqual(len(state), 1)
        self.assertEqual(state[0]["item_id"], "direction-new")
        self.assertEqual(proposals["proposal-old"]["status"], "superseded")
        self.assertEqual(proposals["proposal-new"]["status"], "approved")
        self.assertEqual(supersession["predecessor_payload"], old_payload)
        self.assertEqual(
            supersession["successor_payload"]["statement"], "采用新方向"
        )

    def test_same_state_id_cannot_be_silently_overwritten(self) -> None:
        old_payload = self.create_approved_predecessor()
        self.store.create_proposal(
            self.context(self.ai, "create-conflict"),
            self.proposal("proposal-conflict", "direction-old", "静默改写旧方向"),
        )
        version = self.store.get_project_version("hongri")
        events = len(self.store.list_events("hongri"))
        with self.assertRaises(ResourceConflict):
            self.approve("proposal-conflict", "approve-conflict")
        self.assertEqual(self.store.get_project_version("hongri"), version)
        self.assertEqual(len(self.store.list_events("hongri")), events)
        self.assertEqual(self.store.get_current_state("hongri")[0]["payload"], old_payload)
        proposals = {
            proposal["proposal_id"]: proposal["status"]
            for proposal in self.store.list_proposals("hongri")
        }
        self.assertEqual(proposals["proposal-conflict"], "proposed")

    def test_repeated_supersede_is_rejected(self) -> None:
        old_payload = self.create_approved_predecessor()
        self.create_successor(old_payload)
        self.approve("proposal-new", "approve-new")
        with self.assertRaises(ResourceConflict):
            self.store.create_proposal(
                self.context(self.ai, "create-another"),
                self.proposal(
                    "proposal-another",
                    "direction-another",
                    "再次替代",
                    proposal_kind="supersede",
                    before=old_payload,
                    supersedes_proposal_id="proposal-old",
                ),
            )

    def test_supersession_failure_rolls_back_whole_transaction(self) -> None:
        old_payload = self.create_approved_predecessor()
        self.create_successor(old_payload)
        failing = FailingSupersessionStore(self.database)
        version = self.store.get_project_version("hongri")
        events = len(self.store.list_events("hongri"))
        with self.assertRaises(RuntimeError):
            failing.review_proposal(
                CommandContext("hongri", self.fox, "approve-new", version),
                ProposalReview("proposal-new", ReviewAction.APPROVE, "Fox 明确确认"),
            )
        proposals = {
            proposal["proposal_id"]: proposal["status"]
            for proposal in self.store.list_proposals("hongri")
        }
        self.assertEqual(proposals["proposal-old"], "approved")
        self.assertEqual(proposals["proposal-new"], "proposed")
        self.assertEqual(self.store.get_current_state("hongri")[0]["payload"], old_payload)
        self.assertEqual(self.store.list_proposal_supersessions("hongri"), [])
        self.assertEqual(self.store.get_project_version("hongri"), version)
        self.assertEqual(len(self.store.list_events("hongri")), events)

    def test_state_and_lifecycle_can_be_rebuilt_from_events(self) -> None:
        old_payload = self.create_approved_predecessor()
        self.create_successor(old_payload)
        self.approve("proposal-new", "approve-new")
        expected_state = self.store.get_current_state("hongri")
        expected_statuses = [
            (proposal["proposal_id"], proposal["status"], proposal["revision"])
            for proposal in self.store.list_proposals("hongri")
        ]
        expected_supersessions = self.store.list_proposal_supersessions("hongri")
        expected_actions = self.store.get_proposal_history(
            "hongri", "proposal-old"
        )["lifecycle_actions"]

        with sqlite3.connect(self.database) as connection:
            connection.execute("DELETE FROM state_items")
            connection.execute("DELETE FROM proposal_supersessions")
            connection.execute("DELETE FROM proposal_lifecycle_actions")
            connection.execute("DELETE FROM proposal_lifecycle")

        self.assertEqual(self.store.rebuild_proposal_lifecycle("hongri"), 2)
        self.assertEqual(self.store.rebuild_state_projection("hongri"), 2)
        rebuilt_statuses = [
            (proposal["proposal_id"], proposal["status"], proposal["revision"])
            for proposal in self.store.list_proposals("hongri")
        ]
        self.assertEqual(rebuilt_statuses, expected_statuses)
        self.assertEqual(
            self.store.list_proposal_supersessions("hongri"),
            expected_supersessions,
        )
        self.assertEqual(
            self.store.get_proposal_history("hongri", "proposal-old")[
                "lifecycle_actions"
            ],
            expected_actions,
        )
        self.assertEqual(self.store.get_current_state("hongri"), expected_state)

    def test_meeting_item_link_requires_matching_classification_and_quote(self) -> None:
        digest = hashlib.sha256(b"meeting").hexdigest()
        source_id = "meeting-source"
        self.store.register_source(
            self.context(self.fox, "source"),
            SourceRecord(
                source_id,
                digest,
                7,
                "meetings/meeting.md",
                "meeting_minutes",
                "P2",
            ),
        )
        payload = {
            "schema_version": "meeting-ingest.v1",
            "source_is_data": True,
            "base_state_version": self.store.get_project_version("hongri"),
            "meeting": {
                "meeting_id": "meeting-1",
                "title": "看版会",
                "occurred_at": "2026-07-22T10:00:00+08:00",
                "participants": ["Fox"],
                "mode": "SYNC",
                "mode_confidence": 0.9,
                "source": {
                    "logical_source_id": source_id,
                    "source_version_id": legacy_source_version_id(source_id, digest),
                    "sha256": digest,
                    "verification": "verified",
                },
            },
            "segments": [
                {
                    "segment_id": "segment-1",
                    "locator": "00:00:10-00:00:15",
                    "quote": "月底前最好看到一版。",
                    "speaker": "Fox",
                    "spoken_at": "00:00:10",
                    "start_ms": 10000,
                    "end_ms": 15000,
                    "context": "同步看版时间",
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
                    "reason": "最好表示暂定时间",
                    "requires_human_confirmation": True,
                }
            ],
            "conflicts": [],
        }
        self.store.ingest_meeting_batch(
            self.context(self.ai, "meeting"), parse_meeting_ingest(payload)
        )
        linked = ProposalDraft(
            "proposal-date",
            "create",
            "TARGET_DATE",
            "target-date-1",
            None,
            {"id": "target-date-1", "date": "月底前", "kind": "TENTATIVE_DATE"},
            "来自会议解释项",
            "内部看版",
            ("meeting:meeting-1#segment-1",),
            source_meeting_item_id="item-date",
        )
        self.store.create_proposal(self.context(self.ai, "linked"), linked)
        stored = self.store.list_proposals("hongri")[0]
        self.assertEqual(stored["linked_meeting_item_id"], "item-date")

        with self.assertRaises(ResourceConflict):
            self.store.create_proposal(
                self.context(self.ai, "wrong-class"),
                ProposalDraft(
                    "proposal-wrong-class",
                    "create",
                    "OPEN",
                    "question-1",
                    None,
                    {"id": "question-1"},
                    "分类不一致",
                    "内部看版",
                    ("meeting:meeting-1#segment-1",),
                    source_meeting_item_id="item-date",
                ),
            )
        with self.assertRaises(ResourceConflict):
            self.store.create_proposal(
                self.context(self.ai, "missing-quote"),
                ProposalDraft(
                    "proposal-missing-quote",
                    "create",
                    "TARGET_DATE",
                    "target-date-2",
                    None,
                    {"id": "target-date-2"},
                    "缺少原话引用",
                    "内部看版",
                    ("evidence:other",),
                    source_meeting_item_id="item-date",
                ),
            )


if __name__ == "__main__":
    unittest.main()
