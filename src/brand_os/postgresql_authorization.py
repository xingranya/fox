"""项目 RBAC、服务授权和 PostgreSQL RLS 事务适配器。"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Iterator
from uuid import uuid4

import psycopg
from psycopg import sql

from .authorization import (
    AuthorizationConflict,
    ConfidentialityLevel,
    EmployeeProjectGrant,
    PrincipalKind,
    ProjectAction,
    ProjectAuthorizationService,
    ProjectPrincipal,
    ProjectRole,
    ServiceProjectGrant,
    SERVICE_ACTIONS,
)
from .postgresql_store import PostgreSQLConnection, PostgreSQLStoreBase


PROJECT_AUTHORIZATION_TABLES = frozenset(
    {
        "project_memberships",
        "service_principals",
        "project_service_grants",
        "project_authorization_events",
    }
)


class PostgreSQLProjectAuthorizationRepository(PostgreSQLStoreBase):
    """持久化项目成员、服务身份、撤权状态和授权事件。"""

    def quick_check(self) -> bool:
        """核对授权表、RLS 函数和项目表强制策略。"""

        if not super().quick_check():
            return False
        with self._connect() as connection:
            tables = {
                str(row[0])
                for row in connection.execute(
                    """
                    SELECT table_name FROM information_schema.tables
                    WHERE table_schema = current_schema()
                      AND table_name IN (?, ?, ?, ?)
                    """,
                    tuple(sorted(PROJECT_AUTHORIZATION_TABLES)),
                )
            }
            functions = {
                str(row[0])
                for row in connection.execute(
                    """
                    SELECT routine_name FROM information_schema.routines
                    WHERE routine_schema = current_schema()
                      AND routine_name IN (
                        'brand_os_confidentiality_rank',
                        'brand_os_has_project_action',
                        'brand_os_current_action_permitted'
                      )
                    """
                )
            }
            rls = connection.execute(
                """
                SELECT relrowsecurity, relforcerowsecurity
                FROM pg_class
                WHERE oid = 'projects'::regclass
                """
            ).fetchone()
        return (
            tables == PROJECT_AUTHORIZATION_TABLES
            and functions
            == {
                "brand_os_confidentiality_rank",
                "brand_os_has_project_action",
                "brand_os_current_action_permitted",
            }
            and rls is not None
            and bool(rls[0])
            and bool(rls[1])
        )

    def get_employee_grant(
        self, project_id: str, employee_id: str
    ) -> EmployeeProjectGrant | None:
        """读取员工在指定项目的角色。"""

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT project_id, employee_id, role, confidentiality_ceiling, status
                FROM project_memberships
                WHERE project_id = ? AND employee_id = ?
                """,
                (project_id, employee_id),
            ).fetchone()
        return _employee_grant(row) if row is not None else None

    def get_service_grant(
        self, project_id: str, principal: ProjectPrincipal
    ) -> ServiceProjectGrant | None:
        """读取服务身份的项目动作白名单。"""

        if principal.kind is PrincipalKind.EMPLOYEE:
            return None
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT grant_record.project_id, grant_record.principal_id,
                       service.principal_kind, grant_record.actions,
                       grant_record.confidentiality_ceiling,
                       grant_record.status, service.status AS principal_status
                FROM project_service_grants AS grant_record
                JOIN service_principals AS service
                  ON service.principal_id = grant_record.principal_id
                WHERE grant_record.project_id = ?
                  AND grant_record.principal_id = ?
                  AND service.principal_kind = ?
                """,
                (project_id, principal.principal_id, principal.kind.value),
            ).fetchone()
        return _service_grant(row) if row is not None else None

    def bootstrap_owner(
        self,
        *,
        project_id: str,
        employee_id: str,
        confidentiality_ceiling: ConfidentialityLevel,
        occurred_at: datetime,
    ) -> EmployeeProjectGrant:
        """只允许项目创建员工建立首个 OWNER。"""

        occurred = occurred_at.isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN")
            try:
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(?, 0))",
                    (f"project-owner:{project_id}",),
                )
                existing = connection.execute(
                    """
                    SELECT project_id, employee_id, role, confidentiality_ceiling, status
                    FROM project_memberships
                    WHERE project_id = ? AND employee_id = ?
                    FOR UPDATE
                    """,
                    (project_id, employee_id),
                ).fetchone()
                if existing is not None:
                    grant = _employee_grant(existing)
                    if (
                        grant.role is ProjectRole.OWNER
                        and grant.confidentiality_ceiling is confidentiality_ceiling
                        and grant.active
                    ):
                        connection.execute("COMMIT")
                        return grant
                    raise AuthorizationConflict("项目首位负责人已经建立")

                membership_count = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM project_memberships WHERE project_id = ?",
                        (project_id,),
                    ).fetchone()[0]
                )
                if membership_count:
                    raise AuthorizationConflict(
                        "项目已经存在成员，不能再次建立首位负责人"
                    )
                creator = connection.execute(
                    """
                    SELECT event.actor_kind, event.actor_id
                    FROM events AS event
                    JOIN employees AS employee ON employee.employee_id = event.actor_id
                    WHERE event.project_id = ?
                      AND event.project_version = 1
                      AND event.event_type = 'PROJECT_CREATED'
                      AND employee.status = 'ACTIVE'
                    """,
                    (project_id,),
                ).fetchone()
                if (
                    creator is None
                    or str(creator["actor_kind"]) != "HUMAN"
                    or str(creator["actor_id"]) != employee_id
                ):
                    raise AuthorizationConflict("只有项目创建员工可以成为首位负责人")
                connection.execute(
                    """
                    INSERT INTO project_memberships(
                        project_id, employee_id, role, confidentiality_ceiling,
                        status, granted_by_kind, granted_by_id, granted_at, updated_at
                    ) VALUES (?, ?, 'OWNER', ?, 'ACTIVE', 'EMPLOYEE', ?, ?, ?)
                    """,
                    (
                        project_id,
                        employee_id,
                        confidentiality_ceiling.value,
                        employee_id,
                        occurred,
                        occurred,
                    ),
                )
                self._insert_event(
                    connection,
                    project_id=project_id,
                    target=ProjectPrincipal(PrincipalKind.EMPLOYEE, employee_id),
                    event_type="OWNER_BOOTSTRAPPED",
                    role=ProjectRole.OWNER,
                    actions=None,
                    confidentiality_ceiling=confidentiality_ceiling,
                    reason=None,
                    actor=ProjectPrincipal(PrincipalKind.EMPLOYEE, employee_id),
                    occurred=occurred,
                )
                row = connection.execute(
                    """
                    SELECT project_id, employee_id, role, confidentiality_ceiling, status
                    FROM project_memberships
                    WHERE project_id = ? AND employee_id = ?
                    """,
                    (project_id, employee_id),
                ).fetchone()
                connection.execute("COMMIT")
                return _employee_grant(row)
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def upsert_employee_grant(
        self,
        *,
        project_id: str,
        employee_id: str,
        role: ProjectRole,
        confidentiality_ceiling: ConfidentialityLevel,
        granted_by: ProjectPrincipal,
        occurred_at: datetime,
    ) -> EmployeeProjectGrant:
        """新增或调整员工角色，并保留授权事件。"""

        _require_employee_actor(granted_by)
        occurred = occurred_at.isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN")
            try:
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(?, 0))",
                    (f"project-member:{project_id}:{employee_id}",),
                )
                employee = connection.execute(
                    "SELECT status FROM employees WHERE employee_id = ? FOR UPDATE",
                    (employee_id,),
                ).fetchone()
                if employee is None or str(employee["status"]) != "ACTIVE":
                    raise AuthorizationConflict("只能授权已启用的预登记员工")
                current = connection.execute(
                    """
                    SELECT role, status FROM project_memberships
                    WHERE project_id = ? AND employee_id = ? FOR UPDATE
                    """,
                    (project_id, employee_id),
                ).fetchone()
                if (
                    current is not None
                    and str(current["role"]) == ProjectRole.OWNER.value
                    and role is not ProjectRole.OWNER
                    and str(current["status"]) == "ACTIVE"
                ):
                    self._require_another_owner(connection, project_id, employee_id)
                connection.execute(
                    """
                    INSERT INTO project_memberships(
                        project_id, employee_id, role, confidentiality_ceiling,
                        status, granted_by_kind, granted_by_id, granted_at, updated_at,
                        revoked_at, revoked_by, revocation_reason
                    ) VALUES (?, ?, ?, ?, 'ACTIVE', 'EMPLOYEE', ?, ?, ?, NULL, NULL, NULL)
                    ON CONFLICT(project_id, employee_id) DO UPDATE SET
                        role = excluded.role,
                        confidentiality_ceiling = excluded.confidentiality_ceiling,
                        status = 'ACTIVE',
                        granted_by_kind = 'EMPLOYEE',
                        granted_by_id = excluded.granted_by_id,
                        updated_at = excluded.updated_at,
                        revoked_at = NULL,
                        revoked_by = NULL,
                        revocation_reason = NULL
                    """,
                    (
                        project_id,
                        employee_id,
                        role.value,
                        confidentiality_ceiling.value,
                        granted_by.principal_id,
                        occurred,
                        occurred,
                    ),
                )
                self._insert_event(
                    connection,
                    project_id=project_id,
                    target=ProjectPrincipal(PrincipalKind.EMPLOYEE, employee_id),
                    event_type="EMPLOYEE_GRANTED",
                    role=role,
                    actions=None,
                    confidentiality_ceiling=confidentiality_ceiling,
                    reason=None,
                    actor=granted_by,
                    occurred=occurred,
                )
                row = connection.execute(
                    """
                    SELECT project_id, employee_id, role, confidentiality_ceiling, status
                    FROM project_memberships
                    WHERE project_id = ? AND employee_id = ?
                    """,
                    (project_id, employee_id),
                ).fetchone()
                connection.execute("COMMIT")
                return _employee_grant(row)
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def register_service_principal(
        self,
        principal: ProjectPrincipal,
        *,
        display_name: str,
        registered_by: ProjectPrincipal,
        occurred_at: datetime,
    ) -> None:
        """登记全局唯一服务身份，重复登记不得改变类型。"""

        _require_employee_actor(registered_by)
        if principal.kind is PrincipalKind.EMPLOYEE:
            raise AuthorizationConflict("员工不能登记为服务身份")
        if not display_name.strip():
            raise ValueError("display_name 不能为空")
        occurred = occurred_at.isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN")
            try:
                existing = connection.execute(
                    """
                    SELECT principal_kind, display_name, status
                    FROM service_principals WHERE principal_id = ? FOR UPDATE
                    """,
                    (principal.principal_id,),
                ).fetchone()
                if existing is not None:
                    if (
                        str(existing["principal_kind"]) != principal.kind.value
                        or str(existing["display_name"]) != display_name
                        or str(existing["status"]) != "ACTIVE"
                    ):
                        raise AuthorizationConflict("同一服务 ID 不能静默改变身份资料")
                    connection.execute("COMMIT")
                    return
                employee_collision = connection.execute(
                    "SELECT 1 FROM employees WHERE employee_id = ?",
                    (principal.principal_id,),
                ).fetchone()
                if employee_collision is not None:
                    raise AuthorizationConflict("服务 ID 不能复用员工 ID")
                connection.execute(
                    """
                    INSERT INTO service_principals(
                        principal_id, principal_kind, display_name, status,
                        registered_by_employee_id, registered_at
                    ) VALUES (?, ?, ?, 'ACTIVE', ?, ?)
                    """,
                    (
                        principal.principal_id,
                        principal.kind.value,
                        display_name,
                        registered_by.principal_id,
                        occurred,
                    ),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def upsert_service_grant(
        self,
        *,
        project_id: str,
        principal: ProjectPrincipal,
        actions: frozenset[ProjectAction],
        confidentiality_ceiling: ConfidentialityLevel,
        granted_by: ProjectPrincipal,
        occurred_at: datetime,
    ) -> ServiceProjectGrant:
        """保存服务身份的显式项目动作白名单。"""

        _require_employee_actor(granted_by)
        if principal.kind is PrincipalKind.EMPLOYEE:
            raise AuthorizationConflict("员工不能使用服务授权")
        prohibited = actions - SERVICE_ACTIONS[principal.kind]
        if prohibited:
            names = ", ".join(sorted(action.value for action in prohibited))
            raise AuthorizationConflict(f"服务身份禁止获得动作：{names}")
        occurred = occurred_at.isoformat()
        action_values = sorted(action.value for action in actions)
        with self._connect() as connection:
            connection.execute("BEGIN")
            try:
                service = connection.execute(
                    """
                    SELECT principal_kind, status FROM service_principals
                    WHERE principal_id = ? FOR UPDATE
                    """,
                    (principal.principal_id,),
                ).fetchone()
                if (
                    service is None
                    or str(service["principal_kind"]) != principal.kind.value
                    or str(service["status"]) != "ACTIVE"
                ):
                    raise AuthorizationConflict("服务身份不存在、类型不符或已停用")
                connection.execute(
                    """
                    INSERT INTO project_service_grants(
                        project_id, principal_id, actions, confidentiality_ceiling,
                        status, granted_by_employee_id, granted_at, updated_at,
                        revoked_at, revoked_by, revocation_reason
                    ) VALUES (?, ?, ?, ?, 'ACTIVE', ?, ?, ?, NULL, NULL, NULL)
                    ON CONFLICT(project_id, principal_id) DO UPDATE SET
                        actions = excluded.actions,
                        confidentiality_ceiling = excluded.confidentiality_ceiling,
                        status = 'ACTIVE',
                        granted_by_employee_id = excluded.granted_by_employee_id,
                        updated_at = excluded.updated_at,
                        revoked_at = NULL,
                        revoked_by = NULL,
                        revocation_reason = NULL
                    """,
                    (
                        project_id,
                        principal.principal_id,
                        action_values,
                        confidentiality_ceiling.value,
                        granted_by.principal_id,
                        occurred,
                        occurred,
                    ),
                )
                self._insert_event(
                    connection,
                    project_id=project_id,
                    target=principal,
                    event_type="SERVICE_GRANTED",
                    role=None,
                    actions=actions,
                    confidentiality_ceiling=confidentiality_ceiling,
                    reason=None,
                    actor=granted_by,
                    occurred=occurred,
                )
                row = connection.execute(
                    """
                    SELECT grant_record.project_id, grant_record.principal_id,
                           service.principal_kind, grant_record.actions,
                           grant_record.confidentiality_ceiling,
                           grant_record.status, service.status AS principal_status
                    FROM project_service_grants AS grant_record
                    JOIN service_principals AS service
                      ON service.principal_id = grant_record.principal_id
                    WHERE grant_record.project_id = ?
                      AND grant_record.principal_id = ?
                    """,
                    (project_id, principal.principal_id),
                ).fetchone()
                connection.execute("COMMIT")
                return _service_grant(row)
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def revoke_employee_grant(
        self,
        *,
        project_id: str,
        employee_id: str,
        reason: str,
        revoked_by: ProjectPrincipal,
        occurred_at: datetime,
    ) -> None:
        """撤销员工项目成员关系。"""

        _require_employee_actor(revoked_by)
        if not reason.strip():
            raise ValueError("撤销原因不能为空")
        occurred = occurred_at.isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN")
            try:
                row = connection.execute(
                    """
                    SELECT role, status FROM project_memberships
                    WHERE project_id = ? AND employee_id = ? FOR UPDATE
                    """,
                    (project_id, employee_id),
                ).fetchone()
                if row is None:
                    raise AuthorizationConflict("员工项目授权不存在")
                if str(row["status"]) == "REVOKED":
                    connection.execute("COMMIT")
                    return
                if str(row["role"]) == ProjectRole.OWNER.value:
                    self._require_another_owner(connection, project_id, employee_id)
                connection.execute(
                    """
                    UPDATE project_memberships
                    SET status = 'REVOKED', updated_at = ?, revoked_at = ?,
                        revoked_by = ?, revocation_reason = ?
                    WHERE project_id = ? AND employee_id = ?
                    """,
                    (
                        occurred,
                        occurred,
                        revoked_by.principal_id,
                        reason,
                        project_id,
                        employee_id,
                    ),
                )
                self._insert_event(
                    connection,
                    project_id=project_id,
                    target=ProjectPrincipal(PrincipalKind.EMPLOYEE, employee_id),
                    event_type="EMPLOYEE_REVOKED",
                    role=ProjectRole(str(row["role"])),
                    actions=None,
                    confidentiality_ceiling=None,
                    reason=reason,
                    actor=revoked_by,
                    occurred=occurred,
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def revoke_service_grant(
        self,
        *,
        project_id: str,
        principal: ProjectPrincipal,
        reason: str,
        revoked_by: ProjectPrincipal,
        occurred_at: datetime,
    ) -> None:
        """撤销服务身份在一个项目内的全部动作。"""

        _require_employee_actor(revoked_by)
        if principal.kind is PrincipalKind.EMPLOYEE:
            raise AuthorizationConflict("员工不能使用服务授权")
        if not reason.strip():
            raise ValueError("撤销原因不能为空")
        occurred = occurred_at.isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN")
            try:
                row = connection.execute(
                    """
                    SELECT grant_record.status, grant_record.actions,
                           service.principal_kind
                    FROM project_service_grants AS grant_record
                    JOIN service_principals AS service
                      ON service.principal_id = grant_record.principal_id
                    WHERE grant_record.project_id = ?
                      AND grant_record.principal_id = ?
                    FOR UPDATE
                    """,
                    (project_id, principal.principal_id),
                ).fetchone()
                if row is None or str(row["principal_kind"]) != principal.kind.value:
                    raise AuthorizationConflict("服务项目授权不存在")
                if str(row["status"]) == "REVOKED":
                    connection.execute("COMMIT")
                    return
                connection.execute(
                    """
                    UPDATE project_service_grants
                    SET status = 'REVOKED', updated_at = ?, revoked_at = ?,
                        revoked_by = ?, revocation_reason = ?
                    WHERE project_id = ? AND principal_id = ?
                    """,
                    (
                        occurred,
                        occurred,
                        revoked_by.principal_id,
                        reason,
                        project_id,
                        principal.principal_id,
                    ),
                )
                actions = frozenset(
                    ProjectAction(str(value)) for value in row["actions"]
                )
                self._insert_event(
                    connection,
                    project_id=project_id,
                    target=principal,
                    event_type="SERVICE_REVOKED",
                    role=None,
                    actions=actions,
                    confidentiality_ceiling=None,
                    reason=reason,
                    actor=revoked_by,
                    occurred=occurred,
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    @staticmethod
    def _require_another_owner(
        connection: PostgreSQLConnection,
        project_id: str,
        excluded_employee_id: str,
    ) -> None:
        row = connection.execute(
            """
            SELECT 1 FROM project_memberships
            WHERE project_id = ? AND role = 'OWNER' AND status = 'ACTIVE'
              AND employee_id <> ?
            LIMIT 1
            """,
            (project_id, excluded_employee_id),
        ).fetchone()
        if row is None:
            raise AuthorizationConflict("不能撤销或降级项目最后一个负责人")

    @staticmethod
    def _insert_event(
        connection: PostgreSQLConnection,
        *,
        project_id: str,
        target: ProjectPrincipal,
        event_type: str,
        role: ProjectRole | None,
        actions: frozenset[ProjectAction] | None,
        confidentiality_ceiling: ConfidentialityLevel | None,
        reason: str | None,
        actor: ProjectPrincipal,
        occurred: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO project_authorization_events(
                event_id, project_id, target_kind, target_id, event_type,
                role, actions, confidentiality_ceiling, reason,
                actor_kind, actor_id, occurred_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'EMPLOYEE', ?, ?)
            """,
            (
                f"AUTHZ-{uuid4().hex.upper()}",
                project_id,
                target.kind.value,
                target.principal_id,
                event_type,
                role.value if role is not None else None,
                sorted(action.value for action in actions)
                if actions is not None
                else None,
                confidentiality_ceiling.value
                if confidentiality_ceiling is not None
                else None,
                reason,
                actor.principal_id,
                occurred,
            ),
        )


