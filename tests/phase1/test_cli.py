"""本地 CLI 组合入口测试。"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from brand_os.cli import main


class BrandOSCLITest(unittest.TestCase):
    """验证 CLI 只输出结构化结果且没有审批命令。"""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "workspace"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def invoke(self, *arguments: str) -> tuple[int, dict[str, object], str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = main(["--workspace", str(self.root), *arguments])
        payload = json.loads(stdout.getvalue() or stderr.getvalue())
        return status, payload, stderr.getvalue()

    def test_init_status_doctor_and_verify_share_the_same_database(self) -> None:
        init_status, initialized, _ = self.invoke("init")
        status_code, state, _ = self.invoke("status")
        doctor_code, doctor, _ = self.invoke("doctor")
        verify_code, verification, _ = self.invoke("verify")

        self.assertEqual(init_status, 0)
        self.assertTrue(initialized["created"])
        self.assertEqual(status_code, 0)
        self.assertEqual(state["project_id"], "hongri")
        self.assertEqual(doctor_code, 0)
        self.assertEqual(doctor["status"], "ok")
        self.assertEqual(verify_code, 0)
        self.assertTrue(verification["verified"])

    def test_missing_database_fails_without_creating_one(self) -> None:
        status, error, stderr = self.invoke("status")

        self.assertEqual(status, 2)
        self.assertIn("未找到本地数据库", error["message"])
        self.assertTrue(stderr)
        self.assertFalse((self.root / ".fox" / "state" / "project.db").exists())

    def test_adapter_configs_have_no_provider_credentials(self) -> None:
        self.invoke("init")
        codex_status, codex, _ = self.invoke(
            "adapter", "show", "--runtime", "codex"
        )
        claude_status, claude, _ = self.invoke(
            "adapter", "show", "--runtime", "claude"
        )

        self.assertEqual(codex_status, 0)
        self.assertEqual(claude_status, 0)
        self.assertEqual(codex["mcp_server"], claude["mcp_server"])
        self.assertFalse(codex["brand_os_reads_provider_credentials"])
        self.assertFalse(claude["brand_os_reads_provider_credentials"])

    def test_proposal_create_reads_bounded_json_and_never_approves(self) -> None:
        self.invoke("init")
        status_code, state, _ = self.invoke("status")
        self.assertEqual(status_code, 0)
        request = {
            "proposal_id": "proposal-cli",
            "proposal_kind": "create",
            "classification": "OPEN",
            "subject_id": "question-cli",
            "after": {"id": "question-cli", "question": "这一项是否确认"},
            "reason": "等待 Fox 确认",
            "impact_scope": "CLI 测试",
            "evidence_refs": ["evidence:missing"],
            "expected_version": state["state_version"],
            "idempotency_key": "proposal-cli",
        }
        input_path = self.root / ".fox" / "runtime" / "proposal.json"
        input_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")

        create_status, result, _ = self.invoke(
            "proposal", "create", "--input", str(input_path)
        )
        get_status, proposal, _ = self.invoke(
            "proposal", "get", "--proposal-id", "proposal-cli"
        )

        self.assertEqual(create_status, 0)
        self.assertFalse(result["changes_current_state"])
        self.assertEqual(get_status, 0)
        self.assertEqual(proposal["proposal"]["status"], "proposed")


if __name__ == "__main__":
    unittest.main()
