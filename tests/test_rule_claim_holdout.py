from __future__ import annotations

from scripts.evaluate_rule_claim_holdout import DEFAULT_CASES, evaluate, load_cases


def test_rule_claim_holdout_is_balanced_and_passes_without_hcx() -> None:
    cases = load_cases(DEFAULT_CASES)
    assert len(cases) == 16
    assert sum(case.expected_valid for case in cases) == 8
    report = evaluate(
        cases,
        minimum_positive_acceptance_rate=1.0,
        minimum_unsafe_rejection_rate=1.0,
    )
    assert report["positive_acceptance_rate"] == 1.0
    assert report["unsafe_rejection_rate"] == 1.0
    assert report["failed"] == 0
    assert report["hcx_calls"] == 0
