from __future__ import annotations

import pytest

from app.answer_validation import validate_answer
from app.evidence import EvidenceAssessment
from app.routing import classify_question


CODE_A = "KR510902511M"
CODE_B = "KR5152420028"


def _context() -> str:
    header = (
        "[TYPED PRODUCT COMPARISON]\n"
        "product_code | class | metric | value | unit | as_of_date | "
        "period_years | aggregation | tax_basis | source\n"
    )
    rows = []
    for code, source, fee, expense, one, three in (
        (CODE_A, "a.pdf:page=3", "1.47", "1.49", "4.10", "2.30"),
        (CODE_B, "b.pdf:page=3", "0.145", "0.155", "3.20", "1.80"),
    ):
        for metric, value in (("annual_total_fee_rate", fee), ("annual_total_expense_rate", expense)):
            rows.append(
                f"{code} | C-P | {metric} | {value} | percent_per_year | "
                f"2024-12-31 | | stated_rate | not_applicable | {source}"
            )
        for years, value in ((1, one), (3, three)):
            rows.append(
                f"{code} | C-P | annualized_return_{years}y | {value} | percent | "
                f"2024-12-31 | {years} | annualized | pre_tax | {source}"
            )
    return header + "\n".join(rows)


def _validate(question: str, answer: str, context: str | None = None):
    return validate_answer(
        question=question,
        answer=answer,
        evidence_context=_context() if context is None else context,
        route=classify_question(question),
        assessment=EvidenceAssessment(
            version="test", status="answerable", coverage=(), missing_conditions=()
        ),
    )


@pytest.mark.parametrize("body", [
    "총보수는 1.47%이고 총보수비용은 1.49%입니다 [근거:a.pdf:page=3].",
    "총보수비용은 1.49%이고 총보수는 1.47%입니다 [근거:a.pdf:page=3].",
    "총보수는 1.47%입니다 [근거:a.pdf:page=3]. 총보수비용은 1.49%입니다 [근거:a.pdf:page=3].",
])
def test_both_fee_metrics_are_accepted_in_one_or_separate_sentences(body: str) -> None:
    result = _validate(
        f"{CODE_A} C-P 클래스의 총보수와 총보수비용을 알려주세요.",
        f"{CODE_A} C-P 클래스의 {body}",
    )
    assert result.valid, result.unsupported_claims


@pytest.mark.parametrize("body", [
    "총보수는 1.47%입니다", "총보수비용은 1.49%입니다",
])
def test_one_requested_fee_metric_cannot_substitute_for_both(body: str) -> None:
    result = _validate(
        f"{CODE_A} C-P 클래스의 총보수와 총보수비용을 알려주세요.",
        f"{CODE_A} C-P 클래스의 {body} [근거:a.pdf:page=3].",
    )
    assert not result.valid
    assert any(claim.kind == "product_question_relation" for claim in result.unsupported_claims)


@pytest.mark.parametrize("body, valid", [
    ("총보수와 총보수비용은 각각 1.47%, 1.49%입니다", True),
    ("총보수비용과 총보수는 각각 1.49%, 1.47%입니다", True),
    ("총보수와 총보수비용은 각각 1.49%, 1.47%입니다", False),
    ("총보수와 총보수비용은 각각 1.47%입니다", False),
    ("총보수와 총보수비용은 1.47%, 1.49%입니다", False),
])
def test_explicit_respectively_pairs_equal_length_metric_and_value_lists(
    body: str, valid: bool,
) -> None:
    result = _validate(
        f"{CODE_A} C-P 클래스의 총보수와 총보수비용을 알려주세요.",
        f"{CODE_A} C-P 클래스의 {body} [근거:a.pdf:page=3].",
    )
    assert result.valid is valid, result.unsupported_claims


