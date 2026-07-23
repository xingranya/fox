"""F2.9 可观测性、脱敏、告警和共享限流测试。"""

from __future__ import annotations

import hashlib
import json
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from jsonschema import Draft202012Validator

from brand_os.observability import (
    OBSERVABILITY_CONTRACT,
    AlertManager,
    AlertOperator,
    AlertRule,
    AlertSeverity,
    InMemorySink,
    MetricDefinition,
    MetricKind,
    MetricRegistry,
    ObservabilityRuntime,
    PostgreSQLRateLimiter,
    RateLimitStoreUnavailable,
    StructuredLogger,
    Tracer,
    new_telemetry_context,
    parse_traceparent,
)
from brand_os.postgresql_outbox import PostgreSQLOutboxMixin
from brand_os.postgresql_migrations import (
    POSTGRESQL_RATE_LIMIT_MIGRATION,
    POSTGRESQL_SCHEMA_VERSION,
)


ROOT = Path(__file__).parents[2]
CONTRACT_PATH = ROOT / "contracts" / "phase2" / "observability.json"
SCHEMA_PATH = ROOT / "schemas" / "phase2" / "observability.schema.json"


class FakeCursor:
    """提供共享限流测试所需的最小游标。"""

    def __init__(self, row=None, rows=None) -> None:
        self.row = row
        self.rows = list(rows or [])

    def fetchone(self):
        return self.row

    def fetchall(self):
        return list(self.rows)


class FakeRateLimitDatabase:
    """在多个限流器实例之间共享窗口行。"""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], tuple[str, int]] = {}
        self.seen_keys: list[str] = []

    def connect(self, _dsn: str):
        return FakeRateLimitConnection(self)


class FakeRateLimitConnection:
    """只模拟 PostgreSQLRateLimiter 会执行的语句。"""

    def __init__(self, database: FakeRateLimitDatabase) -> None:
        self.database = database
        self.closed = False

    def execute(self, statement: str, parameters=()):
        sql = " ".join(statement.split()).upper()
        if sql.startswith(("BEGIN", "COMMIT", "ROLLBACK")):
            return FakeCursor()
        if sql.startswith("SELECT WINDOW_STARTED_AT"):
            bucket_key, bucket_name = str(parameters[0]), str(parameters[1])
            self.database.seen_keys.append(bucket_key)
            return FakeCursor(self.database.rows.get((bucket_key, bucket_name)))
        if sql.startswith("INSERT INTO RATE_LIMIT_BUCKETS"):
            bucket_key, bucket_name = str(parameters[0]), str(parameters[1])
            self.database.rows[(bucket_key, bucket_name)] = (
                str(parameters[2]),
                int(parameters[3]),
            )
            return FakeCursor(self.database.rows[(bucket_key, bucket_name)])
        if sql.startswith("UPDATE RATE_LIMIT_BUCKETS"):
            bucket_key, bucket_name = str(parameters[3]), str(parameters[4])
            self.database.rows[(bucket_key, bucket_name)] = (
                str(parameters[0]),
                int(parameters[1]),
            )
            return FakeCursor()
        raise AssertionError(f"未覆盖的共享限流 SQL：{sql}")

    def close(self) -> None:
        self.closed = True


class FakeOutboxConnection:
    """返回不含 Payload 的队列运行行。"""

    def __init__(self) -> None:
        now = datetime.now(UTC)
        self.consumers = [
            {"consumer_name": "default"},
            {"consumer_name": "workflow"},
        ]
        self.messages = [
            {
                "consumer_name": "default",
                "status": "PENDING",
                "created_at": (now - timedelta(seconds=30)).isoformat(),
                "lease_until": None,
            },
            {
                "consumer_name": "default",
                "status": "CLAIMED",
                "created_at": (now - timedelta(seconds=20)).isoformat(),
                "lease_until": (now - timedelta(seconds=1)).isoformat(),
            },
        ]
        self.dead_letters = [
            {"consumer_name": "default", "dead_letter_count": 1}
        ]

    def execute(self, statement: str, _parameters=()):
        sql = " ".join(statement.split()).upper()
        if "FROM OUTBOX_CONSUMERS" in sql:
            return FakeCursor(rows=self.consumers)
        if "FROM OUTBOX_MESSAGES" in sql:
            return FakeCursor(rows=self.messages)
        if "FROM DEAD_LETTER_MESSAGES" in sql:
            return FakeCursor(rows=self.dead_letters)
        raise AssertionError(f"未覆盖的 Outbox 指标 SQL：{sql}")

    def close(self) -> None:
        return None


class FakeOutboxStore(PostgreSQLOutboxMixin):
    """只为 collect_outbox_metrics 提供连接。"""

    def _connect(self):
        return FakeOutboxConnection()


