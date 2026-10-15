from __future__ import annotations

from app.planning import (
    decompose_question,
    general_query_anchors,
    plan_question,
    required_scope_terms,
    unsupported_scope_reasons,
)
from app.routing import classify_question


def test_product_and_account_noun_conjunctions_are_not_split() -> None:
    question = "연금저축과 IRP의 세액공제 차이를 설명해 주세요"
    subquestions = decompose_question(question)
    assert len(subquestions) == 1
    assert "tax" in subquestions[0].route.labels


def test_explicit_compound_question_is_split_and_routed_per_requirement() -> None:
    question = (
        "DB와 DC의 차이를 설명해 주세요. "
        "그리고 IRP 세액공제 한도도 알려주세요. "
        "또한 IRP 중도인출 절차도 알려주세요."
    )
    plan = plan_question(question, classify_question(question))
    assert [item.subquestion_id for item in plan.subquestions] == ["sq1", "sq2", "sq3"]
    assert [item.text for item in plan.subquestions] == [
        "DB와 DC의 차이를 설명해 주세요",
        "IRP 세액공제 한도도 알려주세요",
        "IRP 중도인출 절차도 알려주세요",
    ]
    assert "system" in plan.subquestions[0].route.labels
    assert "tax" in plan.subquestions[1].route.labels
    assert "procedure" in plan.subquestions[2].route.labels
    by_subquestion: dict[str, list[tuple[str, tuple[str, ...]]]] = {}
    for requirement in plan.requirements:
        by_subquestion.setdefault(requirement.subquestion_id, []).append(
            (requirement.route_label or requirement.kind, requirement.topic_terms)
        )
    assert set(by_subquestion) == {"sq1", "sq2", "sq3"}
    assert any(topic_terms == ("세액공제",) for _, topic_terms in by_subquestion["sq2"])
    assert any(topic_terms == ("중도인출",) for _, topic_terms in by_subquestion["sq3"])
    assert all("중도인출" not in terms for _, terms in by_subquestion["sq2"])


def test_unknown_clause_keeps_a_domain_evidence_requirement() -> None:
    question = "연금 생활을 어떻게 준비해야 하나요? 그리고 가족에게 설명할 요점도 알려주세요"
    plan = plan_question(question, classify_question(question))
    assert len(plan.subquestions) == 2
    assert {item.subquestion_id for item in plan.requirements} == {"sq1", "sq2"}


def test_implicit_multi_intent_enumeration_is_split_conservatively() -> None:
    question = "DB형과 DC형 차이, IRP 세액공제 한도, 중도인출 사유를 알려주세요"
    subquestions = decompose_question(question)
    assert [item.text for item in subquestions] == [
        "DB형과 DC형 차이",
        "IRP 세액공제 한도",
        "중도인출 사유를 알려주세요",
    ]
    assert [
        set(item.route.labels) & {"system", "tax", "procedure"}
        for item in subquestions
    ] == [{"system"}, {"system", "tax"}, {"procedure"}]


def test_telegraphic_three_intent_enumeration_is_split_without_punctuation() -> None:
    subquestions = decompose_question(
        "DB형 DC형 차이 IRP 세액공제 한도 중도인출 사유 알려주세요"
    )
    assert [item.text for item in subquestions] == [
        "DB형 DC형 차이",
        "IRP 세액공제 한도",
        "중도인출 사유 알려주세요",
    ]


def test_spacing_collapsed_comma_enumeration_keeps_all_three_intents() -> None:
    subquestions = decompose_question(
        "DB형과DC형차이,IRP세액공제한도,중도인출사유를알려주세요."
    )
    assert [item.text for item in subquestions] == [
        "DB형과DC형차이",
        "IRP세액공제한도",
        "중도인출사유를알려주세요",
    ]


def test_atomic_two_family_question_is_not_split_as_telegraphic_list() -> None:
    question = "IRP 중도인출 시 세금 처리를 알려주세요"
    assert [item.text for item in decompose_question(question)] == [question]


def test_domain_compound_spacing_preserves_route_and_three_way_plan() -> None:
    question = "DB형 DC형 차이 IRP 세액 공제 한도 중도 인출 사유 알려주세요"
    route = classify_question(question)
    subquestions = decompose_question(question)
    assert route.labels == ("system", "tax", "procedure", "combined")
    assert len(subquestions) == 3


