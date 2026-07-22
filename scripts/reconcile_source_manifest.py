#!/usr/bin/env python3
"""执行一次来源 Manifest 导入，并用同一请求复跑验证零新增。"""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from brand_os.domain import Actor, ActorKind, CommandContext
from brand_os.manifest_import import load_source_gaps, load_source_manifest
from brand_os.sqlite_base import ProjectNotFound
from brand_os.sqlite_store import SQLiteCanonicalStore


COUNTED_TABLES = (
    "commands",
    "events",
    "source_import_batches",
    "source_contents",
    "logical_sources",
    "source_versions",
    "source_aliases",
    "source_version_relations",
    "source_gaps",
    "state_items",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="导入来源 Manifest 并生成幂等对账报告")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--gap-file", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--project-id", default="hongri")
    parser.add_argument("--project-name", default="鸿日")
    return parser.parse_args()


def table_counts(database: Path) -> dict[str, int]:
    """读取对账涉及的全部表计数。"""

    with sqlite3.connect(database) as connection:
        return {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in COUNTED_TABLES
        }


def ensure_project(store: SQLiteCanonicalStore, project_id: str, project_name: str) -> None:
    """只在数据库首次建立时创建项目。"""

    try:
        store.get_project_version(project_id)
    except ProjectNotFound:
        store.create_project(
            CommandContext(
                project_id,
                Actor(ActorKind.SYSTEM, "source-reconciler"),
                f"create-project:{project_id}",
                0,
            ),
            project_name,
        )


def main() -> int:
    args = parse_args()
    gaps = load_source_gaps(args.gap_file) if args.gap_file else ()
    batch = load_source_manifest(
        args.manifest,
        origin_ref=args.manifest.name,
        additional_gaps=gaps,
    )
    args.database.parent.mkdir(parents=True, exist_ok=True)
    store = SQLiteCanonicalStore(args.database)
    ensure_project(store, args.project_id, args.project_name)

    start_version = store.get_project_version(args.project_id)
    context = CommandContext(
        args.project_id,
        Actor(ActorKind.SYSTEM, "source-reconciler"),
        f"source-import:{batch.import_digest}:at-{start_version}",
        start_version,
    )
    first = store.import_source_batch(context, batch)
    before_retry = table_counts(args.database)
    second = store.import_source_batch(context, batch)
    after_retry = table_counts(args.database)
    retry_delta = {table: after_retry[table] - before_retry[table] for table in COUNTED_TABLES}
    if any(retry_delta.values()):
        raise RuntimeError(f"重复导入产生了新增记录：{retry_delta}")

    reconciliation = store.get_source_import_report(args.project_id, first.resource_id)
    report = {
        "schema_version": "f1.3-source-reconciliation.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "project_id": args.project_id,
        "manifest": {
            "origin_ref": batch.origin_ref,
            "manifest_schema_version": batch.manifest_schema_version,
            "manifest_sha256": batch.manifest_sha256,
            "import_digest": batch.import_digest,
            "record_count": len(batch.records),
            "gap_count": len(batch.gaps),
        },
        "first_import": asdict(first),
        "second_import": asdict(second),
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
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
