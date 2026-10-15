from __future__ import annotations

import pytest

from app.answer_validation import (
    attach_unambiguous_evidence_citations,
    render_validation_failure,
    validate_answer,
)
from app.evidence import EvidenceAssessment
from app.product_identity import ProductResolution, ResolvedProduct
from app.routing import classify_question
from app.suitability import SuitabilityAssessment, SuitabilityProfile


def _assessment(status: str = "answerable") -> EvidenceAssessment:
    return EvidenceAssessment(
        version="test",
        status=status,
        coverage=(),
        missing_conditions=(),
    )


def _resolution(
    *products: tuple[str, str],
) -> ProductResolution:
    return ProductResolution(
        version="test",
        resolved=tuple(
            ResolvedProduct(
                product_code=code,
                canonical_source_id=code,
                aliases=(code,),
                official_name=name,
                matched_by="code",
            )
            for code, name in products
        ),
        unknown_codes=(),
        ambiguous=(),
    )


def _suitability(status: str = "candidate_shortlist") -> SuitabilityAssessment:
    return SuitabilityAssessment(
        version="test",
        status=status,
        profile=SuitabilityProfile(
            account="irp",
            horizon="long",
            horizon_years=20,
            risk_tolerance="aggressive",
            liquidity="not_needed",
            objective="growth",
            missing_fields=(),
            conflicts=(),
        ),
        required_conditions=(),
        candidates=(),
        evaluated_products=100,
        eligible_products=3,
        policy_limits=(),
    )


def _typed_fee_context() -> str:
    return "\n".join(
        (
            "[TYPED PRODUCT COMPARISON]",
            "product_code | class | metric | value | unit | as_of_date | "
            "period_years | aggregation | tax_basis | source",
            "KR510902511M | A | annual_total_fee_rate | 1.47 | percent_per_year | "
            "2025-01-17 |  | stated_rate | not_applicable | a.pdf:page=3",
            "KR510902511M | A | annual_total_expense_rate | 1.49 | "
            "percent_per_year | 2025-01-17 |  | stated_rate | not_applicable | "
            "a.pdf:page=3",
            "KR5152420028 | A | annual_total_fee_rate | 0.145 | percent_per_year | "
            "2025-01-17 |  | stated_rate | not_applicable | b.pdf:page=6",
        )
    )


def _typed_performance_balance_context() -> str:
    return "\n".join(
        (
            "[TYPED PRODUCT COMPARISON]",
            "product_code | class | metric | value | unit | as_of_date | "
            "period_years | aggregation | tax_basis | source",
            "KR510902511M | A | annualized_return_1y | 4.10 | percent | "
            "2024-12-31 | 1 | annualized | pre_tax | a.pdf:page=8",
            "KR510902511M | A | period_end_balance_units | 130 | "
            "million_units | 2024-12-22 |  | period_end | not_applicable | "
            "a.pdf:page=9",
            "KR510902511M | A | period_end_balance_amount | 130 | "
            "million_won | 2024-12-22 |  | period_end | not_applicable | "
            "a.pdf:page=9",
        )
    )


def _typed_class_context() -> str:
    return "\n".join(
        (
            "[TYPED PRODUCT COMPARISON]",
            "product_code | class | metric | value | unit | as_of_date | "
            "period_years | aggregation | tax_basis | source",
            "KR510902511M | A | annual_total_fee_rate | 1.47 | percent_per_year | "
            "2025-01-17 |  | stated_rate | not_applicable | same.pdf:page=3",
            "KR510902511M | C-P | annual_total_fee_rate | 0.90 | "
            "percent_per_year | 2025-01-17 |  | stated_rate | not_applicable | "
            "same.pdf:page=3",
        )
    )


def _typed_request_scope_context() -> str:
    return "\n".join(
        (
            "[TYPED PRODUCT COMPARISON]",
            "product_code | class | metric | value | unit | as_of_date | "
            "period_years | aggregation | tax_basis | source",
            "KR510902511M | C-P | annual_total_fee_rate | 1.47 | "
            "percent_per_year | 2025-01-17 |  | stated_rate | not_applicable | "
            "fee.pdf:page=3",
            "KR510902511M | C-P | annual_total_expense_rate | 1.49 | "
            "percent_per_year | 2025-01-17 |  | stated_rate | not_applicable | "
            "fee.pdf:page=3",
            "KR510902511M | C-P | annualized_return_1y | 4.10 | percent | "
            "2024-12-31 | 1 | annualized | pre_tax | perf.pdf:page=8",
            "KR510902511M | C-P | annualized_return_3y | 2.30 | percent | "
            "2024-12-31 | 3 | annualized | pre_tax | perf.pdf:page=8",
            "KR510902511M | C-P | annualized_return_1y | 5.20 | percent | "
            "2025-01-31 | 1 | annualized | pre_tax | perf2.pdf:page=8",
        )
    )


def _rule_context() -> str:
    return (
        "[EVIDENCE] source_id=doc20 citation_id=doc20:page=2:chunk=1\n"
        "IRP 중도인출은 법에서 정한 사유에 해당하는 경우에 한해 가능하며, "
        "해당 사유와 필요한 서류를 확인해야 합니다.\n\n"
        "[EVIDENCE] source_id=doc41 citation_id=doc41:page=1:chunk=1\n"
        "연금저축 세액공제는 납입액과 소득 요건에 따라 적용됩니다.\n\n"
        "[EVIDENCE] source_id=doc18 citation_id=doc18:page=3:chunk=1\n"
        "DC형 퇴직연금 가입자는 제도에서 허용하는 운용방법을 선택할 수 있습니다."
    )


def test_supported_percentage_date_amount_and_code_pass() -> None:
    result = validate_answer(
        answer="KR510902511M의 2025년 1월 17일 총보수는 1.47%이며 납입액은 900만원입니다.",
        question="KR510902511M에 900만원 납입",
        evidence_context="as_of_date=2025-01-17 value=1.47 unit=percent_per_year",
        route=classify_question("상품 총보수"),
        assessment=_assessment(),
    )
    assert result.valid is True
    assert result.checked_claims == 4


def test_unsupported_quantitative_claim_is_blocked_without_echoing_value() -> None:
    result = validate_answer(
        answer="이 상품 수익률은 99.9%입니다.",
        question="상품 수익률은?",
        evidence_context="공식 수익률 4.1%",
        route=classify_question("상품 수익률"),
        assessment=_assessment(),
    )
    assert result.valid is False
    assert result.unsupported_claims[0].kind == "percentage"
    safe = render_validation_failure(result)
    assert "99.9" not in safe


def test_well_cited_but_irrelevant_answer_fails_question_coverage() -> None:
    question = (
        "IRP 가입 3년이고 만 55세면 연금을 개시할 수 있나요? "
        "퇴직금이 들어온 계좌도 같은 조건인가요?"
    )
    context = (
        "[EVIDENCE] source_id=doc37 citation_id=doc37:chunk=1\n"
        "만 55세 이상이고 가입기간 5년 이상이면 연금 개시가 가능합니다. "
        "퇴직금이 입금된 연금계좌는 가입기간 요건을 적용하지 않습니다. "
        "연금 수령액이 연간 1,500만원을 초과하면 과세될 수 있습니다."
    )
    result = validate_answer(
        answer=(
            "연금 수령액이 연간 1,500만원을 초과하면 과세될 수 있습니다 "
            "[근거:doc37:chunk=1]."
        ),
        question=question,
        evidence_context=context,
        route=classify_question(question),
        assessment=_assessment(),
    )
    assert result.valid is False
    assert set(result.question_coverage_failures) == {
        "pension_commencement",
        "retirement_money",
        "age_condition",
        "enrollment_period_condition",
    }


