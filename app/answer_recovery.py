from __future__ import annotations

from collections.abc import Mapping

import re
import unicodedata

from app.evidence import EvidenceAssessment
from app.evidence_tables import normalize_evidence_tables
from app.routing import QuestionRoute
from app.rule_claim_validation import (
    is_nonquantitative_rule_claim,
    rule_relation_supports,
    rule_tokens,
    token_supported,
)
from app.semantic_coverage import expressed_slots, required_question_slots
from app.validation_primitives import (
    ANY_CITATION_RE,
    CITATION_RE,
    citation_excerpt_inventory,
    critical_citation_signatures,
    sentence_spans,
    split_citation_references,
)
from app.validation_types import AnswerValidationResult


_MINIMUM_TRIMMED_CHARS = 120
_FALLBACK_SYNONYMS = {
    "일부": "부분",
    "꺼내": "인출",
    "빼": "인출",
    "전부": "전체",
    "전세": "임차",
    "보증금": "임차보증금",
    "돈": "금액",
    "고르": "선택",
    "바꾸": "변경",
}
_FALLBACK_SKIP_TERMS = (
    "authorization",
    "api_key",
    "bearer ",
    "전산 화면",
    "전산 메뉴",
    "처리 코드",
    "화면 번호",
)
# Several organizer documents are written as FAQs, so their headings are
# questions. Quoting "IRP에 누가 가입할 수 있나요" as supporting evidence
# restates the question instead of answering it, and the heading carries no
# rule the reader can act on. A question can never be the evidence for an
# answer, so interrogative sentences are not fallback candidates.
# ``normalize_evidence_tables`` renders a row as "머리글: 칸; 머리글: 칸".
# The context labels its own blocks. Quoting "[PRODUCT EVIDENCE] product_code=
# KR5152420028 axis=class source_path=…" as evidence returns the scaffolding
# rather than anything the document says.
_CONTEXT_SCAFFOLDING_RE = re.compile(
    r"\s*(?:\[(?:PRODUCT EVIDENCE|EVIDENCE|TYPED PRODUCT COMPARISON|"
    r"PRODUCT VALUE CONFLICT|DETERMINISTIC_CALCULATOR|SUITABILITY PREFILTER|SOURCE)\b"
    r"|(?:product_code|source_path|source_id|citation_id|axis|status|dates|units|"
    r"classes|comparator_version|calculator|basis|instruction|missing_inputs)\s*=)"
)

_TABLE_ROW_RE = re.compile(r"[^:;]{1,30}: [^;]+(?:; [^:;]{1,30}: [^;]+)+")

_FALLBACK_QUESTION_RE = re.compile(
    r"(?:\?|나요|가요|까요|는지|ㄹ까|을까|일까|뭘까|뭔가|합니까|입니까|습니까)\s*$"
)


def _paragraph_citations(answer: str, start: int, end: int) -> tuple[str, ...]:
    before = answer.rfind("\n\n", 0, start)
    after = answer.find("\n\n", end)
    block = answer[(0 if before < 0 else before) : (len(answer) if after < 0 else after)]
    return tuple(
        dict.fromkeys(
            part
            for match in CITATION_RE.finditer(block)
            for part in split_citation_references(match.group(1).strip())
        )
    )


def _is_rule_claim(sentence: str, rule_claims: set[str]) -> bool:
    return any(marker and (marker in sentence or sentence in marker) for marker in rule_claims)


_COMPARISON_ROW_RE = re.compile(r"^\s*([A-Z]{2}[A-Z0-9]{8,})\s*\|.*\|\s*(\S+)\s*$")


def _comparison_row_excerpts(evidence_context: str) -> dict[str, str]:
    """Bind each typed comparison row to the source it cites.

    A comparison carries no ``citation_id=`` header, so repair saw no evidence
    at all and could not attach anything. The model then wrote the right source
    on a line of its own — "- 근거: 투자설명서/…pdf:page=3" — and the answer was
    discarded over where the marker sat rather than over what it said.
    """

    # One prospectus page backs several metrics, so every row sharing a source
    # is kept. Registering only the first hid 총보수 behind 총비용 and left the
    # answer's own figure with nothing to cite.
    rows: dict[str, list[str]] = {}
    for line in evidence_context.splitlines():
        match = _COMPARISON_ROW_RE.match(line)
        if match is None:
            continue
        cells = [cell.strip() for cell in line.split("|")]
        # The row keeps its value and its unit in separate columns, so "1.47"
        # read as a bare number and never matched the "연 1.47%" in the answer.
        # Restating the value with its unit lets the same signature comparison
        # used for prose apply here.
        restated = ""
        if len(cells) > 4 and "percent" in cells[4]:
            restated = f" {cells[3]}%"
        rows.setdefault(match.group(2), []).append(f"{line.strip()}{restated}")
    return {source: "\n".join(lines) for source, lines in rows.items()}


