"""PostgreSQL 权威库的版本化、可校验迁移。"""

from __future__ import annotations

from datetime import UTC, datetime

from .sqlite_migrations import MIGRATIONS, Migration


POSTGRESQL_SCHEMA_VERSION = 6


def _translate_statement(statement: str) -> str:
    """把共享 v1-v6 DDL 中唯一的 SQLite 自增语法转换为 PostgreSQL。"""

    return statement.replace(
        "global_position INTEGER PRIMARY KEY AUTOINCREMENT",
        "global_position BIGSERIAL PRIMARY KEY",
    )


POSTGRESQL_MIGRATIONS = tuple(
    Migration(
        migration.version,
        migration.name,
        tuple(_translate_statement(statement) for statement in migration.statements),
    )
    for migration in MIGRATIONS
    if migration.version <= POSTGRESQL_SCHEMA_VERSION
)


def apply_postgresql_migrations(connection) -> int:
    """串行应用 PostgreSQL 迁移，校验已登记版本且整版失败回滚。"""

    lock_name = "brand-project-os:postgresql-migrations"
    connection.execute("SELECT pg_advisory_lock(hashtextextended(?, 0))", (lock_name,))
    try:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                checksum TEXT NOT NULL CHECK(length(checksum) = 64),
                applied_at TEXT NOT NULL
            )
            """
        )
        applied = {
            int(row[0]): str(row[1])
            for row in connection.execute(
                "SELECT version, checksum FROM schema_migrations ORDER BY version"
            )
        }
        for migration in POSTGRESQL_MIGRATIONS:
            if migration.version in applied:
                if applied[migration.version] != migration.checksum:
                    raise RuntimeError(f"迁移 {migration.version} 校验和发生变化")
                continue
            connection.execute("BEGIN")
            try:
                current = connection.execute(
                    "SELECT checksum FROM schema_migrations WHERE version = ? FOR UPDATE",
                    (migration.version,),
                ).fetchone()
                if current is not None:
                    if current[0] != migration.checksum:
                        raise RuntimeError(f"迁移 {migration.version} 校验和发生变化")
                    connection.execute("COMMIT")
                    applied[migration.version] = str(current[0])
                    continue
                for statement in migration.statements:
                    connection.execute(statement)
                connection.execute(
                    """
                    INSERT INTO schema_migrations(version, name, checksum, applied_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        migration.version,
                        migration.name,
                        migration.checksum,
                        datetime.now(UTC).isoformat(),
                    ),
                )
                connection.execute("COMMIT")
                applied[migration.version] = migration.checksum
            except Exception:
                connection.execute("ROLLBACK")
                raise
    finally:
        connection.execute("SELECT pg_advisory_unlock(hashtextextended(?, 0))", (lock_name,))
    return max((migration.version for migration in POSTGRESQL_MIGRATIONS), default=0)


__all__ = [
    "POSTGRESQL_MIGRATIONS",
    "POSTGRESQL_SCHEMA_VERSION",
    "apply_postgresql_migrations",
]