def test_tax_calculation_inputs_are_merged_with_the_following_request() -> None:
    question = (
        "총급여가 5,500만원이고 연금 저축에 600만원을 납입했습니다. "
        "세액 공제액을 계산해 주세요."
    )
    subquestions = decompose_question(question)
    assert len(subquestions) == 1
    assert "총급여가 5,500만원" in subquestions[0].text
    assert "세액 공제액을 계산" in subquestions[0].text
    assert subquestions[0].route.requires_calculation is True


def test_comma_separated_nouns_and_product_codes_are_not_split() -> None:
    assert len(decompose_question("연금저축, IRP의 세액공제 차이를 설명해 주세요")) == 1
    assert (
        len(
            decompose_question(
                "KR510902511M, KR5152420028의 A 클래스 총보수를 비교해 주세요"
            )
        )
        == 1
    )


def test_ellipsis_inherits_only_known_general_topic_for_retrieval() -> None:
    subquestions = decompose_question(
        "연금저축과 IRP의 세액공제 차이는? 한도도 알려주세요"
    )
    assert subquestions[1].text == "한도도 알려주세요"
    assert subquestions[1].inherited_terms == ("세액공제",)
    assert subquestions[1].query_text == "세액공제 한도도 알려주세요"
    assert "tax" in subquestions[1].route.labels


def test_unique_resolved_product_is_carried_into_product_pronoun_query() -> None:
    code = "KR5110501016"
    plan = plan_question(
        (
            "미래에셋프리미엄크레딧알파의 위험등급을 알려주세요. "
            "그 상품의 총보수도 알려주세요"
        ),
        classify_question("상품 위험등급과 총보수"),
        resolved_product_codes=(code,),
    )
    second = plan.subquestions[1]
    assert second.inherited_terms == (code,)
    assert second.query_text.startswith(f"{code} ")
    second_requirements = tuple(
        item for item in plan.requirements if item.subquestion_id == "sq2"
    )
    assert second_requirements
    assert all(item.product_code == code for item in second_requirements)
    assert {item.axis for item in second_requirements if item.axis} == {"fees"}


def test_multiple_resolved_products_are_not_guessed_for_pronoun() -> None:
    plan = plan_question(
        "두 상품을 비교해 주세요. 그 상품의 총보수도 알려주세요",
        classify_question("두 상품을 비교해 주세요"),
        resolved_product_codes=("KR510902511M", "KR5152420028"),
    )
    assert plan.subquestions[1].inherited_terms == ()
    second_identity = next(
        item
        for item in plan.requirements
        if item.subquestion_id == "sq2" and item.kind == "product_identity"
    )
    assert second_identity.product_code is None
    assert second_identity.explicit_identity_required is True


def test_plan_exposes_minimum_deterministic_tool_set() -> None:
    question = (
        "KR510902511M와 KR5152420028 중 총보수가 더 낮은 쪽을 알려주세요. "
        "그리고 IRP를 다른 금융사로 옮길 때 준비할 서류도 알려주세요."
    )
    plan = plan_question(question, classify_question(question))
    assert plan.tools == (
        "general_retriever",
        "product_retriever",
        "product_comparator",
    )


def test_query_anchor_preserves_original_clause_and_adds_official_terms() -> None:
    question = "집을 살 때 퇴직연금에서 돈을 꺼낼 수 있나요?"
    plan = plan_question(question, classify_question(question))
    subquestion = plan.subquestions[0]
    assert plan.question == question
    assert subquestion.text == question.rstrip("?")
    assert subquestion.query_anchors == ("중도인출 무주택 주택 구입 증빙",)
    assert subquestion.query_text.endswith(subquestion.text)


def test_query_anchor_rejects_negated_near_matches() -> None:
    assert general_query_anchors(
        "집을 살 계획이지만 퇴직연금은 빼지 않고 유지할 겁니다."
    ) == ()


def test_explicit_external_irp_provider_scope_is_not_erased() -> None:
    assert required_scope_terms(
        "다른 증권사의 개인형 IRP도 미래에셋 MP 구독 대상인가요?"
    ) == ("다른", "증권사")
    assert required_scope_terms(
        "미래에셋증권의 개인형 IRP 가입자는 MP 구독 대상인가요?"
    ) == ("미래에셋증권",)
    assert general_query_anchors(
        "병원비는 현금으로 내고 IRP는 미리 쓰지 않겠습니다."
    ) == ()


