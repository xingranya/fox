"""OIDC 员工绑定、一次性登录和 PostgreSQL 会话集成测试。"""

from __future__ import annotations

import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import psycopg
from cryptography.fernet import Fernet

from brand_os.domain import Actor, ActorKind
from brand_os.identity import (
    AuthorizationStatus,
    AuthorizationTransaction,
    EmployeeDisabledError,
    IdentityNotBoundError,
    IdentityPermissionDenied,
    LoginReplayError,
    LoginStateError,
    OidcIdentityService,
    OidcProtocolError,
    OidcProviderError,
    OidcTokenSet,
    SensitiveValue,
    SessionExpiredError,
    SessionInvalidError,
    SessionRefreshRequiredError,
    SessionRevokedError,
    VerifiedIdentity,
    pkce_s256_challenge,
    sha256_text,
)
from brand_os.postgresql_identity import PostgreSQLIdentityRepository
from brand_os.secret_cipher import FernetSecretCipher
from phase2.postgresql_test_runtime import TemporaryPostgreSQL


ROOT = Path(__file__).parents[2]
CONTRACT_PATH = ROOT / "contracts" / "phase2" / "oidc-identity.json"
POSTGRESQL: TemporaryPostgreSQL | None = None


def setUpModule() -> None:
    """为本模块启动隔离 PostgreSQL 17。"""

    global POSTGRESQL
    try:
        POSTGRESQL = TemporaryPostgreSQL()
    except RuntimeError as error:
        raise unittest.SkipTest(str(error)) from error
    POSTGRESQL.start()


def tearDownModule() -> None:
    """停止临时 PostgreSQL 并删除测试目录。"""

    if POSTGRESQL is not None:
        POSTGRESQL.stop()


class FakeOidcProvider:
    """只替代外部身份平台，保留服务侧完整状态与加密路径。"""

    def __init__(self) -> None:
        self.issuer = "https://identity.example.test"
        self.subject = "subject-fox"
        self.refresh_subject = "subject-fox"
        self.last_state = ""
        self.last_nonce = ""
        self.last_challenge = ""
        self.last_verifier = ""
        self.access_token = "access-token-v1"
        self.refresh_token = "refresh-token-v1"
        self.refresh_access_token = "access-token-v2"
        self.refresh_refresh_token = "refresh-token-v2"
        self.nonce_mismatch = False
        self.refresh_with_id_token = False
        self.refresh_error: OidcProviderError | None = None
        self.revoke_error: OidcProviderError | None = None
        self.revoked_tokens: list[str] = []

    def authorization_url(
        self,
        *,
        redirect_uri: str,
        state: str,
        nonce: str,
        code_challenge: str,
        scopes,
    ) -> str:
        self.last_state = state
        self.last_nonce = nonce
        self.last_challenge = code_challenge
        return "https://identity.example.test/authorize?" + urlencode(
            {
                "redirect_uri": redirect_uri,
                "state": state,
                "nonce": nonce,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "scope": " ".join(scopes),
            }
        )

    def exchange_code(
        self,
        *,
        code: SensitiveValue,
        code_verifier: SensitiveValue,
        redirect_uri: str,
        occurred_at: datetime,
    ) -> OidcTokenSet:
        del redirect_uri
        if not code.reveal():
            raise OidcProtocolError("授权码为空")
        self.last_verifier = code_verifier.reveal()
        if pkce_s256_challenge(self.last_verifier) != self.last_challenge:
            raise OidcProtocolError("PKCE verifier 不匹配")
        return OidcTokenSet(
            access_token=SensitiveValue(self.access_token),
            id_token=SensitiveValue("login-id-token"),
            refresh_token=SensitiveValue(self.refresh_token),
            token_type="Bearer",
            expires_in=300,
            scope=("openid", "profile", "email"),
            received_at=occurred_at,
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
        del access_token, clock_skew
        if id_token.reveal() == "login-id-token":
            nonce = "wrong-nonce" if self.nonce_mismatch else self.last_nonce
            if expected_nonce_digest != sha256_text(nonce):
                raise OidcProtocolError("nonce 不匹配")
            subject = self.subject
        else:
            if expected_nonce_digest is not None:
                raise OidcProtocolError("刷新令牌不应校验登录 nonce")
            subject = self.refresh_subject
        return VerifiedIdentity(
            issuer=self.issuer,
            subject=subject,
            issued_at=occurred_at,
            expires_at=occurred_at + timedelta(minutes=5),
            email="fox@example.test",
            display_name="Fox",
            email_verified=True,
        )

    def refresh(
        self,
        refresh_token: SensitiveValue,
        *,
        occurred_at: datetime,
    ) -> OidcTokenSet:
        if self.refresh_error is not None:
            raise self.refresh_error
        if refresh_token.reveal() not in {self.refresh_token, self.refresh_refresh_token}:
            raise OidcProviderError("refresh token 被拒绝", retryable=False)
        return OidcTokenSet(
            access_token=SensitiveValue(self.refresh_access_token),
            id_token=(
                SensitiveValue("refresh-id-token")
                if self.refresh_with_id_token
                else None
            ),
            refresh_token=SensitiveValue(self.refresh_refresh_token),
            token_type="Bearer",
            expires_in=600,
            received_at=occurred_at,
        )

    def revoke_token(self, token: SensitiveValue) -> None:
        self.revoked_tokens.append(token.reveal())
        if self.revoke_error is not None:
            raise self.revoke_error


class OidcIdentityContractTest(unittest.TestCase):
    """验证 F2.4 机器契约冻结协议、绑定和人工身份边界。"""

    def test_contract_requires_pkce_prebinding_encryption_and_no_agent_identity(self) -> None:
        contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))

        self.assertEqual(contract["schema_version"], "oidc-identity.v1")
        self.assertEqual(contract["flow"]["pkce_method"], "S256")
        self.assertTrue(contract["flow"]["state_single_use"])
        self.assertTrue(contract["employee_binding"]["pre_registration_required"])
        self.assertFalse(contract["employee_binding"]["email_auto_binding"])
        self.assertTrue(contract["session"]["tokens_encrypted_at_rest"])
        self.assertFalse(contract["authority"]["agent_identity_may_be_employee"])
        self.assertFalse(contract["authority"]["service_identity_may_be_employee"])
        self.assertFalse(contract["migrates_hongri_data"])


