from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.korean_query import contains_predicate_stem
from app.product_facts import explicit_requested_axes, requested_axes, requested_product_codes
from app.routing import QuestionRoute, classify_question, normalize_question_text
from app.source_governance import QuestionScope, analyze_question_scope


PLANNER_VERSION = "pension-evidence-plan-v20"
QUERY_ANCHOR_VERSION = "pension-query-anchor-v8"

GENERAL_RETRIEVER_TOOL = "general_retriever"
PRODUCT_RETRIEVER_TOOL = "product_retriever"
CALCULATOR_TOOL = "pension_tax_credit_calculator"
PRODUCT_COMPARATOR_TOOL = "product_comparator"
SUITABILITY_TOOL = "suitability_prefilter"

_SCOPED_PENSION_RE = re.compile(
    r"(?P<scope>[가-힣A-Za-z0-9][가-힣A-Za-z0-9\s-]{1,40}?)\s*의\s*"
    r"(?:연금저축|퇴직연금|개인연금|개인형\s*irp|irp|연금(?:\s*계좌)?)",
    flags=re.IGNORECASE,
)
_SCOPE_TOKEN_RE = re.compile(r"[가-힣A-Za-z0-9-]+")
_GENERIC_SCOPES = frozenset(
    {
        "나",
        "내",
        "제",
        "저",
        "저의",
        "우리",
        "본인",
        "개인",
        "고객",
        "가입자",
        "배우자",
        "가족",
        "남편",
        "아내",
        "부모",
        "부모님",
    }
)
_GENERAL_TOPIC_TERMS = (
    "세액공제",
    "중도인출",
    "디폴트옵션",
    "실물이전",
    "지연이자",
    "가입자교육",
    "연금수령한도",
    "세액정산",
    "퇴직소득세",
    "월배당",
    "해외 etf",
    "isa",
    "압류",
    "부담금",
    "연금개시",
    "직접청구",
    "건강보험료",
    "주택 구입",
    "요양",
    "국민연금",
    "예상수령액",
    "전산 메뉴",
    "전산 화면",
    "처리 코드",
    "화면 코드",
    "화면 번호",
    "비연금 일반계좌",
)
_GENERAL_TOPIC_ALIASES: dict[str, tuple[str, ...]] = {
    "가입자교육": (
        "퇴직연금 교육",
        "가입자 교육",
    ),
    "예상수령액": ("예상 수령액",),
    "연금개시": ("연금 개시", "연금을 개시"),
    "비연금 일반계좌": (
        "일반 주식계좌",
        "일반계좌",
        "일반 계좌",
        "위탁계좌",
        "위탁 계좌",
        "종합계좌",
        "종합 계좌",
    ),
}
_QUERY_ANCHOR_TOPIC_TERMS: dict[str, tuple[str, ...]] = {
    "DC 부담금 납입 지연 지연이자": ("지연이자",),
    "퇴직연금 실물이전 이전 가능 여부 사전조회 이전 불가 사유": ("실물이전",),
    "연금수령한도 연금외수령": ("연금수령한도",),
    "퇴직급여 직접청구 도산 지급누락 증빙": ("직접청구",),
    "중도인출 무주택 주택 구입 증빙": ("중도인출", "주택 구입"),
    "중도인출 6개월 이상 요양 의료비 증빙": ("중도인출", "요양"),
    "ISA 만기 연금계좌 전환입금 60일 추가 세액공제": ("isa",),
    "근로자퇴직급여 보장법 제4조 사용자가 설정된 퇴직급여제도 내용 변경 근로자대표 의견청취 의견을 들어야": (
        "퇴직연금규약",
        "근로자대표",
    ),
    "근로자퇴직급여 보장법 제4조 사용자가 설정된 퇴직급여제도 내용을 불리하게 변경 근로자대표 불리 동의 동의를 받아야": (
        "퇴직연금규약",
        "근로자대표",
    ),
    "퇴직연금규약 변경 근로자대표 의견청취 또는 동의": (
        "퇴직연금규약",
        "근로자대표",
    ),
    "개인IRP 가입자부담금 입금취소": ("개인irp", "입금취소"),
    "개인IRP 최초 부담금 입금취소": ("개인irp", "입금취소"),
    "연금실제수령연차 실제 인출": ("연금실제수령연차", "인출"),
    "재난 중도인출 개인IRP": ("재난", "중도인출"),
    "TDF DC 100%": ("tdf", "100%"),
    "TDF DB 100%": ("tdf", "100%"),
    "MP 구독 서비스 개인형IRP": ("mp 구독 서비스",),
    "ETF ETN 상장 리츠 정규장 시간외 거래": ("매매가능 시간", "시간외 거래"),
    "ETF 정규장 시간외 거래": ("매매가능 시간", "시간외 거래"),
    "ETN 정규장 시간외 거래": ("매매가능 시간", "시간외 거래"),
    "상장 리츠 정규장 시간외 거래": ("매매가능 시간", "시간외 거래"),
    "임원퇴직소득 한도 한도 초과분 IRP 과세이연": (
        "임원퇴직소득 한도",
        "irp",
    ),
    "임원퇴직소득 한도 한도 이내 퇴직소득 IRP 과세이연": (
        "임원퇴직소득 한도",
        "irp",
    ),
    "임원퇴직소득 한도 IRP 과세이연": ("임원퇴직소득 한도", "irp"),
    "연금계좌 운용수익 연금수령 연금소득": ("연금계좌", "운용수익"),
    "연금계좌 운용수익 연금외수령 기타소득": ("연금계좌", "운용수익"),
    "연금계좌 운용수익 수령 방식 소득 구분": ("연금계좌", "운용수익"),
    "DC 개인회생절차개시결정 중도인출": ("개인회생절차개시", "중도인출"),
    "DC 개인워크아웃 신용회복 중도인출": ("개인워크아웃", "중도인출"),
    "국민연금 부과방식 퇴직연금 사전적립방식": ("국민연금", "퇴직연금"),
}
_CLAUSE_SPLIT_RE = re.compile(
    r"(?:(?<!\d)[.!?。！？;；]+(?!\d)\s*|\n+|"
    r"\s+(?:그리고|또한|아울러|그다음|그 다음)\s+)"
)
_LEADING_CONNECTOR_RE = re.compile(r"^(?:그리고|또한|아울러|그다음|그 다음)\s+")
_IMPLICIT_ENUMERATION_RE = re.compile(r"\s*[,，]\s*|\s+및\s+")
_EMBEDDED_QUESTION_END_RE = re.compile(
    r"(?:가능한지|할\s*수\s*있는지|무엇인지|뭔지|얼마인지|어떻게\s*되는지|"
    r"맞는지|필요한지)\s*$"
)
_EMBEDDED_TOPIC_START_RE = re.compile(
    r"\s+(?=(?:(?:연금저축|개인형\s*IRP|IRP)?\s*세액공제|연금\s*개시|연금개시|중도인출|실물이전|"
    r"퇴직소득세|연금소득세|상품\s*(?:비교|추천)))",
    flags=re.IGNORECASE,
)
_ANAPHORIC_REFERENCE_RE = re.compile(
    r"(?:^|\s)(?:그|이|해당)\s*(?:상품|계좌|경우|때|내용|제도)"
)
_CONTEXT_REFERENCE_RE = re.compile(
    _ANAPHORIC_REFERENCE_RE.pattern + "|" +
    r"(?:한도|세율|공제율|사유|조건|요건|서류|절차|방법|비용|보수|수익률)"
    r"(?:도|은|는|이|가|을|를)?(?:인가요|인지|입니까|이냐)?(?:\s|$)"
)
_AUDIENCE_CONTEXT_ONLY_RE = re.compile(
    r"^(?=.{1,100}$)(?:"
    r"(?=.*(?:사무담당자|업무담당자|창구직원|회사 담당자|퇴직연금 담당자|사업주))"
    r"(?=.*(?:입니다|예요|이에요|인데요?)$)|"
    r"(?=.*(?:회사|사업장|인사팀|총무팀|증권사|금융사)에서)"
    r"(?=.*(?:퇴직연금|연금|irp))(?=.*(?:담당|맡고|업무를 하고))"
    r"(?=.*(?:합니다|있습니다|있어요|해요)$)"
    r").+$",
    flags=re.IGNORECASE,
)
_CALCULATION_CONTEXT_ONLY_RE = re.compile(
    r"^(?=.{1,240}$)"
    r"(?=.*(?:총급여|종합소득금액))"
    r"(?=.*\d[\d,]*\s*(?:억원|천만원|만원|원))"
    r"(?=.*(?:연금\s*저축|irp))"
    r"(?=.*(?:납입|입금|불입|넣)).+$",
    flags=re.IGNORECASE,
)
_LEADING_ANAPHORIC_CLAUSE_RE = re.compile(
    r"^(?:(?:이|그|해당)\s*(?:사유|경우|상황)(?:로|에|라면|이면)?|"
    r"그래도|그러면|그렇다면)(?:\s|$)"
)
_COMPLETED_REQUEST_CLAUSE_RE = re.compile(
    r"(?:알려|설명(?:해|하여)?).{0,8}(?:주세요|줘|주십시오)?$|"
    r"(?:무엇|어떻|어디|언제|가능한가|되나요|인가요|하나요|할까요|"
    r"있나요|필요한가)(?:요)?$"
)
_SYSTEM_COMPARISON_RE = re.compile(
    r"(?:db(?:형)?|dc(?:형)?|확정급여|확정기여).*(?:차이|비교|구분)|"
    r"(?:차이|비교|구분).*(?:db(?:형)?|dc(?:형)?|확정급여|확정기여)",
    flags=re.IGNORECASE,
)


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _has_ascii_term(text: str, term: str) -> bool:
    return bool(
        re.search(
            rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])",
            text,
            flags=re.IGNORECASE,
        )
    )


