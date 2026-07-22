"""本地权威存储机器契约测试。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[2]
CONTRACT_PATH = ROOT / "contracts" / "phase1" / "canonical-store.json"
SOURCE_IMPORT_CONTRACT_PATH = ROOT / "contracts" / "phase1" / "source-import.json"


class CanonicalStoreContractTest(unittest.TestCase):
    """验证 AI 与人工写权限始终分路。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        cls.source_import_contract = json.loads(
            SOURCE_IMPORT_CONTRACT_PATH.read_text(encoding="utf-8")
        )

    def test_contract_is_replaceable_and_versioned(self) -> None:
        self.assertEqual(self.contract["schema_version"], "canonical-store-port.v2")
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
        self.assertTrue({"approve", "modify_and_approve", "reject", "direct_sql"}.issubset(forbidden))

    def test_every_write_requires_idempotency_and_expected_version(self) -> None:
        requirements = self.contract["write_requirements"]
        self.assertTrue(requirements["idempotency_key"])
        self.assertTrue(requirements["expected_version"])
        self.assertTrue(requirements["single_transaction_event_and_projection"])

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


if __name__ == "__main__":
    unittest.main()