class OidcIdentityStoreTest(unittest.TestCase):
    """通过真实 PostgreSQL 验证登录、会话和员工绑定闭环。"""

    def setUp(self) -> None:
        assert POSTGRESQL is not None
        self.database_name, self.dsn = POSTGRESQL.create_database()
        self.key = Fernet.generate_key().decode("ascii")
        self.cipher = FernetSecretCipher(self.key)
        self.repository = PostgreSQLIdentityRepository(
            self.dsn,
            cipher=self.cipher,
        )
        self.provider = FakeOidcProvider()
        self.service = OidcIdentityService(
            provider=self.provider,
            repository=self.repository,
            redirect_uri="https://service.example.test/auth/oidc/callback",
            authorization_ttl=timedelta(minutes=10),
            session_ttl=timedelta(hours=12),
            clock_skew=timedelta(seconds=60),
        )
        self.now = datetime(2026, 7, 23, 10, 0, tzinfo=UTC)
        self.fox = Actor(ActorKind.HUMAN, "Fox")
        self.repository.register_employee(
            employee_id="Fox",
            display_name="Fox",
            primary_email="fox@example.test",
            actor=self.fox,
            occurred_at=self.now,
        )
        self.binding = self.repository.bind_identity(
            employee_id="Fox",
            issuer=self.provider.issuer,
            subject=self.provider.subject,
            email_at_binding="fox@example.test",
            actor=self.fox,
            occurred_at=self.now,
        )

    def tearDown(self) -> None:
        assert POSTGRESQL is not None
        POSTGRESQL.drop_database(self.database_name)

    def begin(self, *, now: datetime | None = None) -> str:
        request = self.service.begin_login(now=now or self.now)
        query = parse_qs(urlparse(request.authorization_url).query)
        return query["state"][0]

    def login(
        self,
        *,
        code: str = "authorization-code",
        now: datetime | None = None,
    ):
        state = self.begin(now=now)
        return self.service.complete_login(
            state=state,
            code=code,
            now=now or self.now,
        )

    def test_latest_schema_and_repository_quick_check(self) -> None:
        self.assertEqual(self.repository.schema_version, 12)
        self.assertTrue(self.repository.quick_check())

    def test_login_creates_interactive_human_principal_and_one_time_state(self) -> None:
        state = self.begin()
        result = self.service.complete_login(
            state=state,
            code="one-time-code",
            now=self.now,
        )

        self.assertEqual(result.principal.employee_id, "Fox")
        self.assertEqual(result.principal.as_actor(), Actor(ActorKind.HUMAN, "Fox"))
        self.assertEqual(
            self.service.authenticate(result.credential.token, now=self.now).session_id,
            result.credential.session_id,
        )
        with self.assertRaises(LoginReplayError):
            self.service.complete_login(
                state=state,
                code="one-time-code",
                now=self.now,
            )

    def test_valid_session_binds_human_command_identity_and_audit(self) -> None:
        result = self.login()

        context = self.service.bind_human_command_context(
            result.credential.token,
            project_id="hongri",
            command_name="review_proposal",
            idempotency_key="review-001",
            expected_version=3,
            now=self.now,
        )

        self.assertEqual(context.actor, Actor(ActorKind.HUMAN, "Fox"))
        self.assertEqual(context.project_id, "hongri")
        self.assertEqual(context.expected_version, 3)
        events = self.repository.list_session_events(result.credential.session_id)
        self.assertEqual([event["event_type"] for event in events], ["CREATED", "IDENTITY_ASSERTED"])
        self.assertEqual([event["sequence_number"] for event in events], [1, 2])
        self.assertEqual(events[-1]["details"]["command_name"], "review_proposal")

    def test_pkce_and_all_raw_credentials_are_not_stored(self) -> None:
        result = self.login(code="private-code")
        raw_session = result.credential.token.reveal()
        raw_secret = raw_session.split(".", 1)[1]

        with psycopg.connect(self.dsn, autocommit=True) as connection:
            authorization = connection.execute(
                """
                SELECT state_digest, nonce_digest, code_verifier_ciphertext,
                       authorization_code_digest
                FROM oidc_authorization_transactions
                """
            ).fetchone()
            session = connection.execute(
                """
                SELECT session_secret_digest, access_token_ciphertext,
                       refresh_token_ciphertext
                FROM employee_sessions
                """
            ).fetchone()

        stored = "|".join(str(value) for value in (*authorization, *session))
        for raw in (
            self.provider.last_state,
            self.provider.last_nonce,
            self.provider.last_verifier,
            "private-code",
            raw_secret,
            self.provider.access_token,
            self.provider.refresh_token,
        ):
            self.assertNotIn(raw, stored)
        self.assertEqual(authorization[0], sha256_text(self.provider.last_state))
        self.assertEqual(session[0], sha256_text(raw_secret))

    def test_wrong_session_secret_and_non_session_actor_cannot_impersonate_employee(self) -> None:
        result = self.login()
        invalid = f"{result.credential.session_id}.wrong-secret"

        with self.assertRaises(SessionInvalidError):
            self.service.authenticate(invalid, now=self.now)
        with self.assertRaises(SessionInvalidError):
            self.service.authenticate(Actor(ActorKind.AI, "codex"), now=self.now)  # type: ignore[arg-type]
        with self.assertRaises(IdentityPermissionDenied):
            self.repository.revoke_employee_sessions(
                "Fox",
                actor_id="service",
                reason="attempted impersonation",
                occurred_at=self.now,
            )
        with self.assertRaises(SessionInvalidError):
            self.service.revoke_employee_sessions(
                "forged-admin-session",
                employee_id="Fox",
                reason="attempted impersonation",
                now=self.now,
            )

    def test_authorization_expiry_nonce_mismatch_and_code_replay_fail_closed(self) -> None:
        expired_state = self.begin()
        with self.assertRaises(LoginStateError):
            self.service.complete_login(
                state=expired_state,
                code="expired-code",
                now=self.now + timedelta(minutes=11),
            )

        self.provider.nonce_mismatch = True
        bad_nonce_state = self.begin()
        with self.assertRaises(OidcProtocolError):
            self.service.complete_login(
                state=bad_nonce_state,
                code="bad-nonce-code",
                now=self.now,
            )
        self.provider.nonce_mismatch = False

        self.login(code="reused-code")
        second_state = self.begin()
        with self.assertRaises(LoginReplayError):
            self.service.complete_login(
                state=second_state,
                code="reused-code",
                now=self.now,
            )

    def test_concurrent_callbacks_cannot_claim_the_same_authorization_code(self) -> None:
        states = ("parallel-state-a", "parallel-state-b")
        for index, state in enumerate(states):
            self.repository.create_authorization(
                AuthorizationTransaction(
                    transaction_id=f"AUTH-PARALLEL-{index}",
                    state_digest=sha256_text(state),
                    nonce_digest=sha256_text(f"nonce-{index}"),
                    code_verifier=SensitiveValue(f"verifier-{index}"),
                    redirect_uri="https://service.example.test/auth/oidc/callback",
                    status=AuthorizationStatus.PENDING,
                    created_at=self.now,
                    expires_at=self.now + timedelta(minutes=10),
                )
            )
        barrier = Barrier(2)

        def claim(state: str) -> str:
            barrier.wait()
            try:
                self.repository.claim_authorization(
                    state_digest=sha256_text(state),
                    authorization_code_digest=sha256_text("parallel-code"),
                    occurred_at=self.now,
                )
            except LoginReplayError:
                return "replay"
            return "claimed"

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = tuple(executor.map(claim, states))

        self.assertEqual(sorted(outcomes), ["claimed", "replay"])

    def test_unbound_subject_and_email_do_not_auto_create_employee(self) -> None:
        self.provider.subject = "unbound-subject"
        state = self.begin()

        with self.assertRaises(IdentityNotBoundError):
            self.service.complete_login(
                state=state,
                code="unbound-code",
                now=self.now,
            )

        with psycopg.connect(self.dsn, autocommit=True) as connection:
            employee_count = connection.execute("SELECT COUNT(*) FROM employees").fetchone()[0]
            session_count = connection.execute(
                "SELECT COUNT(*) FROM employee_sessions"
            ).fetchone()[0]
        self.assertEqual(employee_count, 1)
        self.assertEqual(session_count, 0)

    def test_access_expiry_requires_refresh_and_refresh_rotates_tokens(self) -> None:
        result = self.login()
        after_access_expiry = self.now + timedelta(minutes=6)

        with self.assertRaises(SessionRefreshRequiredError):
            self.service.authenticate(
                result.credential.token,
                now=after_access_expiry,
                require_access_token=True,
            )
        principal = self.service.refresh_session(
            result.credential.token,
            now=after_access_expiry,
        )
        session = self.repository.get_session(result.credential.session_id)

        self.assertEqual(principal.employee_id, "Fox")
        self.assertEqual(session.token_version, 2)
        self.assertEqual(session.access_token.reveal(), "access-token-v2")
        self.assertEqual(session.refresh_token.reveal(), "refresh-token-v2")
        self.assertEqual(
            [event["event_type"] for event in self.repository.list_session_events(session.session_id)],
            ["CREATED", "REFRESHED"],
        )

    def test_nonretryable_refresh_rejection_revokes_session(self) -> None:
        result = self.login()
        self.provider.refresh_error = OidcProviderError(
            "refresh token 被撤销",
            retryable=False,
        )

        with self.assertRaises(OidcProviderError):
            self.service.refresh_session(
                result.credential.token,
                now=self.now + timedelta(minutes=6),
            )
        with self.assertRaises(SessionRevokedError):
            self.service.authenticate(result.credential.token, now=self.now)

    def test_retryable_refresh_failure_keeps_session_active(self) -> None:
        result = self.login()
        self.provider.refresh_error = OidcProviderError(
            "identity provider timeout",
            retryable=True,
        )

        with self.assertRaises(OidcProviderError):
            self.service.refresh_session(
                result.credential.token,
                now=self.now + timedelta(minutes=6),
            )

        principal = self.service.authenticate(result.credential.token, now=self.now)
        self.assertEqual(principal.employee_id, "Fox")

    def test_refresh_identity_change_revokes_session(self) -> None:
        result = self.login()
        self.provider.refresh_with_id_token = True
        self.provider.refresh_subject = "different-subject"

        with self.assertRaises(OidcProtocolError):
            self.service.refresh_session(
                result.credential.token,
                now=self.now + timedelta(minutes=6),
            )
        with self.assertRaises(SessionRevokedError):
            self.service.authenticate(result.credential.token, now=self.now)

    def test_local_logout_remains_revoked_when_provider_revocation_fails(self) -> None:
        result = self.login()
        self.provider.revoke_error = OidcProviderError(
            "provider unavailable",
            retryable=True,
        )

        self.service.revoke_session(result.credential.token, now=self.now)

        self.assertEqual(self.provider.revoked_tokens, ["refresh-token-v1"])
        with psycopg.connect(self.dsn, autocommit=True) as connection:
            stored_tokens = connection.execute(
                """
                SELECT access_token_ciphertext, refresh_token_ciphertext
                FROM employee_sessions WHERE session_id = %s
                """,
                (result.credential.session_id,),
            ).fetchone()
        self.assertEqual(stored_tokens, (None, None))
        with self.assertRaises(SessionRevokedError):
            self.service.authenticate(result.credential.token, now=self.now)

    def test_disabling_employee_revokes_existing_session_and_blocks_new_login(self) -> None:
        result = self.login()
        revoked_count = self.repository.disable_employee(
            "Fox",
            actor=self.fox,
            reason="离职",
            occurred_at=self.now + timedelta(minutes=1),
        )

        self.assertEqual(revoked_count, 1)
        with self.assertRaises(SessionRevokedError):
            self.service.authenticate(result.credential.token, now=self.now)
        state = self.begin()
        with self.assertRaises(EmployeeDisabledError):
            self.service.complete_login(
                state=state,
                code="disabled-code",
                now=self.now,
            )

    def test_authenticated_identity_admin_can_revoke_all_employee_sessions(self) -> None:
        first = self.login(code="first-code")
        second = self.login(code="second-code")

        revoked = self.service.revoke_employee_sessions(
            first.credential.token,
            employee_id="Fox",
            reason="security response",
            now=self.now,
        )

        self.assertEqual(revoked, 2)
        for result in (first, second):
            with self.assertRaises(SessionRevokedError):
                self.service.authenticate(result.credential.token, now=self.now)

    def test_absolute_session_expiry_is_persisted_and_audited(self) -> None:
        result = self.login()

        with self.assertRaises(SessionExpiredError):
            self.service.authenticate(
                result.credential.token,
                now=self.now + timedelta(hours=12),
            )
        events = self.repository.list_session_events(result.credential.session_id)
        self.assertEqual(events[-1]["event_type"], "EXPIRED")

    def test_session_survives_service_restart_with_same_encryption_key(self) -> None:
        result = self.login()
        reopened_repository = PostgreSQLIdentityRepository(
            self.dsn,
            cipher=FernetSecretCipher(self.key),
        )
        reopened_service = OidcIdentityService(
            provider=self.provider,
            repository=reopened_repository,
            redirect_uri="https://service.example.test/auth/oidc/callback",
        )

        principal = reopened_service.authenticate(result.credential.token, now=self.now)

        self.assertEqual(principal.employee_id, "Fox")

    def test_identity_management_rejects_ai_and_service_actors(self) -> None:
        for actor in (
            Actor(ActorKind.AI, "codex"),
            Actor(ActorKind.WORKFLOW, "dify"),
            Actor(ActorKind.SYSTEM, "service"),
        ):
            with self.subTest(kind=actor.kind), self.assertRaises(
                IdentityPermissionDenied
            ):
                self.repository.bind_identity(
                    employee_id="Fox",
                    issuer=self.provider.issuer,
                    subject=f"subject-{actor.kind.value}",
                    email_at_binding=None,
                    actor=actor,
                    occurred_at=self.now,
                )


if __name__ == "__main__":
    unittest.main()
