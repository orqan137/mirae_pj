from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from app.product_facts import AXIS_TERMS, EvidenceBlock, ProductEvidenceIndex, ProductEvidenceRecord
from app.product_values import (
    common_aligned_values,
    normalize_date,
    parse_product_axis_values,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _block(axis: str, raw: str, *, page: int, classes: tuple[str, ...]) -> EvidenceBlock:
    return EvidenceBlock(
        axis=axis,
        page=page,
        raw_text=raw,
        dates=(),
        units=("%",) if axis in {"fees", "performance"} else ("백만",),
        class_labels=classes,
        status="raw_evidence_not_normalized",
    )


def _record(code: str, *, fee: str = "0.31") -> ProductEvidenceRecord:
    evidence: dict[str, tuple[EvidenceBlock, ...]] = {
        axis: () for axis in AXIS_TERMS
    }
    evidence["fees"] = (
        _block(
            "fees",
            f"""투자자가 부담하는 수수료 및 총보수 (단위: %)
클래스 종류 판매수수료 총보수 판매보수 동종유형 총보수·비용 1년 2년 3년 5년 10년
수수료미징구-오프라인-개인연금(C-P) - {fee} 0.20 - 0.33 34 71 109 191 424""",
            page=3,
            classes=("C-P",),
        ),
    )
    evidence["performance"] = (
        _block(
            "performance",
            """3. 집합투자기구의 운용실적 (세전기준)
가. 연평균수익률(단위:%)
연도 최근 1년 최근 2년 최근 3년 최근 5년 설정이후
24.01.01~ 23.01.01~ 22.01.01~ 20.01.01~ 설정일~
24.12.31 24.12.31 24.12.31 24.12.31 24.12.31
종류C-P
4.10 3.20 2.30 1.40 1.00""",
            page=40,
            classes=("C-P",),
        ),
    )
    evidence["balance"] = (
        _block(
            "balance",
            """2. 연도별 설정 및 환매현황(단위:백만)
종류C-P
기간초잔고 설정(발생) 환매 기간말잔고
기간 좌수 금액 좌수 금액 좌수 금액 좌수 금액
'23.12.23~'
100 120 30 35 20 25 110 130
24.12.22""",
            page=39,
            classes=("C-P",),
        ),
    )
    return ProductEvidenceRecord(
        product_code=code,
        canonical_source_id=code,
        aliases=(code,),
        source_path=f"투자설명서/{code}/R2.pdf",
        source_sha256="a" * 64,
        title=f"테스트 상품 {code}",
        basis_dates=("2025년 01월 17일",),
        evidence=evidence,
    )


def _index(*records: ProductEvidenceRecord) -> ProductEvidenceIndex:
    return ProductEvidenceIndex({record.product_code: record for record in records})


def test_date_normalization_rejects_false_table_date() -> None:
    assert normalize_date("2025년 1월 7일") == "2025-01-07"
    assert normalize_date("24.12.31") == "2024-12-31"
    assert normalize_date("23 - 0.34") is None


def test_fee_parser_separates_total_fee_and_total_expense() -> None:
    result = parse_product_axis_values(
        index=_index(_record("KRTEST000001")),
        product_code="KRTEST000001",
        axis="fees",
        class_label="c-p",
    )
    assert result.status == "parsed"
    assert {(item.metric, item.value) for item in result.values} == {
        ("annual_total_fee_rate", Decimal("0.31")),
        ("annual_total_expense_rate", Decimal("0.33")),
    }
    assert {item.as_of_date for item in result.values} == {"2025-01-17"}


def test_fee_parser_preserves_same_key_conflict_and_both_sources() -> None:
    record = _record("KRTEST000001")
    evidence = dict(record.evidence)
    evidence["fees"] = (
        *evidence["fees"],
        _block(
            "fees",
            "Class C-P 총보수 0.32%",
            page=4,
            classes=("C-P",),
        ),
    )
    facts = _index(replace(record, evidence=evidence))
    result = parse_product_axis_values(
        index=facts,
        product_code=record.product_code,
        axis="fees",
        class_label="C-P",
    )
    assert result.status == "conflict"
    assert result.values == ()
    assert len(result.conflicts) == 1
    conflict = result.conflicts[0]
    assert {item.value for item in conflict.values} == {
        Decimal("0.31"),
        Decimal("0.32"),
    }
    assert set(conflict.evidence_refs) == {
        "투자설명서/KRTEST000001/R2.pdf:page=3",
        "투자설명서/KRTEST000001/R2.pdf:page=4",
    }


def test_same_page_multiple_candidates_remain_ambiguous_not_conflicting() -> None:
    record = _record("KRTEST000001")
    evidence = dict(record.evidence)
    evidence["fees"] = (
        *evidence["fees"],
        _block(
            "fees",
            "Class C-P 총보수 0.32%",
            page=3,
            classes=("C-P",),
        ),
    )
    result = parse_product_axis_values(
        index=_index(replace(record, evidence=evidence)),
        product_code=record.product_code,
        axis="fees",
        class_label="C-P",
    )
    assert result.status == "ambiguous"
    assert result.conflicts
    assert result.conflicts[0].independently_sourced is False


def test_performance_parser_keeps_period_semantics_and_tax_basis() -> None:
    result = parse_product_axis_values(
        index=_index(_record("KRTEST000001")),
        product_code="KRTEST000001",
        axis="performance",
        class_label="C-P",
    )
    assert result.status == "parsed"
    one_year = next(item for item in result.values if item.period_years == 1)
    assert one_year.value == Decimal("4.10")
    assert one_year.as_of_date == "2024-12-31"
    assert one_year.aggregation == "annualized"
    assert one_year.tax_basis == "pre_tax"


def test_balance_parser_keeps_units_and_amount_separate() -> None:
    result = parse_product_axis_values(
        index=_index(_record("KRTEST000001")),
        product_code="KRTEST000001",
        axis="balance",
        class_label="C-P",
    )
    assert result.status == "parsed"
    assert {(item.metric, item.value, item.unit) for item in result.values} == {
        ("period_end_balance_units", Decimal("110"), "million_units"),
        ("period_end_balance_amount", Decimal("130"), "million_won"),
    }
    assert {item.as_of_date for item in result.values} == {"2024-12-22"}


def test_common_values_require_same_metric_unit_class_and_date() -> None:
    first = _record("KRTEST000001", fee="0.31")
    second = _record("KRTEST000002", fee="0.25")
    facts = _index(first, second)
    results = tuple(
        parse_product_axis_values(
            index=facts,
            product_code=code,
            axis="fees",
            class_label="C-P",
        )
        for code in (first.product_code, second.product_code)
    )
    aligned = common_aligned_values(results)
    assert len(aligned) == 4
    assert {item.product_code for item in aligned} == {
        "KRTEST000001",
        "KRTEST000002",
    }


def test_real_corpus_parses_known_fee_performance_and_balance_rows() -> None:
    facts = ProductEvidenceIndex.from_path(
        PROJECT_ROOT / "data" / "structured" / "product_evidence.json",
        expected_record_count=100,
    )
    fee = parse_product_axis_values(
        index=facts,
        product_code="KR5110501016",
        axis="fees",
        class_label="C-P",
    )
    performance = parse_product_axis_values(
        index=facts,
        product_code="KR5110501016",
        axis="performance",
        class_label="C-P",
    )
    balance = parse_product_axis_values(
        index=facts,
        product_code="KR5110501016",
        axis="balance",
        class_label="C-P",
    )
    assert fee.status == performance.status == balance.status == "parsed"
    assert ("annual_total_fee_rate", Decimal("0.33")) in {
        (item.metric, item.value) for item in fee.values
    }
    assert ("annualized_return_1y", Decimal("2.95")) in {
        (item.metric, item.value) for item in performance.values
    }
    assert ("period_end_balance_amount", Decimal("67")) in {
        (item.metric, item.value) for item in balance.values
    }
