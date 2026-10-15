from __future__ import annotations

from pathlib import Path

import pytest

from scripts.evaluate_local_grounding import DEFAULT_CASES, load_cases
from scripts.evaluate_retrieval import ranked_metrics
from scripts.run_benchmark_suite import DEFAULT_SUITE, load_suite


def test_ranked_metrics_reward_complete_ideal_order() -> None:
    metrics = ranked_metrics(("a", "b", "noise"), ("a", "b"), k=3)
    assert metrics.recall == 1.0
    assert metrics.reciprocal_rank == 1.0
    assert metrics.ndcg == 1.0
    assert metrics.average_precision == 1.0


def test_ranked_metrics_expose_late_and_missing_evidence() -> None:
    metrics = ranked_metrics(("noise", "a"), ("a", "b"), k=2)
    assert metrics.recall == 0.5
    assert metrics.reciprocal_rank == 0.5
    assert metrics.ndcg < 0.5
    assert metrics.average_precision == 0.25


def test_ranked_metrics_do_not_count_duplicate_sources_twice() -> None:
    metrics = ranked_metrics(("a", "a", "b"), ("a", "b"), k=3)
    assert metrics.recall == 1.0
    assert metrics.ndcg == 1.0
    assert metrics.average_precision == 1.0


def test_ranked_metrics_reject_empty_qrels() -> None:
    with pytest.raises(ValueError, match="relevant_ids"):
        ranked_metrics(("a",), (), k=1)


def test_goldset_has_qrels_and_a_negative_retrieval_case() -> None:
    cases = load_cases(DEFAULT_CASES)
    assert sum(bool(case.retrieval_relevant_sources) for case in cases) == 18
    assert sum(case.expect_no_retrieval for case in cases) == 1


def test_benchmark_suite_has_hcx_free_default_profile() -> None:
    suite = load_suite(DEFAULT_SUITE)
    default = suite["profiles"][suite["default_profile"]]
    assert "endpoint_smoke" not in default
    assert "answer_guard" in default
    assert "domain_coverage" in default
    assert "lifecycle_paraphrase_holdout" in default
    assert "rule_claim_holdout" in default
    assert "context_coverage" in default
    assert suite["live_hcx"]["enabled_by_default"] is False
    assert suite["lifecycle_paraphrase_holdout"]["maximum_hcx_calls"] == 0
    assert Path(DEFAULT_SUITE).is_file()
