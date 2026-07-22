"""SQLite 权威库的领域写命令。"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict

from .domain import (
    ActorKind,
    ClassificationCandidate,
    CommandContext,
    CommandResult,
    RelationDraft,
    SourceRecord,
    legacy_source_version_id,
)
from .sqlite_base import (
    BusinessPermissionDenied,
    ResourceConflict,
    SQLiteStoreBase,
    VersionConflict,
    utc_now,
)


class SQLiteCommandMixin(SQLiteStoreBase):
    """实现项目、来源、候选和工作层关系写入。"""

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
            self._begin_command_transaction(connection, context, "create_project")
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
            connection.execute(
                """
                INSERT INTO source_contents(
                    project_id, sha256, size_bytes, media_type, first_batch_id, created_at
                ) VALUES (?, ?, ?, NULL, NULL, ?)
                """,
                (context.project_id, source.sha256, source.size, utc_now()),
            )
            connection.execute(
                """
                INSERT INTO logical_sources(
                    project_id, logical_source_id, source_role, confidentiality,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    context.project_id,
                    source.source_id,
                    source.source_role,
                    source.confidentiality,
                    source.status,
                    utc_now(),
                    utc_now(),
                ),
            )
            connection.execute(
                """
                INSERT INTO source_versions(
                    project_id, source_version_id, logical_source_id, sha256, relative_path,
                    source_role, confidentiality, status, version_label, observed_at,
                    import_batch_id, registered_event_id, is_current, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL, ?, 1, ?)
                """,
                (
                    context.project_id,
                    legacy_source_version_id(source.source_id, source.sha256),
                    source.source_id,
                    source.sha256,
                    source.relative_path,
                    source.source_role,
                    source.confidentiality,
                    source.status,
                    utc_now(),
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
