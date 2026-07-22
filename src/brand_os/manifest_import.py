"""把受支持的来源 Manifest 标准化为导入批次。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Mapping, Sequence

from .domain import (
    SourceAliasRecord,
    SourceGapRecord,
    SourceImportBatch,
    SourceImportRecord,
)


SUPPORTED_MANIFEST_SCHEMAS = {
    "sample-manifest.v1",
    "remote-source-manifest.v1",
    "source-import.v1",
}


class ManifestImportError(ValueError):
    """表示 Manifest 无法在不猜测内容的前提下导入。"""


def load_source_manifest(
    manifest_path: Path,
    *,
    origin_ref: str | None = None,
    additional_gaps: Sequence[SourceGapRecord] = (),
) -> SourceImportBatch:
    """读取 Manifest 并转换为统一来源导入模型。"""

    path = manifest_path.expanduser().resolve(strict=True)
    raw = path.read_bytes()
    try:
        manifest = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ManifestImportError("Manifest 不是有效的 UTF-8 JSON") from error
    if not isinstance(manifest, dict):
        raise ManifestImportError("Manifest 顶层必须是 JSON 对象")

    schema_version = _required_text(manifest, "schema_version")
    if schema_version not in SUPPORTED_MANIFEST_SCHEMAS:
        raise ManifestImportError(f"不支持的 Manifest 版本：{schema_version}")
    records_data = manifest.get("records")
    if not isinstance(records_data, list):
        raise ManifestImportError("records 必须是数组")
    declared_count = manifest.get("record_count")
    if declared_count is not None and declared_count != len(records_data):
        raise ManifestImportError("record_count 与 records 实际数量不一致")

    if schema_version == "sample-manifest.v1":
        records = tuple(_sample_record(item) for item in records_data)
        derived_gaps = _sample_gaps(manifest)
        snapshot_at = _optional_text(manifest.get("generated_at"), "generated_at")
    elif schema_version == "remote-source-manifest.v1":
        records = tuple(_remote_record(item) for item in records_data)
        derived_gaps = _remote_gaps(manifest)
        snapshot_at = _optional_text(manifest.get("snapshot_date"), "snapshot_date")
    else:
        records = tuple(_canonical_record(item) for item in records_data)
        gaps_data = manifest.get("gaps", [])
        if not isinstance(gaps_data, list):
            raise ManifestImportError("gaps 必须是数组")
        derived_gaps = tuple(_canonical_gap(item) for item in gaps_data)
        snapshot_at = _optional_text(manifest.get("snapshot_at"), "snapshot_at")

    gaps = _merge_gaps(derived_gaps, tuple(additional_gaps))
    manifest_sha256 = hashlib.sha256(raw).hexdigest()
    normalized = {
        "manifest_sha256": manifest_sha256,
        "manifest_schema_version": schema_version,
        "records": [asdict(record) for record in records],
        "gaps": [asdict(gap) for gap in gaps],
        "snapshot_at": snapshot_at,
    }
    import_digest = hashlib.sha256(
        json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return SourceImportBatch(
        manifest_sha256=manifest_sha256,
        import_digest=import_digest,
        manifest_schema_version=schema_version,
        origin_ref=origin_ref or path.name,
        records=records,
        gaps=gaps,
        snapshot_at=snapshot_at,
    )


def load_source_gaps(gap_path: Path) -> tuple[SourceGapRecord, ...]:
    """读取独立缺口清单，避免把缺失资料伪装成来源记录。"""

    path = gap_path.expanduser().resolve(strict=True)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ManifestImportError("缺口清单不是有效的 UTF-8 JSON") from error
    if not isinstance(value, dict) or value.get("schema_version") != "source-gaps.v1":
        raise ManifestImportError("缺口清单版本必须是 source-gaps.v1")
    gaps = value.get("gaps")
    if not isinstance(gaps, list):
        raise ManifestImportError("缺口清单的 gaps 必须是数组")
    parsed = tuple(_canonical_gap(item) for item in gaps)
    if len({gap.gap_id for gap in parsed}) != len(parsed):
        raise ManifestImportError("缺口清单不能重复登记 gap_id")
    return parsed


def _sample_record(value: object) -> SourceImportRecord:
    item = _record_mapping(value)
    return SourceImportRecord(
        logical_source_id=_required_text(item, "source_id"),
        sha256=_required_text(item, "sha256"),
        relative_path=_required_text(item, "filename"),
        source_role=_required_text(item, "source_role"),
        confidentiality=_optional_text(item.get("confidentiality"), "confidentiality"),
        size_bytes=_optional_nonnegative_integer(item.get("size_bytes"), "size_bytes"),
        media_type=_optional_text(item.get("media_type"), "media_type"),
        status=_optional_text(item.get("current_validity"), "current_validity") or "unknown",
        version_label=_optional_text(item.get("version_label"), "version_label"),
        aliases=_aliases(item),
        supersedes_sha256=_string_tuple(item.get("supersedes_sha256", []), "supersedes_sha256"),
    )


def _remote_record(value: object) -> SourceImportRecord:
    item = _record_mapping(value)
    relative_path = _required_text(item, "relative_path")
    logical_source_id = item.get("logical_source_id")
    if logical_source_id is None:
        logical_source_id = "HXD-" + hashlib.sha256(relative_path.encode("utf-8")).hexdigest()[:16].upper()
    elif not isinstance(logical_source_id, str) or not logical_source_id.strip():
        raise ManifestImportError("logical_source_id 必须是非空字符串")
    return SourceImportRecord(
        logical_source_id=logical_source_id,
        sha256=_required_text(item, "sha256"),
        relative_path=relative_path,
        source_role=_required_text(item, "role"),
        confidentiality=_optional_text(item.get("confidentiality"), "confidentiality"),
        size_bytes=_optional_nonnegative_integer(item.get("size_bytes"), "size_bytes"),
        media_type=_optional_text(item.get("media_type"), "media_type"),
        status=_optional_text(item.get("status"), "status") or "observed",
        version_label=_optional_text(item.get("version_label"), "version_label"),
        aliases=_aliases(item),
        supersedes_sha256=_string_tuple(item.get("supersedes_sha256", []), "supersedes_sha256"),
    )


def _canonical_record(value: object) -> SourceImportRecord:
    item = _record_mapping(value)
    return SourceImportRecord(
        logical_source_id=_required_text(item, "logical_source_id"),
        sha256=_required_text(item, "sha256"),
        relative_path=_required_text(item, "relative_path"),
        source_role=_required_text(item, "source_role"),
        confidentiality=_optional_text(item.get("confidentiality"), "confidentiality"),
        size_bytes=_optional_nonnegative_integer(item.get("size_bytes"), "size_bytes"),
        media_type=_optional_text(item.get("media_type"), "media_type"),
        status=_optional_text(item.get("status"), "status") or "observed",
        version_label=_optional_text(item.get("version_label"), "version_label"),
        aliases=_aliases(item),
        supersedes_sha256=_string_tuple(item.get("supersedes_sha256", []), "supersedes_sha256"),
    )


def _aliases(item: Mapping[str, object]) -> tuple[SourceAliasRecord, ...]:
    aliases = item.get("aliases", [])
    if not isinstance(aliases, list):
        raise ManifestImportError("aliases 必须是数组")
    parsed: list[SourceAliasRecord] = []
    for value in aliases:
        mapping = _record_mapping(value)
        parsed.append(
            SourceAliasRecord(
                alias_id=_required_text(mapping, "alias_id"),
                alias_kind=_required_text(mapping, "alias_kind"),
                status=_required_text(mapping, "status"),
            )
        )
    return tuple(parsed)


def _canonical_gap(value: object) -> SourceGapRecord:
    item = _record_mapping(value)
    return SourceGapRecord(
        gap_id=_required_text(item, "gap_id"),
        status=_required_text(item, "status"),
        description=_required_text(item, "description"),
        scope=_required_text(item, "scope"),
        evidence_ref=_required_text(item, "evidence_ref"),
    )


def _sample_gaps(manifest: Mapping[str, object]) -> tuple[SourceGapRecord, ...]:
    if manifest.get("known_source_gap") is not True:
        return ()
    return (
        SourceGapRecord(
            gap_id="GAP-SAMPLE-INCOMPLETE",
            status="KNOWN_SOURCE_GAP",
            description="样本 Manifest 明确声明当前资料集合不完整。",
            scope="sample_manifest",
            evidence_ref="manifest:known_source_gap",
        ),
    )


def _remote_gaps(manifest: Mapping[str, object]) -> tuple[SourceGapRecord, ...]:
    excluded = manifest.get("excluded", [])
    if not isinstance(excluded, list):
        raise ManifestImportError("excluded 必须是数组")
    if not any(isinstance(item, str) and "权限不可读" in item for item in excluded):
        return ()
    return (
        SourceGapRecord(
            gap_id="GAP-001",
            status="PARTIALLY_RESOLVED",
            description="Manifest 声明仍有权限不可读目录，当前导入不能代表全量物理对账。",
            scope="remote_source_root",
            evidence_ref="manifest:excluded/权限不可读目录",
        ),
    )


def _merge_gaps(
    derived: tuple[SourceGapRecord, ...], additional: tuple[SourceGapRecord, ...]
) -> tuple[SourceGapRecord, ...]:
    merged = {gap.gap_id: gap for gap in derived}
    for gap in additional:
        merged[gap.gap_id] = gap
    return tuple(merged[key] for key in sorted(merged))


def _record_mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ManifestImportError("Manifest 记录必须是 JSON 对象")
    return value


def _required_text(item: Mapping[str, object], field: str) -> str:
    value = item.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ManifestImportError(f"{field} 必须是非空字符串")
    return value


def _optional_text(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ManifestImportError(f"{field} 必须为空或非空字符串")
    return value


def _optional_nonnegative_integer(value: object, field: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ManifestImportError(f"{field} 必须为空或非负整数")
    return value


def _string_tuple(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ManifestImportError(f"{field} 必须是非空字符串数组")
    return tuple(value)
