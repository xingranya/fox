"""PostgreSQL 权威库的版本化、可校验迁移。"""

from __future__ import annotations

from datetime import UTC, datetime

from .sqlite_migrations import MIGRATIONS, Migration


POSTGRESQL_SCHEMA_VERSION = 8


def _translate_statement(statement: str) -> str:
    """把共享 v1-v6 DDL 中唯一的 SQLite 自增语法转换为 PostgreSQL。"""

    return statement.replace(
        "global_position INTEGER PRIMARY KEY AUTOINCREMENT",
        "global_position BIGSERIAL PRIMARY KEY",
    )


_SHARED_POSTGRESQL_MIGRATIONS = tuple(
    Migration(
        migration.version,
        migration.name,
        tuple(_translate_statement(statement) for statement in migration.statements),
    )
    for migration in MIGRATIONS
    if migration.version <= 6
)


POSTGRESQL_OBJECT_EVIDENCE_MIGRATION = Migration(
    7,
    "object_evidence_admission",
    (
        """
        CREATE TABLE evidence_uploads (
            upload_id TEXT PRIMARY KEY CHECK(length(upload_id) > 0),
            project_id TEXT NOT NULL,
            logical_source_id TEXT NOT NULL CHECK(length(logical_source_id) > 0),
            original_filename TEXT NOT NULL CHECK(length(original_filename) > 0),
            expected_sha256 TEXT NOT NULL CHECK(length(expected_sha256) = 64),
            expected_size_bytes BIGINT NOT NULL CHECK(expected_size_bytes >= 0),
            expected_media_type TEXT NOT NULL CHECK(length(expected_media_type) > 0),
            confidentiality TEXT NOT NULL CHECK(confidentiality IN ('P0','P1','P2','P3')),
            idempotency_key TEXT NOT NULL CHECK(length(idempotency_key) > 0),
            request_hash TEXT NOT NULL CHECK(length(request_hash) = 64),
            temporary_object_key TEXT NOT NULL UNIQUE,
            temporary_object_version_id TEXT,
            state TEXT NOT NULL CHECK(state IN (
                'UPLOADING','QUARANTINED','VERIFIED','ACTIVE','REJECTED','EXPIRED','REVOKED'
            )),
            actual_sha256 TEXT CHECK(actual_sha256 IS NULL OR length(actual_sha256) = 64),
            actual_size_bytes BIGINT CHECK(actual_size_bytes IS NULL OR actual_size_bytes >= 0),
            detected_media_type TEXT,
            final_object_key TEXT,
            final_object_version_id TEXT,
            rejection_code TEXT,
            rejection_reason TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            UNIQUE(project_id, idempotency_key),
            FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE RESTRICT
        )
        """,
        """
        CREATE TABLE evidence_object_versions (
            version_id TEXT PRIMARY KEY CHECK(length(version_id) > 0),
            project_id TEXT NOT NULL,
            logical_source_id TEXT NOT NULL CHECK(length(logical_source_id) > 0),
            version_number INTEGER NOT NULL CHECK(version_number > 0),
            upload_id TEXT NOT NULL UNIQUE,
            original_filename TEXT NOT NULL,
            sha256 TEXT NOT NULL CHECK(length(sha256) = 64),
            size_bytes BIGINT NOT NULL CHECK(size_bytes >= 0),
            media_type TEXT NOT NULL,
            confidentiality TEXT NOT NULL CHECK(confidentiality IN ('P0','P1','P2','P3')),
            bucket TEXT NOT NULL CHECK(length(bucket) > 0),
            object_key TEXT NOT NULL CHECK(length(object_key) > 0),
            object_version_id TEXT NOT NULL CHECK(length(object_version_id) > 0),
            state TEXT NOT NULL CHECK(state IN ('ACTIVE','REVOKED')),
            activated_at TEXT NOT NULL,
            revoked_at TEXT,
            revoked_by TEXT,
            revocation_reason TEXT,
            object_deleted_at TEXT,
            UNIQUE(project_id, logical_source_id, version_number),
            FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE RESTRICT,
            FOREIGN KEY(upload_id) REFERENCES evidence_uploads(upload_id) ON DELETE RESTRICT
        )
        """,
        """
        CREATE TABLE evidence_state_transitions (
            transition_id TEXT PRIMARY KEY CHECK(length(transition_id) > 0),
            upload_id TEXT NOT NULL,
            from_state TEXT,
            to_state TEXT NOT NULL,
            reason_code TEXT NOT NULL,
            details_json TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            FOREIGN KEY(upload_id) REFERENCES evidence_uploads(upload_id) ON DELETE RESTRICT
        )
        """,
        """
        CREATE TABLE evidence_object_tombstones (
            tombstone_id TEXT PRIMARY KEY CHECK(length(tombstone_id) > 0),
            version_id TEXT NOT NULL UNIQUE,
            bucket TEXT NOT NULL,
            object_key TEXT NOT NULL,
            object_version_id TEXT NOT NULL,
            reason TEXT NOT NULL,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            earliest_delete_at TEXT NOT NULL,
            deletion_claim_id TEXT,
            deletion_claimed_at TEXT,
            deleted_at TEXT,
            FOREIGN KEY(version_id) REFERENCES evidence_object_versions(version_id) ON DELETE RESTRICT
        )
        """,
        """
        CREATE TABLE evidence_reconciliation_runs (
            run_id TEXT PRIMARY KEY CHECK(length(run_id) > 0),
            started_at TEXT NOT NULL,
            completed_at TEXT NOT NULL,
            cleanup_enabled INTEGER NOT NULL CHECK(cleanup_enabled IN (0,1)),
            expired_uploads INTEGER NOT NULL CHECK(expired_uploads >= 0),
            aborted_multipart_uploads INTEGER NOT NULL CHECK(aborted_multipart_uploads >= 0),
            deleted_orphan_objects INTEGER NOT NULL CHECK(deleted_orphan_objects >= 0),
            issue_count INTEGER NOT NULL CHECK(issue_count >= 0),
            details_json TEXT NOT NULL
        )
        """,
        "CREATE INDEX idx_evidence_uploads_state_expiry ON evidence_uploads(state, expires_at)",
        "CREATE INDEX idx_evidence_uploads_project_source ON evidence_uploads(project_id, logical_source_id, created_at)",
        "CREATE INDEX idx_evidence_versions_project_source ON evidence_object_versions(project_id, logical_source_id, version_number)",
        "CREATE INDEX idx_evidence_versions_object ON evidence_object_versions(bucket, object_key, object_version_id)",
        "CREATE INDEX idx_evidence_tombstones_due ON evidence_object_tombstones(deleted_at, earliest_delete_at)",
    ),
)


