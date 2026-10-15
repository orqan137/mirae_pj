#!/usr/bin/env python3
"""Evaluate typed product-table coverage and conflict safety without HCX."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.product_facts import ProductEvidenceIndex  # noqa: E402
from app.product_values import (  # noqa: E402
    PRODUCT_VALUE_PARSER_VERSION,
    parse_product_axis_values,
)

EVALUATOR_VERSION = "pension-product-parser-eval-v1"
DEFAULT_SUITE = PROJECT_ROOT / "benchmarks" / "config" / "benchmark-suite.json"
DEFAULT_EVIDENCE = PROJECT_ROOT / "data" / "structured" / "product_evidence.json"
AXES = ("fees", "performance", "balance")


def load_thresholds(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    thresholds = payload.get("product_parser")
    if not isinstance(thresholds, dict):
        raise ValueError("suite.product_parser must be an object")
    minimums = thresholds.get("minimum_parsed_results")
    if (
        not isinstance(minimums, dict)
        or set(minimums) != set(AXES)
        or any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in minimums.values()
        )
    ):
        raise ValueError("product_parser.minimum_parsed_results is invalid")
    maximum = thresholds.get("maximum_independent_conflicts")
    if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 0:
        raise ValueError("product_parser.maximum_independent_conflicts is invalid")
    if thresholds.get("maximum_hcx_calls") != 0:
        raise ValueError("product_parser.maximum_hcx_calls must be zero")
    return thresholds


def evaluate(
    *,
    index: ProductEvidenceIndex,
    minimum_parsed_results: dict[str, int],
    maximum_independent_conflicts: int,
) -> dict[str, object]:
    axes: dict[str, dict[str, object]] = {}
    independent_conflicts = 0
    for axis in AXES:
        statuses: Counter[str] = Counter()
        candidate_groups = 0
        distinct_ref_groups = 0
        for record in index.records:
            labels = tuple(
                dict.fromkeys(
                    label
                    for block in record.evidence.get(axis, ())
                    for label in block.class_labels
                )
            )
            for label in labels:
                result = parse_product_axis_values(
                    index=index,
                    product_code=record.product_code,
                    axis=axis,
                    class_label=label,
                )
                statuses[result.status] += 1
                candidate_groups += len(result.conflicts)
                current_distinct = sum(
                    conflict.independently_sourced for conflict in result.conflicts
                )
                distinct_ref_groups += current_distinct
                independent_conflicts += current_distinct
        axes[axis] = {
            "status_counts": dict(sorted(statuses.items())),
            "parsed_results": statuses["parsed"],
            "ambiguous_candidate_groups": candidate_groups - distinct_ref_groups,
            "independent_conflicts": distinct_ref_groups,
        }

    failures = [
        f"{axis} parsed_results {axes[axis]['parsed_results']} < {minimum}"
        for axis, minimum in minimum_parsed_results.items()
        if axes[axis]["parsed_results"] < minimum
    ]
    if independent_conflicts > maximum_independent_conflicts:
        failures.append(
            "independent_conflicts "
            f"{independent_conflicts} > {maximum_independent_conflicts}"
        )
    return {
        "evaluator_version": EVALUATOR_VERSION,
        "parser_version": PRODUCT_VALUE_PARSER_VERSION,
        "record_count": index.record_count,
        "axes": axes,
        "independent_conflicts": independent_conflicts,
        "passed": not failures,
        "failures": failures,
        "hcx_calls": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    args = parser.parse_args()
    thresholds = load_thresholds(args.suite.resolve())
    index = ProductEvidenceIndex.from_path(
        args.evidence.resolve(),
        expected_record_count=100,
    )
    report = evaluate(
        index=index,
        minimum_parsed_results=thresholds["minimum_parsed_results"],
        maximum_independent_conflicts=thresholds["maximum_independent_conflicts"],
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
