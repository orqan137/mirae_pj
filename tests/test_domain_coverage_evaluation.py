from __future__ import annotations

import json
from pathlib import Path

from scripts.evaluate_domain_coverage import (
    DEFAULT_CASES,
    DEFAULT_INVENTORY,
    DEFAULT_MANIFEST,
    evaluate_domain_coverage,
)


def _copy_inventory(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    payload = json.loads(DEFAULT_INVENTORY.read_text(encoding="utf-8"))
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path, payload


def test_real_domain_inventory_covers_all_official_documents_and_domains() -> None:
    report = evaluate_domain_coverage(
        inventory_path=DEFAULT_INVENTORY,
        manifest_path=DEFAULT_MANIFEST,
        cases_path=DEFAULT_CASES,
    )
    assert report["official_general_document_count"] == 58
    assert report["required_domain_count"] == 8
    assert report["document_inventory_coverage_rate"] == 1.0
    assert report["domain_document_coverage_rate"] == 1.0
    assert report["domain_evaluation_coverage_rate"] == 1.0
    assert report["failures"] == []
    assert report["hcx_calls"] == 0


def test_domain_inventory_reports_a_missing_official_document(tmp_path: Path) -> None:
    path, payload = _copy_inventory(tmp_path)
    payload["documents"].pop("doc58")
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    report = evaluate_domain_coverage(
        inventory_path=path,
        manifest_path=DEFAULT_MANIFEST,
        cases_path=DEFAULT_CASES,
    )

    assert report["document_inventory_coverage_rate"] < 1.0
    assert "missing inventory document: doc58" in report["failures"]


def test_domain_inventory_rejects_a_case_without_matching_qrel(tmp_path: Path) -> None:
    path, payload = _copy_inventory(tmp_path)
    payload["evaluation_cases"]["transfer_portability"] = ["GROUND-TAX-001"]
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    report = evaluate_domain_coverage(
        inventory_path=path,
        manifest_path=DEFAULT_MANIFEST,
        cases_path=DEFAULT_CASES,
    )

    assert report["domain_evaluation_coverage_rate"] < 1.0
    assert any("transfer_portability:GROUND-TAX-001" in item for item in report["failures"])
