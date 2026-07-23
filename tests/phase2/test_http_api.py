"""F2.8 版本化 HTTP API、鉴权、分页和错误契约测试。"""

from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Mapping

from openapi_spec_validator import validate_spec
from starlette.testclient import TestClient

from brand_os.authorization import (
    AuthorizationDecision,
    ConfidentialityLevel,
    ProjectAction,
    ProjectPrincipal,
    PrincipalKind,
    ProjectAccessDenied,
)
from brand_os.consistency import ConflictCode, ConflictReport, StateSnapshotSummary, WriteExecutionResult, WriteOutcome
from brand_os.domain import Actor, ActorKind, CommandContext, CommandResult
from brand_os.http_api import (
    HTTP_API_CONTRACT,
    HTTP_API_SCHEMA_VERSION,
    HttpApplicationDependencies,
    RateLimitDecision,
    build_http_app,
    build_openapi_document,
)
from brand_os.identity import InteractiveEmployeePrincipal
from brand_os.observability import (
    InMemorySink,
    ObservabilityRuntime,
    RateLimitStoreUnavailable,
    StructuredLogger,
    Tracer,
)
from brand_os.server_config import load_server_settings


ROOT = Path(__file__).parents[2]
CONTRACT_PATH = ROOT / "contracts" / "phase2" / "http-api.json"


def make_test_settings():
    """构造不包含真实凭据的测试服务配置。"""

    return load_server_settings(
        explicit={
            "environment": "test",
            "public_base_url": "http://service.test",
            "database_dsn": "postgresql://test:test@postgres/brand_os",
            "object_store_endpoint": "http://object-store.test",
            "object_store_bucket": "brand-os-test",
            "object_store_access_key": "test-access",
            "object_store_secret_key": "test-secret",
            "oidc_issuer_url": "http://oidc.test",
            "oidc_client_id": "brand-os-test",
            "oidc_client_secret": "test-oidc-secret",
            "session_encryption_key": "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
        },
        environ={},
    )


class FakeStore:
    """只提供 HTTP 适配器需要的读取和 Proposal 命令。"""

    schema_version = 11

    def __init__(self) -> None:
        self.version = 3
        self.state = [
            {"item_id": "state-1", "item_type": "DECISION", "payload": {"value": 1}},
            {"item_id": "state-2", "item_type": "CONSTRAINT", "payload": {"value": 2}},
            {"item_id": "state-3", "item_type": "ACTION", "payload": {"value": 3}},
        ]
        self.proposals = [
            {
                "proposal_id": "proposal-1",
                "status": "proposed",
                "classification": "DECISION_CANDIDATE",
                "after": {"statement": "测试"},
            }
        ]

    def get_project(self, project_id: str) -> Mapping[str, object]:
        if project_id != "hongri":
            from brand_os.sqlite_base import ProjectNotFound

            raise ProjectNotFound(project_id)
        return {"project_id": project_id, "name": "鸿日", "version": self.version}

    def get_project_version(self, project_id: str) -> int:
        self.get_project(project_id)
        return self.version

    def get_current_state(self, project_id: str):
        self.get_project(project_id)
        return list(self.state)

    def list_proposals(self, project_id: str, status: str | None = None):
        self.get_project(project_id)
        return [item for item in self.proposals if status is None or item["status"] == status]

    def get_proposal_history(self, project_id: str, proposal_id: str):
        self.get_project(project_id)
        return [{"proposal_id": proposal_id, "event": "PROPOSAL_CREATED"}]

    def resolve_evidence_ref(self, project_id: str, evidence_ref: str):
        self.get_project(project_id)
        return {"evidence_ref": evidence_ref, "status": "confirmed"}

    def get_task_packet(self, project_id: str, packet_id: str):
        self.get_project(project_id)
        return {"packet_id": packet_id, "content_hash": "a" * 64}

    def create_proposal(self, context: CommandContext, proposal):
        self.version += 1
        self.proposals.append(
            {
                "proposal_id": proposal.proposal_id,
                "status": "proposed",
                "classification": proposal.classification,
                "after": dict(proposal.after),
            }
        )
        return CommandResult(self.version, f"EV-{proposal.proposal_id}", proposal.proposal_id)

    def review_proposal(self, context: CommandContext, review):
        self.version += 1
        for item in self.proposals:
            if item["proposal_id"] == review.proposal_id:
                item["status"] = "approved" if review.action.value != "reject" else "rejected"
        return CommandResult(self.version, f"EV-REVIEW-{review.proposal_id}", review.proposal_id)


