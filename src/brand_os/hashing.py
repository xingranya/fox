"""提供流式 SHA-256 计算。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import BinaryIO


def sha256_stream(stream: BinaryIO, chunk_size: int = 1024 * 1024) -> tuple[str, int]:
    """计算已打开二进制流的 SHA-256 与字节数。"""

    digest = hashlib.sha256()
    size = 0
    while chunk := stream.read(chunk_size):
        digest.update(chunk)
        size += len(chunk)
    return digest.hexdigest(), size


def copy_and_sha256(source: BinaryIO, target: BinaryIO, chunk_size: int = 1024 * 1024) -> tuple[str, int]:
    """单次流式复制文件，并同步计算 SHA-256 与字节数。"""

    digest = hashlib.sha256()
    size = 0
    while chunk := source.read(chunk_size):
        target.write(chunk)
        digest.update(chunk)
        size += len(chunk)
    return digest.hexdigest(), size


def sha256_file(path: Path) -> tuple[str, int]:
    """只读打开文件并计算 SHA-256 与字节数。"""

    with path.open("rb") as stream:
        return sha256_stream(stream)
