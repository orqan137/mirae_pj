from __future__ import annotations

from app.calculations import calculate_pension_tax_credit
from app.evidence import EvidenceAssessment, EvidenceConflict, EvidenceCoverage
from app.evidence_rendering import render_public_evidence, render_public_evidence_result
from app.retrieval import RetrievedDocument


def _assessment(*refs: str) -> EvidenceAssessment:
    return EvidenceAssessment(
        version="test",
        status="answerable",
        coverage=(
            EvidenceCoverage(
                requirement_id="r1",
                label="IRP 세액공제 근거",
                status="supported",
                evidence_refs=refs,
            ),
        ),
        missing_conditions=(),
    )


def _document(source_id: str, text: str, *, index: int = 1) -> RetrievedDocument:
    return RetrievedDocument(
        source=f"general/{source_id}.txt",
        source_id=source_id,
        source_path=f"일반문서/{source_id}.docx",
        source_group="general",
        title="테스트",
        index=index,
        page=None,
        text=text,
        score=1.0,
        locator="sheet=안내",
    )


def test_public_renderer_uses_only_assessment_selected_documents() -> None:
    used = _document(
        "doc41",
        "직원 이력과 무관한 문장\nIRP 세액공제 한도와 공제율 안내\n적용 조건을 확인합니다.",
    )
    unused = _document("doc9", "질문과 무관한 운용인력 변경 이력")
    rendered = render_public_evidence(
        question="IRP 세액공제 근거는?",
        documents=[unused, used],
        assessment=_assessment("doc41:sheet=안내:chunk=1"),
        max_chars=2_000,
    )
    assert "source_id=doc41" in rendered
    assert "citation_id=doc41:sheet=안내:chunk=1" in rendered
    assert "세액공제 한도" in rendered
    assert "source_id=doc9" not in rendered


def test_public_renderer_keeps_minimum_source_per_supported_requirement() -> None:
    assessment = EvidenceAssessment(
        version="test",
        status="answerable",
        coverage=(
            EvidenceCoverage(
                requirement_id="system",
                subquestion_id="sq1",
                label="DB DC 근거",
                status="supported",
                evidence_refs=(
                    "doc15:sheet=안내:chunk=1",
                    "doc11:sheet=안내:chunk=1",
                ),
            ),
            EvidenceCoverage(
                requirement_id="tax",
                subquestion_id="sq2",
                label="세액공제 근거",
                status="supported",
                evidence_refs=(
                    "doc41:sheet=안내:chunk=1",
                    "doc44:sheet=안내:chunk=1",
                ),
            ),
            EvidenceCoverage(
                requirement_id="procedure",
                subquestion_id="sq3",
                label="중도인출 근거",
                status="supported",
                evidence_refs=(
                    "doc20:sheet=안내:chunk=1",
                    "doc22:sheet=안내:chunk=1",
                ),
            ),
        ),
        missing_conditions=(),
    )
    documents = [
        _document("doc15", "DB형과 DC형 차이"),
        _document("doc41", "IRP 세액공제 한도"),
        _document("doc20", "IRP 중도인출 사유"),
        _document("doc11", "DB형 보조 후보"),
        _document("doc44", "세액공제 보조 후보"),
        _document("doc22", "중도인출 보조 후보"),
    ]
    rendered = render_public_evidence(
        question="DB DC 차이, 세액공제 한도, 중도인출 사유",
        documents=documents,
        assessment=assessment,
        max_chars=2_000,
    )
    assert all(f"source_id={source}" in rendered for source in ("doc15", "doc41", "doc20"))

    # The guarantee is that no single requirement may spend the budget the
    # others need.  A budget that fits only the three primaries must still
    # carry one source for each requirement rather than three for one.
    tight = render_public_evidence(
        question="DB DC 차이, 세액공제 한도, 중도인출 사유",
        documents=documents,
        assessment=assessment,
        max_chars=900,
    )
    assert all(f"source_id={source}" in tight for source in ("doc15", "doc41", "doc20"))
    assert len(tight) <= 900


def test_public_renderer_preserves_multi_identity_minimum() -> None:
    assessment = EvidenceAssessment(
        version="test",
        status="answerable",
        coverage=(
            EvidenceCoverage(
                requirement_id="two-products",
                label="비교할 두 상품",
                status="supported",
                evidence_refs=(
                    "KRTEST000001:sheet=안내:chunk=1",
                    "KRTEST000002:sheet=안내:chunk=1",
                    "KRTEST000003:sheet=안내:chunk=1",
                ),
                minimum_evidence_count=2,
            ),
        ),
        missing_conditions=(),
    )
    rendered = render_public_evidence(
        question="두 상품 비교",
        documents=[
            _document("KRTEST000001", "첫 상품"),
            _document("KRTEST000002", "둘째 상품"),
            _document("KRTEST000003", "불필요한 셋째 상품"),
        ],
        assessment=assessment,
        max_chars=2_000,
    )
    assert "source_id=KRTEST000001" in rendered
    assert "source_id=KRTEST000002" in rendered
    assert "source_id=KRTEST000003" not in rendered


