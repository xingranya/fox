"""SQLite 在线备份和恢复测试。"""

from __future__ import annotations

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

    def test_tampered_backup_is_rejected_before_restore(self) -> None:
        backup_id = self.backups.create()
        backup_database = self.layout.backups / backup_id / "project.db"
        with backup_database.open("ab") as stream:
            stream.write(b"tampered")
        with self.assertRaises(BackupError):
            self.backups.restore(backup_id, self.base / "restored.db")


if __name__ == "__main__":
    unittest.main()
