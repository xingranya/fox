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
                self.assertEqual(
                    apply_migrations(connection, MIGRATIONS), MIGRATIONS[-1].version
                )
                versions = [row[0] for row in connection.execute("SELECT version FROM schema_migrations")]
                self.assertEqual(versions, [1, 2, 3, 4, 5, 6, 7])
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

    def test_v4_upgrade_copies_existing_source_into_version_tables(self) -> None:
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
                self.assertEqual(
                    apply_migrations(connection, MIGRATIONS), MIGRATIONS[-1].version
                )
                version = connection.execute(
                    "SELECT logical_source_id, sha256, is_current FROM source_versions"
                ).fetchone()
                self.assertEqual(version, ("SRC-1", digest, 1))
            finally:
                connection.close()

    def test_v5_upgrade_builds_lifecycle_for_existing_proposals(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "project.db"
            connection = sqlite3.connect(database, isolation_level=None)
            try:
                apply_migrations(connection, MIGRATIONS[:4])
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute(
                    """
                    INSERT INTO projects(project_id, name, version, created_at, updated_at)
                    VALUES ('hongri', '鸿日', 2, '2026-07-22T00:00:00+00:00',
                            '2026-07-22T00:00:01+00:00')
                    """
                )
                connection.execute(
                    """
                    INSERT INTO events(
                        event_id, project_id, project_version, aggregate_type, aggregate_id,
                        aggregate_version, event_type, schema_version, actor_kind, actor_id,
                        correlation_id, payload_json, committed_at
                    ) VALUES (
                        'event-project', 'hongri', 1, 'project', 'hongri', 1,
                        'PROJECT_CREATED', 'domain-event.v1', 'HUMAN', 'Fox',
                        'project', '{}', '2026-07-22T00:00:00+00:00'
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO events(
                        event_id, project_id, project_version, aggregate_type, aggregate_id,
                        aggregate_version, event_type, schema_version, actor_kind, actor_id,
                        correlation_id, payload_json, committed_at
                    ) VALUES (
                        'event-proposal', 'hongri', 2, 'proposal', 'proposal-1', 1,
                        'PROPOSAL_CREATED', 'domain-event.v1', 'AI', 'codex',
                        'proposal', '{}', '2026-07-22T00:00:01+00:00'
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO proposals(
                        proposal_id, project_id, base_state_version, proposal_kind,
                        subject_id, classification, before_json, after_json, reason,
                        impact_scope, status, created_event_id, created_at
                    ) VALUES (
                        'proposal-1', 'hongri', 1, 'create', 'question-1', 'OPEN',
                        NULL, '{"id":"question-1"}', '等待确认', '本轮', 'proposed',
                        'event-proposal', '2026-07-22T00:00:01+00:00'
                    )
                    """
                )
                self.assertEqual(apply_migrations(connection, MIGRATIONS[:5]), 5)
                lifecycle = connection.execute(
                    """
                    SELECT status, revision, last_event_id
                    FROM proposal_lifecycle WHERE proposal_id = 'proposal-1'
                    """
                ).fetchone()
                self.assertEqual(lifecycle, ("proposed", 0, "event-proposal"))
            finally:
                connection.close()

    def test_v6_upgrade_adds_state_validity_without_changing_existing_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "project.db"
            connection = sqlite3.connect(database, isolation_level=None)
            try:
                apply_migrations(connection, MIGRATIONS[:5])
                self.assertEqual(apply_migrations(connection, MIGRATIONS[:6]), 6)
                proposal_columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(proposals)")
                }
                state_columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(state_items)")
                }
                self.assertTrue({"valid_from", "valid_until"}.issubset(proposal_columns))
                self.assertTrue({"valid_from", "valid_until"}.issubset(state_columns))
            finally:
                connection.close()

    def test_v7_upgrade_adds_runtime_tables_without_changing_project_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "project.db"
            connection = sqlite3.connect(database, isolation_level=None)
            try:
                apply_migrations(connection, MIGRATIONS[:6])
                connection.execute(
                    """
                    INSERT INTO projects(project_id, name, version, created_at, updated_at)
                    VALUES ('hongri', '鸿日', 3, '2026-07-22T00:00:00+00:00',
                            '2026-07-22T00:00:00+00:00')
                    """
                )
                self.assertEqual(apply_migrations(connection, MIGRATIONS), 7)
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                self.assertTrue(
                    {
                        "runtime_commands",
                        "runtime_tasks",
                        "runtime_mode_switches",
                        "task_packets",
                        "agent_runs",
                    }.issubset(tables)
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT version FROM projects WHERE project_id = 'hongri'"
                    ).fetchone()[0],
                    3,
                )
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
