"""从真实库只读备份生成 F1.10 桌面验收数据库。"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections.abc import Mapping, Sequence
from pathlib import Path

from brand_os.desktop_service import DesktopProjectService
from brand_os.domain import Actor, ActorKind, CommandContext, ProposalDraft
from brand_os.local_access import LocalAIService
from brand_os.sqlite_store import SQLiteCanonicalStore
from brand_os.task_packets import (
    RuntimeTaskDefinition,
    TaskContextRef,
    TaskEvidenceRef,
)


SCHEMA_VERSION = "f1.10-acceptance-database.v1"
EXPECTED_BASELINE = {
    "current_state_count": 0,
    "current_source_count": 9,
    "known_gap_count": 5,
    "proposal_count": 0,
    "runtime_task_count": 0,
    "task_packet_count": 0,
}
PROPOSALS = (
    (
        "F1.10-P-APPROVE",
        "F1.10-Q-APPROVE",
        "F1.10 验收：批准动作是否只能由 Fox 在桌面端完成？",
    ),
    (
        "F1.10-P-MODIFY",
        "F1.10-Q-MODIFY",
        "F1.10 验收：修改后的结构化内容是否按人工版本写入？",
    ),
    (
        "F1.10-P-REJECT",
        "F1.10-Q-REJECT",
        "F1.10 验收：证据不足的变化是否会保持在正式状态之外？",
    ),
    (
        "F1.10-P-CONFLICT",
        "F1.10-Q-CONFLICT",
        "F1.10 验收：陈旧页面提交是否会收到版本冲突？",
    ),
)


class AcceptanceFixtureError(RuntimeError):
    """表示真实基线或验收副本不符合 F1.10 门禁。"""


def file_sha256(path: Path) -> str:
    """流式计算文件哈希，不把数据库整体读入内存。"""

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_only_backup(source: Path, destination: Path) -> None:
    """通过 SQLite 在线备份 API 复制一致快照，源连接强制只读。"""

    if not source.is_file() or source.is_symlink():
        raise AcceptanceFixtureError("源数据库必须是普通文件且不能是符号链接")
    if destination.is_symlink():
        raise AcceptanceFixtureError("验收数据库不能是符号链接")
    if source.resolve() == destination.resolve(strict=False):
        raise AcceptanceFixtureError("验收数据库不能覆盖源数据库")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()
    with sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True) as source_db:
        with sqlite3.connect(destination) as destination_db:
            source_db.backup(destination_db)


def _require_counts(
    summary: Mapping[str, object], expected: Mapping[str, int], *, stage: str
) -> None:
    mismatches = {
        key: {"expected": expected_value, "actual": summary.get(key)}
        for key, expected_value in expected.items()
        if summary.get(key) != expected_value
    }
    if mismatches:
        raise AcceptanceFixtureError(
            f"{stage}计数不符合 F1.10 基线：{json.dumps(mismatches, ensure_ascii=False)}"
        )


def _source_evidence(view: Mapping[str, object]) -> tuple[str, Mapping[str, object]]:
    sources = view["sources"]
    if not isinstance(sources, Sequence):
        raise AcceptanceFixtureError("项目来源列表格式无效")
    selected = next(
        (
            source
            for source in sources
            if isinstance(source, Mapping) and source.get("source_role") == "decision_log"
        ),
        None,
    )
    if selected is None:
        raise AcceptanceFixtureError("真实基线缺少 decision_log 来源")
    source_version_id = selected.get("source_version_id")
    if not isinstance(source_version_id, str) or not source_version_id:
        raise AcceptanceFixtureError("decision_log 来源缺少版本 ID")
    return f"source-version:{source_version_id}#F1.10-desktop-acceptance", selected


def prepare_acceptance_database(
    source: Path, destination: Path, *, project_id: str = "hongri"
) -> Mapping[str, object]:
    """复制真实基线并只在副本中加入明确标记的验收数据。"""

    source_hash_before = file_sha256(source)
    _read_only_backup(source, destination)
    store = SQLiteCanonicalStore(destination)
    service = DesktopProjectService(store, project_id)
    baseline = service.get_project_view()
    baseline_summary = baseline["summary"]
    if not isinstance(baseline_summary, Mapping):
        raise AcceptanceFixtureError("项目摘要格式无效")
    _require_counts(baseline_summary, EXPECTED_BASELINE, stage="真实副本迁移后")

    evidence_ref, source_record = _source_evidence(baseline)
    ai = Actor(ActorKind.AI, "f1.10-fixture-agent")
    for proposal_id, subject_id, question in PROPOSALS:
        store.create_proposal(
            CommandContext(
                project_id,
                ai,
                f"prepare:{proposal_id}",
                store.get_project_version(project_id),
            ),
            ProposalDraft(
                proposal_id=proposal_id,
                proposal_kind="create",
                classification="OPEN",
                subject_id=subject_id,
                before=None,
                after={
                    "id": subject_id,
                    "question": question,
                    "fixture_scope": "F1.10_DESKTOP_E2E",
                },
                reason="这是桌面纵切验收数据，不代表鸿日业务事实或已批准方向。",
                impact_scope="仅限 F1.10 验收数据库副本",
                evidence_refs=(evidence_ref,),
            ),
        )

    gaps = baseline["known_gaps"]
    if not isinstance(gaps, Sequence):
        raise AcceptanceFixtureError("资料缺口列表格式无效")
    gap_ids = tuple(
        str(gap["gap_id"])
        for gap in gaps
        if isinstance(gap, Mapping) and isinstance(gap.get("gap_id"), str)
    )
    task = RuntimeTaskDefinition(
        task_id="F1.10-T-DESKTOP",
        goal="核对鸿日当前状态、资料缺口与待确认变化，并形成可回源工作稿",
        role="BRAND_STRATEGIST",
        work_mode="EVALUATION",
        deliverables=("形成带来源和缺口说明的验收工作稿",),
        non_goals=("不把验收问题写成鸿日业务事实", "不允许 AI 代替 Fox 批准"),
        context_refs=tuple(
            TaskContextRef("PROPOSAL", proposal_id)
            for proposal_id, _, _ in PROPOSALS
        ),
        evidence_refs=(TaskEvidenceRef(evidence_ref, "context"),),
        known_gap_ids=gap_ids,
        allowed_tools=("task_get_packet", "evidence_get", "proposal_create"),
        network="deny",
        model_allowlist=("codex", "claude"),
        output_schema_ref="state-proposal.v1",
        acceptance_criteria=(
            "所有结论可以回到已登记来源",
            "资料缺口明确显示",
            "正式变化只由 Fox 在桌面端确认",
        ),
    )
    fox = Actor(ActorKind.HUMAN, "Fox")
    store.register_runtime_task(
        project_id, fox, task, idempotency_key="prepare:F1.10-T-DESKTOP"
    )
    packet = store.build_task_packet(
        project_id,
        task.task_id,
        Actor(ActorKind.SYSTEM, "f1.10-packet-builder"),
    )

    acceptance = service.get_project_view()
    acceptance_summary = acceptance["summary"]
    if not isinstance(acceptance_summary, Mapping):
        raise AcceptanceFixtureError("验收项目摘要格式无效")
    _require_counts(
        acceptance_summary,
        {
            "current_state_count": 0,
            "current_source_count": 9,
            "known_gap_count": 5,
            "proposal_count": 4,
            "pending_proposal_count": 4,
            "runtime_task_count": 1,
            "task_packet_count": 1,
        },
        stage="验收数据写入后",
    )
    source_hash_after = file_sha256(source)
    if source_hash_after != source_hash_before:
        raise AcceptanceFixtureError("真实源数据库在生成验收副本时发生变化")
    return {
        "schema_version": SCHEMA_VERSION,
        "project_id": project_id,
        "source_database": str(source.resolve()),
        "source_sha256_before": source_hash_before,
        "source_sha256_after": source_hash_after,
        "source_unchanged": True,
        "destination_database": str(destination.resolve()),
        "destination_sha256": file_sha256(destination),
        "baseline_summary": dict(baseline_summary),
        "acceptance_summary": dict(acceptance_summary),
        "evidence_ref": evidence_ref,
        "evidence_source_path": source_record.get("relative_path"),
        "proposal_ids": [proposal_id for proposal_id, _, _ in PROPOSALS],
        "task_id": task.task_id,
        "task_packet_id": packet["packet_id"],
        "agent_can_approve": False,
    }


def advance_acceptance_version(
    database: Path,
    *,
    project_id: str = "hongri",
    proposal_id: str = "F1.10-P-VERSION-ADVANCE",
) -> Mapping[str, object]:
    """模拟 Agent 新增 Proposal，以验证桌面端陈旧版本冲突。"""

    if not database.is_file() or database.is_symlink():
        raise AcceptanceFixtureError("验收数据库必须是普通文件且不能是符号链接")
    store = SQLiteCanonicalStore(database)
    view = DesktopProjectService(store, project_id).get_project_view()
    evidence_ref, _ = _source_evidence(view)
    service = LocalAIService(store, project_id, caller_id="f1.10-conflict-probe")
    previous_version = store.get_project_version(project_id)
    result = service.invoke(
        "proposal_create",
        {
            "proposal_id": proposal_id,
            "proposal_kind": "create",
            "classification": "OPEN",
            "subject_id": f"{proposal_id}-QUESTION",
            "after": {
                "id": f"{proposal_id}-QUESTION",
                "question": "F1.10 验收：并发变化是否会阻止陈旧页面写入？",
                "fixture_scope": "F1.10_DESKTOP_E2E",
            },
            "reason": "模拟页面加载后的并发 Proposal，不改变正式状态。",
            "impact_scope": "仅限 F1.10 验收数据库副本",
            "evidence_refs": [evidence_ref],
            "expected_version": previous_version,
            "idempotency_key": f"advance:{proposal_id}",
        },
    )
    return {
        "schema_version": "f1.10-version-advance.v1",
        "project_id": project_id,
        "previous_version": previous_version,
        "current_version": store.get_project_version(project_id),
        "proposal": service.invoke(
            "proposal_get", {"proposal_id": result["proposal_id"]}
        )["proposal"],
        "current_state_count": len(store.get_current_state(project_id)),
        "agent_can_approve": False,
    }


def build_parser() -> argparse.ArgumentParser:
    """构建验收副本准备与冲突探针命令。"""

    parser = argparse.ArgumentParser(prog="prepare-f1-10-acceptance")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--source", type=Path, required=True)
    prepare.add_argument("--destination", type=Path, required=True)
    prepare.add_argument("--project", default="hongri")
    advance = commands.add_parser("advance")
    advance.add_argument("--database", type=Path, required=True)
    advance.add_argument("--project", default="hongri")
    advance.add_argument("--proposal-id", default="F1.10-P-VERSION-ADVANCE")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """执行命令并输出可归档 JSON 结果。"""

    args = build_parser().parse_args(argv)
    if args.command == "prepare":
        result = prepare_acceptance_database(
            args.source.expanduser(),
            args.destination.expanduser(),
            project_id=args.project,
        )
    else:
        result = advance_acceptance_version(
            args.database.expanduser(),
            project_id=args.project,
            proposal_id=args.proposal_id,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
