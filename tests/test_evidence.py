from __future__ import annotations

from datetime import date

from app.calculations import calculate_pension_tax_credit
from app.evidence import assess_evidence, render_limitation_answer
from app.planning import plan_question
from app.product_comparison import AxisComparison, ProductComparisonResult
from app.retrieval import RetrievedDocument
from app.routing import classify_question
from app.source_governance import SourceGovernanceRecord


def document(
    *,
    source_id: str,
    group: str,
    text: str,
    axes: frozenset[str] = frozenset(),
    governance: SourceGovernanceRecord | None = None,
) -> RetrievedDocument:
    return RetrievedDocument(
        source=f"{group}/{source_id}.txt",
        source_id=source_id,
        source_path=(
            f"일반문서/{source_id}.docx"
            if group == "general"
            else f"투자설명서/{source_id}/R2.pdf"
        ),
        source_group=group,
        title=source_id,
        index=1,
        page=1,
        text=text,
        score=10.0,
        locator="page=1",
        canonical_source_id=source_id,
        product_aliases=(source_id,) if group == "product" else (),
        axes=axes,
        governance=governance,
    )


def governed(
    source_id: str,
    *,
    audiences: tuple[str, ...] = ("customer",),
    jurisdiction: str = "KR",
    effective_from: date | None = None,
    effective_to: date | None = None,
) -> SourceGovernanceRecord:
    return SourceGovernanceRecord(
        source_id=source_id,
        authority="organizer_evidence",
        jurisdiction=jurisdiction,
        audiences=audiences,
        published_date=None,
        reference_date=None,
        effective_from=effective_from,
        effective_to=effective_to,
        date_precision="day" if effective_from or effective_to else None,
        required_query_terms_any=(),
        knowledge_as_of=date(2026, 8, 30),
    )


def test_general_evidence_plan_is_answerable_when_each_route_is_supported() -> None:
    question = "연금저축과 IRP의 세액공제 차이를 설명해 주세요"
    route = classify_question(question)
    plan = plan_question(question, route)
    assessment = assess_evidence(
        plan=plan,
        route=route,
        documents=[
            document(
                source_id="doc41",
                group="general",
                text="연금저축 및 IRP 계좌의 세액공제 한도와 적용 기준",
            )
        ],
        structured_axes={},
        structured_identities={},
        calculation=None,
    )
    assert assessment.status == "answerable"
    assert assessment.should_generate is True
    assert assessment.missing_requirements == ()


def test_pension_account_health_insurance_evidence_supports_system_and_tax() -> None:
    question = "연금계좌 월배당 ETF의 배당이 건강보험료에 잡히나요?"
    route = classify_question(question)
    assessment = assess_evidence(
        plan=plan_question(question, route),
        route=route,
        documents=[
            document(
                source_id="doc43",
                group="general",
                text=(
                    "연금계좌 월배당 ETF의 분배금은 건강보험료 부과 대상이 아니며 "
                    "연금수령 시 연금소득세가 적용된다."
                ),
            )
        ],
        structured_axes={},
        structured_identities={},
        calculation=None,
    )

    assert assessment.status == "answerable"
    assert all(item.status == "supported" for item in assessment.coverage)


def test_missing_recommendation_profile_stops_before_generation() -> None:
    question = "좋은 연금 상품을 추천해 주세요"
    route = classify_question(question)
    assessment = assess_evidence(
        plan=plan_question(question, route),
        route=route,
        documents=[
            document(
                source_id="KRTEST000001",
                group="product",
                text="상품분류와 위험등급 설명",
                axes=frozenset({"identity", "category", "risk"}),
            )
        ],
        structured_axes={},
        structured_identities={},
        calculation=None,
    )
    assert assessment.status == "needs_conditions"
    assert assessment.should_generate is False
    assert "계좌 유형" in assessment.missing_conditions
    assert "위험 선호" in render_limitation_answer(assessment)


def test_no_relevant_documents_is_out_of_corpus() -> None:
    question = "연금저축 이전 절차를 알려 주세요"
    route = classify_question(question)
    assessment = assess_evidence(
        plan=plan_question(question, route),
        route=route,
        documents=[],
        structured_axes={},
        structured_identities={},
        calculation=None,
    )
    assert assessment.status == "out_of_corpus"
    assert assessment.should_generate is False