def test_unavailable_personal_and_internal_access_is_explicitly_scoped_out() -> None:
    assert unsupported_scope_reasons(
        "내 IRP 계좌의 오늘 실시간 잔고와 이번 달 입금 내역을 알려주세요."
    ) == ("private_account_live_access_unavailable",)
    assert unsupported_scope_reasons(
        "제 국민연금 예상수령액을 조회해 주세요."
    ) == ("government_pension_personal_lookup_unavailable",)
    assert unsupported_scope_reasons(
        "고객인데 IRP 입금취소 전산 메뉴와 처리 코드를 알려주세요."
    ) == ("employee_terminal_detail_unavailable_to_customer",)
    assert unsupported_scope_reasons(
        "배우자의 IRP 계좌 현재 잔고를 조회해 주세요."
    ) == ("private_account_live_access_unavailable",)
    assert unsupported_scope_reasons(
        "가족 명의 개인형퇴직연금 계좌의 지금 잔액을 확인해 주세요."
    ) == ("private_account_live_access_unavailable",)
    assert unsupported_scope_reasons(
        "IRP 계좌의 이번 달 납입 내역을 조회해 주세요."
    ) == ("private_account_live_access_unavailable",)


def test_public_rule_explanations_are_not_mistaken_for_live_account_access() -> None:
    assert unsupported_scope_reasons("IRP 중도인출 사유를 설명해 주세요.") == ()
    assert unsupported_scope_reasons(
        "증권사 업무담당자입니다. IRP 입금취소 전산 메뉴를 알려주세요."
    ) == ()
    assert unsupported_scope_reasons(
        "국민연금 예상 수령액은 일반적으로 어떻게 계산되나요?"
    ) == ()
    assert unsupported_scope_reasons(
        "제 국민연금 예상 수령액 계산 원리만 설명해 주세요. 조회는 하지 마세요."
    ) == ()
    assert unsupported_scope_reasons(
        "고객인데 직원용 전산 메뉴나 처리 코드는 말고 공개된 개인IRP "
        "입금취소 절차만 설명해 주세요."
    ) == ()
    assert unsupported_scope_reasons(
        "KR510902511M의 2024년 말 기간말 잔고를 설명해 주세요."
    ) == ()


def test_live_dc_account_lookup_and_execution_are_scoped_out() -> None:
    reasons = unsupported_scope_reasons(
        "내 DC 계좌의 오늘 수익률과 현재 잔액을 조회하고 상품도 변경해 주세요."
    )
    assert reasons == (
        "private_account_live_access_unavailable",
        "account_transaction_execution_unavailable",
    )


def test_decimal_percentage_is_not_split_as_a_sentence_boundary() -> None:
    subquestions = decompose_question(
        "총급여 5천만원이면 16.5% 세액공제되나요? 한도도 알려주세요."
    )
    assert "16.5%" in subquestions[0].text
    assert all(item.text != "5% 세액공제되나요" for item in subquestions)


def test_adjacent_embedded_questions_are_split_without_punctuation() -> None:
    subquestions = decompose_question(
        "DB와 DC의 차이가 무엇인지 IRP 세액공제 한도는 얼마인지 "
        "중도인출은 언제 가능한지 알려주세요"
    )
    assert len(subquestions) == 3
    assert "DB와 DC의 차이" in subquestions[0].text
    assert "세액공제 한도" in subquestions[1].text
    assert "중도인출" in subquestions[2].text


def test_inflected_pension_commencement_phrase_gets_boundary_anchor() -> None:
    anchors = general_query_anchors(
        "IRP 가입 3년이고 만 55세가 되면 연금을 개시할 수 있나요?"
    )
    assert (
        "만 55세 이상 가입자 가입기간 5년 이상 퇴직금이 입금된 "
        "연금계좌 가입기간 5년 이상 요건 미적용"
        in anchors
    )
    plan = plan_question(
        "IRP는 가입한 지 3년이고 만 55세가 되면 연금을 개시할 수 있나요? "
        "퇴직금이 들어온 계좌도 같은 조건인가요?",
        classify_question("IRP 연금 개시 조건"),
    )
    assert plan.subquestions[1].inherited_terms == ("연금개시",)
    assert (
        "만 55세 이상 가입자 가입기간 5년 이상 퇴직금이 입금된 "
        "연금계좌 가입기간 5년 이상 요건 미적용"
        in plan.subquestions[1].query_anchors
    )


