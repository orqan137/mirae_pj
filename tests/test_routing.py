from __future__ import annotations

import unicodedata

from app.routing import classify_question


def test_tax_question_routes_to_general_and_calculation() -> None:
    route = classify_question(
        "총급여 5,500만원이고 IRP에 900만원을 납입했습니다. 공제액을 계산해 주세요."
    )
    assert "tax" in route.labels
    assert route.source_groups == frozenset({"general"})
    assert route.requires_calculation is True


def test_tax_rule_explanation_does_not_trigger_personal_calculator() -> None:
    route = classify_question("연금저축이랑 IRP 세액공제 한도와 공제율을 설명해 주세요")
    assert "tax" in route.labels
    assert route.requires_calculation is False


def test_product_comparison_routes_to_product_corpus() -> None:
    route = classify_question("솔로몬 국공채 단기와 장기 펀드 보수와 위험등급을 비교해 주세요")
    assert "product_comparison" in route.labels
    assert route.source_groups == frozenset({"product"})


def test_fullwidth_product_code_with_korean_particle_is_detected() -> None:
    route = classify_question("ＫＲＴＥＳＴ０００００１과 다른 상품을 비교해줘")
    assert "product_comparison" in route.labels
    assert route.source_groups == frozenset({"product"})


def test_nfkc_and_decomposed_korean_are_classified() -> None:
    question = unicodedata.normalize("NFD", "연금저축 세액공제 한도")
    route = classify_question(question)
    assert {"system", "tax", "combined"} <= set(route.labels)


def test_unconditional_recommendation_requires_customer_conditions() -> None:
    route = classify_question("좋은 연금 상품 하나 추천해 주세요")
    assert "conditional_recommendation" in route.labels
    assert route.source_groups == frozenset({"general", "product"})
    assert route.needs_conditions is True
    assert route.missing_conditions == (
        "계좌 유형",
        "투자기간 또는 은퇴 시점",
        "손실 감내 능력과 위험 선호",
        "유동성 또는 중도인출 계획",
        "노후자금 목표",
    )


def test_partial_recommendation_profile_still_requires_conditions() -> None:
    route = classify_question("IRP에서 20년 투자할 공격적인 상품을 추천해 주세요")
    assert route.needs_conditions is True


def test_complete_recommendation_profile_has_all_five_dimensions() -> None:
    route = classify_question(
        "IRP 계좌에서 20년 투자하고 손실 위험을 감수할 수 있습니다. "
        "중도인출 계획은 없고 노후자금 성장 목표에 맞는 상품을 추천해 주세요."
    )
    assert "conditional_recommendation" in route.labels
    assert route.needs_conditions is False


def test_four_of_five_recommendation_dimensions_are_still_incomplete() -> None:
    route = classify_question(
        "IRP 계좌에서 20년 장기 투자하고 손실을 감내할 수 있으며 "
        "중도인출 계획은 없는 상품을 추천해 주세요."
    )
    assert route.needs_conditions is True


def test_product_risk_word_alone_is_not_a_customer_risk_profile() -> None:
    route = classify_question(
        "IRP 계좌에서 20년 투자하고 중도인출 계획은 없으며 노후자금 목표입니다. "
        "위험등급도 설명하면서 상품을 추천해 주세요."
    )
    assert route.needs_conditions is True


def test_relative_two_product_choice_routes_to_comparator_without_word_compare() -> None:
    route = classify_question(
        "KR510902511M와 KR5152420028 중 총보수가 더 낮은 쪽을 알려주세요"
    )
    assert route.labels == ("product_comparison",)
    assert route.source_groups == frozenset({"product"})


def test_transfer_and_withdrawal_paraphrases_are_procedures() -> None:
    transfer = classify_question(
        "개인형퇴직연금 계좌를 다른 금융사로 옮기려면 무엇을 해야 하나요"
    )
    withdrawal = classify_question(
        "퇴직연금에서 돈을 빼려면 어떤 요건과 서류가 필요한가요"
    )
    assert "procedure" in transfer.labels
    assert "procedure" in withdrawal.labels