def test_public_renderer_prefers_typed_comparison_over_raw_table() -> None:
    rendered = render_public_evidence(
        question="두 상품 총보수 비교",
        documents=[],
        assessment=_assessment("comparison:fees"),
        structured_context="RAW TABLE SHOULD NOT BE RETURNED",
        comparison_context="[TYPED PRODUCT COMPARISON]\nA | 0.31",
        max_chars=2_000,
    )
    assert "TYPED PRODUCT COMPARISON" in rendered
    assert "RAW TABLE" not in rendered


def test_public_renderer_returns_conflict_rows_for_conflicting_evidence() -> None:
    assessment = EvidenceAssessment(
        version="test",
        status="conflicting_evidence",
        coverage=(),
        missing_conditions=(),
        conflicts=(
            EvidenceConflict(
                conflict_id="comparison:fees",
                label="총보수 충돌",
                evidence_refs=("comparison:fees:conflict",),
            ),
        ),
    )
    rendered = render_public_evidence(
        question="두 상품 총보수 비교",
        documents=[],
        assessment=assessment,
        comparison_context="[PRODUCT VALUE CONFLICT]\nA | 0.31\nA | 0.32",
        max_chars=2_000,
    )
    assert "[PRODUCT VALUE CONFLICT]" in rendered
    assert "0.31" in rendered and "0.32" in rendered


def test_structured_product_evidence_suppresses_duplicate_product_chunk() -> None:
    product = RetrievedDocument(
        source="products/KRTEST000001.txt",
        source_id="KRTEST000001",
        source_path="투자설명서/KRTEST000001/R2.pdf",
        source_group="product",
        title="테스트",
        index=9,
        page=13,
        text="운용전문인력 변경내역과 직원 이력",
        score=1.0,
        locator="page=13",
    )
    assessment = EvidenceAssessment(
        version="test",
        status="answerable",
        coverage=(
            EvidenceCoverage(
                requirement_id="identity",
                label="상품 identity",
                status="supported",
                evidence_refs=(
                    "structured:KRTEST000001:identity",
                    "KRTEST000001:page=13:chunk=9",
                ),
            ),
        ),
        missing_conditions=(),
    )
    rendered = render_public_evidence(
        question="상품 총보수",
        documents=[product],
        assessment=assessment,
        structured_context="[PRODUCT EVIDENCE] 총보수 0.31%",
        max_chars=2_000,
    )
    assert "총보수 0.31%" in rendered
    assert "운용전문인력" not in rendered


def test_public_renderer_redacts_credentials_and_contact_data() -> None:
    rendered = render_public_evidence(
        question="IRP 세액공제",
        documents=[
            _document(
                "doc41",
                "IRP 세액공제 Authorization: Bearer secret-token "
                "owner@example.com 010-1234-5678",
            )
        ],
        assessment=_assessment("doc41:sheet=안내:chunk=1"),
        max_chars=2_000,
    )
    assert "secret-token" not in rendered
    assert "owner@example.com" not in rendered
    assert "010-1234-5678" not in rendered
    assert rendered.count("[REDACTED]") >= 1


def test_public_renderer_reserves_mixed_calculation_comparison_and_rule_evidence() -> None:
    calculation_context = calculate_pension_tax_credit(
        "총급여가 5,500만원이고 IRP에 900만원을 납입했습니다. 세액공제액을 계산해 주세요."
    ).render_context()
    comparison_context = "\n".join(
        (
            "[TYPED PRODUCT COMPARISON]",
            "comparator_version=test",
            "product_code | class | metric | value | unit | as_of_date | "
            "period_years | aggregation | tax_basis | source",
            "KR0000000000 | A | annual_total_fee_rate | 0.31 | percent_per_year | "
            "2025-01-01 | | stated_rate | not_applicable | a.pdf:page=4",
            "KR0000000001 | A | annual_total_fee_rate | 0.42 | percent_per_year | "
            "2025-01-01 | | stated_rate | not_applicable | b.pdf:page=4",
        )
    )
    assessment = EvidenceAssessment(
        version="test",
        status="answerable",
        coverage=(
            EvidenceCoverage(
                requirement_id="calc",
                subquestion_id="sq1",
                label="세액공제 계산",
                status="supported",
                evidence_refs=("calculator:pension-tax-credit-doc41-v9",),
            ),
            EvidenceCoverage(
                requirement_id="fees",
                subquestion_id="sq2",
                label="두 상품 총보수",
                status="supported",
                evidence_refs=("comparison:fees",),
            ),
            EvidenceCoverage(
                requirement_id="withdrawal",
                subquestion_id="sq3",
                label="IRP 중도인출 조건",
                status="supported",
                evidence_refs=("doc20:sheet=안내:chunk=1",),
            ),
        ),
        missing_conditions=(),
    )
    rendered = render_public_evidence(
        question="세액공제 계산, 두 상품 총보수 비교, IRP 중도인출 조건",
        documents=[
            _document(
                "doc20",
                "IRP 중도인출은 법정 사유에 해당할 때만 가능하고 서류 확인이 필요합니다.",
            )
        ],
        assessment=assessment,
        comparison_context=comparison_context,
        calculation_context=calculation_context,
        max_chars=1_200,
    )
    assert len(rendered) <= 1_200
    assert "calculated_tax_credit_won=1485000" in rendered
    assert rendered.count("annual_total_fee_rate") == 2
    assert "source_id=doc20" in rendered
    assert "법정 사유" in rendered


