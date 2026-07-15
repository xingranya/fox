"""运行时工作模式切换 Schema 不变量测试。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path


SCHEMA_PATH = Path(__file__).parents[2] / "schemas" / "phase0" / "runtime-mode-switch.schema.json"


class RuntimeModeSwitchSchemaTest(unittest.TestCase):
    """验证 AI 无法成为工作模式切换事件的发起人。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    def test_modes_are_frozen(self) -> None:
        self.assertEqual(
            self.schema["$defs"]["workMode"]["enum"],
            ["EXPLORATION", "EVALUATION", "DECISION", "EXECUTION"],
        )

    def test_only_fox_human_can_initiate_switch(self) -> None:
        properties = self.schema["properties"]
        self.assertEqual(properties["initiated_by"]["const"], "Fox")
        self.assertEqual(properties["initiator_type"]["const"], "HUMAN")

    def test_switch_keeps_audit_fields(self) -> None:
        required = set(self.schema["required"])
        self.assertTrue({"reason", "task_scope", "base_state_version", "occurred_at"}.issubset(required))


if __name__ == "__main__":
    unittest.main()