def test_liquidity_profile_statement_does_not_request_withdrawal_procedure() -> None:
    route = classify_question(
        "IRP 계좌에서 15년 투자하고 손실을 감내할 수 있습니다. "
        "중도인출 계획은 없고 노후자금 성장을 위한 펀드를 추천해 주세요."
    )
    assert "conditional_recommendation" in route.labels
    assert "procedure" not in route.labels


def test_generic_pension_etf_tax_question_stays_in_general_corpus() -> None:
    route = classify_question("연금계좌 월배당 ETF의 배당이 건강보험료에 잡히나요?")
    assert {"system", "tax"}.issubset(route.labels)
    assert not {"product_explanation", "product_comparison"} & set(route.labels)
    assert route.source_groups == frozenset({"general"})


def test_generic_fund_transfer_question_stays_in_general_corpus() -> None:
    route = classify_question("다른 증권사로 연금계좌를 옮길 때 펀드를 꼭 팔아야 하나요?")
    assert {"system", "procedure"}.issubset(route.labels)
    assert not {"product_explanation", "product_comparison"} & set(route.labels)
    assert route.source_groups == frozenset({"general"})


def test_declined_transfer_and_conversion_do_not_add_procedure_route() -> None:
    transfer = classify_question(
        "IRP를 타사로 옮기는 일은 안 하려고요. 세액공제만 설명해 주세요."
    )
    conversion = classify_question(
        "ISA 만기금을 연금계좌로 전환할 의향은 없습니다. 다른 선택지만 알려주세요."
    )

    assert {"system", "tax"}.issubset(transfer.labels)
    assert "procedure" not in transfer.labels
    assert "system" in conversion.labels
    assert "procedure" not in conversion.labels


def test_colloquial_withdrawal_payout_and_direct_claim_are_procedures() -> None:
    questions = (
        "주거용 집 마련에 IRP 일부를 당겨 받아도 되나요?",
        "치료비 마련하려 퇴직연금 일부를 당겨 받을 수 있나요?",
        "ISA 끝난 돈을 연금계좌에 넣으려면 언제까지 해야 하나요?",
        "ISA 만기 대금을 개인형퇴직연금 쪽으로 넘기는 기한이 있나요?",
        "IRP에서 몽땅 수령하면 세금 처리가 달라지나요?",
        "사업주가 폐업해 퇴직급여가 밀리면 금융사에 바로 요구할 수 있나요?",
    )

    assert all("procedure" in classify_question(question).labels for question in questions)


def test_rule_change_actual_receipt_year_and_trading_hours_are_procedural_topics() -> None:
    questions = (
        "퇴직연금 규약을 고칠 때 직원 대표 의견을 들어야 하나요?",
        "연금은 개시했지만 돈을 안 뺀 해도 실제 수령 연차가 올라가나요?",
        "퇴직연금 ETF와 ETN, 리츠의 정규장 거래 시간은 언제인가요?",
    )
    for question in questions:
        route = classify_question(question)
        assert "system" in route.labels
        assert "procedure" in route.labels


def test_single_listed_pension_product_hours_and_sell_word_are_procedural() -> None:
    questions = (
        "퇴직연금 ETF는 시간외에도 거래되나요?",
        "IRP에서 상장 리츠를 장 마감 뒤 팔 수 있나요?",
        "연금계좌 ETN은 몇 시까지 매매할 수 있나요?",
    )
    for question in questions:
        route = classify_question(question)
        assert {"system", "procedure"}.issubset(route.labels)

    non_pension = classify_question(
        "퇴직연금이 아닌 일반 주식계좌에서 ETF를 시간외에 팔 수 있나요?"
    )
    assert "procedure" not in non_pension.labels


def test_national_pension_is_a_system_topic() -> None:
    route = classify_question("국민연금 예상 수령액을 어디서 조회하나요?")
    assert "system" in route.labels


