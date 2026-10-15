from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.corpus_integrity import sha256_file, validate_corpus


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = PROJECT_ROOT / "data" / "knowledge" / "manifest.json"
SOURCE_ROOT = PROJECT_ROOT / "docs" / "official" / "extracted" / "data"


def _write_synthetic_corpus(tmp_path: Path) -> tuple[Path, Path]:
    corpus_root = tmp_path / "knowledge"
    source_root = tmp_path / "source"
    specs = (
        ("doc29", "general", "일반문서/doc29.pdf", "general/doc29.txt", "quarantined"),
        ("P1", "product", "투자설명서/P1/R2_P1.pdf", "products/P1.txt", "raw"),
    )
    documents: list[dict[str, object]] = []
    for source_id, group, source_name, output_name, review_status in specs:
        source = source_root / source_name
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(f"organizer raw bytes for {source_id}".encode())
        source_digest = sha256_file(source)
        content = (f"{source_id} 연금 근거 문서 " * 12).strip()
        header = "\n".join(
            (
                "[METADATA]",
                "source_scope=organizer_provided",
                f"source_id={source_id}",
                f"source_group={group}",
                f"source_path={source_name}",
                f"source_sha256={source_digest}",
                "extraction_method=pdftotext-layout",
                "extractor_fingerprint=pension-corpus-v2",
                f"review_status={review_status}",
                "ocr_requested=true",
                "ocr_dpi=220",
                "min_semantic_chars_per_page=120",
                f"canonical_source_id={source_id}",
                f"product_aliases={source_id if group == 'product' else ''}",
                "page_count=1",
                "[CONTENT]",
            )
        )
        output = corpus_root / output_name
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(f"{header}\n{content}\n", encoding="utf-8")
        documents.append(
            {
                "source_id": source_id,
                "source_group": group,
                "source_path": source_name,
                "output_path": output_name,
                "media_type": "pdf",
                "sha256": source_digest,
                "size_bytes": source.stat().st_size,
                "page_count": 1,
                "extraction_method": "pdftotext-layout",
                "review_status": review_status,
                "ocr_requested": True,
                "ocr_dpi": 220,
                "min_semantic_chars_per_page": 120,
                "extracted_chars": len(content),
                "title_hint": f"fixture {source_id}",
                "text_sha256": sha256_file(output),
                "extractor_fingerprint": "pension-corpus-v2",
                "canonical_source_id": source_id,
                "product_aliases": [source_id] if group == "product" else [],
            }
        )
    (corpus_root / "manifest.json").write_text(
        json.dumps(
            {
                "source": "test fixture",
                "extractor_fingerprint": "pension-corpus-v2",
                "build_options": {
                    "ocr": True,
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
    return corpus_root, source_root


def _validate_fixture(corpus_root: Path, source_root: Path):
    return validate_corpus(
        corpus_root,
        source_root,
        expected_group_counts={"general": 1, "product": 1},
        min_visible_chars={"general": 10, "product": 10},
    )


def _manifest(corpus_root: Path) -> dict[str, object]:
    return json.loads((corpus_root / "manifest.json").read_text(encoding="utf-8"))


def _write_manifest(corpus_root: Path, payload: dict[str, object]) -> None:
    (corpus_root / "manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


@pytest.mark.skipif(not MANIFEST.exists(), reason="organizer corpus is not present")
def test_prepared_organizer_corpus_has_all_documents() -> None:
    report = validate_corpus(MANIFEST.parent, SOURCE_ROOT)
    assert report.ok, "\n".join(report.errors)


def test_synthetic_corpus_passes_full_integrity_check(tmp_path: Path) -> None:
    corpus_root, source_root = _write_synthetic_corpus(tmp_path)
    report = _validate_fixture(corpus_root, source_root)
    assert report.ok, report.errors


def test_integrity_rejects_unmanifested_searchable_file(tmp_path: Path) -> None:
    corpus_root, source_root = _write_synthetic_corpus(tmp_path)
    (corpus_root / "rogue.md").write_text("검색기에 노출되면 안 되는 파일", encoding="utf-8")

    report = _validate_fixture(corpus_root, source_root)

    assert any("unmanifested searchable outputs: rogue.md" in error for error in report.errors)


def test_integrity_rejects_unmanifested_organizer_source(tmp_path: Path) -> None:
    corpus_root, source_root = _write_synthetic_corpus(tmp_path)
    (source_root / "일반문서" / "extra.pdf").write_bytes(b"unexpected organizer file")

    report = _validate_fixture(corpus_root, source_root)

    assert any(
        "unmanifested organizer raw sources: 일반문서/extra.pdf" in error
        for error in report.errors
    )


def test_integrity_rejects_missing_derived_output(tmp_path: Path) -> None:
    corpus_root, source_root = _write_synthetic_corpus(tmp_path)
    (corpus_root / "products" / "P1.txt").unlink()

    report = _validate_fixture(corpus_root, source_root)

    assert any("output is missing" in error and "products/P1.txt" in error for error in report.errors)
    assert any("manifest outputs missing" in error and "products/P1.txt" in error for error in report.errors)


def test_integrity_rejects_changed_derived_text_digest(tmp_path: Path) -> None:
    corpus_root, source_root = _write_synthetic_corpus(tmp_path)
    output = corpus_root / "products" / "P1.txt"
    output.write_text(output.read_text(encoding="utf-8") + "변조\n", encoding="utf-8")

    report = _validate_fixture(corpus_root, source_root)

    assert any("derived text SHA-256 mismatch: products/P1.txt" in error for error in report.errors)


def test_integrity_rejects_changed_raw_source(tmp_path: Path) -> None:
    corpus_root, source_root = _write_synthetic_corpus(tmp_path)
    (source_root / "투자설명서" / "P1" / "R2_P1.pdf").write_bytes(b"tampered")

    report = _validate_fixture(corpus_root, source_root)

    assert any("raw source SHA-256 mismatch" in error for error in report.errors)


@pytest.mark.parametrize(
    ("metadata_key", "bad_value"),
    (
        ("source_id", "wrong-id"),
        ("source_group", "wrong-group"),
        ("source_path", "wrong/path.pdf"),
        ("source_sha256", "0" * 64),
        ("review_status", "reviewed"),
        ("ocr_requested", "false"),
        ("extraction_method", "wrong-extractor"),
        ("extractor_fingerprint", "wrong-fingerprint"),
    ),
)
def test_integrity_rejects_output_metadata_mismatch(
    tmp_path: Path, metadata_key: str, bad_value: str
) -> None:
    corpus_root, source_root = _write_synthetic_corpus(tmp_path)
    output = corpus_root / "products" / "P1.txt"
    text = output.read_text(encoding="utf-8")
    text = text.replace(f"{metadata_key}=", f"{metadata_key}={bad_value}#", 1)
    output.write_text(text, encoding="utf-8")
    payload = _manifest(corpus_root)
    product = payload["documents"][1]
    product["text_sha256"] = sha256_file(output)
    _write_manifest(corpus_root, payload)

    report = _validate_fixture(corpus_root, source_root)

    assert any(f"metadata {metadata_key} mismatch" in error for error in report.errors)


def test_integrity_requires_doc29_quarantine(tmp_path: Path) -> None:
    corpus_root, source_root = _write_synthetic_corpus(tmp_path)
    payload = _manifest(corpus_root)
    payload["documents"][0]["review_status"] = "raw"
    _write_manifest(corpus_root, payload)

    report = _validate_fixture(corpus_root, source_root)

    assert any("general/doc29 must appear exactly once" in error for error in report.errors)


def test_integrity_rejects_quarantining_an_extra_document(tmp_path: Path) -> None:
    corpus_root, source_root = _write_synthetic_corpus(tmp_path)
    payload = _manifest(corpus_root)
    payload["documents"][1]["review_status"] = "quarantined"
    _write_manifest(corpus_root, payload)

    report = _validate_fixture(corpus_root, source_root)

    assert any(
        "document[1].review_status mismatch: expected='raw'" in error
        for error in report.errors
    )


def test_integrity_rejects_content_below_group_threshold(tmp_path: Path) -> None:
    corpus_root, source_root = _write_synthetic_corpus(tmp_path)
    output = corpus_root / "products" / "P1.txt"
    raw = output.read_text(encoding="utf-8")
    header = raw.split("[CONTENT]", maxsplit=1)[0]
    output.write_text(f"{header}[CONTENT]\n짧음\n", encoding="utf-8")
    payload = _manifest(corpus_root)
    product = payload["documents"][1]
    product["extracted_chars"] = 2
    product["text_sha256"] = sha256_file(output)
    _write_manifest(corpus_root, payload)

    report = _validate_fixture(corpus_root, source_root)

    assert any("extracted content too short for product" in error for error in report.errors)


def test_integrity_rejects_mixed_extractor_fingerprints(tmp_path: Path) -> None:
    corpus_root, source_root = _write_synthetic_corpus(tmp_path)
    payload = _manifest(corpus_root)
    payload["documents"][1]["extractor_fingerprint"] = "pension-corpus-v3"
    _write_manifest(corpus_root, payload)

    report = _validate_fixture(corpus_root, source_root)

    assert any("mixed extractor_fingerprint" in error for error in report.errors)
