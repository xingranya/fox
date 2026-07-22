"""真实 OIDC Discovery、令牌交换和 ID Token 校验适配器测试。"""

from __future__ import annotations

import base64
import hashlib
import unittest
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from brand_os.identity import (
    OidcProtocolError,
    SensitiveValue,
    sha256_text,
)
from brand_os.oidc_provider import OidcProviderAdapter


ISSUER = "https://identity.example.test"
CLIENT_ID = "brand-os-service"


class FakeOidcTransport:
    """记录 OIDC 请求并返回测试元数据、JWKS 和令牌响应。"""

    def __init__(self, metadata, jwks) -> None:
        self.metadata = metadata
        self.jwks = jwks
        self.token_response: dict[str, object] = {}
        self.gets: list[str] = []
        self.posts: list[tuple[str, dict[str, str], tuple[str, str] | None]] = []

    def get_json(self, url: str):
        self.gets.append(url)
        if url.endswith("/.well-known/openid-configuration"):
            return self.metadata
        if url == self.metadata["jwks_uri"]:
            return self.jwks
        raise AssertionError(f"意外 GET：{url}")

    def post_form(self, url: str, form, *, basic_auth):
        self.posts.append((url, dict(form), basic_auth))
        if url == self.metadata.get("revocation_endpoint"):
            return {}
        return dict(self.token_response)


def _base64url_int(value: int) -> str:
    width = (value.bit_length() + 7) // 8
    return base64.urlsafe_b64encode(value.to_bytes(width, "big")).rstrip(b"=").decode(
        "ascii"
    )


