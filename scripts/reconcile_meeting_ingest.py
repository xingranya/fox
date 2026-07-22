#!/usr/bin/env python3
"""导入来源和会议 Fixture，并复跑同一请求验证零新增。"""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from brand_os.domain import Actor, ActorKind, CommandContext
from brand_os.manifest_import import load_source_manifest
from brand_os.meeting_ingest import load_meeting_ingest
from brand_os.sqlite_base import ProjectNotFound
from brand_os.sqlite_store import SQLiteCanonicalStore


COUNTED_TABLES = (
    "commands",
    "events",
    "meeting_ingest_batches",
    "meetings",
    "meeting_segments",
    "meeting_interpretation_items",
    "meeting_item_evidence",
    "meeting_conflict_candidates",
    "meeting_conflict_evidence",
    "meeting_batch_segments",
    "meeting_batch_items",
    "meeting_batch_conflicts",
    "state_items",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="导入会议 Fixture 并生成 F1.4 对账报告")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--meeting", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--project-id", default="meeting-fixture")
    parser.add_argument("--project-name", default="非鸿日会议回归样本")
    return parser.parse_args()


def table_counts(database: Path) -> dict[str, int]:
    """读取会议摄取事务会影响的表计数。"""

    with sqlite3.connect(database) as connection:
        return {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in COUNTED_TABLES
        }


def ensure_project(store: SQLiteCanonicalStore, project_id: str, project_name: str) -> None:
    """只在独立 Fixture 数据库首次运行时创建项目。"""

    try:
        store.get_project_version(project_id)
    except ProjectNotFound:
        store.create_project(
            CommandContext(
                project_id,
                Actor(ActorKind.SYSTEM, "meeting-reconciler"),
                f"create-project:{project_id}",
                0,
            ),
            project_name,
        )


def ensure_sources(
    store: SQLiteCanonicalStore, project_id: str, manifest_path: Path
) -> None:
    """把 Fixture 来源先登记为不可变版本。"""

    source_batch = load_source_manifest(manifest_path, origin_ref=manifest_path.name)
    version = store.get_project_version(project_id)
    store.import_source_batch(
        CommandContext(
            project_id,
            Actor(ActorKind.SYSTEM, "meeting-reconciler"),
            f"source-import:{source_batch.import_digest}:at-{version}",
            version,
        ),
        source_batch,
    )


def main() -> int:
    args = parse_args()
    args.database.parent.mkdir(parents=True, exist_ok=True)
    store = SQLiteCanonicalStore(args.database)
    ensure_project(store, args.project_id, args.project_name)
    ensure_sources(store, args.project_id, args.source_manifest)
    batch = load_meeting_ingest(args.meeting)

    start_version = store.get_project_version(args.project_id)
    context = CommandContext(
        args.project_id,
        Actor(ActorKind.AI, "fixture-interpreter"),
        f"meeting-ingest:{batch.ingest_digest}:at-{start_version}",
        start_version,
    )
    first = store.ingest_meeting_batch(context, batch)
    before_retry = table_counts(args.database)
    second = store.ingest_meeting_batch(context, batch)
    after_retry = table_counts(args.database)
    retry_delta = {table: after_retry[table] - before_retry[table] for table in COUNTED_TABLES}
    if any(retry_delta.values()):
        raise RuntimeError(f"重复会议摄取产生了新增记录：{retry_delta}")

    reconciliation = store.get_meeting_ingest_report(args.project_id, first.resource_id)
    report = {
        "schema_version": "f1.4-meeting-reconciliation.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "project_id": args.project_id,
        "fixture_only": True,
        "hongri_business_fact": False,
        "source_notice": "该会议不是鸿日资料，只用于验证 F1.4 摄取、降级和去重。",
        "meeting": {
            "meeting_id": batch.meeting_id,
            "source_version_id": batch.source_version_id,
            "source_sha256": batch.source_sha256,
            "source_verification": batch.source_verification,
            "content_sha256": batch.content_sha256,
            "ingest_digest": batch.ingest_digest,
            "base_state_version": batch.base_state_version,
        },
        "first_ingest": asdict(first),
        "second_ingest": asdict(second),
        "table_counts_before_retry": before_retry,
        "table_counts_after_retry": after_retry,
        "retry_delta": retry_delta,
        "reconciliation": reconciliation,
        "current_business_state_count": len(store.get_current_state(args.project_id)),
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "batch_id": first.resource_id,
                "first_replayed": first.replayed,
                "second_replayed": second.replayed,
                "retry_added_rows": sum(retry_delta.values()),
                "inventory": reconciliation["inventory"],
                "current_business_state_count": report["current_business_state_count"],
                "fixture_only": True,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
