"""为 PostgreSQL 集成测试提供一次性本地数据库。"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql


class TemporaryPostgreSQL:
    """启动隔离 PostgreSQL 集群，并在测试结束后完整停止。"""

    def __init__(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="brand-os-postgres-")
        self.root = Path(self._temporary.name)
        self.data_directory = self.root / "data"
        self.socket_directory = self.root / "socket"
        self.log_path = self.root / "postgres.log"
        self.binary_directory = self._find_binary_directory()
        self.port = self._available_port()
        self._started = False

    @staticmethod
    def _find_binary_directory() -> Path:
        configured = os.environ.get("BRAND_OS_POSTGRES_BIN")
        discovered = shutil.which("initdb")
        candidates = (
            Path(configured) if configured else None,
            Path(discovered).parent if discovered else None,
            Path("/opt/homebrew/opt/postgresql@17/bin"),
            Path("/usr/local/opt/postgresql@17/bin"),
            Path("/usr/lib/postgresql/17/bin"),
        )
        for candidate in candidates:
            if candidate is not None and (candidate / "initdb").is_file():
                return candidate
        raise RuntimeError(
            "未找到 PostgreSQL 17 测试运行时；请设置 BRAND_OS_POSTGRES_BIN"
        )

    @staticmethod
    def _available_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            return int(probe.getsockname()[1])

    @property
    def admin_dsn(self) -> str:
        return f"postgresql://postgres@127.0.0.1:{self.port}/postgres"

    def start(self) -> None:
        """初始化并启动仅监听回环地址的临时集群。"""

        self.socket_directory.mkdir(mode=0o700)
        self._run(
            "initdb",
            "--username=postgres",
            "--encoding=UTF8",
            "--no-locale",
            "--auth-host=trust",
            "--auth-local=trust",
            "--no-sync",
            "-D",
            str(self.data_directory),
        )
        options = (
            f"-h 127.0.0.1 -p {self.port} -k {self.socket_directory} "
            "-c fsync=off -c synchronous_commit=off -c full_page_writes=off"
        )
        self._run(
            "pg_ctl",
            "-D",
            str(self.data_directory),
            "-l",
            str(self.log_path),
            "-o",
            options,
            "-w",
            "-t",
            "30",
            "start",
        )
        self._started = True

    def create_database(self) -> tuple[str, str]:
        """为单项测试创建独立数据库并返回名称与 DSN。"""

        database_name = f"brand_os_{uuid4().hex}"
        with psycopg.connect(self.admin_dsn, autocommit=True) as connection:
            connection.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name))
            )
        return (
            database_name,
            f"postgresql://postgres@127.0.0.1:{self.port}/{database_name}",
        )

    def drop_database(self, database_name: str) -> None:
        """强制关闭测试连接并删除测试数据库。"""

        with psycopg.connect(self.admin_dsn, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    sql.Identifier(database_name)
                )
            )

    def stop(self) -> None:
        """停止临时集群并清理文件。"""

        try:
            if self._started:
                self._run(
                    "pg_ctl",
                    "-D",
                    str(self.data_directory),
                    "-w",
                    "-t",
                    "30",
                    "stop",
                    "-m",
                    "immediate",
                )
        finally:
            self._started = False
            self._temporary.cleanup()

    def _run(self, command: str, *arguments: str) -> None:
        executable = self.binary_directory / command
        completed = subprocess.run(
            (str(executable), *arguments),
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(f"{command} 执行失败：{detail}")
