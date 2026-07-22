"""本地只读证据快照与路径边界测试。"""

from __future__ import annotations

import hashlib
import stat
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / "src"))

from brand_os.config import WorkspaceSettings
from brand_os.evidence import EvidenceIntegrityError, LocalEvidenceStore, SourceBoundaryError
from brand_os.workspace import initialize_workspace


class EvidenceStoreTest(unittest.TestCase):
    """验证原件不被改写、快照只读且不能越界。"""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.workspace = self.base / "workspace"
        self.sources = self.base / "sources"
        self.sources.mkdir()
        settings = WorkspaceSettings(self.workspace, (self.sources,))
        self.layout = initialize_workspace(settings)
        self.store = LocalEvidenceStore(self.layout, settings.source_roots)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_snapshot_is_content_addressed_read_only_and_idempotent(self) -> None:
        source = self.sources / "brief.md"
        source.write_text("原始内容\n", encoding="utf-8")
        original_mode = stat.S_IMODE(source.stat().st_mode)

        first = self.store.snapshot(source)
        second = self.store.snapshot(source)

        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(first.sha256, second.sha256)
        self.assertEqual(first.path.read_bytes(), source.read_bytes())
        self.assertEqual(stat.S_IMODE(first.path.stat().st_mode), 0o400)
        self.assertEqual(stat.S_IMODE(source.stat().st_mode), original_mode)
        self.assertTrue(self.store.verify(first.sha256))

    def test_outside_source_root_is_rejected(self) -> None:
        outside = self.base / "outside.md"
        outside.write_text("越界", encoding="utf-8")
        with self.assertRaises(SourceBoundaryError):
            self.store.snapshot(outside)

    def test_symlink_source_is_rejected(self) -> None:
        source = self.sources / "brief.md"
        source.write_text("原件", encoding="utf-8")
        link = self.sources / "link.md"
        link.symlink_to(source)
        with self.assertRaises(SourceBoundaryError):
            self.store.snapshot(link)

    def test_control_data_cannot_be_reimported_as_source(self) -> None:
        permissive_store = LocalEvidenceStore(self.layout, (self.workspace,))
        controlled = self.layout.state / "state.json"
        controlled.write_text("{}", encoding="utf-8")
        with self.assertRaises(SourceBoundaryError):
            permissive_store.snapshot(controlled)

    def test_tampered_snapshot_is_detected(self) -> None:
        source = self.sources / "brief.md"
        source.write_text("原件", encoding="utf-8")
        snapshot = self.store.snapshot(source)
        snapshot.path.chmod(0o600)
        snapshot.path.write_text("已损坏", encoding="utf-8")
        with self.assertRaises(EvidenceIntegrityError):
            self.store.open(snapshot.sha256)

    def test_content_address_symlink_is_rejected(self) -> None:
        source = self.sources / "brief.md"
        content = b"original"
        source.write_bytes(content)
        outside = self.base / "outside.bin"
        outside.write_bytes(b"do not touch")
        digest = hashlib.sha256(content).hexdigest()
        (self.layout.evidence / digest).symlink_to(outside)

        with self.assertRaises(EvidenceIntegrityError):
            self.store.snapshot(source)
        self.assertEqual(outside.read_bytes(), b"do not touch")


if __name__ == "__main__":
    unittest.main()
