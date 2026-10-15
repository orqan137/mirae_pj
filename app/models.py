from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict


class EvaluationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    question_id: str
    question: str
    retrieved_context: str
    think_trace: str
    answer: str


@dataclass(slots=True)
class BackendOutput:
    answer: Any
    retrieved_context: Any = ""
    trace_summary: Any = ""