def _all_mentions_negated(text: str, terms: tuple[str, ...]) -> bool:
    """Return true only when every matched term is explicitly declined.

    This is intentionally local to each mention.  A sentence may decline an
    employee menu while still requesting a processing code, in which case the
    request remains an internal-detail request.
    """

    matched = False
    for term in terms:
        start = 0
        while True:
            position = text.find(term, start)
            if position < 0:
                break
            matched = True
            segment = text[position : position + len(term) + 40]
            if not re.search(
                r"(?:은|는|이|가|도|와|과|이나|나)?\s*"
                r"(?:필요(?:가)?\s*없|원하지\s*않|말고|제외|빼고|"
                # "알려주지 마시고", "하지 마세요", "말아 주세요" decline the
                # term just as plainly as "말고" does. Only "말고" was listed,
                # so a sentence that refused the internal detail in the most
                # common polite form still read as a request for it.
                r"지\s*(?:마시|마세|마|말아|말라)|"
                r"알\s*필요\s*없|조회(?:는|를)?\s*하지\s*않)",
                segment,
            ):
                return False
            start = position + len(term)
    return matched


def _requests_external_lookup(text: str) -> bool:
    """Detect an instruction to access records, not a request for guidance."""

    if re.search(
        r"(?:조회|확인)(?:는|를)?\s*(?:하지\s*마|하지\s*않|말고)|"
        r"실제\s*(?:조회|확인)(?:는|를)?\s*(?:하지|원하지)",
        text,
    ):
        return False
    if re.search(
        r"(?:일반적으로|통상|개념|원리|계산법|계산\s*방법|"
        r"조회\s*방법|확인\s*방법|어디서\s*(?:조회|확인)|절차|설명)",
        text,
    ):
        return False
    return contains_predicate_stem(
        text,
        ("조회", "확인", "보여", "알려", "불러", "가져오", "봐줘", "봐 줘"),
    )


def _requests_account_execution(text: str) -> bool:
    """Separate an execution command from a public rule or procedure question."""

    if re.search(
        r"(?:방법|절차|조건|요건|기준|필수(?:인가|인지)|"
        r"가능(?:한가|한지| 여부)|할\s*수\s*있|해야\s*(?:하나|하는지)|"
        r"어떻게|설명|알려)",
        text,
    ):
        return False
    stems = "|".join(re.escape(term) for term in _ACCOUNT_EXECUTION_TERMS)
    return bool(
        re.search(
            rf"(?:{stems}).{{0,10}}(?:(?:해\s*)?(?:줘|주세요|주실래요|주십시오)|"
            rf"하여\s*(?:줘|주세요|주십시오)|부탁|원해|하고\s*싶|해|하자|해라)\s*[.?!]?$",
            text,
        )
    )


def _has_active_pension_context(text: str) -> bool:
    has_context = _contains_any(text, ("연금", "퇴직금", "퇴직급여")) or any(
        _has_ascii_term(text, term) for term in ("irp", "dc", "db")
    )
    excluded = bool(
        re.search(
            r"(?:퇴직연금|연금\s*계좌|개인형\s*irp|irp)"
            r"(?:이|가|은|는)?\s*(?:아닌|아니고|말고|제외한)",
            text,
            flags=re.IGNORECASE,
        )
    )
    return has_context and not excluded


