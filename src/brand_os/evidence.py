"""把允许范围内的原件复制为只读内容寻址快照。"""

from __future__ import annotations

import os
import re
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .hashing import copy_and_sha256, sha256_file
from .workspace import WorkspaceLayout


SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")


class SourceBoundaryError(PermissionError):
    """表示原件路径越过允许读取范围。"""


class EvidenceIntegrityError(RuntimeError):
    """表示证据快照与其哈希不一致。"""


@dataclass(frozen=True, slots=True)
class EvidenceSnapshot:
    """描述一个本地只读证据快照。"""

    sha256: str
    size: int
    path: Path
    created: bool


class LocalEvidenceStore:
    """仅从允许的原件根目录读取，并写入受控证据区。"""

    def __init__(self, layout: WorkspaceLayout, source_roots: tuple[Path, ...]) -> None:
        self.layout = layout
        self.source_roots = tuple(root.expanduser().resolve(strict=True) for root in source_roots)

    def resolve_source(self, source_path: Path) -> Path:
        """解析原件路径，拒绝符号链接、控制区和路径逃逸。"""

        if source_path.is_symlink():
            raise SourceBoundaryError(f"原件不能是符号链接：{source_path}")
        try:
            resolved = source_path.expanduser().resolve(strict=True)
        except OSError as exc:
            raise SourceBoundaryError(f"原件不存在或不可读：{source_path}") from exc
        if not resolved.is_file():
            raise SourceBoundaryError(f"原件不是普通文件：{resolved}")
        if resolved.is_relative_to(self.layout.control):
            raise SourceBoundaryError("不能把 .fox 受控数据重新当作原件导入")
        if not any(resolved.is_relative_to(root) for root in self.source_roots):
            raise SourceBoundaryError(f"原件不在允许的读取范围内：{resolved}")
        return resolved

    def snapshot(self, source_path: Path) -> EvidenceSnapshot:
        """只读复制原件，按 SHA-256 原子提交且不覆盖已有版本。"""

        source = self.resolve_source(source_path)
        before = source.stat()
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(source, flags)
        temporary_path: Path | None = None
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode):
                raise SourceBoundaryError("原件不是普通文件")
            if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                raise EvidenceIntegrityError("原件在打开前被替换，请重新执行")
            fd, temporary_name = tempfile.mkstemp(prefix="snapshot-", dir=self.layout.evidence)
            temporary_path = Path(temporary_name)
            with os.fdopen(descriptor, "rb", closefd=True) as source_stream, os.fdopen(fd, "wb") as target_stream:
                descriptor = -1
                digest, size = copy_and_sha256(source_stream, target_stream)
                target_stream.flush()
                os.fsync(target_stream.fileno())
            after = source.stat()
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            ):
                raise EvidenceIntegrityError("原件在快照期间发生变化，请重新执行")
            destination = self.layout.evidence / digest
            if destination.is_symlink():
                raise EvidenceIntegrityError("证据内容地址不能是符号链接")
            if destination.exists():
                existing_digest, existing_size = sha256_file(destination)
                if existing_digest != digest or existing_size != size:
                    raise EvidenceIntegrityError("已有证据快照损坏")
                return EvidenceSnapshot(digest, size, destination, False)
            temporary_path.chmod(0o400)
            os.replace(temporary_path, destination)
            temporary_path = None
            return EvidenceSnapshot(digest, size, destination, True)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def verify(self, digest: str) -> bool:
        """验证指定快照仍与内容地址一致。"""

        path = self._snapshot_path(digest)
        actual_digest, _ = sha256_file(path)
        return actual_digest == digest

    def open(self, digest: str):
        """只读打开已验证的证据快照。"""

        path = self._snapshot_path(digest)
        if not self.verify(digest):
            raise EvidenceIntegrityError("证据快照校验失败")
        return path.open("rb")

    def _snapshot_path(self, digest: str) -> Path:
        """只接受完整 SHA-256，避免证据路径逃逸。"""

        if not SHA256_PATTERN.fullmatch(digest):
            raise SourceBoundaryError("证据引用必须是完整 SHA-256")
        path = self.layout.evidence / digest
        if path.is_symlink() or not path.is_file():
            raise FileNotFoundError(path)
        return path
