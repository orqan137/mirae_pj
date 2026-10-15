from __future__ import annotations

import hashlib
import json
import unicodedata
from collections import Counter
from pathlib import Path

import pytest

from app.retrieval import (
    CorpusLoadError,
    LocalRetriever,
    RetrievedDocument,
    _meaningful_query_words,
    _paragraph_chunks,
    retrieve_for_queries,
    render_context,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_korean_query_particles_preserve_literal_and_add_search_stem() -> None:
    words = _meaningful_query_words(
        "연금계좌에서 배당이 건강보험료에 잡히나요?",
        excluded_words=set(),
    )

    assert "연금계좌에서" in words
    assert "연금계좌" in words
    assert "배당이" in words
    assert "배당" in words
    assert "건강보험료에" in words
    assert "건강보험료" in words


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_corpus(
    path: Path,
    *,
    source_id: str,
    source_path: str,
    content: str,
    source_group: str | None = None,
    review_status: str = "raw",
    title: str | None = None,
    media_type: str = "pdf",
    canonical_source_id: str | None = None,
    product_aliases: tuple[str, ...] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    group = source_group or ("product" if path.parent.name == "products" else "general")
    canonical = canonical_source_id or source_id
    aliases = product_aliases if product_aliases is not None else (
        (source_id,) if group == "product" else ()
    )
    if media_type == "pdf" and "[PAGE " not in content and "\f" not in content:
        content = f"[PAGE 1]\n{content}"
    page_count = (
        len([line for line in content.splitlines() if line.startswith("[PAGE ")])
        if media_type == "pdf"
        else None
    )
    path.write_text(
        "\n".join(
            [
                "[METADATA]",
                "source_scope=organizer_provided",
                f"source_id={source_id}",
                f"source_group={group}",
                f"source_path={source_path}",
                f"source_sha256={'a' * 64}",
                "extraction_method=test",
                "extractor_fingerprint=pension-corpus-v2",
                f"review_status={review_status}",
                "ocr_requested=false",
                "ocr_dpi=",
                "min_semantic_chars_per_page=",
                f"canonical_source_id={canonical}",
                f"product_aliases={','.join(aliases)}",
                f"page_count={page_count if page_count is not None else ''}",
                "[CONTENT]",
                content,
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def write_manifest(knowledge: Path) -> None:
    documents: list[dict[str, object]] = []
    for path in sorted(knowledge.rglob("*.txt")):
        raw = path.read_text(encoding="utf-8")
        raw_metadata, content = raw.split("[CONTENT]", maxsplit=1)
        metadata = dict(
            line.split("=", maxsplit=1)
            for line in raw_metadata.splitlines()[1:]
            if "=" in line
        )
        page_count = metadata["page_count"]
        documents.append(
            {
                "source_id": metadata["source_id"],
                "source_group": metadata["source_group"],
                "source_path": metadata["source_path"],
                "output_path": path.relative_to(knowledge).as_posix(),
                "media_type": Path(metadata["source_path"]).suffix.removeprefix("."),
                "sha256": metadata["source_sha256"],
                "size_bytes": 123,
                "page_count": int(page_count) if page_count else None,
                "extraction_method": metadata["extraction_method"],
                "extractor_fingerprint": metadata["extractor_fingerprint"],
                "review_status": metadata["review_status"],
                "ocr_requested": metadata["ocr_requested"] == "true",
                "ocr_dpi": int(metadata["ocr_dpi"]) if metadata["ocr_dpi"] else None,
                "min_semantic_chars_per_page": (
                    int(metadata["min_semantic_chars_per_page"])
                    if metadata["min_semantic_chars_per_page"]
                    else None
                ),
                "extracted_chars": len(content.strip()),
                "title_hint": next(
                    (
                        line.strip()
                        for line in content.splitlines()
                        if line.strip() and not line.startswith("[")
                    ),
                    metadata["source_id"],
                ),
                "text_sha256": _digest(path),
                "canonical_source_id": metadata["canonical_source_id"],
                "product_aliases": (
                    metadata["product_aliases"].split(",")
                    if metadata["product_aliases"]
                    else []
                ),
            }
        )
    (knowledge / "manifest.json").write_text(
        json.dumps(
            {
                "extractor_fingerprint": "pension-corpus-v2",
                "build_options": {
                    "ocr": False,
                    "ocr_dpi": 220,
                    "min_semantic_chars_per_page": 120,
                },
                "document_count": len(documents),
                "documents": documents,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def load(knowledge: Path) -> LocalRetriever:
    write_manifest(knowledge)
    return LocalRetriever.from_directory(knowledge)


def write_governance(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "registry_version": "pension-source-governance-v4",
                "knowledge_as_of": "2026-08-30",
                "source_scope": "organizer_provided",
                "default": {
                    "authority": "organizer_evidence",
                    "jurisdiction": "KR",
                    "audiences": ["customer"],
                    "published_date": None,
                    "reference_date": None,
                    "effective_from": None,
                    "effective_to": None,
                    "date_precision": None,
                    "required_query_terms_any": [],
                },
                "documents": ["doc1", "doc2"],
                "overrides": {
                    "doc1": {
                        "effective_from": "2025-01-01",
                        "effective_to": "2025-12-31",
                        "date_precision": "day",
                    },
                    "doc2": {
                        "effective_from": "2026-01-01",
                        "effective_to": "2026-12-31",
                        "date_precision": "day",
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_product_code_nfkc_particle_and_exact_filter(tmp_path: Path) -> None:
    knowledge = tmp_path / "knowledge"
    write_corpus(
        knowledge / "products" / "KRTEST000001.txt",
        source_id="KRTEST000001",
        source_path="투자설명서/KRTEST000001/R2.pdf",
        content="[PAGE 1]\n국공채 상품의 총보수는 연 0.31%입니다.",
    )
    write_corpus(
        knowledge / "products" / "KRTEST000002.txt",
        source_id="KRTEST000002",
        source_path="투자설명서/KRTEST000002/R2.pdf",
        content="[PAGE 1]\n국공채 상품의 총보수는 연 0.25%입니다.",
    )

    retriever = load(knowledge)
    results = retriever.retrieve("ＫＲＴＥＳＴ０００００１과 총보수", top_k=5)
    assert results
    assert {result.source_id for result in results} == {"KRTEST000001"}
    context = render_context(results, max_chars=5_000)
    assert "source_id=KRTEST000001" in context
    assert "source_path=투자설명서/KRTEST000001/R2.pdf" in context
    assert "page=1" in context


def test_no_overlap_returns_no_context(tmp_path: Path) -> None:
    knowledge = tmp_path / "knowledge"
    write_corpus(
        knowledge / "general" / "doc1.txt",
        source_id="doc1",
        source_path="일반문서/doc1.pdf",
        source_group="general",
        content="연금저축 제도와 세액공제 설명",
    )
    retriever = load(knowledge)
    assert retriever.retrieve("완전히무관한천문학", top_k=3) == []
    assert render_context([], max_chars=1_000) == ""


def test_unseen_topic_cannot_ride_on_two_generic_corpus_words(
    tmp_path: Path,
) -> None:
    knowledge = tmp_path / "knowledge"
    write_corpus(
        knowledge / "general" / "doc1.txt",
        source_id="doc1",
        source_path="일반문서/doc1.pdf",
        source_group="general",
        content="퇴직연금 가입 조건과 신청 절차 안내",
    )
    retriever = load(knowledge)

    assert retriever.retrieve(
        "주택연금 가입 조건을 알려주세요",
        top_k=3,
        source_groups={"general"},
    ) == []


def test_known_compound_keeps_colloquial_in_domain_query_retrievable(
    tmp_path: Path,
) -> None:
    knowledge = tmp_path / "knowledge"
    write_corpus(
        knowledge / "general" / "doc1.txt",
        source_id="doc1",
        source_path="일반문서/doc1.pdf",
        source_group="general",
        content=(
            "퇴직연금 안내\n"
            "퇴직연금은 상품을 고르지 않으면 자동으로 운용될 수 있습니다."
        ),
    )
    retriever = load(knowledge)

    results = retriever.retrieve(
        "아무것도 고르지 않으면 퇴직연금 돈이 자동으로 굴러가나요",
        top_k=3,
        source_groups={"general"},
    )

    assert [item.source_id for item in results] == ["doc1"]


def test_three_supported_short_concepts_are_sufficient_for_retrieval(
    tmp_path: Path,
) -> None:
    knowledge = tmp_path / "knowledge"
    write_corpus(
        knowledge / "general" / "doc1.txt",
        source_id="doc1",
        source_path="일반문서/doc1.pdf",
        source_group="general",
        content="연금 수령 세율 안내",
    )
    retriever = load(knowledge)

    results = retriever.retrieve(
        "연금 수령 세율",
        top_k=3,
        source_groups={"general"},
    )

    assert [item.source_id for item in results] == ["doc1"]


@pytest.mark.parametrize("scheme", ["DB", "DC"])
def test_one_syllable_scheme_particle_does_not_trigger_abstention(
    tmp_path: Path,
    scheme: str,
) -> None:
    knowledge = tmp_path / "knowledge"
    write_corpus(
        knowledge / "general" / "doc1.txt",
        source_id="doc1",
        source_path="일반문서/doc1.pdf",
        source_group="general",
        content="DB형 DC형 퇴직연금 제도 안내",
    )
    retriever = load(knowledge)

    results = retriever.retrieve(
        f"{scheme}형이 뭔가요",
        top_k=3,
        source_groups={"general"},
    )

    assert [item.source_id for item in results] == ["doc1"]


@pytest.mark.parametrize(
    "question",
    [
        "DC가 뭐예요",
        "DB가 뭐야",
        "IRP가 뭐예요",
        "DC는 무슨 뜻이죠",
        "DC형은 어떻습니까",
        "무엇이 DC인가요",
        "DB는 뭘 의미하나요",
    ],
)
def test_short_acronym_interrogatives_do_not_trigger_abstention(
    tmp_path: Path,
    question: str,
) -> None:
    knowledge = tmp_path / "knowledge"
    write_corpus(
        knowledge / "general" / "doc1.txt",
        source_id="doc1",
        source_path="일반문서/doc1.pdf",
        source_group="general",
        content="DB형 DC형 IRP 퇴직연금 제도 안내",
    )
    retriever = load(knowledge)

    results = retriever.retrieve(
        question,
        top_k=3,
        source_groups={"general"},
    )

    assert [item.source_id for item in results] == ["doc1"]


def test_nfkc_korean_query_and_direct_title_rank_first(tmp_path: Path) -> None:
    knowledge = tmp_path / "knowledge"
    write_corpus(
        knowledge / "general" / "doc41.txt",
        source_id="doc41",
        source_path="일반문서/doc41.pdf",
        source_group="general",
        content=(
            "연금저축계좌, IRP 세액공제 안내\n"
            "연금저축 납입액에 적용되는 세액공제 한도와 공제율입니다."
        ),
    )
    for number in range(1, 4):
        write_corpus(
            knowledge / "general" / f"doc{number}.txt",
            source_id=f"doc{number}",
            source_path=f"일반문서/doc{number}.pdf",
            source_group="general",
            content=(
                f"기타 연금 안내 {number}\n"
                "연금저축 제도 일반 설명과 별도의 세액공제 용어 색인"
            ),
        )
    retriever = load(knowledge)
    decomposed = unicodedata.normalize("NFD", "연금저축 세액공제")
    results = retriever.retrieve(decomposed, top_k=4, source_groups={"general"})
    assert results[0].source_id == "doc41"


def test_explicit_date_ranks_applicable_version_before_lexically_equal_version(
    tmp_path: Path,
) -> None:
    knowledge = tmp_path / "knowledge"
    for source_id in ("doc1", "doc2"):
        write_corpus(
            knowledge / "general" / f"{source_id}.txt",
            source_id=source_id,
            source_path=f"일반문서/{source_id}.pdf",
            source_group="general",
            content="IRP 세액공제 기준과 한도",
        )
    write_manifest(knowledge)
    governance = tmp_path / "governance.json"
    write_governance(governance)
    retriever = LocalRetriever.from_directory(
        knowledge,
        source_governance_path=governance,
    )
    results = retriever.retrieve(
        "2026년 IRP 세액공제 기준과 한도",
        top_k=1,
        source_groups={"general"},
    )
    assert [result.source_id for result in results] == ["doc2"]
    assert "effective_from=2026-01-01" in render_context(results, max_chars=2_000)


def test_quarantined_doc29_is_excluded_defensively(tmp_path: Path) -> None:
    knowledge = tmp_path / "knowledge"
    write_corpus(
        knowledge / "general" / "doc29.txt",
        source_id="doc29",
        source_path="일반문서/doc29.xlsx",
        source_group="general",
        review_status="quarantined",
        media_type="xlsx",
        content="[SHEET FAQ]\n디폴트옵션 자동매수 충돌 가능 자료",
    )
    retriever = load(knowledge)
    assert retriever.document_count == 0
    assert retriever.retrieve("디폴트옵션", top_k=3) == []


def test_two_product_codes_receive_equal_quota_and_axis_coverage(tmp_path: Path) -> None:
    knowledge = tmp_path / "knowledge"
    for code, risk, fee, performance in (
        ("KRTEST000001", "2등급", "0.31%", "4.1%"),
        ("KRTEST000002", "5등급", "0.25%", "3.7%"),
    ):
        write_corpus(
            knowledge / "products" / f"{code}.txt",
            source_id=code,
            source_path=f"투자설명서/{code}/R2.pdf",
            content=(
                f"[PAGE 1]\n{code} 상품의 위험등급은 {risk}입니다.\n"
                f"[PAGE 2]\n{code} 상품의 총보수는 {fee}입니다.\n"
                f"[PAGE 3]\n{code} 상품의 3년 수익률은 {performance}입니다."
            ),
        )
    write_corpus(
        knowledge / "products" / "KRTEST000003.txt",
        source_id="KRTEST000003",
        source_path="투자설명서/KRTEST000003/R2.pdf",
        content="위험등급 총보수 수익률 비교 설명",
    )

    retriever = load(knowledge)
    results = retriever.retrieve(
        "KRTEST000001과 KRTEST000002의 위험등급, 총보수, 3년 수익률을 비교해줘",
        top_k=6,
        source_groups={"product"},
    )
    assert Counter(result.source_id for result in results) == {
        "KRTEST000001": 3,
        "KRTEST000002": 3,
    }
    assert {result.page for result in results if result.source_id == "KRTEST000001"} == {
        1,
        2,
        3,
    }


def test_full_product_quota_does_not_append_general_overflow(tmp_path: Path) -> None:
    knowledge = tmp_path / "knowledge"
    for code in ("KRTEST000001", "KRTEST000002"):
        write_corpus(
            knowledge / "products" / f"{code}.txt",
            source_id=code,
            source_path=f"투자설명서/{code}/R2.pdf",
            content=(
                "[PAGE 1]\n위험등급 2등급\n"
                "[PAGE 2]\n총보수 0.31%\n"
                "[PAGE 3]\n1년 수익률 4.1%"
            ),
        )
    write_corpus(
        knowledge / "general" / "doc1.txt",
        source_id="doc1",
        source_path="일반문서/doc1.pdf",
        source_group="general",
        content="위험등급 총보수 1년 수익률을 비교하는 일반 안내",
    )

    retriever = load(knowledge)
    results = retriever.retrieve(
        "KRTEST000001과 KRTEST000002의 위험등급, 총보수, 1년 수익률을 비교해줘",
        top_k=6,
        source_groups={"general", "product"},
    )

    assert len(results) == 6
    assert Counter(result.source_id for result in results) == {
        "KRTEST000001": 3,
        "KRTEST000002": 3,
    }


def test_bare_two_code_comparison_uses_standard_product_axes(tmp_path: Path) -> None:
    knowledge = tmp_path / "knowledge"
    for code, risk, fee in (
        ("KRTEST000001", "2등급", "0.31%"),
        ("KRTEST000002", "5등급", "0.25%"),
    ):
        write_corpus(
            knowledge / "products" / f"{code}.txt",
            source_id=code,
            source_path=f"투자설명서/{code}/R2.pdf",
            content=(
                f"[PAGE 1]\n위험등급 {risk}\n"
                f"[PAGE 2]\n총보수 {fee}\n"
                "[PAGE 3]\n3년 수익률 4.0%"
            ),
        )
    retriever = load(knowledge)
    results = retriever.retrieve(
        "KRTEST000001과 KRTEST000002를 비교해줘",
        top_k=6,
        source_groups={"product"},
    )
    assert Counter(result.source_id for result in results) == {
        "KRTEST000001": 3,
        "KRTEST000002": 3,
    }


def test_unknown_explicit_product_code_never_falls_through(tmp_path: Path) -> None:
    knowledge = tmp_path / "knowledge"
    write_corpus(
        knowledge / "products" / "KRTEST000001.txt",
        source_id="KRTEST000001",
        source_path="투자설명서/KRTEST000001/R2.pdf",
        content="위험등급은 2등급입니다.",
    )
    retriever = load(knowledge)
    assert retriever.retrieve(
        "KRTEST999999의 위험등급",
        top_k=4,
        source_groups={"product"},
    ) == []


def test_resolved_product_name_code_filters_retrieval_without_query_code(
    tmp_path: Path,
) -> None:
    knowledge = tmp_path / "knowledge"
    for code, name in (
        ("KRTEST000001", "장기성장포커스"),
        ("KRTEST000002", "프리미엄크레딧알파"),
    ):
        write_corpus(
            knowledge / "products" / f"{code}.txt",
            source_id=code,
            source_path=f"투자설명서/{code}/R2.pdf",
            content=f"{name} 상품의 위험등급은 3등급입니다.",
        )
    retriever = load(knowledge)
    results = retriever.retrieve(
        "장기성장포커스 위험등급",
        top_k=4,
        source_groups={"product"},
        resolved_product_codes=("KRTEST000001",),
    )
    assert results
    assert {item.source_id for item in results} == {"KRTEST000001"}


def test_unknown_product_code_does_not_block_general_compound_evidence(
    tmp_path: Path,
) -> None:
    knowledge = tmp_path / "knowledge"
    write_corpus(
        knowledge / "products" / "KRTEST000001.txt",
        source_id="KRTEST000001",
        source_path="투자설명서/KRTEST000001/R2.pdf",
        content="세액공제와 무관한 상품 위험등급 자료",
    )
    write_corpus(
        knowledge / "general" / "doc41.txt",
        source_id="doc41",
        source_path="일반문서/doc41.pdf",
        source_group="general",
        content="연금저축 세액공제 한도와 공제율 안내",
    )
    retriever = load(knowledge)
    results = retriever.retrieve(
        "KRTEST999999 상품과 연금저축 세액공제 한도",
        top_k=4,
        source_groups={"general", "product"},
    )
    assert {result.source_id for result in results} == {"doc41"}


def test_product_quota_is_not_stolen_when_one_code_lacks_evidence(
    tmp_path: Path,
) -> None:
    knowledge = tmp_path / "knowledge"
    write_corpus(
        knowledge / "products" / "KRTEST000001.txt",
        source_id="KRTEST000001",
        source_path="투자설명서/KRTEST000001/R2.pdf",
        content=(
            "[PAGE 1]\n위험등급 2등급\n"
            "[PAGE 2]\n위험등급 산정 안내\n"
            "[PAGE 3]\n위험등급 변경 기준\n"
            "[PAGE 4]\n위험등급 공시"
        ),
    )
    write_corpus(
        knowledge / "products" / "KRTEST000002.txt",
        source_id="KRTEST000002",
        source_path="투자설명서/KRTEST000002/R2.pdf",
        content="상품명만 있고 요청한 비교축은 없습니다.",
    )
    retriever = load(knowledge)
    results = retriever.retrieve(
        "KRTEST000001과 KRTEST000002의 위험등급",
        top_k=6,
        source_groups={"product"},
    )
    counts = Counter(result.source_id for result in results)
    assert counts["KRTEST000001"] <= 3
    assert counts["KRTEST000002"] == 0


def test_generic_results_are_diversified_by_source(tmp_path: Path) -> None:
    knowledge = tmp_path / "knowledge"
    for number in range(1, 4):
        write_corpus(
            knowledge / "general" / f"doc{number}.txt",
            source_id=f"doc{number}",
            source_path=f"일반문서/doc{number}.pdf",
            source_group="general",
            title=f"연금저축 세액공제 문서 {number}",
            content=(
                f"[PAGE 1]\n연금저축 세액공제 한도 안내 {number}\n"
                f"[PAGE 2]\n연금저축 세액공제 적용 안내 {number}"
            ),
        )
    retriever = load(knowledge)
    results = retriever.retrieve("연금저축 세액공제", top_k=3)
    assert len({result.source_id for result in results}) == 3


def test_multi_query_retrieval_preserves_clause_provenance(tmp_path: Path) -> None:
    knowledge = tmp_path / "knowledge"
    for source_id, content in (
        ("doc15", "DB형과 DC형 퇴직연금 차이"),
        ("doc41", "IRP 세액공제 한도와 공제율"),
        ("doc20", "IRP 중도인출 법정 사유와 서류"),
    ):
        write_corpus(
            knowledge / "general" / f"{source_id}.txt",
            source_id=source_id,
            source_path=f"일반문서/{source_id}.pdf",
            content=content,
        )
    results = retrieve_for_queries(
        load(knowledge),
        (
            ("DB형과 DC형 차이", frozenset({"general"})),
            ("IRP 세액공제 한도", frozenset({"general"})),
            ("IRP 중도인출 사유", frozenset({"general"})),
        ),
        top_k=3,
    )
    by_source = {item.source_id: item for item in results}
    assert by_source["doc15"].matched_query_indexes == (1,)
    assert 2 in by_source["doc41"].matched_query_indexes
    assert 3 in by_source["doc20"].matched_query_indexes


def test_multi_query_provenance_is_backfilled_after_output_budget_is_reached() -> None:
    def document(source_id: str, score: float) -> RetrievedDocument:
        return RetrievedDocument(
            source=f"{source_id}.txt",
            source_id=source_id,
            source_path=f"일반문서/{source_id}.txt",
            source_group="general",
            title=source_id,
            index=1,
            page=None,
            text=source_id,
            score=score,
        )

    shared = document("shared", 10.0)

    class StubRetriever:
        def retrieve(self, text: str, **_: object) -> list[RetrievedDocument]:
            if text == "first":
                return [shared, document("a", 9.0), document("b", 8.0)]
            return [
                document("x", 10.0),
                document("y", 9.0),
                document("z", 8.0),
                document("w", 7.0),
                shared,
            ]

    results = retrieve_for_queries(
        StubRetriever(),  # type: ignore[arg-type]
        (
            ("first", frozenset({"general"})),
            ("second", frozenset({"general"})),
        ),
        top_k=6,
    )
    shared_result = next(item for item in results if item.source_id == "shared")
    assert shared_result.matched_query_indexes == (1, 2)


def test_generic_results_deduplicate_exact_product_alias_evidence(
    tmp_path: Path,
) -> None:
    knowledge = tmp_path / "knowledge"
    aliases = ("KRTEST000001", "KRTEST000002")
    for code in aliases:
        write_corpus(
            knowledge / "products" / f"{code}.txt",
            source_id=code,
            source_path=f"투자설명서/{code}/R2.pdf",
            content="총보수는 연 0.31%입니다.",
            canonical_source_id=aliases[0],
            product_aliases=aliases,
        )
    write_corpus(
        knowledge / "products" / "KRTEST000003.txt",
        source_id="KRTEST000003",
        source_path="투자설명서/KRTEST000003/R2.pdf",
        content="총보수는 연 0.25%입니다.",
    )
    retriever = load(knowledge)
    results = retriever.retrieve(
        "총보수",
        top_k=4,
        source_groups={"product"},
    )
    assert len(results) == 2
    assert len({result.canonical_source_id for result in results}) == 2
    context = render_context(results, max_chars=5_000)
    assert "product_aliases=KRTEST000001,KRTEST000002" in context


def test_chunking_preserves_words_rows_and_overlap() -> None:
    text = (
        "첫 번째 근거 문장입니다.\n"
        + "장기투자 홈페이지 " * 40
        + "\n열\t클래스 A\t총보수 0.3%\t이 행은 분리되면 안 됩니다.\n"
        + "마지막 근거 문장입니다."
    )
    chunks = _paragraph_chunks(text, max_chars=180, overlap_chars=60)
    assert len(chunks) > 2
    assert not any("홈페\n이지" in chunk or "홈페 이지" in chunk for chunk in chunks)
    assert any("열\t클래스 A\t총보수 0.3%\t이 행은 분리되면 안 됩니다." in chunk for chunk in chunks)
    assert any(
        set(first.splitlines()) & set(second.splitlines())
        for first, second in zip(chunks, chunks[1:])
    )


def test_missing_manifest_fails_closed(tmp_path: Path) -> None:
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    with pytest.raises(CorpusLoadError, match="manifest is missing"):
        LocalRetriever.from_directory(knowledge)


def test_organizer_manifest_cannot_silently_drop_documents(tmp_path: Path) -> None:
    knowledge = tmp_path / "knowledge"
    write_corpus(
        knowledge / "general" / "doc1.txt",
        source_id="doc1",
        source_path="일반문서/doc1.pdf",
        source_group="general",
        content="연금 근거",
    )
    write_manifest(knowledge)
    payload = json.loads((knowledge / "manifest.json").read_text(encoding="utf-8"))
    payload["source"] = "organizer-provided pension track data"
    (knowledge / "manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    with pytest.raises(CorpusLoadError, match="58 general and 100 product"):
        LocalRetriever.from_directory(knowledge)


def test_changed_output_hash_fails_closed(tmp_path: Path) -> None:
    knowledge = tmp_path / "knowledge"
    path = knowledge / "general" / "doc1.txt"
    write_corpus(
        path,
        source_id="doc1",
        source_path="일반문서/doc1.pdf",
        source_group="general",
        content="연금저축 세액공제",
    )
    write_manifest(knowledge)
    path.write_text(path.read_text(encoding="utf-8") + "변조", encoding="utf-8")
    with pytest.raises(CorpusLoadError, match="SHA-256 mismatch"):
        LocalRetriever.from_directory(knowledge)


def test_out_of_order_pdf_page_markers_fail_closed(tmp_path: Path) -> None:
    knowledge = tmp_path / "knowledge"
    write_corpus(
        knowledge / "general" / "doc1.txt",
        source_id="doc1",
        source_path="일반문서/doc1.pdf",
        source_group="general",
        content="[PAGE 1]\n첫 페이지\n[PAGE 3]\n잘못 표기된 둘째 페이지",
    )
    write_manifest(knowledge)
    with pytest.raises(CorpusLoadError, match="page markers"):
        LocalRetriever.from_directory(knowledge)


def test_office_slide_locator_is_preserved_in_context(tmp_path: Path) -> None:
    knowledge = tmp_path / "knowledge"
    write_corpus(
        knowledge / "general" / "doc33.txt",
        source_id="doc33",
        source_path="일반문서/doc33.pptx",
        source_group="general",
        media_type="pptx",
        content=(
            "[SLIDE 1]\n연금 제도 표지\n"
            "[SLIDE 2]\n연금저축 세액공제 한도 설명"
        ),
    )
    retriever = load(knowledge)
    results = retriever.retrieve("연금저축 세액공제", top_k=2)
    assert results[0].locator == "slide=2"
    assert "slide=2" in render_context(results, max_chars=2_000)


def test_real_corpus_tax_relevance_regression() -> None:
    manifest = PROJECT_ROOT / "data" / "knowledge" / "manifest.json"
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pytest.skip("real corpus has not been generated")
    documents = payload.get("documents", [])
    if not documents or documents[0].get("extractor_fingerprint") != "pension-corpus-v2":
        pytest.skip("real corpus v2 regeneration is still in progress")

    retriever = LocalRetriever.from_directory(manifest.parent)
    results = retriever.retrieve(
        "연금저축 세액공제",
        top_k=4,
        source_groups={"general"},
    )
    assert "doc41" in {result.source_id for result in results}
    assert retriever.retrieve("완전히무관한천문학", top_k=4) == []


def _real_corpus_retriever() -> LocalRetriever:
    manifest = PROJECT_ROOT / "data" / "knowledge" / "manifest.json"
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pytest.skip("real corpus has not been generated")
    documents = payload.get("documents", [])
    if not documents or documents[0].get("extractor_fingerprint") != "pension-corpus-v2":
        pytest.skip("real corpus v2 regeneration is still in progress")
    return LocalRetriever.from_directory(PROJECT_ROOT / "data" / "knowledge")


def test_unspaced_korean_compound_is_segmented_with_corpus_vocabulary() -> None:
    """A question written without spaces must still reach the same document."""

    retriever = _real_corpus_retriever()
    spaced = retriever.retrieve("연금 수령 한도를 알려주세요", top_k=6)
    collapsed = retriever.retrieve("연금수령한도를알려주세요", top_k=6)
    assert collapsed
    assert {item.source_id for item in spaced} & {item.source_id for item in collapsed}


def test_segmentation_does_not_manufacture_out_of_corpus_matches() -> None:
    """Partly unknown runs must not be split into corpus words to force a hit."""

    retriever = _real_corpus_retriever()
    assert retriever.retrieve("완전히무관한천문학", top_k=4) == []


def test_participant_facing_rule_document_is_available_to_customers() -> None:
    """doc12 explains 퇴직연금규약 to the participant, not to an operator.

    Its own body addresses the reader as one of "회사와 직원" and answers
    "직원들의 대표는 누구인가요", so classifying it as employer-only excluded
    the only source that covers 동의 vs 의견청취 from customer questions.
    """

    import json

    governance = json.loads(
        (PROJECT_ROOT / "data" / "structured" / "general_source_governance.json")
        .read_text(encoding="utf-8")
    )
    assert "customer" in governance["overrides"]["doc12"]["audiences"]


def test_staff_manual_stays_out_of_customer_answers() -> None:
    """doc55 is titled 사무담당자 업무 매뉴얼 and must remain staff-only."""

    import json

    governance = json.loads(
        (PROJECT_ROOT / "data" / "structured" / "general_source_governance.json")
        .read_text(encoding="utf-8")
    )
    assert "customer" not in governance["overrides"]["doc55"]["audiences"]


def test_inflected_word_is_supported_by_the_stem_the_corpus_holds(
    tmp_path: Path,
) -> None:
    """"압류될" is "압류" carrying an ending, not an unknown word.

    Its one trigram matched nothing, so the corpus read as having no word for
    the question and retrieval abstained on a question the corpus answers.
    """

    knowledge = tmp_path / "knowledge"
    write_corpus(
        knowledge / "general" / "doc1.txt",
        source_id="doc1",
        source_path="일반문서/doc1.pdf",
        source_group="general",
        content=(
            "퇴직연금 수급권 보호 안내\n"
            "퇴직연금 적립금은 빚 때문에 압류가 될 수 없도록 보호됩니다."
        ),
    )
    retriever = load(knowledge)

    results = retriever.retrieve(
        "퇴직연금도 빚 때문에 압류될 수 있나요",
        top_k=3,
        source_groups={"general"},
    )

    assert [item.source_id for item in results] == ["doc1"]


def test_unknown_compound_head_is_not_covered_by_its_known_tail(
    tmp_path: Path,
) -> None:
    """농지연금 splits to 연금, and counting that would hide the unknown head.

    A modifier precedes in Korean and an ending follows, so only a piece that
    starts the word counts as the word's stem.
    """

    knowledge = tmp_path / "knowledge"
    write_corpus(
        knowledge / "general" / "doc1.txt",
        source_id="doc1",
        source_path="일반문서/doc1.pdf",
        source_group="general",
        content="퇴직연금 신청 방법과 연금 수령 절차 안내",
    )
    retriever = load(knowledge)

    assert retriever.retrieve(
        "농지연금 신청 방법 알려주세요",
        top_k=3,
        source_groups={"general"},
    ) == []


def test_interrogative_wording_does_not_outrank_the_subject(tmp_path: Path) -> None:
    """Several organizer documents are FAQs, so their headings are questions.

    A heading reading "…에 접속하려면 어떻게 해야 하나요" took the top of the
    corpus for a question about 연금 수령 개시 on the shared wording alone.
    """

    knowledge = tmp_path / "knowledge"
    write_corpus(
        knowledge / "general" / "doc1.txt",
        source_id="doc1",
        source_path="일반문서/doc1.pdf",
        source_group="general",
        content="시스템 FAQ\n[연금] 시스템에 접속하려면 어떻게 해야 하나요? 공동인증서로 접속합니다.",
    )
    write_corpus(
        knowledge / "general" / "doc2.txt",
        source_id="doc2",
        source_path="일반문서/doc2.pdf",
        source_group="general",
        content="연금 수령 개시 안내\n연금 수령을 개시하려면 만 55세 이상이어야 합니다.",
    )
    retriever = load(knowledge)

    results = retriever.retrieve(
        "연금 수령을 개시하려면 어떻게 해야 하나요",
        top_k=2,
        source_groups={"general"},
    )

    assert results[0].source_id == "doc2"


def test_period_inside_a_token_is_not_a_sentence_boundary() -> None:
    """Splitting "R2_KR510902511M.pdf:page=3" left ".pdf:page=3" behind."""

    from app.validation_primitives import sentence_spans

    value = "- 근거: 투자설명서/KR510902511M/R2_KR510902511M.pdf:page=3"
    assert [value[start:end] for start, end in sentence_spans(value)] == [value]

    sentence = "연금은 IRP. 그리고 연금저축도 있습니다."
    assert len(sentence_spans(sentence)) == 2