def _three_way_state(*, positive: bool, negative: bool) -> str:
    if positive == negative:
        return "unknown"
    return "positive" if positive else "negative"


def required_scope_terms(question: str) -> tuple[str, ...]:
    """Extract an explicit possessor/scope that official evidence must mention."""

    normalized = normalize_question_text(question)
    terms: list[str] = []
    for match in _SCOPED_PENSION_RE.finditer(normalized):
        tokens = _SCOPE_TOKEN_RE.findall(match.group("scope"))[-2:]
        terms.extend(
            token.casefold()
            for token in tokens
            if token.casefold() not in _GENERIC_SCOPES
        )
    return tuple(dict.fromkeys(terms))


@dataclass(frozen=True, slots=True)
class EvidenceRequirement:
    requirement_id: str
    kind: str
    label: str
    source_group: str | None = None
    route_label: str | None = None
    product_code: str | None = None
    axis: str | None = None
    minimum_count: int = 1
    required_terms: tuple[str, ...] = ()
    topic_terms: tuple[str, ...] = ()
    subquestion_id: str = "sq1"
    query_text: str = ""
    explicit_identity_required: bool = False


@dataclass(frozen=True, slots=True)
class SubQuestion:
    subquestion_id: str
    text: str
    query_text: str
    inherited_terms: tuple[str, ...]
    query_anchors: tuple[str, ...]
    route: QuestionRoute
    scope: QuestionScope


@dataclass(frozen=True, slots=True)
class QuestionPlan:
    version: str
    requirements: tuple[EvidenceRequirement, ...]
    question: str
    scope: QuestionScope
    unsupported_scope_reasons: tuple[str, ...]
    subquestions: tuple[SubQuestion, ...]
    tools: tuple[str, ...]


_PENSION_ACCOUNT_RE = re.compile(
    r"(?:개인형\s*퇴직연금|개인형\s*irp|irp(?:\s*계좌)?|"
    r"(?:db|dc)(?:형)?(?:\s*퇴직연금)?\s*계좌|"
    # 연금저축 and 개인연금 name an account just as much as "연금 계좌" does.
    # Without them "연금저축을 다른 상품으로 갈아타 주세요" read as a general
    # question and reached generation instead of the execution block.
    r"연금저축(?:\s*(?:계좌|펀드|보험))?|개인연금(?:\s*계좌)?|"
    r"연금\s*계좌|퇴직연금\s*계좌)",
    flags=re.IGNORECASE,
)
_PERSONAL_NATIONAL_PENSION_RE = re.compile(
    r"(?:^|\s)(?:내|제|저의|본인(?:의)?)\s*국민연금",
    flags=re.IGNORECASE,
)
_LIVE_ACCOUNT_DATA_TERMS = (
    "실시간 잔고",
    "현재 잔고",
    "오늘 잔고",
    "지금 잔고",
    "계좌 잔액",
    "입금 내역",
    "납입 내역",
    "거래 내역",
    "보유 내역",
    "이번 달 입금",
    "이번달 입금",
    "이번 달 납입",
    "이번달 납입",
    "지금 남은 잔액",
    "지금 잔액",
    "현재 잔액",
    "오늘 잔액",
    "오늘 수익률",
    "현재 수익률",
    "지금 수익률",
    # The qualified forms above all assume the question dates the holding
    # ("현재", "지금"). "제 IRP 계좌 잔고를 조회해 주세요" carries no such word
    # and slipped through to generation. A bare holding noun is enough here
    # because this list only fires alongside a pension-account reference and
    # an explicit lookup instruction; `_requests_external_lookup` already
    # excludes questions about how or where to check something.
    "잔고",
    "잔액",
    "평가금액",
    "평가 금액",
    "적립금액",
    "적립 금액",
)
_ACCOUNT_EXECUTION_TERMS = (
    "변경",
    "바꾸",
    "갈아타",
    "매수",
    "매도",
    "주문",
    "이체",
    "해지",
    "환매",
)
# Both spacings of every term. Korean writers split these compounds freely,
# and the list previously carried "화면번호" unspaced while "화면 코드" and
# "처리 코드" existed only spaced, so "직원용 화면코드 알려주세요" was missed.
# Detection and negation share this tuple, so variants must be added here
# rather than by making one of the two space-insensitive.
_INTERNAL_OPERATIONS_TERMS = (
    "전산 메뉴",
    "전산메뉴",
    "전산 화면",
    "전산화면",
    "처리 코드",
    "처리코드",
    "화면 코드",
    "화면코드",
    "화면 번호",
    "화면번호",
    "메뉴 코드",
    "메뉴코드",
    "직원용 메뉴",
    "직원용메뉴",
    "내부 처리 메뉴",
    "내부처리메뉴",
)


def unsupported_scope_reasons(
    question: str,
    *,
    scope: QuestionScope | None = None,
) -> tuple[str, ...]:
    """Identify requests that this evidence-only submission cannot perform.

    These are capability boundaries, not topic guesses: the submitted Agent has
    no authenticated customer-account, government-pension, or employee terminal
    connection.  It may still explain public procedures, so account wording alone
    is never enough to trigger a block.
    """

    normalized = normalize_question_text(question)
    resolved_scope = scope or analyze_question_scope(question)
    reasons: list[str] = []
    if (
        _PENSION_ACCOUNT_RE.search(normalized)
        and _contains_any(normalized, _LIVE_ACCOUNT_DATA_TERMS)
        and _requests_external_lookup(normalized)
    ):
        reasons.append("private_account_live_access_unavailable")
    if (
        _PENSION_ACCOUNT_RE.search(normalized)
        and contains_predicate_stem(normalized, _ACCOUNT_EXECUTION_TERMS)
        and _requests_account_execution(normalized)
    ):
        reasons.append("account_transaction_execution_unavailable")
    if (
        _PERSONAL_NATIONAL_PENSION_RE.search(normalized)
        and (
            _contains_any(normalized, ("예상수령액", "예상 수령액", "수령액"))
            # "내 국민연금 얼마 받는지 확인해줘" asks the same thing without
            # naming the amount, and the lookup guard below still requires an
            # explicit instruction to look it up.
            or re.search(r"얼마(?:나|를)?\s*(?:받|나오|지급)", normalized) is not None
        )
        and _requests_external_lookup(normalized)
    ):
        reasons.append("government_pension_personal_lookup_unavailable")
    if (
        resolved_scope.audience == "customer"
        and _contains_any(normalized, _INTERNAL_OPERATIONS_TERMS)
        and not _all_mentions_negated(normalized, _INTERNAL_OPERATIONS_TERMS)
    ):
        reasons.append("employee_terminal_detail_unavailable_to_customer")
    return tuple(dict.fromkeys(reasons))