class FakeIdentity:
    """可控的员工会话适配器。"""

    def __init__(self) -> None:
        self.principal = InteractiveEmployeePrincipal(
            employee_id="Fox",
            display_name="Fox",
            session_id="SES-1",
            issuer="https://oidc.test",
            subject="fox-subject",
            authenticated_at=datetime(2026, 7, 23, tzinfo=UTC),
        )

    def authenticate(self, token: str, **kwargs):
        if token != "employee-token":
            raise ValueError("bad token")
        return self.principal

    def bind_human_command_context(self, token: str, **kwargs):
        if token != "employee-token":
            raise ValueError("bad token")
        return CommandContext(
            kwargs["project_id"],
            Actor(ActorKind.HUMAN, "Fox"),
            kwargs["idempotency_key"],
            kwargs["expected_version"],
        )

    def begin_login(self):
        from brand_os.identity import AuthorizationRequest

        return AuthorizationRequest("AUTH-1", "https://oidc.test/authorize", datetime(2026, 7, 23, 13, tzinfo=UTC))


class AllowAuthorization:
    """只允许测试项目，模拟已通过 F2.5 的应用判权。"""

    def authorize(self, principal, *, project_id, action, resource_confidentiality=ConfidentialityLevel.P0):
        if project_id != "hongri":
            raise ProjectAccessDenied("没有项目授权")
        return AuthorizationDecision(
            principal=principal,
            project_id=project_id,
            action=action,
            resource_confidentiality=resource_confidentiality,
            confidentiality_ceiling=ConfidentialityLevel.P3,
        )


class PassthroughConsistency:
    """验证 HTTP 层复用一致性服务返回值，而不是自行改写正式状态。"""

    def __init__(self, conflict: ConflictReport | None = None) -> None:
        self.conflict = conflict

    def execute(self, authorization, *, context, command_name, operation, resource_type=None, resource_id=None):
        if self.conflict is not None:
            return WriteExecutionResult(outcome=WriteOutcome.CONFLICT, conflict=self.conflict)
        result = operation()
        return WriteExecutionResult(
            outcome=WriteOutcome.REPLAYED if result.replayed else WriteOutcome.COMMITTED,
            result=result,
        )


class AlwaysLimited:
    """用于验证 429 错误体和 Retry-After。"""

    def check(self, key, bucket, *, limit, window_seconds):
        return RateLimitDecision(False, limit, 0, 7)


class AlwaysUnavailable:
    """验证共享限流故障时返回 503，且不退回进程内计数。"""

    def check(self, key, bucket, *, limit, window_seconds):
        raise RateLimitStoreUnavailable("共享限流不可用")


def make_client(
    *,
    limiter=None,
    consistency=None,
    observability=None,
    dependency_states=None,
):
    store = FakeStore()
    dependencies = HttpApplicationDependencies(
        store=store,
        identity=FakeIdentity(),
        authorization=AllowAuthorization(),  # type: ignore[arg-type]
        consistency=consistency or PassthroughConsistency(),
        settings=make_test_settings(),
        dependency_states=dependency_states
        or (
            lambda: {
                "postgresql": True,
                "schema": True,
                "object_storage": True,
                "oidc": True,
            }
        ),
        agent_authenticator=lambda token: (
            ProjectPrincipal(PrincipalKind.AI, "agent-1")
            if token == "agent-token"
            else ProjectPrincipal(PrincipalKind.EMPLOYEE, "Fox")
        ),
        rate_limiter=limiter,
        observability=observability,
        cursor_secret=b"http-api-test-cursor-secret-32-bytes!!",
    )
    return TestClient(build_http_app(dependencies)), store


