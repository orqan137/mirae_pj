from __future__ import annotations

import re
import unicodedata
from typing import TYPE_CHECKING

from app.answer_recovery import (
    attach_unambiguous_evidence_citations,
    drop_unsupported_lines,
    render_grounded_extractive_fallback,
    render_validation_failure,
)
from app.evidence import EvidenceAssessment
from app.product_facts import PRODUCT_CODE_RE
from app.product_claim_validation import (
    TypedProductInventory,
    parse_typed_product_inventory,
    typed_citation_excerpt_inventory,
    typed_citation_sources,
    validate_typed_product_relations,
)
from app.product_identity import ProductResolution
from app.product_values import normalize_date
from app.rule_claim_validation import validate_citation_claims
from app.routing import QuestionRoute
from app.semantic_coverage import assess_semantic_coverage
from app.validation_primitives import (
    CONTEXT_CITATION_RE as _CONTEXT_CITATION_RE,
    CURRENCY_RE as _CURRENCY_RE,
    DATE_RE as _DATE_RE,
    PERCENT_RE as _PERCENT_RE,
    citation_excerpt_inventory as _citation_excerpt_inventory,
    date_inventory as _date_inventory,
    normalize_number as _normalize_number,
    number_inventory as _number_inventory,
)
from app.validation_types import AnswerValidationResult, UnsupportedClaim

if TYPE_CHECKING:
    from app.suitability import SuitabilityAssessment


ANSWER_VALIDATOR_VERSION = "pension-answer-validator-v38"

_SENSITIVE_PATTERNS = (
    re.compile(r"authorization\s*:", flags=re.IGNORECASE),
    re.compile(r"bearer\s+[A-Za-z0-9._~+/-]{6,}", flags=re.IGNORECASE),
    re.compile(r"(?:hcx|clova)[_-]?api[_-]?key", flags=re.IGNORECASE),
    re.compile(r"(?:system\s*prompt|시스템\s*프롬프트)", flags=re.IGNORECASE),
)
_UNCONDITIONAL_RECOMMENDATIONS = (
    "추천합니다",
    "가장 적합합니다",
    "가장 좋습니다",
    "가입하세요",
    "매수하세요",
)
_UNSAFE_RECOMMENDATION_CLAIMS = (
    "최고의 상품",
    "가장 좋은 상품",
    "최적의 상품",
    "1위 상품",
    "무조건",
    "법정 적합성 판정을 완료",
    "고객 적합성을 확정",
)
_RETURN_BASED_RECOMMENDATION_CLAIMS = (
    "과거 수익률이 높",
    "수익률이 높",
    "성과가 우수",
)
_COST_BASED_RECOMMENDATION_CLAIMS = (
    "보수가 낮",
    "보수가 저렴",
    "수수료가 낮",
    "수수료가 저렴",
    "비용이 낮",
    "비용이 저렴",
)
# Claims this submission must never make, whatever the question was about.
# The recommendation checks below only run on a recommendation route, so a tax
# or system answer could carry a guarantee, assert that an account had been
# looked up, or crown a product market-best and still pass.
_PROHIBITED_ASSURANCE_RE = re.compile(
    r"(?:원금|수익률|손실|세금|절세|절감|적합성|환급)[^.\n]{0,20}?"
    r"보장(?!\s*법)"
    r"|보장(?!\s*법)[^.\n]{0,10}?(?:합니다|해\s*드립니다|드립니다|됩니다)"
)
_PERFORMED_ACTION_RE = re.compile(
    r"(?:계좌|잔고|잔액|납입\s*내역|거래\s*내역|보유\s*내역|수익률)"
    r"[^.\n]{0,20}?"
    r"(?:조회|확인|열람|조회해|확인해)\s*(?:했|하였|드렸|해\s*드렸|해드렸|완료)"
    r"|(?:이체|매수|매도|해지|가입|출금|입금)\s*(?:했습니다|하였습니다|완료했습니다|처리했습니다)"
)
_MARKET_SUPERLATIVE_RE = re.compile(
    r"(?:전\s*시장|시장\s*전체|모든\s*상품|업계)[^.\n]{0,12}?(?:1위|최고|최적|가장)"
    r"|(?:1위|최적)(?:의)?\s*상품"
    r"|가장\s*좋은\s*상품"
)