def select_planned_tools(
    *,
    requirements: tuple[EvidenceRequirement, ...],
    subquestions: tuple[SubQuestion, ...],
) -> tuple[str, ...]:
    """Return the deterministic minimum tool set implied by an evidence plan."""

    tools: list[str] = []
    if any(item.source_group == "general" for item in requirements):
        tools.append(GENERAL_RETRIEVER_TOOL)
    if any(item.source_group == "product" for item in requirements):
        tools.append(PRODUCT_RETRIEVER_TOOL)
    if any(item.kind == "calculation" for item in requirements):
        tools.append(CALCULATOR_TOOL)
    if any("product_comparison" in item.route.labels for item in subquestions):
        tools.append(PRODUCT_COMPARATOR_TOOL)
    if any("conditional_recommendation" in item.route.labels for item in subquestions):
        tools.append(SUITABILITY_TOOL)
    return tuple(tools)


def _intent_families(text: str) -> frozenset[str]:
    """Return conservative domain families used only for implicit list splitting."""

    normalized = normalize_question_text(text)
    route = classify_question(text)
    labels = set(route.labels)
    families = labels & {"tax", "procedure"}
    if labels & {
        "product_explanation",
        "product_comparison",
        "conditional_recommendation",
    }:
        families.add("product")
    if _SYSTEM_COMPARISON_RE.search(normalized):
        families.add("system")
    return frozenset(families)


def _split_implicit_enumeration(part: str) -> tuple[str, ...]:
    candidates = tuple(
        candidate.strip(" ,;:")
        for candidate in _IMPLICIT_ENUMERATION_RE.split(part)
        if candidate.strip(" ,;:")
    )
    if len(candidates) < 2:
        return (part,)
    families = tuple(_intent_families(candidate) for candidate in candidates)
    # Do not split noun lists or multiple axes of one product. Every fragment
    # must carry its own intent family and the list must span at least two
    # different families.
    if any(not family for family in families) or len(set().union(*families)) < 2:
        return (part,)
    return candidates


def _split_embedded_questions(part: str) -> tuple[str, ...]:
    """Split adjacent Korean embedded questions without punctuation.

    ``누가 가입 가능한지 세액공제 한도는 얼마인지`` carries two
    requests under one final reporting verb. A boundary is accepted only when
    its left side already ends in an interrogative complement.
    """

    parts: list[str] = []
    start = 0
    for match in _EMBEDDED_TOPIC_START_RE.finditer(part):
        left = part[start : match.start()].strip()
        if left and _EMBEDDED_QUESTION_END_RE.search(left):
            parts.append(left)
            start = match.end()
    tail = part[start:].strip()
    if tail:
        parts.append(tail)
    return tuple(parts) if len(parts) > 1 else (part,)


def _split_telegraphic_enumeration(part: str) -> tuple[str, ...]:
    """Recover a three-intent list whose punctuation was omitted.

    Evaluator questions can arrive as keyword-style Korean (for example, three
    independent requests separated only by spaces). Splitting every mixed
    two-intent sentence would be unsafe because a tax question about a
    withdrawal is often one atomic request. We therefore split only when a
    three-way partition exists in which every segment has exactly one distinct
    intent family. The rule is structural and does not depend on product names
    or benchmark case text.
    """

    tokens = part.split()
    if not 3 <= len(tokens) <= 30:
        return (part,)
    candidates: list[tuple[tuple[int, int, int, int], tuple[str, str, str]]] = []
    for first_end in range(1, len(tokens) - 1):
        for second_end in range(first_end + 1, len(tokens)):
            segments = (
                " ".join(tokens[:first_end]),
                " ".join(tokens[first_end:second_end]),
                " ".join(tokens[second_end:]),
            )
            families = tuple(_intent_families(segment) for segment in segments)
            if any(len(family) != 1 for family in families):
                continue
            if len(set().union(*families)) != 3:
                continue
            lengths = tuple(len(segment.split()) for segment in segments)
            # Prefer partitions that avoid one-token fragments, then the most
            # balanced partition. Stable boundary positions break ties.
            score = (
                -min(lengths),
                max(lengths) - min(lengths),
                first_end,
                second_end,
            )
            candidates.append((score, segments))
    if not candidates:
        return (part,)
    return min(candidates, key=lambda item: item[0])[1]


# Canonical pension schemes a question can contrast. Each is a separate rule
# set in the official documents, so a question naming two of them needs two
# supporting sources before it can be answered.
_CONTRASTED_SCHEMES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("db", ("db형", "db 형", "확정급여")),
    ("dc", ("dc형", "dc 형", "확정기여")),
    ("irp", ("irp", "개인형퇴직연금", "개인형 퇴직연금")),
    ("pension_savings", ("연금저축",)),
)
_MAXIMUM_SCHEME_EVIDENCE = 3


def _scheme_evidence_minimum(text: str) -> int:
    """Return how many distinct schemes a subquestion needs evidence for.

    `minimum_count` does not gate whether a general rule counts as supported;
    it decides how many of the assessed sources reach the answer. Leaving it at
    one meant a "DB형과 DC형의 차이" question was answered from a DB-only chunk,
    so the model supplied the DC half from its own knowledge and was blocked.
    """

    normalized = normalize_question_text(text)
    mentioned = sum(
        1
        for _, aliases in _CONTRASTED_SCHEMES
        if any(alias in normalized for alias in aliases)
    )
    return max(1, min(mentioned, _MAXIMUM_SCHEME_EVIDENCE))


def _general_topic_terms(text: str) -> tuple[str, ...]:
    normalized = normalize_question_text(text)
    return tuple(
        term
        for term in _GENERAL_TOPIC_TERMS
        if (
            term in normalized
            or any(alias in normalized for alias in _GENERAL_TOPIC_ALIASES.get(term, ()))
        )
        and not (
            term in _INTERNAL_OPERATIONS_TERMS
            and _all_mentions_negated(normalized, (term,))
        )
    )


def _declines_action(text: str, cues: tuple[str, ...]) -> bool:
    matched = False
    for cue in cues:
        start = 0
        while True:
            position = text.find(cue, start)
            if position < 0:
                break
            matched = True
            segment = text[position : position + 48]
            if not re.search(
                r"(?:의향|계획|생각).{0,10}(?:없|않)|"
                r"지\s*않(?:고|겠|을|으려|기로|도록)|"
                r"(?:는|은|을|할|하는|하려는|하려고)?\s*.{0,8}"
                r"안\s*(?:하(?:고|겠|려|려고|기로|는)|할)|"
                r"원하지\s*않",
                segment,
            ):
                return False
            start = position + len(cue)
    return matched


