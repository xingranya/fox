"""PostgreSQL 正式状态事件重建和冲突差异快照。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Any

import psycopg
from psycopg.rows import dict_row

from .authorization import AuthorizationDecision
from .consistency import (
    CONFLICT_SCHEMA_VERSION,
    ConflictCode,
    ConflictEvent,
    ConflictReport,
    ConsistencyIntegrityError,
    FormalStateChange,
    FormalStateItem,
    StateChangeKind,
    StateSnapshotSummary,
)
from .domain import CommandContext
from .sqlite_base import canonical_json


class PostgreSQLConflictSnapshotRepository:
    """使用 REPEATABLE READ 一致快照生成冲突报告。"""

    def __init__(self, runtime_dsn: str) -> None:
        if not runtime_dsn.strip():
            raise ValueError("PostgreSQL 运行时 DSN 不能为空")
        self.runtime_dsn = runtime_dsn

    def capture_conflict(
        self,
        authorization: AuthorizationDecision,
        *,
        context: CommandContext,
        command_name: str,
        code: ConflictCode,
        reason: str,
        resource_type: str | None,
        resource_id: str | None,
        max_events: int,
    ) -> ConflictReport:
        """读取项目版本、事件与投影，并返回可重复计算的差异。"""

        if max_events <= 0:
            raise ValueError("max_events 必须大于 0")
        with psycopg.connect(
            self.runtime_dsn,
            autocommit=True,
            row_factory=dict_row,
        ) as connection:
            connection.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
            try:
                self._set_authorization_context(connection, authorization)
                project = connection.execute(
                    "SELECT version FROM projects WHERE project_id = %s",
                    (context.project_id,),
                ).fetchone()
                if project is None:
                    raise ConsistencyIntegrityError(
                        "授权快照无法读取项目；项目不存在或 RLS 上下文无效"
                    )
                current_version = int(project["version"])
                approval_events = connection.execute(
                    """
                    SELECT event_id, project_version, payload_json
                    FROM events
                    WHERE project_id = %s
                      AND event_type = 'PROPOSAL_APPROVED'
                      AND project_version <= %s
                    ORDER BY project_version
                    """,
                    (context.project_id, current_version),
                ).fetchall()
                current_items = self._rebuild_state(
                    approval_events,
                    through_version=current_version,
                )
                projection_items = self._read_projection(
                    connection,
                    context.project_id,
                )
                if current_items != projection_items:
                    raise ConsistencyIntegrityError(
                        "当前状态投影与批准事件重建结果不一致"
                    )
                baseline_available = context.expected_version <= current_version
                baseline_items = (
                    self._rebuild_state(
                        approval_events,
                        through_version=context.expected_version,
                    )
                    if baseline_available
                    else ()
                )
                event_rows = self._read_conflict_events(
                    connection,
                    project_id=context.project_id,
                    expected_version=context.expected_version,
                    current_version=current_version,
                    max_events=max_events,
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

        visible_rows = event_rows[:max_events]
        events_truncated = len(event_rows) > max_events
        next_event_version = (
            int(event_rows[max_events]["project_version"]) if events_truncated else None
        )
        resolved_reason = (
            f"预期版本 {context.expected_version} 已过期，当前版本为 {current_version}"
            if code is ConflictCode.VERSION_MISMATCH
            else reason
        )
        return ConflictReport(
            schema_version=CONFLICT_SCHEMA_VERSION,
            http_status=409,
            code=code,
            project_id=context.project_id,
            command_name=command_name,
            idempotency_key=context.idempotency_key,
            resource_type=resource_type,
            resource_id=resource_id,
            expected_version=context.expected_version,
            current_version=current_version,
            reason=resolved_reason,
            baseline=self._summary(
                context.expected_version,
                baseline_items,
                available=baseline_available,
            ),
            current=self._summary(current_version, current_items, available=True),
            state_changes=self._diff(baseline_items, current_items)
            if baseline_available
            else (),
            events=tuple(self._event(row) for row in visible_rows),
            events_truncated=events_truncated,
            next_event_version=next_event_version,
        )

    @staticmethod
    def _set_authorization_context(
        connection: psycopg.Connection,
        authorization: AuthorizationDecision,
    ) -> None:
        for key, value in (
            ("brand_os.principal_kind", authorization.principal.kind.value),
            ("brand_os.principal_id", authorization.principal.principal_id),
            ("brand_os.project_id", authorization.project_id),
            ("brand_os.action", authorization.action.value),
            (
                "brand_os.confidentiality_ceiling",
                authorization.confidentiality_ceiling.value,
            ),
        ):
            connection.execute("SELECT set_config(%s, %s, true)", (key, value))

    @classmethod
    def _rebuild_state(
        cls,
        rows: list[dict[str, Any]],
        *,
        through_version: int,
    ) -> tuple[FormalStateItem, ...]:
        state: dict[tuple[str, str], FormalStateItem] = {}
        for row in rows:
            if int(row["project_version"]) > through_version:
                break
            payload = cls._json_object(row["payload_json"], "事件 payload")
            removed = payload.get("removed_state_item")
            if removed is not None:
                removed_item = cls._state_item(removed)
                state.pop((removed_item.item_type, removed_item.item_id), None)
            item = payload.get("state_item")
            if item is not None:
                state_item = cls._state_item(item)
                state[(state_item.item_type, state_item.item_id)] = state_item
        return tuple(state[key] for key in sorted(state))

    @classmethod
    def _read_projection(
        cls,
        connection: psycopg.Connection,
        project_id: str,
    ) -> tuple[FormalStateItem, ...]:
        rows = connection.execute(
            """
            SELECT item_type, item_id, payload_json, source_proposal_id,
                   valid_from, valid_until
            FROM state_items
            WHERE project_id = %s
            ORDER BY item_type, item_id
            """,
            (project_id,),
        ).fetchall()
        return tuple(
            FormalStateItem(
                item_type=str(row["item_type"]),
                item_id=str(row["item_id"]),
                payload=cls._json_object(row["payload_json"], "投影 payload"),
                source_proposal_id=str(row["source_proposal_id"]),
                valid_from=(
                    str(row["valid_from"]) if row["valid_from"] is not None else None
                ),
                valid_until=(
                    str(row["valid_until"]) if row["valid_until"] is not None else None
                ),
            )
            for row in rows
        )

    @staticmethod
    def _read_conflict_events(
        connection: psycopg.Connection,
        *,
        project_id: str,
        expected_version: int,
        current_version: int,
        max_events: int,
    ) -> list[dict[str, Any]]:
        if expected_version >= current_version:
            return []
        return connection.execute(
            """
            SELECT project_version, event_id, event_type, aggregate_type,
                   aggregate_id, actor_kind, actor_id, committed_at
            FROM events
            WHERE project_id = %s
              AND project_version > %s
              AND project_version <= %s
            ORDER BY project_version
            LIMIT %s
            """,
            (project_id, expected_version, current_version, max_events + 1),
        ).fetchall()

    @staticmethod
    def _json_object(value: object, label: str) -> dict[str, object]:
        parsed = json.loads(value) if isinstance(value, str) else value
        if not isinstance(parsed, dict):
            raise ConsistencyIntegrityError(f"{label} 必须是 JSON 对象")
        return parsed

    @classmethod
    def _state_item(cls, value: object) -> FormalStateItem:
        if not isinstance(value, dict):
            raise ConsistencyIntegrityError("批准事件中的状态项必须是对象")
        payload = value.get("payload")
        if not isinstance(payload, dict):
            raise ConsistencyIntegrityError("批准事件中的状态 payload 必须是对象")
        required = ("item_type", "item_id", "source_proposal_id")
        if any(not isinstance(value.get(field), str) for field in required):
            raise ConsistencyIntegrityError("批准事件中的状态项缺少稳定标识")
        return FormalStateItem(
            item_type=str(value["item_type"]),
            item_id=str(value["item_id"]),
            payload=payload,
            source_proposal_id=str(value["source_proposal_id"]),
            valid_from=(
                str(value["valid_from"])
                if value.get("valid_from") is not None
                else None
            ),
            valid_until=(
                str(value["valid_until"])
                if value.get("valid_until") is not None
                else None
            ),
        )

    @staticmethod
    def _summary(
        version: int,
        items: tuple[FormalStateItem, ...],
        *,
        available: bool,
    ) -> StateSnapshotSummary:
        if not available:
            return StateSnapshotSummary(version, False, None, None)
        digest = hashlib.sha256(
            canonical_json([asdict(item) for item in items]).encode("utf-8")
        ).hexdigest()
        return StateSnapshotSummary(version, True, len(items), digest)

    @staticmethod
    def _diff(
        baseline: tuple[FormalStateItem, ...],
        current: tuple[FormalStateItem, ...],
    ) -> tuple[FormalStateChange, ...]:
        before = {(item.item_type, item.item_id): item for item in baseline}
        after = {(item.item_type, item.item_id): item for item in current}
        changes: list[FormalStateChange] = []
        for item_type, item_id in sorted(before.keys() | after.keys()):
            old = before.get((item_type, item_id))
            new = after.get((item_type, item_id))
            if old is None:
                kind = StateChangeKind.ADDED
            elif new is None:
                kind = StateChangeKind.REMOVED
            elif old != new:
                kind = StateChangeKind.MODIFIED
            else:
                continue
            changes.append(
                FormalStateChange(
                    kind=kind,
                    item_type=item_type,
                    item_id=item_id,
                    before=old,
                    after=new,
                )
            )
        return tuple(changes)

    @staticmethod
    def _event(row: dict[str, Any]) -> ConflictEvent:
        return ConflictEvent(
            project_version=int(row["project_version"]),
            event_id=str(row["event_id"]),
            event_type=str(row["event_type"]),
            aggregate_type=str(row["aggregate_type"]),
            aggregate_id=str(row["aggregate_id"]),
            actor_kind=str(row["actor_kind"]),
            actor_id=str(row["actor_id"]),
            committed_at=str(row["committed_at"]),
        )


__all__ = ["PostgreSQLConflictSnapshotRepository"]
