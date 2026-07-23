"""Brand Project OS 的可观测性、脱敏和共享限流端口。

本模块只保存运行态和派生遥测，不定义正式业务事实。日志、指标、追踪和告警
都通过可替换的 Sink/Port 输出；没有配置外部后端时，运行时仍可以在进程内
收集测试所需的安全摘要。共享限流使用 PostgreSQL 的短期窗口表，不把多副本
限流退化成每个进程各自计数。
"""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import math
import re
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from io import TextIOBase
from typing import Any, Protocol
from uuid import uuid4

import psycopg


OBSERVABILITY_SCHEMA_VERSION = "observability.v1"
TRACE_CONTEXT_SCHEMA_VERSION = "trace-context.v1"
ALERT_SCHEMA_VERSION = "alert.v1"
METRIC_SCHEMA_VERSION = "metrics.v1"
MAX_LOG_STRING_LENGTH = 256
MAX_LOG_DEPTH = 3
MAX_LOG_ITEMS = 20
OBSERVABILITY_CONTRACT: dict[str, object] = {
    "schema_version": OBSERVABILITY_SCHEMA_VERSION,
    "trace_context": {
        "schema_version": TRACE_CONTEXT_SCHEMA_VERSION,
        "request_header": "X-Request-ID",
        "correlation_header": "X-Correlation-ID",
        "w3c_trace_header": "traceparent",
        "invalid_external_identifiers_are_replaced": True,
    },
    "logs": {
        "format": "json-lines",
        "required_fields": [
            "schema_version",
            "timestamp",
            "level",
            "event",
            "request_id",
            "correlation_id",
            "trace_id",
            "span_id",
        ],
        "max_string_length": MAX_LOG_STRING_LENGTH,
        "max_depth": MAX_LOG_DEPTH,
        "max_collection_items": MAX_LOG_ITEMS,
        "never_record": [
            "authorization",
            "cookie",
            "token",
            "secret",
            "password",
            "api_key",
            "private_key",
            "prompt",
            "body",
            "payload",
            "raw_content",
            "signed_url",
            "model_key",
        ],
    },
    "metrics": {
        "schema_version": METRIC_SCHEMA_VERSION,
        "unknown_label_value": "other",
        "max_series_per_metric": 256,
        "fixed_names": [
            "http_requests_total",
            "http_request_duration_seconds",
            "http_errors_total",
            "http_rate_limit_denied_total",
            "dependency_status",
            "outbox_pending_messages",
            "outbox_oldest_age_seconds",
            "outbox_dead_letter_messages",
            "worker_lease_expiry_total",
            "rate_limit_store_errors_total",
            "alert_events_total",
        ],
    },
    "alerts": {
        "schema_version": ALERT_SCHEMA_VERSION,
        "transitions": ["firing", "resolved"],
        "deduplicate_unchanged_state": True,
        "default_rules": ["outbox_stale", "rate_limit_store_unavailable"],
    },
    "shared_rate_limit": {
        "backend": "postgresql",
        "postgresql_schema_version": 11,
        "bucket_key_storage": "sha256",
        "failure_http_status": 503,
        "failure_code": "RATE_LIMIT_STORE_UNAVAILABLE",
        "fail_closed": True,
        "fallback_to_process_local": False,
    },
    "authority": {
        "telemetry_is_derived_runtime_data": True,
        "may_approve_formal_state": False,
        "may_overwrite_formal_state": False,
        "outbox_payload_may_be_exported": False,
    },
}
TRACEPARENT_PATTERN = re.compile(r"^(?P<version>[0-9a-f]{2})-(?P<trace>[0-9a-f]{32})-(?P<span>[0-9a-f]{16})-(?P<flags>[0-9a-f]{2})$")
SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
HEX_PATTERN = re.compile(r"^[0-9a-f]+$")
SENSITIVE_KEY_PATTERN = re.compile(
    r"(?:authorization|cookie|token|secret|password|passwd|api[_-]?key|private[_-]?key|credential|dsn|prompt|payload|body|raw|content|signed[_-]?url|access[_-]?key|refresh[_-]?token)",
    re.IGNORECASE,
)
BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
URL_SECRET_PATTERN = re.compile(r"(?i)([?&](?:token|sig|signature|secret|key|password)=[^&\s]+)")


def _new_hex(byte_count: int) -> str:
    """生成不可读业务含义的随机十六进制标识。"""

    return uuid4().hex[: byte_count * 2]


def _safe_identifier(value: object, *, prefix: str, max_length: int = 128) -> str:
    """只接受可放入关联头和日志字段的安全标识。"""

    if isinstance(value, str):
        candidate = value.strip()
        if len(candidate) <= max_length and SAFE_ID_PATTERN.fullmatch(candidate):
            return candidate
    return f"{prefix}{_new_hex(16)}"


@dataclass(frozen=True, slots=True)
class TraceParent:
    """已校验的 W3C traceparent。"""

    trace_id: str
    span_id: str
    trace_flags: str = "00"
    version: str = "00"

    def to_header(self) -> str:
        """序列化为标准 traceparent 头。"""

        return f"{self.version}-{self.trace_id}-{self.span_id}-{self.trace_flags}"


