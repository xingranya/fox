"""服务器存活、就绪与依赖降级语义测试。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from brand_os.server_baseline import build_liveness_report, build_readiness_report
from brand_os.server_config import load_server_settings


ROOT = Path(__file__).parents[2]
HEALTH_SCHEMA_PATH = ROOT / "schemas" / "phase2" / "service-health.schema.json"


def valid_settings():
    """返回不包含真实凭据的测试环境完整配置。"""

    return load_server_settings(
        explicit={
            "environment": "test",
            "public_base_url": "http://service.test",
            "database_dsn": "postgresql://test:test@postgres/brand_os",
            "object_store_endpoint": "http://object-store.test",
            "object_store_bucket": "brand-os-test",
            "object_store_access_key": "test-access",
            "object_store_secret_key": "test-secret",
            "oidc_issuer_url": "http://oidc.test",
            "oidc_client_id": "brand-os-test",
            "oidc_client_secret": "test-oidc-secret",
        },
        environ={},
    )


class ServiceHealthTest(unittest.TestCase):
    """验证核心依赖和可选组件故障采用不同就绪语义。"""

    def test_liveness_does_not_probe_dependencies(self) -> None:
        report = build_liveness_report()

        self.assertEqual(report.check, "live")
        self.assertEqual(report.status, "live")
        self.assertEqual(report.dependencies, ())

    def test_readiness_fails_when_required_configuration_is_missing(self) -> None:
        report = build_readiness_report(
            load_server_settings(environ={}),
            dependency_states={
                "postgresql": True,
                "schema": True,
                "object_storage": True,
            },
        )

        self.assertEqual(report.status, "not_ready")
        self.assertTrue(report.issues)

    def test_required_dependency_failure_blocks_readiness(self) -> None:
        report = build_readiness_report(
            valid_settings(),
            dependency_states={
                "postgresql": False,
                "schema": True,
                "object_storage": True,
            },
        )

        self.assertEqual(report.status, "not_ready")
        self.assertIn("postgresql", report.blocking_dependencies)

    def test_optional_failures_do_not_block_core_readiness(self) -> None:
        report = build_readiness_report(
            valid_settings(),
            dependency_states={
                "postgresql": True,
                "schema": True,
                "object_storage": True,
                "dify": False,
                "zvec": False,
                "openwork_runtime": False,
            },
        )

        self.assertEqual(report.status, "ready")
        self.assertEqual(report.blocking_dependencies, ())
        self.assertEqual(set(report.degraded_dependencies), {"dify", "zvec", "openwork_runtime"})

    def test_health_serialization_never_contains_secret_values(self) -> None:
        settings = valid_settings()
        report = build_readiness_report(
            settings,
            dependency_states={
                "postgresql": True,
                "schema": True,
                "object_storage": True,
            },
        )

        rendered = json.dumps(report.to_dict(), ensure_ascii=False)

        for secret in (
            "postgresql://test:test@postgres/brand_os",
            "test-access",
            "test-secret",
            "test-oidc-secret",
        ):
            self.assertNotIn(secret, rendered)
        self.assertEqual(report.to_dict()["schema_version"], "service-health.v1")

    def test_health_schema_keeps_live_and_ready_checks_separate(self) -> None:
        schema = json.loads(HEALTH_SCHEMA_PATH.read_text(encoding="utf-8"))

        self.assertEqual(schema["properties"]["schema_version"]["const"], "service-health.v1")
        self.assertEqual(set(schema["properties"]["check"]["enum"]), {"live", "ready"})
        self.assertFalse(schema["additionalProperties"])


if __name__ == "__main__":
    unittest.main()
