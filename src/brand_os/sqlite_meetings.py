"""SQLite 会议增量摄取、去重和冲突候选实现。"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import asdict

from .domain import CommandContext, CommandResult, MeetingIngestBatch
from .sqlite_base import (
    ProjectNotFound,
    ResourceConflict,
    SQLiteStoreBase,
    VersionConflict,
    canonical_json,
    utc_now,
)


class SQLiteMeetingMixin(SQLiteStoreBase):
    """把会议解释保存到工作层，不修改人工确认状态。"""

    def ingest_meeting_batch(
        self, context: CommandContext, batch: MeetingIngestBatch
    ) -> CommandResult:
        """原子写入会议、片段、解释项和冲突候选。"""

        request = {"batch": asdict(batch), "expected_version": context.expected_version}
        request_hash = self._request_hash(request)
        batch_id = f"MEETING-{batch.ingest_digest[:16].upper()}"
        connection = self._connect()
        begun = False
        try:
            self._begin_command_transaction(connection, context, "ingest_meeting_batch")
            begun = True
            existing_command = self._find_command(connection, context, "ingest_meeting_batch")
            if existing_command is not None:
                result = self._replay_command(existing_command, request_hash)
                connection.execute("COMMIT")
                return result

            project = connection.execute(
                "SELECT version FROM projects WHERE project_id = ?", (context.project_id,)
            ).fetchone()
            if project is None:
                raise ProjectNotFound(context.project_id)
            current_version = int(project["version"])
            existing_batch = connection.execute(
                """
                SELECT batch_id, recorded_event_id FROM meeting_ingest_batches
                WHERE project_id = ? AND ingest_digest = ?
                """,
                (context.project_id, batch.ingest_digest),
            ).fetchone()
            if existing_batch is not None:
                result = CommandResult(
                    current_version,
                    existing_batch["recorded_event_id"],
                    existing_batch["batch_id"],
                    replayed=True,
                )
                self._record_command(
                    connection, context, "ingest_meeting_batch", request_hash, result
                )
                connection.execute("COMMIT")
                return result
            if context.expected_version != current_version:
                raise VersionConflict(context.expected_version, current_version)
            if batch.base_state_version != current_version:
                raise VersionConflict(batch.base_state_version, current_version)

            self._validate_source_version(connection, context.project_id, batch)
            next_version = current_version + 1
            event_id = self._append_event(
                connection,
                context,
                next_version,
                "meeting_ingest",
                batch_id,
                "MEETING_INGESTED",
                {
                    "batch_id": batch_id,
                    "meeting_id": batch.meeting_id,
                    "base_state_version": batch.base_state_version,
                    "source_version_id": batch.source_version_id,
                    "source_sha256": batch.source_sha256,
                    "source_verification": batch.source_verification,
                    "meeting_mode": batch.meeting_mode,
                    "content_sha256": batch.content_sha256,
                    "ingest_digest": batch.ingest_digest,
                    "input_segment_count": len(batch.segments),
                    "input_item_count": len(batch.items),
                    "input_conflict_count": len(batch.conflicts),
                    "changes_current_business_state": False,
                },
            )
            now = utc_now()
            self._upsert_meeting(connection, context.project_id, batch, event_id, now)
            connection.execute(
                """
                INSERT INTO meeting_ingest_batches(
                    project_id, batch_id, ingest_digest, meeting_id, base_state_version,
                    source_verification, meeting_mode, mode_confidence,
                    input_segment_count, input_item_count, input_conflict_count,
                    recorded_event_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    context.project_id,
                    batch_id,
                    batch.ingest_digest,
                    batch.meeting_id,
                    batch.base_state_version,
                    batch.source_verification,
                    batch.meeting_mode,
                    batch.mode_confidence,
                    len(batch.segments),
                    len(batch.items),
                    len(batch.conflicts),
                    event_id,
                    now,
                ),
            )

            counts = {
                "new_segment_count": 0,
                "duplicate_segment_count": 0,
                "new_item_count": 0,
                "duplicate_item_count": 0,
                "new_conflict_count": 0,
                "duplicate_conflict_count": 0,
            }
            segment_ids: dict[str, str] = {}
            for segment in batch.segments:
                canonical_id, created = self._upsert_segment(
                    connection,
                    context.project_id,
                    batch,
                    batch_id,
                    event_id,
                    now,
                    segment,
                )
                segment_ids[segment.segment_id] = canonical_id
                counts["new_segment_count" if created else "duplicate_segment_count"] += 1
                connection.execute(
                    """
                    INSERT INTO meeting_batch_segments(project_id, batch_id, segment_id)
                    VALUES (?, ?, ?)
                    """,
                    (context.project_id, batch_id, canonical_id),
                )

            item_ids: dict[str, str] = {}
            for item in batch.items:
                canonical_id, created = self._upsert_item(
                    connection,
                    context.project_id,
                    batch,
                    batch_id,
                    event_id,
                    now,
                    item,
                    segment_ids,
                )
                item_ids[item.item_id] = canonical_id
                counts["new_item_count" if created else "duplicate_item_count"] += 1
                connection.execute(
                    """
                    INSERT INTO meeting_batch_items(project_id, batch_id, item_id)
                    VALUES (?, ?, ?)
                    """,
                    (context.project_id, batch_id, canonical_id),
                )

            for conflict in batch.conflicts:
                canonical_id, created = self._upsert_conflict(
                    connection,
                    context.project_id,
                    batch,
                    batch_id,
                    event_id,
                    now,
                    conflict,
                    item_ids,
                    segment_ids,
                )
                counts["new_conflict_count" if created else "duplicate_conflict_count"] += 1
                connection.execute(
                    """
                    INSERT INTO meeting_batch_conflicts(project_id, batch_id, conflict_id)
                    VALUES (?, ?, ?)
                    """,
                    (context.project_id, batch_id, canonical_id),
                )

            connection.execute(
                """
                UPDATE meeting_ingest_batches SET
                    new_segment_count = ?, duplicate_segment_count = ?,
                    new_item_count = ?, duplicate_item_count = ?,
                    new_conflict_count = ?, duplicate_conflict_count = ?
                WHERE project_id = ? AND batch_id = ?
                """,
                (*counts.values(), context.project_id, batch_id),
            )
            updated = connection.execute(
                "UPDATE projects SET version = ?, updated_at = ? WHERE project_id = ? AND version = ?",
                (next_version, now, context.project_id, current_version),
            )
            if updated.rowcount != 1:
                row = connection.execute(
                    "SELECT version FROM projects WHERE project_id = ?", (context.project_id,)
                ).fetchone()
                raise VersionConflict(context.expected_version, int(row["version"]))
            result = CommandResult(next_version, event_id, batch_id)
            self._record_command(
                connection, context, "ingest_meeting_batch", request_hash, result
            )
            connection.execute("COMMIT")
            return result
        except Exception:
            if begun:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def _validate_source_version(
        self, connection: sqlite3.Connection, project_id: str, batch: MeetingIngestBatch
    ) -> None:
        source = connection.execute(
            """
            SELECT logical_source_id, sha256 FROM source_versions
            WHERE project_id = ? AND source_version_id = ?
            """,
            (project_id, batch.source_version_id),
        ).fetchone()
        if source is None:
            raise ResourceConflict("会议引用的来源版本不存在")
        if (
            source["logical_source_id"] != batch.logical_source_id
            or source["sha256"] != batch.source_sha256
        ):
            raise ResourceConflict("会议引用的逻辑来源、版本或 SHA-256 不匹配")

    def _upsert_meeting(
        self,
        connection: sqlite3.Connection,
        project_id: str,
        batch: MeetingIngestBatch,
        event_id: str,
        now: str,
    ) -> None:
        existing = connection.execute(
            "SELECT * FROM meetings WHERE project_id = ? AND meeting_id = ?",
            (project_id, batch.meeting_id),
        ).fetchone()
        if existing is not None:
            if (
                existing["content_sha256"] != batch.content_sha256
                or existing["source_version_id"] != batch.source_version_id
                or existing["source_sha256"] != batch.source_sha256
            ):
                raise ResourceConflict("同一 meeting_id 对应了不同会议内容或来源版本")
            return
        duplicate = connection.execute(
            "SELECT meeting_id FROM meetings WHERE project_id = ? AND content_sha256 = ?",
            (project_id, batch.content_sha256),
        ).fetchone()
        if duplicate is not None:
            raise ResourceConflict(
                f"相同会议内容已经登记为 {duplicate['meeting_id']}，不能更换 meeting_id"
            )
        connection.execute(
            """
            INSERT INTO meetings(
                project_id, meeting_id, title, occurred_at, participants_json,
                logical_source_id, source_version_id, source_sha256, source_verification,
                content_sha256, first_recorded_event_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                batch.meeting_id,
                batch.title,
                batch.occurred_at,
                canonical_json(list(batch.participants)),
                batch.logical_source_id,
                batch.source_version_id,
                batch.source_sha256,
                batch.source_verification,
                batch.content_sha256,
                event_id,
                now,
            ),
        )

    def _upsert_segment(
        self,
        connection: sqlite3.Connection,
        project_id: str,
        batch: MeetingIngestBatch,
        batch_id: str,
        event_id: str,
        now: str,
        segment,
    ) -> tuple[str, bool]:
        digest = self._stable_digest(
            {
                "source_version_id": batch.source_version_id,
                "locator": segment.locator,
                "quote": segment.quote,
            }
        )
        existing_id = connection.execute(
            "SELECT segment_digest, meeting_id FROM meeting_segments WHERE project_id = ? AND segment_id = ?",
            (project_id, segment.segment_id),
        ).fetchone()
        if existing_id is not None:
            if existing_id["segment_digest"] != digest or existing_id["meeting_id"] != batch.meeting_id:
                raise ResourceConflict("同一 segment_id 对应了不同原话")
            return segment.segment_id, False
        duplicate = connection.execute(
            """
            SELECT segment_id FROM meeting_segments
            WHERE project_id = ? AND meeting_id = ? AND segment_digest = ?
            """,
            (project_id, batch.meeting_id, digest),
        ).fetchone()
        if duplicate is not None:
            return duplicate["segment_id"], False
        connection.execute(
            """
            INSERT INTO meeting_segments(
                project_id, segment_id, meeting_id, segment_digest, locator, quote,
                speaker, spoken_at, start_ms, end_ms, context, transcript_confidence,
                mode, mode_confidence, first_batch_id, recorded_event_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                segment.segment_id,
                batch.meeting_id,
                digest,
                segment.locator,
                segment.quote,
                segment.speaker,
                segment.spoken_at,
                segment.start_ms,
                segment.end_ms,
                segment.context,
                segment.transcript_confidence,
                segment.mode,
                segment.mode_confidence,
                batch_id,
                event_id,
                now,
            ),
        )
        return segment.segment_id, True

    def _upsert_item(
        self,
        connection: sqlite3.Connection,
        project_id: str,
        batch: MeetingIngestBatch,
        batch_id: str,
        event_id: str,
        now: str,
        item,
        segment_ids: dict[str, str],
    ) -> tuple[str, bool]:
        evidence_ids = tuple(segment_ids[item_id] for item_id in item.evidence_segment_ids)
        digest = self._stable_digest(
            {
                "meeting_id": batch.meeting_id,
                "classification": item.classification,
                "summary": item.summary,
                "scope": item.scope,
                "date_kind": item.date_kind,
                "decision_actor": item.decision_actor,
                "decision_verb": item.decision_verb,
                "state_difference": item.state_difference,
                "evidence_segment_ids": sorted(evidence_ids),
            }
        )
        existing_id = connection.execute(
            """
            SELECT candidate_digest, meeting_id FROM meeting_interpretation_items
            WHERE project_id = ? AND item_id = ?
            """,
            (project_id, item.item_id),
        ).fetchone()
        if existing_id is not None:
            if existing_id["candidate_digest"] != digest or existing_id["meeting_id"] != batch.meeting_id:
                raise ResourceConflict("同一 item_id 对应了不同解释候选")
            return item.item_id, False
        duplicate = connection.execute(
            """
            SELECT item_id FROM meeting_interpretation_items
            WHERE project_id = ? AND meeting_id = ? AND candidate_digest = ?
            """,
            (project_id, batch.meeting_id, digest),
        ).fetchone()
        if duplicate is not None:
            return duplicate["item_id"], False
        connection.execute(
            """
            INSERT INTO meeting_interpretation_items(
                project_id, item_id, meeting_id, candidate_digest, suggested_type,
                classification, status, summary, scope, date_kind, decision_actor,
                decision_verb, state_difference, confidence, reason, normalization_reason,
                requires_human_confirmation, first_batch_id, recorded_event_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
            """,
            (
                project_id,
                item.item_id,
                batch.meeting_id,
                digest,
                item.suggested_type,
                item.classification,
                item.status,
                item.summary,
                item.scope,
                item.date_kind,
                item.decision_actor,
                item.decision_verb,
                item.state_difference,
                item.confidence,
                item.reason,
                item.normalization_reason,
                batch_id,
                event_id,
                now,
            ),
        )
        connection.executemany(
            """
            INSERT INTO meeting_item_evidence(project_id, item_id, segment_id)
            VALUES (?, ?, ?)
            """,
            ((project_id, item.item_id, segment_id) for segment_id in evidence_ids),
        )
        return item.item_id, True

    def _upsert_conflict(
        self,
        connection: sqlite3.Connection,
        project_id: str,
        batch: MeetingIngestBatch,
        batch_id: str,
        event_id: str,
        now: str,
        conflict,
        item_ids: dict[str, str],
        segment_ids: dict[str, str],
    ) -> tuple[str, bool]:
        item_id = item_ids[conflict.item_id]
        evidence_ids = tuple(segment_ids[value] for value in conflict.evidence_segment_ids)
        state = connection.execute(
            """
            SELECT payload_json, updated_event_id, state_version FROM state_items
            WHERE project_id = ? AND item_type = ? AND item_id = ?
            """,
            (project_id, conflict.state_item_type, conflict.state_item_id),
        ).fetchone()
        if state is None:
            raise ResourceConflict("冲突候选引用的人工确认状态不存在")
        if int(state["state_version"]) > batch.base_state_version:
            raise ResourceConflict("冲突候选引用了基础状态版本之后产生的状态")
        digest = self._stable_digest(
            {
                "meeting_id": batch.meeting_id,
                "item_id": item_id,
                "state_item_type": conflict.state_item_type,
                "state_item_id": conflict.state_item_id,
            }
        )
        existing_id = connection.execute(
            """
            SELECT conflict_digest, meeting_id FROM meeting_conflict_candidates
            WHERE project_id = ? AND conflict_id = ?
            """,
            (project_id, conflict.conflict_id),
        ).fetchone()
        if existing_id is not None:
            if existing_id["conflict_digest"] != digest or existing_id["meeting_id"] != batch.meeting_id:
                raise ResourceConflict("同一 conflict_id 对应了不同冲突")
            return conflict.conflict_id, False
        duplicate = connection.execute(
            """
            SELECT conflict_id FROM meeting_conflict_candidates
            WHERE project_id = ? AND meeting_id = ? AND conflict_digest = ?
            """,
            (project_id, batch.meeting_id, digest),
        ).fetchone()
        if duplicate is not None:
            return duplicate["conflict_id"], False
        connection.execute(
            """
            INSERT INTO meeting_conflict_candidates(
                project_id, conflict_id, meeting_id, item_id, state_item_type,
                state_item_id, conflict_digest, reason, state_payload_json,
                state_updated_event_id, state_version, first_batch_id,
                recorded_event_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                conflict.conflict_id,
                batch.meeting_id,
                item_id,
                conflict.state_item_type,
                conflict.state_item_id,
                digest,
                conflict.reason,
                state["payload_json"],
                state["updated_event_id"],
                state["state_version"],
                batch_id,
                event_id,
                now,
            ),
        )
        connection.executemany(
            """
            INSERT INTO meeting_conflict_evidence(project_id, conflict_id, segment_id)
            VALUES (?, ?, ?)
            """,
            ((project_id, conflict.conflict_id, segment_id) for segment_id in evidence_ids),
        )
        return conflict.conflict_id, True

    def _stable_digest(self, value: object) -> str:
        return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