def parse_traceparent(value: str | None) -> TraceParent | None:
    """解析并拒绝不符合 W3C 基本约束的 traceparent。"""

    if not isinstance(value, str):
        return None
    match = TRACEPARENT_PATTERN.fullmatch(value.strip().lower())
    if match is None:
        return None
    version = match.group("version")
    trace_id = match.group("trace")
    span_id = match.group("span")
    if version == "ff" or set(trace_id) == {"0"} or set(span_id) == {"0"}:
        return None
    return TraceParent(
        trace_id=trace_id,
        span_id=span_id,
        trace_flags=match.group("flags"),
        version=version,
    )


@dataclass(frozen=True, slots=True)
class TelemetryContext:
    """一次请求或后台运行共享的安全关联上下文。"""

    request_id: str
    correlation_id: str
    trace_id: str
    span_id: str
    parent_span_id: str | None = None
    actor_kind: str | None = None
    actor_id: str | None = None
    project_id: str | None = None
    state_version: int | None = None
    event_id: str | None = None
    run_id: str | None = None

    def with_business(
        self,
        *,
        actor_kind: str | None = None,
        actor_id: str | None = None,
        project_id: str | None = None,
        state_version: int | None = None,
        event_id: str | None = None,
        run_id: str | None = None,
    ) -> "TelemetryContext":
        """在不改变请求关联 ID 的前提下补充业务定位字段。"""

        return replace(
            self,
            actor_kind=actor_kind if actor_kind is not None else self.actor_kind,
            actor_id=actor_id if actor_id is not None else self.actor_id,
            project_id=project_id if project_id is not None else self.project_id,
            state_version=state_version if state_version is not None else self.state_version,
            event_id=event_id if event_id is not None else self.event_id,
            run_id=run_id if run_id is not None else self.run_id,
        )

    def child(self) -> "TelemetryContext":
        """为一个子操作生成新 span，同时保留请求和业务关联。"""

        return TelemetryContext(
            request_id=self.request_id,
            correlation_id=self.correlation_id,
            trace_id=self.trace_id,
            span_id=_new_hex(8),
            parent_span_id=self.span_id,
            actor_kind=self.actor_kind,
            actor_id=self.actor_id,
            project_id=self.project_id,
            state_version=self.state_version,
            event_id=self.event_id,
            run_id=self.run_id,
        )

    def to_fields(self) -> dict[str, object]:
        """返回可安全写入日志和追踪的关联字段。"""

        fields: dict[str, object] = {
            "request_id": self.request_id,
            "correlation_id": self.correlation_id,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
        }
        optional = {
            "actor_kind": self.actor_kind,
            "actor_id": self.actor_id,
            "project_id": self.project_id,
            "state_version": self.state_version,
            "event_id": self.event_id,
            "run_id": self.run_id,
        }
        fields.update({key: value for key, value in optional.items() if value is not None})
        return fields


_CURRENT_CONTEXT: contextvars.ContextVar[TelemetryContext | None] = contextvars.ContextVar(
    "brand_os_telemetry_context", default=None
)


def current_telemetry_context() -> TelemetryContext | None:
    """返回当前协程/线程的关联上下文。"""

    return _CURRENT_CONTEXT.get()


def new_telemetry_context(
    *,
    request_id: str | None = None,
    correlation_id: str | None = None,
    traceparent: str | None = None,
) -> TelemetryContext:
    """根据请求头创建上下文；不合规的外部标识会被替换。"""

    incoming = parse_traceparent(traceparent)
    trace_id = incoming.trace_id if incoming is not None else _new_hex(16)
    parent_span_id = incoming.span_id if incoming is not None else None
    return TelemetryContext(
        request_id=_safe_identifier(request_id, prefix="req_"),
        correlation_id=_safe_identifier(correlation_id, prefix="cor_"),
        trace_id=trace_id,
        span_id=_new_hex(8),
        parent_span_id=parent_span_id,
    )


@contextlib.contextmanager
def telemetry_scope(context: TelemetryContext):
    """在当前执行范围安装上下文，并在退出时恢复上层上下文。"""

    token = _CURRENT_CONTEXT.set(context)
    try:
        yield context
    finally:
        _CURRENT_CONTEXT.reset(token)


class LogSink(Protocol):
    """结构化日志输出端口。"""

    def emit(self, record: Mapping[str, object]) -> None: ...


class TraceSink(Protocol):
    """追踪 span 输出端口。"""

    def emit(self, record: Mapping[str, object]) -> None: ...


class AlertSink(Protocol):
    """告警状态变化输出端口。"""

    def emit(self, record: Mapping[str, object]) -> None: ...


class NullSink:
    """默认空输出，避免库代码隐式写文件或网络。"""

    def emit(self, record: Mapping[str, object]) -> None:
        return None


class InMemorySink:
    """测试和本地诊断使用的有界内存 Sink。"""

    def __init__(self, *, max_records: int = 1000) -> None:
        if max_records <= 0:
            raise ValueError("max_records 必须大于 0")
        self.max_records = max_records
        self.records: list[dict[str, object]] = []
        self._lock = threading.Lock()

    def emit(self, record: Mapping[str, object]) -> None:
        with self._lock:
            self.records.append(dict(record))
            if len(self.records) > self.max_records:
                del self.records[: len(self.records) - self.max_records]


