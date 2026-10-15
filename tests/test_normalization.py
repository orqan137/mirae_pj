from __future__ import annotations

import json

import pytest

from app.errors import InvalidBackendResponse
from app.models import BackendOutput
from app.normalization import normalize_response, sanitize_trace, stringify


def test_stringify_normalizes_complex_context_to_one_string() -> None:
    value = [{"title": "문서", "page": 1}, None, 42, True]
    result = stringify(value, list_tag="DOC")
    assert result.startswith("[DOC 1]")
    assert '"title":"문서"' in result
    assert "[DOC 4]\ntrue" in result


def test_trace_masks_credentials_and_limits_length() -> None:
    trace = sanitize_trace(
        "authorization=abc api_key=def Bearer top.secret.token " + ("x" * 5000)
    )
    assert "abc" not in trace
    assert "def" not in trace
    assert "top.secret.token" not in trace
    assert "[REDACTED]" in trace
    assert trace.endswith("…[truncated]")


def test_trace_masks_credentials_in_structured_and_json_text() -> None:
    structured = sanitize_trace(
        {
            "api_key": "SHOULD_NOT_LEAK",
            "nested": {
                "access_token": "ALSO_SECRET",
                "client_secret": "CLIENT_SECRET",
                "password": "PASSWORD",
                "refresh_token": "REFRESH",
                "cookie": "SESSION_COOKIE",
            },
        }
    )
    json_text = sanitize_trace(
        '{"authorization":"Bearer NEVER_SHOW","safe":"ok"}'
    )
    assert "SHOULD_NOT_LEAK" not in structured
    assert "ALSO_SECRET" not in structured
    assert "NEVER_SHOW" not in json_text
    for secret in ("CLIENT_SECRET", "PASSWORD", "REFRESH", "SESSION_COOKIE"):
        assert secret not in structured
    assert structured.count("[REDACTED]") == 6
    assert "[REDACTED]" in json_text
    assert json.loads(json_text)["authorization"] == "[REDACTED]"
    assert json.loads(json_text)["safe"] == "ok"


def test_plain_authorization_header_does_not_leave_bearer_token() -> None:
    trace = sanitize_trace("Authorization: Bearer abc.def client_secret=hidden")
    assert "abc.def" not in trace
    assert "hidden" not in trace


def test_request_values_are_authoritative() -> None:
    response = normalize_response(
        question_id="Q-원문",
        question="질의 원문",
        output=BackendOutput(answer=123, retrieved_context=None, trace_summary=None),
    )
    assert response.question_id == "Q-원문"
    assert response.question == "질의 원문"
    assert response.answer == "123"
    assert response.retrieved_context == ""
    assert response.think_trace == ""


def test_empty_answer_is_not_a_success() -> None:
    with pytest.raises(InvalidBackendResponse):
        normalize_response(
            question_id="Q",
            question="질문",
            output=BackendOutput(answer="  "),
        )