def test_question_coverage_allows_evidence_to_correct_false_numeric_premise() -> None:
    question = "가입 3년이고 만 55세면 연금을 개시할 수 있나요?"
    context = (
        "[EVIDENCE] source_id=doc37 citation_id=doc37:chunk=1\n"
        "만 55세 이상이고 가입기간 5년 이상이면 연금 개시가 가능합니다."
    )
    result = validate_answer(
        answer=(
            "만 55세 이상이고 가입기간 5년 이상이면 연금 개시가 가능합니다 "
            "[근거:doc37:chunk=1]."
        ),
        question=question,
        evidence_context=context,
        route=classify_question(question),
        assessment=_assessment(),
    )
    assert result.valid is True
    assert result.question_coverage_failures == ()


@pytest.mark.parametrize(
    ("question", "answer", "missing_slot"),
    (
        (
            "연금상품의 총보수를 비교해 주세요.",
            "두 상품의 수익률을 같은 기준일로 비교했습니다.",
            "product_fee",
        ),
        (
            "이 상품의 위험등급을 알려 주세요.",
            "이 상품의 총보수는 문서에 기재되어 있습니다.",
            "risk_grade",
        ),
        (
            "IRP를 다른 증권사로 이전할 수 있나요?",
            "IRP 중도인출은 법정 사유에 한해 가능합니다.",
            "account_transfer",
        ),
        (
            "IRP 중도인출은 어떤 사유일 때 가능한가요?",
            "IRP 실물이전은 이전 가능 여부를 먼저 조회합니다.",
            "withdrawal",
        ),
    ),
)
def test_well_formed_but_off_topic_answers_are_rejected_across_domains(
    question: str,
    answer: str,
    missing_slot: str,
) -> None:
    context = (
        "[EVIDENCE] source_id=doc citation_id=doc:chunk=1\n"
        "연금상품의 총보수와 수익률, 위험등급을 비교할 수 있습니다. "
        "IRP 중도인출은 법정 사유에 한해 가능하며 실물이전은 이전 가능 여부를 조회합니다."
    )
    result = validate_answer(
        answer=f"{answer} [근거:doc:chunk=1].",
        question=question,
        evidence_context=context,
        route=classify_question(question),
        assessment=_assessment(),
    )

    assert result.valid is False
    assert missing_slot in result.question_coverage_failures


def test_unknown_product_code_and_prompt_leakage_are_blocked() -> None:
    result = validate_answer(
        answer="KR9999999999이며 Authorization: Bearer secret-token 입니다.",
        question="상품은?",
        evidence_context="KR510902511M",
        route=classify_question("상품은?"),
        assessment=_assessment(),
    )
    assert result.valid is False
    assert {item.kind for item in result.unsupported_claims} == {"product_code"}
    assert result.sensitive_spans


def test_partial_evidence_blocks_unconditional_recommendation() -> None:
    result = validate_answer(
        answer="이 상품을 가장 좋습니다. 가입하세요.",
        question="상품을 비교해 주세요",
        evidence_context="일부 근거",
        route=classify_question("상품을 비교해 주세요"),
        assessment=_assessment("partially_answerable"),
    )
    assert result.valid is False
    assert result.recommendation_blocked is True


def test_complete_prefilter_allows_conditional_recommendation_but_not_universal_rank() -> None:
    question = (
        "IRP 계좌에서 20년 투자하고 공격적으로 손실을 감내할 수 있으며 "
        "중도인출 계획은 없고 노후자금 성장 목표의 상품을 추천해 주세요."
    )
    route = classify_question(question)
    conditional = validate_answer(
        answer="제시된 조건 범위에서 이 후보를 추천합니다.",
        question=question,
        evidence_context="조건부 후보 근거",
        route=route,
        assessment=_assessment(),
        suitability=_suitability(),
    )
    assert conditional.valid is True

    universal = validate_answer(
        answer="이 후보가 전체 상품 중 최고의 상품이므로 추천합니다.",
        question=question,
        evidence_context="조건부 후보 근거",
        route=route,
        assessment=_assessment(),
        suitability=_suitability(),
    )
    assert universal.valid is False
    assert universal.recommendation_blocked is True


def test_recommendation_cannot_use_past_return_or_unaligned_cost_as_reason() -> None:
    question = (
        "IRP 계좌에서 20년 투자하고 공격적으로 손실을 감내할 수 있으며 "
        "중도인출 계획은 없고 노후자금 성장 목표의 상품을 추천해 주세요."
    )
    route = classify_question(question)
    for answer in (
        "과거 수익률이 높아서 이 후보를 추천합니다.",
        "보수가 저렴해서 이 후보를 추천합니다.",
    ):
        result = validate_answer(
            answer=answer,
            question=question,
            evidence_context="조건부 후보 근거",
            route=route,
            assessment=_assessment(),
            suitability=_suitability(),
        )
        assert result.valid is False
        assert result.recommendation_blocked is True


def test_typed_comparison_binds_value_and_metric_to_each_product_code() -> None:
    context = _typed_fee_context()
    route = classify_question("두 상품 총보수 비교")
    supported = validate_answer(
        answer=(
            "KR510902511M 총보수는 1.47%입니다 [근거:a.pdf:page=3]. "
            "KR5152420028 총보수는 0.145%입니다 [근거:b.pdf:page=6]."
        ),
        question="KR510902511M과 KR5152420028 총보수 비교",
        evidence_context=context,
        route=route,
        assessment=_assessment(),
    )
    assert supported.valid is True

    swapped = validate_answer(
        answer=(
            "KR510902511M 총보수는 0.145%이고 "
            "KR5152420028 총보수는 1.47%입니다."
        ),
        question="KR510902511M과 KR5152420028 총보수 비교",
        evidence_context=context,
        route=route,
        assessment=_assessment(),
    )
    assert swapped.valid is False
    assert {claim.kind for claim in swapped.unsupported_claims} == {
        "product_value_relation"
    }


def test_critical_claim_requires_an_allowed_citation() -> None:
    missing = validate_answer(
        answer="KR510902511M 총보수는 1.47%입니다.",
        question="KR510902511M 총보수",
        evidence_context=_typed_fee_context(),
        route=classify_question("상품 총보수"),
        assessment=_assessment(),
    )
    assert missing.valid is False
    assert {claim.kind for claim in missing.unsupported_claims} == {
        "citation_missing"
    }

    fabricated = validate_answer(
        answer="세액공제율은 16.5%입니다 [근거:doc99:page=1:chunk=1].",
        question="세액공제율은?",
        evidence_context=(
            "[DOC 1] source_id=doc41 citation_id=doc41:page=1:chunk=1\n"
            "세액공제율은 16.5%"
        ),
        route=classify_question("연금저축 세액공제율"),
        assessment=_assessment(),
    )
    assert fabricated.valid is False
    assert {claim.kind for claim in fabricated.unsupported_claims} == {
        "citation_not_in_context"
    }