def _at_hash(access_token: str) -> str:
    digest = hashlib.sha256(access_token.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest[: len(digest) // 2]).rstrip(b"=").decode(
        "ascii"
    )


class OidcProviderAdapterTest(unittest.TestCase):
    """验证生产协议适配器不依赖真实身份平台也能完成安全回归。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        numbers = cls.private_key.public_key().public_numbers()
        cls.jwk = {
            "kty": "RSA",
            "kid": "test-key-1",
            "use": "sig",
            "alg": "RS256",
            "n": _base64url_int(numbers.n),
            "e": _base64url_int(numbers.e),
        }

    def setUp(self) -> None:
        self.now = datetime(2026, 7, 23, 10, 0, tzinfo=UTC)
        self.metadata = {
            "issuer": ISSUER,
            "authorization_endpoint": f"{ISSUER}/authorize",
            "token_endpoint": f"{ISSUER}/token",
            "jwks_uri": f"{ISSUER}/jwks",
            "revocation_endpoint": f"{ISSUER}/revoke",
            "id_token_signing_alg_values_supported": ["RS256", "none", "HS256"],
        }
        self.transport = FakeOidcTransport(self.metadata, {"keys": [self.jwk]})
        self.provider = OidcProviderAdapter(
            issuer=ISSUER,
            client_id=CLIENT_ID,
            client_secret="client-secret",
            transport=self.transport,
        )

    def signed_token(
        self,
        *,
        nonce: str | None = "login-nonce",
        access_token: str = "access-token",
        issuer: str = ISSUER,
        audience: str | list[str] = CLIENT_ID,
        issued_at: datetime | None = None,
        expires_at: datetime | None = None,
        at_hash: str | None = None,
        authorized_party: str | None = None,
    ) -> str:
        claims: dict[str, object] = {
            "iss": issuer,
            "sub": "subject-fox",
            "aud": audience,
            "iat": int((issued_at or self.now).timestamp()),
            "exp": int((expires_at or self.now + timedelta(minutes=5)).timestamp()),
            "email": "fox@example.test",
            "email_verified": True,
            "name": "Fox",
            "at_hash": _at_hash(access_token) if at_hash is None else at_hash,
        }
        if nonce is not None:
            claims["nonce"] = nonce
        if authorized_party is not None:
            claims["azp"] = authorized_party
        return jwt.encode(
            claims,
            self.private_key,
            algorithm="RS256",
            headers={"kid": "test-key-1"},
        )

    def verify(self, raw_token: str, *, skew_seconds: int = 60):
        return self.provider.verify_id_token(
            SensitiveValue(raw_token),
            expected_nonce_digest=sha256_text("login-nonce"),
            access_token=SensitiveValue("access-token"),
            occurred_at=self.now,
            clock_skew=timedelta(seconds=skew_seconds),
        )

    def test_authorization_url_and_code_exchange_use_s256_pkce(self) -> None:
        url = self.provider.authorization_url(
            redirect_uri="https://service.example.test/auth/oidc/callback",
            state="state-value",
            nonce="nonce-value",
            code_challenge="challenge-value",
            scopes=("openid", "profile", "email"),
        )
        query = parse_qs(urlparse(url).query)

        self.assertEqual(query["response_type"], ["code"])
        self.assertEqual(query["state"], ["state-value"])
        self.assertEqual(query["nonce"], ["nonce-value"])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["code_challenge"], ["challenge-value"])

        self.transport.token_response = {
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "id_token": "signed-id-token",
            "token_type": "Bearer",
            "expires_in": 300,
            "scope": "openid profile email",
        }
        token_set = self.provider.exchange_code(
            code=SensitiveValue("authorization-code"),
            code_verifier=SensitiveValue("pkce-verifier"),
            redirect_uri="https://service.example.test/auth/oidc/callback",
            occurred_at=self.now,
        )

        token_url, form, basic_auth = self.transport.posts[-1]
        self.assertEqual(token_url, f"{ISSUER}/token")
        self.assertEqual(form["grant_type"], "authorization_code")
        self.assertEqual(form["code_verifier"], "pkce-verifier")
        self.assertEqual(basic_auth, (CLIENT_ID, "client-secret"))
        self.assertEqual(token_set.refresh_token.reveal(), "refresh-token")

    def test_valid_signed_token_resolves_stable_subject(self) -> None:
        identity = self.verify(self.signed_token())

        self.assertEqual(identity.issuer, ISSUER)
        self.assertEqual(identity.subject, "subject-fox")
        self.assertEqual(identity.email, "fox@example.test")
        self.assertTrue(identity.email_verified)

    def test_nonce_audience_issuer_and_at_hash_mismatch_are_rejected(self) -> None:
        cases = {
            "nonce": self.signed_token(nonce="wrong-nonce"),
            "audience": self.signed_token(audience="another-client"),
            "issuer": self.signed_token(issuer="https://other.example.test"),
            "at_hash": self.signed_token(at_hash="wrong-hash"),
        }

        for name, token in cases.items():
            with self.subTest(name=name), self.assertRaises(OidcProtocolError):
                self.verify(token)

    def test_expiry_and_future_issued_at_honor_bounded_clock_skew(self) -> None:
        within_skew = self.signed_token(
            issued_at=self.now + timedelta(seconds=30),
            expires_at=self.now - timedelta(seconds=30),
        )
        self.assertEqual(self.verify(within_skew, skew_seconds=60).subject, "subject-fox")

        expired = self.signed_token(expires_at=self.now - timedelta(seconds=61))
        future = self.signed_token(issued_at=self.now + timedelta(seconds=61))
        for token in (expired, future):
            with self.assertRaises(OidcProtocolError):
                self.verify(token, skew_seconds=60)

    def test_multiple_audiences_require_matching_authorized_party(self) -> None:
        valid = self.signed_token(
            audience=[CLIENT_ID, "resource-api"],
            authorized_party=CLIENT_ID,
        )
        invalid = self.signed_token(
            audience=[CLIENT_ID, "resource-api"],
            authorized_party="another-client",
        )

        self.assertEqual(self.verify(valid).subject, "subject-fox")
        with self.assertRaises(OidcProtocolError):
            self.verify(invalid)

    def test_refresh_token_and_revocation_use_confidential_client(self) -> None:
        self.transport.token_response = {
            "access_token": "rotated-access",
            "refresh_token": "rotated-refresh",
            "token_type": "Bearer",
            "expires_in": 600,
        }

        refreshed = self.provider.refresh(
            SensitiveValue("refresh-token"),
            occurred_at=self.now,
        )
        self.provider.revoke_token(SensitiveValue("rotated-refresh"))

        self.assertEqual(refreshed.access_token.reveal(), "rotated-access")
        self.assertEqual(self.transport.posts[-2][1]["grant_type"], "refresh_token")
        self.assertEqual(self.transport.posts[-1][0], f"{ISSUER}/revoke")
        self.assertEqual(self.transport.posts[-1][1]["token"], "rotated-refresh")

    def test_discovery_issuer_and_https_are_fail_closed(self) -> None:
        with self.assertRaises(OidcProtocolError):
            OidcProviderAdapter(
                issuer="http://identity.example.test",
                client_id=CLIENT_ID,
                client_secret="secret",
                transport=self.transport,
            )

        mismatched = dict(self.metadata, issuer="https://other.example.test")
        provider = OidcProviderAdapter(
            issuer=ISSUER,
            client_id=CLIENT_ID,
            client_secret="secret",
            transport=FakeOidcTransport(mismatched, {"keys": [self.jwk]}),
        )
        with self.assertRaises(OidcProtocolError):
            provider.authorization_url(
                redirect_uri="https://service.example.test/callback",
                state="state",
                nonce="nonce",
                code_challenge="challenge",
                scopes=("openid",),
            )

    def test_trailing_slash_issuer_is_compared_exactly_without_double_slash_discovery(self) -> None:
        issuer = f"{ISSUER}/"
        metadata = dict(
            self.metadata,
            issuer=issuer,
            authorization_endpoint=f"{issuer}authorize",
            token_endpoint=f"{issuer}token",
            jwks_uri=f"{issuer}jwks",
            revocation_endpoint=f"{issuer}revoke",
        )
        transport = FakeOidcTransport(metadata, {"keys": [self.jwk]})
        provider = OidcProviderAdapter(
            issuer=issuer,
            client_id=CLIENT_ID,
            client_secret="secret",
            transport=transport,
        )

        provider.authorization_url(
            redirect_uri="https://service.example.test/callback",
            state="state",
            nonce="nonce",
            code_challenge="challenge",
            scopes=("openid",),
        )

        self.assertEqual(
            transport.gets[0],
            f"{ISSUER}/.well-known/openid-configuration",
        )

    def test_issuer_rejects_query_parameters(self) -> None:
        with self.assertRaises(OidcProtocolError):
            OidcProviderAdapter(
                issuer=f"{ISSUER}?tenant=fox",
                client_id=CLIENT_ID,
                client_secret="secret",
                transport=self.transport,
            )


if __name__ == "__main__":
    unittest.main()
