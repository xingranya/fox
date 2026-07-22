"""本地工作空间配置与目录分区测试。"""

from __future__ import annotations

import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / "src"))

from brand_os.config import ConfigurationError, load_workspace_settings
from brand_os.workspace import initialize_workspace


class WorkspaceConfigTest(unittest.TestCase):
    """验证路径注入优先级和本地私有目录。"""

    def test_explicit_root_overrides_environment_and_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config = base / "config.json"
            config.write_text(json.dumps({"workspace_root": str(base / "file")}), encoding="utf-8")
            settings = load_workspace_settings(
                explicit_root=base / "explicit",
                config_path=config,
                environ={"BRAND_OS_WORKSPACE": str(base / "environment")},
                home=base,
            )
            self.assertEqual(settings.workspace_root, (base / "explicit").resolve())

    def test_environment_root_overrides_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config = base / "config.json"
            config.write_text(json.dumps({"workspace_root": str(base / "file")}), encoding="utf-8")
            settings = load_workspace_settings(
                config_path=config,
                environ={"BRAND_OS_WORKSPACE": str(base / "environment")},
                home=base,
            )
            self.assertEqual(settings.workspace_root, (base / "environment").resolve())

    def test_relative_source_root_is_scoped_to_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            settings = load_workspace_settings(
                explicit_root=base / "workspace",
                explicit_source_roots=["materials"],
                config_path=base / "missing.json",
                environ={},
                home=base,
            )
            self.assertEqual(settings.source_roots, ((base / "workspace" / "materials").resolve(),))

    def test_config_rejects_secret_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config = base / "config.json"
            config.write_text(json.dumps({"workspace_root": "work", "api_key": "forbidden"}), encoding="utf-8")
            with self.assertRaises(ConfigurationError):
                load_workspace_settings(config_path=config, environ={}, home=base)

    def test_initialize_creates_private_partitioned_layout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            settings = load_workspace_settings(
                explicit_root=base / "workspace",
                config_path=base / "missing.json",
                environ={},
                home=base,
            )
            layout = initialize_workspace(settings)
            for path in (
                layout.control,
                layout.state,
                layout.evidence,
                layout.backups,
                layout.derived,
                layout.runtime,
            ):
                self.assertTrue(path.is_dir())
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)
            metadata = json.loads((layout.control / "workspace.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["schema_version"], "local-workspace.v1")
            self.assertNotIn("secret", json.dumps(metadata).lower())


if __name__ == "__main__":
    unittest.main()
