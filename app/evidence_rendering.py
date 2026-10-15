from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.evidence import EvidenceAssessment
from app.retrieval import RetrievedDocument, evidence_citation_id


PUBLIC_EVIDENCE_RENDERER_VERSION = "pension-public-evidence-v8"

_WORD_RE = re.compile(r"[가-힣]{2,}|[A-Za-z][A-Za-z0-9-]{1,}|\d+(?:\.\d+)?")
_STOPWORDS = frozenset(
    {
        "상품",
        "연금",
        "관련",
        "대해",
        "비교",
        "설명",
        "주세요",
        "해주세요",
        "근거",
    }
)
_REDACTIONS = (
    (
        re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]+=*"),
        "Bearer [REDACTED]",
    ),
    (
        re.compile(
            r"(?i)((?:authorization|api[-_ ]?key|access[-_ ]?token|password|secret)"
            r"\s*[:=]\s*)[^\s,;]+"
        ),
        r"\1[REDACTED]",
    ),
    (
        re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
        "[EMAIL REDACTED]",
    ),
    (
        re.compile(r"(?<!\d)01[016789][ -]?\d{3,4}[ -]?\d{4}(?!\d)"),
        "[PHONE REDACTED]",
    ),
)


def _sanitize(value: str) -> str:
    result = value
    for pattern, replacement in _REDACTIONS:
        result = pattern.sub(replacement, result)
    return result


def _query_terms(question: str, assessment: EvidenceAssessment) -> tuple[str, ...]:
    combined = " ".join(
        [question, *(item.label for item in assessment.coverage if item.status == "supported")]
    )
    return tuple(
        dict.fromkeys(
            token.casefold()
            for token in _WORD_RE.findall(unicodedata.normalize("NFKC", combined))
            if token.casefold() not in _STOPWORDS
        )
    )


