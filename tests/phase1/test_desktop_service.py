"""桌面项目视图、独立人工评审和桥接分路测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from brand_os.desktop_bridge import (
    DESKTOP_BRIDGE_REQUEST_SCHEMA_VERSION,
    DesktopBridgeError,
    dispatch_desktop_request,
)
from brand_os.desktop_service import (
    DESKTOP_PROJECT_VIEW_SCHEMA_VERSION,
    DESKTOP_PROPOSAL_REVIEW_SCHEMA_VERSION,
    DesktopProjectService,
    DesktopServiceError,
)
from brand_os.domain import Actor, ActorKind, CommandContext, ProposalDraft
from brand_os.local_access import LocalAIService, ToolNotAllowed
from brand_os.sqlite_base import VersionConflict
from brand_os.sqlite_store import SQLiteCanonicalStore
from brand_os.task_packets import RuntimeTaskDefinition


class DesktopProjectServiceTest(unittest.TestCase):
    """验证桌面读取与人工确认没有混入 AI 工具面。"""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "project.db"
        self.store = SQLiteCanonicalStore(self.database)
        self.fox = Actor(ActorKind.HUMAN, "Fox")
        self.ai = Actor(ActorKind.AI, "codex")
        self.store.create_project(
            CommandContext("hongri", self.fox, "project", 0), "鸿日"
        )
        self.task = RuntimeTaskDefinition(
            task_id="task-desktop",
            goal="核对鸿日当前状态与待确认变化",
            role="BRAND_STRATEGIST",
            work_mode="EVALUATION",
            deliverables=("形成可回源评审结论",),
            non_goals=("不允许 AI 批准",),
            context_refs=(),
            evidence_refs=(),
            known_gap_ids=(),
            allowed_tools=("task_get_packet", "evidence_get", "proposal_create"),
            network="deny",
            model_allowlist=("codex", "claude"),
            output_schema_ref="state-proposal.v1",
            acceptance_criteria=("所有正式变化由 Fox 确认",),
        )
        self.store.register_runtime_task(
            "hongri", self.fox, self.task, idempotency_key="register-task"
        )
        self.packet = self.store.build_task_packet(
            "hongri", self.task.task_id, Actor(ActorKind.SYSTEM, "packet-builder")
        )
        self.store.create_proposal(
            CommandContext(
                "hongri",
                self.ai,
                "create-proposal",
                self.store.get_project_version("hongri"),
            ),
            ProposalDraft(
                proposal_id="proposal-desktop",
                proposal_kind="create",
                classification="OPEN",
                subject_id="question-main-version",
                before=None,
                after={
                    "id": "question-main-version",
                    "question": "主推版本是否已经确认",
                },
                reason="当前资料没有正式确认记录",
                impact_scope="鸿日当前项目",
                evidence_refs=("evidence:not-registered",),
            ),
        )
        self.service = DesktopProjectService(self.store, "hongri")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_project_view_aggregates_authoritative_data_without_claiming_approval(self) -> None:
        view = self.service.get_project_view()

        self.assertEqual(view["schema_version"], DESKTOP_PROJECT_VIEW_SCHEMA_VERSION)
        self.assertEqual(view["project"]["name"], "鸿日")
        self.assertEqual(view["summary"]["pending_proposal_count"], 1)
        self.assertEqual(view["summary"]["runtime_task_count"], 1)
        self.assertEqual(view["summary"]["task_packet_count"], 1)
        self.assertEqual(view["runtime_tasks"][0]["spec"]["goal"], self.task.goal)
        self.assertEqual(view["task_packets"][0]["packet_id"], self.packet["packet_id"])
        self.assertFalse(view["authority"]["agent_can_approve"])
        self.assertEqual(view["current_state"], [])

    def test_fox_review_changes_state_and_records_the_expected_version(self) -> None:
        expected_version = self.store.get_project_version("hongri")
        result = self.service.review_proposal(
            {
                "schema_version": DESKTOP_PROPOSAL_REVIEW_SCHEMA_VERSION,
                "proposal_id": "proposal-desktop",
                "action": "approve",
                "reason": "Fox 已核对原始资料并确认",
                "expected_version": expected_version,
                "idempotency_key": "desktop-review-proposal",
            }
        )

        self.assertEqual(result["proposal"]["status"], "approved")
        self.assertEqual(result["command"]["project_version"], expected_version + 1)
        self.assertEqual(len(self.store.get_current_state("hongri")), 1)
        action = self.store.list_human_actions("hongri")[0]
        self.assertEqual(action["actor_id"], "Fox")
        self.assertEqual(action["base_state_version"], expected_version)

    def test_review_rejects_stale_version_and_unknown_fields(self) -> None:
        current_version = self.store.get_project_version("hongri")
        self.store.create_proposal(
            CommandContext("hongri", self.ai, "advance-version", current_version),
            ProposalDraft(
                proposal_id="proposal-version-advance",
                proposal_kind="create",
                classification="OPEN",
                subject_id="question-version-advance",
                before=None,
                after={"id": "question-version-advance", "question": "测试版本推进"},
                reason="制造并发版本变化",
                impact_scope="测试",
                evidence_refs=("evidence:not-registered",),
            ),
        )
        with self.assertRaises(VersionConflict):
            self.service.review_proposal(
                {
                    "schema_version": DESKTOP_PROPOSAL_REVIEW_SCHEMA_VERSION,
                    "proposal_id": "proposal-desktop",
                    "action": "reject",
                    "reason": "证据不足",
                    "expected_version": current_version,
                    "idempotency_key": "stale-review",
                }
            )
        with self.assertRaises(DesktopServiceError):
            self.service.review_proposal(
                {
                    "schema_version": DESKTOP_PROPOSAL_REVIEW_SCHEMA_VERSION,
                    "proposal_id": "proposal-desktop",
                    "action": "approve",
                    "reason": "测试",
                    "expected_version": self.store.get_project_version("hongri"),
                    "idempotency_key": "unknown-field",
                    "approved_by_ai": True,
                }
            )

    def test_review_requires_action_specific_replacement_content(self) -> None:
        base = {
            "schema_version": DESKTOP_PROPOSAL_REVIEW_SCHEMA_VERSION,
            "proposal_id": "proposal-desktop",
            "reason": "Fox 核对验收请求",
            "expected_version": self.store.get_project_version("hongri"),
            "idempotency_key": "action-specific-content",
        }
        with self.assertRaisesRegex(DesktopServiceError, "修改后的内容"):
            self.service.review_proposal(
                {**base, "action": "modify_and_approve"}
            )
        with self.assertRaisesRegex(DesktopServiceError, "只有修改后批准"):
            self.service.review_proposal(
                {**base, "action": "approve", "replacement_after": {}}
            )

    def test_bridge_rejects_database_symlink_before_resolving_it(self) -> None:
        from brand_os.desktop_bridge import _database_path

        target = Path(self.temporary.name) / "target.db"
        target.touch()
        link = Path(self.temporary.name) / "link.db"
        link.symlink_to(target)
        with patch("brand_os.desktop_bridge.WorkspaceLayout"):
            with self.assertRaisesRegex(DesktopBridgeError, "符号链接"):
                _database_path(object(), link)  # type: ignore[arg-type]

    def test_mcp_cannot_call_desktop_review_operation(self) -> None:
        ai_service = LocalAIService(self.store, "hongri", caller_id="mcp-test")

        with self.assertRaises(ToolNotAllowed):
            ai_service.invoke("proposal_review", {"proposal_id": "proposal-desktop"})

    def test_bridge_dispatch_has_closed_read_and_write_operations(self) -> None:
        project_view = dispatch_desktop_request(
            self.service,
            {
                "schema_version": DESKTOP_BRIDGE_REQUEST_SCHEMA_VERSION,
                "operation": "project_view",
                "payload": {},
            },
        )
        packet_view = dispatch_desktop_request(
            self.service,
            {
                "schema_version": DESKTOP_BRIDGE_REQUEST_SCHEMA_VERSION,
                "operation": "task_packet_get",
                "payload": {"packet_id": self.packet["packet_id"]},
            },
        )

        self.assertEqual(project_view["project"]["project_id"], "hongri")
        self.assertEqual(packet_view["packet"]["content_hash"], self.packet["content_hash"])
        with self.assertRaises(DesktopBridgeError):
            dispatch_desktop_request(
                self.service,
                {
                    "schema_version": DESKTOP_BRIDGE_REQUEST_SCHEMA_VERSION,
                    "operation": "direct_sql",
                    "payload": {"sql": "SELECT * FROM projects"},
                },
            )

if __name__ == "__main__":
    unittest.main()
