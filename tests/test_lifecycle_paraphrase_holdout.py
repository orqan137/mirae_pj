from __future__ import annotations

import json
from dataclasses import replace

import pytest

from scripts.evaluate_lifecycle_paraphrase_holdout import (
    DEFAULT_CASES,
    DEFAULT_KNOWLEDGE,
    DEFAULT_SOURCE_GOVERNANCE,
    REQUIRED_DOMAINS,
    evaluate_cases,
    load_cases,
    summarize_results,
)


def test_lifecycle_holdout_covers_all_domains_and_passes_without_hcx() -> None:
    cases = load_cases(DEFAULT_CASES)
    results = evaluate_cases(
        cases,
        knowledge_dir=DEFAULT_KNOWLEDGE,
        source_governance_path=DEFAULT_SOURCE_GOVERNANCE,
        top_k=6,
    )
    assert len(cases) == 24
    assert sum(case.kind == "positive" for case in cases) == 16
    assert sum(case.kind == "negative" for case in cases) == 8
    assert {case.domain for case in cases} == REQUIRED_DOMAINS
    assert all(result["passed"] for result in results), results
    assert set(summarize_results(cases, results).values()) == {1.0}


def test_lifecycle_holdout_detects_a_wrong_source_expectation() -> None:
    case = next(
        item
        for item in load_cases(DEFAULT_CASES)
        if item.case_id == "LIFE-CONTRIBUTION-DELAY-001"
    )
    changed = replace(case, relevant_source_ids=("doc58",))
    result = evaluate_cases((changed,), top_k=6)[0]
    assert result["retrieval_passed"] is False
    assert any("relevant sources missing" in failure for failure in result["failures"])


def test_lifecycle_holdout_detects_a_wrong_evidence_status_expectation() -> None:
    case = next(
        item
        for item in load_cases(DEFAULT_CASES)
        if item.case_id == "LIFE-TAX-HEALTH-INSURANCE-001"
    )
    changed = replace(case, expected_evidence_status="out_of_corpus")
    result = evaluate_cases((changed,), top_k=6)[0]
    assert result["evidence_status_exact"] is False
    assert any("evidence status differs" in failure for failure in result["failures"])


def test_lifecycle_holdout_detects_assessor_evidence_outside_qrels() -> None:
    case = next(
        item
        for item in load_cases(DEFAULT_CASES)
        if item.case_id == "LIFE-GOVERNANCE-EDUCATION-001"
    )
    changed = replace(case, relevant_source_ids=("doc58",))
    result = evaluate_cases((changed,), top_k=6)[0]
    assert result["evidence_qrel_aligned"] is False
    assert any("does not overlap retrieval qrels" in failure for failure in result["failures"])


def test_lifecycle_holdout_loader_rejects_negative_qrels(tmp_path) -> None:
    payload = json.loads(DEFAULT_CASES.read_text(encoding="utf-8"))
    negative = next(case for case in payload["cases"] if case["kind"] == "negative")
    negative["relevant_source_ids"] = ["doc1"]
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="negative qrel"):
        load_cases(path)
