from __future__ import annotations

import json
import re
from typing import Any

from app.errors import InvalidBackendResponse
from app.models import BackendOutput, EvaluationResponse


_SECRET_NAME = (
    r"authorization|proxy[-_ ]?authorization|api[-_ ]?key|"
    r"access[-_ ]?token|refresh[-_ ]?token|id[-_ ]?token|"
    r"client[-_ ]?secret|secret|password|passwd|credential|"
    r"private[-_ ]?key|cookie|set[-_ ]?cookie|session(?:[-_ ]?id)?"
)
_SECRET_KEY = re.compile(rf"(?i)^(?:{_SECRET_NAME})$")
_SECRET_PATTERNS = (
    re.compile(
        rf"(?i)([\"']?(?:{_SECRET_NAME})[\"']?\s*[:=]\s*)"
        r"(?:bearer\s+[^\s,;}\]]+|\"[^\"]*\"|'[^']*'|[^\s,;}\]]+)"
    ),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+\-/]+=*"),
)


def stringify(value: Any, *, list_tag: str = "ITEM") -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, (list, tuple)):
        return "\n\n".join(
            f"[{list_tag} {index}]\n{stringify(item, list_tag=list_tag)}"
            for index, item in enumerate(value, start=1)
        )
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _redact_structured(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if _SECRET_KEY.fullmatch(str(key)) else _redact_structured(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_structured(item) for item in value]
    if isinstance(value, str):
        return _SECRET_PATTERNS[1].sub("Bearer [REDACTED]", value)
    return value


def sanitize_trace(value: Any, *, max_chars: int = 4_000) -> str:
    structured = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            pass
        else:
            if isinstance(parsed, (dict, list)):
                structured = parsed
    trace = stringify(_redact_structured(structured))
    if not isinstance(structured, (dict, list, tuple)):
        for pattern in _SECRET_PATTERNS:
            trace = pattern.sub(
                lambda match: f"{match.group(1)}[REDACTED]"
                if match.lastindex == 1
                else "Bearer [REDACTED]",
                trace,
            )
    if len(trace) > max_chars:
        trace = f"{trace[:max_chars]}…[truncated]"
    return trace


def normalize_response(
    *, question_id: str, question: str, output: BackendOutput
) -> EvaluationResponse:
    answer = stringify(output.answer)
    if not answer.strip():
        raise InvalidBackendResponse("Agent backend returned an empty answer")

    return EvaluationResponse(
        question_id=question_id,
        question=question,
        retrieved_context=stringify(output.retrieved_context, list_tag="DOC"),
        think_trace=sanitize_trace(output.trace_summary),
        answer=answer,
    )
