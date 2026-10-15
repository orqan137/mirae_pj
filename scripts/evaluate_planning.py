#!/usr/bin/env python3
"""Evaluate deterministic question decomposition and evidence planning (HCX=0)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.planning import PLANNER_VERSION, plan_question  # noqa: E402
from app.routing import classify_question  # noqa: E402


DEFAULT_CASES = PROJECT_ROOT / "tests" / "fixtures" / "question_planning_cases.json"
EVALUATOR_VERSION = "pension-planning-eval-v2"


def _strings(value: Any, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ValueError(f"{field} must be an array of non-empty strings")
    return tuple(value)


def load_cases(path: Path) -> tuple[dict[str, Any], ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 2:
        raise ValueError("unsupported planning case schema")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("planning cases must be a non-empty array")
    seen: set[str] = set()
    parsed: list[dict[str, Any]] = []
    for position, case in enumerate(cases):
        if not isinstance(case, dict) or set(case) != {
            "case_id",
            "question",
            "expected_subquestions",
        }:
            raise ValueError(f"case[{position}] has invalid fields")
        case_id = case["case_id"]
        question = case["question"]
        expected = case["expected_subquestions"]
        if not isinstance(case_id, str) or not case_id or case_id in seen:
            raise ValueError(f"case[{position}] has invalid or duplicate case_id")
        if not isinstance(question, str) or not question.strip():
            raise ValueError(f"case[{position}] has invalid question")
        if not isinstance(expected, list) or not expected:
            raise ValueError(f"case[{position}] has no expected subquestions")
        normalized_expected: list[dict[str, tuple[str, ...]]] = []
        for sub_index, subquestion in enumerate(expected):
            if not isinstance(subquestion, dict) or set(subquestion) != {
                "required_route_labels",
                "required_requirement_kinds",
                "required_topic_terms",
                "required_query_terms",
            }:
                raise ValueError(
                    f"case[{position}].expected_subquestions[{sub_index}] is invalid"
                )
            normalized_expected.append(
                {
                    key: _strings(value, field=key)
                    for key, value in subquestion.items()
                }
            )
        seen.add(case_id)
        parsed.append(
            {
                "case_id": case_id,
                "question": question,
                "expected_subquestions": tuple(normalized_expected),
            }
        )
    return tuple(parsed)


def evaluate_cases(cases: tuple[dict[str, Any], ...]) -> tuple[dict[str, Any], ...]:
    results: list[dict[str, Any]] = []
    for case in cases:
        question = case["question"]
        plan = plan_question(question, classify_question(question))
        failures: list[str] = []
        expected = case["expected_subquestions"]
        if len(plan.subquestions) != len(expected):
            failures.append(
                f"subquestion_count expected={len(expected)} actual={len(plan.subquestions)}"
            )
        requirements_by_subquestion = {
            subquestion.subquestion_id: tuple(
                requirement
                for requirement in plan.requirements
                if requirement.subquestion_id == subquestion.subquestion_id
            )
            for subquestion in plan.subquestions
        }
        for index, (subquestion, expectation) in enumerate(
            zip(plan.subquestions, expected), start=1
        ):
            routes = set(subquestion.route.labels)
            kinds = {
                item.kind
                for item in requirements_by_subquestion[subquestion.subquestion_id]
            }
            topics = {
                term
                for item in requirements_by_subquestion[subquestion.subquestion_id]
                for term in item.topic_terms
            }
            for label in expectation["required_route_labels"]:
                if label not in routes:
                    failures.append(f"sq{index} missing route={label}")
            for kind in expectation["required_requirement_kinds"]:
                if kind not in kinds:
                    failures.append(f"sq{index} missing requirement_kind={kind}")
            for term in expectation["required_topic_terms"]:
                if term not in topics:
                    failures.append(f"sq{index} missing topic_term={term}")
            normalized_query = subquestion.query_text.casefold()
            for term in expectation["required_query_terms"]:
                if term.casefold() not in normalized_query:
                    failures.append(f"sq{index} missing query_term={term}")
        results.append(
            {
                "case_id": case["case_id"],
                "passed": not failures,
                "subquestion_count": len(plan.subquestions),
                "contextualized_subquestions": sum(
                    bool(item.inherited_terms) for item in plan.subquestions
                ),
                "requirement_count": len(plan.requirements),
                "failures": failures,
            }
        )
    return tuple(results)


def main() -> int:
    cases = load_cases(DEFAULT_CASES)
    results = evaluate_cases(cases)
    failed = sum(not item["passed"] for item in results)
    print(
        json.dumps(
            {
                "evaluator_version": EVALUATOR_VERSION,
                "planner_version": PLANNER_VERSION,
                "case_count": len(results),
                "passed": len(results) - failed,
                "failed": failed,
                "hcx_calls": 0,
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
