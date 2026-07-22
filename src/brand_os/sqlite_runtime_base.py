"""SQLite 运行时端口共享的项目、权限和幂等基础。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping

from .domain import Actor, ActorKind
from .sqlite_base import (
    BusinessPermissionDenied,
    ProjectNotFound,
    ResourceConflict,
    SQLiteStoreBase,
    canonical_json,
    utc_now,
)


class SQLiteRuntimeBaseMixin(SQLiteStoreBase):
    """提供派生运行数据共用的权限、项目读取和幂等处理。"""

    def _require_fox(self, actor: Actor, message: str) -> None:
        if (
            actor.kind is not ActorKind.HUMAN
            or actor.actor_id != "Fox"
            or actor.actor_id not in self.allowed_reviewers
        ):
            raise BusinessPermissionDenied(message)

    def _require_project_row(
        self, connection: sqlite3.Connection, project_id: str
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM projects WHERE project_id = ?", (project_id,)
        ).fetchone()
        if row is None:
            raise ProjectNotFound(project_id)
        return row

    def _runtime_request_hash(self, request: Mapping[str, object]) -> str:
        return hashlib.sha256(canonical_json(request).encode("utf-8")).hexdigest()

    def _runtime_replay(
        self,
        connection: sqlite3.Connection,
        project_id: str,
        actor: Actor,
        command_name: str,
        idempotency_key: str,
        request_hash: str,
    ) -> Mapping[str, object] | None:
        row = connection.execute(
            """
            SELECT request_hash, result_json FROM runtime_commands
            WHERE project_id = ? AND actor_kind = ? AND actor_id = ?
              AND command_name = ? AND idempotency_key = ?
            """,
            (
                project_id,
                actor.kind.value,
                actor.actor_id,
                command_name,
                idempotency_key,
            ),
        ).fetchone()
        if row is None:
            return None
        if row["request_hash"] != request_hash:
            raise ResourceConflict("同一运行时幂等键被用于不同请求")
        return json.loads(row["result_json"])

    def _record_runtime_command(
        self,
        connection: sqlite3.Connection,
        project_id: str,
        actor: Actor,
        command_name: str,
        idempotency_key: str,
        request_hash: str,
        result: Mapping[str, object],
    ) -> None:
        connection.execute(
            """
            INSERT INTO runtime_commands(
                project_id, actor_kind, actor_id, command_name, idempotency_key,
                request_hash, result_json, committed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                actor.kind.value,
                actor.actor_id,
                command_name,
                idempotency_key,
                request_hash,
                canonical_json(result),
                utc_now(),
            ),
        )
