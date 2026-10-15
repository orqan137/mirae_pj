#!/usr/bin/env python3
"""Evaluate ranked evidence retrieval with standard deterministic IR metrics."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.evaluate_local_grounding import (  # noqa: E402
    DEFAULT_CASES,
    DEFAULT_KNOWLEDGE,
    DEFAULT_PRODUCT_EVIDENCE,
    evaluate_cases,
    load_cases,
)


RETRIEVAL_EVALUATOR_VERSION = "pension-retrieval-eval-v1"
DEFAULT_SUITE = PROJECT_ROOT / "benchmarks" / "config" / "benchmark-suite.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True, slots=True)
class RetrievalMetrics:
    recall: float
    reciprocal_rank: float
    ndcg: float
    average_precision: float


def ranked_metrics(
    ranked_ids: tuple[str, ...],
    relevant_ids: tuple[str, ...],
    *,
    k: int,
) -> RetrievalMetrics:
    """Return binary-qrel Recall, MRR, nDCG and AP at k."""

    if k < 1:
        raise ValueError("k must be positive")
    relevant = set(relevant_ids)
    if not relevant:
        raise ValueError("relevant_ids must not be empty")
    ranked = tuple(dict.fromkeys(ranked_ids))[:k]
    hits = [item in relevant for item in ranked]
    recall = len({item for item in ranked if item in relevant}) / len(relevant)
    first_hit = next((rank for rank, hit in enumerate(hits, start=1) if hit), None)
    reciprocal_rank = 0.0 if first_hit is None else 1.0 / first_hit
    dcg = sum(1.0 / math.log2(rank + 1) for rank, hit in enumerate(hits, 1) if hit)
    ideal_hits = min(len(relevant), k)
    ideal_dcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
    ndcg = dcg / ideal_dcg
    found: set[str] = set()
    precision_sum = 0.0
    for rank, item in enumerate(ranked, start=1):
        if item in relevant and item not in found:
            found.add(item)
            precision_sum += len(found) / rank
    average_precision = precision_sum / len(relevant)
    return RetrievalMetrics(recall, reciprocal_rank, ndcg, average_precision)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def evaluate_retrieval(
    *,
    cases_path: Path,
    knowledge_dir: Path,
    product_evidence_path: Path,
    k: int,
) -> dict[str, object]:
    cases = load_cases(cases_path)
    results = evaluate_cases(
        cases,
        knowledge_dir=knowledge_dir,
        product_evidence_path=product_evidence_path,
    )
    rows: list[dict[str, object]] = []
    metric_rows: list[RetrievalMetrics] = []
    no_evidence_checks = 0
    no_evidence_passes = 0
    for case, result in zip(cases, results, strict=True):
        if case.retrieval_relevant_sources:
            metrics = ranked_metrics(
                result.retrieved_source_ids,
                case.retrieval_relevant_sources,
                k=k,
            )
            metric_rows.append(metrics)
            rows.append(
                {
                    "case_id": case.case_id,
                    "ranked_source_ids": list(result.retrieved_source_ids[:k]),
                    "relevant_source_ids": list(case.retrieval_relevant_sources),
                    "recall_at_k": metrics.recall,
                    "mrr_at_k": metrics.reciprocal_rank,
                    "ndcg_at_k": metrics.ndcg,
                    "map_at_k": metrics.average_precision,
                }
            )
        if case.expect_no_retrieval:
            no_evidence_checks += 1
            passed = not result.retrieved_source_ids
            no_evidence_passes += int(passed)
            rows.append(
                {
                    "case_id": case.case_id,
                    "expected_no_retrieval": True,
                    "passed": passed,
                    "ranked_source_ids": list(result.retrieved_source_ids[:k]),
                }
            )
    return {
        "evaluator_version": RETRIEVAL_EVALUATOR_VERSION,
        "cases_sha256": _sha256(cases_path),
        "k": k,
        "evaluated_query_count": len(metric_rows),
        "recall_at_k": _mean([row.recall for row in metric_rows]),
        "mrr_at_k": _mean([row.reciprocal_rank for row in metric_rows]),
        "ndcg_at_k": _mean([row.ndcg for row in metric_rows]),
        "map_at_k": _mean([row.average_precision for row in metric_rows]),
        "no_evidence_rejection_rate": (
            no_evidence_passes / no_evidence_checks if no_evidence_checks else 1.0
        ),
        "results": rows,
        "hcx_calls": 0,
    }


def _load_thresholds(path: Path) -> tuple[int, dict[str, float]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("unsupported benchmark suite schema")
    retrieval = payload.get("retrieval")
    if not isinstance(retrieval, dict):
        raise ValueError("suite retrieval configuration is missing")
    k = retrieval.get("k")
    thresholds = retrieval.get("thresholds")
    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        raise ValueError("retrieval.k must be a positive integer")
    required = {
        "recall_at_k",
        "mrr_at_k",
        "ndcg_at_k",
        "map_at_k",
        "no_evidence_rejection_rate",
    }
    if not isinstance(thresholds, dict) or set(thresholds) != required:
        raise ValueError("retrieval thresholds are invalid")
    parsed: dict[str, float] = {}
    for name, value in thresholds.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"threshold {name} must be numeric")
        number = float(value)
        if not 0.0 <= number <= 1.0:
            raise ValueError(f"threshold {name} must be between zero and one")
        parsed[name] = number
    return k, parsed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--knowledge", type=Path, default=DEFAULT_KNOWLEDGE)
    parser.add_argument("--product-evidence", type=Path, default=DEFAULT_PRODUCT_EVIDENCE)
    args = parser.parse_args()
    k, thresholds = _load_thresholds(args.suite.resolve())
    report = evaluate_retrieval(
        cases_path=args.cases.resolve(),
        knowledge_dir=args.knowledge.resolve(),
        product_evidence_path=args.product_evidence.resolve(),
        k=k,
    )
    failures = [
        f"{name}={report[name]:.6f} below threshold={threshold:.6f}"
        for name, threshold in thresholds.items()
        if float(report[name]) < threshold
    ]
    report["thresholds"] = thresholds
    report["passed"] = not failures
    report["failures"] = failures
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
