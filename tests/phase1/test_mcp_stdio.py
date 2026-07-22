"""官方 MCP SDK 的真实 stdio 进程互操作测试。"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from brand_os.domain import Actor, ActorKind, CommandContext
from brand_os.local_access import MCP_TOOL_NAMES
from brand_os.sqlite_store import SQLiteCanonicalStore
from brand_os.task_packets import RuntimeTaskDefinition
from brand_os.workspace import WorkspaceLayout


class MCPStdioIntegrationTest(unittest.TestCase):
    """验证客户端能列工具、读同一 Packet 并创建待确认 Proposal。"""

    def test_stdio_round_trip_uses_official_protocol_and_fixed_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            layout = WorkspaceLayout.from_root(root)
            layout.state.mkdir(parents=True)
            database = layout.state / "project.db"
            store = SQLiteCanonicalStore(database)
            fox = Actor(ActorKind.HUMAN, "Fox")
            system = Actor(ActorKind.SYSTEM, "test")
            store.create_project(
                CommandContext("hongri", fox, "project", 0), "鸿日"
            )
            task = RuntimeTaskDefinition(
                task_id="stdio-task",
                goal="验证 MCP 读取同一 Packet",
                role="BRAND_RESEARCHER",
                work_mode="EVALUATION",
                deliverables=("一致结果",),
                non_goals=("不批准状态",),
                context_refs=(),
                evidence_refs=(),
                known_gap_ids=(),
                allowed_tools=("task_get_packet", "proposal_create"),
                network="deny",
                model_allowlist=("codex", "claude"),
                output_schema_ref="state-proposal.v1",
                acceptance_criteria=("Packet 哈希一致",),
            )
            store.register_runtime_task(
                "hongri", fox, task, idempotency_key="task"
            )
            packet = store.build_task_packet("hongri", task.task_id, system)

            async def round_trip() -> tuple[
                set[str], dict[str, object], bool, bool, bool
            ]:
                parameters = StdioServerParameters(
                    command=sys.executable,
                    args=[
                        "-m",
                        "brand_os.cli",
                        "--workspace",
                        str(root),
                        "--project",
                        "hongri",
                        "mcp",
                    ],
                    env=os.environ.copy(),
                    cwd=Path(__file__).parents[2],
                )
                async with stdio_client(parameters) as (read_stream, write_stream):
                    async with ClientSession(
                        read_stream,
                        write_stream,
                        read_timeout_seconds=timedelta(seconds=5),
                    ) as session:
                        await session.initialize()
                        listed = await session.list_tools()
                        packet_result = await session.call_tool(
                            "task_get_packet",
                            {"packet_id": packet["packet_id"], "layer": "FULL"},
                        )
                        proposal_result = await session.call_tool(
                            "proposal_create",
                            {
                                "proposal_id": "proposal-stdio",
                                "proposal_kind": "create",
                                "classification": "OPEN",
                                "subject_id": "question-stdio",
                                "after": {
                                    "id": "question-stdio",
                                    "question": "MCP 输出是否由 Fox 确认",
                                },
                                "reason": "MCP 只能生成待确认项",
                                "impact_scope": "stdio 验证",
                                "evidence_refs": ["evidence:stdio"],
                                "expected_version": store.get_project_version("hongri"),
                                "idempotency_key": "proposal-stdio",
                            },
                        )
                        invalid_result = await session.call_tool(
                            "project_get_state", {"direct_sql": "SELECT 1"}
                        )
                        tool_by_name = {tool.name: tool for tool in listed.tools}
                        return (
                            set(tool_by_name),
                            packet_result.structuredContent or {},
                            proposal_result.isError,
                            invalid_result.isError,
                            all(
                                tool.inputSchema.get("additionalProperties") is False
                                for tool in tool_by_name.values()
                            ),
                        )

            (
                tool_names,
                packet_value,
                proposal_is_error,
                invalid_is_error,
                all_schemas_closed,
            ) = asyncio.run(round_trip())
            reopened = SQLiteCanonicalStore(database)
            proposals = reopened.list_proposals("hongri")

            self.assertEqual(tool_names, set(MCP_TOOL_NAMES))
            self.assertNotIn("proposal_approve", tool_names)
            self.assertEqual(packet_value["content_hash"], packet["content_hash"])
            self.assertFalse(proposal_is_error)
            self.assertTrue(invalid_is_error)
            self.assertTrue(all_schemas_closed)
            self.assertEqual(len(proposals), 1)
            self.assertEqual(proposals[0]["status"], "proposed")
            self.assertEqual(reopened.get_current_state("hongri"), [])


if __name__ == "__main__":
    unittest.main()
