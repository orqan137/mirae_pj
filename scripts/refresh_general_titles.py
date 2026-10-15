#!/usr/bin/env python3
"""Atomically refresh only general-document title hints without re-running OCR."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path, PurePosixPath

try:
    from .corpus_integrity import validate_corpus
    from .prepare_knowledge import (
        DEFAULT_GENERAL_CATALOG,
        DEFAULT_INPUT,
        DEFAULT_OUTPUT,
        EXPECTED_GENERAL_SOURCE_IDS,
        load_general_document_titles,
    )
except ImportError:  # Direct execution: python3 scripts/refresh_general_titles.py
    from corpus_integrity import validate_corpus
    from prepare_knowledge import (
        DEFAULT_GENERAL_CATALOG,
        DEFAULT_INPUT,
        DEFAULT_OUTPUT,
        EXPECTED_GENERAL_SOURCE_IDS,
        load_general_document_titles,
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def refreshed_manifest(
    payload: dict[str, object], catalog_titles: dict[str, str], *, catalog_sha256: str
) -> dict[str, object]:
    """Return a copy with exactly 58 general titles updated; products stay byte-equivalent."""

    documents = payload.get("documents")
    if not isinstance(documents, list):
        raise ValueError("Manifest documents must be an array")
    updated = copy.deepcopy(payload)
    updated_documents = updated["documents"]
    seen: set[str] = set()
    general_count = 0
    for index, item in enumerate(updated_documents):
        if not isinstance(item, dict):
            raise ValueError(f"Manifest document[{index}] must be an object")
        if item.get("source_group") != "general":
            continue
        general_count += 1
        source_id = item.get("source_id")
        source_path = item.get("source_path")
        if not isinstance(source_id, str) or source_id in seen:
            raise ValueError(f"Duplicate or invalid general source_id: {source_id!r}")
        if not isinstance(source_path, str):
            raise ValueError(f"Invalid source_path for {source_id}")
        filename = PurePosixPath(source_path).name
        if PurePosixPath(source_path).parts[:1] != ("일반문서",):
            raise ValueError(f"General source is outside 일반문서/: {source_path}")
        if PurePosixPath(filename).stem != source_id or filename not in catalog_titles:
            raise ValueError(f"General source does not match catalog: {source_path}")
        item["title_hint"] = catalog_titles[filename]
        seen.add(source_id)

    if seen != EXPECTED_GENERAL_SOURCE_IDS or general_count != 58:
        missing = sorted(EXPECTED_GENERAL_SOURCE_IDS - seen)
        unexpected = sorted(seen - EXPECTED_GENERAL_SOURCE_IDS)
        raise ValueError(
            "Manifest general-document set is invalid: "
            f"missing={missing}, unexpected={unexpected}, count={general_count}"
        )

    build_options = updated.get("build_options")
    if not isinstance(build_options, dict):
        raise ValueError("Manifest build_options must be an object")
    build_options["general_title_catalog_sha256"] = catalog_sha256

    original_products = [
        item
        for item in documents
        if isinstance(item, dict) and item.get("source_group") == "product"
    ]
    updated_products = [
        item
        for item in updated_documents
        if isinstance(item, dict) and item.get("source_group") == "product"
    ]
    if updated_products != original_products:
        raise RuntimeError("Product manifest entries changed during general-title refresh")
    return updated


def _atomic_replace(path: Path, content: bytes) -> None:
    original_mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".next", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, original_mode)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--source", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_GENERAL_CATALOG)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    corpus = args.corpus.resolve()
    source = args.source.resolve()
    catalog = args.catalog.resolve()
    manifest = corpus / "manifest.json"

    validate_corpus(corpus, source).require_valid()
    original_bytes = manifest.read_bytes()
    original_payload = json.loads(original_bytes)
    catalog_bytes = catalog.read_bytes()
    catalog_titles = load_general_document_titles(catalog)
    updated_payload = refreshed_manifest(
        original_payload,
        catalog_titles,
        catalog_sha256=sha256_bytes(catalog_bytes),
    )
    updated_bytes = (
        json.dumps(updated_payload, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")

    if updated_bytes == original_bytes:
        print("General-document title hints are already current; manifest unchanged.")
        return 0

    _atomic_replace(manifest, updated_bytes)
    try:
        validate_corpus(corpus, source).require_valid()
    except BaseException:
        _atomic_replace(manifest, original_bytes)
        raise

    print("Atomically refreshed 58 general-document title hints without OCR.")
    print(f"Manifest SHA-256: {sha256_bytes(original_bytes)} -> {sha256_bytes(updated_bytes)}")
    structured = corpus.parent / "structured" / "product_evidence.json"
    if structured.exists():
        print(
            "WARNING: product_evidence.json is now stale because it is bound to the "
            "previous corpus manifest; regenerate it with scripts/extract_product_facts.py."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
