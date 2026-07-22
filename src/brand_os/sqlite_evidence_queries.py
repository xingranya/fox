"""SQLite 中决定、开放问题、关系和证据链的只读查询。"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from .domain import RELATION_TYPES
from .sqlite_base import ProjectNotFound, SQLiteStoreBase, canonical_json


MEETING_REF_PATTERN = re.compile(r"^meeting:([^#]+)#(.+)$")
SOURCE_VERSION_REF_PATTERN = re.compile(r"^source-version:([^#]+)#(.+)$")
INACTIVE_VALIDITY = {"superseded", "archived", "expired", "scheduled"}


class SQLiteEvidenceQueryMixin(SQLiteStoreBase):
    """提供不改动事件和投影的证据查询读取面。"""

    def list_decisions(
        self,
        project_id: str,
        *,
        as_of: str | None = None,
        include_inactive: bool = False,
    ) -> list[Mapping[str, object]]:
        """读取决定；默认只返回当前且未过期的正式决定。"""

        return self._list_semantic_items(
            project_id,
            classification="DECISION_CANDIDATE",
            item_type="DECISION",
            as_of=as_of,
            include_inactive=include_inactive,
        )

    def list_open_questions(
        self,
        project_id: str,
        *,
        as_of: str | None = None,
        include_inactive: bool = False,
    ) -> list[Mapping[str, object]]:
        """读取开放问题；默认排除废案、被替代项和过期项。"""

        return self._list_semantic_items(
            project_id,
            classification="OPEN",
            item_type="OPEN",
            as_of=as_of,
            include_inactive=include_inactive,
        )

    def get_evidence_chain(
        self,
        project_id: str,
        item_type: str,
        item_id: str,
        *,
        as_of: str | None = None,
    ) -> Mapping[str, object]:
        """返回一个决定或开放问题的批准、原话和来源版本链。"""

        normalized_type = item_type.upper()
        if normalized_type in {"DECISION", "DECISION_CANDIDATE"}:
            items = self.list_decisions(project_id, as_of=as_of, include_inactive=True)
        elif normalized_type in {"OPEN", "OPEN_QUESTION"}:
            items = self.list_open_questions(project_id, as_of=as_of, include_inactive=True)
        else:
            return self._unconfirmed_chain(item_type, item_id, "不支持的结论类型")
        item = next((value for value in items if value["item_id"] == item_id), None)
        if item is None:
            return self._unconfirmed_chain(item_type, item_id, "未找到对应结论")
        return {
            "schema_version": "evidence-chain.v1",
            "project_id": project_id,
            "item_type": item["item_type"],
            "item_id": item["item_id"],
            "verification": item["evidence_status"],
            "message": (
                "证据链完整"
                if item["evidence_status"] == "confirmed"
                else "未确认：证据链不完整"
            ),
            "conclusion": item,
        }

    def resolve_evidence_ref(
        self, project_id: str, evidence_ref: str
    ) -> Mapping[str, object]:
        """把稳定引用解析为来源版本或会议原话；无法解析时明确未确认。"""

        with self._connect() as connection:
            self._require_project(connection, project_id)
            return self._resolve_evidence_ref(connection, project_id, evidence_ref)

    def query_relations(
        self,
        project_id: str,
        *,
        subject_type: str | None = None,
        subject_id: str | None = None,
        relation_types: Sequence[str] | None = None,
        as_of: str | None = None,
        include_inactive: bool = False,
    ) -> list[Mapping[str, object]]:
        """合并显式与可证明的派生关系，并按端点有效性过滤。"""

        if (subject_type is None) != (subject_id is None):
            raise ValueError("subject_type 与 subject_id 必须同时提供")
        requested_types = set(relation_types or ())
        unknown_types = requested_types - RELATION_TYPES
        if unknown_types:
            raise ValueError(f"未知关系类型：{', '.join(sorted(unknown_types))}")
        as_of_time = self._as_of(as_of)
        with self._connect() as connection:
            self._require_project(connection, project_id)
            rows = self._explicit_relations(connection, project_id)
            rows.extend(self._derived_relations(connection, project_id))
            results: list[Mapping[str, object]] = []
            for row in rows:
                if requested_types and row["relation_type"] not in requested_types:
                    continue
                if subject_type is not None and not (
                    (row["from_type"] == subject_type and row["from_id"] == subject_id)
                    or (row["to_type"] == subject_type and row["to_id"] == subject_id)
                ):
                    continue
                from_validity = self._endpoint_validity(
                    connection,
                    project_id,
                    str(row["from_type"]),
                    str(row["from_id"]),
                    as_of_time,
                )
                to_validity = self._endpoint_validity(
                    connection,
                    project_id,
                    str(row["to_type"]),
                    str(row["to_id"]),
                    as_of_time,
                )
                relation_validity = self._relation_validity(from_validity, to_validity)
                if not include_inactive and relation_validity["status"] in INACTIVE_VALIDITY:
                    continue
                evidence_ref = str(row["evidence_ref"])
                evidence = self._resolve_evidence_ref(connection, project_id, evidence_ref)
                results.append(
                    {
                        "relation_id": row["relation_id"],
                        "relation_type": row["relation_type"],
                        "from": {
                            "type": row["from_type"],
                            "id": row["from_id"],
                            "validity": from_validity,
                        },
                        "to": {
                            "type": row["to_type"],
                            "id": row["to_id"],
                            "validity": to_validity,
                        },
                        "validity": relation_validity,
                        "authority_status": row["authority_status"],
                        "origin": row["origin"],
                        "evidence_ref": evidence_ref,
                        "evidence_status": evidence["verification"],
                        "evidence": evidence,
                        "created_at": row["created_at"],
                    }
                )
        return sorted(results, key=lambda value: (str(value["created_at"]), str(value["relation_id"])))

    def _list_semantic_items(
        self,
        project_id: str,
        *,
        classification: str,
        item_type: str,
        as_of: str | None,
        include_inactive: bool,
    ) -> list[Mapping[str, object]]:
        as_of_time = self._as_of(as_of)
        with self._connect() as connection:
            self._require_project(connection, project_id)
            rows = connection.execute(
                """
                SELECT proposal.*, lifecycle.status AS lifecycle_status,
                       state.item_type AS state_item_type,
                       state.item_id AS state_item_id,
                       state.payload_json AS state_payload_json,
                       state.updated_event_id AS state_updated_event_id,
                       state.state_version,
                       state.valid_from AS state_valid_from,
                       state.valid_until AS state_valid_until,
                       review_event.actor_kind AS review_actor_kind,
                       review_event.actor_id AS review_actor_id,
                       review_event.committed_at AS review_committed_at,
                       supersession.successor_proposal_id
                FROM proposals AS proposal
                JOIN proposal_lifecycle AS lifecycle
                  ON lifecycle.project_id = proposal.project_id
                 AND lifecycle.proposal_id = proposal.proposal_id
                LEFT JOIN state_items AS state
                  ON state.project_id = proposal.project_id
                 AND state.source_proposal_id = proposal.proposal_id
                LEFT JOIN events AS review_event
                  ON review_event.event_id = proposal.reviewed_event_id
                LEFT JOIN proposal_supersessions AS supersession
                  ON supersession.project_id = proposal.project_id
                 AND supersession.predecessor_proposal_id = proposal.proposal_id
                WHERE proposal.project_id = ? AND proposal.classification = ?
                ORDER BY proposal.created_at, proposal.proposal_id
                """,
                (project_id, classification),
            ).fetchall()
            results = [
                self._semantic_item(connection, project_id, row, item_type, as_of_time)
                for row in rows
            ]
        if include_inactive:
            return results
        return [item for item in results if item["validity"]["status"] == "current"]

    def _semantic_item(
        self,
        connection: sqlite3.Connection,
        project_id: str,
        row: sqlite3.Row,
        item_type: str,
        as_of_time: datetime,
    ) -> Mapping[str, object]:
        state_present = row["state_item_id"] is not None
        valid_from = row["state_valid_from"] if state_present else row["valid_from"]
        valid_until = row["state_valid_until"] if state_present else row["valid_until"]
        validity = self._proposal_validity(
            str(row["lifecycle_status"]),
            state_present,
            valid_from,
            valid_until,
            as_of_time,
        )
        payload_json = row["state_payload_json"] if state_present else row["after_json"]
        payload = json.loads(payload_json)
        item_id = row["state_item_id"] or row["subject_id"] or payload.get("id") or row["proposal_id"]
        evidence_refs = [
            str(value[0])
            for value in connection.execute(
                """
                SELECT evidence_ref FROM proposal_evidence
                WHERE proposal_id = ? ORDER BY evidence_ref
                """,
                (row["proposal_id"],),
            )
        ]
        evidence = [
            self._resolve_evidence_ref(connection, project_id, evidence_ref)
            for evidence_ref in evidence_refs
        ]
        authority_confirmed = (
            row["lifecycle_status"] in {"approved", "superseded"}
            and row["review_actor_kind"] == "HUMAN"
            and row["review_actor_id"] in self.allowed_reviewers
        )
        missing = []
        if not authority_confirmed:
            missing.append("human_approval")
        if not evidence:
            missing.append("evidence_ref")
        for value in evidence:
            if value["verification"] != "confirmed":
                missing.extend(str(field) for field in value["missing_fields"])
        evidence_status = "confirmed" if authority_confirmed and evidence and not missing else "unconfirmed"
        return {
            "item_type": item_type,
            "item_id": str(item_id),
            "payload": payload,
            "proposal_id": row["proposal_id"],
            "record_kind": "approved_state" if authority_confirmed else "proposal",
            "scope": row["impact_scope"],
            "state_version": row["state_version"],
            "validity": validity,
            "authority": {
                "status": "confirmed" if authority_confirmed else "unconfirmed",
                "actor_id": row["review_actor_id"] if authority_confirmed else None,
                "event_id": row["reviewed_event_id"] if authority_confirmed else None,
                "confirmed_at": row["review_committed_at"] if authority_confirmed else None,
            },
            "evidence_status": evidence_status,
            "evidence_refs": evidence_refs,
            "evidence": evidence,
            "missing_evidence": sorted(set(missing)),
            "superseded_by_proposal_id": row["successor_proposal_id"],
        }

    def _proposal_validity(
        self,
        lifecycle_status: str,
        state_present: bool,
        valid_from: object,
        valid_until: object,
        as_of_time: datetime,
    ) -> Mapping[str, object]:
        if lifecycle_status == "superseded":
            return self._validity("superseded", "已有经人工批准的后继版本", valid_from, valid_until)
        if lifecycle_status == "rejected":
            return self._validity("archived", "Proposal 已被人工驳回", valid_from, valid_until)
        if lifecycle_status != "approved":
            return self._validity("unknown", "尚未形成人工批准的正式状态", valid_from, valid_until)
        if not state_present:
            return self._validity("unknown", "批准记录未出现在当前投影中", valid_from, valid_until)
        window = self._validity_window(valid_from, valid_until, as_of_time)
        return self._validity(window[0], window[1], valid_from, valid_until)

    def _validity_window(
        self, valid_from: object, valid_until: object, as_of_time: datetime
    ) -> tuple[str, str]:
        try:
            starts = self._optional_timestamp(valid_from)
            ends = self._optional_timestamp(valid_until)
        except ValueError:
            return "unknown", "有效期时间格式无法确认"
        if starts is not None and ends is not None and ends <= starts:
            return "unknown", "有效期起止顺序无效"
        if starts is not None and as_of_time < starts:
            return "scheduled", "尚未到生效时间"
        if ends is not None and as_of_time >= ends:
            return "expired", "已超过有效期"
        return "current", "当前投影有效且未过期"

    def _resolve_evidence_ref(
        self, connection: sqlite3.Connection, project_id: str, evidence_ref: str
    ) -> Mapping[str, object]:
        meeting_match = MEETING_REF_PATTERN.fullmatch(evidence_ref)
        if meeting_match is not None:
            return self._resolve_meeting_ref(
                connection, project_id, evidence_ref, meeting_match.group(1), meeting_match.group(2)
            )
        source_match = SOURCE_VERSION_REF_PATTERN.fullmatch(evidence_ref)
        if source_match is not None:
            return self._resolve_source_version_ref(
                connection, project_id, evidence_ref, source_match.group(1), source_match.group(2)
            )
        return self._unconfirmed_evidence(
            evidence_ref,
            "引用没有绑定可识别的来源版本或会议片段",
            ["supported_evidence_ref"],
        )

    def _resolve_meeting_ref(
        self,
        connection: sqlite3.Connection,
        project_id: str,
        evidence_ref: str,
        meeting_id: str,
        segment_id: str,
    ) -> Mapping[str, object]:
        row = connection.execute(
            """
            SELECT segment.locator, segment.quote, segment.speaker, segment.spoken_at,
                   segment.start_ms, segment.end_ms, segment.context,
                   meeting.meeting_id, meeting.title, meeting.occurred_at,
                   meeting.source_verification,
                   source.source_version_id, source.logical_source_id, source.sha256,
                   source.relative_path, source.confidentiality, source.status,
                   source.is_current, source.version_label
            FROM meeting_segments AS segment
            JOIN meetings AS meeting
              ON meeting.project_id = segment.project_id
             AND meeting.meeting_id = segment.meeting_id
            JOIN source_versions AS source
              ON source.project_id = meeting.project_id
             AND source.source_version_id = meeting.source_version_id
            WHERE segment.project_id = ? AND segment.meeting_id = ?
              AND segment.segment_id = ?
            """,
            (project_id, meeting_id, segment_id),
        ).fetchone()
        if row is None:
            return self._unconfirmed_evidence(
                evidence_ref,
                "会议或原话片段不存在",
                ["meeting_segment"],
            )
        missing = []
        if row["speaker"] is None:
            missing.append("speaker")
        if row["spoken_at"] is None and row["start_ms"] is None:
            missing.append("spoken_at_or_time_range")
        if row["source_verification"] != "verified":
            missing.append("verified_source")
        source = self._source_value(row)
        return {
            "evidence_ref": evidence_ref,
            "kind": "meeting_quote",
            "verification": "confirmed" if not missing else "unconfirmed",
            "message": "证据已定位" if not missing else "未确认：会议证据字段不完整",
            "missing_fields": missing,
            "locator": row["locator"],
            "quote": row["quote"],
            "speaker": row["speaker"],
            "spoken_at": row["spoken_at"],
            "start_ms": row["start_ms"],
            "end_ms": row["end_ms"],
            "context": row["context"],
            "meeting": {
                "meeting_id": row["meeting_id"],
                "title": row["title"],
                "occurred_at": row["occurred_at"],
            },
            "source": source,
            "open_ref": f"evidence://sha256/{row['sha256']}",
        }

    def _resolve_source_version_ref(
        self,
        connection: sqlite3.Connection,
        project_id: str,
        evidence_ref: str,
        source_version_id: str,
        locator: str,
    ) -> Mapping[str, object]:
        row = connection.execute(
            """
            SELECT source_version_id, logical_source_id, sha256, relative_path,
                   confidentiality, status, is_current, version_label, observed_at
            FROM source_versions
            WHERE project_id = ? AND source_version_id = ?
            """,
            (project_id, source_version_id),
        ).fetchone()
        if row is None:
            return self._unconfirmed_evidence(
                evidence_ref,
                "来源版本不存在",
                ["source_version"],
            )
        source = self._source_value(row)
        return {
            "evidence_ref": evidence_ref,
            "kind": "source_excerpt",
            "verification": "confirmed",
            "message": "证据已定位",
            "missing_fields": [],
            "locator": locator,
            "quote": None,
            "speaker": None,
            "spoken_at": row["observed_at"],
            "start_ms": None,
            "end_ms": None,
            "context": None,
            "meeting": None,
            "source": source,
            "open_ref": f"evidence://sha256/{row['sha256']}",
        }

    def _source_value(self, row: Mapping[str, Any]) -> Mapping[str, object]:
        return {
            "logical_source_id": row["logical_source_id"],
            "source_version_id": row["source_version_id"],
            "sha256": row["sha256"],
            "relative_path": row["relative_path"],
            "confidentiality": row["confidentiality"],
            "version_label": row["version_label"],
            "current_validity": self._source_validity(row["status"], bool(row["is_current"])),
        }

    def _source_validity(self, status: object, is_current: bool) -> str:
        if not is_current:
            return "superseded"
        normalized = str(status).lower()
        if normalized in {"current", "active"}:
            return "current"
        if normalized in {"superseded", "archived", "unknown"}:
            return normalized
        return "unknown"

    def _explicit_relations(
        self, connection: sqlite3.Connection, project_id: str
    ) -> list[dict[str, object]]:
        return [
            {
                **dict(row),
                "origin": "explicit_working_relation",
                "authority_status": "working",
            }
            for row in connection.execute(
                """
                SELECT relation_id, from_type, from_id, relation_type,
                       to_type, to_id, evidence_ref, created_at
                FROM relations WHERE project_id = ?
                ORDER BY created_at, relation_id
                """,
                (project_id,),
            )
        ]

    def _derived_relations(
        self, connection: sqlite3.Connection, project_id: str
    ) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        proposals = connection.execute(
            """
            SELECT proposal.proposal_id, proposal.created_at,
                   proposal.source_meeting_item_id, lifecycle.status,
                   review.actor_id AS reviewer_id
            FROM proposals AS proposal
            JOIN proposal_lifecycle AS lifecycle
              ON lifecycle.project_id = proposal.project_id
             AND lifecycle.proposal_id = proposal.proposal_id
            LEFT JOIN events AS review ON review.event_id = proposal.reviewed_event_id
            WHERE proposal.project_id = ?
            ORDER BY proposal.created_at, proposal.proposal_id
            """,
            (project_id,),
        ).fetchall()
        for proposal in proposals:
            evidence_refs = [
                str(row[0])
                for row in connection.execute(
                    "SELECT evidence_ref FROM proposal_evidence WHERE proposal_id = ? ORDER BY evidence_ref",
                    (proposal["proposal_id"],),
                )
            ]
            authority = "confirmed" if proposal["status"] in {"approved", "superseded"} else "working"
            for evidence_ref in evidence_refs:
                rows.append(
                    self._derived_relation(
                        "proposal",
                        proposal["proposal_id"],
                        "sourced_from",
                        "evidence",
                        evidence_ref,
                        evidence_ref,
                        proposal["created_at"],
                        authority,
                    )
                )
            if proposal["source_meeting_item_id"] is not None:
                meeting = connection.execute(
                    """
                    SELECT item.meeting_id, evidence.segment_id
                    FROM meeting_interpretation_items AS item
                    LEFT JOIN meeting_item_evidence AS evidence
                      ON evidence.project_id = item.project_id AND evidence.item_id = item.item_id
                    WHERE item.project_id = ? AND item.item_id = ?
                    ORDER BY evidence.segment_id LIMIT 1
                    """,
                    (project_id, proposal["source_meeting_item_id"]),
                ).fetchone()
                if meeting is not None:
                    evidence_ref = (
                        f"meeting:{meeting['meeting_id']}#{meeting['segment_id']}"
                        if meeting["segment_id"] is not None
                        else f"meeting:{meeting['meeting_id']}#unknown"
                    )
                    rows.append(
                        self._derived_relation(
                            "proposal",
                            proposal["proposal_id"],
                            "raised_in",
                            "meeting_item",
                            proposal["source_meeting_item_id"],
                            evidence_ref,
                            proposal["created_at"],
                            authority,
                        )
                    )
            if proposal["reviewer_id"] is not None and proposal["status"] in {
                "approved",
                "superseded",
            }:
                evidence_ref = evidence_refs[0] if evidence_refs else "unconfirmed:approval"
                rows.append(
                    self._derived_relation(
                        "proposal",
                        proposal["proposal_id"],
                        "approved_by",
                        "human",
                        proposal["reviewer_id"],
                        evidence_ref,
                        proposal["created_at"],
                        authority,
                    )
                )
        for row in connection.execute(
            """
            SELECT relation.successor_version_id, relation.predecessor_version_id,
                   relation.created_at
            FROM source_version_relations AS relation
            WHERE relation.project_id = ? ORDER BY relation.created_at
            """,
            (project_id,),
        ):
            rows.append(
                self._derived_relation(
                    "source_version",
                    row["successor_version_id"],
                    "supersedes",
                    "source_version",
                    row["predecessor_version_id"],
                    f"source-version:{row['successor_version_id']}#version",
                    row["created_at"],
                    "confirmed",
                )
            )
        for row in connection.execute(
            """
            SELECT supersession.successor_proposal_id,
                   supersession.predecessor_proposal_id,
                   supersession.created_at,
                   evidence.evidence_ref
            FROM proposal_supersessions AS supersession
            LEFT JOIN proposal_evidence AS evidence
              ON evidence.proposal_id = supersession.successor_proposal_id
            WHERE supersession.project_id = ?
            GROUP BY supersession.successor_proposal_id
            ORDER BY supersession.created_at
            """,
            (project_id,),
        ):
            rows.append(
                self._derived_relation(
                    "proposal",
                    row["successor_proposal_id"],
                    "supersedes",
                    "proposal",
                    row["predecessor_proposal_id"],
                    row["evidence_ref"] or "unconfirmed:supersession",
                    row["created_at"],
                    "confirmed",
                )
            )
        for row in connection.execute(
            """
            SELECT conflict.item_id, conflict.state_item_type, conflict.state_item_id,
                   conflict.created_at, evidence.segment_id, conflict.meeting_id
            FROM meeting_conflict_candidates AS conflict
            LEFT JOIN meeting_conflict_evidence AS evidence
              ON evidence.project_id = conflict.project_id
             AND evidence.conflict_id = conflict.conflict_id
            WHERE conflict.project_id = ?
            GROUP BY conflict.conflict_id
            ORDER BY conflict.created_at
            """,
            (project_id,),
        ):
            evidence_ref = (
                f"meeting:{row['meeting_id']}#{row['segment_id']}"
                if row["segment_id"] is not None
                else "unconfirmed:conflict"
            )
            rows.append(
                self._derived_relation(
                    "meeting_item",
                    row["item_id"],
                    "conflicts_with",
                    "state_item",
                    f"{row['state_item_type']}:{row['state_item_id']}",
                    evidence_ref,
                    row["created_at"],
                    "working",
                )
            )
        return rows

    def _derived_relation(
        self,
        from_type: object,
        from_id: object,
        relation_type: object,
        to_type: object,
        to_id: object,
        evidence_ref: object,
        created_at: object,
        authority_status: str,
    ) -> dict[str, object]:
        identity = canonical_json(
            [from_type, from_id, relation_type, to_type, to_id, evidence_ref]
        )
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        return {
            "relation_id": f"derived-{digest}",
            "from_type": from_type,
            "from_id": from_id,
            "relation_type": relation_type,
            "to_type": to_type,
            "to_id": to_id,
            "evidence_ref": evidence_ref,
            "created_at": created_at,
            "origin": "derived_from_authoritative_records",
            "authority_status": authority_status,
        }

    def _endpoint_validity(
        self,
        connection: sqlite3.Connection,
        project_id: str,
        entity_type: str,
        entity_id: str,
        as_of_time: datetime,
    ) -> Mapping[str, object]:
        normalized_type = entity_type.lower()
        if normalized_type == "proposal":
            row = connection.execute(
                """
                SELECT lifecycle.status, proposal.valid_from, proposal.valid_until,
                       state.item_id AS state_item_id
                FROM proposal_lifecycle AS lifecycle
                JOIN proposals AS proposal ON proposal.proposal_id = lifecycle.proposal_id
                LEFT JOIN state_items AS state
                  ON state.project_id = proposal.project_id
                 AND state.source_proposal_id = proposal.proposal_id
                WHERE lifecycle.project_id = ? AND lifecycle.proposal_id = ?
                """,
                (project_id, entity_id),
            ).fetchone()
            if row is None:
                return self._validity("unknown", "端点不存在")
            return self._proposal_validity(
                str(row["status"]),
                row["state_item_id"] is not None,
                row["valid_from"],
                row["valid_until"],
                as_of_time,
            )
        if normalized_type in {"decision", "open", "open_question", "state_item"}:
            if normalized_type == "state_item" and ":" in entity_id:
                state_type, state_id = entity_id.split(":", 1)
            else:
                state_type = "DECISION" if normalized_type == "decision" else "OPEN"
                state_id = entity_id
            row = connection.execute(
                """
                SELECT valid_from, valid_until FROM state_items
                WHERE project_id = ? AND item_type = ? AND item_id = ?
                """,
                (project_id, state_type.upper(), state_id),
            ).fetchone()
            if row is None:
                historical = connection.execute(
                    """
                    SELECT lifecycle.status, proposal.valid_from, proposal.valid_until
                    FROM proposals AS proposal
                    JOIN proposal_lifecycle AS lifecycle
                      ON lifecycle.project_id = proposal.project_id
                     AND lifecycle.proposal_id = proposal.proposal_id
                    WHERE proposal.project_id = ? AND proposal.subject_id = ?
                    ORDER BY proposal.created_at DESC LIMIT 1
                    """,
                    (project_id, state_id),
                ).fetchone()
                if historical is None:
                    return self._validity("unknown", "端点不存在")
                return self._proposal_validity(
                    str(historical["status"]),
                    False,
                    historical["valid_from"],
                    historical["valid_until"],
                    as_of_time,
                )
            status, reason = self._validity_window(row["valid_from"], row["valid_until"], as_of_time)
            return self._validity(status, reason, row["valid_from"], row["valid_until"])
        if normalized_type == "source_version":
            row = connection.execute(
                "SELECT status, is_current FROM source_versions WHERE project_id = ? AND source_version_id = ?",
                (project_id, entity_id),
            ).fetchone()
            if row is None:
                return self._validity("unknown", "来源版本不存在")
            status = self._source_validity(row["status"], bool(row["is_current"]))
            return self._validity(status, "来自不可变来源版本状态")
        if normalized_type == "source":
            row = connection.execute(
                """
                SELECT status, is_current FROM source_versions
                WHERE project_id = ? AND logical_source_id = ? AND is_current = 1
                """,
                (project_id, entity_id),
            ).fetchone()
            if row is None:
                return self._validity("unknown", "逻辑来源没有当前版本")
            status = self._source_validity(row["status"], bool(row["is_current"]))
            return self._validity(status, "来自逻辑来源当前版本")
        if normalized_type == "evidence":
            evidence = self._resolve_evidence_ref(connection, project_id, entity_id)
            if evidence["verification"] == "confirmed":
                return self._validity(
                    "current",
                    "不可变证据引用仍可回源；来源是否为当前版本另行标注",
                )
            return self._validity("unknown", "证据引用无法完整解析")
        existence_queries = {
            "meeting": ("meetings", "meeting_id"),
            "meeting_segment": ("meeting_segments", "segment_id"),
            "meeting_item": ("meeting_interpretation_items", "item_id"),
        }
        if normalized_type in existence_queries:
            table, identifier = existence_queries[normalized_type]
            row = connection.execute(
                f"SELECT 1 FROM {table} WHERE project_id = ? AND {identifier} = ?",
                (project_id, entity_id),
            ).fetchone()
            return self._validity(
                "current" if row is not None else "unknown",
                "记录存在" if row is not None else "端点不存在",
            )
        if normalized_type == "human" and entity_id.strip():
            return self._validity("current", "人工主体 ID 已记录在审计关系中")
        return self._validity("unknown", "未知端点类型")

    def _relation_validity(
        self, from_validity: Mapping[str, object], to_validity: Mapping[str, object]
    ) -> Mapping[str, object]:
        statuses = {str(from_validity["status"]), str(to_validity["status"])}
        for status in ("superseded", "archived", "expired", "scheduled", "unknown"):
            if status in statuses:
                return self._validity(status, f"关系端点包含 {status} 状态")
        return self._validity("current", "两个关系端点当前可用")

    def _as_of(self, value: str | None) -> datetime:
        if value is None:
            return datetime.now(UTC)
        parsed = self._optional_timestamp(value)
        if parsed is None:
            raise ValueError("as_of 不能为空")
        return parsed

    def _optional_timestamp(self, value: object) -> datetime | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError("时间必须是非空字符串")
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("时间必须包含时区")
        return parsed

    def _validity(
        self,
        status: str,
        reason: str,
        valid_from: object = None,
        valid_until: object = None,
    ) -> Mapping[str, object]:
        return {
            "status": status,
            "reason": reason,
            "valid_from": valid_from,
            "valid_until": valid_until,
        }

    def _require_project(self, connection: sqlite3.Connection, project_id: str) -> None:
        if connection.execute(
            "SELECT 1 FROM projects WHERE project_id = ?", (project_id,)
        ).fetchone() is None:
            raise ProjectNotFound(project_id)

    def _unconfirmed_evidence(
        self, evidence_ref: str, reason: str, missing_fields: list[str]
    ) -> Mapping[str, object]:
        return {
            "evidence_ref": evidence_ref,
            "kind": "unknown",
            "verification": "unconfirmed",
            "message": f"未确认：{reason}",
            "missing_fields": missing_fields,
            "locator": None,
            "quote": None,
            "speaker": None,
            "spoken_at": None,
            "start_ms": None,
            "end_ms": None,
            "context": None,
            "meeting": None,
            "source": None,
            "open_ref": None,
        }

    def _unconfirmed_chain(
        self, item_type: str, item_id: str, reason: str
    ) -> Mapping[str, object]:
        return {
            "schema_version": "evidence-chain.v1",
            "item_type": item_type,
            "item_id": item_id,
            "verification": "unconfirmed",
            "message": f"未确认：{reason}",
            "conclusion": None,
        }
