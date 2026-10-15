#!/usr/bin/env python3
"""Measure retrieval stability under meaning-preserving question rewrites.

The existing retrieval stage scores 1.0 on 18 developer-authored queries whose
wording already matches the corpus. That saturation hides the failure mode this
system is most exposed to: a lexical BM25 index has no synonym or morphology
model, so an evaluator who phrases the same question differently can miss the
document entirely.

This evaluator needs no new gold labels. Each perturbation rewrites an existing
qrel question in a way a Korean speaker would still consider the same request,
so the original ``retrieval_relevant_sources`` remain correct by construction.
Retention is measured against each case's own baseline, which keeps the metric
honest when a baseline query was already imperfect.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from dataclasses import dataclass, replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.evaluate_local_grounding import (  # noqa: E402
    DEFAULT_CASES,
    DEFAULT_KNOWLEDGE,
    DEFAULT_PRODUCT_EVIDENCE,
    GroundingCase,
    evaluate_cases,
    load_cases,
)
from scripts.evaluate_retrieval import ranked_metrics  # noqa: E402

ROBUSTNESS_EVALUATOR_VERSION = "pension-retrieval-robustness-eval-v1"
DEFAULT_SUITE = PROJECT_ROOT / "benchmarks" / "config" / "benchmark-suite.json"

# Particles and polite endings stripped to build a keyword-style query. Ordered
# longest-first so "에서" is removed before "에".
_PARTICLES = (
    "으로부터",
    "에서는",
    "에서의",
    "에게서",
    "으로써",
    "에서",
    "에게",
    "까지",
    "부터",
    "으로",
    "이나",
    "라도",
    "이란",
    "은",
    "는",
    "이",
    "가",
    "을",
    "를",
    "의",
    "도",
    "만",
    "로",
    "에",
    "와",
    "과",
)

_ENDING_REWRITES = (
    ("알려주세요", "알려줘"),
    ("설명해주세요", "설명해줘"),
    ("비교해 주세요", "비교해줘"),
    ("비교해주세요", "비교해줘"),
    ("해 주세요", "해줘"),
    ("해주세요", "해줘"),
    ("주세요", "줘"),
    ("있습니까", "있나요"),
    ("됩니까", "되나요"),
    ("합니까", "하나요"),
    ("입니까", "인가요"),
    ("인가요", "이야"),
    ("나요", "냐"),
)

_TRAILING_POLITENESS = ("궁금합니다", "알고 싶습니다", "문의드립니다")


def _hangul_words(text: str) -> list[str]:
    return re.findall(r"[가-힣]{2,}", text)


def collapse_spacing(question: str) -> str:
    """Korean readers routinely run compounds together; the index should not care."""

    return re.sub(r"\s+", "", question)


def telegraphic(question: str) -> str:
    """Keyword-style query: content words with particles and endings removed."""

    words = re.findall(r"[가-힣A-Za-z0-9]+", unicodedata.normalize("NFKC", question))
    kept: list[str] = []
    for word in words:
        stripped = word
        for particle in _PARTICLES:
            if len(stripped) > len(particle) + 1 and stripped.endswith(particle):
                stripped = stripped[: -len(particle)]
                break
        if len(stripped) >= 2:
            kept.append(stripped)
    return " ".join(kept)


def casual_ending(question: str) -> str:
    """Same request in a less formal register."""

    rewritten = question
    for polite, casual in _ENDING_REWRITES:
        if polite in rewritten:
            rewritten = rewritten.replace(polite, casual)
            break
    for trailing in _TRAILING_POLITENESS:
        rewritten = rewritten.replace(trailing, "")
    return rewritten.strip()


def dropped_syllable(question: str) -> str:
    """Delete one syllable from the longest content word: a realistic typo.

    The longest word is chosen deterministically so the perturbation is stable
    across runs, and the middle syllable is dropped so the word keeps both its
    leading and trailing context.
    """

    words = _hangul_words(question)
    if not words:
        return question
    target = max(words, key=lambda word: (len(word), word))
    if len(target) < 3:
        return question
    cut = len(target) // 2
    return question.replace(target, target[:cut] + target[cut + 1 :], 1)


PERTURBATIONS: tuple[tuple[str, object], ...] = (
    ("spacing_collapsed", collapse_spacing),
    ("telegraphic", telegraphic),
    ("casual_ending", casual_ending),
    ("dropped_syllable", dropped_syllable),
)


@dataclass(frozen=True, slots=True)
class VariantOutcome:
    case_id: str
    perturbation: str
    question: str
    recall: float
    reciprocal_rank: float
    retained: bool


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _qrel_cases(cases: tuple[GroundingCase, ...]) -> tuple[GroundingCase, ...]:
    return tuple(case for case in cases if case.retrieval_relevant_sources)


def _run(
    cases: tuple[GroundingCase, ...],
    *,
    knowledge_dir: Path,
    product_evidence_path: Path,
    k: int,
) -> dict[str, tuple[float, float]]:
    results = evaluate_cases(
        list(cases),
        knowledge_dir=knowledge_dir,
        product_evidence_path=product_evidence_path,
    )
    scored: dict[str, tuple[float, float]] = {}
    for case, result in zip(cases, results, strict=True):
        metrics = ranked_metrics(
            result.retrieved_source_ids,
            case.retrieval_relevant_sources,
            k=k,
        )
        scored[case.case_id] = (metrics.recall, metrics.reciprocal_rank)
    return scored


def evaluate_robustness(
    *,
    cases_path: Path,
    knowledge_dir: Path,
    product_evidence_path: Path,
    k: int,
) -> dict[str, object]:
    cases = _qrel_cases(load_cases(cases_path))
    if not cases:
        raise ValueError("no qrel-bearing cases found")

    baseline = _run(
        cases,
        knowledge_dir=knowledge_dir,
        product_evidence_path=product_evidence_path,
        k=k,
    )

    outcomes: list[VariantOutcome] = []
    for name, rewrite in PERTURBATIONS:
        variants = tuple(
            replace(case, question=rewrite(case.question))  # type: ignore[operator]
            for case in cases
        )
        # A rewrite that changed nothing would inflate retention, so it is
        # reported but excluded from the retention denominator.
        scored = _run(
            variants,
            knowledge_dir=knowledge_dir,
            product_evidence_path=product_evidence_path,
            k=k,
        )
        for case, variant in zip(cases, variants, strict=True):
            base_recall, _ = baseline[case.case_id]
            recall, reciprocal_rank = scored[case.case_id]
            outcomes.append(
                VariantOutcome(
                    case_id=case.case_id,
                    perturbation=name,
                    question=variant.question,
                    recall=recall,
                    reciprocal_rank=reciprocal_rank,
                    retained=recall >= base_recall,
                )
            )

    by_perturbation: dict[str, dict[str, float]] = {}
    for name, _ in PERTURBATIONS:
        rows = [item for item in outcomes if item.perturbation == name]
        by_perturbation[name] = {
            "case_count": len(rows),
            "retention_rate": sum(item.retained for item in rows) / len(rows),
            "recall_at_k": sum(item.recall for item in rows) / len(rows),
            "mrr_at_k": sum(item.reciprocal_rank for item in rows) / len(rows),
        }

    baseline_recall = sum(value[0] for value in baseline.values()) / len(baseline)
    overall_retention = sum(item.retained for item in outcomes) / len(outcomes)
    worst = min(by_perturbation.items(), key=lambda item: item[1]["retention_rate"])
    return {
        "evaluator_version": ROBUSTNESS_EVALUATOR_VERSION,
        "cases_sha256": _sha256(cases_path),
        "k": k,
        "case_count": len(cases),
        "variant_count": len(outcomes),
        "baseline_recall_at_k": baseline_recall,
        "overall_retention_rate": overall_retention,
        "worst_perturbation": worst[0],
        "worst_retention_rate": worst[1]["retention_rate"],
        "by_perturbation": by_perturbation,
        "regressions": [
            {
                "case_id": item.case_id,
                "perturbation": item.perturbation,
                "question": item.question,
                "recall_at_k": item.recall,
            }
            for item in outcomes
            if not item.retained
        ],
        "hcx_calls": 0,
    }


def _thresholds(suite_path: Path) -> dict[str, float]:
    if not suite_path.is_file():
        return {}
    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    config = suite.get("retrieval_robustness", {})
    return dict(config.get("thresholds", {}))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--knowledge", type=Path, default=DEFAULT_KNOWLEDGE)
    parser.add_argument("--product-evidence", type=Path, default=DEFAULT_PRODUCT_EVIDENCE)
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    parser.add_argument("--k", type=int, default=6)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()

    report = evaluate_robustness(
        cases_path=arguments.cases,
        knowledge_dir=arguments.knowledge,
        product_evidence_path=arguments.product_evidence,
        k=arguments.k,
    )
    thresholds = _thresholds(arguments.suite)
    failures = [
        f"{name} {report[name]:.3f} < {minimum:.3f}"
        for name, minimum in thresholds.items()
        if isinstance(report.get(name), (int, float)) and report[name] < minimum
    ]
    report["thresholds"] = thresholds
    report["passed"] = not failures
    report["threshold_failures"] = failures

    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
