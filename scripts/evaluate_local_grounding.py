#!/usr/bin/env python3
"""Evaluate deterministic grounding and gate behavior without calling HCX."""

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

from app.calculations import calculate_pension_tax_credit  # noqa: E402
from app.evidence import assess_evidence  # noqa: E402
from app.planning import plan_question  # noqa: E402
from app.product_comparison import compare_product_facts  # noqa: E402
from app.product_facts import (  # noqa: E402
    ProductEvidenceIndex,
    explicit_requested_axes,
    requested_axes,
    requested_product_codes,
)
from app.product_identity import ProductIdentityResolver  # noqa: E402
from app.retrieval import LocalRetriever, retrieve_for_queries  # noqa: E402
from app.routing import classify_question  # noqa: E402
from app.suitability import assess_suitability  # noqa: E402


LOCAL_EVALUATOR_VERSION = "pension-local-grounding-eval-v3"
DEFAULT_CASES = PROJECT_ROOT / "tests" / "fixtures" / "pension_grounding_cases.json"
DEFAULT_KNOWLEDGE = PROJECT_ROOT / "data" / "knowledge"
DEFAULT_PRODUCT_EVIDENCE = PROJECT_ROOT / "data" / "structured" / "product_evidence.json"
DEFAULT_SOURCE_GOVERNANCE = (
    PROJECT_ROOT / "data" / "structured" / "general_source_governance.json"
)
_CASE_FIELDS = frozenset(
    {
        "case_id",
        "category",
        "question",
        "expected_elements",
        "forbidden_claims",
        "required_evidence",
        "required_conditions",
        "calculation_expected",
        "expected_status",
        "expected_route_labels",
        "expected_product_codes",
        "expected_comparable_axes",
        "retrieval_relevant_sources",
        "expect_no_retrieval",
    }
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _string_list(value: Any, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field} must be an array of strings")
    if any(not item.strip() for item in value):
        raise ValueError(f"{field} must not contain empty strings")
    return tuple(value)


@dataclass(frozen=True, slots=True)
class GroundingCase:
    case_id: str
    category: str
    question: str
    expected_elements: tuple[str, ...]
    forbidden_claims: tuple[str, ...]
    required_evidence_prefixes: tuple[str, ...]
    required_conditions: tuple[str, ...]
    calculation_expected: bool
    expected_status: str
    expected_route_labels: tuple[str, ...]
    expected_product_codes: tuple[str, ...]
    expected_comparable_axes: tuple[str, ...]
    retrieval_relevant_sources: tuple[str, ...]
    expect_no_retrieval: bool


@dataclass(frozen=True, slots=True)
class LocalEvaluationResult:
    case_id: str
    passed: bool
    actual_status: str
    failures: tuple[str, ...]
    supported_evidence: tuple[str, ...]
    retrieved_source_ids: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "passed": self.passed,
            "actual_status": self.actual_status,
            "failures": list(self.failures),
            "supported_evidence_count": len(self.supported_evidence),
            "retrieved_source_ids": list(self.retrieved_source_ids),
        }


