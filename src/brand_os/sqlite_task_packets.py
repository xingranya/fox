"""SQLite 中不可变 Task Packet 的生成、分层读取和校验。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from .domain import Actor
from .sqlite_base import (
    BusinessPermissionDenied,
    ProjectNotFound,
    ResourceConflict,
    VersionConflict,
    canonical_json,
    utc_now,
)
from .sqlite_task_packet_assembly import SQLiteTaskPacketAssemblyMixin
from .task_packets import (
    ALLOWED_RUN_STARTER_KINDS,
    TASK_PACKET_ASSEMBLY_VERSION,
    TASK_PACKET_SCHEMA_VERSION,
)


class SQLiteTaskPacketMixin(SQLiteTaskPacketAssemblyMixin):
    """从受控任务定义生成可复核、可分层读取的上下文快照。"""

    def build_task_packet(
        self,
        project_id: str,
        task_id: str,
        actor: Actor,
        *,
        expected_state_version: int | None = None,
    ) -> Mapping[str, object]:
        """从受控任务定义和当前状态生成不可变 Task Packet。"""

        if actor.kind not in ALLOWED_RUN_STARTER_KINDS:
            raise BusinessPermissionDenied("AI 不能自行生成或改写 Task Packet")
        with self._connect() as connection:
            project = self._require_project_row(connection, project_id)
            task_row = connection.execute(
                "SELECT * FROM runtime_tasks WHERE project_id = ? AND task_id = ?",
                (project_id, task_id),
            ).fetchone()
        if task_row is None:
            raise ProjectNotFound(f"未找到运行任务 {task_id}")
        base_state_version = int(project["version"])
        if expected_state_version is not None and expected_state_version != base_state_version:
            raise VersionConflict(expected_state_version, base_state_version)
        generated_at = utc_now()
        seed = self._assemble_packet_seed(
            project_id,
            dict(task_row),
            base_state_version=base_state_version,
            generated_at=generated_at,
        )
        fingerprint = hashlib.sha256(canonical_json(seed).encode("utf-8")).hexdigest()

        connection = self._connect()
        begun = False
        try:
            connection.execute("BEGIN IMMEDIATE")
            begun = True
            current_project = self._require_project_row(connection, project_id)
            current_task = connection.execute(
                "SELECT * FROM runtime_tasks WHERE project_id = ? AND task_id = ?",
                (project_id, task_id),
            ).fetchone()
            if int(current_project["version"]) != base_state_version:
                raise VersionConflict(base_state_version, int(current_project["version"]))
            if current_task is None:
                raise ProjectNotFound(f"未找到运行任务 {task_id}")
            if (
                int(current_task["task_revision"]) != int(task_row["task_revision"])
                or current_task["spec_hash"] != task_row["spec_hash"]
            ):
                raise ResourceConflict("装配期间任务角色或模式已经变化")
            existing = connection.execute(
                """
                SELECT content_json FROM task_packets
                WHERE project_id = ? AND task_id = ? AND fingerprint = ?
                """,
                (project_id, task_id, fingerprint),
            ).fetchone()
            if existing is not None:
                connection.execute("COMMIT")
                return json.loads(existing["content_json"])
            packet_version = int(
                connection.execute(
                    """
                    SELECT COALESCE(MAX(packet_version), 0) + 1
                    FROM task_packets WHERE project_id = ? AND task_id = ?
                    """,
                    (project_id, task_id),
                ).fetchone()[0]
            )
            packet_id = f"TP-{fingerprint[:24].upper()}"
            packet_without_hash = {
                **seed,
                "packet_id": packet_id,
                "packet_version": packet_version,
                "generated_at": generated_at,
            }
            content_hash = hashlib.sha256(
                canonical_json(packet_without_hash).encode("utf-8")
            ).hexdigest()
            packet = {**packet_without_hash, "content_hash": content_hash}
            connection.execute(
                """
                INSERT INTO task_packets(
                    packet_id, project_id, task_id, packet_version, task_revision,
                    base_state_version, schema_version, assembly_policy_version,
                    fingerprint, content_hash, content_json, generated_by_kind,
                    generated_by_id, generated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    packet_id,
                    project_id,
                    task_id,
                    packet_version,
                    int(task_row["task_revision"]),
                    base_state_version,
                    TASK_PACKET_SCHEMA_VERSION,
                    TASK_PACKET_ASSEMBLY_VERSION,
                    fingerprint,
                    content_hash,
                    canonical_json(packet),
                    actor.kind.value,
                    actor.actor_id,
                    generated_at,
                ),
            )
            connection.execute("COMMIT")
            return packet
        except Exception:
            if begun:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def get_task_packet(self, project_id: str, packet_id: str) -> Mapping[str, object]:
        """读取已生成的不可变 Task Packet。"""

        with self._connect() as connection:
            row = connection.execute(
                "SELECT content_json FROM task_packets WHERE project_id = ? AND packet_id = ?",
                (project_id, packet_id),
            ).fetchone()
        if row is None:
            raise ProjectNotFound(f"未找到 Task Packet {packet_id}")
        return json.loads(row["content_json"])

    def list_task_packets(self, project_id: str) -> list[Mapping[str, object]]:
        """读取 Packet 元数据，不在列表接口重复返回完整上下文。"""

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT packet_id, project_id, task_id, packet_version, task_revision,
                       base_state_version, schema_version, assembly_policy_version,
                       content_hash, generated_by_kind, generated_by_id, generated_at
                FROM task_packets
                WHERE project_id = ?
                ORDER BY generated_at DESC, task_id, packet_version DESC
                """,
                (project_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_task_packet_layer(
        self, project_id: str, packet_id: str, layer: str
    ) -> Mapping[str, object]:
        """按 L0-L4 返回任务头、当前状态、证据或按需层说明。"""

        packet = self.get_task_packet(project_id, packet_id)
        normalized = layer.upper()
        if normalized == "L0":
            return {
                "layer": "L0",
                "task": packet["task"],
                "role_contract": packet["role_contract"],
                "mode_contract": packet["mode_contract"],
                "runtime_policy": packet["runtime_policy"],
                "output_contract": packet["output_contract"],
                "protocol_versions": packet["protocol_versions"],
                "vetoes": packet["vetoes"],
            }
        if normalized == "L1":
            return {
                "layer": "L1",
                "base_state_version": packet["base_state_version"],
                "approved_state": packet["approved_state"],
                "working_state": packet["working_state"],
                "known_gaps": packet["known_gaps"],
                "conflicts": packet["conflicts"],
                "context_watermark": packet["context_watermark"],
            }
        if normalized == "L2":
            return {"layer": "L2", "relevant_evidence": packet["relevant_evidence"]}
        if normalized == "L3":
            return {
                "layer": "L3",
                "loaded": False,
                "open_refs": [
                    value.get("open_ref")
                    for value in packet["relevant_evidence"]
                    if value.get("open_ref")
                ],
            }
        if normalized == "L4":
            return {
                "layer": "L4",
                "loaded": False,
                "reason": "历史与废案仅在复盘、排重、冲突或风险检查时加载",
            }
        raise ValueError("layer 必须是 L0-L4")

    def validate_task_packet(self, project_id: str, packet_id: str) -> Mapping[str, object]:
        """复算 Task Packet 哈希并检查不可越权字段。"""

        packet = dict(self.get_task_packet(project_id, packet_id))
        stored_hash = packet.pop("content_hash", None)
        calculated_hash = hashlib.sha256(canonical_json(packet).encode("utf-8")).hexdigest()
        errors: list[str] = []
        if stored_hash != calculated_hash:
            errors.append("content_hash 不匹配")
        if packet.get("schema_version") != TASK_PACKET_SCHEMA_VERSION:
            errors.append("Task Packet Schema 版本不匹配")
        runtime_policy = packet.get("runtime_policy")
        if not isinstance(runtime_policy, dict) or runtime_policy.get("mode_switch_authority") != "Fox":
            errors.append("工作模式切换权限不是 Fox")
        output_contract = packet.get("output_contract")
        if not isinstance(output_contract, dict) or output_contract.get("proposal_only") is not True:
            errors.append("输出契约未限制为 Proposal")
        return {
            "packet_id": packet_id,
            "valid": not errors,
            "content_hash": stored_hash,
            "calculated_hash": calculated_hash,
            "errors": errors,
        }
