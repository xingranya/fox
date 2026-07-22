#!/usr/bin/env python3
"""验证 Phase 0 契约、黄金用例覆盖和 BrandBench 待评状态。"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).parents[2]
GOLDEN_PATH = ROOT / "fixtures" / "phase0" / "golden-cases.json"
BRANDBENCH_PATH = ROOT / "fixtures" / "phase0" / "brandbench-review-template.json"
BRANDBENCH_BASELINE_PATH = ROOT / "fixtures" / "phase0" / "brandbench-baseline-result.json"
PORT_CATALOG_PATH = ROOT / "contracts" / "phase0" / "port-catalog.json"
OPENWORK_ADAPTER_PATH = ROOT / "contracts" / "phase0" / "openwork-adapter.json"
REPORT_PATH = ROOT / ".work" / "phase0" / "contract-verification.json"

EXPECTED_CASE_IDS = {f"G-{index:02d}" for index in range(1, 17)}
EXPECTED_JOURNEYS = {
    "COLD_START", "MEETING_INTERPRETATION", "EVIDENCE_TRACE", "INCREMENTAL_UPDATE",
    "STRATEGY_EXPLORATION", "EXECUTION_DELIVERY", "MODEL_HANDOFF", "BRAND_REVIEW",
}
EXPECTED_VETOES = {
    "FABRICATED_FACT", "DISCUSSION_TO_DECISION", "TENTATIVE_TO_DEADLINE",
    "SUPERSEDED_AS_CURRENT", "UNTRACEABLE_CONCLUSION", "UNCONFIRMED_STATE_CHANGE",
    "FORCED_SINGLE_ANSWER_IN_EXPLORATION",
}
EXPECTED_DIMENSIONS = ["战略锋利度", "自然中文", "消费者真实", "记忆性", "产品咬合度", "证据纪律"]
FORBIDDEN_SENSITIVE_PATTERNS = {
    "完整 SHA-256": re.compile(r"\b[a-f0-9]{64}\b"),
    "本地来源 ID": re.compile(r"\bEX-[A-F0-9]{12}\b"),
    "疑似银行卡号": re.compile(r"(?<!\d)\d{16,19}(?!\d)"),
}


@dataclass(frozen=True)
class CheckResult:
    """描述一个契约验证结果。"""

    name: str
    passed: bool
    detail: str


def load_json(path: Path) -> dict[str, object]:
    """读取 UTF-8 JSON 对象。"""

    return json.loads(path.read_text(encoding="utf-8"))


def validate_golden_cases(data: dict[str, object]) -> list[CheckResult]:
    """验证 16 个用例、八条旅程、七项一票否决和脱敏边界。"""

    cases = data.get("cases", [])
    ids = {case.get("case_id") for case in cases}
    journeys = {case.get("journey") for case in cases}
    vetoes = {veto for case in cases for veto in case.get("vetoes", [])}
    serialized = json.dumps(data, ensure_ascii=False)
    results = [
        CheckResult("黄金用例数量", 10 <= len(cases) <= 20, f"实际 {len(cases)} 个"),
        CheckResult("固定用例 ID", ids == EXPECTED_CASE_IDS, f"缺失 {sorted(EXPECTED_CASE_IDS - ids)}"),
        CheckResult("八条真实旅程", journeys == EXPECTED_JOURNEYS, f"缺失 {sorted(EXPECTED_JOURNEYS - journeys)}"),
        CheckResult("七项一票否决", vetoes == EXPECTED_VETOES, f"缺失 {sorted(EXPECTED_VETOES - vetoes)}"),
        CheckResult(
            "用例断言完整",
            all(case.get("expected_assertions") and case.get("forbidden_outcomes") for case in cases),
            "每个用例必须同时声明必须结果和禁止结果",
        ),
    ]
    for name, pattern in FORBIDDEN_SENSITIVE_PATTERNS.items():
        results.append(CheckResult(f"脱敏检查：{name}", pattern.search(serialized) is None, "仓库 Fixture 不得包含该类内容"))
    return results


def validate_brandbench(data: dict[str, object]) -> list[CheckResult]:
    """验证评分维度和人工评分待办状态，禁止伪造人工基线。"""

    scores = data.get("scores", [])
    candidates = data.get("candidate_order", [])
    expected_pairs = {(candidate, dimension) for candidate in candidates for dimension in EXPECTED_DIMENSIONS}
    actual_pairs = {(row.get("candidate"), row.get("dimension")) for row in scores}
    pending = data.get("review_status") == "pending"
    all_unscored = all(row.get("score") is None for row in scores)
    reviewer = data.get("reviewer_confirmation", {})
    return [
        CheckResult("BrandBench 六维", data.get("dimensions") == EXPECTED_DIMENSIONS, "评分维度必须固定"),
        CheckResult("候选评分矩阵", actual_pairs == expected_pairs, f"应有 {len(expected_pairs)} 个评分单元"),
        CheckResult("人工基线待评", pending and all_unscored, "Fox 未评分前必须保持 pending/null"),
        CheckResult("匿名评审", reviewer.get("model_identity_hidden") is True, "必须隐藏模型身份"),
        CheckResult("未伪造评审人", reviewer.get("reviewed_by") is None, "当前尚无 Fox 人工确认"),
    ]


def validate_brandbench_baseline(data: dict[str, object]) -> list[CheckResult]:
    """验证 Fox 首轮匿名评分完整，并确认质量门没有被误报为通过。"""

    scores = data.get("scores", [])
    by_candidate: dict[str, dict[str, int]] = {}
    for row in scores:
        candidate = row.get("candidate")
        dimension = row.get("dimension")
        score = row.get("score")
        if isinstance(candidate, str) and isinstance(dimension, str) and isinstance(score, int):
            by_candidate.setdefault(candidate, {})[dimension] = score
    a_scores = by_candidate.get("A", {})
    b_scores = by_candidate.get("B", {})
    improved = sum(b_scores.get(dimension, 0) > a_scores.get(dimension, 0) for dimension in EXPECTED_DIMENSIONS)
    return [
        CheckResult("首轮人工评审完成", data.get("review_status") == "completed", "必须由 Fox 完成"),
        CheckResult("首轮评分矩阵完整", all(len(by_candidate.get(candidate, {})) == 6 for candidate in ["A", "B"]), "A/B 均须有六维评分"),
        CheckResult("首轮评审人为 Fox", data.get("reviewer_confirmation", {}).get("reviewed_by") == "Fox", "AI 不得代评"),
        CheckResult("首轮总分可复算", sum(a_scores.values()) == 19 and sum(b_scores.values()) == 12, "记录分数必须与用户输入一致"),
        CheckResult("质量门正确失败", improved == 0 and b_scores.get("自然中文") == 1, "Task Packet 版没有产生质量改善"),
    ]


def validate_technical_boundary(
    port_catalog: dict[str, object], openwork_adapter: dict[str, object]
) -> list[CheckResult]:
    """验证外部组件可替换、无服务器前置和 OpenWork 无业务批准权。"""

    forbidden_operations = set(port_catalog.get("forbidden_agent_operations", []))
    openwork_forbidden = set(openwork_adapter.get("forbidden_tools", []))
    separation = openwork_adapter.get("separation_rules", {})
    ownership = openwork_adapter.get("data_ownership", {})
    return [
        CheckResult("无服务器前置", port_catalog.get("required_server_components") == [], "本地 MVP 不依赖服务器组件"),
        CheckResult("外部组件可选", all(not port.get("required") for port in port_catalog.get("ports", []) if port.get("name") == "ExternalWorkflowPort"), "外部工作流必须可禁用"),
        CheckResult("Agent 禁止业务批准", {"approve", "reject", "switch_work_mode"}.issubset(forbidden_operations), "批准和模式切换不进入 Agent 能力表"),
        CheckResult("OpenWork 无业务权威", openwork_adapter.get("business_authority") is False, "OpenWork 只能是适配器"),
        CheckResult("OpenWork 不要求远程服务", openwork_adapter.get("server_required") is False and openwork_adapter.get("remote_host_required") is False, "本地纵切不依赖远程 Host"),
        CheckResult("权限与批准分路", separation.get("shared_route") is False and separation.get("shared_handler") is False and separation.get("tool_permission_can_change_business_state") is False, "Tool Permission 不得改变业务状态"),
        CheckResult("OpenWork 禁止最终化工具", {"proposal_approve", "proposal_reject", "mode_switch_force"}.issubset(openwork_forbidden), "客户端工具表不得出现业务最终化能力"),
        CheckResult("正式数据不归 OpenWork", all(ownership.get(field) is False for field in ["canonical_state_in_openwork", "evidence_in_openwork", "approvals_in_openwork"]), "删除运行态不得丢业务数据"),
    ]


def build_report() -> dict[str, object]:
    """执行全部静态契约检查并生成机器可读报告。"""

    results = validate_golden_cases(load_json(GOLDEN_PATH))
    results.extend(validate_brandbench(load_json(BRANDBENCH_PATH)))
    results.extend(validate_brandbench_baseline(load_json(BRANDBENCH_BASELINE_PATH)))
    results.extend(
        validate_technical_boundary(
            load_json(PORT_CATALOG_PATH), load_json(OPENWORK_ADAPTER_PATH)
        )
    )
    return {
        "schema_version": "phase0-contract-verification.v1",
        "passed": all(result.passed for result in results),
        "checks": [result.__dict__ for result in results],
        "brandbench_baseline": "completed_failed_quality_gate",
    }


def main() -> int:
    """运行检查，按需写入被 Git 忽略的本地报告。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-report", action="store_true", help="写入 .work/phase0 本地报告")
    args = parser.parse_args()
    report = build_report()
    if args.write_report:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