class HttpApiContractTest(unittest.TestCase):
    """冻结机器契约、OpenAPI 路由和安全定义。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))

    def test_contract_matches_source_and_openapi_is_valid(self) -> None:
        self.assertEqual(self.contract, HTTP_API_CONTRACT)
        document = build_openapi_document()
        validate_spec(document)
        self.assertEqual(document["info"]["x-contract-schema"], HTTP_API_SCHEMA_VERSION)
        self.assertEqual(set(document["paths"]), {"/livez", "/readyz", "/openapi.json", *[route["path"] for route in self.contract["routes"]]})

    def test_openapi_has_separate_employee_and_agent_bearer_schemes(self) -> None:
        schemes = build_openapi_document()["components"]["securitySchemes"]
        self.assertEqual(set(schemes), {"EmployeeSession", "AgentBearer"})
        self.assertTrue(HTTP_API_CONTRACT["security"]["agent_cannot_review"])

    def test_request_models_reject_extra_fields(self) -> None:
        schema = build_openapi_document()["components"]["schemas"]["ProposalCreate"]
        self.assertFalse(schema["additionalProperties"])
        self.assertFalse(build_openapi_document()["components"]["schemas"]["ProposalReview"]["additionalProperties"])


class HttpApiIntegrationTest(unittest.TestCase):
    """验证 HTTP 层的真实入口、授权、分页和稳定错误。"""

    def test_health_and_version_compatibility(self) -> None:
        client, _ = make_client()
        with client:
            self.assertEqual(client.get("/livez").status_code, 200)
            self.assertEqual(client.get("/readyz").status_code, 200)
            retired = client.get("/api/v2/anything")
            self.assertEqual(retired.status_code, 410)
            self.assertEqual(retired.json()["code"], "API_VERSION_RETIRED")

    def test_method_not_allowed_returns_legal_allow_header(self) -> None:
        client, _ = make_client()
        with client:
            response = client.post(
                "/livez",
            )
            self.assertEqual(response.status_code, 405)
            self.assertEqual(set(response.headers["allow"].split(", ")), {"GET", "HEAD"})
            self.assertNotIn("POST", response.headers["allow"])
            self.assertEqual(response.json()["code"], "METHOD_NOT_ALLOWED")

    def test_missing_and_wrong_identity_are_separated(self) -> None:
        client, _ = make_client()
        with client:
            missing = client.get("/api/v1/employee/me")
            self.assertEqual(missing.status_code, 401)
            self.assertEqual(missing.json()["schema_version"], "http-error.v1")
            wrong_surface = client.get(
                "/api/v1/agent/projects/hongri/state",
                headers={"Authorization": "Bearer employee-token"},
            )
            self.assertEqual(wrong_surface.status_code, 403)
            self.assertEqual(wrong_surface.json()["code"], "EMPLOYEE_TOKEN_NOT_ALLOWED")

    def test_employee_reads_use_project_authorization_and_cursor_pagination(self) -> None:
        client, store = make_client()
        with client:
            first = client.get(
                "/api/v1/employee/projects/hongri/state?limit=1",
                headers={"Authorization": "Bearer employee-token"},
            )
            self.assertEqual(first.status_code, 200)
            self.assertEqual(len(first.json()["items"]), 1)
            cursor = first.json()["pagination"]["next_cursor"]
            self.assertTrue(cursor)
            second = client.get(
                f"/api/v1/employee/projects/hongri/state?limit=1&cursor={cursor}",
                headers={"Authorization": "Bearer employee-token"},
            )
            self.assertEqual(second.status_code, 200)
            self.assertEqual(second.json()["items"][0]["item_id"], "state-2")
            store.version += 1
            stale = client.get(
                f"/api/v1/employee/projects/hongri/state?limit=1&cursor={cursor}",
                headers={"Authorization": "Bearer employee-token"},
            )
            self.assertEqual(stale.status_code, 409)
            self.assertEqual(stale.json()["code"], "PAGINATION_CURSOR_STALE")

    def test_unknown_fields_and_missing_write_headers_have_stable_errors(self) -> None:
        client, _ = make_client()
        with client:
            body = {
                "proposal_id": "p-new",
                "proposal_kind": "create",
                "classification": "OPEN",
                "after": {"question": "待确认"},
                "reason": "测试",
                "impact_scope": "项目",
                "evidence_refs": ["evidence:test"],
                "not_allowed": True,
            }
            unknown = client.post(
                "/api/v1/employee/projects/hongri/proposals",
                json=body,
                headers={"Authorization": "Bearer employee-token"},
            )
            self.assertEqual(unknown.status_code, 422)
            self.assertEqual(unknown.json()["code"], "UNKNOWN_FIELD")
            missing_headers = client.post(
                "/api/v1/employee/projects/hongri/proposals",
                json={key: value for key, value in body.items() if key != "not_allowed"},
                headers={"Authorization": "Bearer employee-token"},
            )
            self.assertEqual(missing_headers.status_code, 400)
            self.assertEqual(missing_headers.json()["code"], "MISSING_IF_MATCH")

    def test_agent_can_create_proposal_but_has_no_review_route(self) -> None:
        client, store = make_client()
        with client:
            body = {
                "proposal_id": "agent-proposal",
                "proposal_kind": "create",
                "classification": "OPEN",
                "after": {"question": "Agent 建议"},
                "reason": "Agent 只提出候选",
                "impact_scope": "测试项目",
                "evidence_refs": ["evidence:test"],
            }
            created = client.post(
                "/api/v1/agent/projects/hongri/proposals",
                json=body,
                headers={
                    "Authorization": "Bearer agent-token",
                    "Idempotency-Key": "agent-create-1",
                    "If-Match": '"3"',
                },
            )
            self.assertEqual(created.status_code, 201)
            self.assertEqual(created.json()["outcome"], "COMMITTED")
            self.assertEqual(store.version, 4)
            review = client.post(
                "/api/v1/agent/projects/hongri/proposals/agent-proposal/review",
                json={"action": "approve", "reason": "不允许"},
                headers={"Authorization": "Bearer agent-token"},
            )
            self.assertEqual(review.status_code, 404)

    def test_employee_review_uses_human_identity_and_if_match(self) -> None:
        client, store = make_client()
        with client:
            result = client.post(
                "/api/v1/employee/projects/hongri/proposals/proposal-1/review",
                json={"action": "approve", "reason": "Fox 已核对"},
                headers={
                    "Authorization": "Bearer employee-token",
                    "Idempotency-Key": "review-1",
                    "If-Match": '"3"',
                },
            )
            self.assertEqual(result.status_code, 201)
            self.assertEqual(result.json()["outcome"], "COMMITTED")
            self.assertEqual(store.proposals[0]["status"], "approved")

    def test_conflict_report_maps_to_409_without_changing_http_semantics(self) -> None:
        conflict = ConflictReport(
            schema_version="write-conflict.v1",
            http_status=409,
            code=ConflictCode.VERSION_MISMATCH,
            project_id="hongri",
            command_name="create_proposal",
            idempotency_key="conflict-1",
            resource_type="proposal",
            resource_id="p",
            expected_version=2,
            current_version=3,
            reason="版本已变化",
            baseline=StateSnapshotSummary(2, True, 0, "a" * 64),
            current=StateSnapshotSummary(3, True, 0, "b" * 64),
            state_changes=(),
            events=(),
            events_truncated=False,
            next_event_version=None,
        )
        client, _ = make_client(consistency=PassthroughConsistency(conflict))
        with client:
            response = client.post(
                "/api/v1/agent/projects/hongri/proposals",
                json={
                    "proposal_id": "p",
                    "proposal_kind": "create",
                    "classification": "OPEN",
                    "after": {"question": "x"},
                    "reason": "x",
                    "impact_scope": "x",
                    "evidence_refs": ["e"],
                },
                headers={"Authorization": "Bearer agent-token", "Idempotency-Key": "conflict-1", "If-Match": "2"},
            )
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["code"], "VERSION_MISMATCH")

    def test_rate_limit_returns_retry_after_and_request_id(self) -> None:
        client, _ = make_client(limiter=AlwaysLimited())
        with client:
            response = client.get("/api/v1/agent/projects/hongri/state", headers={"Authorization": "Bearer agent-token"})
            self.assertEqual(response.status_code, 429)
            self.assertEqual(response.headers["retry-after"], "7")
            self.assertEqual(response.json()["schema_version"], "http-error.v1")
            self.assertTrue(response.headers.get("x-request-id"))

    def test_request_correlation_trace_headers_and_runtime_telemetry(self) -> None:
        logs = InMemorySink()
        traces = InMemorySink()
        runtime = ObservabilityRuntime(
            logger=StructuredLogger(logs),
            tracer=Tracer(traces),
        )
        trace_id = "1" * 32
        client, _ = make_client(observability=runtime)
        with client:
            response = client.get(
                "/api/v1/agent/projects/hongri/state",
                headers={
                    "Authorization": "Bearer agent-token",
                    "X-Request-ID": "request-1",
                    "X-Correlation-ID": "correlation-1",
                    "traceparent": f"00-{trace_id}-{'2' * 16}-01",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["x-request-id"], "request-1")
        self.assertEqual(response.headers["x-correlation-id"], "correlation-1")
        self.assertEqual(response.headers["traceparent"].split("-")[1], trace_id)
        self.assertEqual(runtime.metrics.value("http_requests_total"), 1)
        self.assertEqual(len(traces.records), 1)
        self.assertEqual(traces.records[0]["name"], "http.request")
        rendered_logs = json.dumps(logs.records, ensure_ascii=False)
        self.assertNotIn("agent-token", rendered_logs)
        self.assertEqual(logs.records[0]["actor_id"], "agent-1")
        self.assertEqual(logs.records[0]["project_id"], "hongri")
        self.assertEqual(logs.records[0]["state_version"], 3)

    def test_readiness_updates_dependency_metrics_without_mislabeling_503(self) -> None:
        logs = InMemorySink()
        runtime = ObservabilityRuntime(logger=StructuredLogger(logs))
        client, _ = make_client(
            observability=runtime,
            dependency_states=lambda: {
                "postgresql": True,
                "schema": True,
                "object_storage": False,
                "oidc": True,
            },
        )
        with client:
            response = client.get("/readyz")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            runtime.metrics.value(
                "dependency_status",
                labels={"dependency": "object_storage", "status": "down"},
            ),
            0,
        )
        self.assertEqual(runtime.metrics.value("rate_limit_store_errors_total"), 0)
        self.assertNotEqual(logs.records[-1].get("error_code"), "RATE_LIMIT_STORE_UNAVAILABLE")

    def test_shared_rate_limit_failure_returns_503_without_local_fallback(self) -> None:
        runtime = ObservabilityRuntime()
        client, _ = make_client(
            limiter=AlwaysUnavailable(),
            observability=runtime,
        )
        with client:
            response = client.get(
                "/api/v1/agent/projects/hongri/state",
                headers={"Authorization": "Bearer agent-token"},
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["code"], "RATE_LIMIT_STORE_UNAVAILABLE")
        self.assertEqual(response.headers["retry-after"], "5")
        self.assertTrue(response.headers.get("x-correlation-id"))
        self.assertEqual(runtime.metrics.value("rate_limit_store_errors_total"), 1)
        self.assertEqual(runtime.metrics.value("http_errors_total"), 1)


if __name__ == "__main__":
    unittest.main()
