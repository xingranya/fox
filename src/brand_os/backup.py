"""为本地权威状态目录创建并校验可恢复快照。"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .hashing import sha256_file
from .workspace import WorkspaceLayout


BACKUP_ID_PATTERN = re.compile(r"^[0-9TZ-]+-[a-f0-9]{12}$")


class BackupError(RuntimeError):
    """表示备份不完整、已损坏或无法安全恢复。"""


class StateBackupService:
    """备份 `.fox/state`；运行态和派生数据不进入备份。"""

    def __init__(self, layout: WorkspaceLayout) -> None:
        self.layout = layout

    def create(self) -> str:
        """将当前状态目录复制到原子提交的版本化备份。"""

        backup_id = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:12]}"
        destination = self.layout.backups / backup_id
        temporary = Path(tempfile.mkdtemp(prefix="backup-", dir=self.layout.backups))
        data_dir = temporary / "state"
        data_dir.mkdir(mode=0o700)
        files: list[dict[str, object]] = []
        try:
            for source in sorted(self.layout.state.rglob("*")):
                if source.is_symlink():
                    raise BackupError(f"状态目录不能包含符号链接：{source}")
                if not source.is_file():
                    continue
                relative = source.relative_to(self.layout.state)
                target = data_dir / relative
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                before = source.stat()
                shutil.copyfile(source, target)
                after = source.stat()
                if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                    after.st_dev,
                    after.st_ino,
                    after.st_size,
                    after.st_mtime_ns,
                ):
                    raise BackupError(f"状态文件在备份期间发生变化：{relative}")
                digest, size = sha256_file(target)
                files.append({"path": relative.as_posix(), "sha256": digest, "size": size})
            manifest = {
                "schema_version": "state-backup.v1",
                "backup_id": backup_id,
                "created_at": datetime.now(UTC).isoformat(),
                "files": files,
            }
            manifest_path = temporary / "manifest.json"
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, destination)
            return backup_id
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise

    def restore(self, backup_id: str, destination: Path) -> Path:
        """校验备份后恢复到一个尚不存在的新目录。"""

        if not BACKUP_ID_PATTERN.fullmatch(backup_id):
            raise BackupError("备份 ID 格式无效")
        source = self.layout.backups / backup_id
        manifest_path = source / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BackupError("备份清单缺失或损坏") from exc
        if manifest.get("schema_version") != "state-backup.v1" or manifest.get("backup_id") != backup_id:
            raise BackupError("备份清单版本或 ID 不匹配")
        destination = destination.expanduser().resolve(strict=False)
        if destination.exists():
            raise BackupError("恢复目标必须尚不存在")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix="restore-", dir=destination.parent))
        try:
            for item in manifest.get("files", []):
                relative = Path(item["path"])
                if relative.is_absolute() or ".." in relative.parts:
                    raise BackupError("备份清单包含越界路径")
                backup_file = source / "state" / relative
                digest, size = sha256_file(backup_file)
                if digest != item.get("sha256") or size != item.get("size"):
                    raise BackupError(f"备份文件校验失败：{relative}")
                target = temporary / relative
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                shutil.copyfile(backup_file, target)
            os.replace(temporary, destination)
            return destination
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
