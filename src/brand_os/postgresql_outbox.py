"""PostgreSQL 审计、Outbox/Inbox 和后台投递边界。

本模块只负责派生任务的可靠投递，不在消费者侧推进正式业务状态。领域事件、
审计记录和 Outbox 消息由 ``_append_event`` 在同一事务中写入；消费者通过
租约领取消息，并用 Inbox 记录幂等结果。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

from .domain import CommandContext
from .sqlite_base import canonical_json, utc_now

if TYPE_CHECKING:
    from .postgresql_store import PostgreSQLConnection


AUDIT_OUTBOX_SCHEMA_VERSION = "audit-outbox.v1"
DEFAULT_OUTBOX_CONSUMER = "default"
MAX_ERROR_LENGTH = 2000


def _short_error(error: BaseException | str) -> str:
    """把异常压缩为可审计但不会无限增长的文本。"""

    value = str(error).strip()
    if not value and isinstance(error, BaseException):
        value = error.__class__.__name__
    if not value:
        value = "未知错误"
    return value[:MAX_ERROR_LENGTH]


def _parse_json(value: object, fallback: object = None) -> object:
    """解析数据库中的 JSON；损坏数据只返回可诊断的回退值。"""

    if not isinstance(value, str):
        return fallback if value is None else value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback if fallback is not None else value


class OutboxDeliveryConflict(RuntimeError):
    """消息不存在、租约不匹配或状态不允许当前投递。"""


class PostgreSQLOutboxMixin:
    """为 PostgreSQL 权威适配器提供可替换的后台投递实现。"""

    def _append_event(
        self,
        connection: PostgreSQLConnection,
        context: CommandContext,
        project_version: int,
        aggregate_type: str,
        aggregate_id: str,
        event_type: str,
        payload: Mapping[str, object],
    ) -> str:
        """追加事件，并在同一事务写入审计记录和 Outbox。"""

        event_id = super()._append_event(
            connection,
            context,
            project_version,
            aggregate_type,
            aggregate_id,
            event_type,
            payload,
        )
        event = connection.execute(
            """
            SELECT global_position, aggregate_version, schema_version, payload_json,
                   committed_at
            FROM events WHERE event_id = ?
            """,
            (event_id,),
        ).fetchone()
        if event is None:
            raise RuntimeError(f"事件 {event_id} 写入后无法读取")
        payload_json = str(event["payload_json"])
        payload_digest = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        details_json = canonical_json(
            {
                "schema_version": AUDIT_OUTBOX_SCHEMA_VERSION,
                "event_schema_version": event["schema_version"],
                "payload_fields": sorted(str(key) for key in payload),
            }
        )
        self._insert_audit_record(
            connection,
            project_id=context.project_id,
            event_id=event_id,
            audit_type="DOMAIN_EVENT",
            operation=event_type,
            outcome="COMMITTED",
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            project_version=project_version,
            aggregate_version=int(event["aggregate_version"]),
            actor_kind=context.actor.kind.value,
            actor_id=context.actor.actor_id,
            correlation_id=context.idempotency_key,
            causation_id=None,
            idempotency_key=context.idempotency_key,
            payload_digest=payload_digest,
            details_json=details_json,
        )
        self._enqueue_event_for_consumers(
            connection,
            event_id=event_id,
            event_global_position=int(event["global_position"]),
            project_id=context.project_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            aggregate_version=int(event["aggregate_version"]),
            event_type=event_type,
            schema_version=str(event["schema_version"]),
            payload_json=payload_json,
            created_at=str(event["committed_at"]),
        )
        return event_id

    def _insert_audit_record(
        self,
        connection: PostgreSQLConnection,
        *,
        project_id: str,
        event_id: str | None,
        audit_type: str,
        operation: str,
        outcome: str,
        aggregate_type: str | None,
        aggregate_id: str | None,
        event_type: str | None,
        project_version: int | None,
        aggregate_version: int | None,
        actor_kind: str,
        actor_id: str,
        correlation_id: str | None,
        causation_id: str | None,
        idempotency_key: str | None,
        payload_digest: str | None,
        details_json: str,
    ) -> str:
        """写入不可变审计行；调用方必须已经处于业务事务或独立任务事务。"""

        audit_id = f"AUD-{uuid4().hex}"
        connection.execute(
            """
            INSERT INTO audit_records(
                audit_id, project_id, event_id, audit_type, operation, outcome,
                aggregate_type, aggregate_id, event_type, project_version,
                aggregate_version, actor_kind, actor_id, correlation_id, causation_id,
                idempotency_key, payload_digest, details_json, occurred_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audit_id,
                project_id,
                event_id,
                audit_type,
                operation,
                outcome,
                aggregate_type,
                aggregate_id,
                event_type,
                project_version,
                aggregate_version,
                actor_kind,
                actor_id,
                correlation_id,
                causation_id,
                idempotency_key,
                payload_digest,
                details_json,
                utc_now(),
            ),
        )
        return audit_id

    def _enqueue_event_for_consumers(
        self,
        connection: PostgreSQLConnection,
        *,
        event_id: str,
        event_global_position: int,
        project_id: str,
        aggregate_type: str,
        aggregate_id: str,
        aggregate_version: int,
        event_type: str,
        schema_version: str,
        payload_json: str,
        created_at: str,
    ) -> int:
        """为所有活动消费者建立独立投递行，避免一个消费者确认后吞掉其他消费者。"""

        consumers = connection.execute(
            "SELECT consumer_name FROM outbox_consumers WHERE status = 'ACTIVE' ORDER BY consumer_name"
        ).fetchall()
        if not consumers:
            consumers = [{"consumer_name": DEFAULT_OUTBOX_CONSUMER}]
        inserted = 0
        for consumer in consumers:
            result = connection.execute(
                """
                INSERT INTO outbox_messages(
                    message_id, consumer_name, event_id, event_global_position, project_id,
                    aggregate_type, aggregate_id, aggregate_version, event_type,
                    schema_version, payload_json, status, attempts, available_at,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', 0, ?, ?, ?)
                ON CONFLICT(consumer_name, event_id) DO NOTHING
                """,
                (
                    f"MSG-{uuid4().hex}",
                    str(consumer["consumer_name"]),
                    event_id,
                    event_global_position,
                    project_id,
                    aggregate_type,
                    aggregate_id,
                    aggregate_version,
                    event_type,
                    schema_version,
                    payload_json,
                    created_at,
                    created_at,
                    created_at,
                ),
            )
            inserted += int(result.rowcount or 0)
        return inserted

    def register_outbox_consumer(
        self,
        consumer_name: str,
        *,
        replay_from_start: bool = True,
    ) -> Mapping[str, object]:
        """登记派生消费者，并可为已有事件补建缺失的投递行。"""

        if not consumer_name.strip():
            raise ValueError("consumer_name 不能为空")
        connection = self._connect()
        begun = False
        try:
            connection.execute("BEGIN")
            begun = True
            now = utc_now()
            connection.execute(
                """
                INSERT INTO outbox_consumers(consumer_name, status, created_at, updated_at)
                VALUES (?, 'ACTIVE', ?, ?)
                ON CONFLICT(consumer_name) DO UPDATE SET status = 'ACTIVE', updated_at = excluded.updated_at
                """,
                (consumer_name, now, now),
            )
            backfilled = 0
            if replay_from_start:
                events = connection.execute(
                    """
                    SELECT event_id, global_position, project_id, aggregate_type, aggregate_id,
                           aggregate_version, event_type, schema_version, payload_json, committed_at
                    FROM events ORDER BY global_position
                    """
                ).fetchall()
                for event in events:
                    result = connection.execute(
                        """
                        INSERT INTO outbox_messages(
                            message_id, consumer_name, event_id, event_global_position, project_id,
                            aggregate_type, aggregate_id, aggregate_version, event_type,
                            schema_version, payload_json, status, attempts, available_at,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', 0, ?, ?, ?)
                        ON CONFLICT(consumer_name, event_id) DO NOTHING
                        """,
                        (
                            f"MSG-{uuid4().hex}",
                            consumer_name,
                            str(event["event_id"]),
                            int(event["global_position"]),
                            str(event["project_id"]),
                            str(event["aggregate_type"]),
                            str(event["aggregate_id"]),
                            int(event["aggregate_version"]),
                            str(event["event_type"]),
                            str(event["schema_version"]),
                            str(event["payload_json"]),
                            str(event["committed_at"]),
                            str(event["committed_at"]),
                            str(event["committed_at"]),
                        ),
                    )
                    backfilled += int(result.rowcount or 0)
            connection.execute("COMMIT")
            return {"consumer_name": consumer_name, "backfilled": backfilled}
        except Exception:
            if begun:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def pause_outbox_consumer(self, consumer_name: str) -> None:
        """暂停新消息领取，不删除已有投递记录。"""

        self._set_consumer_status(consumer_name, "PAUSED")

    def retire_outbox_consumer(self, consumer_name: str) -> None:
        """永久停止消费者；历史消息仍保留用于审计和对账。"""

        self._set_consumer_status(consumer_name, "RETIRED")

    def _set_consumer_status(self, consumer_name: str, status: str) -> None:
        if status not in {"PAUSED", "RETIRED"}:
            raise ValueError("不支持的消费者状态")
        connection = self._connect()
        begun = False
        try:
            connection.execute("BEGIN")
            begun = True
            updated = connection.execute(
                "UPDATE outbox_consumers SET status = ?, updated_at = ? WHERE consumer_name = ?",
                (status, utc_now(), consumer_name),
            )
            if updated.rowcount != 1:
                raise OutboxDeliveryConflict(f"未找到消费者：{consumer_name}")
            connection.execute("COMMIT")
        except Exception:
            if begun:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def claim_outbox_messages(
        self,
        consumer_name: str,
        worker_id: str,
        *,
        limit: int = 10,
        lease_seconds: int = 60,
        project_id: str | None = None,
    ) -> list[Mapping[str, object]]:
        """以至少一次语义领取消息；同一聚合严格按事件版本推进。"""

        if not consumer_name.strip() or not worker_id.strip():
            raise ValueError("consumer_name 和 worker_id 不能为空")
        if limit <= 0 or lease_seconds <= 0:
            raise ValueError("limit 和 lease_seconds 必须大于 0")
        now = datetime.now(UTC)
        now_text = now.isoformat()
        lease_until = (now + timedelta(seconds=lease_seconds)).isoformat()
        lease_token = f"LEASE-{uuid4().hex}"
        connection = self._connect()
        begun = False
        try:
            connection.execute("BEGIN")
            begun = True
            consumer = connection.execute(
                "SELECT status FROM outbox_consumers WHERE consumer_name = ? FOR UPDATE",
                (consumer_name,),
            ).fetchone()
            if consumer is None:
                raise OutboxDeliveryConflict(f"未找到消费者：{consumer_name}")
            if str(consumer["status"]) != "ACTIVE":
                raise OutboxDeliveryConflict(f"消费者未处于 ACTIVE：{consumer_name}")
            project_clause = "" if project_id is None else " AND message.project_id = ?"
            parameters: list[object] = [consumer_name, now_text, now_text]
            if project_id is not None:
                parameters.append(project_id)
            parameters.append(limit)
            rows = connection.execute(
                f"""
                SELECT message.*
                FROM outbox_messages AS message
                WHERE message.consumer_name = ?
                  AND (
                      (message.status IN ('PENDING','RETRY') AND message.available_at <= ?)
                      OR (message.status = 'CLAIMED' AND message.lease_until IS NOT NULL
                          AND message.lease_until < ?)
                  )
                  {project_clause}
                  AND NOT EXISTS (
                      SELECT 1 FROM outbox_messages AS earlier
                      WHERE earlier.consumer_name = message.consumer_name
                        AND earlier.project_id = message.project_id
                        AND earlier.aggregate_type = message.aggregate_type
                        AND earlier.aggregate_id = message.aggregate_id
                        AND earlier.aggregate_version < message.aggregate_version
                        AND earlier.status <> 'ACKED'
                  )
                ORDER BY message.event_global_position
                FOR UPDATE OF message SKIP LOCKED
                LIMIT ?
                """,
                parameters,
            ).fetchall()
            connection.execute(
                """
                INSERT INTO background_worker_leases(
                    consumer_name, worker_id, lease_token, acquired_at, expires_at,
                    heartbeat_at, status
                ) VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE')
                ON CONFLICT(consumer_name, worker_id) DO UPDATE SET
                    lease_token = excluded.lease_token,
                    acquired_at = excluded.acquired_at,
                    expires_at = excluded.expires_at,
                    heartbeat_at = excluded.heartbeat_at,
                    status = 'ACTIVE'
                """,
                (consumer_name, worker_id, lease_token, now_text, lease_until, now_text),
            )
            claimed: list[Mapping[str, object]] = []
            for row in rows:
                updated = connection.execute(
                    """
                    UPDATE outbox_messages
                    SET status = 'CLAIMED', attempts = attempts + 1,
                        claimed_by = ?, lease_token = ?, lease_until = ?,
                        updated_at = ?, last_error = NULL
                    WHERE message_id = ?
                    RETURNING *
                    """,
                    (worker_id, lease_token, lease_until, now_text, row["message_id"]),
                ).fetchone()
                if updated is not None:
                    claimed.append(self._outbox_row(updated, lease_token=lease_token))
            connection.execute("COMMIT")
            return claimed
        except Exception:
            if begun:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def heartbeat_worker(
        self,
        consumer_name: str,
        worker_id: str,
        lease_token: str,
        *,
        lease_seconds: int = 60,
    ) -> bool:
        """延长后台 Worker 租约，不触碰任何正式状态表。"""

        if lease_seconds <= 0:
            raise ValueError("lease_seconds 必须大于 0")
        now = datetime.now(UTC)
        expires = (now + timedelta(seconds=lease_seconds)).isoformat()
        connection = self._connect()
        begun = False
        try:
            connection.execute("BEGIN")
            begun = True
            updated = connection.execute(
                """
                UPDATE background_worker_leases
                SET expires_at = ?, heartbeat_at = ?
                WHERE consumer_name = ? AND worker_id = ? AND lease_token = ? AND status = 'ACTIVE'
                """,
                (expires, now.isoformat(), consumer_name, worker_id, lease_token),
            )
            connection.execute("COMMIT")
            return bool(updated.rowcount)
        except Exception:
            if begun:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def ack_outbox_message(
        self,
        consumer_name: str,
        message_id: str,
        *,
        worker_id: str | None = None,
        lease_token: str | None = None,
        result: object = None,
    ) -> Mapping[str, object]:
        """确认一条消息；确认只改变派生投递状态。"""

        connection = self._connect()
        begun = False
        try:
            connection.execute("BEGIN")
            begun = True
            row = self._lock_claimed_message(
                connection,
                consumer_name,
                message_id,
                worker_id,
                lease_token,
                allow_acked=True,
            )
            if str(row["status"]) == "ACKED":
                connection.execute("COMMIT")
                return {"status": "REPLAYED", "message_id": message_id}
            now = utc_now()
            connection.execute(
                """
                UPDATE outbox_messages
                SET status = 'ACKED', acked_at = ?, updated_at = ?,
                    claimed_by = NULL, lease_token = NULL, lease_until = NULL,
                    last_error = NULL
                WHERE message_id = ?
                """,
                (now, now, message_id),
            )
            self._insert_inbox_processed(
                connection,
                row,
                result=result,
                attempts=int(row["attempts"]),
            )
            self._insert_delivery_audit(
                connection,
                row,
                worker_id=worker_id or str(row["claimed_by"] or "worker"),
                outcome="ACKNOWLEDGED",
                details={"result": result},
            )
            connection.execute("COMMIT")
            return {"status": "ACKNOWLEDGED", "message_id": message_id}
        except Exception:
            if begun:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def fail_outbox_message(
        self,
        consumer_name: str,
        message_id: str,
        *,
        error: BaseException | str,
        worker_id: str | None = None,
        lease_token: str | None = None,
        retryable: bool = True,
        max_attempts: int = 3,
        retry_delay_seconds: int = 5,
    ) -> Mapping[str, object]:
        """记录失败；瞬时错误重试，永久错误或超过次数进入死信。"""

        if max_attempts <= 0 or retry_delay_seconds < 0:
            raise ValueError("max_attempts 必须大于 0，retry_delay_seconds 不能小于 0")
        error_text = _short_error(error)
        connection = self._connect()
        begun = False
        try:
            connection.execute("BEGIN")
            begun = True
            row = self._lock_claimed_message(
                connection, consumer_name, message_id, worker_id, lease_token
            )
            attempts = int(row["attempts"])
            dead_letter = (not retryable) or attempts >= max_attempts
            now = datetime.now(UTC)
            if dead_letter:
                reason = "NON_RETRYABLE" if not retryable else "MAX_ATTEMPTS"
                connection.execute(
                    """
                    UPDATE outbox_messages
                    SET status = 'DEAD_LETTER', dead_letter_reason = ?, last_error = ?,
                        claimed_by = NULL, lease_token = NULL, lease_until = NULL,
                        updated_at = ?
                    WHERE message_id = ?
                    """,
                    (reason, error_text, now.isoformat(), message_id),
                )
                connection.execute(
                    """
                    INSERT INTO dead_letter_messages(
                        dead_letter_id, message_id, consumer_name, event_id, project_id,
                        reason, error_message, attempts, payload_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"DLQ-{uuid4().hex}",
                        message_id,
                        consumer_name,
                        row["event_id"],
                        row["project_id"],
                        reason,
                        error_text,
                        attempts,
                        row["payload_json"],
                        now.isoformat(),
                    ),
                )
                outcome = "FAILED"
            else:
                available = (now + timedelta(seconds=retry_delay_seconds)).isoformat()
                connection.execute(
                    """
                    UPDATE outbox_messages
                    SET status = 'RETRY', available_at = ?, last_error = ?,
                        claimed_by = NULL, lease_token = NULL, lease_until = NULL,
                        updated_at = ?
                    WHERE message_id = ?
                    """,
                    (available, error_text, now.isoformat(), message_id),
                )
                outcome = "RETRY"
            self._insert_inbox_failed(connection, row, error_text, attempts)
            self._insert_delivery_audit(
                connection,
                row,
                worker_id=worker_id or str(row["claimed_by"] or "worker"),
                outcome=outcome,
                details={"error": error_text, "retryable": retryable, "max_attempts": max_attempts},
            )
            connection.execute("COMMIT")
            return {
                "status": "DEAD_LETTER" if dead_letter else "RETRY",
                "message_id": message_id,
                "attempts": attempts,
            }
        except Exception:
            if begun:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def deliver_outbox_message(
        self,
        consumer_name: str,
        message: Mapping[str, object],
        handler: Callable[[Mapping[str, object]], object],
        *,
        worker_id: str,
        lease_token: str | None = None,
        max_attempts: int = 3,
    ) -> Mapping[str, object]:
        """执行一次派生处理，并把 Inbox 与 Outbox 结果原子落库。"""

        message_id = str(message["message_id"])
        token = lease_token or str(message.get("lease_token") or "")
        if not token:
            raise OutboxDeliveryConflict("消息缺少 lease_token")
        connection = self._connect()
        begun = False
        try:
            connection.execute("BEGIN")
            begun = True
            row = self._lock_claimed_message(
                connection, consumer_name, message_id, worker_id, token
            )
            inbox = connection.execute(
                """
                SELECT status FROM inbox_messages
                WHERE consumer_name = ? AND event_id = ?
                FOR UPDATE
                """,
                (consumer_name, row["event_id"]),
            ).fetchone()
            if inbox is not None and str(inbox["status"]) == "PROCESSED":
                now = utc_now()
                connection.execute(
                    """
                    UPDATE outbox_messages
                    SET status = 'ACKED', acked_at = ?, updated_at = ?,
                        claimed_by = NULL, lease_token = NULL, lease_until = NULL
                    WHERE message_id = ?
                    """,
                    (now, now, message_id),
                )
                connection.execute("COMMIT")
                return {"status": "REPLAYED", "message_id": message_id}
            connection.execute(
                """
                INSERT INTO inbox_messages(
                    consumer_name, event_id, message_id, project_id, status,
                    attempts, first_seen_at, updated_at
                ) VALUES (?, ?, ?, ?, 'IN_PROGRESS', 1, ?, ?)
                ON CONFLICT(consumer_name, event_id) DO UPDATE SET
                    message_id = excluded.message_id,
                    status = 'IN_PROGRESS',
                    attempts = inbox_messages.attempts + 1,
                    last_error = NULL,
                    updated_at = excluded.updated_at
                """,
                (
                    consumer_name,
                    row["event_id"],
                    message_id,
                    row["project_id"],
                    utc_now(),
                    utc_now(),
                ),
            )
            connection.execute("COMMIT")
            begun = False
        except Exception:
            if begun:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

        payload = self._outbox_row(row)["payload"]
        try:
            handler_result = handler(
                {
                    **self._outbox_row(row),
                    "payload": payload,
                }
            )
        except Exception as error:
            return self.fail_outbox_message(
                consumer_name,
                message_id,
                error=error,
                worker_id=worker_id,
                lease_token=token,
                retryable=True,
                max_attempts=max_attempts,
            )
        return self.ack_outbox_message(
            consumer_name,
            message_id,
            worker_id=worker_id,
            lease_token=token,
            result=handler_result,
        )

    def run_outbox_once(
        self,
        consumer_name: str,
        worker_id: str,
        handler: Callable[[Mapping[str, object]], object],
        *,
        limit: int = 10,
        lease_seconds: int = 60,
        max_attempts: int = 3,
    ) -> list[Mapping[str, object]]:
        """执行一轮非阻塞后台任务；正式命令无需等待此函数。"""

        messages = self.claim_outbox_messages(
            consumer_name,
            worker_id,
            limit=limit,
            lease_seconds=lease_seconds,
        )
        return [
            self.deliver_outbox_message(
                consumer_name,
                message,
                handler,
                worker_id=worker_id,
                lease_token=str(message["lease_token"]),
                max_attempts=max_attempts,
            )
            for message in messages
        ]

    def replay_dead_letter(
        self,
        consumer_name: str,
        *,
        dead_letter_id: str | None = None,
        worker_id: str = "replay",
    ) -> Mapping[str, object]:
        """将死信重新放回队列；保留原死信和重放审计，不覆盖正式状态。"""

        connection = self._connect()
        begun = False
        try:
            connection.execute("BEGIN")
            begun = True
            if dead_letter_id is None:
                dead = connection.execute(
                    """
                    SELECT * FROM dead_letter_messages
                    WHERE consumer_name = ? AND resolved_at IS NULL
                    ORDER BY created_at, dead_letter_id
                    LIMIT 1 FOR UPDATE
                    """,
                    (consumer_name,),
                ).fetchone()
            else:
                dead = connection.execute(
                    """
                    SELECT * FROM dead_letter_messages
                    WHERE consumer_name = ? AND dead_letter_id = ? AND resolved_at IS NULL
                    FOR UPDATE
                    """,
                    (consumer_name, dead_letter_id),
                ).fetchone()
            if dead is None:
                raise OutboxDeliveryConflict("没有可重放的死信")
            now = utc_now()
            connection.execute(
                """
                UPDATE outbox_messages
                SET status = 'RETRY', available_at = ?, dead_letter_reason = NULL,
                    last_error = NULL, claimed_by = NULL, lease_token = NULL,
                    lease_until = NULL, updated_at = ?
                WHERE message_id = ? AND status = 'DEAD_LETTER'
                """,
                (now, now, dead["message_id"]),
            )
            connection.execute(
                """
                UPDATE dead_letter_messages
                SET resolved_at = ?, resolved_by = ?, resolution = 'REQUEUED'
                WHERE dead_letter_id = ?
                """,
                (now, worker_id, dead["dead_letter_id"]),
            )
            self._insert_audit_record(
                connection,
                project_id=str(dead["project_id"]),
                event_id=str(dead["event_id"]),
                audit_type="REPLAY",
                operation="REPLAY_DEAD_LETTER",
                outcome="REPLAYED",
                aggregate_type=None,
                aggregate_id=None,
                event_type=None,
                project_version=None,
                aggregate_version=None,
                actor_kind="SYSTEM",
                actor_id=worker_id,
                correlation_id=str(dead["message_id"]),
                causation_id=str(dead["dead_letter_id"]),
                idempotency_key=None,
                payload_digest=None,
                details_json=canonical_json({"dead_letter_id": dead["dead_letter_id"]}),
            )
            connection.execute("COMMIT")
            return {"status": "REQUEUED", "dead_letter_id": dead["dead_letter_id"]}
        except Exception:
            if begun:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def replay_outbox_message(
        self,
        consumer_name: str,
        message_id: str,
        *,
        worker_id: str = "replay",
    ) -> Mapping[str, object]:
        """重新发布一条已确认或死信消息；Inbox 会阻止重复副作用。"""

        connection = self._connect()
        begun = False
        try:
            connection.execute("BEGIN")
            begun = True
            row = connection.execute(
                "SELECT * FROM outbox_messages WHERE consumer_name = ? AND message_id = ? FOR UPDATE",
                (consumer_name, message_id),
            ).fetchone()
            if row is None:
                raise OutboxDeliveryConflict(f"未找到消息：{message_id}")
            now = utc_now()
            connection.execute(
                """
                UPDATE outbox_messages
                SET status = 'RETRY', available_at = ?, dead_letter_reason = NULL,
                    last_error = NULL, claimed_by = NULL, lease_token = NULL,
                    lease_until = NULL, updated_at = ?, acked_at = NULL
                WHERE consumer_name = ? AND message_id = ?
                """,
                (now, now, consumer_name, message_id),
            )
            self._insert_audit_record(
                connection,
                project_id=str(row["project_id"]),
                event_id=str(row["event_id"]),
                audit_type="REPLAY",
                operation="REPLAY_OUTBOX_MESSAGE",
                outcome="REPLAYED",
                aggregate_type=str(row["aggregate_type"]),
                aggregate_id=str(row["aggregate_id"]),
                event_type=str(row["event_type"]),
                project_version=None,
                aggregate_version=int(row["aggregate_version"]),
                actor_kind="SYSTEM",
                actor_id=worker_id,
                correlation_id=message_id,
                causation_id=str(row["event_id"]),
                idempotency_key=None,
                payload_digest=hashlib.sha256(str(row["payload_json"]).encode()).hexdigest(),
                details_json=canonical_json({"message_id": message_id}),
            )
            connection.execute("COMMIT")
            return {"status": "REQUEUED", "message_id": message_id}
        except Exception:
            if begun:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def list_audit_records(
        self,
        project_id: str,
        *,
        event_id: str | None = None,
    ) -> list[Mapping[str, object]]:
        """读取项目审计，不返回事件正文。"""

        connection = self._connect()
        try:
            statement = "SELECT * FROM audit_records WHERE project_id = ?"
            parameters: list[object] = [project_id]
            if event_id is not None:
                statement += " AND event_id = ?"
                parameters.append(event_id)
            statement += " ORDER BY occurred_at, audit_id"
            return [dict(row) for row in connection.execute(statement, parameters).fetchall()]
        finally:
            connection.close()

    def list_outbox_messages(
        self,
        project_id: str,
        *,
        consumer_name: str | None = None,
        statuses: Sequence[str] | None = None,
    ) -> list[Mapping[str, object]]:
        """读取 Outbox 状态和投递租约。"""

        connection = self._connect()
        try:
            statement = "SELECT * FROM outbox_messages WHERE project_id = ?"
            parameters: list[object] = [project_id]
            if consumer_name is not None:
                statement += " AND consumer_name = ?"
                parameters.append(consumer_name)
            if statuses:
                placeholders = ",".join("?" for _ in statuses)
                statement += f" AND status IN ({placeholders})"
                parameters.extend(statuses)
            statement += " ORDER BY event_global_position, consumer_name"
            return [self._outbox_row(row) for row in connection.execute(statement, parameters).fetchall()]
        finally:
            connection.close()

    def list_inbox_messages(
        self,
        project_id: str,
        *,
        consumer_name: str | None = None,
    ) -> list[Mapping[str, object]]:
        """读取消费者去重记录。"""

        connection = self._connect()
        try:
            statement = "SELECT * FROM inbox_messages WHERE project_id = ?"
            parameters: list[object] = [project_id]
            if consumer_name is not None:
                statement += " AND consumer_name = ?"
                parameters.append(consumer_name)
            statement += " ORDER BY updated_at, event_id"
            rows = connection.execute(statement, parameters).fetchall()
            return [dict(row) for row in rows]
        finally:
            connection.close()

    def list_dead_letters(
        self,
        project_id: str,
        *,
        unresolved_only: bool = False,
    ) -> list[Mapping[str, object]]:
        """读取可审计死信。"""

        connection = self._connect()
        try:
            statement = "SELECT * FROM dead_letter_messages WHERE project_id = ?"
            parameters: list[object] = [project_id]
            if unresolved_only:
                statement += " AND resolved_at IS NULL"
            statement += " ORDER BY created_at, dead_letter_id"
            return [dict(row) for row in connection.execute(statement, parameters).fetchall()]
        finally:
            connection.close()

    def _lock_claimed_message(
        self,
        connection: PostgreSQLConnection,
        consumer_name: str,
        message_id: str,
        worker_id: str | None,
        lease_token: str | None,
        *,
        allow_acked: bool = False,
    ):
        row = connection.execute(
            "SELECT * FROM outbox_messages WHERE consumer_name = ? AND message_id = ? FOR UPDATE",
            (consumer_name, message_id),
        ).fetchone()
        if row is None:
            raise OutboxDeliveryConflict(f"未找到消息：{message_id}")
        status = str(row["status"])
        if status == "ACKED":
            if allow_acked:
                return row
            raise OutboxDeliveryConflict(f"消息 {message_id} 已经确认")
        if status != "CLAIMED":
            raise OutboxDeliveryConflict(f"消息 {message_id} 当前状态不允许确认：{status}")
        if worker_id is not None and str(row["claimed_by"]) != worker_id:
            raise OutboxDeliveryConflict(f"消息 {message_id} 不属于 Worker {worker_id}")
        if lease_token is not None and str(row["lease_token"]) != lease_token:
            raise OutboxDeliveryConflict(f"消息 {message_id} 的租约不匹配")
        return row

    def _outbox_row(self, row, *, lease_token: str | None = None) -> dict[str, object]:
        """把数据库行转换成跨适配器可序列化的消息。"""

        return {
            "message_id": str(row["message_id"]),
            "consumer_name": str(row["consumer_name"]),
            "event_id": str(row["event_id"]),
            "event_global_position": int(row["event_global_position"]),
            "project_id": str(row["project_id"]),
            "aggregate_type": str(row["aggregate_type"]),
            "aggregate_id": str(row["aggregate_id"]),
            "aggregate_version": int(row["aggregate_version"]),
            "event_type": str(row["event_type"]),
            "schema_version": str(row["schema_version"]),
            "payload": _parse_json(row["payload_json"], {}),
            "status": str(row["status"]),
            "attempts": int(row["attempts"]),
            "available_at": str(row["available_at"]),
            "claimed_by": row["claimed_by"],
            "lease_token": lease_token or row["lease_token"],
            "lease_until": row["lease_until"],
            "last_error": row["last_error"],
        }

    def _insert_inbox_processed(
        self,
        connection: PostgreSQLConnection,
        row,
        *,
        result: object,
        attempts: int,
    ) -> None:
        connection.execute(
            """
            INSERT INTO inbox_messages(
                consumer_name, event_id, message_id, project_id, status,
                attempts, result_json, first_seen_at, processed_at, updated_at
            ) VALUES (?, ?, ?, ?, 'PROCESSED', ?, ?, ?, ?, ?)
            ON CONFLICT(consumer_name, event_id) DO UPDATE SET
                message_id = excluded.message_id,
                status = 'PROCESSED', attempts = excluded.attempts,
                result_json = excluded.result_json, last_error = NULL,
                processed_at = excluded.processed_at, updated_at = excluded.updated_at
            """,
            (
                row["consumer_name"],
                row["event_id"],
                row["message_id"],
                row["project_id"],
                attempts,
                canonical_json(result),
                utc_now(),
                utc_now(),
                utc_now(),
            ),
        )

    def _insert_inbox_failed(
        self,
        connection: PostgreSQLConnection,
        row,
        error_text: str,
        attempts: int,
    ) -> None:
        connection.execute(
            """
            INSERT INTO inbox_messages(
                consumer_name, event_id, message_id, project_id, status,
                attempts, last_error, first_seen_at, updated_at
            ) VALUES (?, ?, ?, ?, 'FAILED', ?, ?, ?, ?)
            ON CONFLICT(consumer_name, event_id) DO UPDATE SET
                message_id = excluded.message_id,
                status = 'FAILED', attempts = excluded.attempts,
                last_error = excluded.last_error, updated_at = excluded.updated_at
            """,
            (
                row["consumer_name"],
                row["event_id"],
                row["message_id"],
                row["project_id"],
                attempts,
                error_text,
                utc_now(),
                utc_now(),
            ),
        )

    def _insert_delivery_audit(
        self,
        connection: PostgreSQLConnection,
        row,
        *,
        worker_id: str,
        outcome: str,
        details: Mapping[str, object],
    ) -> None:
        payload_digest = hashlib.sha256(str(row["payload_json"]).encode()).hexdigest()
        self._insert_audit_record(
            connection,
            project_id=str(row["project_id"]),
            event_id=str(row["event_id"]),
            audit_type="DEAD_LETTER" if outcome == "FAILED" else "DELIVERY",
            operation="OUTBOX_DELIVERY",
            outcome=outcome,
            aggregate_type=str(row["aggregate_type"]),
            aggregate_id=str(row["aggregate_id"]),
            event_type=str(row["event_type"]),
            project_version=None,
            aggregate_version=int(row["aggregate_version"]),
            actor_kind="SYSTEM",
            actor_id=worker_id,
            correlation_id=str(row["message_id"]),
            causation_id=str(row["event_id"]),
            idempotency_key=None,
            payload_digest=payload_digest,
            details_json=canonical_json(details),
        )


__all__ = [
    "AUDIT_OUTBOX_SCHEMA_VERSION",
    "DEFAULT_OUTBOX_CONSUMER",
    "OutboxDeliveryConflict",
    "PostgreSQLOutboxMixin",
]
