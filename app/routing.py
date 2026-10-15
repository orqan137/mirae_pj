from __future__ import annotations

import re
from dataclasses import dataclass

from app.korean_query import normalize_korean_query

ROUTER_VERSION = "pension-question-router-v12"

_PRODUCT_CODE_RE = re.compile(
    r"(?<![A-Za-z0-9])kr[0-9a-z]{8,}(?![A-Za-z0-9])",
    flags=re.IGNORECASE,
)
_SUITABILITY_WITHDRAWAL_PLAN_RE = re.compile(
    r"(?:중도\s*인출|인출)\s*계획(?:은|이|도|을)?\s*(?:없|있)"
)


@dataclass(frozen=True, slots=True)
class QuestionRoute:
    labels: tuple[str, ...]
    source_groups: frozenset[str]
    needs_conditions: bool
    missing_conditions: tuple[str, ...]
    requires_calculation: bool


def normalize_question_text(text: str) -> str:
    """Normalize compatibility forms and safe spacing variants of domain terms."""

    return normalize_korean_query(text).normalized


def _normalize(text: str) -> str:
    return normalize_question_text(text)


def _contains(text: str, words: tuple[str, ...]) -> bool:
    return any(word in text for word in words)


def _ascii_term(text: str, term: str) -> bool:
    return bool(
        re.search(
            rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])",
            text,
            flags=re.IGNORECASE,
        )
    )


def _declines_action(text: str, cues: tuple[str, ...]) -> bool:
    """Return whether every mentioned cue is an explicit refusal, not a question.

    Korean counterfactuals such as ``옮기지 않으면`` can still ask about a
    procedure, so they are deliberately not treated as refusals.  This helper
    only recognizes stated intent/plan refusals and continuation forms such as
    ``빼지 않고 유지``.
    """

    matched = False
    for cue in cues:
        start = 0
        while True:
            position = text.find(cue, start)
            if position < 0:
                break
            matched = True
            segment = text[position : position + 48]
            refused = bool(
                re.search(
                    r"(?:의향|계획|생각).{0,10}(?:없|않)|"
                    r"지\s*않(?:고|겠|을|으려|기로|도록)|"
                    r"(?:는|은|을|할|하는|하려는|하려고)?\s*.{0,8}"
                    r"안\s*(?:하(?:고|겠|려|려고|기로|는)|할)|"
                    r"원하지\s*않",
                    segment,
                )
            )
            if not refused:
                return False
            start = position + len(cue)
    return matched


def _has_active_procedure_intent(text: str) -> bool:
    action_terms = (
        "중도인출",
        "중도 인출",
        "해지",
        "이전",
        "옮기",
        "옮길",
        "옮겨",
        "이관",
        "갈아타",
        "전환",
        "가입",
        "신청",
        "서류",
        "준비물",
        "절차",
        "청구",
        "입금",
        "넣으려",
        "납입하려",
        "돈을 빼",
        "돈을 꺼내",
        "빼려",
        "빼면",
        "꺼낼",
        "미리 쓸",
        "미리 쓰",
        "당겨 받",
        "넘기",
        "연금수령",
        "연금 수령",
        "수령 개시",
        "지급 신청",
    )
    return any(
        term in text and not _declines_action(text, (term,))
        for term in action_terms
    )


# Asking which product ranks first is a recommendation, whatever verb carries
# it. "연금저축 수익률이 가장 높은 상품 1위를 알려주세요" was labelled a product
# explanation, skipped the suitability gate and reached generation — the one
# path that must never put a product forward without the five conditions, and
# the charter forbids ranking the whole market besides. A superlative counts
# only where it selects among products; the rank words stand on their own.
_RANK_WORD_RE = re.compile(r"\b\d+\s*위\b|순위|베스트|랭킹|top\s*\d")
_SUPERLATIVE_SELECTION_RE = re.compile(
    r"(?:가장|제일|최고|최저)\s*\S{0,6}?\s*(?:좋|낫|높|낮|저렴|싼|유리|우수)[^.?!]{0,20}"
    r"(?:상품|펀드|종목|투자신탁)"
    r"|(?:상품|펀드|종목|투자신탁)[^.?!]{0,20}(?:가장|제일|최고|최저)\s*\S{0,6}?\s*"
    r"(?:좋|낫|높|낮|저렴|싼|유리|우수)"
)


