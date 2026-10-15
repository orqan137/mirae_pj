#!/usr/bin/env python3
"""Evaluate pension route and deterministic tool selection without HCX."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.planning import PLANNER_VERSION, plan_question  # noqa: E402
from app.routing import ROUTER_VERSION, classify_question  # noqa: E402


EVALUATOR_VERSION = "pension-tool-selection-eval-v1"
DEFAULT_CASES = PROJECT_ROOT / "tests" / "fixtures" / "tool_selection_holdout_cases.json"
DEFAULT_SUITE = PROJECT_ROOT / "benchmarks" / "config" / "benchmark-suite.json"
_CASE_FIELDS = frozenset(
    {
        "case_id",
        "question",
        "expected_route_labels",
        "expected_source_groups",
        "expected_tools",
        "expected_needs_conditions",
        "expected_requires_calculation",
    }
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _strings(value: Any, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ValueError(f"{field} must be an array of non-empty strings")
    if len(value) != len(set(value)):
        raise ValueError(f"{field} must contain unique strings")
    return tuple(value)


@dataclass(frozen=True, slots=True)
class ToolSelectionCase:
    case_id: str
    question: str
    expected_route_labels: tuple[str, ...]
    expected_source_groups: tuple[str, ...]
    expected_tools: tuple[str, ...]
    expected_needs_conditions: bool
    expected_requires_calculation: bool


def load_cases(path: Path) -> tuple[ToolSelectionCase, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("unsupported tool-selection case schema")
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("cases must be a non-empty array")
    seen: set[str] = set()
    cases: list[ToolSelectionCase] = []
    for position, item in enumerate(raw_cases):
        if not isinstance(item, dict) or set(item) != _CASE_FIELDS:
            raise ValueError(f"case[{position}] has invalid fields")
        case_id = item["case_id"]
        question = item["question"]
        if not isinstance(case_id, str) or not case_id or case_id in seen:
            raise ValueError(f"case[{position}] has invalid or duplicate case_id")
        if not isinstance(question, str) or not question.strip():
            raise ValueError(f"case[{position}] has invalid question")
        for field in ("expected_needs_conditions", "expected_requires_calculation"):
            if not isinstance(item[field], bool):
                raise ValueError(f"case[{position}] has invalid {field}")
        seen.add(case_id)
        cases.append(
            ToolSelectionCase(
                case_id=case_id,
                question=question,
                expected_route_labels=_strings(
                    item["expected_route_labels"], field="expected_route_labels"
                ),
                expected_source_groups=_strings(
                    item["expected_source_groups"], field="expected_source_groups"
                ),
                expected_tools=_strings(item["expected_tools"], field="expected_tools"),
                expected_needs_conditions=item["expected_needs_conditions"],
                expected_requires_calculation=item["expected_requires_calculation"],
            )
        )
    return tuple(cases)


def evaluate_cases(cases: tuple[ToolSelectionCase, ...]) -> tuple[dict[str, Any], ...]:
    results: list[dict[str, Any]] = []
    for case in cases:
        route = classify_question(case.question)
        plan = plan_question(case.question, route)
        route_match = set(route.labels) == set(case.expected_route_labels)
        source_match = route.source_groups == frozenset(case.expected_source_groups)
        tools_match = set(plan.tools) == set(case.expected_tools)
        controls_match = (
            route.needs_conditions == case.expected_needs_conditions
            and route.requires_calculation == case.expected_requires_calculation
        )
        failures: list[str] = []
        if not route_match:
            failures.append(
                f"routes expected={list(case.expected_route_labels)} actual={list(route.labels)}"
            )
        if not source_match:
            failures.append(
                "source_groups expected="
                f"{list(case.expected_source_groups)} actual={sorted(route.source_groups)}"
            )
        if not tools_match:
            failures.append(
                f"tools expected={list(case.expected_tools)} actual={list(plan.tools)}"
            )
        if not controls_match:
            failures.append(
                "controls expected="
                f"({case.expected_needs_conditions}, {case.expected_requires_calculation}) "
                f"actual=({route.needs_conditions}, {route.requires_calculation})"
            )
        results.append(
            {
                "case_id": case.case_id,
                "passed": not failures,
                "route_exact": route_match,
                "source_groups_exact": source_match,
                "tools_exact": tools_match,
                "controls_exact": controls_match,
                "failures": failures,
            }
        )
    return tuple(results)


def _rate(results: tuple[dict[str, Any], ...], field: str) -> float:
    return sum(bool(item[field]) for item in results) / len(results)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    args = parser.parse_args()

    cases_path = args.cases.resolve()
    suite_path = args.suite.resolve()
    cases = load_cases(cases_path)
    results = evaluate_cases(cases)
    metrics = {
        "route_exact_match_rate": _rate(results, "route_exact"),
        "source_group_exact_match_rate": _rate(results, "source_groups_exact"),
        "tool_selection_exact_match_rate": _rate(results, "tools_exact"),
        "control_decision_accuracy": _rate(results, "controls_exact"),
    }
    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    thresholds = suite["tool_selection"]["thresholds"]
    passed = all(metrics[name] >= threshold for name, threshold in thresholds.items())
    failed_cases = sum(not item["passed"] for item in results)
    print(
        json.dumps(
            {
                "evaluator_version": EVALUATOR_VERSION,
                "router_version": ROUTER_VERSION,
                "planner_version": PLANNER_VERSION,
                "cases_sha256": _sha256(cases_path),
                "case_count": len(results),
                "passed": len(results) - failed_cases,
                "failed": failed_cases,
                **metrics,
                "thresholds_passed": passed,
                "hcx_calls": 0,
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if passed and failed_cases == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