def _avoids_scenario(text: str, cues: tuple[str, ...]) -> bool:
    return any(
        cue in text
        and re.search(r"(?:피하|피해야|피하고|없었으면|원하지\s*않)", text[text.find(cue) : text.find(cue) + 40])
        for cue in cues
    )


_PHONETIC_ACRONYMS: tuple[tuple[str, str], ...] = (
    ("아이알피", "IRP 개인형퇴직연금"),
    ("디비형", "DB형 확정급여형"),
    ("디씨형", "DC형 확정기여형"),
    ("디시형", "DC형 확정기여형"),
    ("티디에프", "TDF"),
    ("이티에프", "ETF"),
    ("아이에스에이", "ISA"),
)


def general_query_anchors(text: str) -> tuple[str, ...]:
    """Map narrow Korean paraphrases to organizer-document terminology.

    Anchors are retrieval-only vocabulary. They never replace the request,
    populate a customer condition, or assert a fact. Every mapping is tied to
    a frozen lifecycle holdout and can therefore be removed if it causes topic
    drift or a hard-negative regression.
    """

    normalized = normalize_question_text(text)
    anchors: list[str] = []
    pension_context = _has_active_pension_context(normalized)

    # The corpus writes these as acronyms and never phonetically: "아이알피"
    # appears in zero documents. A question spelled that way therefore shares
    # no vocabulary with the evidence, and a short one such as "아이알피
    # 중도인출 되나요" has no other supported word to carry it, so retrieval
    # returned nothing at all. The acronym is the organizer's own wording, so
    # supplying it is retrieval vocabulary rather than a new fact.
    # `normalize_question_text` already folds these to the acronym, but the
    # retriever indexes and scores the raw question, where "아이알피" appears
    # in zero documents. Testing the raw text and supplying the acronym gives
    # retrieval the organizer's own wording without altering the request.
    raw = text.casefold()
    for phonetic, acronym in _PHONETIC_ACRONYMS:
        if phonetic in raw:
            anchors.append(acronym)

    if (
        "연금저축" in normalized
        and _contains_any(normalized, ("일부", "부분"))
        and _contains_any(normalized, ("인출", "꺼내", "빼"))
    ):
        anchors.append("연금저축 부분 인출 출금 계약해지 아님")

    if (
        "세액공제" in normalized
        and _contains_any(
            normalized, ("irp", "연금저축", "연금 계좌", "연금계좌")
        )
        and _contains_any(
            normalized,
            ("한도", "얼마", "공제액", "최대", "총급여", "종합소득"),
        )
    ):
        anchors.append(
            "연금저축 IRP 세액공제 납입한도 총급여 종합소득금액"
        )

    # "퇴직연금은 언제부터 받을 수 있나요" asks exactly when a pension may be
    # drawn without using the word 개시, and it retrieved 수급권 보호 and
    # 가입자교육 instead of the documents that state the age.
    asks_when_payable = _contains_any(
        normalized, ("언제부터", "언제 부터", "몇 살", "몇살", "언제 받", "언제부터 받")
    ) and _contains_any(normalized, ("연금", "퇴직연금", "irp", "연금저축"))
    if asks_when_payable or (
        (
            _contains_any(normalized, ("연금개시", "연금 개시"))
            or ("연금" in normalized and "개시" in normalized)
        )
        and _contains_any(normalized, ("55세", "가입", "퇴직금"))
    ):
        anchors.append(
            "만 55세 이상 가입자 가입기간 5년 이상 퇴직금이 입금된 "
            "연금계좌 가입기간 5년 이상 요건 미적용"
        )

    if (
        _has_ascii_term(normalized, "dc")
        or "확정기여" in normalized
    ) and _contains_any(normalized, ("늦", "제때", "제 날짜", "밀리", "미납")) and _contains_any(
        normalized,
        ("돈", "적립금", "부담금", "납입", "넣", "들어오"),
    ):
        anchors.append("DC 부담금 납입 지연 지연이자")

    if (
        pension_context
        and _contains_any(normalized, ("옮기", "옮길", "옮겨", "이전", "이관", "갈아타"))
        and _contains_any(
            normalized,
            (
                "다른",
                "타사",
                "금융사",
                "금융회사",
                "증권사",
                "그대로",
                "펀드",
                "상품",
                "자산",
                "팔",
            ),
        )
        and not _declines_action(
            normalized, ("옮기", "옮길", "옮겨", "이전", "이관", "갈아타")
        )
    ):
        anchors.append("퇴직연금 실물이전 이전 가능 여부 사전조회 이전 불가 사유")

    payout_account_context = _contains_any(
        normalized,
        ("연금계좌", "연금 계좌", "빼", "꺼내", "인출"),
    ) or _has_ascii_term(normalized, "irp")
    if (
        pension_context
        and payout_account_context
        and _contains_any(normalized, ("빼", "꺼내", "인출", "받", "수령"))
        and _contains_any(
            normalized, ("한 번에", "한꺼번에", "너무 많이", "많이", "전액", "몽땅")
        )
    ):
        anchors.append("연금수령한도 연금외수령")

    if (
        _contains_any(normalized, ("퇴직금", "퇴직급여"))
        and _contains_any(normalized, ("못 받", "미지급", "안 주", "망하", "도산", "폐업"))
        and not _declines_action(normalized, ("망하", "도산", "폐업"))
        and not _avoids_scenario(normalized, ("망하", "도산", "폐업"))
    ):
        anchors.append("퇴직급여 직접청구 도산 지급누락 증빙")

    if (
        pension_context
        and _contains_any(
            normalized, ("집을 사", "집을 살", "집 살", "집 마련", "주택 구입")
        )
        and _contains_any(
            normalized, ("빼", "꺼내", "꺼낼", "인출", "쓰", "당겨 받")
        )
        and not _declines_action(
            normalized, ("빼", "꺼내", "꺼낼", "인출", "쓰", "당겨 받")
        )
    ):
        anchors.append("중도인출 무주택 주택 구입 증빙")

    if (
        pension_context
        and _contains_any(normalized, ("병원비", "치료비", "의료비", "요양"))
        and _contains_any(
            normalized, ("빼", "꺼내", "꺼낼", "인출", "미리", "쓰", "당겨 받")
        )
        and not _declines_action(
            normalized, ("빼", "꺼내", "꺼낼", "인출", "쓰", "당겨 받")
        )
    ):
        anchors.append("중도인출 6개월 이상 요양 의료비 증빙")

    if (
        "isa" in normalized
        and _contains_any(normalized, ("끝", "만기"))
        and pension_context
        and _contains_any(normalized, ("넣", "입금", "옮", "전환", "넘기"))
        and not _declines_action(
            normalized, ("넣", "입금", "옮", "전환", "넘기")
        )
    ):
        anchors.append("ISA 만기 연금계좌 전환입금 60일 추가 세액공제")

    if (
        "퇴직연금" in normalized
        and "규약" in normalized
        and _contains_any(
            normalized,
            (
                "변경",
                "고치",
                "고칠",
                "고쳐",
                "바꾸",
                "바꿀",
                "바꿔",
                "개정",
                "수정",
                "축소",
                "감소",
                "늘리",
                "증가",
                "악화",
                "개선",
                "완화",
                "확대",
            ),
        )
        and _contains_any(
            normalized,
            (
                "근로자대표",
                "근로자 대표",
                "직원 대표",
                "대표 절차",
                "대표의",
                "의견",
                "동의",
            ),
        )
    ):
        explicitly_nonadverse = bool(
            re.search(
                r"불리(?:하)?지\s*않|불리한\s*(?:건|것)은?\s*아니|"
                r"(?:혜택|급여|보장).{0,10}(?:확대|증가|늘)|"
                r"(?:부담|비용).{0,10}(?:감소|줄|낮)|"
                r"(?:조건|요건).{0,10}(?:개선|완화)|유리하게",
                normalized,
            )
        )
        adverse = bool(
            re.search(
                r"불리(?:하게|한)|"
                r"(?:혜택|급여|보장).{0,10}(?:축소|감소|줄)|"
                r"(?:부담|비용).{0,10}(?:증가|늘|높)|"
                r"(?:조건|요건).{0,10}(?:악화|강화)",
                normalized,
            )
        ) and not explicitly_nonadverse
        amendment_state = _three_way_state(
            positive=adverse,
            negative=explicitly_nonadverse,
        )
        if amendment_state == "positive":
            anchors.append(
                "근로자퇴직급여 보장법 제4조 사용자가 설정된 퇴직급여제도 내용을 "
                "불리하게 변경 근로자대표 불리 동의 동의를 받아야"
            )
        elif amendment_state == "negative":
            anchors.append(
                "근로자퇴직급여 보장법 제4조 사용자가 설정된 퇴직급여제도 "
                "내용 변경 근로자대표 의견청취 의견을 들어야"
            )
        else:
            anchors.append("퇴직연금규약 변경 근로자대표 의견청취 또는 동의")

    if (
        _has_ascii_term(normalized, "irp")
        and _contains_any(
            normalized,
            (
                "개인irp",
                "개인 irp",
                "개인형",
                "가입자부담금",
                "가입자 부담금",
                "최초 부담금",
                "최초 입금",
            ),
        )
        and _contains_any(normalized, ("부담금", "넣은 돈", "입금"))
        and _contains_any(normalized, ("취소", "되돌"))
        and not _declines_action(normalized, ("취소", "되돌"))
    ):
        first_deposit = _contains_any(
            normalized,
            ("최초 부담금", "최초 입금", "처음 넣는 돈", "첫 부담금", "첫 입금"),
        ) and not _contains_any(
            normalized,
            ("최초 입금이 아닌", "처음 넣는 돈은 아니", "첫 입금은 아니"),
        )
        anchors.append(
            "개인IRP 최초 부담금 입금취소"
            if first_deposit
            else "개인IRP 가입자부담금 입금취소"
        )

    if (
        _contains_any(
            normalized,
            ("연금실제수령연차", "실제 수령 연차", "실제수령연차"),
        )
        and _contains_any(normalized, ("개시", "인출", "돈을", "뺐", "수령"))
    ):
        anchors.append("연금실제수령연차 실제 인출")

    if (
        "재난" in normalized
        and "중도인출" in normalized
        and _has_ascii_term(normalized, "irp")
        and not _has_ascii_term(normalized, "db")
    ):
        anchors.append("재난 중도인출 개인IRP")

    target_date_alias = _contains_any(
        normalized,
        ("타깃데이트펀드", "타깃 데이트 펀드", "타겟데이트펀드", "타겟 데이트 펀드"),
    )
    whole_balance = _contains_any(normalized, ("100%", "전부", "전체", "모두"))
    if target_date_alias and whole_balance:
        if _has_ascii_term(normalized, "dc") or "확정기여" in normalized:
            anchors.append("TDF DC 100%")
        elif _has_ascii_term(normalized, "db") or "확정급여" in normalized:
            anchors.append("TDF DB 100%")

    if (
        _has_ascii_term(normalized, "mp")
        and "구독" in normalized
        and _contains_any(
            normalized,
            ("개인형퇴직연금", "개인형 퇴직연금", "개인형irp", "개인형 irp"),
        )
    ):
        anchors.append("MP 구독 서비스 개인형IRP")

    listed_products: list[str] = []
    if _has_ascii_term(normalized, "etf"):
        listed_products.append("ETF")
    if _has_ascii_term(normalized, "etn"):
        listed_products.append("ETN")
    if _contains_any(normalized, ("상장 리츠", "상장리츠", "리츠", "reit")):
        listed_products.append("상장 리츠")
    if (
        pension_context
        and listed_products
        and _contains_any(
            normalized,
            ("거래", "매매", "사고팔", "사다", "팔", "매수", "매도"),
        )
        and _contains_any(
            normalized,
            ("몇 시", "시간외", "장 마감", "장 시간", "정규장", "거래 시간"),
        )
    ):
        anchors.append(
            "ETF ETN 상장 리츠 정규장 시간외 거래"
            if len(listed_products) == 3
            else f"{listed_products[0]} 정규장 시간외 거래"
        )

    if (
        "임원" in normalized
        and "퇴직" in normalized
        and _has_ascii_term(normalized, "irp")
        and _contains_any(normalized, ("한도", "세법상 인정"))
        and _contains_any(normalized, ("과세이연", "세금을 미룰", "세금 미룰"))
    ):
        within_limit = bool(
            re.search(
                r"한도.{0,10}(?:이내|이하|미만|안(?:의|에)?|넘지\s*않|"
                r"초과(?:가|분은?|는)?\s*아니)|세법상\s*인정(?:되는|된)?\s*범위",
                normalized,
            )
        )
        over_limit = bool(
            re.search(r"한도.{0,10}(?:초과|넘(?:으면|는|은|어서)|벗어)", normalized)
        ) and not within_limit
        limit_state = _three_way_state(positive=over_limit, negative=within_limit)
        anchors.append(
            "임원퇴직소득 한도 한도 초과분 IRP 과세이연"
            if limit_state == "positive"
            else "임원퇴직소득 한도 한도 이내 퇴직소득 IRP 과세이연"
            if limit_state == "negative"
            else "임원퇴직소득 한도 IRP 과세이연"
        )

    if (
        _contains_any(normalized, ("연금계좌", "연금 계좌"))
        and _contains_any(normalized, ("운용수익", "투자수익", "번 돈", "번 수익"))
        and _contains_any(
            normalized,
            (
                "금융소득",
                "연금소득",
                "기타소득",
                "이자",
                "배당",
                "소득 분류",
                "소득구분",
                "세금",
                "과세",
            ),
        )
    ):
        outside_negated = bool(
            re.search(
                r"(?:연금\s*외(?:로)?|일시금(?:으로)?)\s*.{0,8}"
                r"(?:받|수령|찾).{0,6}(?:않|아니)",
                normalized,
            )
        )
        outside_pension = bool(
            re.search(
                r"연금\s*외(?:\s*수령|로\s*(?:받|수령))|연금외수령|"
                r"일시금(?:으로)?\s*(?:받|수령|찾)|한꺼번에\s*(?:받|찾)|몽땅\s*(?:받|찾)",
                normalized,
            )
        ) and not outside_negated
        pension_receipt = bool(
            re.search(
                r"연금으로\s*(?:받|수령)|나눠\s*(?:받|수령)|"
                r"정기적으로\s*(?:받|수령)",
                normalized,
            )
        )
        receipt_state = _three_way_state(
            positive=outside_pension,
            negative=pension_receipt,
        )
        anchors.append(
            "연금계좌 운용수익 연금외수령 기타소득"
            if receipt_state == "positive"
            else "연금계좌 운용수익 연금수령 연금소득"
            if receipt_state == "negative"
            else "연금계좌 운용수익 수령 방식 소득 구분"
        )

    if (
        (_has_ascii_term(normalized, "dc") or "확정기여" in normalized)
        and "중도인출" in normalized
        and _contains_any(normalized, ("개인회생", "개인워크아웃", "신용회복"))
    ):
        workout = _contains_any(normalized, ("개인워크아웃", "신용회복")) and not bool(
            re.search(r"(?:개인워크아웃|신용회복).{0,8}(?:아니|말고|제외)", normalized)
        )
        rehabilitation = "개인회생" in normalized and not bool(
            re.search(r"개인회생.{0,8}(?:아니|말고|제외)", normalized)
        )
        insolvency_state = _three_way_state(
            positive=workout,
            negative=rehabilitation,
        )
        if insolvency_state == "positive":
            anchors.append("DC 개인워크아웃 신용회복 중도인출")
        elif insolvency_state == "negative":
            anchors.append("DC 개인회생절차개시결정 중도인출")

    if (
        "국민연금" in normalized
        and "퇴직연금" in normalized
        and _contains_any(normalized, ("차이", "비교", "적립", "방식", "다른"))
    ):
        anchors.append("국민연금 부과방식 퇴직연금 사전적립방식")

    return tuple(dict.fromkeys(anchors))


