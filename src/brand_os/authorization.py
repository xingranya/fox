"""项目角色、服务身份和保密级别授权规则。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from .ports import ProjectAuthorizationRepositoryPort


class PrincipalKind(StrEnum):
    """区分员工和不同类型的非交互式服务身份。"""

    EMPLOYEE = "EMPLOYEE"
    AI = "AI"
    MCP = "MCP"
    WORKFLOW = "WORKFLOW"
    SYSTEM = "SYSTEM"


class ProjectRole(StrEnum):
    """员工在单个项目内的角色。"""

    OWNER = "OWNER"
    MANAGER = "MANAGER"
    EDITOR = "EDITOR"
    REVIEWER = "REVIEWER"
    VIEWER = "VIEWER"


class ProjectAction(StrEnum):
    """应用服务执行授权时使用的稳定动作集合。"""

    PROJECT_READ = "PROJECT_READ"
    EVIDENCE_READ = "EVIDENCE_READ"
    EVIDENCE_WRITE = "EVIDENCE_WRITE"
    WORKING_WRITE = "WORKING_WRITE"
    PROPOSAL_CREATE = "PROPOSAL_CREATE"
    PROPOSAL_REVIEW = "PROPOSAL_REVIEW"
    TASK_READ = "TASK_READ"
    RUNTIME_START = "RUNTIME_START"
    ACCESS_MANAGE = "ACCESS_MANAGE"


class ConfidentialityLevel(StrEnum):
    """项目资料保密级别，数值越高访问要求越高。"""

    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


CONFIDENTIALITY_RANK = {
    ConfidentialityLevel.P0: 0,
    ConfidentialityLevel.P1: 1,
    ConfidentialityLevel.P2: 2,
    ConfidentialityLevel.P3: 3,
}

ROLE_ACTIONS = {
    ProjectRole.OWNER: frozenset(ProjectAction),
    ProjectRole.MANAGER: frozenset(
        {
            ProjectAction.PROJECT_READ,
            ProjectAction.EVIDENCE_READ,
            ProjectAction.EVIDENCE_WRITE,
            ProjectAction.WORKING_WRITE,
            ProjectAction.PROPOSAL_CREATE,
            ProjectAction.PROPOSAL_REVIEW,
            ProjectAction.TASK_READ,
            ProjectAction.RUNTIME_START,
        }
    ),
    ProjectRole.EDITOR: frozenset(
        {
            ProjectAction.PROJECT_READ,
            ProjectAction.EVIDENCE_READ,
            ProjectAction.EVIDENCE_WRITE,
            ProjectAction.WORKING_WRITE,
            ProjectAction.PROPOSAL_CREATE,
            ProjectAction.TASK_READ,
            ProjectAction.RUNTIME_START,
        }
    ),
    ProjectRole.REVIEWER: frozenset(
        {
            ProjectAction.PROJECT_READ,
            ProjectAction.EVIDENCE_READ,
            ProjectAction.PROPOSAL_CREATE,
            ProjectAction.PROPOSAL_REVIEW,
            ProjectAction.TASK_READ,
        }
    ),
    ProjectRole.VIEWER: frozenset(
        {
            ProjectAction.PROJECT_READ,
            ProjectAction.EVIDENCE_READ,
            ProjectAction.TASK_READ,
        }
    ),
}

SERVICE_ACTIONS = {
    PrincipalKind.AI: frozenset(
        {
            ProjectAction.PROJECT_READ,
            ProjectAction.EVIDENCE_READ,
            ProjectAction.PROPOSAL_CREATE,
            ProjectAction.TASK_READ,
        }
    ),
    PrincipalKind.MCP: frozenset(
        {
            ProjectAction.PROJECT_READ,
            ProjectAction.EVIDENCE_READ,
            ProjectAction.PROPOSAL_CREATE,
            ProjectAction.TASK_READ,
        }
    ),
    PrincipalKind.WORKFLOW: frozenset(
        {
            ProjectAction.PROJECT_READ,
            ProjectAction.EVIDENCE_READ,
            ProjectAction.PROPOSAL_CREATE,
            ProjectAction.TASK_READ,
            ProjectAction.RUNTIME_START,
        }
    ),
    PrincipalKind.SYSTEM: frozenset(
        {
            ProjectAction.PROJECT_READ,
            ProjectAction.EVIDENCE_READ,
            ProjectAction.EVIDENCE_WRITE,
            ProjectAction.WORKING_WRITE,
            ProjectAction.PROPOSAL_CREATE,
            ProjectAction.TASK_READ,
            ProjectAction.RUNTIME_START,
        }
    ),
}


class AuthorizationError(RuntimeError):
    """项目授权操作无法安全完成。"""


class ProjectAccessDenied(AuthorizationError, PermissionError):
    """主体没有项目动作或资料保密级别权限。"""


class AuthorizationConflict(AuthorizationError):
    """授权记录与当前状态冲突。"""


@dataclass(frozen=True, slots=True)
class ProjectPrincipal:
    """应用服务已认证的员工或服务主体。"""

    kind: PrincipalKind
    principal_id: str

    def __post_init__(self) -> None:
        if not self.principal_id.strip():
            raise ValueError("principal_id 不能为空")


@dataclass(frozen=True, slots=True)
class EmployeeProjectGrant:
    """员工在一个项目内的有效角色和保密级别上限。"""

    project_id: str
    employee_id: str
    role: ProjectRole
    confidentiality_ceiling: ConfidentialityLevel
    active: bool


@dataclass(frozen=True, slots=True)
class ServiceProjectGrant:
    """非交互式主体在一个项目内的显式动作白名单。"""

    project_id: str
    principal: ProjectPrincipal
    actions: frozenset[ProjectAction]
    confidentiality_ceiling: ConfidentialityLevel
    active: bool


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    """一次应用层授权结果，可用于注入当前数据库事务。"""

    principal: ProjectPrincipal
    project_id: str
    action: ProjectAction
    resource_confidentiality: ConfidentialityLevel
    confidentiality_ceiling: ConfidentialityLevel


class ProjectAuthorizationService:
    """在进入存储或工作流适配器前执行项目显式授权。"""

    def __init__(self, repository: ProjectAuthorizationRepositoryPort) -> None:
        self.repository = repository

    def authorize(
        self,
        principal: ProjectPrincipal,
        *,
        project_id: str,
        action: ProjectAction,
        resource_confidentiality: ConfidentialityLevel = ConfidentialityLevel.P0,
    ) -> AuthorizationDecision:
        """校验项目、动作和资料级别，拒绝依赖 RLS 代替应用判权。"""

        if not project_id.strip():
            raise ValueError("project_id 不能为空")
        if principal.kind is PrincipalKind.EMPLOYEE:
            grant = self.repository.get_employee_grant(
                project_id, principal.principal_id
            )
            if grant is None or not grant.active:
                raise ProjectAccessDenied("员工没有该项目的有效授权")
            allowed_actions = ROLE_ACTIONS[grant.role]
            confidentiality_ceiling = grant.confidentiality_ceiling
        else:
            grant = self.repository.get_service_grant(project_id, principal)
            if grant is None or not grant.active:
                raise ProjectAccessDenied("服务身份没有该项目的有效授权")
            allowed_actions = grant.actions
            confidentiality_ceiling = grant.confidentiality_ceiling

        if action not in allowed_actions:
            raise ProjectAccessDenied("当前项目授权不允许执行该动作")
        if (
            CONFIDENTIALITY_RANK[resource_confidentiality]
            > CONFIDENTIALITY_RANK[confidentiality_ceiling]
        ):
            raise ProjectAccessDenied("资料保密级别超出当前授权上限")
        return AuthorizationDecision(
            principal=principal,
            project_id=project_id,
            action=action,
            resource_confidentiality=resource_confidentiality,
            confidentiality_ceiling=confidentiality_ceiling,
        )

    def bootstrap_owner(
        self,
        principal: ProjectPrincipal,
        *,
        project_id: str,
        confidentiality_ceiling: ConfidentialityLevel,
        occurred_at: datetime | None = None,
    ) -> EmployeeProjectGrant:
        """把由该员工创建且尚无成员的项目绑定首位负责人。"""

        if principal.kind is not PrincipalKind.EMPLOYEE:
            raise ProjectAccessDenied("只有项目创建员工可以建立首位负责人")
        return self.repository.bootstrap_owner(
            project_id=project_id,
            employee_id=principal.principal_id,
            confidentiality_ceiling=confidentiality_ceiling,
            occurred_at=_utc(occurred_at),
        )

    def grant_employee(
        self,
        principal: ProjectPrincipal,
        *,
        project_id: str,
        employee_id: str,
        role: ProjectRole,
        confidentiality_ceiling: ConfidentialityLevel,
        occurred_at: datetime | None = None,
    ) -> EmployeeProjectGrant:
        """由项目负责人授予或调整员工角色。"""

        self.authorize(
            principal,
            project_id=project_id,
            action=ProjectAction.ACCESS_MANAGE,
        )
        return self.repository.upsert_employee_grant(
            project_id=project_id,
            employee_id=employee_id,
            role=role,
            confidentiality_ceiling=confidentiality_ceiling,
            granted_by=principal,
            occurred_at=_utc(occurred_at),
        )

    def register_service(
        self,
        principal: ProjectPrincipal,
        *,
        project_id: str,
        service_principal: ProjectPrincipal,
        display_name: str,
        occurred_at: datetime | None = None,
    ) -> None:
        """登记独立服务身份，不允许把员工身份复用于服务。"""

        self.authorize(
            principal,
            project_id=project_id,
            action=ProjectAction.ACCESS_MANAGE,
        )
        if service_principal.kind is PrincipalKind.EMPLOYEE:
            raise ProjectAccessDenied("员工身份不能登记为服务身份")
        self.repository.register_service_principal(
            service_principal,
            display_name=display_name,
            registered_by=principal,
            occurred_at=_utc(occurred_at),
        )

    def grant_service(
        self,
        principal: ProjectPrincipal,
        *,
        project_id: str,
        service_principal: ProjectPrincipal,
        actions: Iterable[ProjectAction],
        confidentiality_ceiling: ConfidentialityLevel,
        occurred_at: datetime | None = None,
    ) -> ServiceProjectGrant:
        """授予服务身份最小动作集合，人工批准和权限管理永远不可授予。"""

        self.authorize(
            principal,
            project_id=project_id,
            action=ProjectAction.ACCESS_MANAGE,
        )
        if service_principal.kind is PrincipalKind.EMPLOYEE:
            raise ProjectAccessDenied("员工必须使用项目角色，不能使用服务授权")
        requested_actions = frozenset(actions)
        if not requested_actions:
            raise ValueError("服务授权动作不能为空")
        allowed_actions = SERVICE_ACTIONS[service_principal.kind]
        prohibited = requested_actions - allowed_actions
        if prohibited:
            names = ", ".join(sorted(action.value for action in prohibited))
            raise ProjectAccessDenied(f"服务身份禁止获得动作：{names}")
        return self.repository.upsert_service_grant(
            project_id=project_id,
            principal=service_principal,
            actions=requested_actions,
            confidentiality_ceiling=confidentiality_ceiling,
            granted_by=principal,
            occurred_at=_utc(occurred_at),
        )

    def revoke_employee(
        self,
        principal: ProjectPrincipal,
        *,
        project_id: str,
        employee_id: str,
        reason: str,
        occurred_at: datetime | None = None,
    ) -> None:
        """撤销员工项目授权，禁止移除最后一个有效负责人。"""

        self.authorize(
            principal,
            project_id=project_id,
            action=ProjectAction.ACCESS_MANAGE,
        )
        self.repository.revoke_employee_grant(
            project_id=project_id,
            employee_id=employee_id,
            reason=reason,
            revoked_by=principal,
            occurred_at=_utc(occurred_at),
        )

    def revoke_service(
        self,
        principal: ProjectPrincipal,
        *,
        project_id: str,
        service_principal: ProjectPrincipal,
        reason: str,
        occurred_at: datetime | None = None,
    ) -> None:
        """撤销单个项目内的服务授权。"""

        self.authorize(
            principal,
            project_id=project_id,
            action=ProjectAction.ACCESS_MANAGE,
        )
        self.repository.revoke_service_grant(
            project_id=project_id,
            principal=service_principal,
            reason=reason,
            revoked_by=principal,
            occurred_at=_utc(occurred_at),
        )


def _utc(value: datetime | None) -> datetime:
    resolved = value or datetime.now(UTC)
    if resolved.tzinfo is None or resolved.utcoffset() is None:
        raise ValueError("occurred_at 必须包含时区")
    return resolved.astimezone(UTC)


__all__ = [
    "AuthorizationConflict",
    "AuthorizationDecision",
    "AuthorizationError",
    "CONFIDENTIALITY_RANK",
    "ConfidentialityLevel",
    "EmployeeProjectGrant",
    "PrincipalKind",
    "ProjectAccessDenied",
    "ProjectAction",
    "ProjectAuthorizationService",
    "ProjectPrincipal",
    "ProjectRole",
    "ROLE_ACTIONS",
    "SERVICE_ACTIONS",
    "ServiceProjectGrant",
]
