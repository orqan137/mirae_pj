#!/usr/bin/env python3
"""Evaluate requirement-preserving public evidence rendering without HCX."""

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
from app.evidence import EvidenceAssessment, EvidenceCoverage  # noqa: E402
from app.evidence_rendering import (  # noqa: E402
    PUBLIC_EVIDENCE_RENDERER_VERSION,
    PublicEvidenceRenderResult,
    render_public_evidence_result,
)
from app.retrieval import RetrievedDocument  # noqa: E402


CONTEXT_COVERAGE_EVALUATOR_VERSION = "pension-context-coverage-v2"
DEFAULT_CASES = PROJECT_ROOT / "tests" / "fixtures" / "context_coverage_cases.json"
DEFAULT_SUITE = PROJECT_ROOT / "benchmarks" / "config" / "benchmark-suite.json"
_CASE_FIELDS = frozenset(
    {
        "case_id",
        "scenario",
        "max_chars",
        "expected_complete",
        "required_markers",
        "forbidden_markers",
    }
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _strings(value: Any, *, field: str, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field} must be an array of strings")
    if not allow_empty and not value:
        raise ValueError(f"{field} must not be empty")
    if any(not item for item in value):
        raise ValueError(f"{field} must not contain empty strings")
    return tuple(value)


@dataclass(frozen=True, slots=True)
class ContextCoverageCase:
    case_id: str
    scenario: str
    max_chars: int
    expected_complete: bool
    required_markers: tuple[str, ...]
    forbidden_markers: tuple[str, ...]


def load_cases(path: Path) -> tuple[ContextCoverageCase, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("unsupported context coverage case schema")
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("cases must be a non-empty array")
    parsed: list[ContextCoverageCase] = []
    seen: set[str] = set()
    allowed_scenarios = {
        "calculation_comparison_general",
        "three_general_requirements",
        "structured_product_and_general",
    }
    for position, item in enumerate(raw_cases):
        if not isinstance(item, dict) or set(item) != _CASE_FIELDS:
            raise ValueError(f"case[{position}] has invalid fields")
        case_id = item["case_id"]
        scenario = item["scenario"]
        if not isinstance(case_id, str) or not case_id or case_id in seen:
            raise ValueError(f"case[{position}] has invalid or duplicate case_id")
        seen.add(case_id)
        if scenario not in allowed_scenarios:
            raise ValueError(f"case[{position}] has invalid scenario")
        max_chars = item["max_chars"]
        if not isinstance(max_chars, int) or max_chars < 300:
            raise ValueError(f"case[{position}] has invalid max_chars")
        if not isinstance(item["expected_complete"], bool):
            raise ValueError(f"case[{position}] has invalid expected_complete")
        parsed.append(
            ContextCoverageCase(
                case_id=case_id,
                scenario=scenario,
                max_chars=max_chars,
                expected_complete=item["expected_complete"],
                required_markers=_strings(
                    item["required_markers"], field="required_markers"
                ),
                forbidden_markers=_strings(
                    item["forbidden_markers"],
                    field="forbidden_markers",
                    allow_empty=True,
                ),
            )
        )
    return tuple(parsed)


def _document(
    source_id: str,
    text: str,
    *,
    source_group: str = "general",
    index: int = 1,
) -> RetrievedDocument:
    return RetrievedDocument(
        source=f"{source_group}/{source_id}.txt",
        source_id=source_id,
        source_path=f"일반문서/{source_id}.pdf",
        source_group=source_group,
        title="context coverage fixture",
        index=index,
        page=None,
        text=text,
        score=1.0,
        locator="sheet=안내",
    )


def _coverage(
    requirement_id: str,
    subquestion_id: str,
    label: str,
    *refs: str,
) -> EvidenceCoverage:
    return EvidenceCoverage(
        requirement_id=requirement_id,
        subquestion_id=subquestion_id,
        label=label,
        status="supported",
        evidence_refs=refs,
    )


def _mixed(case: ContextCoverageCase) -> PublicEvidenceRenderResult:
    calculation_context = calculate_pension_tax_credit(
        "총급여가 5,500만원이고 IRP에 900만원을 납입했습니다. 세액공제액을 계산해 주세요."
    ).render_context()
    comparison_context = "\n".join(
        (
            "[TYPED PRODUCT COMPARISON]",
            "comparator_version=context-fixture",
            "product_code | class | metric | value | unit | as_of_date | "
            "period_years | aggregation | tax_basis | source",
            "KR0000000000 | A | annual_total_fee_rate | 0.31 | percent_per_year | "
            "2025-01-01 | | stated_rate | not_applicable | a.pdf:page=4",
            "KR0000000001 | A | annual_total_fee_rate | 0.42 | percent_per_year | "
            "2025-01-01 | | stated_rate | not_applicable | b.pdf:page=4",
        )
    )
    assessment = EvidenceAssessment(
        version="context-fixture",
        status="answerable",
        coverage=(
            _coverage(
                "calc",
                "sq1",
                "세액공제 계산",
                "calculator:pension-tax-credit-doc41-v3",
            ),
            _coverage("fees", "sq2", "두 상품 총보수", "comparison:fees"),
            _coverage(
                "withdrawal",
                "sq3",
                "IRP 중도인출 조건",
                "doc20:sheet=안내:chunk=1",
            ),
        ),
        missing_conditions=(),
    )
    return render_public_evidence_result(
        question="세액공제 계산, 두 상품 총보수 비교, IRP 중도인출 조건",
        documents=[
            _document(
                "doc20",
                "IRP 중도인출은 법정 사유에 해당할 때만 가능하고 서류 확인이 필요합니다.",
            )
        ],
        assessment=assessment,
        comparison_context=comparison_context,
        calculation_context=calculation_context,
        max_chars=case.max_chars,
    )


def _three_general(case: ContextCoverageCase) -> PublicEvidenceRenderResult:
    selected = (
        ("doc15", "DB형과 DC형 차이"),
        ("doc41", "연금저축 세액공제 소득 요건"),
        ("doc20", "IRP 중도인출 법정 사유"),
    )
    backups = (
        ("doc11", "DB DC 보조"),
        ("doc44", "세액공제 보조"),
        ("doc22", "중도인출 보조"),
    )
    assessment = EvidenceAssessment(
        version="context-fixture",
        status="answerable",
        coverage=tuple(
            _coverage(label, f"sq{position}", label, f"{source}:sheet=안내:chunk=1")
            for position, (source, label) in enumerate(selected, start=1)
        ),
        missing_conditions=(),
    )
    return render_public_evidence_result(
        question="DB DC 차이, 세액공제 조건, 중도인출 사유",
        documents=[_document(source, text) for source, text in (*selected, *backups)],
        assessment=assessment,
        max_chars=case.max_chars,
    )


def _structured_and_general(case: ContextCoverageCase) -> PublicEvidenceRenderResult:
    assessment = EvidenceAssessment(
        version="context-fixture",
        status="answerable",
        coverage=(
            _coverage(
                "identity",
                "sq1",
                "상품 identity",
                "structured:KR0000000000:identity",
            ),
            _coverage(
                "withdrawal",
                "sq2",
                "IRP 중도인출 서류",
                "doc20:sheet=안내:chunk=1",
            ),
        ),
        missing_conditions=(),
    )
    product = _document(
        "KR0000000000",
        "운용전문인력 변경내역",
        source_group="product",
    )
    return render_public_evidence_result(
        question="상품을 설명하고 IRP 중도인출 서류도 알려주세요",
        documents=[product, _document("doc20", "IRP 중도인출에는 필요한 서류 확인이 필요합니다.")],
        assessment=assessment,
        structured_context="[PRODUCT EVIDENCE]\nproduct_code=KR0000000000\ncategory=채권형",
        max_chars=case.max_chars,
    )


def render_case(case: ContextCoverageCase) -> PublicEvidenceRenderResult:
    renderers = {
        "calculation_comparison_general": _mixed,
        "three_general_requirements": _three_general,
        "structured_product_and_general": _structured_and_general,
    }
    return renderers[case.scenario](case)


def evaluate_case(case: ContextCoverageCase) -> dict[str, object]:
    render_result = render_case(case)
    rendered = render_result.context
    missing = [marker for marker in case.required_markers if marker not in rendered]
    forbidden = [marker for marker in case.forbidden_markers if marker in rendered]
    over_budget = len(rendered) > case.max_chars
    completeness_matches = render_result.complete is case.expected_complete
    return {
        "case_id": case.case_id,
        "passed": not missing and not forbidden and not over_budget and completeness_matches,
        "rendered_chars": len(rendered),
        "max_chars": case.max_chars,
        "required_markers": len(case.required_markers),
        "missing_required_markers": missing,
        "forbidden_marker_violations": forbidden,
        "over_budget": over_budget,
        "expected_complete": case.expected_complete,
        "actual_complete": render_result.complete,
        "missing_sections": list(render_result.missing_sections),
        "completeness_matches": completeness_matches,
    }


def _thresholds(path: Path) -> tuple[float, float, float, float]:
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    config = payload.get("context_coverage") if isinstance(payload, dict) else None
    if not isinstance(config, dict):
        raise ValueError("suite is missing context_coverage config")
    names = (
        "minimum_required_marker_recall",
        "minimum_forbidden_marker_rejection_rate",
        "minimum_budget_adherence_rate",
        "minimum_completeness_decision_accuracy",
    )
    values = tuple(config.get(name) for name in names)
    if not all(isinstance(value, (int, float)) for value in values):
        raise ValueError("context coverage thresholds must be numeric")
    parsed = tuple(float(value) for value in values)
    if not all(0.0 <= value <= 1.0 for value in parsed):
        raise ValueError("context coverage thresholds must be between zero and one")
    return parsed[0], parsed[1], parsed[2], parsed[3]


def evaluate(
    cases: tuple[ContextCoverageCase, ...],
    *,
    minimum_required_marker_recall: float,
    minimum_forbidden_marker_rejection_rate: float,
    minimum_budget_adherence_rate: float,
    minimum_completeness_decision_accuracy: float,
) -> dict[str, object]:
    results = [evaluate_case(case) for case in cases]
    required_total = sum(int(result["required_markers"]) for result in results)
    missing_total = sum(len(result["missing_required_markers"]) for result in results)
    forbidden_total = sum(len(case.forbidden_markers) for case in cases)
    forbidden_violations = sum(
        len(result["forbidden_marker_violations"]) for result in results
    )
    required_recall = (required_total - missing_total) / required_total
    forbidden_rejection = (
        (forbidden_total - forbidden_violations) / forbidden_total
        if forbidden_total
        else 1.0
    )
    budget_adherence = sum(not bool(result["over_budget"]) for result in results) / len(
        results
    )
    completeness_accuracy = sum(
        bool(result["completeness_matches"]) for result in results
    ) / len(results)
    thresholds_passed = (
        required_recall >= minimum_required_marker_recall
        and forbidden_rejection >= minimum_forbidden_marker_rejection_rate
        and budget_adherence >= minimum_budget_adherence_rate
        and completeness_accuracy >= minimum_completeness_decision_accuracy
    )
    passed = sum(bool(result["passed"]) for result in results)
    return {
        "evaluator_version": CONTEXT_COVERAGE_EVALUATOR_VERSION,
        "renderer_version": PUBLIC_EVIDENCE_RENDERER_VERSION,
        "case_count": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "required_marker_recall": required_recall,
        "forbidden_marker_rejection_rate": forbidden_rejection,
        "budget_adherence_rate": budget_adherence,
        "completeness_decision_accuracy": completeness_accuracy,
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
    thresholds = _thresholds(args.suite.resolve())
    report = evaluate(
        load_cases(cases_path),
        minimum_required_marker_recall=thresholds[0],
        minimum_forbidden_marker_rejection_rate=thresholds[1],
        minimum_budget_adherence_rate=thresholds[2],
        minimum_completeness_decision_accuracy=thresholds[3],
    )
    report["cases_sha256"] = _sha256(cases_path)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["thresholds_passed"] and report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
