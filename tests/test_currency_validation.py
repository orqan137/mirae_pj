from __future__ import annotations

import pytest

from app.answer_validation import validate_answer
from app.evidence import EvidenceAssessment
from app.routing import classify_question
from app.validation_primitives import CURRENCY_RE, critical_citation_signatures


@pytest.mark.parametrize("expression", [
    "900만원", "900만 원", "900 만 원", "연900만원", "합산900만 원",
])
def test_currency_spacing_and_korean_prefixes_preserve_same_amount(expression: str) -> None:
    assert CURRENCY_RE.search(expression) is not None
    assert critical_citation_signatures(expression) == {"currency_won:9000000"}


@pytest.mark.parametrize("expression", ["A900만원", "ref_900만원", ".900만원"])
def test_currency_detection_does_not_take_fragments_from_identifiers(expression: str) -> None:
    assert CURRENCY_RE.search(expression) is None
    assert critical_citation_signatures(expression) == frozenset()


def test_currency_detection_keeps_complete_numbers_and_decimal_units() -> None:
    assert [match.group(1) for match in CURRENCY_RE.finditer("1900만원")] == ["1900"]
    assert critical_citation_signatures("평가금액410.21백만 원") == {"currency_won:410210000"}


def _validate(answer: str):
    question = "연금계좌 세액공제를 설명해 주세요."
    context = (
        "[EVIDENCE] source_id=doc41 citation_id=doc41:chunk=1\n"
        "IRP 세액공제 납입한도는 연금저축 납입액을 포함해서 연900만원입니다.\n\n"
        "[EVIDENCE] source_id=doc23 citation_id=doc23:chunk=1\n"
        "ISA 만기자금 전환 시 추가 세액공제 납입한도는 300만 원이고 합산 최대 한도는 1,200만 원입니다."
    )
    return validate_answer(
        answer=answer, question=question, evidence_context=context,
        route=classify_question(question),
        assessment=EvidenceAssessment(version="test", status="answerable", coverage=(), missing_conditions=()),
    )


def test_spaced_currency_claim_cannot_borrow_an_unrelated_citation() -> None:
    result = _validate(
        "ISA 만기자금을 전환하면 추가 세액공제 납입한도는 300만 원이고 "
        "합산 최대 한도는 1,200만 원입니다 [근거:doc41:chunk=1]."
    )
    assert not result.valid
    assert any(claim.kind == "numeric_citation_relation" for claim in result.unsupported_claims)


def test_spaced_currency_claim_matches_unspaced_korean_prefixed_evidence() -> None:
    result = _validate(
        "IRP 세액공제 납입한도는 연금저축 납입액을 포함해서 연 900만 원입니다 "
        "[근거:doc41:chunk=1]."
    )
    assert result.valid, result.unsupported_claims
