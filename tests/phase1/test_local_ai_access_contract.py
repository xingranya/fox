"""本地 CLI/MCP 和模型适配机器契约测试。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from brand_os.local_access import FORBIDDEN_AI_OPERATIONS, MCP_TOOL_NAMES
from brand_os.mcp_server import (
    DEFAULT_TOOL_TIMEOUT_SECONDS,
    MAX_TOOL_TIMEOUT_SECONDS,
    build_mcp_tools,
)
from brand_os.runtime_adapters import RUNTIME_ADAPTERS


ROOT = Path(__file__).parents[2]
CONTRACT_PATH = ROOT / "contracts" / "phase1" / "local-ai-access.json"
PROPOSAL_SCHEMA_PATH = (
    ROOT / "schemas" / "phase1" / "proposal-create-input.schema.json"
)


class LocalAIAccessContractTest(unittest.TestCase):
    """确保文档化工具表与官方 MCP 实际 Schema 完全一致。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        cls.proposal_schema = json.loads(
            PROPOSAL_SCHEMA_PATH.read_text(encoding="utf-8")
        )
        cls.tools = build_mcp_tools()

    def test_contract_tools_match_application_and_mcp_exactly(self) -> None:
        contract_names = tuple(tool["name"] for tool in self.contract["tools"])
        actual_names = tuple(tool.name for tool in self.tools)

        self.assertEqual(self.contract["schema_version"], "local-ai-access.v1")
        self.assertEqual(contract_names, MCP_TOOL_NAMES)
        self.assertEqual(actual_names, MCP_TOOL_NAMES)
        self.assertEqual(
            tuple(self.contract["forbidden_operations"]), FORBIDDEN_AI_OPERATIONS
        )
        self.assertTrue(set(actual_names).isdisjoint(FORBIDDEN_AI_OPERATIONS))

    def test_all_mcp_inputs_are_closed_and_proposal_schema_is_frozen(self) -> None:
        tool_by_name = {tool.name: tool for tool in self.tools}

        self.assertTrue(
            all(
                tool.inputSchema["additionalProperties"] is False
                for tool in self.tools
            )
        )
        self.assertEqual(
            tool_by_name["proposal_create"].inputSchema,
            {
                key: value
                for key, value in self.proposal_schema.items()
                if key not in {"$schema", "$id", "title"}
            },
        )
        self.assertFalse(
            tool_by_name["proposal_create"].annotations.readOnlyHint
        )
        self.assertFalse(
            tool_by_name["proposal_create"].annotations.destructiveHint
        )

    def test_timeout_and_runtime_profiles_match_contract(self) -> None:
        policy = self.contract["execution_policy"]
        adapters = {
            value["name"]: value for value in self.contract["runtime_adapters"]
        }

        self.assertEqual(
            policy["default_timeout_seconds"], DEFAULT_TOOL_TIMEOUT_SECONDS
        )
        self.assertEqual(
            policy["maximum_timeout_seconds"], MAX_TOOL_TIMEOUT_SECONDS
        )
        self.assertEqual(set(adapters), set(RUNTIME_ADAPTERS))
        for name, profile in RUNTIME_ADAPTERS.items():
            self.assertEqual(adapters[name]["runtime_id"], profile.runtime_id)
            self.assertEqual(
                adapters[name]["credential_policy"], "runtime_managed"
            )
            self.assertFalse(profile.brand_os_reads_provider_credentials)


if __name__ == "__main__":
    unittest.main()