@pytest.mark.parametrize("body, valid", [
    ("1년 4.10%, 3년 2.30%입니다", True),
    ("3년 2.30%, 1년 4.10%입니다", True),
    ("1년 4.10%입니다", False),
    ("3년 2.30%입니다", False),
    ("1년 2.30%, 3년 4.10%입니다", False),
])
def test_requested_return_periods_are_bound_to_each_value(body: str, valid: bool) -> None:
    result = _validate(
        f"{CODE_A} C-P 클래스의 2024-12-31 기준 1년과 3년 연환산 세전 수익률은?",
        f"{CODE_A} C-P 클래스의 2024-12-31 기준 연환산 세전 수익률은 "
        f"{body} [근거:a.pdf:page=3].",
    )
    assert result.valid is valid, result.unsupported_claims


@pytest.mark.parametrize("values, valid", [
    ("4.10%, 2.30%", True), ("2.30%, 4.10%", False),
])
def test_explicit_respectively_preserves_return_period_value_binding(
    values: str, valid: bool,
) -> None:
    result = _validate(
        f"{CODE_A} C-P 클래스의 2024-12-31 기준 1년과 3년 연환산 세전 수익률은?",
        f"{CODE_A} C-P 클래스의 2024-12-31 기준 1년과 3년 연환산 세전 수익률은 "
        f"각각 {values}입니다 [근거:a.pdf:page=3].",
    )
    assert result.valid is valid, result.unsupported_claims


def test_distinct_product_requests_do_not_form_a_cartesian_product() -> None:
    result = _validate(
        f"{CODE_A} C-P 클래스 총보수와 {CODE_B} C-P 클래스 총보수비용은?",
        f"{CODE_A} C-P 클래스 총보수는 1.47%이고 "
        f"{CODE_B} C-P 클래스 총보수비용은 0.155%입니다 "
        "[근거:a.pdf:page=3, b.pdf:page=3].",
    )
    assert result.valid, result.unsupported_claims


def test_each_product_must_answer_its_distinct_request() -> None:
    result = _validate(
        f"{CODE_A} C-P 클래스 총보수와 {CODE_B} C-P 클래스 총보수비용은?",
        f"{CODE_A} C-P 클래스 총보수는 1.47%입니다 [근거:a.pdf:page=3].",
    )
    assert not result.valid
    assert any(CODE_B in claim.normalized for claim in result.unsupported_claims)


def test_multiple_products_can_continue_their_metrics_in_pronoun_sentences() -> None:
    question = f"{CODE_A}과 {CODE_B} C-P 클래스의 총보수와 총보수비용을 알려주세요."
    answer = (
        f"{CODE_A} C-P 클래스 총보수는 1.47%입니다 [근거:a.pdf:page=3]. "
        "이 상품의 총보수비용은 1.49%입니다 [근거:a.pdf:page=3]. "
        f"{CODE_B} C-P 클래스 총보수는 0.145%입니다 [근거:b.pdf:page=3]. "
        "이 상품의 총보수비용은 0.155%입니다 [근거:b.pdf:page=3]."
    )
    result = _validate(question, answer)
    assert result.valid, result.unsupported_claims
    wrong_product_value = _validate(question, answer.replace("0.155%", "1.49%"))
    assert not wrong_product_value.valid


def test_supplementary_metric_remains_allowed_after_requested_metric() -> None:
    result = _validate(
        f"{CODE_A} C-P 클래스의 총보수를 알려주세요.",
        f"{CODE_A} C-P 클래스 총보수는 1.47%이며 총보수비용은 1.49%입니다 "
        "[근거:a.pdf:page=3].",
    )
    assert result.valid, result.unsupported_claims


def test_unavailable_requested_metric_allows_explicit_partial_answer() -> None:
    context = "\n".join(
        line for line in _context().splitlines() if "annual_total_expense_rate" not in line
    )
    result = _validate(
        f"{CODE_A} C-P 클래스의 총보수와 총보수비용을 알려주세요.",
        f"{CODE_A} C-P 클래스 총보수는 1.47%입니다 [근거:a.pdf:page=3]. "
        "총보수비용은 제공 자료에서 확인되지 않습니다.",
        context,
    )
    assert result.valid, result.unsupported_claims


