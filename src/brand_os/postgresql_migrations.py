"""PostgreSQL 权威库的版本化、可校验迁移。"""

from __future__ import annotations

from datetime import UTC, datetime

from .sqlite_migrations import MIGRATIONS, Migration


POSTGRESQL_SCHEMA_VERSION = 12


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


POSTGRESQL_PROJECT_AUTHORIZATION_MIGRATION = Migration(
    9,
    "project_rbac_confidentiality_and_rls",
    (
        """
        CREATE TABLE project_memberships (
            project_id TEXT NOT NULL,
            employee_id TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN (
                'OWNER','MANAGER','EDITOR','REVIEWER','VIEWER'
            )),
            confidentiality_ceiling TEXT NOT NULL CHECK(
                confidentiality_ceiling IN ('P0','P1','P2','P3')
            ),
            status TEXT NOT NULL CHECK(status IN ('ACTIVE','REVOKED')),
            granted_by_kind TEXT NOT NULL CHECK(granted_by_kind = 'EMPLOYEE'),
            granted_by_id TEXT NOT NULL CHECK(length(granted_by_id) > 0),
            granted_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            revoked_at TEXT,
            revoked_by TEXT,
            revocation_reason TEXT,
            PRIMARY KEY(project_id, employee_id),
            FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE RESTRICT,
            FOREIGN KEY(employee_id) REFERENCES employees(employee_id) ON DELETE RESTRICT
        )
        """,
        """
        CREATE TABLE service_principals (
            principal_id TEXT PRIMARY KEY CHECK(length(principal_id) > 0),
            principal_kind TEXT NOT NULL CHECK(principal_kind IN (
                'AI','MCP','WORKFLOW','SYSTEM'
            )),
            display_name TEXT NOT NULL CHECK(length(display_name) > 0),
            status TEXT NOT NULL CHECK(status IN ('ACTIVE','DISABLED')),
            registered_by_employee_id TEXT NOT NULL,
            registered_at TEXT NOT NULL,
            disabled_at TEXT,
            disabled_by TEXT,
            disable_reason TEXT,
            FOREIGN KEY(registered_by_employee_id)
                REFERENCES employees(employee_id) ON DELETE RESTRICT
        )
        """,
        """
        CREATE TABLE project_service_grants (
            project_id TEXT NOT NULL,
            principal_id TEXT NOT NULL,
            actions TEXT[] NOT NULL CHECK(
                cardinality(actions) > 0
                AND actions <@ ARRAY[
                    'PROJECT_READ','EVIDENCE_READ','EVIDENCE_WRITE','WORKING_WRITE',
                    'PROPOSAL_CREATE','TASK_READ','RUNTIME_START'
                ]::TEXT[]
            ),
            confidentiality_ceiling TEXT NOT NULL CHECK(
                confidentiality_ceiling IN ('P0','P1','P2','P3')
            ),
            status TEXT NOT NULL CHECK(status IN ('ACTIVE','REVOKED')),
            granted_by_employee_id TEXT NOT NULL,
            granted_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            revoked_at TEXT,
            revoked_by TEXT,
            revocation_reason TEXT,
            PRIMARY KEY(project_id, principal_id),
            FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE RESTRICT,
            FOREIGN KEY(principal_id)
                REFERENCES service_principals(principal_id) ON DELETE RESTRICT,
            FOREIGN KEY(granted_by_employee_id)
                REFERENCES employees(employee_id) ON DELETE RESTRICT
        )
        """,
        """
        CREATE TABLE project_authorization_events (
            global_position BIGSERIAL PRIMARY KEY,
            event_id TEXT NOT NULL UNIQUE CHECK(length(event_id) > 0),
            project_id TEXT NOT NULL,
            target_kind TEXT NOT NULL CHECK(target_kind IN (
                'EMPLOYEE','AI','MCP','WORKFLOW','SYSTEM'
            )),
            target_id TEXT NOT NULL CHECK(length(target_id) > 0),
            event_type TEXT NOT NULL CHECK(event_type IN (
                'OWNER_BOOTSTRAPPED','EMPLOYEE_GRANTED','EMPLOYEE_REVOKED',
                'SERVICE_GRANTED','SERVICE_REVOKED'
            )),
            role TEXT CHECK(role IS NULL OR role IN (
                'OWNER','MANAGER','EDITOR','REVIEWER','VIEWER'
            )),
            actions TEXT[],
            confidentiality_ceiling TEXT CHECK(
                confidentiality_ceiling IS NULL
                OR confidentiality_ceiling IN ('P0','P1','P2','P3')
            ),
            reason TEXT,
            actor_kind TEXT NOT NULL CHECK(actor_kind = 'EMPLOYEE'),
            actor_id TEXT NOT NULL CHECK(length(actor_id) > 0),
            occurred_at TEXT NOT NULL,
            FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE RESTRICT
        )
        """,
        "CREATE INDEX idx_project_memberships_employee ON project_memberships(employee_id, status, project_id)",
        "CREATE INDEX idx_project_service_grants_principal ON project_service_grants(principal_id, status, project_id)",
        "CREATE INDEX idx_project_authorization_events_project ON project_authorization_events(project_id, global_position)",
        """
        CREATE FUNCTION brand_os_confidentiality_rank(value TEXT)
        RETURNS INTEGER
        LANGUAGE SQL
        IMMUTABLE
        PARALLEL SAFE
        AS $$
            SELECT CASE value
                WHEN 'P0' THEN 0
                WHEN 'P1' THEN 1
                WHEN 'P2' THEN 2
                WHEN 'P3' THEN 3
                ELSE -1
            END
        $$
        """,
        """
        CREATE FUNCTION brand_os_has_project_action(
            row_project_id TEXT,
            required_action TEXT,
            row_confidentiality TEXT DEFAULT 'P0'
        )
        RETURNS BOOLEAN
        LANGUAGE plpgsql
        STABLE
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
        DECLARE
            principal_kind TEXT := current_setting('brand_os.principal_kind', true);
            principal_id TEXT := current_setting('brand_os.principal_id', true);
            selected_project_id TEXT := current_setting('brand_os.project_id', true);
            employee_role TEXT;
            allowed_actions TEXT[];
            ceiling TEXT;
        BEGIN
            IF principal_kind IS NULL OR principal_kind = ''
                OR principal_id IS NULL OR principal_id = ''
                OR selected_project_id IS NULL OR selected_project_id = ''
                OR selected_project_id <> row_project_id
                OR brand_os_confidentiality_rank(row_confidentiality) < 0
            THEN
                RETURN FALSE;
            END IF;

            IF principal_kind = 'EMPLOYEE' THEN
                SELECT membership.role, membership.confidentiality_ceiling
                INTO employee_role, ceiling
                FROM project_memberships AS membership
                JOIN employees AS employee
                  ON employee.employee_id = membership.employee_id
                WHERE membership.project_id = row_project_id
                  AND membership.employee_id = principal_id
                  AND membership.status = 'ACTIVE'
                  AND employee.status = 'ACTIVE';

                IF employee_role IS NULL THEN
                    RETURN FALSE;
                END IF;
                allowed_actions := CASE employee_role
                    WHEN 'OWNER' THEN ARRAY[
                        'PROJECT_READ','EVIDENCE_READ','EVIDENCE_WRITE','WORKING_WRITE',
                        'PROPOSAL_CREATE','PROPOSAL_REVIEW','TASK_READ','RUNTIME_START',
                        'ACCESS_MANAGE'
                    ]::TEXT[]
                    WHEN 'MANAGER' THEN ARRAY[
                        'PROJECT_READ','EVIDENCE_READ','EVIDENCE_WRITE','WORKING_WRITE',
                        'PROPOSAL_CREATE','PROPOSAL_REVIEW','TASK_READ','RUNTIME_START'
                    ]::TEXT[]
                    WHEN 'EDITOR' THEN ARRAY[
                        'PROJECT_READ','EVIDENCE_READ','EVIDENCE_WRITE','WORKING_WRITE',
                        'PROPOSAL_CREATE','TASK_READ','RUNTIME_START'
                    ]::TEXT[]
                    WHEN 'REVIEWER' THEN ARRAY[
                        'PROJECT_READ','EVIDENCE_READ','PROPOSAL_CREATE',
                        'PROPOSAL_REVIEW','TASK_READ'
                    ]::TEXT[]
                    WHEN 'VIEWER' THEN ARRAY[
                        'PROJECT_READ','EVIDENCE_READ','TASK_READ'
                    ]::TEXT[]
                    ELSE ARRAY[]::TEXT[]
                END;
            ELSIF principal_kind IN ('AI','MCP','WORKFLOW','SYSTEM') THEN
                SELECT grant_record.actions, grant_record.confidentiality_ceiling
                INTO allowed_actions, ceiling
                FROM project_service_grants AS grant_record
                JOIN service_principals AS service
                  ON service.principal_id = grant_record.principal_id
                WHERE grant_record.project_id = row_project_id
                  AND grant_record.principal_id = principal_id
                  AND grant_record.status = 'ACTIVE'
                  AND service.status = 'ACTIVE'
                  AND service.principal_kind = principal_kind;
            ELSE
                RETURN FALSE;
            END IF;

            RETURN required_action = ANY(COALESCE(allowed_actions, ARRAY[]::TEXT[]))
                AND brand_os_confidentiality_rank(row_confidentiality)
                    <= brand_os_confidentiality_rank(ceiling);
        END
        $$
        """,
        """
        CREATE FUNCTION brand_os_current_action_permitted(
            row_project_id TEXT,
            row_confidentiality TEXT,
            table_actions TEXT[]
        )
        RETURNS BOOLEAN
        LANGUAGE SQL
        STABLE
        AS $$
            SELECT current_setting('brand_os.action', true)
                    = ANY(COALESCE(table_actions, ARRAY[]::TEXT[]))
               AND brand_os_has_project_action(
                    row_project_id,
                    current_setting('brand_os.action', true),
                    row_confidentiality
               )
        $$
        """,
        "REVOKE ALL ON FUNCTION brand_os_confidentiality_rank(TEXT) FROM PUBLIC",
        "REVOKE ALL ON FUNCTION brand_os_has_project_action(TEXT, TEXT, TEXT) FROM PUBLIC",
        "REVOKE ALL ON FUNCTION brand_os_current_action_permitted(TEXT, TEXT, TEXT[]) FROM PUBLIC",
        """
        DO $$
        DECLARE
            table_record RECORD;
            confidentiality_expression TEXT;
            read_action TEXT;
            write_actions TEXT;
        BEGIN
            FOR table_record IN
                SELECT table_name
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND column_name = 'project_id'
                  AND table_name NOT IN (
                      'project_memberships',
                      'project_service_grants',
                      'project_authorization_events'
                  )
                ORDER BY table_name
            LOOP
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = table_record.table_name
                      AND column_name = 'confidentiality'
                ) THEN
                    confidentiality_expression := 'COALESCE(confidentiality, ''P0'')';
                ELSE
                    confidentiality_expression := '''P0''';
                END IF;

                IF table_record.table_name IN (
                    'sources','source_import_batches','source_contents','logical_sources',
                    'source_versions','source_aliases','source_version_relations','source_gaps',
                    'evidence_uploads','evidence_object_versions'
                ) THEN
                    read_action := 'EVIDENCE_READ';
                    write_actions := 'ARRAY[''EVIDENCE_WRITE'']::TEXT[]';
                ELSIF table_record.table_name IN (
                    'runtime_commands','runtime_tasks','runtime_mode_switches',
                    'task_packets','agent_runs'
                ) THEN
                    read_action := 'TASK_READ';
                    write_actions := 'ARRAY[''RUNTIME_START'']::TEXT[]';
                ELSIF table_record.table_name IN (
                    'human_actions','state_items','proposal_lifecycle_actions',
                    'proposal_supersessions'
                ) THEN
                    read_action := 'PROJECT_READ';
                    write_actions := 'ARRAY[''PROPOSAL_REVIEW'']::TEXT[]';
                ELSIF table_record.table_name IN ('proposals','proposal_lifecycle') THEN
                    read_action := 'PROJECT_READ';
                    write_actions := 'ARRAY[''PROPOSAL_CREATE'',''PROPOSAL_REVIEW'']::TEXT[]';
                ELSIF table_record.table_name IN ('projects','commands','events') THEN
                    read_action := 'PROJECT_READ';
                    write_actions := 'ARRAY[
                        ''EVIDENCE_WRITE'',''WORKING_WRITE'',''PROPOSAL_CREATE'',
                        ''PROPOSAL_REVIEW'',''RUNTIME_START'',''ACCESS_MANAGE''
                    ]::TEXT[]';
                ELSE
                    read_action := 'PROJECT_READ';
                    write_actions := 'ARRAY[''WORKING_WRITE'',''PROPOSAL_CREATE'']::TEXT[]';
                END IF;

                EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', table_record.table_name);
                EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', table_record.table_name);
                EXECUTE format(
                    'CREATE POLICY project_scope_select ON %I FOR SELECT USING '
                    || '(brand_os_has_project_action(project_id, %L, %s))',
                    table_record.table_name,
                    read_action,
                    confidentiality_expression
                );
                EXECUTE format(
                    'CREATE POLICY project_scope_insert ON %I FOR INSERT WITH CHECK '
                    || '(brand_os_current_action_permitted(project_id, %s, %s))',
                    table_record.table_name,
                    confidentiality_expression,
                    write_actions
                );
                EXECUTE format(
                    'CREATE POLICY project_scope_update ON %I FOR UPDATE USING '
                    || '(brand_os_current_action_permitted(project_id, %s, %s)) WITH CHECK '
                    || '(brand_os_current_action_permitted(project_id, %s, %s))',
                    table_record.table_name,
                    confidentiality_expression,
                    write_actions,
                    confidentiality_expression,
                    write_actions
                );
                EXECUTE format(
                    'CREATE POLICY project_scope_delete ON %I FOR DELETE USING '
                    || '(brand_os_current_action_permitted(project_id, %s, %s))',
                    table_record.table_name,
                    confidentiality_expression,
                    write_actions
                );
            END LOOP;
        END
        $$
        """,
        """
        ALTER TABLE proposal_evidence ENABLE ROW LEVEL SECURITY;
        ALTER TABLE proposal_evidence FORCE ROW LEVEL SECURITY;
        CREATE POLICY proposal_evidence_select ON proposal_evidence FOR SELECT USING (
            EXISTS (
                SELECT 1 FROM proposals
                WHERE proposals.proposal_id = proposal_evidence.proposal_id
            )
        );
        CREATE POLICY proposal_evidence_insert ON proposal_evidence FOR INSERT WITH CHECK (
            EXISTS (
                SELECT 1 FROM proposals
                WHERE proposals.proposal_id = proposal_evidence.proposal_id
                  AND brand_os_current_action_permitted(
                      proposals.project_id,
                      'P0',
                      ARRAY['PROPOSAL_CREATE','PROPOSAL_REVIEW']::TEXT[]
                  )
            )
        )
        """,
        """
        ALTER TABLE evidence_state_transitions ENABLE ROW LEVEL SECURITY;
        ALTER TABLE evidence_state_transitions FORCE ROW LEVEL SECURITY;
        CREATE POLICY evidence_transitions_select ON evidence_state_transitions FOR SELECT USING (
            EXISTS (
                SELECT 1 FROM evidence_uploads
                WHERE evidence_uploads.upload_id = evidence_state_transitions.upload_id
            )
        );
        CREATE POLICY evidence_transitions_insert ON evidence_state_transitions FOR INSERT WITH CHECK (
            EXISTS (
                SELECT 1 FROM evidence_uploads
                WHERE evidence_uploads.upload_id = evidence_state_transitions.upload_id
                  AND brand_os_current_action_permitted(
                      evidence_uploads.project_id,
                      evidence_uploads.confidentiality,
                      ARRAY['EVIDENCE_WRITE']::TEXT[]
                  )
            )
        )
        """,
        """
        ALTER TABLE evidence_object_tombstones ENABLE ROW LEVEL SECURITY;
        ALTER TABLE evidence_object_tombstones FORCE ROW LEVEL SECURITY;
        CREATE POLICY evidence_tombstones_select ON evidence_object_tombstones FOR SELECT USING (
            EXISTS (
                SELECT 1 FROM evidence_object_versions
                WHERE evidence_object_versions.version_id = evidence_object_tombstones.version_id
            )
        );
        CREATE POLICY evidence_tombstones_insert ON evidence_object_tombstones FOR INSERT WITH CHECK (
            EXISTS (
                SELECT 1 FROM evidence_object_versions
                WHERE evidence_object_versions.version_id = evidence_object_tombstones.version_id
                  AND brand_os_current_action_permitted(
                      evidence_object_versions.project_id,
                      evidence_object_versions.confidentiality,
                      ARRAY['EVIDENCE_WRITE']::TEXT[]
                  )
            )
        );
        CREATE POLICY evidence_tombstones_update ON evidence_object_tombstones FOR UPDATE USING (
            EXISTS (
                SELECT 1 FROM evidence_object_versions
                WHERE evidence_object_versions.version_id = evidence_object_tombstones.version_id
                  AND brand_os_current_action_permitted(
                      evidence_object_versions.project_id,
                      evidence_object_versions.confidentiality,
                      ARRAY['EVIDENCE_WRITE']::TEXT[]
                  )
            )
        ) WITH CHECK (
            EXISTS (
                SELECT 1 FROM evidence_object_versions
                WHERE evidence_object_versions.version_id = evidence_object_tombstones.version_id
                  AND brand_os_current_action_permitted(
                      evidence_object_versions.project_id,
                      evidence_object_versions.confidentiality,
                      ARRAY['EVIDENCE_WRITE']::TEXT[]
                  )
            )
        )
        """,
    ),
)


