"""SQLite 来源 Manifest 幂等导入与对账。"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict

from .domain import ActorKind, CommandContext, CommandResult, SourceImportBatch, imported_source_version_id
from .sqlite_base import (
    BusinessPermissionDenied,
    ProjectNotFound,
    ResourceConflict,
    SQLiteStoreBase,
    VersionConflict,
    utc_now,
)


class SQLiteImportMixin(SQLiteStoreBase):
    """导入来源版本、旧 ID、替代关系和已知缺口。"""

    def import_source_batch(
        self, context: CommandContext, batch: SourceImportBatch
    ) -> CommandResult:
        """整批导入标准化 Manifest；失败时不保留部分结果。"""

        if context.actor.kind not in {ActorKind.HUMAN, ActorKind.SYSTEM}:
            raise BusinessPermissionDenied("只有人或本地导入系统可以导入来源 Manifest")
        request = {"batch": asdict(batch), "expected_version": context.expected_version}
        request_hash = self._request_hash(request)
        batch_id = f"IMPORT-{batch.import_digest[:16].upper()}"
        connection = self._connect()
        begun = False
        try:
            connection.execute("BEGIN IMMEDIATE")
            begun = True
            existing_command = self._find_command(connection, context, "import_source_batch")
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
            if context.expected_version != current_version:
                raise VersionConflict(context.expected_version, current_version)

            existing_batch = connection.execute(
                """
                SELECT batch_id, imported_event_id FROM source_import_batches
                WHERE project_id = ? AND import_digest = ?
                """,
                (context.project_id, batch.import_digest),
            ).fetchone()
            if existing_batch is not None:
                result = CommandResult(
                    current_version,
                    existing_batch["imported_event_id"],
                    existing_batch["batch_id"],
                    replayed=True,
                )
                self._record_command(
                    connection, context, "import_source_batch", request_hash, result
                )
                connection.execute("COMMIT")
                return result

            next_version = current_version + 1
            event_id = self._append_event(
                connection,
                context,
                next_version,
                "source_import",
                batch_id,
                "SOURCE_IMPORT_COMPLETED",
                {
                    "batch_id": batch_id,
                    "manifest_sha256": batch.manifest_sha256,
                    "import_digest": batch.import_digest,
                    "manifest_schema_version": batch.manifest_schema_version,
                    "input_record_count": len(batch.records),
                    "input_gap_count": len(batch.gaps),
                },
            )
            now = utc_now()
            connection.execute(
                """
                INSERT INTO source_import_batches(
                    project_id, batch_id, manifest_sha256, import_digest,
                    manifest_schema_version, origin_ref, snapshot_at,
                    input_record_count, input_gap_count, imported_event_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    context.project_id,
                    batch_id,
                    batch.manifest_sha256,
                    batch.import_digest,
                    batch.manifest_schema_version,
                    batch.origin_ref,
                    batch.snapshot_at,
                    len(batch.records),
                    len(batch.gaps),
                    event_id,
                    now,
                ),
            )

            counts = {
                "new_logical_source_count": 0,
                "new_content_count": 0,
                "enriched_content_count": 0,
                "new_version_count": 0,
                "duplicate_record_count": 0,
                "new_alias_count": 0,
                "updated_alias_count": 0,
                "new_supersession_count": 0,
                "gap_observation_count": 0,
            }
            for record in batch.records:
                self._import_record(connection, context.project_id, batch_id, event_id, record, counts)
            for gap in batch.gaps:
                connection.execute(
                    """
                    INSERT INTO source_gaps(
                        project_id, gap_id, import_batch_id, status, description,
                        scope, evidence_ref, observed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        context.project_id,
                        gap.gap_id,
                        batch_id,
                        gap.status,
                        gap.description,
                        gap.scope,
                        gap.evidence_ref,
                        now,
                    ),
                )
                counts["gap_observation_count"] += 1

            connection.execute(
                """
                UPDATE source_import_batches SET
                    new_logical_source_count = ?, new_content_count = ?,
                    enriched_content_count = ?, new_version_count = ?,
                    duplicate_record_count = ?, new_alias_count = ?,
                    updated_alias_count = ?, new_supersession_count = ?,
                    gap_observation_count = ?
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
            self._record_command(connection, context, "import_source_batch", request_hash, result)
            connection.execute("COMMIT")
            return result
        except Exception:
            if begun:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def _import_record(
        self,
        connection: sqlite3.Connection,
        project_id: str,
        batch_id: str,
        event_id: str,
        record,
        counts: dict[str, int],
    ) -> None:
        """在当前批次事务内导入一条来源记录。"""

        now = utc_now()
        content = connection.execute(
            "SELECT size_bytes, media_type FROM source_contents WHERE project_id = ? AND sha256 = ?",
            (project_id, record.sha256),
        ).fetchone()
        if content is None:
            connection.execute(
                """
                INSERT INTO source_contents(
                    project_id, sha256, size_bytes, media_type, first_batch_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (project_id, record.sha256, record.size_bytes, record.media_type, batch_id, now),
            )
            counts["new_content_count"] += 1
        else:
            self._check_content_metadata(content, record)
            size_bytes = content["size_bytes"] if content["size_bytes"] is not None else record.size_bytes
            media_type = content["media_type"] if content["media_type"] is not None else record.media_type
            if size_bytes != content["size_bytes"] or media_type != content["media_type"]:
                connection.execute(
                    """
                    UPDATE source_contents SET size_bytes = ?, media_type = ?
                    WHERE project_id = ? AND sha256 = ?
                    """,
                    (size_bytes, media_type, project_id, record.sha256),
                )
                counts["enriched_content_count"] += 1

        logical = connection.execute(
            """
            SELECT logical_source_id FROM logical_sources
            WHERE project_id = ? AND logical_source_id = ?
            """,
            (project_id, record.logical_source_id),
        ).fetchone()
        if logical is None:
            connection.execute(
                """
                INSERT INTO logical_sources(
                    project_id, logical_source_id, source_role, confidentiality,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    record.logical_source_id,
                    record.source_role,
                    record.confidentiality,
                    record.status,
                    now,
                    now,
                ),
            )
            counts["new_logical_source_count"] += 1
        else:
            connection.execute(
                """
                UPDATE logical_sources SET source_role = ?, confidentiality = ?, status = ?, updated_at = ?
                WHERE project_id = ? AND logical_source_id = ?
                """,
                (
                    record.source_role,
                    record.confidentiality,
                    record.status,
                    now,
                    project_id,
                    record.logical_source_id,
                ),
            )

        existing_version = connection.execute(
            """
            SELECT source_version_id FROM source_versions
            WHERE project_id = ? AND logical_source_id = ? AND sha256 = ?
            """,
            (project_id, record.logical_source_id, record.sha256),
        ).fetchone()
        if existing_version is None:
            current = connection.execute(
                """
                SELECT source_version_id FROM source_versions
                WHERE project_id = ? AND logical_source_id = ? AND is_current = 1
                """,
                (project_id, record.logical_source_id),
            ).fetchone()
            self._validate_supersedes(connection, project_id, record)
            if current is not None:
                connection.execute(
                    """
                    UPDATE source_versions SET is_current = 0
                    WHERE project_id = ? AND source_version_id = ?
                    """,
                    (project_id, current["source_version_id"]),
                )
            version_id = imported_source_version_id(record.logical_source_id, record.sha256)
            connection.execute(
                """
                INSERT INTO source_versions(
                    project_id, source_version_id, logical_source_id, sha256, relative_path,
                    source_role, confidentiality, status, version_label, observed_at,
                    import_batch_id, registered_event_id, is_current, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    project_id,
                    version_id,
                    record.logical_source_id,
                    record.sha256,
                    record.relative_path,
                    record.source_role,
                    record.confidentiality,
                    record.status,
                    record.version_label,
                    now,
                    batch_id,
                    event_id,
                    now,
                ),
            )
            counts["new_version_count"] += 1
            predecessors = set(record.supersedes_sha256)
            if current is not None:
                predecessor = connection.execute(
                    """
                    SELECT sha256 FROM source_versions
                    WHERE project_id = ? AND source_version_id = ?
                    """,
                    (project_id, current["source_version_id"]),
                ).fetchone()
                predecessors.add(predecessor["sha256"])
            for predecessor_sha256 in sorted(predecessors):
                predecessor = connection.execute(
                    """
                    SELECT source_version_id FROM source_versions
                    WHERE project_id = ? AND logical_source_id = ? AND sha256 = ?
                    """,
                    (project_id, record.logical_source_id, predecessor_sha256),
                ).fetchone()
                if predecessor["source_version_id"] == version_id:
                    continue
                inserted = connection.execute(
                    """
                    INSERT OR IGNORE INTO source_version_relations(
                        project_id, predecessor_version_id, successor_version_id,
                        relation_type, import_batch_id, created_at
                    ) VALUES (?, ?, ?, 'supersedes', ?, ?)
                    """,
                    (project_id, predecessor["source_version_id"], version_id, batch_id, now),
                )
                counts["new_supersession_count"] += max(inserted.rowcount, 0)
        else:
            counts["duplicate_record_count"] += 1

        for alias in record.aliases:
            self._upsert_alias(connection, project_id, batch_id, record.logical_source_id, alias, counts)

    def _check_content_metadata(self, content: sqlite3.Row, record) -> None:
        if (
            content["size_bytes"] is not None
            and record.size_bytes is not None
            and content["size_bytes"] != record.size_bytes
        ):
            raise ResourceConflict("同一内容哈希对应了不同文件大小")
        if (
            content["media_type"] is not None
            and record.media_type is not None
            and content["media_type"] != record.media_type
        ):
            raise ResourceConflict("同一内容哈希对应了不同媒体类型")

    def _validate_supersedes(self, connection: sqlite3.Connection, project_id: str, record) -> None:
        for sha256 in record.supersedes_sha256:
            predecessor = connection.execute(
                """
                SELECT 1 FROM source_versions
                WHERE project_id = ? AND logical_source_id = ? AND sha256 = ?
                """,
                (project_id, record.logical_source_id, sha256),
            ).fetchone()
            if predecessor is None:
                raise ResourceConflict("supersedes_sha256 引用的前序版本不存在")

    def _upsert_alias(
        self,
        connection: sqlite3.Connection,
        project_id: str,
        batch_id: str,
        logical_source_id: str,
        alias,
        counts: dict[str, int],
    ) -> None:
        existing = connection.execute(
            """
            SELECT logical_source_id, alias_kind, status FROM source_aliases
            WHERE project_id = ? AND alias_id = ?
            """,
            (project_id, alias.alias_id),
        ).fetchone()
        now = utc_now()
        if existing is None:
            connection.execute(
                """
                INSERT INTO source_aliases(
                    project_id, alias_id, logical_source_id, alias_kind, status,
                    first_batch_id, last_batch_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    alias.alias_id,
                    logical_source_id,
                    alias.alias_kind,
                    alias.status,
                    batch_id,
                    batch_id,
                    now,
                    now,
                ),
            )
            counts["new_alias_count"] += 1
            return
        if existing["logical_source_id"] != logical_source_id:
            raise ResourceConflict("同一旧 ID 不能映射到两个逻辑来源")
        if existing["alias_kind"] != alias.alias_kind:
            raise ResourceConflict("同一旧 ID 的类型不能静默改变")
        if existing["status"] != alias.status:
            connection.execute(
                """
                UPDATE source_aliases SET status = ?, last_batch_id = ?, updated_at = ?
                WHERE project_id = ? AND alias_id = ?
                """,
                (alias.status, batch_id, now, project_id, alias.alias_id),
            )
            counts["updated_alias_count"] += 1
