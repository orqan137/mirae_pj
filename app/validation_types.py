from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class UnsupportedClaim:
    """A consequential answer claim that could not be bound to evidence."""

    kind: str
    text: str
    normalized: str


@dataclass(frozen=True, slots=True)
class AnswerValidationResult:
    """Stable result contract shared by validation and recovery layers."""

    version: str
    valid: bool
    checked_claims: int
    product_value_relations_checked: int
    citations_checked: int
    rule_claims_checked: int
    citation_failures: int
    unsupported_claims: tuple[UnsupportedClaim, ...]
    sensitive_spans: tuple[str, ...]
    recommendation_blocked: bool
    question_coverage_failures: tuple[str, ...] = ()
    question_coverage_required: int = 0
    question_coverage_covered: int = 0

    def trace_fields(self) -> dict[str, object]:
        return {
            "name": "answer_validator",
            "version": self.version,
            "status": "ok" if self.valid else "blocked",
            "checked_claims": self.checked_claims,
            "product_value_relations_checked": self.product_value_relations_checked,
            "citations_checked": self.citations_checked,
            "rule_claims_checked": self.rule_claims_checked,
            "citation_failures": self.citation_failures,
            "unsupported_claims": len(self.unsupported_claims),
            "sensitive_spans": len(self.sensitive_spans),
            "recommendation_blocked": self.recommendation_blocked,
            "question_coverage_required": self.question_coverage_required,
            "question_coverage_covered": self.question_coverage_covered,
            "question_coverage_failures": list(self.question_coverage_failures),
        }
