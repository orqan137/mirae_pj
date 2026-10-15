from __future__ import annotations

import re
from dataclasses import dataclass

from app.routing import normalize_question_text


SOURCE_ID = "doc41"
SOURCE_PATH = "일반문서/doc41.docx"
CALCULATOR_VERSION = "pension-tax-credit-doc41-v9"

PENSION_SAVINGS_LIMIT_WON = 6_000_000
COMBINED_LIMIT_WON = 9_000_000
SALARY_THRESHOLD_WON = 55_000_000
COMPREHENSIVE_INCOME_THRESHOLD_WON = 45_000_000
LOW_INCOME_RATE_PER_MILLE = 165
STANDARD_RATE_PER_MILLE = 132

_AMOUNT = (
    r"(?P<amount>(?:0|[1-9][0-9]{0,2}(?:,[0-9]{3})+|[1-9][0-9]*))\s*"
    r"(?P<unit>억원|천만원|만원|원)"
)
_ACCOUNT_AMOUNT = re.compile(
    rf"(?P<account>연금저축(?:계좌)?|IRP)\s*"
    rf"(?:(?:에만|에는|에|으로만|으로는|으로|은|는)\s*)?"
    rf"(?:(?:연간|올해)\s*)?"
    rf"(?:(?:납입|입금|불입|적립)(?:한|할|하는|액)?\s*)?"
    rf"(?:금액\s*)?(?:은|는|이|가|을|를|:|=)?\s*{_AMOUNT}",
    flags=re.IGNORECASE,
)
_AMOUNT_ACCOUNT = re.compile(
    rf"{_AMOUNT}\s*(?:을|를)?\s*"
    rf"(?P<account>연금저축(?:계좌)?|IRP)\s*(?:에|으로)\s*"
    rf"(?:납입|입금|불입|적립)",
    flags=re.IGNORECASE,
)
_INCOME_AMOUNT = re.compile(
    rf"(?P<basis>총급여(?:액)?|종합소득금액)\s*"
    rf"(?:은|는|이|가|:|=)?\s*{_AMOUNT}",
)
_NON_EXACT_SUFFIX = re.compile(r"^\s*(?:이하|초과|미만|이상|기준|경계)")
_CONTRIBUTION_WORD = re.compile(r"납입|입금|불입|적립|넣")
_TRAILING_CONTRIBUTION_WORD = re.compile(
    r"^\s*(?:을|를|씩)?\s*(?:납입|입금|불입|적립|넣)"
)
_NON_ANNUAL_PERIOD = re.compile(r"매월|매달|월납|개월|분기|매주|매일|[0-9]+\s*년간")
_NON_PERSONAL_CONTRIBUTION = re.compile(
    r"회사(?:가|에서|부담|납입)|고용주|사용자\s*부담금|퇴직금"
)


def _normalize(text: str) -> str:
    return normalize_question_text(text)


def _won(amount: str, unit: str) -> int:
    value = int(amount.replace(",", ""))
    multipliers = {
        "원": 1,
        "만원": 10_000,
        "천만원": 10_000_000,
        "억원": 100_000_000,
    }
    return value * multipliers[unit]


def _has_shared_trailing_contribution(question: str, end: int) -> bool:
    """Accept one trailing verb shared by a short list of account amounts.

    Korean commonly states ``연금저축에 600만원, IRP에 300만원을 넣었다``.
    Requiring the verb beside every amount rejects fully specified inputs.  The
    search is bounded to the same sentence and only runs after an explicit
    account-and-amount pair has already matched.
    """

    suffix = question[end : end + 80]
    suffix = re.split(r"[.!?。！？\n]", suffix, maxsplit=1)[0]
    before_verb = re.split(r"납입|입금|불입|적립|넣", suffix, maxsplit=1)[0]
    if "한도" in before_verb:
        return False
    return _CONTRIBUTION_WORD.search(suffix) is not None


def _account_key(value: str) -> str:
    return "irp" if value.casefold() == "irp" else "pension_savings"


def _account_amounts(question: str) -> dict[str, list[int]]:
    found: dict[str, list[tuple[tuple[int, int], int]]] = {
        "pension_savings": [],
        "irp": [],
    }
    for pattern in (_ACCOUNT_AMOUNT, _AMOUNT_ACCOUNT):
        for match in pattern.finditer(question):
            if pattern is _ACCOUNT_AMOUNT:
                matched_text = match.group(0)
                suffix = question[match.end() : min(len(question), match.end() + 12)]
                if not (
                    _CONTRIBUTION_WORD.search(matched_text)
                    or _TRAILING_CONTRIBUTION_WORD.match(suffix)
                    or _has_shared_trailing_contribution(question, match.end())
                ):
                    continue
            period_window = question[
                max(0, match.start() - 12) : min(len(question), match.end() + 16)
            ]
            if _NON_ANNUAL_PERIOD.search(period_window):
                continue
            key = _account_key(match.group("account"))
            span = match.span()
            value = _won(match.group("amount"), match.group("unit"))
            if not any(existing_span == span for existing_span, _ in found[key]):
                found[key].append((span, value))
    return {
        key: [value for _, value in matches]
        for key, matches in found.items()
    }