def test_unique_exact_value_excerpt_can_repair_missing_citation() -> None:
    context = (
        "[DOC 1] source_id=doc41 citation_id=doc41:page=1:chunk=1\n"
        "연금저축 세액공제율은 16.5%입니다.\n"
        "[DOC 2] source_id=doc20 citation_id=doc20:page=2:chunk=1\n"
        "IRP 중도인출은 법정 사유에 한해 가능합니다."
    )
    answer = "연금저축 세액공제율은 16.5%입니다."
    route = classify_question("연금저축 세액공제율")
    initial = validate_answer(
        answer=answer,
        question="연금저축 세액공제율",
        evidence_context=context,
        route=route,
        assessment=_assessment(),
    )
    repaired = attach_unambiguous_evidence_citations(answer, context, initial)
    assert repaired == (
        "연금저축 세액공제율은 16.5%입니다 [근거:doc41:page=1:chunk=1]."
    )
    assert validate_answer(
        answer=repaired,
        question="연금저축 세액공제율",
        evidence_context=context,
        route=route,
        assessment=_assessment(),
    ).valid is True


def test_citation_repair_refuses_ambiguous_exact_value_excerpts() -> None:
    context = (
        "[DOC 1] source_id=doc41 citation_id=doc41:page=1:chunk=1\n"
        "연금저축 세액공제율은 16.5%입니다.\n"
        "[DOC 2] source_id=doc42 citation_id=doc42:page=1:chunk=1\n"
        "다른 세제 구간에도 16.5%가 기재되어 있습니다."
    )
    answer = "연금저축 세액공제율은 16.5%입니다."
    route = classify_question("연금저축 세액공제율")
    initial = validate_answer(
        answer=answer,
        question="연금저축 세액공제율",
        evidence_context=context,
        route=route,
        assessment=_assessment(),
    )
    assert attach_unambiguous_evidence_citations(answer, context, initial) is None


def test_citation_repair_does_not_use_number_from_question_only() -> None:
    context = (
        "[DOC 1] source_id=doc41 citation_id=doc41:page=1:chunk=1\n"
        "연금저축 세액공제의 적용 기준을 설명합니다."
    )
    answer = "질문에 언급된 세액공제율은 16.5%입니다."
    route = classify_question("세액공제율 16.5%가 맞나요")
    initial = validate_answer(
        answer=answer,
        question="세액공제율 16.5%가 맞나요",
        evidence_context=context,
        route=route,
        assessment=_assessment(),
    )
    assert attach_unambiguous_evidence_citations(answer, context, initial) is None


def test_question_number_cannot_be_supported_by_unrelated_cited_excerpt() -> None:
    question = "연금저축 세액공제율 99.9%가 맞나요?"
    context = (
        "[DOC 1] source_id=doc41 citation_id=doc41:page=1:chunk=1\n"
        "연금저축 세액공제는 납입액과 소득 요건에 따라 적용됩니다."
    )
    result = validate_answer(
        answer=(
            "연금저축 세액공제율은 99.9%입니다 "
            "[근거:doc41:page=1:chunk=1]."
        ),
        question=question,
        evidence_context=context,
        route=classify_question(question),
        assessment=_assessment(),
    )

    assert result.valid is False
    assert "numeric_citation_relation" in {
        claim.kind for claim in result.unsupported_claims
    }


def test_question_number_cannot_hide_behind_typed_only_citation() -> None:
    question = "이 상품 총보수가 99.9% 맞나요?"
    result = validate_answer(
        answer="이 상품 총보수는 99.9%입니다 [근거:a.pdf:page=3].",
        question=question,
        evidence_context=_typed_fee_context(),
        route=classify_question(question),
        assessment=_assessment(),
    )

    assert result.valid is False
    assert "product_value_relation" in {
        claim.kind for claim in result.unsupported_claims
    }


def test_pronoun_product_claim_still_binds_metric_to_typed_value() -> None:
    question = "KR510902511M의 총보수가 1.49% 맞나요?"
    result = validate_answer(
        answer="이 상품의 총보수는 1.49%입니다 [근거:a.pdf:page=3].",
        question=question,
        evidence_context=_typed_fee_context(),
        route=classify_question(question),
        assessment=_assessment(),
    )

    assert result.valid is False
    assert "product_value_relation" in {
        claim.kind for claim in result.unsupported_claims
    }


def test_pronoun_product_claim_accepts_matching_typed_metric_and_value() -> None:
    question = "KR510902511M의 총보수를 알려주세요."
    result = validate_answer(
        answer="이 상품의 총보수는 1.47%입니다 [근거:a.pdf:page=3].",
        question=question,
        evidence_context=_typed_fee_context(),
        route=classify_question(question),
        assessment=_assessment(),
    )

    assert result.valid is True


def test_single_product_question_rejects_other_products_valid_row() -> None:
    question = "KR510902511M의 총보수를 알려주세요."
    result = validate_answer(
        answer=(
            "KR5152420028의 총보수는 0.145%입니다 "
            "[근거:b.pdf:page=6]."
        ),
        question=question,
        evidence_context=_typed_fee_context(),
        route=classify_question(question),
        assessment=_assessment(),
    )

    assert result.valid is False
    assert "product_identity_relation" in {
        claim.kind for claim in result.unsupported_claims
    }


def test_pronoun_claim_is_limited_to_product_asked_in_question() -> None:
    question = "KR510902511M의 총보수를 알려주세요."
    result = validate_answer(
        answer="이 상품의 총보수는 0.145%입니다 [근거:b.pdf:page=6].",
        question=question,
        evidence_context=_typed_fee_context(),
        route=classify_question(question),
        assessment=_assessment(),
    )

    assert result.valid is False
    assert "product_value_relation" in {
        claim.kind for claim in result.unsupported_claims
    }


def test_typed_claim_rejects_other_products_source_even_when_value_is_correct() -> None:
    result = validate_answer(
        answer="KR510902511M 총보수는 1.47%입니다 [근거:b.pdf:page=6].",
        question="KR510902511M 총보수",
        evidence_context=_typed_fee_context(),
        route=classify_question("상품 총보수"),
        assessment=_assessment(),
    )
    assert result.valid is False
    assert {claim.kind for claim in result.unsupported_claims} == {
        "product_citation_relation"
    }


@pytest.mark.parametrize("separator", (",", ";"))
def test_typed_comparison_allows_shared_multi_source_marker(separator: str) -> None:
    question = "KR510902511M과 KR5152420028의 총보수를 비교해 주세요."
    result = validate_answer(
        answer=(
            "KR510902511M 총보수는 1.47%이고 KR5152420028 총보수는 "
            f"0.145%입니다 [근거:a.pdf:page=3{separator} b.pdf:page=6]."
        ),
        question=question,
        evidence_context=_typed_fee_context(),
        route=classify_question(question),
        assessment=_assessment(),
    )
    assert result.valid is True
    assert result.product_value_relations_checked == 2


def test_typed_shared_marker_rejects_same_value_from_other_product() -> None:
    context = "\n".join(
        (
            "[TYPED PRODUCT COMPARISON]",
            "product_code | class | metric | value | unit | as_of_date | "
            "period_years | aggregation | tax_basis | source",
            "KR510902511M | A | annual_total_fee_rate | 1.47 | percent_per_year | "
            "2025-01-17 |  | stated_rate | not_applicable | a.pdf:page=3",
            "KR5152420028 | A | annual_total_fee_rate | 1.47 | percent_per_year | "
            "2025-01-17 |  | stated_rate | not_applicable | b.pdf:page=6",
        )
    )
    question = "KR510902511M과 KR5152420028의 총보수를 비교해 주세요."
    result = validate_answer(
        answer=(
            "KR510902511M 총보수는 1.47%이고 KR5152420028 총보수는 "
            "1.47%입니다 [근거:b.pdf:page=6]."
        ),
        question=question,
        evidence_context=context,
        route=classify_question(question),
        assessment=_assessment(),
    )
    assert result.valid is False
    assert "product_citation_relation" in {
        claim.kind for claim in result.unsupported_claims
    }


