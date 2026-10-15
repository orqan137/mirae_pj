#!/usr/bin/env python3
"""Audit official-document and goldset coverage across pension domains without HCX."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.evaluate_local_grounding import DEFAULT_CASES, load_cases  # noqa: E402


EVALUATOR_VERSION = "pension-domain-coverage-eval-v1"
DEFAULT_INVENTORY = (
    PROJECT_ROOT / "data" / "structured" / "general_topic_inventory.json"
)
DEFAULT_MANIFEST = PROJECT_ROOT / "data" / "knowledge" / "manifest.json"
DEFAULT_SUITE = PROJECT_ROOT / "benchmarks" / "config" / "benchmark-suite.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _string_list(value: Any, *, field: str) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) and item for item in value)
        or len(value) != len(set(value))
    ):
        raise ValueError(f"{field} must be a non-empty array of unique strings")
    return tuple(value)


def _general_source_ids(manifest_path: Path) -> frozenset[str]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    documents = payload.get("documents") if isinstance(payload, dict) else None
    if not isinstance(documents, list):
        raise ValueError("manifest documents must be an array")
    source_ids = tuple(
        item.get("source_id")
        for item in documents
        if isinstance(item, dict) and item.get("source_group") == "general"
    )
    if not source_ids or not all(isinstance(item, str) and item for item in source_ids):
        raise ValueError("manifest contains invalid general source IDs")
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("manifest contains duplicate general source IDs")
    return frozenset(source_ids)


def evaluate_domain_coverage(
    *,
    inventory_path: Path,
    manifest_path: Path,
    cases_path: Path,
) -> dict[str, object]:
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    if not isinstance(inventory, dict) or inventory.get("schema_version") != 1:
        raise ValueError("unsupported domain inventory schema")
    if inventory.get("inventory_version") != "pension-general-topic-inventory-v1":
        raise ValueError("unexpected domain inventory version")
    if inventory.get("source_scope") != "organizer_provided_general_documents":
        raise ValueError("domain inventory source scope is invalid")

    domains = _string_list(inventory.get("required_domains"), field="required_domains")
    domain_set = set(domains)
    raw_documents = inventory.get("documents")
    raw_evaluation_cases = inventory.get("evaluation_cases")
    if not isinstance(raw_documents, dict) or not isinstance(raw_evaluation_cases, dict):
        raise ValueError("domain inventory documents/evaluation_cases must be objects")
    if set(raw_evaluation_cases) != domain_set:
        raise ValueError("evaluation_cases must cover every required domain exactly")

    manifest_ids = _general_source_ids(manifest_path)
    inventory_ids = frozenset(raw_documents)
    missing_documents = sorted(manifest_ids - inventory_ids)
    extra_documents = sorted(inventory_ids - manifest_ids)
    invalid_document_tags: list[str] = []
    documents_by_domain: dict[str, set[str]] = {domain: set() for domain in domains}
    for source_id, raw_tags in raw_documents.items():
        try:
            tags = _string_list(raw_tags, field=f"documents.{source_id}")
        except ValueError as exc:
            invalid_document_tags.append(str(exc))
            continue
        unknown = sorted(set(tags) - domain_set)
        if unknown:
            invalid_document_tags.append(
                f"documents.{source_id} contains unknown domains: {unknown}"
            )
            continue
        for domain in tags:
            documents_by_domain[domain].add(source_id)

    cases = {case.case_id: case for case in load_cases(cases_path)}
    unknown_case_ids: list[str] = []
    qrel_mismatches: list[str] = []
    domain_rows: list[dict[str, object]] = []
    evaluated_domains = 0
    for domain in domains:
        case_ids = _string_list(
            raw_evaluation_cases[domain], field=f"evaluation_cases.{domain}"
        )
        matched_sources: set[str] = set()
        for case_id in case_ids:
            case = cases.get(case_id)
            if case is None:
                unknown_case_ids.append(case_id)
                continue
            overlap = set(case.retrieval_relevant_sources) & documents_by_domain[domain]
            if not overlap:
                qrel_mismatches.append(f"{domain}:{case_id}")
            matched_sources.update(overlap)
        evaluated = bool(case_ids) and not any(
            item.startswith(f"{domain}:") for item in qrel_mismatches
        ) and all(case_id in cases for case_id in case_ids)
        evaluated_domains += int(evaluated)
        domain_rows.append(
            {
                "domain": domain,
                "document_count": len(documents_by_domain[domain]),
                "evaluation_case_ids": list(case_ids),
                "matched_qrel_source_ids": sorted(matched_sources),
                "evaluated": evaluated,
            }
        )

    document_coverage_rate = (
        len(manifest_ids & inventory_ids) / len(manifest_ids) if manifest_ids else 0.0
    )
    domain_document_coverage_rate = (
        sum(bool(documents_by_domain[domain]) for domain in domains) / len(domains)
    )
    domain_evaluation_coverage_rate = evaluated_domains / len(domains)
    failures = [
        *(f"missing inventory document: {item}" for item in missing_documents),
        *(f"unknown inventory document: {item}" for item in extra_documents),
        *invalid_document_tags,
        *(f"unknown evaluation case: {item}" for item in sorted(set(unknown_case_ids))),
        *(f"evaluation qrel does not match domain inventory: {item}" for item in qrel_mismatches),
    ]
    if domain_document_coverage_rate < 1.0:
        failures.append("one or more required domains have no official document")

    return {
        "evaluator_version": EVALUATOR_VERSION,
        "inventory_sha256": _sha256(inventory_path),
        "manifest_sha256": _sha256(manifest_path),
        "cases_sha256": _sha256(cases_path),
        "official_general_document_count": len(manifest_ids),
        "inventoried_document_count": len(inventory_ids & manifest_ids),
        "required_domain_count": len(domains),
        "document_inventory_coverage_rate": document_coverage_rate,
        "domain_document_coverage_rate": domain_document_coverage_rate,
        "domain_evaluation_coverage_rate": domain_evaluation_coverage_rate,
        "domains": domain_rows,
        "failures": failures,
        "hcx_calls": 0,
    }


def _thresholds(path: Path) -> dict[str, float]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    config = payload.get("domain_coverage") if isinstance(payload, dict) else None
    raw = config.get("thresholds") if isinstance(config, dict) else None
    required = {
        "document_inventory_coverage_rate",
        "domain_document_coverage_rate",
        "domain_evaluation_coverage_rate",
    }
    if not isinstance(raw, dict) or set(raw) != required:
        raise ValueError("domain coverage thresholds are invalid")
    parsed: dict[str, float] = {}
    for name, value in raw.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"threshold {name} must be numeric")
        parsed[name] = float(value)
        if not 0.0 <= parsed[name] <= 1.0:
            raise ValueError(f"threshold {name} must be between zero and one")
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    args = parser.parse_args()
    report = evaluate_domain_coverage(
        inventory_path=args.inventory.resolve(),
        manifest_path=args.manifest.resolve(),
        cases_path=args.cases.resolve(),
    )
    thresholds = _thresholds(args.suite.resolve())
    threshold_failures = [
        f"{name}={float(report[name]):.6f} below threshold={threshold:.6f}"
        for name, threshold in thresholds.items()
        if float(report[name]) < threshold
    ]
    report["thresholds"] = thresholds
    report["failures"] = [*report["failures"], *threshold_failures]
    report["passed"] = not report["failures"]
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
