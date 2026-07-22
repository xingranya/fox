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
    Migration(
        3,
        "versioned_source_imports",
        (
            """
            CREATE TABLE source_import_batches (
                project_id TEXT NOT NULL,
                batch_id TEXT NOT NULL,
                manifest_sha256 TEXT NOT NULL CHECK(length(manifest_sha256) = 64),
                import_digest TEXT NOT NULL CHECK(length(import_digest) = 64),
                manifest_schema_version TEXT NOT NULL,
                origin_ref TEXT NOT NULL,
                snapshot_at TEXT,
                input_record_count INTEGER NOT NULL CHECK(input_record_count >= 0),
                input_gap_count INTEGER NOT NULL CHECK(input_gap_count >= 0),
                new_logical_source_count INTEGER NOT NULL DEFAULT 0 CHECK(new_logical_source_count >= 0),
                new_content_count INTEGER NOT NULL DEFAULT 0 CHECK(new_content_count >= 0),
                enriched_content_count INTEGER NOT NULL DEFAULT 0 CHECK(enriched_content_count >= 0),
                new_version_count INTEGER NOT NULL DEFAULT 0 CHECK(new_version_count >= 0),
                duplicate_record_count INTEGER NOT NULL DEFAULT 0 CHECK(duplicate_record_count >= 0),
                new_alias_count INTEGER NOT NULL DEFAULT 0 CHECK(new_alias_count >= 0),
                updated_alias_count INTEGER NOT NULL DEFAULT 0 CHECK(updated_alias_count >= 0),
                new_supersession_count INTEGER NOT NULL DEFAULT 0 CHECK(new_supersession_count >= 0),
                gap_observation_count INTEGER NOT NULL DEFAULT 0 CHECK(gap_observation_count >= 0),
                imported_event_id TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                PRIMARY KEY(project_id, batch_id),
                UNIQUE(project_id, import_digest),
                FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE RESTRICT,
                FOREIGN KEY(imported_event_id) REFERENCES events(event_id) ON DELETE RESTRICT
            )
            """,
            """
            CREATE TABLE source_contents (
                project_id TEXT NOT NULL,
                sha256 TEXT NOT NULL CHECK(length(sha256) = 64),
                size_bytes INTEGER CHECK(size_bytes IS NULL OR size_bytes >= 0),
                media_type TEXT,
                first_batch_id TEXT,
                created_at TEXT NOT NULL,
                PRIMARY KEY(project_id, sha256),
                FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE RESTRICT,
                FOREIGN KEY(project_id, first_batch_id)
                    REFERENCES source_import_batches(project_id, batch_id) ON DELETE RESTRICT
            )
            """,
            """
            CREATE TABLE logical_sources (
                project_id TEXT NOT NULL,
                logical_source_id TEXT NOT NULL,
                source_role TEXT NOT NULL,
                confidentiality TEXT CHECK(confidentiality IS NULL OR confidentiality IN ('P0','P1','P2','P3')),
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(project_id, logical_source_id),
                FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE RESTRICT
            )
            """,
            """
            CREATE TABLE source_versions (
                project_id TEXT NOT NULL,
                source_version_id TEXT NOT NULL,
                logical_source_id TEXT NOT NULL,
                sha256 TEXT NOT NULL CHECK(length(sha256) = 64),
                relative_path TEXT NOT NULL,
                source_role TEXT NOT NULL,
                confidentiality TEXT CHECK(confidentiality IS NULL OR confidentiality IN ('P0','P1','P2','P3')),
                status TEXT NOT NULL,
                version_label TEXT,
                observed_at TEXT,
                import_batch_id TEXT,
                registered_event_id TEXT NOT NULL,
                is_current INTEGER NOT NULL CHECK(is_current IN (0,1)),
                created_at TEXT NOT NULL,
                PRIMARY KEY(project_id, source_version_id),
                UNIQUE(project_id, logical_source_id, sha256),
                FOREIGN KEY(project_id, logical_source_id)
                    REFERENCES logical_sources(project_id, logical_source_id) ON DELETE RESTRICT,
                FOREIGN KEY(project_id, sha256)
                    REFERENCES source_contents(project_id, sha256) ON DELETE RESTRICT,
                FOREIGN KEY(project_id, import_batch_id)
                    REFERENCES source_import_batches(project_id, batch_id) ON DELETE RESTRICT,
                FOREIGN KEY(registered_event_id) REFERENCES events(event_id) ON DELETE RESTRICT
            )
            """,
            """
            CREATE TABLE source_aliases (
                project_id TEXT NOT NULL,
                alias_id TEXT NOT NULL,
                logical_source_id TEXT NOT NULL,
                alias_kind TEXT NOT NULL CHECK(alias_kind IN ('legacy_id','reserved_id','path')),
                status TEXT NOT NULL CHECK(status IN ('active','deprecated','reserved')),
                first_batch_id TEXT NOT NULL,
                last_batch_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(project_id, alias_id),
                FOREIGN KEY(project_id, logical_source_id)
                    REFERENCES logical_sources(project_id, logical_source_id) ON DELETE RESTRICT,
                FOREIGN KEY(project_id, first_batch_id)
                    REFERENCES source_import_batches(project_id, batch_id) ON DELETE RESTRICT,
                FOREIGN KEY(project_id, last_batch_id)
                    REFERENCES source_import_batches(project_id, batch_id) ON DELETE RESTRICT
            )
            """,
            """
            CREATE TABLE source_version_relations (
                project_id TEXT NOT NULL,
                predecessor_version_id TEXT NOT NULL,
                successor_version_id TEXT NOT NULL,
                relation_type TEXT NOT NULL CHECK(relation_type = 'supersedes'),
                import_batch_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(project_id, predecessor_version_id, successor_version_id),
                CHECK(predecessor_version_id <> successor_version_id),
                FOREIGN KEY(project_id, predecessor_version_id)
                    REFERENCES source_versions(project_id, source_version_id) ON DELETE RESTRICT,
                FOREIGN KEY(project_id, successor_version_id)
                    REFERENCES source_versions(project_id, source_version_id) ON DELETE RESTRICT,
                FOREIGN KEY(project_id, import_batch_id)
                    REFERENCES source_import_batches(project_id, batch_id) ON DELETE RESTRICT
            )
            """,
            """
            CREATE TABLE source_gaps (
                project_id TEXT NOT NULL,
                gap_id TEXT NOT NULL,
                import_batch_id TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('KNOWN_SOURCE_GAP','PARTIALLY_RESOLVED','RESOLVED')),
                description TEXT NOT NULL,
                scope TEXT NOT NULL,
                evidence_ref TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                PRIMARY KEY(project_id, gap_id, import_batch_id),
                FOREIGN KEY(project_id, import_batch_id)
                    REFERENCES source_import_batches(project_id, batch_id) ON DELETE RESTRICT
            )
            """,
            "CREATE UNIQUE INDEX idx_source_versions_one_current ON source_versions(project_id, logical_source_id) WHERE is_current = 1",
            "CREATE INDEX idx_source_versions_hash ON source_versions(project_id, sha256)",
            "CREATE INDEX idx_source_aliases_source ON source_aliases(project_id, logical_source_id)",
            "CREATE INDEX idx_source_gaps_latest ON source_gaps(project_id, gap_id, observed_at)",
            "CREATE INDEX idx_source_import_manifest ON source_import_batches(project_id, manifest_sha256)",
            """
            INSERT INTO source_contents(project_id, sha256, size_bytes, media_type, first_batch_id, created_at)
            SELECT project_id, sha256, size, NULL, NULL, created_at FROM sources
            """,
            """
            INSERT INTO logical_sources(
                project_id, logical_source_id, source_role, confidentiality, status, created_at, updated_at
            )
            SELECT project_id, source_id, source_role, confidentiality, status, created_at, created_at
            FROM sources
            """,
            """
            INSERT INTO source_versions(
                project_id, source_version_id, logical_source_id, sha256, relative_path,
                source_role, confidentiality, status, version_label, observed_at,
                import_batch_id, registered_event_id, is_current, created_at
            )
            SELECT project_id, 'LEGACY-' || source_id || '@' || substr(sha256, 1, 16),
                   source_id, sha256, relative_path, source_role, confidentiality, status,
                   NULL, created_at, NULL, registered_event_id, 1, created_at
            FROM sources
            """,
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
