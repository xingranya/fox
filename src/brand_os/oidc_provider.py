"""基于 OIDC Discovery、JWKS 和授权码交换的提供方适配器。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Mapping, Protocol, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

import jwt

from .identity import (
    OidcProtocolError,
    OidcProviderError,
    OidcTokenSet,
    SensitiveValue,
    VerifiedIdentity,
    sha256_text,
)
from .server_config import SecretValue, ServerEnvironment, ServerSettings


ASYMMETRIC_SIGNING_ALGORITHMS = frozenset(
    {
        "RS256",
        "RS384",
        "RS512",
        "PS256",
        "PS384",
        "PS512",
        "ES256",
        "ES384",
        "ES512",
        "EdDSA",
    }
)


class OidcHttpTransport(Protocol):
    """隔离 OIDC HTTP 访问，便于离线契约测试。"""

    def get_json(self, url: str) -> Mapping[str, object]: ...

    def post_form(
        self,
        url: str,
        form: Mapping[str, str],
        *,
        basic_auth: tuple[str, str] | None,
    ) -> Mapping[str, object]: ...


class UrllibOidcHttpTransport:
    """使用标准库执行有超时的 OIDC JSON 请求。"""

    def __init__(self, *, timeout_seconds: float = 3.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("OIDC HTTP 超时必须大于 0")
        self.timeout_seconds = timeout_seconds

    def get_json(self, url: str) -> Mapping[str, object]:
        request = Request(url, headers={"Accept": "application/json"})
        return self._request_json(request)

    def post_form(
        self,
        url: str,
        form: Mapping[str, str],
        *,
        basic_auth: tuple[str, str] | None,
    ) -> Mapping[str, object]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        if basic_auth is not None:
            username, password = basic_auth
            encoded = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode(
                "ascii"
            )
            headers["Authorization"] = f"Basic {encoded}"
        request = Request(
            url,
            data=urlencode(form).encode("ascii"),
            headers=headers,
            method="POST",
        )
        return self._request_json(request)

    def _request_json(self, request: Request) -> Mapping[str, object]:
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310
                payload = response.read()
        except HTTPError as error:
            retryable = error.code >= 500 or error.code == 429
            raise OidcProviderError(
                f"OIDC 提供方返回 HTTP {error.code}",
                retryable=retryable,
            ) from error
        except (URLError, TimeoutError) as error:
            raise OidcProviderError("OIDC 提供方暂时不可用", retryable=True) from error
        try:
            document = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise OidcProviderError("OIDC 提供方返回无效 JSON", retryable=False) from error
        if not isinstance(document, dict):
            raise OidcProviderError("OIDC 提供方响应根节点必须是对象", retryable=False)
        error_code = document.get("error")
        if isinstance(error_code, str) and error_code:
            retryable = error_code in {"temporarily_unavailable", "server_error"}
            raise OidcProviderError(
                f"OIDC 提供方拒绝请求：{error_code}",
                retryable=retryable,
            )
        return document


@dataclass(frozen=True, slots=True)
class OidcMetadata:
    """经过 issuer 与 HTTPS 校验的 OIDC Discovery 元数据。"""

    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    revocation_endpoint: str | None
    signing_algorithms: tuple[str, ...]


class OidcProviderAdapter:
    """实现真实 OIDC Code + PKCE、刷新、撤销与 ID Token 校验。"""

    def __init__(
        self,
        *,
        issuer: str,
        client_id: str,
        client_secret: str | SecretValue,
        transport: OidcHttpTransport | None = None,
        allow_insecure_http: bool = False,
    ) -> None:
        self.issuer = issuer
        if not self.issuer or not client_id.strip():
            raise ValueError("OIDC issuer 和 client_id 不能为空")
        self.client_id = client_id
        raw_secret = (
            client_secret.reveal()
            if isinstance(client_secret, SecretValue)
            else client_secret
        )
        self.client_secret = SensitiveValue(raw_secret)
        self.transport = transport or UrllibOidcHttpTransport()
        self.allow_insecure_http = allow_insecure_http
        _validate_issuer(self.issuer, allow_insecure_http=allow_insecure_http)
        self._cached_metadata: OidcMetadata | None = None
        self._cached_jwks: tuple[Mapping[str, object], ...] | None = None

    @classmethod
    def from_settings(
        cls,
        settings: ServerSettings,
        *,
        transport: OidcHttpTransport | None = None,
    ) -> OidcProviderAdapter:
        """从已校验服务器配置创建适配器。"""

        if (
            settings.oidc_issuer_url is None
            or settings.oidc_client_id is None
            or settings.oidc_client_secret is None
        ):
            raise ValueError("OIDC 配置不完整")
        return cls(
            issuer=settings.oidc_issuer_url,
            client_id=settings.oidc_client_id,
            client_secret=settings.oidc_client_secret,
            transport=transport
            or UrllibOidcHttpTransport(
                timeout_seconds=settings.dependency_timeout_seconds
            ),
            allow_insecure_http=settings.environment is not ServerEnvironment.PRODUCTION,
        )

    def authorization_url(
        self,
        *,
        redirect_uri: str,
        state: str,
        nonce: str,
        code_challenge: str,
        scopes: Sequence[str],
    ) -> str:
        """生成只使用 S256 PKCE 的授权地址。"""

        for value, name in (
            (redirect_uri, "redirect_uri"),
            (state, "state"),
            (nonce, "nonce"),
            (code_challenge, "code_challenge"),
        ):
            if not value:
                raise ValueError(f"{name} 不能为空")
        metadata = self._metadata()
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self.client_id,
                "redirect_uri": redirect_uri,
                "scope": " ".join(scopes),
                "state": state,
                "nonce": nonce,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }
        )
        separator = "&" if "?" in metadata.authorization_endpoint else "?"
        return f"{metadata.authorization_endpoint}{separator}{query}"

    def exchange_code(
        self,
        *,
        code: SensitiveValue,
        code_verifier: SensitiveValue,
        redirect_uri: str,
        occurred_at: datetime,
    ) -> OidcTokenSet:
        """用一次性授权码和原 PKCE verifier 换取令牌。"""

        response = self.transport.post_form(
            self._metadata().token_endpoint,
            {
                "grant_type": "authorization_code",
                "code": code.reveal(),
                "redirect_uri": redirect_uri,
                "client_id": self.client_id,
                "code_verifier": code_verifier.reveal(),
            },
            basic_auth=(self.client_id, self.client_secret.reveal()),
        )
        return _parse_token_response(response, occurred_at=occurred_at, require_id_token=True)

    def refresh(
        self,
        refresh_token: SensitiveValue,
        *,
        occurred_at: datetime,
    ) -> OidcTokenSet:
        """使用服务器保存的刷新令牌换取新访问令牌。"""

        response = self.transport.post_form(
            self._metadata().token_endpoint,
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token.reveal(),
                "client_id": self.client_id,
            },
            basic_auth=(self.client_id, self.client_secret.reveal()),
        )
        return _parse_token_response(response, occurred_at=occurred_at, require_id_token=False)

    def revoke_token(self, token: SensitiveValue) -> None:
        """调用可选撤销端点；没有端点时本地撤销仍然生效。"""

        endpoint = self._metadata().revocation_endpoint
        if endpoint is None:
            return
        self.transport.post_form(
            endpoint,
            {
                "token": token.reveal(),
                "token_type_hint": "refresh_token",
                "client_id": self.client_id,
            },
            basic_auth=(self.client_id, self.client_secret.reveal()),
        )

    def verify_id_token(
        self,
        id_token: SensitiveValue,
        *,
        expected_nonce_digest: str | None,
        access_token: SensitiveValue,
        occurred_at: datetime,
        clock_skew: timedelta,
    ) -> VerifiedIdentity:
        """验证签名、issuer、audience、时间、nonce、azp 和 at_hash。"""

        raw_token = id_token.reveal()
        try:
            header = jwt.get_unverified_header(raw_token)
        except jwt.PyJWTError as error:
            raise OidcProtocolError("ID Token 头部无效") from error
        algorithm = header.get("alg")
        if not isinstance(algorithm, str) or algorithm not in ASYMMETRIC_SIGNING_ALGORITHMS:
            raise OidcProtocolError("ID Token 只允许受支持的非对称签名算法")
        metadata = self._metadata()
        if algorithm not in metadata.signing_algorithms:
            raise OidcProtocolError("ID Token 签名算法未被 OIDC 元数据允许")
        key_data = self._select_jwk(header.get("kid"), algorithm)
        try:
            signing_key = jwt.PyJWK.from_dict(dict(key_data), algorithm=algorithm).key
            claims = jwt.decode(
                raw_token,
                signing_key,
                algorithms=[algorithm],
                audience=self.client_id,
                issuer=self.issuer,
                options={
                    "require": ["iss", "sub", "aud", "exp", "iat"],
                    "verify_exp": False,
                    "verify_iat": False,
                    "verify_nbf": False,
                },
            )
        except jwt.PyJWTError as error:
            raise OidcProtocolError("ID Token 签名或标准声明校验失败") from error

        now_timestamp = occurred_at.astimezone(UTC).timestamp()
        leeway_seconds = clock_skew.total_seconds()
        expires_at = _numeric_date(claims, "exp")
        issued_at = _numeric_date(claims, "iat")
        if now_timestamp > expires_at + leeway_seconds:
            raise OidcProtocolError("ID Token 已过期")
        if issued_at > now_timestamp + leeway_seconds:
            raise OidcProtocolError("ID Token 签发时间超出允许时钟偏差")
        if "nbf" in claims and _numeric_date(claims, "nbf") > now_timestamp + leeway_seconds:
            raise OidcProtocolError("ID Token 尚未生效")
        if "auth_time" in claims and _numeric_date(
            claims, "auth_time"
        ) > now_timestamp + leeway_seconds:
            raise OidcProtocolError("ID Token 认证时间超出允许时钟偏差")

        nonce = claims.get("nonce")
        if expected_nonce_digest is None:
            if nonce is not None:
                raise OidcProtocolError("刷新得到的 ID Token 不应包含 nonce")
        elif not isinstance(nonce, str) or not hmac.compare_digest(
            sha256_text(nonce), expected_nonce_digest
        ):
            raise OidcProtocolError("ID Token nonce 不匹配")

        audience = claims.get("aud")
        authorized_party = claims.get("azp")
        if isinstance(audience, list) and len(audience) > 1:
            if authorized_party != self.client_id:
                raise OidcProtocolError("多 audience ID Token 的 azp 不匹配")
        elif authorized_party is not None and authorized_party != self.client_id:
            raise OidcProtocolError("ID Token azp 不匹配")

        at_hash = claims.get("at_hash")
        if at_hash is not None:
            if not isinstance(at_hash, str) or not hmac.compare_digest(
                at_hash,
                _access_token_hash(access_token.reveal(), algorithm),
            ):
                raise OidcProtocolError("ID Token at_hash 不匹配")

        subject = claims.get("sub")
        issuer = claims.get("iss")
        if not isinstance(subject, str) or not subject:
            raise OidcProtocolError("ID Token sub 无效")
        if not isinstance(issuer, str):
            raise OidcProtocolError("ID Token iss 无效")
        email = claims.get("email")
        name = claims.get("name") or claims.get("preferred_username")
        email_verified = claims.get("email_verified")
        return VerifiedIdentity(
            issuer=issuer,
            subject=subject,
            issued_at=datetime.fromtimestamp(issued_at, UTC),
            expires_at=datetime.fromtimestamp(expires_at, UTC),
            email=email if isinstance(email, str) else None,
            display_name=name if isinstance(name, str) else None,
            email_verified=(
                email_verified if isinstance(email_verified, bool) else None
            ),
        )

    def _metadata(self) -> OidcMetadata:
        if self._cached_metadata is not None:
            return self._cached_metadata
        discovery_url = f"{self.issuer.rstrip('/')}/.well-known/openid-configuration"
        document = self.transport.get_json(discovery_url)
        issuer = _required_string(document, "issuer")
        if not hmac.compare_digest(issuer, self.issuer):
            raise OidcProtocolError("OIDC Discovery issuer 与配置不一致")
        authorization_endpoint = _required_string(document, "authorization_endpoint")
        token_endpoint = _required_string(document, "token_endpoint")
        jwks_uri = _required_string(document, "jwks_uri")
        revocation = document.get("revocation_endpoint")
        revocation_endpoint = revocation if isinstance(revocation, str) and revocation else None
        for endpoint in (
            authorization_endpoint,
            token_endpoint,
            jwks_uri,
            revocation_endpoint,
        ):
            if endpoint is not None:
                _validate_endpoint(
                    endpoint,
                    allow_insecure_http=self.allow_insecure_http,
                )
        raw_algorithms = document.get("id_token_signing_alg_values_supported")
        if not isinstance(raw_algorithms, list):
            raise OidcProtocolError("OIDC Discovery 缺少签名算法列表")
        algorithms = tuple(
            algorithm
            for algorithm in raw_algorithms
            if isinstance(algorithm, str) and algorithm in ASYMMETRIC_SIGNING_ALGORITHMS
        )
        if not algorithms:
            raise OidcProtocolError("OIDC 提供方没有可接受的非对称签名算法")
        self._cached_metadata = OidcMetadata(
            issuer=issuer,
            authorization_endpoint=authorization_endpoint,
            token_endpoint=token_endpoint,
            jwks_uri=jwks_uri,
            revocation_endpoint=revocation_endpoint,
            signing_algorithms=algorithms,
        )
        return self._cached_metadata

    def _select_jwk(self, key_id: object, algorithm: str) -> Mapping[str, object]:
        keys = self._jwks(force_refresh=False)
        selected = _matching_keys(keys, key_id=key_id, algorithm=algorithm)
        if not selected:
            selected = _matching_keys(
                self._jwks(force_refresh=True),
                key_id=key_id,
                algorithm=algorithm,
            )
        if len(selected) != 1:
            raise OidcProtocolError("无法唯一确定 ID Token 签名密钥")
        return selected[0]

    def _jwks(self, *, force_refresh: bool) -> tuple[Mapping[str, object], ...]:
        if self._cached_jwks is not None and not force_refresh:
            return self._cached_jwks
        document = self.transport.get_json(self._metadata().jwks_uri)
        raw_keys = document.get("keys")
        if not isinstance(raw_keys, list):
            raise OidcProtocolError("JWKS keys 必须是数组")
        keys = tuple(key for key in raw_keys if isinstance(key, dict))
        if not keys:
            raise OidcProtocolError("JWKS 不包含签名密钥")
        self._cached_jwks = keys
        return keys


def _parse_token_response(
    document: Mapping[str, object],
    *,
    occurred_at: datetime,
    require_id_token: bool,
) -> OidcTokenSet:
    access_token = _required_string(document, "access_token")
    token_type = _required_string(document, "token_type")
    expires_in_value = document.get("expires_in")
    if isinstance(expires_in_value, bool):
        raise OidcProtocolError("OIDC expires_in 无效")
    try:
        expires_in = int(expires_in_value)
    except (TypeError, ValueError) as error:
        raise OidcProtocolError("OIDC expires_in 无效") from error
    id_token_value = document.get("id_token")
    if require_id_token and (not isinstance(id_token_value, str) or not id_token_value):
        raise OidcProtocolError("OIDC 授权码响应缺少 id_token")
    refresh_value = document.get("refresh_token")
    raw_scope = document.get("scope", "")
    scope = tuple(str(raw_scope).split()) if raw_scope else ()
    return OidcTokenSet(
        access_token=SensitiveValue(access_token),
        id_token=(
            SensitiveValue(id_token_value)
            if isinstance(id_token_value, str) and id_token_value
            else None
        ),
        refresh_token=(
            SensitiveValue(refresh_value)
            if isinstance(refresh_value, str) and refresh_value
            else None
        ),
        token_type=token_type,
        expires_in=expires_in,
        scope=scope,
        received_at=occurred_at.astimezone(UTC),
    )


def _matching_keys(
    keys: Sequence[Mapping[str, object]],
    *,
    key_id: object,
    algorithm: str,
) -> tuple[Mapping[str, object], ...]:
    candidates = []
    for key in keys:
        if key.get("use") not in {None, "sig"}:
            continue
        if key.get("alg") not in {None, algorithm}:
            continue
        if key_id is not None and key.get("kid") != key_id:
            continue
        candidates.append(key)
    if key_id is None and len(candidates) != 1:
        return ()
    return tuple(candidates)


def _required_string(document: Mapping[str, object], field_name: str) -> str:
    value = document.get(field_name)
    if not isinstance(value, str) or not value:
        raise OidcProtocolError(f"OIDC 响应缺少 {field_name}")
    return value


def _numeric_date(claims: Mapping[str, object], field_name: str) -> float:
    value = claims.get(field_name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OidcProtocolError(f"ID Token {field_name} 必须是 NumericDate")
    return float(value)


def _access_token_hash(access_token: str, algorithm: str) -> str:
    if algorithm == "EdDSA":
        digest = hashlib.sha512(access_token.encode("ascii")).digest()
    else:
        suffix = algorithm[-3:]
        digest_function = {
            "256": hashlib.sha256,
            "384": hashlib.sha384,
            "512": hashlib.sha512,
        }.get(suffix)
        if digest_function is None:
            raise OidcProtocolError("无法计算当前签名算法的 at_hash")
        digest = digest_function(access_token.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest[: len(digest) // 2]).rstrip(b"=").decode(
        "ascii"
    )


def _validate_endpoint(value: str, *, allow_insecure_http: bool) -> None:
    parsed = urlparse(value)
    if not parsed.netloc or parsed.fragment or parsed.username or parsed.password:
        raise OidcProtocolError("OIDC 端点地址无效")
    if parsed.scheme == "https":
        return
    if parsed.scheme == "http" and allow_insecure_http:
        return
    raise OidcProtocolError("OIDC 端点必须使用 HTTPS")


def _validate_issuer(value: str, *, allow_insecure_http: bool) -> None:
    """校验 issuer，并保留其原始尾部斜杠用于精确声明比较。"""

    parsed = urlparse(value)
    if parsed.query:
        raise OidcProtocolError("OIDC issuer 不能包含查询参数")
    _validate_endpoint(value, allow_insecure_http=allow_insecure_http)


__all__ = [
    "ASYMMETRIC_SIGNING_ALGORITHMS",
    "OidcHttpTransport",
    "OidcMetadata",
    "OidcProviderAdapter",
    "UrllibOidcHttpTransport",
]
