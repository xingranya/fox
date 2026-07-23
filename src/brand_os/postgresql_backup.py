"""PostgreSQL 一致快照、逻辑备份和隔离恢复适配器。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time as datetime_time
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from .backup import BACKUP_ID_PATTERN, BackupError
from .hashing import sha256_file


POSTGRESQL_BACKUP_SCHEMA_VERSION = "postgresql-backup.v1"
POSTGRESQL_SNAPSHOT_SCHEMA_VERSION = "postgresql-recovery-snapshot.v1"
ARCHIVE_FILENAME = "database.dump"
MANIFEST_FILENAME = "manifest.json"
SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")

_LIBPQ_ENVIRONMENT = {
    "host": "PGHOST",
    "hostaddr": "PGHOSTADDR",
    "port": "PGPORT",
    "dbname": "PGDATABASE",
    "user": "PGUSER",
    "password": "PGPASSWORD",
    "passfile": "PGPASSFILE",
    "service": "PGSERVICE",
    "servicefile": "PGSERVICEFILE",
    "connect_timeout": "PGCONNECT_TIMEOUT",
    "client_encoding": "PGCLIENTENCODING",
    "options": "PGOPTIONS",
    "application_name": "PGAPPNAME",
    "fallback_application_name": "PGAPPNAME",
    "keepalives": "PGKEEPALIVES",
    "keepalives_idle": "PGKEEPALIVESIDLE",
    "keepalives_interval": "PGKEEPALIVESINTERVAL",
    "keepalives_count": "PGKEEPALIVESCOUNT",
    "tcp_user_timeout": "PGTCPUSER_TIMEOUT",
    "sslmode": "PGSSLMODE",
    "sslcompression": "PGSSLCOMPRESSION",
    "sslcert": "PGSSLCERT",
    "sslkey": "PGSSLKEY",
    "sslpassword": "PGSSLPASSWORD",
    "sslrootcert": "PGSSLROOTCERT",
    "sslcrl": "PGSSLCRL",
    "sslcrldir": "PGSSLCRLDIR",
    "sslsni": "PGSSLSNI",
    "requirepeer": "PGREQUIREPEER",
    "gssencmode": "PGGSSENCMODE",
    "krbsrvname": "PGKRBSRVNAME",
    "gsslib": "PGGSSLIB",
    "target_session_attrs": "PGTARGETSESSIONATTRS",
    "channel_binding": "PGCHANNELBINDING",
}


@dataclass(frozen=True, slots=True)
class PostgreSQLTableDigest:
    """一致快照中一张表的行数和内容摘要。"""

    table_name: str
    row_count: int
    sha256: str

    def __post_init__(self) -> None:
        if not self.table_name.strip():
            raise BackupError("快照表名不能为空")
        if self.row_count < 0:
            raise BackupError("快照表行数不能小于 0")
        if not SHA256_PATTERN.fullmatch(self.sha256):
            raise BackupError("快照表摘要必须是完整小写 SHA-256")

    def as_dict(self) -> dict[str, object]:
        """返回可写入备份清单的安全摘要。"""

        return {
            "table_name": self.table_name,
            "row_count": self.row_count,
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class PostgreSQLRecoverySnapshot:
    """不包含业务原文的 PostgreSQL 一致快照摘要。"""

    schema_version: str
    database_schema_version: int
    tables: tuple[PostgreSQLTableDigest, ...]
    snapshot_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != POSTGRESQL_SNAPSHOT_SCHEMA_VERSION:
            raise BackupError("PostgreSQL 恢复快照版本不受支持")
        if self.database_schema_version < 0:
            raise BackupError("PostgreSQL 数据库 Schema 版本无效")
        names = tuple(item.table_name for item in self.tables)
        if names != tuple(sorted(names)) or len(names) != len(set(names)):
            raise BackupError("PostgreSQL 快照表必须按名称排序且不能重复")
        expected = self._calculate_sha256(
            self.database_schema_version,
            self.tables,
        )
        if self.snapshot_sha256 != expected:
            raise BackupError("PostgreSQL 快照总摘要不一致")

    @classmethod
    def build(
        cls,
        *,
        database_schema_version: int,
        tables: Sequence[PostgreSQLTableDigest],
    ) -> PostgreSQLRecoverySnapshot:
        """根据排序后的表摘要创建可复算快照。"""

        ordered = tuple(sorted(tables, key=lambda item: item.table_name))
        return cls(
            schema_version=POSTGRESQL_SNAPSHOT_SCHEMA_VERSION,
            database_schema_version=database_schema_version,
            tables=ordered,
            snapshot_sha256=cls._calculate_sha256(
                database_schema_version,
                ordered,
            ),
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> PostgreSQLRecoverySnapshot:
        """严格读取备份清单中的快照摘要。"""

        try:
            table_values = value["tables"]
            if not isinstance(table_values, list):
                raise TypeError
            tables = tuple(
                PostgreSQLTableDigest(
                    table_name=str(item["table_name"]),
                    row_count=int(item["row_count"]),
                    sha256=str(item["sha256"]),
                )
                for item in table_values
                if isinstance(item, Mapping)
            )
            if len(tables) != len(table_values):
                raise TypeError
            return cls(
                schema_version=str(value["schema_version"]),
                database_schema_version=int(value["database_schema_version"]),
                tables=tables,
                snapshot_sha256=str(value["snapshot_sha256"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise BackupError("PostgreSQL 备份快照清单无效") from error

    def as_dict(self) -> dict[str, object]:
        """返回不含表内容和连接秘密的清单数据。"""

        return {
            "schema_version": self.schema_version,
            "database_schema_version": self.database_schema_version,
            "tables": [item.as_dict() for item in self.tables],
            "snapshot_sha256": self.snapshot_sha256,
        }

    def table(self, table_name: str) -> PostgreSQLTableDigest:
        """读取指定表摘要，缺失时视为恢复不完整。"""

        for item in self.tables:
            if item.table_name == table_name:
                return item
        raise BackupError(f"PostgreSQL 恢复快照缺少表：{table_name}")

    @staticmethod
    def _calculate_sha256(
        database_schema_version: int,
        tables: Sequence[PostgreSQLTableDigest],
    ) -> str:
        payload = {
            "schema_version": POSTGRESQL_SNAPSHOT_SCHEMA_VERSION,
            "database_schema_version": database_schema_version,
            "tables": [item.as_dict() for item in tables],
        }
        return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class PostgreSQLRestoreResult:
    """一次隔离恢复和完整快照复核的结果。"""

    backup_id: str
    backup_created_at: str
    restored_at: str
    duration_seconds: float
    archive_sha256: str
    snapshot: PostgreSQLRecoverySnapshot

    def as_dict(self) -> dict[str, object]:
        """返回不含 DSN 和凭据的恢复结果。"""

        return {
            "backup_id": self.backup_id,
            "backup_created_at": self.backup_created_at,
            "restored_at": self.restored_at,
            "duration_seconds": self.duration_seconds,
            "archive_sha256": self.archive_sha256,
            "snapshot": self.snapshot.as_dict(),
        }


class PostgreSQLBackupService:
    """用 pg_dump/pg_restore 完成一致逻辑备份和空库恢复。"""

    def __init__(
        self,
        source_dsn: str,
        backup_root: Path,
        *,
        binary_directory: Path | None = None,
    ) -> None:
        if not source_dsn.strip():
            raise ValueError("PostgreSQL 源 DSN 不能为空")
        expanded_root = backup_root.expanduser()
        if expanded_root.is_symlink():
            raise BackupError("PostgreSQL 备份目录不能是符号链接")
        expanded_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.source_dsn = source_dsn
        self.backup_root = expanded_root.resolve(strict=True)
        self.binary_directory = self._resolve_binary_directory(binary_directory)

    def create(self) -> str:
        """在导出快照事务内生成自校验 custom-format 逻辑备份。"""

        backup_id = (
            f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:12]}"
        )
        destination = self.backup_root / backup_id
        temporary = Path(
            tempfile.mkdtemp(prefix="postgresql-backup-", dir=self.backup_root)
        )
        archive_path = temporary / ARCHIVE_FILENAME
        try:
            with psycopg.connect(
                self.source_dsn,
                autocommit=True,
                row_factory=dict_row,
            ) as connection:
                connection.execute(
                    "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY"
                )
                connection.execute("SET LOCAL row_security = off")
                snapshot_id = str(
                    connection.execute("SELECT pg_export_snapshot()").fetchone()[
                        "pg_export_snapshot"
                    ]
                )
                snapshot = self._snapshot_connection(connection)
                self._run_tool(
                    "pg_dump",
                    (
                        "--format=custom",
                        "--no-owner",
                        "--no-privileges",
                        f"--snapshot={snapshot_id}",
                        f"--file={archive_path}",
                    ),
                    self.source_dsn,
                )
                connection.execute("COMMIT")

            archive_sha256, archive_size = sha256_file(archive_path)
            archive_path.chmod(0o600)
            created_at = datetime.now(UTC).isoformat()
            manifest = {
                "schema_version": POSTGRESQL_BACKUP_SCHEMA_VERSION,
                "backup_id": backup_id,
                "created_at": created_at,
                "archive": {
                    "filename": ARCHIVE_FILENAME,
                    "format": "pg_dump-custom",
                    "sha256": archive_sha256,
                    "size_bytes": archive_size,
                    "tool_version": self._tool_version("pg_dump"),
                },
                "snapshot": snapshot.as_dict(),
                "recovery_scope": {
                    "logical_backup": True,
                    "pitr": False,
                    "target_must_be_empty": True,
                    "restore_in_place": False,
                },
            }
            manifest_path = temporary / MANIFEST_FILENAME
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            manifest_path.chmod(0o600)
            os.replace(temporary, destination)
            return backup_id
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise

    def restore(self, backup_id: str, target_dsn: str) -> PostgreSQLRestoreResult:
        """把已校验备份恢复到空数据库，并复核全部表摘要。"""

        if not target_dsn.strip():
            raise ValueError("PostgreSQL 恢复目标 DSN 不能为空")
        manifest = self.load_manifest(backup_id)
        self._require_empty_target(target_dsn)
        archive_path = self.backup_root / backup_id / ARCHIVE_FILENAME
        started = time.perf_counter()
        self._run_tool(
            "pg_restore",
            (
                f"--dbname={self._database_name(target_dsn)}",
                "--exit-on-error",
                "--single-transaction",
                "--no-owner",
                "--no-privileges",
                str(archive_path),
            ),
            target_dsn,
        )
        restored_snapshot = self.snapshot(target_dsn)
        expected_snapshot = PostgreSQLRecoverySnapshot.from_mapping(
            _mapping(manifest["snapshot"], "snapshot")
        )
        if restored_snapshot != expected_snapshot:
            raise BackupError("PostgreSQL 恢复结果与备份一致快照不匹配")
        self._validate_event_sequence(target_dsn)
        archive = _mapping(manifest["archive"], "archive")
        return PostgreSQLRestoreResult(
            backup_id=backup_id,
            backup_created_at=str(manifest["created_at"]),
            restored_at=datetime.now(UTC).isoformat(),
            duration_seconds=round(time.perf_counter() - started, 6),
            archive_sha256=str(archive["sha256"]),
            snapshot=restored_snapshot,
        )

    def load_manifest(self, backup_id: str) -> dict[str, object]:
        """读取并校验备份 ID、清单、归档哈希和安全路径。"""

        backup_directory = self._backup_directory(backup_id)
        manifest_path = backup_directory / MANIFEST_FILENAME
        archive_path = backup_directory / ARCHIVE_FILENAME
        if manifest_path.is_symlink() or archive_path.is_symlink():
            raise BackupError("PostgreSQL 备份文件不能是符号链接")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise BackupError("PostgreSQL 备份清单缺失或损坏") from error
        if not isinstance(manifest, dict):
            raise BackupError("PostgreSQL 备份清单必须是 JSON 对象")
        if (
            manifest.get("schema_version") != POSTGRESQL_BACKUP_SCHEMA_VERSION
            or manifest.get("backup_id") != backup_id
        ):
            raise BackupError("PostgreSQL 备份清单版本或 ID 不匹配")
        try:
            created_at = datetime.fromisoformat(str(manifest["created_at"]))
        except (KeyError, ValueError) as error:
            raise BackupError("PostgreSQL 备份清单创建时间无效") from error
        if created_at.tzinfo is None:
            raise BackupError("PostgreSQL 备份清单创建时间必须包含时区")
        archive = _mapping(manifest.get("archive"), "archive")
        if archive.get("filename") != ARCHIVE_FILENAME:
            raise BackupError("PostgreSQL 备份归档文件名无效")
        digest, size = sha256_file(archive_path)
        if digest != archive.get("sha256") or size != archive.get("size_bytes"):
            raise BackupError("PostgreSQL 备份归档哈希或大小不匹配")
        PostgreSQLRecoverySnapshot.from_mapping(
            _mapping(manifest.get("snapshot"), "snapshot")
        )
        return manifest

    def snapshot(self, dsn: str) -> PostgreSQLRecoverySnapshot:
        """在只读一致事务中计算数据库表摘要。"""

        if not dsn.strip():
            raise ValueError("PostgreSQL DSN 不能为空")
        with psycopg.connect(
            dsn,
            autocommit=True,
            row_factory=dict_row,
        ) as connection:
            connection.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
            try:
                connection.execute("SET LOCAL row_security = off")
                snapshot = self._snapshot_connection(connection)
                connection.execute("COMMIT")
                return snapshot
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def list_project_ids(self, dsn: str) -> tuple[str, ...]:
        """从恢复库读取项目 ID；备份清单不保存业务标识。"""

        with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as connection:
            rows = connection.execute(
                "SELECT project_id FROM projects ORDER BY project_id"
            ).fetchall()
        return tuple(str(row["project_id"]) for row in rows)

    def _snapshot_connection(
        self,
        connection: psycopg.Connection,
    ) -> PostgreSQLRecoverySnapshot:
        schema_name = str(
            connection.execute("SELECT current_schema()").fetchone()["current_schema"]
        )
        table_rows = connection.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = %s AND table_type = 'BASE TABLE'
            ORDER BY table_name
            """,
            (schema_name,),
        ).fetchall()
        table_names = tuple(str(row["table_name"]) for row in table_rows)
        if "schema_migrations" not in table_names:
            raise BackupError("PostgreSQL 权威库缺少 schema_migrations")
        digests = tuple(
            self._table_digest(connection, schema_name, table_name)
            for table_name in table_names
        )
        schema_version_row = connection.execute(
            "SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations"
        ).fetchone()
        return PostgreSQLRecoverySnapshot.build(
            database_schema_version=int(schema_version_row["version"]),
            tables=digests,
        )

    def _table_digest(
        self,
        connection: psycopg.Connection,
        schema_name: str,
        table_name: str,
    ) -> PostgreSQLTableDigest:
        primary_key_rows = connection.execute(
            """
            SELECT attribute.attname AS column_name
            FROM pg_index AS index_definition
            JOIN pg_class AS relation
              ON relation.oid = index_definition.indrelid
            JOIN pg_namespace AS namespace
              ON namespace.oid = relation.relnamespace
            JOIN LATERAL unnest(index_definition.indkey)
              WITH ORDINALITY AS key_column(attnum, position) ON TRUE
            JOIN pg_attribute AS attribute
              ON attribute.attrelid = relation.oid
             AND attribute.attnum = key_column.attnum
            WHERE namespace.nspname = %s
              AND relation.relname = %s
              AND index_definition.indisprimary
            ORDER BY key_column.position
            """,
            (schema_name, table_name),
        ).fetchall()
        primary_keys = tuple(str(row["column_name"]) for row in primary_key_rows)
        if not primary_keys:
            raise BackupError(f"PostgreSQL 表缺少主键，无法稳定对账：{table_name}")
        query = sql.SQL("SELECT * FROM {}.{} ORDER BY {}").format(
            sql.Identifier(schema_name),
            sql.Identifier(table_name),
            sql.SQL(", ").join(sql.Identifier(name) for name in primary_keys),
        )
        digest = hashlib.sha256()
        row_count = 0
        cursor = connection.execute(query)
        for row in cursor:
            encoded = _canonical_json(dict(row)).encode("utf-8")
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)
            row_count += 1
        return PostgreSQLTableDigest(table_name, row_count, digest.hexdigest())

    def _require_empty_target(self, target_dsn: str) -> None:
        with psycopg.connect(target_dsn, autocommit=True, row_factory=dict_row) as connection:
            relation_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM pg_class AS relation
                    JOIN pg_namespace AS namespace
                      ON namespace.oid = relation.relnamespace
                    WHERE namespace.nspname = current_schema()
                      AND relation.relkind IN ('r','p','v','m','S','f')
                    """
                ).fetchone()["count"]
            )
            function_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM pg_proc AS procedure
                    JOIN pg_namespace AS namespace
                      ON namespace.oid = procedure.pronamespace
                    WHERE namespace.nspname = current_schema()
                    """
                ).fetchone()["count"]
            )
        if relation_count or function_count:
            raise BackupError("PostgreSQL 恢复目标必须是空数据库，禁止原地覆盖")

    def _validate_event_sequence(self, target_dsn: str) -> None:
        with psycopg.connect(target_dsn, autocommit=True, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                SELECT COALESCE(MAX(global_position), 0) AS max_position
                FROM events
                """
            ).fetchone()
            sequence = connection.execute(
                """
                SELECT last_value
                FROM pg_sequences
                WHERE schemaname = current_schema()
                  AND sequencename = 'events_global_position_seq'
                """
            ).fetchone()
        if sequence is None or sequence["last_value"] is None:
            if int(row["max_position"]) == 0:
                return
            raise BackupError("PostgreSQL 恢复后事件序列状态缺失")
        if int(sequence["last_value"]) < int(row["max_position"]):
            raise BackupError("PostgreSQL 恢复后事件序列落后于事件表")

    def _backup_directory(self, backup_id: str) -> Path:
        if not BACKUP_ID_PATTERN.fullmatch(backup_id):
            raise BackupError("PostgreSQL 备份 ID 格式无效")
        candidate = self.backup_root / backup_id
        if candidate.is_symlink():
            raise BackupError("PostgreSQL 备份目录不能是符号链接")
        resolved = candidate.resolve(strict=False)
        if resolved.parent != self.backup_root:
            raise BackupError("PostgreSQL 备份目录越界")
        return resolved

    def _run_tool(
        self,
        tool_name: str,
        arguments: Sequence[str],
        dsn: str,
    ) -> None:
        environment, secret_values = self._connection_environment(dsn)
        executable = self.binary_directory / tool_name
        completed = subprocess.run(
            (str(executable), *arguments),
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        if completed.returncode == 0:
            return
        detail = completed.stderr.strip() or completed.stdout.strip() or "无错误详情"
        for secret in secret_values:
            if secret:
                detail = detail.replace(secret, "[REDACTED]")
        raise BackupError(
            f"{tool_name} 执行失败（退出码 {completed.returncode}）：{detail[:1000]}"
        )

    def _tool_version(self, tool_name: str) -> str:
        completed = subprocess.run(
            (str(self.binary_directory / tool_name), "--version"),
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise BackupError(f"无法读取 {tool_name} 版本")
        return completed.stdout.strip()

    @staticmethod
    def _connection_environment(dsn: str) -> tuple[dict[str, str], tuple[str, ...]]:
        try:
            parameters = conninfo_to_dict(dsn)
        except psycopg.ProgrammingError as error:
            raise BackupError("PostgreSQL DSN 格式无效") from error
        environment = os.environ.copy()
        for variable in set(_LIBPQ_ENVIRONMENT.values()):
            environment.pop(variable, None)
        for key, variable in _LIBPQ_ENVIRONMENT.items():
            value = parameters.get(key)
            if value is not None and str(value):
                environment[variable] = str(value)
        environment["PGAPPNAME"] = "brand-project-os-recovery"
        secret_values = tuple(
            str(parameters[key])
            for key in ("password", "sslpassword")
            if parameters.get(key)
        )
        return environment, secret_values

    @staticmethod
    def _database_name(dsn: str) -> str:
        """只提取数据库名供 pg_restore 选择连接模式，不暴露完整 DSN。"""

        try:
            database_name = str(conninfo_to_dict(dsn).get("dbname") or "")
        except psycopg.ProgrammingError as error:
            raise BackupError("PostgreSQL DSN 格式无效") from error
        if not database_name:
            raise BackupError("PostgreSQL DSN 缺少数据库名")
        return database_name

    @staticmethod
    def _resolve_binary_directory(configured: Path | None) -> Path:
        candidates: list[Path] = []
        if configured is not None:
            candidates.append(configured.expanduser())
        discovered_dump = shutil.which("pg_dump")
        discovered_restore = shutil.which("pg_restore")
        if discovered_dump:
            candidates.append(Path(discovered_dump).parent)
        if discovered_restore:
            candidates.append(Path(discovered_restore).parent)
        candidates.extend(
            (
                Path("/opt/homebrew/opt/postgresql@17/bin"),
                Path("/usr/local/opt/postgresql@17/bin"),
                Path("/usr/lib/postgresql/17/bin"),
            )
        )
        for candidate in candidates:
            if (candidate / "pg_dump").is_file() and (
                candidate / "pg_restore"
            ).is_file():
                return candidate.resolve(strict=True)
        raise BackupError("未找到可用的 pg_dump 和 pg_restore")


def _canonical_json(value: object) -> str:
    return json.dumps(
        _canonical_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _canonical_value(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (datetime, date, datetime_time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"$bytes": bytes(value).hex()}
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_canonical_value(item) for item in value]
    return str(value)


def _mapping(value: object, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise BackupError(f"PostgreSQL 备份清单字段无效：{field_name}")
    return value


__all__ = [
    "POSTGRESQL_BACKUP_SCHEMA_VERSION",
    "POSTGRESQL_SNAPSHOT_SCHEMA_VERSION",
    "PostgreSQLBackupService",
    "PostgreSQLRecoverySnapshot",
    "PostgreSQLRestoreResult",
    "PostgreSQLTableDigest",
]
