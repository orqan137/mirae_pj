#!/usr/bin/env python3
"""Evaluate frozen pension-rule paraphrases and hard negatives without HCX."""

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
from app.routing import classify_question  # noqa: E402


RULE_CLAIM_HOLDOUT_EVALUATOR_VERSION = "pension-rule-claim-holdout-v1"
DEFAULT_CASES = PROJECT_ROOT / "tests" / "fixtures" / "rule_claim_holdout_cases.json"
DEFAULT_SUITE = PROJECT_ROOT / "benchmarks" / "config" / "benchmark-suite.json"
_CASE_FIELDS = frozenset(
    {
        "case_id",
        "stratum",
        "question",
        "answer",
        "evidence_context_file",
        "expected_valid",
    }
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True, slots=True)
class RuleClaimHoldoutCase:
    case_id: str
    stratum: str
    question: str
    answer: str
    evidence_context_path: Path
    expected_valid: bool


def load_cases(path: Path) -> tuple[RuleClaimHoldoutCase, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("unsupported rule claim holdout schema")
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("cases must be a non-empty array")
    fixture_root = path.parent.resolve()
    parsed: list[RuleClaimHoldoutCase] = []
    seen: set[str] = set()
    for position, item in enumerate(raw_cases):
        if not isinstance(item, dict) or set(item) != _CASE_FIELDS:
            raise ValueError(f"case[{position}] has invalid fields")
        string_fields = ("case_id", "stratum", "question", "answer")
        if not all(isinstance(item[field], str) and item[field].strip() for field in string_fields):
            raise ValueError(f"case[{position}] has invalid string fields")
        if item["case_id"] in seen:
            raise ValueError(f"duplicate case_id: {item['case_id']}")
        seen.add(item["case_id"])
        if item["stratum"] not in {"supported_paraphrase", "unsafe_hard_negative"}:
            raise ValueError(f"case[{position}] has invalid stratum")
        if not isinstance(item["expected_valid"], bool):
            raise ValueError(f"case[{position}].expected_valid must be boolean")
        expected_for_stratum = item["stratum"] == "supported_paraphrase"
        if item["expected_valid"] is not expected_for_stratum:
            raise ValueError(f"case[{position}] stratum disagrees with expected_valid")
        context_name = item["evidence_context_file"]
        if not isinstance(context_name, str) or not context_name:
            raise ValueError(f"case[{position}] has invalid evidence context")
        context_path = (fixture_root / context_name).resolve()
        if not context_path.is_relative_to(fixture_root) or not context_path.is_file():
            raise ValueError(f"case[{position}] evidence context is unsafe or missing")
        parsed.append(
            RuleClaimHoldoutCase(
                case_id=item["case_id"],
                stratum=item["stratum"],
                question=item["question"],
                answer=item["answer"],
                evidence_context_path=context_path,
                expected_valid=item["expected_valid"],
            )
        )
    return tuple(parsed)


def _thresholds(path: Path) -> tuple[float, float]:
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    config = payload.get("rule_claim_holdout") if isinstance(payload, dict) else None
    if not isinstance(config, dict):
        raise ValueError("suite is missing rule_claim_holdout config")
    positive = config.get("minimum_positive_acceptance_rate")
    negative = config.get("minimum_unsafe_rejection_rate")
    if not all(isinstance(value, (int, float)) for value in (positive, negative)):
        raise ValueError("rule claim holdout thresholds must be numeric")
    if not all(0.0 <= float(value) <= 1.0 for value in (positive, negative)):
        raise ValueError("rule claim holdout thresholds must be between zero and one")
    return float(positive), float(negative)


def evaluate_case(case: RuleClaimHoldoutCase) -> dict[str, object]:
    result = validate_answer(
        answer=case.answer,
        question=case.question,
        evidence_context=case.evidence_context_path.read_text(encoding="utf-8"),
        route=classify_question(case.question),
        assessment=EvidenceAssessment(
            version="rule-claim-holdout",
            status="answerable",
            coverage=(),
            missing_conditions=(),
        ),
    )
    failure_kinds = tuple(dict.fromkeys(claim.kind for claim in result.unsupported_claims))
    return {
        "case_id": case.case_id,
        "stratum": case.stratum,
        "passed": result.valid is case.expected_valid,
        "expected_valid": case.expected_valid,
        "actual_valid": result.valid,
        "rule_claims_checked": result.rule_claims_checked,
        "actual_failure_kinds": list(failure_kinds),
    }


def evaluate(
    cases: tuple[RuleClaimHoldoutCase, ...],
    *,
    minimum_positive_acceptance_rate: float,
    minimum_unsafe_rejection_rate: float,
) -> dict[str, object]:
    results = [evaluate_case(case) for case in cases]
    positives = [result for result in results if bool(result["expected_valid"])]
    negatives = [result for result in results if not bool(result["expected_valid"])]
    if not positives or not negatives:
        raise ValueError("holdout must include positive and negative strata")
    positive_acceptance_rate = sum(
        bool(result["actual_valid"]) for result in positives
    ) / len(positives)
    unsafe_rejection_rate = sum(
        not bool(result["actual_valid"]) for result in negatives
    ) / len(negatives)
    case_passes = sum(bool(result["passed"]) for result in results)
    thresholds_passed = (
        positive_acceptance_rate >= minimum_positive_acceptance_rate
        and unsafe_rejection_rate >= minimum_unsafe_rejection_rate
    )
    return {
        "evaluator_version": RULE_CLAIM_HOLDOUT_EVALUATOR_VERSION,
        "answer_validator_version": ANSWER_VALIDATOR_VERSION,
        "case_count": len(results),
        "positive_count": len(positives),
        "negative_count": len(negatives),
        "passed": case_passes,
        "failed": len(results) - case_passes,
        "positive_acceptance_rate": positive_acceptance_rate,
        "unsafe_rejection_rate": unsafe_rejection_rate,
        "thresholds": {
            "minimum_positive_acceptance_rate": minimum_positive_acceptance_rate,
            "minimum_unsafe_rejection_rate": minimum_unsafe_rejection_rate,
        },
        "thresholds_passed": thresholds_passed,
        "results": results,
        "hcx_calls": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    args = parser.parse_args()
    cases_path = args.cases.resolve()
    suite_path = args.suite.resolve()
    positive_threshold, negative_threshold = _thresholds(suite_path)
    report = evaluate(
        load_cases(cases_path),
        minimum_positive_acceptance_rate=positive_threshold,
        minimum_unsafe_rejection_rate=negative_threshold,
    )
    report["cases_sha256"] = _sha256(cases_path)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["thresholds_passed"] and report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
