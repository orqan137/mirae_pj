from __future__ import annotations

import pytest

from app.answer_recovery import (
    attach_unambiguous_evidence_citations,
    drop_unsupported_lines,
    render_grounded_extractive_fallback,
)
from app.answer_validation import validate_answer
from app.evidence import EvidenceAssessment, EvidenceCoverage
from app.validation_types import AnswerValidationResult, UnsupportedClaim
from app.routing import classify_question


def _failed(*, claims: tuple[UnsupportedClaim, ...]) -> AnswerValidationResult:
    return AnswerValidationResult(
        version="test",
        valid=False,
        checked_claims=len(claims),
        product_value_relations_checked=0,
        citations_checked=0,
        rule_claims_checked=len(claims),
        citation_failures=0,
        unsupported_claims=claims,
        sensitive_spans=(),
        recommendation_blocked=False,
    )


def _assessment(label: str, *refs: str) -> EvidenceAssessment:
    return EvidenceAssessment(
        version="test",
        status="answerable",
        coverage=(
            EvidenceCoverage(
                requirement_id="r1",
                label=label,
                status="supported",
                evidence_refs=refs,
            ),
        ),
        missing_conditions=(),
    )


def _context(source_id: str, body: str) -> str:
    return (
        f"[EVIDENCE] source_id={source_id} source_path=일반문서/{source_id}.docx "
        f"locator=unavailable chunk=1 citation_id={source_id}:chunk=1\n{body}"
    )


def test_fallback_keeps_a_sentence_that_answers_without_sharing_question_words() -> None:
    """Relevance is not only lexical overlap with the question.

    "사유에 해당하지 않는다면 연금외수령으로 16.5% 기타소득세가 과세된다"
    answers a question about 중도해지 세금 while sharing no word with it.
    Requiring a shared token dropped it, and the fallback quoted unrelated
    lines until the coverage check refused the whole answer.
    """

    question = "연금저축을 중도해지하면 세금이 어떻게 되나요"
    context = _context(
        "doc20",
        "연금계좌에서 돈을 꺼낼 수 있게 됐다고 해서 끝이 아니다.\n"
        "사유에 해당하지 않는다면 연금외수령으로 16.5% 기타소득세가 과세된다.\n"
        "부득이한 사유에 해당하면 중도 인출 시에도 전액 5.5% ~ 3.3% "
        "저율의 연금소득세를 적용 받는다.",
    )
    answer = render_grounded_extractive_fallback(
        question=question,
        evidence_context=context,
        assessment=_assessment("연금 세제 근거", "doc20:chunk=1"),
        route=classify_question(question),
    )
    assert answer is not None
    assert "기타소득세" in answer or "연금소득세" in answer
    assert "[근거:doc20:chunk=1]" in answer


def test_fallback_never_quotes_a_question_heading_as_evidence() -> None:
    """Several organizer documents are FAQs, so their headings are questions.

    Quoting "IRP에 누가 가입할 수 있나요" restates the question instead of
    answering it, and the heading carries no rule the reader can act on.
    """

    question = "퇴직연금 DC형에서 운용지시를 하지 않으면 어떻게 되나요"
    context = _context(
        "doc32",
        "⑤ 퇴직연금 DC 형에서 운용지시는 어떻게 하나요?\n"
        "② 개인형 퇴직연금제도(IRP)에 누가 가입할 수 있나요\n"
        "운용지시를 하지 않으면 사전에 지정한 디폴트옵션으로 자동매수 됩니다.",
    )
    answer = render_grounded_extractive_fallback(
        question=question,
        evidence_context=context,
        assessment=_assessment("연금 제도 근거", "doc32:chunk=1"),
        route=classify_question(question),
    )
    assert answer is not None
    assert "디폴트옵션으로 자동매수" in answer
    assert "누가 가입할 수 있나요" not in answer
    assert "운용지시는 어떻게 하나요" not in answer


