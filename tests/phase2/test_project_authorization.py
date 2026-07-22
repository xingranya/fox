"""项目 RBAC、保密级别和 PostgreSQL RLS 集成测试。"""

from __future__ import annotations

import hashlib
import json
import unittest
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import psycopg
from cryptography.fernet import Fernet
from psycopg import sql

from brand_os.authorization import (
    AuthorizationConflict,
    ConfidentialityLevel,
    PrincipalKind,
    ProjectAccessDenied,
    ProjectAction,
    ProjectAuthorizationService,
    ProjectPrincipal,
    ProjectRole,
    ROLE_ACTIONS,
)
from brand_os.domain import Actor, ActorKind, CommandContext, SourceRecord
from brand_os.postgresql_authorization import (
    PROJECT_AUTHORIZATION_TABLES,
    PostgreSQLProjectAuthorizationRepository,
    authorized_project_transaction,
    grant_project_runtime_role,
)
from brand_os.postgresql_identity import PostgreSQLIdentityRepository
from brand_os.postgresql_store import PostgreSQLCanonicalStore
from brand_os.secret_cipher import FernetSecretCipher
from phase2.postgresql_test_runtime import TemporaryPostgreSQL


ROOT = Path(__file__).parents[2]
CONTRACT_PATH = ROOT / "contracts" / "phase2" / "project-authorization.json"
POSTGRESQL: TemporaryPostgreSQL | None = None
RUNTIME_ROLE = f"brand_os_runtime_{uuid4().hex}"


def setUpModule() -> None:
    """启动隔离 PostgreSQL，并创建无 RLS 旁路权的运行时角色。"""

    global POSTGRESQL
    try:
        POSTGRESQL = TemporaryPostgreSQL()
    except RuntimeError as error:
        raise unittest.SkipTest(str(error)) from error
    POSTGRESQL.start()
    with psycopg.connect(POSTGRESQL.admin_dsn, autocommit=True) as connection:
        connection.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS"
            ).format(sql.Identifier(RUNTIME_ROLE))
        )


def tearDownModule() -> None:
    """删除测试角色并停止临时 PostgreSQL。"""

    if POSTGRESQL is None:
        return
    with psycopg.connect(POSTGRESQL.admin_dsn, autocommit=True) as connection:
        connection.execute(
            sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(RUNTIME_ROLE))
        )
    POSTGRESQL.stop()


class ProjectAuthorizationContractTest(unittest.TestCase):
    """冻结 F2.5 的身份、批准和 RLS 边界。"""

    def test_contract_requires_application_authorization_and_service_denials(
        self,
    ) -> None:
        contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))

        self.assertEqual(contract["schema_version"], "project-authorization.v1")
        self.assertTrue(contract["authority"]["application_authorization_required"])
        self.assertTrue(contract["authority"]["rls_is_defense_in_depth"])
        self.assertFalse(contract["authority"]["service_may_review_proposal"])
        self.assertFalse(contract["authority"]["service_may_manage_access"])
        self.assertTrue(contract["rls"]["forced_on_project_tables"])
        self.assertFalse(contract["rls"]["runtime_role_may_bypass_rls"])
        self.assertEqual(contract["storage"]["postgresql_schema_version"], 10)
        self.assertNotIn("outbox", contract["deferred"])
        self.assertFalse(contract["migrates_hongri_data"])