@pytest.mark.parametrize(
    "answer",
    (
        "KR510902511M C-P 클래스 총보수는 1.47%입니다 "
        "[근거:same.pdf:page=3].",
        "이 상품의 C-P 클래스 총보수는 1.47%입니다 [근거:same.pdf:page=3].",
        "KR510902511M A 클래스 총보수는 1.47%입니다 "
        "[근거:same.pdf:page=3].",
        "KR510902511M 총보수는 1.47%입니다 [근거:same.pdf:page=3].",
    ),
)
def test_typed_claim_rejects_value_from_different_sales_class(answer: str) -> None:
    question = "KR510902511M의 C-P 클래스 총보수를 알려주세요."
    result = validate_answer(
        answer=answer,
        question=question,
        evidence_context=_typed_class_context(),
        route=classify_question(question),
        assessment=_assessment(),
    )
    assert result.valid is False
    assert "product_qualifier_relation" in {
        claim.kind for claim in result.unsupported_claims
    }


@pytest.mark.parametrize(
    "answer",
    (
        "KR510902511M C-P 클래스 총보수는 0.90%입니다 "
        "[근거:same.pdf:page=3].",
        "KR510902511M 총보수는 0.90%입니다 [근거:same.pdf:page=3].",
    ),
)
def test_typed_claim_accepts_question_sales_class_value(answer: str) -> None:
    question = "KR510902511M의 C-P 클래스 총보수를 알려주세요."
    result = validate_answer(
        answer=answer,
        question=question,
        evidence_context=_typed_class_context(),
        route=classify_question(question),
        assessment=_assessment(),
    )
    assert result.valid is True


@pytest.mark.parametrize(
    ("question", "answer"),
    (
        (
            "KR510902511M C-P 클래스 총보수는 얼마인가요?",
            "KR510902511M C-P 클래스 총보수비용은 1.49%입니다 "
            "[근거:fee.pdf:page=3].",
        ),
        (
            "KR510902511M C-P 클래스의 2024년 12월 31일 기준 1년 수익률은?",
            "KR510902511M C-P 클래스의 2024년 12월 31일 기준 3년 연환산 "
            "세전 수익률은 2.30%입니다 [근거:perf.pdf:page=8].",
        ),
        (
            "KR510902511M C-P 클래스의 2024년 12월 31일 기준 1년 수익률은?",
            "KR510902511M C-P 클래스의 2025년 1월 31일 기준 1년 연환산 "
            "세전 수익률은 5.20%입니다 [근거:perf2.pdf:page=8].",
        ),
    ),
)
def test_typed_claim_rejects_different_product_question_axis(
    question: str,
    answer: str,
) -> None:
    result = validate_answer(
        answer=answer,
        question=question,
        evidence_context=_typed_request_scope_context(),
        route=classify_question(question),
        assessment=_assessment(),
    )
    assert result.valid is False
    assert "product_question_relation" in {
        claim.kind for claim in result.unsupported_claims
    }


@pytest.mark.parametrize(
    "answer",
    (
        "KR510902511M C-P 클래스의 2024년 12월 31일 기준 1년 연환산 "
        "세전 수익률은 4.10%입니다 [근거:perf.pdf:page=8].",
        "KR510902511M C-P 클래스의 2024년 12월 31일 기준 연환산 "
        "세전 수익률은 4.10%입니다 [근거:perf.pdf:page=8].",
    ),
)
def test_typed_claim_accepts_matching_or_inherited_question_axis(answer: str) -> None:
    question = (
        "KR510902511M C-P 클래스의 2024년 12월 31일 기준 1년 수익률은?"
    )
    result = validate_answer(
        answer=answer,
        question=question,
        evidence_context=_typed_request_scope_context(),
        route=classify_question(question),
        assessment=_assessment(),
    )
    assert result.valid is True


def test_product_question_axis_is_satisfied_per_product() -> None:
    context = "\n".join(
        (
            "[TYPED PRODUCT COMPARISON]",
            "product_code | class | metric | value | unit | as_of_date | "
            "period_years | aggregation | tax_basis | source",
            "KR510902511M | C-P | annual_total_fee_rate | 1.47 | "
            "percent_per_year | 2025-01-17 |  | stated_rate | not_applicable | "
            "a.pdf:page=3",
            "KR5152420028 | C-P | annual_total_fee_rate | 0.145 | "
            "percent_per_year | 2025-01-17 |  | stated_rate | not_applicable | "
            "b.pdf:page=6",
            "KR5152420028 | C-P | annual_total_expense_rate | 0.155 | "
            "percent_per_year | 2025-01-17 |  | stated_rate | not_applicable | "
            "b.pdf:page=6",
        )
    )
    question = (
        "KR510902511M과 KR5152420028의 C-P 클래스 총보수를 비교해 주세요."
    )
    result = validate_answer(
        answer=(
            "KR510902511M의 C-P 클래스 총보수는 1.47%입니다 "
            "[근거:a.pdf:page=3]. KR5152420028의 C-P 클래스 총보수비용은 "
            "0.155%입니다 [근거:b.pdf:page=6]."
        ),
        question=question,
        evidence_context=context,
        route=classify_question(question),
        assessment=_assessment(),
    )
    assert result.valid is False
    assert "product_question_relation" in {
        claim.kind for claim in result.unsupported_claims
    }


def test_typed_comparison_distinguishes_total_fee_from_total_expense() -> None:
    context = "\n".join(
        (
            "[TYPED PRODUCT COMPARISON]",
            "product_code | class | metric | value | unit | as_of_date | "
            "period_years | aggregation | tax_basis | source",
            "KR510902511M | A | annual_total_fee_rate | 1.47 | percent_per_year | "
            "2025-01-17 |  | stated_rate | not_applicable | a.pdf:page=3",
            "KR510902511M | A | annual_total_expense_rate | 1.49 | "
            "percent_per_year | 2025-01-17 |  | stated_rate | not_applicable | "
            "a.pdf:page=3",
        )
    )
    result = validate_answer(
        answer="KR510902511M 총보수는 1.49%입니다.",
        question="KR510902511M 총보수",
        evidence_context=context,
        route=classify_question("상품 총보수"),
        assessment=_assessment(),
    )
    assert result.valid is False
    assert result.unsupported_claims[-1].kind == "product_value_relation"


def test_performance_value_requires_period_aggregation_tax_and_date() -> None:
    context = _typed_performance_balance_context()
    supported = validate_answer(
        answer=(
            "KR510902511M의 2024년 12월 31일 기준 1년 연환산 세전 "
            "수익률은 4.10%입니다 [근거:a.pdf:page=8]."
        ),
        question="KR510902511M의 A 클래스 1년 수익률",
        evidence_context=context,
        route=classify_question("상품 수익률"),
        assessment=_assessment(),
    )
    assert supported.valid is True

    wrong_basis = validate_answer(
        answer=(
            "KR510902511M의 2024년 12월 31일 기준 1년 누적 세후 "
            "수익률은 4.10%입니다."
        ),
        question="KR510902511M의 A 클래스 1년 수익률",
        evidence_context=context,
        route=classify_question("상품 수익률"),
        assessment=_assessment(),
    )
    assert wrong_basis.valid is False
    assert wrong_basis.unsupported_claims[-1].kind == "product_qualifier_relation"

    missing_basis = validate_answer(
        answer="KR510902511M의 2024년 12월 31일 기준 1년 수익률은 4.10%입니다.",
        question="KR510902511M의 A 클래스 1년 수익률",
        evidence_context=context,
        route=classify_question("상품 수익률"),
        assessment=_assessment(),
    )
    assert missing_basis.valid is False
    assert missing_basis.unsupported_claims[-1].kind == "product_qualifier_missing"