def test_unavailable_live_account_access_blocks_generation_even_with_documents() -> None:
    question = "내 IRP 계좌의 오늘 실시간 잔고와 이번 달 입금 내역을 알려주세요."
    route = classify_question(question)
    assessment = assess_evidence(
        plan=plan_question(question, route),
        route=route,
        documents=[
            document(
                source_id="doc-general",
                group="general",
                text="IRP 연금계좌의 부담금 입금과 잔고 관련 일반 안내",
            )
        ],
        structured_axes={},
        structured_identities={},
        calculation=None,
    )
    assert assessment.status == "out_of_scope"
    assert assessment.should_generate is False
    assert (
        "request:private_account_live_access_unavailable"
        in assessment.scope_exclusions
    )
    assert "접속할 수 없습니다" in render_limitation_answer(assessment)


def test_unavailable_external_or_internal_lookup_blocks_generation() -> None:
    for question, reason in (
        (
            "제 국민연금 예상수령액을 조회해 주세요.",
            "government_pension_personal_lookup_unavailable",
        ),
        (
            "고객인데 개인IRP 입금취소 전산 메뉴와 처리 코드를 알려주세요.",
            "employee_terminal_detail_unavailable_to_customer",
        ),
    ):
        route = classify_question(question)
        assessment = assess_evidence(
            plan=plan_question(question, route),
            route=route,
            documents=[
                document(
                    source_id="doc-near-match",
                    group="general",
                    text="퇴직연금 IRP 가입자부담금 입금취소 절차와 연금 수령 안내",
                )
            ],
            structured_axes={},
            structured_identities={},
            calculation=None,
        )
        assert assessment.status == "out_of_scope"
        assert assessment.should_generate is False
        assert f"request:{reason}" in assessment.scope_exclusions


def test_explicit_scope_missing_from_documents_is_out_of_corpus() -> None:
    question = "화성 기지의 연금 계좌 규칙을 알려주세요"
    route = classify_question(question)
    plan = plan_question(question, route)
    assert plan.requirements[0].required_terms == ("화성", "기지")
    assessment = assess_evidence(
        plan=plan,
        route=route,
        documents=[
            document(
                source_id="doc1",
                group="general",
                text="연금저축계좌의 일반 규칙과 가입 절차",
            )
        ],
        structured_axes={},
        structured_identities={},
        calculation=None,
    )
    assert assessment.status == "out_of_corpus"
    assert assessment.should_generate is False


def test_explicit_year_with_unknown_effective_date_is_out_of_corpus() -> None:
    question = "2026년 IRP 세액공제 기준을 알려주세요"
    route = classify_question(question)
    assessment = assess_evidence(
        plan=plan_question(question, route),
        route=route,
        documents=[
            document(
                source_id="doc41",
                group="general",
                text="IRP 세액공제 기준",
                governance=governed("doc41"),
            )
        ],
        structured_axes={},
        structured_identities={},
        calculation=None,
    )
    assert assessment.status == "out_of_corpus"
    assert assessment.should_generate is False
    assert assessment.missing_conditions == ()
    assert "doc41:effective_date_unknown" in assessment.scope_exclusions


def test_expired_rule_is_not_used_for_later_year() -> None:
    question = "2027년 연금 인출 기준을 설명해 주세요"
    route = classify_question(question)
    assessment = assess_evidence(
        plan=plan_question(question, route),
        route=route,
        documents=[
            document(
                source_id="doc37",
                group="general",
                text="연금 인출 기준과 세금",
                governance=governed(
                    "doc37",
                    effective_from=date(2025, 9, 1),
                    effective_to=date(2026, 12, 31),
                ),
            )
        ],
        structured_axes={},
        structured_identities={},
        calculation=None,
    )
    assert assessment.status == "out_of_corpus"
    assert assessment.should_generate is False
    assert "doc37:after_effective_period" in assessment.scope_exclusions