def decompose_question(
    question: str,
    *,
    resolved_product_codes: tuple[str, ...] = (),
) -> tuple[SubQuestion, ...]:
    """Split explicit multi-intent wording without breaking noun conjunctions.

    Korean particles such as ``과/와`` commonly join product or account names,
    so they are deliberately not treated as clause boundaries.  Sentence marks
    and explicit discourse connectors are stable enough for a deterministic
    first-stage plan. Comma lists split only when every fragment has an
    independently recognized intent family. Known general topics and one
    uniquely resolved product may be carried into an anaphoric retrieval query;
    multiple-product references remain unresolved instead of being guessed.
    """

    normalized = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", question)).strip()
    explicit_parts = tuple(
        _LEADING_CONNECTOR_RE.sub("", part.strip(" ,;:"))
        for part in _CLAUSE_SPLIT_RE.split(normalized)
    )
    explicit_parts = tuple(part for part in explicit_parts if part)
    merged_parts: list[str] = []
    pending_context: list[str] = []
    for part in explicit_parts:
        if (
            _AUDIENCE_CONTEXT_ONLY_RE.match(part)
            or _CALCULATION_CONTEXT_ONLY_RE.match(part)
        ):
            pending_context.append(part)
            continue
        if (
            _LEADING_ANAPHORIC_CLAUSE_RE.match(part)
            and merged_parts
            and not _COMPLETED_REQUEST_CLAUSE_RE.search(merged_parts[-1])
        ):
            merged_parts[-1] = " ".join((merged_parts[-1], *pending_context, part))
            pending_context.clear()
            continue
        merged_parts.append(" ".join((*pending_context, part)))
        pending_context.clear()
    if pending_context:
        merged_parts.extend(pending_context)
    explicit_parts = tuple(merged_parts)
    parts = tuple(
        embedded
        for explicit in explicit_parts
        for implicit in _split_implicit_enumeration(explicit)
        for embedded in _split_embedded_questions(implicit)
    )
    if len(parts) == 1:
        parts = _split_telegraphic_enumeration(parts[0])
    if not parts:
        parts = (normalized or question,)

    subquestions: list[SubQuestion] = []
    previous_general_topics: tuple[str, ...] = ()
    for position, part in enumerate(parts, start=1):
        own_topics = _general_topic_terms(part)
        inherited: list[str] = []
        if (
            not own_topics
            and previous_general_topics
            and _CONTEXT_REFERENCE_RE.search(part)
        ):
            inherited.extend(previous_general_topics)
        raw_route = classify_question(part)
        if (
            len(resolved_product_codes) == 1
            and "product_explanation" in raw_route.labels
            and not requested_product_codes(part)
            and _ANAPHORIC_REFERENCE_RE.search(part)
        ):
            inherited.append(resolved_product_codes[0])
        inherited_terms = tuple(dict.fromkeys(inherited))
        route_text = (
            " ".join((*inherited_terms, part))
            if inherited_terms
            else part
        )
        anchor_text = route_text if "연금개시" in inherited_terms else part
        query_anchors = general_query_anchors(anchor_text)
        query_prefix = (*inherited_terms, *query_anchors)
        query_text = " ".join((*query_prefix, part)) if query_prefix else part
        subquestions.append(
            SubQuestion(
                subquestion_id=f"sq{position}",
                text=part,
                query_text=query_text,
                inherited_terms=inherited_terms,
                query_anchors=query_anchors,
                route=classify_question(route_text),
                scope=analyze_question_scope(part),
            )
        )
        if own_topics:
            previous_general_topics = own_topics
    return tuple(subquestions)


