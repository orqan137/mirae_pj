from __future__ import annotations

import pytest

from app.answer_validation import validate_answer
from app.calculations import calculate_pension_tax_credit
from app.evidence import EvidenceAssessment
from app.routing import classify_question


@pytest.mark.parametrize(
    ("question", "eligible", "rate", "credit"),
    [
        (
            "총급여가 5,500만원이고 IRP에 900만원을 납입했습니다. 세액공제액을 계산해 주세요.",
            9_000_000,
            165,
            1_485_000,
        ),
        (
            "종합소득금액 4,500만원이고 연금저축에 600만원을 납입하고 "
            "IRP에 300만원을 납입하면 얼마인가요?",
            9_000_000,
            165,
            1_485_000,
        ),
        (
            "총급여 6,000만원이고 연금저축에 600만원을 납입하고 "
            "IRP에 300만원을 납입했습니다. 세액공제액 계산",
            9_000_000,
            132,
            1_188_000,
        ),
        (
            "총급여 6,000만원이고 연금저축에 900만원을 납입할 때 세액공제액은 얼마인가요?",
            6_000_000,
            132,
            792_000,
        ),
    ],
)
def test_doc41_calculation_boundaries_and_caps(
    question: str,
    eligible: int,
    rate: int,
    credit: int,
) -> None:
    result = calculate_pension_tax_credit(question)

    assert result.status == "calculated"
    assert result.eligible_contribution_won == eligible
    assert result.rate_per_mille == rate
    assert result.calculated_credit_won == credit


def test_won_amount_and_reverse_account_order_are_accepted() -> None:
    result = calculate_pension_tax_credit(
        "총급여액 55,000,001원이며 9,000,000원을 IRP에 납입했습니다. 세액공제액 계산"
    )

    assert result.status == "calculated"
    assert result.rate_per_mille == 132
    assert result.calculated_credit_won == 1_188_000


def test_korean_amount_units_and_shared_trailing_verb_are_accepted() -> None:
    result = calculate_pension_tax_credit(
        "총급여가 6천만원이고 올해 연금저축에 600만원, "
        "IRP에 400만원을 넣었습니다. 세액공제액을 계산해 주세요."
    )

    assert result.status == "calculated"
    assert result.income_won == 60_000_000
    assert result.eligible_contribution_won == 9_000_000
    assert result.rate_per_mille == 132
    assert result.calculated_credit_won == 1_188_000


def test_particle_attached_account_contribution_is_accepted() -> None:
    result = calculate_pension_tax_credit(
        "총급여 5천만원이고 연금저축에만 900만원을 넣었습니다. "
        "세액공제액은 얼마인가요?"
    )

    assert result.status == "calculated"
    assert result.eligible_contribution_won == 6_000_000
    assert result.rate_per_mille == 165
    assert result.calculated_credit_won == 990_000


def test_domain_compound_spacing_does_not_change_tax_credit_calculation() -> None:
    canonical = calculate_pension_tax_credit(
        "총급여 6,000만원이고 연금저축에 600만원을 납입하고 "
        "IRP에 300만원을 납입했습니다. 세액공제액 계산"
    )
    spaced = calculate_pension_tax_credit(
        "총급여 6,000만원이고 연금 저축에 600만원을 납입하고 "
        "IRP에 300만원을 납입했습니다. 세액 공제액 계산"
    )
    assert spaced == canonical


@pytest.mark.parametrize(
    "question",
    [
        "연금저축과 IRP에 합산 900만원을 납입하면 세액공제액은 얼마인가요?",
        "IRP에 900만원을 납입하면 세액공제액은 얼마인가요?",
        "총급여 5,500만원 이하이고 IRP에 900만원을 납입하면 세액공제액은?",
        "총급여 5,500만원이고 IRP 세액공제 한도 900만원이면 얼마인가요?",
        "총급여 오천만원이고 IRP에 구백만원을 납입하면 세액공제액은?",
        "총급여 5,000만원이고 매월 IRP에 50만원을 납입하면 세액공제액은?",
        "총급여 5,000만원이고 IRP는 900만원 한도라 500만원 납입하면 세액공제액은?",
        "총급여 5,000만원이고 회사에서 IRP에 300만원을 납입하면 세액공제액은?",
        "총급여 5,000만원이고 퇴직금 900만원을 IRP에 납입하면 세액공제액은?",
    ],
)
def test_ambiguous_or_unsupported_inputs_fail_closed(question: str) -> None:
    result = calculate_pension_tax_credit(question)
    context = result.render_context()

    assert result.status == "needs_input"
    assert result.calculated_credit_won is None
    assert "calculation_performed=false" in context
    assert "missing_inputs=" in context
    assert "calculated_tax_credit_won=" not in context


def test_calculator_context_corrects_typo_without_changing_source() -> None:
    result = calculate_pension_tax_credit(
        "총급여 5,500만원이고 IRP에 900만원을 납입했습니다. 세액공제액 계산"
    )
    context = result.render_context()

    assert "canonical_doc41_max_credit_won=1485000" in context
    assert "calculated_tax_credit_won=1485000" in context
    assert "148만 5천만원" not in context
    assert "주최 측 원본은 수정하지 않음" in context
    assert "실제 세금 감소액" in context
    assert result.trace_fields()["tax_liability_limit_disclosed"] is True


@pytest.mark.parametrize(
    "question",
    [
        "총급여가 5,500만원이고 IRP에 900만원을 납입했습니다. 세액공제액을 계산해 주세요.",
        "종합소득금액 4,500만원이고 연금저축에 600만원을 납입하고 "
        "IRP에 300만원을 납입하면 얼마인가요?",
        "총급여 6,000만원이고 연금저축에 600만원을 납입하고 "
        "IRP에 300만원을 납입했습니다. 세액공제액 계산",
        "총급여 6,000만원이고 연금저축에 900만원을 납입할 때 세액공제액은 얼마인가요?",
    ],
)
def test_deterministic_calculation_answer_passes_the_same_validator(
    question: str,
) -> None:
    calculation = calculate_pension_tax_credit(question)
    answer = calculation.render_answer()
    result = validate_answer(
        answer=answer,
        question=question,
        evidence_context=calculation.render_context(),
        route=classify_question(question),
        assessment=EvidenceAssessment(
            version="test",
            status="answerable",
            coverage=(),
            missing_conditions=(),
        ),
    )

    assert result.valid is True
    assert "[근거:doc41:calculator]" in answer
    assert str(calculation.calculated_credit_won) not in answer


def test_rate_is_settled_by_income_even_without_a_contribution() -> None:
    """The credit needs a contribution, but the rate does not.

    Dropping the income on the needs_input path left "총급여 7천만원이면
    세액공제율이 몇 퍼센트인가요" with no bracket at all, and the answer quoted
    the 16.5% row that applies to the other side of the threshold.
    """

    over = calculate_pension_tax_credit("총급여 7천만원이면 세액공제율이 몇 퍼센트인가요")
    assert over.status == "needs_input"
    assert over.rate_per_mille == 132
    assert "13.2%" in over.render_settled_rate()
    assert "doc41:calculator" in over.render_settled_rate()

    under = calculate_pension_tax_credit("총급여 5천만원이면 공제율이 얼마인가요")
    assert under.rate_per_mille == 165
    assert "16.5%" in under.render_settled_rate()


def test_no_income_settles_no_rate() -> None:
    result = calculate_pension_tax_credit("연금저축에 600만원 넣으면 세금 얼마 아끼나요")
    assert result.status == "needs_input"
    assert result.rate_per_mille is None
    assert result.render_settled_rate() == ""