def attach_unambiguous_evidence_citations(
    answer: str,
    evidence_context: str,
    result: AnswerValidationResult,
) -> str | None:
    """Attach a real citation only when exact values identify one excerpt.

    The caller must run the unchanged validator again before accepting the
    repaired answer. Rule claims receive ``근거`` only after relation checking;
    figure-only repairs receive the narrower ``수치출처`` marker.
    """

    rule_claims = {
        claim.text.strip()
        for claim in result.unsupported_claims
        if claim.kind == "rule_citation_missing" and claim.text.strip()
    }
    value_claims = {
        claim.text.strip()
        for claim in result.unsupported_claims
        if claim.kind == "citation_missing" and claim.text.strip()
    }
    flagged = tuple(rule_claims | value_claims)
    if not flagged:
        return None
    excerpts = dict(citation_excerpt_inventory(evidence_context))
    excerpts.update(_comparison_row_excerpts(evidence_context))
    if not excerpts:
        return None
    excerpt_signatures = {
        reference: critical_citation_signatures(excerpt)
        for reference, excerpt in excerpts.items()
    }
    insertions: list[tuple[int, str]] = []
    for start, end in sentence_spans(answer):
        sentence = answer[start:end].strip()
        # A sentence already carrying either marker needs no repair.
        if not sentence or ANY_CITATION_RE.search(sentence) is not None:
            continue
        if not any(marker in sentence or sentence in marker for marker in flagged):
            continue
        signatures = critical_citation_signatures(sentence)
        is_rule = _is_rule_claim(sentence, rule_claims)
        if signatures:
            candidates = tuple(
                reference
                for reference, available in excerpt_signatures.items()
                if signatures.issubset(available)
            )
        elif is_rule:
            # Repair was written for figures: a sentence carrying no number was
            # skipped outright. Most of what the model leaves uncited is a plain
            # rule — "DB와 DC 제도는 재직 중인 회사를 통해서만 이전 신청이
            # 가능합니다" — so the answer was discarded for a missing marker
            # while the evidence for it sat in the context. Ask the same
            # relation check the validator applies which excerpts support the
            # sentence; a marker is attached only where that check already
            # holds, and the caller revalidates the whole answer regardless.
            candidates = tuple(
                reference
                for reference, excerpt in excerpts.items()
                if rule_relation_supports(sentence, excerpt)
            )
        else:
            continue
        calculator = tuple(
            reference for reference in candidates if reference.endswith(":calculator")
        )
        marker = "근거" if is_rule else "수치출처"
        if len(calculator) == 1:
            insertions.append((end, f" [{marker}:{calculator[0]}]"))
            continue
        paragraph = _paragraph_citations(answer, start, end)
        supported_in_paragraph = tuple(
            reference for reference in paragraph if reference in candidates
        )
        if len(supported_in_paragraph) == 1:
            insertions.append((end, f" [{marker}:{supported_in_paragraph[0]}]"))
            continue
        sources = {reference.split(":", maxsplit=1)[0] for reference in candidates}
        if candidates and len(sources) == 1:
            insertions.append((end, f" [{marker}:{sorted(candidates)[0]}]"))
    if not insertions:
        return None
    repaired = answer
    for position, citation in reversed(insertions):
        repaired = f"{repaired[:position]}{citation}{repaired[position:]}"
    return repaired


_NUMBERED_ITEM_RE = re.compile(r"^\s*(\d{1,2})\.\s")

_BULLET_ONLY_RE = re.compile(r"^[\s\-*•·>#\d.)\]]*$")


