from __future__ import annotations

from dataclasses import replace

from scripts.evaluate_answer_guard import DEFAULT_CASES, evaluate_case, load_cases


def test_real_answer_guard_goldset_passes_without_model_calls() -> None:
    cases = load_cases(DEFAULT_CASES)
    results = [evaluate_case(case) for case in cases]
    assert len(cases) == 45
    assert all(result["passed"] for result in results), {
        result["case_id"]: result["failures"]
        for result in results
        if not result["passed"]
    }


def test_answer_guard_reports_expectation_regression() -> None:
    case = load_cases(DEFAULT_CASES)[0]
    changed = replace(case, expected_valid=False)
    result = evaluate_case(changed)
    assert result["passed"] is False
    assert "valid expected=False actual=True" in result["failures"]