def _income_amounts(question: str) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    for match in _INCOME_AMOUNT.finditer(question):
        # A threshold statement such as "5,500만원 이하" is not an exact
        # personal-income input.  Treat it as missing rather than guessing.
        if _NON_EXACT_SUFFIX.match(question[match.end() :]):
            continue
        basis = "total_salary" if match.group("basis").startswith("총급여") else "comprehensive_income"
        found.append((basis, _won(match.group("amount"), match.group("unit"))))
    return found


@dataclass(frozen=True, slots=True)
class PensionTaxCreditCalculation:
    status: str
    missing_inputs: tuple[str, ...]
    income_basis: str | None = None
    income_won: int | None = None
    pension_savings_contribution_won: int | None = None
    irp_contribution_won: int | None = None
    eligible_contribution_won: int | None = None
    rate_per_mille: int | None = None
    calculated_credit_won: int | None = None

    @property
    def performed(self) -> bool:
        return self.status == "calculated"

    def render_settled_rate(self) -> str:
        """State the rate the question already fixes, when the credit needs more.

        doc41 sets the rate by income alone, so an asker who gave their income
        has settled it even without a contribution amount.
        """

        if self.performed or self.rate_per_mille is None or self.income_won is None:
            return ""
        label = _INCOME_BASIS_LABELS.get(self.income_basis, self.income_basis)
        return (
            f"{label} {_won_display(self.income_won)} 기준으로 문서상 적용되는 "
            f"세액공제율은 {self.rate_per_mille / 10:g}%입니다 "
            f"[근거:{SOURCE_ID}:calculator]."
        )

    def render_context(self) -> str:
        quality_note = (
            "source_quality_note=doc41 본문의 최대 절세액 단위 오탈자는 같은 문서의 "
            "표와 산식으로 교차검증해 표의 원 단위를 canonical로 사용; "
            "주최 측 원본은 수정하지 않음"
        )
        common = [
            "[DETERMINISTIC_CALCULATOR]",
            f"calculator={CALCULATOR_VERSION}",
            f"status={self.status}",
            "calculation_performed=" + ("true" if self.performed else "false"),
            f"source_id={SOURCE_ID}",
            f"source_path={SOURCE_PATH}",
            f"citation_id={SOURCE_ID}:calculator",
            "source_effective_date=not_stated_in_doc41",
        ]
        if not self.performed:
            common.extend(
                [
                    "missing_inputs=" + "; ".join(self.missing_inputs),
                    "instruction=개인별 계산값을 만들지 말고 missing_inputs만 확인할 것",
                    quality_note,
                    "[SOURCE doc41]",
                    (
                        "basis=연금저축 세액공제 대상 납입액은 연 600만 원 이내, "
                        "IRP를 포함한 연금계좌 합산은 연 900만 원 이내"
                    ),
                    (
                        "basis=총급여 5,500만 원 또는 종합소득금액 4,500만 원을 "
                        "경계로 문서상 16.5%/13.2% 적용"
                    ),
                ]
            )
            if self.rate_per_mille is not None and self.income_won is not None:
                label = _INCOME_BASIS_LABELS.get(self.income_basis, self.income_basis)
                common.append(
                    f"applicable_rate_percent={self.rate_per_mille / 10:g} "
                    f"({label} {_won_display(self.income_won)} 구간)"
                )
            return "\n".join(common)

        assert self.income_basis is not None
        assert self.income_won is not None
        assert self.pension_savings_contribution_won is not None
        assert self.irp_contribution_won is not None
        assert self.eligible_contribution_won is not None
        assert self.rate_per_mille is not None
        assert self.calculated_credit_won is not None
        # The key=value lines carry the figures only in bare digits, so a
        # sentence stating "세액공제액은 1,485,000원입니다" had no won-unit value
        # to bind against and too few shared words for the rule-relation check.
        # This line restates the same computed result in the wording an answer
        # uses, which keeps the calculator the citable source for its own
        # output instead of pushing the claim onto the prose chunk.
        result_sentence = (
            f"result={_INCOME_BASIS_LABELS.get(self.income_basis, self.income_basis)}"
            f" {_won_display(self.income_won)} 기준 "
            f"연금저축·IRP 합산 납입 {_won_display(self.eligible_contribution_won)}의 "
            f"세액공제액은 {_won_display(self.calculated_credit_won)}"
            f"(={_won_display(self.eligible_contribution_won)} × "
            f"{self.rate_per_mille / 10:g}%)"
        )
        input_sentence = (
            f"input_summary={_INCOME_BASIS_LABELS.get(self.income_basis, self.income_basis)} "
            f"{_won_display(self.income_won)}, "
            f"연금저축 납입액 {_won_display(self.pension_savings_contribution_won)}, "
            f"IRP 납입액 {_won_display(self.irp_contribution_won)}"
        )
        common.extend(
            [
                "canonical_doc41_max_credit_won=1485000",
                f"income_basis={self.income_basis}",
                f"income_won={self.income_won}",
                f"pension_savings_contribution_won={self.pension_savings_contribution_won}",
                f"irp_contribution_won={self.irp_contribution_won}",
                f"eligible_contribution_won={self.eligible_contribution_won}",
                f"credit_rate_percent={self.rate_per_mille / 10:g}",
                f"calculated_tax_credit_won={self.calculated_credit_won}",
                (
                    "formula=min(min(연금저축납입액,6000000)+IRP납입액,9000000)"
                    f"×{self.rate_per_mille}/1000"
                ),
                (
                    "limitation=계산값은 문서상 최대 세액공제액이며 실제 세금 감소액은 "
                    "공제 전 납부(차감)할 세액을 넘지 않음; 해당 세액이 더 작으면 그 금액으로 제한"
                ),
                input_sentence,
                "rounding=원 미만 절사(계산기 표시 규칙; doc41의 세법상 절사 규정이라는 뜻은 아님)",
                quality_note,
                "instruction=이 결정론적 결과를 재계산하거나 다른 세제 규칙을 덧붙이지 말 것",
                # Placed after the markers `_calculation_markers` requires so a
                # tight context budget drops this readable restatement first
                # rather than a required marker.
                result_sentence,
                "canonical_doc41_max_credit=1,485,000원",
                "[SOURCE doc41]",
                (
                    "basis=연금저축 연 600만 원, IRP 포함 합산 연 900만 원 한도; "
                    "총급여 5,500만 원/종합소득금액 4,500만 원 이하 16.5%, 초과 13.2%"
                ),
            ]
        )
        return "\n".join(common)

    def render_answer(self) -> str:
        """Render a complete, cited answer for a successfully parsed calculation.

        The calculator already owns every number and rule used below.  Keeping
        this rendering deterministic provides a safe fallback when an HCX draft
        is factually supported but cannot satisfy the citation contract.  The
        caller still validates this text against the public calculator block.
        """

        if not self.performed:
            raise ValueError("a calculation answer requires status=calculated")
        assert self.income_basis is not None
        assert self.income_won is not None
        assert self.pension_savings_contribution_won is not None
        assert self.irp_contribution_won is not None
        assert self.eligible_contribution_won is not None
        assert self.rate_per_mille is not None
        assert self.calculated_credit_won is not None

        income_label = _INCOME_BASIS_LABELS.get(
            self.income_basis, self.income_basis
        )
        threshold = (
            SALARY_THRESHOLD_WON
            if self.income_basis == "total_salary"
            else COMPREHENSIVE_INCOME_THRESHOLD_WON
        )
        bracket = "이하" if self.income_won <= threshold else "초과"
        rate = self.rate_per_mille / 10
        citation = f"[근거:{SOURCE_ID}:calculator]"
        return "\n".join(
            (
                (
                    f"입력값은 {income_label} {_won_display(self.income_won)}, "
                    f"연금저축 납입액 {_won_display(self.pension_savings_contribution_won)}, "
                    f"IRP 납입액 {_won_display(self.irp_contribution_won)}입니다 {citation}."
                ),
                (
                    f"문서 기준 {income_label} {_manwon_display(threshold)} {bracket} 구간이므로 "
                    f"공제율 {rate:g}%를 적용합니다 {citation}."
                ),
                (
                    f"연금저축은 {_won_display(PENSION_SAVINGS_LIMIT_WON)}까지만 반영하고 "
                    f"IRP와 합산해 {_won_display(COMBINED_LIMIT_WON)} 한도를 적용한 "
                    f"세액공제 대상 납입액은 {_won_display(self.eligible_contribution_won)}입니다 "
                    f"{citation}."
                ),
                (
                    f"계산은 {_won_display(self.eligible_contribution_won)} × {rate:g}%이며, "
                    f"문서상 최대 세액공제액은 {_won_display(self.calculated_credit_won)}입니다 "
                    f"{citation}."
                ),
                (
                    "실제 세금 감소액은 공제 전 납부하거나 차감할 세액을 넘지 않으며, "
                    f"해당 세액이 더 작으면 그 금액으로 제한됩니다 {citation}."
                ),
            )
        )

    def trace_fields(self) -> dict[str, object]:
        trace: dict[str, object] = {
            "name": "pension_tax_credit_calculator",
            "version": CALCULATOR_VERSION,
            "status": self.status,
            "source_id": SOURCE_ID,
            "calculation_performed": self.performed,
        }
        if self.performed:
            trace.update(
                {
                    "eligible_contribution_won": self.eligible_contribution_won,
                    "rate_percent": self.rate_per_mille / 10 if self.rate_per_mille else None,
                    "calculated_credit_won": self.calculated_credit_won,
                    "tax_liability_limit_disclosed": True,
                }
            )
        else:
            trace["missing_inputs"] = list(self.missing_inputs)
        return trace


