"""SQLite 中的 Agent 运行起始留痕。"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import asdict

from .domain import Actor
from .sqlite_base import BusinessPermissionDenied, ProjectNotFound, ResourceConflict, canonical_json, utc_now
from .sqlite_runtime_base import SQLiteRuntimeBaseMixin
from .task_packets import ALLOWED_RUN_STARTER_KINDS, RUNTIME_RUN_SCHEMA_VERSION, AgentRunRequest


class SQLiteAgentRunMixin(SQLiteRuntimeBaseMixin):
    """把一次运行绑定到不可变 Task Packet，而不是聊天记忆。"""

    def record_agent_run(
        self, project_id: str, actor: Actor, request: AgentRunRequest
    ) -> Mapping[str, object]:
        """登记运行时、模型、Task Packet 和协议版本，不接受外部传入模式。"""

        if actor.kind not in ALLOWED_RUN_STARTER_KINDS:
            raise BusinessPermissionDenied("Agent 不能自行写入权威运行留痕")
        request_value = asdict(request)
        request_hash = self._runtime_request_hash(request_value)
        connection = self._connect()
        begun = False
        try:
            connection.execute("BEGIN IMMEDIATE")
            begun = True
            packet_row = connection.execute(
                "SELECT * FROM task_packets WHERE project_id = ? AND packet_id = ?",
                (project_id, request.packet_id),
            ).fetchone()
            if packet_row is None:
                raise ProjectNotFound(f"未找到 Task Packet {request.packet_id}")
            if packet_row["content_hash"] != request.expected_packet_hash:
                raise ResourceConflict("Task Packet 哈希不匹配")
            existing = connection.execute(
                """
                SELECT * FROM agent_runs
                WHERE project_id = ? AND runtime_id = ? AND idempotency_key = ?
                """,
                (project_id, request.runtime_id, request.idempotency_key),
            ).fetchone()
            if existing is not None:
                if existing["request_hash"] != request_hash:
                    raise ResourceConflict("同一运行幂等键被用于不同请求")
                connection.execute("COMMIT")
                return self._agent_run_value(existing)
            if connection.execute(
                "SELECT 1 FROM agent_runs WHERE run_id = ?", (request.run_id,)
            ).fetchone() is not None:
                raise ResourceConflict(f"run_id 已存在：{request.run_id}")
            packet = json.loads(packet_row["content_json"])
            now = utc_now()
            connection.execute(
                """
                INSERT INTO agent_runs(
                    run_id, schema_version, project_id, task_id, packet_id, packet_hash,
                    packet_version, task_revision, base_state_version, role, work_mode,
                    protocol_versions_json, runtime_id, runtime_version, model_id,
                    model_version, status, started_by_kind, started_by_id,
                    idempotency_key, request_hash, started_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'created',
                          ?, ?, ?, ?, ?)
                """,
                (
                    request.run_id,
                    RUNTIME_RUN_SCHEMA_VERSION,
                    project_id,
                    packet["task"]["task_id"],
                    request.packet_id,
                    request.expected_packet_hash,
                    packet["packet_version"],
                    packet["context_watermark"]["task_revision"],
                    packet["base_state_version"],
                    packet["task"]["role"],
                    packet["task"]["work_mode"],
                    canonical_json(packet["protocol_versions"]),
                    request.runtime_id,
                    request.runtime_version,
                    request.model_id,
                    request.model_version,
                    actor.kind.value,
                    actor.actor_id,
                    request.idempotency_key,
                    request_hash,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM agent_runs WHERE run_id = ?", (request.run_id,)
            ).fetchone()
            connection.execute("COMMIT")
            return self._agent_run_value(row)
        except Exception:
            if begun:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def get_agent_run(self, project_id: str, run_id: str) -> Mapping[str, object]:
        """读取一次运行实际使用的状态、模式、协议、运行时和模型版本。"""

        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_runs WHERE project_id = ? AND run_id = ?",
                (project_id, run_id),
            ).fetchone()
        if row is None:
            raise ProjectNotFound(f"未找到 Agent 运行 {run_id}")
        return self._agent_run_value(row)

    def _agent_run_value(self, row: sqlite3.Row) -> Mapping[str, object]:
        value = dict(row)
        value["protocol_versions"] = json.loads(value.pop("protocol_versions_json"))
        return value