def test_balance_value_requires_metric_unit_date_and_historical_scope() -> None:
    context = _typed_performance_balance_context()
    supported = validate_answer(
        answer=(
            "KR510902511M의 2024년 12월 22일 기준 기간말 잔고 금액은 "
            "130백만원입니다 [근거:a.pdf:page=9]."
        ),
        question="KR510902511M의 A 클래스 기간말 잔고",
        evidence_context=context,
        route=classify_question("상품 기간말 잔고"),
        assessment=_assessment(),
    )
    assert supported.valid is True

    wrong_unit = validate_answer(
        answer=(
            "KR510902511M의 2024년 12월 22일 기준 기간말 잔고 좌수는 "
            "130백만원입니다."
        ),
        question="KR510902511M의 A 클래스 기간말 잔고",
        evidence_context=context,
        route=classify_question("상품 기간말 잔고"),
        assessment=_assessment(),
    )
    assert wrong_unit.valid is False
    assert wrong_unit.unsupported_claims[-1].kind == "product_qualifier_relation"

    current_aum = validate_answer(
        answer=(
            "KR510902511M의 2024년 12월 22일 기준 현재 기간말 잔고 금액은 "
            "130백만원입니다."
        ),
        question="KR510902511M의 A 클래스 기간말 잔고",
        evidence_context=context,
        route=classify_question("상품 기간말 잔고"),
        assessment=_assessment(),
    )
    assert current_aum.valid is False
    assert current_aum.unsupported_claims[-1].kind == "historical_balance_as_current"


def test_typed_comparison_binds_short_official_names_to_values() -> None:
    resolution = _resolution(
        (
            "KR510902511M",
            "미래에셋장기성장포커스증권자투자신탁1호(주식)",
        ),
        (
            "KR5152420028",
            "미래에셋프리미엄크레딧알파증권자투자신탁(채권)",
        ),
    )
    supported = validate_answer(
        answer=(
            "장기성장포커스 총보수는 1.47%입니다 [근거:a.pdf:page=3]. "
            "프리미엄크레딧알파 총보수는 0.145%입니다 [근거:b.pdf:page=6]."
        ),
        question="두 상품 총보수 비교",
        evidence_context=_typed_fee_context(),
        route=classify_question("두 상품 총보수 비교"),
        assessment=_assessment(),
        product_resolution=resolution,
    )
    assert supported.valid is True

    swapped = validate_answer(
        answer=(
            "장기성장포커스 총보수는 0.145%이고 "
            "프리미엄크레딧알파 총보수는 1.47%입니다."
        ),
        question="두 상품 총보수 비교",
        evidence_context=_typed_fee_context(),
        route=classify_question("두 상품 총보수 비교"),
        assessment=_assessment(),
        product_resolution=resolution,
    )
    assert swapped.valid is False
    assert {claim.kind for claim in swapped.unsupported_claims} == {
        "product_value_relation"
    }


def test_typed_comparison_binds_metric_for_official_name() -> None:
    resolution = _resolution(
        (
            "KR510902511M",
            "미래에셋장기성장포커스증권자투자신탁1호(주식)",
        ),
    )
    result = validate_answer(
        answer="장기성장포커스 총보수는 1.49%입니다.",
        question="장기성장포커스 총보수",
        evidence_context=_typed_fee_context(),
        route=classify_question("상품 총보수"),
        assessment=_assessment(),
        product_resolution=resolution,
    )
    assert result.valid is False
    assert result.unsupported_claims[-1].kind == "product_value_relation"


def test_same_official_name_for_two_codes_blocks_quantitative_relation() -> None:
    name = "한국투자 골드플랜 연금 증권 자투자신탁 1호(채권)"
    resolution = _resolution(
        ("KR510902511M", name),
        ("KR5152420028", name),
    )
    result = validate_answer(
        answer=f"{name} 총보수는 1.47%입니다.",
        question="두 코드 비교",
        evidence_context=_typed_fee_context(),
        route=classify_question("두 상품 총보수 비교"),
        assessment=_assessment(),
        product_resolution=resolution,
    )
    assert result.valid is False
    assert result.unsupported_claims[-1].kind == "ambiguous_product_name_relation"


def test_nonquantitative_rule_requires_same_sentence_citation() -> None:
    supported = validate_answer(
        answer=(
            "IRP 중도인출은 법정 사유에 해당하는 경우에만 가능합니다 "
            "[근거:doc20:page=2:chunk=1]."
        ),
        question="IRP 중도인출 조건을 설명해 주세요.",
        evidence_context=_rule_context(),
        route=classify_question("IRP 중도인출 조건을 설명해 주세요."),
        assessment=_assessment(),
    )
    assert supported.valid is True
    assert supported.rule_claims_checked == 1

    missing = validate_answer(
        answer="IRP 중도인출은 법정 사유에 해당하는 경우에만 가능합니다.",
        question="IRP 중도인출 조건을 설명해 주세요.",
        evidence_context=_rule_context(),
        route=classify_question("IRP 중도인출 조건을 설명해 주세요."),
        assessment=_assessment(),
    )
    assert missing.valid is False
    assert {claim.kind for claim in missing.unsupported_claims} == {
        "rule_citation_missing"
    }


def test_nonquantitative_rule_rejects_wrong_topic_and_scope_reversal() -> None:
    wrong_topic = validate_answer(
        answer=(
            "IRP 중도인출은 법정 사유에 해당하는 경우에만 가능합니다 "
            "[근거:doc41:page=1:chunk=1]."
        ),
        question="IRP 중도인출 조건을 설명해 주세요.",
        evidence_context=_rule_context(),
        route=classify_question("IRP 중도인출 조건을 설명해 주세요."),
        assessment=_assessment(),
    )
    assert wrong_topic.valid is False
    assert {claim.kind for claim in wrong_topic.unsupported_claims} == {
        "rule_citation_relation"
    }

    unrestricted = validate_answer(
        answer=(
            "IRP는 아무 사유 없이 언제든 중도인출할 수 있습니다 "
            "[근거:doc20:page=2:chunk=1]."
        ),
        question="IRP 중도인출 조건을 설명해 주세요.",
        evidence_context=_rule_context(),
        route=classify_question("IRP 중도인출 조건을 설명해 주세요."),
        assessment=_assessment(),
    )
    assert unrestricted.valid is False
    assert {claim.kind for claim in unrestricted.unsupported_claims} == {
        "rule_qualifier_contradiction"
    }


def test_nonquantitative_rule_rejects_permission_polarity_reversal() -> None:
    result = validate_answer(
        answer=(
            "DC형 퇴직연금 가입자는 운용방법을 선택할 수 없습니다 "
            "[근거:doc18:page=3:chunk=1]."
        ),
        question="DC형 퇴직연금 가입자의 운용방법 선택이 가능한가요?",
        evidence_context=_rule_context(),
        route=classify_question("DC형 퇴직연금 가입자의 운용방법 선택이 가능한가요?"),
        assessment=_assessment(),
    )
    assert result.valid is False
    assert {claim.kind for claim in result.unsupported_claims} == {
        "rule_polarity_contradiction"
    }