def test_repeated_product_mentions_accumulate_requested_metrics() -> None:
    question = (
        f"{CODE_A} 총보수, {CODE_B} 총보수, {CODE_A} 총보수비용, "
        f"{CODE_B} 총보수비용을 C-P 클래스 기준으로 알려주세요."
    )
    result = _validate(
        question,
        f"{CODE_A} C-P 클래스 총보수비용은 1.49%입니다 [근거:a.pdf:page=3]. "
        f"{CODE_B} C-P 클래스 총보수비용은 0.155%입니다 [근거:b.pdf:page=3].",
    )
    assert not result.valid
    assert sum(claim.kind == "product_question_relation" for claim in result.unsupported_claims) == 2


@pytest.mark.parametrize("answer", [
    f"{CODE_A} C-P 클래스의 총보수는 1.47%입니다 [근거:a.pdf:page=3]. "
    f"{CODE_A} C-P 클래스의 2024-12-31 기준 1년 연환산 세전 수익률은 4.10%입니다 [근거:a.pdf:page=3].",
    f"{CODE_A} C-P 클래스의 2024-12-31 기준 총보수는 1.47%이고 "
    "1년 연환산 세전 수익률은 4.10%입니다 [근거:a.pdf:page=3].",
])
def test_mixed_fee_and_return_do_not_share_performance_only_qualifiers(answer: str) -> None:
    result = _validate(
        f"{CODE_A} C-P 클래스의 총보수와 2024-12-31 기준 1년 연환산 세전 수익률은?",
        answer,
    )
    assert result.valid, result.unsupported_claims


@pytest.mark.parametrize("source, valid", [("a.pdf:page=3", True), ("new.pdf:page=8", False)])
def test_citation_must_match_same_row_as_inherited_date(source: str, valid: bool) -> None:
    context = _context() + (
        f"\n{CODE_A} | C-P | annualized_return_1y | 4.10 | percent | "
        "2025-01-31 | 1 | annualized | pre_tax | new.pdf:page=8"
    )
    result = _validate(
        f"{CODE_A} C-P 클래스의 2024-12-31 기준 1년 연환산 세전 수익률은?",
        "기준일은 2024-12-31입니다 [근거:a.pdf:page=3].\n"
        f"{CODE_A} C-P 클래스의 1년 연환산 세전 수익률은 4.10%입니다 [근거:{source}].",
        context,
    )
    assert result.valid is valid, result.unsupported_claims
    if not valid:
        assert any(claim.kind == "product_citation_relation" for claim in result.unsupported_claims)


@pytest.mark.parametrize("excluded, requested, value", [
    ("총보수가 아니라", "총보수비용", "1.49"),
    ("총보수 말고", "총보수비용", "1.49"),
    ("총보수를 제외하고", "총보수비용", "1.49"),
    ("총보수비용이 아니라", "총보수", "1.47"),
])
def test_explicitly_excluded_question_metric_is_not_required(
    excluded: str, requested: str, value: str
) -> None:
    question = f"{CODE_A} C-P 클래스의 {excluded} {requested}만 알려주세요."
    result = _validate(
        question,
        f"{CODE_A} C-P 클래스의 {requested}는 {value}%입니다 [근거:a.pdf:page=3].",
    )
    assert result.valid, result.unsupported_claims
    excluded_metric, excluded_value = (
        ("총보수", "1.47") if requested == "총보수비용" else ("총보수비용", "1.49")
    )
    wrong = _validate(
        question,
        f"{CODE_A} C-P 클래스의 {excluded_metric}는 {excluded_value}%입니다 [근거:a.pdf:page=3].",
    )
    assert not wrong.valid
    assert any(claim.kind == "product_question_relation" for claim in wrong.unsupported_claims)


def test_excluded_metric_does_not_override_inherited_requested_metric() -> None:
    result = _validate(
        f"{CODE_A} C-P 클래스의 총보수비용이 아니라 총보수만 알려주세요.",
        f"{CODE_A} C-P 클래스의 보수율은 1.47%입니다 [근거:a.pdf:page=3].",
    )
    assert result.valid, result.unsupported_claims
