from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


SEMANTIC_COVERAGE_VERSION = "pension-semantic-coverage-v3"

_MONEY_AMOUNT = r"\d[\d,]*(?:\.\d+)?\s*(?:천만|백만|십만|억|만|천|백)?\s*원"


def _pattern(value: str) -> re.Pattern[str]:
    return re.compile(value, flags=re.IGNORECASE | re.DOTALL)


def _normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


@dataclass(frozen=True, slots=True)
class SemanticSlot:
    """A domain concept requested by a question and expressible in an answer."""

    name: str
    question_patterns: tuple[re.Pattern[str], ...]
    answer_patterns: tuple[re.Pattern[str], ...]
    question_exclusions: tuple[re.Pattern[str], ...] = ()

    def requested_by(self, question: str) -> bool:
        return (
            any(pattern.search(question) for pattern in self.question_patterns)
            and not any(pattern.search(question) for pattern in self.question_exclusions)
        )

    def expressed_by(self, answer: str) -> bool:
        return any(pattern.search(answer) for pattern in self.answer_patterns)


# These slots describe broad pension intents. They are deliberately independent
# of source IDs, benchmark case IDs and exact correct values. A question can
# contain a false number; numeric correctness remains the citation validator's
# job, while this module only checks whether the requested subject was covered.
SEMANTIC_SLOTS: tuple[SemanticSlot, ...] = (
    SemanticSlot(
        name="pension_commencement",
        question_patterns=(
            _pattern(r"연금.{0,10}(?:개시|시작)"),
            _pattern(r"(?:개시|시작).{0,10}연금"),
            _pattern(
                r"연금.{0,10}(?:받을\s*수|수령[^.!?\n]{0,10}(?:가능|할\s*수))"
            ),
        ),
        answer_patterns=(
            _pattern(r"연금.{0,10}(?:개시|시작)"),
            _pattern(r"(?:개시|시작).{0,10}연금"),
            _pattern(r"연금\s*수령.{0,12}(?:요건|조건|가능|시작)"),
        ),
    ),
    SemanticSlot(
        name="withdrawal",
        question_patterns=(
            _pattern(
                r"중도\s*인출[^.!?\n]{0,20}"
                r"(?:가능|할\s*수|되나|사유|요건|조건|언제|어떻게)"
            ),
            _pattern(r"(?:꺼내|빼)(?:도|려|려고|면|는\s*법|는\s*방법)"),
            _pattern(r"해지.{0,20}(?:가능|할\s*수|되나|세금|과세|절차|방법|하면|할\s*때)"),
        ),
        answer_patterns=(
            _pattern(r"중도\s*인출|인출.{0,14}(?:가능|불가|요건|사유|절차)"),
            _pattern(r"해지.{0,14}(?:가능|불가|세금|과세|절차|방법)"),
        ),
    ),
    SemanticSlot(
        name="account_transfer",
        question_patterns=(
            _pattern(r"실물\s*이전"),
            _pattern(r"계좌.{0,12}(?:이전|이체|옮기|갈아타)"),
            _pattern(r"(?:이전|이체|옮기|갈아타).{0,12}계좌"),
            _pattern(r"(?:irp|연금저축|퇴직연금).{0,12}(?:이전|이체|옮기|갈아타)"),
        ),
        answer_patterns=(
            _pattern(r"실물\s*이전"),
            _pattern(r"계좌.{0,14}(?:이전|이체|옮기)"),
            _pattern(r"(?:이전|이체|옮기).{0,14}계좌"),
            _pattern(r"(?:irp|연금저축|퇴직연금).{0,14}(?:이전|이체|옮기)"),
        ),
    ),
    SemanticSlot(
        name="tax_credit",
        question_patterns=(_pattern(r"세액\s*공제|공제율|공제\s*한도|세금\s*혜택"),),
        answer_patterns=(_pattern(r"세액\s*공제|공제율|공제\s*한도"),),
    ),
    SemanticSlot(
        name="tax_credit_limit",
        question_patterns=(
            _pattern(r"(?:세액\s*공제|공제)[^.!?\n]{0,16}한도"),
        ),
        question_exclusions=(
            _pattern(
                r"\A(?!.*(?:얼마|금액|액수|몇|숫자))"
                r"(?=.*(?:한도란|한도[^.!?\n]{0,20}(?:뜻|의미|정의)))"
            ),
        ),
        answer_patterns=(
            _pattern(
                rf"(?:세액\s*공제|공제)[^.!?\n]{{0,20}}(?:한도|상한)"
                rf"[^.!?\n]{{0,30}}{_MONEY_AMOUNT}"
            ),
            _pattern(
                rf"세액\s*공제[^.!?\n]{{0,20}}{_MONEY_AMOUNT}"
                r"[^.!?\n]{0,10}(?:한도|상한)"
            ),
            _pattern(
                rf"{_MONEY_AMOUNT}\s*까지[^.!?\n]{{0,12}}세액\s*공제"
                r"[^.!?\n]{0,12}(?:받|적용)"
            ),
            _pattern(
                rf"세액\s*공제[^.!?\n]{{0,20}}(?:대상|받을\s*수\s*있는)"
                rf"[^.!?\n]{{0,20}}납입[^.!?\n]{{0,15}}{_MONEY_AMOUNT}\s*까지"
            ),
            _pattern(
                r"(?:세액\s*공제|공제)[^.!?\n]{0,20}한도[^.!?\n]{0,35}"
                r"(?:(?:확인|확정)\s*(?:할\s*수\s*없|되지\s*않)|조건.{0,8}필요)"
            ),
            # A direct amount can answer a limit question, but a rate or a
            # longer refund example containing that amount cannot replace it.
            _pattern(
                rf"\A\s*(?:연간?|매년)?\s*{_MONEY_AMOUNT}\s*(?:까지)?\s*"
                r"(?:입니다|이에요|이다)?\s*[.!]?\s*"
                r"(?:\[(?:근거|수치출처):[^\]]+\]\s*[.!]?)?\s*\Z"
            ),
        ),
    ),
    SemanticSlot(
        name="taxation",
        question_patterns=(
            _pattern(
                r"연금\s*소득세|퇴직\s*소득세|기타\s*소득세|과세|세율|세금(?!\s*혜택)"
            ),
        ),
        answer_patterns=(
            _pattern(r"연금\s*소득세|퇴직\s*소득세|기타\s*소득세|과세|세율|세금"),
        ),
    ),
    SemanticSlot(
        name="product_fee",
        question_patterns=(_pattern(r"총\s*보수|보수율|수수료|운용\s*비용"),),
        answer_patterns=(_pattern(r"총\s*보수|보수율|수수료|운용\s*비용"),),
    ),
    SemanticSlot(
        name="investment_return",
        question_patterns=(_pattern(r"수익률|운용\s*성과"),),
        answer_patterns=(_pattern(r"수익률|운용\s*성과"),),
    ),
    SemanticSlot(
        name="risk_grade",
        question_patterns=(_pattern(r"위험\s*등급|위험도|위험\s*수준|원금\s*손실"),),
        answer_patterns=(_pattern(r"위험\s*등급|위험도|위험\s*수준|원금\s*손실"),),
    ),
    SemanticSlot(
        name="default_option",
        question_patterns=(_pattern(r"디폴트\s*옵션|사전\s*지정\s*운용"),),
        answer_patterns=(_pattern(r"디폴트\s*옵션|사전\s*지정\s*운용"),),
    ),
    SemanticSlot(
        name="contribution",
        question_patterns=(
            _pattern(r"(?:납입|입금|불입).{0,14}(?:한도|가능|얼마|세액\s*공제|방법)"),
            _pattern(r"(?:한도|얼마).{0,14}(?:납입|입금|불입)"),
        ),
        answer_patterns=(_pattern(r"납입|입금|불입|부담금"),),
    ),
    SemanticSlot(
        name="retirement_money",
        question_patterns=(_pattern(r"퇴직금|이연\s*퇴직\s*소득"),),
        answer_patterns=(_pattern(r"퇴직금|이연\s*퇴직\s*소득"),),
    ),
    SemanticSlot(
        name="age_condition",
        question_patterns=(
            _pattern(
                r"(?:만\s*)?\d{1,3}\s*세.{0,30}"
                r"(?:가능|조건|요건|개시|시작|수령|받을\s*수|인출|언제)"
            ),
            _pattern(
                r"(?:가능|조건|요건|개시|시작|수령|받을\s*수|인출|언제).{0,30}"
                r"(?:만\s*)?\d{1,3}\s*세"
            ),
        ),
        answer_patterns=(_pattern(r"(?:만\s*)?\d{1,3}\s*세"),),
    ),
    SemanticSlot(
        name="enrollment_period_condition",
        question_patterns=(
            _pattern(
                r"(?=.*(?:가입\s*기간|가입.{0,8}\d+(?:\.\d+)?\s*년))"
                r"(?=.*(?:가능|조건|요건|개시|수령|인출|해지))"
            ),
        ),
        answer_patterns=(
            _pattern(
                r"(?=.*(?:가입|가입\s*기간|기간))"
                r"(?=.*\d+(?:\.\d+)?\s*년)"
            ),
        ),
    ),
)

