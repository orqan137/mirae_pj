from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.evidence_tables import normalize_evidence_tables
from app.product_facts import PRODUCT_CODE_RE
from app.routing import QuestionRoute
from app.semantic_coverage import PENSION_RULE_TOPICS
from app.validation_primitives import (
    ANY_CITATION_RE,
    BALANCE_UNITS_RE,
    CITATION_RE,
    CURRENCY_RE,
    DATE_RE,
    PERCENT_RE,
    citation_spans,
    critical_citation_signatures,
    inside_span,
    resolve_citation_reference,
    sentence_span,
    sentence_spans,
    split_citation_references,
)
from app.validation_types import UnsupportedClaim


_LIST_LEAD_IN_RE = re.compile(
    r"[^:\r\n]{0,100}?(?:다음과 같습니다|다음과 같다|다음과 같이 정리할 수 있습니다"
    r"|아래와 같습니다|아래와 같다|정리하면|요약하면|설명드리겠습니다)\s*[::]\s*"
)
# A question that matched no other family is still answered with pension
# rules. Leaving "combined" out meant a 790-character procedural answer was
# validated with zero claims checked, because rule extraction never ran.
_RULE_ROUTE_LABELS = frozenset({"system", "tax", "procedure", "combined"})
_RULE_DOMAIN_TERMS = (
    "연금", "irp", "db", "dc", "퇴직", "계좌", "가입", "납입", "입금",
    "중도인출", "중도 인출", "이전", "수령", "해지", "세액공제", "과세",
    "세금", "세율",
)
_RULE_PREDICATE_TERMS = (
    "가능", "불가", "할 수 있", "할 수 없", "해야", "하여야", "대상", "요건",
    "제외", "금지", "허용", "필요", "적용", "인정", "가입", "납입", "인출",
    "이전", "수령", "과세", "공제",
)
_RULE_META_LIMITS = (
    "제공 자료에서 확인", "주최 측 자료에서 확인", "근거를 찾지 못", "확인되지 않은",
    "추정하지 않", "확정할 수 없", "추가 확인",
)
_RULE_NEGATIVE_TERMS = (
    "불가", "할 수 없", "허용되지 않", "필요하지 않", "필요 없", "불필요", "금지",
    "제외", "아니",
)
_RULE_POSITIVE_TERMS = ("가능", "할 수 있", "허용", "인정")
_RULE_UNRESTRICTED_TERMS = (
    "아무 사유 없이", "제한 없이", "조건 없이", "언제든", "무조건", "모든 경우",
    "항상", "누구나", "자유롭게", "소득과 무관하게", "소득에 관계없이",
    "납입액과 무관하게", "사유가 없어도", "요건이 없어도",
)
_RULE_RESTRICTED_TERMS = (
    "법정 사유", "법에서 정한", "특정 사유", "경우에 한", "한하여", "요건", "제한",
    "제외", "단,",
)
_RULE_REQUIRED_TERMS = ("필요", "해야", "하여야", "요구")
_RULE_NOT_REQUIRED_TERMS = ("필요하지 않", "필요 없", "불필요", "없이도")
_RULE_ACTION_NEGATIVE_RE = re.compile(
    r"(?:선택|고르|고를|인출|이전|가입|납입|수령)[^.!?\n]{0,12}"
    r"(?:할 수 없|할수 없|수 없|하지 못|불가)"
)
_RULE_ACTION_POSITIVE_RE = re.compile(
    r"(?:선택|고르|고를|인출|이전|가입|납입|수령)[^.!?\n]{0,12}"
    r"(?:할 수 있|할수 있|수 있|가능)"
)
_RULE_STATE_ROOTS = r"(?:적용|과세|공제|인정|허용)"
_RULE_STATE_PARTICLE = r"(?:이|가|은|는|을|를)?"
_RULE_STATE_NEGATIVE_RE = re.compile(
    rf"(?:{_RULE_STATE_ROOTS}(?:되지\s*않|되지\s*못|하지\s*않|하지\s*못))|"
    rf"(?:{_RULE_STATE_ROOTS}{_RULE_STATE_PARTICLE}\s*"
    r"(?:(?:대상|범위)(?:이|가|은|는|에서|에)?\s*)?"
    r"(?:해당하지\s*않|받지\s*못|받을\s*수\s*없|할\s*수\s*없|"
    r"안\s*됩|안\s*됨|불가|아니|아닙|아님|아닌|제외))"
)
_RULE_STATE_POSITIVE_RE = re.compile(
    rf"(?:{_RULE_STATE_ROOTS}(?:됩|되|됨|합|함|한다))|"
    rf"(?:{_RULE_STATE_ROOTS}{_RULE_STATE_PARTICLE}\s*"
    r"(?:(?:대상(?:입|임|에\s*해당))|(?:받을|할)\s*수\s*있|가능))"
)
_RULE_CLAUSE_CONNECTOR_RE = re.compile(
    r"\s+(?:그리고|또한|반면|하지만|그러나|다만)\s+|"
    r"(?P<predicate>할 수 있|할 수 없|필요하지 않|허용되지 않|"
    r"가능|불가|필요|불필요|허용|금지|인정|적용되|적용|요구되|요구|가입|납입|"
    r"인출|이전|수령|과세|공제)(?P<ending>하지만|하고|하며|이고|이며|하나|고|며)\s+"
)
_RULE_TOKEN_RE = re.compile(r"[가-힣]{2,}|[A-Za-z][A-Za-z0-9_-]+")
_RULE_STOPWORDS = frozenset(
    {"그리고", "그러나", "따라", "대한", "위해", "또는", "경우", "해당", "관련", "내용", "기준"}
)
_RULE_SUFFIXES = (
    "했습니다", "됩니다", "합니다", "입니다", "있습니다", "없습니다", "으로부터",
    "에서부터", "에게서", "으로", "에서", "에게", "까지", "부터", "이라도", "이라면",
    "에는", "으로는", "은", "는", "이", "가", "을", "를", "의", "에", "도", "만",
    "와", "과",
)
# An excerpt whose topic tag is missing must share at least this share of the
# claim's content words (1/N) before it can stand as the claim's evidence.
# Calibrated against the answer guard and rule-claim holdout below.
_RULE_UNTAGGED_OVERLAP_DENOMINATOR = 3

