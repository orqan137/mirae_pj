from __future__ import annotations

import copy
import stat
from pathlib import Path

from scripts.prepare_knowledge import EXPECTED_GENERAL_SOURCE_IDS
from scripts.refresh_general_titles import _atomic_replace, refreshed_manifest


def test_refresh_updates_all_general_titles_and_preserves_products() -> None:
    documents: list[dict[str, object]] = [
        {
            "source_id": f"doc{number}",
            "source_group": "general",
            "source_path": f"일반문서/doc{number}.pdf",
            "title_hint": "old",
        }
        for number in range(1, 59)
    ]
    product = {
        "source_id": "KR1234567890",
        "source_group": "product",
        "source_path": "투자설명서/KR1234567890/file.pdf",
        "title_hint": "extracted product title",
    }
    documents.append(product)
    payload: dict[str, object] = {
        "build_options": {"ocr": True},
        "documents": documents,
    }
    before = copy.deepcopy(payload)
    titles = {f"doc{number}.pdf": f"authoritative title {number}" for number in range(1, 59)}

    updated = refreshed_manifest(payload, titles, catalog_sha256="a" * 64)

    assert payload == before
    assert updated["documents"][0]["title_hint"] == "authoritative title 1"
    assert updated["documents"][57]["title_hint"] == "authoritative title 58"
    assert updated["documents"][58] == product
    assert updated["build_options"]["general_title_catalog_sha256"] == "a" * 64


def test_refresh_rejects_incomplete_general_manifest() -> None:
    source_ids = sorted(EXPECTED_GENERAL_SOURCE_IDS)[:-1]
    payload: dict[str, object] = {
        "build_options": {},
        "documents": [
            {
                "source_id": source_id,
                "source_group": "general",
                "source_path": f"일반문서/{source_id}.pdf",
                "title_hint": "old",
            }
            for source_id in source_ids
        ],
    }
    titles = {f"doc{number}.pdf": f"title {number}" for number in range(1, 59)}

    try:
        refreshed_manifest(payload, titles, catalog_sha256="a" * 64)
    except ValueError as exc:
        assert "Manifest general-document set is invalid" in str(exc)
    else:
        raise AssertionError("incomplete manifest was accepted")


def test_atomic_replace_preserves_manifest_permissions(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_bytes(b"old")
    manifest.chmod(0o640)

    _atomic_replace(manifest, b"new")

    assert manifest.read_bytes() == b"new"
    assert stat.S_IMODE(manifest.stat().st_mode) == 0o640
