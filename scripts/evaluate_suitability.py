#!/usr/bin/env python3
"""Evaluate the deterministic pension-product suitability prefilter (HCX=0)."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.product_facts import ProductEvidenceIndex  # noqa: E402
from app.suitability import SUITABILITY_VERSION, assess_suitability  # noqa: E402


DEFAULT_CASES = PROJECT_ROOT / "tests/fixtures/suitability_cases.json"
DEFAULT_PRODUCTS = PROJECT_ROOT / "data/structured/product_evidence.json"
DEFAULT_MANIFEST = PROJECT_ROOT / "data/knowledge/manifest.json"
EVALUATOR_VERSION = "pension-suitability-eval-v1"


def _strings(value: Any, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ValueError(f"{field} must be an array of non-empty strings")
    return tuple(value)


def load_cases(path: Path) -> tuple[dict[str, Any], ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("unsupported suitability case schema")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("suitability cases must be a non-empty array")
    expected_fields = {
        "case_id",
        "question",
        "expected_status",
        "required_conditions_contains",
        "expected_candidate_codes",
        "allowed_risk_grades",
        "allowed_categories",
    }
    parsed: list[dict[str, Any]] = []
    seen: set[str] = set()
    for position, case in enumerate(cases):
        if not isinstance(case, dict) or set(case) != expected_fields:
            raise ValueError(f"case[{position}] has invalid fields")
        case_id = case["case_id"]
        question = case["question"]
        status = case["expected_status"]
        if (
            not isinstance(case_id, str)
            or not case_id
            or case_id in seen
            or not isinstance(question, str)
            or not question
            or status not in {"needs_conditions", "candidate_shortlist", "out_of_corpus"}
        ):
            raise ValueError(f"case[{position}] has invalid identity/status")
        grades = case["allowed_risk_grades"]
        if not isinstance(grades, list) or not all(
            isinstance(item, int) and not isinstance(item, bool) and 1 <= item <= 6
            for item in grades
        ):
            raise ValueError(f"case[{position}] has invalid risk grades")
        parsed.append(
            {
                "case_id": case_id,
                "question": question,
                "expected_status": status,
                "required_conditions_contains": _strings(
                    case["required_conditions_contains"], field="required_conditions_contains"
                ),
                "expected_candidate_codes": _strings(
                    case["expected_candidate_codes"], field="expected_candidate_codes"
                ),
                "allowed_risk_grades": tuple(grades),
                "allowed_categories": _strings(
                    case["allowed_categories"], field="allowed_categories"
                ),
            }
        )
        seen.add(case_id)
    return tuple(parsed)


def evaluate_cases(
    cases: tuple[dict[str, Any], ...],
    *,
    products_path: Path = DEFAULT_PRODUCTS,
    manifest_path: Path = DEFAULT_MANIFEST,
) -> tuple[dict[str, Any], ...]:
    index = ProductEvidenceIndex.from_path(
        products_path,
        expected_manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        expected_record_count=100,
    )
    rows: list[dict[str, Any]] = []
    for case in cases:
        result = assess_suitability(question=case["question"], index=index)
        failures: list[str] = []
        if result.status != case["expected_status"]:
            failures.append(
                f"status expected={case['expected_status']} actual={result.status}"
            )
        if result.candidate_codes != case["expected_candidate_codes"]:
            failures.append(
                f"candidates expected={case['expected_candidate_codes']} "
                f"actual={result.candidate_codes}"
            )
        for required in case["required_conditions_contains"]:
            if required not in result.required_conditions:
                failures.append(f"missing required_condition={required}")
        allowed_grades = set(case["allowed_risk_grades"])
        if any(item.risk_grade not in allowed_grades for item in result.candidates):
            failures.append("candidate outside allowed risk grades")
        allowed_categories = set(case["allowed_categories"])
        if any(item.category not in allowed_categories for item in result.candidates):
            failures.append("candidate outside allowed categories")
        if len({item.canonical_source_id for item in result.candidates}) != len(
            result.candidates
        ):
            failures.append("duplicate canonical evidence in shortlist")
        rows.append(
            {
                "case_id": case["case_id"],
                "passed": not failures,
                "status": result.status,
                "candidate_count": len(result.candidates),
                "failures": failures,
            }
        )
    return tuple(rows)


def main() -> int:
    cases = load_cases(DEFAULT_CASES)
    results = evaluate_cases(cases)
    failed = sum(not item["passed"] for item in results)
    print(
        json.dumps(
            {
                "evaluator_version": EVALUATOR_VERSION,
                "suitability_version": SUITABILITY_VERSION,
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