class JsonLineLogSink:
    """向已由调用方管理的文本流写入 JSON 行，不负责打开文件。"""

    def __init__(self, stream: TextIOBase) -> None:
        self.stream = stream
        self._lock = threading.Lock()

    def emit(self, record: Mapping[str, object]) -> None:
        rendered = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self._lock:
            self.stream.write(rendered + "\n")
            self.stream.flush()


def _redact_string(value: str) -> str:
    """移除常见凭据形态并限制单字段长度。"""

    value = BEARER_PATTERN.sub("Bearer [REDACTED]", value)
    value = URL_SECRET_PATTERN.sub(lambda match: match.group(1).split("=", 1)[0] + "=[REDACTED]", value)
    if len(value) > MAX_LOG_STRING_LENGTH:
        value = value[:MAX_LOG_STRING_LENGTH] + "…"
    return value


def redact_value(value: object, *, key: str = "", depth: int = 0) -> object:
    """递归脱敏值；原文、提示词、请求体和密钥字段永不进入日志。"""

    if SENSITIVE_KEY_PATTERN.search(key):
        return "[REDACTED]"
    if depth >= MAX_LOG_DEPTH:
        return "[TRUNCATED]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _redact_string(value)
    if isinstance(value, Mapping):
        items = list(value.items())[:MAX_LOG_ITEMS]
        return {
            str(item_key): redact_value(item_value, key=str(item_key), depth=depth + 1)
            for item_key, item_value in items
        }
    if isinstance(value, (tuple, list, set, frozenset)):
        return [redact_value(item, depth=depth + 1) for item in list(value)[:MAX_LOG_ITEMS]]
    return _redact_string(str(value))


class StructuredLogger:
    """带上下文和统一脱敏规则的结构化日志器。"""

    def __init__(self, sink: LogSink | None = None, *, clock: Callable[[], datetime] | None = None) -> None:
        self.sink = sink or NullSink()
        self.clock = clock or (lambda: datetime.now(UTC))

    def log(
        self,
        level: str,
        event: str,
        *,
        context: TelemetryContext | None = None,
        **fields: object,
    ) -> dict[str, object]:
        """写出一条不包含秘密和原文的日志记录。"""

        active = context or current_telemetry_context()
        record: dict[str, object] = {
            "schema_version": OBSERVABILITY_SCHEMA_VERSION,
            "timestamp": self.clock().astimezone(UTC).isoformat(),
            "level": str(level).upper(),
            "event": _redact_string(event),
        }
        if active is not None:
            record.update(active.to_fields())
        record.update(
            {
                str(key): redact_value(value, key=str(key))
                for key, value in fields.items()
                if str(key) not in record
            }
        )
        self.sink.emit(record)
        return record

    def info(self, event: str, **fields: object) -> dict[str, object]:
        """记录信息事件。"""

        return self.log("INFO", event, **fields)

    def warning(self, event: str, **fields: object) -> dict[str, object]:
        """记录警告事件。"""

        return self.log("WARNING", event, **fields)

    def error(self, event: str, **fields: object) -> dict[str, object]:
        """记录错误事件。"""

        return self.log("ERROR", event, **fields)


class MetricKind(StrEnum):
    """指标的聚合类型。"""

    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    """一个固定名称和固定标签集合的指标定义。"""

    name: str
    kind: MetricKind
    label_names: tuple[str, ...] = ()
    buckets: tuple[float, ...] = ()
    allowed_values: Mapping[str, frozenset[str]] = field(default_factory=dict)
    max_series: int = 256


@dataclass(frozen=True, slots=True)
class MetricSample:
    """指标快照中的单条样本。"""

    name: str
    kind: str
    labels: Mapping[str, str]
    value: float | int | Mapping[str, float | int]

    def to_dict(self) -> dict[str, object]:
        """序列化为安全的指标样本。"""

        return {"name": self.name, "kind": self.kind, "labels": dict(self.labels), "value": self.value}


