"""本地应用服务、模型切换和 MCP 超时取消测试。"""

from __future__ import annotations

import asyncio
import tempfile
import threading
import time
import unittest
from pathlib import Path

from brand_os.domain import Actor, ActorKind, CommandContext
from brand_os.local_access import (
    FORBIDDEN_AI_OPERATIONS,
    MCP_TOOL_NAMES,
    LocalAIService,
    ModelNotAllowed,
    ToolNotAllowed,
)
from brand_os.mcp_server import LocalMCPGateway, ToolCallTimeout
from brand_os.sqlite_store import SQLiteCanonicalStore
from brand_os.task_packets import RuntimeTaskDefinition


class LocalAIServiceTest(unittest.TestCase):
    """验证所有 AI 入口共享项目范围、Packet 和权限边界。"""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = self.root / "project.db"
        self.store = SQLiteCanonicalStore(self.database)
        self.fox = Actor(ActorKind.HUMAN, "Fox")
        self.system = Actor(ActorKind.SYSTEM, "packet-builder")
        self.store.create_project(
            CommandContext("hongri", self.fox, "project", 0), "鸿日"
        )
        task = RuntimeTaskDefinition(
            task_id="task-ai-access",
            goal="让两个模型读取同一份任务上下文",
            role="BRAND_RESEARCHER",
            work_mode="EVALUATION",
            deliverables=("证据一致的结论",),
            non_goals=("不批准业务状态",),
            context_refs=(),
            evidence_refs=(),
            known_gap_ids=(),
            allowed_tools=("task_get_packet", "evidence_get", "proposal_create"),
            network="deny",
            model_allowlist=("codex", "claude"),
            output_schema_ref="state-proposal.v1",
            acceptance_criteria=("两个模型使用相同事实和证据",),
        )
        self.store.register_runtime_task(
            "hongri", self.fox, task, idempotency_key="register-task"
        )
        self.packet = self.store.build_task_packet(
            "hongri", task.task_id, self.system
        )
        self.service = LocalAIService(
            self.store,
            "hongri",
            caller_id="brand-os-test",
            caller_kind=ActorKind.WORKFLOW,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_allowlist_has_no_business_approval_or_storage_escape(self) -> None:
        doctor = self.service.invoke("system_doctor", {})

        self.assertEqual(tuple(doctor["allowed_tools"]), MCP_TOOL_NAMES)
        self.assertEqual(
            set(doctor["forbidden_operations"]), set(FORBIDDEN_AI_OPERATIONS)
        )
        self.assertTrue(set(MCP_TOOL_NAMES).isdisjoint(FORBIDDEN_AI_OPERATIONS))
        self.assertFalse(doctor["provider_credentials_read"])
        self.assertNotIn("database", doctor)
        with self.assertRaises(ToolNotAllowed):
            self.service.invoke("proposal_approve", {"proposal_id": "P-1"})
        with self.assertRaises(ValueError):
            LocalAIService(
                self.store,
                "hongri",
                caller_id="Fox",
                caller_kind=ActorKind.HUMAN,
            )

    def test_packet_evidence_and_proposal_use_one_application_service(self) -> None:
        state_before = self.service.invoke("project_get_state", {})
        packet = self.service.invoke(
            "task_get_packet",
            {"packet_id": self.packet["packet_id"], "layer": "FULL"},
        )
        evidence = self.service.invoke(
            "evidence_get", {"evidence_ref": "evidence:not-registered"}
        )
        proposal = self.service.invoke(
            "proposal_create",
            {
                "proposal_id": "proposal-from-mcp",
                "proposal_kind": "create",
                "classification": "OPEN",
                "subject_id": "question-from-mcp",
                "after": {
                    "id": "question-from-mcp",
                    "question": "主推版本是否已经确认",
                },
                "reason": "当前 Packet 没有这项确认",
                "impact_scope": "本轮模型评估",
                "evidence_refs": ["evidence:not-registered"],
                "expected_version": state_before["state_version"],
                "idempotency_key": "proposal-from-mcp",
            },
        )
        proposal_view = self.service.invoke(
            "proposal_get", {"proposal_id": "proposal-from-mcp"}
        )
        state_after = self.service.invoke("project_get_state", {})

        self.assertEqual(packet["content_hash"], self.packet["content_hash"])
        self.assertEqual(evidence["verification"], "unconfirmed")
        self.assertFalse(proposal["changes_current_state"])
        self.assertEqual(proposal_view["proposal"]["status"], "proposed")
        self.assertEqual(state_before["items"], [])
        self.assertEqual(state_after["items"], [])
        self.assertEqual(
            state_after["state_version"], state_before["state_version"] + 1
        )

    def test_codex_and_claude_runs_keep_the_same_packet_and_state(self) -> None:
        codex = self.service.start_agent_run(
            packet_id=self.packet["packet_id"],
            packet_hash=self.packet["content_hash"],
            runtime_name="codex",
            runtime_version="1.0.0",
            model_id="codex",
            model_version="gpt-5",
            run_id="run-codex",
            idempotency_key="run-codex",
        )
        claude = self.service.start_agent_run(
            packet_id=self.packet["packet_id"],
            packet_hash=self.packet["content_hash"],
            runtime_name="claude",
            runtime_version="1.0.0",
            model_id="claude",
            model_version="sonnet",
            run_id="run-claude",
            idempotency_key="run-claude",
        )

        for field in (
            "packet_id",
            "packet_hash",
            "packet_version",
            "task_revision",
            "base_state_version",
            "role",
            "work_mode",
            "protocol_versions",
        ):
            self.assertEqual(codex[field], claude[field])
        self.assertEqual(codex["runtime_id"], "codex-cli")
        self.assertEqual(claude["runtime_id"], "claude-code")
        with self.assertRaises(ModelNotAllowed):
            self.service.start_agent_run(
                packet_id=self.packet["packet_id"],
                packet_hash=self.packet["content_hash"],
                runtime_name="codex",
                runtime_version="1.0.0",
                model_id="unapproved-model",
                model_version="1",
                run_id="run-forbidden",
                idempotency_key="run-forbidden",
            )

    def test_codex_and_claude_configs_share_one_credential_free_mcp(self) -> None:
        codex = self.service.adapter_config(
            "codex", workspace_root=self.root, database_path=self.database
        )
        claude = self.service.adapter_config(
            "claude", workspace_root=self.root, database_path=self.database
        )

        self.assertEqual(codex["mcp_server"], claude["mcp_server"])
        self.assertEqual(codex["credential_policy"], "runtime_managed")
        self.assertEqual(claude["credential_policy"], "runtime_managed")
        self.assertFalse(codex["brand_os_reads_provider_credentials"])
        self.assertNotIn("env", codex["mcp_server"])
        self.assertNotIn("env", claude["mcp_server"])

    def test_verify_does_not_claim_business_acceptance(self) -> None:
        result = self.service.invoke("project_verify", {})

        self.assertTrue(result["verified"])
        self.assertIn("Fox", result["note"])
        self.assertEqual(result["decision_count"], 0)
        self.assertEqual(result["open_question_count"], 0)


class _BlockingService:
    def __init__(self, release: threading.Event) -> None:
        self.release = release

    def invoke(self, tool_name: str, arguments: dict[str, object]):
        self.release.wait(1)
        return {"tool": tool_name, "arguments": arguments}


class LocalMCPGatewayTest(unittest.TestCase):
    """验证 stdio MCP 的应用调用可以超时和由客户端取消。"""

    def test_timeout_returns_retryable_error(self) -> None:
        release = threading.Event()
        gateway = LocalMCPGateway(_BlockingService(release), timeout_seconds=0.01)  # type: ignore[arg-type]
        try:
            with self.assertRaises(ToolCallTimeout) as raised:
                asyncio.run(gateway.invoke("project_get_state", {}))
            self.assertIn("同一幂等键", str(raised.exception))
        finally:
            release.set()

    def test_cancellation_is_not_converted_to_success(self) -> None:
        release = threading.Event()
        gateway = LocalMCPGateway(_BlockingService(release), timeout_seconds=1)  # type: ignore[arg-type]

        async def scenario() -> None:
            task = asyncio.create_task(gateway.invoke("project_get_state", {}))
            await asyncio.sleep(0.01)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        try:
            asyncio.run(scenario())
        finally:
            release.set()
            time.sleep(0.01)


if __name__ == "__main__":
    unittest.main()
