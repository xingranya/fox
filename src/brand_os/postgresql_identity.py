"""OIDC 员工绑定、授权事务和服务器会话的 PostgreSQL 适配器。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

from .domain import Actor, ActorKind
from .identity import (
    AuthorizationStatus,
    AuthorizationTransaction,
    EmployeeAccount,
    EmployeeSession,
    EmployeeStatus,
    IdentityBinding,
    IdentityBindingStatus,
    IdentityError,
    IdentityNotBoundError,
    IdentityPermissionDenied,
    LoginReplayError,
    LoginStateError,
    OidcTokenSet,
    SessionExpiredError,
    SessionInvalidError,
    SessionRevokedError,
    SessionStatus,
)
from .postgresql_store import PostgreSQLConnection, PostgreSQLStoreBase
from .secret_cipher import FernetSecretCipher


OIDC_IDENTITY_TABLES = frozenset(
    {
        "employees",
        "oidc_identity_bindings",
        "oidc_authorization_transactions",
        "employee_sessions",
        "employee_session_events",
    }
)


class PostgreSQLIdentityRepository(PostgreSQLStoreBase):
    """持久化预登记员工、单次授权事务和加密会话令牌。"""

    def __init__(
        self,
        dsn: str,
        *,
        cipher: FernetSecretCipher,
        identity_admins: tuple[str, ...] = ("Fox",),
    ) -> None:
        self.cipher = cipher
        self.identity_admins = frozenset(identity_admins)
        super().__init__(dsn)

    def quick_check(self) -> bool:
        """核对公共迁移和 F2.4 身份会话表。"""

        if not super().quick_check():
            return False
        with self._connect() as connection:
            tables = {
                str(row[0])
                for row in connection.execute(
                    """
                    SELECT table_name FROM information_schema.tables
                    WHERE table_schema = current_schema()
                      AND table_name IN (?, ?, ?, ?, ?)
                    """,
                    tuple(sorted(OIDC_IDENTITY_TABLES)),
                )
            }
        return tables == OIDC_IDENTITY_TABLES

    def register_employee(
        self,
        *,
        employee_id: str,
        display_name: str,
        primary_email: str | None,
        actor: Actor,
        occurred_at: datetime,
    ) -> EmployeeAccount:
        """由身份管理员预登记员工；OIDC 登录不能自动建号。"""

        self._require_identity_admin(actor)
        if not employee_id.strip() or not display_name.strip():
            raise ValueError("employee_id 和 display_name 不能为空")
        occurred = _iso(occurred_at)
        with self._connect() as connection:
            connection.execute("BEGIN")
            try:
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(?, 0))",
                    (f"employee:{employee_id}",),
                )
                existing = connection.execute(
                    "SELECT * FROM employees WHERE employee_id = ? FOR UPDATE",
                    (employee_id,),
                ).fetchone()
                if existing is not None:
                    employee = _employee_from_row(existing)
                    if (
                        employee.display_name != display_name
                        or employee.primary_email != primary_email
                    ):
                        raise IdentityError("同一 employee_id 不能静默绑定不同员工资料")
                    connection.execute("COMMIT")
                    return employee
                connection.execute(
                    """
                    INSERT INTO employees(
                        employee_id, display_name, primary_email, status,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, 'ACTIVE', ?, ?)
                    """,
                    (employee_id, display_name, primary_email, occurred, occurred),
                )
                row = connection.execute(
                    "SELECT * FROM employees WHERE employee_id = ?",
                    (employee_id,),
                ).fetchone()
                connection.execute("COMMIT")
                return _employee_from_row(row)
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def bind_identity(
        self,
        *,
        employee_id: str,
        issuer: str,
        subject: str,
        email_at_binding: str | None,
        actor: Actor,
        occurred_at: datetime,
    ) -> IdentityBinding:
        """显式绑定外部 issuer/subject，不按邮箱自动合并员工。"""

        self._require_identity_admin(actor)
        normalized_issuer = issuer
        if not normalized_issuer or not subject.strip():
            raise ValueError("issuer 和 subject 不能为空")
        occurred = _iso(occurred_at)
        lock_key = f"oidc-binding:{normalized_issuer}:{subject}"
        with self._connect() as connection:
            connection.execute("BEGIN")
            try:
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(?, 0))",
                    (lock_key,),
                )
                employee_row = connection.execute(
                    "SELECT * FROM employees WHERE employee_id = ? FOR UPDATE",
                    (employee_id,),
                ).fetchone()
                if employee_row is None:
                    raise IdentityError("待绑定员工不存在")
                employee = _employee_from_row(employee_row)
                if employee.status is not EmployeeStatus.ACTIVE:
                    raise IdentityError("不能给已停用员工新增身份绑定")
                existing = self._load_binding_row(
                    connection,
                    normalized_issuer,
                    subject,
                    for_update=True,
                )
                if existing is not None:
                    binding = _binding_from_row(existing)
                    if binding.employee.employee_id != employee_id:
                        raise IdentityError("OIDC 身份已经绑定其他员工")
                    connection.execute("COMMIT")
                    return binding
                binding_id = f"BIND-{uuid4().hex.upper()}"
                connection.execute(
                    """
                    INSERT INTO oidc_identity_bindings(
                        binding_id, issuer, subject, employee_id, email_at_binding,
                        status, created_at, created_by
                    ) VALUES (?, ?, ?, ?, ?, 'ACTIVE', ?, ?)
                    """,
                    (
                        binding_id,
                        normalized_issuer,
                        subject,
                        employee_id,
                        email_at_binding,
                        occurred,
                        actor.actor_id,
                    ),
                )
                row = self._load_binding_row(
                    connection,
                    normalized_issuer,
                    subject,
                )
                connection.execute("COMMIT")
                return _binding_from_row(row)
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def disable_employee(
        self,
        employee_id: str,
        *,
        actor: Actor,
        reason: str,
        occurred_at: datetime,
    ) -> int:
        """停用员工并在同一事务撤销其全部活动会话。"""

        self._require_identity_admin(actor)
        if not reason.strip():
            raise ValueError("停用原因不能为空")
        occurred = _iso(occurred_at)
        with self._connect() as connection:
            connection.execute("BEGIN")
            try:
                row = connection.execute(
                    "SELECT * FROM employees WHERE employee_id = ? FOR UPDATE",
                    (employee_id,),
                ).fetchone()
                if row is None:
                    raise IdentityError("员工不存在")
                connection.execute(
                    """
                    UPDATE employees
                    SET status = 'DISABLED', updated_at = ?, disabled_at = ?,
                        disabled_by = ?, disable_reason = ?
                    WHERE employee_id = ?
                    """,
                    (occurred, occurred, actor.actor_id, reason, employee_id),
                )
                sessions = connection.execute(
                    """
                    SELECT session_id FROM employee_sessions
                    WHERE employee_id = ? AND status = 'ACTIVE'
                    FOR UPDATE
                    """,
                    (employee_id,),
                ).fetchall()
                for session in sessions:
                    self._revoke_locked_session(
                        connection,
                        str(session["session_id"]),
                        employee_id,
                        reason="employee_disabled",
                        actor_kind=actor.kind,
                        actor_id=actor.actor_id,
                        occurred=occurred,
                    )
                connection.execute("COMMIT")
                return len(sessions)
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def disable_binding(
        self,
        binding_id: str,
        *,
        actor: Actor,
        reason: str,
        occurred_at: datetime,
    ) -> int:
        """停用一个外部身份绑定并撤销由它建立的活动会话。"""

        self._require_identity_admin(actor)
        if not reason.strip():
            raise ValueError("停用原因不能为空")
        occurred = _iso(occurred_at)
        with self._connect() as connection:
            connection.execute("BEGIN")
            try:
                binding = connection.execute(
                    """
                    SELECT * FROM oidc_identity_bindings
                    WHERE binding_id = ? FOR UPDATE
                    """,
                    (binding_id,),
                ).fetchone()
                if binding is None:
                    raise IdentityError("OIDC 身份绑定不存在")
                connection.execute(
                    """
                    UPDATE oidc_identity_bindings
                    SET status = 'DISABLED', disabled_at = ?, disabled_by = ?,
                        disable_reason = ?
                    WHERE binding_id = ?
                    """,
                    (occurred, actor.actor_id, reason, binding_id),
                )
                sessions = connection.execute(
                    """
                    SELECT session_id, employee_id FROM employee_sessions
                    WHERE binding_id = ? AND status = 'ACTIVE'
                    FOR UPDATE
                    """,
                    (binding_id,),
                ).fetchall()
                for session in sessions:
                    self._revoke_locked_session(
                        connection,
                        str(session["session_id"]),
                        str(session["employee_id"]),
                        reason="identity_binding_disabled",
                        actor_kind=actor.kind,
                        actor_id=actor.actor_id,
                        occurred=occurred,
                    )
                connection.execute("COMMIT")
                return len(sessions)
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def create_authorization(self, transaction: AuthorizationTransaction) -> None:
        """保存只包含摘要和加密 verifier 的一次性授权事务。"""

        verifier_ciphertext = self.cipher.encrypt(transaction.code_verifier)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO oidc_authorization_transactions(
                    transaction_id, state_digest, nonce_digest,
                    code_verifier_ciphertext, redirect_uri, status,
                    created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    transaction.transaction_id,
                    transaction.state_digest,
                    transaction.nonce_digest,
                    verifier_ciphertext,
                    transaction.redirect_uri,
                    transaction.status.value,
                    _iso(transaction.created_at),
                    _iso(transaction.expires_at),
                ),
            )

    def claim_authorization(
        self,
        *,
        state_digest: str,
        authorization_code_digest: str,
        occurred_at: datetime,
    ) -> AuthorizationTransaction:
        """原子认领 state 和授权码，确保并发回调只有一个成功。"""

        occurred = _iso(occurred_at)
        resolved: AuthorizationTransaction | None = None
        terminal_error: IdentityError | None = None
        with self._connect() as connection:
            connection.execute("BEGIN")
            try:
                for lock_key in (
                    f"oidc-state:{state_digest}",
                    f"oidc-code:{authorization_code_digest}",
                ):
                    connection.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended(?, 0))",
                        (lock_key,),
                    )
                row = connection.execute(
                    """
                    SELECT * FROM oidc_authorization_transactions
                    WHERE state_digest = ? FOR UPDATE
                    """,
                    (state_digest,),
                ).fetchone()
                if row is None:
                    terminal_error = LoginStateError("OIDC state 不存在")
                elif AuthorizationStatus(str(row["status"])) is not AuthorizationStatus.PENDING:
                    terminal_error = LoginReplayError("OIDC state 已被使用")
                elif _datetime(str(row["expires_at"])) <= occurred_at.astimezone(UTC):
                    connection.execute(
                        """
                        UPDATE oidc_authorization_transactions
                        SET status = 'EXPIRED', failed_at = ?, failure_code = 'state_expired'
                        WHERE transaction_id = ?
                        """,
                        (occurred, row["transaction_id"]),
                    )
                    terminal_error = LoginStateError("OIDC state 已过期")
                else:
                    used_code = connection.execute(
                        """
                        SELECT transaction_id FROM oidc_authorization_transactions
                        WHERE authorization_code_digest = ?
                        FOR UPDATE
                        """,
                        (authorization_code_digest,),
                    ).fetchone()
                    if used_code is not None:
                        connection.execute(
                            """
                            UPDATE oidc_authorization_transactions
                            SET status = 'FAILED', failed_at = ?, failure_code = 'code_replay'
                            WHERE transaction_id = ?
                            """,
                            (occurred, row["transaction_id"]),
                        )
                        terminal_error = LoginReplayError("OIDC 授权码已被使用")
                    else:
                        connection.execute(
                            """
                            UPDATE oidc_authorization_transactions
                            SET status = 'PROCESSING', authorization_code_digest = ?,
                                claimed_at = ?
                            WHERE transaction_id = ?
                            """,
                            (
                                authorization_code_digest,
                                occurred,
                                row["transaction_id"],
                            ),
                        )
                        updated = connection.execute(
                            """
                            SELECT * FROM oidc_authorization_transactions
                            WHERE transaction_id = ?
                            """,
                            (row["transaction_id"],),
                        ).fetchone()
                        resolved = self._authorization_from_row(updated)
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        if terminal_error is not None:
            raise terminal_error
        if resolved is None:
            raise LoginStateError("OIDC 授权事务无法认领")
        return resolved

    def fail_authorization(
        self,
        transaction_id: str,
        *,
        reason_code: str,
        occurred_at: datetime,
    ) -> None:
        """记录已认领授权事务的安全失败类型，不保存令牌或异常正文。"""

        with self._connect() as connection:
            connection.execute(
                """
                UPDATE oidc_authorization_transactions
                SET status = 'FAILED', failed_at = ?, failure_code = ?
                WHERE transaction_id = ? AND status = 'PROCESSING'
                """,
                (_iso(occurred_at), reason_code[:120], transaction_id),
            )

    def resolve_binding(self, issuer: str, subject: str) -> IdentityBinding:
        """只按稳定 issuer/subject 查找预绑定员工。"""

        with self._connect() as connection:
            row = self._load_binding_row(
                connection,
                issuer,
                subject,
            )
        if row is None:
            raise IdentityNotBoundError("OIDC 身份尚未绑定内部员工")
        return _binding_from_row(row)

    def create_session(
        self,
        *,
        transaction_id: str,
        binding: IdentityBinding,
        session_id: str,
        session_secret_digest: str,
        token_set: OidcTokenSet,
        access_token_expires_at: datetime,
        session_expires_at: datetime,
        occurred_at: datetime,
    ) -> EmployeeSession:
        """在一个事务中消费授权事务并创建员工会话。"""

        occurred = _iso(occurred_at)
        access_ciphertext = self.cipher.encrypt(token_set.access_token)
        refresh_ciphertext = (
            self.cipher.encrypt(token_set.refresh_token)
            if token_set.refresh_token is not None
            else None
        )
        with self._connect() as connection:
            connection.execute("BEGIN")
            try:
                authorization = connection.execute(
                    """
                    SELECT * FROM oidc_authorization_transactions
                    WHERE transaction_id = ? FOR UPDATE
                    """,
                    (transaction_id,),
                ).fetchone()
                if authorization is None or authorization["status"] != "PROCESSING":
                    raise LoginReplayError("OIDC 授权事务不能重复创建会话")
                binding_row = connection.execute(
                    """
                    SELECT b.*, e.display_name AS employee_display_name,
                           e.primary_email AS employee_primary_email,
                           e.status AS employee_status,
                           e.created_at AS employee_created_at,
                           e.updated_at AS employee_updated_at
                    FROM oidc_identity_bindings b
                    JOIN employees e ON e.employee_id = b.employee_id
                    WHERE b.binding_id = ? FOR UPDATE
                    """,
                    (binding.binding_id,),
                ).fetchone()
                if binding_row is None:
                    raise IdentityNotBoundError("OIDC 身份绑定不存在")
                current_binding = _binding_from_row(binding_row)
                if (
                    current_binding.status is not IdentityBindingStatus.ACTIVE
                    or current_binding.employee.status is not EmployeeStatus.ACTIVE
                ):
                    raise IdentityError("员工或 OIDC 身份绑定已经停用")
                connection.execute(
                    """
                    INSERT INTO employee_sessions(
                        session_id, session_secret_digest, employee_id, binding_id,
                        status, access_token_ciphertext, refresh_token_ciphertext,
                        token_version, access_token_expires_at, session_expires_at,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'ACTIVE', ?, ?, 1, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        session_secret_digest,
                        current_binding.employee.employee_id,
                        current_binding.binding_id,
                        access_ciphertext,
                        refresh_ciphertext,
                        _iso(access_token_expires_at),
                        _iso(session_expires_at),
                        occurred,
                        occurred,
                    ),
                )
                connection.execute(
                    """
                    UPDATE oidc_authorization_transactions
                    SET status = 'CONSUMED', consumed_at = ?
                    WHERE transaction_id = ?
                    """,
                    (occurred, transaction_id),
                )
                self._insert_session_event(
                    connection,
                    session_id=session_id,
                    employee_id=current_binding.employee.employee_id,
                    event_type="CREATED",
                    actor_kind=ActorKind.HUMAN,
                    actor_id=current_binding.employee.employee_id,
                    details={"binding_id": current_binding.binding_id},
                    occurred=occurred,
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return self.get_session(session_id)

    def get_session(self, session_id: str) -> EmployeeSession:
        """读取一个会话并解密令牌材料。"""

        with self._connect() as connection:
            row = self._load_session_row(connection, session_id)
        if row is None:
            raise SessionInvalidError("会话不存在")
        return self._session_from_row(row)

    def rotate_session_tokens(
        self,
        session_id: str,
        *,
        expected_token_version: int,
        token_set: OidcTokenSet,
        access_token_expires_at: datetime,
        occurred_at: datetime,
    ) -> EmployeeSession:
        """乐观锁更新令牌，支持提供方旋转刷新令牌。"""

        occurred = _iso(occurred_at)
        access_ciphertext = self.cipher.encrypt(token_set.access_token)
        with self._connect() as connection:
            connection.execute("BEGIN")
            try:
                row = self._load_session_row(connection, session_id, for_update=True)
                if row is None:
                    raise SessionInvalidError("会话不存在")
                if row["status"] != "ACTIVE":
                    raise SessionRevokedError("会话已经失效")
                if int(row["token_version"]) != expected_token_version:
                    raise IdentityError("会话令牌版本冲突")
                refresh_ciphertext = (
                    self.cipher.encrypt(token_set.refresh_token)
                    if token_set.refresh_token is not None
                    else str(row["refresh_token_ciphertext"])
                    if row["refresh_token_ciphertext"] is not None
                    else None
                )
                connection.execute(
                    """
                    UPDATE employee_sessions
                    SET access_token_ciphertext = ?, refresh_token_ciphertext = ?,
                        token_version = token_version + 1,
                        access_token_expires_at = ?, updated_at = ?
                    WHERE session_id = ? AND token_version = ?
                    """,
                    (
                        access_ciphertext,
                        refresh_ciphertext,
                        _iso(access_token_expires_at),
                        occurred,
                        session_id,
                        expected_token_version,
                    ),
                )
                self._insert_session_event(
                    connection,
                    session_id=session_id,
                    employee_id=str(row["employee_id"]),
                    event_type="REFRESHED",
                    actor_kind=ActorKind.SYSTEM,
                    actor_id="oidc-session-service",
                    details={"token_version": expected_token_version + 1},
                    occurred=occurred,
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return self.get_session(session_id)

    def revoke_session(
        self,
        session_id: str,
        *,
        reason: str,
        actor_kind: ActorKind,
        actor_id: str,
        occurred_at: datetime,
    ) -> bool:
        """幂等撤销一个活动会话。"""

        occurred = _iso(occurred_at)
        with self._connect() as connection:
            connection.execute("BEGIN")
            try:
                row = connection.execute(
                    """
                    SELECT session_id, employee_id, status FROM employee_sessions
                    WHERE session_id = ? FOR UPDATE
                    """,
                    (session_id,),
                ).fetchone()
                if row is None:
                    raise SessionInvalidError("会话不存在")
                changed = row["status"] == "ACTIVE"
                if changed:
                    self._revoke_locked_session(
                        connection,
                        session_id,
                        str(row["employee_id"]),
                        reason=reason,
                        actor_kind=actor_kind,
                        actor_id=actor_id,
                        occurred=occurred,
                    )
                connection.execute("COMMIT")
                return changed
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def revoke_employee_sessions(
        self,
        employee_id: str,
        *,
        reason: str,
        actor_id: str,
        occurred_at: datetime,
    ) -> int:
        """由已验证身份管理员撤销某员工全部活动会话。"""

        if actor_id not in self.identity_admins:
            raise IdentityPermissionDenied("当前员工没有身份管理权限")
        occurred = _iso(occurred_at)
        with self._connect() as connection:
            connection.execute("BEGIN")
            try:
                rows = connection.execute(
                    """
                    SELECT session_id FROM employee_sessions
                    WHERE employee_id = ? AND status = 'ACTIVE'
                    FOR UPDATE
                    """,
                    (employee_id,),
                ).fetchall()
                for row in rows:
                    self._revoke_locked_session(
                        connection,
                        str(row["session_id"]),
                        employee_id,
                        reason=reason,
                        actor_kind=ActorKind.HUMAN,
                        actor_id=actor_id,
                        occurred=occurred,
                    )
                connection.execute("COMMIT")
                return len(rows)
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def expire_session(self, session_id: str, *, occurred_at: datetime) -> bool:
        """惰性标记一个已经超过绝对有效期的会话。"""

        occurred = _iso(occurred_at)
        with self._connect() as connection:
            connection.execute("BEGIN")
            try:
                row = connection.execute(
                    """
                    SELECT session_id, employee_id, status, session_expires_at
                    FROM employee_sessions WHERE session_id = ? FOR UPDATE
                    """,
                    (session_id,),
                ).fetchone()
                if row is None:
                    raise SessionInvalidError("会话不存在")
                changed = (
                    row["status"] == "ACTIVE"
                    and _datetime(str(row["session_expires_at"])) <= occurred_at.astimezone(UTC)
                )
                if changed:
                    connection.execute(
                        """
                    UPDATE employee_sessions
                    SET status = 'EXPIRED', updated_at = ?, revoked_at = ?,
                            revoked_by = 'oidc-session-service',
                            revocation_reason = 'absolute_session_expired',
                            access_token_ciphertext = NULL,
                            refresh_token_ciphertext = NULL
                        WHERE session_id = ?
                        """,
                        (occurred, occurred, session_id),
                    )
                    self._insert_session_event(
                        connection,
                        session_id=session_id,
                        employee_id=str(row["employee_id"]),
                        event_type="EXPIRED",
                        actor_kind=ActorKind.SYSTEM,
                        actor_id="oidc-session-service",
                        details={"reason": "absolute_session_expired"},
                        occurred=occurred,
                    )
                connection.execute("COMMIT")
                return changed
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def list_session_events(self, session_id: str) -> tuple[dict[str, object], ...]:
        """按时间返回不含令牌的会话审计事件。"""

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM employee_session_events
                WHERE session_id = ? ORDER BY sequence_number
                """,
                (session_id,),
            ).fetchall()
        return tuple(
            {
                "event_id": str(row["event_id"]),
                "session_id": str(row["session_id"]),
                "sequence_number": int(row["sequence_number"]),
                "employee_id": str(row["employee_id"]),
                "event_type": str(row["event_type"]),
                "actor_kind": str(row["actor_kind"]),
                "actor_id": str(row["actor_id"]),
                "details": json.loads(str(row["details_json"])),
                "occurred_at": str(row["occurred_at"]),
            }
            for row in rows
        )

    def record_identity_assertion(
        self,
        session_id: str,
        *,
        project_id: str,
        command_name: str,
        idempotency_key: str,
        occurred_at: datetime,
    ) -> None:
        """记录某领域命令使用了哪一个交互式员工会话。"""

        occurred = _iso(occurred_at)
        with self._connect() as connection:
            connection.execute("BEGIN")
            try:
                row = connection.execute(
                    """
                    SELECT session_id, employee_id, status, session_expires_at
                    FROM employee_sessions WHERE session_id = ? FOR UPDATE
                    """,
                    (session_id,),
                ).fetchone()
                if row is None or row["status"] != "ACTIVE":
                    raise SessionInvalidError("只有活动员工会话可以绑定领域命令")
                if _datetime(str(row["session_expires_at"])) <= occurred_at.astimezone(UTC):
                    raise SessionExpiredError("员工会话已经过期")
                self._insert_session_event(
                    connection,
                    session_id=session_id,
                    employee_id=str(row["employee_id"]),
                    event_type="IDENTITY_ASSERTED",
                    actor_kind=ActorKind.HUMAN,
                    actor_id=str(row["employee_id"]),
                    details={
                        "project_id": project_id,
                        "command_name": command_name,
                        "idempotency_key": idempotency_key,
                    },
                    occurred=occurred,
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def expire_due_authorizations(self, *, occurred_at: datetime) -> int:
        """批量关闭无人回调的过期 state。"""

        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE oidc_authorization_transactions
                SET status = 'EXPIRED', failed_at = ?, failure_code = 'state_expired'
                WHERE status = 'PENDING' AND expires_at <= ?
                """,
                (_iso(occurred_at), _iso(occurred_at)),
            )
        return max(cursor.rowcount, 0)

    def _authorization_from_row(self, row) -> AuthorizationTransaction:
        return AuthorizationTransaction(
            transaction_id=str(row["transaction_id"]),
            state_digest=str(row["state_digest"]),
            nonce_digest=str(row["nonce_digest"]),
            code_verifier=self.cipher.decrypt(str(row["code_verifier_ciphertext"])),
            redirect_uri=str(row["redirect_uri"]),
            status=AuthorizationStatus(str(row["status"])),
            created_at=_datetime(str(row["created_at"])),
            expires_at=_datetime(str(row["expires_at"])),
            authorization_code_digest=(
                str(row["authorization_code_digest"])
                if row["authorization_code_digest"] is not None
                else None
            ),
        )

    @staticmethod
    def _load_binding_row(
        connection: PostgreSQLConnection,
        issuer: str,
        subject: str,
        *,
        for_update: bool = False,
    ):
        suffix = " FOR UPDATE" if for_update else ""
        return connection.execute(
            """
            SELECT b.*, e.display_name AS employee_display_name,
                   e.primary_email AS employee_primary_email,
                   e.status AS employee_status,
                   e.created_at AS employee_created_at,
                   e.updated_at AS employee_updated_at
            FROM oidc_identity_bindings b
            JOIN employees e ON e.employee_id = b.employee_id
            WHERE b.issuer = ? AND b.subject = ?
            """
            + suffix,
            (issuer, subject),
        ).fetchone()

    @staticmethod
    def _load_session_row(
        connection: PostgreSQLConnection,
        session_id: str,
        *,
        for_update: bool = False,
    ):
        suffix = " FOR UPDATE" if for_update else ""
        return connection.execute(
            """
            SELECT s.*,
                   b.issuer, b.subject, b.email_at_binding,
                   b.status AS binding_status, b.created_at AS binding_created_at,
                   b.disabled_at AS binding_disabled_at,
                   e.display_name AS employee_display_name,
                   e.primary_email AS employee_primary_email,
                   e.status AS employee_status,
                   e.created_at AS employee_created_at,
                   e.updated_at AS employee_updated_at
            FROM employee_sessions s
            JOIN oidc_identity_bindings b ON b.binding_id = s.binding_id
            JOIN employees e ON e.employee_id = s.employee_id
            WHERE s.session_id = ?
            """
            + suffix,
            (session_id,),
        ).fetchone()

    def _session_from_row(self, row) -> EmployeeSession:
        employee = EmployeeAccount(
            employee_id=str(row["employee_id"]),
            display_name=str(row["employee_display_name"]),
            primary_email=(
                str(row["employee_primary_email"])
                if row["employee_primary_email"] is not None
                else None
            ),
            status=EmployeeStatus(str(row["employee_status"])),
            created_at=_datetime(str(row["employee_created_at"])),
            updated_at=_datetime(str(row["employee_updated_at"])),
        )
        binding = IdentityBinding(
            binding_id=str(row["binding_id"]),
            issuer=str(row["issuer"]),
            subject=str(row["subject"]),
            employee=employee,
            email_at_binding=(
                str(row["email_at_binding"])
                if row["email_at_binding"] is not None
                else None
            ),
            status=IdentityBindingStatus(str(row["binding_status"])),
            created_at=_datetime(str(row["binding_created_at"])),
            disabled_at=(
                _datetime(str(row["binding_disabled_at"]))
                if row["binding_disabled_at"] is not None
                else None
            ),
        )
        return EmployeeSession(
            session_id=str(row["session_id"]),
            session_secret_digest=str(row["session_secret_digest"]),
            binding=binding,
            access_token=(
                self.cipher.decrypt(str(row["access_token_ciphertext"]))
                if row["access_token_ciphertext"] is not None
                else None
            ),
            refresh_token=(
                self.cipher.decrypt(str(row["refresh_token_ciphertext"]))
                if row["refresh_token_ciphertext"] is not None
                else None
            ),
            status=SessionStatus(str(row["status"])),
            token_version=int(row["token_version"]),
            created_at=_datetime(str(row["created_at"])),
            updated_at=_datetime(str(row["updated_at"])),
            access_token_expires_at=_datetime(str(row["access_token_expires_at"])),
            session_expires_at=_datetime(str(row["session_expires_at"])),
            revoked_at=(
                _datetime(str(row["revoked_at"]))
                if row["revoked_at"] is not None
                else None
            ),
            revocation_reason=(
                str(row["revocation_reason"])
                if row["revocation_reason"] is not None
                else None
            ),
        )

    @staticmethod
    def _insert_session_event(
        connection: PostgreSQLConnection,
        *,
        session_id: str,
        employee_id: str,
        event_type: str,
        actor_kind: ActorKind,
        actor_id: str,
        details: dict[str, object],
        occurred: str,
    ) -> None:
        sequence_row = connection.execute(
            """
            SELECT COALESCE(MAX(sequence_number), 0) + 1
            FROM employee_session_events WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()
        sequence_number = int(sequence_row[0])
        connection.execute(
            """
            INSERT INTO employee_session_events(
                event_id, session_id, sequence_number, employee_id, event_type,
                actor_kind, actor_id, details_json, occurred_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"SE-{uuid4().hex.upper()}",
                session_id,
                sequence_number,
                employee_id,
                event_type,
                actor_kind.value,
                actor_id,
                json.dumps(details, ensure_ascii=False, sort_keys=True),
                occurred,
            ),
        )

    def _revoke_locked_session(
        self,
        connection: PostgreSQLConnection,
        session_id: str,
        employee_id: str,
        *,
        reason: str,
        actor_kind: ActorKind,
        actor_id: str,
        occurred: str,
    ) -> None:
        connection.execute(
            """
            UPDATE employee_sessions
            SET status = 'REVOKED', updated_at = ?, revoked_at = ?,
                revoked_by = ?, revocation_reason = ?,
                access_token_ciphertext = NULL,
                refresh_token_ciphertext = NULL
            WHERE session_id = ? AND status = 'ACTIVE'
            """,
            (occurred, occurred, actor_id, reason, session_id),
        )
        self._insert_session_event(
            connection,
            session_id=session_id,
            employee_id=employee_id,
            event_type="REVOKED",
            actor_kind=actor_kind,
            actor_id=actor_id,
            details={"reason": reason},
            occurred=occurred,
        )

    def _require_identity_admin(self, actor: Actor) -> None:
        if actor.kind is not ActorKind.HUMAN or actor.actor_id not in self.identity_admins:
            raise IdentityPermissionDenied("只有获授权的真实员工可以管理身份绑定")


def _employee_from_row(row) -> EmployeeAccount:
    return EmployeeAccount(
        employee_id=str(row["employee_id"]),
        display_name=str(row["display_name"]),
        primary_email=(
            str(row["primary_email"]) if row["primary_email"] is not None else None
        ),
        status=EmployeeStatus(str(row["status"])),
        created_at=_datetime(str(row["created_at"])),
        updated_at=_datetime(str(row["updated_at"])),
    )


def _binding_from_row(row) -> IdentityBinding:
    employee = EmployeeAccount(
        employee_id=str(row["employee_id"]),
        display_name=str(row["employee_display_name"]),
        primary_email=(
            str(row["employee_primary_email"])
            if row["employee_primary_email"] is not None
            else None
        ),
        status=EmployeeStatus(str(row["employee_status"])),
        created_at=_datetime(str(row["employee_created_at"])),
        updated_at=_datetime(str(row["employee_updated_at"])),
    )
    return IdentityBinding(
        binding_id=str(row["binding_id"]),
        issuer=str(row["issuer"]),
        subject=str(row["subject"]),
        employee=employee,
        email_at_binding=(
            str(row["email_at_binding"])
            if row["email_at_binding"] is not None
            else None
        ),
        status=IdentityBindingStatus(str(row["status"])),
        created_at=_datetime(str(row["created_at"])),
        disabled_at=(
            _datetime(str(row["disabled_at"]))
            if row["disabled_at"] is not None
            else None
        ),
    )


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("时间必须包含时区")
    return value.astimezone(UTC).isoformat()


def _datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise IdentityError("数据库身份时间缺少时区")
    return parsed.astimezone(UTC)


__all__ = ["OIDC_IDENTITY_TABLES", "PostgreSQLIdentityRepository"]
