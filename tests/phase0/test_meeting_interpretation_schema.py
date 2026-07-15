"""会议解释 Schema 与高风险分类不变量测试。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path


SCHEMA_PATH = Path(__file__).parents[2] / "schemas" / "phase0" / "meeting-interpretation.schema.json"


class MeetingInterpretationSchemaTest(unittest.TestCase):
    """验证枚举、人工确认门和条件必填字段。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        cls.defs = cls.schema["$defs"]

    def test_meeting_modes_are_frozen(self) -> None:
        self.assertEqual(
            self.defs["meetingMode"]["enum"],
            ["EXPLORATION", "EVALUATION", "DECISION", "SYNC", "MIXED", "UNKNOWN"],
        )

    def test_working_types_exclude_approved_state_types(self) -> None:
        information_types = set(self.defs["informationType"]["enum"])
        self.assertNotIn("DECISION", information_types)
        self.assertNotIn("CONSTRAINT", information_types)
        self.assertNotIn("ACTION", information_types)
        self.assertIn("DECISION_CANDIDATE", information_types)

    def test_every_item_requires_human_confirmation(self) -> None:
        item = self.defs["interpretationItem"]
        self.assertIn("requires_human_confirmation", item["required"])
        self.assertTrue(item["properties"]["requires_human_confirmation"]["const"])

    def test_target_date_requires_date_kind(self) -> None:
        rules = self.defs["interpretationItem"]["allOf"]
        target_date_rule = next(
            rule for rule in rules if rule["if"]["properties"]["type"].get("const") == "TARGET_DATE"
        )
        self.assertIn("date_kind", target_date_rule["then"]["required"])

    def test_decision_candidate_requires_actor_and_verb(self) -> None:
        rules = self.defs["interpretationItem"]["allOf"]
        decision_rule = next(
            rule
            for rule in rules
            if rule["if"]["properties"]["type"].get("const") == "DECISION_CANDIDATE"
        )
        self.assertEqual(decision_rule["then"]["required"], ["decision_actor", "decision_verb"])


if __name__ == "__main__":
    unittest.main()
