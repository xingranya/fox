"""SQLite 适配器共享的连接、事务、事件和幂等基础。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .domain import CommandContext, CommandResult
from .sqlite_migrations import MIGRATIONS, apply_migrations


MIN_SQLITE_VERSION = (3, 35, 0)
APPROVED_TYPE_MAP = {
    "DECISION_CANDIDATE": "DECISION",
    "CONSTRAINT_CANDIDATE": "CONSTRAINT",
    "ACTION_CANDIDATE": "ACTION",
}


class CanonicalStoreError(RuntimeError):
    """表示权威存储命令无法安全完成。"""


class ProjectNotFound(CanonicalStoreError):
    """表示项目不存在。"""


class ResourceConflict(CanonicalStoreError):
    """表示资源、幂等键或当前状态发生冲突。"""


class IdempotencyKeyConflict(ResourceConflict):
    """表示同一幂等键绑定了不同请求摘要。"""

    def __init__(self, stored_request_hash: str, received_request_hash: str) -> None:
        super().__init__("同一幂等键被用于不同请求")
        self.stored_request_hash = stored_request_hash
        self.received_request_hash = received_request_hash


class VersionConflict(ResourceConflict):
    """表示调用方的预期版本已经过期。"""

    def __init__(self, expected: int, current: int) -> None:
        super().__init__(f"预期版本 {expected} 已过期，当前版本为 {current}")
        self.expected = expected
        self.current = current


class BusinessPermissionDenied(CanonicalStoreError):
    """表示操作者没有改变正式业务状态的权限。"""


def canonical_json(value: object) -> str:
    """生成可重复计算摘要的 JSON。"""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def utc_now() -> str:
    """返回带时区的 UTC 时间。"""

    return datetime.now(UTC).isoformat()


class SQLiteStoreBase:
    """提供 SQLite 连接策略和单事件命令事务。"""

    def __init__(self, database_path: Path, allowed_reviewers: Sequence[str] = ("Fox",)) -> None:
        if sqlite3.sqlite_version_info < MIN_SQLITE_VERSION:
            raise CanonicalStoreError(
                f"SQLite 版本过低：{sqlite3.sqlite_version}，至少需要 {'.'.join(map(str, MIN_SQLITE_VERSION))}"
            )
        self.database_path = database_path.expanduser().resolve(strict=False)
        self.database_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.allowed_reviewers = frozenset(allowed_reviewers)
        with self._connect() as connection:
            apply_migrations(connection, MIGRATIONS)
        self.database_path.chmod(0o600)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    @property
    def schema_version(self) -> int:
        """返回当前已应用迁移版本。"""

        with self._connect() as connection:
            row = connection.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()
            return int(row[0])

    def _execute(
        self,
        context: CommandContext,
        command_name: str,
        request: Mapping[str, object],
        operation: Callable[[sqlite3.Connection, int], tuple[str, str]],
    ) -> CommandResult:
        """执行带幂等和乐观版本检查的单事件命令。"""

        request_hash = self._request_hash(request)
        connection = self._connect()
        begun = False
        try:
            self._begin_command_transaction(connection, context, command_name)
            begun = True
            existing = self._find_command(connection, context, command_name)
            if existing is not None:
                result = self._replay_command(existing, request_hash)
                connection.execute("COMMIT")
                return result
            project = connection.execute(
                "SELECT version FROM projects WHERE project_id = ?", (context.project_id,)
            ).fetchone()
            if project is None:
                raise ProjectNotFound(context.project_id)
            current_version = int(project["version"])
            if context.expected_version != current_version:
                raise VersionConflict(context.expected_version, current_version)
            next_version = current_version + 1
            event_id, resource_id = operation(connection, next_version)
            updated = connection.execute(
                "UPDATE projects SET version = ?, updated_at = ? WHERE project_id = ? AND version = ?",
                (next_version, utc_now(), context.project_id, current_version),
            )
            if updated.rowcount != 1:
                row = connection.execute(
                    "SELECT version FROM projects WHERE project_id = ?", (context.project_id,)
                ).fetchone()
                raise VersionConflict(context.expected_version, int(row["version"]))
            result = CommandResult(next_version, event_id, resource_id)
            self._record_command(connection, context, command_name, request_hash, result)
            connection.execute("COMMIT")
            return result
        except Exception:
            if begun:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def _begin_command_transaction(
        self,
        connection: sqlite3.Connection,
        context: CommandContext,
        command_name: str,
    ) -> None:
        """开始写事务；其他关系型适配器可在此增加命令级串行化。"""

        del context, command_name
        connection.execute("BEGIN IMMEDIATE")

    def _append_event(
        self,
        connection: sqlite3.Connection,
        context: CommandContext,
        project_version: int,
        aggregate_type: str,
        aggregate_id: str,
        event_type: str,
        payload: Mapping[str, object],
    ) -> str:
        """追加一个版本化领域事件。"""

        row = connection.execute(
            """
            SELECT COALESCE(MAX(aggregate_version), 0) + 1
            FROM events WHERE project_id = ? AND aggregate_type = ? AND aggregate_id = ?
            """,
            (context.project_id, aggregate_type, aggregate_id),
        ).fetchone()
        event_id = str(uuid4())
        connection.execute(
            """
            INSERT INTO events(
                event_id, project_id, project_version, aggregate_type, aggregate_id,
                aggregate_version, event_type, schema_version, actor_kind, actor_id,
                correlation_id, causation_id, payload_json, committed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
            """,
            (
                event_id,
                context.project_id,
                project_version,
                aggregate_type,
                aggregate_id,
                int(row[0]),
                event_type,
                "domain-event.v1",
                context.actor.kind.value,
                context.actor.actor_id,
                context.idempotency_key,
                canonical_json(payload),
                utc_now(),
            ),
        )
        return event_id

    def _apply_approval_projection(
        self,
        connection: sqlite3.Connection,
        project_id: str,
        state_item: Mapping[str, object],
        event_id: str,
        state_version: int,
    ) -> None:
        """在评审事件的同一事务内更新当前状态投影。"""

        connection.execute(
            """
            INSERT INTO state_items(
                project_id, item_type, item_id, payload_json, source_proposal_id,
                updated_event_id, state_version, valid_from, valid_until
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id, item_type, item_id) DO UPDATE SET
                payload_json = excluded.payload_json,
                source_proposal_id = excluded.source_proposal_id,
                updated_event_id = excluded.updated_event_id,
                state_version = excluded.state_version,
                valid_from = excluded.valid_from,
                valid_until = excluded.valid_until
            """,
            (
                project_id,
                state_item["item_type"],
                state_item["item_id"],
                canonical_json(state_item["payload"]),
                state_item["source_proposal_id"],
                event_id,
                state_version,
                state_item.get("valid_from"),
                state_item.get("valid_until"),
            ),
        )

    def _request_hash(self, request: Mapping[str, object]) -> str:
        return hashlib.sha256(canonical_json(request).encode("utf-8")).hexdigest()

    def _find_command(
        self, connection: sqlite3.Connection, context: CommandContext, command_name: str
    ) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT request_hash, result_json FROM commands
            WHERE project_id = ? AND actor_kind = ? AND actor_id = ?
              AND command_name = ? AND idempotency_key = ?
            """,
            (
                context.project_id,
                context.actor.kind.value,
                context.actor.actor_id,
                command_name,
                context.idempotency_key,
            ),
        ).fetchone()

    def _replay_command(self, row: sqlite3.Row, request_hash: str) -> CommandResult:
        if row["request_hash"] != request_hash:
            raise IdempotencyKeyConflict(str(row["request_hash"]), request_hash)
        data = json.loads(row["result_json"])
        return replace(CommandResult(**data), replayed=True)

    def _record_command(
        self,
        connection: sqlite3.Connection,
        context: CommandContext,
        command_name: str,
        request_hash: str,
        result: CommandResult,
    ) -> None:
        connection.execute(
            """
            INSERT INTO commands(
                project_id, actor_kind, actor_id, command_name, idempotency_key,
                request_hash, result_json, committed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                context.project_id,
                context.actor.kind.value,
                context.actor.actor_id,
                command_name,
                context.idempotency_key,
                request_hash,
                canonical_json(asdict(result)),
                utc_now(),
            ),
        )

    def get_project_version(self, project_id: str) -> int:
        """读取项目当前版本。"""

        with self._connect() as connection:
            row = connection.execute(
                "SELECT version FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone()
            if row is None:
                raise ProjectNotFound(project_id)
            return int(row["version"])

    def quick_check(self) -> bool:
        """执行 SQLite 快速完整性检查。"""

        with self._connect() as connection:
            return connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