POSTGRESQL_AUDIT_OUTBOX_MIGRATION = Migration(
    10,
    "audit_outbox_inbox_background_boundary",
    (
        """
        CREATE TABLE audit_records (
            audit_id TEXT PRIMARY KEY CHECK(length(audit_id) > 0),
            project_id TEXT NOT NULL,
            event_id TEXT,
            audit_type TEXT NOT NULL CHECK(audit_type IN (
                'DOMAIN_EVENT','DELIVERY','REPLAY','DEAD_LETTER'
            )),
            operation TEXT NOT NULL CHECK(length(operation) > 0),
            outcome TEXT NOT NULL CHECK(outcome IN (
                'COMMITTED','REPLAYED','RETRY','FAILED','ACKNOWLEDGED'
            )),
            aggregate_type TEXT,
            aggregate_id TEXT,
            event_type TEXT,
            project_version INTEGER CHECK(project_version IS NULL OR project_version >= 0),
            aggregate_version INTEGER CHECK(aggregate_version IS NULL OR aggregate_version > 0),
            actor_kind TEXT NOT NULL CHECK(actor_kind IN ('HUMAN','AI','WORKFLOW','SYSTEM')),
            actor_id TEXT NOT NULL CHECK(length(actor_id) > 0),
            correlation_id TEXT,
            causation_id TEXT,
            idempotency_key TEXT,
            payload_digest TEXT CHECK(payload_digest IS NULL OR length(payload_digest) = 64),
            details_json TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE RESTRICT,
            FOREIGN KEY(event_id) REFERENCES events(event_id) ON DELETE RESTRICT
        )
        """,
        """
        CREATE TABLE outbox_consumers (
            consumer_name TEXT PRIMARY KEY CHECK(length(consumer_name) > 0),
            status TEXT NOT NULL CHECK(status IN ('ACTIVE','PAUSED','RETIRED')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE outbox_messages (
            message_id TEXT PRIMARY KEY CHECK(length(message_id) > 0),
            consumer_name TEXT NOT NULL,
            event_id TEXT NOT NULL,
            event_global_position BIGINT NOT NULL,
            project_id TEXT NOT NULL,
            aggregate_type TEXT NOT NULL,
            aggregate_id TEXT NOT NULL,
            aggregate_version INTEGER NOT NULL CHECK(aggregate_version > 0),
            event_type TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN (
                'PENDING','CLAIMED','RETRY','ACKED','DEAD_LETTER'
            )),
            attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
            available_at TEXT NOT NULL,
            claimed_by TEXT,
            lease_token TEXT,
            lease_until TEXT,
            last_error TEXT,
            dead_letter_reason TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            acked_at TEXT,
            FOREIGN KEY(consumer_name) REFERENCES outbox_consumers(consumer_name) ON DELETE RESTRICT,
            FOREIGN KEY(event_id) REFERENCES events(event_id) ON DELETE RESTRICT,
            FOREIGN KEY(event_global_position) REFERENCES events(global_position) ON DELETE RESTRICT,
            FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE RESTRICT,
            UNIQUE(consumer_name, event_id),
            UNIQUE(consumer_name, project_id, aggregate_type, aggregate_id, aggregate_version)
        )
        """,
        """
        CREATE TABLE inbox_messages (
            consumer_name TEXT NOT NULL,
            event_id TEXT NOT NULL,
            message_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('IN_PROGRESS','PROCESSED','IGNORED','FAILED')),
            attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
            result_json TEXT,
            last_error TEXT,
            first_seen_at TEXT NOT NULL,
            processed_at TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(consumer_name, event_id),
            FOREIGN KEY(consumer_name) REFERENCES outbox_consumers(consumer_name) ON DELETE RESTRICT,
            FOREIGN KEY(event_id) REFERENCES events(event_id) ON DELETE RESTRICT,
            FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE RESTRICT
        )
        """,
        """
        CREATE TABLE dead_letter_messages (
            dead_letter_id TEXT PRIMARY KEY CHECK(length(dead_letter_id) > 0),
            message_id TEXT NOT NULL,
            consumer_name TEXT NOT NULL,
            event_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            reason TEXT NOT NULL,
            error_message TEXT NOT NULL,
            attempts INTEGER NOT NULL CHECK(attempts > 0),
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            resolved_at TEXT,
            resolved_by TEXT,
            resolution TEXT,
            FOREIGN KEY(message_id) REFERENCES outbox_messages(message_id) ON DELETE RESTRICT,
            FOREIGN KEY(consumer_name) REFERENCES outbox_consumers(consumer_name) ON DELETE RESTRICT,
            FOREIGN KEY(event_id) REFERENCES events(event_id) ON DELETE RESTRICT,
            FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE RESTRICT
        )
        """,
        """
        CREATE TABLE background_worker_leases (
            consumer_name TEXT NOT NULL,
            worker_id TEXT NOT NULL CHECK(length(worker_id) > 0),
            lease_token TEXT NOT NULL CHECK(length(lease_token) > 0),
            acquired_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            heartbeat_at TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('ACTIVE','EXPIRED','RELEASED')),
            PRIMARY KEY(consumer_name, worker_id),
            FOREIGN KEY(consumer_name) REFERENCES outbox_consumers(consumer_name) ON DELETE RESTRICT
        )
        """,
        "CREATE INDEX idx_audit_records_project_time ON audit_records(project_id, occurred_at, audit_id)",
        "CREATE INDEX idx_audit_records_event ON audit_records(event_id, occurred_at)",
        "CREATE INDEX idx_outbox_messages_claimable ON outbox_messages(consumer_name, status, available_at, event_global_position)",
        "CREATE INDEX idx_outbox_messages_aggregate ON outbox_messages(consumer_name, project_id, aggregate_type, aggregate_id, aggregate_version)",
        "CREATE INDEX idx_inbox_messages_project_status ON inbox_messages(project_id, status, updated_at)",
        "CREATE INDEX idx_dead_letter_messages_project_time ON dead_letter_messages(project_id, created_at, dead_letter_id)",
        """
        INSERT INTO outbox_consumers(consumer_name, status, created_at, updated_at)
        VALUES ('default', 'ACTIVE', CURRENT_TIMESTAMP::TEXT, CURRENT_TIMESTAMP::TEXT)
        ON CONFLICT(consumer_name) DO NOTHING
        """,
        """
        ALTER TABLE audit_records ENABLE ROW LEVEL SECURITY;
        ALTER TABLE audit_records FORCE ROW LEVEL SECURITY;
        CREATE POLICY audit_records_select ON audit_records FOR SELECT USING (
            brand_os_has_project_action(project_id, 'PROJECT_READ', 'P0')
        );
        CREATE POLICY audit_records_insert ON audit_records FOR INSERT WITH CHECK (
            brand_os_current_action_permitted(
                project_id, 'P0',
                ARRAY[
                    'EVIDENCE_WRITE','WORKING_WRITE','PROPOSAL_CREATE',
                    'PROPOSAL_REVIEW','RUNTIME_START','ACCESS_MANAGE'
                ]::TEXT[]
            )
        );
        CREATE POLICY audit_records_update ON audit_records FOR UPDATE USING (
            brand_os_current_action_permitted(project_id, 'P0', ARRAY['RUNTIME_START']::TEXT[])
        ) WITH CHECK (
            brand_os_current_action_permitted(project_id, 'P0', ARRAY['RUNTIME_START']::TEXT[])
        )
        """,
        """
        ALTER TABLE outbox_messages ENABLE ROW LEVEL SECURITY;
        ALTER TABLE outbox_messages FORCE ROW LEVEL SECURITY;
        CREATE POLICY outbox_messages_select ON outbox_messages FOR SELECT USING (
            brand_os_has_project_action(project_id, 'PROJECT_READ', 'P0')
            OR brand_os_has_project_action(project_id, 'RUNTIME_START', 'P0')
        );
        CREATE POLICY outbox_messages_insert ON outbox_messages FOR INSERT WITH CHECK (
            brand_os_current_action_permitted(
                project_id, 'P0',
                ARRAY[
                    'EVIDENCE_WRITE','WORKING_WRITE','PROPOSAL_CREATE',
                    'PROPOSAL_REVIEW','RUNTIME_START','ACCESS_MANAGE'
                ]::TEXT[]
            )
        );
        CREATE POLICY outbox_messages_update ON outbox_messages FOR UPDATE USING (
            brand_os_current_action_permitted(
                project_id, 'P0',
                ARRAY[
                    'EVIDENCE_WRITE','WORKING_WRITE','PROPOSAL_CREATE',
                    'PROPOSAL_REVIEW','RUNTIME_START','ACCESS_MANAGE'
                ]::TEXT[]
            )
        ) WITH CHECK (
            brand_os_current_action_permitted(
                project_id, 'P0',
                ARRAY[
                    'EVIDENCE_WRITE','WORKING_WRITE','PROPOSAL_CREATE',
                    'PROPOSAL_REVIEW','RUNTIME_START','ACCESS_MANAGE'
                ]::TEXT[]
            )
        )
        """,
        """
        ALTER TABLE inbox_messages ENABLE ROW LEVEL SECURITY;
        ALTER TABLE inbox_messages FORCE ROW LEVEL SECURITY;
        CREATE POLICY inbox_messages_select ON inbox_messages FOR SELECT USING (
            brand_os_has_project_action(project_id, 'PROJECT_READ', 'P0')
            OR brand_os_has_project_action(project_id, 'RUNTIME_START', 'P0')
        );
        CREATE POLICY inbox_messages_insert ON inbox_messages FOR INSERT WITH CHECK (
            brand_os_current_action_permitted(project_id, 'P0', ARRAY['RUNTIME_START']::TEXT[])
        );
        CREATE POLICY inbox_messages_update ON inbox_messages FOR UPDATE USING (
            brand_os_current_action_permitted(project_id, 'P0', ARRAY['RUNTIME_START']::TEXT[])
        ) WITH CHECK (
            brand_os_current_action_permitted(project_id, 'P0', ARRAY['RUNTIME_START']::TEXT[])
        )
        """,
        """
        ALTER TABLE dead_letter_messages ENABLE ROW LEVEL SECURITY;
        ALTER TABLE dead_letter_messages FORCE ROW LEVEL SECURITY;
        CREATE POLICY dead_letter_messages_select ON dead_letter_messages FOR SELECT USING (
            brand_os_has_project_action(project_id, 'PROJECT_READ', 'P0')
            OR brand_os_has_project_action(project_id, 'RUNTIME_START', 'P0')
        );
        CREATE POLICY dead_letter_messages_insert ON dead_letter_messages FOR INSERT WITH CHECK (
            brand_os_current_action_permitted(project_id, 'P0', ARRAY['RUNTIME_START']::TEXT[])
        );
        CREATE POLICY dead_letter_messages_update ON dead_letter_messages FOR UPDATE USING (
            brand_os_current_action_permitted(project_id, 'P0', ARRAY['RUNTIME_START']::TEXT[])
        ) WITH CHECK (
            brand_os_current_action_permitted(project_id, 'P0', ARRAY['RUNTIME_START']::TEXT[])
        )
        """,
        """
        ALTER TABLE background_worker_leases ENABLE ROW LEVEL SECURITY;
        ALTER TABLE background_worker_leases FORCE ROW LEVEL SECURITY;
        CREATE POLICY background_worker_leases_all ON background_worker_leases
        USING (true) WITH CHECK (true)
        """,
    ),
)


