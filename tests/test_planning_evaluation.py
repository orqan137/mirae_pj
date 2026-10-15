from __future__ import annotations

from scripts.evaluate_planning import DEFAULT_CASES, evaluate_cases, load_cases


def test_planning_goldset_passes_without_hcx() -> None:
    cases = load_cases(DEFAULT_CASES)
    results = evaluate_cases(cases)
    assert len(cases) == 8
    assert {
        "PLAN-IMPLICIT-ENUMERATION-001",
        "PLAN-TAX-ELLIPSIS-001",
        "PLAN-PROCEDURE-COREFERENCE-001",
        "PLAN-NOUN-COMMA-NONSPLIT-001",
    }.issubset(case["case_id"] for case in cases)
    assert all(result["passed"] for result in results), results
