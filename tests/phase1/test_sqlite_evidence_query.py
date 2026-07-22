"""决定、开放问题、关系有效性和全链回源测试。"""

from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from brand_os.domain import (
    RELATION_TYPES,
    Actor,
    ActorKind,
    CommandContext,
    ProposalDraft,
    ProposalReview,
    RelationDraft,
    ReviewAction,
    SourceImportBatch,
    SourceImportRecord,
    SourceRecord,
    imported_source_version_id,
    legacy_source_version_id,
)
from brand_os.meeting_ingest import parse_meeting_ingest
from brand_os.sqlite_store import SQLiteCanonicalStore


AS_OF = "2026-07-22T12:00:00+08:00"


class SQLiteEvidenceQueryTest(unittest.TestCase):
    """验证读取面不把废案、过期项或缺证据内容冒充当前事实。"""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "project.db"
        self.store = SQLiteCanonicalStore(self.database)
        self.fox = Actor(ActorKind.HUMAN, "Fox")
        self.ai = Actor(ActorKind.AI, "codex")
        self.store.create_project(self.context(self.fox, "project", 0), "鸿日")
        self.source_sha256 = hashlib.sha256(b"meeting-source").hexdigest()
        self.source_id = "meeting-source"
        self.source_version_id = legacy_source_version_id(
            self.source_id, self.source_sha256
        )
        self.store.register_source(
            self.context(self.fox, "source"),
            SourceRecord(
                self.source_id,
                self.source_sha256,
                14,
                "meetings/meeting-1.md",
                "meeting_minutes",
                "P2",
                "current",
            ),
        )
        self._ingest_meeting()
        self._create_and_review(
            ProposalDraft(
                "proposal-current",
                "create",
                "DECISION_CANDIDATE",
                "decision-current",
                None,
                {"id": "decision-current", "statement": "本轮采用方案甲"},
                "来自已核验会议原话",
                "7 月经销商会",
                ("meeting:meeting-1#segment-1",),
                source_meeting_item_id="item-decision",
            ),
            ReviewAction.APPROVE,
            "确认本轮方向",
        )

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

    def _ingest_meeting(self) -> None:
        payload = {
            "schema_version": "meeting-ingest.v1",
            "source_is_data": True,
            "base_state_version": self.store.get_project_version("hongri"),
            "meeting": {
                "meeting_id": "meeting-1",
                "title": "方向确认会",
                "occurred_at": "2026-07-22T10:00:00+08:00",
                "participants": ["Fox", "同事甲"],
                "mode": "DECISION",
                "mode_confidence": 0.9,
                "source": {
                    "logical_source_id": self.source_id,
                    "source_version_id": self.source_version_id,
                    "sha256": self.source_sha256,
                    "verification": "verified",
                },
            },
            "segments": [
                {
                    "segment_id": "segment-1",
                    "locator": "00:01:10-00:01:18",
                    "quote": "本轮确定采用方案甲。",
                    "speaker": "Fox",
                    "spoken_at": "00:01:10",
                    "start_ms": 70000,
                    "end_ms": 78000,
                    "context": "比较方案甲和方案乙之后",
                    "transcript_confidence": 0.96,
                    "mode": "DECISION",
                    "mode_confidence": 0.9,
                }
            ],
            "items": [
                {
                    "item_id": "item-decision",
                    "type": "DECISION_CANDIDATE",
                    "summary": "本轮采用方案甲",
                    "scope": "7 月经销商会",
                    "decision_actor": "Fox",
                    "decision_verb": "确定采用",
                    "state_difference": "由未确认变为采用方案甲",
                    "evidence_segment_ids": ["segment-1"],
                    "confidence": 0.9,
                    "reason": "原话包含决定人、明确动词和范围",
                    "requires_human_confirmation": True,
                }
            ],
            "conflicts": [],
        }
        self.store.ingest_meeting_batch(
            self.context(self.ai, "meeting"), parse_meeting_ingest(payload)
        )

    def _create_and_review(
        self,
        proposal: ProposalDraft,
        action: ReviewAction,
        reason: str,
    ) -> None:
        self.store.create_proposal(
            self.context(self.ai, f"create-{proposal.proposal_id}"), proposal
        )
        self.store.review_proposal(
            self.context(self.fox, f"review-{proposal.proposal_id}"),
            ProposalReview(proposal.proposal_id, action, reason),
        )

    def test_decision_returns_human_approval_and_full_meeting_source_chain(self) -> None:
        decision = self.store.list_decisions("hongri", as_of=AS_OF)[0]
        evidence = decision["evidence"][0]

        self.assertEqual(decision["validity"]["status"], "current")
        self.assertEqual(decision["authority"]["actor_id"], "Fox")
        self.assertEqual(decision["scope"], "7 月经销商会")
        self.assertEqual(decision["evidence_status"], "confirmed")
        self.assertEqual(evidence["quote"], "本轮确定采用方案甲。")
        self.assertEqual(evidence["speaker"], "Fox")
        self.assertEqual(evidence["spoken_at"], "00:01:10")
        self.assertEqual(evidence["source"]["source_version_id"], self.source_version_id)
        self.assertEqual(evidence["source"]["sha256"], self.source_sha256)
        self.assertEqual(
            evidence["open_ref"], f"evidence://sha256/{self.source_sha256}"
        )

        chain = self.store.get_evidence_chain(
            "hongri", "DECISION", "decision-current", as_of=AS_OF
        )
        self.assertEqual(chain["verification"], "confirmed")
        self.assertEqual(chain["conclusion"]["proposal_id"], "proposal-current")

    def test_unknown_evidence_is_explicitly_unconfirmed_without_guessing(self) -> None:
        self._create_and_review(
            ProposalDraft(
                "proposal-missing-evidence",
                "create",
                "DECISION_CANDIDATE",
                "decision-missing-evidence",
                None,
                {"id": "decision-missing-evidence", "statement": "等待补证的方向"},
                "沿用旧引用格式",
                "本轮内容",
                ("evidence:unknown",),
            ),
            ReviewAction.APPROVE,
            "先记录决定，证据仍需补齐",
        )

        decisions = {
            item["item_id"]: item
            for item in self.store.list_decisions("hongri", as_of=AS_OF)
        }
        missing = decisions["decision-missing-evidence"]
        self.assertEqual(missing["authority"]["status"], "confirmed")
        self.assertEqual(missing["evidence_status"], "unconfirmed")
        self.assertIn("supported_evidence_ref", missing["missing_evidence"])
        self.assertTrue(missing["evidence"][0]["message"].startswith("未确认"))

        unknown = self.store.get_evidence_chain(
            "hongri", "DECISION", "does-not-exist", as_of=AS_OF
        )
        self.assertEqual(unknown["verification"], "unconfirmed")
        self.assertIsNone(unknown["conclusion"])

    def test_meeting_evidence_without_speaker_and_time_is_unconfirmed(self) -> None:
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                """
                UPDATE meeting_segments
                SET speaker = NULL, spoken_at = NULL, start_ms = NULL, end_ms = NULL
                WHERE project_id = 'hongri' AND segment_id = 'segment-1'
                """
            )
        decision = self.store.list_decisions("hongri", as_of=AS_OF)[0]
        evidence = decision["evidence"][0]
        self.assertEqual(decision["evidence_status"], "unconfirmed")
        self.assertEqual(evidence["verification"], "unconfirmed")
        self.assertEqual(
            set(evidence["missing_fields"]), {"speaker", "spoken_at_or_time_range"}
        )

    def test_expired_and_scheduled_items_are_filtered_by_default(self) -> None:
        for proposal_id, item_id, valid_from, valid_until in (
            (
                "proposal-expired",
                "decision-expired",
                "2026-07-01T00:00:00+08:00",
                "2026-07-22T08:00:00+08:00",
            ),
            (
                "proposal-scheduled",
                "decision-scheduled",
                "2026-07-23T00:00:00+08:00",
                None,
            ),
        ):
            self._create_and_review(
                ProposalDraft(
                    proposal_id,
                    "create",
                    "DECISION_CANDIDATE",
                    item_id,
                    None,
                    {"id": item_id, "statement": item_id},
                    "验证有效期过滤",
                    "本轮内容",
                    (f"source-version:{self.source_version_id}#line:1",),
                    valid_from=valid_from,
                    valid_until=valid_until,
                ),
                ReviewAction.APPROVE,
                "确认有效期",
            )

        current_ids = {
            item["item_id"] for item in self.store.list_decisions("hongri", as_of=AS_OF)
        }
        self.assertEqual(current_ids, {"decision-current"})
        all_items = {
            item["item_id"]: item
            for item in self.store.list_decisions(
                "hongri", as_of=AS_OF, include_inactive=True
            )
        }
        self.assertEqual(all_items["decision-expired"]["validity"]["status"], "expired")
        self.assertEqual(
            all_items["decision-scheduled"]["validity"]["status"], "scheduled"
        )

    def test_superseded_and_rejected_drafts_do_not_enter_current_decisions(self) -> None:
        old_payload = {"id": "decision-current", "statement": "本轮采用方案甲"}
        self._create_and_review(
            ProposalDraft(
                "proposal-successor",
                "supersede",
                "DECISION_CANDIDATE",
                "decision-successor",
                old_payload,
                {"id": "decision-successor", "statement": "本轮改用方案乙"},
                "新证据改变方向",
                "7 月经销商会",
                ("meeting:meeting-1#segment-1",),
                supersedes_proposal_id="proposal-current",
            ),
            ReviewAction.APPROVE,
            "确认由方案乙替代方案甲",
        )
        self._create_and_review(
            ProposalDraft(
                "proposal-rejected",
                "create",
                "DECISION_CANDIDATE",
                "decision-rejected",
                None,
                {"id": "decision-rejected", "statement": "未采用方案"},
                "待评审",
                "本轮内容",
                ("meeting:meeting-1#segment-1",),
            ),
            ReviewAction.REJECT,
            "不采用",
        )

        current = self.store.list_decisions("hongri", as_of=AS_OF)
        self.assertEqual([item["item_id"] for item in current], ["decision-successor"])
        history = {
            item["item_id"]: item
            for item in self.store.list_decisions(
                "hongri", as_of=AS_OF, include_inactive=True
            )
        }
        self.assertEqual(history["decision-current"]["validity"]["status"], "superseded")
        self.assertEqual(history["decision-rejected"]["validity"]["status"], "archived")
        self.assertEqual(history["decision-rejected"]["authority"]["status"], "unconfirmed")

        default_relations = self.store.query_relations("hongri", as_of=AS_OF)
        historical_relations = self.store.query_relations(
            "hongri", as_of=AS_OF, include_inactive=True
        )
        self.assertFalse(
            any(
                relation["relation_type"] == "supersedes"
                and relation["to"]["id"] == "proposal-current"
                for relation in default_relations
            )
        )
        self.assertTrue(
            any(
                relation["relation_type"] == "supersedes"
                and relation["to"]["id"] == "proposal-current"
                for relation in historical_relations
            )
        )

    def test_open_questions_apply_the_same_current_filter(self) -> None:
        self._create_and_review(
            ProposalDraft(
                "proposal-open-current",
                "create",
                "OPEN",
                "question-current",
                None,
                {"id": "question-current", "question": "本轮主推哪个版本"},
                "会议尚未确认",
                "7 月经销商会",
                (f"source-version:{self.source_version_id}#line:12",),
            ),
            ReviewAction.APPROVE,
            "确认这是当前开放问题",
        )
        self._create_and_review(
            ProposalDraft(
                "proposal-open-rejected",
                "create",
                "OPEN",
                "question-rejected",
                None,
                {"id": "question-rejected", "question": "已废弃问题"},
                "等待确认",
                "历史内容",
                (f"source-version:{self.source_version_id}#line:13",),
            ),
            ReviewAction.REJECT,
            "问题已经无效",
        )

        current = self.store.list_open_questions("hongri", as_of=AS_OF)
        self.assertEqual([item["item_id"] for item in current], ["question-current"])

    def test_all_relation_types_are_queryable_and_unknown_endpoint_stays_visible(self) -> None:
        for index, relation_type in enumerate(sorted(RELATION_TYPES)):
            self.store.add_relation(
                self.context(self.ai, f"relation-{index}"),
                RelationDraft(
                    f"relation-{index}",
                    "proposal",
                    "proposal-current",
                    relation_type,
                    "human",
                    "Fox",
                    "meeting:meeting-1#segment-1",
                ),
            )
        self.store.add_relation(
            self.context(self.ai, "relation-unknown"),
            RelationDraft(
                "relation-unknown",
                "unknown_entity",
                "missing-a",
                "depends_on",
                "unknown_entity",
                "missing-b",
                "evidence:missing",
            ),
        )

        relations = self.store.query_relations("hongri", as_of=AS_OF)
        explicit_types = {
            relation["relation_type"]
            for relation in relations
            if relation["origin"] == "explicit_working_relation"
        }
        self.assertEqual(explicit_types, RELATION_TYPES)
        unknown = next(
            relation for relation in relations if relation["relation_id"] == "relation-unknown"
        )
        self.assertEqual(unknown["validity"]["status"], "unknown")
        self.assertEqual(unknown["evidence_status"], "unconfirmed")
        with self.assertRaises(ValueError):
            self.store.query_relations("hongri", relation_types=["similar_to"])

    def test_source_version_ref_is_stable_and_reports_source_validity(self) -> None:
        evidence_ref = f"source-version:{self.source_version_id}#line:8"
        evidence = self.store.resolve_evidence_ref("hongri", evidence_ref)
        self.assertEqual(evidence["verification"], "confirmed")
        self.assertEqual(evidence["locator"], "line:8")
        self.assertEqual(evidence["source"]["current_validity"], "current")
        self.assertEqual(
            evidence["open_ref"], f"evidence://sha256/{self.source_sha256}"
        )

    def test_source_version_change_keeps_old_version_and_filters_old_relation(self) -> None:
        new_sha256 = hashlib.sha256(b"meeting-source-v2").hexdigest()
        batch = SourceImportBatch(
            manifest_sha256=hashlib.sha256(b"manifest-v2").hexdigest(),
            import_digest=hashlib.sha256(b"import-v2").hexdigest(),
            manifest_schema_version="source-import.v1",
            origin_ref="test:source-v2",
            records=(
                SourceImportRecord(
                    logical_source_id=self.source_id,
                    sha256=new_sha256,
                    relative_path="meetings/meeting-1-v2.md",
                    source_role="meeting_minutes",
                    confidentiality="P2",
                    size_bytes=17,
                    media_type="text/markdown",
                    status="current",
                    version_label="v2",
                    supersedes_sha256=(self.source_sha256,),
                ),
            ),
        )
        self.store.import_source_batch(self.context(self.fox, "source-v2"), batch)
        successor_id = imported_source_version_id(self.source_id, new_sha256)

        old_evidence = self.store.resolve_evidence_ref(
            "hongri", f"source-version:{self.source_version_id}#line:1"
        )
        new_evidence = self.store.resolve_evidence_ref(
            "hongri", f"source-version:{successor_id}#line:1"
        )
        self.assertEqual(old_evidence["source"]["current_validity"], "superseded")
        self.assertEqual(new_evidence["source"]["current_validity"], "current")

        current_relations = self.store.query_relations("hongri", as_of=AS_OF)
        historical_relations = self.store.query_relations(
            "hongri", as_of=AS_OF, include_inactive=True
        )
        self.assertFalse(
            any(
                relation["relation_type"] == "supersedes"
                and relation["from"]["id"] == successor_id
                for relation in current_relations
            )
        )
        self.assertTrue(
            any(
                relation["relation_type"] == "supersedes"
                and relation["from"]["id"] == successor_id
                for relation in historical_relations
            )
        )


if __name__ == "__main__":
    unittest.main()
