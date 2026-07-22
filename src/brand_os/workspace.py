"""创建本地工作空间的受控目录，不改写原件目录。"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .config import WorkspaceSettings


class WorkspaceError(RuntimeError):
    """表示工作空间目录不符合安全边界。"""


@dataclass(frozen=True, slots=True)
class WorkspaceLayout:
    """集中描述权威、派生与运行态目录。"""

    root: Path
    control: Path
    state: Path
    evidence: Path
    backups: Path
    derived: Path
    runtime: Path

    @classmethod
    def from_root(cls, root: Path) -> "WorkspaceLayout":
        """根据工作空间根目录生成固定分区。"""

        absolute_root = root.expanduser().resolve(strict=False)
        control = absolute_root / ".fox"
        return cls(
            root=absolute_root,
            control=control,
            state=control / "state",
            evidence=control / "evidence" / "sha256",
            backups=control / "backups",
            derived=control / "derived",
            runtime=control / "runtime",
        )


def _ensure_private_directory(path: Path) -> None:
    """创建仅当前用户可访问的目录，并拒绝符号链接。"""

    if path.is_symlink():
        raise WorkspaceError(f"受控目录不能是符号链接：{path}")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.is_dir():
        raise WorkspaceError(f"路径不是目录：{path}")
    path.chmod(0o700)


def _write_metadata(layout: WorkspaceLayout) -> None:
    """首次初始化时原子写入不含密钥的目录说明。"""

    metadata_path = layout.control / "workspace.json"
    if metadata_path.exists():
        if metadata_path.is_symlink() or not metadata_path.is_file():
            raise WorkspaceError("workspace.json 必须是受控普通文件")
        return
    content = {
        "schema_version": "local-workspace.v1",
        "authority_dir": "state",
        "evidence_dir": "evidence/sha256",
        "backup_dir": "backups",
        "derived_dir": "derived",
        "runtime_dir": "runtime",
    }
    fd, temporary_name = tempfile.mkstemp(prefix="workspace-", suffix=".tmp", dir=layout.control)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(content, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary_path.chmod(0o600)
        os.replace(temporary_path, metadata_path)
    finally:
        temporary_path.unlink(missing_ok=True)


def initialize_workspace(settings: WorkspaceSettings) -> WorkspaceLayout:
    """初始化 `.fox` 分区；原件只通过 source_roots 读取。"""

    if settings.workspace_root.is_symlink():
        raise WorkspaceError("工作空间根目录不能是符号链接")
    settings.workspace_root.mkdir(parents=True, exist_ok=True)
    layout = WorkspaceLayout.from_root(settings.workspace_root)
    for directory in (
        layout.control,
        layout.state,
        layout.evidence,
        layout.backups,
        layout.derived,
        layout.runtime,
    ):
        _ensure_private_directory(directory)
    _write_metadata(layout)
    return layout
