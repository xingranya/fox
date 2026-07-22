"""Brand Project OS 的版本化 HTTP API 与 OpenAPI 契约。

本模块只负责传输、身份入口、请求校验和错误映射。正式状态仍由领域应用
服务和其端口负责；HTTP 层不直接操作 PostgreSQL、S3 或模型运行时。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime
from io import BytesIO
from typing import Protocol
from uuid import uuid4

from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from .authorization import (
    ConfidentialityLevel,
    ProjectAccessDenied,
    ProjectAction,
    ProjectAuthorizationService,
    ProjectPrincipal,
    PrincipalKind,
)
from .consistency import (
    ConsistencyAuthorizationError,
    ConsistencyIntegrityError,
    ConflictCode,
    WriteExecutionResult,
    WriteOutcome,
)
from .domain import Actor, ActorKind, CommandContext, ProposalDraft, ProposalReview, ReviewAction
from .evidence import EvidenceIntegrityError as QueryEvidenceIntegrityError
from .identity import (
    IdentityError,
    InteractiveEmployeePrincipal,
    OidcIdentityService,
    SensitiveValue,
)
from .object_evidence import (
    EvidenceAdmissionRequest,
    EvidenceAdmissionService,
    EvidenceIntegrityError,
    EvidencePermissionDenied,
    EvidenceRejectedError,
    EvidenceStateError,
)
from .ports import LocalAccessStorePort
from .server_baseline import build_liveness_report, build_readiness_report
from .server_config import ServerSettings, load_server_settings
from .sqlite_base import IdempotencyKeyConflict, ProjectNotFound, ResourceConflict, VersionConflict


HTTP_API_SCHEMA_VERSION = "http-api.v1"
HTTP_ERROR_SCHEMA_VERSION = "http-error.v1"
OPENAPI_VERSION = "3.1.0"
API_MAJOR_VERSION = 1
MIN_SUPPORTED_API_MAJOR = 1
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100
MAX_REQUEST_BODY_BYTES = 50 * 1024 * 1024
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")


# 该契约是 HTTP 层的机器真源；领域 Schema 仍分别维护在 contracts/phase2。
HTTP_API_CONTRACT: dict[str, object] = {
    "schema_version": HTTP_API_SCHEMA_VERSION,
    "openapi_version": OPENAPI_VERSION,
    "major_version": API_MAJOR_VERSION,
    "minimum_supported_major": MIN_SUPPORTED_API_MAJOR,
    "compatibility_window": {
        "breaking_change_requires_new_major": True,
        "minor_additive_changes_allowed": True,
        "retired_major_response": 410,
        "v1_supported_until": "下一主版本发布后至少 90 天，具体日期由发布记录确认",
    },
    "health_paths": ["/livez", "/readyz"],
    "openapi_path": "/openapi.json",
    "employee_prefix": "/api/v1/employee",
    "agent_prefix": "/api/v1/agent",
    "routes": [
        {"method": "POST", "path": "/api/v1/employee/auth/login", "surface": "employee_public", "auth": "none"},
        {"method": "GET", "path": "/api/v1/employee/auth/callback", "surface": "employee_public", "auth": "none"},
        {"method": "POST", "path": "/api/v1/employee/auth/refresh", "surface": "employee", "auth": "employee_session"},
        {"method": "POST", "path": "/api/v1/employee/auth/logout", "surface": "employee", "auth": "employee_session"},
        {"method": "GET", "path": "/api/v1/employee/me", "surface": "employee", "auth": "employee_session"},
        {"method": "GET", "path": "/api/v1/employee/projects/{project_id}", "surface": "employee", "auth": "employee_session", "action": "PROJECT_READ"},
        {"method": "GET", "path": "/api/v1/employee/projects/{project_id}/state", "surface": "employee", "auth": "employee_session", "action": "PROJECT_READ", "pagination": True},
        {"method": "GET", "path": "/api/v1/employee/projects/{project_id}/proposals", "surface": "employee", "auth": "employee_session", "action": "PROJECT_READ", "pagination": True},
        {"method": "GET", "path": "/api/v1/employee/projects/{project_id}/proposals/{proposal_id}", "surface": "employee", "auth": "employee_session", "action": "PROJECT_READ"},
        {"method": "POST", "path": "/api/v1/employee/projects/{project_id}/proposals", "surface": "employee", "auth": "employee_session", "action": "PROPOSAL_CREATE", "write": True},
        {"method": "POST", "path": "/api/v1/employee/projects/{project_id}/proposals/{proposal_id}/review", "surface": "employee", "auth": "employee_session", "action": "PROPOSAL_REVIEW", "write": True},
        {"method": "GET", "path": "/api/v1/employee/projects/{project_id}/evidence", "surface": "employee", "auth": "employee_session", "action": "EVIDENCE_READ"},
        {"method": "POST", "path": "/api/v1/employee/projects/{project_id}/evidence/uploads", "surface": "employee", "auth": "employee_session", "action": "EVIDENCE_WRITE", "write": True},
        {"method": "PUT", "path": "/api/v1/employee/projects/{project_id}/evidence/uploads/{upload_id}/content", "surface": "employee", "auth": "employee_session", "action": "EVIDENCE_WRITE", "write": True},
        {"method": "GET", "path": "/api/v1/employee/projects/{project_id}/evidence/versions/{version_id}/content", "surface": "employee", "auth": "employee_session", "action": "EVIDENCE_READ"},
        {"method": "GET", "path": "/api/v1/employee/projects/{project_id}/tasks/{packet_id}", "surface": "employee", "auth": "employee_session", "action": "TASK_READ"},
        {"method": "GET", "path": "/api/v1/agent/projects/{project_id}/state", "surface": "agent", "auth": "agent_bearer", "action": "PROJECT_READ", "pagination": True},
        {"method": "GET", "path": "/api/v1/agent/projects/{project_id}/proposals", "surface": "agent", "auth": "agent_bearer", "action": "PROJECT_READ", "pagination": True},
        {"method": "GET", "path": "/api/v1/agent/projects/{project_id}/proposals/{proposal_id}", "surface": "agent", "auth": "agent_bearer", "action": "PROJECT_READ"},
        {"method": "POST", "path": "/api/v1/agent/projects/{project_id}/proposals", "surface": "agent", "auth": "agent_bearer", "action": "PROPOSAL_CREATE", "write": True},
        {"method": "GET", "path": "/api/v1/agent/projects/{project_id}/evidence", "surface": "agent", "auth": "agent_bearer", "action": "EVIDENCE_READ"},
        {"method": "GET", "path": "/api/v1/agent/projects/{project_id}/tasks/{packet_id}", "surface": "agent", "auth": "agent_bearer", "action": "TASK_READ"},
    ],
    "error_statuses": {
        "400": "请求格式、游标或前置版本不正确",
        "401": "缺少或无效的身份凭据",
        "403": "身份有效但没有项目动作权限",
        "404": "项目、Proposal、证据或 Packet 不存在",
        "409": "正式写冲突或游标对应的状态版本已变化",
        "422": "请求字段未通过 Schema 校验",
        "429": "超过当前入口限流",
        "503": "核心依赖未就绪或可选适配器未提供",
    },
    "limits": {
        "employee_public_per_minute": 20,
        "employee_per_minute": 120,
        "agent_per_minute": 60,
    },
    "security": {
        "employee_session_is_opaque": True,
        "agent_cannot_review": True,
        "client_cannot_access_postgresql_or_s3": True,
        "remote_mcp_oauth_deferred_to": "F3.6",
    },
    "deferred": [
        "remote_mcp_oauth_and_mcp_command_identity",
        "distributed_rate_limit_store",
        "metrics_tracing_and_alerting",
    ],
}


class ApiError(RuntimeError):
    """可安全返回给调用方的稳定 HTTP 错误。"""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        details: Mapping[str, object] | None = None,
        retryable: bool = False,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = dict(details or {})
        self.retryable = retryable
        self.headers = dict(headers or {})


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    """一次限流检查的结果。"""

    allowed: bool
    limit: int
    remaining: int
    retry_after: int


class RateLimiter(Protocol):
    """可替换的限流端口；生产多副本应注入共享实现。"""

    def check(
        self,
        key: str,
        bucket: str,
        *,
        limit: int,
        window_seconds: int,
    ) -> RateLimitDecision: ...


class InMemoryRateLimiter:
    """进程内固定窗口限流器，仅作为开发和单进程部署默认实现。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._windows: dict[tuple[str, str], tuple[float, int]] = {}

    def check(
        self,
        key: str,
        bucket: str,
        *,
        limit: int,
        window_seconds: int,
    ) -> RateLimitDecision:
        now = time.monotonic()
        window_key = (key, bucket)
        with self._lock:
            started, count = self._windows.get(window_key, (now, 0))
            if now - started >= window_seconds:
                started, count = now, 0
            count += 1
            self._windows[window_key] = (started, count)
            remaining = max(0, limit - count)
            retry_after = max(1, int(window_seconds - (now - started)))
            return RateLimitDecision(
                allowed=count <= limit,
                limit=limit,
                remaining=remaining,
                retry_after=retry_after,
            )