POSTGRESQL_OIDC_IDENTITY_MIGRATION = Migration(
    8,
    "oidc_employee_identity_and_sessions",
    (
        """
        CREATE TABLE employees (
            employee_id TEXT PRIMARY KEY CHECK(length(employee_id) > 0),
            display_name TEXT NOT NULL CHECK(length(display_name) > 0),
            primary_email TEXT,
            status TEXT NOT NULL CHECK(status IN ('ACTIVE','DISABLED')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            disabled_at TEXT,
            disabled_by TEXT,
            disable_reason TEXT
        )
        """,
        """
        CREATE TABLE oidc_identity_bindings (
            binding_id TEXT PRIMARY KEY CHECK(length(binding_id) > 0),
            issuer TEXT NOT NULL CHECK(length(issuer) > 0),
            subject TEXT NOT NULL CHECK(length(subject) > 0),
            employee_id TEXT NOT NULL,
            email_at_binding TEXT,
            status TEXT NOT NULL CHECK(status IN ('ACTIVE','DISABLED')),
            created_at TEXT NOT NULL,
            created_by TEXT NOT NULL CHECK(length(created_by) > 0),
            disabled_at TEXT,
            disabled_by TEXT,
            disable_reason TEXT,
            UNIQUE(issuer, subject),
            FOREIGN KEY(employee_id) REFERENCES employees(employee_id) ON DELETE RESTRICT
        )
        """,
        """
        CREATE TABLE oidc_authorization_transactions (
            transaction_id TEXT PRIMARY KEY CHECK(length(transaction_id) > 0),
            state_digest TEXT NOT NULL UNIQUE CHECK(length(state_digest) = 64),
            nonce_digest TEXT NOT NULL CHECK(length(nonce_digest) = 64),
            code_verifier_ciphertext TEXT NOT NULL CHECK(length(code_verifier_ciphertext) > 0),
            redirect_uri TEXT NOT NULL CHECK(length(redirect_uri) > 0),
            status TEXT NOT NULL CHECK(status IN (
                'PENDING','PROCESSING','CONSUMED','FAILED','EXPIRED'
            )),
            authorization_code_digest TEXT UNIQUE CHECK(
                authorization_code_digest IS NULL OR length(authorization_code_digest) = 64
            ),
            failure_code TEXT,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            claimed_at TEXT,
            consumed_at TEXT,
            failed_at TEXT
        )
        """,
        """
        CREATE TABLE employee_sessions (
            session_id TEXT PRIMARY KEY CHECK(length(session_id) > 0),
            session_secret_digest TEXT NOT NULL UNIQUE CHECK(length(session_secret_digest) = 64),
            employee_id TEXT NOT NULL,
            binding_id TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('ACTIVE','REVOKED','EXPIRED')),
            access_token_ciphertext TEXT CHECK(
                access_token_ciphertext IS NULL OR length(access_token_ciphertext) > 0
            ),
            refresh_token_ciphertext TEXT,
            token_version INTEGER NOT NULL CHECK(token_version > 0),
            access_token_expires_at TEXT NOT NULL,
            session_expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            revoked_at TEXT,
            revoked_by TEXT,
            revocation_reason TEXT,
            CHECK(status != 'ACTIVE' OR access_token_ciphertext IS NOT NULL),
            FOREIGN KEY(employee_id) REFERENCES employees(employee_id) ON DELETE RESTRICT,
            FOREIGN KEY(binding_id) REFERENCES oidc_identity_bindings(binding_id) ON DELETE RESTRICT
        )
        """,
        """
        CREATE TABLE employee_session_events (
            event_id TEXT PRIMARY KEY CHECK(length(event_id) > 0),
            session_id TEXT NOT NULL,
            sequence_number INTEGER NOT NULL CHECK(sequence_number > 0),
            employee_id TEXT NOT NULL,
            event_type TEXT NOT NULL CHECK(event_type IN (
                'CREATED','REFRESHED','IDENTITY_ASSERTED','REVOKED','EXPIRED'
            )),
            actor_kind TEXT NOT NULL CHECK(actor_kind IN ('HUMAN','AI','WORKFLOW','SYSTEM')),
            actor_id TEXT NOT NULL CHECK(length(actor_id) > 0),
            details_json TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            UNIQUE(session_id, sequence_number),
            FOREIGN KEY(session_id) REFERENCES employee_sessions(session_id) ON DELETE RESTRICT,
            FOREIGN KEY(employee_id) REFERENCES employees(employee_id) ON DELETE RESTRICT
        )
        """,
        "CREATE INDEX idx_oidc_authorization_expiry ON oidc_authorization_transactions(status, expires_at)",
        "CREATE INDEX idx_oidc_bindings_employee ON oidc_identity_bindings(employee_id, status)",
        "CREATE INDEX idx_employee_sessions_employee ON employee_sessions(employee_id, status, session_expires_at)",
        "CREATE INDEX idx_employee_sessions_expiry ON employee_sessions(status, session_expires_at)",
        "CREATE INDEX idx_employee_session_events_session ON employee_session_events(session_id, occurred_at)",
    ),
)


POSTGRESQL_MIGRATIONS = (
    *_SHARED_POSTGRESQL_MIGRATIONS,
    POSTGRESQL_OBJECT_EVIDENCE_MIGRATION,
    POSTGRESQL_OIDC_IDENTITY_MIGRATION,
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
    "POSTGRESQL_OBJECT_EVIDENCE_MIGRATION",
    "POSTGRESQL_OIDC_IDENTITY_MIGRATION",
    "POSTGRESQL_SCHEMA_VERSION",
    "apply_postgresql_migrations",
]
