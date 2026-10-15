from __future__ import annotations

from scripts.evaluate_context_coverage import DEFAULT_CASES, evaluate, load_cases


def test_context_coverage_cases_preserve_required_families_without_hcx() -> None:
    cases = load_cases(DEFAULT_CASES)
    assert len(cases) == 4
    report = evaluate(
        cases,
        minimum_required_marker_recall=1.0,
        minimum_forbidden_marker_rejection_rate=1.0,
        minimum_budget_adherence_rate=1.0,
        minimum_completeness_decision_accuracy=1.0,
    )
    assert report["required_marker_recall"] == 1.0
    assert report["forbidden_marker_rejection_rate"] == 1.0
    assert report["budget_adherence_rate"] == 1.0
    assert report["completeness_decision_accuracy"] == 1.0
    assert report["failed"] == 0
    assert report["hcx_calls"] == 0