class AgentCredentialVerifier(Protocol):
    """解析 Agent 不透明 Bearer 凭据，不把员工会话复用到 Agent 面。"""

    def __call__(self, token: str) -> ProjectPrincipal: ...


class WriteService(Protocol):
    """HTTP 层需要的最小一致性应用服务端口。"""

    def execute(
        self,
        authorization: object,
        *,
        context: CommandContext,
        command_name: str,
        operation: Callable[[], object],
        resource_type: str | None = None,
        resource_id: str | None = None,
    ) -> WriteExecutionResult: ...


@dataclass(slots=True)
class HttpApplicationDependencies:
    """组装 HTTP 适配器所需的领域端口。"""

    store: LocalAccessStorePort | None = None
    identity: OidcIdentityService | None = None
    authorization: ProjectAuthorizationService | None = None
    consistency: WriteService | None = None
    evidence: EvidenceAdmissionService | None = None
    settings: ServerSettings | None = None
    dependency_states: Callable[[], Mapping[str, bool | None]] | None = None
    agent_authenticator: AgentCredentialVerifier | None = None
    rate_limiter: RateLimiter | None = None
    cursor_secret: bytes | None = None
    max_page_size: int = MAX_PAGE_SIZE

    def __post_init__(self) -> None:
        if self.max_page_size <= 0 or self.max_page_size > MAX_PAGE_SIZE:
            raise ValueError(f"max_page_size 必须位于 1 到 {MAX_PAGE_SIZE} 之间")
        if self.rate_limiter is None:
            self.rate_limiter = InMemoryRateLimiter()


class CursorError(ValueError):
    """不透明分页游标无效。"""


class CursorCodec:
    """带完整性校验的分页游标；游标不携带正式正文。"""

    def __init__(self, secret: bytes) -> None:
        if len(secret) < 16:
            raise ValueError("分页游标签名密钥至少需要 16 字节")
        self.secret = secret

    def encode(self, *, scope: Mapping[str, object], offset: int) -> str:
        if offset < 0:
            raise ValueError("游标 offset 不能小于 0")
        payload = json.dumps(
            {"schema_version": "cursor.v1", "scope": dict(scope), "offset": offset},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        body = base64.urlsafe_b64encode(payload).rstrip(b"=")
        signature = hmac.new(self.secret, body, hashlib.sha256).digest()
        signed = body + b"." + base64.urlsafe_b64encode(signature).rstrip(b"=")
        return signed.decode("ascii")

    def decode(self, token: str) -> tuple[dict[str, object], int]:
        if not token or len(token) > 2048 or "." not in token:
            raise CursorError("分页游标格式不正确")
        body_text, signature_text = token.split(".", 1)
        try:
            body = body_text.encode("ascii")
            signature = base64.urlsafe_b64decode(signature_text + "===")
            expected = hmac.new(self.secret, body, hashlib.sha256).digest()
            if not hmac.compare_digest(signature, expected):
                raise CursorError("分页游标签名不正确")
            payload = json.loads(base64.urlsafe_b64decode(body + b"===").decode("utf-8"))
        except (UnicodeError, ValueError, TypeError, json.JSONDecodeError, binascii.Error) as error:
            raise CursorError("分页游标无法解析") from error
        if not isinstance(payload, dict) or payload.get("schema_version") != "cursor.v1":
            raise CursorError("分页游标版本不受支持")
        scope = payload.get("scope")
        offset = payload.get("offset")
        if not isinstance(scope, dict) or not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise CursorError("分页游标内容不正确")
        return scope, offset


def _default_dependency_states(dependencies: HttpApplicationDependencies) -> Mapping[str, bool | None]:
    """根据已注入适配器提供测试友好的默认依赖状态。"""

    return {
        "postgresql": dependencies.store is not None,
        "schema": dependencies.store is not None,
        "object_storage": dependencies.evidence is not None,
        "oidc": dependencies.identity is not None,
    }


def _jsonable(value: object) -> object:
    """把领域值对象安全转换为 JSON，不展开敏感值。"""

    if isinstance(value, SensitiveValue):
        return "***"
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "value") and isinstance(getattr(value, "value"), str):
        return getattr(value, "value")
    return value


def _request_id(request: Request) -> str:
    """读取中间件生成的关联 ID。"""

    return str(getattr(request.state, "request_id", "unknown"))


def _error_body(request: Request, error: ApiError) -> dict[str, object]:
    """生成不泄露凭据和原文的统一错误体。"""

    return {
        "schema_version": HTTP_ERROR_SCHEMA_VERSION,
        "code": error.code,
        "message": error.message,
        "request_id": _request_id(request),
        "retryable": error.retryable,
        "details": _jsonable(error.details),
    }


