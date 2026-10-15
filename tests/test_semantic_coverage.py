from __future__ import annotations

import pytest

from app.semantic_coverage import (
    assess_semantic_coverage,
    covered_question_slots,
    missing_question_slots,
    required_question_slots,
)


_SLOT_EXAMPLES = (
    (
        "pension_commencement",
        "연금은 언제부터 시작할 수 있나요?",
        "연금 개시 요건을 확인해야 합니다.",
    ),
    (
        "withdrawal",
        "IRP 중도인출은 어떤 사유일 때 가능한가요?",
        "중도인출 가능 사유를 확인해야 합니다.",
    ),
    (
        "account_transfer",
        "IRP를 다른 증권사로 이전할 수 있나요?",
        "IRP 계좌 이전 절차를 확인해야 합니다.",
    ),
    (
        "tax_credit",
        "연금저축 세액공제 한도는 얼마인가요?",
        "세액공제 한도는 소득과 납입액에 따라 확인합니다.",
    ),
    (
        "taxation",
        "연금을 받을 때 어떤 세금이 붙나요?",
        "연금 수령 시 과세 방식을 확인해야 합니다.",
    ),
    (
        "product_fee",
        "이 상품의 총보수는 얼마인가요?",
        "총보수는 상품 설명서에서 확인합니다.",
    ),
    (
        "investment_return",
        "이 상품의 최근 수익률을 알려 주세요.",
        "수익률은 같은 기간과 기준일로 비교합니다.",
    ),
    (
        "risk_grade",
        "이 상품의 원금 손실 위험은 어느 정도인가요?",
        "원금 손실 위험과 위험등급을 함께 확인합니다.",
    ),
    (
        "default_option",
        "퇴직연금 디폴트옵션이 무엇인가요?",
        "디폴트옵션은 사전지정운용제도입니다.",
    ),
    (
        "contribution",
        "IRP에는 얼마까지 납입할 수 있나요?",
        "납입 한도를 확인해야 합니다.",
    ),
    (
        "retirement_money",
        "퇴직금이 들어온 IRP도 같은가요?",
        "퇴직금이 들어온 연금계좌에는 별도 조건이 적용될 수 있습니다.",
    ),
    (
        "age_condition",
        "만 55세가 되면 연금을 시작할 수 있나요?",
        "만 55세라는 연령 조건을 확인해야 합니다.",
    ),
    (
        "enrollment_period_condition",
        "가입 3년이면 연금을 개시할 수 있나요?",
        "가입기간 5년이라는 기간 조건을 확인해야 합니다.",
    ),
)


@pytest.mark.parametrize(("slot", "question", "answer"), _SLOT_EXAMPLES)
def test_each_semantic_slot_accepts_an_independent_paraphrase(
    slot: str,
    question: str,
    answer: str,
) -> None:
    assert slot in required_question_slots(question)
    assert slot in covered_question_slots(question, answer)


@pytest.mark.parametrize(
    ("slot", "question", "unrelated_answer"),
    tuple(
        (slot, question, _SLOT_EXAMPLES[(position + 1) % len(_SLOT_EXAMPLES)][2])
        for position, (slot, question, _answer) in enumerate(_SLOT_EXAMPLES)
    ),
)
def test_each_semantic_slot_rejects_a_rotated_off_topic_answer(
    slot: str,
    question: str,
    unrelated_answer: str,
) -> None:
    assert slot in missing_question_slots(question, unrelated_answer)


@pytest.mark.parametrize(
    ("question", "expected"),
    (
        (
            "IRP 중도인출은 어떤 사유일 때 가능한가요?",
            {"withdrawal"},
        ),
        (
            "연금계좌를 다른 금융회사로 옮기는 절차를 알려 주세요.",
            {"account_transfer"},
        ),
        (
            "IRP를 다른 증권사로 이전할 수 있나요?",
            {"account_transfer"},
        ),
        (
            "연금저축의 총보수와 최근 1년 수익률을 비교해 주세요.",
            {"product_fee", "investment_return"},
        ),
        (
            "DC형 디폴트옵션의 위험등급은 어떻게 되나요?",
            {"default_option", "risk_grade"},
        ),
        (
            "연금저축에 얼마까지 납입할 수 있고 세액공제 한도는 얼마인가요?",
            {"contribution", "tax_credit"},
        ),
        (
            "만 60세가 되면 연금을 받을 수 있나요?",
            {"pension_commencement", "age_condition"},
        ),
    ),
)
def test_broad_pension_intents_become_semantic_slots(
    question: str,
    expected: set[str],
) -> None:
    assert expected <= required_question_slots(question)


def test_incidental_no_withdrawal_preference_is_not_a_withdrawal_question() -> None:
    question = (
        "IRP에서 20년 투자할 예정이고 중도인출 계획은 없습니다. "
        "위험을 감수할 수 있으니 상품을 추천해 주세요."
    )
    assert "withdrawal" not in required_question_slots(question)


