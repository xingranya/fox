"""SQLite 来源导入、版本、旧 ID、缺口与去重测试。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from brand_os.domain import Actor, ActorKind, CommandContext
from brand_os.manifest_import import load_source_manifest
from brand_os.sqlite_store import BusinessPermissionDenied, ResourceConflict, SQLiteCanonicalStore


class SQLiteSourceImportTest(unittest.TestCase):
    """验证导入不会覆盖旧版本，也不会改变人工确认状态。"""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = SQLiteCanonicalStore(self.root / "project.db")
        self.system = Actor(ActorKind.SYSTEM, "source-importer")
        self.ai = Actor(ActorKind.AI, "codex")
        self.store.create_project(self.context(self.system, "project", 0), "鸿日")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def context(self, actor: Actor, key: str, version: int | None = None) -> CommandContext:
        return CommandContext(
            "hongri",
            actor,
            key,
            self.store.get_project_version("hongri") if version is None else version,
        )

    def batch(self, name: str, records: list[dict], gaps: list[dict] | None = None):
        path = self.root / name
        path.write_text(
            json.dumps(
                {
                    "schema_version": "source-import.v1",
                    "snapshot_at": "2026-07-22",
                    "records": records,
                    "gaps": gaps or [],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return load_source_manifest(path, origin_ref=name)

    def record(self, source_id: str, content: bytes, **overrides) -> dict:
        value = {
            "logical_source_id": source_id,
            "sha256": hashlib.sha256(content).hexdigest(),
            "relative_path": f"资料/{source_id}.md",
            "source_role": "working_source",
            "confidentiality": "P2",
            "size_bytes": len(content),
            "media_type": "text/markdown",
            "status": "current",
        }
        value.update(overrides)
        return value

    def test_same_manifest_retry_adds_nothing(self) -> None:
        batch = self.batch("same.json", [self.record("SRC-1", b"v1")])
        context = self.context(self.system, "import-same")
        first = self.store.import_source_batch(context, batch)
        before = self.table_counts()
        second = self.store.import_source_batch(context, batch)
        self.assertFalse(first.replayed)
        self.assertTrue(second.replayed)
        self.assertEqual(first.event_id, second.event_id)
        self.assertEqual(before, self.table_counts())
        self.assertEqual(self.store.get_project_version("hongri"), 2)

    def test_same_import_with_new_key_reuses_batch_without_event_or_version(self) -> None:
        batch = self.batch("dedupe.json", [self.record("SRC-1", b"v1")])
        first = self.store.import_source_batch(self.context(self.system, "import-1"), batch)
        before_events = len(self.store.list_events("hongri"))
        second = self.store.import_source_batch(self.context(self.system, "import-2"), batch)
        self.assertTrue(second.replayed)
        self.assertEqual(second.event_id, first.event_id)
        self.assertEqual(len(self.store.list_events("hongri")), before_events)
        self.assertEqual(self.store.get_project_version("hongri"), first.project_version)

    def test_hash_change_creates_new_version_and_supersession(self) -> None:
        first_record = self.record("SRC-1", b"v1", version_label="v1")
        second_record = self.record(
            "SRC-1",
            b"v2",
            version_label="v2",
            supersedes_sha256=[first_record["sha256"]],
        )
        self.store.import_source_batch(
            self.context(self.system, "import-v1"), self.batch("v1.json", [first_record])
        )
        result = self.store.import_source_batch(
            self.context(self.system, "import-v2"), self.batch("v2.json", [second_record])
        )
        versions = self.store.list_source_versions("hongri", "SRC-1")
        self.assertEqual(len(versions), 2)
        self.assertEqual([row["is_current"] for row in versions], [0, 1])
        self.assertEqual(versions[1]["sha256"], second_record["sha256"])
        report = self.store.get_source_import_report("hongri", result.resource_id)
        self.assertEqual(report["batch"]["new_version_count"], 1)
        self.assertEqual(report["batch"]["new_supersession_count"], 1)

    def test_content_is_deduplicated_across_two_logical_sources(self) -> None:
        records = [self.record("SRC-1", b"same"), self.record("SRC-2", b"same")]
        result = self.store.import_source_batch(
            self.context(self.system, "content-dedupe"), self.batch("content.json", records)
        )
        report = self.store.get_source_import_report("hongri", result.resource_id)
        self.assertEqual(report["inventory"]["logical_source_count"], 2)
        self.assertEqual(report["inventory"]["content_count"], 1)
        self.assertEqual(report["inventory"]["source_version_count"], 2)

    def test_legacy_and_reserved_ids_are_kept_and_can_change_status(self) -> None:
        aliases = [
            {"alias_id": "V5-OLD-001", "alias_kind": "legacy_id", "status": "active"},
            {"alias_id": "V5-VOID-002", "alias_kind": "reserved_id", "status": "reserved"},
        ]
        self.store.import_source_batch(
            self.context(self.system, "alias-v1"),
            self.batch("alias-v1.json", [self.record("SRC-1", b"v1", aliases=aliases)]),
        )
        aliases[0]["status"] = "deprecated"
        result = self.store.import_source_batch(
            self.context(self.system, "alias-v2"),
            self.batch("alias-v2.json", [self.record("SRC-1", b"v2", aliases=aliases)]),
        )
        stored = {row["alias_id"]: row for row in self.store.list_source_aliases("hongri")}
        self.assertEqual(stored["V5-OLD-001"]["status"], "deprecated")
        self.assertEqual(stored["V5-VOID-002"]["status"], "reserved")
        report = self.store.get_source_import_report("hongri", result.resource_id)
        self.assertEqual(report["batch"]["updated_alias_count"], 1)

    def test_gap_is_recorded_without_fabricating_source(self) -> None:
        gap = {
            "gap_id": "GAP-V5",
            "status": "KNOWN_SOURCE_GAP",
            "description": "V5 原件尚未取得。",
            "scope": "v5",
            "evidence_ref": "manual:GAP-V5",
        }
        result = self.store.import_source_batch(
            self.context(self.system, "gap"), self.batch("gap.json", [], [gap])
        )
        report = self.store.get_source_import_report("hongri", result.resource_id)
        self.assertEqual(report["inventory"]["logical_source_count"], 0)
        self.assertEqual(report["gaps"][0]["gap_id"], "GAP-V5")

    def test_alias_collision_rolls_back_whole_batch(self) -> None:
        alias = [{"alias_id": "OLD-1", "alias_kind": "legacy_id", "status": "active"}]
        self.store.import_source_batch(
            self.context(self.system, "alias-owner"),
            self.batch("owner.json", [self.record("SRC-1", b"v1", aliases=alias)]),
        )
        before = self.table_counts()
        with self.assertRaises(ResourceConflict):
            self.store.import_source_batch(
                self.context(self.system, "alias-conflict"),
                self.batch("conflict.json", [self.record("SRC-2", b"v2", aliases=alias)]),
            )
        self.assertEqual(before, self.table_counts())

    def test_missing_superseded_version_rolls_back(self) -> None:
        record = self.record(
            "SRC-1",
            b"v2",
            supersedes_sha256=[hashlib.sha256(b"missing").hexdigest()],
        )
        before = self.table_counts()
        with self.assertRaises(ResourceConflict):
            self.store.import_source_batch(
                self.context(self.system, "missing-predecessor"),
                self.batch("missing.json", [record]),
            )
        self.assertEqual(before, self.table_counts())

    def test_ai_cannot_import_sources(self) -> None:
        batch = self.batch("ai.json", [self.record("SRC-1", b"v1")])
        with self.assertRaises(BusinessPermissionDenied):
            self.store.import_source_batch(self.context(self.ai, "ai-import"), batch)

    def table_counts(self) -> dict[str, int]:
        tables = (
            "commands",
            "events",
            "source_import_batches",
            "source_contents",
            "logical_sources",
            "source_versions",
            "source_aliases",
            "source_version_relations",
            "source_gaps",
        )
        with sqlite3.connect(self.store.database_path) as connection:
            return {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in tables
            }


if __name__ == "__main__":
    unittest.main()