_SLOT_BY_NAME = {slot.name: slot for slot in SEMANTIC_SLOTS}


@dataclass(frozen=True, slots=True)
class SemanticCoverageAssessment:
    version: str
    required: frozenset[str]
    covered: frozenset[str]
    missing: tuple[str, ...]


def required_question_slots(question: str) -> frozenset[str]:
    normalized = _normalize(question)
    return frozenset(
        slot.name for slot in SEMANTIC_SLOTS if slot.requested_by(normalized)
    )


def expressed_slots(answer: str, required: frozenset[str]) -> frozenset[str]:
    normalized_answer = _normalize(answer)
    return frozenset(
        name
        for name in required
        if name in _SLOT_BY_NAME and _SLOT_BY_NAME[name].expressed_by(normalized_answer)
    )


# Slots whose subject is a figure. When the question asks for the magnitude
# outright, the number is the answer.
_QUANTITATIVE_SLOTS = frozenset(
    {"tax_credit", "taxation", "product_fee", "investment_return"}
)
_MAGNITUDE_QUESTION_RE = re.compile(
    r"얼마|몇\s|몇$|한도(?:가|는|를)?\s*(?:얼마|어떻게|어떻게\s*되)|"
    r"율(?:이|은)?\s*(?:얼마|어떻게)|비율(?:이|은)?\s*(?:얼마|어떻게)"
)
_MAGNITUDE_ANSWER_RE = re.compile(
    r"\d[\d,]*\s*(?:%|퍼센트|원|만원|억원|천만원|백만원)"
)