def test_operations_only_rule_is_not_promoted_for_customer_question() -> None:
    question = "IRP 입금 취소 절차를 알려주세요"
    route = classify_question(question)
    assessment = assess_evidence(
        plan=plan_question(question, route),
        route=route,
        documents=[
            document(
                source_id="doc6",
                group="general",
                text="IRP 입금 취소 업무 절차",
                governance=governed("doc6", audiences=("operations_staff",)),
            )
        ],
        structured_axes={},
        structured_identities={},
        calculation=None,
    )
    assert assessment.status == "out_of_corpus"
    assert "doc6:audience_mismatch" in assessment.scope_exclusions


def test_foreign_rule_question_rejects_kr_governance() -> None:
    question = "미국의 IRP 세액공제 기준을 알려주세요"
    route = classify_question(question)
    assessment = assess_evidence(
        plan=plan_question(question, route),
        route=route,
        documents=[
            document(
                source_id="doc41",
                group="general",
                text="IRP 세액공제 기준과 미국 ETF 세금",
                governance=governed("doc41"),
            )
        ],
        structured_axes={},
        structured_identities={},
        calculation=None,
    )
    assert assessment.status == "out_of_corpus"
    assert assessment.should_generate is False
    assert "doc41:jurisdiction_mismatch" in assessment.scope_exclusions


def test_two_product_comparison_reports_missing_axis_as_partial() -> None:
    question = "KRTEST000001과 KRTEST000002의 위험등급과 총보수를 비교해 주세요"
    route = classify_question(question)
    documents = [
        document(
            source_id=code,
            group="product",
            text=f"{code} 투자위험등급 3등급",
            axes=frozenset({"risk"}),
        )
        for code in ("KRTEST000001", "KRTEST000002")
    ]
    assessment = assess_evidence(
        plan=plan_question(question, route),
        route=route,
        documents=documents,
        structured_axes={
            "KRTEST000001": frozenset({"risk", "fees"}),
            "KRTEST000002": frozenset({"risk"}),
        },
        structured_identities={
            "KRTEST000001": "KRTEST000001",
            "KRTEST000002": "KRTEST000002",
        },
        calculation=None,
        comparison=ProductComparisonResult(
            version="test",
            product_codes=("KRTEST000001", "KRTEST000002"),
            requested_classes=(),
            axes=(
                AxisComparison(
                    axis="risk",
                    status="comparable",
                    reason="test",
                    evidence_refs=("risk-1", "risk-2"),
                ),
                AxisComparison(
                    axis="fees",
                    status="missing_evidence",
                    reason="test",
                    evidence_refs=(),
                ),
            ),
        ),
    )
    assert assessment.status == "partially_answerable"
    assert assessment.should_generate is True
    assert assessment.missing_requirements == (
        "상품코드 KRTEST000001의 fees 근거",
        "상품코드 KRTEST000002의 fees 근거",
    )


def test_same_scope_product_value_conflict_stops_generation_with_sources() -> None:
    question = "KRTEST000001과 KRTEST000002의 C-P 총보수를 비교해 주세요"
    route = classify_question(question)
    documents = [
        document(
            source_id=code,
            group="product",
            text="Class C-P 총보수 공식 원문",
            axes=frozenset({"fees"}),
        )
        for code in ("KRTEST000001", "KRTEST000002")
    ]
    comparison = ProductComparisonResult(
        version="test",
        product_codes=("KRTEST000001", "KRTEST000002"),
        requested_classes=("C-P",),
        axes=(
            AxisComparison(
                axis="fees",
                status="value_conflict",
                reason="동일 조건 값 충돌",
                evidence_refs=(
                    "투자설명서/KRTEST000001/R2.pdf:page=3",
                    "투자설명서/KRTEST000001/R2.pdf:page=4",
                ),
            ),
        ),
    )
    assessment = assess_evidence(
        plan=plan_question(question, route),
        route=route,
        documents=documents,
        structured_axes={
            "KRTEST000001": frozenset({"fees"}),
            "KRTEST000002": frozenset({"fees"}),
        },
        structured_identities={
            "KRTEST000001": "KRTEST000001",
            "KRTEST000002": "KRTEST000002",
        },
        calculation=None,
        comparison=comparison,
    )
    assert assessment.status == "conflicting_evidence"
    assert assessment.should_generate is False
    assert assessment.conflicts[0].conflict_id == "comparison:fees"
    assert "page=3" in render_limitation_answer(assessment)