def _line_bounded(value: str, *, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    if len(value) <= max_chars:
        return value
    lines: list[str] = []
    used = 0
    for line in value.splitlines():
        addition = len(line) + (1 if lines else 0)
        if used + addition > max_chars:
            break
        lines.append(line)
        used += addition
    return "\n".join(lines)


def _best_excerpt(text: str, *, terms: tuple[str, ...], max_chars: int) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    normalized = [unicodedata.normalize("NFKC", line).casefold() for line in lines]
    window_size = min(6, len(lines))
    candidates: list[tuple[int, int, int]] = []
    for start in range(len(lines)):
        end = min(start + window_size, len(lines))
        joined = " ".join(normalized[start:end])
        score = sum(term in joined for term in terms)
        candidates.append((score, -start, start))
        if end == len(lines):
            break
    # The scoring window only locates where the relevant passage begins. Ending
    # the excerpt there as well used to cut a chunk off mid-rule — the DB payout
    # formula was lost right after "퇴직급여는 다음과 같이" — which left the model
    # to fill the gap from its own knowledge and get blocked. Extend from the
    # chosen start and let the character budget decide where to stop.
    _, _, best_start = max(candidates)
    excerpt = "\n".join(lines[best_start:])
    return _line_bounded(_sanitize(excerpt), max_chars=max_chars)


def _used_refs(assessment: EvidenceAssessment) -> frozenset[str]:
    supported: list[str] = []
    special_prefixes = ("calculator:", "comparison:", "structured:")
    for coverage in assessment.coverage:
        if coverage.status != "supported":
            continue
        special = tuple(
            ref for ref in coverage.evidence_refs if ref.startswith(special_prefixes)
        )
        if special:
            supported.extend(special)
            continue
        supported.extend(
            coverage.evidence_refs[: max(coverage.minimum_evidence_count, 1)]
        )
    conflicting = frozenset(
        ref
        for conflict in assessment.conflicts
        for ref in conflict.evidence_refs
    )
    return frozenset(supported) | conflicting


@dataclass(frozen=True, slots=True)
class _EvidenceSection:
    section_id: str
    kind: str
    text: str
    required_markers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PublicEvidenceRenderResult:
    version: str
    context: str
    complete: bool
    selected_sections: int
    rendered_sections: int
    missing_sections: tuple[str, ...]
    rendered_chars: int
    max_chars: int

    def trace_fields(self) -> dict[str, object]:
        return {
            "name": "public_evidence_renderer",
            "version": self.version,
            "status": "ok" if self.complete else "context_budget_exceeded",
            "selected_sections": self.selected_sections,
            "rendered_sections": self.rendered_sections,
            "missing_sections": list(self.missing_sections),
            "rendered_chars": self.rendered_chars,
            "max_chars": self.max_chars,
        }


_SECTION_RESERVED_CHARS = {
    "calculation": 600,
    "product": 500,
    "document": 300,
}


def _pack_sections(
    candidates: list[_EvidenceSection],
    *,
    expected_sections: frozenset[str],
    max_chars: int,
) -> tuple[list[str], tuple[str, ...]]:
    """Reserve space for every required evidence family before adding detail."""

    sanitized = [
        _EvidenceSection(
            section_id=item.section_id,
            kind=item.kind,
            text=_sanitize(item.text),
            required_markers=tuple(_sanitize(marker) for marker in item.required_markers),
        )
        for item in candidates
    ]
    if not sanitized:
        return [], tuple(sorted(expected_sections))
    separator_chars = 2 * max(len(sanitized) - 1, 0)
    available = max(max_chars - separator_chars, 0)
    if sum(len(item.text) for item in sanitized) <= available:
        sections = [item.text for item in sanitized if item.text]
        complete_ids = {
            item.section_id
            for item in sanitized
            if item.text and all(marker in item.text for marker in item.required_markers)
        }
        return sections, tuple(sorted(expected_sections - complete_ids))

    base = [
        min(len(item.text), _SECTION_RESERVED_CHARS[item.kind])
        for item in sanitized
    ]
    base_total = sum(base)
    if base_total > available and base_total:
        scale = available / base_total
        allocations = [int(value * scale) for value in base]
    else:
        allocations = list(base)
    remaining = available - sum(allocations)
    for index, item in enumerate(sanitized):
        if remaining <= 0:
            break
        addition = min(len(item.text) - allocations[index], remaining)
        allocations[index] += addition
        remaining -= addition

    sections: list[str] = []
    complete_ids: set[str] = set()
    for item, allocation in zip(sanitized, allocations):
        bounded = _line_bounded(item.text, max_chars=allocation)
        if bounded:
            sections.append(bounded)
            if all(marker in bounded for marker in item.required_markers):
                complete_ids.add(item.section_id)
    return sections, tuple(sorted(expected_sections - complete_ids))


def _line_with_prefix(value: str, prefix: str) -> str | None:
    return next(
        (line for line in value.splitlines() if line.startswith(prefix)),
        None,
    )


def _calculation_markers(value: str) -> tuple[str, ...]:
    prefixes = [
        "[DETERMINISTIC_CALCULATOR]",
        "citation_id=",
        "status=",
    ]
    if "status=calculated" in value:
        prefixes.extend(("calculated_tax_credit_won=", "formula=", "limitation="))
    elif "status=needs_input" in value:
        prefixes.append("missing_inputs=")
    markers = tuple(
        line
        for prefix in prefixes
        if (line := _line_with_prefix(value, prefix)) is not None
    )
    return markers or ("[DETERMINISTIC_CALCULATOR]",)


def _comparison_markers(value: str) -> tuple[str, ...]:
    lines = tuple(line for line in value.splitlines() if line)
    headers = tuple(
        line
        for line in lines
        if line.startswith(("[TYPED PRODUCT COMPARISON]", "[PRODUCT VALUE CONFLICT]"))
    )
    rows = tuple(
        line
        for line in lines
        if " | " in line and not line.startswith("product_code |")
    )
    return headers + rows or ("[TYPED PRODUCT COMPARISON]",)


def _structured_markers(value: str) -> tuple[str, ...]:
    lines = tuple(line for line in value.splitlines() if line)
    suitability_markers = tuple(
        line
        for line in lines
        if line.startswith(("[SUITABILITY PREFILTER]", "[SUITABILITY CANDIDATE]"))
    )
    if suitability_markers:
        return suitability_markers
    product_markers = tuple(line for line in lines if line.startswith("[PRODUCT EVIDENCE]"))
    return product_markers or lines[:1]


def _supported_source_ids(assessment: EvidenceAssessment) -> frozenset[str]:
    """Every plain-document source the assessor accepted for a requirement.

    ``_used_refs`` names the floor the answer must cite, one source per
    requirement.  This is the wider set the assessment already approved, which
    stays inside the evidence boundary the charter draws.

    A requirement that declares its own minimum is enumerating specific items,
    such as the two products a comparison names.  Widening it would put an
    unrequested product in front of the model, so only requirements that any
    one accepted source satisfies are widened.
    """

    special_prefixes = ("calculator:", "comparison:", "structured:")
    approved: set[str] = set()
    for coverage in assessment.coverage:
        if coverage.status not in {"supported", "conflicting"}:
            continue
        if coverage.minimum_evidence_count > 1:
            continue
        for ref in coverage.evidence_refs:
            if ref.startswith(special_prefixes):
                continue
            source_id, _, _ = ref.partition(":")
            if source_id:
                approved.add(source_id)
    return frozenset(approved)


def _document_section(
    document: RetrievedDocument, *, terms: tuple[str, ...], max_chars: int
) -> _EvidenceSection | None:
    header = (
        f"[EVIDENCE] source_id={document.source_id} "
        f"source_path={document.source_path} "
        f"locator={document.locator or 'unavailable'} chunk={document.index} "
        f"citation_id={evidence_citation_id(document)}"
        f"{document.governance.context_fields() if document.governance is not None else ''}"
    )
    excerpt = _best_excerpt(
        document.text,
        terms=terms,
        max_chars=max(max_chars - len(header) - 1, 0),
    )
    if not excerpt:
        return None
    return _EvidenceSection(
        f"document:{document.source_id}",
        "document",
        f"{header}\n{excerpt}",
        (f"citation_id={evidence_citation_id(document)}", excerpt.splitlines()[0]),
    )


def render_public_evidence_result(
    *,
    question: str,
    documents: list[RetrievedDocument],
    assessment: EvidenceAssessment,
    structured_context: str = "",
    comparison_context: str = "",
    calculation_context: str = "",
    stated_only_axes: frozenset[str] = frozenset(),
    max_chars: int,
) -> PublicEvidenceRenderResult:
    """Render only evidence selected by the deterministic assessment.

    The returned text is the single approved evidence boundary used by both
    the generation model and the evaluation response. Broader retrieval
    candidates stay visible only to the deterministic assessor.
    """

    if max_chars <= 0:
        return PublicEvidenceRenderResult(
            version=PUBLIC_EVIDENCE_RENDERER_VERSION,
            context="",
            complete=False,
            selected_sections=0,
            rendered_sections=0,
            missing_sections=("context_budget",),
            rendered_chars=0,
            max_chars=max_chars,
        )
    refs = _used_refs(assessment)
    terms = _query_terms(question, assessment)
    candidates: list[_EvidenceSection] = []
    expected_sections: set[str] = set()
    has_structured_product_evidence = any(
        ref.startswith(("comparison:", "structured:")) for ref in refs
    )

    if any(ref.startswith("calculator:") for ref in refs):
        section_id = "calculation"
        expected_sections.add(section_id)
        candidates.append(
            _EvidenceSection(
                section_id,
                "calculation",
                calculation_context,
                _calculation_markers(calculation_context),
            )
        )
    # A comparison only renders rows for the axes that carry typed values.
    # 위험등급 and 상품분류 are stated classifications, so a comparison that
    # settles only those produces no rows at all — and expecting a section that
    # can never be filled reported the whole answer as over budget. The values
    # themselves are already carried by the structured product evidence, so in
    # that case require that section instead.
    if any(ref.startswith("comparison:") for ref in refs) and comparison_context:
        section_id = "product:comparison"
        expected_sections.add(section_id)
        candidates.append(
            _EvidenceSection(
                section_id,
                "product",
                comparison_context,
                _comparison_markers(comparison_context),
            )
        )
        # A comparison of 위험등급 and 총보수 together renders rows only for the
        # fee, because a grade is stated rather than measured. Asking for both
        # then produced a context with no 위험등급 in it, and the model had
        # nothing to cite for half the question.
        if stated_only_axes and structured_context:
            stated_section_id = "product:structured"
            expected_sections.add(stated_section_id)
            candidates.append(
                _EvidenceSection(
                    stated_section_id,
                    "product",
                    structured_context,
                    _structured_markers(structured_context),
                )
            )
    elif any(
        ref.startswith(("comparison:", "structured:")) for ref in refs
    ) and structured_context:
        section_id = "product:structured"
        expected_sections.add(section_id)
        candidates.append(
            _EvidenceSection(
                section_id,
                "product",
                structured_context,
                _structured_markers(structured_context),
            )
        )

    used_source_ids = {
        ref.split(":", maxsplit=1)[0]
        for ref in refs
        if not ref.startswith(("calculator:", "comparison:", "structured:"))
    }
    expected_sections.update(f"document:{source_id}" for source_id in used_source_ids)
    approved_source_ids = _supported_source_ids(assessment)
    seen: set[tuple[str, int, str | None]] = set()
    fill: list[_EvidenceSection] = []
    for document in documents:
        if has_structured_product_evidence and document.source_group == "product":
            continue
        required = document.source_id in used_source_ids
        if not required and document.source_id not in approved_source_ids:
            continue
        identity = (document.source_id, document.index, document.locator)
        if identity in seen:
            continue
        seen.add(identity)
        section = _document_section(document, terms=terms, max_chars=max_chars)
        if section is None:
            continue
        (candidates if required else fill).append(section)
    sections, missing_sections = _pack_sections(
        candidates,
        expected_sections=frozenset(expected_sections),
        max_chars=max_chars,
    )
    # One source per requirement is the floor the answer must cite, not a
    # reason to hand back a fraction of the budget.  A question whose answer
    # sat in the third accepted document was refused while three quarters of
    # the context budget went unused.  The required sections are packed first
    # and keep their guarantee; whatever budget survives carries the remaining
    # documents the assessment already approved, so the evidence boundary is
    # unchanged.
    if fill:
        used = len("\n\n".join(sections))
        remaining = max_chars - used - (2 if sections else 0)
        if remaining > 0:
            extra, _ = _pack_sections(
                fill,
                expected_sections=frozenset(),
                max_chars=remaining,
            )
            sections.extend(extra)
    context = "\n\n".join(sections)
    return PublicEvidenceRenderResult(
        version=PUBLIC_EVIDENCE_RENDERER_VERSION,
        context=context,
        complete=not missing_sections,
        selected_sections=len(expected_sections),
        rendered_sections=len(sections),
        missing_sections=missing_sections,
        rendered_chars=len(context),
        max_chars=max_chars,
    )


def render_public_evidence(
    *,
    question: str,
    documents: list[RetrievedDocument],
    assessment: EvidenceAssessment,
    structured_context: str = "",
    comparison_context: str = "",
    calculation_context: str = "",
    max_chars: int,
) -> str:
    """Compatibility wrapper returning only the approved public context."""

    return render_public_evidence_result(
        question=question,
        documents=documents,
        assessment=assessment,
        structured_context=structured_context,
        comparison_context=comparison_context,
        calculation_context=calculation_context,
        max_chars=max_chars,
    ).context
