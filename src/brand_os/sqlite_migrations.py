"""SQLite 权威库的显式、可校验迁移。"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Sequence


@dataclass(frozen=True, slots=True)
class Migration:
    """一组必须在同一事务内完成的 SQL 语句。"""

    version: int
    name: str
    statements: tuple[str, ...]

    @property
    def checksum(self) -> str:
        content = "\n".join(self.statements).encode("utf-8")
        return hashlib.sha256(content).hexdigest()


MIGRATIONS = (
    Migration(
        1,
        "initial_authority_store",
        (
            """
            CREATE TABLE projects (
                project_id TEXT PRIMARY KEY CHECK(length(project_id) > 0),
                name TEXT NOT NULL CHECK(length(name) > 0),
                version INTEGER NOT NULL DEFAULT 0 CHECK(version >= 0),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE commands (
                project_id TEXT NOT NULL,
                actor_kind TEXT NOT NULL CHECK(actor_kind IN ('HUMAN','AI','WORKFLOW','SYSTEM')),
                actor_id TEXT NOT NULL CHECK(length(actor_id) > 0),
                command_name TEXT NOT NULL CHECK(length(command_name) > 0),
                idempotency_key TEXT NOT NULL CHECK(length(idempotency_key) > 0),
                request_hash TEXT NOT NULL CHECK(length(request_hash) = 64),
                result_json TEXT NOT NULL,
                committed_at TEXT NOT NULL,
                PRIMARY KEY(project_id, actor_kind, actor_id, command_name, idempotency_key),
                FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE RESTRICT
            )
            """,
            """
            CREATE TABLE events (
                global_position INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                project_id TEXT NOT NULL,
                project_version INTEGER NOT NULL CHECK(project_version > 0),
                aggregate_type TEXT NOT NULL,
                aggregate_id TEXT NOT NULL,
                aggregate_version INTEGER NOT NULL CHECK(aggregate_version > 0),
                event_type TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                actor_kind TEXT NOT NULL CHECK(actor_kind IN ('HUMAN','AI','WORKFLOW','SYSTEM')),
                actor_id TEXT NOT NULL,
                correlation_id TEXT NOT NULL,
                causation_id TEXT,
                payload_json TEXT NOT NULL,
                committed_at TEXT NOT NULL,
                UNIQUE(project_id, project_version),
                UNIQUE(project_id, aggregate_type, aggregate_id, aggregate_version),
                FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE RESTRICT
            )
            """,
            """
            CREATE TABLE sources (
                source_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                sha256 TEXT NOT NULL CHECK(length(sha256) = 64),
                size INTEGER NOT NULL CHECK(size >= 0),
                relative_path TEXT NOT NULL,
                source_role TEXT NOT NULL,
                confidentiality TEXT NOT NULL CHECK(confidentiality IN ('P0','P1','P2','P3')),
                status TEXT NOT NULL,
                registered_event_id TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                UNIQUE(project_id, sha256),
                UNIQUE(project_id, source_id, sha256),
                FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE RESTRICT,
                FOREIGN KEY(registered_event_id) REFERENCES events(event_id) ON DELETE RESTRICT
            )
            """,
            """
            CREATE TABLE classification_candidates (
                candidate_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                source_id TEXT NOT NULL,
                source_sha256 TEXT NOT NULL CHECK(length(source_sha256) = 64),
                locator TEXT NOT NULL CHECK(length(locator) > 0),
                excerpt TEXT NOT NULL CHECK(length(excerpt) > 0),
                classification TEXT NOT NULL CHECK(classification IN (
                    'FACT','VIEW','PREFERENCE','HYPOTHESIS','OPTION','TENDENCY','TARGET_DATE',
                    'DECISION_CANDIDATE','CONSTRAINT_CANDIDATE','ACTION_CANDIDATE','OPEN'
                )),
                reasoning TEXT NOT NULL CHECK(length(reasoning) > 0),
                status TEXT NOT NULL DEFAULT 'proposed' CHECK(status = 'proposed'),
                recorded_event_id TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE RESTRICT,
                FOREIGN KEY(project_id, source_id, source_sha256)
                    REFERENCES sources(project_id, source_id, sha256) ON DELETE RESTRICT,
                FOREIGN KEY(recorded_event_id) REFERENCES events(event_id) ON DELETE RESTRICT
            )
            """,
            """
            CREATE TABLE proposals (
                proposal_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                base_state_version INTEGER NOT NULL CHECK(base_state_version >= 0),
                proposal_kind TEXT NOT NULL CHECK(proposal_kind IN ('create','update','supersede','link','flag_conflict')),
                subject_id TEXT,
                classification TEXT NOT NULL CHECK(classification IN (
                    'FACT','VIEW','PREFERENCE','HYPOTHESIS','OPTION','TENDENCY','TARGET_DATE',
                    'DECISION_CANDIDATE','CONSTRAINT_CANDIDATE','ACTION_CANDIDATE','OPEN'
                )),
                before_json TEXT,
                after_json TEXT NOT NULL,
                reason TEXT NOT NULL,
                impact_scope TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'proposed' CHECK(status IN ('proposed','approved','rejected')),
                created_event_id TEXT NOT NULL UNIQUE,
                reviewed_event_id TEXT UNIQUE,
                created_at TEXT NOT NULL,
                reviewed_at TEXT,
                FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE RESTRICT,
                FOREIGN KEY(created_event_id) REFERENCES events(event_id) ON DELETE RESTRICT,
                FOREIGN KEY(reviewed_event_id) REFERENCES events(event_id) ON DELETE RESTRICT
            )
            """,
            """
            CREATE TABLE proposal_evidence (
                proposal_id TEXT NOT NULL,
                evidence_ref TEXT NOT NULL CHECK(length(evidence_ref) > 0),
                PRIMARY KEY(proposal_id, evidence_ref),
                FOREIGN KEY(proposal_id) REFERENCES proposals(proposal_id) ON DELETE CASCADE
            )
            """,
            """
            CREATE TABLE relations (
                relation_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                from_type TEXT NOT NULL,
                from_id TEXT NOT NULL,
                relation_type TEXT NOT NULL CHECK(relation_type IN (
                    'sourced_from','raised_in','supports','opposes','conflicts_with','applies_to',
                    'approved_by','supersedes','depends_on','answers','pending_confirmation'
                )),
                to_type TEXT NOT NULL,
                to_id TEXT NOT NULL,
                evidence_ref TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'proposed' CHECK(status = 'proposed'),
                recorded_event_id TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE RESTRICT,
                FOREIGN KEY(recorded_event_id) REFERENCES events(event_id) ON DELETE RESTRICT
            )
            """,
            """
            CREATE TABLE human_actions (
                action_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                proposal_id TEXT NOT NULL,
                action TEXT NOT NULL CHECK(action IN ('approve','modify_and_approve','reject')),
                actor_id TEXT NOT NULL,
                reason TEXT NOT NULL,
                before_json TEXT,
                after_json TEXT,
                evidence_json TEXT NOT NULL,
                base_state_version INTEGER NOT NULL,
                event_id TEXT NOT NULL UNIQUE,
                acted_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE RESTRICT,
                FOREIGN KEY(proposal_id) REFERENCES proposals(proposal_id) ON DELETE RESTRICT,
                FOREIGN KEY(event_id) REFERENCES events(event_id) ON DELETE RESTRICT
            )
            """,
            """
            CREATE TABLE state_items (
                project_id TEXT NOT NULL,
                item_type TEXT NOT NULL,
                item_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                source_proposal_id TEXT NOT NULL,
                updated_event_id TEXT NOT NULL,
                state_version INTEGER NOT NULL CHECK(state_version > 0),
                PRIMARY KEY(project_id, item_type, item_id),
                FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE RESTRICT,
                FOREIGN KEY(source_proposal_id) REFERENCES proposals(proposal_id) ON DELETE RESTRICT,
                FOREIGN KEY(updated_event_id) REFERENCES events(event_id) ON DELETE RESTRICT
            )
            """,
        ),
    ),
    Migration(
        2,
        "read_indexes",
        (
            "CREATE INDEX idx_events_project_position ON events(project_id, global_position)",
            "CREATE INDEX idx_proposals_project_status ON proposals(project_id, status, created_at)",
            "CREATE INDEX idx_candidates_project_source ON classification_candidates(project_id, source_id)",
            "CREATE INDEX idx_relations_project_from ON relations(project_id, from_type, from_id)",
            "CREATE INDEX idx_relations_project_to ON relations(project_id, to_type, to_id)",
        ),
    ),
)


def ensure_migration_table(connection: sqlite3.Connection) -> None:
    """建立独立的迁移登记表。"""

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


def apply_migrations(
    connection: sqlite3.Connection, migrations: Sequence[Migration] = MIGRATIONS
) -> int:
    """按版本应用迁移；任一语句失败时回滚整版。"""

    ensure_migration_table(connection)
    applied = {
        row[0]: row[1]
        for row in connection.execute("SELECT version, checksum FROM schema_migrations ORDER BY version")
    }
    for migration in migrations:
        if migration.version in applied:
            if applied[migration.version] != migration.checksum:
                raise RuntimeError(f"迁移 {migration.version} 校验和发生变化")
            continue
        connection.execute("BEGIN IMMEDIATE")
        try:
            for statement in migration.statements:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migrations(version, name, checksum, applied_at) VALUES (?, ?, ?, ?)",
                (migration.version, migration.name, migration.checksum, datetime.now(UTC).isoformat()),
            )
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
    return max((migration.version for migration in migrations), default=0)
