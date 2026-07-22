"""SQLite 迁移、版本和回滚测试。"""

from __future__ import annotations

import sqlite3
import stat
import tempfile
import unittest
from pathlib import Path


from brand_os.sqlite_migrations import MIGRATIONS, Migration, apply_migrations
from brand_os.sqlite_store import MIN_SQLITE_VERSION, SQLiteCanonicalStore


class SQLiteMigrationTest(unittest.TestCase):
    """验证迁移可升级、失败整版回滚且运行版本受控。"""

    def test_new_database_reaches_current_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteCanonicalStore(Path(directory) / "project.db")
            self.assertEqual(store.schema_version, MIGRATIONS[-1].version)
            self.assertTrue(store.quick_check())
            self.assertGreaterEqual(sqlite3.sqlite_version_info, MIN_SQLITE_VERSION)
            self.assertEqual(stat.S_IMODE(store.database_path.stat().st_mode), 0o600)
            with sqlite3.connect(store.database_path) as connection:
                self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")

    def test_partial_database_upgrades_without_reapplying_old_migration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "project.db"
            connection = sqlite3.connect(database, isolation_level=None)
            try:
                self.assertEqual(apply_migrations(connection, MIGRATIONS[:1]), 1)
                self.assertEqual(apply_migrations(connection, MIGRATIONS), 3)
                versions = [row[0] for row in connection.execute("SELECT version FROM schema_migrations")]
                self.assertEqual(versions, [1, 2, 3])
            finally:
                connection.close()

    def test_failed_migration_rolls_back_entire_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection = sqlite3.connect(Path(directory) / "project.db", isolation_level=None)
            bad = Migration(
                MIGRATIONS[-1].version + 1,
                "must_rollback",
                ("CREATE TABLE should_not_remain(id INTEGER)", "THIS IS NOT SQL"),
            )
            try:
                apply_migrations(connection, MIGRATIONS)
                with self.assertRaises(sqlite3.DatabaseError):
                    apply_migrations(connection, (*MIGRATIONS, bad))
                table = connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'should_not_remain'"
                ).fetchone()
                migration = connection.execute(
                    "SELECT version FROM schema_migrations WHERE version = ?",
                    (bad.version,),
                ).fetchone()
                self.assertIsNone(table)
                self.assertIsNone(migration)
            finally:
                connection.close()

    def test_v3_upgrade_copies_existing_source_into_version_tables(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "project.db"
            connection = sqlite3.connect(database, isolation_level=None)
            digest = "3bfc269594ef649228e9a74bab00f042efc91d5acc6fbee31a382e80d42388fe"
            try:
                apply_migrations(connection, MIGRATIONS[:2])
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute(
                    """
                    INSERT INTO projects(project_id, name, version, created_at, updated_at)
                    VALUES ('hongri', '鸿日', 1, '2026-07-22T00:00:00+00:00', '2026-07-22T00:00:00+00:00')
                    """
                )
                connection.execute(
                    """
                    INSERT INTO events(
                        event_id, project_id, project_version, aggregate_type, aggregate_id,
                        aggregate_version, event_type, schema_version, actor_kind, actor_id,
                        correlation_id, payload_json, committed_at
                    ) VALUES (
                        'event-1', 'hongri', 1, 'source', 'SRC-1', 1, 'SOURCE_REGISTERED',
                        'domain-event.v1', 'SYSTEM', 'migration-test', 'test', '{}',
                        '2026-07-22T00:00:00+00:00'
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO sources(
                        source_id, project_id, sha256, size, relative_path, source_role,
                        confidentiality, status, registered_event_id, created_at
                    ) VALUES (
                        'SRC-1', 'hongri', ?, 2, '资料/总控.md', 'project_control',
                        'P2', 'current', 'event-1', '2026-07-22T00:00:00+00:00'
                    )
                    """,
                    (digest,),
                )
                self.assertEqual(apply_migrations(connection, MIGRATIONS), 3)
                version = connection.execute(
                    "SELECT logical_source_id, sha256, is_current FROM source_versions"
                ).fetchone()
                self.assertEqual(version, ("SRC-1", digest, 1))
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
