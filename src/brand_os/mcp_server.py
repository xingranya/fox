"""使用官方 MCP Python SDK 提供严格 Schema 的本地 stdio 工具。"""

from __future__ import annotations

from typing import Any

import anyio
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, ToolAnnotations

from .local_access import LocalAIService, MCP_TOOL_NAMES


DEFAULT_TOOL_TIMEOUT_SECONDS = 10.0
MAX_TOOL_TIMEOUT_SECONDS = 60.0
MCP_SERVER_VERSION = "local-mcp.v1"
OBJECT_OUTPUT_SCHEMA = {"type": "object", "additionalProperties": True}


class ToolCallTimeout(RuntimeError):
    """表示本地工具超过时限；写请求可用原幂等键安全重试。"""


class LocalMCPGateway:
    """为同步本地应用服务增加 MCP 超时与取消传播。"""

    def __init__(
        self,
        service: LocalAIService,
        *,
        timeout_seconds: float = DEFAULT_TOOL_TIMEOUT_SECONDS,
    ) -> None:
        if not 0 < timeout_seconds <= MAX_TOOL_TIMEOUT_SECONDS:
            raise ValueError(
                f"timeout_seconds 必须大于 0 且不超过 {MAX_TOOL_TIMEOUT_SECONDS:g}"
            )
        self.service = service
        self.timeout_seconds = timeout_seconds

    async def invoke(
        self, tool_name: str, arguments: dict[str, object]
    ) -> dict[str, object]:
        """在线程中执行短事务；取消由 anyio 原样传回 MCP 客户端。"""

        try:
            with anyio.fail_after(self.timeout_seconds):
                result = await anyio.to_thread.run_sync(
                    self.service.invoke,
                    tool_name,
                    arguments,
                    abandon_on_cancel=True,
                )
        except TimeoutError as exc:
            raise ToolCallTimeout(
                f"工具 {tool_name} 超时；写请求请使用同一幂等键查询或重试"
            ) from exc
        return dict(result)


def _object_schema(
    properties: dict[str, object],
    *,
    required: tuple[str, ...] = (),
) -> dict[str, object]:
    schema: dict[str, object] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = list(required)
    return schema


def build_mcp_tools() -> list[Tool]:
    """返回顺序稳定、参数封闭的 MCP 工具定义。"""

    read_only = ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
    proposal_write = ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
    empty = _object_schema({})
    boolean_history = _object_schema(
        {"include_inactive": {"type": "boolean", "default": False}}
    )
    tools = [
        Tool(
            name="project_get_state",
            description="读取当前人工确认状态及项目版本。",
            inputSchema=empty,
            outputSchema=OBJECT_OUTPUT_SCHEMA,
            annotations=read_only,
        ),
        Tool(
            name="task_get_packet",
            description="读取既有 Task Packet 全文或 L0-L4 指定层。",
            inputSchema=_object_schema(
                {
                    "packet_id": {"type": "string", "minLength": 1},
                    "layer": {
                        "enum": ["FULL", "L0", "L1", "L2", "L3", "L4"],
                        "default": "FULL",
                    },
                },
                required=("packet_id",),
            ),
            outputSchema=OBJECT_OUTPUT_SCHEMA,
            annotations=read_only,
        ),
        Tool(
            name="evidence_get",
            description="按稳定 evidence_ref 回到来源版本或会议原话。",
            inputSchema=_object_schema(
                {"evidence_ref": {"type": "string", "minLength": 1}},
                required=("evidence_ref",),
            ),
            outputSchema=OBJECT_OUTPUT_SCHEMA,
            annotations=read_only,
        ),
        Tool(
            name="decision_list",
            description="列出当前决定；只有显式请求才返回失效历史。",
            inputSchema=boolean_history,
            outputSchema=OBJECT_OUTPUT_SCHEMA,
            annotations=read_only,
        ),
        Tool(
            name="open_question_list",
            description="列出当前仍未解决的开放问题。",
            inputSchema=boolean_history,
            outputSchema=OBJECT_OUTPUT_SCHEMA,
            annotations=read_only,
        ),
        Tool(
            name="proposal_create",
            description="创建带证据、待 Fox 确认的 Proposal；不会改变当前状态。",
            inputSchema=_proposal_create_schema(),
            outputSchema=OBJECT_OUTPUT_SCHEMA,
            annotations=proposal_write,
        ),
        Tool(
            name="proposal_get",
            description="读取一个 Proposal 的当前待确认状态。",
            inputSchema=_object_schema(
                {"proposal_id": {"type": "string", "minLength": 1}},
                required=("proposal_id",),
            ),
            outputSchema=OBJECT_OUTPUT_SCHEMA,
            annotations=read_only,
        ),
        Tool(
            name="system_doctor",
            description="检查本地存储、协议和 AI 工具白名单。",
            inputSchema=empty,
            outputSchema=OBJECT_OUTPUT_SCHEMA,
            annotations=read_only,
        ),
        Tool(
            name="project_verify",
            description="核对当前状态与证据概况；不代替 Fox 的业务验收。",
            inputSchema=empty,
            outputSchema=OBJECT_OUTPUT_SCHEMA,
            annotations=read_only,
        ),
    ]
    if tuple(tool.name for tool in tools) != MCP_TOOL_NAMES:
        raise RuntimeError("MCP 工具定义与应用层白名单不一致")
    return tools