def _json_response(
    request: Request,
    value: object,
    *,
    status_code: int = 200,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """返回统一 JSON 响应并附带关联 ID。"""

    response_headers = {"X-Request-ID": _request_id(request)}
    response_headers.update(headers or {})
    return JSONResponse(_jsonable(value), status_code=status_code, headers=response_headers)


def _raise_missing_dependency(name: str) -> None:
    raise ApiError(
        503,
        "DEPENDENCY_UNAVAILABLE",
        f"服务依赖尚未就绪：{name}",
        details={"dependency": name},
        retryable=True,
    )


def _bearer_token(request: Request) -> str:
    value = request.headers.get("authorization", "")
    scheme, _, token = value.partition(" ")
    if scheme.lower() != "bearer" or not token.strip() or "\n" in token or "\r" in token:
        raise ApiError(
            401,
            "AUTHENTICATION_REQUIRED",
            "需要有效的 Bearer 凭据",
            headers={"WWW-Authenticate": 'Bearer realm="brand-project-os"'},
        )
    return token.strip()


def _require_idempotency_key(request: Request, body: Mapping[str, object] | None = None) -> str:
    value = request.headers.get("idempotency-key")
    if value is None and body is not None:
        candidate = body.get("idempotency_key")
        if isinstance(candidate, str):
            value = candidate
    if value is None or not value.strip() or len(value.strip()) > 128:
        raise ApiError(400, "MISSING_IDEMPOTENCY_KEY", "正式写请求必须提供 Idempotency-Key")
    if "\r" in value or "\n" in value:
        raise ApiError(400, "INVALID_IDEMPOTENCY_KEY", "Idempotency-Key 格式不正确")
    return value.strip()


def _parse_expected_version(request: Request, body: Mapping[str, object] | None = None) -> int:
    header = request.headers.get("if-match")
    body_value = body.get("expected_version") if body is not None else None
    if header is None and body_value is None:
        raise ApiError(400, "MISSING_IF_MATCH", "正式写请求必须提供 If-Match 预期版本")
    parsed_header: int | None = None
    if header is not None:
        raw = header.strip()
        if raw.startswith('W/"') or raw.startswith('w/"'):
            raise ApiError(400, "INVALID_IF_MATCH", "If-Match 必须使用强版本标记")
        if raw.startswith('"') and raw.endswith('"'):
            raw = raw[1:-1]
        if not raw.isdigit():
            raise ApiError(400, "INVALID_IF_MATCH", "If-Match 必须是非负整数版本")
        parsed_header = int(raw)
    parsed_body: int | None = None
    if body_value is not None:
        if not isinstance(body_value, int) or isinstance(body_value, bool) or body_value < 0:
            raise ApiError(422, "INVALID_EXPECTED_VERSION", "expected_version 必须是非负整数")
        parsed_body = body_value
    if parsed_header is not None and parsed_body is not None and parsed_header != parsed_body:
        raise ApiError(400, "VERSION_HEADER_BODY_MISMATCH", "If-Match 与 expected_version 不一致")
    return parsed_header if parsed_header is not None else parsed_body  # type: ignore[return-value]


async def _require_object_body(request: Request) -> Mapping[str, object]:
    """读取 JSON 对象；语法错误与字段语义错误分开返回。"""

    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_REQUEST_BODY_BYTES:
                raise ApiError(413, "REQUEST_TOO_LARGE", "请求体超过允许大小")
        except ValueError as error:
            raise ApiError(400, "INVALID_CONTENT_LENGTH", "Content-Length 格式不正确") from error
    try:
        value = await request.json()
    except ApiError:
        raise
    except Exception as error:
        raise ApiError(400, "INVALID_JSON", "请求体不是有效 JSON") from error
    if not isinstance(value, Mapping):
        raise ApiError(422, "OBJECT_REQUIRED", "请求体必须是 JSON 对象")
    return value


def _require_fields(
    body: Mapping[str, object],
    *,
    allowed: set[str],
    required: set[str],
) -> None:
    unknown = sorted(set(body) - allowed)
    missing = sorted(required - set(body))
    if unknown:
        raise ApiError(422, "UNKNOWN_FIELD", "请求包含未声明字段", details={"fields": unknown})
    if missing:
        raise ApiError(422, "MISSING_FIELD", "请求缺少必填字段", details={"fields": missing})


def _text(body: Mapping[str, object], field: str, *, allow_empty: bool = False) -> str:
    value = body.get(field)
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ApiError(422, "INVALID_FIELD", f"{field} 必须是非空字符串", details={"field": field})
    return value.strip()


def _optional_text(body: Mapping[str, object], field: str) -> str | None:
    value = body.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ApiError(422, "INVALID_FIELD", f"{field} 必须是字符串或 null", details={"field": field})
    return value.strip()


def _confidentiality(value: object) -> ConfidentialityLevel:
    if not isinstance(value, str):
        raise ApiError(422, "INVALID_CONFIDENTIALITY", "confidentiality 必须是 P0-P3")
    try:
        return ConfidentialityLevel(value)
    except ValueError as error:
        raise ApiError(422, "INVALID_CONFIDENTIALITY", "confidentiality 必须是 P0-P3") from error


def _parse_limit(request: Request, max_page_size: int) -> int:
    value = request.query_params.get("limit")
    if value is None:
        return min(DEFAULT_PAGE_SIZE, max_page_size)
    try:
        limit = int(value)
    except ValueError as error:
        raise ApiError(422, "INVALID_LIMIT", "limit 必须是正整数") from error
    if limit <= 0 or limit > max_page_size:
        raise ApiError(422, "LIMIT_OUT_OF_RANGE", f"limit 必须位于 1 到 {max_page_size} 之间")
    return limit


def _principal_payload(principal: InteractiveEmployeePrincipal) -> dict[str, object]:
    return {
        "employee_id": principal.employee_id,
        "display_name": principal.display_name,
        "session_id": principal.session_id,
        "issuer": principal.issuer,
        "subject": principal.subject,
        "authenticated_at": principal.authenticated_at.isoformat(),
    }


def _actor_for_principal(principal: ProjectPrincipal) -> Actor:
    mapping = {
        PrincipalKind.EMPLOYEE: ActorKind.HUMAN,
        PrincipalKind.AI: ActorKind.AI,
        PrincipalKind.WORKFLOW: ActorKind.WORKFLOW,
        PrincipalKind.SYSTEM: ActorKind.SYSTEM,
    }
    actor_kind = mapping.get(principal.kind)
    if actor_kind is None:
        raise ApiError(
            403,
            "AGENT_WRITE_NOT_READY",
            "当前 Agent 身份尚未获得正式写入命令身份",
            details={"principal_kind": principal.kind.value},
        )
    return Actor(actor_kind, principal.principal_id)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """为每个请求生成关联 ID，并在入口处执行粗粒度限流。"""

    def __init__(self, app, *, dependencies: HttpApplicationDependencies) -> None:
        super().__init__(app)
        self.dependencies = dependencies

    async def dispatch(self, request: Request, call_next):
        incoming = request.headers.get("x-request-id")
        request_id = incoming.strip() if incoming and REQUEST_ID_PATTERN.fullmatch(incoming.strip()) else f"req_{uuid4().hex}"
        request.state.request_id = request_id

        path = request.url.path
        if path.startswith("/api/v1/employee/auth"):
            bucket, limit = "employee_public", int(HTTP_API_CONTRACT["limits"]["employee_public_per_minute"])
        elif path.startswith("/api/v1/employee"):
            bucket, limit = "employee", int(HTTP_API_CONTRACT["limits"]["employee_per_minute"])
        elif path.startswith("/api/v1/agent"):
            bucket, limit = "agent", int(HTTP_API_CONTRACT["limits"]["agent_per_minute"])
        else:
            bucket, limit = "public", 0

        if limit and self.dependencies.rate_limiter is not None:
            client = request.client.host if request.client is not None else "unknown"
            decision = self.dependencies.rate_limiter.check(
                client,
                bucket,
                limit=limit,
                window_seconds=60,
            )
            request.state.rate_limit_decision = decision
            if not decision.allowed:
                error = ApiError(
                    429,
                    "RATE_LIMITED",
                    "请求过于频繁，请稍后再试",
                    details={"bucket": bucket},
                    retryable=True,
                    headers={"Retry-After": str(decision.retry_after)},
                )
                response = _json_response(
                    request,
                    _error_body(request, error),
                    status_code=error.status_code,
                    headers={
                        "Retry-After": str(decision.retry_after),
                        "X-RateLimit-Limit": str(decision.limit),
                        "X-RateLimit-Remaining": str(decision.remaining),
                    },
                )
                return response

        response = await call_next(request)
        response.headers.setdefault("X-Request-ID", request_id)
        decision = getattr(request.state, "rate_limit_decision", None)
        if decision is not None:
            response.headers.setdefault("X-RateLimit-Limit", str(decision.limit))
            response.headers.setdefault("X-RateLimit-Remaining", str(decision.remaining))
        return response


class ApiExceptionMiddleware(BaseHTTPMiddleware):
    """把领域异常收敛到稳定错误 Schema，避免把内部堆栈暴露给客户端。"""

    async def dispatch(self, request: Request, call_next):
        try:
            return await call_next(request)
        except ApiError as error:
            return _json_response(
                request,
                _error_body(request, error),
                status_code=error.status_code,
                headers=error.headers,
            )
        except ProjectNotFound as error:
            return _json_response(
                request,
                _error_body(request, ApiError(404, "NOT_FOUND", str(error))),
                status_code=404,
            )
        except ProjectAccessDenied as error:
            return _json_response(
                request,
                _error_body(request, ApiError(403, "PROJECT_ACCESS_DENIED", str(error))),
                status_code=403,
            )
        except ConsistencyAuthorizationError as error:
            return _json_response(
                request,
                _error_body(request, ApiError(403, "WRITE_AUTHORIZATION_MISMATCH", str(error))),
                status_code=403,
            )
        except (IdentityError, PermissionError) as error:
            return _json_response(
                request,
                _error_body(
                    request,
                    ApiError(
                        401,
                        "AUTHENTICATION_FAILED",
                        "身份凭据无效或已失效",
                        headers={"WWW-Authenticate": 'Bearer realm="brand-project-os"'},
                    ),
                ),
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="brand-project-os"'},
            )
        except VersionConflict as error:
            return _json_response(
                request,
                _error_body(
                    request,
                    ApiError(
                        409,
                        ConflictCode.VERSION_MISMATCH.value,
                        str(error),
                        details={"expected_version": error.expected, "current_version": error.current},
                    ),
                ),
                status_code=409,
            )
        except IdempotencyKeyConflict as error:
            return _json_response(
                request,
                _error_body(
                    request,
                    ApiError(409, ConflictCode.IDEMPOTENCY_KEY_REUSED.value, str(error)),
                ),
                status_code=409,
            )
        except ResourceConflict as error:
            return _json_response(
                request,
                _error_body(
                    request,
                    ApiError(409, ConflictCode.RESOURCE_STATE_CHANGED.value, str(error)),
                ),
                status_code=409,
            )
        except ConsistencyIntegrityError as error:
            return _json_response(
                request,
                _error_body(
                    request,
                    ApiError(503, "STATE_INTEGRITY_UNAVAILABLE", str(error), retryable=True),
                ),
                status_code=503,
            )
        except EvidencePermissionDenied as error:
            return _json_response(
                request,
                _error_body(request, ApiError(403, "EVIDENCE_ACCESS_DENIED", str(error))),
                status_code=403,
            )
        except (EvidenceStateError, EvidenceRejectedError) as error:
            return _json_response(
                request,
                _error_body(request, ApiError(409, "EVIDENCE_STATE_CONFLICT", str(error))),
                status_code=409,
            )
        except (EvidenceIntegrityError, QueryEvidenceIntegrityError) as error:
            return _json_response(
                request,
                _error_body(request, ApiError(503, "EVIDENCE_INTEGRITY_UNAVAILABLE", str(error), retryable=True)),
                status_code=503,
            )
        except ValueError as error:
            return _json_response(
                request,
                _error_body(request, ApiError(422, "VALIDATION_ERROR", str(error))),
                status_code=422,
            )
        except Exception:
            return _json_response(
                request,
                _error_body(
                    request,
                    ApiError(500, "INTERNAL_ERROR", "服务暂时无法完成请求", retryable=True),
                ),
                status_code=500,
            )


class HttpApplication:
    """把领域端口组装为 Employee/Agent 两个隔离入口。"""

    def __init__(self, dependencies: HttpApplicationDependencies) -> None:
        self.dependencies = dependencies
        settings = dependencies.settings
        self.settings = settings or load_server_settings(environ={})
        secret = dependencies.cursor_secret
        if secret is None:
            if self.settings.session_encryption_key is not None:
                secret = hashlib.sha256(
                    self.settings.session_encryption_key.reveal().encode("utf-8")
                ).digest()
            else:
                secret = os.urandom(32)
        self.cursor = CursorCodec(secret)

    def routes(self) -> list[Route]:
        """返回所有已发布的路由；Agent 面没有人工批准路由。"""

        return [
            Route("/livez", self.livez, methods=["GET"]),
            Route("/readyz", self.readyz, methods=["GET"]),
            Route("/openapi.json", self.openapi, methods=["GET"]),
            Route("/api/v1/employee/auth/login", self.employee_login, methods=["POST"]),
            Route("/api/v1/employee/auth/callback", self.employee_callback, methods=["GET"]),
            Route("/api/v1/employee/auth/refresh", self.employee_refresh, methods=["POST"]),
            Route("/api/v1/employee/auth/logout", self.employee_logout, methods=["POST"]),
            Route("/api/v1/employee/me", self.employee_me, methods=["GET"]),
            Route("/api/v1/employee/projects/{project_id}", self.employee_project, methods=["GET"]),
            Route("/api/v1/employee/projects/{project_id}/state", self.employee_state, methods=["GET"]),
            Route("/api/v1/employee/projects/{project_id}/proposals", self.employee_proposals, methods=["GET"]),
            Route("/api/v1/employee/projects/{project_id}/proposals", self.employee_create_proposal, methods=["POST"]),
            Route("/api/v1/employee/projects/{project_id}/proposals/{proposal_id}", self.employee_proposal, methods=["GET"]),
            Route("/api/v1/employee/projects/{project_id}/proposals/{proposal_id}/review", self.employee_review, methods=["POST"]),
            Route("/api/v1/employee/projects/{project_id}/evidence", self.employee_evidence, methods=["GET"]),
            Route("/api/v1/employee/projects/{project_id}/evidence/uploads", self.employee_begin_upload, methods=["POST"]),
            Route("/api/v1/employee/projects/{project_id}/evidence/uploads/{upload_id}/content", self.employee_upload_content, methods=["PUT"]),
            Route("/api/v1/employee/projects/{project_id}/evidence/versions/{version_id}/content", self.employee_stream_evidence, methods=["GET"]),
            Route("/api/v1/employee/projects/{project_id}/tasks/{packet_id}", self.employee_task_packet, methods=["GET"]),
            Route("/api/v1/agent/projects/{project_id}/state", self.agent_state, methods=["GET"]),
            Route("/api/v1/agent/projects/{project_id}/proposals", self.agent_proposals, methods=["GET"]),
            Route("/api/v1/agent/projects/{project_id}/proposals", self.agent_create_proposal, methods=["POST"]),
            Route("/api/v1/agent/projects/{project_id}/proposals/{proposal_id}", self.agent_proposal, methods=["GET"]),
            Route("/api/v1/agent/projects/{project_id}/evidence", self.agent_evidence, methods=["GET"]),
            Route("/api/v1/agent/projects/{project_id}/tasks/{packet_id}", self.agent_task_packet, methods=["GET"]),
            Route("/api/v{version:int}", self.retired_version, methods=["GET", "POST", "PUT", "PATCH", "DELETE"]),
            Route("/api/v{version:int}/{path:path}", self.retired_version, methods=["GET", "POST", "PUT", "PATCH", "DELETE"]),
        ]

    async def livez(self, request: Request) -> Response:
        return _json_response(request, build_liveness_report().to_dict())

    async def readyz(self, request: Request) -> Response:
        try:
            states = (
                self.dependencies.dependency_states()
                if self.dependencies.dependency_states is not None
                else _default_dependency_states(self.dependencies)
            )
        except Exception:
            states = {}
        report = build_readiness_report(self.settings, dependency_states=states)
        status = 200 if report.status == "ready" else 503
        return _json_response(request, report.to_dict(), status_code=status)

    async def openapi(self, request: Request) -> Response:
        return _json_response(
            request,
            build_openapi_document(),
            headers={"Cache-Control": "no-store"},
        )

    async def retired_version(self, request: Request) -> Response:
        version = int(request.path_params.get("version", 0))
        if version == API_MAJOR_VERSION:
            raise ApiError(404, "NOT_FOUND", "请求路径不存在")
        raise ApiError(
            410,
            "API_VERSION_RETIRED",
            "请求的 API 主版本已经停止服务",
            details={"requested_major": version, "current_major": API_MAJOR_VERSION},
            headers={"X-API-Version-Status": "retired"},
        )

    async def employee_login(self, request: Request) -> Response:
        identity = self.dependencies.identity
        if identity is None:
            _raise_missing_dependency("oidc")
        result = identity.begin_login()
        return _json_response(
            request,
            {
                "schema_version": "employee-login.v1",
                "transaction_id": result.transaction_id,
                "authorization_url": result.authorization_url,
                "expires_at": result.expires_at,
            },
            status_code=200,
            headers={"Cache-Control": "no-store"},
        )

    async def employee_callback(self, request: Request) -> Response:
        identity = self.dependencies.identity
        if identity is None:
            _raise_missing_dependency("oidc")
        state = request.query_params.get("state", "")
        code = request.query_params.get("code", "")
        if not state or not code:
            raise ApiError(400, "MISSING_OIDC_CALLBACK", "OIDC 回调必须包含 state 和 code")
        try:
            result = identity.complete_login(state=state, code=code)
        except (IdentityError, ValueError) as error:
            raise ApiError(401, "OIDC_CALLBACK_REJECTED", "OIDC 回调未通过校验") from error
        return _json_response(
            request,
            {
                "schema_version": "employee-session.v1",
                "session_id": result.credential.session_id,
                "session_token": result.credential.token.reveal(),
                "expires_at": result.credential.expires_at,
                "employee": _principal_payload(result.principal),
            },
            headers={"Cache-Control": "no-store"},
        )

    async def employee_refresh(self, request: Request) -> Response:
        identity = self.dependencies.identity
        if identity is None:
            _raise_missing_dependency("oidc")
        token = _bearer_token(request)
        try:
            principal = identity.refresh_session(token)
        except (IdentityError, ValueError) as error:
            raise ApiError(401, "SESSION_REFRESH_REJECTED", "会话刷新失败") from error
        return _json_response(
            request,
            {"schema_version": "employee-session-refresh.v1", "employee": _principal_payload(principal)},
            headers={"Cache-Control": "no-store"},
        )

    async def employee_logout(self, request: Request) -> Response:
        identity = self.dependencies.identity
        if identity is None:
            _raise_missing_dependency("oidc")
        token = _bearer_token(request)
        try:
            identity.revoke_session(token)
        except (IdentityError, ValueError) as error:
            raise ApiError(401, "SESSION_REVOKE_REJECTED", "会话撤销失败") from error
        return _json_response(request, {"schema_version": "employee-logout.v1", "revoked": True})

    async def employee_me(self, request: Request) -> Response:
        principal, _ = self._employee_identity(request)
        return _json_response(
            request,
            {"schema_version": "employee-principal.v1", "employee": _principal_payload(principal)},
        )

    def _store(self) -> LocalAccessStorePort:
        if self.dependencies.store is None:
            _raise_missing_dependency("postgresql")
        return self.dependencies.store

    def _employee_identity(self, request: Request) -> tuple[InteractiveEmployeePrincipal, str]:
        identity = self.dependencies.identity
        if identity is None:
            _raise_missing_dependency("oidc")
        token = _bearer_token(request)
        try:
            principal = identity.authenticate(token)
        except (IdentityError, ValueError) as error:
            raise ApiError(
                401,
                "AUTHENTICATION_FAILED",
                "员工会话无效或已过期",
                headers={"WWW-Authenticate": 'Bearer realm="brand-project-os"'},
            ) from error
        request.state.employee_principal = principal
        return principal, token

    def _agent_identity(self, request: Request) -> ProjectPrincipal:
        verifier = self.dependencies.agent_authenticator
        if verifier is None:
            _raise_missing_dependency("agent_authenticator")
        token = _bearer_token(request)
        try:
            principal = verifier(token)
        except ApiError:
            raise
        except Exception as error:
            raise ApiError(
                401,
                "AGENT_AUTHENTICATION_FAILED",
                "Agent 凭据无效或已撤销",
                headers={"WWW-Authenticate": 'Bearer realm="brand-project-os-agent"'},
            ) from error
        if not isinstance(principal, ProjectPrincipal) or principal.kind is PrincipalKind.EMPLOYEE:
            raise ApiError(403, "EMPLOYEE_TOKEN_NOT_ALLOWED", "员工会话不能用于 Agent 入口")
        request.state.agent_principal = principal
        return principal

    def _authorize(
        self,
        principal: ProjectPrincipal,
        *,
        project_id: str,
        action: ProjectAction,
        confidentiality: ConfidentialityLevel = ConfidentialityLevel.P0,
    ) -> object:
        service = self.dependencies.authorization
        if service is None:
            _raise_missing_dependency("project_authorization")
        try:
            return service.authorize(
                principal,
                project_id=project_id,
                action=action,
                resource_confidentiality=confidentiality,
            )
        except ProjectAccessDenied as error:
            raise ApiError(403, "PROJECT_ACCESS_DENIED", str(error)) from error

    def _page(
        self,
        request: Request,
        items: Sequence[Mapping[str, object]],
        *,
        scope: Mapping[str, object],
        state_version: int,
    ) -> tuple[list[Mapping[str, object]], dict[str, object]]:
        limit = _parse_limit(request, self.dependencies.max_page_size)
        offset = 0
        cursor_token = request.query_params.get("cursor")
        expected_scope = {**dict(scope), "state_version": state_version}
        if cursor_token:
            try:
                cursor_scope, offset = self.cursor.decode(cursor_token)
            except CursorError as error:
                raise ApiError(400, "INVALID_CURSOR", str(error)) from error
            if cursor_scope != expected_scope:
                raise ApiError(
                    409,
                    "PAGINATION_CURSOR_STALE",
                    "分页游标对应的项目状态已经变化，请重新加载",
                    details={"state_version": state_version},
                )
        if offset > len(items):
            raise ApiError(400, "INVALID_CURSOR", "分页游标超出当前结果范围")
        selected = list(items[offset : offset + limit])
        next_offset = offset + len(selected)
        has_more = next_offset < len(items)
        next_cursor = self.cursor.encode(scope=expected_scope, offset=next_offset) if has_more else None
        return selected, {"limit": limit, "next_cursor": next_cursor, "has_more": has_more}

    def _project_read(
        self,
        request: Request,
        *,
        surface: str,
        action: ProjectAction = ProjectAction.PROJECT_READ,
    ) -> tuple[ProjectPrincipal, str, Mapping[str, object], int]:
        project_id = str(request.path_params["project_id"])
        if surface == "employee":
            principal, _ = self._employee_identity(request)
            project_principal = ProjectPrincipal(PrincipalKind.EMPLOYEE, principal.employee_id)
        else:
            project_principal = self._agent_identity(request)
        self._authorize(project_principal, project_id=project_id, action=action)
        store = self._store()
        project = dict(store.get_project(project_id))
        version = int(store.get_project_version(project_id))
        return project_principal, project_id, project, version

    async def employee_project(self, request: Request) -> Response:
        _, project_id, project, version = self._project_read(request, surface="employee")
        return _json_response(
            request,
            {
                "schema_version": "project-view.v1",
                "project": {**project, "state_version": version},
                "authority": {"current": "postgresql", "agent_can_approve": False},
            },
        )

    async def _state(self, request: Request, *, surface: str) -> Response:
        _, project_id, _, version = self._project_read(request, surface=surface)
        items = list(self._store().get_current_state(project_id))
        selected, pagination = self._page(
            request,
            items,
            scope={"surface": surface, "resource": "state", "project_id": project_id},
            state_version=version,
        )
        return _json_response(
            request,
            {
                "schema_version": "state-page.v1",
                "project_id": project_id,
                "state_version": version,
                "items": selected,
                "pagination": pagination,
            },
        )

    async def employee_state(self, request: Request) -> Response:
        return await self._state(request, surface="employee")

    async def agent_state(self, request: Request) -> Response:
        return await self._state(request, surface="agent")

    async def _proposals(self, request: Request, *, surface: str) -> Response:
        _, project_id, _, version = self._project_read(request, surface=surface)
        status = request.query_params.get("status")
        if status is not None and (not status.strip() or len(status) > 64):
            raise ApiError(422, "INVALID_STATUS", "status 格式不正确")
        items = list(self._store().list_proposals(project_id, status=status))
        selected, pagination = self._page(
            request,
            items,
            scope={"surface": surface, "resource": "proposals", "project_id": project_id, "status": status},
            state_version=version,
        )
        return _json_response(
            request,
            {
                "schema_version": "proposal-page.v1",
                "project_id": project_id,
                "state_version": version,
                "items": selected,
                "pagination": pagination,
            },
        )

    async def employee_proposals(self, request: Request) -> Response:
        return await self._proposals(request, surface="employee")

    async def agent_proposals(self, request: Request) -> Response:
        return await self._proposals(request, surface="agent")

    async def _proposal(self, request: Request, *, surface: str) -> Response:
        _, project_id, _, version = self._project_read(request, surface=surface)
        proposal_id = str(request.path_params["proposal_id"])
        proposals = self._store().list_proposals(project_id)
        proposal = next((item for item in proposals if str(item.get("proposal_id")) == proposal_id), None)
        if proposal is None:
            raise ApiError(404, "PROPOSAL_NOT_FOUND", "Proposal 不存在", details={"proposal_id": proposal_id})
        return _json_response(
            request,
            {
                "schema_version": "proposal-detail.v1",
                "project_id": project_id,
                "state_version": version,
                "proposal": proposal,
                "history": self._store().get_proposal_history(project_id, proposal_id),
            },
        )

    async def employee_proposal(self, request: Request) -> Response:
        return await self._proposal(request, surface="employee")

    async def agent_proposal(self, request: Request) -> Response:
        return await self._proposal(request, surface="agent")

    async def _evidence_ref(self, request: Request, *, surface: str) -> Response:
        project_id = str(request.path_params["project_id"])
        if surface == "employee":
            principal, _ = self._employee_identity(request)
            project_principal = ProjectPrincipal(PrincipalKind.EMPLOYEE, principal.employee_id)
        else:
            project_principal = self._agent_identity(request)
        self._authorize(project_principal, project_id=project_id, action=ProjectAction.EVIDENCE_READ)
        evidence_ref = request.query_params.get("evidence_ref")
        if not evidence_ref or len(evidence_ref) > 512:
            raise ApiError(422, "INVALID_EVIDENCE_REF", "必须提供有效的 evidence_ref")
        value = self._store().resolve_evidence_ref(project_id, evidence_ref)
        return _json_response(
            request,
            {"schema_version": "evidence-view.v1", "project_id": project_id, "evidence": value},
        )

    async def employee_evidence(self, request: Request) -> Response:
        return await self._evidence_ref(request, surface="employee")

    async def agent_evidence(self, request: Request) -> Response:
        return await self._evidence_ref(request, surface="agent")

    async def _task_packet(self, request: Request, *, surface: str) -> Response:
        _, project_id, _, version = self._project_read(request, surface=surface, action=ProjectAction.TASK_READ)
        packet_id = str(request.path_params["packet_id"])
        packet = self._store().get_task_packet(project_id, packet_id)
        return _json_response(
            request,
            {"schema_version": "task-packet-view.v1", "project_id": project_id, "state_version": version, "packet": packet},
        )

    async def employee_task_packet(self, request: Request) -> Response:
        return await self._task_packet(request, surface="employee")

    async def agent_task_packet(self, request: Request) -> Response:
        return await self._task_packet(request, surface="agent")

    async def _proposal_create(self, request: Request, *, surface: str) -> Response:
        body = await _require_object_body(request)
        allowed = {
            "proposal_id",
            "proposal_kind",
            "classification",
            "subject_id",
            "before",
            "after",
            "reason",
            "impact_scope",
            "evidence_refs",
            "supersedes_proposal_id",
            "source_meeting_item_id",
            "valid_from",
            "valid_until",
            "expected_version",
            "idempotency_key",
        }
        required = {
            "proposal_id",
            "proposal_kind",
            "classification",
            "after",
            "reason",
            "impact_scope",
            "evidence_refs",
        }
        _require_fields(body, allowed=allowed, required=required)
        project_id = str(request.path_params["project_id"])
        if surface == "employee":
            employee, token = self._employee_identity(request)
            principal = ProjectPrincipal(PrincipalKind.EMPLOYEE, employee.employee_id)
        else:
            token = ""
            principal = self._agent_identity(request)
            if principal.kind not in {PrincipalKind.AI, PrincipalKind.WORKFLOW, PrincipalKind.SYSTEM}:
                raise ApiError(
                    403,
                    "AGENT_WRITE_NOT_READY",
                    "当前 Agent 身份尚未获得正式写入命令身份",
                    details={"principal_kind": principal.kind.value},
                )
        authorization = self._authorize(
            principal,
            project_id=project_id,
            action=ProjectAction.PROPOSAL_CREATE,
        )
        expected_version = _parse_expected_version(request, body)
        idempotency_key = _require_idempotency_key(request, body)

        after = body.get("after")
        before = body.get("before")
        evidence_refs = body.get("evidence_refs")
        if not isinstance(after, Mapping):
            raise ApiError(422, "INVALID_AFTER", "after 必须是对象")
        if before is not None and not isinstance(before, Mapping):
            raise ApiError(422, "INVALID_BEFORE", "before 必须是对象或 null")
        if (
            not isinstance(evidence_refs, Sequence)
            or isinstance(evidence_refs, (str, bytes))
            or not all(isinstance(item, str) and item.strip() for item in evidence_refs)
        ):
            raise ApiError(422, "INVALID_EVIDENCE_REFS", "evidence_refs 必须是非空字符串数组")
        try:
            draft = ProposalDraft(
                proposal_id=_text(body, "proposal_id"),
                proposal_kind=_text(body, "proposal_kind"),
                classification=_text(body, "classification"),
                subject_id=_optional_text(body, "subject_id"),
                before=before,
                after=after,
                reason=_text(body, "reason"),
                impact_scope=_text(body, "impact_scope"),
                evidence_refs=tuple(str(item).strip() for item in evidence_refs),
                supersedes_proposal_id=_optional_text(body, "supersedes_proposal_id"),
                source_meeting_item_id=_optional_text(body, "source_meeting_item_id"),
                valid_from=_optional_text(body, "valid_from"),
                valid_until=_optional_text(body, "valid_until"),
            )
        except ValueError as error:
            raise ApiError(422, "INVALID_PROPOSAL", str(error)) from error

        if surface == "employee" and self.dependencies.identity is not None:
            try:
                context = self.dependencies.identity.bind_human_command_context(
                    token,
                    project_id=project_id,
                    command_name="create_proposal",
                    idempotency_key=idempotency_key,
                    expected_version=expected_version,
                )
            except (IdentityError, ValueError) as error:
                raise ApiError(401, "IDENTITY_ASSERTION_FAILED", "无法绑定员工命令身份") from error
        else:
            context = CommandContext(
                project_id,
                _actor_for_principal(principal),
                idempotency_key,
                expected_version,
            )
        consistency = self.dependencies.consistency
        if consistency is None:
            _raise_missing_dependency("write_consistency")
        result = consistency.execute(
            authorization,
            context=context,
            command_name="create_proposal",
            operation=lambda: self._store().create_proposal(context, draft),
            resource_type="proposal",
            resource_id=draft.proposal_id,
        )
        return self._write_response(
            request,
            result,
            project_id=project_id,
            schema_version="proposal-create-result.v1",
            resource_id=draft.proposal_id,
        )

    async def employee_create_proposal(self, request: Request) -> Response:
        return await self._proposal_create(request, surface="employee")

    async def agent_create_proposal(self, request: Request) -> Response:
        return await self._proposal_create(request, surface="agent")

    def _write_response(
        self,
        request: Request,
        result: WriteExecutionResult,
        *,
        project_id: str,
        schema_version: str,
        resource_id: str,
    ) -> Response:
        if result.outcome is WriteOutcome.CONFLICT:
            conflict = result.conflict
            if conflict is None:
                raise ApiError(503, "CONFLICT_REPORT_UNAVAILABLE", "冲突报告无法生成", retryable=True)
            raise ApiError(
                409,
                conflict.code.value,
                conflict.reason,
                details=_jsonable(conflict),
            )
        command = result.result
        if command is None:
            raise ApiError(503, "WRITE_RESULT_UNAVAILABLE", "正式写结果缺失", retryable=True)
        status = 201 if result.outcome is WriteOutcome.COMMITTED else 200
        return _json_response(
            request,
            {
                "schema_version": schema_version,
                "project_id": project_id,
                "resource_id": resource_id,
                "outcome": result.outcome,
                "command": command,
                "changes_current_state": False,
            },
            status_code=status,
            headers={"ETag": f'"{command.project_version}"'},
        )

    async def employee_review(self, request: Request) -> Response:
        employee, token = self._employee_identity(request)
        project_id = str(request.path_params["project_id"])
        proposal_id = str(request.path_params["proposal_id"])
        principal = ProjectPrincipal(PrincipalKind.EMPLOYEE, employee.employee_id)
        authorization = self._authorize(
            principal,
            project_id=project_id,
            action=ProjectAction.PROPOSAL_REVIEW,
        )
        body = await _require_object_body(request)
        _require_fields(
            body,
            allowed={"action", "reason", "replacement_after", "expected_version", "idempotency_key"},
            required={"action", "reason"},
        )
        action_text = _text(body, "action")
        try:
            action = ReviewAction(action_text)
        except ValueError as error:
            raise ApiError(422, "INVALID_REVIEW_ACTION", "人工评审动作无效") from error
        replacement = body.get("replacement_after")
        if replacement is not None and not isinstance(replacement, Mapping):
            raise ApiError(422, "INVALID_REPLACEMENT", "replacement_after 必须是对象或 null")
        if action is ReviewAction.MODIFY_AND_APPROVE and replacement is None:
            raise ApiError(422, "REPLACEMENT_REQUIRED", "修改后批准必须提供 replacement_after")
        if action is not ReviewAction.MODIFY_AND_APPROVE and replacement is not None:
            raise ApiError(422, "REPLACEMENT_NOT_ALLOWED", "只有修改后批准可以提交 replacement_after")
        expected_version = _parse_expected_version(request, body)
        idempotency_key = _require_idempotency_key(request, body)
        identity = self.dependencies.identity
        if identity is None:
            _raise_missing_dependency("oidc")
        try:
            context = identity.bind_human_command_context(
                token,
                project_id=project_id,
                command_name="review_proposal",
                idempotency_key=idempotency_key,
                expected_version=expected_version,
            )
        except (IdentityError, ValueError) as error:
            raise ApiError(401, "IDENTITY_ASSERTION_FAILED", "无法绑定员工命令身份") from error
        review = ProposalReview(
            proposal_id=proposal_id,
            action=action,
            reason=_text(body, "reason"),
            replacement_after=replacement,
        )
        consistency = self.dependencies.consistency
        if consistency is None:
            _raise_missing_dependency("write_consistency")
        result = consistency.execute(
            authorization,
            context=context,
            command_name="review_proposal",
            operation=lambda: self._store().review_proposal(context, review),
            resource_type="proposal",
            resource_id=proposal_id,
        )
        response = self._write_response(
            request,
            result,
            project_id=project_id,
            schema_version="proposal-review-result.v1",
            resource_id=proposal_id,
        )
        return response

    async def employee_begin_upload(self, request: Request) -> Response:
        employee, _ = self._employee_identity(request)
        project_id = str(request.path_params["project_id"])
        principal = ProjectPrincipal(PrincipalKind.EMPLOYEE, employee.employee_id)
        body = await _require_object_body(request)
        _require_fields(
            body,
            allowed={
                "logical_source_id",
                "original_filename",
                "expected_sha256",
                "expected_size_bytes",
                "expected_media_type",
                "confidentiality",
                "idempotency_key",
            },
            required={
                "logical_source_id",
                "original_filename",
                "expected_sha256",
                "expected_size_bytes",
                "expected_media_type",
                "confidentiality",
            },
        )
        confidentiality = _confidentiality(body.get("confidentiality"))
        self._authorize(
            principal,
            project_id=project_id,
            action=ProjectAction.EVIDENCE_WRITE,
            confidentiality=confidentiality,
        )
        evidence = self.dependencies.evidence
        if evidence is None:
            _raise_missing_dependency("object_storage")
        sha256 = body.get("expected_sha256")
        size = body.get("expected_size_bytes")
        if not isinstance(sha256, str) or not SHA256_PATTERN.fullmatch(sha256):
            raise ApiError(422, "INVALID_SHA256", "expected_sha256 必须是完整的小写 SHA-256")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise ApiError(422, "INVALID_SIZE", "expected_size_bytes 必须是非负整数")
        idempotency_key = _require_idempotency_key(request, body)
        admission = EvidenceAdmissionRequest(
            project_id=project_id,
            logical_source_id=_text(body, "logical_source_id"),
            original_filename=_text(body, "original_filename"),
            expected_sha256=sha256,
            expected_size_bytes=size,
            expected_media_type=_text(body, "expected_media_type"),
            confidentiality=confidentiality.value,
            idempotency_key=idempotency_key,
        )
        upload = evidence.begin_upload(admission)
        return _json_response(
            request,
            {
                "schema_version": "evidence-upload.v1",
                "project_id": project_id,
                "upload": upload,
                "content_endpoint": f"/api/v1/employee/projects/{project_id}/evidence/uploads/{upload.upload_id}/content",
            },
            status_code=201,
        )

    async def employee_upload_content(self, request: Request) -> Response:
        employee, _ = self._employee_identity(request)
        project_id = str(request.path_params["project_id"])
        upload_id = str(request.path_params["upload_id"])
        principal = ProjectPrincipal(PrincipalKind.EMPLOYEE, employee.employee_id)
        evidence = self.dependencies.evidence
        if evidence is None:
            _raise_missing_dependency("object_storage")
        upload = evidence.metadata.get_upload(upload_id)
        if upload.project_id != project_id:
            raise ApiError(404, "UPLOAD_NOT_FOUND", "上传会话不存在")
        self._authorize(
            principal,
            project_id=project_id,
            action=ProjectAction.EVIDENCE_WRITE,
            confidentiality=ConfidentialityLevel(upload.confidentiality),
        )
        raw = await request.body()
        if len(raw) > MAX_REQUEST_BODY_BYTES:
            raise ApiError(413, "REQUEST_TOO_LARGE", "上传内容超过允许大小")
        updated = evidence.upload_and_quarantine(upload_id, BytesIO(raw))
        return _json_response(
            request,
            {"schema_version": "evidence-upload-content.v1", "project_id": project_id, "upload": updated},
        )

    async def employee_stream_evidence(self, request: Request) -> Response:
        employee, _ = self._employee_identity(request)
        project_id = str(request.path_params["project_id"])
        version_id = str(request.path_params["version_id"])
        principal = ProjectPrincipal(PrincipalKind.EMPLOYEE, employee.employee_id)
        evidence = self.dependencies.evidence
        if evidence is None:
            _raise_missing_dependency("object_storage")
        version = evidence.metadata.get_version(version_id)
        if version.project_id != project_id:
            raise ApiError(404, "EVIDENCE_VERSION_NOT_FOUND", "证据版本不存在")
        self._authorize(
            principal,
            project_id=project_id,
            action=ProjectAction.EVIDENCE_READ,
            confidentiality=ConfidentialityLevel(version.confidentiality),
        )
        stream = evidence.stream_active(version_id)
        filename = version.original_filename.replace('"', "_")
        return StreamingResponse(
            stream,
            media_type=version.media_type,
            headers={
                "X-Request-ID": _request_id(request),
                "ETag": f'"{version.sha256}"',
                "X-Evidence-Version": version.version_id,
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )


def _openapi_parameter(name: str, location: str, schema: Mapping[str, object], *, required: bool = False) -> dict[str, object]:
    """创建 OpenAPI 参数，避免路由定义散落未版本化字段。"""

    return {"name": name, "in": location, "required": required, "schema": dict(schema)}


def _openapi_response(description: str, schema_ref: str | None = None) -> dict[str, object]:
    response: dict[str, object] = {"description": description}
    if schema_ref is not None:
        response["content"] = {"application/json": {"schema": {"$ref": schema_ref}}}
    return response


def build_openapi_document() -> dict[str, object]:
    """生成可供客户端和契约检查使用的 OpenAPI 3.1 文档。"""

    project_parameter = _openapi_parameter("project_id", "path", {"type": "string"}, required=True)
    proposal_parameter = _openapi_parameter("proposal_id", "path", {"type": "string"}, required=True)
    packet_parameter = _openapi_parameter("packet_id", "path", {"type": "string"}, required=True)
    common_errors = {
        "400": _openapi_response("请求格式错误", "#/components/schemas/Error"),
        "401": _openapi_response("身份凭据无效", "#/components/schemas/Error"),
        "403": _openapi_response("项目动作不允许", "#/components/schemas/Error"),
        "404": _openapi_response("资源不存在", "#/components/schemas/Error"),
        "409": _openapi_response("版本或资源冲突", "#/components/schemas/Error"),
        "422": _openapi_response("字段校验失败", "#/components/schemas/Error"),
        "429": _openapi_response("超过限流", "#/components/schemas/Error"),
        "503": _openapi_response("服务未就绪", "#/components/schemas/Error"),
    }

    def operation(
        operation_id: str,
        *,
        surface: str,
        response_ref: str = "#/components/schemas/JsonObject",
        method: str = "GET",
        parameters: Iterable[Mapping[str, object]] = (),
        request_ref: str | None = None,
        security: list[dict[str, list[object]]] | None = None,
        status: str = "200",
        extra_responses: Mapping[str, Mapping[str, object]] | None = None,
    ) -> dict[str, object]:
        responses = dict(common_errors)
        responses[status] = _openapi_response("请求成功", response_ref)
        if extra_responses:
            responses.update(extra_responses)
        value: dict[str, object] = {
            "operationId": operation_id,
            "tags": [surface],
            "parameters": [dict(item) for item in parameters],
            "responses": responses,
        }
        if security is not None:
            value["security"] = security
        if request_ref is not None:
            value["requestBody"] = {
                "required": True,
                "content": {"application/json": {"schema": {"$ref": request_ref}}},
            }
        return value

    employee_security = [{"EmployeeSession": []}]
    agent_security = [{"AgentBearer": []}]
    pagination_parameters = [
        _openapi_parameter("limit", "query", {"type": "integer", "minimum": 1, "maximum": MAX_PAGE_SIZE}),
        _openapi_parameter("cursor", "query", {"type": "string"}),
    ]
    paths: dict[str, object] = {
        "/livez": {"get": operation("livez", surface="health", response_ref="#/components/schemas/Health")},
        "/readyz": {
            "get": operation(
                "readyz",
                surface="health",
                response_ref="#/components/schemas/Health",
                extra_responses={"503": _openapi_response("核心依赖未就绪", "#/components/schemas/Error")},
            )
        },
        "/openapi.json": {"get": operation("openapi", surface="health", response_ref="#/components/schemas/OpenAPI")},
        "/api/v1/employee/auth/login": {
            "post": operation("employeeLogin", surface="employee_public", response_ref="#/components/schemas/LoginStart", security=[])
        },
        "/api/v1/employee/auth/callback": {
            "get": operation(
                "employeeCallback",
                surface="employee_public",
                response_ref="#/components/schemas/Session",
                security=[],
                parameters=[
                    _openapi_parameter("state", "query", {"type": "string"}, required=True),
                    _openapi_parameter("code", "query", {"type": "string"}, required=True),
                ],
            )
        },
        "/api/v1/employee/auth/refresh": {
            "post": operation("employeeRefresh", surface="employee", response_ref="#/components/schemas/Principal", security=employee_security)
        },
        "/api/v1/employee/auth/logout": {
            "post": operation("employeeLogout", surface="employee", response_ref="#/components/schemas/JsonObject", security=employee_security)
        },
        "/api/v1/employee/me": {
            "get": operation("employeeMe", surface="employee", response_ref="#/components/schemas/Principal", security=employee_security)
        },
        "/api/v1/employee/projects/{project_id}": {
            "get": operation("employeeProject", surface="employee", response_ref="#/components/schemas/Project", security=employee_security, parameters=[project_parameter])
        },
        "/api/v1/employee/projects/{project_id}/state": {
            "get": operation("employeeState", surface="employee", response_ref="#/components/schemas/Page", security=employee_security, parameters=[project_parameter, *pagination_parameters])
        },
        "/api/v1/employee/projects/{project_id}/proposals": {
            "get": operation("employeeProposals", surface="employee", response_ref="#/components/schemas/Page", security=employee_security, parameters=[project_parameter, *pagination_parameters, _openapi_parameter("status", "query", {"type": "string"})]),
            "post": operation("employeeCreateProposal", surface="employee", response_ref="#/components/schemas/WriteResult", security=employee_security, parameters=[project_parameter, _openapi_parameter("Idempotency-Key", "header", {"type": "string"}, required=True), _openapi_parameter("If-Match", "header", {"type": "string"}, required=True)], request_ref="#/components/schemas/ProposalCreate", status="201"),
        },
        "/api/v1/employee/projects/{project_id}/proposals/{proposal_id}": {
            "get": operation("employeeProposal", surface="employee", response_ref="#/components/schemas/ProposalDetail", security=employee_security, parameters=[project_parameter, proposal_parameter])
        },
        "/api/v1/employee/projects/{project_id}/proposals/{proposal_id}/review": {
            "post": operation("employeeReview", surface="employee", response_ref="#/components/schemas/WriteResult", security=employee_security, parameters=[project_parameter, proposal_parameter, _openapi_parameter("Idempotency-Key", "header", {"type": "string"}, required=True), _openapi_parameter("If-Match", "header", {"type": "string"}, required=True)], request_ref="#/components/schemas/ProposalReview", status="201")
        },
        "/api/v1/employee/projects/{project_id}/evidence": {
            "get": operation("employeeEvidence", surface="employee", response_ref="#/components/schemas/JsonObject", security=employee_security, parameters=[project_parameter, _openapi_parameter("evidence_ref", "query", {"type": "string"}, required=True)])
        },
        "/api/v1/employee/projects/{project_id}/evidence/uploads": {
            "post": operation("employeeBeginEvidenceUpload", surface="employee", response_ref="#/components/schemas/JsonObject", security=employee_security, parameters=[project_parameter, _openapi_parameter("Idempotency-Key", "header", {"type": "string"}, required=True)], request_ref="#/components/schemas/EvidenceUploadRequest", status="201")
        },
        "/api/v1/employee/projects/{project_id}/evidence/uploads/{upload_id}/content": {
            "put": operation("employeeUploadEvidenceContent", surface="employee", response_ref="#/components/schemas/JsonObject", security=employee_security, parameters=[project_parameter, _openapi_parameter("upload_id", "path", {"type": "string"}, required=True)], status="200")
        },
        "/api/v1/employee/projects/{project_id}/evidence/versions/{version_id}/content": {
            "get": operation("employeeStreamEvidence", surface="employee", response_ref="#/components/schemas/Binary", security=employee_security, parameters=[project_parameter, _openapi_parameter("version_id", "path", {"type": "string"}, required=True)])
        },
        "/api/v1/employee/projects/{project_id}/tasks/{packet_id}": {
            "get": operation("employeeTaskPacket", surface="employee", response_ref="#/components/schemas/JsonObject", security=employee_security, parameters=[project_parameter, packet_parameter])
        },
        "/api/v1/agent/projects/{project_id}/state": {
            "get": operation("agentState", surface="agent", response_ref="#/components/schemas/Page", security=agent_security, parameters=[project_parameter, *pagination_parameters])
        },
        "/api/v1/agent/projects/{project_id}/proposals": {
            "get": operation("agentProposals", surface="agent", response_ref="#/components/schemas/Page", security=agent_security, parameters=[project_parameter, *pagination_parameters, _openapi_parameter("status", "query", {"type": "string"})]),
            "post": operation("agentCreateProposal", surface="agent", response_ref="#/components/schemas/WriteResult", security=agent_security, parameters=[project_parameter, _openapi_parameter("Idempotency-Key", "header", {"type": "string"}, required=True), _openapi_parameter("If-Match", "header", {"type": "string"}, required=True)], request_ref="#/components/schemas/ProposalCreate", status="201")
        },
        "/api/v1/agent/projects/{project_id}/proposals/{proposal_id}": {
            "get": operation("agentProposal", surface="agent", response_ref="#/components/schemas/ProposalDetail", security=agent_security, parameters=[project_parameter, proposal_parameter])
        },
        "/api/v1/agent/projects/{project_id}/evidence": {
            "get": operation("agentEvidence", surface="agent", response_ref="#/components/schemas/JsonObject", security=agent_security, parameters=[project_parameter, _openapi_parameter("evidence_ref", "query", {"type": "string"}, required=True)])
        },
        "/api/v1/agent/projects/{project_id}/tasks/{packet_id}": {
            "get": operation("agentTaskPacket", surface="agent", response_ref="#/components/schemas/JsonObject", security=agent_security, parameters=[project_parameter, packet_parameter])
        },
    }
    return {
        "openapi": OPENAPI_VERSION,
        "info": {
            "title": "Brand Project OS Service API",
            "version": "1.0.0",
            "description": "唯一 OpenWork 客户端与受控 Agent 访问同一项目权威服务的版本化接口。",
            "x-contract-schema": HTTP_API_SCHEMA_VERSION,
        },
        "servers": [{"url": "/"}],
        "paths": paths,
        "components": {
            "securitySchemes": {
                "EmployeeSession": {"type": "http", "scheme": "bearer", "bearerFormat": "opaque-session"},
                "AgentBearer": {"type": "http", "scheme": "bearer", "bearerFormat": "opaque-agent-token"},
            },
            "schemas": {
                "JsonObject": {"type": "object", "additionalProperties": True},
                "OpenAPI": {"type": "object", "additionalProperties": True},
                "Binary": {"type": "string", "format": "binary"},
                "Error": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["schema_version", "code", "message", "request_id", "retryable", "details"],
                    "properties": {
                        "schema_version": {"const": HTTP_ERROR_SCHEMA_VERSION},
                        "code": {"type": "string"},
                        "message": {"type": "string"},
                        "request_id": {"type": "string"},
                        "retryable": {"type": "boolean"},
                        "details": {"type": "object", "additionalProperties": True},
                    },
                },
                "Health": {"type": "object", "required": ["schema_version", "check", "status"], "properties": {"schema_version": {"const": "service-health.v1"}, "check": {"enum": ["live", "ready"]}, "status": {"type": "string"}}, "additionalProperties": True},
                "LoginStart": {"type": "object", "additionalProperties": False, "required": ["schema_version", "transaction_id", "authorization_url", "expires_at"], "properties": {"schema_version": {"const": "employee-login.v1"}, "transaction_id": {"type": "string"}, "authorization_url": {"type": "string", "format": "uri"}, "expires_at": {"type": "string", "format": "date-time"}}},
                "Session": {"type": "object", "additionalProperties": False, "required": ["schema_version", "session_id", "session_token", "expires_at", "employee"], "properties": {"schema_version": {"const": "employee-session.v1"}, "session_id": {"type": "string"}, "session_token": {"type": "string"}, "expires_at": {"type": "string", "format": "date-time"}, "employee": {"$ref": "#/components/schemas/Principal"}}},
                "Principal": {"type": "object", "required": ["employee_id", "display_name", "session_id"], "properties": {"employee_id": {"type": "string"}, "display_name": {"type": "string"}, "session_id": {"type": "string"}, "issuer": {"type": "string"}, "subject": {"type": "string"}, "authenticated_at": {"type": "string", "format": "date-time"}}, "additionalProperties": False},
                "Project": {"type": "object", "additionalProperties": True},
                "Page": {"type": "object", "required": ["schema_version", "project_id", "state_version", "items", "pagination"], "properties": {"schema_version": {"type": "string"}, "project_id": {"type": "string"}, "state_version": {"type": "integer", "minimum": 0}, "items": {"type": "array"}, "pagination": {"type": "object", "additionalProperties": False, "required": ["limit", "has_more", "next_cursor"], "properties": {"limit": {"type": "integer"}, "has_more": {"type": "boolean"}, "next_cursor": {"type": ["string", "null"]}}}}, "additionalProperties": False},
                "ProposalDetail": {"type": "object", "additionalProperties": True},
                "WriteResult": {"type": "object", "additionalProperties": True},
                "ProposalCreate": {"type": "object", "additionalProperties": False, "required": ["proposal_id", "proposal_kind", "classification", "after", "reason", "impact_scope", "evidence_refs"], "properties": {"proposal_id": {"type": "string"}, "proposal_kind": {"type": "string"}, "classification": {"type": "string"}, "subject_id": {"type": ["string", "null"]}, "before": {"type": ["object", "null"]}, "after": {"type": "object"}, "reason": {"type": "string"}, "impact_scope": {"type": "string"}, "evidence_refs": {"type": "array", "items": {"type": "string"}}, "supersedes_proposal_id": {"type": ["string", "null"]}, "source_meeting_item_id": {"type": ["string", "null"]}, "valid_from": {"type": ["string", "null"]}, "valid_until": {"type": ["string", "null"]}, "expected_version": {"type": "integer"}, "idempotency_key": {"type": "string"}}},
                "ProposalReview": {"type": "object", "additionalProperties": False, "required": ["action", "reason"], "properties": {"action": {"enum": ["approve", "modify_and_approve", "reject"]}, "reason": {"type": "string"}, "replacement_after": {"type": ["object", "null"]}, "expected_version": {"type": "integer"}, "idempotency_key": {"type": "string"}}},
                "EvidenceUploadRequest": {"type": "object", "additionalProperties": False, "required": ["logical_source_id", "original_filename", "expected_sha256", "expected_size_bytes", "expected_media_type", "confidentiality"], "properties": {"logical_source_id": {"type": "string"}, "original_filename": {"type": "string"}, "expected_sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"}, "expected_size_bytes": {"type": "integer", "minimum": 0}, "expected_media_type": {"type": "string"}, "confidentiality": {"enum": ["P0", "P1", "P2", "P3"]}, "idempotency_key": {"type": "string"}}},
            },
        },
        "x-brand-project-os": {
            "schema_version": HTTP_API_SCHEMA_VERSION,
            "major_version": API_MAJOR_VERSION,
            "minimum_supported_major": MIN_SUPPORTED_API_MAJOR,
            "agent_human_review_route": False,
            "client_direct_storage_access": False,
            "compatibility_window": HTTP_API_CONTRACT["compatibility_window"],
        },
    }


