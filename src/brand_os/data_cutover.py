"""SQLite 到 PostgreSQL/S3 的一次性导出、对账和权威切换。"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path, PurePath
from typing import Mapping, Sequence

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from .domain import Actor, ActorKind
from .evidence import LocalEvidenceStore
from .hashing import sha256_file
from .object_evidence import (
    CONTENT_OBJECT_PREFIX,
    TEMPORARY_OBJECT_PREFIX,
    EvidenceAdmissionRequest,
    EvidenceAdmissionService,
)
from .postgresql_store import PostgreSQLCanonicalStore
from .sqlite_store import SQLiteCanonicalStore


DATA_CUTOVER_SCHEMA_VERSION = "data-cutover.v1"
DATA_CUTOVER_REPORT_SCHEMA_VERSION = "data-cutover-report.v1"
CUTOVER_ID_PATTERN = re.compile(r"^CUT-[A-Z0-9][A-Z0-9_-]{2,63}$")
SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")

# v1-v6 是 SQLite 与 PostgreSQL 共享的正式领域表。v7 之后的本机运行态不迁移。
FORMAL_TABLES = (
    "projects",
    "events",
    "commands",
    "sources",
    "source_import_batches",
    "source_contents",
    "logical_sources",
    "source_versions",
    "source_aliases",
    "source_version_relations",
    "source_gaps",
    "classification_candidates",
    "proposals",
    "proposal_evidence",
    "relations",
    "human_actions",
    "state_items",
    "proposal_lifecycle",
    "meetings",
    "meeting_ingest_batches",
    "meeting_segments",
    "meeting_interpretation_items",
    "meeting_item_evidence",
    "meeting_conflict_candidates",
    "meeting_conflict_evidence",
    "meeting_batch_segments",
    "meeting_batch_items",
    "meeting_batch_conflicts",
    "meeting_item_proposals",
    "proposal_lifecycle_actions",
    "proposal_supersessions",
)
LOCAL_RUNTIME_TABLES = (
    "runtime_commands",
    "runtime_tasks",
    "runtime_mode_switches",
    "task_packets",
    "agent_runs",
)
MANIFEST_FILENAME = "manifest.json"


class DataCutoverError(RuntimeError):
    """一次性数据切换错误基类。"""


class DataCutoverIntegrityError(DataCutoverError):
    """导出、来源、目标或对象完整性不匹配。"""


class DataCutoverTargetNotEmpty(DataCutoverError):
    """目标数据库已经承载数据，不能执行一次性导入。"""


class DataCutoverPermissionDenied(DataCutoverError):
    """调用方不是获授权的真实员工。"""


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _require_exact_keys(value: Mapping[str, object], expected: set[str], name: str) -> None:
    if set(value) != expected:
        raise DataCutoverIntegrityError(f"{name} 字段不符合冻结契约")


@dataclass(frozen=True, slots=True)
class DataCutoverTable:
    """一个正式表的不可变导出文件摘要。"""

    name: str
    file: str
    columns: tuple[str, ...]
    row_count: int
    sha256: str

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "file": self.file,
            "columns": list(self.columns),
            "row_count": self.row_count,
            "sha256": self.sha256,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "DataCutoverTable":
        _require_exact_keys(
            value,
            {"name", "file", "columns", "row_count", "sha256"},
            "tables[]",
        )
        columns = value["columns"]
        if not isinstance(columns, list) or not columns or not all(
            isinstance(item, str) and item for item in columns
        ):
            raise DataCutoverIntegrityError("导出表 columns 无效")
        item = cls(
            name=str(value["name"]),
            file=str(value["file"]),
            columns=tuple(columns),
            row_count=int(value["row_count"]),
            sha256=str(value["sha256"]),
        )
        if item.name not in FORMAL_TABLES:
            raise DataCutoverIntegrityError(f"导出包含非正式表：{item.name}")
        expected_file = f"tables/{FORMAL_TABLES.index(item.name) + 1:02d}-{item.name}.jsonl"
        if item.file != expected_file or item.row_count < 0:
            raise DataCutoverIntegrityError(f"导出表路径或行数无效：{item.name}")
        if not SHA256_PATTERN.fullmatch(item.sha256):
            raise DataCutoverIntegrityError(f"导出表哈希无效：{item.name}")
        return item


@dataclass(frozen=True, slots=True)
class DataCutoverEvidence:
    """一个 SQLite 来源版本对应的本地内容寻址原件。"""

    project_id: str
    logical_source_id: str
    source_version_id: str
    sha256: str
    size_bytes: int
    media_type: str
    confidentiality: str
    original_filename: str

    def as_dict(self) -> dict[str, object]:
        return {
            "project_id": self.project_id,
            "logical_source_id": self.logical_source_id,
            "source_version_id": self.source_version_id,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "media_type": self.media_type,
            "confidentiality": self.confidentiality,
            "original_filename": self.original_filename,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "DataCutoverEvidence":
        keys = {
            "project_id",
            "logical_source_id",
            "source_version_id",
            "sha256",
            "size_bytes",
            "media_type",
            "confidentiality",
            "original_filename",
        }
        _require_exact_keys(value, keys, "evidence[]")
        item = cls(
            project_id=str(value["project_id"]),
            logical_source_id=str(value["logical_source_id"]),
            source_version_id=str(value["source_version_id"]),
            sha256=str(value["sha256"]),
            size_bytes=int(value["size_bytes"]),
            media_type=str(value["media_type"]),
            confidentiality=str(value["confidentiality"]),
            original_filename=str(value["original_filename"]),
        )
        for field_value, field_name in (
            (item.project_id, "project_id"),
            (item.logical_source_id, "logical_source_id"),
            (item.source_version_id, "source_version_id"),
            (item.media_type, "media_type"),
            (item.original_filename, "original_filename"),
        ):
            if not field_value:
                raise DataCutoverIntegrityError(f"证据 {field_name} 不能为空")
        if not SHA256_PATTERN.fullmatch(item.sha256) or item.size_bytes < 0:
            raise DataCutoverIntegrityError("证据哈希或大小无效")
        if item.confidentiality not in {"P0", "P1", "P2", "P3"}:
            raise DataCutoverIntegrityError("证据保密级别必须是 P0-P3")
        if PurePath(item.original_filename).name != item.original_filename:
            raise DataCutoverIntegrityError("证据文件名不能包含路径")
        return item


@dataclass(frozen=True, slots=True)
class DataCutoverManifest:
    """可独立复算的正式表与证据导出清单。"""

    cutover_id: str
    created_at: str
    source_schema_version: int
    source_snapshot_sha256: str
    tables: tuple[DataCutoverTable, ...]
    evidence: tuple[DataCutoverEvidence, ...]
    excluded_tables: tuple[str, ...]
    manifest_sha256: str
    schema_version: str = DATA_CUTOVER_SCHEMA_VERSION

    def unsigned_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "cutover_id": self.cutover_id,
            "created_at": self.created_at,
            "source_schema_version": self.source_schema_version,
            "source_snapshot_sha256": self.source_snapshot_sha256,
            "tables": [item.as_dict() for item in self.tables],
            "evidence": [item.as_dict() for item in self.evidence],
            "excluded_tables": list(self.excluded_tables),
        }

    def as_dict(self) -> dict[str, object]:
        return {**self.unsigned_dict(), "manifest_sha256": self.manifest_sha256}

    def validate(self) -> None:
        if self.schema_version != DATA_CUTOVER_SCHEMA_VERSION:
            raise DataCutoverIntegrityError("切换清单版本不受支持")
        if not CUTOVER_ID_PATTERN.fullmatch(self.cutover_id):
            raise DataCutoverIntegrityError("cutover_id 格式无效")
        if self.source_schema_version < 7:
            raise DataCutoverIntegrityError("SQLite Schema 版本低于迁移下限")
        if tuple(item.name for item in self.tables) != FORMAL_TABLES:
            raise DataCutoverIntegrityError("正式表清单不完整或顺序不一致")
        if self.excluded_tables != LOCAL_RUNTIME_TABLES:
            raise DataCutoverIntegrityError("本机运行态排除表发生变化")
        expected = _sha256_bytes(_canonical_json(self.unsigned_dict()).encode("utf-8"))
        if expected != self.manifest_sha256:
            raise DataCutoverIntegrityError("切换清单哈希不匹配")
        snapshot = _snapshot_digest(self.tables)
        if snapshot != self.source_snapshot_sha256:
            raise DataCutoverIntegrityError("来源快照摘要与表摘要不一致")

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "DataCutoverManifest":
        keys = {
            "schema_version",
            "cutover_id",
            "created_at",
            "source_schema_version",
            "source_snapshot_sha256",
            "tables",
            "evidence",
            "excluded_tables",
            "manifest_sha256",
        }
        _require_exact_keys(value, keys, "manifest")
        tables_value = value["tables"]
        evidence_value = value["evidence"]
        excluded_value = value["excluded_tables"]
        if not isinstance(tables_value, list) or not all(
            isinstance(item, dict) for item in tables_value
        ):
            raise DataCutoverIntegrityError("manifest.tables 无效")
        if not isinstance(evidence_value, list) or not all(
            isinstance(item, dict) for item in evidence_value
        ):
            raise DataCutoverIntegrityError("manifest.evidence 无效")
        if not isinstance(excluded_value, list) or not all(
            isinstance(item, str) for item in excluded_value
        ):
            raise DataCutoverIntegrityError("manifest.excluded_tables 无效")
        manifest = cls(
            schema_version=str(value["schema_version"]),
            cutover_id=str(value["cutover_id"]),
            created_at=str(value["created_at"]),
            source_schema_version=int(value["source_schema_version"]),
            source_snapshot_sha256=str(value["source_snapshot_sha256"]),
            tables=tuple(DataCutoverTable.from_mapping(item) for item in tables_value),
            evidence=tuple(DataCutoverEvidence.from_mapping(item) for item in evidence_value),
            excluded_tables=tuple(excluded_value),
            manifest_sha256=str(value["manifest_sha256"]),
        )
        manifest.validate()
        return manifest


@dataclass(frozen=True, slots=True)
class DataCutoverEvidenceMapping:
    """来源版本到明确 S3 原件版本的切换映射。"""

    project_id: str
    source_version_id: str
    evidence_version_id: str
    sha256: str
    object_version_id: str


@dataclass(frozen=True, slots=True)
class DataCutoverReport:
    """不包含 DSN、对象键或业务原文的切换结果。"""

    cutover_id: str
    manifest_sha256: str
    source_snapshot_sha256: str
    table_count: int
    row_count: int
    evidence_count: int
    event_count: int
    human_action_count: int
    state_item_count: int
    activated_at: str
    result: str
    replayed: bool = False
    schema_version: str = DATA_CUTOVER_REPORT_SCHEMA_VERSION

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "cutover_id": self.cutover_id,
            "manifest_sha256": self.manifest_sha256,
            "source_snapshot_sha256": self.source_snapshot_sha256,
            "table_count": self.table_count,
            "row_count": self.row_count,
            "evidence_count": self.evidence_count,
            "event_count": self.event_count,
            "human_action_count": self.human_action_count,
            "state_item_count": self.state_item_count,
            "activated_at": self.activated_at,
            "result": self.result,
            "replayed": self.replayed,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "DataCutoverReport":
        return cls(
            schema_version=str(value["schema_version"]),
            cutover_id=str(value["cutover_id"]),
            manifest_sha256=str(value["manifest_sha256"]),
            source_snapshot_sha256=str(value["source_snapshot_sha256"]),
            table_count=int(value["table_count"]),
            row_count=int(value["row_count"]),
            evidence_count=int(value["evidence_count"]),
            event_count=int(value["event_count"]),
            human_action_count=int(value["human_action_count"]),
            state_item_count=int(value["state_item_count"]),
            activated_at=str(value["activated_at"]),
            result=str(value["result"]),
            replayed=bool(value.get("replayed", False)),
        )


def _snapshot_digest(tables: Sequence[DataCutoverTable]) -> str:
    payload = [
        {"name": item.name, "row_count": item.row_count, "sha256": item.sha256}
        for item in tables
    ]
    return _sha256_bytes(_canonical_json(payload).encode("utf-8"))


class DataCutoverService:
    """把冻结的 SQLite 正式领域数据一次性切换到 PostgreSQL 和 S3。"""

    def __init__(
        self,
        *,
        source_database: Path,
        local_evidence: LocalEvidenceStore,
        target_dsn: str,
        evidence_admission: EvidenceAdmissionService,
        export_root: Path,
        allowed_operators: Sequence[str] = ("Fox",),
    ) -> None:
        self.source_database = source_database.expanduser().resolve(strict=True)
        self.local_evidence = local_evidence
        if not target_dsn.strip():
            raise ValueError("PostgreSQL 目标 DSN 不能为空")
        self.target_dsn = target_dsn
        self.evidence_admission = evidence_admission
        self.export_root = export_root.expanduser().resolve(strict=False)
        self.allowed_operators = frozenset(allowed_operators)
        if not self.allowed_operators:
            raise ValueError("至少需要一个获授权切换操作人")
        if self.export_root.is_symlink():
            raise DataCutoverIntegrityError("切换导出目录不能是符号链接")
        self.export_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.export_root.chmod(0o700)

    def run(self, cutover_id: str, actor: Actor) -> DataCutoverReport:
        """准备导出并执行切换；成功重跑只返回原始结果。"""

        self._require_operator(actor)
        active = self._active_report(cutover_id)
        if active is not None:
            manifest = self.load_manifest(cutover_id)
            if active.manifest_sha256 != manifest.manifest_sha256:
                raise DataCutoverIntegrityError("ACTIVE 切换记录与本地清单不一致")
            self._activate_source(manifest)
            return replace(active, replayed=True)
        manifest = self.prepare(cutover_id, actor)
        return self.execute(manifest.cutover_id, actor)

    def prepare(self, cutover_id: str, actor: Actor) -> DataCutoverManifest:
        """先检查空目标，再冻结 SQLite 并写出带哈希的正式数据清单。"""

        self._require_operator(actor)
        self._require_cutover_id(cutover_id)
        PostgreSQLCanonicalStore(self.target_dsn)
        self._require_empty_target()
        SQLiteCanonicalStore(self.source_database)
        status = self.source_cutover_status(cutover_id)
        destination = self.export_directory(cutover_id)
        if status == "PREPARING" and destination.is_dir():
            return self.load_manifest(cutover_id)
        if status in {"ACTIVE", "ABORTED"}:
            raise DataCutoverIntegrityError(f"cutover_id 已处于 {status} 状态")
        if destination.exists() or destination.is_symlink():
            raise DataCutoverIntegrityError("切换导出目录已存在但没有可恢复的冻结记录")

        self._freeze_source(cutover_id, actor.actor_id)
        temporary = Path(tempfile.mkdtemp(prefix="cutover-export-", dir=self.export_root))
        try:
            tables_directory = temporary / "tables"
            tables_directory.mkdir(mode=0o700)
            with self._source_connection(read_only=True) as connection:
                schema_version = int(
                    connection.execute(
                        "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
                    ).fetchone()[0]
                )
                if schema_version < 8:
                    raise DataCutoverIntegrityError("SQLite 尚未安装切换守卫迁移")
                tables = self._export_tables(connection, temporary)
                evidence = self._build_evidence_manifest(connection)
            snapshot_sha256 = _snapshot_digest(tables)
            unsigned = DataCutoverManifest(
                cutover_id=cutover_id,
                created_at=_utc_now(),
                source_schema_version=schema_version,
                source_snapshot_sha256=snapshot_sha256,
                tables=tables,
                evidence=evidence,
                excluded_tables=LOCAL_RUNTIME_TABLES,
                manifest_sha256="",
            )
            manifest = replace(
                unsigned,
                manifest_sha256=_sha256_bytes(
                    _canonical_json(unsigned.unsigned_dict()).encode("utf-8")
                ),
            )
            manifest.validate()
            manifest_path = temporary / MANIFEST_FILENAME
            manifest_path.write_text(
                json.dumps(manifest.as_dict(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            manifest_path.chmod(0o400)
            self._bind_source_manifest(manifest)
            os.replace(temporary, destination)
            destination.chmod(0o700)
            self.source_database.chmod(0o400)
            return manifest
        except Exception as error:
            shutil.rmtree(temporary, ignore_errors=True)
            self._abort_source(cutover_id, str(error))
            raise

    def execute(self, cutover_id: str, actor: Actor) -> DataCutoverReport:
        """校验导出、导入目标、准入原件、双向对账并激活唯一权威。"""

        self._require_operator(actor)
        active = self._active_report(cutover_id)
        if active is not None:
            manifest = self.load_manifest(cutover_id)
            self._activate_source(manifest)
            return replace(active, replayed=True)
        manifest: DataCutoverManifest | None = None
        object_baseline: set[tuple[str, str]] | None = None
        try:
            manifest = self.load_manifest(cutover_id)
            self._require_source_preparing(manifest)
            self._require_empty_target()
            object_baseline = self._object_version_identities()
            table_rows = {
                table.name: self._load_table_rows(manifest, table)
                for table in manifest.tables
            }
            self._import_tables(manifest, table_rows, actor.actor_id)
            self._admit_evidence(manifest)
            reconciliation = self.evidence_admission.reconcile(cleanup=False)
            if reconciliation.issues:
                codes = ",".join(sorted({item.code for item in reconciliation.issues}))
                raise DataCutoverIntegrityError(f"对象对账失败：{codes}")
            self._verify_target(manifest)
            self._verify_source_unchanged(manifest)
            report = self._build_report(manifest)
        except Exception as error:
            self._rollback_before_activation(
                cutover_id,
                manifest,
                str(error),
                object_baseline,
            )
            raise

        self._activate_target(report)
        self._activate_source(manifest)
        return report

    def export_directory(self, cutover_id: str) -> Path:
        """返回单次切换的固定导出目录。"""

        self._require_cutover_id(cutover_id)
        return self.export_root / cutover_id

    def load_manifest(self, cutover_id: str) -> DataCutoverManifest:
        """读取清单并在使用任何表文件前验证自身摘要。"""

        path = self.export_directory(cutover_id) / MANIFEST_FILENAME
        if path.is_symlink() or not path.is_file():
            raise DataCutoverIntegrityError("切换清单不存在或不是普通文件")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise DataCutoverIntegrityError("切换清单无法读取") from error
        if not isinstance(value, dict):
            raise DataCutoverIntegrityError("切换清单根节点必须是对象")
        manifest = DataCutoverManifest.from_mapping(value)
        if manifest.cutover_id != cutover_id:
            raise DataCutoverIntegrityError("切换清单 ID 与目录不一致")
        return manifest

    def source_cutover_status(self, cutover_id: str) -> str | None:
        """读取 SQLite 中一次切换的当前状态。"""

        self._require_cutover_id(cutover_id)
        with self._source_connection(read_only=True) as connection:
            table = connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = 'authority_cutovers'
                """
            ).fetchone()
            if table is None:
                return None
            row = connection.execute(
                "SELECT status FROM authority_cutovers WHERE cutover_id = ?",
                (cutover_id,),
            ).fetchone()
            return None if row is None else str(row[0])

    def list_evidence_mappings(
        self,
        cutover_id: str,
    ) -> tuple[DataCutoverEvidenceMapping, ...]:
        """读取来源版本到明确 S3 VersionId 的切换映射。"""

        self._require_cutover_id(cutover_id)
        with psycopg.connect(self.target_dsn, row_factory=dict_row) as connection:
            rows = connection.execute(
                """
                SELECT mapping.project_id, mapping.source_version_id,
                       mapping.evidence_version_id, mapping.sha256,
                       mapping.object_version_id
                FROM data_cutover_source_evidence AS mapping
                JOIN source_versions AS source_version
                  ON source_version.project_id = mapping.project_id
                 AND source_version.source_version_id = mapping.source_version_id
                WHERE mapping.cutover_id = %s
                ORDER BY mapping.project_id, source_version.created_at,
                         mapping.source_version_id
                """,
                (cutover_id,),
            ).fetchall()
        return tuple(DataCutoverEvidenceMapping(**dict(row)) for row in rows)

    def _require_operator(self, actor: Actor) -> None:
        if actor.kind is not ActorKind.HUMAN or actor.actor_id not in self.allowed_operators:
            raise DataCutoverPermissionDenied("只有获授权的真实员工可以执行权威切换")

    @staticmethod
    def _require_cutover_id(cutover_id: str) -> None:
        if not CUTOVER_ID_PATTERN.fullmatch(cutover_id):
            raise ValueError("cutover_id 格式无效")

    def _source_connection(self, *, read_only: bool) -> sqlite3.Connection:
        if read_only:
            connection = sqlite3.connect(
                f"file:{self.source_database.as_posix()}?mode=ro",
                uri=True,
                timeout=5,
            )
        else:
            connection = sqlite3.connect(self.source_database, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _require_empty_target(self) -> None:
        with psycopg.connect(self.target_dsn, autocommit=True) as connection:
            tables = [
                str(row[0])
                for row in connection.execute(
                    """
                    SELECT table_name FROM information_schema.tables
                    WHERE table_schema = current_schema()
                      AND table_type = 'BASE TABLE'
                      AND table_name NOT IN (
                          'schema_migrations','data_cutover_runs','outbox_consumers'
                      )
                    ORDER BY table_name
                    """
                )
            ]
            nonempty: list[str] = []
            for table in tables:
                count = int(
                    connection.execute(
                        sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(table))
                    ).fetchone()[0]
                )
                if count:
                    nonempty.append(table)
            unexpected_consumers = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM outbox_consumers
                    WHERE consumer_name <> 'default' OR status <> 'ACTIVE'
                    """
                ).fetchone()[0]
            )
            unfinished_cutovers = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM data_cutover_runs
                    WHERE status IN ('PREPARED','ACTIVE')
                    """
                ).fetchone()[0]
            )
        if nonempty or unexpected_consumers or unfinished_cutovers:
            names = ",".join(nonempty) or "outbox_consumers"
            if unfinished_cutovers:
                names = "data_cutover_runs"
            raise DataCutoverTargetNotEmpty(f"PostgreSQL 切换目标不是空库：{names}")

    def _freeze_source(self, cutover_id: str, operator_id: str) -> None:
        self.source_database.chmod(0o600)
        with self._source_connection(read_only=False) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                active = connection.execute(
                    """
                    SELECT cutover_id, status FROM authority_cutovers
                    WHERE status IN ('PREPARING','ACTIVE') LIMIT 1
                    """
                ).fetchone()
                if active is not None:
                    raise DataCutoverIntegrityError(
                        f"SQLite 已由 {active['cutover_id']} 进入 {active['status']} 状态"
                    )
                schema_version = int(
                    connection.execute(
                        "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
                    ).fetchone()[0]
                )
                connection.execute(
                    """
                    INSERT INTO authority_cutovers(
                        cutover_id, source_schema_version, status,
                        operator_id, started_at
                    ) VALUES (?, ?, 'PREPARING', ?, ?)
                    """,
                    (cutover_id, schema_version, operator_id, _utc_now()),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def _bind_source_manifest(self, manifest: DataCutoverManifest) -> None:
        with self._source_connection(read_only=False) as connection:
            updated = connection.execute(
                """
                UPDATE authority_cutovers
                SET manifest_sha256 = ?, source_snapshot_sha256 = ?
                WHERE cutover_id = ? AND status = 'PREPARING'
                  AND manifest_sha256 IS NULL
                """,
                (
                    manifest.manifest_sha256,
                    manifest.source_snapshot_sha256,
                    manifest.cutover_id,
                ),
            )
            if updated.rowcount != 1:
                raise DataCutoverIntegrityError("无法把导出清单绑定到 SQLite 冻结记录")
            connection.commit()

    def _export_tables(
        self,
        connection: sqlite3.Connection,
        root: Path,
    ) -> tuple[DataCutoverTable, ...]:
        results: list[DataCutoverTable] = []
        for index, table in enumerate(FORMAL_TABLES, 1):
            columns = tuple(
                str(row[1])
                for row in connection.execute(f'PRAGMA table_info("{table}")')
            )
            if not columns:
                raise DataCutoverIntegrityError(f"SQLite 缺少正式表：{table}")
            rows = [dict(row) for row in connection.execute(f'SELECT * FROM "{table}"')]
            rows.sort(key=_canonical_json)
            relative = f"tables/{index:02d}-{table}.jsonl"
            path = root / relative
            payload = b"".join(
                _canonical_json(row).encode("utf-8") + b"\n" for row in rows
            )
            path.write_bytes(payload)
            path.chmod(0o400)
            results.append(
                DataCutoverTable(
                    name=table,
                    file=relative,
                    columns=columns,
                    row_count=len(rows),
                    sha256=_sha256_bytes(payload),
                )
            )
        return tuple(results)

    def _build_evidence_manifest(
        self,
        connection: sqlite3.Connection,
    ) -> tuple[DataCutoverEvidence, ...]:
        rows = connection.execute(
            """
            SELECT sv.project_id, sv.logical_source_id, sv.source_version_id,
                   sv.sha256, sv.relative_path, sv.confidentiality,
                   sv.created_at, sc.size_bytes, sc.media_type
            FROM source_versions sv
            JOIN source_contents sc
              ON sc.project_id = sv.project_id AND sc.sha256 = sv.sha256
            ORDER BY sv.project_id, sv.logical_source_id, sv.created_at, sv.source_version_id
            """
        ).fetchall()
        evidence: list[DataCutoverEvidence] = []
        for row in rows:
            confidentiality = row["confidentiality"]
            if confidentiality not in {"P0", "P1", "P2", "P3"}:
                raise DataCutoverIntegrityError(
                    f"来源版本缺少有效保密级别：{row['source_version_id']}"
                )
            try:
                with self.local_evidence.open(str(row["sha256"])) as stream:
                    digest = hashlib.sha256()
                    size = 0
                    while chunk := stream.read(1024 * 1024):
                        digest.update(chunk)
                        size += len(chunk)
            except (OSError, RuntimeError) as error:
                raise DataCutoverIntegrityError(
                    f"本地证据快照缺失或损坏：{row['source_version_id']}"
                ) from error
            if digest.hexdigest() != row["sha256"]:
                raise DataCutoverIntegrityError(
                    f"本地证据哈希不匹配：{row['source_version_id']}"
                )
            declared_size = row["size_bytes"]
            if declared_size is not None and int(declared_size) != size:
                raise DataCutoverIntegrityError(
                    f"本地证据大小不匹配：{row['source_version_id']}"
                )
            filename = PurePath(str(row["relative_path"])).name
            media_type = str(
                row["media_type"]
                or mimetypes.guess_type(filename)[0]
                or "application/octet-stream"
            )
            evidence.append(
                DataCutoverEvidence(
                    project_id=str(row["project_id"]),
                    logical_source_id=str(row["logical_source_id"]),
                    source_version_id=str(row["source_version_id"]),
                    sha256=str(row["sha256"]),
                    size_bytes=size,
                    media_type=media_type,
                    confidentiality=str(confidentiality),
                    original_filename=filename,
                )
            )
        return tuple(evidence)

    def _load_table_rows(
        self,
        manifest: DataCutoverManifest,
        table: DataCutoverTable,
    ) -> tuple[dict[str, object], ...]:
        root = self.export_directory(manifest.cutover_id).resolve(strict=True)
        path = (root / table.file).resolve(strict=True)
        if path.is_symlink() or not path.is_file() or not path.is_relative_to(root):
            raise DataCutoverIntegrityError(f"导出表文件路径越界：{table.name}")
        payload = path.read_bytes()
        if _sha256_bytes(payload) != table.sha256:
            raise DataCutoverIntegrityError(f"导出表文件哈希不匹配：{table.name}")
        rows: list[dict[str, object]] = []
        try:
            for line in payload.splitlines():
                value = json.loads(line)
                if not isinstance(value, dict) or set(value) != set(table.columns):
                    raise DataCutoverIntegrityError(f"导出表行字段不匹配：{table.name}")
                rows.append(value)
        except json.JSONDecodeError as error:
            raise DataCutoverIntegrityError(f"导出表 JSON 损坏：{table.name}") from error
        if len(rows) != table.row_count:
            raise DataCutoverIntegrityError(f"导出表行数不匹配：{table.name}")
        if rows != sorted(rows, key=_canonical_json):
            raise DataCutoverIntegrityError(f"导出表行顺序不是规范顺序：{table.name}")
        return tuple(rows)

    def _import_tables(
        self,
        manifest: DataCutoverManifest,
        table_rows: Mapping[str, Sequence[Mapping[str, object]]],
        operator_id: str,
    ) -> None:
        with psycopg.connect(self.target_dsn, autocommit=True) as connection:
            connection.execute("BEGIN")
            try:
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    ("brand-project-os:data-cutover",),
                )
                for table in manifest.tables:
                    rows = table_rows[table.name]
                    if not rows:
                        continue
                    statement = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                        sql.Identifier(table.name),
                        sql.SQL(", ").join(map(sql.Identifier, table.columns)),
                        sql.SQL(", ").join(sql.Placeholder() for _ in table.columns),
                    )
                    with connection.cursor() as cursor:
                        cursor.executemany(
                            statement,
                            [tuple(row[column] for column in table.columns) for row in rows],
                        )
                event_count = len(table_rows["events"])
                if event_count:
                    connection.execute(
                        """
                        SELECT setval(
                            pg_get_serial_sequence('events', 'global_position'),
                            (SELECT MAX(global_position) FROM events),
                            true
                        )
                        """
                    )
                connection.execute(
                    """
                    INSERT INTO data_cutover_runs(
                        cutover_id, manifest_sha256, source_snapshot_sha256,
                        source_schema_version, status, operator_id, table_count,
                        row_count, evidence_count, started_at
                    ) VALUES (%s, %s, %s, %s, 'PREPARED', %s, %s, %s, %s, %s)
                    """,
                    (
                        manifest.cutover_id,
                        manifest.manifest_sha256,
                        manifest.source_snapshot_sha256,
                        manifest.source_schema_version,
                        operator_id,
                        len(manifest.tables),
                        sum(item.row_count for item in manifest.tables),
                        len(manifest.evidence),
                        _utc_now(),
                    ),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def _admit_evidence(self, manifest: DataCutoverManifest) -> None:
        for item in manifest.evidence:
            upload = self.evidence_admission.begin_upload(
                EvidenceAdmissionRequest(
                    project_id=item.project_id,
                    logical_source_id=item.logical_source_id,
                    original_filename=item.original_filename,
                    expected_sha256=item.sha256,
                    expected_size_bytes=item.size_bytes,
                    expected_media_type=item.media_type,
                    confidentiality=item.confidentiality,
                    idempotency_key=f"cutover:{manifest.cutover_id}:{item.source_version_id}",
                )
            )
            with self.local_evidence.open(item.sha256) as stream:
                self.evidence_admission.upload_and_quarantine(upload.upload_id, stream)
            version = self.evidence_admission.verify_and_activate(
                upload.upload_id,
                detected_media_type=item.media_type,
                security_scan_passed=True,
            )
            if (
                version.sha256 != item.sha256
                or version.size_bytes != item.size_bytes
                or not version.object_version_id
            ):
                raise DataCutoverIntegrityError(
                    f"S3 准入结果与来源版本不一致：{item.source_version_id}"
                )
            with psycopg.connect(self.target_dsn) as connection:
                connection.execute(
                    """
                    INSERT INTO data_cutover_source_evidence(
                        cutover_id, project_id, source_version_id,
                        evidence_version_id, sha256, object_version_id, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        manifest.cutover_id,
                        item.project_id,
                        item.source_version_id,
                        version.version_id,
                        version.sha256,
                        version.object_version_id,
                        _utc_now(),
                    ),
                )

    def _verify_target(self, manifest: DataCutoverManifest) -> None:
        with psycopg.connect(self.target_dsn, row_factory=dict_row) as connection:
            for table in manifest.tables:
                rows = [
                    dict(row)
                    for row in connection.execute(
                        sql.SQL("SELECT * FROM {}").format(sql.Identifier(table.name))
                    )
                ]
                rows.sort(key=_canonical_json)
                payload = b"".join(
                    _canonical_json(row).encode("utf-8") + b"\n" for row in rows
                )
                if len(rows) != table.row_count or _sha256_bytes(payload) != table.sha256:
                    raise DataCutoverIntegrityError(
                        f"PostgreSQL 表对账失败：{table.name}"
                    )
            mapping_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM data_cutover_source_evidence
                    WHERE cutover_id = %s
                    """,
                    (manifest.cutover_id,),
                ).fetchone()["count"]
            )
        if mapping_count != len(manifest.evidence):
            raise DataCutoverIntegrityError("来源版本与 S3 原件版本映射数量不一致")

    def _verify_source_unchanged(self, manifest: DataCutoverManifest) -> None:
        with self._source_connection(read_only=True) as connection:
            current: list[DataCutoverTable] = []
            for table in manifest.tables:
                rows = [dict(row) for row in connection.execute(f'SELECT * FROM "{table.name}"')]
                rows.sort(key=_canonical_json)
                payload = b"".join(
                    _canonical_json(row).encode("utf-8") + b"\n" for row in rows
                )
                current.append(
                    DataCutoverTable(
                        table.name,
                        table.file,
                        table.columns,
                        len(rows),
                        _sha256_bytes(payload),
                    )
                )
        if _snapshot_digest(current) != manifest.source_snapshot_sha256:
            raise DataCutoverIntegrityError("SQLite 正式数据在冻结后发生变化")

    def _build_report(self, manifest: DataCutoverManifest) -> DataCutoverReport:
        counts = {item.name: item.row_count for item in manifest.tables}
        return DataCutoverReport(
            cutover_id=manifest.cutover_id,
            manifest_sha256=manifest.manifest_sha256,
            source_snapshot_sha256=manifest.source_snapshot_sha256,
            table_count=len(manifest.tables),
            row_count=sum(counts.values()),
            evidence_count=len(manifest.evidence),
            event_count=counts["events"],
            human_action_count=counts["human_actions"],
            state_item_count=counts["state_items"],
            activated_at=_utc_now(),
            result="activated",
        )

    def _activate_target(self, report: DataCutoverReport) -> None:
        with psycopg.connect(self.target_dsn) as connection:
            updated = connection.execute(
                """
                UPDATE data_cutover_runs
                SET status = 'ACTIVE', report_json = %s, activated_at = %s
                WHERE cutover_id = %s AND status = 'PREPARED'
                """,
                (
                    _canonical_json(report.as_dict()),
                    report.activated_at,
                    report.cutover_id,
                ),
            )
            if updated.rowcount != 1:
                raise DataCutoverIntegrityError("无法激活 PostgreSQL 权威切换记录")

    def _activate_source(self, manifest: DataCutoverManifest) -> None:
        self.source_database.chmod(0o600)
        with self._source_connection(read_only=False) as connection:
            row = connection.execute(
                """
                SELECT status, manifest_sha256, source_snapshot_sha256
                FROM authority_cutovers WHERE cutover_id = ?
                """,
                (manifest.cutover_id,),
            ).fetchone()
            if row is None:
                raise DataCutoverIntegrityError("SQLite 缺少切换记录")
            if (
                row["manifest_sha256"] != manifest.manifest_sha256
                or row["source_snapshot_sha256"] != manifest.source_snapshot_sha256
            ):
                raise DataCutoverIntegrityError("SQLite 切换记录与清单不一致")
            if row["status"] == "ACTIVE":
                self.source_database.chmod(0o400)
                return
            updated = connection.execute(
                """
                UPDATE authority_cutovers
                SET status = 'ACTIVE', completed_at = ?
                WHERE cutover_id = ? AND status = 'PREPARING'
                """,
                (_utc_now(), manifest.cutover_id),
            )
            if updated.rowcount != 1:
                raise DataCutoverIntegrityError("无法把 SQLite 固定为只读来源")
            connection.commit()
        self.source_database.chmod(0o400)

    def _require_source_preparing(self, manifest: DataCutoverManifest) -> None:
        with self._source_connection(read_only=True) as connection:
            row = connection.execute(
                """
                SELECT status, manifest_sha256, source_snapshot_sha256
                FROM authority_cutovers WHERE cutover_id = ?
                """,
                (manifest.cutover_id,),
            ).fetchone()
        if row is None or row["status"] != "PREPARING":
            raise DataCutoverIntegrityError("SQLite 不在对应切换的 PREPARING 状态")
        if (
            row["manifest_sha256"] != manifest.manifest_sha256
            or row["source_snapshot_sha256"] != manifest.source_snapshot_sha256
        ):
            raise DataCutoverIntegrityError("SQLite 冻结记录与导出清单不一致")

    def _active_report(self, cutover_id: str) -> DataCutoverReport | None:
        self._require_cutover_id(cutover_id)
        PostgreSQLCanonicalStore(self.target_dsn)
        with psycopg.connect(self.target_dsn, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                SELECT report_json FROM data_cutover_runs
                WHERE cutover_id = %s AND status = 'ACTIVE'
                """,
                (cutover_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            value = json.loads(str(row["report_json"]))
        except json.JSONDecodeError as error:
            raise DataCutoverIntegrityError("ACTIVE 切换报告损坏") from error
        if not isinstance(value, dict):
            raise DataCutoverIntegrityError("ACTIVE 切换报告格式无效")
        return DataCutoverReport.from_mapping(value)

    def _rollback_before_activation(
        self,
        cutover_id: str,
        manifest: DataCutoverManifest | None,
        reason: str,
        object_baseline: set[tuple[str, str]] | None,
    ) -> None:
        object_versions: set[tuple[str, str]] = set()
        temporary_versions: set[tuple[str, str]] = set()
        upload_ids: set[str] = set()
        temporary_keys: set[str] = set()
        try:
            with psycopg.connect(self.target_dsn, row_factory=dict_row) as connection:
                uploads = connection.execute(
                    """
                    SELECT upload_id, temporary_object_key, temporary_object_version_id,
                           final_object_key, final_object_version_id
                    FROM evidence_uploads
                    WHERE idempotency_key LIKE %s
                    """,
                    (f"cutover:{cutover_id}:%",),
                ).fetchall()
                for row in uploads:
                    upload_ids.add(str(row["upload_id"]))
                    temporary_keys.add(str(row["temporary_object_key"]))
                    if row["temporary_object_version_id"]:
                        temporary_versions.add(
                            (
                                str(row["temporary_object_key"]),
                                str(row["temporary_object_version_id"]),
                            )
                        )
                    if row["final_object_key"] and row["final_object_version_id"]:
                        object_versions.add(
                            (
                                str(row["final_object_key"]),
                                str(row["final_object_version_id"]),
                            )
                        )
        except psycopg.Error:
            pass
        if object_baseline is not None:
            relevant_content_keys = {
                f"{CONTENT_OBJECT_PREFIX}{item.sha256[:2]}/{item.sha256}"
                for item in (manifest.evidence if manifest is not None else ())
            }
            for prefix in (TEMPORARY_OBJECT_PREFIX, CONTENT_OBJECT_PREFIX):
                try:
                    current = self.evidence_admission.objects.list_objects(prefix)
                except Exception:
                    continue
                for info in current:
                    identity = (info.key, info.version_id)
                    if identity in object_baseline:
                        continue
                    if (
                        info.metadata.get("upload-id") in upload_ids
                        or info.key in temporary_keys
                        or info.key in relevant_content_keys
                    ):
                        object_versions.add(identity)
        for key, version_id in temporary_versions | object_versions:
            try:
                self.evidence_admission.objects.delete(key, version_id=version_id)
            except Exception:
                continue
        try:
            with psycopg.connect(self.target_dsn, autocommit=True) as connection:
                connection.execute("BEGIN")
                connection.execute(
                    "TRUNCATE TABLE projects, evidence_reconciliation_runs RESTART IDENTITY CASCADE"
                )
                source_snapshot = (
                    manifest.source_snapshot_sha256 if manifest is not None else "0" * 64
                )
                source_schema = manifest.source_schema_version if manifest is not None else 7
                manifest_sha = manifest.manifest_sha256 if manifest is not None else "0" * 64
                table_count = len(manifest.tables) if manifest is not None else 1
                row_count = (
                    sum(item.row_count for item in manifest.tables) if manifest is not None else 0
                )
                evidence_count = len(manifest.evidence) if manifest is not None else 0
                connection.execute(
                    """
                    INSERT INTO data_cutover_runs(
                        cutover_id, manifest_sha256, source_snapshot_sha256,
                        source_schema_version, status, operator_id, table_count,
                        row_count, evidence_count, started_at, rolled_back_at,
                        failure_reason
                    ) VALUES (%s, %s, %s, %s, 'ROLLED_BACK', 'system', %s, %s, %s, %s, %s, %s)
                    ON CONFLICT(cutover_id) DO UPDATE SET
                        status = 'ROLLED_BACK', rolled_back_at = excluded.rolled_back_at,
                        failure_reason = excluded.failure_reason
                    """,
                    (
                        cutover_id,
                        manifest_sha,
                        source_snapshot,
                        source_schema,
                        table_count,
                        row_count,
                        evidence_count,
                        _utc_now(),
                        _utc_now(),
                        reason[:1000],
                    ),
                )
                connection.execute("COMMIT")
        finally:
            self._abort_source(cutover_id, reason)

    def _object_version_identities(self) -> set[tuple[str, str]]:
        """记录切换前对象版本，回滚时只删除本次新增版本。"""

        identities: set[tuple[str, str]] = set()
        for prefix in (TEMPORARY_OBJECT_PREFIX, CONTENT_OBJECT_PREFIX):
            identities.update(
                (item.key, item.version_id)
                for item in self.evidence_admission.objects.list_objects(prefix)
            )
        return identities

    def _abort_source(self, cutover_id: str, reason: str) -> None:
        try:
            self.source_database.chmod(0o600)
            with self._source_connection(read_only=False) as connection:
                connection.execute(
                    """
                    UPDATE authority_cutovers
                    SET status = 'ABORTED', completed_at = ?, failure_reason = ?
                    WHERE cutover_id = ? AND status = 'PREPARING'
                    """,
                    (_utc_now(), reason[:1000], cutover_id),
                )
                connection.commit()
        finally:
            self.source_database.chmod(0o600)


__all__ = [
    "DATA_CUTOVER_REPORT_SCHEMA_VERSION",
    "DATA_CUTOVER_SCHEMA_VERSION",
    "DataCutoverError",
    "DataCutoverEvidence",
    "DataCutoverEvidenceMapping",
    "DataCutoverIntegrityError",
    "DataCutoverManifest",
    "DataCutoverPermissionDenied",
    "DataCutoverReport",
    "DataCutoverService",
    "DataCutoverTable",
    "DataCutoverTargetNotEmpty",
    "FORMAL_TABLES",
    "LOCAL_RUNTIME_TABLES",
]