def test_rule_claim_units_validate_two_independent_citations() -> None:
    result = validate_answer(
        answer=(
            "IRP 중도인출은 법정 사유에만 가능합니다 "
            "[근거:doc20:page=2:chunk=1] 그리고 연금저축 세액공제는 "
            "소득 요건에 따라 적용됩니다 [근거:doc41:page=1:chunk=1]."
        ),
        question="IRP 중도인출과 연금저축 세액공제를 설명해 주세요.",
        evidence_context=_rule_context(),
        route=classify_question(
            "IRP 중도인출과 연금저축 세액공제를 설명해 주세요."
        ),
        assessment=_assessment(),
    )
    assert result.valid is True
    assert result.rule_claims_checked == 2


def test_rule_claim_units_allow_shared_multi_source_marker() -> None:
    result = validate_answer(
        answer=(
            "IRP 중도인출은 법정 사유에만 가능하고 연금저축 세액공제는 "
            "납입액과 소득 요건에 따라 적용됩니다 "
            "[근거:doc20:page=2:chunk=1; doc41:page=1:chunk=1]."
        ),
        question="IRP 중도인출과 연금저축 세액공제를 설명해 주세요.",
        evidence_context=_rule_context(),
        route=classify_question(
            "IRP 중도인출과 연금저축 세액공제를 설명해 주세요."
        ),
        assessment=_assessment(),
    )
    assert result.valid is True
    assert result.rule_claims_checked == 2


def test_rule_claim_units_shared_marker_needs_support_for_every_unit() -> None:
    result = validate_answer(
        answer=(
            "IRP 중도인출은 법정 사유에만 가능하고 연금저축 세액공제는 "
            "납입액과 소득 요건에 따라 적용됩니다 "
            "[근거:doc20:page=2:chunk=1; doc18:page=3:chunk=1]."
        ),
        question="IRP 중도인출과 연금저축 세액공제를 설명해 주세요.",
        evidence_context=_rule_context(),
        route=classify_question(
            "IRP 중도인출과 연금저축 세액공제를 설명해 주세요."
        ),
        assessment=_assessment(),
    )
    assert result.valid is False
    assert result.rule_claims_checked == 2
    assert {claim.kind for claim in result.unsupported_claims} == {
        "rule_citation_relation"
    }


def test_rule_claim_allows_supporting_and_unrelated_citation() -> None:
    result = validate_answer(
        answer=(
            "연금저축 세액공제는 납입액과 소득 요건에 따라 적용됩니다 "
            "[근거:doc41:page=1:chunk=1; doc20:page=2:chunk=1]."
        ),
        question="연금저축 세액공제 조건을 알려주세요.",
        evidence_context=_rule_context(),
        route=classify_question("연금저축 세액공제 조건을 알려주세요."),
        assessment=_assessment(),
    )
    assert result.valid is True
    assert result.rule_claims_checked == 1


def test_rule_claim_units_reject_unsupported_conjoined_requirement() -> None:
    result = validate_answer(
        answer=(
            "IRP 중도인출은 법정 사유에만 가능하고 별도 서류는 필요하지 "
            "않습니다 [근거:doc20:page=2:chunk=1]."
        ),
        question="IRP 중도인출 조건과 서류를 설명해 주세요.",
        evidence_context=_rule_context(),
        route=classify_question("IRP 중도인출 조건과 서류를 설명해 주세요."),
        assessment=_assessment(),
    )
    assert result.valid is False
    assert result.rule_claims_checked == 2
    assert {claim.kind for claim in result.unsupported_claims} == {
        "rule_polarity_contradiction"
    }


def test_rule_claim_units_reject_wrong_citation_for_second_claim() -> None:
    result = validate_answer(
        answer=(
            "IRP 중도인출은 법정 사유에만 가능합니다 "
            "[근거:doc20:page=2:chunk=1] 그리고 연금저축 세액공제는 "
            "소득 요건에 따라 적용됩니다 [근거:doc20:page=2:chunk=1]."
        ),
        question="IRP 중도인출과 연금저축 세액공제를 설명해 주세요.",
        evidence_context=_rule_context(),
        route=classify_question(
            "IRP 중도인출과 연금저축 세액공제를 설명해 주세요."
        ),
        assessment=_assessment(),
    )
    assert result.valid is False
    assert result.rule_claims_checked == 2
    assert {claim.kind for claim in result.unsupported_claims} == {
        "rule_citation_relation"
    }


def test_rule_claim_rejects_income_independent_tax_credit_scope() -> None:
    result = validate_answer(
        answer=(
            "연금저축 세액공제는 소득과 무관하게 적용됩니다 "
            "[근거:doc41:page=1:chunk=1]."
        ),
        question="연금저축 세액공제 조건을 설명해 주세요.",
        evidence_context=_rule_context(),
        route=classify_question("연금저축 세액공제 조건을 설명해 주세요."),
        assessment=_assessment(),
    )
    assert result.valid is False
    assert {claim.kind for claim in result.unsupported_claims} == {
        "rule_qualifier_contradiction"
    }


def test_quantitative_rule_rejects_scope_reversal_even_when_value_matches() -> None:
    question = "연금저축 세액공제율과 소득 요건을 알려주세요."
    context = (
        "[EVIDENCE] source_id=doc41 citation_id=doc41:page=1:chunk=1\n"
        "연금저축 세액공제율 16.5%는 소득 요건에 따라 적용됩니다."
    )
    result = validate_answer(
        answer=(
            "연금저축 세액공제율 16.5%는 소득과 무관하게 누구나 적용됩니다 "
            "[근거:doc41:page=1:chunk=1]."
        ),
        question=question,
        evidence_context=context,
        route=classify_question(question),
        assessment=_assessment(),
    )

    assert result.valid is False
    assert "rule_qualifier_contradiction" in {
        claim.kind for claim in result.unsupported_claims
    }


def test_quantitative_rule_accepts_matching_value_and_scope() -> None:
    question = "연금저축 세액공제율과 소득 요건을 알려주세요."
    context = (
        "[EVIDENCE] source_id=doc41 citation_id=doc41:page=1:chunk=1\n"
        "연금저축 세액공제율 16.5%는 소득 요건에 따라 적용됩니다."
    )
    result = validate_answer(
        answer=(
            "연금저축 세액공제율 16.5%는 소득 요건에 따라 적용됩니다 "
            "[근거:doc41:page=1:chunk=1]."
        ),
        question=question,
        evidence_context=context,
        route=classify_question(question),
        assessment=_assessment(),
    )

    assert result.valid is True


@pytest.mark.parametrize(
    "answer_clause",
    (
        "연금저축 세액공제율 16.5%는 적용되지 않습니다",
        "연금저축 세액공제율 16.5%는 적용되지 않아요",
    ),
)
def test_quantitative_rule_rejects_korean_state_negation(
    answer_clause: str,
) -> None:
    question = "연금저축 세액공제율 적용 여부를 알려주세요."
    context = (
        "[EVIDENCE] source_id=doc41 citation_id=doc41:page=1:chunk=1\n"
        "연금저축 세액공제율 16.5%는 소득 요건에 따라 적용됩니다."
    )
    result = validate_answer(
        answer=f"{answer_clause} [근거:doc41:page=1:chunk=1].",
        question=question,
        evidence_context=context,
        route=classify_question(question),
        assessment=_assessment(),
    )

    assert result.valid is False
    assert "rule_polarity_contradiction" in {
        claim.kind for claim in result.unsupported_claims
    }


