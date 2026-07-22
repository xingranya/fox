"""服务器配置、环境分层与秘密注入测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from brand_os.config import ConfigurationError
from brand_os.server_config import ServerEnvironment, load_server_settings


ROOT = Path(__file__).parents[2]
CONFIG_SCHEMA_PATH = ROOT / "schemas" / "phase2" / "service-config.schema.json"


class ServerConfigTest(unittest.TestCase):
    """验证服务器配置来源、生产约束和脱敏边界。"""

    def test_explicit_values_override_environment_file_and_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config_path = base / "server.json"
            config_path.write_text(
                json.dumps(
                    {
                        "environment": "development",
                        "public_base_url": "http://file.example.test",
                        "database_pool_size": 3,
                    }
                ),
                encoding="utf-8",
            )

            settings = load_server_settings(
                explicit={
                    "environment": "test",
                    "public_base_url": "http://explicit.example.test",
                    "database_pool_size": 9,
                },
                config_path=config_path,
                environ={
                    "BRAND_OS_SERVER_ENVIRONMENT": "production",
                    "BRAND_OS_SERVER_PUBLIC_BASE_URL": "https://env.example.test",
                    "BRAND_OS_SERVER_DATABASE_POOL_SIZE": "6",
                },
            )

            self.assertEqual(settings.environment, ServerEnvironment.TEST)
            self.assertEqual(settings.public_base_url, "http://explicit.example.test")
            self.assertEqual(settings.database_pool_size, 9)

    def test_environment_values_override_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config_path = base / "server.json"
            config_path.write_text(
                json.dumps({"database_pool_size": 3}),
                encoding="utf-8",
            )

            settings = load_server_settings(
                config_path=config_path,
                environ={"BRAND_OS_SERVER_DATABASE_POOL_SIZE": "7"},
            )

            self.assertEqual(settings.database_pool_size, 7)

    def test_explicit_environment_accepts_public_enum(self) -> None:
        settings = load_server_settings(
            explicit={"environment": ServerEnvironment.TEST},
            environ={},
        )

        self.assertEqual(settings.environment, ServerEnvironment.TEST)

    def test_config_file_rejects_secret_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "server.json"
            config_path.write_text(
                json.dumps({"database_dsn": "postgresql://secret@db/brand_os"}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ConfigurationError, "敏感字段"):
                load_server_settings(config_path=config_path, environ={})

    def test_missing_secrets_fail_closed_without_leaking_values(self) -> None:
        settings = load_server_settings(
            explicit={
                "environment": "production",
                "public_base_url": "https://brand.example.com",
                "object_store_endpoint": "https://objects.example.com",
                "object_store_bucket": "brand-os",
                "oidc_issuer_url": "https://id.example.com",
                "oidc_client_id": "brand-os-service",
            },
            environ={},
        )

        issues = settings.validation_issues()

        self.assertEqual(
            {issue.field for issue in issues if issue.code == "missing_secret"},
            {
                "database_dsn",
                "object_store_access_key",
                "object_store_secret_key",
                "oidc_client_secret",
                "session_encryption_key",
            },
        )
        self.assertFalse(settings.safe_dict()["secrets"]["database_dsn"]["configured"])

    def test_repr_and_safe_serialization_redact_secret_values(self) -> None:
        secrets = {
            "BRAND_OS_SERVER_DATABASE_DSN": "postgresql://admin:db-password@db/brand_os",
            "BRAND_OS_SERVER_OBJECT_STORE_ACCESS_KEY": "object-access-key",
            "BRAND_OS_SERVER_OBJECT_STORE_SECRET_KEY": "object-secret-key",
            "BRAND_OS_SERVER_OIDC_CLIENT_SECRET": "oidc-client-secret",
            "BRAND_OS_SERVER_SESSION_ENCRYPTION_KEY": "session-encryption-key",
        }
        settings = load_server_settings(environ=secrets)

        rendered = repr(settings) + json.dumps(settings.safe_dict(), ensure_ascii=False)

        for value in secrets.values():
            self.assertNotIn(value, rendered)
        self.assertTrue(settings.safe_dict()["secrets"]["database_dsn"]["configured"])

    def test_production_requires_https_and_postgresql(self) -> None:
        settings = load_server_settings(
            explicit={
                "environment": "production",
                "public_base_url": "http://brand.example.com",
                "database_dsn": "sqlite:///brand.db",
                "object_store_endpoint": "http://objects.example.com",
                "object_store_bucket": "brand-os",
                "object_store_access_key": "access",
                "object_store_secret_key": "secret",
                "oidc_issuer_url": "http://id.example.com",
                "oidc_client_id": "brand-os-service",
                "oidc_client_secret": "secret",
                "session_encryption_key": "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
            },
            environ={},
        )

        issue_codes = {issue.code for issue in settings.validation_issues()}

        self.assertTrue(
            {
                "public_https_required",
                "postgresql_required",
                "object_store_https_required",
                "oidc_https_required",
            }.issubset(issue_codes)
        )

    def test_session_encryption_key_must_be_valid_without_leaking_value(self) -> None:
        invalid_key = "not-a-fernet-key"
        settings = load_server_settings(
            explicit={"session_encryption_key": invalid_key},
            environ={},
        )

        issues = settings.validation_issues()

        self.assertIn("invalid_session_encryption_key", {issue.code for issue in issues})
        self.assertNotIn(invalid_key, json.dumps(settings.safe_dict(), ensure_ascii=False))

    def test_safe_config_schema_cannot_serialize_secret_values(self) -> None:
        schema = json.loads(CONFIG_SCHEMA_PATH.read_text(encoding="utf-8"))
        secret_status = schema["$defs"]["secretStatus"]

        self.assertEqual(schema["properties"]["schema_version"]["const"], "service-config.v2")
        self.assertFalse(secret_status["additionalProperties"])
        self.assertEqual(set(secret_status["properties"]), {"configured"})
        self.assertIn(
            "session_encryption_key",
            schema["properties"]["secrets"]["required"],
        )


if __name__ == "__main__":
    unittest.main()
