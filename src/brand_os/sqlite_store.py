"""组装 SQLite 权威存储公开适配器。"""

from __future__ import annotations

from .sqlite_base import (
    MIN_SQLITE_VERSION,
    BusinessPermissionDenied,
    CanonicalStoreError,
    ProjectNotFound,
    ResourceConflict,
    VersionConflict,
)
from .sqlite_commands import SQLiteCommandMixin
from .sqlite_evidence_queries import SQLiteEvidenceQueryMixin
from .sqlite_imports import SQLiteImportMixin
from .sqlite_meetings import SQLiteMeetingMixin
from .sqlite_proposals import SQLiteProposalMixin
from .sqlite_queries import SQLiteQueryMixin


class SQLiteCanonicalStore(
    SQLiteEvidenceQueryMixin,
    SQLiteProposalMixin,
    SQLiteMeetingMixin,
    SQLiteImportMixin,
    SQLiteCommandMixin,
    SQLiteQueryMixin,
):
    """本地单用户 SQLite 适配器。"""


__all__ = [
    "MIN_SQLITE_VERSION",
    "BusinessPermissionDenied",
    "CanonicalStoreError",
    "ProjectNotFound",
    "ResourceConflict",
    "SQLiteCanonicalStore",
    "VersionConflict",
]
