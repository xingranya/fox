"""加载服务器配置，并确保秘密不会进入文件或诊断输出。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse

from .config import ConfigurationError


ENV_CONFIG_PATH = "BRAND_OS_SERVER_CONFIG"
ENV_BY_FIELD = {
    "environment": "BRAND_OS_SERVER_ENVIRONMENT",
    "public_base_url": "BRAND_OS_SERVER_PUBLIC_BASE_URL",
    "database_dsn": "BRAND_OS_SERVER_DATABASE_DSN",
    "database_pool_size": "BRAND_OS_SERVER_DATABASE_POOL_SIZE",
    "object_store_endpoint": "BRAND_OS_SERVER_OBJECT_STORE_ENDPOINT",
    "object_store_bucket": "BRAND_OS_SERVER_OBJECT_STORE_BUCKET",
    "object_store_access_key": "BRAND_OS_SERVER_OBJECT_STORE_ACCESS_KEY",
    "object_store_secret_key": "BRAND_OS_SERVER_OBJECT_STORE_SECRET_KEY",
    "oidc_issuer_url": "BRAND_OS_SERVER_OIDC_ISSUER_URL",
    "oidc_client_id": "BRAND_OS_SERVER_OIDC_CLIENT_ID",
    "oidc_client_secret": "BRAND_OS_SERVER_OIDC_CLIENT_SECRET",
    "dependency_timeout_seconds": "BRAND_OS_SERVER_DEPENDENCY_TIMEOUT_SECONDS",
}
NON_SECRET_FIELDS = {
    "environment",
    "public_base_url",
    "database_pool_size",
    "object_store_endpoint",
    "object_store_bucket",
    "oidc_issuer_url",
    "oidc_client_id",
    "dependency_timeout_seconds",
}
SECRET_FIELDS = {
    "database_dsn",
    "object_store_access_key",
    "object_store_secret_key",
    "oidc_client_secret",
}
ALL_FIELDS = NON_SECRET_FIELDS | SECRET_FIELDS
DEFAULTS: Mapping[str, object] = {
    "environment": "development",
    "public_base_url": "http://127.0.0.1:8765",
    "database_pool_size": 10,
    "object_store_endpoint": None,
    "object_store_bucket": None,
    "oidc_issuer_url": None,
    "oidc_client_id": None,
    "dependency_timeout_seconds": 3.0,
}


class ServerEnvironment(str, Enum):
    """服务器支持的环境分层。"""

    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class SecretValue:
    """保存只能由基础设施适配器显式取出的敏感值。"""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if not value:
            raise ConfigurationError("敏感配置值不能为空")
        self._value = value

    def reveal(self) -> str:
        """只供需要建立外部连接的适配器读取原值。"""

        return self._value

    def __repr__(self) -> str:
        return "SecretValue(***)"

    def __str__(self) -> str:
        return "***"


@dataclass(frozen=True, slots=True)
class ConfigurationIssue:
    """描述不包含配置值的启动校验问题。"""

    code: str
    field: str
    message: str

    def to_dict(self) -> dict[str, str]:
        """返回可用于健康报告的安全结构。"""

        return {"code": self.code, "field": self.field, "message": self.message}


@dataclass(frozen=True, slots=True)
class ServerSettings:
    """描述 Brand Project OS Service 的环境与连接配置。"""

    environment: ServerEnvironment
    public_base_url: str
    database_pool_size: int
    object_store_endpoint: str | None
    object_store_bucket: str | None
    oidc_issuer_url: str | None
    oidc_client_id: str | None
    dependency_timeout_seconds: float
    database_dsn: SecretValue | None = field(repr=False)
    object_store_access_key: SecretValue | None = field(repr=False)
    object_store_secret_key: SecretValue | None = field(repr=False)
    oidc_client_secret: SecretValue | None = field(repr=False)
    schema_version: str = field(default="service-config.v1", init=False)

    def validation_issues(self) -> tuple[ConfigurationIssue, ...]:
        """校验启动必需配置和生产环境安全约束。"""

        issues: list[ConfigurationIssue] = []
        required_values = {
            "object_store_endpoint": self.object_store_endpoint,
            "object_store_bucket": self.object_store_bucket,
            "oidc_issuer_url": self.oidc_issuer_url,
            "oidc_client_id": self.oidc_client_id,
        }
        for field_name, value in required_values.items():
            if value is None or not value.strip():
                issues.append(
                    ConfigurationIssue(
                        "missing_config",
                        field_name,
                        f"缺少必需配置：{field_name}",
                    )
                )

        required_secrets = {
            "database_dsn": self.database_dsn,
            "object_store_access_key": self.object_store_access_key,
            "object_store_secret_key": self.object_store_secret_key,
            "oidc_client_secret": self.oidc_client_secret,
        }
        for field_name, value in required_secrets.items():
            if value is None:
                issues.append(
                    ConfigurationIssue(
                        "missing_secret",
                        field_name,
                        f"缺少必需秘密：{field_name}",
                    )
                )

        if self.database_pool_size < 1:
            issues.append(
                ConfigurationIssue(
                    "invalid_database_pool_size",
                    "database_pool_size",
                    "database_pool_size 必须大于 0",
                )
            )
        if self.dependency_timeout_seconds <= 0:
            issues.append(
                ConfigurationIssue(
                    "invalid_dependency_timeout",
                    "dependency_timeout_seconds",
                    "dependency_timeout_seconds 必须大于 0",
                )
            )

        if self.environment is ServerEnvironment.PRODUCTION:
            if not _uses_https(self.public_base_url):
                issues.append(
                    ConfigurationIssue(
                        "public_https_required",
                        "public_base_url",
                        "生产环境公开地址必须使用 HTTPS",
                    )
                )
            if self.database_dsn is not None and not self.database_dsn.reveal().startswith(
                ("postgresql://", "postgresql+psycopg://")
            ):
                issues.append(
                    ConfigurationIssue(
                        "postgresql_required",
                        "database_dsn",
                        "生产环境数据库必须使用 PostgreSQL DSN",
                    )
                )
            if self.object_store_endpoint and not _uses_https(self.object_store_endpoint):
                issues.append(
                    ConfigurationIssue(
                        "object_store_https_required",
                        "object_store_endpoint",
                        "生产环境对象存储地址必须使用 HTTPS",
                    )
                )
            if self.oidc_issuer_url and not _uses_https(self.oidc_issuer_url):
                issues.append(
                    ConfigurationIssue(
                        "oidc_https_required",
                        "oidc_issuer_url",
                        "生产环境 OIDC 发行方地址必须使用 HTTPS",
                    )
                )
        return tuple(issues)

    def require_valid(self) -> None:
        """在服务启动前拒绝缺失或不安全的配置。"""

        issues = self.validation_issues()
        if issues:
            summary = "；".join(issue.message for issue in issues)
            raise ConfigurationError(summary)

    def safe_dict(self) -> dict[str, object]:
        """序列化非敏感配置，只报告秘密是否已经注入。"""

        issues = self.validation_issues()
        return {
            "schema_version": self.schema_version,
            "environment": self.environment.value,
            "public_base_url": self.public_base_url,
            "database_pool_size": self.database_pool_size,
            "object_store_endpoint": self.object_store_endpoint,
            "object_store_bucket": self.object_store_bucket,
            "oidc_issuer_url": self.oidc_issuer_url,
            "oidc_client_id": self.oidc_client_id,
            "dependency_timeout_seconds": self.dependency_timeout_seconds,
            "secrets": {
                "database_dsn": {"configured": self.database_dsn is not None},
                "object_store_access_key": {
                    "configured": self.object_store_access_key is not None
                },
                "object_store_secret_key": {
                    "configured": self.object_store_secret_key is not None
                },
                "oidc_client_secret": {"configured": self.oidc_client_secret is not None},
            },
            "valid": not issues,
            "issues": [issue.to_dict() for issue in issues],
        }


def _uses_https(value: str) -> bool:
    """判断服务地址是否明确使用 HTTPS。"""

    return urlparse(value).scheme.lower() == "https"


def _load_config_file(path: Path) -> dict[str, object]:
    """读取只允许非敏感服务器字段的 JSON 文件。"""

    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"无法读取服务器配置文件：{path}") from exc
    if not isinstance(data, dict):
        raise ConfigurationError("服务器配置文件根节点必须是对象")
    secret_fields = sorted(set(data) & SECRET_FIELDS)
    if secret_fields:
        raise ConfigurationError(
            f"服务器配置文件包含敏感字段：{', '.join(secret_fields)}"
        )
    unknown = sorted(set(data) - NON_SECRET_FIELDS)
    if unknown:
        raise ConfigurationError(
            f"服务器配置文件包含不允许的字段：{', '.join(unknown)}"
        )
    return data


def _selected_value(
    field_name: str,
    explicit: Mapping[str, object],
    environ: Mapping[str, str],
    config: Mapping[str, object],
) -> object | None:
    """按显式参数、环境变量、文件、默认值选择配置。"""

    if field_name in explicit and explicit[field_name] is not None:
        return explicit[field_name]
    environment_value = environ.get(ENV_BY_FIELD[field_name])
    if environment_value:
        return environment_value
    if field_name in config:
        return config[field_name]
    return DEFAULTS.get(field_name)


def _string_or_none(field_name: str, value: object | None) -> str | None:
    """把可选配置规范为非空字符串。"""

    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ConfigurationError(f"{field_name} 必须是字符串")
    return value


def _positive_number(field_name: str, value: object, number_type: type[int] | type[float]):
    """解析整数或浮点配置，范围由启动校验统一报告。"""

    if isinstance(value, bool):
        raise ConfigurationError(f"{field_name} 必须是数字")
    try:
        return number_type(value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{field_name} 必须是数字") from exc


def _secret(field_name: str, value: object | None) -> SecretValue | None:
    """把显式或环境变量中的秘密包装为不可直接序列化的值。"""

    normalized = _string_or_none(field_name, value)
    return None if normalized is None else SecretValue(normalized)


def load_server_settings(
    *,
    explicit: Mapping[str, object] | None = None,
    config_path: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> ServerSettings:
    """按显式参数、环境变量、非敏感配置文件、默认值加载服务器配置。"""

    env = os.environ if environ is None else environ
    provided = {} if explicit is None else dict(explicit)
    unknown_explicit = sorted(set(provided) - ALL_FIELDS)
    if unknown_explicit:
        raise ConfigurationError(f"显式服务器配置包含未知字段：{', '.join(unknown_explicit)}")

    user_home = (home or Path.home()).expanduser().resolve(strict=False)
    selected_path = config_path or env.get(ENV_CONFIG_PATH)
    if selected_path is None:
        selected_path = user_home / ".config" / "brand-project-os" / "server.json"
    config = _load_config_file(Path(selected_path).expanduser().resolve(strict=False))

    raw = {
        field_name: _selected_value(field_name, provided, env, config)
        for field_name in ALL_FIELDS
    }
    environment_value = raw["environment"]
    try:
        environment = (
            environment_value
            if isinstance(environment_value, ServerEnvironment)
            else ServerEnvironment(str(environment_value))
        )
    except ValueError as exc:
        raise ConfigurationError(
            "environment 必须是 development、test 或 production"
        ) from exc

    public_base_url = _string_or_none("public_base_url", raw["public_base_url"])
    if public_base_url is None:
        raise ConfigurationError("public_base_url 不能为空")
    return ServerSettings(
        environment=environment,
        public_base_url=public_base_url,
        database_pool_size=_positive_number(
            "database_pool_size", raw["database_pool_size"], int
        ),
        object_store_endpoint=_string_or_none(
            "object_store_endpoint", raw["object_store_endpoint"]
        ),
        object_store_bucket=_string_or_none(
            "object_store_bucket", raw["object_store_bucket"]
        ),
        oidc_issuer_url=_string_or_none("oidc_issuer_url", raw["oidc_issuer_url"]),
        oidc_client_id=_string_or_none("oidc_client_id", raw["oidc_client_id"]),
        dependency_timeout_seconds=_positive_number(
            "dependency_timeout_seconds", raw["dependency_timeout_seconds"], float
        ),
        database_dsn=_secret("database_dsn", raw["database_dsn"]),
        object_store_access_key=_secret(
            "object_store_access_key", raw["object_store_access_key"]
        ),
        object_store_secret_key=_secret(
            "object_store_secret_key", raw["object_store_secret_key"]
        ),
        oidc_client_secret=_secret("oidc_client_secret", raw["oidc_client_secret"]),
    )