def test_official_domain_concepts_normalize_paraphrases_for_retrieval_only() -> None:
    cases = (
        (
            "퇴직연금 규약을 직원에게 불리하지 않게 바꿀 때 직원 대표 의견을 들어야 하나요?",
            "근로자퇴직급여 보장법 제4조 사용자가 설정된 퇴직급여제도 내용 변경 근로자대표 의견청취 의견을 들어야",
        ),
        (
            "개인형 IRP에 넣은 가입자 부담금을 당일 되돌릴 수 있나요?",
            "개인IRP 가입자부담금 입금취소",
        ),
        (
            "연금은 개시했지만 돈을 안 뺀 해도 실제 수령 연차가 올라가나요?",
            "연금실제수령연차 실제 인출",
        ),
        (
            "개인 IRP 재난 중도인출 때 필요한 증빙은 무엇인가요?",
            "재난 중도인출 개인IRP",
        ),
        (
            "확정기여형에서 타깃데이트펀드 하나로 적립금을 전부 굴려도 되나요?",
            "TDF DC 100%",
        ),
        (
            "개인형 퇴직연금 고객은 MP 구독을 신청할 수 있나요?",
            "MP 구독 서비스 개인형IRP",
        ),
        (
            "퇴직연금 ETF, ETN, 리츠는 시간외에도 거래되나요?",
            "ETF ETN 상장 리츠 정규장 시간외 거래",
        ),
        (
            "임원 퇴직금이 세법상 한도를 넘으면 IRP로 옮겨 세금을 미룰 수 있나요?",
            "임원퇴직소득 한도 한도 초과분 IRP 과세이연",
        ),
        (
            "연금 계좌 투자수익을 받으면 이자소득 대신 연금소득으로 보나요?",
            "연금계좌 운용수익 수령 방식 소득 구분",
        ),
        (
            "확정기여형 가입자가 개인회생 결정 뒤 중도인출할 수 있나요?",
            "DC 개인회생절차개시결정 중도인출",
        ),
    )
    for question, expected_anchor in cases:
        anchors = general_query_anchors(question)
        assert expected_anchor in anchors, (question, anchors)


def test_domain_anchor_controls_do_not_expand_broader_near_matches() -> None:
    assert "개인IRP 가입자부담금 입금취소" not in general_query_anchors(
        "IRP 입금 취소 절차를 알려주세요."
    )
    assert "TDF DC 100%" not in general_query_anchors(
        "DC에서 타깃데이트펀드의 특징을 설명해 주세요."
    )
    assert general_query_anchors(
        "퇴직연금이 아닌 일반 주식계좌에서 ETF, ETN, 상장 리츠를 "
        "시간외에 거래할 수 있나요?"
    ) == ()


def test_leading_audience_context_is_joined_to_the_following_request() -> None:
    question = (
        "증권사 퇴직연금 업무담당자입니다. "
        "개인형 IRP 가입자 부담금 입금취소를 확인해 주세요."
    )
    plan = plan_question(question, classify_question(question))
    assert len(plan.subquestions) == 1
    assert plan.subquestions[0].scope.audience == "operations_staff"
    assert plan.subquestions[0].text.startswith("증권사 퇴직연금 업무담당자입니다 ")


def test_natural_role_description_is_joined_to_following_request() -> None:
    cases = (
        (
            "저는 회사에서 퇴직연금 제도를 담당합니다. 규약 변경 절차를 알려주세요.",
            "employer_admin",
        ),
        (
            "증권사에서 IRP 입금 업무를 맡고 있습니다. "
            "가입자부담금 취소 기준을 알려주세요.",
            "operations_staff",
        ),
    )
    for question, expected_audience in cases:
        plan = plan_question(question, classify_question(question))
        assert len(plan.subquestions) == 1
        assert plan.subquestions[0].scope.audience == expected_audience


def test_directional_concepts_use_three_way_anchors_without_opposite_defaults() -> None:
    cases = (
        (
            "퇴직연금 규약을 바꿔 가입자 혜택을 축소할 때 근로자대표 절차는?",
            "불리하게 변경",
            "의견청취 의견을 들어야",
        ),
        (
            "임원 퇴직금 중 한도를 넘지 않는 금액은 IRP 과세이연이 되나요?",
            "한도 이내",
            "한도 초과분",
        ),
        (
            "연금계좌 운용수익을 일시금으로 찾으면 기타소득인가요?",
            "연금외수령 기타소득",
            "연금수령 연금소득",
        ),
    )
    for question, required, forbidden in cases:
        rendered = " ".join(general_query_anchors(question))
        assert required in rendered
        assert forbidden not in rendered

    assert general_query_anchors(
        "임원 퇴직금 한도와 IRP 과세이연 적용을 설명해 주세요."
    ) == ("임원퇴직소득 한도 IRP 과세이연",)
    assert general_query_anchors(
        "연금계좌 운용수익을 연금 외로 받지는 않을 때 소득 분류는?"
    ) == ("연금계좌 운용수익 수령 방식 소득 구분",)