def test_fallback_preserves_table_headers_with_every_amount() -> None:
    question = "IRP 세액공제 한도가 얼마인가요?"
    context = _context(
        "doc23",
        "[TABLE 1 ROW 1]\n구분\t세액공제 납입한도\tISA 추가 공제한도\t합산 최대 공제\n\n"
        "[TABLE 1 ROW 2]\n세액공제 한도\t900만 원\t300만 원\t1,200만 원",
    )
    assessment = _assessment("연금 세제 근거", "doc23:chunk=1")
    route = classify_question(question)
    answer = render_grounded_extractive_fallback(
        question=question, evidence_context=context, assessment=assessment, route=route
    )
    assert answer is not None
    assert "세액공제 납입한도: 900만 원" in answer
    assert "ISA 추가 공제한도: 300만 원" in answer
    assert "합산 최대 공제: 1,200만 원" in answer
    assert answer.count("900만 원") == 1
    assert "[TABLE" not in answer
    result = validate_answer(
        answer=answer, question=question, evidence_context=context,
        route=route, assessment=assessment,
    )
    assert result.valid, result.unsupported_claims


@pytest.mark.parametrize("table", [
    "[TABLE 1 ROW 2]\n세액공제 한도\t900만 원\t300만 원",
    "[TABLE 1 ROW 1]\n구분\t\t합산\n[TABLE 1 ROW 2]\n세액공제 한도\t900만 원\t300만 원",
    "[TABLE 1 ROW 1]\n구분\t한도\n[TABLE 1 ROW 2]\n세액공제 한도\t900만 원\t300만 원",
    "[TABLE 1 ROW 1]\n구분\t한도\n[TABLE 2 ROW 2]\n세액공제 한도\t900만 원",
    "세액공제 한도\t900만 원\t300만 원\t1,200만 원",
])
def test_fallback_does_not_invent_missing_or_merged_table_columns(table: str) -> None:
    question = "IRP 세액공제 한도가 얼마인가요?"
    answer = render_grounded_extractive_fallback(
        question=question,
        evidence_context=_context("doc23", table),
        assessment=_assessment("연금 세제 근거", "doc23:chunk=1"),
        route=classify_question(question),
    )
    assert answer is None


def test_fallback_keeps_self_contained_prose_after_an_ambiguous_table() -> None:
    question = "IRP 세액공제 한도가 얼마인가요?"
    context = _context(
        "doc23", "[TABLE 1 ROW 2]\n세액공제 한도\t900만 원\t300만 원\t1,200만 원"
    ) + "\n\n" + _context(
        "doc41", "IRP 세액공제 납입한도는 연금저축 납입액을 포함해서 연900만원이다."
    )
    answer = render_grounded_extractive_fallback(
        question=question, evidence_context=context,
        assessment=_assessment("연금 세제 근거", "doc23:chunk=1", "doc41:chunk=1"),
        route=classify_question(question),
    )
    assert answer is not None
    assert "연금저축 납입액을 포함해서 연900만원" in answer
    assert "300만 원" not in answer


def test_header_bound_withdrawal_rule_is_checked_against_the_same_table_row() -> None:
    question = "IRP 중도인출 사유와 세금을 알려주세요."
    context = _context(
        "doc20",
        "[TABLE 1 ROW 1]\n사유\t근퇴법상중도인출 사유\t세법상부득이한 사유\t세율\n"
        "[TABLE 1 ROW 2]\n가입자 또는 부양가족의 6개월 이상 요양\tO\t"
        "O *3개월 이상 요양 시 적용(단, 200만원 + 의료실비 + 휴직월수 x 150만원 한도)\t"
        "5.5% ~ 3.3% 연금소득세",
    )
    assessment = _assessment("연금 절차 근거", "doc20:chunk=1")
    route = classify_question(question)
    answer = render_grounded_extractive_fallback(
        question=question, evidence_context=context, assessment=assessment, route=route,
    )
    assert answer is not None
    assert "근퇴법상중도인출 사유: O" in answer
    result = validate_answer(
        question=question, answer=answer, evidence_context=context,
        assessment=assessment, route=route,
    )
    assert result.valid, result.unsupported_claims
    fabricated = validate_answer(
        question=question, answer=answer.replace("5.5%", "99.9%"),
        evidence_context=context, assessment=assessment, route=route,
    )
    assert not fabricated.valid