def grant_project_runtime_role(dsn: str, role_name: str) -> None:
    """只给运行时角色授予受 RLS 保护表和辅助函数权限。"""

    if not role_name.strip():
        raise ValueError("role_name 不能为空")
    with psycopg.connect(dsn, autocommit=True) as connection:
        protected_tables = [
            str(row[0])
            for row in connection.execute(
                """
                SELECT class.relname
                FROM pg_class AS class
                JOIN pg_namespace AS namespace ON namespace.oid = class.relnamespace
                WHERE namespace.nspname = current_schema()
                  AND class.relkind = 'r'
                  AND class.relrowsecurity
                ORDER BY class.relname
                """
            )
        ]
        role = sql.Identifier(role_name)
        connection.execute(
            sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                sql.Identifier("public"), role
            )
        )
        for table_name in protected_tables:
            connection.execute(
                sql.SQL(
                    "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {} TO {}"
                ).format(sql.Identifier(table_name), role)
            )
        connection.execute(
            sql.SQL(
                "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}"
            ).format(role)
        )
        for signature in (
            "brand_os_confidentiality_rank(TEXT)",
            "brand_os_has_project_action(TEXT, TEXT, TEXT)",
            "brand_os_current_action_permitted(TEXT, TEXT, TEXT[])",
        ):
            connection.execute(
                sql.SQL("GRANT EXECUTE ON FUNCTION {} TO {}").format(
                    sql.SQL(signature), role
                )
            )


