#!/usr/bin/env python3
"""Evaluate colloquial pension routing, query anchors, and retrieval without HCX."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.planning import (  # noqa: E402
    PLANNER_VERSION,
    QUERY_ANCHOR_VERSION,
    plan_question,
)
from app.evidence import assess_evidence  # noqa: E402
from app.retrieval import LocalRetriever, retrieve_for_queries  # noqa: E402
from app.routing import ROUTER_VERSION, classify_question  # noqa: E402
from scripts.evaluate_local_grounding import (  # noqa: E402
    DEFAULT_KNOWLEDGE,
    DEFAULT_SOURCE_GOVERNANCE,
)


EVALUATOR_VERSION = "pension-lifecycle-paraphrase-eval-v3"
DEFAULT_CASES = PROJECT_ROOT / "tests" / "fixtures" / "lifecycle_paraphrase_holdout_cases.json"
DEFAULT_SUITE = PROJECT_ROOT / "benchmarks" / "config" / "benchmark-suite.json"
REQUIRED_DOMAINS = frozenset(
    {
        "contribution_onboarding",
        "plan_design_governance",
        "investing_operations",
        "transfer_portability",
        "withdrawal_liquidity",
        "retirement_payout",
        "taxation",
        "protection_remedies",
    }
)
_CASE_FIELDS = frozenset(
    {
        "case_id",
        "kind",
        "expected_evidence_status",
        "domain",
        "question",
        "required_route_labels",
        "expected_source_groups",
        "required_query_terms",
        "forbidden_query_terms",
        "relevant_source_ids",
        "minimum_relevant_sources",
    }
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _strings(value: Any, *, field: str, allow_empty: bool = True) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ValueError(f"{field} must be an array of non-empty strings")
    if not allow_empty and not value:
        raise ValueError(f"{field} must not be empty")
    normalized = tuple(_normalize(item) for item in value)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field} must contain unique strings")
    return tuple(value)


@dataclass(frozen=True, slots=True)
class LifecycleParaphraseCase:
    case_id: str
    kind: str
    expected_evidence_status: str
    domain: str
    question: str
    required_route_labels: tuple[str, ...]
    expected_source_groups: tuple[str, ...]
    required_query_terms: tuple[str, ...]
    forbidden_query_terms: tuple[str, ...]
    relevant_source_ids: tuple[str, ...]
    minimum_relevant_sources: int


def load_cases(path: Path) -> tuple[LifecycleParaphraseCase, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 2:
        raise ValueError("unsupported lifecycle-paraphrase case schema")
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("cases must be a non-empty array")

    cases: list[LifecycleParaphraseCase] = []
    seen: set[str] = set()
    for position, item in enumerate(raw_cases):
        if not isinstance(item, dict) or set(item) != _CASE_FIELDS:
            raise ValueError(f"case[{position}] has invalid fields")
        case_id = item["case_id"]
        kind = item["kind"]
        expected_evidence_status = item["expected_evidence_status"]
        domain = item["domain"]
        question = item["question"]
        if not isinstance(case_id, str) or not case_id or case_id in seen:
            raise ValueError(f"case[{position}] has invalid or duplicate case_id")
        if kind not in {"positive", "negative"}:
            raise ValueError(f"case[{position}] has invalid kind")
        expected_status = "answerable" if kind == "positive" else "not_evaluated"
        if expected_evidence_status != expected_status:
            raise ValueError(
                f"case[{position}] expected_evidence_status must be {expected_status}"
            )
        if domain not in REQUIRED_DOMAINS:
            raise ValueError(f"case[{position}] has invalid domain")
        if not isinstance(question, str) or not question.strip():
            raise ValueError(f"case[{position}] has invalid question")
        minimum = item["minimum_relevant_sources"]
        if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 0:
            raise ValueError(f"case[{position}] has invalid minimum_relevant_sources")

        required_terms = _strings(
            item["required_query_terms"], field="required_query_terms"
        )
        forbidden_terms = _strings(
            item["forbidden_query_terms"], field="forbidden_query_terms"
        )
        if set(map(_normalize, required_terms)) & set(map(_normalize, forbidden_terms)):
            raise ValueError(f"case[{position}] has overlapping query terms")
        relevant_ids = _strings(item["relevant_source_ids"], field="relevant_source_ids")
        if kind == "positive" and (not relevant_ids or minimum < 1 or minimum > len(relevant_ids)):
            raise ValueError(f"case[{position}] has invalid positive qrel")
        if kind == "negative" and (relevant_ids or minimum != 0):
            raise ValueError(f"case[{position}] has invalid negative qrel")

        seen.add(case_id)
        cases.append(
            LifecycleParaphraseCase(
                case_id=case_id,
                kind=kind,
                expected_evidence_status=expected_evidence_status,
                domain=domain,
                question=question,
                required_route_labels=_strings(
                    item["required_route_labels"], field="required_route_labels"
                ),
                expected_source_groups=_strings(
                    item["expected_source_groups"],
                    field="expected_source_groups",
                    allow_empty=False,
                ),
                required_query_terms=required_terms,
                forbidden_query_terms=forbidden_terms,
                relevant_source_ids=relevant_ids,
                minimum_relevant_sources=minimum,
            )
        )
    return tuple(cases)


def evaluate_cases(
    cases: tuple[LifecycleParaphraseCase, ...],
    *,
    knowledge_dir: Path = DEFAULT_KNOWLEDGE,
    source_governance_path: Path = DEFAULT_SOURCE_GOVERNANCE,
    top_k: int = 6,
) -> tuple[dict[str, Any], ...]:
    if top_k < 1:
        raise ValueError("top_k must be positive")
    retriever = LocalRetriever.from_directory(
        knowledge_dir,
        source_governance_path=source_governance_path,
    )
    results: list[dict[str, Any]] = []
    for case in cases:
        route = classify_question(case.question)
        plan = plan_question(case.question, route)
        documents = retrieve_for_queries(
            retriever,
            tuple(
                (subquestion.query_text, frozenset(subquestion.route.source_groups))
                for subquestion in plan.subquestions
            ),
            top_k=top_k,
        )
        ranked_ids = tuple(dict.fromkeys(item.source_id for item in documents))
        combined_query = "\n".join(
            subquestion.query_text for subquestion in plan.subquestions
        )
        normalized_query = _normalize(combined_query)
        route_covered = set(case.required_route_labels).issubset(route.labels)
        source_groups_exact = route.source_groups == frozenset(case.expected_source_groups)
        required_terms_present = all(
            _normalize(term) in normalized_query for term in case.required_query_terms
        )
        forbidden_terms_absent = all(
            _normalize(term) not in normalized_query for term in case.forbidden_query_terms
        )
        relevant_found = tuple(
            source_id for source_id in ranked_ids if source_id in case.relevant_source_ids
        )
        assessment = (
            assess_evidence(
                plan=plan,
                route=route,
                documents=documents,
                structured_axes={},
                structured_identities={},
                calculation=None,
            )
            if case.kind == "positive"
            else None
        )
        retrieval_passed = (
            len(relevant_found) >= case.minimum_relevant_sources
            if case.kind == "positive"
            else True
        )
        evidence_status = assessment.status if assessment is not None else "not_evaluated"
        evidence_status_exact = evidence_status == case.expected_evidence_status
        evidence_generation_eligible = (
            assessment.should_generate if assessment is not None else True
        )
        evidence_source_ids = tuple(
            dict.fromkeys(
                ref.split(":", maxsplit=1)[0]
                for item in (assessment.coverage if assessment is not None else ())
                for ref in item.evidence_refs
            )
        )
        evidence_qrel_aligned = (
            bool(set(case.relevant_source_ids) & set(evidence_source_ids))
            if case.kind == "positive"
            else True
        )

        failures: list[str] = []
        if not route_covered:
            failures.append(
                "required routes missing: "
                f"required={list(case.required_route_labels)} actual={list(route.labels)}"
            )
        if not source_groups_exact:
            failures.append(
                "source groups differ: "
                f"expected={list(case.expected_source_groups)} "
                f"actual={sorted(route.source_groups)}"
            )
        if not required_terms_present:
            missing = [
                term
                for term in case.required_query_terms
                if _normalize(term) not in normalized_query
            ]
            failures.append(f"required query terms missing: {missing}")
        if not forbidden_terms_absent:
            present = [
                term
                for term in case.forbidden_query_terms
                if _normalize(term) in normalized_query
            ]
            failures.append(f"forbidden query terms present: {present}")
        if not retrieval_passed:
            failures.append(
                "relevant sources missing: "
                f"minimum={case.minimum_relevant_sources} "
                f"relevant={list(case.relevant_source_ids)} ranked={list(ranked_ids)}"
            )
        if not evidence_status_exact:
            failures.append(
                "evidence status differs: "
                f"expected={case.expected_evidence_status} actual={evidence_status}"
            )
        if case.kind == "positive" and not evidence_generation_eligible:
            failures.append("positive case is not eligible for generation")
        if not evidence_qrel_aligned:
            failures.append(
                "assessor evidence does not overlap retrieval qrels: "
                f"qrels={list(case.relevant_source_ids)} "
                f"evidence_sources={list(evidence_source_ids)}"
            )

        results.append(
            {
                "case_id": case.case_id,
                "kind": case.kind,
                "domain": case.domain,
                "passed": not failures,
                "route_covered": route_covered,
                "source_groups_exact": source_groups_exact,
                "required_query_terms_present": required_terms_present,
                "forbidden_query_terms_absent": forbidden_terms_absent,
                "retrieval_passed": retrieval_passed,
                "actual_route_labels": list(route.labels),
                "actual_source_groups": sorted(route.source_groups),
                "query_anchors": [
                    anchor
                    for subquestion in plan.subquestions
                    for anchor in subquestion.query_anchors
                ],
                "ranked_source_ids": list(ranked_ids),
                "relevant_sources_found": list(relevant_found),
                "expected_evidence_status": case.expected_evidence_status,
                "evidence_status": evidence_status,
                "evidence_status_exact": evidence_status_exact,
                "evidence_generation_eligible": evidence_generation_eligible,
                "evidence_source_ids": list(evidence_source_ids),
                "evidence_qrel_aligned": evidence_qrel_aligned,
                "evidence_should_generate": (
                    assessment.should_generate if assessment is not None else None
                ),
                "evidence_coverage": (
                    [
                        {
                            "requirement_id": item.requirement_id,
                            "status": item.status,
                            "evidence_refs": list(item.evidence_refs),
                        }
                        for item in assessment.coverage
                    ]
                    if assessment is not None
                    else []
                ),
                "failures": failures,
            }
        )
    return tuple(results)


def _rate(rows: tuple[dict[str, Any], ...], field: str) -> float:
    return sum(bool(row[field]) for row in rows) / len(rows) if rows else 0.0


def summarize_results(
    cases: tuple[LifecycleParaphraseCase, ...],
    results: tuple[dict[str, Any], ...],
) -> dict[str, float]:
    positives = tuple(row for row in results if row["kind"] == "positive")
    negatives = tuple(row for row in results if row["kind"] == "negative")
    positive_domains = {
        domain: tuple(
            row
            for row in positives
            if row["domain"] == domain
        )
        for domain in REQUIRED_DOMAINS
    }
    return {
        "positive_retrieval_success_rate": _rate(positives, "retrieval_passed"),
        "positive_route_coverage_rate": _rate(positives, "route_covered"),
        "source_group_exact_match_rate": _rate(results, "source_groups_exact"),
        "required_query_term_coverage_rate": _rate(
            positives, "required_query_terms_present"
        ),
        "negative_anchor_rejection_rate": _rate(
            negatives, "forbidden_query_terms_absent"
        ),
        "domain_retrieval_coverage_rate": (
            sum(
                bool(rows) and all(row["retrieval_passed"] for row in rows)
                for rows in positive_domains.values()
            )
            / len(REQUIRED_DOMAINS)
        ),
        "positive_evidence_status_accuracy": _rate(
            positives, "evidence_status_exact"
        ),
        "positive_generation_eligibility_rate": _rate(
            positives, "evidence_generation_eligible"
        ),
        "positive_evidence_qrel_alignment_rate": _rate(
            positives, "evidence_qrel_aligned"
        ),
        "domain_evidence_coverage_rate": (
            sum(
                bool(rows)
                and all(
                    row["evidence_status_exact"]
                    and row["evidence_generation_eligible"]
                    and row["evidence_qrel_aligned"]
                    for row in rows
                )
                for rows in positive_domains.values()
            )
            / len(REQUIRED_DOMAINS)
        ),
    }


def _load_configuration(path: Path) -> tuple[int, dict[str, float], int]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("unsupported benchmark suite schema")
    config = payload.get("lifecycle_paraphrase_holdout")
    if not isinstance(config, dict):
        raise ValueError("suite lifecycle_paraphrase_holdout configuration is missing")
    top_k = config.get("k")
    thresholds = config.get("thresholds")
    maximum_hcx_calls = config.get("maximum_hcx_calls")
    required_thresholds = {
        "positive_retrieval_success_rate",
        "positive_route_coverage_rate",
        "source_group_exact_match_rate",
        "required_query_term_coverage_rate",
        "negative_anchor_rejection_rate",
        "domain_retrieval_coverage_rate",
        "positive_evidence_status_accuracy",
        "positive_generation_eligibility_rate",
        "positive_evidence_qrel_alignment_rate",
        "domain_evidence_coverage_rate",
    }
    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
        raise ValueError("lifecycle_paraphrase_holdout.k must be a positive integer")
    if not isinstance(thresholds, dict) or set(thresholds) != required_thresholds:
        raise ValueError("lifecycle_paraphrase_holdout thresholds are invalid")
    parsed: dict[str, float] = {}
    for name, value in thresholds.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"threshold {name} must be numeric")
        number = float(value)
        if not 0.0 <= number <= 1.0:
            raise ValueError(f"threshold {name} must be between zero and one")
        parsed[name] = number
    if maximum_hcx_calls != 0:
        raise ValueError("lifecycle paraphrase evaluation must have a zero HCX budget")
    return top_k, parsed, maximum_hcx_calls


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--knowledge", type=Path, default=DEFAULT_KNOWLEDGE)
    parser.add_argument("--source-governance", type=Path, default=DEFAULT_SOURCE_GOVERNANCE)
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    args = parser.parse_args()

    cases_path = args.cases.resolve()
    top_k, thresholds, maximum_hcx_calls = _load_configuration(args.suite.resolve())
    cases = load_cases(cases_path)
    results = evaluate_cases(
        cases,
        knowledge_dir=args.knowledge.resolve(),
        source_governance_path=args.source_governance.resolve(),
        top_k=top_k,
    )
    metrics = summarize_results(cases, results)
    failures = [
        f"{name}={metrics[name]:.6f} below threshold={threshold:.6f}"
        for name, threshold in thresholds.items()
        if metrics[name] < threshold
    ]
    failed_cases = sum(not row["passed"] for row in results)
    report = {
        "evaluator_version": EVALUATOR_VERSION,
        "router_version": ROUTER_VERSION,
        "planner_version": PLANNER_VERSION,
        "query_anchor_version": QUERY_ANCHOR_VERSION,
        "cases_sha256": _sha256(cases_path),
        "k": top_k,
        "case_count": len(results),
        "positive_case_count": sum(case.kind == "positive" for case in cases),
        "negative_case_count": sum(case.kind == "negative" for case in cases),
        "passed": len(results) - failed_cases,
        "failed": failed_cases,
        **metrics,
        "thresholds": thresholds,
        "thresholds_passed": not failures,
        "hcx_calls": maximum_hcx_calls,
        "failures": failures,
        "results": results,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not failures and failed_cases == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
