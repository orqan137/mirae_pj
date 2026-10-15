from __future__ import annotations

from scripts.evaluate_suitability import DEFAULT_CASES, evaluate_cases, load_cases


def test_suitability_goldset_passes_without_hcx() -> None:
    cases = load_cases(DEFAULT_CASES)
    results = evaluate_cases(cases)
    assert len(cases) == 5
    assert all(result["passed"] for result in results), results
