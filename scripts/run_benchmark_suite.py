#!/usr/bin/env python3
"""Run reproducible build and evaluation gates with an explicit HCX budget."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SUITE = PROJECT_ROOT / "benchmarks" / "config" / "benchmark-suite.json"
RUNNER_VERSION = "pension-benchmark-suite-v14"
SCENARIO_STAGE_CASES = {
    "scenario_robustness": PROJECT_ROOT
    / "benchmarks"
    / "scenarios"
    / "development-v1.json",
    "scenario_robustness_expansion": PROJECT_ROOT
    / "benchmarks"
    / "scenarios"
    / "development-expansion-v1.json",
    "scenario_safety": PROJECT_ROOT
    / "benchmarks"
    / "scenarios"
    / "development-safety-v1.json",
}
ALLOWED_STAGES = frozenset(
    {
        "source_compile",
        "dependency_integrity",
        "environment_contract",
        "domain_coverage",
        "lifecycle_paraphrase_holdout",
        "scenario_robustness",
        "scenario_robustness_expansion",
        "scenario_safety",
        "contract_tests",
        "planning",
        "tool_selection",
        "suitability",
        "local_grounding",
        "retrieval",
        "retrieval_robustness",
        "abstention",
        "product_parser",
        "answer_guard",
        "rule_claim_holdout",
        "context_coverage",
        "hcx_precision_plan",
        "container_build",
        "container_contract",
        "preflight",
        "endpoint_smoke",
    }
)


@dataclass(frozen=True, slots=True)
class StageResult:
    name: str
    passed: bool
    exit_code: int
    duration_ms: int
    summary: str


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_suite(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("unsupported benchmark suite schema")
    profiles = payload.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        raise ValueError("benchmark profiles must be a non-empty object")
    for name, stages in profiles.items():
        if not isinstance(name, str) or not name:
            raise ValueError("benchmark profile name is invalid")
        if (
            not isinstance(stages, list)
            or not stages
            or not all(isinstance(stage, str) for stage in stages)
            or len(stages) != len(set(stages))
        ):
            raise ValueError(f"benchmark profile {name} has invalid stages")
        unknown = set(stages) - ALLOWED_STAGES
        if unknown:
            raise ValueError(f"benchmark profile {name} has unknown stages: {sorted(unknown)}")
    return payload


def _stage_command(
    name: str,
    *,
    suite_path: Path,
    endpoint: str | None,
    allow_live_hcx: bool,
) -> tuple[list[str], float]:
    python = sys.executable
    commands: dict[str, tuple[list[str], float]] = {
        "source_compile": ([python, "-m", "compileall", "-q", "app", "scripts"], 60.0),
        "dependency_integrity": ([python, "-m", "pip", "check"], 60.0),
        "environment_contract": (
            [python, "scripts/verify_runtime_environment.py", "--profile", "development"],
            60.0,
        ),
        "domain_coverage": (
            [python, "scripts/evaluate_domain_coverage.py", "--suite", str(suite_path)],
            60.0,
        ),
        "lifecycle_paraphrase_holdout": (
            [
                python,
                "scripts/evaluate_lifecycle_paraphrase_holdout.py",
                "--suite",
                str(suite_path),
            ],
            90.0,
        ),
        **{
            stage_name: (
                [
                    python,
                    "scripts/evaluate_scenario_robustness.py",
                    "--cases",
                    str(cases_path),
                    "--suite",
                    str(suite_path),
                ],
                120.0,
            )
            for stage_name, cases_path in SCENARIO_STAGE_CASES.items()
        },
        "contract_tests": ([python, "-m", "pytest"], 240.0),
        "planning": ([python, "scripts/evaluate_planning.py"], 60.0),
        "tool_selection": (
            [python, "scripts/evaluate_tool_selection.py", "--suite", str(suite_path)],
            60.0,
        ),
        "suitability": ([python, "scripts/evaluate_suitability.py"], 60.0),
        "local_grounding": ([python, "scripts/evaluate_local_grounding.py"], 90.0),
        "retrieval": (
            [python, "scripts/evaluate_retrieval.py", "--suite", str(suite_path)],
            90.0,
        ),
        "retrieval_robustness": (
            [
                python,
                "scripts/evaluate_retrieval_robustness.py",
                "--suite",
                str(suite_path),
            ],
            180.0,
        ),
        "abstention": (
            [python, "scripts/evaluate_abstention.py", "--suite", str(suite_path)],
            120.0,
        ),
        "product_parser": (
            [python, "scripts/evaluate_product_parser.py", "--suite", str(suite_path)],
            90.0,
        ),
        "answer_guard": ([python, "scripts/evaluate_answer_guard.py"], 60.0),
        "rule_claim_holdout": (
            [
                python,
                "scripts/evaluate_rule_claim_holdout.py",
                "--suite",
                str(suite_path),
            ],
            60.0,
        ),
        "context_coverage": (
            [
                python,
                "scripts/evaluate_context_coverage.py",
                "--suite",
                str(suite_path),
            ],
            60.0,
        ),
        "hcx_precision_plan": (
            [python, "scripts/evaluate_hcx_precision.py"],
            60.0,
        ),
        "container_build": (["docker", "compose", "build", "pension-agent"], 600.0),
        "container_contract": (
            [
                "docker",
                "compose",
                "run",
                "--rm",
                "--no-deps",
                "pension-agent",
                "python",
                "scripts/verify_runtime_environment.py",
                "--profile",
                "production",
                "--environment-only",
            ],
            120.0,
        ),
        "preflight": ([python, "scripts/preflight.py"], 90.0),
    }
    if name == "endpoint_smoke":
        if endpoint is None:
            raise ValueError("submission profile requires --endpoint")
        if not allow_live_hcx:
            raise ValueError("endpoint smoke requires explicit --allow-live-hcx")
        return [python, "scripts/smoke_test.py", endpoint, "--timeout", "300"], 320.0
    return commands[name]


def _summarize_output(name: str, output: str, *, limit: int = 4_000) -> str:
    normalized = output.strip()
    if not normalized:
        return "completed"
    if name in {
        "planning",
        "tool_selection",
        "environment_contract",
        "container_contract",
        "domain_coverage",
        "lifecycle_paraphrase_holdout",
        "scenario_robustness",
        "scenario_robustness_expansion",
        "scenario_safety",
        "suitability",
        "local_grounding",
        "retrieval",
        "product_parser",
        "answer_guard",
        "rule_claim_holdout",
        "context_coverage",
        "hcx_precision_plan",
    }:
        try:
            payload = json.loads(normalized)
        except json.JSONDecodeError:
            pass
        else:
            if name in {"environment_contract", "container_contract"}:
                environment = payload.get("environment", {})
                repository = payload.get("repository", {})
                return json.dumps(
                    {
                        "passed": payload.get("passed"),
                        "profile": environment.get("profile"),
                        "python": environment.get("python"),
                        "machine": environment.get("machine"),
                        "expected_dependency_count": environment.get(
                            "expected_dependency_count"
                        ),
                        "dependency_mismatch_count": environment.get(
                            "dependency_mismatch_count"
                        ),
                        "production_pin_count": repository.get(
                            "production_pin_count"
                        ),
                        "errors": payload.get("errors"),
                        "hcx_calls": payload.get("hcx_calls"),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            if name == "rule_claim_holdout":
                return json.dumps(
                    {
                        "case_count": payload.get("case_count"),
                        "passed": payload.get("passed"),
                        "failed": payload.get("failed"),
                        "positive_acceptance_rate": payload.get(
                            "positive_acceptance_rate"
                        ),
                        "unsafe_rejection_rate": payload.get(
                            "unsafe_rejection_rate"
                        ),
                        "hcx_calls": payload.get("hcx_calls"),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            if name == "domain_coverage":
                return json.dumps(
                    {
                        "official_general_document_count": payload.get(
                            "official_general_document_count"
                        ),
                        "required_domain_count": payload.get("required_domain_count"),
                        "document_inventory_coverage_rate": payload.get(
                            "document_inventory_coverage_rate"
                        ),
                        "domain_document_coverage_rate": payload.get(
                            "domain_document_coverage_rate"
                        ),
                        "domain_evaluation_coverage_rate": payload.get(
                            "domain_evaluation_coverage_rate"
                        ),
                        "passed": payload.get("passed"),
                        "hcx_calls": payload.get("hcx_calls"),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            if name == "lifecycle_paraphrase_holdout":
                return json.dumps(
                    {
                        "case_count": payload.get("case_count"),
                        "positive_case_count": payload.get("positive_case_count"),
                        "negative_case_count": payload.get("negative_case_count"),
                        "passed": payload.get("passed"),
                        "failed": payload.get("failed"),
                        "positive_retrieval_success_rate": payload.get(
                            "positive_retrieval_success_rate"
                        ),
                        "positive_route_coverage_rate": payload.get(
                            "positive_route_coverage_rate"
                        ),
                        "source_group_exact_match_rate": payload.get(
                            "source_group_exact_match_rate"
                        ),
                        "required_query_term_coverage_rate": payload.get(
                            "required_query_term_coverage_rate"
                        ),
                        "negative_anchor_rejection_rate": payload.get(
                            "negative_anchor_rejection_rate"
                        ),
                        "domain_retrieval_coverage_rate": payload.get(
                            "domain_retrieval_coverage_rate"
                        ),
                        "positive_evidence_status_accuracy": payload.get(
                            "positive_evidence_status_accuracy"
                        ),
                        "positive_generation_eligibility_rate": payload.get(
                            "positive_generation_eligibility_rate"
                        ),
                        "positive_evidence_qrel_alignment_rate": payload.get(
                            "positive_evidence_qrel_alignment_rate"
                        ),
                        "domain_evidence_coverage_rate": payload.get(
                            "domain_evidence_coverage_rate"
                        ),
                        "hcx_calls": payload.get("hcx_calls"),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            if name in SCENARIO_STAGE_CASES:
                return json.dumps(
                    {
                        "case_count": payload.get("case_count"),
                        "passed": payload.get("passed"),
                        "failed": payload.get("failed"),
                        "case_pass_rate": payload.get("case_pass_rate"),
                        "family_all_critical_pass_rate": payload.get(
                            "family_all_critical_pass_rate"
                        ),
                        "worst_axis_pass_rate": payload.get(
                            "worst_axis_pass_rate"
                        ),
                        "unsafe_generation_count": payload.get(
                            "unsafe_generation_count"
                        ),
                        "hcx_calls": payload.get("hcx_calls"),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            if name == "context_coverage":
                return json.dumps(
                    {
                        "case_count": payload.get("case_count"),
                        "passed": payload.get("passed"),
                        "failed": payload.get("failed"),
                        "required_marker_recall": payload.get(
                            "required_marker_recall"
                        ),
                        "forbidden_marker_rejection_rate": payload.get(
                            "forbidden_marker_rejection_rate"
                        ),
                        "budget_adherence_rate": payload.get(
                            "budget_adherence_rate"
                        ),
                        "completeness_decision_accuracy": payload.get(
                            "completeness_decision_accuracy"
                        ),
                        "hcx_calls": payload.get("hcx_calls"),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            if name == "tool_selection":
                return json.dumps(
                    {
                        "case_count": payload.get("case_count"),
                        "passed": payload.get("passed"),
                        "failed": payload.get("failed"),
                        "route_exact_match_rate": payload.get(
                            "route_exact_match_rate"
                        ),
                        "source_group_exact_match_rate": payload.get(
                            "source_group_exact_match_rate"
                        ),
                        "tool_selection_exact_match_rate": payload.get(
                            "tool_selection_exact_match_rate"
                        ),
                        "control_decision_accuracy": payload.get(
                            "control_decision_accuracy"
                        ),
                        "hcx_calls": payload.get("hcx_calls"),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            if name in {
                "planning",
                "suitability",
                "local_grounding",
                "answer_guard",
                "hcx_precision_plan",
            }:
                return json.dumps(
                    {
                        "case_count": payload.get("case_count"),
                        "passed": payload.get("passed"),
                        "failed": payload.get("failed"),
                        "hcx_calls": payload.get("hcx_calls"),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            if name == "product_parser":
                return json.dumps(
                    {
                        "record_count": payload.get("record_count"),
                        "independent_conflicts": payload.get("independent_conflicts"),
                        "passed": payload.get("passed"),
                        "hcx_calls": payload.get("hcx_calls"),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            return json.dumps(
                {
                    "evaluated_query_count": payload.get("evaluated_query_count"),
                    "recall_at_k": payload.get("recall_at_k"),
                    "mrr_at_k": payload.get("mrr_at_k"),
                    "ndcg_at_k": payload.get("ndcg_at_k"),
                    "map_at_k": payload.get("map_at_k"),
                    "no_evidence_rejection_rate": payload.get(
                        "no_evidence_rejection_rate"
                    ),
                    "hcx_calls": payload.get("hcx_calls"),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
    if name in {"contract_tests", "container_build"}:
        lines = [line for line in normalized.splitlines() if line.strip()]
        return "\n".join(lines[-8:])
    if len(normalized) <= limit:
        return normalized
    return normalized[-limit:]


def run_stage(
    name: str,
    *,
    suite_path: Path,
    endpoint: str | None,
    allow_live_hcx: bool,
) -> StageResult:
    started = time.monotonic()
    try:
        command, timeout = _stage_command(
            name,
            suite_path=suite_path,
            endpoint=endpoint,
            allow_live_hcx=allow_live_hcx,
        )
        completed = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        combined = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
        return StageResult(
            name=name,
            passed=completed.returncode == 0,
            exit_code=completed.returncode,
            duration_ms=round((time.monotonic() - started) * 1_000),
            summary=_summarize_output(name, combined),
        )
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        return StageResult(
            name=name,
            passed=False,
            exit_code=124 if isinstance(exc, subprocess.TimeoutExpired) else 2,
            duration_ms=round((time.monotonic() - started) * 1_000),
            summary=str(exc),
        )


def _atomic_write(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    parser.add_argument("--profile")
    parser.add_argument("--endpoint")
    parser.add_argument(
        "--allow-live-hcx",
        action="store_true",
        help="Permit the one-question submission smoke stage to reach production HCX.",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    suite_path = args.suite.resolve()
    suite = load_suite(suite_path)
    profile = args.profile or suite.get("default_profile")
    profiles = suite["profiles"]
    if profile not in profiles:
        parser.error(f"unknown profile: {profile}")
    if args.allow_live_hcx and profile != "submission":
        parser.error("--allow-live-hcx is only valid with --profile submission")

    stages = [
        run_stage(
            name,
            suite_path=suite_path,
            endpoint=args.endpoint,
            allow_live_hcx=args.allow_live_hcx,
        )
        for name in profiles[profile]
    ]
    live_stage_attempted = any(stage.name == "endpoint_smoke" for stage in stages)
    report = {
        "runner_version": RUNNER_VERSION,
        "suite_id": suite["suite_id"],
        "suite_sha256": _sha256(suite_path),
        "profile": profile,
        "passed": all(stage.passed for stage in stages),
        "hcx_budget": {
            "enabled": args.allow_live_hcx,
            "maximum_logical_questions": 1 if args.allow_live_hcx else 0,
            "endpoint_smoke_attempted": live_stage_attempted,
        },
        "artifacts": {
            "corpus_manifest_sha256": _sha256(PROJECT_ROOT / "data/knowledge/manifest.json"),
            "product_evidence_sha256": _sha256(
                PROJECT_ROOT / "data/structured/product_evidence.json"
            ),
            "requirements_lock_sha256": _sha256(
                PROJECT_ROOT / "requirements.lock"
            ),
            "requirements_dev_lock_sha256": _sha256(
                PROJECT_ROOT / "requirements-dev.lock"
            ),
            "gold_cases_sha256": _sha256(
                PROJECT_ROOT / "tests/fixtures/pension_grounding_cases.json"
            ),
            "domain_inventory_sha256": _sha256(
                PROJECT_ROOT / "data/structured/general_topic_inventory.json"
            ),
            "planning_cases_sha256": _sha256(
                PROJECT_ROOT / "tests/fixtures/question_planning_cases.json"
            ),
            "tool_selection_cases_sha256": _sha256(
                PROJECT_ROOT / "tests/fixtures/tool_selection_holdout_cases.json"
            ),
            "lifecycle_paraphrase_holdout_cases_sha256": _sha256(
                PROJECT_ROOT
                / "tests/fixtures/lifecycle_paraphrase_holdout_cases.json"
            ),
            "scenario_robustness_development_cases_sha256": _sha256(
                SCENARIO_STAGE_CASES["scenario_robustness"]
            ),
            "scenario_robustness_expansion_development_cases_sha256": _sha256(
                SCENARIO_STAGE_CASES["scenario_robustness_expansion"]
            ),
            "scenario_safety_development_cases_sha256": _sha256(
                SCENARIO_STAGE_CASES["scenario_safety"]
            ),
            "suitability_cases_sha256": _sha256(
                PROJECT_ROOT / "tests/fixtures/suitability_cases.json"
            ),
            "answer_guard_cases_sha256": _sha256(
                PROJECT_ROOT / "tests/fixtures/answer_validation_cases.json"
            ),
            "rule_claim_holdout_cases_sha256": _sha256(
                PROJECT_ROOT / "tests/fixtures/rule_claim_holdout_cases.json"
            ),
            "context_coverage_cases_sha256": _sha256(
                PROJECT_ROOT / "tests/fixtures/context_coverage_cases.json"
            ),
            "hcx_precision_cases_sha256": _sha256(
                PROJECT_ROOT / "benchmarks/config/hcx-precision-cases.json"
            ),
        },
        "stages": [asdict(stage) for stage in stages],
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output is not None:
        _atomic_write(args.output.resolve(), rendered)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
