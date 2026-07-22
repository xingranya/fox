"""本地权威存储机器契约测试。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[2]
CONTRACT_PATH = ROOT / "contracts" / "phase1" / "canonical-store.json"


class CanonicalStoreContractTest(unittest.TestCase):
    """验证 AI 与人工写权限始终分路。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))

    def test_contract_is_replaceable_and_versioned(self) -> None:
        self.assertEqual(self.contract["schema_version"], "canonical-store-port.v1")
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


if __name__ == "__main__":
    unittest.main()