def drop_unsupported_lines(
    answer: str,
    result: AnswerValidationResult,
) -> str | None:
    """Remove the claims that could not be tied to evidence.

    The validator flags a sentence, so a sentence is what is removed. Dropping
    the whole line discarded the cited sentences sharing it: a paragraph that
    ended "…자세한 사항은 금융기관에 문의하시기 바랍니다" lost its grounded
    content too, and what remained was usually too short to keep, so the answer
    was refused instead of trimmed.
    """

    flagged = {
        item.text.strip()
        for item in result.unsupported_claims
        if isinstance(item.text, str) and item.text.strip()
    }
    if not flagged:
        return None
    dropped: list[tuple[int, int]] = [
        (start, end)
        for start, end in sentence_spans(answer)
        if (sentence := answer[start:end].strip())
        and any(marker and (marker in sentence or sentence in marker) for marker in flagged)
    ]
    if not dropped:
        return None
    kept_text: list[str] = []
    cursor = 0
    for start, end in dropped:
        kept_text.append(answer[cursor:start])
        cursor = end
    kept_text.append(answer[cursor:])
    # A bullet whose only sentence was removed leaves its marker behind.
    lines = [
        line
        for line in "".join(kept_text).splitlines()
        if not _BULLET_ONLY_RE.match(line) or not line.strip()
    ]
    trimmed = re.sub(r"[ \t]{2,}", " ", "\n".join(lines))
    # Removing a sentence leaves the punctuation that separated it behind.
    trimmed = re.sub(r"(?m)^[\s.·]*\.\s+", "", trimmed)
    trimmed = re.sub(r"\.\s*\.(?=\s|$)", ".", trimmed)
    trimmed = re.sub(r"\s+([.,])", r"\1", trimmed)
    trimmed = re.sub(r"\n{3,}", "\n\n", trimmed).strip()
    trimmed = _renumber_list_items(trimmed)
    # A figure-only repair leaves 수치출처 markers, which are citations too.
    # Requiring 근거 here threw away answers whose every claim was a figure.
    if len(trimmed) < _MINIMUM_TRIMMED_CHARS or ANY_CITATION_RE.search(trimmed) is None:
        return None
    return trimmed


def _renumber_list_items(answer: str) -> str:
    """Close the gap a removed numbered item leaves in a list.

    Dropping "1. 일반 중도해지…" left the answer opening at "2.", which reads
    as a missing step. Only the numbering is rewritten; no wording changes.
    """

    items = [
        index
        for index, line in enumerate(answer.splitlines())
        if _NUMBERED_ITEM_RE.match(line)
    ]
    if not items:
        return answer
    lines = answer.splitlines()
    if _NUMBERED_ITEM_RE.match(lines[items[0]]).group(1) == "1":
        return answer
    for position, index in enumerate(items, start=1):
        lines[index] = _NUMBERED_ITEM_RE.sub(
            lambda match, value=position: f"{match.group(0)[: match.start(1)]}"
            f"{value}{match.group(0)[match.end(1) :]}",
            lines[index],
            count=1,
        )
    return "\n".join(lines)


def _fallback_query_tokens(
    question: str,
    assessment: EvidenceAssessment,
) -> frozenset[str]:
    value = " ".join(
        (question, *(item.label for item in assessment.coverage if item.status == "supported"))
    )
    normalized = unicodedata.normalize("NFKC", value).casefold()
    for source, target in _FALLBACK_SYNONYMS.items():
        normalized = normalized.replace(source, f"{source} {target}")
    return rule_tokens(normalized)


def _subquestion_sources(assessment: EvidenceAssessment) -> dict[str, frozenset[str]]:
    """Map each subquestion to the plain-document sources that support it.

    A compound question is planned as several subquestions, and the assessment
    records which sources answer each one. The fallback needs that split: the
    union of the question's slots can be satisfied while a whole subquestion
    goes unanswered.
    """

    special_prefixes = ("calculator:", "comparison:", "structured:")
    mapping: dict[str, set[str]] = {}
    for coverage in assessment.coverage:
        if coverage.status not in {"supported", "conflicting"}:
            continue
        subquestion_id = coverage.subquestion_id
        if not subquestion_id:
            continue
        for ref in coverage.evidence_refs:
            if ref.startswith(special_prefixes):
                continue
            source_id, _, _ = ref.partition(":")
            if source_id:
                mapping.setdefault(subquestion_id, set()).add(source_id)
    return {key: frozenset(value) for key, value in mapping.items() if value}


_GARBLED_SYMBOL_RE = re.compile(r"([@#*|~^\\])\1|\[\)|\(\]")
# A bare jamo is never written in prose, and a lone lowercase letter dropped
# between Korean words is what a scan leaves behind: "연금 수령 s 후부터
# 가능ㅎ 하셔야 현". "x" is excluded because the documents write multiplication
# that way, as in "900만원 x 16.5%".
_STRAY_JAMO_RE = re.compile(r"[ㄱ-ㅎㅏ-ㅣ]")
_STRAY_LETTER_RE = re.compile(r"(?<![A-Za-z0-9-])[a-wyz](?![A-Za-z0-9-])")
_BRACKET_PAIRS = (("(", ")"), ("[", "]"), ("{", "}"))