def _explicitly_denies_claim(
    answer: str, match: re.Match[str], kind: str
) -> bool:
    """Recognize a local disclaimer without exempting another positive claim."""

    if kind not in {"prohibited_assurance", "market_superlative"}:
        return False
    # Do not borrow a negation from another sentence or clause.
    suffix = re.split(
        r"[.!?\n,;\[]|지만|그러나|다만", answer[match.end() :], maxsplit=1
    )[0]
    suffix = re.sub(r"\s+", "", suffix)
    if kind == "prohibited_assurance":
        negative = re.match(
            r"(?:(?:되지|하지)(?:는|도)?않|(?:할|될)수(?:는)?없"
            r"|(?:되는|하는)?(?:상품|것|대상)(?:이|은|는)?아[니닌닐닙]"
            r"|(?:이|은|는)?(?:아[니닌닐닙]|없))",
            suffix,
        )
    else:
        negative = re.match(
            r"(?:(?:좋은|적합한)?상품)?(?:이|은|는|이라고|이라|라고)?"
            r"(?:아[니닌닐닙]|(?:단정|확정|보장)(?:할수(?:는)?없|하지(?:는)?않))",
            suffix,
        )
    if negative is None:
        return False
    # "보장하지 않는 것은 아닙니다" is not a denial of a guarantee.
    remainder = suffix[negative.end() :]
    return not (
        re.search(r"(?:것|건|뜻|말|사실).{0,12}(?:아[니닌닐닙]|없)", remainder)
        or re.match(r"(?:을)?수(?:는|가)?없|(?:지|지는)않", remainder)
    )


def _citation_inventory(
    context: str,
    inventory: TypedProductInventory,
) -> frozenset[str]:
    explicit = {
        match.group(1).strip()
        for match in _CONTEXT_CITATION_RE.finditer(context)
        if match.group(1).strip()
    }
    return frozenset(explicit | set(typed_citation_sources(inventory)))


