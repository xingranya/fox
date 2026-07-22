"""本地状态目录备份与恢复烟测。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / "src"))

from brand_os.backup import BackupError, StateBackupService
from brand_os.config import WorkspaceSettings
from brand_os.workspace import initialize_workspace


class WorkspaceBackupTest(unittest.TestCase):
    """验证只备份权威状态，并在恢复前校验完整性。"""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        settings = WorkspaceSettings(self.base / "workspace", (self.base / "sources",))
        self.layout = initialize_workspace(settings)
        self.service = StateBackupService(self.layout)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_backup_restores_nested_state_and_excludes_runtime(self) -> None:
        state_file = self.layout.state / "projects" / "hongri.json"
        state_file.parent.mkdir()
        state_file.write_text('{"version": 1}\n', encoding="utf-8")
        (self.layout.runtime / "session.tmp").write_text("可删除", encoding="utf-8")

        backup_id = self.service.create()
        restored = self.service.restore(backup_id, self.base / "restored")

        self.assertEqual((restored / "projects" / "hongri.json").read_text(encoding="utf-8"), state_file.read_text(encoding="utf-8"))
        self.assertFalse((restored / "session.tmp").exists())
        manifest = json.loads((self.layout.backups / backup_id / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema_version"], "state-backup.v1")

    def test_restore_rejects_tampered_backup(self) -> None:
        state_file = self.layout.state / "state.json"
        state_file.write_text("{}", encoding="utf-8")
        backup_id = self.service.create()
        backup_file = self.layout.backups / backup_id / "state" / "state.json"
        backup_file.write_text("损坏", encoding="utf-8")
        with self.assertRaises(BackupError):
            self.service.restore(backup_id, self.base / "restored")

    def test_backup_rejects_state_symlink(self) -> None:
        outside = self.base / "outside.json"
        outside.write_text("{}", encoding="utf-8")
        (self.layout.state / "link.json").symlink_to(outside)
        with self.assertRaises(BackupError):
            self.service.create()


if __name__ == "__main__":
    unittest.main()