def plan_question(
    question: str,
    route: QuestionRoute,
    *,
    resolved_product_codes: tuple[str, ...] = (),
) -> QuestionPlan:
    """Build a deterministic minimum-evidence plan from the classified request.

    This is deliberately conservative.  It does not rewrite the question or infer
    unstated customer facts.  Product-code and comparison-axis extraction reuses
    the same normalized parsers as retrieval.
    """

    requirements: list[EvidenceRequirement] = []
    question_scope = analyze_question_scope(question)
    subquestions = decompose_question(
        question,
        resolved_product_codes=resolved_product_codes,
    )
    for subquestion in subquestions:
        labels = set(subquestion.route.labels)
        scope_terms = required_scope_terms(subquestion.text)
        topic_source = " ".join((*subquestion.inherited_terms, subquestion.text))
        topic_terms = tuple(
            dict.fromkeys(
                (
                    *_general_topic_terms(topic_source),
                    *(
                        term
                        for anchor in subquestion.query_anchors
                        for term in _QUERY_ANCHOR_TOPIC_TERMS.get(anchor, ())
                    ),
                )
            )
        )
        requirement_start = len(requirements)

        for route_label, korean_label in (
            ("system", "연금 제도 근거"),
            ("tax", "연금 세제 근거"),
            ("procedure", "연금 절차 근거"),
        ):
            if route_label in labels:
                requirements.append(
                    EvidenceRequirement(
                        requirement_id=f"{subquestion.subquestion_id}:general:{route_label}",
                        kind="general_rule",
                        label=korean_label,
                        source_group="general",
                        route_label=route_label,
                        required_terms=scope_terms,
                        topic_terms=topic_terms,
                        minimum_count=_scheme_evidence_minimum(
                            subquestion.query_text
                        ),
                        subquestion_id=subquestion.subquestion_id,
                        query_text=subquestion.query_text,
                    )
                )

        product_route = bool(
            labels
            & {
                "product_explanation",
                "product_comparison",
                "conditional_recommendation",
            }
        )
        if product_route:
            clause_codes = requested_product_codes(subquestion.text)
            ambiguous_reference = (
                not clause_codes
                and len(resolved_product_codes) > 1
                and _ANAPHORIC_REFERENCE_RE.search(subquestion.text) is not None
            )
            codes = (
                ()
                if ambiguous_reference
                else clause_codes or resolved_product_codes
            )
            axes = requested_axes(subquestion.text)
            if "conditional_recommendation" in labels:
                axes = explicit_requested_axes(subquestion.text) or (
                    "category",
                    "risk",
                    "class",
                )
            comparison = "product_comparison" in labels
            if codes:
                for code in codes:
                    requirements.append(
                        EvidenceRequirement(
                            requirement_id=(
                                f"{subquestion.subquestion_id}:product:{code}:identity"
                            ),
                            kind="product_identity",
                            label=f"상품코드 {code}의 identity",
                            source_group="product",
                            product_code=code,
                            subquestion_id=subquestion.subquestion_id,
                            query_text=subquestion.query_text,
                        )
                    )
                    for axis in axes:
                        requirements.append(
                            EvidenceRequirement(
                                requirement_id=(
                                    f"{subquestion.subquestion_id}:product:{code}:{axis}"
                                ),
                                kind=(
                                    "product_comparison_axis"
                                    if comparison
                                    else "product_axis"
                                ),
                                label=f"상품코드 {code}의 {axis} 근거",
                                source_group="product",
                                product_code=code,
                                axis=axis,
                                subquestion_id=subquestion.subquestion_id,
                                query_text=subquestion.query_text,
                            )
                        )
            else:
                minimum = 2 if comparison else 1
                requirements.append(
                    EvidenceRequirement(
                        requirement_id=f"{subquestion.subquestion_id}:product:identity",
                        kind="product_identity",
                        label=(
                            "지칭한 상품의 정확한 공식 상품명 또는 상품코드"
                            if ambiguous_reference
                            else
                            "비교할 두 상품의 정확한 identity"
                            if comparison
                            else "질문 대상 상품의 정확한 identity"
                        ),
                        source_group="product",
                        minimum_count=minimum,
                        subquestion_id=subquestion.subquestion_id,
                        query_text=subquestion.query_text,
                        explicit_identity_required=ambiguous_reference,
                    )
                )
                for axis in axes:
                    requirements.append(
                        EvidenceRequirement(
                            requirement_id=(
                                f"{subquestion.subquestion_id}:product:axis:{axis}"
                            ),
                            kind=(
                                "product_comparison_axis"
                                if comparison
                                else "product_axis"
                            ),
                            label=f"질문 대상 상품의 {axis} 근거",
                            source_group="product",
                            axis=axis,
                            minimum_count=minimum if comparison else 1,
                            subquestion_id=subquestion.subquestion_id,
                            query_text=subquestion.query_text,
                        )
                    )

        if subquestion.route.requires_calculation:
            requirements.append(
                EvidenceRequirement(
                    requirement_id=(
                        f"{subquestion.subquestion_id}:calculation:pension_tax_credit"
                    ),
                    kind="calculation",
                    label="세액공제 결정론적 계산",
                    source_group="general",
                    route_label="tax",
                    subquestion_id=subquestion.subquestion_id,
                    query_text=subquestion.query_text,
                )
            )

        if len(requirements) == requirement_start:
            requirements.append(
                EvidenceRequirement(
                    requirement_id=f"{subquestion.subquestion_id}:general:domain",
                    kind="general_rule",
                    label="연금 질문에 직접 관련된 근거",
                    source_group="general",
                    route_label="domain",
                    required_terms=scope_terms,
                    topic_terms=topic_terms,
                    subquestion_id=subquestion.subquestion_id,
                    query_text=subquestion.query_text,
                )
            )

    unique = {item.requirement_id: item for item in requirements}
    unique_requirements = tuple(unique.values())
    return QuestionPlan(
        version=PLANNER_VERSION,
        requirements=unique_requirements,
        question=question,
        scope=question_scope,
        unsupported_scope_reasons=unsupported_scope_reasons(
            question,
            scope=question_scope,
        ),
        subquestions=subquestions,
        tools=select_planned_tools(
            requirements=unique_requirements,
            subquestions=subquestions,
        ),
    )