def test_single_listed_pension_product_receives_hours_anchor() -> None:
    assert general_query_anchors("퇴직연금 ETF는 시간외에도 거래되나요?") == (
        "ETF 정규장 시간외 거래",
    )
    assert general_query_anchors("IRP 상장 리츠는 몇 시까지 팔 수 있나요?") == (
        "상장 리츠 정규장 시간외 거래",
    )


def test_contrast_negation_selects_rehabilitation_not_workout() -> None:
    anchors = general_query_anchors(
        "DC 가입자가 개인워크아웃이 아니라 법원 개인회생 개시 결정을 받고 "
        "중도인출하려 합니다."
    )
    assert anchors == ("DC 개인회생절차개시결정 중도인출",)


def test_anaphoric_followup_keeps_the_declared_case_facts_in_one_query() -> None:
    question = (
        "DC 가입자가 개인회생 개시 결정을 받았고 절차가 진행 중입니다. "
        "이 사유로 중도인출할 수 있나요?"
    )
    plan = plan_question(question, classify_question(question))
    assert len(plan.subquestions) == 1
    assert plan.subquestions[0].text.startswith("DC 가입자가")
    assert "DC 개인회생절차개시결정 중도인출" in plan.subquestions[0].query_anchors


def test_query_anchor_does_not_invent_route_or_evidence_topic() -> None:
    question = "ISA 끝난 돈을 연금계좌에 넣으려면 언제까지 해야 하나요?"
    plan = plan_question(question, classify_question(question))
    subquestion = plan.subquestions[0]
    assert subquestion.query_anchors == (
        "ISA 만기 연금계좌 전환입금 60일 추가 세액공제",
    )
    assert "tax" not in subquestion.route.labels
    assert all("세액공제" not in item.topic_terms for item in plan.requirements)


def test_retirement_education_phrase_maps_to_official_topic_term() -> None:
    question = "회사가 해줘야 하는 퇴직연금 교육은 몇 번인가요?"
    route = classify_question(question)
    plan = plan_question(question, route)

    assert plan.requirements[0].topic_terms == ("가입자교육",)


def test_adversarial_colloquial_queries_receive_narrow_official_anchors() -> None:
    cases = {
        "DC 회사 적립금이 제 날짜에 안 들어오면 어떻게 대응하나요?": (
            "DC 부담금 납입 지연 지연이자",
            ("지연이자",),
        ),
        "주거용 집 마련에 IRP 일부를 당겨 받아도 되나요?": (
            "중도인출 무주택 주택 구입 증빙",
            ("중도인출", "주택 구입"),
        ),
        "치료비 마련하려 퇴직연금 일부를 당겨 받을 수 있나요?": (
            "중도인출 6개월 이상 요양 의료비 증빙",
            ("중도인출", "요양"),
        ),
        "ISA 만기 대금을 개인형퇴직연금 쪽으로 넘기는 기한이 있나요?": (
            "ISA 만기 연금계좌 전환입금 60일 추가 세액공제",
            ("isa",),
        ),
        "IRP에서 몽땅 수령하면 세금 처리가 달라지나요?": (
            "연금수령한도 연금외수령",
            ("연금수령한도",),
        ),
        "사업주가 폐업해 퇴직급여가 밀리면 금융사에 바로 요구할 수 있나요?": (
            "퇴직급여 직접청구 도산 지급누락 증빙",
            ("직접청구",),
        ),
    }

    for question, (anchor, expected_topics) in cases.items():
        plan = plan_question(question, classify_question(question))
        assert plan.subquestions[0].query_anchors == (anchor,)
        assert all(
            set(expected_topics).issubset(requirement.topic_terms)
            for requirement in plan.requirements
            if requirement.kind == "general_rule"
        )


def test_explicitly_declined_scenarios_never_inject_target_anchors() -> None:
    questions = (
        "IRP를 타사로 옮기는 일은 안 하려고요. 세액공제만 설명해 주세요.",
        "병원비에 IRP를 쓰는 건 안 할 거예요. 계좌 유지 방법만 알려주세요.",
        "ISA 만기금을 연금계좌로 전환할 의향은 없습니다. 다른 선택지만 알려주세요.",
        "회사 도산은 피해야죠. 퇴직급여 제도의 큰 흐름만 설명해 주세요.",
    )

    assert all(general_query_anchors(question) == () for question in questions)


