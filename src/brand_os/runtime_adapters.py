"""生成 Codex 与 Claude 共用本地 MCP 的无密钥适配配置。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping


RUNTIME_ADAPTER_SCHEMA_VERSION = "runtime-adapter.v1"
MCP_SERVER_NAME = "brand-project-os"


class RuntimeAdapterError(ValueError):
    """表示运行时名称或适配参数不受支持。"""


@dataclass(frozen=True, slots=True)
class RuntimeAdapterProfile:
    """描述一个只消费 Task Packet 的本地 Agent 运行时。"""

    name: str
    runtime_id: str
    client_config_key: str
    credential_policy: str = "runtime_managed"
    brand_os_reads_provider_credentials: bool = False


RUNTIME_ADAPTERS: Mapping[str, RuntimeAdapterProfile] = {
    "codex": RuntimeAdapterProfile("codex", "codex-cli", "mcp_servers"),
    "claude": RuntimeAdapterProfile("claude", "claude-code", "mcpServers"),
}


def get_runtime_adapter(name: str) -> RuntimeAdapterProfile:
    """返回受支持的运行时；未知名称不降级到默认实现。"""

    try:
        return RUNTIME_ADAPTERS[name.lower()]
    except KeyError as exc:
        supported = "、".join(sorted(RUNTIME_ADAPTERS))
        raise RuntimeAdapterError(f"不支持的运行时：{name}；当前支持 {supported}") from exc


def build_mcp_adapter_config(
    runtime_name: str,
    *,
    workspace_root: Path,
    project_id: str,
    database_path: Path | None = None,
    command: str = "brand-os",
) -> dict[str, object]:
    """生成客户端配置；本地 MCP 不接收任何模型提供商凭据。"""

    if not project_id.strip():
        raise RuntimeAdapterError("project_id 不能为空")
    if not command.strip():
        raise RuntimeAdapterError("command 不能为空")
    profile = get_runtime_adapter(runtime_name)
    root = workspace_root.expanduser().resolve(strict=False)
    default_database = root / ".fox" / "state" / "project.db"
    selected_database = (
        database_path.expanduser().resolve(strict=False)
        if database_path is not None
        else default_database
    )
    args = ["--workspace", str(root), "--project", project_id]
    if selected_database != default_database:
        args.extend(["--database", str(selected_database)])
    args.append("mcp")
    server = {
        "transport": "stdio",
        "command": command,
        "args": args,
    }
    return {
        "schema_version": RUNTIME_ADAPTER_SCHEMA_VERSION,
        "runtime": profile.name,
        "runtime_id": profile.runtime_id,
        "credential_policy": profile.credential_policy,
        "brand_os_reads_provider_credentials": (
            profile.brand_os_reads_provider_credentials
        ),
        "mcp_server_name": MCP_SERVER_NAME,
        "mcp_server": server,
        "client_config": {
            profile.client_config_key: {MCP_SERVER_NAME: server},
        },
        "profile": asdict(profile),
    }