def build_http_app(dependencies: HttpApplicationDependencies | None = None) -> Starlette:
    """构建可嵌入 Uvicorn 或测试客户端的 ASGI 应用；不会自动启动进程。"""

    resolved = dependencies or HttpApplicationDependencies()
    application = HttpApplication(resolved)
    async def not_found(request: Request, _exception) -> Response:
        error = ApiError(404, "NOT_FOUND", "请求路径不存在")
        return _json_response(request, _error_body(request, error), status_code=404)

    async def method_not_allowed(request: Request, exception) -> Response:
        error = ApiError(405, "METHOD_NOT_ALLOWED", "请求方法不受支持")
        # Starlette 在匹配到路径但方法不允许时会把合法方法放在异常头中。
        # 不能回显当前请求方法，否则 Allow 会错误地宣称该方法是合法的。
        exception_headers = getattr(exception, "headers", None) or {}
        return _json_response(
            request,
            _error_body(request, error),
            status_code=405,
            headers=dict(exception_headers),
        )

    app = Starlette(
        debug=False,
        routes=application.routes(),
        exception_handlers={404: not_found, 405: method_not_allowed},
    )
    app.add_middleware(RequestContextMiddleware, dependencies=resolved)
    app.add_middleware(ApiExceptionMiddleware)
    app.state.brand_os_http = application
    return app


__all__ = [
    "API_MAJOR_VERSION",
    "HTTP_API_CONTRACT",
    "HTTP_API_SCHEMA_VERSION",
    "HttpApplicationDependencies",
    "InMemoryRateLimiter",
    "RateLimitDecision",
    "build_http_app",
    "build_openapi_document",
]