def load_cases(path: Path) -> tuple[GroundingCase, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 2:
        raise ValueError("unsupported local grounding case schema")
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("cases must be a non-empty array")
    parsed: list[GroundingCase] = []
    seen: set[str] = set()
    for position, item in enumerate(raw_cases):
        if not isinstance(item, dict) or set(item) != _CASE_FIELDS:
            raise ValueError(f"case[{position}] has invalid fields")
        case_id = item["case_id"]
        category = item["category"]
        question = item["question"]
        status = item["expected_status"]
        if not all(
            isinstance(value, str) and value.strip()
            for value in (case_id, category, question)
        ):
            raise ValueError(f"case[{position}] has invalid identity fields")
        if case_id in seen:
            raise ValueError(f"duplicate case_id: {case_id}")
        seen.add(case_id)
        if status not in {
            "answerable",
            "partially_answerable",
            "needs_conditions",
            "out_of_corpus",
            "conflicting_evidence",
        }:
            raise ValueError(f"case[{position}] has invalid expected_status")
        required_evidence = item["required_evidence"]
        if not isinstance(required_evidence, list) or not all(
            isinstance(ref, dict)
            and set(ref) == {"ref_prefix"}
            and isinstance(ref["ref_prefix"], str)
            and ref["ref_prefix"]
            for ref in required_evidence
        ):
            raise ValueError(f"case[{position}].required_evidence is invalid")
        calculation_expected = item["calculation_expected"]
        if not isinstance(calculation_expected, bool):
            raise ValueError(f"case[{position}].calculation_expected must be boolean")
        expect_no_retrieval = item["expect_no_retrieval"]
        if not isinstance(expect_no_retrieval, bool):
            raise ValueError(f"case[{position}].expect_no_retrieval must be boolean")
        parsed.append(
            GroundingCase(
                case_id=case_id,
                category=category,
                question=question,
                expected_elements=_string_list(
                    item["expected_elements"], field="expected_elements"
                ),
                forbidden_claims=_string_list(item["forbidden_claims"], field="forbidden_claims"),
                required_evidence_prefixes=tuple(ref["ref_prefix"] for ref in required_evidence),
                required_conditions=_string_list(
                    item["required_conditions"], field="required_conditions"
                ),
                calculation_expected=calculation_expected,
                expected_status=status,
                expected_route_labels=_string_list(
                    item["expected_route_labels"], field="expected_route_labels"
                ),
                expected_product_codes=_string_list(
                    item["expected_product_codes"], field="expected_product_codes"
                ),
                expected_comparable_axes=_string_list(
                    item["expected_comparable_axes"], field="expected_comparable_axes"
                ),
                retrieval_relevant_sources=_string_list(
                    item["retrieval_relevant_sources"],
                    field="retrieval_relevant_sources",
                ),
                expect_no_retrieval=expect_no_retrieval,
            )
        )
    return tuple(parsed)


class LocalGroundingEvaluator:
    def __init__(
        self,
        *,
        knowledge_dir: Path,
        product_evidence_path: Path,
        source_governance_path: Path = DEFAULT_SOURCE_GOVERNANCE,
    ) -> None:
        manifest = knowledge_dir / "manifest.json"
        self._retriever = LocalRetriever.from_directory(
            knowledge_dir,
            source_governance_path=source_governance_path,
        )
        self._index = ProductEvidenceIndex.from_path(
            product_evidence_path,
            expected_manifest_sha256=_sha256(manifest),
            expected_record_count=100,
        )
        self._resolver = ProductIdentityResolver.from_index(self._index)

    def evaluate(self, case: GroundingCase) -> LocalEvaluationResult:
        route = classify_question(case.question)
        resolution = self._resolver.resolve(case.question)
        resolved_codes = resolution.product_codes
        suitability = (
            assess_suitability(
                question=case.question,
                index=self._index,
                requested_product_codes=resolved_codes,
            )
            if "conditional_recommendation" in route.labels
            else None
        )
        planned_codes = resolved_codes or (
            suitability.candidate_codes if suitability is not None else ()
        )
        product_codes = planned_codes or requested_product_codes(case.question)
        axes = requested_axes(case.question)
        if "conditional_recommendation" in route.labels:
            axes = explicit_requested_axes(case.question) or (
                "identity",
                "category",
                "risk",
                "class",
            )
        plan = plan_question(
            case.question,
            route,
            resolved_product_codes=planned_codes,
        )
        documents = retrieve_for_queries(
            self._retriever,
            tuple(
                (subquestion.query_text, frozenset(subquestion.route.source_groups))
                for subquestion in plan.subquestions
            ),
            top_k=6,
            resolved_product_codes=planned_codes,
        )
        retrieved_source_ids = tuple(dict.fromkeys(item.source_id for item in documents))
        structured_axes = (
            self._index.evidence_coverage(product_codes=product_codes, axes=axes)
            if product_codes
            else {}
        )
        structured_identities = (
            self._index.canonical_identities(product_codes=product_codes)
            if product_codes
            else {}
        )
        calculation = (
            calculate_pension_tax_credit(case.question)
            if route.requires_calculation
            else None
        )
        comparison = (
            compare_product_facts(
                index=self._index,
                product_codes=product_codes,
                axes=axes,
                question=case.question,
            )
            if "product_comparison" in route.labels and len(product_codes) >= 2
            else None
        )
        assessment = assess_evidence(
            plan=plan,
            route=route,
            documents=documents,
            structured_axes=structured_axes,
            structured_identities=structured_identities,
            calculation=calculation,
            identity_conditions=resolution.required_conditions(
                comparison="product_comparison" in route.labels
            )
            + (suitability.required_conditions if suitability is not None else ()),
            comparison=comparison,
            suitability=suitability,
        )
        supported_refs = tuple(
            sorted(
                {
                    ref
                    for coverage in assessment.coverage
                    if coverage.status == "supported"
                    for ref in coverage.evidence_refs
                }
            )
        )
        failures: list[str] = []
        if assessment.status != case.expected_status:
            failures.append(
                f"status expected={case.expected_status} actual={assessment.status}"
            )
        if route.labels != case.expected_route_labels:
            failures.append(
                f"routes expected={case.expected_route_labels} actual={route.labels}"
            )
        if product_codes != case.expected_product_codes:
            failures.append(
                f"products expected={case.expected_product_codes} actual={product_codes}"
            )
        comparable_axes = (
            tuple(sorted(comparison.comparable_axes)) if comparison is not None else ()
        )
        if comparable_axes != tuple(sorted(case.expected_comparable_axes)):
            failures.append(
                "comparable_axes "
                f"expected={tuple(sorted(case.expected_comparable_axes))} actual={comparable_axes}"
            )
        performed = calculation is not None and calculation.performed
        if performed != case.calculation_expected:
            failures.append(
                f"calculation_expected={case.calculation_expected} actual={performed}"
            )
        for prefix in case.required_evidence_prefixes:
            if not any(ref.startswith(prefix) for ref in supported_refs):
                failures.append(f"missing evidence prefix: {prefix}")
        for required in case.required_conditions:
            if not any(required in condition for condition in assessment.missing_conditions):
                failures.append(f"missing required condition: {required}")
        if case.expect_no_retrieval and retrieved_source_ids:
            failures.append(
                f"expected no retrieval, got sources={retrieved_source_ids}"
            )
        return LocalEvaluationResult(
            case_id=case.case_id,
            passed=not failures,
            actual_status=assessment.status,
            failures=tuple(failures),
            supported_evidence=supported_refs,
            retrieved_source_ids=retrieved_source_ids,
        )


def evaluate_cases(
    cases: tuple[GroundingCase, ...],
    *,
    knowledge_dir: Path = DEFAULT_KNOWLEDGE,
    product_evidence_path: Path = DEFAULT_PRODUCT_EVIDENCE,
) -> tuple[LocalEvaluationResult, ...]:
    evaluator = LocalGroundingEvaluator(
        knowledge_dir=knowledge_dir,
        product_evidence_path=product_evidence_path,
    )
    return tuple(evaluator.evaluate(case) for case in cases)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--knowledge", type=Path, default=DEFAULT_KNOWLEDGE)
    parser.add_argument("--product-evidence", type=Path, default=DEFAULT_PRODUCT_EVIDENCE)
    args = parser.parse_args()
    cases = load_cases(args.cases.resolve())
    results = evaluate_cases(
        cases,
        knowledge_dir=args.knowledge.resolve(),
        product_evidence_path=args.product_evidence.resolve(),
    )
    print(
        json.dumps(
            {
                "evaluator_version": LOCAL_EVALUATOR_VERSION,
                "case_count": len(results),
                "passed": sum(result.passed for result in results),
                "failed": sum(not result.passed for result in results),
                "results": [result.as_dict() for result in results],
                "hcx_calls": 0,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if all(result.passed for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
