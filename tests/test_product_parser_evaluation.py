from __future__ import annotations

from pathlib import Path

from app.product_facts import ProductEvidenceIndex
from scripts.evaluate_product_parser import DEFAULT_SUITE, evaluate, load_thresholds


def test_official_product_parser_gate_passes_without_hcx() -> None:
    project_root = Path(__file__).resolve().parents[1]
    thresholds = load_thresholds(DEFAULT_SUITE)
    report = evaluate(
        index=ProductEvidenceIndex.from_path(
            project_root / "data" / "structured" / "product_evidence.json",
            expected_record_count=100,
        ),
        minimum_parsed_results=thresholds["minimum_parsed_results"],
        maximum_independent_conflicts=thresholds["maximum_independent_conflicts"],
    )
    assert report["passed"] is True
    assert report["hcx_calls"] == 0
    assert report["independent_conflicts"] == 0
    assert report["axes"]["fees"]["parsed_results"] >= 309
    assert report["axes"]["performance"]["parsed_results"] >= 43
    assert report["axes"]["balance"]["parsed_results"] >= 342