class ObservabilityContractTest(unittest.TestCase):
    """冻结观测契约和 JSON Schema。"""

    def test_contract_matches_source_and_schema(self) -> None:
        contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertEqual(contract, OBSERVABILITY_CONTRACT)
        Draft202012Validator(schema).validate(contract)
        self.assertFalse(contract["shared_rate_limit"]["fallback_to_process_local"])
        self.assertFalse(contract["authority"]["may_approve_formal_state"])

    def test_shared_rate_limit_migration_is_versioned_and_does_not_store_raw_key(self) -> None:
        self.assertEqual(POSTGRESQL_SCHEMA_VERSION, 12)
        self.assertEqual(POSTGRESQL_RATE_LIMIT_MIGRATION.version, 11)
        ddl = "\n".join(POSTGRESQL_RATE_LIMIT_MIGRATION.statements)
        self.assertIn("CREATE TABLE rate_limit_buckets", ddl)
        self.assertIn("length(bucket_key) = 64", ddl)


class TelemetryAndLoggingTest(unittest.TestCase):
    """验证关联上下文和日志脱敏。"""

    def test_traceparent_is_inherited_and_unsafe_identifiers_are_replaced(self) -> None:
        trace_id = "1" * 32
        parent_span_id = "2" * 16
        context = new_telemetry_context(
            request_id="<bad request>",
            correlation_id="correlation-1",
            traceparent=f"00-{trace_id}-{parent_span_id}-01",
        )

        self.assertTrue(context.request_id.startswith("req_"))
        self.assertEqual(context.correlation_id, "correlation-1")
        self.assertEqual(context.trace_id, trace_id)
        self.assertEqual(context.parent_span_id, parent_span_id)
        self.assertEqual(parse_traceparent("00-" + "0" * 32 + "-" + "1" * 16 + "-00"), None)

    def test_structured_log_redacts_credentials_content_and_signed_urls(self) -> None:
        sink = InMemorySink()
        logger = StructuredLogger(sink)
        context = new_telemetry_context(request_id="req-1", correlation_id="cor-1")
        record = logger.info(
            "worker.completed",
            context=context,
            authorization="Bearer very-secret-token",
            password="plain-password",
            prompt="请复制全部原文",
            body={"content": "内部原文", "safe": "保留"},
            note="下载地址 https://example.test/file?token=url-secret&x=1",
            long_text="x" * 300,
            items=list(range(30)),
        )

        rendered = json.dumps(record, ensure_ascii=False)
        for secret in (
            "very-secret-token",
            "plain-password",
            "请复制全部原文",
            "内部原文",
            "url-secret",
        ):
            self.assertNotIn(secret, rendered)
        self.assertEqual(record["authorization"], "[REDACTED]")
        self.assertEqual(record["body"], "[REDACTED]")
        self.assertTrue(str(record["long_text"]).endswith("…"))
        self.assertEqual(len(record["items"]), 20)


class MetricsAndTracingTest(unittest.TestCase):
    """验证固定标签、时间序列上限和轻量追踪。"""

    def test_metric_labels_are_strict_bounded_and_exportable(self) -> None:
        registry = MetricRegistry()
        with self.assertRaisesRegex(ValueError, "指标标签不匹配"):
            registry.inc("http_requests_total", labels={"method": "GET"})

        registry.inc(
            "http_requests_total",
            labels={"method": "TRACE", "surface": "unknown-surface", "status_class": "2xx"},
        )
        registry.observe(
            "http_request_duration_seconds",
            0.03,
            labels={"method": "GET", "surface": "public", "status_class": "2xx"},
        )
        sample = next(
            item
            for item in registry.snapshot()["samples"]
            if item["name"] == "http_requests_total"
        )
        self.assertEqual(sample["labels"]["method"], "other")
        self.assertEqual(sample["labels"]["surface"], "other")
        prometheus = registry.prometheus_text()
        self.assertIn("http_requests_total", prometheus)
        self.assertIn("http_request_duration_seconds_bucket", prometheus)
        self.assertIn("http_request_duration_seconds_count", prometheus)

        bounded = MetricRegistry(
            [MetricDefinition("bounded_total", MetricKind.COUNTER, ("tenant",), max_series=2)]
        )
        for tenant in ("one", "two", "three"):
            bounded.inc("bounded_total", labels={"tenant": tenant})
        samples = bounded.snapshot()["samples"]
        self.assertEqual(len(samples), 2)
        self.assertIn("other", {item["labels"]["tenant"] for item in samples})

    def test_tracer_records_success_and_error_without_swallowing_exception(self) -> None:
        sink = InMemorySink()
        tracer = Tracer(sink)
        context = new_telemetry_context(request_id="req-1", correlation_id="cor-1")

        with tracer.span("http.request", context=context, attributes={"body": "secret"}):
            pass
        with self.assertRaisesRegex(RuntimeError, "boom"):
            with tracer.span("worker.run", context=context):
                raise RuntimeError("boom")

        self.assertEqual([record["status"] for record in sink.records], ["OK", "ERROR"])
        self.assertEqual(sink.records[0]["attributes"]["body"], "[REDACTED]")
        self.assertEqual(sink.records[0]["trace_id"], context.trace_id)