_INCOME_BASIS_LABELS = {
    "total_salary": "총급여",
    "comprehensive_income": "종합소득금액",
}


def _won_display(value: int) -> str:
    """Format a won amount the way an answer sentence writes it."""

    return f"{value:,}원"


def _manwon_display(value: int) -> str:
    """Preserve doc41's 만 원 unit for its two income thresholds."""

    return f"{value // 10_000:,}만 원"


def _rate_per_mille(income_basis: str | None, income_won: int | None) -> int | None:
    """The credit rate, which doc41 sets by income alone."""

    if income_basis is None or income_won is None:
        return None
    threshold = (
        SALARY_THRESHOLD_WON
        if income_basis == "total_salary"
        else COMPREHENSIVE_INCOME_THRESHOLD_WON
    )
    return (
        LOW_INCOME_RATE_PER_MILLE
        if income_won <= threshold
        else STANDARD_RATE_PER_MILLE
    )


def calculate_pension_tax_credit(question: str) -> PensionTaxCreditCalculation:
    """Calculate only the doc41 pension-savings/IRP tax-credit scenario.

    The parser intentionally accepts only Arabic-numeral amounts followed by
    ``원`` or ``만원`` and explicit account/income labels.  Anything less
    specific fails closed to a list of required inputs.
    """

    normalized = _normalize(question)
    amounts = _account_amounts(normalized)
    incomes = _income_amounts(normalized)
    has_pension_savings = "연금저축" in normalized
    has_irp = bool(re.search(r"(?<![A-Za-z0-9])IRP(?![A-Za-z0-9])", normalized, re.IGNORECASE))

    missing: list[str] = []
    pension_savings: int | None = None
    irp: int | None = None

    if _NON_PERSONAL_CONTRIBUTION.search(normalized):
        missing.append("본인이 세액공제 대상으로 납입한 계좌별 금액")
    elif not has_pension_savings and not has_irp:
        missing.append("연금저축 또는 IRP의 계좌별 납입액")
    elif has_pension_savings and has_irp:
        if len(amounts["pension_savings"]) != 1 or len(amounts["irp"]) != 1:
            missing.append("연금저축 납입액과 IRP 납입액을 각각 구분한 금액")
        else:
            pension_savings = amounts["pension_savings"][0]
            irp = amounts["irp"][0]
    elif has_pension_savings:
        if len(amounts["pension_savings"]) != 1:
            missing.append("연금저축 납입액")
        else:
            pension_savings = amounts["pension_savings"][0]
            irp = 0
    else:
        if len(amounts["irp"]) != 1:
            missing.append("IRP 납입액")
        else:
            pension_savings = 0
            irp = amounts["irp"][0]

    income_basis: str | None = None
    income_won: int | None = None
    if len(incomes) != 1:
        missing.append("총급여 또는 종합소득금액 중 하나의 정확한 금액")
    else:
        income_basis, income_won = incomes[0]

    if missing:
        # The credit needs a contribution, but the rate does not: it follows
        # from income alone. Dropping the income here left "총급여 7천만원이면
        # 세액공제율이 몇 퍼센트인가요" with no bracket at all, and the answer
        # quoted the 16.5% row that applies to the other side of the threshold.
        return PensionTaxCreditCalculation(
            status="needs_input",
            missing_inputs=tuple(missing),
            income_basis=income_basis,
            income_won=income_won,
            rate_per_mille=_rate_per_mille(income_basis, income_won),
        )

    assert pension_savings is not None
    assert irp is not None
    assert income_basis is not None
    assert income_won is not None
    rate = _rate_per_mille(income_basis, income_won)
    assert rate is not None
    eligible = min(
        min(pension_savings, PENSION_SAVINGS_LIMIT_WON) + irp,
        COMBINED_LIMIT_WON,
    )
    credit = eligible * rate // 1_000
    return PensionTaxCreditCalculation(
        status="calculated",
        missing_inputs=(),
        income_basis=income_basis,
        income_won=income_won,
        pension_savings_contribution_won=pension_savings,
        irp_contribution_won=irp,
        eligible_contribution_won=eligible,
        rate_per_mille=rate,
        calculated_credit_won=credit,
    )