def test_pension_income_classification_is_tax_routed_without_literal_tax_word() -> None:
    route = classify_question(
        "연금 계좌에서 번 투자수익은 금융소득 말고 연금소득으로 보는 건가요?"
    )
    assert {"system", "tax"}.issubset(route.labels)


def test_predicate_form_recommendation_is_routed_as_one() -> None:
    """"뭐가 좋나요" asks for a suggestion as plainly as "추천해주세요".

    The recommendation terms were all noun phrases, so a predicate phrasing
    carried no recommendation label. Without it the suitability prefilter never
    ran and the question reached generation, the one path that must not produce
    a product suggestion before the five conditions are confirmed.
    """

    for question in (
        "위험등급 낮은 상품 중에 뭐가 좋나요",
        "어떤 펀드가 괜찮을까요",
        "어떤 상품이 좋을까요",
        "뭐가 나을까요",
    ):
        assert "conditional_recommendation" in classify_question(question).labels, question


def test_comparing_two_schemes_is_not_a_product_recommendation() -> None:
    """Weighing two 제도 is a system question, not a request to pick a fund."""

    for question in (
        "DB형과 DC형 중 어느 제도가 유리한가요",
        "연금저축과 IRP의 세액공제 한도를 알려주세요",
        "디폴트옵션은 무엇인가요",
    ):
        assert (
            "conditional_recommendation" not in classify_question(question).labels
        ), question


def test_ranking_request_is_a_recommendation() -> None:
    """Asking which product ranks first is a recommendation, whatever verb carries it.

    "연금저축 수익률이 가장 높은 상품 1위를 알려주세요" was labelled a product
    explanation, skipped the suitability gate and reached generation — the one
    path that must never put a product forward without the five conditions.
    """

    for question in (
        "연금저축 수익률이 가장 높은 상품 1위를 알려주세요",
        "총보수가 가장 저렴한 상품은 무엇인가요",
        "수익률 순위 top 3 알려줘",
        "제일 좋은 연금 상품 알려주세요",
    ):
        assert "conditional_recommendation" in classify_question(question).labels


def test_superlative_about_one_named_product_is_not_a_recommendation() -> None:
    """A superlative selects among products only when none is named."""

    for question in (
        "이 상품의 위험등급이 가장 높은 등급인가요",
        "KR510902511M의 총보수가 가장 저렴한 클래스는 무엇인가요",
    ):
        assert "conditional_recommendation" not in classify_question(question).labels


def test_rate_question_with_an_income_amount_requires_the_calculator() -> None:
    """The rate follows from the asker's own income, so it is a per-person question.

    Without the calculator "총급여 7천만원이면 세액공제율이 몇 퍼센트인가요" was
    answered from whichever bracket row retrieval surfaced, and a 7천만원 asker
    was told 16.5%.
    """

    for question in (
        "총급여 7천만원이면 세액공제율이 몇 퍼센트인가요",
        "총급여 5천만원이면 공제율이 얼마인가요",
    ):
        assert classify_question(question).requires_calculation


def test_a_general_rate_question_does_not_require_the_calculator() -> None:
    """No income means no personal bracket to settle."""

    assert not classify_question(
        "세액공제율은 소득에 따라 어떻게 다른가요"
    ).requires_calculation


def test_plain_recommendation_wording_is_recognised_whatever_noun_it_uses() -> None:
    """The noun is whatever the asker calls the product.

    Enumerating "어떤 상품이 좋", "어떤 펀드가 좋" and the rest left the plainest
    recommendation of all — "어떤 연금상품이 좋을까요" — with no label, and it
    was refused instead of asking for the five conditions.
    """

    for question in (
        "어떤 연금상품이 좋을까요",
        "어떤 펀드를 추천하나요",
        "어느 계좌가 나을까요",
        "무슨 상품이 적합한가요",
    ):
        assert "conditional_recommendation" in classify_question(question).labels


def test_a_determiner_before_a_non_choice_predicate_is_not_a_recommendation() -> None:
    for question in ("어떤 서류가 필요한가요", "어떤 사유로 중도인출이 가능한가요"):
        assert "conditional_recommendation" not in classify_question(question).labels