class AlertAndRuntimeTest(unittest.TestCase):
    """验证告警状态变化和运行时汇总。"""

    def test_alert_only_emits_on_firing_and_resolved_transitions(self) -> None:
        registry = MetricRegistry()
        sink = InMemorySink()
        manager = AlertManager(
            registry,
            sink=sink,
            rules=(
                AlertRule(
                    "outbox_stale",
                    "outbox_oldest_age_seconds",
                    AlertOperator.GT,
                    5,
                    AlertSeverity.WARNING,
                    labels={"consumer": "default"},
                ),
            ),
        )
        registry.set("outbox_oldest_age_seconds", 10, labels={"consumer": "default"})
        self.assertEqual([event.transition for event in manager.evaluate()], ["firing"])
        self.assertEqual(manager.evaluate(), ())
        registry.set("outbox_oldest_age_seconds", 0, labels={"consumer": "default"})
        self.assertEqual([event.transition for event in manager.evaluate()], ["resolved"])
        self.assertEqual(manager.evaluate(), ())
        self.assertEqual(len(sink.records), 2)

    def test_runtime_records_http_outbox_and_lease_delta(self) -> None:
        logs = InMemorySink()
        runtime = ObservabilityRuntime(logger=StructuredLogger(logs))
        context = new_telemetry_context(request_id="req-1", correlation_id="cor-1")
        runtime.request_finished(
            context,
            method="GET",
            path="/api/v1/employee/projects/hongri/state",
            status_code=200,
            duration_seconds=0.02,
        )
        runtime.observe_outbox_snapshot(
            [
                {
                    "consumer": "default",
                    "pending": 3,
                    "oldest_age_seconds": 12,
                    "dead_letter_count": 1,
                    "expired_lease_count": 1,
                }
            ]
        )
        runtime.observe_outbox_snapshot(
            [
                {
                    "consumer": "default",
                    "pending": 2,
                    "oldest_age_seconds": 4,
                    "dead_letter_count": 0,
                    "expired_lease_count": 3,
                }
            ]
        )

        self.assertEqual(runtime.metrics.value("http_requests_total"), 1)
        self.assertEqual(
            runtime.metrics.value("outbox_pending_messages", labels={"consumer": "default"}),
            2,
        )
        self.assertEqual(
            runtime.metrics.value("worker_lease_expiry_total", labels={"consumer": "default"}),
            3,
        )
        self.assertEqual(logs.records[0]["event"], "http.request.completed")

    def test_outbox_repository_snapshot_never_contains_payload(self) -> None:
        snapshot = FakeOutboxStore().collect_outbox_metrics()
        default = next(item for item in snapshot if item["consumer"] == "default")
        self.assertEqual(default["pending"], 2)
        self.assertGreaterEqual(default["oldest_age_seconds"], 29)
        self.assertEqual(default["dead_letter_count"], 1)
        self.assertEqual(default["expired_lease_count"], 1)
        self.assertTrue(all("payload" not in item for item in snapshot))


class PostgreSQLRateLimiterTest(unittest.TestCase):
    """验证多实例共享计数和后端失败语义。"""

    def test_instances_share_count_and_window_reset(self) -> None:
        database = FakeRateLimitDatabase()
        now = [datetime(2026, 7, 23, 10, tzinfo=UTC)]
        first = PostgreSQLRateLimiter(
            "postgresql://test",
            connection_factory=database.connect,
            clock=lambda: now[0],
        )
        second = PostgreSQLRateLimiter(
            "postgresql://test",
            connection_factory=database.connect,
            clock=lambda: now[0],
        )

        self.assertTrue(first.check("10.0.0.8", "agent", limit=2, window_seconds=60).allowed)
        self.assertTrue(second.check("10.0.0.8", "agent", limit=2, window_seconds=60).allowed)
        denied = first.check("10.0.0.8", "agent", limit=2, window_seconds=60)
        self.assertFalse(denied.allowed)
        self.assertEqual(denied.remaining, 0)
        expected_digest = hashlib.sha256(b"10.0.0.8").hexdigest()
        self.assertEqual(set(database.seen_keys), {expected_digest})
        self.assertNotIn("10.0.0.8", database.rows)

        now[0] += timedelta(seconds=61)
        reset = second.check("10.0.0.8", "agent", limit=2, window_seconds=60)
        self.assertTrue(reset.allowed)
        self.assertEqual(reset.remaining, 1)

    def test_store_failure_is_explicit_and_never_falls_back(self) -> None:
        def unavailable(_dsn: str):
            raise RuntimeError("database down")

        limiter = PostgreSQLRateLimiter(
            "postgresql://test",
            connection_factory=unavailable,
        )
        with self.assertRaises(RateLimitStoreUnavailable):
            limiter.check("10.0.0.8", "agent", limit=2, window_seconds=60)


if __name__ == "__main__":
    unittest.main()
