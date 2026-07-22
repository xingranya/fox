"""SQLite 在线备份和恢复测试。"""

from __future__ import annotations

import json
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
)
from brand_os.manifest_import import load_source_manifest
from brand_os.sqlite_backup import SQLiteBackupService
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
        ):
            manifest.pop(key)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        restored_path = self.backups.restore(backup_id, self.base / "legacy-restored.db")
        self.assertTrue(SQLiteCanonicalStore(restored_path).quick_check())


if __name__ == "__main__":
    unittest.main()
