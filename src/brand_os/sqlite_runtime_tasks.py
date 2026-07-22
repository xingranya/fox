"""SQLite 中由 Fox 管理的运行任务和工作模式切换。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from uuid import uuid4

from .domain import Actor
from .sqlite_base import ProjectNotFound, ResourceConflict, VersionConflict, canonical_json, utc_now
from .sqlite_runtime_base import SQLiteRuntimeBaseMixin
from .task_packets import (
    RUNTIME_MODE_SWITCH_SCHEMA_VERSION,
    RuntimeTaskDefinition,
    WorkModeSwitch,
)


class SQLiteRuntimeTaskMixin(SQLiteRuntimeBaseMixin):
    """登记任务角色与模式，并保存 Fox 的每次模式切换。"""

    def register_runtime_task(
        self,
        project_id: str,
        actor: Actor,
        task: RuntimeTaskDefinition,
        *,
        idempotency_key: str,
    ) -> Mapping[str, object]:
        """由 Fox 登记任务角色、模式和最小上下文范围。"""

        self._require_fox(actor, "只有 Fox 可以登记运行任务的角色和模式")
        request = {"task": asdict(task)}
        request_hash = self._runtime_request_hash(request)
        connection = self._connect()
        begun = False
        try:
            connection.execute("BEGIN IMMEDIATE")
            begun = True
            replay = self._runtime_replay(
                connection,
                project_id,
                actor,
                "register_runtime_task",
                idempotency_key,
                request_hash,
            )
            if replay is not None:
                connection.execute("COMMIT")
                return replay
            self._require_project_row(connection, project_id)
            if connection.execute(
                "SELECT 1 FROM runtime_tasks WHERE project_id = ? AND task_id = ?",
                (project_id, task.task_id),
            ).fetchone() is not None:
                raise ResourceConflict(f"运行任务已存在：{task.task_id}")
            now = utc_now()
            spec_json = canonical_json(asdict(task))
            spec_hash = hashlib.sha256(spec_json.encode("utf-8")).hexdigest()
            connection.execute(
                """
                INSERT INTO runtime_tasks(
                    project_id, task_id, task_revision, role, work_mode, spec_json,
                    spec_hash, created_by, created_at, updated_by, updated_at
                ) VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    task.task_id,
                    task.role,
                    task.work_mode,
                    spec_json,
                    spec_hash,
                    actor.actor_id,
                    now,
                    actor.actor_id,
                    now,
                ),
            )
            result = {
                "project_id": project_id,
                "task_id": task.task_id,
                "task_revision": 1,
                "role": task.role,
                "work_mode": task.work_mode,
                "spec_hash": spec_hash,
                "registered_by": actor.actor_id,
                "registered_at": now,
            }
            self._record_runtime_command(
                connection,
                project_id,
                actor,
                "register_runtime_task",
                idempotency_key,
                request_hash,
                result,
            )
            connection.execute("COMMIT")
            return result
        except Exception:
            if begun:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def switch_work_mode(
        self,
        project_id: str,
        actor: Actor,
        switch: WorkModeSwitch,
        *,
        idempotency_key: str,
    ) -> Mapping[str, object]:
        """只接受 Fox 的显式切换，并保留旧任务版本。"""

        self._require_fox(actor, "AI 和未授权人员不能切换工作模式")
        request = {"switch": asdict(switch)}
        request_hash = self._runtime_request_hash(request)
        connection = self._connect()
        begun = False
        try:
            connection.execute("BEGIN IMMEDIATE")
            begun = True
            replay = self._runtime_replay(
                connection,
                project_id,
                actor,
                "switch_work_mode",
                idempotency_key,
                request_hash,
            )
            if replay is not None:
                connection.execute("COMMIT")
                return replay
            project = self._require_project_row(connection, project_id)
            row = connection.execute(
                "SELECT * FROM runtime_tasks WHERE project_id = ? AND task_id = ?",
                (project_id, switch.task_id),
            ).fetchone()
            if row is None:
                raise ProjectNotFound(f"未找到运行任务 {switch.task_id}")
            current_revision = int(row["task_revision"])
            if switch.expected_task_revision != current_revision:
                raise VersionConflict(switch.expected_task_revision, current_revision)
            if row["work_mode"] == switch.to_mode:
                raise ResourceConflict("工作模式没有变化")
            next_revision = current_revision + 1
            spec = json.loads(row["spec_json"])
            spec["work_mode"] = switch.to_mode
            spec_json = canonical_json(spec)
            spec_hash = hashlib.sha256(spec_json.encode("utf-8")).hexdigest()
            now = utc_now()
            event_id = str(uuid4())
            updated = connection.execute(
                """
                UPDATE runtime_tasks
                SET task_revision = ?, work_mode = ?, spec_json = ?, spec_hash = ?,
                    updated_by = ?, updated_at = ?
                WHERE project_id = ? AND task_id = ? AND task_revision = ?
                """,
                (
                    next_revision,
                    switch.to_mode,
                    spec_json,
                    spec_hash,
                    actor.actor_id,
                    now,
                    project_id,
                    switch.task_id,
                    current_revision,
                ),
            )
            if updated.rowcount != 1:
                raise ResourceConflict("工作模式切换时任务版本已变化")
            connection.execute(
                """
                INSERT INTO runtime_mode_switches(
                    event_id, schema_version, project_id, task_id, from_mode, to_mode,
                    initiated_by, initiator_type, reason, task_scope, base_state_version,
                    from_task_revision, to_task_revision, suggested_by_runtime, occurred_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'Fox', 'HUMAN',
                          ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    RUNTIME_MODE_SWITCH_SCHEMA_VERSION,
                    project_id,
                    switch.task_id,
                    row["work_mode"],
                    switch.to_mode,
                    switch.reason,
                    switch.task_scope,
                    int(project["version"]),
                    current_revision,
                    next_revision,
                    switch.suggested_by_runtime,
                    now,
                ),
            )
            result = {
                "schema_version": RUNTIME_MODE_SWITCH_SCHEMA_VERSION,
                "event_id": event_id,
                "project_id": project_id,
                "task_id": switch.task_id,
                "from_mode": row["work_mode"],
                "to_mode": switch.to_mode,
                "initiated_by": "Fox",
                "initiator_type": "HUMAN",
                "reason": switch.reason,
                "task_scope": switch.task_scope,
                "base_state_version": int(project["version"]),
                "from_task_revision": current_revision,
                "to_task_revision": next_revision,
                "suggested_by_runtime": switch.suggested_by_runtime,
                "occurred_at": now,
            }
            self._record_runtime_command(
                connection,
                project_id,
                actor,
                "switch_work_mode",
                idempotency_key,
                request_hash,
                result,
            )
            connection.execute("COMMIT")
            return result
        except Exception:
            if begun:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def list_runtime_mode_switches(
        self, project_id: str, task_id: str
    ) -> Sequence[Mapping[str, object]]:
        """按任务版本读取 Fox 的模式切换记录。"""

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM runtime_mode_switches
                WHERE project_id = ? AND task_id = ?
                ORDER BY to_task_revision
                """,
                (project_id, task_id),
            ).fetchall()
        return [dict(row) for row in rows]
