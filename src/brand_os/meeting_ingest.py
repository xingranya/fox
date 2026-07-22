"""读取会议解释载荷，并在进入存储前执行保守分类规则。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .domain import (
    DATE_KINDS,
    INFORMATION_TYPES,
    MEETING_MODES,
    MeetingConflictCandidate,
    MeetingIngestBatch,
    MeetingInterpretationItem,
    MeetingSegment,
)


class MeetingIngestError(ValueError):
    """表示会议载荷缺少必要字段或试图绕过分类边界。"""


def load_meeting_ingest(path: Path) -> MeetingIngestBatch:
    """读取 `meeting-ingest.v1` JSON，并返回规范化批次。"""

    try:
        raw = path.read_bytes()
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise MeetingIngestError("会议摄取文件无法读取或不是有效 JSON") from exc
    if not isinstance(payload, dict):
        raise MeetingIngestError("会议摄取根节点必须是对象")
    return parse_meeting_ingest(payload)


def parse_meeting_ingest(payload: dict[str, Any]) -> MeetingIngestBatch:
    """校验来源绑定，保守处理高风险分类并计算稳定摘要。"""

    if payload.get("schema_version") != "meeting-ingest.v1":
        raise MeetingIngestError("仅支持 meeting-ingest.v1")
    if payload.get("source_is_data") is not True:
        raise MeetingIngestError("source_is_data 必须为 true，会议原话只能作为数据处理")
    meeting = _required_mapping(payload, "meeting")
    source = _required_mapping(meeting, "source")
    segments = tuple(_parse_segment(value) for value in _required_list(payload, "segments"))
    if not segments:
        raise MeetingIngestError("segments 不能为空")
    segment_map = {segment.segment_id: segment for segment in segments}
    if len(segment_map) != len(segments):
        raise MeetingIngestError("segments 不能重复 segment_id")

    occurred_at = _optional_text(meeting, "occurred_at")
    source_verification = _required_text(source, "verification")
    items = tuple(
        _parse_item(value, segment_map, occurred_at, source_verification)
        for value in _optional_list(payload, "items")
    )
    conflicts = tuple(
        _parse_conflict(value) for value in _optional_list(payload, "conflicts")
    )
    participants = tuple(_text_list(meeting.get("participants", []), "participants"))
    meeting_mode = _required_text(meeting, "mode")
    if meeting_mode not in MEETING_MODES:
        raise MeetingIngestError("meeting.mode 不在冻结词表中")

    content_payload = {
        "meeting_id": _required_text(meeting, "meeting_id"),
        "title": _required_text(meeting, "title"),
        "occurred_at": occurred_at,
        "participants": list(participants),
        "logical_source_id": _required_text(source, "logical_source_id"),
        "source_version_id": _required_text(source, "source_version_id"),
        "source_sha256": _required_text(source, "sha256"),
        "segments": [
            {
                "locator": segment.locator,
                "quote": segment.quote,
                "speaker": segment.speaker,
                "spoken_at": segment.spoken_at,
                "start_ms": segment.start_ms,
                "end_ms": segment.end_ms,
                "context": segment.context,
                "transcript_confidence": segment.transcript_confidence,
            }
            for segment in segments
        ],
    }
    content_sha256 = _digest(content_payload)
    normalized_payload = {
        **content_payload,
        "source_verification": source_verification,
        "base_state_version": _required_int(payload, "base_state_version"),
        "meeting_mode": meeting_mode,
        "mode_confidence": _required_confidence(meeting, "mode_confidence"),
        "items": [asdict(item) for item in items],
        "conflicts": [asdict(conflict) for conflict in conflicts],
    }
    return MeetingIngestBatch(
        meeting_id=content_payload["meeting_id"],
        title=content_payload["title"],
        occurred_at=occurred_at,
        participants=participants,
        logical_source_id=content_payload["logical_source_id"],
        source_version_id=content_payload["source_version_id"],
        source_sha256=content_payload["source_sha256"],
        source_verification=source_verification,
        base_state_version=normalized_payload["base_state_version"],
        meeting_mode=meeting_mode,
        mode_confidence=normalized_payload["mode_confidence"],
        content_sha256=content_sha256,
        ingest_digest=_digest(normalized_payload),
        segments=segments,
        items=items,
        conflicts=conflicts,
    )


def _parse_segment(value: object) -> MeetingSegment:
    item = _mapping(value, "segment")
    return MeetingSegment(
        segment_id=_required_text(item, "segment_id"),
        locator=_required_text(item, "locator"),
        quote=_required_text(item, "quote"),
        speaker=_optional_text(item, "speaker"),
        spoken_at=_optional_text(item, "spoken_at"),
        start_ms=_optional_int(item, "start_ms"),
        end_ms=_optional_int(item, "end_ms"),
        context=_optional_text(item, "context"),
        transcript_confidence=_optional_confidence(item, "transcript_confidence"),
        mode=_required_text(item, "mode"),
        mode_confidence=_required_confidence(item, "mode_confidence"),
    )


def _parse_item(
    value: object,
    segment_map: dict[str, MeetingSegment],
    occurred_at: str | None,
    source_verification: str,
) -> MeetingInterpretationItem:
    item = _mapping(value, "item")
    if item.get("requires_human_confirmation") is not True:
        raise MeetingIngestError("每个会议解释项都必须等待人工确认")
    suggested_type = _required_text(item, "type")
    if suggested_type not in INFORMATION_TYPES:
        raise MeetingIngestError("会议解释不能直接生成 DECISION、CONSTRAINT 或 ACTION")
    evidence_segment_ids = tuple(
        _text_list(item.get("evidence_segment_ids"), "evidence_segment_ids")
    )
    if not evidence_segment_ids:
        raise MeetingIngestError("会议解释项必须引用原话片段")
    try:
        evidence_segments = [segment_map[segment_id] for segment_id in evidence_segment_ids]
    except KeyError as exc:
        raise MeetingIngestError("会议解释项引用了不存在的片段") from exc

    decision_actor = _optional_text(item, "decision_actor")
    decision_verb = _optional_text(item, "decision_verb")
    state_difference = _optional_text(item, "state_difference")
    classification = suggested_type
    normalization_notes: list[str] = []
    date_kind = _optional_text(item, "date_kind")
    if suggested_type == "TARGET_DATE" and date_kind is None:
        date_kind = "UNKNOWN"
        normalization_notes.append("未提供时间性质，按 UNKNOWN 等待确认")
    if date_kind is not None and date_kind not in DATE_KINDS:
        raise MeetingIngestError("date_kind 不在冻结词表中")

    if suggested_type == "DECISION_CANDIDATE":
        missing: list[str] = []
        if decision_actor is None:
            missing.append("决定人")
        if decision_verb is None:
            missing.append("决定动词")
        if state_difference is None:
            missing.append("状态差异")
        if occurred_at is None:
            missing.append("会议时间")
        if any(segment.speaker is None for segment in evidence_segments):
            missing.append("发言人")
        if any(not segment.has_time_position for segment in evidence_segments):
            missing.append("原话时间位置")
        if source_verification != "verified":
            missing.append("已核验原话")
        if missing:
            classification = "OPEN"
            normalization_notes.append(f"决定候选证据不足：缺少{'、'.join(missing)}")

    status_by_type = {
        "TENDENCY": "preferred",
        "TARGET_DATE": "tentative",
        "DECISION_CANDIDATE": "proposed",
        "CONSTRAINT_CANDIDATE": "proposed",
        "ACTION_CANDIDATE": "proposed",
        "FACT": "proposed",
    }
    status = status_by_type.get(classification, "working")
    normalization_reason = "；".join(normalization_notes) or None
    reason = _required_text(item, "reason")
    if normalization_reason is not None:
        reason = f"{reason}；{normalization_reason}"
    return MeetingInterpretationItem(
        item_id=_required_text(item, "item_id"),
        suggested_type=suggested_type,
        classification=classification,
        status=status,
        summary=_required_text(item, "summary"),
        scope=_required_text(item, "scope"),
        evidence_segment_ids=evidence_segment_ids,
        confidence=_required_confidence(item, "confidence"),
        reason=reason,
        date_kind=date_kind,
        decision_actor=decision_actor,
        decision_verb=decision_verb,
        state_difference=state_difference,
        normalization_reason=normalization_reason,
    )


def _parse_conflict(value: object) -> MeetingConflictCandidate:
    item = _mapping(value, "conflict")
    return MeetingConflictCandidate(
        conflict_id=_required_text(item, "conflict_id"),
        item_id=_required_text(item, "item_id"),
        state_item_type=_required_text(item, "state_item_type"),
        state_item_id=_required_text(item, "state_item_id"),
        reason=_required_text(item, "reason"),
        evidence_segment_ids=tuple(
            _text_list(item.get("evidence_segment_ids"), "evidence_segment_ids")
        ),
    )


def _digest(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _mapping(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MeetingIngestError(f"{field} 必须是对象")
    return value


def _required_mapping(value: dict[str, Any], field: str) -> dict[str, Any]:
    return _mapping(value.get(field), field)


def _required_list(value: dict[str, Any], field: str) -> list[object]:
    result = value.get(field)
    if not isinstance(result, list):
        raise MeetingIngestError(f"{field} 必须是数组")
    return result


def _optional_list(value: dict[str, Any], field: str) -> list[object]:
    result = value.get(field, [])
    if not isinstance(result, list):
        raise MeetingIngestError(f"{field} 必须是数组")
    return result


def _required_text(value: dict[str, Any], field: str) -> str:
    result = value.get(field)
    if not isinstance(result, str) or not result.strip():
        raise MeetingIngestError(f"{field} 必须是非空字符串")
    return result


def _optional_text(value: dict[str, Any], field: str) -> str | None:
    result = value.get(field)
    if result is None:
        return None
    if not isinstance(result, str) or not result.strip():
        raise MeetingIngestError(f"{field} 必须为空或非空字符串")
    return result


def _required_int(value: dict[str, Any], field: str) -> int:
    result = value.get(field)
    if isinstance(result, bool) or not isinstance(result, int):
        raise MeetingIngestError(f"{field} 必须是整数")
    return result


def _optional_int(value: dict[str, Any], field: str) -> int | None:
    result = value.get(field)
    if result is None:
        return None
    if isinstance(result, bool) or not isinstance(result, int):
        raise MeetingIngestError(f"{field} 必须为空或整数")
    return result


def _required_confidence(value: dict[str, Any], field: str) -> float:
    result = value.get(field)
    if isinstance(result, bool) or not isinstance(result, (int, float)):
        raise MeetingIngestError(f"{field} 必须是 0-1 数字")
    result = float(result)
    if not 0 <= result <= 1:
        raise MeetingIngestError(f"{field} 必须位于 0-1")
    return result


def _optional_confidence(value: dict[str, Any], field: str) -> float | None:
    if value.get(field) is None:
        return None
    return _required_confidence(value, field)


def _text_list(value: object, field: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise MeetingIngestError(f"{field} 必须是非空字符串数组")
    return value
