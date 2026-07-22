"""Electron 主进程调用的短生命周期桌面桥接入口。"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from .config import ConfigurationError, load_workspace_settings
from .desktop_service import DesktopProjectService, DesktopServiceError
from .sqlite_base import CanonicalStoreError
from .sqlite_store import SQLiteCanonicalStore
from .workspace import WorkspaceError, WorkspaceLayout


DESKTOP_BRIDGE_REQUEST_SCHEMA_VERSION = "desktop-bridge-request.v1"
MAX_DESKTOP_REQUEST_BYTES = 1024 * 1024
READ_OPERATIONS = frozenset(
    {"project_view", "evidence_get", "task_packet_get", "proposal_get"}
)
WRITE_OPERATIONS = frozenset({"proposal_review"})


class DesktopBridgeError(RuntimeError):
    """表示 Electron 桥接请求无法安全执行。"""


def build_parser() -> argparse.ArgumentParser:
    """构建只供 Electron 主进程调用的桥接参数。"""

    parser = argparse.ArgumentParser(prog="brand-os-desktop-bridge")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--project", default="hongri")
    return parser


def dispatch_desktop_request(
    service: DesktopProjectService, request: Mapping[str, object]
) -> Mapping[str, object]:
    """按封闭操作表分发桌面请求。"""

    allowed = {"schema_version", "operation", "payload"}
    unknown = set(request) - allowed
    missing = {"schema_version", "operation", "payload"} - set(request)
    if unknown:
        raise DesktopBridgeError(
            f"桌面请求包含未声明字段：{', '.join(sorted(unknown))}"
        )
    if missing:
        raise DesktopBridgeError(
            f"桌面请求缺少必填字段：{', '.join(sorted(missing))}"
        )
    if request["schema_version"] != DESKTOP_BRIDGE_REQUEST_SCHEMA_VERSION:
        raise DesktopBridgeError("桌面桥接 Schema 版本不受支持")
    operation = request["operation"]
    payload = request["payload"]
    if not isinstance(operation, str):
        raise DesktopBridgeError("operation 必须是字符串")
    if not isinstance(payload, Mapping):
        raise DesktopBridgeError("payload 必须是对象")
    if operation == "project_view":
        _require_empty_payload(payload)
        return service.get_project_view()
    if operation == "evidence_get":
        return service.get_evidence(_single_text(payload, "evidence_ref"))
    if operation == "task_packet_get":
        return service.get_task_packet(_single_text(payload, "packet_id"))
    if operation == "proposal_get":
        return service.get_proposal(_single_text(payload, "proposal_id"))
    if operation == "proposal_review":
        return service.review_proposal(payload)
    raise DesktopBridgeError(f"桌面操作未开放：{operation}")


def main(argv: Sequence[str] | None = None) -> int:
    """读取一次 JSON 请求并输出一次 JSON 响应。"""

    args = build_parser().parse_args(argv)
    try:
        settings = load_workspace_settings(explicit_root=args.workspace)
        layout = WorkspaceLayout.from_root(settings.workspace_root)
        database = _database_path(layout, args.database)
        request = _read_request(sys.stdin.buffer)
        service = DesktopProjectService(
            _open_store(database), args.project, reviewer_id="Fox"
        )
        _emit(dispatch_desktop_request(service, request), sys.stdout)
        return 0
    except KeyboardInterrupt:
        _emit_error("操作已取消", "cancelled")
        return 130
    except (
        DesktopBridgeError,
        DesktopServiceError,
        ConfigurationError,
        WorkspaceError,
        CanonicalStoreError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        _emit_error(str(exc), exc.__class__.__name__)
        return 2
    except OSError:
        _emit_error("本地业务数据无法安全读取或写入", "OSError")
        return 2


def _open_store(database: Path) -> SQLiteCanonicalStore:
    if database.is_symlink():
        raise DesktopBridgeError("数据库文件不能是符号链接")
    if not database.is_file():
        raise DesktopBridgeError("未找到本地业务数据库")
    return SQLiteCanonicalStore(database)


def _database_path(layout: WorkspaceLayout, explicit: Path | None) -> Path:
    if explicit is None:
        return layout.state / "project.db"
    selected = explicit.expanduser()
    if selected.is_symlink():
        raise DesktopBridgeError("数据库文件不能是符号链接")
    return selected.resolve(strict=False)


def _read_request(stream) -> Mapping[str, object]:
    content = stream.read(MAX_DESKTOP_REQUEST_BYTES + 1)
    if len(content) > MAX_DESKTOP_REQUEST_BYTES:
        raise DesktopBridgeError("桌面请求超过 1 MiB")
    value = json.loads(content.decode("utf-8"))
    if not isinstance(value, dict):
        raise DesktopBridgeError("桌面请求根节点必须是对象")
    return value


def _require_empty_payload(payload: Mapping[str, object]) -> None:
    if payload:
        raise DesktopBridgeError("project_view 不接受额外参数")


def _single_text(payload: Mapping[str, object], field: str) -> str:
    if set(payload) != {field}:
        raise DesktopBridgeError(f"请求只允许字段：{field}")
    value = payload[field]
    if not isinstance(value, str) or not value.strip():
        raise DesktopBridgeError(f"{field} 不能为空")
    return value.strip()


def _emit(value: Mapping[str, object], stream) -> None:
    json.dump(value, stream, ensure_ascii=False)
    stream.write("\n")


def _emit_error(message: str, error_type: str) -> None:
    _emit(
        {
            "schema_version": "desktop-bridge-error.v1",
            "error": error_type,
            "message": message,
        },
        sys.stderr,
    )


if __name__ == "__main__":
    raise SystemExit(main())