def test_bare_holding_noun_lookup_is_out_of_scope() -> None:
    """A holding request needs no time word to be a private-data lookup.

    The blocked phrases used to assume the question dated the holding
    ("현재 잔고", "지금 잔액"). "제 IRP 계좌 잔고를 조회해 주세요" carries no
    such word and reached generation, which is the one request this guard
    exists to stop.
    """

    for question in (
        "제 IRP 계좌 잔고를 조회해 주세요.",
        "내 연금계좌 잔액 알려줘",
        "배우자 IRP 계좌 평가금액을 확인해 주세요",
        "제 퇴직연금 계좌 적립금액 보여주세요",
    ):
        plan = plan_question(question, classify_question(question))
        assert plan.unsupported_scope_reasons == (
            "private_account_live_access_unavailable",
        ), question


def test_asking_how_to_check_a_holding_stays_answerable() -> None:
    """Explaining where to look is a public procedure, not a lookup."""

    for question in (
        "IRP 계좌 잔고는 어디서 조회하나요?",
        "퇴직연금 계좌 잔액 확인 방법을 알려주세요",
        "연금계좌 적립금은 어떻게 운용되나요?",
        "연금계좌 평가금액은 무엇을 기준으로 산정되나요?",
    ):
        plan = plan_question(question, classify_question(question))
        assert plan.unsupported_scope_reasons == (), question


def test_execution_request_on_a_pension_savings_account_is_blocked() -> None:
    """연금저축 names an account as much as "연금 계좌" does."""

    question = "연금저축을 다른 상품으로 갈아타 주세요"
    plan = plan_question(question, classify_question(question))
    assert plan.unsupported_scope_reasons == (
        "account_transaction_execution_unavailable",
    )


def test_personal_national_pension_amount_without_the_noun_is_blocked() -> None:
    """Asking "얼마 받는지" requests the same private figure as 예상수령액."""

    question = "내 국민연금 얼마 받는지 확인해줘"
    plan = plan_question(question, classify_question(question))
    assert plan.unsupported_scope_reasons == (
        "government_pension_personal_lookup_unavailable",
    )


def test_internal_terms_are_caught_whether_or_not_they_are_spaced() -> None:
    """Korean writers split these compounds freely; both spellings are one term."""

    for question in (
        "퇴직연금 처리하는 직원용 화면코드 알려주세요",
        "직원만 쓰는 처리코드가 뭔가요",
        "내부 전산 메뉴 번호를 알려주세요",
    ):
        plan = plan_question(question, classify_question(question))
        assert plan.unsupported_scope_reasons == (
            "employee_terminal_detail_unavailable_to_customer",
        ), question


def test_declining_an_internal_term_politely_still_counts_as_declining() -> None:
    """"알려주지 마시고" refuses the detail exactly as "말고" does."""

    for question in (
        "직원용 화면코드는 알려주지 마시고 공개된 절차만 알려주세요",
        "전산 메뉴는 알려주지 마세요. 고객이 할 수 있는 절차만 알려주세요",
    ):
        plan = plan_question(question, classify_question(question))
        assert plan.unsupported_scope_reasons == (), question


def test_phonetic_acronym_reaches_the_corpus_vocabulary() -> None:
    """A question spelled phonetically must still find the acronym's evidence.

    The corpus writes IRP as an acronym and never as "아이알피", so a short
    phonetic question shared no vocabulary with the evidence and retrieved
    nothing at all. The acronym is the organizer's own wording, so supplying
    it is retrieval vocabulary rather than an added fact.
    """

    anchors = general_query_anchors("아이알피 중도인출 되나요")
    assert any("IRP" in anchor for anchor in anchors)

    for phonetic, expected in (
        ("디시형 부담금은 얼마인가요", "DC"),
        ("디비형 급여는 어떻게 계산되나요", "DB"),
        ("티디에프 상품을 알려주세요", "TDF"),
    ):
        assert any(
            expected in anchor for anchor in general_query_anchors(phonetic)
        ), phonetic


def test_phonetic_anchors_do_not_fire_without_the_spelling() -> None:
    """The anchor is keyed to the spelling, not to the topic."""

    assert not any(
        "IRP" in anchor
        for anchor in general_query_anchors("퇴직급여 직접청구 절차를 알려주세요")
    )
