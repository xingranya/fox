"""本地权威存储机器契约测试。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[2]
CONTRACT_PATH = ROOT / "contracts" / "phase1" / "canonical-store.json"
SOURCE_IMPORT_CONTRACT_PATH = ROOT / "contracts" / "phase1" / "source-import.json"
MEETING_INGEST_CONTRACT_PATH = ROOT / "contracts" / "phase1" / "meeting-ingest.json"
PROPOSAL_LIFECYCLE_CONTRACT_PATH = (
    ROOT / "contracts" / "phase1" / "proposal-lifecycle.json"
)
EVIDENCE_QUERY_CONTRACT_PATH = ROOT / "contracts" / "phase1" / "evidence-query.json"
TASK_PACKET_CONTRACT_PATH = ROOT / "contracts" / "phase1" / "task-packet-assembly.json"
AGENT_RUN_CONTRACT_PATH = ROOT / "contracts" / "phase1" / "agent-runtime-run.json"
TASK_PACKET_SCHEMA_PATH = ROOT / "schemas" / "phase1" / "task-packet.schema.json"
RUNTIME_MODE_SWITCH_SCHEMA_PATH = (
    ROOT / "schemas" / "phase1" / "runtime-mode-switch.schema.json"
)
RUNTIME_RUN_SCHEMA_PATH = ROOT / "schemas" / "phase1" / "runtime-run.schema.json"


class CanonicalStoreContractTest(unittest.TestCase):
    """验证 AI 与人工写权限始终分路。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        cls.source_import_contract = json.loads(
            SOURCE_IMPORT_CONTRACT_PATH.read_text(encoding="utf-8")
        )
        cls.meeting_ingest_contract = json.loads(
            MEETING_INGEST_CONTRACT_PATH.read_text(encoding="utf-8")
        )
        cls.proposal_lifecycle_contract = json.loads(
            PROPOSAL_LIFECYCLE_CONTRACT_PATH.read_text(encoding="utf-8")
        )
        cls.evidence_query_contract = json.loads(
            EVIDENCE_QUERY_CONTRACT_PATH.read_text(encoding="utf-8")
        )
        cls.task_packet_contract = json.loads(
            TASK_PACKET_CONTRACT_PATH.read_text(encoding="utf-8")
        )
        cls.agent_run_contract = json.loads(
            AGENT_RUN_CONTRACT_PATH.read_text(encoding="utf-8")
        )
        cls.task_packet_schema = json.loads(
            TASK_PACKET_SCHEMA_PATH.read_text(encoding="utf-8")
        )
        cls.runtime_mode_switch_schema = json.loads(
            RUNTIME_MODE_SWITCH_SCHEMA_PATH.read_text(encoding="utf-8")
        )
        cls.runtime_run_schema = json.loads(
            RUNTIME_RUN_SCHEMA_PATH.read_text(encoding="utf-8")
        )

    def test_contract_is_replaceable_and_versioned(self) -> None:
        self.assertEqual(self.contract["schema_version"], "canonical-store-port.v6")
        self.assertTrue(self.contract["replaceable"])
        self.assertEqual(self.contract["current_implementation"], "sqlite")

    def test_only_fox_review_can_change_current_state(self) -> None:
        state_commands = [
            command for command in self.contract["commands"] if command["changes_current_state"]
        ]
        self.assertEqual(len(state_commands), 1)
        self.assertEqual(state_commands[0]["name"], "review_proposal")
        self.assertEqual(state_commands[0]["allowed_actor_kinds"], ["HUMAN"])
        self.assertEqual(state_commands[0]["allowed_actor_ids"], ["Fox"])

    def test_agent_cannot_receive_business_approval_or_direct_sql(self) -> None:
        forbidden = set(self.contract["forbidden_agent_operations"])
        self.assertTrue(
            {
                "approve",
                "modify_and_approve",
                "reject",
                "reopen",
                "approve_supersede",
                "direct_sql",
            }.issubset(forbidden)
        )

    def test_reopen_and_supersede_keep_fox_as_the_only_business_reviewer(self) -> None:
        reopen = next(
            command for command in self.contract["commands"]
            if command["name"] == "reopen_proposal"
        )
        self.assertEqual(reopen["allowed_actor_kinds"], ["HUMAN"])
        self.assertEqual(reopen["allowed_actor_ids"], ["Fox"])
        self.assertFalse(reopen["changes_current_state"])
        self.assertTrue(
            self.proposal_lifecycle_contract["state_projection"][
                "supersede_removes_predecessor_from_current_projection"
            ]
        )
        self.assertFalse(
            self.proposal_lifecycle_contract["authority"][
                "tool_permission_is_business_approval"
            ]
        )
        self.assertEqual(
            self.contract["proposal_lifecycle_rebuild_source"],
            "proposal events from configured human reviewers",
        )
        self.assertEqual(self.contract["backup"]["schema_version"], "sqlite-backup.v6")

    def test_evidence_queries_are_read_only_versioned_and_fail_closed(self) -> None:
        self.assertEqual(
            self.evidence_query_contract["schema_version"], "evidence-query.v1"
        )
        self.assertTrue(self.evidence_query_contract["read_only"])
        self.assertEqual(
            set(self.evidence_query_contract["relation_types"]),
            {
                "sourced_from",
                "raised_in",
                "supports",
                "opposes",
                "conflicts_with",
                "applies_to",
                "approved_by",
                "supersedes",
                "depends_on",
                "answers",
                "pending_confirmation",
            },
        )
        self.assertFalse(
            self.evidence_query_contract["authority"][
                "unresolved_evidence_may_be_guessed"
            ]
        )

    def test_every_write_requires_idempotency_and_expected_version(self) -> None:
        requirements = self.contract["write_requirements"]
        self.assertTrue(requirements["idempotency_key"])
        self.assertTrue(requirements["expected_version"])
        self.assertTrue(requirements["single_transaction_event_and_projection"])

    def test_task_packet_and_agent_run_bind_mode_without_agent_authority(self) -> None:
        self.assertEqual(
            self.task_packet_contract["schema_version"], "task-packet-assembly.v1"
        )
        self.assertEqual(self.task_packet_contract["packet_schema"], "task-packet.v2")
        self.assertEqual(
            self.task_packet_contract["mode_switch_schema"],
            "runtime-mode-switch.v2",
        )
        self.assertEqual(
            self.task_packet_contract["authority"]["mode_switch"], "Fox"
        )
        self.assertFalse(
            self.task_packet_contract["authority"]["runtime_may_apply_mode_switch"]
        )
        self.assertFalse(
            self.task_packet_contract["context_policy"][
                "historical_items_loaded_by_default"
            ]
        )
        self.assertEqual(
            self.task_packet_schema["properties"]["schema_version"]["const"],
            "task-packet.v2",
        )
        self.assertEqual(
            self.runtime_mode_switch_schema["properties"]["schema_version"]["const"],
            "runtime-mode-switch.v2",
        )
        self.assertEqual(
            self.runtime_mode_switch_schema["properties"]["base_state_version"]["type"],
            "integer",
        )
        self.assertEqual(
            self.runtime_run_schema["properties"]["schema_version"]["const"],
            "runtime-run.v1",
        )
        authority = self.agent_run_contract["authority"]
        self.assertTrue(authority["work_mode_copied_from_task_packet"])
        self.assertFalse(authority["caller_may_override_work_mode"])
        self.assertFalse(authority["agent_may_write_audit_record"])

    def test_source_import_is_versioned_and_cannot_approve_business_state(self) -> None:
        command = next(
            command for command in self.contract["commands"] if command["name"] == "import_source_batch"
        )
        self.assertEqual(command["request_schema"], "source-import.v1")
        self.assertEqual(command["allowed_actor_kinds"], ["HUMAN", "SYSTEM"])
        self.assertFalse(command["changes_current_state"])
        self.assertFalse(
            self.source_import_contract["authority"]["imports_decisions_as_approved"]
        )
        self.assertTrue(
            self.source_import_contract["reconciliation"][
                "same_import_digest_adds_no_batch_event_or_source_object"
            ]
        )

    def test_meeting_ingest_only_creates_working_layer_candidates(self) -> None:
        command = next(
            command for command in self.contract["commands"]
            if command["name"] == "ingest_meeting_batch"
        )
        self.assertEqual(command["request_schema"], "meeting-ingest.v1")
        self.assertFalse(command["changes_current_state"])
        self.assertFalse(
            self.meeting_ingest_contract["authority"]["imports_decisions_as_approved"]
        )
        self.assertEqual(
            set(self.meeting_ingest_contract["forbidden_output_types"]),
            {"DECISION", "CONSTRAINT", "ACTION", "DEADLINE"},
        )


if __name__ == "__main__":
    unittest.main()