_RULE_FAILURE_PRIORITY = {
    "rule_polarity_contradiction": 0,
    "rule_qualifier_contradiction": 1,
    "rule_citation_relation": 2,
}


def _rule_token(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    for suffix in _RULE_SUFFIXES:
        if normalized.endswith(suffix) and len(normalized) - len(suffix) >= 2:
            return normalized[: -len(suffix)]
    return normalized


def rule_tokens(value: str) -> frozenset[str]:
    without_citations = ANY_CITATION_RE.sub(" ", value)
    return frozenset(
        token
        for raw in _RULE_TOKEN_RE.findall(without_citations)
        if len(token := _rule_token(raw)) >= 2 and token not in _RULE_STOPWORDS
    )


def _rule_topics(value: str) -> frozenset[str]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return frozenset(
        name
        for name, terms in PENSION_RULE_TOPICS
        if any(term in normalized for term in terms)
    )


def _rule_topic_anchor(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    matches: list[tuple[int, str]] = []
    for _name, terms in PENSION_RULE_TOPICS:
        candidates = (
            (position, term)
            for term in terms
            if (position := normalized.find(term)) >= 0
        )
        match = min(candidates, default=None, key=lambda item: (item[0], -len(item[1])))
        if match is not None:
            matches.append(match)
    return " ".join(
        term for _position, term in sorted(set(matches), key=lambda item: item[0])
    )


@dataclass(frozen=True, slots=True)
class _RuleClaimUnit:
    text: str
    citation_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _RulePolarity:
    positive: bool
    negative: bool
    action_positive: bool
    action_negative: bool
    state_positive: bool
    state_negative: bool


def _rule_polarity(normalized: str) -> _RulePolarity:
    state_negative = _RULE_STATE_NEGATIVE_RE.search(normalized) is not None
    return _RulePolarity(
        positive=any(term in normalized for term in _RULE_POSITIVE_TERMS),
        negative=any(term in normalized for term in _RULE_NEGATIVE_TERMS),
        action_positive=_RULE_ACTION_POSITIVE_RE.search(normalized) is not None,
        action_negative=_RULE_ACTION_NEGATIVE_RE.search(normalized) is not None,
        state_positive=(
            not state_negative
            and _RULE_STATE_POSITIVE_RE.search(normalized) is not None
        ),
        state_negative=state_negative,
    )


def _opposite_rule_polarity(
    claim: _RulePolarity,
    evidence: _RulePolarity,
) -> bool:
    if (claim.action_negative and evidence.action_positive) or (
        claim.action_positive and evidence.action_negative
    ):
        return True
    if (claim.state_negative and evidence.state_positive) or (
        claim.state_positive and evidence.state_negative
    ):
        return True
    if claim.positive and not claim.negative and evidence.negative and not evidence.positive:
        return True
    return bool(
        claim.negative
        and not claim.positive
        and evidence.positive
        and not evidence.negative
    )


def _has_quantitative_or_identity_claim(value: str) -> bool:
    return any(
        pattern.search(value) is not None
        for pattern in (PERCENT_RE, CURRENCY_RE, BALANCE_UNITS_RE, DATE_RE, PRODUCT_CODE_RE)
    )


def is_rule_claim(value: str, route: QuestionRoute) -> bool:
    if not (_RULE_ROUTE_LABELS & set(route.labels)):
        return False
    normalized = unicodedata.normalize("NFKC", ANY_CITATION_RE.sub(" ", value)).casefold()
    if not normalized.strip() or "?" in normalized:
        return False
    if any(term in normalized for term in _RULE_META_LIMITS):
        return False
    return any(term in normalized for term in _RULE_DOMAIN_TERMS) and any(
        term in normalized for term in _RULE_PREDICATE_TERMS
    )


def is_nonquantitative_rule_claim(value: str, route: QuestionRoute) -> bool:
    return is_rule_claim(value, route) and not _has_quantitative_or_identity_claim(value)


def _rule_claim_units(sentence: str, route: QuestionRoute) -> tuple[_RuleClaimUnit, ...]:
    stripped = sentence.strip()
    if _LIST_LEAD_IN_RE.fullmatch(stripped) and not critical_citation_signatures(stripped):
        return ()

    citation_ranges = citation_spans(sentence)
    boundaries = tuple(
        match
        for match in _RULE_CLAUSE_CONNECTOR_RE.finditer(sentence)
        if not inside_span(match.start(), citation_ranges)
    )
    segments: list[str] = []
    start = 0
    for boundary in boundaries:
        ending_start = boundary.start("ending")
        end = ending_start if ending_start >= 0 else boundary.start()
        if sentence[start:end].strip():
            segments.append(sentence[start:end])
        start = boundary.end()
    if sentence[start:].strip():
        segments.append(sentence[start:])

    sentence_refs = tuple(match.group(1).strip() for match in CITATION_RE.finditer(sentence))
    single_sentence_ref = sentence_refs if len(sentence_refs) == 1 else ()
    inherited_anchor = ""
    units: list[_RuleClaimUnit] = []
    for segment in segments:
        local_refs = tuple(match.group(1).strip() for match in CITATION_RE.finditer(segment))
        semantic_text = re.sub(r"\s+", " ", CITATION_RE.sub(" ", segment)).strip()
        semantic_text = semantic_text.strip(" ,;")
        if not semantic_text:
            continue
        anchor = _rule_topic_anchor(semantic_text)
        contextual_text = (
            semantic_text
            if anchor or not inherited_anchor
            else f"{inherited_anchor} {semantic_text}"
        )
        if anchor:
            inherited_anchor = anchor
        if is_rule_claim(contextual_text, route):
            units.append(
                _RuleClaimUnit(
                    text=contextual_text,
                    citation_refs=local_refs or single_sentence_ref,
                )
            )
    return tuple(units)


def token_supported(token: str, evidence_tokens: frozenset[str]) -> bool:
    return any(
        token == evidence
        or (len(token) >= 3 and token in evidence)
        or (len(evidence) >= 3 and evidence in token)
        for evidence in evidence_tokens
    )


# The corpus writes the schemes every way a heading allows — "DB형", "DB 제도",
# "DB,DC", "DB/DC/기업형IRP" — so the acronym is matched on its own boundaries
# rather than against a list of suffixes.
_RULE_SCHEME_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("db", re.compile(r"(?<![a-z0-9])db(?![a-z0-9])|확정급여")),
    ("dc", re.compile(r"(?<![a-z0-9])dc(?![a-z0-9])|확정기여")),
    ("irp", re.compile(r"(?<![a-z0-9])irp(?![a-z0-9])|개인형\s*퇴직연금")),
    ("pension_savings", re.compile(r"연금저축")),
)


def _rule_schemes(value: str) -> frozenset[str]:
    """Name the pension schemes a sentence is about.

    A rule about 연금저축 is not evidenced by a rule about DB and DC, however
    much wording the two share. The topic tag used to block that pairing as a
    side effect; naming the scheme states the requirement directly.
    """

    normalized = unicodedata.normalize("NFKC", value).casefold()
    return frozenset(
        scheme
        for scheme, pattern in _RULE_SCHEME_PATTERNS
        if pattern.search(normalized)
    )


def _best_rule_evidence_sentence(claim: str, excerpt: str) -> str:
    excerpt = normalize_evidence_tables(excerpt)
    claim_tokens = rule_tokens(claim)
    claim_topics = _rule_topics(claim)
    candidates = tuple(
        excerpt[start:end].strip()
        for start, end in sentence_spans(excerpt)
        if excerpt[start:end].strip()
    ) or (excerpt,)
    # Ranking the topic tag above token overlap let a sentence sharing three
    # words beat one sharing six. The corpus writes "퇴직연금 금융기관을
    # 변경할 수 있는 제도" where the claim says 이전, so the near-verbatim
    # sentence carried no topic tag and lost to a loosely related one. Overlap
    # decides which sentence is being talked about; the tag breaks ties.
    return max(
        candidates,
        key=lambda candidate: (
            sum(
                token_supported(token, rule_tokens(candidate))
                for token in claim_tokens
            ),
            int(claim_topics.issubset(_rule_topics(candidate))),
        ),
    )


def rule_relation_supports(claim: str, excerpt: str) -> bool:
    """True when the excerpt passes the same relation check the validator applies.

    Repair uses this so a citation is only ever attached where the validator
    would accept the relation. The caller still re-runs the whole validator on
    the repaired answer, so this widens nothing.
    """

    return _rule_relation_failure(claim, excerpt) is None


def _rule_relation_failure(claim: str, excerpt: str) -> str | None:
    best = _best_rule_evidence_sentence(claim, excerpt)
    claim_topics = _rule_topics(claim)
    evidence_topics = _rule_topics(best)
    claim_tokens = rule_tokens(claim)
    evidence_tokens = rule_tokens(best)
    supported_tokens = sum(token_supported(token, evidence_tokens) for token in claim_tokens)
    minimum_overlap = (
        1
        if critical_citation_signatures(claim)
        else max(2, (len(claim_tokens) + 2) // 3)
    )
    if supported_tokens < minimum_overlap:
        return "rule_citation_relation"
    # The topic tag guards against citing a sentence about a different subject.
    # Its vocabulary is not exhaustive, so a near-verbatim excerpt can carry no
    # tag at all and be rejected outright. Sharing most of the claim's content
    # words establishes the same subject more directly than the tag does, so
    # either signal satisfies the gate.
    if not claim_topics.issubset(evidence_topics) and (
        supported_tokens * _RULE_UNTAGGED_OVERLAP_DENOMINATOR < len(claim_tokens)
    ):
        return "rule_citation_relation"
    claim_schemes = _rule_schemes(claim)
    if claim_schemes and not claim_schemes.issubset(_rule_schemes(best)):
        return "rule_citation_relation"

    normalized_claim = unicodedata.normalize("NFKC", claim).casefold()
    normalized_evidence = unicodedata.normalize("NFKC", best).casefold()
    if _opposite_rule_polarity(
        _rule_polarity(normalized_claim),
        _rule_polarity(normalized_evidence),
    ):
        return "rule_polarity_contradiction"
    claim_not_required = any(term in normalized_claim for term in _RULE_NOT_REQUIRED_TERMS)
    evidence_not_required = any(term in normalized_evidence for term in _RULE_NOT_REQUIRED_TERMS)
    claim_required = not claim_not_required and any(
        term in normalized_claim for term in _RULE_REQUIRED_TERMS
    )
    evidence_required = not evidence_not_required and any(
        term in normalized_evidence for term in _RULE_REQUIRED_TERMS
    )
    if (claim_not_required and evidence_required) or (claim_required and evidence_not_required):
        return "rule_polarity_contradiction"
    if any(term in normalized_claim for term in _RULE_UNRESTRICTED_TERMS) and any(
        term in normalized_evidence for term in _RULE_RESTRICTED_TERMS
    ):
        return "rule_qualifier_contradiction"
    return None


def validate_citation_claims(
    *,
    answer: str,
    allowed: frozenset[str],
    excerpts: dict[str, str],
    route: QuestionRoute,
) -> tuple[int, int, tuple[UnsupportedClaim, ...]]:
    """Validate citation identity, completeness and rule-to-excerpt relations."""

    if not allowed:
        return 0, 0, ()
    citations = tuple(ANY_CITATION_RE.finditer(answer))
    failures: list[UnsupportedClaim] = []
    for match in citations:
        written = match.group(1).strip()
        parts = split_citation_references(written)
        resolved = tuple(resolve_citation_reference(part, allowed) for part in parts)
        reference = None if any(item is None for item in resolved) else resolved[0]
        if reference is None:
            failures.append(UnsupportedClaim("citation_not_in_context", match.group(0), written))

    citation_ranges = tuple((match.start(), match.end()) for match in citations)
    critical_sentences: set[tuple[int, int]] = set()
    for pattern in (PERCENT_RE, CURRENCY_RE, BALANCE_UNITS_RE, DATE_RE, PRODUCT_CODE_RE):
        for match in pattern.finditer(answer):
            if not inside_span(match.start(), citation_ranges):
                critical_sentences.add(sentence_span(answer, match.start()))
    for start, end in sorted(critical_sentences):
        sentence = answer[start:end]
        sentence_citations = tuple(ANY_CITATION_RE.finditer(sentence))
        if not sentence_citations:
            failures.append(
                UnsupportedClaim(
                    "citation_missing",
                    sentence.strip()[:200],
                    "critical_sentence_without_citation",
                )
            )
            continue

        # A citation marker proves nothing about a figure unless the cited
        # excerpt contains that same consequential value. Question numbers
        # remain available to the generic inventory so an answer can restate
        # user inputs, but they cannot be laundered into a factual rule by
        # attaching an unrelated source.
        claim_signatures = critical_citation_signatures(sentence)
        cited_excerpts: list[str] = []
        for citation in sentence_citations:
            for written_ref in split_citation_references(citation.group(1).strip()):
                reference = resolve_citation_reference(written_ref, allowed)
                if reference is not None and reference in excerpts:
                    cited_excerpts.append(excerpts[reference])
        if claim_signatures and cited_excerpts:
            evidence_signatures = frozenset(
                signature
                for excerpt in cited_excerpts
                for signature in critical_citation_signatures(excerpt)
            )
            missing_signatures = claim_signatures - evidence_signatures
            if missing_signatures:
                failures.append(
                    UnsupportedClaim(
                        "numeric_citation_relation",
                        sentence.strip()[:200],
                        "|".join(sorted(missing_signatures)),
                    )
                )

    rule_claims_checked = 0
    for start, end in sentence_spans(answer):
        sentence = answer[start:end].strip()
        for unit in _rule_claim_units(sentence, route):
            rule_claims_checked += 1
            if not unit.citation_refs:
                failures.append(
                    UnsupportedClaim(
                        "rule_citation_missing",
                        unit.text[:200],
                        "rule_claim_without_citation",
                    )
                )
                continue
            expanded = tuple(
                part
                for written_ref in unit.citation_refs
                for part in split_citation_references(written_ref)
            )
            relation_failures: list[tuple[str, str]] = []
            for written_ref in expanded:
                reference = resolve_citation_reference(written_ref, allowed)
                excerpt = excerpts.get(reference) if reference else None
                if reference is None or excerpt is None:
                    if reference is not None and excerpt is None:
                        relation_failures.append(
                            ("rule_citation_relation", f"{reference}:no_rule_excerpt")
                        )
                    continue
                failure = _rule_relation_failure(unit.text, excerpt)
                if failure is None:
                    relation_failures.clear()
                    break
                relation_failures.append((failure, reference))
            else:
                if relation_failures:
                    failure, reference = min(
                        relation_failures,
                        key=lambda item: _RULE_FAILURE_PRIORITY[item[0]],
                    )
                    failures.append(UnsupportedClaim(failure, unit.text[:200], reference))
    return len(citations), rule_claims_checked, tuple(failures)
