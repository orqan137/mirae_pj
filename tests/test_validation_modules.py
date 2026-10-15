from __future__ import annotations

import pytest

from app.answer_recovery import (
    attach_unambiguous_evidence_citations as recovery_attach_citations,
)
from app.answer_validation import (
    AnswerValidationResult as facade_result_type,
    attach_unambiguous_evidence_citations as facade_attach_citations,
)
from app.product_claim_validation import (
    parse_typed_product_inventory,
    typed_citation_excerpt_inventory,
    typed_citation_sources,
    validate_typed_product_relations,
)
from app.routing import classify_question
from app.rule_claim_validation import validate_citation_claims
from app.validation_primitives import (
    citation_excerpt_inventory,
    critical_citation_signatures,
    normalize_currency_won,
    normalize_number,
    resolve_citation_reference,
    sentence_spans,
    split_citation_references,
)
from app.validation_types import AnswerValidationResult


def _typed_context() -> str:
    return "\n".join(
        (
            "[TYPED PRODUCT COMPARISON]",
            "product_code | class | metric | value | unit | as_of_date | "
            "period_years | aggregation | tax_basis | source",
            "KR510902511M | A | annual_total_fee_rate | 1.47 | "
            "percent_per_year | 2025-01-17 |  | annual | pre_tax | fee.pdf:page=3",
        )
    )


def test_answer_validation_facade_preserves_public_imports() -> None:
    assert facade_result_type is AnswerValidationResult
    assert facade_attach_citations is recovery_attach_citations


def test_numeric_normalization_is_shared_across_validators() -> None:
    assert normalize_number("1,200.00") == "1200"
    assert normalize_number("-0.50") == "-0.5"
    assert normalize_number("not-a-number") is None


@pytest.mark.parametrize(
    ("number", "unit", "expected"),
    (
        ("600", "만 원", "6000000"),
        ("6", "백만원", "6000000"),
        ("0.6", "천만원", "6000000"),
        ("1.2", "억원", "120000000"),
        ("2", "조원", "2000000000000"),
    ),
)
def test_currency_units_share_one_won_signature(
    number: str,
    unit: str,
    expected: str,
) -> None:
    assert normalize_currency_won(number, unit) == expected


def test_equivalent_currency_surface_forms_have_same_signature() -> None:
    assert critical_citation_signatures("6,000,000원") == (
        critical_citation_signatures("600만 원")
    )


def test_sentence_boundaries_ignore_periods_inside_citation_ids() -> None:
    answer = "총보수는 1.47%입니다 [근거:fee.v2.pdf:page=3]. 다음 문장입니다."
    spans = sentence_spans(answer)

    assert len(spans) == 2
    assert answer[slice(*spans[0])].endswith("[근거:fee.v2.pdf:page=3]")


def test_citation_resolution_accepts_only_unambiguous_abbreviation() -> None:
    allowed = frozenset({"doc41:page=1", "doc42:page=2"})
    assert resolve_citation_reference("doc41", allowed) == "doc41:page=1"
    assert resolve_citation_reference("doc", allowed) is None
    assert split_citation_references("doc41, doc42") == ("doc41", "doc42")
    assert split_citation_references("doc41; doc42") == ("doc41", "doc42")


def test_excerpt_inventory_keeps_repeated_blocks_for_same_source() -> None:
    context = "\n".join(
        (
            "citation_id=rule.pdf:page=1",
            "첫 번째 근거",
            "citation_id=rule.pdf:page=1",
            "두 번째 근거",
        )
    )

    assert citation_excerpt_inventory(context)["rule.pdf:page=1"] == (
        "첫 번째 근거\n두 번째 근거"
    )


def test_product_module_binds_value_metric_product_and_source() -> None:
    inventory = parse_typed_product_inventory(_typed_context())

    assert typed_citation_sources(inventory) == frozenset({"fee.pdf:page=3"})
    checked, failures = validate_typed_product_relations(
        answer="KR510902511M의 총보수는 1.47%입니다 [근거:fee.pdf:page=3].",
        inventory=inventory,
        resolution=None,
    )

    assert checked == 1
    assert failures == ()


def test_typed_rows_render_unit_bearing_citation_excerpts() -> None:
    inventory = parse_typed_product_inventory(_typed_context())

    assert typed_citation_excerpt_inventory(inventory)["fee.pdf:page=3"] == (
        "KR510902511M A annual_total_fee_rate 1.47% 2025-01-17 annual pre_tax"
    )


def test_product_module_rejects_wrong_source_for_correct_value() -> None:
    inventory = parse_typed_product_inventory(_typed_context())
    _checked, failures = validate_typed_product_relations(
        answer="KR510902511M의 총보수는 1.47%입니다 [근거:other.pdf:page=9].",
        inventory=inventory,
        resolution=None,
    )

    assert {failure.kind for failure in failures} == {"product_citation_relation"}


def test_rule_module_rejects_scope_reversal_against_cited_excerpt() -> None:
    reference = "rule.pdf:page=4"
    _citations, checked, failures = validate_citation_claims(
        answer=(
            "IRP 적립금은 아무 사유 없이 언제든 중도인출할 수 있습니다 "
            f"[근거:{reference}]."
        ),
        allowed=frozenset({reference}),
        excerpts={reference: "IRP 적립금은 법정 사유에 한해 중도인출할 수 있습니다."},
        route=classify_question("IRP 중도인출 요건을 알려주세요"),
    )

    assert checked == 1
    assert "rule_qualifier_contradiction" in {failure.kind for failure in failures}


def test_rule_module_requires_citation_for_consequential_number() -> None:
    reference = "tax.pdf:page=2"
    _citations, _checked, failures = validate_citation_claims(
        answer="연금 세율은 3.3%입니다.",
        allowed=frozenset({reference}),
        excerpts={reference: "연금 세율은 3.3%입니다."},
        route=classify_question("연금 세율"),
    )

    assert "citation_missing" in {failure.kind for failure in failures}


@pytest.mark.parametrize(
    ("claim", "excerpt"),
    (
        ("세액공제율은 99.9%입니다", "세액공제는 소득 요건에 따라 적용됩니다"),
        ("납입 한도는 9,999만원입니다", "납입 한도는 별도 기준을 따릅니다"),
        ("시행일은 2026년 9월 5일입니다", "시행일은 공지에서 확인합니다"),
        ("기간말 잔고는 999백만좌입니다", "기간말 잔고는 기준일 값입니다"),
    ),
)
def test_cited_critical_value_must_exist_in_that_excerpt(
    claim: str,
    excerpt: str,
) -> None:
    reference = "rule.pdf:page=1"
    _citations, _checked, failures = validate_citation_claims(
        answer=f"{claim} [근거:{reference}].",
        allowed=frozenset({reference}),
        excerpts={reference: excerpt},
        route=classify_question("연금 규정을 알려주세요"),
    )

    assert "numeric_citation_relation" in {failure.kind for failure in failures}


def test_multiple_citations_can_jointly_support_values_in_one_sentence() -> None:
    first = "a.pdf:page=1"
    second = "b.pdf:page=2"
    citations, _checked, failures = validate_citation_claims(
        answer=f"A는 3.3%, B는 5.5%입니다 [근거:{first}, {second}].",
        allowed=frozenset({first, second}),
        excerpts={first: "A는 3.3%입니다.", second: "B는 5.5%입니다."},
        route=classify_question("연금 세율 비교"),
    )

    assert citations == 1
    assert failures == ()
