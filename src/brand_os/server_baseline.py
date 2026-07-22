"""定义服务器组件边界，以及不启动服务也可验证的健康语义。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .server_config import ServerSettings


SERVER_BOUNDARY: dict[str, object] = {
    "schema_version": "server-boundary.v3",
    "service": "Brand Project OS Service",
    "authority": {
        "only_application_service_may_advance_formal_state": True,
        "human_review_requires_interactive_employee_identity": True,
        "agent_and_service_accounts_may_approve": False,
        "client_may_access_storage_directly": False,
        "long_term_dual_write_allowed": False,
        "project_authorization_precedes_storage": True,
        "rls_is_defense_in_depth": True,
    },
    "components": [
        {
            "id": "application_service",
            "kind": "business_application",
            "business_operations": ["read", "create_proposal", "human_review"],
            "forbidden_operations": ["direct_client_storage_access"],
            "may_advance_formal_state": True,
            "stores_formal_business_state": False,
            "stores": [],
            "replaceable": False,
            "required_for_core_readiness": True,
        },
        {
            "id": "employee_api",
            "kind": "protocol_adapter",
            "business_operations": ["read", "create_proposal", "submit_human_review"],
            "forbidden_operations": ["direct_storage_write"],
            "may_advance_formal_state": False,
            "stores_formal_business_state": False,
            "stores": [],
            "replaceable": True,
            "required_for_core_readiness": True,
        },
        {
            "id": "oidc_identity_adapter",
            "kind": "identity_adapter",
            "business_operations": ["authenticate_employee"],
            "forbidden_operations": ["human_review", "direct_storage_write"],
            "may_advance_formal_state": False,
            "stores_formal_business_state": False,
            "stores": [],
            "replaceable": True,
            "required_for_core_readiness": True,
        },
        {
            "id": "identity_session_store",
            "kind": "storage_adapter",
            "business_operations": [],
            "forbidden_operations": ["direct_client_access", "direct_agent_access"],
            "may_advance_formal_state": False,
            "stores_formal_business_state": False,
            "stores": [
                "employee_accounts",
                "oidc_identity_bindings",
                "authorization_transactions",
                "employee_sessions",
                "session_audit",
            ],
            "replaceable": True,
            "required_for_core_readiness": True,
        },
        {
            "id": "project_authorization_service",
            "kind": "business_authorization",
            "business_operations": ["authorize_project_action"],
            "forbidden_operations": ["authenticate_employee", "human_review"],
            "may_advance_formal_state": False,
            "stores_formal_business_state": False,
            "stores": [],
            "replaceable": False,
            "required_for_core_readiness": True,
        },
        {
            "id": "mcp_gateway",
            "kind": "protocol_adapter",
            "business_operations": ["read", "create_proposal"],
            "forbidden_operations": ["human_review", "direct_storage_write"],
            "may_advance_formal_state": False,
            "stores_formal_business_state": False,
            "stores": [],
            "replaceable": True,
            "required_for_core_readiness": False,
        },
        {
            "id": "canonical_store_adapter",
            "kind": "storage_adapter",
            "business_operations": [],
            "forbidden_operations": ["direct_client_access", "direct_agent_access"],
            "may_advance_formal_state": False,
            "stores_formal_business_state": True,
            "stores": ["events", "approvals", "projections", "audit", "outbox"],
            "replaceable": True,
            "required_for_core_readiness": True,
        },
        {
            "id": "object_store_adapter",
            "kind": "storage_adapter",
            "business_operations": [],
            "forbidden_operations": ["direct_client_access", "direct_agent_access"],
            "may_advance_formal_state": False,
            "stores_formal_business_state": False,
            "stores": ["immutable_evidence_versions"],
            "replaceable": True,
            "required_for_core_readiness": True,
        },
        {
            "id": "openwork_runtime",
            "kind": "agent_runtime",
            "business_operations": ["read", "create_proposal"],
            "forbidden_operations": ["human_review", "direct_storage_write"],
            "may_advance_formal_state": False,
            "stores_formal_business_state": False,
            "stores": ["session_state", "tool_permissions", "runtime_events"],
            "replaceable": True,
            "required_for_core_readiness": False,
        },
        {
            "id": "workflow_adapter",
            "kind": "optional_adapter",
            "business_operations": ["read", "create_proposal"],
            "forbidden_operations": ["human_review", "direct_storage_write"],
            "may_advance_formal_state": False,
            "stores_formal_business_state": False,
            "stores": ["workflow_run_state"],
            "replaceable": True,
            "required_for_core_readiness": False,
        },
    ],
    "readiness": {
        "required_dependencies": ["postgresql", "schema", "object_storage", "oidc"],
        "optional_dependencies": [
            "openwork_runtime",
            "dify",
            "zvec",
            "open_notebook",
            "nubase",
            "flowlong",
        ],
    },
    "deferred_from_f2_1": [
        "http_and_mcp_routes",
        "hongri_data_migration",
    ],
}

REQUIRED_DEPENDENCIES = tuple(SERVER_BOUNDARY["readiness"]["required_dependencies"])
OPTIONAL_DEPENDENCIES = tuple(SERVER_BOUNDARY["readiness"]["optional_dependencies"])


@dataclass(frozen=True, slots=True)
class DependencyHealth:
    """描述单个依赖是否影响核心就绪状态。"""

    name: str
    status: str
    required: bool

    def to_dict(self) -> dict[str, object]:
        """返回稳定的健康依赖结构。"""

        return {"name": self.name, "status": self.status, "required": self.required}


@dataclass(frozen=True, slots=True)
class ServiceHealthReport:
    """描述服务存活或就绪检查结果。"""

    check: str
    status: str
    dependencies: tuple[DependencyHealth, ...]
    issues: tuple[Mapping[str, str], ...] = ()
    schema_version: str = "service-health.v1"

    @property
    def blocking_dependencies(self) -> tuple[str, ...]:
        """返回阻断核心就绪状态的必需依赖。"""

        return tuple(
            dependency.name
            for dependency in self.dependencies
            if dependency.required and dependency.status != "up"
        )

    @property
    def degraded_dependencies(self) -> tuple[str, ...]:
        """返回故障但不阻断核心服务的可选依赖。"""

        return tuple(
            dependency.name
            for dependency in self.dependencies
            if not dependency.required and dependency.status in {"down", "unknown"}
        )

    def to_dict(self) -> dict[str, object]:
        """返回不包含秘密或连接字符串的健康报告。"""

        return {
            "schema_version": self.schema_version,
            "check": self.check,
            "status": self.status,
            "dependencies": [dependency.to_dict() for dependency in self.dependencies],
            "blocking_dependencies": list(self.blocking_dependencies),
            "degraded_dependencies": list(self.degraded_dependencies),
            "issues": [dict(issue) for issue in self.issues],
        }


def build_liveness_report() -> ServiceHealthReport:
    """只证明当前进程可以响应，不探测任何外部依赖。"""

    return ServiceHealthReport(check="live", status="live", dependencies=())


def build_readiness_report(
    settings: ServerSettings,
    *,
    dependency_states: Mapping[str, bool | None],
) -> ServiceHealthReport:
    """检查必需配置和核心依赖，并隔离可选组件故障。"""

    dependencies: list[DependencyHealth] = []
    for name in REQUIRED_DEPENDENCIES:
        dependencies.append(
            DependencyHealth(
                name=name,
                status=_dependency_status(dependency_states.get(name)),
                required=True,
            )
        )
    for name in OPTIONAL_DEPENDENCIES:
        status = (
            _dependency_status(dependency_states[name])
            if name in dependency_states
            else "disabled"
        )
        dependencies.append(DependencyHealth(name=name, status=status, required=False))

    issues = tuple(issue.to_dict() for issue in settings.validation_issues())
    blocking = any(
        dependency.required and dependency.status != "up"
        for dependency in dependencies
    )
    return ServiceHealthReport(
        check="ready",
        status="not_ready" if issues or blocking else "ready",
        dependencies=tuple(dependencies),
        issues=issues,
    )


def _dependency_status(state: bool | None) -> str:
    """把依赖探测结果规范为机器可读状态。"""

    if state is True:
        return "up"
    if state is False:
        return "down"
    return "unknown"


def validate_server_boundary(document: Mapping[str, object]) -> tuple[str, ...]:
    """检查组件职责契约的一票否决项。"""

    errors: list[str] = []
    if document.get("schema_version") != "server-boundary.v3":
        errors.append("服务器边界 Schema 版本不正确")
    components = document.get("components")
    if not isinstance(components, list):
        return ("components 必须是数组",)
    advancing = [
        component.get("id")
        for component in components
        if isinstance(component, dict) and component.get("may_advance_formal_state") is True
    ]
    if advancing != ["application_service"]:
        errors.append("只有 application_service 可以推进正式状态")

    by_id = {
        str(component.get("id")): component
        for component in components
        if isinstance(component, dict)
    }
    for component_id in ("mcp_gateway", "workflow_adapter", "openwork_runtime"):
        component = by_id.get(component_id)
        if component is None:
            errors.append(f"缺少组件：{component_id}")
            continue
        if set(component.get("business_operations", [])) != {"read", "create_proposal"}:
            errors.append(f"{component_id} 只能读取或创建 Proposal")
        if "human_review" not in component.get("forbidden_operations", []):
            errors.append(f"{component_id} 必须禁止人工审批操作")

    runtime = by_id.get("openwork_runtime")
    if runtime and (
        runtime.get("kind") != "agent_runtime"
        or runtime.get("stores_formal_business_state") is not False
    ):
        errors.append("OpenWork Server/Orchestrator 只能属于 Agent Runtime")
    identity = by_id.get("oidc_identity_adapter")
    if identity is None:
        errors.append("缺少组件：oidc_identity_adapter")
    elif (
        identity.get("business_operations") != ["authenticate_employee"]
        or "human_review" not in identity.get("forbidden_operations", [])
        or identity.get("may_advance_formal_state") is not False
    ):
        errors.append("OIDC 只能认证员工，不能执行人工审批")
    authorization = by_id.get("project_authorization_service")
    if authorization is None:
        errors.append("缺少组件：project_authorization_service")
    elif (
        authorization.get("business_operations") != ["authorize_project_action"]
        or "human_review" not in authorization.get("forbidden_operations", [])
        or authorization.get("required_for_core_readiness") is not True
    ):
        errors.append("项目授权必须先于存储访问且不能执行人工审批")
    readiness = document.get("readiness")
    if not isinstance(readiness, dict) or "oidc" not in readiness.get(
        "required_dependencies", []
    ):
        errors.append("OIDC 必须属于服务器就绪依赖")
    authority = document.get("authority")
    if (
        not isinstance(authority, dict)
        or authority.get("client_may_access_storage_directly") is not False
        or authority.get("project_authorization_precedes_storage") is not True
        or authority.get("rls_is_defense_in_depth") is not True
    ):
        errors.append("客户端不得直连权威存储")
    return tuple(errors)