def _is_garbled(sentence: str) -> bool:
    """True when the line carries the marks of a broken text extraction.

    Some organizer pages are scanned, and their text arrives as "- 0[) 계좌
    개설 | 퇴직연금(개인형16) 계좌가 없다면 1-51". Quoting that as evidence
    reads as a fault in the answer. Brackets that do not close, and repeated
    stray symbols, are what separates it from prose; a fragment that merely
    lost its opening words has no such mark and is not caught here.
    """

    if _GARBLED_SYMBOL_RE.search(sentence):
        return True
    if _STRAY_JAMO_RE.search(sentence) or _STRAY_LETTER_RE.search(sentence):
        return True
    # Only a bracket that closes what never opened. An excerpt is cut at a line
    # boundary, so a real sentence routinely ends mid-parenthesis — "퇴직 시
    # 근로자가 연금(만 55 세 이후" is the corpus answering when the pension can
    # be drawn, and counting that as damage discarded it.
    depth = 0
    for character in sentence:
        if character in "([{":
            depth += 1
        elif character in ")]}":
            depth -= 1
            if depth < 0:
                return True
    return False


def _restates_title(sentence: str, title: str | None) -> bool:
    """True when the sentence says no more than the document's title does."""

    if not title:
        return False
    title_tokens = rule_tokens(title)
    if not title_tokens:
        return False
    sentence_tokens = rule_tokens(sentence)
    return all(token_supported(token, sentence_tokens) for token in title_tokens)


