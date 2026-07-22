"""F1.10 验收副本、真实库只读和并发冲突探针测试。"""

from __future__ import annotations

import hashlib
import importlib.util
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

from brand_os.desktop_service import DesktopProjectService
from brand_os.domain import (
    Actor,
    ActorKind,
    CommandContext,
    SourceGapRecord,
    SourceImportBatch,
    SourceImportRecord,
)
from brand_os.sqlite_store import SQLiteCanonicalStore


MODULE_PATH = (
    Path(__file__).parents[2]
    / "scripts"
    / "phase1"
    / "prepare_f1_10_acceptance.py"
)
SPEC = importlib.util.spec_from_file_location("prepare_f1_10_acceptance", MODULE_PATH)
assert SPEC and SPEC.loader
acceptance = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = acceptance
SPEC.loader.exec_module(acceptance)


class F110AcceptanceFixtureTest(unittest.TestCase):
    """验证验收数据只写副本，并保留真实基线计数。"""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source.db"
        self.destination = self.root / "acceptance.db"
        store = SQLiteCanonicalStore(self.source)
        system = Actor(ActorKind.SYSTEM, "fixture-source")
        store.create_project(CommandContext("hongri", system, "project", 0), "鸿日")
        records = tuple(
            SourceImportRecord(
                logical_source_id=f"SRC-{index:02d}",
                sha256=hashlib.sha256(f"source-{index}".encode()).hexdigest(),
                relative_path=f"资料/source-{index}.md",
                source_role="decision_log" if index == 0 else "working_source",
                status="observed",
            )
            for index in range(9)
        )
        gaps = tuple(
            SourceGapRecord(
                gap_id=f"GAP-{index:02d}",
                status="KNOWN_SOURCE_GAP",
                description=f"验收缺口 {index}",
                scope="fixture",
                evidence_ref=f"manual:GAP-{index:02d}",
            )
            for index in range(5)
        )
        store.import_source_batch(
            CommandContext("hongri", system, "sources", 1),
            SourceImportBatch(
                manifest_sha256="a" * 64,
                import_digest="b" * 64,
                manifest_schema_version="source-import.v1",
                origin_ref="fixture-manifest",
                records=records,
                gaps=gaps,
                snapshot_at="2026-07-22T00:00:00+00:00",
            ),
        )
        with sqlite3.connect(self.source) as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_prepare_keeps_source_unchanged_and_marks_all_acceptance_data(self) -> None:
        source_hash = acceptance.file_sha256(self.source)

        result = acceptance.prepare_acceptance_database(
            self.source, self.destination
        )

        self.assertEqual(result["source_sha256_before"], source_hash)
        self.assertEqual(result["source_sha256_after"], source_hash)
        self.assertTrue(result["source_unchanged"])
        self.assertFalse(result["agent_can_approve"])
        view = DesktopProjectService(
            SQLiteCanonicalStore(self.destination), "hongri"
        ).get_project_view()
        self.assertEqual(view["summary"]["current_source_count"], 9)
        self.assertEqual(view["summary"]["known_gap_count"], 5)
        self.assertEqual(view["summary"]["pending_proposal_count"], 4)
        self.assertEqual(view["summary"]["runtime_task_count"], 1)
        self.assertEqual(view["summary"]["task_packet_count"], 1)
        self.assertEqual(view["current_state"], [])
        for proposal in view["proposals"]:
            self.assertEqual(
                proposal["after"]["fixture_scope"], "F1.10_DESKTOP_E2E"
            )

    def test_advance_uses_agent_proposal_path_without_changing_formal_state(self) -> None:
        acceptance.prepare_acceptance_database(self.source, self.destination)
        previous = SQLiteCanonicalStore(self.destination).get_project_version("hongri")

        result = acceptance.advance_acceptance_version(self.destination)

        self.assertEqual(result["previous_version"], previous)
        self.assertEqual(result["current_version"], previous + 1)
        self.assertEqual(result["proposal"]["status"], "proposed")
        self.assertEqual(result["current_state_count"], 0)
        self.assertFalse(result["agent_can_approve"])

    def test_prepare_refuses_to_overwrite_source_database(self) -> None:
        with self.assertRaisesRegex(acceptance.AcceptanceFixtureError, "不能覆盖"):
            acceptance.prepare_acceptance_database(self.source, self.source)


if __name__ == "__main__":
    unittest.main()