@contextmanager
def authorized_project_transaction(
    *,
    authorization_service: ProjectAuthorizationService,
    runtime_dsn: str,
    principal: ProjectPrincipal,
    project_id: str,
    action: ProjectAction,
    resource_confidentiality: ConfidentialityLevel = ConfidentialityLevel.P0,
) -> Iterator[PostgreSQLConnection]:
    """先做应用授权，再用 SET LOCAL 为单个数据库事务注入 RLS 上下文。"""

    decision = authorization_service.authorize(
        principal,
        project_id=project_id,
        action=action,
        resource_confidentiality=resource_confidentiality,
    )
    raw_connection = psycopg.connect(runtime_dsn, autocommit=True)
    connection = PostgreSQLConnection(raw_connection)
    connection.execute("BEGIN")
    try:
        for key, value in (
            ("brand_os.principal_kind", decision.principal.kind.value),
            ("brand_os.principal_id", decision.principal.principal_id),
            ("brand_os.project_id", decision.project_id),
            ("brand_os.action", decision.action.value),
            (
                "brand_os.confidentiality_ceiling",
                decision.confidentiality_ceiling.value,
            ),
        ):
            connection.execute("SELECT set_config(?, ?, true)", (key, value))
        yield connection
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()


def _employee_grant(row) -> EmployeeProjectGrant:
    return EmployeeProjectGrant(
        project_id=str(row["project_id"]),
        employee_id=str(row["employee_id"]),
        role=ProjectRole(str(row["role"])),
        confidentiality_ceiling=ConfidentialityLevel(
            str(row["confidentiality_ceiling"])
        ),
        active=str(row["status"]) == "ACTIVE",
    )


def _service_grant(row) -> ServiceProjectGrant:
    return ServiceProjectGrant(
        project_id=str(row["project_id"]),
        principal=ProjectPrincipal(
            PrincipalKind(str(row["principal_kind"])),
            str(row["principal_id"]),
        ),
        actions=frozenset(ProjectAction(str(value)) for value in row["actions"]),
        confidentiality_ceiling=ConfidentialityLevel(
            str(row["confidentiality_ceiling"])
        ),
        active=(
            str(row["status"]) == "ACTIVE" and str(row["principal_status"]) == "ACTIVE"
        ),
    )


def _require_employee_actor(principal: ProjectPrincipal) -> None:
    if principal.kind is not PrincipalKind.EMPLOYEE:
        raise AuthorizationConflict("只有员工可以管理项目授权")


__all__ = [
    "PROJECT_AUTHORIZATION_TABLES",
    "PostgreSQLProjectAuthorizationRepository",
    "authorized_project_transaction",
    "grant_project_runtime_role",
]
