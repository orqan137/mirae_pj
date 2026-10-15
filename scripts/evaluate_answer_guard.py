#!/usr/bin/env python3
"""Evaluate deterministic post-generation guards without calling HCX."""

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

from app.answer_validation import ANSWER_VALIDATOR_VERSION, validate_answer  # noqa: E402
from app.evidence import EvidenceAssessment  # noqa: E402
from app.product_identity import ProductResolution, ResolvedProduct  # noqa: E402
from app.routing import classify_question  # noqa: E402


ANSWER_GUARD_EVALUATOR_VERSION = "pension-answer-guard-eval-v1"
DEFAULT_CASES = PROJECT_ROOT / "tests" / "fixtures" / "answer_validation_cases.json"
_CASE_FIELDS = frozenset(
    {
        "case_id",
        "category",
        "question",
        "answer",
        "evidence_context_file",
        "assessment_status",
        "resolved_products",
        "expected_elements",
        "forbidden_claims",
        "required_evidence",
        "required_conditions",
        "calculation_expected",
        "expected_valid",
        "expected_failure_kinds",
    }
)
_PRODUCT_FIELDS = frozenset(
    {"product_code", "canonical_source_id", "aliases", "official_name"}
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _strings(value: Any, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field} must be an array of strings")
    if any(not item.strip() for item in value):
        raise ValueError(f"{field} must not contain empty strings")
    return tuple(value)


@dataclass(frozen=True, slots=True)
class AnswerGuardCase:
    case_id: str
    category: str
    question: str
    answer: str
    evidence_context_path: Path
    assessment_status: str
    resolved_products: tuple[ResolvedProduct, ...]
    expected_elements: tuple[str, ...]
    forbidden_claims: tuple[str, ...]
    required_evidence: tuple[str, ...]
    required_conditions: tuple[str, ...]
    calculation_expected: bool
    expected_valid: bool
    expected_failure_kinds: tuple[str, ...]


def load_cases(path: Path) -> tuple[AnswerGuardCase, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("unsupported answer guard case schema")
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("cases must be a non-empty array")
    fixture_root = path.parent.resolve()
    parsed: list[AnswerGuardCase] = []
    seen: set[str] = set()
    for position, item in enumerate(raw_cases):
        if not isinstance(item, dict) or set(item) != _CASE_FIELDS:
            raise ValueError(f"case[{position}] has invalid fields")
        identity_values = (item["case_id"], item["category"], item["question"], item["answer"])
        if not all(isinstance(value, str) and value.strip() for value in identity_values):
            raise ValueError(f"case[{position}] has invalid identity fields")
        if item["case_id"] in seen:
            raise ValueError(f"duplicate case_id: {item['case_id']}")
        seen.add(item["case_id"])
        context_name = item["evidence_context_file"]
        if not isinstance(context_name, str) or not context_name:
            raise ValueError(f"case[{position}] has invalid evidence context")
        context_path = (fixture_root / context_name).resolve()
        if not context_path.is_relative_to(fixture_root) or not context_path.is_file():
            raise ValueError(f"case[{position}] evidence context is unsafe or missing")
        raw_products = item["resolved_products"]
        if not isinstance(raw_products, list):
            raise ValueError(f"case[{position}].resolved_products must be an array")
        products: list[ResolvedProduct] = []
        for product_position, product in enumerate(raw_products):
            if not isinstance(product, dict) or set(product) != _PRODUCT_FIELDS:
                raise ValueError(
                    f"case[{position}].resolved_products[{product_position}] is invalid"
                )
            aliases = _strings(product["aliases"], field="aliases")
            values = (
                product["product_code"],
                product["canonical_source_id"],
                product["official_name"],
            )
            if not all(isinstance(value, str) and value.strip() for value in values):
                raise ValueError(f"case[{position}] has invalid resolved product")
            products.append(
                ResolvedProduct(
                    product_code=product["product_code"],
                    canonical_source_id=product["canonical_source_id"],
                    aliases=aliases,
                    official_name=product["official_name"],
                    matched_by="gold_case",
                )
            )
        for boolean_field in ("calculation_expected", "expected_valid"):
            if not isinstance(item[boolean_field], bool):
                raise ValueError(f"case[{position}].{boolean_field} must be boolean")
        parsed.append(
            AnswerGuardCase(
                case_id=item["case_id"],
                category=item["category"],
                question=item["question"],
                answer=item["answer"],
                evidence_context_path=context_path,
                assessment_status=item["assessment_status"],
                resolved_products=tuple(products),
                expected_elements=_strings(item["expected_elements"], field="expected_elements"),
                forbidden_claims=_strings(item["forbidden_claims"], field="forbidden_claims"),
                required_evidence=_strings(item["required_evidence"], field="required_evidence"),
                required_conditions=_strings(
                    item["required_conditions"], field="required_conditions"
                ),
                calculation_expected=item["calculation_expected"],
                expected_valid=item["expected_valid"],
                expected_failure_kinds=_strings(
                    item["expected_failure_kinds"], field="expected_failure_kinds"
                ),
            )
        )
    return tuple(parsed)


def evaluate_case(case: AnswerGuardCase) -> dict[str, object]:
    resolution = (
        ProductResolution(
            version="gold-case",
            resolved=case.resolved_products,
            unknown_codes=(),
            ambiguous=(),
        )
        if case.resolved_products
        else None
    )
    assessment = EvidenceAssessment(
        version="gold-case",
        status=case.assessment_status,
        coverage=(),
        missing_conditions=case.required_conditions,
    )
    result = validate_answer(
        answer=case.answer,
        question=case.question,
        evidence_context=case.evidence_context_path.read_text(encoding="utf-8"),
        route=classify_question(case.question),
        assessment=assessment,
        product_resolution=resolution,
    )
    actual_kinds = tuple(dict.fromkeys(claim.kind for claim in result.unsupported_claims))
    failures: list[str] = []
    if result.valid is not case.expected_valid:
        failures.append(f"valid expected={case.expected_valid} actual={result.valid}")
    if set(actual_kinds) != set(case.expected_failure_kinds):
        failures.append(
            f"failure_kinds expected={case.expected_failure_kinds} actual={actual_kinds}"
        )
    return {
        "case_id": case.case_id,
        "passed": not failures,
        "expected_valid": case.expected_valid,
        "actual_valid": result.valid,
        "actual_failure_kinds": list(actual_kinds),
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    args = parser.parse_args()
    path = args.cases.resolve()
    cases = load_cases(path)
    results = [evaluate_case(case) for case in cases]
    report = {
        "evaluator_version": ANSWER_GUARD_EVALUATOR_VERSION,
        "answer_validator_version": ANSWER_VALIDATOR_VERSION,
        "cases_sha256": _sha256(path),
        "case_count": len(results),
        "passed": sum(bool(result["passed"]) for result in results),
        "failed": sum(not bool(result["passed"]) for result in results),
        "results": results,
        "hcx_calls": 0,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if all(bool(result["passed"]) for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