def render_grounded_extractive_fallback(
    *,
    question: str,
    evidence_context: str,
    assessment: EvidenceAssessment,
    route: QuestionRoute,
    document_titles: Mapping[str, str] | None = None,
    max_bullets: int = 7,
) -> str | None:
    """Build a last-resort answer from exact approved evidence sentences."""

    excerpts = citation_excerpt_inventory(evidence_context)
    if not excerpts:
        return None
    titles = document_titles or {}
    query_tokens = _fallback_query_tokens(question, assessment)
    required_slots = required_question_slots(question)
    candidates: list[
        tuple[int, int, str, str, frozenset[str], frozenset[str], frozenset[str]]
    ] = []
    for reference, excerpt in excerpts.items():
        excerpt = normalize_evidence_tables(excerpt)
        for position, (start, end) in enumerate(sentence_spans(excerpt)):
            raw = re.sub(r"\s+", " ", excerpt[start:end]).strip(" \t-·■")
            if not 18 <= len(raw) <= 520:
                continue
            normalized = unicodedata.normalize("NFKC", raw).casefold()
            if any(term in normalized for term in _FALLBACK_SKIP_TERMS):
                continue
            if _FALLBACK_QUESTION_RE.search(raw):
                continue
            if _is_garbled(raw):
                continue
            if _CONTEXT_SCAFFOLDING_RE.match(raw):
                continue
            # A heading names the document; it states nothing the reader can
            # act on, and quoting it as evidence says only that the document
            # exists. The registry title is curated, so a line is read as the
            # document's own heading when it restates every word of that title.
            if _restates_title(raw, titles.get(reference)):
                continue
            tokens = rule_tokens(raw)
            overlap = frozenset(
                token for token in query_tokens if token_supported(token, tokens)
            )
            covered_slots = set(expressed_slots(raw, required_slots))
            # Sharing a word with the question is one way to be relevant, not
            # the only one.  "사유에 해당하지 않는다면 연금외수령으로 16.5%
            # 기타소득세가 과세된다" answers a question about 중도해지 세금
            # while sharing no token with it, and dropping it left the fallback
            # quoting unrelated lines until the coverage check refused the
            # whole answer.  A required slot the sentence expresses is the same
            # relevance signal the coverage check applies, so either qualifies.
            if not overlap and not covered_slots:
                continue
            consequential = int(is_nonquantitative_rule_claim(raw, route))
            # Counting figures is meant to favour the sentence that answers a
            # "how much" question. A normalised table row carries the figures of
            # one case, so density let "사유: 6개월 이상 요양 … 세율: 5.5%" beat
            # the rule it is an instance of, answering about a 사유 the question
            # never raised. A row still competes on slots and wording; it just
            # cannot win on how many numbers its case happens to hold.
            quantitative = (
                0
                if _TABLE_ROW_RE.match(raw)
                else len(critical_citation_signatures(raw))
            )
            score = (
                len(overlap) * 20
                + len(covered_slots) * 35
                + consequential * 5
                + quantitative * 3
            )
            candidates.append(
                (
                    score,
                    -position,
                    reference,
                    raw,
                    overlap,
                    frozenset(covered_slots),
                    critical_citation_signatures(raw),
                )
            )
    if not candidates:
        return None

    # "DB형과 DC형의 차이, IRP 세액공제 한도, IRP 중도인출 절차"의 세 하위질문
    # 중 두 개만 답해도 질문 전체의 슬롯은 채워졌고, 그 시점에 선택이 멈춰
    # DB/DC 차이가 통째로 빠졌다. 어떤 하위질문도 근거 없이 남겨두지 않도록
    # 후보가 존재하는 하위질문은 모두 한 건 이상 채운 뒤에 멈춘다.
    subquestion_sources = _subquestion_sources(assessment)
    candidate_sources = {item[2].split(":", maxsplit=1)[0] for item in candidates}
    pending_subquestions = {
        subquestion_id
        for subquestion_id, sources in subquestion_sources.items()
        if sources & candidate_sources
    }

    def _serves(reference: str) -> frozenset[str]:
        source = reference.split(":", maxsplit=1)[0]
        return frozenset(
            subquestion_id
            for subquestion_id, sources in subquestion_sources.items()
            if source in sources
        )

    selected: list[tuple[str, str]] = []
    covered: set[str] = set()
    covered_slots: set[str] = set()
    per_source: dict[str, int] = {}
    remaining = list(candidates)
    while remaining and len(selected) < max_bullets:
        best = max(
            remaining,
            key=lambda item: (
                len(_serves(item[2]) & pending_subquestions) * 80
                + len(item[5] - covered_slots) * 60
                + len(item[4] - covered) * 25
                + item[0],
                item[1],
                item[2],
            ),
        )
        remaining.remove(best)
        _score, _position, reference, sentence, overlap, slots, _signatures = best
        source = reference.split(":", maxsplit=1)[0]
        if per_source.get(source, 0) >= 3:
            continue
        if any(sentence == existing for existing, _ in selected):
            continue
        selected.append((sentence, reference))
        per_source[source] = per_source.get(source, 0) + 1
        covered.update(overlap)
        covered_slots.update(slots)
        pending_subquestions -= _serves(reference)
        if pending_subquestions:
            continue
        # One quoted sentence is rarely the answer. "부득이한 사유에 해당하면
        # … 저율의 연금소득세를 적용 받는다" covered both required slots on its
        # own, so selection stopped there and the answer stated only the
        # exception, and a reader would take the low rate for the ordinary
        # case. A second sentence is kept whenever one is left that still adds
        # something the first did not.
        if required_slots and covered_slots >= required_slots and len(selected) >= 2:
            break
        if (
            not required_slots
            and query_tokens
            and len(covered) >= min(len(query_tokens), 8)
            and len(selected) >= 2
        ):
            break
    if not selected:
        return None
    bullets = "\n".join(
        f"- {sentence} [근거:{reference}]." for sentence, reference in selected
    )
    return (
        "생성 초안 대신, 질문과 직접 관련된 제공 자료의 확인 내용을 제시합니다.\n"
        f"{bullets}\n"
        "위 내용은 응답의 retrieved_context에 함께 제공된 자료 범위에 한정됩니다."
    )


def render_validation_failure(result: AnswerValidationResult) -> str:
    reasons: list[str] = []
    if result.unsupported_claims:
        kinds = ", ".join(dict.fromkeys(claim.kind for claim in result.unsupported_claims))
        reasons.append(f"근거에서 확인되지 않은 정량·식별·규칙 주장({kinds})")
    if result.sensitive_spans:
        reasons.append("노출하면 안 되는 내부 제어·인증 표현")
    if result.recommendation_blocked:
        reasons.append("필수 조건이 확인되지 않은 단정적 추천")
    if result.question_coverage_failures:
        reasons.append("질문의 핵심 조건·예외에 대한 답변 누락")
    detail = ", ".join(reasons) or "검증되지 않은 주장"
    return (
        f"생성된 초안에 {detail}이 포함되어 안전하게 제공하지 않습니다. "
        "주최 측 자료에서 직접 확인되는 범위로 질문 조건이나 비교 기준을 더 구체화해 주세요."
    )