def test_table_header_normalization_does_not_allow_reversed_rule_polarity() -> None:
    question = "IRP 중도인출 사유와 세금을 알려주세요."
    context = _context(
        "doc20",
        "[TABLE 1 ROW 1]\n사유\t중도인출\t세율\n"
        "[TABLE 1 ROW 2]\n가입자 또는 부양가족의 6개월 이상 요양\t가능\t"
        "5.5% 연금소득세",
    )
    assessment = _assessment("연금 절차 근거", "doc20:chunk=1")
    route = classify_question(question)
    answer = render_grounded_extractive_fallback(
        question=question, evidence_context=context, assessment=assessment, route=route,
    )
    assert answer is not None
    supported = validate_answer(
        question=question, answer=answer, evidence_context=context,
        assessment=assessment, route=route,
    )
    assert supported.valid, supported.unsupported_claims
    reversed_rule = validate_answer(
        question=question, answer=answer.replace("중도인출: 가능", "중도인출: 불가"),
        evidence_context=context, assessment=assessment, route=route,
    )
    assert not reversed_rule.valid
    assert any(
        claim.kind == "rule_polarity_contradiction"
        for claim in reversed_rule.unsupported_claims
    )


def test_fallback_answers_every_subquestion_of_a_compound_question() -> None:
    """A compound question is not answered when only some of it is.

    The three subquestions here are DB/DC 차이, 세액공제 한도, 중도인출 절차.
    Two bullets already satisfied the union of the question's slots, so the
    selection stopped and DB/DC 차이 was dropped entirely while the validator
    still reported full coverage.
    """

    question = (
        "DB형과 DC형 퇴직연금의 차이를 설명해 주세요. 그리고 IRP 세액공제 한도도 "
        "알려주세요. 또한 IRP 중도인출 사유와 절차도 알려주세요."
    )
    assessment = EvidenceAssessment(
        version="test",
        status="answerable",
        coverage=(
            EvidenceCoverage(
                requirement_id="system",
                subquestion_id="sq1",
                label="연금 제도 근거",
                status="supported",
                evidence_refs=("doc11:chunk=1",),
            ),
            EvidenceCoverage(
                requirement_id="tax",
                subquestion_id="sq2",
                label="연금 세제 근거",
                status="supported",
                evidence_refs=("doc41:chunk=1",),
            ),
            EvidenceCoverage(
                requirement_id="procedure",
                subquestion_id="sq3",
                label="연금 절차 근거",
                status="supported",
                evidence_refs=("doc20:chunk=1",),
            ),
        ),
        missing_conditions=(),
    )
    context = "\n\n".join(
        (
            _context(
                "doc11",
                "확정급여형 퇴직연금제도(DB제도)는 근로자가 받을 급여가 사전에 "
                "확정된 제도이다.",
            ),
            _context(
                "doc41",
                "세액공제 납입한도는 연 900만 원이고 연금저축 단독으로는 600만 원이다.",
            ),
            _context(
                "doc20",
                "부득이한 사유에 해당하면 중도 인출 시에도 저율의 연금소득세를 "
                "적용 받는다.",
            ),
        )
    )
    answer = render_grounded_extractive_fallback(
        question=question,
        evidence_context=context,
        assessment=assessment,
        route=classify_question(question),
    )
    assert answer is not None
    for reference in ("doc11:chunk=1", "doc41:chunk=1", "doc20:chunk=1"):
        assert f"[근거:{reference}]" in answer


def test_trim_keeps_cited_sentences_sharing_a_line_with_an_unsupported_one() -> None:
    """The validator flags a sentence, so a sentence is what is removed.

    Dropping the whole line discarded the cited sentences beside it, and what
    remained was usually too short to keep, so a well-grounded answer was
    refused instead of trimmed.
    """

    answer = (
        "DB형은 근로자가 퇴직할 때 받을 급여가 사전에 확정된 제도입니다 "
        "[근거:doc15:chunk=1]. 자세한 사항은 금융기관에 문의하시기 바랍니다. "
        "DC형은 회사가 납입한 부담금을 근로자가 직접 운용하는 제도입니다 "
        "[근거:doc15:chunk=1]. 두 제도는 급여 산정 방식이 서로 다릅니다 "
        "[근거:doc15:chunk=1]."
    )
    result = _failed(claims=(
            UnsupportedClaim(
                "rule_citation_missing",
                "자세한 사항은 금융기관에 문의하시기 바랍니다",
                "rule_claim_without_citation",
            ),
        ),
    )
    trimmed = drop_unsupported_lines(answer, result)
    assert trimmed is not None
    assert "문의하시기 바랍니다" not in trimmed
    assert "DB형은 근로자가 퇴직할 때 받을 급여가 사전에 확정된 제도입니다" in trimmed
    assert "DC형은 회사가 납입한 부담금을 근로자가 직접 운용하는 제도입니다" in trimmed


