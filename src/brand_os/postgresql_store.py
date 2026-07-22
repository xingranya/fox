"""PostgreSQL 权威事件、人工审批和当前投影适配器。"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from typing import Any

import psycopg

from .domain import CommandContext
from .postgresql_migrations import (
    POSTGRESQL_MIGRATIONS,
    apply_postgresql_migrations,
)
from .sqlite_base import SQLiteStoreBase
from .sqlite_commands import SQLiteCommandMixin
from .sqlite_evidence_queries import SQLiteEvidenceQueryMixin
from .sqlite_imports import SQLiteImportMixin
from .sqlite_meetings import SQLiteMeetingMixin
from .sqlite_proposals import SQLiteProposalMixin
from .sqlite_queries import SQLiteQueryMixin


class PostgreSQLRow(Mapping[str, object]):
    """同时支持字段名和数字下标，保持现有关系型领域实现的读取语义。"""

    def __init__(self, columns: tuple[str, ...], values: Sequence[object]) -> None:
        self._columns = columns
        self._values = tuple(values)
        self._indexes = {column: index for index, column in enumerate(columns)}

    def __getitem__(self, key: str | int) -> object:
        if isinstance(key, int):
            return self._values[key]
        return self._values[self._indexes[key]]

    def __iter__(self) -> Iterator[str]:
        return iter(self._columns)

    def __len__(self) -> int:
        return len(self._columns)


def _row_factory(cursor) -> Any:
    columns = tuple(column.name for column in (cursor.description or ()))
    return lambda values: PostgreSQLRow(columns, values)


class PostgreSQLConnection:
    """把共享关系型命令使用的参数与事务语法适配到 psycopg。"""

    _PROJECT_LOCK_QUERY = re.compile(
        r"^SELECT (?:VERSION|1) FROM PROJECTS WHERE PROJECT_ID = %S$"
    )

    def __init__(self, connection: psycopg.Connection) -> None:
        self._connection = connection
        self._write_transaction = False

    def execute(self, statement: str, parameters: Sequence[object] = ()):
        normalized = " ".join(statement.split()).upper()
        if normalized == "BEGIN IMMEDIATE":
            statement = "BEGIN"
            normalized = "BEGIN"
        if normalized == "BEGIN":
            cursor = self._connection.execute(statement)
            self._write_transaction = True
            return cursor
        if normalized in {"COMMIT", "ROLLBACK"}:
            try:
                return self._connection.execute(normalized)
            finally:
                self._write_transaction = False

        translated = self._translate(statement)
        translated_normalized = " ".join(translated.split()).upper()
        if (
            self._write_transaction
            and self._PROJECT_LOCK_QUERY.fullmatch(translated_normalized)
        ):
            translated = f"{translated.rstrip().rstrip(';')} FOR UPDATE"
        return self._connection.execute(translated, parameters)

    def executemany(self, statement: str, parameters):
        return self._connection.cursor().executemany(
            self._translate(statement), parameters
        )

    @staticmethod
    def _translate(statement: str) -> str:
        return statement.replace("?", "%s")

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> PostgreSQLConnection:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        try:
            if self._write_transaction:
                self._connection.execute("ROLLBACK")
        finally:
            self._write_transaction = False
            self.close()


class PostgreSQLStoreBase(SQLiteStoreBase):
    """复用 v1-v6 领域语义，并承载后续服务器版本化迁移。"""

    def __init__(self, dsn: str, allowed_reviewers: Sequence[str] = ("Fox",)) -> None:
        if not dsn.strip():
            raise ValueError("PostgreSQL DSN 不能为空")
        self.dsn = dsn
        self.allowed_reviewers = frozenset(allowed_reviewers)
        with self._connect() as connection:
            apply_postgresql_migrations(connection)

    def _connect(self) -> PostgreSQLConnection:
        raw_connection = psycopg.connect(
            self.dsn,
            autocommit=True,
            row_factory=_row_factory,
        )
        return PostgreSQLConnection(raw_connection)

    def _begin_command_transaction(
        self,
        connection: PostgreSQLConnection,
        context: CommandContext,
        command_name: str,
    ) -> None:
        """串行化同一幂等命令，随后由项目行锁保护版本推进。"""

        connection.execute("BEGIN")
        lock_key = ":".join(
            (
                context.project_id,
                context.actor.kind.value,
                context.actor.actor_id,
                command_name,
                context.idempotency_key,
            )
        )
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(?, 0))",
            (lock_key,),
        )

    @property
    def schema_version(self) -> int:
        """返回 PostgreSQL 当前已应用迁移版本。"""

        with self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
            ).fetchone()
            return int(row[0])

    def quick_check(self) -> bool:
        """核对迁移版本、校验和及 F2.2 核心表是否完整。"""

        expected = {migration.version: migration.checksum for migration in POSTGRESQL_MIGRATIONS}
        required_tables = ("projects", "events", "proposals", "human_actions", "state_items")
        with self._connect() as connection:
            applied = {
                int(row[0]): str(row[1])
                for row in connection.execute(
                    "SELECT version, checksum FROM schema_migrations ORDER BY version"
                )
            }
            tables = {
                str(row[0])
                for row in connection.execute(
                    """
                    SELECT table_name FROM information_schema.tables
                    WHERE table_schema = current_schema()
                      AND table_name IN (?, ?, ?, ?, ?)
                    """,
                    required_tables,
                )
            }
        return applied == expected and tables == set(required_tables)


class PostgreSQLCanonicalStore(
    PostgreSQLStoreBase,
    SQLiteEvidenceQueryMixin,
    SQLiteProposalMixin,
    SQLiteMeetingMixin,
    SQLiteImportMixin,
    SQLiteCommandMixin,
    SQLiteQueryMixin,
):
    """服务器侧权威事件、人工审批和投影适配器。"""


__all__ = ["PostgreSQLCanonicalStore", "PostgreSQLStoreBase"]
