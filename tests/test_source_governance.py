from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.source_governance import (
    SourceGovernanceError,
    analyze_question_scope,
    assess_source_applicability,
    load_source_governance,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY = PROJECT_ROOT / "data" / "structured" / "general_source_governance.json"


def test_registry_covers_every_official_general_document() -> None:
    expected = {f"doc{number}" for number in range(1, 59)}
    registry = load_source_governance(REGISTRY, expected_source_ids=expected)
    assert registry.knowledge_as_of == date(2026, 8, 31)
    assert set(registry.records) == expected
    assert registry.records["doc29"].published_date == date(2026, 3, 13)
    assert registry.records["doc18"].audiences == ("customer", "employer_admin")


def test_registry_rejects_incomplete_expected_coverage() -> None:
    with pytest.raises(SourceGovernanceError, match="coverage mismatch"):
        load_source_governance(REGISTRY, expected_source_ids={"doc1"})


def test_explicit_date_requires_a_known_effective_period() -> None:
    registry = load_source_governance(REGISTRY)
    scope = analyze_question_scope("2026년 8월 30일 IRP 세액공제 한도를 알려주세요")
    decision = assess_source_applicability(
        record=registry.records["doc41"],
        scope=scope,
        question="2026년 8월 30일 IRP 세액공제 한도를 알려주세요",
    )
    assert decision.applicable is False
    assert decision.reason == "effective_date_unknown"


def test_known_effective_interval_accepts_inside_and_rejects_after() -> None:
    registry = load_source_governance(REGISTRY)
    record = registry.records["doc37"]
    inside = assess_source_applicability(
        record=record,
        scope=analyze_question_scope("2026년 연금 인출 가이드"),
        question="2026년 연금 인출 가이드",
    )
    after = assess_source_applicability(
        record=record,
        scope=analyze_question_scope("2027년 연금 인출 가이드"),
        question="2027년 연금 인출 가이드",
    )
    assert inside.applicable is True
    assert after.applicable is False
    assert after.reason == "after_effective_period"


def test_customer_scope_rejects_operations_only_source() -> None:
    registry = load_source_governance(REGISTRY)
    decision = assess_source_applicability(
        record=registry.records["doc6"],
        scope=analyze_question_scope("IRP 입금 취소 절차를 알려주세요"),
        question="IRP 입금 취소 절차를 알려주세요",
    )
    assert decision.applicable is False
    assert decision.reason == "audience_mismatch"


def test_mentioned_actor_is_not_mistaken_for_the_questioner_audience() -> None:
    customer = analyze_question_scope(
        "사업주가 폐업해 퇴직급여가 밀리면 금융사에 바로 요구할 수 있나요?"
    )
    employer = analyze_question_scope(
        "저는 회사 담당자인데 퇴직연금 규약 변경 절차를 알려주세요."
    )
    operations = analyze_question_scope(
        "퇴직연금 사무담당자로서 고객 입금 취소 업무를 확인하고 싶습니다."
    )

    assert customer.audience == "customer"
    assert employer.audience == "employer_admin"
    assert operations.audience == "operations_staff"


def test_polite_self_identification_endings_set_the_declared_audience() -> None:
    assert analyze_question_scope(
        "회사 퇴직연금 담당자예요. 규약 변경 절차가 궁금합니다."
    ).audience == "employer_admin"
    assert analyze_question_scope(
        "증권사 퇴직연금 업무담당자예요. 입금취소를 확인합니다."
    ).audience == "operations_staff"
    assert analyze_question_scope(
        "저는 회사에서 퇴직연금 제도를 담당합니다. 규약을 확인합니다."
    ).audience == "employer_admin"
    assert analyze_question_scope(
        "증권사에서 IRP 입금 업무를 맡고 있습니다. 취소 기준을 확인합니다."
    ).audience == "operations_staff"


def test_historical_premise_with_present_question_is_not_an_explicit_answer_date() -> None:
    scope = analyze_question_scope(
        "2023년에는 연금저축 세액공제 한도가 달랐는데 지금도 그런가요?"
    )

    assert scope.reference_date is None
    assert scope.explicit_date is False
    assert scope.temporal_unresolved is False


def test_customer_identity_and_employee_word_mentions_do_not_escalate_audience() -> None:
    assert analyze_question_scope(
        "가입자인데 개인IRP 직원용 전산 메뉴가 궁금합니다."
    ).audience == "customer"
    assert analyze_question_scope(
        "고객 입장에서 퇴직연금 담당자용 안내를 보고 싶습니다."
    ).audience == "customer"
    assert analyze_question_scope(
        "회사에서 퇴직연금 담당자가 규약을 바꾼다고 안내했습니다."
    ).audience == "customer"


def test_foreign_jurisdiction_does_not_inherit_korean_rule() -> None:
    registry = load_source_governance(REGISTRY)
    decision = assess_source_applicability(
        record=registry.records["doc41"],
        scope=analyze_question_scope("미국의 연금 세액공제를 알려주세요"),
        question="미국의 연금 세액공제를 알려주세요",
    )
    assert decision.applicable is False
    assert decision.reason == "jurisdiction_mismatch"


def test_legacy_product_source_requires_explicit_legacy_scope() -> None:
    registry = load_source_governance(REGISTRY)
    record = registry.records["doc25"]
    generic = assess_source_applicability(
        record=record,
        scope=analyze_question_scope("개인연금 세제 혜택을 알려주세요"),
        question="개인연금 세제 혜택을 알려주세요",
    )
    legacy = assess_source_applicability(
        record=record,
        scope=analyze_question_scope("구개인연금 세제 혜택을 알려주세요"),
        question="구개인연금 세제 혜택을 알려주세요",
    )
    assert generic.applicable is False
    assert generic.reason == "legacy_scope_mismatch"
    assert legacy.applicable is True
    assert legacy.warning is not None
