"""领域值对象的边界测试。"""

from __future__ import annotations

import hashlib
import unittest

from brand_os.domain import ProposalDraft, SourceRecord


class DomainValueTest(unittest.TestCase):
    """验证来源元数据不能携带逃逸路径。"""

    def test_source_relative_path_rejects_parent_escape(self) -> None:
        with self.assertRaises(ValueError):
            SourceRecord(
                "source-1",
                hashlib.sha256(b"source").hexdigest(),
                6,
                "../outside.md",
                "current_work",
                "P2",
            )

    def test_proposal_validity_requires_timezone_and_ordered_window(self) -> None:
        fields = {
            "proposal_id": "proposal-1",
            "proposal_kind": "create",
            "classification": "OPEN",
            "subject_id": "question-1",
            "before": None,
            "after": {"id": "question-1"},
            "reason": "等待确认",
            "impact_scope": "本轮",
            "evidence_refs": ("source-version:SV-1#line:1",),
        }
        with self.assertRaises(ValueError):
            ProposalDraft(**fields, valid_from="2026-07-22T10:00:00")
        with self.assertRaises(ValueError):
            ProposalDraft(
                **fields,
                valid_from="2026-07-23T10:00:00+08:00",
                valid_until="2026-07-22T10:00:00+08:00",
            )


if __name__ == "__main__":
    unittest.main()