def validate_answer(
    *,
    answer: str,
    question: str,
    evidence_context: str,
    route: QuestionRoute,
    assessment: EvidenceAssessment,
    product_resolution: ProductResolution | None = None,
    suitability: SuitabilityAssessment | None = None,
) -> AnswerValidationResult:
    """Reject unsupported consequential tokens without another model call.

    This validator is intentionally conservative. It checks exact quantitative/
    date/product identity and typed value-relation claims, then screens cited
    non-quantitative pension rules for topic, polarity, and scope consistency.
    It does not claim to provide unrestricted semantic entailment.
    """

    normalized_answer = unicodedata.normalize("NFKC", answer)
    support_text = f"{question}\n{evidence_context}"
    typed_inventory = parse_typed_product_inventory(evidence_context)
    allowed_citations = _citation_inventory(evidence_context, typed_inventory)
    citation_excerpts = _citation_excerpt_inventory(evidence_context)
    for reference, excerpt in typed_citation_excerpt_inventory(typed_inventory).items():
        existing = citation_excerpts.get(reference)
        citation_excerpts[reference] = f"{existing}\n{excerpt}" if existing else excerpt
    supported_numbers = _number_inventory(support_text)
    supported_dates = _date_inventory(support_text)
    supported_codes = frozenset(
        match.group(0).upper() for match in PRODUCT_CODE_RE.finditer(support_text)
    )
    unsupported: list[UnsupportedClaim] = []
    checked = 0

    for match in _PERCENT_RE.finditer(normalized_answer):
        checked += 1
        normalized = _normalize_number(match.group(1))
        if normalized is not None and normalized not in supported_numbers:
            unsupported.append(
                UnsupportedClaim("percentage", match.group(0), normalized)
            )

    for match in _CURRENCY_RE.finditer(normalized_answer):
        checked += 1
        normalized_number = _normalize_number(match.group(1))
        normalized = f"{normalized_number}:{match.group(2)}"
        if normalized_number is not None and normalized_number not in supported_numbers:
            unsupported.append(UnsupportedClaim("currency", match.group(0), normalized))

    for match in _DATE_RE.finditer(normalized_answer):
        normalized = normalize_date(match.group(0))
        if normalized is None:
            continue
        checked += 1
        if normalized not in supported_dates:
            unsupported.append(UnsupportedClaim("date", match.group(0), normalized))

    for match in PRODUCT_CODE_RE.finditer(normalized_answer):
        checked += 1
        normalized = match.group(0).upper()
        if normalized not in supported_codes:
            unsupported.append(UnsupportedClaim("product_code", match.group(0), normalized))

    relation_checked, relation_unsupported = validate_typed_product_relations(
        answer=normalized_answer,
        question=question,
        inventory=typed_inventory,
        resolution=product_resolution,
    )
    checked += relation_checked
    unsupported.extend(relation_unsupported)

    # Citation completeness is evaluated only after the underlying claim has
    # passed value/identity/qualifier checks. This keeps one primary failure
    # class per bad claim and avoids masking the more consequential defect.
    if unsupported:
        citations_checked = 0
        rule_claims_checked = 0
        citation_unsupported: tuple[UnsupportedClaim, ...] = ()
    else:
        citations_checked, rule_claims_checked, citation_unsupported = validate_citation_claims(
            answer=normalized_answer,
            allowed=allowed_citations,
            excerpts=citation_excerpts,
            route=route,
        )
        unsupported.extend(citation_unsupported)

    sensitive = tuple(
        dict.fromkeys(
            pattern.pattern
            for pattern in _SENSITIVE_PATTERNS
            if pattern.search(normalized_answer)
        )
    )
    has_recommendation_claim = any(
        term in normalized_answer for term in _UNCONDITIONAL_RECOMMENDATIONS
    )
    recommendation_gate_missing = (
        assessment.status != "answerable"
        or (
            "conditional_recommendation" in route.labels
            and (
                suitability is None
                or suitability.status != "candidate_shortlist"
            )
        )
    )
    recommendation_blocked = (
        has_recommendation_claim and recommendation_gate_missing
    ) or (
        "conditional_recommendation" in route.labels
        and any(
            not _explicitly_denies_claim(normalized_answer, match, "market_superlative")
            for term in _UNSAFE_RECOMMENDATION_CLAIMS
            for match in re.finditer(re.escape(term), normalized_answer)
        )
    ) or (
        "conditional_recommendation" in route.labels
        and any(
            term in normalized_answer
            for term in _RETURN_BASED_RECOMMENDATION_CLAIMS
        )
    ) or (
        "conditional_recommendation" in route.labels
        and "[TYPED PRODUCT COMPARISON]" not in evidence_context
        and any(term in normalized_answer for term in _COST_BASED_RECOMMENDATION_CLAIMS)
    )
    for pattern, kind, label in (
        (_PROHIBITED_ASSURANCE_RE, "prohibited_assurance", "보장 표현"),
        (_PERFORMED_ACTION_RE, "unperformed_action_claim", "수행 주장"),
        (_MARKET_SUPERLATIVE_RE, "market_superlative", "전 시장 최상급"),
    ):
        match = next(
            (
                candidate
                for candidate in pattern.finditer(normalized_answer)
                if not _explicitly_denies_claim(normalized_answer, candidate, kind)
            ),
            None,
        )
        if match is not None:
            unsupported.append(
                UnsupportedClaim(
                    kind=kind,
                    text=match.group(0)[:200],
                    normalized=label,
                )
            )

    semantic_coverage = assess_semantic_coverage(question, normalized_answer)
    question_coverage_failures = semantic_coverage.missing
    valid = (
        not unsupported
        and not sensitive
        and not recommendation_blocked
        and not question_coverage_failures
    )
    return AnswerValidationResult(
        version=ANSWER_VALIDATOR_VERSION,
        valid=valid,
        checked_claims=checked,
        product_value_relations_checked=relation_checked,
        citations_checked=citations_checked,
        rule_claims_checked=rule_claims_checked,
        citation_failures=len(citation_unsupported),
        unsupported_claims=tuple(unsupported),
        sensitive_spans=sensitive,
        recommendation_blocked=recommendation_blocked,
        question_coverage_failures=question_coverage_failures,
        question_coverage_required=len(semantic_coverage.required),
        question_coverage_covered=len(semantic_coverage.covered),
    )