def default_metric_definitions() -> tuple[MetricDefinition, ...]:
    """返回当前服务器使用的固定基数指标清单。"""

    common_status = frozenset({"1xx", "2xx", "3xx", "4xx", "5xx", "other"})
    method_values = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "other"})
    surface_values = frozenset({"employee", "agent", "public", "retired", "other"})
    dependency_values = frozenset(
        {
            "postgresql",
            "schema",
            "object_storage",
            "oidc",
            "openwork_runtime",
            "outbox_worker",
            "dify",
            "zvec",
            "open_notebook",
            "nubase",
            "flowlong",
            "other",
        }
    )
    consumer_values = frozenset({"default", "search-index", "workflow", "other"})
    return (
        MetricDefinition(
            "http_requests_total",
            MetricKind.COUNTER,
            ("method", "surface", "status_class"),
            allowed_values={"method": method_values, "surface": surface_values, "status_class": common_status},
        ),
        MetricDefinition(
            "http_request_duration_seconds",
            MetricKind.HISTOGRAM,
            ("method", "surface", "status_class"),
            buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
            allowed_values={"method": method_values, "surface": surface_values, "status_class": common_status},
        ),
        MetricDefinition(
            "http_errors_total",
            MetricKind.COUNTER,
            ("surface", "error_code"),
            allowed_values={
                "surface": surface_values,
                "error_code": frozenset(
                    {
                        "AUTHENTICATION_FAILED",
                        "PROJECT_ACCESS_DENIED",
                        "VERSION_MISMATCH",
                        "RATE_LIMITED",
                        "RATE_LIMIT_STORE_UNAVAILABLE",
                        "INTERNAL_ERROR",
                        "other",
                    }
                ),
            },
        ),
        MetricDefinition(
            "http_rate_limit_denied_total",
            MetricKind.COUNTER,
            ("surface", "bucket"),
            allowed_values={"surface": surface_values, "bucket": frozenset({"employee", "employee_public", "agent", "public", "other"})},
        ),
        MetricDefinition(
            "dependency_status",
            MetricKind.GAUGE,
            ("dependency", "status"),
            allowed_values={
                "dependency": dependency_values,
                "status": frozenset({"up", "down", "unknown", "disabled", "other"}),
            },
        ),
        MetricDefinition(
            "outbox_pending_messages",
            MetricKind.GAUGE,
            ("consumer",),
            allowed_values={"consumer": consumer_values},
        ),
        MetricDefinition(
            "outbox_oldest_age_seconds",
            MetricKind.GAUGE,
            ("consumer",),
            allowed_values={"consumer": consumer_values},
        ),
        MetricDefinition(
            "outbox_dead_letter_messages",
            MetricKind.GAUGE,
            ("consumer",),
            allowed_values={"consumer": consumer_values},
        ),
        MetricDefinition(
            "worker_lease_expiry_total",
            MetricKind.COUNTER,
            ("consumer",),
            allowed_values={"consumer": consumer_values},
        ),
        MetricDefinition(
            "rate_limit_store_errors_total",
            MetricKind.COUNTER,
            ("backend",),
            allowed_values={"backend": frozenset({"postgresql", "other"})},
        ),
        MetricDefinition(
            "alert_events_total",
            MetricKind.COUNTER,
            ("rule", "severity", "transition"),
            allowed_values={
                "rule": frozenset({"core_unavailable", "outbox_stale", "rate_limit_store_unavailable", "other"}),
                "severity": frozenset({"info", "warning", "critical", "other"}),
                "transition": frozenset({"firing", "resolved", "other"}),
            },
        ),
    )


