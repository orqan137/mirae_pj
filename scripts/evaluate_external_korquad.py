#!/usr/bin/env python3
"""Evaluate paragraph retrieval adapted from the KorQuAD v1.0 dev dataset.

This is not the official KorQuAD reading-comprehension EM/F1 protocol and does
not evaluate answer generation. Repeated use for development makes this a
development diagnostic, not an untouched holdout.

The in-repo retrieval qrels are saturated: 18 developer-written queries over 13
gold documents, all scoring 1.0. They cannot show whether a change helps or
hurts. KorQuAD v1.0 supplies 5,774 questions written by annotators who never
saw this code, over a paragraph collection of comparable size to the pension
corpus, so it measures the scorer itself rather than our own vocabulary.

The dataset is not vendored: it is not organizer material and must not enter the
operational corpus. Fetch it once into a scratch path and pass --dataset.

    curl -sSL -o /tmp/korquad_dev.json \\
      https://korquad.github.io/dataset/KorQuAD_v1.0_dev.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.retrieval import (  # noqa: E402
    LocalRetriever,
    RETRIEVER_VERSION,
    TextChunk,
    _normalize,
    _tokens,
)

EXTERNAL_EVALUATOR_VERSION = "pension-external-korquad-eval-v2"


def load_benchmark(path: Path) -> tuple[list[TextChunk], list[tuple[str, str]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))["data"]
    chunks: list[TextChunk] = []
    gold: list[tuple[str, str]] = []
    for index, article in enumerate(payload):
        for offset, paragraph in enumerate(article["paragraphs"]):
            source_id = f"p{index}_{offset}"
            text = paragraph["context"]
            chunks.append(
                TextChunk(
                    source=f"korquad/{source_id}.txt",
                    source_id=source_id,
                    source_path=f"korquad/{source_id}",
                    source_group="general",
                    canonical_source_id=source_id,
                    product_aliases=(),
                    title=article["title"],
                    index=0,
                    page=None,
                    locator=None,
                    text=text,
                    tokens=_tokens(f"{article['title']}\n{text}"),
                    normalized_text=_normalize(text),
                    axes=frozenset(),
                    governance=None,
                )
            )
            gold.extend((qa["question"], source_id) for qa in paragraph["qas"])
    return chunks, gold


def score(
    retriever: LocalRetriever,
    pairs: list[tuple[str, str]],
    *,
    k: int,
) -> dict[str, float]:
    if k <= 0 or not pairs:
        raise ValueError("k and the number of questions must be positive")
    precision_at_1 = 0
    recall_at_k = 0
    reciprocal = 0.0
    empty = 0
    for question, source_id in pairs:
        ranked = [item.source_id for item in retriever.retrieve(question, top_k=k)]
        if not ranked:
            empty += 1
        if ranked[:1] == [source_id]:
            precision_at_1 += 1
        if source_id in ranked:
            recall_at_k += 1
            reciprocal += 1.0 / (ranked.index(source_id) + 1)
    total = len(pairs)
    return {
        "precision_at_1": precision_at_1 / total,
        "recall_at_k": recall_at_k / total,
        "mrr_at_k": reciprocal / total,
        "empty_result_rate": empty / total,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path("/tmp/korquad_dev.json"))
    parser.add_argument("--sample", type=int, default=1500)
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    if arguments.sample <= 0 or arguments.k <= 0:
        parser.error("--sample and --k must be positive")

    if not arguments.dataset.is_file():
        print(
            json.dumps(
                {
                    "evaluator_version": EXTERNAL_EVALUATOR_VERSION,
                    "skipped": True,
                    "passed": False,
                    "reason": f"dataset not found: {arguments.dataset}",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1

    chunks, gold = load_benchmark(arguments.dataset)
    if not chunks or not gold:
        parser.error("dataset must contain paragraphs and questions")
    retriever = LocalRetriever(chunks)
    sample = random.Random(arguments.seed).sample(gold, min(arguments.sample, len(gold)))
    collapsed = [(re.sub(r"\s+", "", q), source) for q, source in sample]

    report = {
        "evaluator_version": EXTERNAL_EVALUATOR_VERSION,
        "evaluation_task": "paragraph_retrieval_adaptation",
        "official_korquad_em_f1": False,
        "evaluation_usage": "development_diagnostic",
        "dataset": arguments.dataset.name,
        "dataset_sha256": hashlib.sha256(arguments.dataset.read_bytes()).hexdigest(),
        "seed": arguments.seed,
        "sample_sha256": hashlib.sha256(
            json.dumps(sample, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "retriever_version": RETRIEVER_VERSION,
        "retriever_sha256": hashlib.sha256(
            (PROJECT_ROOT / "app/retrieval.py").read_bytes()
        ).hexdigest(),
        "evaluator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "paragraph_count": len(chunks),
        "question_pool": len(gold),
        "sampled_questions": len(sample),
        "k": arguments.k,
        "as_written": score(retriever, sample, k=arguments.k),
        "spacing_collapsed": score(retriever, collapsed, k=arguments.k),
        "hcx_calls": 0,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
