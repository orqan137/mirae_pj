from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.product_facts import (
    AXIS_TERMS,
    COMPARISON_AXES,
    EXTRACTOR_FINGERPRINT,
    ProductEvidenceError,
    ProductEvidenceIndex,
    requested_axes,
    requested_product_codes,
)
from scripts.extract_product_facts import annotations, extract_axis_evidence, extract_basis_dates


def write_index(path: Path) -> None:
    records = []
    for code, fee in (("KRTEST000001", "0.31%"), ("KRTEST000002", "0.25%")):
        evidence: dict[str, list[dict[str, object]]] = {
            axis: [] for axis in AXIS_TERMS
        }
        evidence.update(
            {
                "risk": [
                    {
                        "page": 2,
                        "raw_text": "투자위험등급 3등급",
                        "dates": ["2025-01-01"],
                        "units": [],
                        "class_labels": [],
                        "status": "raw_evidence_not_normalized",
                    }
                ],
                "fees": [
                    {
                        "page": 4,
                        "raw_text": f"Class C-P 총보수 {fee}",
                        "dates": [],
                        "units": ["연 %"],
                        "class_labels": ["C-P"],
                        "status": "raw_evidence_not_normalized",
                    }
                ],
            }
        )
        records.append(
            {
                "product_code": code,
                "canonical_source_id": code,
                "aliases": [code],
                "source_path": f"투자설명서/{code}/R2.pdf",
                "source_sha256": "a" * 64,
                "title": f"테스트 상품 {code}",
                "basis_dates": ["2025-01-01"],
                "evidence": evidence,
            }
        )
    path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "extractor_fingerprint": EXTRACTOR_FINGERPRINT,
                "corpus_manifest_sha256": "b" * 64,
                "record_count": 2,
                "records": records,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_requested_codes_and_axes_handle_fullwidth() -> None:
    question = "ＫＲＴＥＳＴ０００００１과 KRTEST000002의 위험등급과 총보수 비교"
    assert requested_product_codes(question) == ("KRTEST000001", "KRTEST000002")
    assert {"risk", "fees"} <= set(requested_axes(question))


def test_generic_product_comparison_requests_all_six_required_axes() -> None:
    assert requested_axes("두 연금상품의 차이를 비교해 주세요") == COMPARISON_AXES


def test_specific_comparison_does_not_add_unrequested_axes() -> None:
    assert requested_axes("두 상품의 총보수만 비교") == ("fees",)


def test_context_interleaves_products_by_axis(tmp_path: Path) -> None:
    path = tmp_path / "facts.json"
    write_index(path)
    index = ProductEvidenceIndex.from_path(path)
    context = index.render_context(
        product_codes=("KRTEST000001", "KRTEST000002"),
        axes=("risk", "fees"),
        max_chars=10_000,
    )
    sections = context.split("\n\n")
    assert "product_code=KRTEST000001 axis=risk" in sections[0]
    assert "product_code=KRTEST000002 axis=risk" in sections[1]
    assert "classes=C-P" in context
    assert "status=raw_evidence_not_normalized" in context


def test_typed_evidence_coverage_excludes_unknown_codes_and_empty_axes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "facts.json"
    write_index(path)
    index = ProductEvidenceIndex.from_path(path)
    assert index.evidence_coverage(
        product_codes=("KRTEST000001", "KRUNKNOWN0001"),
        axes=("risk", "fees", "performance"),
    ) == {
        "KRTEST000001": frozenset({"risk", "fees"}),
    }
    assert index.canonical_identities(
        product_codes=("KRTEST000001", "KRUNKNOWN0001")
    ) == {"KRTEST000001": "KRTEST000001"}


def test_context_budget_keeps_verbatim_evidence_for_both_products(tmp_path: Path) -> None:
    path = tmp_path / "facts.json"
    write_index(path)
    index = ProductEvidenceIndex.from_path(path)
    context = index.render_context(
        product_codes=("KRTEST000001", "KRTEST000002"),
        axes=("fees",),
        max_chars=700,
    )
    assert "product_code=KRTEST000001" in context
    assert "product_code=KRTEST000002" in context
    assert "0.31%" in context
    assert "0.25%" in context
    assert len(context) <= 700


def test_invalid_status_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "facts.json"
    write_index(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["records"][0]["evidence"]["risk"][0]["status"] = "normalized"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ProductEvidenceError, match="invalid required fields"):
        ProductEvidenceIndex.from_path(path)


def test_index_rejects_different_corpus_manifest(tmp_path: Path) -> None:
    path = tmp_path / "facts.json"
    write_index(path)
    with pytest.raises(ProductEvidenceError, match="different corpus"):
        ProductEvidenceIndex.from_path(path, expected_manifest_sha256="c" * 64)


def test_index_rejects_inconsistent_alias_groups(tmp_path: Path) -> None:
    path = tmp_path / "facts.json"
    write_index(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["records"][0]["aliases"] = ["KRTEST000001", "KRTEST000002"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ProductEvidenceError, match="alias group"):
        ProductEvidenceIndex.from_path(path)


def test_index_requires_all_axes_and_extractor_identity(tmp_path: Path) -> None:
    path = tmp_path / "facts.json"
    write_index(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    del payload["records"][0]["evidence"]["balance"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ProductEvidenceError, match="every supported axis"):
        ProductEvidenceIndex.from_path(path)

    write_index(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["extractor_fingerprint"] = "unknown-extractor"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ProductEvidenceError, match="extractor"):
        ProductEvidenceIndex.from_path(path)


def test_extractor_prefers_actual_performance_and_balance_tables() -> None:
    content = """[PAGE 1]
목차
3. 집합투자기구의 운용실적과 수익률
2. 연도별 설정 및 환매 현황
[PAGE 5]
투자실적 추이 (연평균 수익률)
[단위 : %]
종류(Class) 최근 1년 최근 2년 최근 3년
C-P 2024.02.29 ~ 2025.02.28 5.17 4.90 2.83
[PAGE 30]
2. 연도별 설정 및 환매 현황 (단위:백만원)
기간 회계기간중 재투자 금액
기간초잔고 설정(발행) 환매 기간말잔고
2024.01.01 ~ 2024.12.31 100 20 10 110
"""
    evidence = extract_axis_evidence(content)
    assert evidence["performance"][0]["page"] == 5
    assert "C-P 2024.02.29" in evidence["performance"][0]["raw_text"]
    assert evidence["performance"][0]["dates"] == ["2024.02.29", "2025.02.28"]
    assert evidence["performance"][0]["units"] == ["%"]
    assert evidence["balance"][0]["page"] == 30
    assert "기간말잔고" in evidence["balance"][0]["raw_text"]
    assert evidence["balance"][0]["units"] == ["백만원"]


def test_annotations_preserve_class_date_and_unit_without_value_normalization() -> None:
    raw = "종류(Class) C-RF 2025년 2월 28일 [단위 : %] 총보수 0.5440"
    dates, units, classes = annotations(raw, axis="fees")
    assert dates == ["2025년 2월 28일"]
    assert units == ["%"]
    assert "C-RF" in classes
    assert "0.5440" in raw


def test_basis_date_extraction_requires_explicit_label() -> None:
    content = """[PAGE 1]
효력발생일 2025년 1월 8일
4. 작성 기준일 2025년 01월 01일
[PAGE 3]
(작성기준일 : 2025년 01월 01일)
최근 1년 2024.01.01~2024.12.31
"""
    assert extract_basis_dates(content) == ["2025년 01월 01일"]