def test_repair_cites_a_rule_claim_that_carries_no_figure() -> None:
    """Repair was written for figures and skipped every figure-free rule.

    Most of what the model leaves uncited is a plain rule, so the answer was
    discarded for a missing marker while its evidence sat in the context.
    """

    answer = "DB와 DC 제도는 재직 중인 회사를 통해서만 이전 신청이 가능합니다."
    context = _context("doc35", "DB,DC제도는 재직중인 회사를 통해서만 이전 신청 가능")
    result = _failed(claims=(
            UnsupportedClaim(
                "rule_citation_missing",
                "DB와 DC 제도는 재직 중인 회사를 통해서만 이전 신청이 가능합니다",
                "rule_claim_without_citation",
            ),
        ),
    )
    repaired = attach_unambiguous_evidence_citations(answer, context, result)
    assert repaired is not None
    assert "[근거:doc35:chunk=1]" in repaired


def test_fallback_does_not_quote_a_document_heading_as_evidence() -> None:
    """A heading names the document; it states nothing the reader can act on.

    Quoting "연금저축, IRP에서 해외 ETF투자할 때 세금 안내" as supporting
    evidence says only that the document exists.
    """

    question = "연금저축 세금은 어떻게 되나요"
    context = _context(
        "doc42",
        "연금저축, IRP에서 해외 ETF투자할 때 세금 안내(일반계좌 비교)\n"
        "연금저축계좌에서 발생한 수익은 인출할 때 과세된다.",
    )
    answer = render_grounded_extractive_fallback(
        question=question,
        evidence_context=context,
        assessment=_assessment("연금 세제 근거", "doc42:chunk=1"),
        route=classify_question(question),
        document_titles={"doc42:chunk=1": "연금저축·IRP 해외 ETF 세금"},
    )
    assert answer is not None
    assert "해외 ETF투자할 때 세금 안내" not in answer
    assert "인출할 때 과세된다" in answer


def test_table_row_figures_do_not_outrank_the_rule_they_instance() -> None:
    """Counting figures favours the sentence that answers a "how much" question.

    A normalised table row carries the figures of one case, so density let a
    row about 요양 beat the rule it is an instance of, answering about a 사유
    the question never raised.
    """

    question = "연금저축을 중도해지하면 세금이 어떻게 되나요"
    context = _context(
        "doc20",
        "[TABLE 1 ROW 1] 사유\t근퇴법상 사유\t세율\n"
        "[TABLE 1 ROW 2] 가입자의 6개월 이상 요양\tO\t5.5% ~ 3.3% 연금소득세\n"
        "부득이한 사유에 해당하면 중도 인출 시에도 전액 5.5% ~ 3.3% "
        "저율의 연금소득세를 적용 받는다.",
    )
    answer = render_grounded_extractive_fallback(
        question=question,
        evidence_context=context,
        assessment=_assessment("연금 세제 근거", "doc20:chunk=1"),
        route=classify_question(question),
    )
    assert answer is not None
    first_bullet = next(line for line in answer.splitlines() if line.startswith("- "))
    assert "부득이한 사유에 해당하면" in first_bullet


def test_trim_does_not_leave_the_punctuation_of_a_removed_sentence() -> None:
    """Removing a sentence leaves the punctuation that separated it behind."""

    answer = (
        "연금저축을 중도해지할 경우 세금은 다음과 같이 적용됩니다 [근거:doc42:chunk=1]. "
        "일반 사유로 해지하면 받았던 세액공제를 반환해야 합니다. "
        "이는 기타소득세로 16.5%가 부과되며 연금소득세보다 높은 세율입니다 "
        "[근거:doc41:chunk=1]. 부득이한 사유에 해당하면 낮은 세율의 연금소득세가 "
        "적용됩니다 [근거:doc20:chunk=1]."
    )
    result = _failed(claims=(
            UnsupportedClaim(
                "rule_citation_missing",
                "일반 사유로 해지하면 받았던 세액공제를 반환해야 합니다",
                "rule_claim_without_citation",
            ),
        ),
    )
    trimmed = drop_unsupported_lines(answer, result)
    assert trimmed is not None
    assert "세액공제를 반환해야" not in trimmed
    assert ". ." not in trimmed
    assert not any(line.lstrip().startswith(".") for line in trimmed.splitlines())


