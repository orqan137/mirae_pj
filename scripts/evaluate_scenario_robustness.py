#!/usr/bin/env python3
"""Evaluate deterministic pension scenarios and metamorphic relations without HCX."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.evidence import assess_evidence  # noqa: E402
from app.planning import (  # noqa: E402
    PLANNER_VERSION,
    QUERY_ANCHOR_VERSION,
    plan_question,
)
from app.retrieval import (  # noqa: E402
    LocalRetriever,
    RetrievedDocument,
    evidence_citation_id,
    retrieve_for_queries,
)
from app.routing import ROUTER_VERSION, classify_question  # noqa: E402


EVALUATOR_VERSION = "pension-scenario-robustness-eval-v3"
DEFAULT_CASES = PROJECT_ROOT / "benchmarks" / "scenarios" / "development-v1.json"
CANONICAL_DEVELOPMENT_CASES = (
    DEFAULT_CASES,
    DEFAULT_CASES.with_name("development-expansion-v1.json"),
    DEFAULT_CASES.with_name("development-safety-v1.json"),
)
DEFAULT_KNOWLEDGE = PROJECT_ROOT / "data" / "knowledge"
DEFAULT_MANIFEST = DEFAULT_KNOWLEDGE / "manifest.json"
DEFAULT_SOURCE_GOVERNANCE = (
    PROJECT_ROOT / "data" / "structured" / "general_source_governance.json"
)
DEFAULT_SUITE = PROJECT_ROOT / "benchmarks" / "config" / "benchmark-suite.json"

_PACK_FIELDS = frozenset(
    {
        "schema_version",
        "pack_id",
        "split",
        "panel",
        "pack_fingerprint",
        "corpus_manifest_sha256",
        "cases",
    }
)
_CASE_FIELDS = frozenset(
    {
        "case_id",
        "domain",
        "semantic_family_id",
        "template_family_id",
        "source_lineage_family",
        "scenario_axes",
        "coverage_tags",
        "test_type",
        "parent_case_id",
        "relation",
        "question",
        "required_route_labels",
        "forbidden_route_labels",
        "expected_source_groups",
        "required_query_terms",
        "forbidden_query_terms",
        "expected_evidence_status",
        "expected_should_generate",
        "qrels",
        "minimum_relevant_qrels",
        "requires_qrel_support",
        "provenance",
    }
)
_QREL_FIELDS = frozenset(
    {
        "granularity",
        "source_id",
        "locator",
        "chunk_index",
        "content_sha256",
    }
)
_COVERAGE_TAG_FIELDS = frozenset({"as_of", "audience"})
_PROVENANCE_FIELDS = frozenset({"origin", "reviewer", "review_status"})
_SPLITS = frozenset({"development", "validation", "sealed"})
_PANELS = frozenset({"development", "L", "S"})
_TEST_RELATIONS = {
    "MFT": "independent",
    "INV": "semantic_invariant",
    "DIR": "directional_contrast",
}
_EVIDENCE_STATUSES = frozenset(
    {
        "answerable",
        "partially_answerable",
        "needs_conditions",
        "out_of_corpus",
        "conflicting_evidence",
        "context_budget_exceeded",
        "out_of_scope",
    }
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _term_present(term: str, text: str) -> bool:
    """Match a declared term while ignoring whitespace inside the same spelling.

    Korean compounds are routinely written both closed and spaced.  This only
    tolerates whitespace between the exact normalized characters; it does not
    introduce aliases or synonym matching.
    """

    normalized_term = _normalize(term)
    normalized_text = _normalize(text)
    if normalized_term in normalized_text:
        return True
    compact_term = re.sub(r"\s+", "", normalized_term)
    if not compact_term:
        return False
    whitespace_tolerant = r"\s*".join(re.escape(character) for character in compact_term)
    return re.search(whitespace_tolerant, normalized_text) is not None


def _canonical_json(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def compute_pack_fingerprint(payload: Mapping[str, object]) -> str:
    """Hash the complete pack except its self-referential fingerprint field."""

    unsigned = dict(payload)
    unsigned.pop("pack_fingerprint", None)
    return hashlib.sha256(_canonical_json(unsigned)).hexdigest()


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _string(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _identifier(value: object, *, field: str) -> str:
    """Return a canonical identifier while rejecting ambiguous spellings."""

    parsed = _string(value, field=field)
    if parsed != parsed.strip():
        raise ValueError(f"{field} must not contain leading or trailing whitespace")
    if unicodedata.normalize("NFKC", parsed) != parsed:
        raise ValueError(f"{field} must use NFKC-normalized characters")
    if any(character.isspace() for character in parsed):
        raise ValueError(f"{field} must not contain whitespace")
    if not all(
        character.isascii() and (character.isalnum() or character in "._-")
        for character in parsed
    ):
        raise ValueError(f"{field} must use ASCII letters, digits, dot, dash, or underscore")
    return parsed


def _identifier_key(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _strings(
    value: object,
    *,
    field: str,
    allow_empty: bool = True,
) -> tuple[str, ...]:
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


def _manifest_records(path: Path) -> dict[str, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    documents = payload.get("documents") if isinstance(payload, dict) else None
    if not isinstance(documents, list) or not documents:
        raise ValueError("manifest documents must be a non-empty array")
    records: dict[str, str] = {}
    for position, item in enumerate(documents):
        if not isinstance(item, dict):
            raise ValueError(f"manifest document[{position}] must be an object")
        source_id = item.get("source_id")
        text_digest = item.get("text_sha256")
        if not isinstance(source_id, str) or not source_id or not _is_sha256(text_digest):
            raise ValueError(f"manifest document[{position}] has invalid identity/hash")
        if source_id in records:
            raise ValueError(f"manifest contains duplicate source_id: {source_id}")
        records[source_id] = text_digest
    return records


@lru_cache(maxsize=4)
def _cached_chunk_records(
    knowledge_dir: str,
    source_governance_path: str,
    manifest_sha256: str,
    source_governance_sha256: str,
) -> dict[tuple[str, str, int], str]:
    """Resolve the exact current corpus chunks used by LocalRetriever.

    The manifest digest is part of the cache key so a rebuilt corpus cannot reuse
    stale chunk hashes.  LocalRetriever does not expose a public chunk iterator;
    this evaluation-only adapter intentionally inspects its immutable loaded
    chunks until a stable EvidenceID API exists.
    """

    del manifest_sha256, source_governance_sha256
    retriever = LocalRetriever.from_directory(
        Path(knowledge_dir),
        source_governance_path=Path(source_governance_path),
    )
    return _chunk_records_from_retriever(retriever)


def _chunk_records_from_retriever(
    retriever: LocalRetriever,
) -> dict[tuple[str, str, int], str]:
    """Hash the exact chunks loaded by the retriever used for evaluation."""

    chunks = getattr(retriever, "_chunks", None)
    if not isinstance(chunks, (list, tuple)):
        raise ValueError("LocalRetriever chunk inventory is unavailable")
    records: dict[tuple[str, str, int], str] = {}
    for chunk in chunks:
        key = (chunk.source_id, chunk.locator or "unavailable", chunk.index)
        if key in records:
            raise ValueError(f"duplicate corpus chunk identity: {key}")
        records[key] = _text_sha256(chunk.text)
    return records


@dataclass(frozen=True, slots=True)
class ScenarioQrel:
    granularity: str
    source_id: str
    locator: str | None
    chunk_index: int | None
    content_sha256: str | None


@dataclass(frozen=True, slots=True)
class ScenarioProvenance:
    origin: str
    reviewer: str
    review_status: str


@dataclass(frozen=True, slots=True)
class ScenarioCase:
    case_id: str
    domain: str
    semantic_family_id: str
    template_family_id: str
    source_lineage_family: str
    scenario_axes: tuple[str, ...]
    coverage_tags: Mapping[str, str]
    test_type: str
    parent_case_id: str | None
    relation: str
    question: str
    required_route_labels: tuple[str, ...]
    forbidden_route_labels: tuple[str, ...]
    expected_source_groups: tuple[str, ...]
    required_query_terms: tuple[str, ...]
    forbidden_query_terms: tuple[str, ...]
    expected_evidence_status: str | None
    expected_should_generate: bool | None
    qrels: tuple[ScenarioQrel, ...]
    minimum_relevant_qrels: int
    requires_qrel_support: bool
    provenance: ScenarioProvenance


@dataclass(frozen=True, slots=True)
class ScenarioPack:
    path: Path
    pack_id: str
    split: str
    panel: str
    pack_fingerprint: str
    corpus_manifest_sha256: str
    cases: tuple[ScenarioCase, ...]


@dataclass(frozen=True, slots=True)
class ScenarioObservation:
    route_labels: tuple[str, ...]
    source_groups: tuple[str, ...]
    combined_query: str
    query_anchors: tuple[str, ...]
    documents: tuple[RetrievedDocument, ...]
    evidence_status: str | None
    should_generate: bool | None
    selected_documents: tuple[RetrievedDocument, ...]


def _load_qrel(
    item: object,
    *,
    field: str,
    manifest_records: Mapping[str, str],
    chunk_records: Mapping[tuple[str, str, int], str],
) -> ScenarioQrel:
    if not isinstance(item, dict) or set(item) != _QREL_FIELDS:
        raise ValueError(f"{field} has invalid fields")
    granularity = item["granularity"]
    source_id = item["source_id"]
    if granularity not in {"source", "locator_chunk"}:
        raise ValueError(f"{field}.granularity is invalid")
    if not isinstance(source_id, str) or source_id not in manifest_records:
        raise ValueError(f"{field}.source_id is absent from the corpus manifest")
    locator = item["locator"]
    chunk_index = item["chunk_index"]
    content_sha256 = item["content_sha256"]
    if granularity == "source":
        if any(value is not None for value in (locator, chunk_index, content_sha256)):
            raise ValueError(
                f"{field} source-granularity qrel must not claim locator/content proof"
            )
    else:
        if not isinstance(locator, str) or not locator:
            raise ValueError(f"{field}.locator must be a non-empty string")
        if isinstance(chunk_index, bool) or not isinstance(chunk_index, int) or chunk_index < 1:
            raise ValueError(f"{field}.chunk_index must be a positive integer")
        if not _is_sha256(content_sha256):
            raise ValueError(f"{field}.content_sha256 must be a SHA-256 digest")
        chunk_key = (source_id, locator, chunk_index)
        actual_chunk_sha256 = chunk_records.get(chunk_key)
        if actual_chunk_sha256 is None:
            raise ValueError(f"{field} locator/chunk is absent from the current corpus")
        if content_sha256 != actual_chunk_sha256:
            raise ValueError(f"{field}.content_sha256 mismatches the corpus chunk")
    return ScenarioQrel(
        granularity=granularity,
        source_id=source_id,
        locator=locator,
        chunk_index=chunk_index,
        content_sha256=content_sha256,
    )


def _load_case(
    item: object,
    *,
    position: int,
    manifest_records: Mapping[str, str],
    chunk_records: Mapping[tuple[str, str, int], str],
) -> ScenarioCase:
    field = f"case[{position}]"
    if not isinstance(item, dict) or set(item) != _CASE_FIELDS:
        raise ValueError(f"{field} has invalid fields")
    case_id = _identifier(item["case_id"], field=f"{field}.case_id")
    semantic_family_id = _identifier(
        item["semantic_family_id"], field=f"{field}.semantic_family_id"
    )
    template_family_id = _identifier(
        item["template_family_id"], field=f"{field}.template_family_id"
    )
    source_lineage_family = _identifier(
        item["source_lineage_family"], field=f"{field}.source_lineage_family"
    )
    test_type = item["test_type"]
    relation = item["relation"]
    if test_type not in _TEST_RELATIONS or relation != _TEST_RELATIONS[test_type]:
        raise ValueError(f"{field} test_type and relation disagree")
    parent_case_id = item["parent_case_id"]
    if test_type == "MFT":
        if parent_case_id is not None:
            raise ValueError(f"{field} MFT must not have a parent")
    else:
        parent_case_id = _identifier(
            parent_case_id,
            field=f"{field}.parent_case_id",
        )

    required_routes = _strings(
        item["required_route_labels"], field=f"{field}.required_route_labels"
    )
    forbidden_routes = _strings(
        item["forbidden_route_labels"], field=f"{field}.forbidden_route_labels"
    )
    if set(map(_normalize, required_routes)) & set(map(_normalize, forbidden_routes)):
        raise ValueError(f"{field} required/forbidden route labels overlap")
    required_terms = _strings(
        item["required_query_terms"], field=f"{field}.required_query_terms"
    )
    forbidden_terms = _strings(
        item["forbidden_query_terms"], field=f"{field}.forbidden_query_terms"
    )
    if set(map(_normalize, required_terms)) & set(map(_normalize, forbidden_terms)):
        raise ValueError(f"{field} required/forbidden query terms overlap")
    if test_type == "DIR" and not forbidden_routes and not forbidden_terms:
        raise ValueError(f"{field} DIR must declare a forbidden route or query term")

    coverage_tags = item["coverage_tags"]
    if (
        not isinstance(coverage_tags, dict)
        or set(coverage_tags) != _COVERAGE_TAG_FIELDS
        or not all(isinstance(value, str) and value for value in coverage_tags.values())
    ):
        raise ValueError(f"{field}.coverage_tags is invalid")
    provenance = item["provenance"]
    if not isinstance(provenance, dict) or set(provenance) != _PROVENANCE_FIELDS:
        raise ValueError(f"{field}.provenance has invalid fields")
    parsed_provenance = ScenarioProvenance(
        origin=_string(provenance["origin"], field=f"{field}.provenance.origin"),
        reviewer=_string(provenance["reviewer"], field=f"{field}.provenance.reviewer"),
        review_status=_string(
            provenance["review_status"], field=f"{field}.provenance.review_status"
        ),
    )

    expected_status = item["expected_evidence_status"]
    expected_generate = item["expected_should_generate"]
    if expected_status is None:
        if expected_generate is not None:
            raise ValueError(f"{field} planner-only case must not set should_generate")
    else:
        if expected_status not in _EVIDENCE_STATUSES:
            raise ValueError(f"{field}.expected_evidence_status is invalid")
        if not isinstance(expected_generate, bool):
            raise ValueError(f"{field}.expected_should_generate must be boolean")
        status_generates = expected_status in {"answerable", "partially_answerable"}
        if expected_generate is not status_generates:
            raise ValueError(f"{field} evidence status and should_generate disagree")

    raw_qrels = item["qrels"]
    if not isinstance(raw_qrels, list):
        raise ValueError(f"{field}.qrels must be an array")
    qrels = tuple(
        _load_qrel(
            qrel,
            field=f"{field}.qrels[{qrel_position}]",
            manifest_records=manifest_records,
            chunk_records=chunk_records,
        )
        for qrel_position, qrel in enumerate(raw_qrels)
    )
    qrel_keys = tuple(
        (qrel.granularity, qrel.source_id, qrel.locator, qrel.chunk_index)
        for qrel in qrels
    )
    if len(qrel_keys) != len(set(qrel_keys)):
        raise ValueError(f"{field}.qrels must be unique")
    minimum = item["minimum_relevant_qrels"]
    if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 0:
        raise ValueError(f"{field}.minimum_relevant_qrels is invalid")
    if (not qrels and minimum != 0) or (qrels and not 1 <= minimum <= len(qrels)):
        raise ValueError(f"{field}.minimum_relevant_qrels disagrees with qrels")
    requires_support = item["requires_qrel_support"]
    if not isinstance(requires_support, bool):
        raise ValueError(f"{field}.requires_qrel_support must be boolean")
    if requires_support and (not qrels or expected_status is None):
        raise ValueError(f"{field} qrel support requires qrels and evidence evaluation")

    scenario_axes = _strings(
        item["scenario_axes"], field=f"{field}.scenario_axes", allow_empty=False
    )
    expected_source_groups = _strings(
        item["expected_source_groups"],
        field=f"{field}.expected_source_groups",
        allow_empty=False,
    )
    return ScenarioCase(
        case_id=case_id,
        domain=_string(item["domain"], field=f"{field}.domain"),
        semantic_family_id=semantic_family_id,
        template_family_id=template_family_id,
        source_lineage_family=source_lineage_family,
        scenario_axes=scenario_axes,
        coverage_tags=dict(coverage_tags),
        test_type=test_type,
        parent_case_id=parent_case_id,
        relation=relation,
        question=_string(item["question"], field=f"{field}.question"),
        required_route_labels=required_routes,
        forbidden_route_labels=forbidden_routes,
        expected_source_groups=expected_source_groups,
        required_query_terms=required_terms,
        forbidden_query_terms=forbidden_terms,
        expected_evidence_status=expected_status,
        expected_should_generate=expected_generate,
        qrels=qrels,
        minimum_relevant_qrels=minimum,
        requires_qrel_support=requires_support,
        provenance=parsed_provenance,
    )


def load_pack(
    path: Path,
    *,
    manifest_path: Path = DEFAULT_MANIFEST,
    source_governance_path: Path = DEFAULT_SOURCE_GOVERNANCE,
) -> ScenarioPack:
    resolved = path.resolve()
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or set(payload) != _PACK_FIELDS:
        raise ValueError("scenario pack has invalid fields")
    if payload.get("schema_version") != 1:
        raise ValueError("unsupported scenario pack schema")
    split = payload["split"]
    panel = payload["panel"]
    if split not in _SPLITS:
        raise ValueError("scenario pack split is invalid")
    if panel not in _PANELS:
        raise ValueError("scenario pack panel is invalid")
    if (split == "development") is not (panel == "development"):
        raise ValueError("development split/panel pairing is invalid")
    manifest_digest = _sha256(manifest_path)
    if payload["corpus_manifest_sha256"] != manifest_digest:
        raise ValueError("scenario pack corpus manifest fingerprint mismatch")
    expected_fingerprint = compute_pack_fingerprint(payload)
    if payload["pack_fingerprint"] != expected_fingerprint:
        raise ValueError("scenario pack fingerprint mismatch")
    manifest_records = _manifest_records(manifest_path)
    chunk_records = _cached_chunk_records(
        str(manifest_path.resolve().parent),
        str(source_governance_path.resolve()),
        manifest_digest,
        _sha256(source_governance_path),
    )
    raw_cases = payload["cases"]
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("scenario pack cases must be a non-empty array")
    cases = tuple(
        _load_case(
            item,
            position=position,
            manifest_records=manifest_records,
            chunk_records=chunk_records,
        )
        for position, item in enumerate(raw_cases)
    )
    ids = [_identifier_key(case.case_id) for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("scenario pack contains normalized duplicate case_id")
    for attribute in (
        "semantic_family_id",
        "template_family_id",
        "source_lineage_family",
    ):
        spellings: dict[str, str] = {}
        for case in cases:
            value = getattr(case, attribute)
            key = _identifier_key(value)
            prior = spellings.setdefault(key, value)
            if prior != value:
                raise ValueError(
                    f"scenario pack contains inconsistent normalized {attribute} spellings"
                )
    questions = [_normalize(case.question) for case in cases]
    if len(questions) != len(set(questions)):
        raise ValueError("scenario pack contains normalized duplicate questions")
    by_id = {_identifier_key(case.case_id): case for case in cases}
    for case in cases:
        if case.test_type == "MFT":
            continue
        parent_key = _identifier_key(case.parent_case_id or "")
        if parent_key == _identifier_key(case.case_id):
            raise ValueError(f"case {case.case_id} must not reference itself")
        parent = by_id.get(parent_key)
        if parent is None:
            raise ValueError(f"case {case.case_id} parent is absent from the same split")
        if parent.test_type != "MFT":
            raise ValueError(f"case {case.case_id} parent must be an MFT case")
        if _identifier_key(parent.semantic_family_id) != _identifier_key(
            case.semantic_family_id
        ):
            raise ValueError(f"case {case.case_id} parent semantic family differs")
    return ScenarioPack(
        path=resolved,
        pack_id=_identifier(payload["pack_id"], field="pack_id"),
        split=split,
        panel=panel,
        pack_fingerprint=payload["pack_fingerprint"],
        corpus_manifest_sha256=payload["corpus_manifest_sha256"],
        cases=cases,
    )


def validate_split_isolation(packs: Iterable[ScenarioPack]) -> None:
    """Reject cross-split semantic leakage and declared panel leakage."""

    items = tuple(packs)
    seen_case_ids: dict[str, tuple[str, str]] = {}
    seen_questions: dict[str, tuple[str, str]] = {}
    family_splits: dict[str, set[str]] = defaultdict(set)
    for pack in items:
        for case in pack.cases:
            case_key = _identifier_key(case.case_id)
            prior_case = seen_case_ids.get(case_key)
            if prior_case is not None:
                raise ValueError(
                    "normalized case_id is reused across scenario packs: "
                    f"{prior_case[1]} and {case.case_id}"
                )
            seen_case_ids[case_key] = (pack.split, case.case_id)
            normalized = _normalize(case.question)
            prior = seen_questions.get(normalized)
            if prior is not None and prior[0] != pack.split:
                raise ValueError(
                    "normalized question leaks across splits: "
                    f"{prior[1]} and {case.case_id}"
                )
            seen_questions[normalized] = (pack.split, case.case_id)
            family_splits[_identifier_key(case.semantic_family_id)].add(pack.split)
    leaked = sorted(family for family, splits in family_splits.items() if len(splits) > 1)
    if leaked:
        raise ValueError(f"semantic families leak across splits: {leaked}")

    for target in items:
        others = tuple(pack for pack in items if pack.split != target.split)
        if not others or target.panel == "development":
            continue
        if target.panel == "L":
            target_templates = {
                _identifier_key(case.template_family_id) for case in target.cases
            }
            other_templates = {
                _identifier_key(case.template_family_id)
                for pack in others
                for case in pack.cases
            }
            overlap = sorted(target_templates & other_templates)
            if overlap:
                raise ValueError(f"panel L template families leak: {overlap}")
        if target.panel == "S":
            target_lineages = {
                _identifier_key(case.source_lineage_family) for case in target.cases
            }
            other_lineages = {
                _identifier_key(case.source_lineage_family)
                for pack in others
                for case in pack.cases
            }
            overlap = sorted(target_lineages & other_lineages)
            if overlap:
                raise ValueError(f"panel S source lineages leak: {overlap}")


def _observe_case(
    case: ScenarioCase,
    *,
    retriever: LocalRetriever,
    top_k: int,
) -> ScenarioObservation:
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
    combined_query = "\n".join(
        subquestion.query_text for subquestion in plan.subquestions
    )
    assessment = None
    if case.expected_evidence_status is not None:
        assessment = assess_evidence(
            plan=plan,
            route=route,
            documents=documents,
            structured_axes={},
            structured_identities={},
            calculation=None,
        )
    selected_refs = {
        ref
        for coverage in (assessment.coverage if assessment is not None else ())
        for ref in coverage.evidence_refs
    }
    selected_documents = tuple(
        document
        for document in documents
        if (
            evidence_citation_id(document) in selected_refs
            or (
                document.locator is None
                and f"{document.source_id}:chunk={document.index}" in selected_refs
            )
        )
    )
    return ScenarioObservation(
        route_labels=tuple(route.labels),
        source_groups=tuple(sorted(route.source_groups)),
        combined_query=combined_query,
        query_anchors=tuple(
            anchor
            for subquestion in plan.subquestions
            for anchor in subquestion.query_anchors
        ),
        documents=tuple(documents),
        evidence_status=assessment.status if assessment is not None else None,
        should_generate=assessment.should_generate if assessment is not None else None,
        selected_documents=selected_documents,
    )


def _qrel_matches_document(qrel: ScenarioQrel, document: RetrievedDocument) -> bool:
    if qrel.source_id != document.source_id:
        return False
    if qrel.granularity == "source":
        return True
    actual_locator = document.locator or "unavailable"
    return (
        qrel.locator == actual_locator
        and qrel.chunk_index == document.index
        and qrel.content_sha256 == _text_sha256(document.text)
    )


def _validate_runtime_qrels(
    pack: ScenarioPack,
    chunk_records: Mapping[tuple[str, str, int], str],
) -> None:
    """Rebind locator qrels to the exact retriever inventory used in this run."""

    for case in pack.cases:
        for position, qrel in enumerate(case.qrels):
            if qrel.granularity != "locator_chunk":
                continue
            key = (
                qrel.source_id,
                qrel.locator or "unavailable",
                qrel.chunk_index or 0,
            )
            actual_digest = chunk_records.get(key)
            if actual_digest is None:
                raise ValueError(
                    f"case {case.case_id} runtime qrel[{position}] chunk is absent"
                )
            if actual_digest != qrel.content_sha256:
                raise ValueError(
                    f"case {case.case_id} runtime qrel[{position}] content mismatch"
                )


def _matched_qrels(
    qrels: tuple[ScenarioQrel, ...],
    documents: tuple[RetrievedDocument, ...],
) -> tuple[ScenarioQrel, ...]:
    return tuple(
        qrel
        for qrel in qrels
        if any(_qrel_matches_document(qrel, document) for document in documents)
    )


def _relation_passed(
    case: ScenarioCase,
    observation: ScenarioObservation,
    *,
    cases_by_id: Mapping[str, ScenarioCase],
    observations_by_id: Mapping[str, ScenarioObservation],
) -> bool:
    if case.test_type == "MFT":
        return True
    parent = cases_by_id[_identifier_key(case.parent_case_id or "")]
    parent_observation = observations_by_id[parent.case_id]
    if case.test_type == "INV":
        return (
            set(observation.route_labels) == set(parent_observation.route_labels)
            and observation.source_groups == parent_observation.source_groups
            and observation.evidence_status == parent_observation.evidence_status
            and observation.should_generate == parent_observation.should_generate
        )
    query_direction = any(
        _term_present(term, parent_observation.combined_query)
        and not _term_present(term, observation.combined_query)
        for term in case.forbidden_query_terms
    )
    route_direction = any(
        label in parent_observation.route_labels and label not in observation.route_labels
        for label in case.forbidden_route_labels
    )
    return query_direction or route_direction


def unsafe_generation_reasons(
    case: ScenarioCase,
    observation: ScenarioObservation,
    *,
    evidence_qrel_aligned: bool,
) -> tuple[str, ...]:
    """Return only hard, oracle-backed unsafe-generation reasons."""

    reasons: list[str] = []
    if case.expected_should_generate is False and observation.should_generate is True:
        reasons.append("expected_false")
    if (
        case.requires_qrel_support
        and observation.should_generate is True
        and not evidence_qrel_aligned
    ):
        reasons.append("qrel_alignment")
    return tuple(reasons)


def evaluate_pack(
    pack: ScenarioPack,
    *,
    knowledge_dir: Path = DEFAULT_KNOWLEDGE,
    source_governance_path: Path = DEFAULT_SOURCE_GOVERNANCE,
    top_k: int = 6,
    isolation_packs: Iterable[ScenarioPack] = (),
) -> tuple[dict[str, object], ...]:
    if top_k < 1:
        raise ValueError("top_k must be positive")
    resolved_knowledge = knowledge_dir.resolve()
    resolved_governance = source_governance_path.resolve()
    runtime_manifest = resolved_knowledge / "manifest.json"
    manifest_digest_before = _sha256(runtime_manifest)
    if manifest_digest_before != pack.corpus_manifest_sha256:
        raise ValueError("evaluation corpus manifest fingerprint mismatch")
    references = tuple(isolation_packs)
    if pack.split in {"validation", "sealed"}:
        development_packs = _load_canonical_development_packs(
            manifest_path=runtime_manifest,
            source_governance_path=resolved_governance,
        )
        validate_execution_isolation(
            pack,
            development_packs=development_packs,
            isolation_packs=references,
        )
    elif references:
        raise ValueError("isolation packs are only valid for protected evaluation")
    retriever = LocalRetriever.from_directory(
        resolved_knowledge,
        source_governance_path=resolved_governance,
    )
    runtime_chunks = _chunk_records_from_retriever(retriever)
    _validate_runtime_qrels(pack, runtime_chunks)
    manifest_digest_after = _sha256(runtime_manifest)
    if manifest_digest_after != manifest_digest_before:
        raise ValueError("evaluation corpus manifest changed during retriever load")
    observations = {
        case.case_id: _observe_case(case, retriever=retriever, top_k=top_k)
        for case in pack.cases
    }
    cases_by_id = {_identifier_key(case.case_id): case for case in pack.cases}
    results: list[dict[str, object]] = []
    for case in pack.cases:
        observation = observations[case.case_id]
        required_routes_present = set(case.required_route_labels).issubset(
            observation.route_labels
        )
        forbidden_routes_absent = set(case.forbidden_route_labels).isdisjoint(
            observation.route_labels
        )
        source_groups_exact = set(observation.source_groups) == set(
            case.expected_source_groups
        )
        required_terms_present = all(
            _term_present(term, observation.combined_query)
            for term in case.required_query_terms
        )
        forbidden_terms_absent = all(
            not _term_present(term, observation.combined_query)
            for term in case.forbidden_query_terms
        )
        retrieved_qrels = _matched_qrels(case.qrels, observation.documents)
        selected_qrels = _matched_qrels(case.qrels, observation.selected_documents)
        retrieval_qrel_aligned = len(retrieved_qrels) >= case.minimum_relevant_qrels
        evidence_qrel_aligned = (
            len(selected_qrels) >= case.minimum_relevant_qrels
            if case.requires_qrel_support
            else True
        )
        status_exact = (
            observation.evidence_status == case.expected_evidence_status
            if case.expected_evidence_status is not None
            else True
        )
        generation_exact = (
            observation.should_generate is case.expected_should_generate
            if case.expected_should_generate is not None
            else True
        )
        relation_passed = _relation_passed(
            case,
            observation,
            cases_by_id=cases_by_id,
            observations_by_id=observations,
        )

        unsafe_reasons = unsafe_generation_reasons(
            case,
            observation,
            evidence_qrel_aligned=evidence_qrel_aligned,
        )
        failures: list[str] = []
        checks = {
            "required_routes_present": required_routes_present,
            "forbidden_routes_absent": forbidden_routes_absent,
            "source_groups_exact": source_groups_exact,
            "required_query_terms_present": required_terms_present,
            "forbidden_query_terms_absent": forbidden_terms_absent,
            "retrieval_qrel_aligned": retrieval_qrel_aligned,
            "evidence_status_exact": status_exact,
            "generation_decision_exact": generation_exact,
            "evidence_qrel_aligned": evidence_qrel_aligned,
            "relation_passed": relation_passed,
            "unsafe_generation_absent": not unsafe_reasons,
        }
        for name, passed in checks.items():
            if not passed:
                failures.append(name)
        results.append(
            {
                "case_id": case.case_id,
                "semantic_family_id": case.semantic_family_id,
                "scenario_axes": list(case.scenario_axes),
                "test_type": case.test_type,
                "passed": not failures,
                "checks": checks,
                "actual_route_labels": list(observation.route_labels),
                "actual_source_groups": list(observation.source_groups),
                "query_anchors": list(observation.query_anchors),
                "ranked_evidence_refs": [
                    evidence_citation_id(document) for document in observation.documents
                ],
                "selected_evidence_refs": [
                    evidence_citation_id(document)
                    for document in observation.selected_documents
                ],
                "qrel_granularities": sorted(
                    {qrel.granularity for qrel in case.qrels}
                ),
                "retrieved_qrel_count": len(retrieved_qrels),
                "selected_qrel_count": len(selected_qrels),
                "actual_evidence_status": observation.evidence_status,
                "actual_should_generate": observation.should_generate,
                "unsafe_reasons": list(unsafe_reasons),
                "failures": failures,
            }
        )
    return tuple(results)


def summarize_results(
    pack: ScenarioPack,
    results: tuple[dict[str, object], ...],
) -> dict[str, object]:
    if len(pack.cases) != len(results):
        raise ValueError("scenario cases/results length mismatch")
    result_by_id = {str(result["case_id"]): result for result in results}
    axis_rows: dict[str, dict[str, object]] = {}
    for axis in sorted({axis for case in pack.cases for axis in case.scenario_axes}):
        rows = [result_by_id[case.case_id] for case in pack.cases if axis in case.scenario_axes]
        passed = sum(bool(row["passed"]) for row in rows)
        axis_rows[axis] = {
            "case_count": len(rows),
            "passed": passed,
            "pass_rate": passed / len(rows),
        }
    family_rows: dict[str, dict[str, object]] = {}
    for family in sorted({case.semantic_family_id for case in pack.cases}):
        rows = [
            result_by_id[case.case_id]
            for case in pack.cases
            if case.semantic_family_id == family
        ]
        family_rows[family] = {
            "case_count": len(rows),
            "all_critical_passed": all(bool(row["passed"]) for row in rows),
        }
    family_passes = sum(
        bool(row["all_critical_passed"]) for row in family_rows.values()
    )
    control_rows = [
        result
        for case, result in zip(pack.cases, results, strict=True)
        if case.forbidden_route_labels or case.forbidden_query_terms
    ]
    control_passes = sum(
        bool(result["checks"]["forbidden_routes_absent"])
        and bool(result["checks"]["forbidden_query_terms_absent"])
        for result in control_rows
    )
    granularities = defaultdict(int)
    for case in pack.cases:
        if not case.qrels:
            granularities["none"] += 1
        elif all(qrel.granularity == "locator_chunk" for qrel in case.qrels):
            granularities["locator_chunk"] += 1
        elif all(qrel.granularity == "source" for qrel in case.qrels):
            granularities["source"] += 1
        else:
            granularities["mixed"] += 1
    passed_cases = sum(bool(result["passed"]) for result in results)
    return {
        "case_count": len(results),
        "passed": passed_cases,
        "failed": len(results) - passed_cases,
        "case_pass_rate": passed_cases / len(results),
        "family_count": len(family_rows),
        "family_all_critical_pass_rate": family_passes / len(family_rows),
        "worst_axis_pass_rate": min(
            float(row["pass_rate"]) for row in axis_rows.values()
        ),
        "worst_family_pass_rate": min(
            float(bool(row["all_critical_passed"])) for row in family_rows.values()
        ),
        "anchor_control_rejection_rate": (
            control_passes / len(control_rows) if control_rows else 1.0
        ),
        "unsafe_generation_count": sum(
            bool(result["unsafe_reasons"]) for result in results
        ),
        "per_axis": axis_rows,
        "per_family": family_rows,
        "qrel_granularity_case_counts": dict(sorted(granularities.items())),
    }


def render_report(
    pack: ScenarioPack,
    results: tuple[dict[str, object], ...],
    *,
    report_level: str = "auto",
    thresholds: Mapping[str, float | int] | None = None,
) -> dict[str, object]:
    if report_level not in {"auto", "aggregate", "detailed"}:
        raise ValueError("report_level must be auto, aggregate, or detailed")
    protected = pack.split in {"validation", "sealed"}
    if protected and report_level == "detailed":
        raise ValueError("validation/sealed scenario reports are aggregate-only")
    detailed = report_level == "detailed" or (
        report_level == "auto" and not protected
    )
    metrics = summarize_results(pack, results)
    threshold_failures: list[str] = []
    if thresholds is not None:
        comparisons = {
            "minimum_case_pass_rate": float(metrics["case_pass_rate"]),
            "minimum_family_all_critical_pass_rate": float(
                metrics["family_all_critical_pass_rate"]
            ),
            "minimum_worst_axis_pass_rate": float(metrics["worst_axis_pass_rate"]),
        }
        for name, actual in comparisons.items():
            if actual < float(thresholds[name]):
                threshold_failures.append(name)
        if int(metrics["unsafe_generation_count"]) > int(
            thresholds["maximum_unsafe_generation_count"]
        ):
            threshold_failures.append("maximum_unsafe_generation_count")
    report: dict[str, object] = {
        "evaluator_version": EVALUATOR_VERSION,
        "router_version": ROUTER_VERSION,
        "planner_version": PLANNER_VERSION,
        "query_anchor_version": QUERY_ANCHOR_VERSION,
        "split": pack.split,
        "panel": pack.panel,
        "pack_fingerprint": pack.pack_fingerprint,
        "corpus_manifest_sha256": pack.corpus_manifest_sha256,
        "case_count": metrics["case_count"],
        "passed": metrics["passed"],
        "failed": metrics["failed"],
        "case_pass_rate": metrics["case_pass_rate"],
        "family_count": metrics["family_count"],
        "family_all_critical_pass_rate": metrics[
            "family_all_critical_pass_rate"
        ],
        "worst_axis_pass_rate": metrics["worst_axis_pass_rate"],
        "worst_family_pass_rate": metrics["worst_family_pass_rate"],
        "unsafe_generation_count": metrics["unsafe_generation_count"],
        "thresholds_passed": not threshold_failures,
        "hcx_calls": 0,
    }
    if not protected:
        report["pack_id"] = pack.pack_id
        report["anchor_control_rejection_rate"] = metrics[
            "anchor_control_rejection_rate"
        ]
        report["per_axis"] = metrics["per_axis"]
        report["qrel_granularity_case_counts"] = metrics[
            "qrel_granularity_case_counts"
        ]
        report["per_family"] = metrics["per_family"]
        report["threshold_failures"] = threshold_failures
    if detailed:
        report["results"] = list(results)
    return report


def validate_execution_isolation(
    pack: ScenarioPack,
    *,
    development_packs: Iterable[ScenarioPack],
    isolation_packs: Iterable[ScenarioPack] = (),
) -> None:
    """Fail closed unless a protected pack is isolated from every required pack."""

    if pack.split not in {"validation", "sealed"}:
        raise ValueError("execution isolation is only defined for protected packs")

    expected_paths = tuple(path.resolve() for path in CANONICAL_DEVELOPMENT_CASES)
    supplied = tuple(development_packs)
    supplied_by_path = {development.path.resolve(): development for development in supplied}
    if (
        len(supplied) != len(expected_paths)
        or len(supplied_by_path) != len(expected_paths)
        or set(supplied_by_path) != set(expected_paths)
    ):
        raise ValueError(
            "protected execution requires all canonical development packs"
        )
    canonical_development = tuple(
        supplied_by_path[path] for path in expected_paths
    )
    for development in canonical_development:
        if development.split != "development" or development.panel != "development":
            raise ValueError(
                "canonical development packs must use the development split/panel"
            )
        if development.corpus_manifest_sha256 != pack.corpus_manifest_sha256:
            raise ValueError("protected packs must use one corpus manifest")

    references = tuple(isolation_packs)
    target_path = pack.path.resolve()
    for reference in references:
        if reference.path.resolve() == target_path:
            raise ValueError("a protected pack cannot isolate against itself")
        if reference.split != "validation" or reference.panel not in {"L", "S"}:
            raise ValueError("explicit isolation packs must be validation packs")
        if reference.corpus_manifest_sha256 != pack.corpus_manifest_sha256:
            raise ValueError("protected packs must use one corpus manifest")
    if pack.split == "sealed" and not references:
        raise ValueError("sealed execution requires a separate validation isolation pack")
    validate_split_isolation((*canonical_development, *references, pack))


def _load_canonical_development_packs(
    *,
    manifest_path: Path,
    source_governance_path: Path,
) -> tuple[ScenarioPack, ...]:
    """Load every fixed development reference or fail before protected scoring."""

    return tuple(
        load_pack(
            path,
            manifest_path=manifest_path,
            source_governance_path=source_governance_path,
        )
        for path in CANONICAL_DEVELOPMENT_CASES
    )


def _load_protected_references(
    pack: ScenarioPack,
    isolation_paths: Iterable[Path],
    *,
    manifest_path: Path,
    source_governance_path: Path,
) -> tuple[ScenarioPack, ...]:
    references: list[ScenarioPack] = []
    seen_paths = {
        *(path.resolve() for path in CANONICAL_DEVELOPMENT_CASES),
        pack.path.resolve(),
    }
    for path in isolation_paths:
        resolved = path.resolve()
        if resolved in seen_paths:
            raise ValueError("scenario isolation pack paths must be distinct")
        seen_paths.add(resolved)
        references.append(
            load_pack(
                resolved,
                manifest_path=manifest_path,
                source_governance_path=source_governance_path,
            )
        )
    return tuple(references)


def _declared_split(path: Path) -> str | None:
    """Peek only at the public split label so protected failures can be redacted."""

    try:
        payload = json.loads(path.resolve().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    split = payload.get("split") if isinstance(payload, dict) else None
    if not isinstance(split, str):
        return None
    normalized = _normalize(split)
    return normalized if normalized in _SPLITS else split


def _load_configuration(path: Path) -> tuple[int, dict[str, float | int], int]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    config = payload.get("scenario_robustness") if isinstance(payload, dict) else None
    if not isinstance(config, dict):
        raise ValueError("suite scenario_robustness configuration is missing")
    if set(config) != {"k", "thresholds", "maximum_hcx_calls"}:
        raise ValueError("scenario_robustness configuration has invalid fields")
    top_k = config["k"]
    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
        raise ValueError("scenario_robustness.k must be a positive integer")
    raw_thresholds = config["thresholds"]
    required = {
        "minimum_case_pass_rate",
        "minimum_family_all_critical_pass_rate",
        "minimum_worst_axis_pass_rate",
        "maximum_unsafe_generation_count",
    }
    if not isinstance(raw_thresholds, dict) or set(raw_thresholds) != required:
        raise ValueError("scenario_robustness thresholds are invalid")
    thresholds: dict[str, float | int] = {}
    for name in required - {"maximum_unsafe_generation_count"}:
        value = raw_thresholds[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name} must be numeric")
        parsed = float(value)
        if not 0.0 <= parsed <= 1.0:
            raise ValueError(f"{name} must be between zero and one")
        thresholds[name] = parsed
    maximum_unsafe = raw_thresholds["maximum_unsafe_generation_count"]
    if (
        isinstance(maximum_unsafe, bool)
        or not isinstance(maximum_unsafe, int)
        or maximum_unsafe < 0
    ):
        raise ValueError("maximum_unsafe_generation_count must be non-negative")
    thresholds["maximum_unsafe_generation_count"] = maximum_unsafe
    maximum_hcx_calls = config["maximum_hcx_calls"]
    if maximum_hcx_calls != 0:
        raise ValueError("scenario robustness evaluation must have zero HCX budget")
    return top_k, thresholds, maximum_hcx_calls


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--knowledge", type=Path, default=DEFAULT_KNOWLEDGE)
    parser.add_argument("--source-governance", type=Path, default=DEFAULT_SOURCE_GOVERNANCE)
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    parser.add_argument(
        "--isolation-pack",
        type=Path,
        action="append",
        default=[],
        metavar="PATH",
        help=(
            "Validation reference pack used for protected split isolation; repeatable "
            "and required at least once for sealed execution."
        ),
    )
    parser.add_argument(
        "--report-level",
        choices=("auto", "aggregate", "detailed"),
        default="auto",
    )
    args = parser.parse_args()

    declared_split = _declared_split(args.cases)
    protected_execution = declared_split in {"validation", "sealed"}
    protected_split = declared_split if protected_execution else None
    try:
        top_k, thresholds, _ = _load_configuration(args.suite.resolve())
        manifest_path = args.knowledge.resolve() / "manifest.json"
        source_governance_path = args.source_governance.resolve()
        pack = load_pack(
            args.cases.resolve(),
            manifest_path=manifest_path,
            source_governance_path=source_governance_path,
        )
        protected_execution = protected_execution or pack.split in {
            "validation",
            "sealed",
        }
        if pack.split in {"validation", "sealed"}:
            protected_split = pack.split
        isolation_packs: tuple[ScenarioPack, ...] = ()
        if pack.split in {"validation", "sealed"}:
            isolation_packs = _load_protected_references(
                pack,
                args.isolation_pack,
                manifest_path=manifest_path,
                source_governance_path=source_governance_path,
            )
        elif args.isolation_pack:
            raise ValueError("--isolation-pack is only valid for protected packs")
        results = evaluate_pack(
            pack,
            knowledge_dir=args.knowledge.resolve(),
            source_governance_path=source_governance_path,
            top_k=top_k,
            isolation_packs=isolation_packs,
        )
        report = render_report(
            pack,
            results,
            report_level=args.report_level,
            thresholds=thresholds,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["thresholds_passed"] and report["failed"] == 0 else 1
    except Exception:
        if protected_execution:
            print(
                json.dumps(
                    {
                        "error": "protected_scenario_evaluation_failed",
                        "split": protected_split or "protected",
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                file=sys.stderr,
            )
            return 2
        raise


if __name__ == "__main__":
    raise SystemExit(main())