def assess_semantic_coverage(
    question: str,
    answer: str,
) -> SemanticCoverageAssessment:
    required = required_question_slots(question)
    covered = expressed_slots(answer, required)
    # "IRP 세액공제 한도가 얼마인가요" answered with "연 900만원입니다" is
    # responsive, but the slot patterns look for the topic word and the concise
    # answer repeats none of it. Requiring the echo discarded a correct answer.
    # Only a question that asks for the magnitude outright can be satisfied
    # this way, and only by a slot whose subject is a figure, so a question
    # about 중도인출 사유 still cannot be answered with a rate.
    normalized_question = _normalize(question)
    if _MAGNITUDE_QUESTION_RE.search(normalized_question) and _MAGNITUDE_ANSWER_RE.search(
        _normalize(answer)
    ):
        covered = covered | (required & _QUANTITATIVE_SLOTS)
    missing = tuple(
        slot.name for slot in SEMANTIC_SLOTS if slot.name in required - covered
    )
    return SemanticCoverageAssessment(
        version=SEMANTIC_COVERAGE_VERSION,
        required=required,
        covered=covered,
        missing=missing,
    )


def covered_question_slots(question: str, answer: str) -> frozenset[str]:
    return assess_semantic_coverage(question, answer).covered


def missing_question_slots(question: str, answer: str) -> tuple[str, ...]:
    return assess_semantic_coverage(question, answer).missing


PENSION_RULE_TOPICS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("irp", ("irp", "개인형 퇴직연금")),
    ("db", ("db형", "확정급여")),
    ("dc", ("dc형", "확정기여")),
    ("pension_savings", ("연금저축",)),
    ("withdrawal", ("중도인출", "중도 인출", "인출", "해지")),
    ("transfer", ("실물이전", "실물 이전", "이전", "이체")),
    ("tax_credit", ("세액공제", "공제율", "공제 한도", "공제한도")),
    ("tax", ("과세", "세금", "세율", "퇴직소득세", "연금소득세")),
    ("contribution", ("납입", "입금", "불입", "부담금")),
    ("receipt", ("연금수령", "연금 수령", "수령")),
    ("enrollment", ("가입", "가입자")),
    ("fee", ("총보수", "총 보수", "보수율", "수수료")),
    ("return", ("수익률", "운용성과", "운용 성과")),
    ("risk", ("위험등급", "위험 등급", "위험도")),
)