def _proposal_create_schema() -> dict[str, object]:
    nullable_string = {"type": ["string", "null"], "minLength": 1}
    nullable_object = {"type": ["object", "null"]}
    return _object_schema(
        {
            "proposal_id": {"type": "string", "minLength": 1},
            "proposal_kind": {
                "enum": ["create", "update", "supersede", "link", "flag_conflict"]
            },
            "classification": {"type": "string", "minLength": 1},
            "subject_id": nullable_string,
            "before": nullable_object,
            "after": {"type": "object"},
            "reason": {"type": "string", "minLength": 1},
            "impact_scope": {"type": "string", "minLength": 1},
            "evidence_refs": {
                "type": "array",
                "minItems": 1,
                "uniqueItems": True,
                "items": {"type": "string", "minLength": 1},
            },
            "supersedes_proposal_id": nullable_string,
            "source_meeting_item_id": nullable_string,
            "valid_from": {
                "type": ["string", "null"],
                "minLength": 1,
                "format": "date-time",
            },
            "valid_until": {
                "type": ["string", "null"],
                "minLength": 1,
                "format": "date-time",
            },
            "expected_version": {"type": "integer", "minimum": 0},
            "idempotency_key": {"type": "string", "minLength": 1},
        },
        required=(
            "proposal_id",
            "proposal_kind",
            "classification",
            "after",
            "reason",
            "impact_scope",
            "evidence_refs",
            "expected_version",
            "idempotency_key",
        ),
    )


def create_mcp_server(
    service: LocalAIService,
    *,
    timeout_seconds: float = DEFAULT_TOOL_TIMEOUT_SECONDS,
) -> Server[object, object]:
    """创建只包含白名单工具的官方 MCP Server。"""

    gateway = LocalMCPGateway(service, timeout_seconds=timeout_seconds)
    server: Server[object, object] = Server(
        "brand-project-os",
        version=MCP_SERVER_VERSION,
        instructions=(
            "先读取 task_get_packet，再按引用读取证据。只能创建 Proposal，"
            "不能批准业务状态或切换工作模式。"
        ),
    )
    tools = build_mcp_tools()

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return tools

    @server.call_tool(validate_input=True)
    async def call_tool(
        name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        return await gateway.invoke(name, arguments)

    return server


async def _run_mcp_stdio(
    service: LocalAIService,
    timeout_seconds: float,
) -> None:
    server = create_mcp_server(service, timeout_seconds=timeout_seconds)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def run_mcp_stdio(
    service: LocalAIService,
    *,
    timeout_seconds: float = DEFAULT_TOOL_TIMEOUT_SECONDS,
) -> None:
    """在当前进程运行 stdio MCP；stdout 只用于协议帧。"""

    anyio.run(_run_mcp_stdio, service, timeout_seconds)
