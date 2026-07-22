"""使用 SQLite 在线备份 API 创建和恢复权威库快照。"""

from __future__ import annotations

import json
import hashlib
import os
import shutil
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .backup import BACKUP_ID_PATTERN, BackupError
from .hashing import sha256_file
from .workspace import WorkspaceLayout


class SQLiteBackupService:
    """在线备份 SQLite，清单只记录可复算的数据库状态。"""

    def __init__(self, layout: WorkspaceLayout, database_path: Path) -> None:
        self.layout = layout
        self.database_path = database_path.expanduser().resolve(strict=True)

    def create(self) -> str:
        """通过 SQLite backup API 生成一致快照并原子提交。"""

        backup_id = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:12]}"
        destination = self.layout.backups / backup_id
        temporary = Path(tempfile.mkdtemp(prefix="sqlite-backup-", dir=self.layout.backups))
        backup_database = temporary / "project.db"
        try:
            with sqlite3.connect(self.database_path) as source, sqlite3.connect(backup_database) as target:
                source.backup(target)
            metadata = self._database_metadata(backup_database)
            digest, size = sha256_file(backup_database)
            manifest = {
                "schema_version": "sqlite-backup.v6",
                "backup_id": backup_id,
                "created_at": datetime.now(UTC).isoformat(),
                "database_sha256": digest,
                "database_size": size,
                **metadata,
            }
            (temporary / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            backup_database.chmod(0o600)
            os.replace(temporary, destination)
            return backup_id
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise

    def restore(self, backup_id: str, destination: Path) -> Path:
        """校验快照后通过 SQLite backup API 恢复到新数据库。"""

        if not BACKUP_ID_PATTERN.fullmatch(backup_id):
            raise BackupError("备份 ID 格式无效")
        source_directory = self.layout.backups / backup_id
        source_database = source_directory / "project.db"
        manifest_path = source_directory / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BackupError("SQLite 备份清单缺失或损坏") from exc
        backup_schema = manifest.get("schema_version")
        if backup_schema not in {
            "sqlite-backup.v1",
            "sqlite-backup.v2",
            "sqlite-backup.v3",
            "sqlite-backup.v4",
            "sqlite-backup.v5",
            "sqlite-backup.v6",
        } or manifest.get("backup_id") != backup_id:
            raise BackupError("SQLite 备份版本或 ID 不匹配")
        digest, size = sha256_file(source_database)
        if digest != manifest.get("database_sha256") or size != manifest.get("database_size"):
            raise BackupError("SQLite 备份文件哈希不匹配")
        if self._comparable_metadata(source_database, manifest) != self._manifest_metadata(manifest):
            raise BackupError("SQLite 备份内容与清单不一致")

        expanded_destination = destination.expanduser()
        if expanded_destination.is_symlink():
            raise BackupError("恢复目标不能是符号链接")
        destination = expanded_destination.resolve(strict=False)
        if destination.exists() or destination.is_symlink():
            raise BackupError("恢复目标必须尚不存在")
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="sqlite-restore-", suffix=".db", dir=destination.parent
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            with sqlite3.connect(source_database) as source, sqlite3.connect(temporary) as target:
                source.backup(target)
            if self._comparable_metadata(temporary, manifest) != self._manifest_metadata(manifest):
                raise BackupError("SQLite 恢复结果与备份清单不一致")
            temporary.chmod(0o600)
            os.replace(temporary, destination)
            return destination
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    def _database_metadata(self, database_path: Path) -> dict[str, object]:
        """读取可用于备份恢复对账的最小状态。"""

        uri = f"file:{database_path.as_posix()}?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise BackupError("SQLite 快照未通过 quick_check")
            schema_version = int(
                connection.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()[0]
            )
            event_count = int(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0])
            proposal_count = int(connection.execute("SELECT COUNT(*) FROM proposals").fetchone()[0])
            human_action_count = int(connection.execute("SELECT COUNT(*) FROM human_actions").fetchone()[0])
            source_import_batch_count = 0
            logical_source_count = 0
            source_version_count = 0
            source_gap_count = 0
            if schema_version >= 3:
                source_import_batch_count = int(
                    connection.execute("SELECT COUNT(*) FROM source_import_batches").fetchone()[0]
                )
                logical_source_count = int(
                    connection.execute("SELECT COUNT(*) FROM logical_sources").fetchone()[0]
                )
                source_version_count = int(
                    connection.execute("SELECT COUNT(*) FROM source_versions").fetchone()[0]
                )
                source_gap_count = int(
                    connection.execute("SELECT COUNT(*) FROM source_gaps").fetchone()[0]
                )
            meeting_ingest_batch_count = 0
            meeting_count = 0
            meeting_segment_count = 0
            meeting_item_count = 0
            meeting_conflict_count = 0
            if schema_version >= 4:
                meeting_ingest_batch_count = int(
                    connection.execute("SELECT COUNT(*) FROM meeting_ingest_batches").fetchone()[0]
                )
                meeting_count = int(
                    connection.execute("SELECT COUNT(*) FROM meetings").fetchone()[0]
                )
                meeting_segment_count = int(
                    connection.execute("SELECT COUNT(*) FROM meeting_segments").fetchone()[0]
                )
                meeting_item_count = int(
                    connection.execute("SELECT COUNT(*) FROM meeting_interpretation_items").fetchone()[0]
                )
                meeting_conflict_count = int(
                    connection.execute("SELECT COUNT(*) FROM meeting_conflict_candidates").fetchone()[0]
                )
            project_versions = [
                {"project_id": row[0], "version": row[1]}
                for row in connection.execute("SELECT project_id, version FROM projects ORDER BY project_id")
            ]
            state_columns = """
                project_id, item_type, item_id, payload_json, source_proposal_id,
                updated_event_id, state_version
            """
            if schema_version >= 6:
                state_columns += ", valid_from, valid_until"
            state_rows = [
                list(row)
                for row in connection.execute(
                    f"""
                    SELECT {state_columns}
                    FROM state_items ORDER BY project_id, item_type, item_id
                    """
                )
            ]
            state_digest = hashlib.sha256(
                json.dumps(state_rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            source_rows = []
            if schema_version >= 3:
                source_rows = [
                    list(row)
                    for row in connection.execute(
                        """
                        SELECT project_id, logical_source_id, sha256, relative_path,
                               status, version_label, is_current
                        FROM source_versions
                        ORDER BY project_id, logical_source_id, created_at, source_version_id
                        """
                    )
                ]
            source_digest = hashlib.sha256(
                json.dumps(source_rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            meeting_rows = {}
            if schema_version >= 4:
                meeting_rows = {
                    table: [
                        list(row)
                        for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid")
                    ]
                    for table in (
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
                    )
                }
            meeting_digest = hashlib.sha256(
                json.dumps(
                    meeting_rows,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            proposal_lifecycle_count = 0
            proposal_lifecycle_action_count = 0
            proposal_reopen_count = 0
            proposal_supersession_count = 0
            meeting_item_proposal_count = 0
            proposal_rows = {}
            if schema_version >= 5:
                proposal_lifecycle_count = int(
                    connection.execute("SELECT COUNT(*) FROM proposal_lifecycle").fetchone()[0]
                )
                proposal_lifecycle_action_count = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM proposal_lifecycle_actions"
                    ).fetchone()[0]
                )
                proposal_reopen_count = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM proposal_lifecycle_actions WHERE action = 'reopen'"
                    ).fetchone()[0]
                )
                proposal_supersession_count = int(
                    connection.execute("SELECT COUNT(*) FROM proposal_supersessions").fetchone()[0]
                )
                meeting_item_proposal_count = int(
                    connection.execute("SELECT COUNT(*) FROM meeting_item_proposals").fetchone()[0]
                )
                proposal_rows = {
                    table: [
                        list(row)
                        for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid")
                    ]
                    for table in (
                        "proposals",
                        "proposal_evidence",
                        "human_actions",
                        "proposal_lifecycle",
                        "meeting_item_proposals",
                        "proposal_lifecycle_actions",
                        "proposal_supersessions",
                    )
                }
            proposal_digest = hashlib.sha256(
                json.dumps(
                    proposal_rows,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            runtime_task_count = 0
            runtime_mode_switch_count = 0
            task_packet_count = 0
            agent_run_count = 0
            runtime_rows = {}
            if schema_version >= 7:
                runtime_task_count = int(
                    connection.execute("SELECT COUNT(*) FROM runtime_tasks").fetchone()[0]
                )
                runtime_mode_switch_count = int(
                    connection.execute("SELECT COUNT(*) FROM runtime_mode_switches").fetchone()[0]
                )
                task_packet_count = int(
                    connection.execute("SELECT COUNT(*) FROM task_packets").fetchone()[0]
                )
                agent_run_count = int(
                    connection.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0]
                )
                runtime_rows = {
                    table: [
                        list(row)
                        for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid")
                    ]
                    for table in (
                        "runtime_commands",
                        "runtime_tasks",
                        "runtime_mode_switches",
                        "task_packets",
                        "agent_runs",
                    )
                }
            runtime_digest = hashlib.sha256(
                json.dumps(
                    runtime_rows,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
        return {
            "store_schema_version": schema_version,
            "event_count": event_count,
            "proposal_count": proposal_count,
            "human_action_count": human_action_count,
            "source_import_batch_count": source_import_batch_count,
            "logical_source_count": logical_source_count,
            "source_version_count": source_version_count,
            "source_gap_count": source_gap_count,
            "meeting_ingest_batch_count": meeting_ingest_batch_count,
            "meeting_count": meeting_count,
            "meeting_segment_count": meeting_segment_count,
            "meeting_item_count": meeting_item_count,
            "meeting_conflict_count": meeting_conflict_count,
            "proposal_lifecycle_count": proposal_lifecycle_count,
            "proposal_lifecycle_action_count": proposal_lifecycle_action_count,
            "proposal_reopen_count": proposal_reopen_count,
            "proposal_supersession_count": proposal_supersession_count,
            "meeting_item_proposal_count": meeting_item_proposal_count,
            "runtime_task_count": runtime_task_count,
            "runtime_mode_switch_count": runtime_mode_switch_count,
            "task_packet_count": task_packet_count,
            "agent_run_count": agent_run_count,
            "project_versions": project_versions,
            "state_digest": state_digest,
            "source_digest": source_digest,
            "meeting_digest": meeting_digest,
            "proposal_digest": proposal_digest,
            "runtime_digest": runtime_digest,
        }

    def _manifest_metadata(self, manifest: dict[str, object]) -> dict[str, object]:
        metadata = {
            "store_schema_version": manifest.get("store_schema_version"),
            "event_count": manifest.get("event_count"),
            "proposal_count": manifest.get("proposal_count"),
            "human_action_count": manifest.get("human_action_count"),
            "project_versions": manifest.get("project_versions"),
            "state_digest": manifest.get("state_digest"),
        }
        if manifest.get("schema_version") in {
            "sqlite-backup.v2",
            "sqlite-backup.v3",
            "sqlite-backup.v4",
            "sqlite-backup.v5",
            "sqlite-backup.v6",
        }:
            metadata.update(
                {
                    "source_import_batch_count": manifest.get("source_import_batch_count"),
                    "logical_source_count": manifest.get("logical_source_count"),
                    "source_version_count": manifest.get("source_version_count"),
                    "source_gap_count": manifest.get("source_gap_count"),
                    "source_digest": manifest.get("source_digest"),
                }
            )
        if manifest.get("schema_version") in {
            "sqlite-backup.v3",
            "sqlite-backup.v4",
            "sqlite-backup.v5",
            "sqlite-backup.v6",
        }:
            metadata.update(
                {
                    "meeting_ingest_batch_count": manifest.get("meeting_ingest_batch_count"),
                    "meeting_count": manifest.get("meeting_count"),
                    "meeting_segment_count": manifest.get("meeting_segment_count"),
                    "meeting_item_count": manifest.get("meeting_item_count"),
                    "meeting_conflict_count": manifest.get("meeting_conflict_count"),
                    "meeting_digest": manifest.get("meeting_digest"),
                }
            )
        if manifest.get("schema_version") in {
            "sqlite-backup.v4",
            "sqlite-backup.v5",
            "sqlite-backup.v6",
        }:
            metadata.update(
                {
                    "proposal_lifecycle_count": manifest.get("proposal_lifecycle_count"),
                    "proposal_lifecycle_action_count": manifest.get(
                        "proposal_lifecycle_action_count"
                    ),
                    "proposal_reopen_count": manifest.get("proposal_reopen_count"),
                    "proposal_supersession_count": manifest.get("proposal_supersession_count"),
                    "meeting_item_proposal_count": manifest.get(
                        "meeting_item_proposal_count"
                    ),
                    "proposal_digest": manifest.get("proposal_digest"),
                }
            )
        if manifest.get("schema_version") == "sqlite-backup.v6":
            metadata.update(
                {
                    "runtime_task_count": manifest.get("runtime_task_count"),
                    "runtime_mode_switch_count": manifest.get(
                        "runtime_mode_switch_count"
                    ),
                    "task_packet_count": manifest.get("task_packet_count"),
                    "agent_run_count": manifest.get("agent_run_count"),
                    "runtime_digest": manifest.get("runtime_digest"),
                }
            )
        return metadata

    def _comparable_metadata(
        self, database_path: Path, manifest: dict[str, object]
    ) -> dict[str, object]:
        """按备份清单版本选择对账字段，兼容已生成的旧快照。"""

        metadata = self._database_metadata(database_path)
        if manifest.get("schema_version") == "sqlite-backup.v1":
            for key in (
                "source_import_batch_count",
                "logical_source_count",
                "source_version_count",
                "source_gap_count",
                "source_digest",
            ):
                metadata.pop(key)
        if manifest.get("schema_version") in {"sqlite-backup.v1", "sqlite-backup.v2"}:
            for key in (
                "meeting_ingest_batch_count",
                "meeting_count",
                "meeting_segment_count",
                "meeting_item_count",
                "meeting_conflict_count",
                "meeting_digest",
            ):
                metadata.pop(key)
        if manifest.get("schema_version") in {
            "sqlite-backup.v1",
            "sqlite-backup.v2",
            "sqlite-backup.v3",
        }:
            for key in (
                "proposal_lifecycle_count",
                "proposal_lifecycle_action_count",
                "proposal_reopen_count",
                "proposal_supersession_count",
                "meeting_item_proposal_count",
                "proposal_digest",
            ):
                metadata.pop(key)
        if manifest.get("schema_version") != "sqlite-backup.v6":
            for key in (
                "runtime_task_count",
                "runtime_mode_switch_count",
                "task_packet_count",
                "agent_run_count",
                "runtime_digest",
            ):
                metadata.pop(key)
        return metadata