class MetricRegistry:
    """固定名称、固定标签和有界时间序列的进程内指标注册表。"""

    def __init__(self, definitions: Iterable[MetricDefinition] | None = None) -> None:
        selected = tuple(definitions or default_metric_definitions())
        self.definitions = {definition.name: definition for definition in selected}
        if len(self.definitions) != len(selected):
            raise ValueError("指标名称不能重复")
        if any(definition.max_series <= 0 for definition in selected):
            raise ValueError("指标时间序列上限必须大于 0")
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._histograms: dict[tuple[str, tuple[tuple[str, str], ...]], list[int | float]] = {}
        self._lock = threading.Lock()

    def _definition(self, name: str, kind: MetricKind | None = None) -> MetricDefinition:
        definition = self.definitions.get(name)
        if definition is None:
            raise KeyError(f"未登记指标：{name}")
        if kind is not None and definition.kind != kind:
            raise TypeError(f"指标 {name} 类型不是 {kind.value}")
        return definition

    @staticmethod
    def _status_class(status_code: int) -> str:
        return f"{status_code // 100}xx" if 100 <= status_code <= 599 else "other"

    @staticmethod
    def surface_for_path(path: str) -> str:
        """把任意路径压缩为固定入口标签，避免把项目 ID 变成时间序列标签。"""

        if path.startswith("/api/v1/employee"):
            return "employee"
        if path.startswith("/api/v1/agent"):
            return "agent"
        if path.startswith("/api/v"):
            return "retired"
        if path in {"/livez", "/readyz", "/openapi.json"}:
            return "public"
        return "other"

    @staticmethod
    def _bounded_value(definition: MetricDefinition, label: str, value: object) -> str:
        candidate = str(value)[:64]
        allowed = definition.allowed_values.get(label)
        if allowed is not None and candidate not in allowed:
            return "other" if "other" in allowed else sorted(allowed)[0]
        return candidate

    def _labels(
        self,
        definition: MetricDefinition,
        labels: Mapping[str, object] | None,
        storage: Mapping[tuple[str, tuple[tuple[str, str], ...]], object],
    ) -> tuple[tuple[str, str], ...]:
        incoming = labels or {}
        if set(incoming) != set(definition.label_names):
            missing = sorted(set(definition.label_names) - set(incoming))
            extra = sorted(set(incoming) - set(definition.label_names))
            raise ValueError(f"指标标签不匹配：缺少={missing}，多余={extra}")
        normalized = tuple(
            (name, self._bounded_value(definition, name, incoming[name]))
            for name in definition.label_names
        )
        key = (definition.name, normalized)
        if key not in storage:
            existing = sum(1 for metric_name, _ in storage if metric_name == definition.name)
            if existing >= definition.max_series - 1:
                normalized = tuple(
                    (name, "other") if name not in definition.allowed_values or "other" in definition.allowed_values[name] else (name, value)
                    for name, value in normalized
                )
        return normalized

    def inc(self, name: str, value: float = 1, *, labels: Mapping[str, object] | None = None) -> None:
        """增加计数器。"""

        if value < 0:
            raise ValueError("计数器不能减少")
        definition = self._definition(name, MetricKind.COUNTER)
        with self._lock:
            normalized = self._labels(definition, labels, self._counters)
            key = (name, normalized)
            self._counters[key] = self._counters.get(key, 0.0) + value

    def set(self, name: str, value: float, *, labels: Mapping[str, object] | None = None) -> None:
        """设置一个瞬时仪表值。"""

        definition = self._definition(name, MetricKind.GAUGE)
        with self._lock:
            normalized = self._labels(definition, labels, self._gauges)
            self._gauges[(name, normalized)] = float(value)

    def observe(self, name: str, value: float, *, labels: Mapping[str, object] | None = None) -> None:
        """记录直方图观测值。"""

        if value < 0:
            raise ValueError("直方图观测值不能小于 0")
        definition = self._definition(name, MetricKind.HISTOGRAM)
        with self._lock:
            normalized = self._labels(definition, labels, self._histograms)
            key = (name, normalized)
            buckets = self._histograms.setdefault(key, [0] * (len(definition.buckets) + 1) + [0.0, 0.0])
            for index, boundary in enumerate(definition.buckets):
                if value <= boundary:
                    buckets[index] += 1
            buckets[len(definition.buckets)] += 1
            buckets[-2] += value
            buckets[-1] += 1

    def value(self, name: str, *, labels: Mapping[str, object] | None = None) -> float:
        """读取指标值；未提供标签时返回所有时间序列之和。"""

        definition = self._definition(name)
        storage: Mapping[tuple[str, tuple[tuple[str, str], ...]], object]
        if definition.kind == MetricKind.COUNTER:
            storage = self._counters
        elif definition.kind == MetricKind.GAUGE:
            storage = self._gauges
        else:
            raise TypeError("直方图请使用 snapshot 读取")
        with self._lock:
            if labels is None:
                return float(sum(float(value) for (metric_name, _), value in storage.items() if metric_name == name))
            normalized = self._labels(definition, labels, storage)
            return float(storage.get((name, normalized), 0.0))  # type: ignore[arg-type]

    def snapshot(self) -> dict[str, object]:
        """返回可传给告警引擎或运维端点的安全快照。"""

        samples: list[dict[str, object]] = []
        with self._lock:
            for (name, labels), value in sorted(self._counters.items()):
                samples.append(MetricSample(name, MetricKind.COUNTER.value, dict(labels), value).to_dict())
            for (name, labels), value in sorted(self._gauges.items()):
                samples.append(MetricSample(name, MetricKind.GAUGE.value, dict(labels), value).to_dict())
            for (name, labels), values in sorted(self._histograms.items()):
                definition = self.definitions[name]
                counts = values[: len(definition.buckets) + 1]
                samples.append(
                    MetricSample(
                        name,
                        MetricKind.HISTOGRAM.value,
                        dict(labels),
                        {
                            "buckets": {
                                str(boundary): counts[index]
                                for index, boundary in enumerate(definition.buckets)
                            }
                            | {"+Inf": counts[-1]},
                            "sum": values[-2],
                            "count": values[-1],
                        },
                    ).to_dict()
                )
        return {"schema_version": METRIC_SCHEMA_VERSION, "samples": samples}

    def prometheus_text(self) -> str:
        """导出无秘密的 Prometheus 文本；标签集合仍受定义限制。"""

        lines: list[str] = []
        snapshot = self.snapshot()
        for sample in snapshot["samples"]:
            assert isinstance(sample, dict)
            name = str(sample["name"])
            labels = sample["labels"]
            assert isinstance(labels, dict)
            label_text = ""
            if labels:
                label_text = "{" + ",".join(f'{key}="{str(value).replace(chr(92), chr(92) + chr(92)).replace(chr(34), chr(92) + chr(34))}"' for key, value in labels.items()) + "}"
            value = sample["value"]
            if sample["kind"] == MetricKind.HISTOGRAM.value:
                assert isinstance(value, dict)
                buckets = value["buckets"]
                assert isinstance(buckets, dict)
                for boundary, count in buckets.items():
                    bucket_labels = dict(labels)
                    bucket_labels["le"] = boundary
                    bucket_text = "{" + ",".join(f'{key}="{str(item).replace(chr(92), chr(92) + chr(92)).replace(chr(34), chr(92) + chr(34))}"' for key, item in bucket_labels.items()) + "}"
                    lines.append(f"{name}_bucket{bucket_text} {count}")
                lines.append(f"{name}_sum{label_text} {value['sum']}")
                lines.append(f"{name}_count{label_text} {value['count']}")
            else:
                lines.append(f"{name}{label_text} {value}")
        return "\n".join(lines) + ("\n" if lines else "")