def test_public_renderer_reports_incomplete_required_document() -> None:
    result = render_public_evidence_result(
        question="IRP 중도인출 조건",
        documents=[_document("doc20", "IRP 중도인출은 법정 사유와 서류 확인이 필요합니다.")],
        assessment=_assessment("doc20:sheet=안내:chunk=1"),
        max_chars=80,
    )
    assert result.complete is False
    assert result.missing_sections == ("document:doc20",)
    assert result.rendered_chars <= 80
    assert result.trace_fields()["status"] == "context_budget_exceeded"


def test_classification_only_comparison_uses_the_structured_section() -> None:
    """A comparison that settles only 위험등급 renders no typed rows.

    `render_context` emits rows for typed values, and 위험등급 is a stated
    classification rather than a typed metric, so the comparison section is
    empty. Expecting a section that can never be filled reported the whole
    answer as over budget; the grades themselves are carried by the structured
    product evidence, so that section is required instead.
    """

    assessment = EvidenceAssessment(
        version="test",
        status="answerable",
        coverage=(
            EvidenceCoverage(
                requirement_id="risk",
                subquestion_id="sq1",
                label="위험등급 비교",
                status="supported",
                evidence_refs=("comparison:risk",),
            ),
        ),
        missing_conditions=(),
    )
    rendered = render_public_evidence_result(
        question="두 상품의 위험등급을 비교해 주세요",
        documents=[],
        assessment=assessment,
        structured_context="[PRODUCT EVIDENCE] product_code=KRTEST1 axis=risk 3등급",
        comparison_context="",
        max_chars=7_000,
    )
    assert rendered.complete is True
    assert rendered.missing_sections == ()
    assert "PRODUCT EVIDENCE" in rendered.context


def test_leftover_budget_carries_further_accepted_documents() -> None:
    """One source per requirement is a floor, not the whole budget.

    The assessor accepted four documents for the requirement and the renderer
    returned only the first, so a question whose answer sat in the third was
    refused while three quarters of the budget went unused. The extra sources
    were already inside the evidence boundary, so the leftover budget carries
    them.
    """

    assessment = _assessment(
        "doc42:sheet=안내:chunk=1",
        "doc51:sheet=안내:chunk=1",
        "doc20:sheet=안내:chunk=1",
    )
    result = render_public_evidence_result(
        question="연금저축을 중도해지하면 세금이 어떻게 되나요",
        documents=[
            _document("doc42", "해외 ETF 분배금은 배당소득으로 과세한다."),
            _document("doc51", "퇴직금을 연금계좌에 넣으면 과세가 이연된다."),
            _document("doc20", "연금외수령으로 16.5% 기타소득세가 과세된다."),
        ],
        assessment=assessment,
        max_chars=4_000,
    )
    assert result.complete is True
    assert result.missing_sections == ()
    for source_id in ("doc42", "doc51", "doc20"):
        assert f"source_id={source_id}" in result.context
    assert "16.5% 기타소득세" in result.context


def test_leftover_fill_never_reaches_beyond_the_assessed_evidence() -> None:
    """The fill widens the budget, never the evidence boundary."""

    result = render_public_evidence_result(
        question="IRP 세액공제 한도",
        documents=[
            _document("doc41", "연금계좌 납입액은 세액공제 대상이다."),
            _document("doc99", "평가에 쓰이지 않은 후보 문서"),
        ],
        assessment=_assessment("doc41:sheet=안내:chunk=1"),
        max_chars=4_000,
    )
    assert "source_id=doc41" in result.context
    assert "source_id=doc99" not in result.context
