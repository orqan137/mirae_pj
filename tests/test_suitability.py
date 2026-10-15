from __future__ import annotations

from pathlib import Path

from app.product_facts import ProductEvidenceIndex
from app.suitability import assess_suitability, parse_suitability_profile


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _index() -> ProductEvidenceIndex:
    return ProductEvidenceIndex.from_path(
        PROJECT_ROOT / "data/structured/product_evidence.json",
        expected_record_count=100,
    )


def _aggressive_growth_question() -> str:
    return (
        "IRP 계좌에서 20년 투자하고 손실 위험을 감수할 수 있습니다. "
        "중도인출 계획은 없고 노후자금 성장 목표에 맞는 상품을 추천해 주세요."
    )


def test_profile_uses_only_explicit_five_dimensions() -> None:
    profile = parse_suitability_profile(_aggressive_growth_question())
    assert profile.account == "irp"
    assert profile.horizon == "long"
    assert profile.horizon_years == 20
    assert profile.risk_tolerance == "aggressive"
    assert profile.liquidity == "not_needed"
    assert profile.objective == "growth"
    assert profile.missing_fields == ()
    assert profile.conflicts == ()


def test_incomplete_profile_is_not_filled_with_defaults() -> None:
    result = assess_suitability(
        question="IRP에서 20년 투자할 상품을 추천해 주세요",
        index=_index(),
    )
    assert result.status == "needs_conditions"
    assert "손실 감내 능력과 위험 선호" in result.required_conditions
    assert "유동성 또는 중도인출 계획" in result.required_conditions
    assert result.candidates == ()


def test_db_personal_product_selection_is_not_assumed() -> None:
    result = assess_suitability(
        question=(
            "DB형 계좌에서 20년 투자하고 공격적으로 손실을 감내할 수 있으며 "
            "중도인출 계획은 없고 노후자금 성장 목표의 상품을 추천해 주세요"
        ),
        index=_index(),
    )
    assert result.status == "needs_conditions"
    assert any("개인이 운용상품을 선택" in item for item in result.required_conditions)


def test_liquidity_need_requires_withdrawal_eligibility_before_shortlist() -> None:
    result = assess_suitability(
        question=(
            "IRP에서 20년 투자하고 공격적으로 손실을 감내할 수 있습니다. "
            "긴급자금이 필요하고 노후자금 성장 목표의 상품을 추천해 주세요."
        ),
        index=_index(),
    )
    assert result.status == "needs_conditions"
    assert result.required_conditions == (
        "필요 인출 시점과 법정 중도인출 사유 해당 여부",
    )
    assert result.candidates == ()


def test_withdrawal_timing_is_not_misread_as_a_second_investment_horizon() -> None:
    profile = parse_suitability_profile(
        "IRP 계좌에서 20년 장기 투자로 적극적 성장을 원하지만 "
        "1년 안에 긴급자금으로 중도인출할 계획입니다."
    )

    assert profile.horizon == "long"
    assert profile.horizon_years == 20
    assert profile.risk_tolerance == "aggressive"
    assert profile.liquidity == "needed"
    assert profile.objective == "growth"
    assert profile.conflicts == ()


def test_complete_profile_returns_evidence_bound_candidates_not_a_global_rank() -> None:
    result = assess_suitability(question=_aggressive_growth_question(), index=_index())
    assert result.status == "candidate_shortlist"
    assert 1 <= len(result.candidates) <= 3
    assert result.eligible_products >= len(result.candidates)
    assert len({item.canonical_source_id for item in result.candidates}) == len(
        result.candidates
    )
    assert all(item.category in {"equity", "mixed", "target_date"} for item in result.candidates)
    assert all(1 <= item.risk_grade <= 6 for item in result.candidates)
    assert all(":class:page=" in item.account_citation for item in result.candidates)
    context = result.render_context()
    assert "not a statutory suitability determination" in context
    assert "universal product ranking" in context
    assert any("판매 클래스 매핑" in item for item in result.policy_limits)


def test_conservative_preservation_filter_never_admits_high_risk_or_equity() -> None:
    result = assess_suitability(
        question=(
            "연금저축 계좌에서 10년 투자하고 보수적으로 원금 보전을 중시합니다. "
            "중도인출 계획은 없고 안정적 운용 목표의 상품을 추천해 주세요."
        ),
        index=_index(),
    )
    assert result.status == "candidate_shortlist"
    assert result.candidates
    assert all(item.risk_grade in {5, 6} for item in result.candidates)
    assert all(item.category == "bond" for item in result.candidates)


def test_liquidity_stated_without_a_particle_is_not_inverted() -> None:
    """"중도인출 계획 없음" must not read as needing liquidity.

    The negative phrases all carried a topic particle ("계획은 없", "계획이
    없"), so a bare noun phrase fell through to the positive list, which
    contains the substring "중도인출 계획". The profile then recorded the exact
    opposite of what the customer said, steering the shortlist toward liquid
    products.
    """

    for question in (
        "IRP 장기 투자, 손실 감내 가능, 중도인출 계획 없음, 노후자금 성장",
        "IRP에서 장기 투자하고 인출 계획 없습니다. 노후자금 성장을 원합니다",
    ):
        assert parse_suitability_profile(question).liquidity == "not_needed", question


def test_contradictory_liquidity_statements_are_a_conflict() -> None:
    """Stating both must ask, not silently keep the illiquid reading."""

    profile = parse_suitability_profile(
        "IRP 장기 투자, 중도인출 계획은 없지만 긴급자금이 필요합니다. 노후자금 성장 목표"
    )
    assert profile.liquidity is None
    assert any("유동성" in item for item in profile.conflicts)


def test_declining_liquidity_is_not_read_as_asserting_it() -> None:
    """Every denial contains its own positive form; only the denial counts."""

    profile = parse_suitability_profile(
        "IRP에서 장기 투자하고 손실을 감내할 수 있으며 중도인출 계획은 없고 "
        "노후자금 성장을 원합니다"
    )
    assert profile.liquidity == "not_needed"
    assert profile.conflicts == ()
