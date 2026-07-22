"""OIDC 登录、员工身份绑定和服务器会话的领域用例。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Iterable
from urllib.parse import urlparse
from uuid import uuid4

from .domain import Actor, ActorKind, CommandContext

if TYPE_CHECKING:
    from .ports import IdentityRepositoryPort, OidcProviderPort


DEFAULT_OIDC_SCOPES = ("openid", "profile", "email")


class EmployeeStatus(StrEnum):
    """员工账号是否允许建立交互式会话。"""

    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class IdentityBindingStatus(StrEnum):
    """外部 OIDC 身份与内部员工的绑定状态。"""

    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class AuthorizationStatus(StrEnum):
    """一次 OIDC 授权事务的状态。"""

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    CONSUMED = "CONSUMED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


class SessionStatus(StrEnum):
    """员工服务器会话的状态。"""

    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


class IdentityError(RuntimeError):
    """身份与会话错误基类。"""


class OidcProtocolError(IdentityError):
    """OIDC 提供方响应或令牌不符合协议。"""


class OidcProviderError(IdentityError):
    """OIDC 提供方调用失败，并区分是否允许安全重试。"""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class LoginStateError(IdentityError):
    """OIDC 登录 state 不存在、过期或状态不正确。"""


class LoginReplayError(LoginStateError):
    """state 或授权码已经被使用。"""


class IdentityNotBoundError(IdentityError):
    """OIDC 身份尚未绑定内部员工。"""


class EmployeeDisabledError(IdentityError):
    """员工或身份绑定已经停用。"""


class SessionInvalidError(IdentityError):
    """会话凭据不存在、格式错误或校验失败。"""


class SessionExpiredError(SessionInvalidError):
    """服务器会话已经过期。"""


class SessionRevokedError(SessionInvalidError):
    """服务器会话已经撤销。"""


class SessionRefreshRequiredError(SessionInvalidError):
    """访问令牌已过期，需要先刷新会话。"""


class IdentityPermissionDenied(IdentityError):
    """调用方没有身份管理权限。"""


class SensitiveValue:
    """避免 verifier、令牌和会话秘密进入日志或 repr。"""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if not value:
            raise ValueError("敏感值不能为空")
        self._value = value

    def reveal(self) -> str:
        """只供协议适配器或加密存储显式读取原值。"""

        return self._value

    def __repr__(self) -> str:
        return "SensitiveValue(***)"

    def __str__(self) -> str:
        return "***"


@dataclass(frozen=True, slots=True)
class EmployeeAccount:
    """内部员工账号；OIDC 邮箱不能自动创建或替代该账号。"""

    employee_id: str
    display_name: str
    primary_email: str | None
    status: EmployeeStatus
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class IdentityBinding:
    """一个明确 OIDC issuer/subject 与内部员工的绑定。"""

    binding_id: str
    issuer: str
    subject: str
    employee: EmployeeAccount
    email_at_binding: str | None
    status: IdentityBindingStatus
    created_at: datetime
    disabled_at: datetime | None


@dataclass(frozen=True, slots=True)
class AuthorizationTransaction:
    """一次性 OIDC state、nonce 和 PKCE verifier。"""

    transaction_id: str
    state_digest: str
    nonce_digest: str
    code_verifier: SensitiveValue = field(repr=False)
    redirect_uri: str = ""
    status: AuthorizationStatus = AuthorizationStatus.PENDING
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    authorization_code_digest: str | None = None


@dataclass(frozen=True, slots=True)
class AuthorizationRequest:
    """交给唯一员工客户端打开的 OIDC 授权请求。"""

    transaction_id: str
    authorization_url: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class OidcTokenSet:
    """OIDC 提供方返回的令牌集合，所有原值都必须脱敏。"""

    access_token: SensitiveValue | None = field(default=None, repr=False)
    id_token: SensitiveValue | None = field(default=None, repr=False)
    refresh_token: SensitiveValue | None = field(default=None, repr=False)
    token_type: str = "Bearer"
    expires_in: int = 0
    scope: tuple[str, ...] = ()
    received_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if self.token_type.lower() != "bearer":
            raise OidcProtocolError("OIDC token_type 必须是 Bearer")
        if self.expires_in <= 0:
            raise OidcProtocolError("OIDC expires_in 必须大于 0")
        _require_aware(self.received_at, "received_at")


@dataclass(frozen=True, slots=True)
class VerifiedIdentity:
    """经过签名、issuer、audience、nonce 和时间校验的身份。"""

    issuer: str
    subject: str
    issued_at: datetime
    expires_at: datetime
    email: str | None
    display_name: str | None
    email_verified: bool | None


@dataclass(frozen=True, slots=True)
class EmployeeSession:
    """服务器持有的员工会话和加密令牌材料。"""

    session_id: str
    session_secret_digest: str = field(repr=False)
    binding: IdentityBinding
    access_token: SensitiveValue | None = field(repr=False)
    refresh_token: SensitiveValue | None = field(default=None, repr=False)
    status: SessionStatus = SessionStatus.ACTIVE
    token_version: int = 1
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    access_token_expires_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    session_expires_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    revoked_at: datetime | None = None
    revocation_reason: str | None = None


@dataclass(frozen=True, slots=True)
class SessionCredential:
    """客户端保存到系统钥匙串的不可恢复服务器会话凭据。"""

    session_id: str
    token: SensitiveValue = field(repr=False)
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class InteractiveEmployeePrincipal:
    """只能由有效 OIDC 员工会话解析出的交互式身份。"""

    employee_id: str
    display_name: str
    session_id: str
    issuer: str
    subject: str
    authenticated_at: datetime

    def as_actor(self) -> Actor:
        """把已验证员工会话映射为现有领域命令操作者。"""

        return Actor(ActorKind.HUMAN, self.employee_id)


@dataclass(frozen=True, slots=True)
class LoginResult:
    """登录成功后返回给 Employee API 的最小结果。"""

    credential: SessionCredential
    principal: InteractiveEmployeePrincipal


def sha256_text(value: str) -> str:
    """对一次性凭据做固定编码 SHA-256。"""

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def pkce_s256_challenge(code_verifier: str) -> str:
    """按 RFC 7636 生成不含填充的 S256 challenge。"""

    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class OidcIdentityService:
    """协调 OIDC、预绑定员工身份和可撤销服务器会话。"""

    def __init__(
        self,
        *,
        provider: OidcProviderPort,
        repository: IdentityRepositoryPort,
        redirect_uri: str,
        scopes: Iterable[str] = DEFAULT_OIDC_SCOPES,
        authorization_ttl: timedelta = timedelta(minutes=10),
        session_ttl: timedelta = timedelta(hours=12),
        clock_skew: timedelta = timedelta(seconds=60),
        identity_admins: Iterable[str] = ("Fox",),
    ) -> None:
        self.provider = provider
        self.repository = repository
        self.redirect_uri = _validate_redirect_uri(redirect_uri)
        self.scopes = tuple(dict.fromkeys(scope.strip() for scope in scopes if scope.strip()))
        if "openid" not in self.scopes:
            raise ValueError("OIDC scope 必须包含 openid")
        if authorization_ttl <= timedelta(0):
            raise ValueError("authorization_ttl 必须大于 0")
        if session_ttl <= timedelta(0):
            raise ValueError("session_ttl 必须大于 0")
        if clock_skew < timedelta(0) or clock_skew > timedelta(minutes=5):
            raise ValueError("clock_skew 必须位于 0 到 5 分钟之间")
        self.authorization_ttl = authorization_ttl
        self.session_ttl = session_ttl
        self.clock_skew = clock_skew
        self.identity_admins = frozenset(identity_admins)

    def begin_login(self, *, now: datetime | None = None) -> AuthorizationRequest:
        """创建一次性 state、nonce 和 PKCE 事务。"""

        occurred_at = _utc(now)
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        code_verifier = secrets.token_urlsafe(64)
        transaction = AuthorizationTransaction(
            transaction_id=f"AUTH-{uuid4().hex.upper()}",
            state_digest=sha256_text(state),
            nonce_digest=sha256_text(nonce),
            code_verifier=SensitiveValue(code_verifier),
            redirect_uri=self.redirect_uri,
            status=AuthorizationStatus.PENDING,
            created_at=occurred_at,
            expires_at=occurred_at + self.authorization_ttl,
        )
        self.repository.create_authorization(transaction)
        authorization_url = self.provider.authorization_url(
            redirect_uri=self.redirect_uri,
            state=state,
            nonce=nonce,
            code_challenge=pkce_s256_challenge(code_verifier),
            scopes=self.scopes,
        )
        return AuthorizationRequest(
            transaction_id=transaction.transaction_id,
            authorization_url=authorization_url,
            expires_at=transaction.expires_at,
        )

    def complete_login(
        self,
        *,
        state: str,
        code: str,
        now: datetime | None = None,
    ) -> LoginResult:
        """一次性消费授权码，并只为预绑定的启用员工建立会话。"""

        if not state or not code:
            raise LoginStateError("OIDC state 和 code 不能为空")
        occurred_at = _utc(now)
        transaction = self.repository.claim_authorization(
            state_digest=sha256_text(state),
            authorization_code_digest=sha256_text(code),
            occurred_at=occurred_at,
        )
        try:
            token_set = self.provider.exchange_code(
                code=SensitiveValue(code),
                code_verifier=transaction.code_verifier,
                redirect_uri=transaction.redirect_uri,
                occurred_at=occurred_at,
            )
            if token_set.id_token is None:
                raise OidcProtocolError("授权码交换结果缺少 id_token")
            identity = self.provider.verify_id_token(
                token_set.id_token,
                expected_nonce_digest=transaction.nonce_digest,
                access_token=token_set.access_token,
                occurred_at=occurred_at,
                clock_skew=self.clock_skew,
            )
            binding = self.repository.resolve_binding(identity.issuer, identity.subject)
            self._require_active_binding(binding)
            session_id = f"SES-{uuid4().hex.upper()}"
            session_secret = secrets.token_urlsafe(32)
            access_expires_at = token_set.received_at + timedelta(
                seconds=token_set.expires_in
            )
            session = self.repository.create_session(
                transaction_id=transaction.transaction_id,
                binding=binding,
                session_id=session_id,
                session_secret_digest=sha256_text(session_secret),
                token_set=token_set,
                access_token_expires_at=access_expires_at,
                session_expires_at=occurred_at + self.session_ttl,
                occurred_at=occurred_at,
            )
        except Exception as error:
            self.repository.fail_authorization(
                transaction.transaction_id,
                reason_code=type(error).__name__,
                occurred_at=occurred_at,
            )
            raise

        credential = SessionCredential(
            session_id=session.session_id,
            token=SensitiveValue(f"{session.session_id}.{session_secret}"),
            expires_at=session.session_expires_at,
        )
        return LoginResult(
            credential=credential,
            principal=self._principal(session, occurred_at),
        )

    def authenticate(
        self,
        session_token: str | SensitiveValue,
        *,
        now: datetime | None = None,
        require_access_token: bool = False,
    ) -> InteractiveEmployeePrincipal:
        """把不透明会话凭据解析为交互式员工身份。"""

        occurred_at = _utc(now)
        session = self._load_valid_session(session_token, occurred_at)
        if require_access_token and occurred_at >= session.access_token_expires_at:
            raise SessionRefreshRequiredError("OIDC 访问令牌已过期")
        return self._principal(session, occurred_at)

    def bind_human_command_context(
        self,
        session_token: str | SensitiveValue,
        *,
        project_id: str,
        command_name: str,
        idempotency_key: str,
        expected_version: int,
        now: datetime | None = None,
    ) -> CommandContext:
        """用有效员工会话生成命令身份；项目权限仍由 F2.5 校验。"""

        if not command_name.strip():
            raise ValueError("command_name 不能为空")
        occurred_at = _utc(now)
        session = self._load_valid_session(session_token, occurred_at)
        context = CommandContext(
            project_id=project_id,
            actor=Actor(ActorKind.HUMAN, session.binding.employee.employee_id),
            idempotency_key=idempotency_key,
            expected_version=expected_version,
        )
        self.repository.record_identity_assertion(
            session.session_id,
            project_id=project_id,
            command_name=command_name,
            idempotency_key=idempotency_key,
            occurred_at=occurred_at,
        )
        return context

    def refresh_session(
        self,
        session_token: str | SensitiveValue,
        *,
        now: datetime | None = None,
    ) -> InteractiveEmployeePrincipal:
        """刷新访问令牌；不可重试的拒绝会立即撤销本地会话。"""

        occurred_at = _utc(now)
        session = self._load_valid_session(session_token, occurred_at)
        if session.refresh_token is None:
            self.repository.revoke_session(
                session.session_id,
                reason="missing_refresh_token",
                actor_kind=ActorKind.SYSTEM,
                actor_id="oidc-session-service",
                occurred_at=occurred_at,
            )
            raise SessionRevokedError("会话没有可用的刷新令牌")
        try:
            token_set = self.provider.refresh(
                session.refresh_token,
                occurred_at=occurred_at,
            )
            if token_set.id_token is not None:
                identity = self.provider.verify_id_token(
                    token_set.id_token,
                    expected_nonce_digest=None,
                    access_token=token_set.access_token,
                    occurred_at=occurred_at,
                    clock_skew=self.clock_skew,
                )
                if not (
                    hmac.compare_digest(identity.issuer, session.binding.issuer)
                    and hmac.compare_digest(identity.subject, session.binding.subject)
                ):
                    raise OidcProtocolError("刷新后的身份与原会话不一致")
        except OidcProviderError as error:
            if not error.retryable:
                self.repository.revoke_session(
                    session.session_id,
                    reason="refresh_rejected",
                    actor_kind=ActorKind.SYSTEM,
                    actor_id="oidc-session-service",
                    occurred_at=occurred_at,
                )
            raise
        except OidcProtocolError:
            self.repository.revoke_session(
                session.session_id,
                reason="refresh_identity_mismatch",
                actor_kind=ActorKind.SYSTEM,
                actor_id="oidc-session-service",
                occurred_at=occurred_at,
            )
            raise

        rotated = self.repository.rotate_session_tokens(
            session.session_id,
            expected_token_version=session.token_version,
            token_set=token_set,
            access_token_expires_at=token_set.received_at
            + timedelta(seconds=token_set.expires_in),
            occurred_at=occurred_at,
        )
        return self._principal(rotated, occurred_at)

    def revoke_session(
        self,
        session_token: str | SensitiveValue,
        *,
        reason: str = "employee_logout",
        now: datetime | None = None,
    ) -> None:
        """先撤销本地会话，再尽力通知 OIDC 提供方撤销刷新令牌。"""

        if not reason.strip():
            raise ValueError("撤销原因不能为空")
        occurred_at = _utc(now)
        session = self._load_valid_session(session_token, occurred_at)
        self.repository.revoke_session(
            session.session_id,
            reason=reason,
            actor_kind=ActorKind.HUMAN,
            actor_id=session.binding.employee.employee_id,
            occurred_at=occurred_at,
        )
        token = session.refresh_token or session.access_token
        if token is not None:
            try:
                self.provider.revoke_token(token)
            except OidcProviderError:
                pass

    def revoke_employee_sessions(
        self,
        admin_session_token: str | SensitiveValue,
        *,
        employee_id: str,
        reason: str,
        now: datetime | None = None,
    ) -> int:
        """只允许已登录的身份管理员撤销某员工的全部会话。"""

        admin = self.authenticate(admin_session_token, now=now)
        if admin.employee_id not in self.identity_admins:
            raise IdentityPermissionDenied("当前员工无权撤销他人会话")
        if not employee_id.strip() or not reason.strip():
            raise ValueError("employee_id 和撤销原因不能为空")
        return self.repository.revoke_employee_sessions(
            employee_id,
            reason=reason,
            actor_id=admin.employee_id,
            occurred_at=_utc(now),
        )

    def _load_valid_session(
        self,
        session_token: str | SensitiveValue,
        occurred_at: datetime,
    ) -> EmployeeSession:
        raw_token = (
            session_token.reveal()
            if isinstance(session_token, SensitiveValue)
            else session_token
        )
        if not isinstance(raw_token, str) or "." not in raw_token:
            raise SessionInvalidError("会话凭据格式不正确")
        session_id, secret = raw_token.split(".", 1)
        if not session_id or not secret:
            raise SessionInvalidError("会话凭据格式不正确")
        session = self.repository.get_session(session_id)
        if not hmac.compare_digest(session.session_secret_digest, sha256_text(secret)):
            raise SessionInvalidError("会话凭据校验失败")
        if session.status is SessionStatus.REVOKED:
            raise SessionRevokedError("会话已经撤销")
        if session.status is SessionStatus.EXPIRED or occurred_at >= session.session_expires_at:
            if session.status is SessionStatus.ACTIVE:
                self.repository.expire_session(session.session_id, occurred_at=occurred_at)
            raise SessionExpiredError("会话已经过期")
        try:
            self._require_active_binding(session.binding)
        except EmployeeDisabledError:
            self.repository.revoke_session(
                session.session_id,
                reason="employee_or_binding_disabled",
                actor_kind=ActorKind.SYSTEM,
                actor_id="oidc-session-service",
                occurred_at=occurred_at,
            )
            raise
        return session

    @staticmethod
    def _require_active_binding(binding: IdentityBinding) -> None:
        if binding.status is not IdentityBindingStatus.ACTIVE:
            raise EmployeeDisabledError("OIDC 身份绑定已经停用")
        if binding.employee.status is not EmployeeStatus.ACTIVE:
            raise EmployeeDisabledError("员工账号已经停用")

    @staticmethod
    def _principal(
        session: EmployeeSession,
        occurred_at: datetime,
    ) -> InteractiveEmployeePrincipal:
        return InteractiveEmployeePrincipal(
            employee_id=session.binding.employee.employee_id,
            display_name=session.binding.employee.display_name,
            session_id=session.session_id,
            issuer=session.binding.issuer,
            subject=session.binding.subject,
            authenticated_at=occurred_at,
        )


def _utc(value: datetime | None) -> datetime:
    resolved = datetime.now(UTC) if value is None else value
    _require_aware(resolved, "时间")
    return resolved.astimezone(UTC)


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} 必须包含时区")


def _validate_redirect_uri(value: str) -> str:
    if not value.strip():
        raise ValueError("redirect_uri 不能为空")
    parsed = urlparse(value)
    if parsed.fragment:
        raise ValueError("redirect_uri 不能包含片段")
    if parsed.scheme not in {"https", "http"}:
        raise ValueError("服务器 OIDC redirect_uri 必须使用 HTTP 或 HTTPS")
    if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("非本机 OIDC redirect_uri 必须使用 HTTPS")
    if not parsed.netloc:
        raise ValueError("redirect_uri 必须包含主机")
    return value


__all__ = [
    "AuthorizationRequest",
    "AuthorizationStatus",
    "AuthorizationTransaction",
    "DEFAULT_OIDC_SCOPES",
    "EmployeeAccount",
    "EmployeeDisabledError",
    "EmployeeSession",
    "EmployeeStatus",
    "IdentityBinding",
    "IdentityBindingStatus",
    "IdentityError",
    "IdentityNotBoundError",
    "IdentityPermissionDenied",
    "InteractiveEmployeePrincipal",
    "LoginReplayError",
    "LoginResult",
    "LoginStateError",
    "OidcIdentityService",
    "OidcProtocolError",
    "OidcProviderError",
    "OidcTokenSet",
    "SensitiveValue",
    "SessionCredential",
    "SessionExpiredError",
    "SessionInvalidError",
    "SessionRefreshRequiredError",
    "SessionRevokedError",
    "SessionStatus",
    "VerifiedIdentity",
    "pkce_s256_challenge",
    "sha256_text",
]