@dataclass(frozen=True, slots=True)
class SpanRecord:
    """已结束的追踪 span。"""

    name: str
    trace_id: str
    span_id: str
    parent_span_id: str | None
    started_at: str
    ended_at: str
    duration_seconds: float
    status: str
    attributes: Mapping[str, object]

    def to_dict(self) -> dict[str, object]:
        """序列化 span，属性经过统一脱敏。"""

        return {
            "schema_version": TRACE_CONTEXT_SCHEMA_VERSION,
            "name": self.name,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_seconds": self.duration_seconds,
            "status": self.status,
            "attributes": dict(self.attributes),
        }


class Tracer:
    """轻量、可替换的进程内追踪器。"""

    def __init__(self, sink: TraceSink | None = None, *, clock: Callable[[], datetime] | None = None) -> None:
        self.sink = sink or NullSink()
        self.clock = clock or (lambda: datetime.now(UTC))

    @contextlib.contextmanager
    def span(
        self,
        name: str,
        *,
        context: TelemetryContext | None = None,
        attributes: Mapping[str, object] | None = None,
    ):
        """创建一个 span，异常只记录为 ERROR，不吞掉异常。"""

        parent = context or current_telemetry_context()
        child = parent.child() if parent is not None else new_telemetry_context()
        started = self.clock().astimezone(UTC)
        monotonic_started = time.perf_counter()
        status = "OK"
        safe_attributes = {
            str(key): redact_value(value, key=str(key))
            for key, value in (attributes or {}).items()
        }
        try:
            with telemetry_scope(child):
                yield child
        except Exception:
            status = "ERROR"
            raise
        finally:
            ended = self.clock().astimezone(UTC)
            record = SpanRecord(
                name=_redact_string(name),
                trace_id=child.trace_id,
                span_id=child.span_id,
                parent_span_id=child.parent_span_id,
                started_at=started.isoformat(),
                ended_at=ended.isoformat(),
                duration_seconds=max(0.0, time.perf_counter() - monotonic_started),
                status=status,
                attributes=safe_attributes,
            )
            self.sink.emit(record.to_dict())


class AlertSeverity(StrEnum):
    """告警严重程度。"""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertOperator(StrEnum):
    """告警阈值比较方式。"""

    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    EQ = "eq"


@dataclass(frozen=True, slots=True)
class AlertRule:
    """一个只依赖指标快照的告警规则。"""

    rule_id: str
    metric_name: str
    operator: AlertOperator
    threshold: float
    severity: AlertSeverity
    labels: Mapping[str, object] = field(default_factory=dict)
    for_seconds: float = 0.0


@dataclass(frozen=True, slots=True)
class AlertEvent:
    """告警触发或恢复事件。"""

    rule_id: str
    fingerprint: str
    transition: str
    severity: str
    value: float
    threshold: float
    occurred_at: str

    def to_dict(self) -> dict[str, object]:
        """返回不含原文的告警结构。"""

        return {
            "schema_version": ALERT_SCHEMA_VERSION,
            "rule_id": self.rule_id,
            "fingerprint": self.fingerprint,
            "transition": self.transition,
            "severity": self.severity,
            "value": self.value,
            "threshold": self.threshold,
            "occurred_at": self.occurred_at,
        }