def test_trim_closes_the_gap_a_removed_numbered_item_leaves() -> None:
    """An answer opening at "2." reads as a missing step."""

    answer = (
        "연금저축 중도해지 시 세금은 사유에 따라 다릅니다 [근거:doc42:chunk=1].\n"
        "1. 일반 해지: 받았던 세액공제를 반환해야 합니다.\n"
        "2. 부득이한 사유: 낮은 세율의 연금소득세가 적용됩니다 [근거:doc20:chunk=1].\n"
        "본인의 상황을 확인하시기 바랍니다 [근거:doc20:chunk=1]."
    )
    result = _failed(claims=(
            UnsupportedClaim(
                "rule_citation_missing",
                "1. 일반 해지: 받았던 세액공제를 반환해야 합니다",
                "rule_claim_without_citation",
            ),
        ),
    )
    trimmed = drop_unsupported_lines(answer, result)
    assert trimmed is not None
    assert "세액공제를 반환해야" not in trimmed
    assert "1. 부득이한 사유" in trimmed


def test_repair_cites_a_typed_comparison_row_by_its_source() -> None:
    """A comparison carries no ``citation_id=`` header, so repair saw nothing.

    The model wrote the right source on a line of its own — "- 근거: 투자설명서
    /…pdf:page=3" — and the answer was discarded over where the marker sat
    rather than over what it said. One prospectus page backs several metrics,
    so every row sharing a source has to be registered, and the row keeps its
    value and its unit in separate columns.
    """

    context = "\n".join(
        (
            "[TYPED PRODUCT COMPARISON]",
            "product_code | class | metric | value | unit | as_of_date | "
            "period_years | aggregation | tax_basis | source",
            "KR510902511M | A | annual_total_expense_rate | 1.47 | "
            "percent_per_year | 2025-01-17 |  | stated_rate | not_applicable | "
            "투자설명서/KR510902511M/R2.pdf:page=3",
            "KR5152420028 | A | annual_total_fee_rate | 0.145 | "
            "percent_per_year | 2025-01-17 |  | stated_rate | not_applicable | "
            "투자설명서/KR5152420028/R2.pdf:page=6",
        )
    )
    answer = "- KR5152420028의 A클래스 총보수: 연 0.145%"
    result = _failed(claims=(
            UnsupportedClaim(
                "citation_missing",
                "- KR5152420028의 A클래스 총보수: 연 0.145%",
                "critical_sentence_without_citation",
            ),
        ),
    )
    repaired = attach_unambiguous_evidence_citations(answer, context, result)
    assert repaired is not None
    assert "투자설명서/KR5152420028/R2.pdf:page=6]" in repaired


def test_trim_keeps_an_answer_whose_citations_are_all_figure_sources() -> None:
    """A figure-only repair leaves 수치출처 markers, which are citations too."""

    answer = (
        "- KR510902511M의 A클래스 총보수: 연 1.47% "
        "[수치출처:투자설명서/KR510902511M/R2.pdf:page=3]\n"
        "- 이 값들의 차이는 1.325%p입니다\n"
        "- KR5152420028의 A클래스 총보수: 연 0.145% "
        "[수치출처:투자설명서/KR5152420028/R2.pdf:page=6]"
    )
    result = _failed(claims=(
            UnsupportedClaim("percentage", "- 이 값들의 차이는 1.325%p입니다", "1.325"),
        ),
    )
    trimmed = drop_unsupported_lines(answer, result)
    assert trimmed is not None
    assert "1.325" not in trimmed
    assert trimmed.count("수치출처") == 2


def test_fallback_does_not_quote_a_garbled_extraction() -> None:
    """Some organizer pages are scanned, and their text arrives broken.

    Quoting "- 0[) 계좌 개설 | 퇴직연금(개인형16) 계좌가 없다면 1-51" reads as
    a fault in the answer.
    """

    question = "IRP 계좌 개설은 어떻게 하나요"
    context = _context(
        "doc7",
        "- 0[) 계좌 개설 | 퇴직연금(개인형16) 계좌가 없다면 1-51\n"
        "IRP 계좌 개설은 회원가입 후 공동인증서를 발급받아 신청합니다.",
    )
    answer = render_grounded_extractive_fallback(
        question=question,
        evidence_context=context,
        assessment=_assessment("연금 절차 근거", "doc7:chunk=1"),
        route=classify_question(question),
    )
    assert answer is not None
    assert "0[)" not in answer
    assert "공동인증서를 발급받아 신청합니다" in answer
