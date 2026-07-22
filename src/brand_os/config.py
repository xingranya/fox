"""加载本地工作空间配置，不接受密钥类字段。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


ENV_CONFIG_PATH = "BRAND_OS_CONFIG"
ENV_WORKSPACE_ROOT = "BRAND_OS_WORKSPACE"
ENV_SOURCE_ROOTS = "BRAND_OS_SOURCE_ROOTS"
ALLOWED_CONFIG_KEYS = {"workspace_root", "source_roots"}


class ConfigurationError(ValueError):
    """表示配置无法安全加载。"""


@dataclass(frozen=True, slots=True)
class WorkspaceSettings:
    """描述一个本地工作空间及允许读取的原件根目录。"""

    workspace_root: Path
    source_roots: tuple[Path, ...]


def _absolute_path(value: str | Path, base: Path | None = None) -> Path:
    """展开用户目录，并把相对路径固定到给定基准。"""

    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (base or Path.cwd()) / path
    return path.resolve(strict=False)


def _load_config_file(path: Path) -> dict[str, object]:
    """读取只允许路径字段的 JSON 配置。"""

    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"无法读取配置文件：{path}") from exc
    if not isinstance(data, dict):
        raise ConfigurationError("配置文件根节点必须是对象")
    unknown = sorted(set(data) - ALLOWED_CONFIG_KEYS)
    if unknown:
        raise ConfigurationError(f"配置文件包含不允许的字段：{', '.join(unknown)}")
    if "workspace_root" in data and not isinstance(data["workspace_root"], str):
        raise ConfigurationError("workspace_root 必须是字符串路径")
    if "source_roots" in data and (
        not isinstance(data["source_roots"], list)
        or not all(isinstance(item, str) for item in data["source_roots"])
    ):
        raise ConfigurationError("source_roots 必须是字符串路径数组")
    return data


def load_workspace_settings(
    *,
    explicit_root: str | Path | None = None,
    explicit_source_roots: Sequence[str | Path] | None = None,
    config_path: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> WorkspaceSettings:
    """按显式参数、环境变量、配置文件、默认值的顺序加载路径。"""

    env = os.environ if environ is None else environ
    user_home = (home or Path.home()).expanduser().resolve(strict=False)
    selected_config_path = config_path or env.get(ENV_CONFIG_PATH)
    if selected_config_path is None:
        selected_config_path = user_home / ".config" / "brand-project-os" / "config.json"
    config = _load_config_file(_absolute_path(selected_config_path))

    root_value = explicit_root or env.get(ENV_WORKSPACE_ROOT) or config.get("workspace_root")
    workspace_root = _absolute_path(root_value or user_home / "FoxWork")

    source_values: Sequence[str | Path] | None = explicit_source_roots
    if source_values is None and env.get(ENV_SOURCE_ROOTS):
        source_values = tuple(part for part in env[ENV_SOURCE_ROOTS].split(os.pathsep) if part)
    if source_values is None:
        source_values = config.get("source_roots")  # type: ignore[assignment]
    if not source_values:
        source_values = (workspace_root,)

    source_roots = tuple(dict.fromkeys(_absolute_path(value, workspace_root) for value in source_values))
    return WorkspaceSettings(workspace_root=workspace_root, source_roots=source_roots)