# Enumerating "어떤 상품이 좋", "어떤 펀드가 좋" and the rest left the plainest
# recommendation of all — "어떤 연금상품이 좋을까요" — with no label, and it was
# refused instead of asking for the five conditions. The noun between the
# determiner and the predicate is whatever the asker calls the product.
_ASKS_WHICH_IS_BETTER_RE = re.compile(
    r"(?:어떤|어느|무슨|뭐|무엇)\s*\S{0,12}?\s*(?:이|가|을|를|은|는)?\s*"
    r"(?:좋|낫|괜찮|추천|적합|맞)"
)


def classify_question(question: str) -> QuestionRoute:
    normalized = _normalize(question)
    labels: list[str] = []

    has_tax = _contains(
        normalized,
        (
            "세액공제",
            "공제액",
            # "총급여 5천만원이면 공제율이 얼마인가요" carries no other tax word.
            "공제율",
            "소득공제",
            "세금",
            "과세",
            "세율",
            "절세",
            "연금소득세",
            "퇴직소득세",
            "세제혜택",
            "세제 혜택",
            "세금혜택",
            "세금 혜택",
            "연말정산",
            "건강보험료",
            "건보료",
            "금융소득",
            "연금소득",
            "기타소득",
            "소득 분류",
            "소득구분",
        ),
    )
    has_procedure = _has_active_procedure_intent(normalized)
    actual_receipt_year_topic = _contains(
        normalized,
        ("연금실제수령연차", "실제 수령 연차", "실제수령연차"),
    )
    rule_amendment_topic = (
        "퇴직연금" in normalized
        and "규약" in normalized
        and _contains(
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
        and _contains(
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
    )
    pension_context = (
        _contains(normalized, ("퇴직연금", "연금계좌", "연금 계좌"))
        or _ascii_term(normalized, "irp")
    ) and not bool(
        re.search(
            r"(?:퇴직연금|연금\s*계좌|개인형\s*irp|irp)"
            r"(?:이|가|은|는)?\s*(?:아닌|아니고|말고|제외한)",
            normalized,
            flags=re.IGNORECASE,
        )
    )
    listed_product_hours_topic = (
        pension_context
        and (
            _ascii_term(normalized, "etf")
            or _ascii_term(normalized, "etn")
            or _contains(normalized, ("상장 리츠", "상장리츠", "reit"))
        )
        and _contains(
            normalized,
            ("거래", "사고팔", "매매", "사다", "팔", "매수", "매도"),
        )
        and _contains(
            normalized,
            ("몇 시", "시간외", "장 마감", "장 시간", "정규장", "거래 시간"),
        )
    )
    if actual_receipt_year_topic or rule_amendment_topic or listed_product_hours_topic:
        has_procedure = True
    if _SUITABILITY_WITHDRAWAL_PLAN_RE.search(normalized) and not _contains(
        normalized,
        (
            "중도인출 사유",
            "중도 인출 사유",
            "중도인출 요건",
            "중도 인출 요건",
            "중도인출 조건",
            "중도 인출 조건",
            "중도인출 서류",
            "중도 인출 서류",
            "중도인출 절차",
            "중도 인출 절차",
            "해지",
            "이전",
            "옮기",
            "이관",
            "갈아타",
            "전환",
            "가입 신청",
            "지급 신청",
            "서류",
            "준비물",
            "절차",
            "청구",
        ),
    ):
        # A stated liquidity preference is a suitability input, not by itself
        # a request to explain withdrawal rules.
        has_procedure = False
    product_codes = tuple(_PRODUCT_CODE_RE.findall(normalized))
    has_product_code = bool(product_codes)
    has_product_mention = has_product_code or _contains(
        normalized,
        (
            "상품",
            "펀드",
            "투자신탁",
            "채권",
            "국공채",
            "보수",
            "수수료",
            "총비용",
            "총 비용",
            "수익률",
            "운용성과",
            "시장잔고",
            "시장 잔고",
            "위험등급",
            "위험 등급",
            "판매클래스",
            "판매 클래스",
        ),
    ) or any(_ascii_term(normalized, term) for term in ("etf", "aum"))
    plural_product_reference = len(product_codes) >= 2 or _contains(
        normalized,
        ("두 상품", "두 펀드", "둘 중", "상품 중", "펀드 중"),
    )
    relative_choice = _contains(
        normalized,
        (
            "더 낮",
            "더 높",
            "낮은 쪽",
            "높은 쪽",
            "어느 쪽",
            "어떤 쪽",
            "유리한 쪽",
            "불리한 쪽",
            "중에서",
            "가운데",
        ),
    )
    has_comparison = _contains(
        normalized,
        ("비교", "차이", "대비", "어떤 게", "어느 게", "뭐가 달라"),
    ) or (has_product_mention and plural_product_reference and relative_choice)
    has_recommendation = _contains(
        normalized,
        (
            "추천",
            "골라",
            "좋은 상품",
            "적합한 상품",
            "맞는 상품",
            "맞는 펀드",
            "어울리는 상품",
            "어울리는 펀드",
            # The list held only noun phrases, so "위험등급 낮은 상품 중에
            # 뭐가 좋나요" carried no recommendation label, skipped the
            # suitability gate and reached generation — the one path that must
            # never produce a product suggestion without the five conditions.
            "뭐가 좋",
            "뭐가 나",
            "무엇이 좋",
            "어떤 게 좋",
            "어떤 것이 좋",
            "어느 게 좋",
            "어느 것이 좋",
            "어떤 상품이 좋",
            "어떤 펀드가 좋",
            "추천해",
            "추천해줘",
            "추천해주세요",
            "괜찮을까",
            "괜찮은가",
            "나을까",
            "고르면 좋",
            "선택하면 좋",
        ),
    ) or _ASKS_WHICH_IS_BETTER_RE.search(normalized) is not None or (
        _RANK_WORD_RE.search(normalized) is not None
    ) or (
        # A superlative selects among products only when none is named. "이
        # 상품의 위험등급이 가장 높은 등급인가요" asks about one prospectus and
        # is not a request to rank the market.
        not has_product_code
        and not _contains(normalized, ("이 상품", "해당 상품", "그 상품", "이 펀드", "해당 펀드"))
        and _SUPERLATIVE_SELECTION_RE.search(normalized) is not None
    )
    has_system = _contains(
        normalized,
        (
            "연금저축",
            "퇴직연금",
            "국민연금",
            "개인연금",
            "개인형퇴직연금",
            "연금계좌",
            "연금 계좌",
            "확정급여",
            "확정기여",
            "디폴트옵션",
            "디폴트 옵션",
        ),
    ) or any(_ascii_term(normalized, term) for term in ("db", "dc", "irp"))
    explicitly_non_pension_account = bool(
        re.search(
            r"(?:퇴직연금|연금\s*계좌|개인형\s*irp|irp)"
            r"(?:이|가|은|는)?\s*(?:아닌|아니고|말고|제외한)",
            normalized,
            flags=re.IGNORECASE,
        )
    ) and _contains(
        normalized,
        (
            "일반 주식계좌",
            "일반계좌",
            "일반 계좌",
            "위탁계좌",
            "위탁 계좌",
            "종합계좌",
            "종합 계좌",
        ),
    )
    if explicitly_non_pension_account:
        has_system = False
    has_system = has_system or actual_receipt_year_topic

    if (
        has_system
        and "수령" in normalized
        and _contains(
            normalized,
            (
                "몽땅",
                "전액",
                "한꺼번에",
                "한 번에",
                "수령 개시",
                "수령 신청",
                "수령한도",
                "수령 한도",
            ),
        )
    ):
        has_procedure = has_procedure or not _declines_action(normalized, ("수령",))
    if (
        "퇴직급여" in normalized
        and "요구" in normalized
        and _contains(normalized, ("밀리", "미지급", "폐업", "도산", "망하"))
    ):
        has_procedure = True

    # Generic asset-class words such as ETF, fund or bond often appear in
    # pension rules (eligible assets, transfer, taxation). They do not by
    # themselves identify one of the 100 product prospectuses. Select the
    # product evidence path only for an explicit code, a requested product
    # attribute/comparison, or a recommendation that needs product candidates.
    has_product_axis = _contains(
        normalized,
        (
            "상품분류",
            "자산유형",
            "보수",
            "수수료",
            "총비용",
            "총 비용",
            "수익률",
            "운용성과",
            "시장잔고",
            "시장 잔고",
            "위험등급",
            "위험 등급",
            "판매클래스",
            "판매 클래스",
        ),
    ) or _ascii_term(normalized, "aum")
    has_product_intent = (
        has_product_code
        or has_product_axis
        or (has_product_mention and has_comparison)
        or has_recommendation
    )

    if has_system:
        labels.append("system")
    if has_tax:
        labels.append("tax")
    if has_procedure:
        labels.append("procedure")
    if has_product_intent and has_comparison:
        labels.append("product_comparison")
    elif has_product_intent:
        labels.append("product_explanation")
    if has_recommendation:
        labels.append("conditional_recommendation")
    if not labels or (len(labels) >= 2 and not has_recommendation):
        labels.append("combined")

    product_only = has_product_intent and not (
        has_tax or has_procedure or has_system or has_recommendation
    )
    if product_only:
        groups = frozenset({"product"})
    elif has_product_intent or has_recommendation:
        groups = frozenset({"general", "product"})
    else:
        groups = frozenset({"general"})

    # Tax terminology alone is not a request for a personal calculation.
    # Route to the calculator only when the user explicitly asks to calculate
    # an amount or supplies a contribution amount for that purpose.
    # The calculator reads 억·천만·백만·만 as well, so the router recognising
    # only 원/만원 sent "총급여 7천만원" down the path with no calculator.
    has_explicit_amount = bool(
        re.search(r"\d[\d,]*\s*(?:억|천만|백만|만)?\s*원", normalized)
    )
    requires_calculation = has_tax and (
        _contains(normalized, ("계산해", "계산해줘", "계산해주세요", "공제액을 계산"))
        or (has_explicit_amount and _contains(normalized, ("얼마", "공제액")))
        # The rate follows from the asker's own income, so "총급여 7천만원이면
        # 세액공제율이 몇 퍼센트인가요" is a per-person question. Without the
        # calculator it was answered from whichever bracket row retrieval
        # surfaced, and a 7천만원 asker was told 16.5%.
        or (
            has_explicit_amount
            and _contains(normalized, ("공제율", "세액공제율", "몇 퍼센트", "몇 %", "적용 세율"))
        )
    )

    # A recommendation is conditional until all five suitability dimensions
    # are present.  Mentioning one or two dimensions is not a complete profile.
    condition_dimensions = {
        "account": _contains(normalized, ("계좌", "연금저축", "퇴직연금"))
        or any(_ascii_term(normalized, term) for term in ("irp", "dc", "db")),
        "horizon": _contains(
            normalized,
            (
                "투자기간",
                "투자 기간",
                "은퇴시점",
                "은퇴 시점",
                "장기 투자",
                "단기 투자",
                "보유기간",
                "보유 기간",
            ),
        )
        or bool(re.search(r"\d+\s*년", normalized)),
        "risk": _contains(
            normalized,
            (
                "위험 감수",
                "위험을 감수",
                "위험성향",
                "위험 성향",
                "안정형",
                "안정적",
                "공격형",
                "공격적",
                "손실 감내",
                "손실을 감내",
                "변동성 감수",
                "변동성을 감수",
                "원금보전",
                "원금 보전",
            ),
        ),
        "liquidity": _contains(
            normalized,
            (
                "유동성 필요",
                "유동성이",
                "중도인출 계획",
                "중도 인출 계획",
                "중도인출 가능",
                "중도 인출 가능",
                "환매 가능",
                "현금화",
                "긴급자금",
                "긴급 자금",
                "자금 필요",
                "인출 계획",
            ),
        ),
        "objective": _contains(
            normalized,
            (
                "노후자금",
                "노후 자금",
                "노후 대비",
                "자산성장",
                "자산 성장",
                "소득 창출",
                "연금소득 목표",
                "연금 소득 목표",
                "현금흐름",
                "현금 흐름",
                "수익 추구",
                "원금 보전",
            ),
        ),
    }
    condition_labels = {
        "account": "계좌 유형",
        "horizon": "투자기간 또는 은퇴 시점",
        "risk": "손실 감내 능력과 위험 선호",
        "liquidity": "유동성 또는 중도인출 계획",
        "objective": "노후자금 목표",
    }
    missing_conditions = tuple(
        condition_labels[name]
        for name, present in condition_dimensions.items()
        if has_recommendation and not present
    )
    needs_conditions = bool(missing_conditions)
    return QuestionRoute(
        labels=tuple(dict.fromkeys(labels)),
        source_groups=groups,
        needs_conditions=needs_conditions,
        missing_conditions=missing_conditions,
        requires_calculation=requires_calculation,
    )
