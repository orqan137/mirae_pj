#!/usr/bin/env python3
"""Check that questions the corpus cannot support return no evidence.

Answering an unanswerable question from a plausible-but-wrong document costs
points on accuracy, groundedness and information-limit handling at once, so a
confident wrong retrieval is worse than an empty one. Every out-of-corpus topic
here is verified absent from the general corpus by full-text scan, which means
the labels come from the corpus rather than from the retriever's own output.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.retrieval import LocalRetriever  # noqa: E402

ABSTENTION_EVALUATOR_VERSION = "pension-abstention-eval-v1"
DEFAULT_CASES = PROJECT_ROOT / "benchmarks" / "config" / "abstention-cases.json"
DEFAULT_KNOWLEDGE = PROJECT_ROOT / "data" / "knowledge"
DEFAULT_SUITE = PROJECT_ROOT / "benchmarks" / "config" / "benchmark-suite.json"


def evaluate(*, cases_path: Path, knowledge_dir: Path, k: int) -> dict[str, object]:
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    general = knowledge_dir / "general"
    retriever = LocalRetriever.from_directory(knowledge_dir)

    # Re-verify each label instead of trusting the file.
    mislabelled = [
        item["absent_term"]
        for item in cases["out_of_corpus"]
        if any(
            item["absent_term"] in path.read_text(encoding="utf-8", errors="ignore")
            for path in general.glob("*.txt")
        )
    ]

    leaks = [
        {
            "question": item["question"],
            "sources": [doc.source_id for doc in hits[:3]],
        }
        for item in cases["out_of_corpus"]
        if (
            hits := retriever.retrieve(
                item["question"], top_k=k, source_groups={"general"}
            )
        )
    ]
    missed = [
        question
        for question in cases["in_corpus"]
        if not retriever.retrieve(question, top_k=k, source_groups={"general"})
    ]

    out_total = len(cases["out_of_corpus"])
    in_total = len(cases["in_corpus"])
    return {
        "evaluator_version": ABSTENTION_EVALUATOR_VERSION,
        "case_set_id": cases["case_set_id"],
        "out_of_corpus_count": out_total,
        "in_corpus_count": in_total,
        "abstention_rate": (out_total - len(leaks)) / out_total,
        "in_corpus_retrieval_rate": (in_total - len(missed)) / in_total,
        "mislabelled_absent_terms": mislabelled,
        "leaks": leaks,
        "missed_in_corpus": missed,
        "hcx_calls": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--knowledge", type=Path, default=DEFAULT_KNOWLEDGE)
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    parser.add_argument("--k", type=int, default=6)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()

    report = evaluate(
        cases_path=arguments.cases,
        knowledge_dir=arguments.knowledge,
        k=arguments.k,
    )
    thresholds: dict[str, float] = {}
    if arguments.suite.is_file():
        suite = json.loads(arguments.suite.read_text(encoding="utf-8"))
        thresholds = dict(suite.get("abstention", {}).get("thresholds", {}))

    failures = [
        f"{name} {report[name]:.3f} < {minimum:.3f}"
        for name, minimum in thresholds.items()
        if isinstance(report.get(name), (int, float)) and report[name] < minimum
    ]
    if report["mislabelled_absent_terms"]:
        failures.append(
            "labels stale: terms now present in corpus "
            f"{report['mislabelled_absent_terms']}"
        )
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
