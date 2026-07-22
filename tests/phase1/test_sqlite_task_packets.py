"""Task Packet 最小装配、模式切换和 Agent 运行留痕测试。"""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from brand_os.domain import (
    Actor,
    ActorKind,
    CommandContext,
    ProposalDraft,
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
from brand_os.task_packets import (
    AgentRunRequest,
    RuntimeTaskDefinition,
    TaskContextRef,
    WorkModeSwitch,
)


class SQLiteTaskPacketTest(unittest.TestCase):
    """验证 Task Packet 只带当前相关内容，且模式由 Fox 控制。"""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "project.db"
        self.store = SQLiteCanonicalStore(self.database)
        self.fox = Actor(ActorKind.HUMAN, "Fox")
        self.ai = Actor(ActorKind.AI, "codex")
        self.system = Actor(ActorKind.SYSTEM, "local-runtime")
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
                "meetings/current.md",
                "meeting_minutes",
                "P2",
            ),
        )
        self.store.ingest_meeting_batch(
            self.context(self.ai, "meeting"), self.meeting_batch()
        )
        self.create_and_approve(
            ProposalDraft(
                "proposal-current",
                "create",
                "DECISION_CANDIDATE",
                "decision-current",
                None,
                {"id": "decision-current", "statement": "采用当前方向"},
                "Fox 在会上明确确认",
                "本轮品牌任务",
                ("meeting:meeting-1#segment-1",),
            ),
            "current",
        )
        self.create_and_approve(
            ProposalDraft(
                "proposal-open",
                "create",
                "OPEN",
                "question-current",
                None,
                {"id": "question-current", "question": "主推版本还需确认"},
                "会议尚未回答",
                "本轮品牌任务",
                ("evidence:unknown",),
            ),
            "open",
        )
        self.create_and_approve(
            ProposalDraft(
                "proposal-unrelated",
                "create",
                "DECISION_CANDIDATE",
                "decision-unrelated",
                None,
                {"id": "decision-unrelated", "statement": "其他任务的决定"},
                "与本轮无关",
                "其他任务",
                ("meeting:meeting-1#segment-1",),
            ),
            "unrelated",
        )
        self.create_and_approve(
            ProposalDraft(
                "proposal-expired",
                "create",
                "DECISION_CANDIDATE",
                "decision-expired",
                None,
                {"id": "decision-expired", "statement": "已经过期的方向"},
                "只在旧阶段有效",
                "旧阶段",
                ("meeting:meeting-1#segment-1",),
                valid_until="2020-01-01T00:00:00+08:00",
            ),
            "expired",
        )
        self.task = RuntimeTaskDefinition(
            task_id="task-brand-copy",
            goal="按当前决定准备一版品牌文案",
            role="BRAND_STRATEGIST",
            work_mode="EXPLORATION",
            deliverables=("两个有取舍的方向",),
            non_goals=("不替 Fox 批准方向",),
            context_refs=(
                TaskContextRef("DECISION", "decision-current"),
                TaskContextRef("OPEN", "question-current"),
                TaskContextRef("DECISION", "decision-expired"),
            ),
            evidence_refs=(),
            known_gap_ids=(),
            allowed_tools=("evidence_get",),
            network="deny",
            model_allowlist=("codex", "claude"),
            output_schema_ref="state-proposal.v1",
            acceptance_criteria=("重要结论可回源", "没有一票否决"),
        )
        self.registration = self.store.register_runtime_task(
            "hongri", self.fox, self.task, idempotency_key="register-task"
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def context(
        self, actor: Actor, key: str, version: int | None = None
    ) -> CommandContext:
        current = (
            self.store.get_project_version("hongri") if version is None else version
        )
        return CommandContext("hongri", actor, key, current)

    def meeting_batch(self):
        return parse_meeting_ingest(
            {
                "schema_version": "meeting-ingest.v1",
                "source_is_data": True,
                "base_state_version": self.store.get_project_version("hongri"),
                "meeting": {
                    "meeting_id": "meeting-1",
                    "title": "方向确认会",
                    "occurred_at": "2026-07-22T10:00:00+08:00",
                    "participants": ["Fox"],
                    "mode": "DECISION",
                    "mode_confidence": 0.95,
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
                        "locator": "00:01:00-00:01:08",
                        "quote": "本轮就按这个方向继续。",
                        "speaker": "Fox",
                        "spoken_at": "00:01:00",
                        "start_ms": 60000,
                        "end_ms": 68000,
                        "context": "确认本轮方向",
                        "mode": "DECISION",
                        "mode_confidence": 0.95,
                    }
                ],
                "items": [],
                "conflicts": [],
            }
        )

    def create_and_approve(self, proposal: ProposalDraft, key: str) -> None:
        self.store.create_proposal(
            self.context(self.ai, f"create-{key}"), proposal
        )
        self.store.review_proposal(
            self.context(self.fox, f"approve-{key}"),
            ProposalReview(proposal.proposal_id, ReviewAction.APPROVE, "Fox 明确确认"),
        )

    def test_packet_only_contains_selected_current_context_and_explicit_gaps(self) -> None:
        version = self.store.get_project_version("hongri")
        packet = self.store.build_task_packet(
            "hongri", self.task.task_id, self.system, expected_state_version=version
        )

        decision_ids = [
            value["item_id"] for value in packet["approved_state"]["decisions"]
        ]
        question_ids = [
            value["item_id"]
            for value in packet["working_state"]["open_questions"]
        ]
        gap_ids = {value["gap_id"] for value in packet["known_gaps"]}
        evidence = {
            value["evidence_ref"]: value for value in packet["relevant_evidence"]
        }

        self.assertEqual(decision_ids, ["decision-current"])
        self.assertEqual(question_ids, ["question-current"])
        self.assertNotIn("decision-unrelated", decision_ids)
        self.assertIn("context:DECISION:decision-expired", gap_ids)
        self.assertEqual(
            evidence["meeting:meeting-1#segment-1"]["verification"], "confirmed"
        )
        self.assertEqual(evidence["evidence:unknown"]["verification"], "unconfirmed")
        self.assertIn("evidence:evidence:unknown", gap_ids)
        self.assertEqual(packet["runtime_policy"]["mode_switch_authority"], "Fox")
        self.assertFalse(packet["mode_contract"]["runtime_may_apply_switch"])
        self.assertEqual(packet["context_watermark"]["selected_context_count"], 2)
        self.assertEqual(packet["base_state_version"], version)
        self.assertTrue(self.store.validate_task_packet("hongri", packet["packet_id"])["valid"])

        repeated = self.store.build_task_packet("hongri", self.task.task_id, self.system)
        self.assertEqual(repeated["packet_id"], packet["packet_id"])
        self.assertEqual(repeated["packet_version"], 1)
        self.assertEqual(
            self.store.get_task_packet_layer("hongri", packet["packet_id"], "L2")[
                "relevant_evidence"
            ],
            packet["relevant_evidence"],
        )
        self.assertFalse(
            self.store.get_task_packet_layer("hongri", packet["packet_id"], "L4")[
                "loaded"
            ]
        )

    def test_only_fox_can_choose_or_switch_mode_and_old_packets_remain_immutable(self) -> None:
        old_packet = self.store.build_task_packet("hongri", self.task.task_id, self.system)
        with self.assertRaises(BusinessPermissionDenied):
            self.store.register_runtime_task(
                "hongri", self.ai, self.task, idempotency_key="ai-register"
            )
        with self.assertRaises(BusinessPermissionDenied):
            self.store.switch_work_mode(
                "hongri",
                self.ai,
                WorkModeSwitch(
                    self.task.task_id,
                    "EXECUTION",
                    "AI 试图切换",
                    "本轮品牌任务",
                    1,
                ),
                idempotency_key="ai-switch",
            )

        switch = WorkModeSwitch(
            self.task.task_id,
            "EXECUTION",
            "Fox 已确认进入执行",
            "本轮品牌任务",
            1,
            suggested_by_runtime="codex",
        )
        first = self.store.switch_work_mode(
            "hongri", self.fox, switch, idempotency_key="fox-switch"
        )
        repeated = self.store.switch_work_mode(
            "hongri", self.fox, switch, idempotency_key="fox-switch"
        )
        new_packet = self.store.build_task_packet("hongri", self.task.task_id, self.system)

        self.assertEqual(first, repeated)
        self.assertEqual(first["schema_version"], "runtime-mode-switch.v2")
        self.assertEqual(first["from_mode"], "EXPLORATION")
        self.assertEqual(first["to_mode"], "EXECUTION")
        self.assertEqual(first["to_task_revision"], 2)
        self.assertEqual(new_packet["task"]["work_mode"], "EXECUTION")
        self.assertEqual(new_packet["context_watermark"]["task_revision"], 2)
        self.assertEqual(new_packet["packet_version"], 2)
        self.assertEqual(
            self.store.get_task_packet("hongri", old_packet["packet_id"])["task"][
                "work_mode"
            ],
            "EXPLORATION",
        )
        self.assertEqual(
            len(self.store.list_runtime_mode_switches("hongri", self.task.task_id)), 1
        )

    def test_agent_run_copies_mode_and_versions_from_packet(self) -> None:
        packet = self.store.build_task_packet("hongri", self.task.task_id, self.system)
        request = AgentRunRequest(
            run_id="run-1",
            packet_id=packet["packet_id"],
            expected_packet_hash=packet["content_hash"],
            runtime_id="codex-cli",
            runtime_version="1.0.0",
            model_id="codex",
            model_version="gpt-5",
            idempotency_key="run-start-1",
        )
        first = self.store.record_agent_run("hongri", self.system, request)
        repeated = self.store.record_agent_run("hongri", self.system, request)

        self.assertEqual(first, repeated)
        self.assertEqual(first["work_mode"], packet["task"]["work_mode"])
        self.assertEqual(first["role"], packet["task"]["role"])
        self.assertEqual(first["base_state_version"], packet["base_state_version"])
        self.assertEqual(first["task_revision"], 1)
        self.assertEqual(first["protocol_versions"], packet["protocol_versions"])
        self.assertEqual(
            self.store.get_agent_run("hongri", "run-1")["model_version"], "gpt-5"
        )
        with self.assertRaises(BusinessPermissionDenied):
            self.store.record_agent_run(
                "hongri",
                self.ai,
                AgentRunRequest(
                    "run-ai",
                    packet["packet_id"],
                    packet["content_hash"],
                    "codex-cli",
                    "1.0.0",
                    "codex",
                    "gpt-5",
                    "run-ai",
                ),
            )
        with self.assertRaises(ResourceConflict):
            self.store.record_agent_run(
                "hongri",
                self.system,
                AgentRunRequest(
                    "run-bad-hash",
                    packet["packet_id"],
                    "0" * 64,
                    "codex-cli",
                    "1.0.0",
                    "codex",
                    "gpt-5",
                    "run-bad-hash",
                ),
            )

    def test_stale_state_version_is_rejected_before_packet_is_saved(self) -> None:
        current = self.store.get_project_version("hongri")
        with self.assertRaises(VersionConflict):
            self.store.build_task_packet(
                "hongri",
                self.task.task_id,
                self.system,
                expected_state_version=current - 1,
            )


if __name__ == "__main__":
    unittest.main()
