"""SQLite 在线备份和恢复测试。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path


from brand_os.backup import BackupError
from brand_os.config import WorkspaceSettings
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
from brand_os.manifest_import import load_source_manifest
from brand_os.meeting_ingest import parse_meeting_ingest
from brand_os.sqlite_backup import SQLiteBackupService
from brand_os.sqlite_migrations import MIGRATIONS, apply_migrations
from brand_os.sqlite_store import SQLiteCanonicalStore
from brand_os.workspace import initialize_workspace


class SQLiteBackupTest(unittest.TestCase):
    """验证在线快照、清单对账和恢复后的事件一致。"""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        settings = WorkspaceSettings(self.base / "workspace", (self.base / "sources",))
        self.layout = initialize_workspace(settings)
        self.database = self.layout.state / "project.db"
        self.store = SQLiteCanonicalStore(self.database)
        self.fox = Actor(ActorKind.HUMAN, "Fox")
        self.ai = Actor(ActorKind.AI, "codex")
        self.store.create_project(CommandContext("hongri", self.fox, "create", 0), "鸿日")
        self.store.create_proposal(
            CommandContext("hongri", self.ai, "proposal", 1),
            ProposalDraft(
                "proposal-1",
                "create",
                "OPEN",
                "question-1",
                None,
                {"id": "question-1", "question": "主推版本是什么"},
                "会议尚未确认",
                "本轮内容",
                ("evidence:meeting-1#20",),
            ),
        )
        self.backups = SQLiteBackupService(self.layout, self.database)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_online_backup_restores_schema_events_and_versions(self) -> None:
        self.store.review_proposal(
            CommandContext("hongri", self.fox, "review", 2),
            ProposalReview("proposal-1", ReviewAction.APPROVE, "确认开放问题"),
        )
        backup_id = self.backups.create()
        restored_path = self.backups.restore(backup_id, self.base / "restored" / "project.db")
        restored = SQLiteCanonicalStore(restored_path)
        self.assertEqual(restored.schema_version, self.store.schema_version)
        self.assertEqual(restored.get_project_version("hongri"), self.store.get_project_version("hongri"))
        self.assertEqual(restored.list_events("hongri"), self.store.list_events("hongri"))
        self.assertEqual(restored.get_current_state("hongri"), self.store.get_current_state("hongri"))
        self.assertEqual(restored.list_human_actions("hongri"), self.store.list_human_actions("hongri"))
        self.assertTrue(restored.quick_check())

    def test_online_backup_reconciles_imported_source_versions(self) -> None:
        manifest_path = self.base / "source-import.json"
        manifest_path.write_text(
            """{
              "schema_version": "source-import.v1",
              "records": [{
                "logical_source_id": "SRC-1",
                "sha256": "3bfc269594ef649228e9a74bab00f042efc91d5acc6fbee31a382e80d42388fe",
                "relative_path": "资料/总控.md",
                "source_role": "project_control"
              }],
              "gaps": [{
                "gap_id": "GAP-1",
                "status": "KNOWN_SOURCE_GAP",
                "description": "未取得全量目录。",
                "scope": "source_root",
                "evidence_ref": "manifest:gap"
              }]
            }""",
            encoding="utf-8",
        )
        result = self.store.import_source_batch(
            CommandContext("hongri", self.fox, "source-import", 2),
            load_source_manifest(manifest_path),
        )
        backup_id = self.backups.create()
        restored_path = self.backups.restore(backup_id, self.base / "source-restored.db")
        restored = SQLiteCanonicalStore(restored_path)
        self.assertEqual(
            restored.get_source_import_report("hongri", result.resource_id),
            self.store.get_source_import_report("hongri", result.resource_id),
        )

    def test_online_backup_reconciles_meeting_working_layer(self) -> None:
        digest = hashlib.sha256(b"meeting").hexdigest()
        source_id = "meeting-source"
        self.store.register_source(
            CommandContext("hongri", self.fox, "meeting-source", 2),
            SourceRecord(
                source_id,
                digest,
                7,
                "meetings/meeting.md",
                "meeting_minutes",
                "P2",
            ),
        )
        batch = parse_meeting_ingest(
            {
                "schema_version": "meeting-ingest.v1",
                "source_is_data": True,
                "base_state_version": 3,
                "meeting": {
                    "meeting_id": "meeting-1",
                    "title": "测试会议",
                    "occurred_at": "2026-07-22T10:00:00+08:00",
                    "participants": ["Fox"],
                    "mode": "SYNC",
                    "mode_confidence": 0.9,
                    "source": {
                        "logical_source_id": source_id,
                        "source_version_id": legacy_source_version_id(source_id, digest),
                        "sha256": digest,
                        "verification": "verified",
                    },
                },
                "segments": [
                    {
                        "segment_id": "segment-1",
                        "locator": "00:00:10-00:00:15",
                        "quote": "下周最好先看一版。",
                        "speaker": "Fox",
                        "spoken_at": "00:00:10",
                        "start_ms": 10000,
                        "end_ms": 15000,
                        "context": "同步看版安排",
                        "mode": "SYNC",
                        "mode_confidence": 0.9,
                    }
                ],
                "items": [
                    {
                        "item_id": "item-1",
                        "type": "TARGET_DATE",
                        "summary": "希望下周先看一版",
                        "scope": "内部看版",
                        "date_kind": "TENTATIVE_DATE",
                        "evidence_segment_ids": ["segment-1"],
                        "confidence": 0.9,
                        "reason": "最好表示暂定时间",
                        "requires_human_confirmation": True,
                    }
                ],
                "conflicts": [],
            }
        )
        result = self.store.ingest_meeting_batch(
            CommandContext("hongri", self.ai, "meeting", 3), batch
        )
        backup_id = self.backups.create()
        restored_path = self.backups.restore(backup_id, self.base / "meeting-restored.db")
        restored = SQLiteCanonicalStore(restored_path)
        self.assertEqual(
            restored.get_meeting_ingest_report("hongri", result.resource_id),
            self.store.get_meeting_ingest_report("hongri", result.resource_id),
        )

    def test_tampered_backup_is_rejected_before_restore(self) -> None:
        backup_id = self.backups.create()
        backup_database = self.layout.backups / backup_id / "project.db"
        with backup_database.open("ab") as stream:
            stream.write(b"tampered")
        with self.assertRaises(BackupError):
            self.backups.restore(backup_id, self.base / "restored.db")

    def test_legacy_v1_manifest_remains_restorable(self) -> None:
        backup_id = self.backups.create()
        manifest_path = self.layout.backups / backup_id / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["schema_version"] = "sqlite-backup.v1"
        for key in (
            "source_import_batch_count",
            "logical_source_count",
            "source_version_count",
            "source_gap_count",
            "source_digest",
            "meeting_ingest_batch_count",
            "meeting_count",
            "meeting_segment_count",
            "meeting_item_count",
            "meeting_conflict_count",
            "meeting_digest",
        ):
            manifest.pop(key)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        restored_path = self.backups.restore(backup_id, self.base / "legacy-restored.db")
        self.assertTrue(SQLiteCanonicalStore(restored_path).quick_check())

    def test_legacy_v2_manifest_remains_restorable(self) -> None:
        backup_id = self.backups.create()
        manifest_path = self.layout.backups / backup_id / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["schema_version"] = "sqlite-backup.v2"
        for key in (
            "meeting_ingest_batch_count",
            "meeting_count",
            "meeting_segment_count",
            "meeting_item_count",
            "meeting_conflict_count",
            "meeting_digest",
        ):
            manifest.pop(key)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        restored_path = self.backups.restore(backup_id, self.base / "v2-restored.db")
        self.assertTrue(SQLiteCanonicalStore(restored_path).quick_check())

    def test_actual_old_schema_databases_do_not_require_newer_tables(self) -> None:
        cases = (
            (2, "sqlite-backup.v1"),
            (3, "sqlite-backup.v2"),
        )
        source_fields = (
            "source_import_batch_count",
            "logical_source_count",
            "source_version_count",
            "source_gap_count",
            "source_digest",
        )
        meeting_fields = (
            "meeting_ingest_batch_count",
            "meeting_count",
            "meeting_segment_count",
            "meeting_item_count",
            "meeting_conflict_count",
            "meeting_digest",
        )
        for schema_version, manifest_version in cases:
            with self.subTest(schema_version=schema_version, manifest_version=manifest_version):
                database = self.base / f"schema-{schema_version}.db"
                with sqlite3.connect(database, isolation_level=None) as connection:
                    apply_migrations(connection, MIGRATIONS[:schema_version])
                backups = SQLiteBackupService(self.layout, database)
                backup_id = backups.create()
                manifest_path = self.layout.backups / backup_id / "manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest["schema_version"] = manifest_version
                fields = meeting_fields if schema_version == 3 else (*source_fields, *meeting_fields)
                for key in fields:
                    manifest.pop(key)
                manifest_path.write_text(
                    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                restored = backups.restore(
                    backup_id, self.base / f"schema-{schema_version}-restored.db"
                )
                with sqlite3.connect(restored) as connection:
                    restored_version = connection.execute(
                        "SELECT MAX(version) FROM schema_migrations"
                    ).fetchone()[0]
                self.assertEqual(restored_version, schema_version)


if __name__ == "__main__":
    unittest.main()
