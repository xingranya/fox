"""SQLite Proposal 生命周期、人工评审、重开和替代实现。"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import asdict
from uuid import uuid4

from .domain import (
    ActorKind,
    CommandContext,
    CommandResult,
    ProposalDraft,
    ProposalReopen,
    ProposalReview,
    ReviewAction,
)
from .sqlite_base import (
    APPROVED_TYPE_MAP,
    BusinessPermissionDenied,
    ResourceConflict,
    SQLiteStoreBase,
    canonical_json,
)


class SQLiteProposalMixin(SQLiteStoreBase):
    """实现 Proposal 创建、人工评审、重开和显式替代。"""

    def create_proposal(self, context: CommandContext, proposal: ProposalDraft) -> CommandResult:
        """创建工作层 Proposal，不直接改变当前状态。"""

        request = {"proposal": asdict(proposal), "expected_version": context.expected_version}

        def operation(connection: sqlite3.Connection, version: int) -> tuple[str, str]:
            if proposal.source_meeting_item_id is not None:
                self._validate_meeting_item_link(connection, context.project_id, proposal)
            if proposal.proposal_kind == "supersede":
                self._validate_supersede_draft(connection, context.project_id, proposal)
            payload = {
                **asdict(proposal),
                "before": dict(proposal.before) if proposal.before is not None else None,
                "after": dict(proposal.after),
                "evidence_refs": list(proposal.evidence_refs),
            }
            event_id = self._append_event(
                connection,
                context,
                version,
                "proposal",
                proposal.proposal_id,
                "PROPOSAL_CREATED",
                payload,
            )
            now = self._event_time(connection, event_id)
            connection.execute(
                """
                INSERT INTO proposals(
                    proposal_id, project_id, base_state_version, proposal_kind, subject_id,
                    classification, before_json, after_json, reason, impact_scope,
                    created_event_id, created_at, supersedes_proposal_id,
                    source_meeting_item_id, valid_from, valid_until
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    proposal.proposal_id,
                    context.project_id,
                    context.expected_version,
                    proposal.proposal_kind,
                    proposal.subject_id,
                    proposal.classification,
                    canonical_json(proposal.before) if proposal.before is not None else None,
                    canonical_json(proposal.after),
                    proposal.reason,
                    proposal.impact_scope,
                    event_id,
                    now,
                    proposal.supersedes_proposal_id,
                    proposal.source_meeting_item_id,
                    proposal.valid_from,
                    proposal.valid_until,
                ),
            )
            connection.execute(
                """
                INSERT INTO proposal_lifecycle(
                    project_id, proposal_id, status, revision, last_event_id, updated_at
                ) VALUES (?, ?, 'proposed', 0, ?, ?)
                """,
                (context.project_id, proposal.proposal_id, event_id, now),
            )
            connection.executemany(
                "INSERT INTO proposal_evidence(proposal_id, evidence_ref) VALUES (?, ?)",
                ((proposal.proposal_id, evidence_ref) for evidence_ref in proposal.evidence_refs),
            )
            if proposal.source_meeting_item_id is not None:
                connection.execute(
                    """
                    INSERT INTO meeting_item_proposals(
                        project_id, item_id, proposal_id, linked_event_id, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        context.project_id,
                        proposal.source_meeting_item_id,
                        proposal.proposal_id,
                        event_id,
                        now,
                    ),
                )
            return event_id, proposal.proposal_id

        return self._execute(context, "create_proposal", request, operation)

    def review_proposal(self, context: CommandContext, review: ProposalReview) -> CommandResult:
        """只允许 Fox 批准、修改后批准或驳回 Proposal。"""

        self._require_reviewer(context)
        request = {"review": asdict(review), "expected_version": context.expected_version}

        def operation(connection: sqlite3.Connection, version: int) -> tuple[str, str]:
            proposal = connection.execute(
                """
                SELECT proposal.*, lifecycle.status AS lifecycle_status
                FROM proposals AS proposal
                JOIN proposal_lifecycle AS lifecycle
                  ON lifecycle.project_id = proposal.project_id
                 AND lifecycle.proposal_id = proposal.proposal_id
                WHERE proposal.project_id = ? AND proposal.proposal_id = ?
                """,
                (context.project_id, review.proposal_id),
            ).fetchone()
            if proposal is None:
                raise ResourceConflict("Proposal 不存在")
            if proposal["lifecycle_status"] != "proposed":
                raise ResourceConflict("Proposal 当前不处于待确认状态")
            evidence_refs = [
                row[0]
                for row in connection.execute(
                    "SELECT evidence_ref FROM proposal_evidence WHERE proposal_id = ? ORDER BY evidence_ref",
                    (review.proposal_id,),
                )
            ]
            before = json.loads(proposal["before_json"]) if proposal["before_json"] else None
            original_after = json.loads(proposal["after_json"])
            after = (
                dict(review.replacement_after)
                if review.replacement_after is not None
                else original_after
            )
            approved = review.action in {ReviewAction.APPROVE, ReviewAction.MODIFY_AND_APPROVE}
            state_item = self._state_item(proposal, after) if approved else None
            supersession = None
            if state_item is not None:
                if proposal["proposal_kind"] == "supersede":
                    supersession = self._prepare_supersession(
                        connection, context.project_id, proposal, state_item
                    )
                else:
                    self._ensure_no_silent_state_overwrite(
                        connection, context.project_id, state_item
                    )
            payload = {
                "proposal_id": review.proposal_id,
                "action": review.action.value,
                "reason": review.reason,
                "before": before,
                "after": after if approved else None,
                "evidence_refs": evidence_refs,
                "state_item": state_item,
                "removed_state_item": (
                    supersession["predecessor_state_item"] if supersession is not None else None
                ),
                "supersession": supersession,
            }
            event_type = "PROPOSAL_APPROVED" if approved else "PROPOSAL_REJECTED"
            event_id = self._append_event(
                connection,
                context,
                version,
                "proposal",
                review.proposal_id,
                event_type,
                payload,
            )
            now = self._event_time(connection, event_id)
            new_status = "approved" if approved else "rejected"
            updated = connection.execute(
                """
                UPDATE proposals
                SET status = ?, after_json = ?, reviewed_event_id = ?, reviewed_at = ?
                WHERE project_id = ? AND proposal_id = ? AND status = 'proposed'
                """,
                (
                    new_status,
                    canonical_json(after if approved else original_after),
                    event_id,
                    now,
                    context.project_id,
                    review.proposal_id,
                ),
            )
            if updated.rowcount != 1:
                raise ResourceConflict("Proposal 评审状态已变化")
            lifecycle_updated = connection.execute(
                """
                UPDATE proposal_lifecycle
                SET status = ?, last_event_id = ?, updated_at = ?
                WHERE project_id = ? AND proposal_id = ? AND status = 'proposed'
                """,
                (new_status, event_id, now, context.project_id, review.proposal_id),
            )
            if lifecycle_updated.rowcount != 1:
                raise ResourceConflict("Proposal 生命周期已变化")
            connection.execute(
                """
                INSERT INTO human_actions(
                    action_id, project_id, proposal_id, action, actor_id, reason,
                    before_json, after_json, evidence_json, base_state_version, event_id, acted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    context.project_id,
                    review.proposal_id,
                    review.action.value,
                    context.actor.actor_id,
                    review.reason,
                    canonical_json(before) if before is not None else None,
                    canonical_json(after) if approved else None,
                    canonical_json(evidence_refs),
                    context.expected_version,
                    event_id,
                    now,
                ),
            )
            if supersession is not None:
                self._apply_supersession(
                    connection,
                    context,
                    proposal,
                    supersession,
                    evidence_refs,
                    event_id,
                    now,
                )
            if state_item is not None:
                self._apply_approval_projection(
                    connection, context.project_id, state_item, event_id, version
                )
            return event_id, review.proposal_id

        return self._execute(context, "review_proposal", request, operation)

    def reopen_proposal(
        self, context: CommandContext, reopen: ProposalReopen
    ) -> CommandResult:
        """由 Fox 使用新证据重开已驳回的 Proposal。"""

        self._require_reviewer(context)
        request = {"reopen": asdict(reopen), "expected_version": context.expected_version}

        def operation(connection: sqlite3.Connection, version: int) -> tuple[str, str]:
            proposal = connection.execute(
                """
                SELECT proposal.proposal_id, lifecycle.status AS lifecycle_status
                FROM proposals AS proposal
                JOIN proposal_lifecycle AS lifecycle
                  ON lifecycle.project_id = proposal.project_id
                 AND lifecycle.proposal_id = proposal.proposal_id
                WHERE proposal.project_id = ? AND proposal.proposal_id = ?
                """,
                (context.project_id, reopen.proposal_id),
            ).fetchone()
            if proposal is None:
                raise ResourceConflict("Proposal 不存在")
            if proposal["lifecycle_status"] != "rejected":
                raise ResourceConflict("只有已驳回 Proposal 可以重开")
            existing_evidence = {
                row[0]
                for row in connection.execute(
                    "SELECT evidence_ref FROM proposal_evidence WHERE proposal_id = ?",
                    (reopen.proposal_id,),
                )
            }
            if not set(reopen.evidence_refs) - existing_evidence:
                raise ResourceConflict("重开 Proposal 必须补充至少一条新证据")
            event_id = self._append_event(
                connection,
                context,
                version,
                "proposal",
                reopen.proposal_id,
                "PROPOSAL_REOPENED",
                {
                    "proposal_id": reopen.proposal_id,
                    "reason": reopen.reason,
                    "evidence_refs": list(reopen.evidence_refs),
                    "old_base_state_version": connection.execute(
                        "SELECT base_state_version FROM proposals WHERE proposal_id = ?",
                        (reopen.proposal_id,),
                    ).fetchone()[0],
                    "new_base_state_version": context.expected_version,
                },
            )
            now = self._event_time(connection, event_id)
            proposal_updated = connection.execute(
                """
                UPDATE proposals
                SET status = 'proposed', base_state_version = ?,
                    reviewed_event_id = NULL, reviewed_at = NULL
                WHERE project_id = ? AND proposal_id = ? AND status = 'rejected'
                """,
                (context.expected_version, context.project_id, reopen.proposal_id),
            )
            if proposal_updated.rowcount != 1:
                raise ResourceConflict("Proposal 评审状态已变化")
            lifecycle_updated = connection.execute(
                """
                UPDATE proposal_lifecycle
                SET status = 'proposed', revision = revision + 1,
                    last_event_id = ?, updated_at = ?
                WHERE project_id = ? AND proposal_id = ? AND status = 'rejected'
                """,
                (event_id, now, context.project_id, reopen.proposal_id),
            )
            if lifecycle_updated.rowcount != 1:
                raise ResourceConflict("Proposal 生命周期已变化")
            connection.executemany(
                """
                INSERT INTO proposal_evidence(proposal_id, evidence_ref) VALUES (?, ?)
                ON CONFLICT DO NOTHING
                """,
                ((reopen.proposal_id, evidence_ref) for evidence_ref in reopen.evidence_refs),
            )
            self._record_lifecycle_action(
                connection,
                context,
                reopen.proposal_id,
                "reopen",
                reopen.reason,
                "rejected",
                "proposed",
                list(reopen.evidence_refs),
                event_id,
                now,
            )
            return event_id, reopen.proposal_id

        return self._execute(context, "reopen_proposal", request, operation)

    def _require_reviewer(self, context: CommandContext) -> None:
        if (
            context.actor.kind is not ActorKind.HUMAN
            or context.actor.actor_id not in self.allowed_reviewers
        ):
            raise BusinessPermissionDenied("只有已配置的人工评审人可以改变 Proposal 生命周期")

    def _event_time(self, connection: sqlite3.Connection, event_id: str) -> str:
        row = connection.execute(
            "SELECT committed_at FROM events WHERE event_id = ?", (event_id,)
        ).fetchone()
        if row is None:
            raise ResourceConflict("Proposal 事件不存在")
        return str(row["committed_at"])

    def _validate_meeting_item_link(
        self, connection: sqlite3.Connection, project_id: str, proposal: ProposalDraft
    ) -> None:
        item = connection.execute(
            """
            SELECT meeting_id, classification FROM meeting_interpretation_items
            WHERE project_id = ? AND item_id = ?
            """,
            (project_id, proposal.source_meeting_item_id),
        ).fetchone()
        if item is None:
            raise ResourceConflict("Proposal 引用的会议解释项不存在")
        if item["classification"] != proposal.classification:
            raise ResourceConflict("Proposal 分类与会议解释项不一致")
        segment_ids = [
            row[0]
            for row in connection.execute(
                """
                SELECT segment_id FROM meeting_item_evidence
                WHERE project_id = ? AND item_id = ? ORDER BY segment_id
                """,
                (project_id, proposal.source_meeting_item_id),
            )
        ]
        expected_refs = {
            f"meeting:{item['meeting_id']}#{segment_id}" for segment_id in segment_ids
        }
        if not expected_refs.issubset(set(proposal.evidence_refs)):
            raise ResourceConflict("Proposal 缺少会议解释项对应的原话证据")

    def _validate_supersede_draft(
        self, connection: sqlite3.Connection, project_id: str, proposal: ProposalDraft
    ) -> None:
        if proposal.supersedes_proposal_id == proposal.proposal_id:
            raise ResourceConflict("Proposal 不能替代自己")
        predecessor = connection.execute(
            """
            SELECT lifecycle.status FROM proposal_lifecycle AS lifecycle
            WHERE lifecycle.project_id = ? AND lifecycle.proposal_id = ?
            """,
            (project_id, proposal.supersedes_proposal_id),
        ).fetchone()
        if predecessor is None or predecessor["status"] != "approved":
            raise ResourceConflict("被替代 Proposal 不存在或不是当前已批准状态")
        state = connection.execute(
            """
            SELECT item_type, item_id, payload_json FROM state_items
            WHERE project_id = ? AND source_proposal_id = ?
            """,
            (project_id, proposal.supersedes_proposal_id),
        ).fetchone()
        if state is None:
            raise ResourceConflict("被替代 Proposal 已不在当前状态投影中")
        if canonical_json(proposal.before) != state["payload_json"]:
            raise ResourceConflict("supersede Proposal 的 before 与当前旧值不一致")
        new_item = self._state_item_from_draft(proposal)
        if new_item["item_type"] != state["item_type"]:
            raise ResourceConflict("替代前后的正式状态类型必须一致")
        if new_item["item_id"] == state["item_id"]:
            raise ResourceConflict("替代必须使用新的 subject_id，不能覆盖旧状态 ID")

    def _state_item_from_draft(self, proposal: ProposalDraft) -> dict[str, object]:
        item_id = proposal.subject_id or proposal.after.get("id")
        if not isinstance(item_id, str) or not item_id.strip():
            raise ResourceConflict("Proposal 必须有稳定 subject_id 或 after.id")
        return {
            "item_type": APPROVED_TYPE_MAP.get(proposal.classification, proposal.classification),
            "item_id": item_id,
            "payload": dict(proposal.after),
            "source_proposal_id": proposal.proposal_id,
            "valid_from": proposal.valid_from,
            "valid_until": proposal.valid_until,
        }

    def _state_item(self, proposal: sqlite3.Row, after: Mapping[str, object]) -> dict[str, object]:
        item_id = proposal["subject_id"] or after.get("id")
        if not isinstance(item_id, str) or not item_id.strip():
            raise ResourceConflict("批准 Proposal 前必须有稳定 subject_id 或 after.id")
        return {
            "item_type": APPROVED_TYPE_MAP.get(proposal["classification"], proposal["classification"]),
            "item_id": item_id,
            "payload": dict(after),
            "source_proposal_id": proposal["proposal_id"],
            "valid_from": proposal["valid_from"],
            "valid_until": proposal["valid_until"],
        }

    def _ensure_no_silent_state_overwrite(
        self,
        connection: sqlite3.Connection,
        project_id: str,
        state_item: Mapping[str, object],
    ) -> None:
        existing = connection.execute(
            """
            SELECT source_proposal_id FROM state_items
            WHERE project_id = ? AND item_type = ? AND item_id = ?
            """,
            (project_id, state_item["item_type"], state_item["item_id"]),
        ).fetchone()
        if existing is not None and existing["source_proposal_id"] != state_item["source_proposal_id"]:
            raise ResourceConflict("当前状态已有同 ID 项；必须使用 supersede Proposal 显式替代")

    def _prepare_supersession(
        self,
        connection: sqlite3.Connection,
        project_id: str,
        proposal: sqlite3.Row,
        successor_state_item: Mapping[str, object],
    ) -> dict[str, object]:
        predecessor_id = proposal["supersedes_proposal_id"]
        predecessor = connection.execute(
            """
            SELECT status FROM proposal_lifecycle
            WHERE project_id = ? AND proposal_id = ?
            """,
            (project_id, predecessor_id),
        ).fetchone()
        if predecessor is None or predecessor["status"] != "approved":
            raise ResourceConflict("被替代 Proposal 已不是当前批准状态")
        state = connection.execute(
            """
            SELECT item_type, item_id, payload_json, source_proposal_id,
                   updated_event_id, state_version, valid_from, valid_until
            FROM state_items WHERE project_id = ? AND source_proposal_id = ?
            """,
            (project_id, predecessor_id),
        ).fetchone()
        if state is None:
            raise ResourceConflict("被替代 Proposal 已不在当前状态投影中")
        predecessor_payload = json.loads(state["payload_json"])
        if proposal["before_json"] != canonical_json(predecessor_payload):
            raise ResourceConflict("被替代状态已变化，请重新创建 Proposal")
        if successor_state_item["item_type"] != state["item_type"]:
            raise ResourceConflict("替代前后的正式状态类型必须一致")
        if successor_state_item["item_id"] == state["item_id"]:
            raise ResourceConflict("替代必须使用新的状态 ID")
        return {
            "predecessor_proposal_id": predecessor_id,
            "successor_proposal_id": proposal["proposal_id"],
            "predecessor_state_item": {
                "item_type": state["item_type"],
                "item_id": state["item_id"],
                "payload": predecessor_payload,
                "source_proposal_id": state["source_proposal_id"],
                "updated_event_id": state["updated_event_id"],
                "state_version": state["state_version"],
                "valid_from": state["valid_from"],
                "valid_until": state["valid_until"],
            },
            "successor_state_item": dict(successor_state_item),
        }

    def _apply_supersession(
        self,
        connection: sqlite3.Connection,
        context: CommandContext,
        proposal: sqlite3.Row,
        supersession: Mapping[str, object],
        evidence_refs: list[str],
        event_id: str,
        now: str,
    ) -> None:
        predecessor = supersession["predecessor_state_item"]
        successor = supersession["successor_state_item"]
        deleted = connection.execute(
            """
            DELETE FROM state_items
            WHERE project_id = ? AND item_type = ? AND item_id = ?
              AND source_proposal_id = ?
            """,
            (
                context.project_id,
                predecessor["item_type"],
                predecessor["item_id"],
                supersession["predecessor_proposal_id"],
            ),
        )
        if deleted.rowcount != 1:
            raise ResourceConflict("被替代状态已变化")
        updated = connection.execute(
            """
            UPDATE proposal_lifecycle
            SET status = 'superseded', last_event_id = ?, updated_at = ?
            WHERE project_id = ? AND proposal_id = ? AND status = 'approved'
            """,
            (
                event_id,
                now,
                context.project_id,
                supersession["predecessor_proposal_id"],
            ),
        )
        if updated.rowcount != 1:
            raise ResourceConflict("被替代 Proposal 生命周期已变化")
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
                context.project_id,
                supersession["predecessor_proposal_id"],
                proposal["proposal_id"],
                predecessor["item_type"],
                predecessor["item_id"],
                successor["item_type"],
                successor["item_id"],
                canonical_json(predecessor["payload"]),
                canonical_json(successor["payload"]),
                event_id,
                now,
            ),
        )
        self._record_lifecycle_action(
            connection,
            context,
            supersession["predecessor_proposal_id"],
            "supersede",
            f"由 Proposal {proposal['proposal_id']} 替代",
            "approved",
            "superseded",
            evidence_refs,
            event_id,
            now,
        )

    def _record_lifecycle_action(
        self,
        connection: sqlite3.Connection,
        context: CommandContext,
        proposal_id: str,
        action: str,
        reason: str,
        before_status: str,
        after_status: str,
        evidence_refs: list[str],
        event_id: str,
        now: str,
    ) -> None:
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
                context.project_id,
                proposal_id,
                action,
                context.actor.actor_id,
                reason,
                before_status,
                after_status,
                canonical_json(evidence_refs),
                context.expected_version,
                event_id,
                now,
            ),
        )
