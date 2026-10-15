from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from scripts.evaluate_local_grounding import DEFAULT_CASES, evaluate_cases, load_cases


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_real_grounding_goldset_passes_without_model_calls() -> None:
    cases = load_cases(DEFAULT_CASES)
    results = evaluate_cases(cases)
    assert len(cases) == 25
    assert {
        "GROUND-IMPLICIT-COMPOUND-001",
        "GROUND-TAX-ELLIPSIS-001",
        "GROUND-PROCEDURE-COREFERENCE-001",
        "GROUND-PRODUCT-COREFERENCE-001",
        "GROUND-CONTRIBUTION-ISA-001",
        "GROUND-INVESTING-DEFAULT-001",
        "GROUND-TRANSFER-IN-KIND-001",
        "GROUND-PAYOUT-LIMIT-001",
        "GROUND-REMEDY-DIRECT-CLAIM-001",
    }.issubset(case.case_id for case in cases)
    assert all(result.passed for result in results), {
        result.case_id: result.failures for result in results if not result.passed
    }


def test_local_evaluator_reports_regression_instead_of_loosening_expectation() -> None:
    case = load_cases(DEFAULT_CASES)[0]
    changed = replace(case, expected_status="out_of_corpus")
    result = evaluate_cases((changed,))[0]
    assert result.passed is False
    assert "status expected=out_of_corpus actual=answerable" in result.failures