def test_coverage_checks_all_requested_product_axes() -> None:
    question = "두 연금상품의 총보수, 수익률, 위험등급을 비교해 주세요."
    answer = "두 상품의 총보수와 위험등급을 같은 기준일로 비교했습니다."

    assert covered_question_slots(question, answer) == {
        "product_fee",
        "risk_grade",
    }
    assert missing_question_slots(question, answer) == ("investment_return",)


def test_condition_slots_allow_correction_instead_of_copying_question_numbers() -> None:
    question = "가입한 지 3년이고 만 55세면 연금을 개시할 수 있나요?"
    answer = "만 55세 이상이며 가입기간 5년 이상이면 연금 개시가 가능합니다."

    assert missing_question_slots(question, answer) == ()


def test_irrelevant_tax_sentence_does_not_cover_commencement_question() -> None:
    question = (
        "만 55세가 되면 연금을 시작할 수 있나요? "
        "퇴직금이 들어온 계좌도 같은 조건인가요?"
    )
    answer = "연금 수령액이 일정 금액을 넘으면 과세될 수 있습니다."

    assert set(missing_question_slots(question, answer)) == {
        "pension_commencement",
        "retirement_money",
        "age_condition",
    }


def test_tax_benefit_does_not_require_unrelated_taxation_slot() -> None:
    question = "연금저축의 세금 혜택과 세액공제 한도를 알려 주세요."

    assert required_question_slots(question) == {"tax_credit", "tax_credit_limit"}


def test_a_magnitude_question_is_answered_by_the_figure() -> None:
    """"한도가 얼마인가요" answered with "연 900만원입니다" is responsive.

    The slot patterns look for the topic word, so a concise correct answer that
    supplies only the amount counted as uncovered and the whole answer was
    discarded. Only a question that asks for the magnitude outright can be
    satisfied this way, and only for a slot whose subject is a figure.
    """

    for question, answer in (
        ("IRP 세액공제 한도가 얼마인가요", "연 900만원입니다."),
        ("연금저축 공제율이 얼마인가요", "16.5%입니다."),
        ("총보수가 얼마인가요", "연 1.47%입니다."),
    ):
        assert assess_semantic_coverage(question, answer).missing == (), question


def test_a_figure_does_not_answer_a_non_quantitative_question() -> None:
    """A rate cannot stand in for the reasons 중도인출 is allowed."""

    for question, answer in (
        ("IRP 중도인출이 가능한 사유를 설명해 주세요.", "연금저축 세액공제율은 16.5%입니다."),
        ("IRP 중도인출 사유가 몇 가지인가요", "연금저축 공제율은 16.5%입니다."),
    ):
        assert "withdrawal" in assess_semantic_coverage(question, answer).missing, question


@pytest.mark.parametrize("answer", [
    "IRP 세액공제 납입한도는 연 900만원입니다.",
    "세액공제 대상 납입액은 연900만원까지입니다.",
    "세액공제 받을 수 있는 납입한도는 연 900만원입니다.",
    "구분: 세액공제 한도; 세액공제 납입한도: 900만 원; ISA 추가 공제한도: 300만 원.",
    "세액공제 한도는 제공 자료만으로 확인할 수 없습니다.",
    "연 900만원입니다 [근거:doc41:chunk=1].",
    "IRP 세액공제는 연900만원 한도입니다.",
    "연900만원까지 세액공제를 받을 수 있습니다.",
    "세액공제 납입 상한은 연900만원입니다.",
])
def test_credit_limit_question_requires_limit_or_explicit_limitation(answer: str) -> None:
    result = assess_semantic_coverage("IRP 세액공제 한도가 얼마인가요?", answer)
    assert "tax_credit_limit" in result.required
    assert "tax_credit_limit" not in result.missing


@pytest.mark.parametrize("answer", [
    "세액공제율은 16.5% 또는 13.2%입니다.",
    "IRP에 900만원 납입했을 때 최대 148만 5천원을 환급받습니다.",
    "세액공제액은 납입한 금액에 16.5%를 곱해서 계산합니다. "
    "IRP에 900만원 납입했을 때 최대 148만 5천원입니다.",
    "세액공제 한도 안내입니다. 적용 공제율은 16.5%입니다.",
])
def test_rate_or_refund_example_cannot_replace_requested_credit_limit(answer: str) -> None:
    assert "tax_credit_limit" in assess_semantic_coverage(
        "IRP 세액공제 한도가 얼마인가요? & + % 🚀", answer
    ).missing


def test_credit_limit_slot_does_not_apply_to_a_rate_only_question() -> None:
    assert "tax_credit_limit" not in required_question_slots("IRP 세액공제율은 얼마인가요?")


def test_definition_only_limit_question_does_not_require_an_amount() -> None:
    answer = "세액공제 한도는 세액공제 대상으로 인정되는 최대 납입액입니다."
    definition = assess_semantic_coverage("세액공제 한도란 무슨 뜻인가요?", answer)
    assert definition.missing == ()
    assert "tax_credit_limit" not in definition.required
    for question in (
        "IRP 세액공제 한도가 얼마인가요?",
        "세액공제 한도란 무슨 뜻이고 한도 금액은 얼마인가요?",
    ):
        assert "tax_credit_limit" in assess_semantic_coverage(question, answer).missing