def test_same_page_value_ambiguity_is_not_promoted_to_official_conflict() -> None:
    question = "KRTEST000001과 KRTEST000002의 C-P 총보수를 비교해 주세요"
    route = classify_question(question)
    comparison = ProductComparisonResult(
        version="test",
        product_codes=("KRTEST000001", "KRTEST000002"),
        requested_classes=("C-P",),
        axes=(
            AxisComparison(
                axis="fees",
                status="value_ambiguous",
                reason="같은 페이지 복합 표 후보",
                evidence_refs=("투자설명서/KRTEST000001/R2.pdf:page=3",),
            ),
        ),
    )
    assessment = assess_evidence(
        plan=plan_question(question, route),
        route=route,
        documents=[],
        structured_axes={
            "KRTEST000001": frozenset({"fees"}),
            "KRTEST000002": frozenset({"fees"}),
        },
        structured_identities={
            "KRTEST000001": "KRTEST000001",
            "KRTEST000002": "KRTEST000002",
        },
        calculation=None,
        comparison=comparison,
    )
    assert assessment.status == "out_of_corpus"
    assert assessment.conflicts == ()
    assert assessment.should_generate is False


def test_incomplete_deterministic_calculation_needs_inputs() -> None:
    question = "IRP에 900만원을 납입했습니다. 세액공제액을 계산해 주세요."
    route = classify_question(question)
    calculation = calculate_pension_tax_credit(question)
    assessment = assess_evidence(
        plan=plan_question(question, route),
        route=route,
        documents=[
            document(
                source_id="doc41",
                group="general",
                text="IRP 세액공제 한도와 공제율",
            )
        ],
        structured_axes={},
        structured_identities={},
        calculation=calculation,
    )
    assert assessment.status == "needs_conditions"
    assert assessment.should_generate is False
    assert assessment.missing_conditions == (
        "총급여 또는 종합소득금액 중 하나의 정확한 금액",
    )


def test_ambiguous_product_pronoun_stops_before_generation() -> None:
    first = "KRTEST000001"
    second = "KRTEST000002"
    question = (
        f"{first}과 {second}의 위험등급을 비교해 주세요. "
        "그 상품의 총보수도 알려주세요"
    )
    route = classify_question(question)
    plan = plan_question(
        question,
        route,
        resolved_product_codes=(first, second),
    )
    assessment = assess_evidence(
        plan=plan,
        route=route,
        documents=[
            document(
                source_id=code,
                group="product",
                text="위험등급과 총보수 공식 근거",
                axes=frozenset({"risk", "fees"}),
            )
            for code in (first, second)
        ],
        structured_axes={
            first: frozenset({"risk", "fees"}),
            second: frozenset({"risk", "fees"}),
        },
        structured_identities={first: first, second: second},
        calculation=None,
    )
    assert assessment.should_generate is False
    assert assessment.status == "needs_conditions"
    assert "지칭한 상품의 정확한 공식 상품명 또는 상품코드" in (
        assessment.missing_conditions
    )


def test_alias_product_codes_require_class_mapping_before_comparison() -> None:
    question = "KRTEST000001과 KRTEST000002의 총보수를 비교해 주세요"
    route = classify_question(question)
    documents = [
        document(
            source_id=code,
            group="product",
            text="Class C-P 총보수 0.31%",
            axes=frozenset({"fees"}),
        )
        for code in ("KRTEST000001", "KRTEST000002")
    ]
    assessment = assess_evidence(
        plan=plan_question(question, route),
        route=route,
        documents=documents,
        structured_axes={
            "KRTEST000001": frozenset({"fees"}),
            "KRTEST000002": frozenset({"fees"}),
        },
        structured_identities={
            "KRTEST000001": "KRTEST000001",
            "KRTEST000002": "KRTEST000001",
        },
        calculation=None,
    )
    assert assessment.status == "needs_conditions"
    assert assessment.should_generate is False
    assert assessment.missing_conditions == (
        "비교 상품코드가 같은 투자설명서를 공유하므로 각 코드의 판매 클래스 매핑",
    )