@pytest.mark.parametrize(
    ("question", "evidence_clause", "answer_clause"),
    (
        (
            "연금저축 세액공제 적용 여부를 알려주세요.",
            "연금저축 세액공제는 적용 대상입니다.",
            "연금저축 세액공제는 적용 대상이 아닙니다",
        ),
        (
            "연금 수령액 과세 여부를 알려주세요.",
            "연금 수령액은 과세 대상입니다.",
            "연금 수령액은 과세 대상이 아닙니다",
        ),
        (
            "연금저축 납입액의 세액공제 가능 여부를 알려주세요.",
            "연금저축 납입액은 세액공제를 받을 수 있습니다.",
            "연금저축 납입액은 세액공제를 받을 수 없습니다",
        ),
        (
            "연금저축 납입액의 세액공제 인정 여부를 알려주세요.",
            "연금저축 납입액은 세액공제로 인정됩니다.",
            "연금저축 납입액은 세액공제 인정 대상에서 제외됩니다",
        ),
        (
            "IRP 중도인출이 허용되는지 알려주세요.",
            "IRP 중도인출은 법정 사유에서 허용됩니다.",
            "IRP 중도인출은 허용 대상이 아닙니다",
        ),
    ),
)
def test_rule_state_predicates_reject_negative_paraphrases(
    question: str,
    evidence_clause: str,
    answer_clause: str,
) -> None:
    context = (
        "[EVIDENCE] source_id=doc citation_id=doc:page=1:chunk=1\n"
        f"{evidence_clause}"
    )
    result = validate_answer(
        answer=f"{answer_clause} [근거:doc:page=1:chunk=1].",
        question=question,
        evidence_context=context,
        route=classify_question(question),
        assessment=_assessment(),
    )

    assert result.valid is False
    assert "rule_polarity_contradiction" in {
        claim.kind for claim in result.unsupported_claims
    }


@pytest.mark.parametrize(
    ("question", "evidence_clause", "answer_clause"),
    (
        (
            "연금저축 세액공제 적용 여부를 알려주세요.",
            "연금저축 세액공제는 적용 대상이 아닙니다.",
            "연금저축 세액공제는 적용 대상입니다",
        ),
        (
            "연금 수령액 과세 여부를 알려주세요.",
            "연금 수령액은 과세 대상이 아닙니다.",
            "연금 수령액은 과세 대상입니다",
        ),
        (
            "연금저축 납입액의 세액공제 가능 여부를 알려주세요.",
            "연금저축 납입액은 세액공제를 받을 수 없습니다.",
            "연금저축 납입액은 세액공제를 받을 수 있습니다",
        ),
        (
            "연금저축 납입액의 세액공제 인정 여부를 알려주세요.",
            "연금저축 납입액은 세액공제 인정 대상에서 제외됩니다.",
            "연금저축 납입액은 세액공제로 인정됩니다",
        ),
        (
            "IRP 중도인출이 허용되는지 알려주세요.",
            "IRP 중도인출은 허용 대상이 아닙니다.",
            "IRP 중도인출은 법정 사유에서 허용됩니다",
        ),
    ),
)
def test_rule_state_predicates_reject_positive_reversals(
    question: str,
    evidence_clause: str,
    answer_clause: str,
) -> None:
    context = (
        "[EVIDENCE] source_id=doc citation_id=doc:page=1:chunk=1\n"
        f"{evidence_clause}"
    )
    result = validate_answer(
        answer=f"{answer_clause} [근거:doc:page=1:chunk=1].",
        question=question,
        evidence_context=context,
        route=classify_question(question),
        assessment=_assessment(),
    )

    assert result.valid is False
    assert "rule_polarity_contradiction" in {
        claim.kind for claim in result.unsupported_claims
    }


@pytest.mark.parametrize(
    ("question", "evidence_clause", "answer_clause"),
    (
        (
            "연금저축 세액공제 적용 여부를 알려주세요.",
            "연금저축 세액공제는 적용 대상입니다.",
            "연금저축 세액공제는 적용됩니다",
        ),
        (
            "연금 수령액 과세 여부를 알려주세요.",
            "연금 수령액은 과세 대상입니다.",
            "연금 수령액은 과세됩니다",
        ),
        (
            "연금저축 납입액의 세액공제 가능 여부를 알려주세요.",
            "연금저축 납입액은 세액공제를 받을 수 있습니다.",
            "연금저축 납입액은 세액공제가 가능합니다",
        ),
        (
            "연금저축 납입액의 세액공제 인정 여부를 알려주세요.",
            "연금저축 납입액은 세액공제로 인정됩니다.",
            "연금저축 납입액은 세액공제 인정 대상입니다",
        ),
        (
            "IRP 중도인출이 허용되는지 알려주세요.",
            "IRP 중도인출은 법정 사유에서 허용됩니다.",
            "IRP 중도인출은 법정 사유에서 허용됩니다",
        ),
        (
            "연금저축 세액공제 적용 여부를 알려주세요.",
            "연금저축 세액공제는 적용 대상이 아닙니다.",
            "연금저축 세액공제는 적용되지 않습니다",
        ),
        (
            "연금 수령액 과세 여부를 알려주세요.",
            "연금 수령액은 과세 대상이 아닙니다.",
            "연금 수령액은 과세되지 않습니다",
        ),
        (
            "연금저축 납입액의 세액공제 가능 여부를 알려주세요.",
            "연금저축 납입액은 세액공제를 받을 수 없습니다.",
            "연금저축 납입액은 세액공제 대상이 아닙니다",
        ),
        (
            "연금저축 납입액의 세액공제 인정 여부를 알려주세요.",
            "연금저축 납입액은 세액공제 인정 대상에서 제외됩니다.",
            "연금저축 납입액은 세액공제로 인정되지 않습니다",
        ),
        (
            "IRP 중도인출이 허용되는지 알려주세요.",
            "IRP 중도인출은 허용 대상이 아닙니다.",
            "IRP 중도인출은 허용되지 않습니다",
        ),
    ),
)
def test_rule_state_predicates_accept_same_polarity_paraphrases(
    question: str,
    evidence_clause: str,
    answer_clause: str,
) -> None:
    context = (
        "[EVIDENCE] source_id=doc citation_id=doc:page=1:chunk=1\n"
        f"{evidence_clause}"
    )
    result = validate_answer(
        answer=f"{answer_clause} [근거:doc:page=1:chunk=1].",
        question=question,
        evidence_context=context,
        route=classify_question(question),
        assessment=_assessment(),
    )

    assert result.valid is True


def test_rule_claim_uses_main_action_polarity_not_nested_permission_word() -> None:
    result = validate_answer(
        answer=(
            "DC형 가입자는 허용된 운용방법을 직접 고를 수 없습니다 "
            "[근거:doc18:page=3:chunk=1]."
        ),
        question="DC형 퇴직연금 운용방법 선택이 가능한가요?",
        evidence_context=_rule_context(),
        route=classify_question("DC형 퇴직연금 운용방법 선택이 가능한가요?"),
        assessment=_assessment(),
    )
    assert result.valid is False
    assert {claim.kind for claim in result.unsupported_claims} == {
        "rule_polarity_contradiction"
    }


def test_value_source_mark_does_not_satisfy_a_rule_claim() -> None:
    """The narrow mark must not stand in for full support of a rule.

    `[수치출처:…]` records only that a figure was located inside the cited
    excerpt. A rule sentence asserts something the excerpt has to actually
    say, so accepting the narrow mark there would let a number match launder
    an unchecked assertion into what reads like a citation.
    """

    result = validate_answer(
        answer=(
            "IRP 중도인출은 사유와 무관하게 언제든 가능합니다 "
            "[수치출처:doc20:page=2:chunk=1]."
        ),
        question="IRP 중도인출 조건을 설명해 주세요.",
        evidence_context=_rule_context(),
        route=classify_question("IRP 중도인출 조건을 설명해 주세요."),
        assessment=_assessment(),
    )
    assert result.valid is False
    assert "rule_citation_missing" in {
        claim.kind for claim in result.unsupported_claims
    }


