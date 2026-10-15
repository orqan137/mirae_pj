from __future__ import annotations

from pathlib import Path

import pytest

from scripts.prepare_knowledge import (
    ExtractedDocument,
    extract_one,
    extract_pdf,
    extract_xlsx,
    load_general_document_titles,
    output_location,
    title_hint,
    validate_general_document_sources,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "docs" / "official" / "extracted" / "data"
GENERAL_CATALOG = PROJECT_ROOT / "docs" / "official" / "GENERAL_DOCUMENT_CATALOG.md"


@pytest.mark.skipif(not SOURCE_ROOT.exists(), reason="organizer source data is not present")
def test_pdf_extraction_preserves_every_page_marker() -> None:
    result = extract_pdf(
        SOURCE_ROOT / "일반문서" / "doc7.pdf",
        enable_ocr=False,
        min_chars_per_page=120,
        ocr_dpi=220,
        ocr_workers=1,
    )
    assert result.page_count == 21
    assert result.text.count("[PAGE ") == 21
    assert "[PAGE 1]" in result.text
    assert "[PAGE 21]" in result.text


@pytest.mark.skipif(not SOURCE_ROOT.exists(), reason="organizer source data is not present")
def test_xlsx_styled_date_is_not_left_as_excel_serial() -> None:
    text = extract_xlsx(SOURCE_ROOT / "일반문서" / "doc29.xlsx")
    assert "2026-03-13" in text
    assert "작성 기준일 46094" not in text
    assert "[ROW " in text


def test_title_hint_skips_locator_and_ocr_noise() -> None:
    text = "[PAGE 1]\nx 그 x oI xn Ee\n[업무매뉴얼] 연금저축 세액공제 안내\n본문"
    assert title_hint(text, "doc") == "[업무매뉴얼] 연금저축 세액공제 안내"


def test_general_catalog_has_exactly_all_58_authoritative_titles() -> None:
    titles = load_general_document_titles(GENERAL_CATALOG)

    assert len(titles) == 58
    assert titles["doc1.pdf"] == "개인연금저축·연금저축계좌 개요와 주요 특징"
    assert titles["doc58.pdf"] == "퇴직연금 적립금 100%·70% 투자 가능 운용방법"


@pytest.mark.skipif(not SOURCE_ROOT.exists(), reason="organizer source data is not present")
def test_delivered_general_sources_exactly_match_catalog() -> None:
    titles = load_general_document_titles(GENERAL_CATALOG)
    paths = sorted((SOURCE_ROOT / "일반문서").iterdir())

    validate_general_document_sources(paths, titles)


def test_general_catalog_rejects_duplicate_and_missing_ids(tmp_path: Path) -> None:
    lines = [f"- `doc{number}.pdf` — 제목 {number} — 설명" for number in range(1, 59)]
    lines[-1] = lines[0]
    catalog = tmp_path / "catalog.md"
    catalog.write_text("\n".join(lines), encoding="utf-8")

    with pytest.raises(ValueError, match="Duplicate general-document catalog entry"):
        load_general_document_titles(catalog)

    catalog.write_text("\n".join(lines[:-1]), encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid general-document catalog"):
        load_general_document_titles(catalog)


def test_general_sources_must_match_catalog_extensions(tmp_path: Path) -> None:
    titles = {f"doc{number}.pdf": f"제목 {number}" for number in range(1, 59)}
    paths = [tmp_path / filename for filename in titles]
    paths[-1] = tmp_path / "doc58.docx"

    with pytest.raises(ValueError, match="do not match the title catalog"):
        validate_general_document_sources(paths, titles)


def test_extract_one_uses_catalog_title_only_for_general_documents(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    output_root = tmp_path / "output"
    general = input_root / "일반문서" / "doc1.pdf"
    product_code = "KR1234567890"
    product = input_root / "투자설명서" / product_code / "R2_product.pdf"
    general.parent.mkdir(parents=True)
    product.parent.mkdir(parents=True)
    general.write_bytes(b"general source")
    product.write_bytes(b"product source")

    general_entry = extract_one(
        general,
        input_root=input_root,
        output_root=output_root,
        enable_ocr=False,
        min_chars_per_page=120,
        ocr_dpi=220,
        ocr_workers=1,
        source_digest="a" * 64,
        product_aliases=(),
        extracted=ExtractedDocument("일반문서 OCR 잡음 제목", "fixture", 1),
        general_title_hint="카탈로그의 권위 있는 제목",
    )
    product_entry = extract_one(
        product,
        input_root=input_root,
        output_root=output_root,
        enable_ocr=False,
        min_chars_per_page=120,
        ocr_dpi=220,
        ocr_workers=1,
        source_digest="b" * 64,
        product_aliases=(product_code,),
        extracted=ExtractedDocument("미래에셋 연금 투자 펀드 안내\n본문", "fixture", 1),
    )

    assert general_entry.title_hint == "카탈로그의 권위 있는 제목"
    assert product_entry.title_hint == "미래에셋 연금 투자 펀드 안내"


def test_output_location_rejects_unknown_organizer_layout(tmp_path: Path) -> None:
    path = tmp_path / "다른자료" / "doc1.pdf"
    path.parent.mkdir()
    path.touch()
    with pytest.raises(ValueError, match="Unexpected organizer-data path"):
        output_location(path, tmp_path)
