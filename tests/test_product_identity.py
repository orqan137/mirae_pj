from __future__ import annotations

import hashlib
from pathlib import Path

from app.product_facts import EvidenceBlock, ProductEvidenceIndex, ProductEvidenceRecord
from app.product_identity import ProductIdentityResolver, official_product_name


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def record(
    code: str,
    name: str,
    *,
    canonical: str | None = None,
    aliases: tuple[str, ...] | None = None,
    multiline: bool = False,
) -> ProductEvidenceRecord:
    canonical_id = canonical or code
    alias_values = aliases or (code,)
    raw = (
        f"1. 집합투자기구의 명칭\n    {name}\n집합투자기구 명칭\n(펀드코드: TEST)"
        if multiline
        else f"1. 집합투자기구 명칭    {name}\n2. 집합투자업자 명칭 테스트운용"
    )
    evidence = {
        axis: ()
        for axis in (
            "identity",
            "category",
            "risk",
            "class",
            "fees",
            "performance",
            "balance",
        )
    }
    evidence["identity"] = (
        EvidenceBlock(
            axis="identity",
            page=1,
            raw_text=raw,
            dates=(),
            units=(),
            class_labels=(),
            status="raw_evidence_not_normalized",
        ),
    )
    return ProductEvidenceRecord(
        product_code=code,
        canonical_source_id=canonical_id,
        aliases=alias_values,
        source_path=f"투자설명서/{code}/R2.pdf",
        source_sha256="a" * 64,
        title=f"이 투자설명서는 {name}에 대한 자세한 내용을 담고 있습니다.",
        basis_dates=(),
        evidence=evidence,
    )


def test_official_name_supports_inline_and_multiline_identity_tables() -> None:
    inline = record("KRTEST000001", "미래에셋장기성장포커스증권자투자신탁1호(주식)")
    multiline = record(
        "KRTEST000002",
        "우리 하이플러스채권 증권 자투자신탁 1호(채권)",
        multiline=True,
    )
    assert official_product_name(inline).startswith("미래에셋장기성장포커스")
    assert official_product_name(multiline).startswith("우리 하이플러스채권")


def test_resolver_handles_codes_short_names_and_two_products() -> None:
    resolver = ProductIdentityResolver(
        (
            record(
                "KRTEST000001",
                "미래에셋장기성장포커스증권자투자신탁1호(주식)",
            ),
            record(
                "KRTEST000002",
                "미래에셋프리미엄크레딧알파증권자투자신탁(채권)",
            ),
        )
    )
    resolution = resolver.resolve(
        "장기성장포커스와 프리미엄크레딧알파의 위험등급을 비교해 주세요"
    )
    assert resolution.product_codes == ("KRTEST000001", "KRTEST000002")
    assert resolution.required_conditions(comparison=True) == ()

    by_code = resolver.resolve("KRTEST000002 총보수")
    assert by_code.product_codes == ("KRTEST000002",)
    assert by_code.resolved[0].matched_by == "code"


def test_resolver_recovers_one_edit_in_a_unique_long_product_name() -> None:
    resolver = ProductIdentityResolver(
        (
            record(
                "KRTEST000001",
                "미래에셋장기성장포커스증권자투자신탁1호(주식)",
            ),
            record(
                "KRTEST000002",
                "미래에셋프리미엄크레딧알파증권자투자신탁(채권)",
            ),
        )
    )
    resolution = resolver.resolve(
        "장기성장포커스와 프리미크레딧알파의 총보수를 비교해 주세요"
    )
    assert resolution.product_codes == ("KRTEST000001", "KRTEST000002")
    assert resolution.resolved[1].matched_by.startswith("fuzzy_name:")
    assert resolution.required_conditions(comparison=True) == ()


def test_resolver_does_not_guess_an_ambiguous_one_edit_name() -> None:
    resolver = ProductIdentityResolver(
        (
            record("KRTEST000001", "미래에셋글로벌알파증권자투자신탁1호(주식)"),
            record("KRTEST000002", "미래에셋글로벌알피증권자투자신탁1호(채권)"),
        )
    )
    resolution = resolver.resolve("미래에셋글로벌알프 상품의 위험등급을 알려주세요")
    assert resolution.product_codes == ()
    assert resolution.ambiguous


def test_resolver_preserves_two_explicit_codes_that_share_one_document() -> None:
    aliases = ("KRTEST000001", "KRTEST000002")
    records = tuple(
        record(
            code,
            "한국투자 골드플랜 연금 증권 자투자신탁 1호(채권)",
            canonical=aliases[0],
            aliases=aliases,
        )
        for code in aliases
    )
    resolution = ProductIdentityResolver(records).resolve(
        "KRTEST000001과 KRTEST000002 비교"
    )
    assert resolution.product_codes == aliases
    assert resolution.canonical_ids == (aliases[0],)
    assert resolution.required_conditions(comparison=True) == ()


def test_unknown_code_and_ambiguous_short_name_require_input() -> None:
    resolver = ProductIdentityResolver(
        (
            record("KRTEST000001", "한국투자 골드플랜 연금 증권 투자신탁 1호(채권)"),
            record("KRTEST000002", "한국투자 골드플랜 연금 증권 투자신탁 1호(주식)"),
        )
    )
    unknown = resolver.resolve("KRUNKNOWN001 위험등급")
    assert unknown.unknown_codes == ("KRUNKNOWN001",)
    assert "정확한 상품코드" in unknown.required_conditions(comparison=False)[0]

    ambiguous = resolver.resolve("골드플랜 연금 상품을 설명해 주세요")
    assert ambiguous.ambiguous
    assert ambiguous.required_conditions(comparison=False) == (
        "모호하지 않은 공식 상품명 또는 상품코드",
    )


def test_real_product_index_resolves_every_unique_identity() -> None:
    path = PROJECT_ROOT / "data" / "structured" / "product_evidence.json"
    if not path.is_file():
        return
    index = ProductEvidenceIndex.from_path(path, expected_record_count=100)
    resolver = ProductIdentityResolver.from_index(index)
    assert resolver.identity_count == 92
    first = index.records[0]
    resolution = resolver.resolve(official_product_name(first))
    assert resolution.resolved
    assert resolution.resolved[0].canonical_source_id == first.canonical_source_id
