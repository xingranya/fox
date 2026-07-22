"""Phase 0 黄金用例与 BrandBench 契约测试。"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[2] / "scripts" / "phase0" / "verify_phase0_contracts.py"
SPEC = importlib.util.spec_from_file_location("verify_phase0_contracts", MODULE_PATH)
assert SPEC and SPEC.loader
verify = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = verify
SPEC.loader.exec_module(verify)


class VerifyPhase0ContractsTest(unittest.TestCase):
    """验证正式 Fixture 可以完整通过静态发布门。"""

    def test_golden_cases_cover_all_journeys_and_vetoes(self) -> None:
        data = verify.load_json(verify.GOLDEN_PATH)
        results = verify.validate_golden_cases(data)
        self.assertTrue(all(result.passed for result in results), [result.detail for result in results if not result.passed])

    def test_brandbench_remains_pending_without_fox_scores(self) -> None:
        data = verify.load_json(verify.BRANDBENCH_PATH)
        results = verify.validate_brandbench(data)
        self.assertTrue(all(result.passed for result in results), [result.detail for result in results if not result.passed])

    def test_brandbench_baseline_records_failed_quality_gate(self) -> None:
        data = verify.load_json(verify.BRANDBENCH_BASELINE_PATH)
        results = verify.validate_brandbench_baseline(data)
        self.assertTrue(all(result.passed for result in results), [result.detail for result in results if not result.passed])

    def test_humanized_brandbench_passes_quality_gate(self) -> None:
        data = verify.load_json(verify.BRANDBENCH_HUMANIZED_PATH)
        baseline = verify.load_json(verify.BRANDBENCH_BASELINE_PATH)
        results = verify.validate_brandbench_humanized(data, baseline)
        self.assertTrue(all(result.passed for result in results), [result.detail for result in results if not result.passed])

    def test_report_is_machine_readable_and_passes(self) -> None:
        report = verify.build_report()
        self.assertEqual(report["schema_version"], "phase0-contract-verification.v1")
        self.assertTrue(report["passed"])
        self.assertEqual(report["brandbench_baseline"], "completed_failed_quality_gate")
        self.assertEqual(report["brandbench_quality_gate"], "completed_passed_quality_gate")

    def test_openwork_remains_replaceable_and_has_no_business_authority(self) -> None:
        port_catalog = verify.load_json(verify.PORT_CATALOG_PATH)
        openwork_adapter = verify.load_json(verify.OPENWORK_ADAPTER_PATH)
        results = verify.validate_technical_boundary(port_catalog, openwork_adapter)
        self.assertTrue(all(result.passed for result in results), [result.detail for result in results if not result.passed])


if __name__ == "__main__":
    unittest.main()