def test_value_source_mark_satisfies_a_figure_only_sentence() -> None:
    """A located figure is exactly what the narrow mark is for."""

    result = validate_answer(
        answer="문서의 표시값은 16.5%입니다 [수치출처:doc41:page=1:chunk=1].",
        question="문서에 표시된 수치를 알려주세요.",
        evidence_context=(
            "[EVIDENCE] source_id=doc41 citation_id=doc41:page=1:chunk=1\n"
            "문서의 표시값은 16.5%입니다."
        ),
        route=classify_question("문서에 표시된 수치를 알려주세요."),
        assessment=_assessment(),
    )
    assert result.valid is True


def test_value_source_mark_must_name_a_real_context_citation() -> None:
    """The narrow mark is still a citation and cannot invent a source."""

    result = validate_answer(
        answer="연금저축 세액공제율은 16.5%입니다 [수치출처:doc99:page=1:chunk=1].",
        question="연금저축 세액공제율",
        evidence_context=(
            "[EVIDENCE] source_id=doc41 citation_id=doc41:page=1:chunk=1\n"
            "연금저축 세액공제율은 문서상 16.5%입니다."
        ),
        route=classify_question("연금저축 세액공제율"),
        assessment=_assessment(),
    )
    assert result.valid is False
    assert "citation_not_in_context" in {
        claim.kind for claim in result.unsupported_claims
    }


def _tax_context() -> str:
    return (
        "[EVIDENCE] source_id=doc41 citation_id=doc41:chunk=1\n"
        "연금저축 세액공제 대상 납입한도는 연 600만원, IRP 포함 합산은 연 900만원이다. "
        "총급여 5,500만원 이하는 16.5%, 초과는 13.2%를 적용한다. "
        "근로자퇴직급여 보장법에 따라 중도인출 사유가 정해져 있다."
    )


_GROUNDED_TAX_ANSWER = (
    "연금저축 세액공제 대상 납입한도는 연 600만원이고 IRP 포함 합산은 "
    "연 900만원입니다 [근거:doc41:chunk=1]."
)


def test_prohibited_claims_are_blocked_on_any_route() -> None:
    """A guarantee or a performed action is forbidden whatever was asked.

    The recommendation checks only run on a recommendation route, so a tax
    answer could promise principal, assert that an account had been looked up,
    or crown a product market-best and still validate — provided it also
    answered the question.
    """

    question = "연금저축과 IRP 세액공제 한도"
    route = classify_question(question)
    for tail in (
        "이 상품은 원금이 보장됩니다 [근거:doc41:chunk=1].",
        "수익률을 보장해 드립니다 [근거:doc41:chunk=1].",
        "고객님 계좌를 조회해 잔고를 확인했습니다 [근거:doc41:chunk=1].",
        "요청하신 상품을 매수했습니다 [근거:doc41:chunk=1].",
        "이 상품이 전 시장 1위입니다 [근거:doc41:chunk=1].",
        "최적 상품입니다 [근거:doc41:chunk=1].",
    ):
        result = validate_answer(
            answer=f"{_GROUNDED_TAX_ANSWER} {tail}",
            question=question,
            evidence_context=_tax_context(),
            route=route,
            assessment=_assessment(),
        )
        assert result.valid is False, tail


def test_naming_the_statute_is_not_a_guarantee() -> None:
    """근로자퇴직급여 보장법 contains 보장 and must stay answerable."""

    question = "연금저축과 IRP 세액공제 한도"
    result = validate_answer(
        answer=(
            f"{_GROUNDED_TAX_ANSWER} 근로자퇴직급여 보장법에 따라 정해집니다 "
            "[근거:doc41:chunk=1]."
        ),
        question=question,
        evidence_context=_tax_context(),
        route=classify_question(question),
        assessment=_assessment(),
    )
    assert result.valid is True


@pytest.mark.parametrize("tail", [
    "원금이나 수익률은 보장되지 않습니다.",
    "원금 보장 상품이 아닙니다.",
    "수익률을 보장할 수 없습니다.",
    "전 시장에서 가장 좋은 상품이라고 단정할 수 없습니다.",
    "최적 상품은 아닙니다.",
])
def test_explicit_disclaimer_does_not_block_grounded_answer(tail: str) -> None:
    question = "연금저축과 IRP 세액공제 한도"
    result = validate_answer(
        answer=f"{_GROUNDED_TAX_ANSWER} {tail}",
        question=question,
        evidence_context=_tax_context(),
        route=classify_question(question),
        assessment=_assessment(),
    )
    assert result.valid, result.unsupported_claims


@pytest.mark.parametrize("tail", [
    "원금은 보장되지 않지만 수익률은 보장됩니다.",
    "원금은 보장되지 않습니다. 수익률은 보장합니다.",
    "원금은 보장되지 않습니다. 최적 상품입니다.",
    "원금을 보장하지 않는 것은 아닙니다.",
    "원금 보장 상품이 아닌 것은 아닙니다.",
    "원금을 보장하지 않을 수 없습니다.",
    "최적 상품이 아닐 수 없습니다.",
    "원금을 보장할 수 없지는 않습니다.",
    "원금이 보장됩니다. 다른 상품에는 적용되지 않습니다.",
    "최적 상품입니다. 원금은 보장되지 않습니다.",
])
def test_disclaimer_does_not_hide_positive_or_double_negative_claim(tail: str) -> None:
    question = "연금저축과 IRP 세액공제 한도"
    result = validate_answer(
        answer=f"{_GROUNDED_TAX_ANSWER} {tail}",
        question=question,
        evidence_context=_tax_context(),
        route=classify_question(question),
        assessment=_assessment(),
    )
    assert not result.valid
    assert {claim.kind for claim in result.unsupported_claims} & {
        "prohibited_assurance", "market_superlative"
    }


@pytest.mark.parametrize("answer, blocked", [
    ("최적의 상품은 아닙니다.", False),
    ("최고의 상품이라고 단정할 수 없습니다.", False),
    ("최적의 상품입니다.", True),
    ("최적의 상품이 아닐 수 없습니다.", True),
    ("최고의 상품이라고 단정할 수 없습니다. 최적의 상품입니다.", True),
])
def test_recommendation_superlative_check_respects_local_denial(answer: str, blocked: bool) -> None:
    question = "IRP에서 가입할 상품을 추천해 주세요."
    result = validate_answer(
        answer=answer,
        question=question,
        evidence_context="",
        route=classify_question(question),
        assessment=_assessment(),
        suitability=_suitability(),
    )
    assert result.recommendation_blocked is blocked


def test_combined_route_answers_are_checked_for_rule_claims() -> None:
    """A question that matched no other family is still answered with rules.

    Leaving "combined" out of the rule route labels meant a long procedural
    answer was validated with zero claims checked, because rule extraction
    never ran for it.
    """

    from app.routing import QuestionRoute
    from app.rule_claim_validation import is_nonquantitative_rule_claim

    route = QuestionRoute(
        labels=("combined",),
        source_groups=frozenset({"general"}),
        needs_conditions=False,
        missing_conditions=(),
        requires_calculation=False,
    )
    assert is_nonquantitative_rule_claim(
        "회원가입 후 공동인증서를 발급받아야 합니다", route
    )
