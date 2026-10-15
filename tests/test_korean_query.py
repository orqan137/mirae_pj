from __future__ import annotations

import pytest

from app.korean_query import (
    contains_predicate_stem,
    normalize_korean_query,
    strip_final_particle,
)
from app.planning import unsupported_scope_reasons
from app.routing import classify_question


def test_surface_layer_preserves_original_and_builds_controlled_match_forms() -> None:
    question = "  연금 저축으로는 600만원, 아이 알 피에는 300만원을 넣었습니다.  "
    surface = normalize_korean_query(question)

    assert surface.original == question
    assert "연금저축으로는" in surface.normalized
    assert "irp에는" in surface.normalized
    assert " " not in surface.compact


@pytest.mark.parametrize(
    ("token", "stem"),
    [
        ("연금저축에만", "연금저축"),
        ("IRP으로는", "IRP"),
        ("계좌에서는", "계좌"),
        ("가입자에게", "가입자"),
    ],
)
def test_nominal_layer_separates_common_particles(token: str, stem: str) -> None:
    assert strip_final_particle(token) == stem


@pytest.mark.parametrize(
    "phrase",
    (
        "납입했습니다",
        "납입할까요",
        "넣었습니다",
        "적립하려고 합니다",
    ),
)
def test_predicate_layer_matches_inflected_contribution_stems(phrase: str) -> None:
    assert contains_predicate_stem(phrase, ("납입", "넣", "적립")) is True


@pytest.mark.parametrize(
    "question",
    (
        "내 IRP 계좌 잔액 좀 봐줘요.",
        "제 DC 계좌의 현재 수익률을 알려 주실래요?",
        "우리 퇴직연금 계좌 보유 내역 확인 부탁드립니다.",
    ),
)
def test_capability_layer_blocks_synthetic_live_lookup_variants(question: str) -> None:
    assert "private_account_live_access_unavailable" in unsupported_scope_reasons(
        question
    )


@pytest.mark.parametrize(
    "question",
    (
        "내 IRP 상품을 다른 펀드로 갈아타 주세요.",
        "제 DC 계좌의 상품도 변경해 주실래요?",
        "내 퇴직연금 계좌에서 이 상품을 환매해 줘.",
    ),
)
def test_capability_layer_blocks_synthetic_execution_variants(question: str) -> None:
    assert "account_transaction_execution_unavailable" in unsupported_scope_reasons(
        question
    )


def test_public_procedure_question_is_not_blocked_by_execution_stem() -> None:
    assert unsupported_scope_reasons("IRP 상품을 변경하는 방법을 설명해 주세요.") == ()


def test_hangul_irp_and_tax_benefit_paraphrase_reach_tax_route() -> None:
    route = classify_question("연금 저축이랑 아이알피의 세금 혜택 차이가 궁금합니다.")
    assert "tax" in route.labels
    assert "system" in route.labels