class ProjectAuthorizationStoreTest(unittest.TestCase):
    """用真实非所有者连接验证应用判权与 RLS 纵深防线。"""

    def setUp(self) -> None:
        assert POSTGRESQL is not None
        self.database_name, self.dsn = POSTGRESQL.create_database()
        self.canonical = PostgreSQLCanonicalStore(self.dsn)
        self.identity = PostgreSQLIdentityRepository(
            self.dsn,
            cipher=FernetSecretCipher(Fernet.generate_key().decode("ascii")),
        )
        self.repository = PostgreSQLProjectAuthorizationRepository(self.dsn)
        self.service = ProjectAuthorizationService(self.repository)
        self.now = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)
        self.fox_actor = Actor(ActorKind.HUMAN, "Fox")
        self.fox = ProjectPrincipal(PrincipalKind.EMPLOYEE, "Fox")
        self.alice = ProjectPrincipal(PrincipalKind.EMPLOYEE, "Alice")
        self.bob = ProjectPrincipal(PrincipalKind.EMPLOYEE, "Bob")
        for employee_id in ("Fox", "Alice", "Bob"):
            self.identity.register_employee(
                employee_id=employee_id,
                display_name=employee_id,
                primary_email=f"{employee_id.lower()}@example.test",
                actor=self.fox_actor,
                occurred_at=self.now,
            )
        self._create_project("project-a", "项目 A")
        self._create_project("project-b", "项目 B")
        self.service.bootstrap_owner(
            self.fox,
            project_id="project-a",
            confidentiality_ceiling=ConfidentialityLevel.P3,
            occurred_at=self.now,
        )
        self.service.bootstrap_owner(
            self.fox,
            project_id="project-b",
            confidentiality_ceiling=ConfidentialityLevel.P3,
            occurred_at=self.now,
        )
        grant_project_runtime_role(self.dsn, RUNTIME_ROLE)
        self.runtime_dsn = f"postgresql://{RUNTIME_ROLE}@127.0.0.1:{POSTGRESQL.port}/{self.database_name}"

    def tearDown(self) -> None:
        assert POSTGRESQL is not None
        POSTGRESQL.drop_database(self.database_name)

    def _create_project(self, project_id: str, name: str) -> None:
        self.canonical.create_project(
            CommandContext(
                project_id=project_id,
                actor=self.fox_actor,
                idempotency_key=f"create-{project_id}",
                expected_version=0,
            ),
            name,
        )

    def _register_source(
        self,
        project_id: str,
        source_id: str,
        confidentiality: str,
    ) -> None:
        content = f"{project_id}:{source_id}:{confidentiality}".encode()
        self.canonical.register_source(
            CommandContext(
                project_id=project_id,
                actor=self.fox_actor,
                idempotency_key=f"source-{source_id}",
                expected_version=self.canonical.get_project_version(project_id),
            ),
            SourceRecord(
                source_id=source_id,
                sha256=hashlib.sha256(content).hexdigest(),
                size=len(content),
                relative_path=f"sources/{source_id}.md",
                source_role="working_source",
                confidentiality=confidentiality,
            ),
        )

    def test_schema_v10_tables_and_forced_rls_are_complete(self) -> None:
        self.assertEqual(self.repository.schema_version, 10)
        self.assertTrue(self.repository.quick_check())
        with psycopg.connect(self.dsn, autocommit=True) as connection:
            rows = connection.execute(
                """
                SELECT column_record.table_name,
                       class.relrowsecurity,
                       class.relforcerowsecurity
                FROM information_schema.columns AS column_record
                JOIN pg_class AS class ON class.relname = column_record.table_name
                JOIN pg_namespace AS namespace ON namespace.oid = class.relnamespace
                WHERE column_record.table_schema = current_schema()
                  AND namespace.nspname = current_schema()
                  AND column_record.column_name = 'project_id'
                  AND column_record.table_name NOT IN (
                    'project_memberships',
                    'project_service_grants',
                    'project_authorization_events'
                  )
                ORDER BY column_record.table_name
                """
            ).fetchall()
            self.assertGreater(len(rows), 20)
            self.assertTrue(all(bool(row[1]) and bool(row[2]) for row in rows))
            for table_name in PROJECT_AUTHORIZATION_TABLES:
                privilege = connection.execute(
                    "SELECT has_table_privilege(%s, %s, 'SELECT')",
                    (RUNTIME_ROLE, table_name),
                ).fetchone()[0]
                self.assertFalse(privilege, table_name)

    def test_role_action_matrix_and_confidentiality_ceiling(self) -> None:
        expected = {
            ProjectRole.OWNER: set(ProjectAction),
            ProjectRole.MANAGER: set(ProjectAction) - {ProjectAction.ACCESS_MANAGE},
            ProjectRole.EDITOR: {
                ProjectAction.PROJECT_READ,
                ProjectAction.EVIDENCE_READ,
                ProjectAction.EVIDENCE_WRITE,
                ProjectAction.WORKING_WRITE,
                ProjectAction.PROPOSAL_CREATE,
                ProjectAction.TASK_READ,
                ProjectAction.RUNTIME_START,
            },
            ProjectRole.REVIEWER: {
                ProjectAction.PROJECT_READ,
                ProjectAction.EVIDENCE_READ,
                ProjectAction.PROPOSAL_CREATE,
                ProjectAction.PROPOSAL_REVIEW,
                ProjectAction.TASK_READ,
            },
            ProjectRole.VIEWER: {
                ProjectAction.PROJECT_READ,
                ProjectAction.EVIDENCE_READ,
                ProjectAction.TASK_READ,
            },
        }
        self.assertEqual(
            {role: set(actions) for role, actions in ROLE_ACTIONS.items()}, expected
        )

        self.service.grant_employee(
            self.fox,
            project_id="project-a",
            employee_id="Alice",
            role=ProjectRole.REVIEWER,
            confidentiality_ceiling=ConfidentialityLevel.P1,
            occurred_at=self.now,
        )
        self.service.authorize(
            self.alice,
            project_id="project-a",
            action=ProjectAction.PROPOSAL_REVIEW,
            resource_confidentiality=ConfidentialityLevel.P1,
        )
        with self.assertRaises(ProjectAccessDenied):
            self.service.authorize(
                self.alice,
                project_id="project-a",
                action=ProjectAction.EVIDENCE_WRITE,
            )
        with self.assertRaises(ProjectAccessDenied):
            self.service.authorize(
                self.alice,
                project_id="project-a",
                action=ProjectAction.EVIDENCE_READ,
                resource_confidentiality=ConfidentialityLevel.P2,
            )

    def test_cross_project_and_revoked_membership_are_denied(self) -> None:
        self.service.grant_employee(
            self.fox,
            project_id="project-a",
            employee_id="Bob",
            role=ProjectRole.VIEWER,
            confidentiality_ceiling=ConfidentialityLevel.P2,
            occurred_at=self.now,
        )
        self.service.authorize(
            self.bob,
            project_id="project-a",
            action=ProjectAction.PROJECT_READ,
        )
        with self.assertRaises(ProjectAccessDenied):
            self.service.authorize(
                self.bob,
                project_id="project-b",
                action=ProjectAction.PROJECT_READ,
            )
        self.service.revoke_employee(
            self.fox,
            project_id="project-a",
            employee_id="Bob",
            reason="退出项目",
            occurred_at=self.now,
        )
        with self.assertRaises(ProjectAccessDenied):
            self.service.authorize(
                self.bob,
                project_id="project-a",
                action=ProjectAction.PROJECT_READ,
            )

    def test_last_owner_cannot_be_revoked_or_demoted(self) -> None:
        with self.assertRaisesRegex(AuthorizationConflict, "最后一个负责人"):
            self.service.revoke_employee(
                self.fox,
                project_id="project-a",
                employee_id="Fox",
                reason="错误操作",
                occurred_at=self.now,
            )
        with self.assertRaisesRegex(AuthorizationConflict, "最后一个负责人"):
            self.service.grant_employee(
                self.fox,
                project_id="project-a",
                employee_id="Fox",
                role=ProjectRole.MANAGER,
                confidentiality_ceiling=ConfidentialityLevel.P3,
                occurred_at=self.now,
            )

    def test_service_identity_cannot_receive_or_execute_human_approval(self) -> None:
        with self.assertRaisesRegex(AuthorizationConflict, "不能复用员工 ID"):
            self.service.register_service(
                self.fox,
                project_id="project-a",
                service_principal=ProjectPrincipal(PrincipalKind.MCP, "Fox"),
                display_name="错误复用",
                occurred_at=self.now,
            )
        mcp = ProjectPrincipal(PrincipalKind.MCP, "mcp-codex")
        self.service.register_service(
            self.fox,
            project_id="project-a",
            service_principal=mcp,
            display_name="Codex MCP",
            occurred_at=self.now,
        )
        with self.assertRaisesRegex(ProjectAccessDenied, "PROPOSAL_REVIEW"):
            self.service.grant_service(
                self.fox,
                project_id="project-a",
                service_principal=mcp,
                actions={ProjectAction.PROPOSAL_REVIEW},
                confidentiality_ceiling=ConfidentialityLevel.P1,
                occurred_at=self.now,
            )
        self.service.grant_service(
            self.fox,
            project_id="project-a",
            service_principal=mcp,
            actions={
                ProjectAction.PROJECT_READ,
                ProjectAction.EVIDENCE_READ,
                ProjectAction.PROPOSAL_CREATE,
            },
            confidentiality_ceiling=ConfidentialityLevel.P1,
            occurred_at=self.now,
        )
        with self.assertRaises(ProjectAccessDenied):
            self.service.authorize(
                mcp,
                project_id="project-a",
                action=ProjectAction.PROPOSAL_REVIEW,
            )
        with authorized_project_transaction(
            authorization_service=self.service,
            runtime_dsn=self.runtime_dsn,
            principal=mcp,
            project_id="project-a",
            action=ProjectAction.PROPOSAL_CREATE,
        ) as connection:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                connection.execute(
                    """
                    INSERT INTO human_actions(
                        action_id, project_id, proposal_id, action, actor_id,
                        reason, before_json, after_json, evidence_json,
                        base_state_version, event_id, acted_at
                    ) VALUES (
                        'forged-action', 'project-a', 'missing-proposal', 'approve',
                        'mcp-codex', '伪造批准', NULL, '{}', '[]', 0,
                        'missing-event', ?
                    )
                    """,
                    (self.now.isoformat(),),
                )

    def test_rls_filters_project_and_confidentiality_for_runtime_role(self) -> None:
        self._register_source("project-a", "source-p1", "P1")
        self._register_source("project-a", "source-p3", "P3")
        self._register_source("project-b", "source-other", "P0")
        self.service.grant_employee(
            self.fox,
            project_id="project-a",
            employee_id="Alice",
            role=ProjectRole.VIEWER,
            confidentiality_ceiling=ConfidentialityLevel.P1,
            occurred_at=self.now,
        )

        with psycopg.connect(self.runtime_dsn, autocommit=True) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0], 0
            )

        with authorized_project_transaction(
            authorization_service=self.service,
            runtime_dsn=self.runtime_dsn,
            principal=self.alice,
            project_id="project-a",
            action=ProjectAction.EVIDENCE_READ,
            resource_confidentiality=ConfidentialityLevel.P1,
        ) as connection:
            projects = connection.execute(
                "SELECT project_id FROM projects ORDER BY project_id"
            ).fetchall()
            sources = connection.execute(
                "SELECT source_id FROM sources ORDER BY source_id"
            ).fetchall()
            self.assertEqual([row[0] for row in projects], ["project-a"])
            self.assertEqual([row[0] for row in sources], ["source-p1"])

    def test_rls_blocks_cross_project_write_even_for_editor(self) -> None:
        self.service.grant_employee(
            self.fox,
            project_id="project-a",
            employee_id="Alice",
            role=ProjectRole.EDITOR,
            confidentiality_ceiling=ConfidentialityLevel.P2,
            occurred_at=self.now,
        )
        with authorized_project_transaction(
            authorization_service=self.service,
            runtime_dsn=self.runtime_dsn,
            principal=self.alice,
            project_id="project-a",
            action=ProjectAction.WORKING_WRITE,
        ) as connection:
            updated = connection.execute(
                "UPDATE projects SET name = '越权改名' WHERE project_id = 'project-b'"
            )
            self.assertEqual(updated.rowcount, 0)
        self.assertEqual(self.canonical.get_project("project-b")["name"], "项目 B")


if __name__ == "__main__":
    unittest.main()
