"""SQLite 权威库的读取与投影重建。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from .domain import ActorKind
from .sqlite_base import CanonicalStoreError, ProjectNotFound, SQLiteStoreBase


class SQLiteQueryMixin(SQLiteStoreBase):
    """集中实现不会创建新业务事件的读取与重建。"""

    def get_current_state(self, project_id: str) -> list[Mapping[str, object]]:
        """读取人工确认后的当前状态投影。"""

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT item_type, item_id, payload_json, source_proposal_id,
                       updated_event_id, state_version
                FROM state_items WHERE project_id = ? ORDER BY item_type, item_id
                """,
                (project_id,),
            ).fetchall()
        return [
            {
                "item_type": row["item_type"],
                "item_id": row["item_id"],
                "payload": json.loads(row["payload_json"]),
                "source_proposal_id": row["source_proposal_id"],
                "updated_event_id": row["updated_event_id"],
                "state_version": row["state_version"],
            }
            for row in rows
        ]

    def get_source(self, project_id: str, source_id: str) -> Mapping[str, object]:
        """读取原件版本元数据。"""

        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM sources WHERE project_id = ? AND source_id = ?",
                (project_id, source_id),
            ).fetchone()
        if row is None:
            raise ProjectNotFound(f"未找到来源 {source_id}")
        return dict(row)

    def get_source_import_report(self, project_id: str, batch_id: str) -> Mapping[str, object]:
        """返回单批导入差异和导入后的来源库存计数。"""

        with self._connect() as connection:
            batch = connection.execute(
                """
                SELECT * FROM source_import_batches
                WHERE project_id = ? AND batch_id = ?
                """,
                (project_id, batch_id),
            ).fetchone()
            if batch is None:
                raise ProjectNotFound(f"未找到来源导入批次 {batch_id}")
            inventory = {
                "logical_source_count": connection.execute(
                    "SELECT COUNT(*) FROM logical_sources WHERE project_id = ?", (project_id,)
                ).fetchone()[0],
                "content_count": connection.execute(
                    "SELECT COUNT(*) FROM source_contents WHERE project_id = ?", (project_id,)
                ).fetchone()[0],
                "source_version_count": connection.execute(
                    "SELECT COUNT(*) FROM source_versions WHERE project_id = ?", (project_id,)
                ).fetchone()[0],
                "current_version_count": connection.execute(
                    "SELECT COUNT(*) FROM source_versions WHERE project_id = ? AND is_current = 1",
                    (project_id,),
                ).fetchone()[0],
                "alias_count": connection.execute(
                    "SELECT COUNT(*) FROM source_aliases WHERE project_id = ?", (project_id,)
                ).fetchone()[0],
                "supersession_count": connection.execute(
                    "SELECT COUNT(*) FROM source_version_relations WHERE project_id = ?",
                    (project_id,),
                ).fetchone()[0],
                "gap_observation_count": connection.execute(
                    "SELECT COUNT(*) FROM source_gaps WHERE project_id = ?", (project_id,)
                ).fetchone()[0],
            }
            gaps = connection.execute(
                """
                SELECT gap_id, status, description, scope, evidence_ref, observed_at
                FROM source_gaps WHERE project_id = ? AND import_batch_id = ?
                ORDER BY gap_id
                """,
                (project_id, batch_id),
            ).fetchall()
        return {
            "batch": dict(batch),
            "inventory": inventory,
            "gaps": [dict(row) for row in gaps],
        }

    def list_source_versions(
        self, project_id: str, logical_source_id: str | None = None, *, current_only: bool = False
    ) -> list[Mapping[str, object]]:
        """按逻辑来源读取不可变内容版本。"""

        query = "SELECT * FROM source_versions WHERE project_id = ?"
        parameters: list[object] = [project_id]
        if logical_source_id is not None:
            query += " AND logical_source_id = ?"
            parameters.append(logical_source_id)
        if current_only:
            query += " AND is_current = 1"
        query += " ORDER BY logical_source_id, created_at, source_version_id"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [dict(row) for row in rows]

    def list_source_aliases(self, project_id: str) -> list[Mapping[str, object]]:
        """读取旧 ID、废弃保号和路径别名。"""

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM source_aliases WHERE project_id = ?
                ORDER BY alias_id
                """,
                (project_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_source_gaps(self, project_id: str) -> list[Mapping[str, object]]:
        """读取每个导入批次留下的资料缺口观察。"""

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM source_gaps WHERE project_id = ?
                ORDER BY observed_at, gap_id
                """,
                (project_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_candidates(self, project_id: str) -> list[Mapping[str, object]]:
        """读取仍处于 proposed 的分类候选。"""

        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM classification_candidates WHERE project_id = ? ORDER BY created_at, candidate_id",
                (project_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_relations(self, project_id: str) -> list[Mapping[str, object]]:
        """读取带证据的工作层关系。"""

        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM relations WHERE project_id = ? ORDER BY created_at, relation_id",
                (project_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_human_actions(self, project_id: str) -> list[Mapping[str, object]]:
        """读取人工评审审计记录。"""

        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM human_actions WHERE project_id = ? ORDER BY acted_at, action_id",
                (project_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_events(self, project_id: str) -> list[Mapping[str, object]]:
        """按全局位置读取项目事件。"""

        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM events WHERE project_id = ? ORDER BY global_position", (project_id,)
            ).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload_json"])} for row in rows]

    def list_proposals(self, project_id: str, status: str | None = None) -> list[Mapping[str, object]]:
        """读取 Proposal，可按状态过滤。"""

        query = "SELECT * FROM proposals WHERE project_id = ?"
        parameters: list[Any] = [project_id]
        if status is not None:
            query += " AND status = ?"
            parameters.append(status)
        query += " ORDER BY created_at, proposal_id"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [
            {
                **dict(row),
                "before": json.loads(row["before_json"]) if row["before_json"] else None,
                "after": json.loads(row["after_json"]),
            }
            for row in rows
        ]

    def rebuild_state_projection(self, project_id: str) -> int:
        """只根据人工批准事件重建当前状态投影。"""

        connection = self._connect()
        begun = False
        try:
            connection.execute("BEGIN IMMEDIATE")
            begun = True
            if connection.execute(
                "SELECT 1 FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone() is None:
                raise ProjectNotFound(project_id)
            connection.execute("DELETE FROM state_items WHERE project_id = ?", (project_id,))
            rows = connection.execute(
                """
                SELECT event_id, project_version, actor_kind, actor_id, payload_json
                FROM events
                WHERE project_id = ? AND event_type = 'PROPOSAL_APPROVED'
                ORDER BY global_position
                """,
                (project_id,),
            ).fetchall()
            rebuilt = 0
            for row in rows:
                if (
                    row["actor_kind"] != ActorKind.HUMAN.value
                    or row["actor_id"] not in self.allowed_reviewers
                ):
                    raise CanonicalStoreError("批准事件不是由已配置的人工评审人产生")
                payload = json.loads(row["payload_json"])
                state_item = payload.get("state_item")
                if not isinstance(state_item, dict):
                    raise CanonicalStoreError("批准事件缺少可重放的 state_item")
                self._apply_approval_projection(
                    connection, project_id, state_item, row["event_id"], row["project_version"]
                )
                rebuilt += 1
            connection.execute("COMMIT")
            return rebuilt
        except Exception:
            if begun:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()