POSTGRESQL_SHARED_RATE_LIMIT_MIGRATION = Migration(
    11,
    "shared_rate_limit_buckets",
    (
        """
        CREATE TABLE rate_limit_buckets (
            bucket_key TEXT NOT NULL CHECK(length(bucket_key) = 64),
            bucket_name TEXT NOT NULL CHECK(length(bucket_name) > 0),
            window_started_at TEXT NOT NULL,
            request_count INTEGER NOT NULL CHECK(request_count >= 0),
            updated_at TEXT NOT NULL,
            PRIMARY KEY(bucket_key, bucket_name)
        )
        """,
        "CREATE INDEX idx_rate_limit_buckets_updated_at ON rate_limit_buckets(updated_at)",
    ),
)
POSTGRESQL_RATE_LIMIT_MIGRATION = POSTGRESQL_SHARED_RATE_LIMIT_MIGRATION


POSTGRESQL_DATA_CUTOVER_MIGRATION = Migration(
    12,
    "one_time_data_cutover",
    (
        """
        CREATE TABLE data_cutover_runs (
            cutover_id TEXT PRIMARY KEY CHECK(length(cutover_id) > 0),
            manifest_sha256 TEXT NOT NULL UNIQUE CHECK(length(manifest_sha256) = 64),
            source_snapshot_sha256 TEXT NOT NULL CHECK(length(source_snapshot_sha256) = 64),
            source_schema_version INTEGER NOT NULL CHECK(source_schema_version >= 7),
            status TEXT NOT NULL CHECK(status IN ('PREPARED','ACTIVE','ROLLED_BACK')),
            operator_id TEXT NOT NULL CHECK(length(operator_id) > 0),
            table_count INTEGER NOT NULL CHECK(table_count > 0),
            row_count INTEGER NOT NULL CHECK(row_count >= 0),
            evidence_count INTEGER NOT NULL CHECK(evidence_count >= 0),
            report_json TEXT,
            started_at TEXT NOT NULL,
            activated_at TEXT,
            rolled_back_at TEXT,
            failure_reason TEXT
        )
        """,
        """
        CREATE UNIQUE INDEX idx_data_cutover_single_active
        ON data_cutover_runs((1))
        WHERE status = 'ACTIVE'
        """,
        """
        CREATE TABLE data_cutover_source_evidence (
            cutover_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            source_version_id TEXT NOT NULL,
            evidence_version_id TEXT NOT NULL UNIQUE,
            sha256 TEXT NOT NULL CHECK(length(sha256) = 64),
            object_version_id TEXT NOT NULL CHECK(length(object_version_id) > 0),
            created_at TEXT NOT NULL,
            PRIMARY KEY(cutover_id, project_id, source_version_id),
            FOREIGN KEY(cutover_id) REFERENCES data_cutover_runs(cutover_id) ON DELETE RESTRICT,
            FOREIGN KEY(project_id, source_version_id)
                REFERENCES source_versions(project_id, source_version_id) ON DELETE RESTRICT,
            FOREIGN KEY(evidence_version_id)
                REFERENCES evidence_object_versions(version_id) ON DELETE RESTRICT
        )
        """,
        "CREATE INDEX idx_data_cutover_evidence_project ON data_cutover_source_evidence(project_id, source_version_id)",
        """
        ALTER TABLE data_cutover_source_evidence ENABLE ROW LEVEL SECURITY;
        ALTER TABLE data_cutover_source_evidence FORCE ROW LEVEL SECURITY;
        CREATE POLICY data_cutover_source_evidence_select
        ON data_cutover_source_evidence FOR SELECT USING (
            brand_os_has_project_action(project_id, 'PROJECT_READ', 'P0')
        )
        """,
    ),
)


POSTGRESQL_MIGRATIONS = (
    *_SHARED_POSTGRESQL_MIGRATIONS,
    POSTGRESQL_OBJECT_EVIDENCE_MIGRATION,
    POSTGRESQL_OIDC_IDENTITY_MIGRATION,
    POSTGRESQL_PROJECT_AUTHORIZATION_MIGRATION,
    POSTGRESQL_AUDIT_OUTBOX_MIGRATION,
    POSTGRESQL_SHARED_RATE_LIMIT_MIGRATION,
    POSTGRESQL_DATA_CUTOVER_MIGRATION,
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
    "POSTGRESQL_PROJECT_AUTHORIZATION_MIGRATION",
    "POSTGRESQL_AUDIT_OUTBOX_MIGRATION",
    "POSTGRESQL_SHARED_RATE_LIMIT_MIGRATION",
    "POSTGRESQL_RATE_LIMIT_MIGRATION",
    "POSTGRESQL_SCHEMA_VERSION",
    "apply_postgresql_migrations",
    "POSTGRESQL_DATA_CUTOVER_MIGRATION",
]
