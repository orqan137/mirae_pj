from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from scripts.evaluate_hcx_precision import (
    PrecisionCase,
    _validate_endpoint,
    grade_response,
    load_cases,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _case(*, expected_hcx_called: bool = True) -> PrecisionCase:
    return PrecisionCase(
        case_id="GROUND-SYSTEM-001",
        category="system",
        question="DB형과 DC형 퇴직연금의 차이를 설명해 주세요.",
        expected_status="answerable",
        expected_route_labels=("system",),
        forbidden_claims=("개인 계좌를 조회했다는 표현",),
        expected_hcx_called=expected_hcx_called,
    )


def test_precision_plan_is_valid_and_capped_at_one_question() -> None:
    plan, cases = load_cases(
        PROJECT_ROOT / "benchmarks/config/hcx-precision-cases.json"
    )
    assert plan["maximum_logical_questions_per_run"] == 1
    assert len(cases) == 9
    assert any(case.expected_hcx_called for case in cases.values())
    assert any(not case.expected_hcx_called for case in cases.values())


def test_grade_response_requires_contract_trace_validation_and_usage() -> None:
    case = _case()
    payload = {
        "question_id": case.case_id,
        "question": case.question,
        "retrieved_context": "source_id=doc15",
        "think_trace": json.dumps(
            {
                "decision_status": "answerable",
                "route": ["system"],
                "answer_validation": "passed",
                "tools": [
                    {
                        "name": "hyperclova_x",
                        "status": "ok",
                        "telemetry": {
                            "usage": {
                                "prompt_tokens": 100,
                                "completion_tokens": 20,
                                "total_tokens": 120,
                            },
                            "rate_limit": {"remaining_requests": 59},
                        },
                    }
                ],
            },
            ensure_ascii=False,
        ),
        "answer": "근거에 따른 설명입니다.",
    }
    response = httpx.Response(
        200,
        headers={"content-type": "application/json"},
        json=payload,
    )
    result = grade_response(case, response=response, elapsed_ms=123)
    assert result["passed"] is True
    assert result["hcx_called_observed"] is True
    assert result["usage"]["total_tokens"] == 120
    assert "근거에 따른 설명입니다." not in json.dumps(result, ensure_ascii=False)


def test_grade_response_detects_unexpected_model_call() -> None:
    case = _case(expected_hcx_called=False)
    response = httpx.Response(
        200,
        headers={"content-type": "application/json"},
        json={
            "question_id": case.case_id,
            "question": case.question,
            "retrieved_context": "",
            "think_trace": json.dumps(
                {
                    "decision_status": "answerable",
                    "route": ["system"],
                    "tools": [{"name": "hyperclova_x", "status": "ok"}],
                }
            ),
            "answer": "설명",
        },
    )
    result = grade_response(case, response=response, elapsed_ms=1)
    assert result["passed"] is False
    assert result["checks"]["hcx_call_policy"] is False


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://example.com/not-answer",
        "https://user:secret@example.com/answer",
        "https://example.com/answer?token=secret",
        "file:///answer",
    ],
)
def test_validate_endpoint_rejects_non_contract_or_secret_bearing_urls(
    endpoint: str,
) -> None:
    with pytest.raises(ValueError):
        _validate_endpoint(endpoint)
