from __future__ import annotations

from scripts.evaluate_tool_selection import DEFAULT_CASES, evaluate_cases, load_cases


def test_tool_selection_holdout_passes_without_hcx() -> None:
    cases = load_cases(DEFAULT_CASES)
    results = evaluate_cases(cases)
    assert len(cases) == 13
    assert {
        "TOOL-IMPLICIT-FEE-COMPARISON-001",
        "TOOL-TRANSFER-PARAPHRASE-001",
        "TOOL-CALCULATION-RELEVANCE-NEGATIVE-001",
        "TOOL-COMPLETE-RECOMMENDATION-001",
    }.issubset(case.case_id for case in cases)
    assert all(result["passed"] for result in results), results
