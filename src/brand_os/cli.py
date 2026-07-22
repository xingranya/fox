"""Brand Project OS 的确定性本地 CLI 与 stdio MCP 组合入口。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Mapping, Sequence

from .config import ConfigurationError, load_workspace_settings
from .domain import Actor, ActorKind, CommandContext
from .local_access import LocalAIService, LocalAccessError
from .mcp_server import DEFAULT_TOOL_TIMEOUT_SECONDS, run_mcp_stdio
from .runtime_adapters import RuntimeAdapterError
from .sqlite_base import CanonicalStoreError, ProjectNotFound
from .sqlite_store import SQLiteCanonicalStore
from .workspace import WorkspaceError, WorkspaceLayout, initialize_workspace


CLI_SCHEMA_VERSION = "brand-os-cli.v1"
MAX_PROPOSAL_INPUT_BYTES = 1024 * 1024


class CLIError(RuntimeError):
    """表示 CLI 参数已经解析，但运行条件不满足。"""


def build_parser() -> argparse.ArgumentParser:
    """构建不会把人工审批暴露给脚本或 Agent 的命令树。"""

    parser = argparse.ArgumentParser(prog="brand-os")
    parser.add_argument("--workspace", type=Path, help="Fox 工作空间根目录")
    parser.add_argument("--database", type=Path, help="显式 SQLite 文件")
    parser.add_argument("--project", default="hongri", help="固定项目 ID")
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="初始化本地工作空间与项目")
    init.add_argument("--project-name", default="鸿日")

    commands.add_parser("status", help="读取当前状态与版本")

    task = commands.add_parser("task", help="读取任务上下文")
    task_commands = task.add_subparsers(dest="task_command", required=True)
    packet = task_commands.add_parser("packet", help="读取既有 Task Packet")
    packet.add_argument("--packet-id", required=True)
    packet.add_argument("--layer", default="FULL", choices=("FULL", "L0", "L1", "L2", "L3", "L4"))

    evidence = commands.add_parser("evidence", help="证据回源")
    evidence_commands = evidence.add_subparsers(dest="evidence_command", required=True)
    evidence_get = evidence_commands.add_parser("get", help="读取稳定证据引用")
    evidence_get.add_argument("--ref", required=True)

    decision = commands.add_parser("decision", help="当前决定")
    decision_commands = decision.add_subparsers(dest="decision_command", required=True)
    decision_list = decision_commands.add_parser("list", help="列出决定")
    decision_list.add_argument("--include-inactive", action="store_true")

    open_question = commands.add_parser("open-question", help="开放问题")
    open_commands = open_question.add_subparsers(dest="open_command", required=True)
    open_list = open_commands.add_parser("list", help="列出开放问题")
    open_list.add_argument("--include-inactive", action="store_true")

    proposal = commands.add_parser("proposal", help="Proposal 工作层")
    proposal_commands = proposal.add_subparsers(dest="proposal_command", required=True)
    proposal_create = proposal_commands.add_parser("create", help="创建待确认 Proposal")
    proposal_create.add_argument("--input", type=Path, required=True, help="JSON 请求文件")
    proposal_get = proposal_commands.add_parser("get", help="读取 Proposal")
    proposal_get.add_argument("--proposal-id", required=True)

    run = commands.add_parser("run", help="登记 Agent 运行")
    run_commands = run.add_subparsers(dest="run_command", required=True)
    run_start = run_commands.add_parser("start", help="把模型运行绑定到既有 Packet")
    run_start.add_argument("--packet-id", required=True)
    run_start.add_argument("--packet-hash", required=True)
    run_start.add_argument("--runtime", required=True, choices=("codex", "claude"))
    run_start.add_argument("--runtime-version", required=True)
    run_start.add_argument("--model-id", required=True)
    run_start.add_argument("--model-version", required=True)
    run_start.add_argument("--run-id", required=True)
    run_start.add_argument("--idempotency-key", required=True)

    adapter = commands.add_parser("adapter", help="生成 Codex/Claude 本地 MCP 配置")
    adapter_commands = adapter.add_subparsers(dest="adapter_command", required=True)
    adapter_show = adapter_commands.add_parser("show", help="输出无密钥适配配置")
    adapter_show.add_argument("--runtime", required=True, choices=("codex", "claude"))
    adapter_show.add_argument("--command", dest="executable", default="brand-os")

    commands.add_parser("doctor", help="检查本地存储和工具边界")
    commands.add_parser("verify", help="核对项目状态和证据概况")
    mcp = commands.add_parser("mcp", help="运行本地 stdio MCP")
    mcp.add_argument(
        "--timeout-seconds", type=float, default=DEFAULT_TOOL_TIMEOUT_SECONDS
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """执行一个命令；所有非 MCP 输出均为 UTF-8 JSON。"""

    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        settings = load_workspace_settings(explicit_root=args.workspace)
        layout = WorkspaceLayout.from_root(settings.workspace_root)
        database = _database_path(layout, args.database)
        if args.command == "init":
            result = _initialize(args, settings, database)
        else:
            service = _open_service(database, args.project)
            if args.command == "mcp":
                run_mcp_stdio(service, timeout_seconds=args.timeout_seconds)
                return 0
            result = _dispatch(args, service, layout, database)
        _emit(result, sys.stdout)
        return 0
    except KeyboardInterrupt:
        _emit_error("操作已取消", "cancelled")
        return 130
    except (
        CLIError,
        ConfigurationError,
        WorkspaceError,
        CanonicalStoreError,
        LocalAccessError,
        RuntimeAdapterError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        _emit_error(str(exc), exc.__class__.__name__)
        return 2
    except OSError:
        _emit_error("本地文件无法安全读取或写入", "OSError")
        return 2


def _initialize(
    args: argparse.Namespace,
    settings,
    database: Path,
) -> Mapping[str, object]:
    layout = initialize_workspace(settings)
    store = SQLiteCanonicalStore(database)
    try:
        version = store.get_project_version(args.project)
        created = False
    except ProjectNotFound:
        store.create_project(
            CommandContext(
                args.project,
                Actor(ActorKind.SYSTEM, "brand-os-cli"),
                f"init:{args.project}",
                0,
            ),
            args.project_name,
        )
        version = store.get_project_version(args.project)
        created = True
    return {
        "schema_version": CLI_SCHEMA_VERSION,
        "workspace_schema_version": "local-workspace.v1",
        "project_id": args.project,
        "project_version": version,
        "created": created,
        "database": str(database.relative_to(layout.root)),
    }


def _dispatch(
    args: argparse.Namespace,
    service: LocalAIService,
    layout: WorkspaceLayout,
    database: Path,
) -> Mapping[str, object]:
    if args.command == "status":
        return service.invoke("project_get_state", {})
    if args.command == "task" and args.task_command == "packet":
        return service.invoke(
            "task_get_packet", {"packet_id": args.packet_id, "layer": args.layer}
        )
    if args.command == "evidence" and args.evidence_command == "get":
        return service.invoke("evidence_get", {"evidence_ref": args.ref})
    if args.command == "decision" and args.decision_command == "list":
        return service.invoke(
            "decision_list", {"include_inactive": args.include_inactive}
        )
    if args.command == "open-question" and args.open_command == "list":
        return service.invoke(
            "open_question_list", {"include_inactive": args.include_inactive}
        )
    if args.command == "proposal" and args.proposal_command == "create":
        return service.invoke("proposal_create", _load_proposal_input(args.input))
    if args.command == "proposal" and args.proposal_command == "get":
        return service.invoke("proposal_get", {"proposal_id": args.proposal_id})
    if args.command == "run" and args.run_command == "start":
        return service.start_agent_run(
            packet_id=args.packet_id,
            packet_hash=args.packet_hash,
            runtime_name=args.runtime,
            runtime_version=args.runtime_version,
            model_id=args.model_id,
            model_version=args.model_version,
            run_id=args.run_id,
            idempotency_key=args.idempotency_key,
        )
    if args.command == "adapter" and args.adapter_command == "show":
        return service.adapter_config(
            args.runtime,
            workspace_root=layout.root,
            database_path=database,
            command=args.executable,
        )
    if args.command == "doctor":
        return service.invoke("system_doctor", {})
    if args.command == "verify":
        return service.invoke("project_verify", {})
    raise CLIError("命令没有对应的受控应用用例")


def _open_service(database: Path, project_id: str) -> LocalAIService:
    if database.is_symlink():
        raise CLIError("数据库文件不能是符号链接")
    if not database.is_file():
        raise CLIError("未找到本地数据库，请先运行 brand-os init")
    store = SQLiteCanonicalStore(database)
    return LocalAIService(store, project_id, caller_id="brand-os-cli")


def _database_path(layout: WorkspaceLayout, explicit: Path | None) -> Path:
    return (
        explicit.expanduser().resolve(strict=False)
        if explicit is not None
        else layout.state / "project.db"
    )


def _load_proposal_input(path: Path) -> Mapping[str, object]:
    if path.expanduser().is_symlink():
        raise CLIError("Proposal 输入不能是符号链接")
    selected = path.expanduser().resolve(strict=True)
    if not selected.is_file():
        raise CLIError("Proposal 输入必须是普通 JSON 文件")
    if selected.stat().st_size > MAX_PROPOSAL_INPUT_BYTES:
        raise CLIError("Proposal 输入超过 1 MiB")
    value = json.loads(selected.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise CLIError("Proposal 输入根节点必须是对象")
    return value


def _emit(value: Mapping[str, object], stream) -> None:
    json.dump(value, stream, ensure_ascii=False, indent=2)
    stream.write("\n")


def _emit_error(message: str, error_type: str) -> None:
    _emit(
        {
            "schema_version": "brand-os-error.v1",
            "error": error_type,
            "message": message,
        },
        sys.stderr,
    )


if __name__ == "__main__":
    raise SystemExit(main())