class AlertManager:
    """有去重和恢复语义的指标告警评估器。"""

    def __init__(
        self,
        registry: MetricRegistry,
        *,
        sink: AlertSink | None = None,
        rules: Sequence[AlertRule] = (),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.registry = registry
        self.sink = sink or NullSink()
        self.rules = tuple(rules)
        self.clock = clock or (lambda: datetime.now(UTC))
        self._breach_started: dict[str, datetime] = {}
        self._firing: set[str] = set()
        self._lock = threading.Lock()

    @staticmethod
    def default_rules() -> tuple[AlertRule, ...]:
        """返回不依赖具体部署阈值的初始规则。"""

        return (
            AlertRule("outbox_stale", "outbox_oldest_age_seconds", AlertOperator.GT, 300.0, AlertSeverity.WARNING),
            AlertRule("rate_limit_store_unavailable", "rate_limit_store_errors_total", AlertOperator.GTE, 1.0, AlertSeverity.CRITICAL),
        )

    @staticmethod
    def _compare(value: float, operator: AlertOperator, threshold: float) -> bool:
        return {
            AlertOperator.GT: value > threshold,
            AlertOperator.GTE: value >= threshold,
            AlertOperator.LT: value < threshold,
            AlertOperator.LTE: value <= threshold,
            AlertOperator.EQ: value == threshold,
        }[operator]

    def evaluate(self) -> tuple[AlertEvent, ...]:
        """评估全部规则，只在状态变化时输出事件。"""

        now = self.clock().astimezone(UTC)
        emitted: list[AlertEvent] = []
        with self._lock:
            for rule in self.rules:
                value = self.registry.value(rule.metric_name, labels=rule.labels or None)
                breached = self._compare(value, rule.operator, rule.threshold)
                if breached:
                    started = self._breach_started.setdefault(rule.rule_id, now)
                    if now - started < timedelta(seconds=max(0.0, rule.for_seconds)):
                        continue
                else:
                    self._breach_started.pop(rule.rule_id, None)
                fingerprint = hashlib.sha256(
                    json.dumps({"rule_id": rule.rule_id, "labels": dict(rule.labels)}, sort_keys=True).encode()
                ).hexdigest()[:32]
                if breached and fingerprint not in self._firing:
                    self._firing.add(fingerprint)
                    emitted.append(self._event(rule, fingerprint, "firing", value, now))
                elif not breached and fingerprint in self._firing:
                    self._firing.remove(fingerprint)
                    emitted.append(self._event(rule, fingerprint, "resolved", value, now))
        for event in emitted:
            self.sink.emit(event.to_dict())
            self.registry.inc(
                "alert_events_total",
                labels={
                    "rule": event.rule_id,
                    "severity": event.severity,
                    "transition": event.transition,
                },
            )
        return tuple(emitted)

    @staticmethod
    def _event(
        rule: AlertRule,
        fingerprint: str,
        transition: str,
        value: float,
        now: datetime,
    ) -> AlertEvent:
        return AlertEvent(
            rule_id=rule.rule_id,
            fingerprint=fingerprint,
            transition=transition,
            severity=rule.severity.value,
            value=value,
            threshold=rule.threshold,
            occurred_at=now.isoformat(),
        )


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    """固定窗口限流结果；后端信息用于诊断，不参与业务状态。"""

    allowed: bool
    limit: int
    remaining: int
    retry_after: int
    backend: str = "in_memory"
    shared: bool = False


class RateLimitStoreUnavailable(RuntimeError):
    """共享限流存储无法判断本次请求时抛出。"""


class SharedRateLimiter(Protocol):
    """HTTP 层可以注入的共享限流端口。"""

    backend: str
    shared: bool

    def check(
        self,
        key: str,
        bucket: str,
        *,
        limit: int,
        window_seconds: int,
    ) -> RateLimitDecision: ...


class PostgreSQLRateLimiter:
    """使用 PostgreSQL 短期窗口表的多副本限流器。"""

    backend = "postgresql"
    shared = True

    def __init__(
        self,
        dsn: str,
        *,
        connection_factory: Callable[[str], Any] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(dsn, str) or not dsn.strip():
            raise ValueError("共享限流 DSN 不能为空")
        self.dsn = dsn
        self.connection_factory = connection_factory or (
            lambda value: psycopg.connect(value, autocommit=True)
        )
        self.clock = clock or (lambda: datetime.now(UTC))

    def check(
        self,
        key: str,
        bucket: str,
        *,
        limit: int,
        window_seconds: int,
    ) -> RateLimitDecision:
        """在事务内锁定窗口行，保证不同 API 副本共享同一计数。"""

        if not key or len(key) > 128 or not SAFE_ID_PATTERN.fullmatch(key):
            raise ValueError("共享限流 key 格式不正确")
        if not bucket or len(bucket) > 64 or not SAFE_ID_PATTERN.fullmatch(bucket):
            raise ValueError("共享限流 bucket 格式不正确")
        if limit <= 0 or window_seconds <= 0:
            raise ValueError("limit 和 window_seconds 必须大于 0")
        bucket_key = hashlib.sha256(key.encode("utf-8")).hexdigest()
        connection = None
        began = False
        now = self.clock().astimezone(UTC)
        try:
            connection = self.connection_factory(self.dsn)
            connection.execute("BEGIN")
            began = True
            row = connection.execute(
                """
                SELECT window_started_at, request_count
                FROM rate_limit_buckets
                WHERE bucket_key = %s AND bucket_name = %s
                FOR UPDATE
                """,
                (bucket_key, bucket),
            ).fetchone()
            if row is None:
                inserted = connection.execute(
                    """
                    INSERT INTO rate_limit_buckets(
                        bucket_key, bucket_name, window_started_at, request_count, updated_at
                    ) VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT(bucket_key, bucket_name) DO NOTHING
                    RETURNING window_started_at, request_count
                    """,
                    (bucket_key, bucket, now.isoformat(), 1, now.isoformat()),
                ).fetchone()
                if inserted is not None:
                    started = _parse_timestamp(inserted[0], fallback=now)
                    count = int(inserted[1])
                else:
                    row = connection.execute(
                        """
                        SELECT window_started_at, request_count
                        FROM rate_limit_buckets
                        WHERE bucket_key = %s AND bucket_name = %s
                        FOR UPDATE
                        """,
                        (bucket_key, bucket),
                    ).fetchone()
                    if row is None:
                        raise RateLimitStoreUnavailable("共享限流窗口在并发创建后不可见")
            if row is not None:
                started = _parse_timestamp(row[0], fallback=now)
                if now - started >= timedelta(seconds=window_seconds):
                    started = now
                    count = 1
                else:
                    count = int(row[1]) + 1
                connection.execute(
                    """
                    UPDATE rate_limit_buckets
                    SET window_started_at = %s, request_count = %s, updated_at = %s
                    WHERE bucket_key = %s AND bucket_name = %s
                    """,
                    (started.isoformat(), count, now.isoformat(), bucket_key, bucket),
                )
            connection.execute("COMMIT")
            began = False
            elapsed = max(0.0, (now - started).total_seconds())
            return RateLimitDecision(
                allowed=count <= limit,
                limit=limit,
                remaining=max(0, limit - count),
                retry_after=max(1, math.ceil(window_seconds - elapsed)),
                backend=self.backend,
                shared=True,
            )
        except RateLimitStoreUnavailable:
            raise
        except Exception as error:
            if connection is not None and began:
                with contextlib.suppress(Exception):
                    connection.execute("ROLLBACK")
            raise RateLimitStoreUnavailable("共享限流存储暂时不可用") from error
        finally:
            if connection is not None:
                with contextlib.suppress(Exception):
                    connection.close()


@dataclass(slots=True)
class ObservabilityRuntime:
    """把日志、指标、追踪和告警组合成一个可注入运行时。"""

    logger: StructuredLogger = field(default_factory=StructuredLogger)
    metrics: MetricRegistry = field(default_factory=MetricRegistry)
    tracer: Tracer = field(default_factory=Tracer)
    alerts: AlertManager | None = None
    _lease_expiry_seen: dict[str, int] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.alerts is None:
            self.alerts = AlertManager(
                self.metrics,
                rules=AlertManager.default_rules(),
            )

    def request_finished(
        self,
        context: TelemetryContext,
        *,
        method: str,
        path: str,
        status_code: int,
        duration_seconds: float,
        error_code: str | None = None,
    ) -> None:
        """记录一条 HTTP 请求的低基数指标和安全日志。"""

        surface = self.metrics.surface_for_path(path)
        labels = {
            "method": method.upper(),
            "surface": surface,
            "status_class": self.metrics._status_class(status_code),
        }
        self.metrics.inc("http_requests_total", labels=labels)
        self.metrics.observe("http_request_duration_seconds", duration_seconds, labels=labels)
        if error_code is not None:
            self.metrics.inc("http_errors_total", labels={"surface": surface, "error_code": error_code})
        self.logger.log(
            "INFO" if status_code < 500 else "ERROR",
            "http.request.completed",
            context=context,
            method=method.upper(),
            route_surface=surface,
            status_code=status_code,
            duration_ms=round(duration_seconds * 1000, 3),
            error_code=error_code,
        )
        if self.alerts is not None:
            self.alerts.evaluate()

    def observe_dependency(self, name: str, status: str) -> None:
        """写入固定标签的依赖状态。"""

        self.metrics.set("dependency_status", 1.0 if status == "up" else 0.0, labels={"dependency": name, "status": status})

    def observe_outbox_snapshot(self, snapshot: Iterable[Mapping[str, object]]) -> None:
        """记录 Outbox 水位、死信和过期租约，不接收消息正文。"""

        for item in snapshot:
            consumer = str(item.get("consumer", "other"))
            pending = max(0, int(item.get("pending", 0)))
            oldest_age = max(0.0, float(item.get("oldest_age_seconds", 0.0)))
            dead_letters = max(0, int(item.get("dead_letter_count", 0)))
            expired_leases = max(0, int(item.get("expired_lease_count", 0)))
            labels = {"consumer": consumer}
            self.metrics.set("outbox_pending_messages", pending, labels=labels)
            self.metrics.set("outbox_oldest_age_seconds", oldest_age, labels=labels)
            self.metrics.set("outbox_dead_letter_messages", dead_letters, labels=labels)
            previous = self._lease_expiry_seen.get(consumer, 0)
            if expired_leases > previous:
                self.metrics.inc(
                    "worker_lease_expiry_total",
                    expired_leases - previous,
                    labels=labels,
                )
            self._lease_expiry_seen[consumer] = expired_leases
        if self.alerts is not None:
            self.alerts.evaluate()

    def observe_rate_limit_store_error(self, backend: str = "postgresql") -> None:
        """记录共享限流后端不可用。"""

        self.metrics.inc("rate_limit_store_errors_total", labels={"backend": backend})
        if self.alerts is not None:
            self.alerts.evaluate()


def _parse_timestamp(value: object, *, fallback: datetime) -> datetime:
    """解析数据库窗口时间，异常时使用当前时间而不扩大窗口。"""

    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        try:
            result = datetime.fromisoformat(value)
        except ValueError:
            return fallback
    else:
        return fallback
    if result.tzinfo is None:
        result = result.replace(tzinfo=UTC)
    return result.astimezone(UTC)


__all__ = [
    "ALERT_SCHEMA_VERSION",
    "AlertEvent",
    "AlertManager",
    "AlertOperator",
    "AlertRule",
    "AlertSeverity",
    "InMemorySink",
    "JsonLineLogSink",
    "MetricDefinition",
    "MetricKind",
    "MetricRegistry",
    "MetricSample",
    "METRIC_SCHEMA_VERSION",
    "OBSERVABILITY_SCHEMA_VERSION",
    "OBSERVABILITY_CONTRACT",
    "ObservabilityRuntime",
    "PostgreSQLRateLimiter",
    "RateLimitDecision",
    "RateLimitStoreUnavailable",
    "SharedRateLimiter",
    "SpanRecord",
    "StructuredLogger",
    "TelemetryContext",
    "TraceParent",
    "Tracer",
    "TRACE_CONTEXT_SCHEMA_VERSION",
    "current_telemetry_context",
    "default_metric_definitions",
    "new_telemetry_context",
    "parse_traceparent",
    "redact_value",
    "telemetry_scope",
]
