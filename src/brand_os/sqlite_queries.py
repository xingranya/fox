"""SQLite 权威库的读取与投影重建。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from .domain import ActorKind
from .sqlite_base import CanonicalStoreError, ProjectNotFound, SQLiteStoreBase


class SQLiteQueryMixin(SQLiteStoreBase):
    """集中实现不会创建新业务事件的读取与重建。"""

    def get_project(self, project_id: str) -> Mapping[str, object]:
        """读取项目身份、当前版本和更新时间。"""

        with self._connect() as connection:
            row = connection.execute(
                "SELECT project_id, name, version, created_at, updated_at FROM projects WHERE project_id = ?",
                (project_id,),
            ).fetchone()
        if row is None:
            raise ProjectNotFound(project_id)
        return dict(row)

    def get_current_state(self, project_id: str) -> list[Mapping[str, object]]:
        """读取人工确认后的当前状态投影。"""

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT item_type, item_id, payload_json, source_proposal_id,
                       updated_event_id, state_version, valid_from, valid_until
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
                "valid_from": row["valid_from"],
                "valid_until": row["valid_until"],
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

    def get_meeting_ingest_report(self, project_id: str, batch_id: str) -> Mapping[str, object]:
        """返回一次会议摄取的增量候选、冲突和工作层库存。"""

        with self._connect() as connection:
            batch = connection.execute(
                """
                SELECT * FROM meeting_ingest_batches
                WHERE project_id = ? AND batch_id = ?
                """,
                (project_id, batch_id),
            ).fetchone()
            if batch is None:
                raise ProjectNotFound(f"未找到会议摄取批次 {batch_id}")
            meeting = connection.execute(
                "SELECT * FROM meetings WHERE project_id = ? AND meeting_id = ?",
                (project_id, batch["meeting_id"]),
            ).fetchone()
            segments = connection.execute(
                """
                SELECT segment.* FROM meeting_segments AS segment
                JOIN meeting_batch_segments AS link
                  ON link.project_id = segment.project_id AND link.segment_id = segment.segment_id
                WHERE link.project_id = ? AND link.batch_id = ?
                ORDER BY segment.locator, segment.segment_id
                """,
                (project_id, batch_id),
            ).fetchall()
            items = connection.execute(
                """
                SELECT item.* FROM meeting_interpretation_items AS item
                JOIN meeting_batch_items AS link
                  ON link.project_id = item.project_id AND link.item_id = item.item_id
                WHERE link.project_id = ? AND link.batch_id = ?
                ORDER BY item.created_at, item.item_id
                """,
                (project_id, batch_id),
            ).fetchall()
            conflicts = connection.execute(
                """
                SELECT conflict.* FROM meeting_conflict_candidates AS conflict
                JOIN meeting_batch_conflicts AS link
                  ON link.project_id = conflict.project_id
                 AND link.conflict_id = conflict.conflict_id
                WHERE link.project_id = ? AND link.batch_id = ?
                ORDER BY conflict.created_at, conflict.conflict_id
                """,
                (project_id, batch_id),
            ).fetchall()
            evidence_by_item = {
                row["item_id"]: [] for row in items
            }
            for row in connection.execute(
                """
                SELECT evidence.item_id, evidence.segment_id
                FROM meeting_item_evidence AS evidence
                JOIN meeting_batch_items AS link
                  ON link.project_id = evidence.project_id AND link.item_id = evidence.item_id
                WHERE link.project_id = ? AND link.batch_id = ?
                ORDER BY evidence.item_id, evidence.segment_id
                """,
                (project_id, batch_id),
            ):
                evidence_by_item[row["item_id"]].append(row["segment_id"])
            evidence_by_conflict = {
                row["conflict_id"]: [] for row in conflicts
            }
            for row in connection.execute(
                """
                SELECT evidence.conflict_id, evidence.segment_id
                FROM meeting_conflict_evidence AS evidence
                JOIN meeting_batch_conflicts AS link
                  ON link.project_id = evidence.project_id
                 AND link.conflict_id = evidence.conflict_id
                WHERE link.project_id = ? AND link.batch_id = ?
                ORDER BY evidence.conflict_id, evidence.segment_id
                """,
                (project_id, batch_id),
            ):
                evidence_by_conflict[row["conflict_id"]].append(row["segment_id"])
            inventory = {
                "meeting_count": connection.execute(
                    "SELECT COUNT(*) FROM meetings WHERE project_id = ?", (project_id,)
                ).fetchone()[0],
                "segment_count": connection.execute(
                    "SELECT COUNT(*) FROM meeting_segments WHERE project_id = ?", (project_id,)
                ).fetchone()[0],
                "interpretation_item_count": connection.execute(
                    "SELECT COUNT(*) FROM meeting_interpretation_items WHERE project_id = ?",
                    (project_id,),
                ).fetchone()[0],
                "conflict_candidate_count": connection.execute(
                    "SELECT COUNT(*) FROM meeting_conflict_candidates WHERE project_id = ?",
                    (project_id,),
                ).fetchone()[0],
                "current_business_state_count": connection.execute(
                    "SELECT COUNT(*) FROM state_items WHERE project_id = ?", (project_id,)
                ).fetchone()[0],
            }
        meeting_data = dict(meeting)
        meeting_data["participants"] = json.loads(meeting_data.pop("participants_json"))
        item_data = []
        for row in items:
            value = dict(row)
            value["evidence_segment_ids"] = evidence_by_item[row["item_id"]]
            value["requires_human_confirmation"] = bool(value["requires_human_confirmation"])
            item_data.append(value)
        conflict_data = []
        for row in conflicts:
            value = dict(row)
            value["state_payload"] = json.loads(value.pop("state_payload_json"))
            value["evidence_segment_ids"] = evidence_by_conflict[row["conflict_id"]]
            conflict_data.append(value)
        return {
            "batch": dict(batch),
            "meeting": meeting_data,
            "segments": [dict(row) for row in segments],
            "items": item_data,
            "conflicts": conflict_data,
            "inventory": inventory,
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
        """读取 Proposal，可按生命周期状态过滤。"""

        query = """
            SELECT proposal.*, lifecycle.status AS lifecycle_status,
                   lifecycle.revision, lifecycle.last_event_id AS lifecycle_event_id,
                   lifecycle.updated_at AS lifecycle_updated_at,
                   meeting_link.item_id AS linked_meeting_item_id
            FROM proposals AS proposal
            JOIN proposal_lifecycle AS lifecycle
              ON lifecycle.project_id = proposal.project_id
             AND lifecycle.proposal_id = proposal.proposal_id
            LEFT JOIN meeting_item_proposals AS meeting_link
              ON meeting_link.project_id = proposal.project_id
             AND meeting_link.proposal_id = proposal.proposal_id
            WHERE proposal.project_id = ?
        """
        parameters: list[Any] = [project_id]
        if status is not None:
            query += " AND lifecycle.status = ?"
            parameters.append(status)
        query += " ORDER BY proposal.created_at, proposal.proposal_id"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
            evidence_by_proposal = {row["proposal_id"]: [] for row in rows}
            for row in connection.execute(
                """
                SELECT evidence.proposal_id, evidence.evidence_ref
                FROM proposal_evidence AS evidence
                JOIN proposals AS proposal ON proposal.proposal_id = evidence.proposal_id
                WHERE proposal.project_id = ?
                ORDER BY evidence.proposal_id, evidence.evidence_ref
                """,
                (project_id,),
            ):
                if row["proposal_id"] in evidence_by_proposal:
                    evidence_by_proposal[row["proposal_id"]].append(row["evidence_ref"])
        proposals = []
        for row in rows:
            value = dict(row)
            value["storage_review_status"] = value["status"]
            value["status"] = value.pop("lifecycle_status")
            value["before"] = json.loads(value.pop("before_json")) if row["before_json"] else None
            value["after"] = json.loads(value.pop("after_json"))
            value["evidence_refs"] = evidence_by_proposal[row["proposal_id"]]
            proposals.append(value)
        return proposals

    def get_proposal_history(self, project_id: str, proposal_id: str) -> Mapping[str, object]:
        """返回 Proposal 事件、人工动作、生命周期动作和替代关系。"""

        with self._connect() as connection:
            proposal = connection.execute(
                """
                SELECT proposal.proposal_id, lifecycle.status, lifecycle.revision
                FROM proposals AS proposal
                JOIN proposal_lifecycle AS lifecycle
                  ON lifecycle.project_id = proposal.project_id
                 AND lifecycle.proposal_id = proposal.proposal_id
                WHERE proposal.project_id = ? AND proposal.proposal_id = ?
                """,
                (project_id, proposal_id),
            ).fetchone()
            if proposal is None:
                raise ProjectNotFound(f"未找到 Proposal {proposal_id}")
            events = connection.execute(
                """
                SELECT * FROM events
                WHERE project_id = ? AND aggregate_type = 'proposal' AND aggregate_id = ?
                ORDER BY global_position
                """,
                (project_id, proposal_id),
            ).fetchall()
            human_actions = connection.execute(
                """
                SELECT * FROM human_actions
                WHERE project_id = ? AND proposal_id = ? ORDER BY acted_at, action_id
                """,
                (project_id, proposal_id),
            ).fetchall()
            lifecycle_actions = connection.execute(
                """
                SELECT * FROM proposal_lifecycle_actions
                WHERE project_id = ? AND proposal_id = ? ORDER BY acted_at, action_id
                """,
                (project_id, proposal_id),
            ).fetchall()
            supersessions = connection.execute(
                """
                SELECT * FROM proposal_supersessions
                WHERE project_id = ?
                  AND (predecessor_proposal_id = ? OR successor_proposal_id = ?)
                ORDER BY created_at
                """,
                (project_id, proposal_id, proposal_id),
            ).fetchall()
        return {
            "proposal": dict(proposal),
            "events": [
                {**dict(row), "payload": json.loads(row["payload_json"])} for row in events
            ],
            "human_actions": [dict(row) for row in human_actions],
            "lifecycle_actions": [
                {**dict(row), "evidence": json.loads(row["evidence_json"])}
                for row in lifecycle_actions
            ],
            "supersessions": [self._supersession_value(row) for row in supersessions],
        }

    def list_proposal_supersessions(self, project_id: str) -> list[Mapping[str, object]]:
        """读取正式 Proposal 替代链及替代前后的状态快照。"""

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM proposal_supersessions
                WHERE project_id = ? ORDER BY created_at, predecessor_proposal_id
                """,
                (project_id,),
            ).fetchall()
        return [self._supersession_value(row) for row in rows]

    def _supersession_value(self, row) -> Mapping[str, object]:
        value = dict(row)
        value["predecessor_payload"] = json.loads(value.pop("predecessor_payload_json"))
        value["successor_payload"] = json.loads(value.pop("successor_payload_json"))
        return value

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
                removed_state_item = payload.get("removed_state_item")
                if removed_state_item is not None:
                    if not isinstance(removed_state_item, dict):
                        raise CanonicalStoreError("批准事件的 removed_state_item 无效")
                    connection.execute(
                        """
                        DELETE FROM state_items
                        WHERE project_id = ? AND item_type = ? AND item_id = ?
                        """,
                        (
                            project_id,
                            removed_state_item["item_type"],
                            removed_state_item["item_id"],
                        ),
                    )
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

    def rebuild_proposal_lifecycle(self, project_id: str) -> int:
        """根据人工 Proposal 事件重建生命周期和正式替代关系。"""

        connection = self._connect()
        begun = False
        try:
            connection.execute("BEGIN IMMEDIATE")
            begun = True
            if connection.execute(
                "SELECT 1 FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone() is None:
                raise ProjectNotFound(project_id)
            connection.execute(
                "DELETE FROM proposal_supersessions WHERE project_id = ?", (project_id,)
            )
            connection.execute(
                "DELETE FROM proposal_lifecycle_actions WHERE project_id = ?", (project_id,)
            )
            connection.execute(
                "DELETE FROM proposal_lifecycle WHERE project_id = ?", (project_id,)
            )
            connection.execute(
                """
                INSERT INTO proposal_lifecycle(
                    project_id, proposal_id, status, revision, last_event_id, updated_at
                )
                SELECT project_id, proposal_id, 'proposed', 0, created_event_id, created_at
                FROM proposals WHERE project_id = ?
                """,
                (project_id,),
            )
            rows = connection.execute(
                """
                SELECT event_id, project_version, event_type, aggregate_id,
                       actor_kind, actor_id, payload_json, committed_at
                FROM events
                WHERE project_id = ? AND aggregate_type = 'proposal'
                  AND event_type IN (
                      'PROPOSAL_APPROVED','PROPOSAL_REJECTED','PROPOSAL_REOPENED'
                  )
                ORDER BY global_position
                """,
                (project_id,),
            ).fetchall()
            replayed = 0
            for row in rows:
                if (
                    row["actor_kind"] != ActorKind.HUMAN.value
                    or row["actor_id"] not in self.allowed_reviewers
                ):
                    raise CanonicalStoreError("Proposal 生命周期事件不是由已配置人工评审人产生")
                payload = json.loads(row["payload_json"])
                if row["event_type"] == "PROPOSAL_REOPENED":
                    evidence_refs = payload.get("evidence_refs")
                    if not isinstance(evidence_refs, list) or not evidence_refs:
                        raise CanonicalStoreError("重开事件缺少新证据")
                    updated = connection.execute(
                        """
                        UPDATE proposal_lifecycle
                        SET status = 'proposed', revision = revision + 1,
                            last_event_id = ?, updated_at = ?
                        WHERE project_id = ? AND proposal_id = ? AND status = 'rejected'
                        """,
                        (row["event_id"], row["committed_at"], project_id, row["aggregate_id"]),
                    )
                    if updated.rowcount != 1:
                        raise CanonicalStoreError("重开事件不符合 Proposal 生命周期")
                    self._replay_lifecycle_action(
                        connection,
                        project_id=project_id,
                        proposal_id=row["aggregate_id"],
                        action="reopen",
                        actor_id=row["actor_id"],
                        reason=payload.get("reason"),
                        before_status="rejected",
                        after_status="proposed",
                        evidence_refs=evidence_refs,
                        base_state_version=payload.get("new_base_state_version"),
                        event_id=row["event_id"],
                        committed_at=row["committed_at"],
                    )
                else:
                    status = "approved" if row["event_type"] == "PROPOSAL_APPROVED" else "rejected"
                    updated = connection.execute(
                        """
                        UPDATE proposal_lifecycle
                        SET status = ?, last_event_id = ?, updated_at = ?
                        WHERE project_id = ? AND proposal_id = ? AND status = 'proposed'
                        """,
                        (status, row["event_id"], row["committed_at"], project_id, row["aggregate_id"]),
                    )
                    if updated.rowcount != 1:
                        raise CanonicalStoreError("评审事件不符合 Proposal 生命周期")
                    supersession = payload.get("supersession")
                    if supersession is not None:
                        if not isinstance(supersession, dict):
                            raise CanonicalStoreError("替代事件的前后状态快照无效")
                        if supersession.get("successor_proposal_id") != row["aggregate_id"]:
                            raise CanonicalStoreError("替代事件的后继 Proposal 不一致")
                        self._replay_supersession(
                            connection,
                            project_id,
                            supersession,
                            row["actor_id"],
                            payload.get("evidence_refs"),
                            row["project_version"] - 1,
                            row["event_id"],
                            row["committed_at"],
                        )
                replayed += 1
            connection.execute("COMMIT")
            return replayed
        except Exception:
            if begun:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def _replay_supersession(
        self,
        connection,
        project_id: str,
        supersession: Mapping[str, object],
        actor_id: str,
        evidence_refs: object,
        base_state_version: int,
        event_id: str,
        committed_at: str,
    ) -> None:
        predecessor = supersession.get("predecessor_state_item")
        successor = supersession.get("successor_state_item")
        if not isinstance(predecessor, dict) or not isinstance(successor, dict):
            raise CanonicalStoreError("替代事件缺少前后状态快照")
        predecessor_proposal_id = supersession.get("predecessor_proposal_id")
        successor_proposal_id = supersession.get("successor_proposal_id")
        if not isinstance(predecessor_proposal_id, str) or not isinstance(
            successor_proposal_id, str
        ):
            raise CanonicalStoreError("替代事件缺少 Proposal ID")
        updated = connection.execute(
            """
            UPDATE proposal_lifecycle SET status = 'superseded',
                last_event_id = ?, updated_at = ?
            WHERE project_id = ? AND proposal_id = ? AND status = 'approved'
            """,
            (event_id, committed_at, project_id, predecessor_proposal_id),
        )
        if updated.rowcount != 1:
            raise CanonicalStoreError("被替代 Proposal 不处于已批准状态")
        connection.execute(
            """
            INSERT INTO proposal_supersessions(
                project_id, predecessor_proposal_id, successor_proposal_id,
                predecessor_item_type, predecessor_item_id,
                successor_item_type, successor_item_id,
                predecessor_payload_json, successor_payload_json,
                approved_event_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                predecessor_proposal_id,
                successor_proposal_id,
                predecessor["item_type"],
                predecessor["item_id"],
                successor["item_type"],
                successor["item_id"],
                json.dumps(
                    predecessor["payload"],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                json.dumps(
                    successor["payload"],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                event_id,
                committed_at,
            ),
        )
        self._replay_lifecycle_action(
            connection,
            project_id=project_id,
            proposal_id=predecessor_proposal_id,
            action="supersede",
            actor_id=actor_id,
            reason=f"由 Proposal {successor_proposal_id} 替代",
            before_status="approved",
            after_status="superseded",
            evidence_refs=evidence_refs,
            base_state_version=base_state_version,
            event_id=event_id,
            committed_at=committed_at,
        )

    def _replay_lifecycle_action(
        self,
        connection,
        *,
        project_id: str,
        proposal_id: str,
        action: str,
        actor_id: str,
        reason: object,
        before_status: str,
        after_status: str,
        evidence_refs: object,
        base_state_version: object,
        event_id: str,
        committed_at: str,
    ) -> None:
        if not isinstance(reason, str) or not reason.strip():
            raise CanonicalStoreError("Proposal 生命周期事件缺少原因")
        if not isinstance(evidence_refs, list) or any(
            not isinstance(item, str) or not item.strip() for item in evidence_refs
        ):
            raise CanonicalStoreError("Proposal 生命周期事件证据无效")
        if isinstance(base_state_version, bool) or not isinstance(base_state_version, int):
            raise CanonicalStoreError("Proposal 生命周期事件版本无效")
        connection.execute(
            """
            INSERT INTO proposal_lifecycle_actions(
                action_id, project_id, proposal_id, action, actor_id, reason,
                before_status, after_status, evidence_json,
                base_state_version, event_id, acted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"{event_id}:{action}:{proposal_id}",
                project_id,
                proposal_id,
                action,
                actor_id,
                reason,
                before_status,
                after_status,
                json.dumps(
                    evidence_refs,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                base_state_version,
                event_id,
                committed_at,
            ),
        )
