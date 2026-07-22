"""Brand Project OS Service 组件职责契约测试。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from brand_os.server_baseline import SERVER_BOUNDARY, validate_server_boundary


ROOT = Path(__file__).parents[2]
CONTRACT_PATH = ROOT / "contracts" / "phase2" / "server-boundary.json"


class ServerBoundaryContractTest(unittest.TestCase):
    """验证服务器业务边界不会被运行时或外部组件穿透。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))

    def test_machine_contract_matches_runtime_boundary(self) -> None:
        self.assertEqual(self.contract, SERVER_BOUNDARY)
        self.assertEqual(validate_server_boundary(self.contract), ())
        self.assertEqual(self.contract["schema_version"], "server-boundary.v4")

    def test_only_application_service_may_advance_formal_state(self) -> None:
        advancing = [
            component["id"]
            for component in self.contract["components"]
            if component["may_advance_formal_state"]
        ]

        self.assertEqual(advancing, ["application_service"])
        self.assertTrue(
            self.contract["authority"][
                "human_review_requires_interactive_employee_identity"
            ]
        )

    def test_mcp_workflow_and_service_accounts_cannot_approve(self) -> None:
        components = {
            component["id"]: component for component in self.contract["components"]
        }
        for component_id in ("mcp_gateway", "workflow_adapter", "openwork_runtime"):
            component = components[component_id]
            self.assertEqual(set(component["business_operations"]), {"read", "create_proposal"})
            self.assertIn("human_review", component["forbidden_operations"])
            self.assertFalse(component["may_advance_formal_state"])

    def test_openwork_server_is_runtime_not_business_service(self) -> None:
        runtime = next(
            component
            for component in self.contract["components"]
            if component["id"] == "openwork_runtime"
        )

        self.assertEqual(runtime["kind"], "agent_runtime")
        self.assertFalse(runtime["required_for_core_readiness"])
        self.assertFalse(runtime["stores_formal_business_state"])
        self.assertIn("session_state", runtime["stores"])

    def test_outbox_worker_cannot_write_formal_state(self) -> None:
        worker = next(
            component
            for component in self.contract["components"]
            if component["id"] == "outbox_worker"
        )

        self.assertEqual(worker["kind"], "background_worker")
        self.assertFalse(worker["may_advance_formal_state"])
        self.assertFalse(worker["required_for_core_readiness"])
        self.assertIn("direct_formal_table_write", worker["forbidden_operations"])

    def test_oidc_authenticates_employee_without_business_approval(self) -> None:
        identity = next(
            component
            for component in self.contract["components"]
            if component["id"] == "oidc_identity_adapter"
        )

        self.assertEqual(identity["business_operations"], ["authenticate_employee"])
        self.assertIn("human_review", identity["forbidden_operations"])
        self.assertFalse(identity["may_advance_formal_state"])
        self.assertIn("oidc", self.contract["readiness"]["required_dependencies"])

    def test_project_authorization_is_required_before_storage(self) -> None:
        authorization = next(
            component
            for component in self.contract["components"]
            if component["id"] == "project_authorization_service"
        )

        self.assertTrue(
            self.contract["authority"]["project_authorization_precedes_storage"]
        )
        self.assertTrue(self.contract["authority"]["rls_is_defense_in_depth"])
        self.assertTrue(authorization["required_for_core_readiness"])
        self.assertIn("human_review", authorization["forbidden_operations"])

    def test_storage_and_optional_adapters_are_replaceable(self) -> None:
        adapters = [
            component
            for component in self.contract["components"]
            if component["kind"] in {"storage_adapter", "optional_adapter"}
        ]

        self.assertTrue(adapters)
        self.assertTrue(all(component["replaceable"] for component in adapters))
        self.assertFalse(self.contract["authority"]["client_may_access_storage_directly"])

    def test_later_phase_implementations_are_explicitly_deferred(self) -> None:
        self.assertEqual(
            set(self.contract["deferred_from_f2_1"]),
            {
                "http_and_mcp_routes",
                "hongri_data_migration",
            },
        )


if __name__ == "__main__":
    unittest.main()
