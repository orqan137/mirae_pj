from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from app.product_comparison import compare_product_facts, requested_class_labels
from app.product_facts import AXIS_TERMS, EvidenceBlock, ProductEvidenceIndex, ProductEvidenceRecord


def block(
    axis: str,
    text: str,
    *,
    dates: tuple[str, ...] = (),
    units: tuple[str, ...] = (),
    classes: tuple[str, ...] = (),
    page: int = 1,
) -> EvidenceBlock:
    return EvidenceBlock(
        axis=axis,
        page=page,
        raw_text=text,
        dates=dates,
        units=units,
        class_labels=classes,
        status="raw_evidence_not_normalized",
    )


def product(
    code: str,
    *,
    basis_date: str = "2025-02-28",
    fee_unit: str = "%",
    fee_class: str = "C-P",
) -> ProductEvidenceRecord:
    evidence: dict[str, tuple[EvidenceBlock, ...]] = {
        axis: () for axis in AXIS_TERMS
    }
    evidence.update(
        {
            "identity": (
                block(
                    "identity",
                    f"집합투자기구 명칭 테스트상품 {code}",
                    dates=(basis_date,),
                ),
            ),
            "risk": (
                block("risk", "투자위험등급 3등급", dates=(basis_date,), page=2),
            ),
            "fees": (
                block(
                    "fees",
                    f"Class {fee_class} 총보수 0.31% [단위:%]",
                    units=(fee_unit,),
                    classes=(fee_class,),
                    page=4,
                ),
            ),
            "performance": (
                block(
                    "performance",
                    f"""3. 집합투자기구의 운용실적 (세전기준)
가. 연평균수익률(단위:%)
최근 1년 최근 2년 최근 3년 최근 5년 설정이후
24.03.01~ 23.03.01~ 22.03.01~ 20.03.01~ 설정일~
25.02.28 25.02.28 25.02.28 25.02.28 25.02.28
Class {fee_class}
4.10 3.20 2.30 1.40 1.00""",
                    dates=(basis_date,),
                    units=("%",),
                    classes=(fee_class,),
                    page=8,
                ),
            ),
        }
    )
    return ProductEvidenceRecord(
        product_code=code,
        canonical_source_id=code,
        aliases=(code,),
        source_path=f"투자설명서/{code}/R2.pdf",
        source_sha256="a" * 64,
        title=f"테스트상품 {code}",
        basis_dates=(basis_date,),
        evidence=evidence,
    )


def index(*records: ProductEvidenceRecord) -> ProductEvidenceIndex:
    return ProductEvidenceIndex({record.product_code: record for record in records})


def test_requested_class_labels_are_nfkc_case_normalized() -> None:
    assert requested_class_labels("Class c-p와 Ｃ-ＰＥ 기준") == ("C-P", "C-PE")
    assert requested_class_labels("A 클래스 기준") == ("A",)


def test_unnamed_class_aligns_on_a_shared_documented_class() -> None:
    """A class the question omits may be recovered from the evidence itself.

    Both prospectuses document C-P, so comparing on C-P keeps the two sides in
    the same sales class without assuming any code-to-class mapping. The class
    used must be reported so the answer can state its basis.
    """

    result = compare_product_facts(
        index=index(product("KRTEST000001"), product("KRTEST000002")),
        product_codes=("KRTEST000001", "KRTEST000002"),
        axes=("fees",),
        question="두 상품 총보수 비교",
    )
    assert result.status_for("fees") == "comparable"
    assert result.required_conditions == ()
    fees = next(item for item in result.axes if item.axis == "fees")
    assert fees.aligned_classes == ("C-P",)


def test_class_dependent_axis_requires_a_class_when_none_is_shared() -> None:
    """No shared class means no sound alignment, so the clarification stands."""

    result = compare_product_facts(
        index=index(
            product("KRTEST000001", fee_class="C-P"),
            product("KRTEST000002", fee_class="A-E"),
        ),
        product_codes=("KRTEST000001", "KRTEST000002"),
        axes=("fees",),
        question="두 상품 총보수 비교",
    )
    assert result.status_for("fees") == "needs_class"
    assert result.required_conditions == ("비교할 동일 판매 클래스(예: C-P, C-PE)",)


def test_unnamed_class_does_not_downgrade_to_missing_evidence() -> None:
    """Auto-alignment must never turn a clarification into a dead end.

    The two products share C-P but their fee tables carry different 작성기준일,
    so no aligned value exists. The comparator must fall back to asking for a
    class rather than reporting that no evidence was found.
    """

    result = compare_product_facts(
        index=index(
            product("KRTEST000001", basis_date="2025-02-28"),
            product("KRTEST000002", basis_date="2024-12-31"),
        ),
        product_codes=("KRTEST000001", "KRTEST000002"),
        axes=("fees",),
        question="두 상품 총보수 비교",
    )
    assert result.status_for("fees") == "needs_class"


def test_matching_class_date_and_unit_is_comparable() -> None:
    result = compare_product_facts(
        index=index(product("KRTEST000001"), product("KRTEST000002", fee_unit="연 %")),
        product_codes=("KRTEST000001", "KRTEST000002"),
        axes=("risk", "fees", "performance"),
        question="두 상품의 C-P 위험등급, 총보수, 수익률 비교",
    )
    assert result.comparable_axes == frozenset({"risk", "fees", "performance"})
    fees = next(item for item in result.axes if item.axis == "fees")
    assert fees.aligned_classes == ("C-P",)
    assert fees.aligned_dates == ("2025-02-28",)
    assert fees.aligned_units == ("percent_per_year",)
    assert len(fees.evidence_refs) == 2
    assert len(fees.typed_values) == 2
    assert "annual_total_fee_rate" in result.render_context(max_chars=5_000)


