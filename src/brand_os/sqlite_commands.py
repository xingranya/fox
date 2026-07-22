"""SQLite 权威库的领域写命令。"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import asdict
from uuid import uuid4

from .domain import (
    ActorKind,
    ClassificationCandidate,
    CommandContext,
    CommandResult,
    ProposalDraft,
    ProposalReview,
    RelationDraft,
    ReviewAction,
    SourceRecord,
)
from .sqlite_base import (
    APPROVED_TYPE_MAP,
    BusinessPermissionDenied,
    ResourceConflict,
    SQLiteStoreBase,
    VersionConflict,
    canonical_json,
    utc_now,
)


class SQLiteCommandMixin(SQLiteStoreBase):
    """实现创建项目、来源、候选、Proposal、关系和人工评审。"""

    def create_project(self, context: CommandContext, name: str) -> CommandResult:
        """创建项目并写入第一条权威事件。"""

        if context.actor.kind not in {ActorKind.HUMAN, ActorKind.SYSTEM}:
            raise BusinessPermissionDenied("只有人或本地系统初始化流程可以创建项目")
        if not name.strip():
            raise ValueError("项目名称不能为空")
        request = {"name": name, "expected_version": context.expected_version}
        request_hash = self._request_hash(request)
        connection = self._connect()
        begun = False
        try:
            connection.execute("BEGIN IMMEDIATE")
            begun = True
            existing = self._find_command(connection, context, "create_project")
            if existing is not None:
                result = self._replay_command(existing, request_hash)
                connection.execute("COMMIT")
                return result
            project = connection.execute(
                "SELECT version FROM projects WHERE project_id = ?", (context.project_id,)
            ).fetchone()
            if project is not None:
                raise ResourceConflict(f"项目已存在：{context.project_id}")
            if context.expected_version != 0:
                raise VersionConflict(context.expected_version, 0)
            now = utc_now()
            connection.execute(
                "INSERT INTO projects(project_id, name, version, created_at, updated_at) VALUES (?, ?, 0, ?, ?)",
                (context.project_id, name, now, now),
            )
            event_id = self._append_event(
                connection,
                context,
                project_version=1,
                aggregate_type="project",
                aggregate_id=context.project_id,
                event_type="PROJECT_CREATED",
                payload={"name": name},
            )
            updated = connection.execute(
                "UPDATE projects SET version = 1, updated_at = ? WHERE project_id = ? AND version = 0",
                (now, context.project_id),
            )
            if updated.rowcount != 1:
                raise ResourceConflict("项目初始化版本写入失败")
            result = CommandResult(1, event_id, context.project_id)
            self._record_command(connection, context, "create_project", request_hash, result)
            connection.execute("COMMIT")
            return result
        except Exception:
            if begun:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def register_source(self, context: CommandContext, source: SourceRecord) -> CommandResult:
        """登记原件版本元数据，不复制无来源正文。"""

        if context.actor.kind not in {ActorKind.HUMAN, ActorKind.SYSTEM}:
            raise BusinessPermissionDenied("只有人或本地导入系统可以登记原件")
        request = {"source": asdict(source), "expected_version": context.expected_version}

        def operation(connection: sqlite3.Connection, version: int) -> tuple[str, str]:
            event_id = self._append_event(
                connection,
                context,
                version,
                "source",
                source.source_id,
                "SOURCE_REGISTERED",
                asdict(source),
            )
            connection.execute(
                """
                INSERT INTO sources(
                    source_id, project_id, sha256, size, relative_path, source_role,
                    confidentiality, status, registered_event_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source.source_id,
                    context.project_id,
                    source.sha256,
                    source.size,
                    source.relative_path,
                    source.source_role,
                    source.confidentiality,
                    source.status,
                    event_id,
                    utc_now(),
                ),
            )
            return event_id, source.source_id

        return self._execute(context, "register_source", request, operation)

    def record_candidate(
        self, context: CommandContext, candidate: ClassificationCandidate
    ) -> CommandResult:
        """登记带原件版本和定位的工作层分类候选。"""

        request = {"candidate": asdict(candidate), "expected_version": context.expected_version}

        def operation(connection: sqlite3.Connection, version: int) -> tuple[str, str]:
            source = connection.execute(
                "SELECT sha256 FROM sources WHERE project_id = ? AND source_id = ?",
                (context.project_id, candidate.source_id),
            ).fetchone()
            if source is None or source["sha256"] != candidate.source_sha256:
                raise ResourceConflict("候选引用的原件版本不存在或哈希不匹配")
            event_id = self._append_event(
                connection,
                context,
                version,
                "classification_candidate",
                candidate.candidate_id,
                "CLASSIFICATION_CANDIDATE_RECORDED",
                asdict(candidate),
            )
            connection.execute(
                """
                INSERT INTO classification_candidates(
                    candidate_id, project_id, source_id, source_sha256, locator, excerpt,
                    classification, reasoning, recorded_event_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate.candidate_id,
                    context.project_id,
                    candidate.source_id,
                    candidate.source_sha256,
                    candidate.locator,
                    candidate.excerpt,
                    candidate.classification,
                    candidate.reasoning,
                    event_id,
                    utc_now(),
                ),
            )
            return event_id, candidate.candidate_id

        return self._execute(context, "record_candidate", request, operation)

    def create_proposal(self, context: CommandContext, proposal: ProposalDraft) -> CommandResult:
        """创建不会直接改变当前状态的 Proposal。"""

        request = {"proposal": asdict(proposal), "expected_version": context.expected_version}

        def operation(connection: sqlite3.Connection, version: int) -> tuple[str, str]:
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
            connection.execute(
                """
                INSERT INTO proposals(
                    proposal_id, project_id, base_state_version, proposal_kind, subject_id,
                    classification, before_json, after_json, reason, impact_scope,
                    created_event_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    utc_now(),
                ),
            )
            connection.executemany(
                "INSERT INTO proposal_evidence(proposal_id, evidence_ref) VALUES (?, ?)",
                ((proposal.proposal_id, evidence_ref) for evidence_ref in proposal.evidence_refs),
            )
            return event_id, proposal.proposal_id

        return self._execute(context, "create_proposal", request, operation)

    def add_relation(self, context: CommandContext, relation: RelationDraft) -> CommandResult:
        """登记带证据的工作层关系，不自动提升为正式决定。"""

        request = {"relation": asdict(relation), "expected_version": context.expected_version}

        def operation(connection: sqlite3.Connection, version: int) -> tuple[str, str]:
            event_id = self._append_event(
                connection,
                context,
                version,
                "relation",
                relation.relation_id,
                "RELATION_RECORDED",
                asdict(relation),
            )
            connection.execute(
                """
                INSERT INTO relations(
                    relation_id, project_id, from_type, from_id, relation_type,
                    to_type, to_id, evidence_ref, recorded_event_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    relation.relation_id,
                    context.project_id,
                    relation.from_type,
                    relation.from_id,
                    relation.relation_type,
                    relation.to_type,
                    relation.to_id,
                    relation.evidence_ref,
                    event_id,
                    utc_now(),
                ),
            )
            return event_id, relation.relation_id

        return self._execute(context, "add_relation", request, operation)

    def review_proposal(self, context: CommandContext, review: ProposalReview) -> CommandResult:
        """仅允许 Fox 的人工动作改变 Proposal 和当前投影。"""

        if context.actor.kind is not ActorKind.HUMAN or context.actor.actor_id not in self.allowed_reviewers:
            raise BusinessPermissionDenied("只有已配置的人工评审人可以改变正式状态")
        request = {"review": asdict(review), "expected_version": context.expected_version}

        def operation(connection: sqlite3.Connection, version: int) -> tuple[str, str]:
            proposal = connection.execute(
                "SELECT * FROM proposals WHERE project_id = ? AND proposal_id = ?",
                (context.project_id, review.proposal_id),
            ).fetchone()
            if proposal is None:
                raise ResourceConflict("Proposal 不存在")
            if proposal["status"] != "proposed":
                raise ResourceConflict("Proposal 已经完成评审")
            evidence_refs = [
                row[0]
                for row in connection.execute(
                    "SELECT evidence_ref FROM proposal_evidence WHERE proposal_id = ? ORDER BY evidence_ref",
                    (review.proposal_id,),
                )
            ]
            before = json.loads(proposal["before_json"]) if proposal["before_json"] else None
            original_after = json.loads(proposal["after_json"])
            after = dict(review.replacement_after) if review.replacement_after is not None else original_after
            approved = review.action in {ReviewAction.APPROVE, ReviewAction.MODIFY_AND_APPROVE}
            state_item = self._state_item(proposal, after) if approved else None
            payload = {
                "proposal_id": review.proposal_id,
                "action": review.action.value,
                "reason": review.reason,
                "before": before,
                "after": after if approved else None,
                "evidence_refs": evidence_refs,
                "state_item": state_item,
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
            now = utc_now()
            connection.execute(
                """
                UPDATE proposals
                SET status = ?, after_json = ?, reviewed_event_id = ?, reviewed_at = ?
                WHERE proposal_id = ? AND status = 'proposed'
                """,
                (
                    "approved" if approved else "rejected",
                    canonical_json(after if approved else original_after),
                    event_id,
                    now,
                    review.proposal_id,
                ),
            )
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
            if state_item is not None:
                self._apply_approval_projection(connection, context.project_id, state_item, event_id, version)
            return event_id, review.proposal_id

        return self._execute(context, "review_proposal", request, operation)

    def _state_item(self, proposal: sqlite3.Row, after: Mapping[str, object]) -> dict[str, object]:
        """从批准后的 Proposal 生成最小当前状态项。"""

        item_id = proposal["subject_id"] or after.get("id")
        if not isinstance(item_id, str) or not item_id.strip():
            raise ResourceConflict("批准 Proposal 前必须有稳定 subject_id 或 after.id")
        return {
            "item_type": APPROVED_TYPE_MAP.get(proposal["classification"], proposal["classification"]),
            "item_id": item_id,
            "payload": dict(after),
            "source_proposal_id": proposal["proposal_id"],
        }