def test_mismatched_basis_date_has_no_aligned_typed_value() -> None:
    date_result = compare_product_facts(
        index=index(
            product("KRTEST000001", basis_date="2025-02-28"),
            product("KRTEST000002", basis_date="2025-01-31"),
        ),
        product_codes=("KRTEST000001", "KRTEST000002"),
        axes=("fees",),
        question="두 상품의 C-P 총보수 비교",
    )
    assert date_result.status_for("fees") == "value_unresolved"


def test_same_semantic_key_conflict_is_not_downgraded_to_missing() -> None:
    first = product("KRTEST000001")
    evidence = dict(first.evidence)
    evidence["fees"] = (
        *evidence["fees"],
        block(
            "fees",
            "Class C-P 총보수 0.32% [단위:%]",
            units=("%",),
            classes=("C-P",),
            page=5,
        ),
    )
    result = compare_product_facts(
        index=index(replace(first, evidence=evidence), product("KRTEST000002")),
        product_codes=("KRTEST000001", "KRTEST000002"),
        axes=("fees",),
        question="두 상품의 C-P 총보수를 비교해 주세요",
    )
    assert result.status_for("fees") == "value_conflict"
    assert result.trace_fields()["status"] == "conflict"
    context = result.render_context(max_chars=5_000)
    assert "[PRODUCT VALUE CONFLICT]" in context
    assert "annual_total_fee_rate | 0.31" in context
    assert "annual_total_fee_rate | 0.32" in context


def test_same_page_candidates_remain_ambiguous_and_are_not_rendered_as_conflict() -> None:
    first = product("KRTEST000001")
    evidence = dict(first.evidence)
    evidence["fees"] = (
        *evidence["fees"],
        block(
            "fees",
            "Class C-P 총보수 0.32% [단위:%]",
            units=("%",),
            classes=("C-P",),
            page=4,
        ),
    )
    result = compare_product_facts(
        index=index(replace(first, evidence=evidence), product("KRTEST000002")),
        product_codes=("KRTEST000001", "KRTEST000002"),
        axes=("fees",),
        question="두 상품의 C-P 총보수를 비교해 주세요",
    )
    assert result.status_for("fees") == "value_ambiguous"
    assert result.trace_fields()["status"] == "partial"
    assert result.trace_fields()["ambiguous_axes"] == 1
    assert "[PRODUCT VALUE CONFLICT]" not in result.render_context(max_chars=5_000)


def test_missing_requested_class_evidence_is_reported() -> None:
    result = compare_product_facts(
        index=index(
            product("KRTEST000001", fee_class="C-P"),
            product("KRTEST000002", fee_class="C-PE"),
        ),
        product_codes=("KRTEST000001", "KRTEST000002"),
        axes=("fees",),
        question="두 상품의 C-P 총보수 비교",
    )
    assert result.status_for("fees") == "class_evidence_missing"
    assert result.required_conditions == ()


def test_real_corpus_comparison_renders_only_aligned_typed_rows() -> None:
    facts = ProductEvidenceIndex.from_path(
        Path(__file__).resolve().parents[1]
        / "data"
        / "structured"
        / "product_evidence.json",
        expected_record_count=100,
    )
    result = compare_product_facts(
        index=facts,
        product_codes=("KR510902511M", "KR5152420028"),
        axes=("fees",),
        question="두 상품의 A 클래스 총보수를 비교해 주세요",
    )
    assert result.status_for("fees") == "comparable"
    context = result.render_context(max_chars=5_000)
    assert "annual_total_fee_rate | 1.47" in context
    assert "annual_total_fee_rate | 0.145" in context
    assert "percent_per_year | 2025-01-17" in context


def test_stated_classification_compares_across_document_basis_dates() -> None:
    """A grade is not a measurement, so two prospectus dates do not block it.

    위험등급 is a classification the prospectus states outright. Requiring the
    two documents to share a 작성기준일 contradicted its exclusion from
    ``DATE_REQUIRED_AXES`` and left only about one percent of product pairs
    comparable on two of the six axes the task names.
    """

    result = compare_product_facts(
        index=index(
            product("KRTEST000001", basis_date="2025-02-28"),
            product("KRTEST000002", basis_date="2024-11-30"),
        ),
        product_codes=("KRTEST000001", "KRTEST000002"),
        axes=("risk",),
        question="두 상품의 위험등급을 비교해 주세요",
    )
    assert result.status_for("risk") == "comparable"


def test_measured_fee_still_requires_one_basis_date() -> None:
    """A fee is charged over a period, so the same-date rule still holds."""

    result = compare_product_facts(
        index=index(
            product("KRTEST000001", basis_date="2025-02-28"),
            product("KRTEST000002", basis_date="2024-11-30"),
        ),
        product_codes=("KRTEST000001", "KRTEST000002"),
        axes=("fees",),
        question="두 상품의 C-P 총보수를 비교해 주세요",
    )
    assert result.status_for("fees") != "comparable"
