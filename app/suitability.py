from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from app.product_facts import EvidenceBlock, ProductEvidenceIndex, ProductEvidenceRecord
from app.product_values import normalize_date
from app.routing import normalize_question_text


SUITABILITY_VERSION = "pension-suitability-prefilter-v6"
_YEAR_RE = re.compile(r"(?<!\d)(\d{1,2})\s*년")
_RISK_GRADE_RE = re.compile(r"(?<!\d)([1-6])\s*등급")
_MAX_CANDIDATES = 3

_FIELD_LABELS = {
    "account": "계좌 유형",
    "horizon": "투자기간 또는 은퇴 시점",
    "risk": "손실 감내 능력과 위험 선호",
    "liquidity": "유동성 또는 중도인출 계획",
    "objective": "노후자금 목표",
}


def _normalize(value: str) -> str:
    return normalize_question_text(value)


def _matches_any(text: str, terms: Iterable[str]) -> bool:
    return any(_normalize(term) in text for term in terms)


def _explicit_horizon_years(text: str) -> tuple[int, ...]:
    """Return years explicitly tied to investing, holding, or retirement.

    A recommendation can mention a 20-year investment horizon and a withdrawal
    needed within one year. Treating every ``N년`` token as a horizon creates a
    false conflict, so the local context around each value must identify it as
    the investment horizon.
    """

    values: list[int] = []
    for match in _YEAR_RE.finditer(text):
        before = text[max(0, match.start() - 16) : match.start()]
        after = text[match.end() : match.end() + 18]
        if _matches_any(
            before,
            (
                "투자기간",
                "투자 기간",
                "보유기간",
                "보유 기간",
                "은퇴 시점",
                # Colloquial ways of stating the same horizon. "은퇴까지 20년"
                # carries the horizon in the prefix rather than a suffix verb.
                "은퇴까지",
                "퇴직까지",
                "은퇴할 때까지",
                "정년까지",
                "만기까지",
            ),
        ) or _matches_any(
            after,
            (
                "투자",
                "운용",
                "보유",
                "은퇴",
                # "20년 남았", "20년 넘게 굴릴", "20년 묵혀" describe the holding
                # period without using the official term.
                "남았",
                "남아",
                "굴리",
                "굴릴",
                "묵혀",
                "넣어둘",
                "넣어둬",
            ),
        ):
            values.append(int(match.group(1)))
    return tuple(values)


@dataclass(frozen=True, slots=True)
class SuitabilityProfile:
    account: str | None
    horizon: str | None
    horizon_years: int | None
    risk_tolerance: str | None
    liquidity: str | None
    objective: str | None
    missing_fields: tuple[str, ...]
    conflicts: tuple[str, ...]

    def trace_fields(self) -> dict[str, object]:
        return {
            "account": self.account,
            "horizon": self.horizon,
            "risk_tolerance": self.risk_tolerance,
            "liquidity": self.liquidity,
            "objective": self.objective,
            "missing_fields": list(self.missing_fields),
            "conflicts": list(self.conflicts),
        }


@dataclass(frozen=True, slots=True)
class SuitabilityCandidate:
    product_code: str
    canonical_source_id: str
    title: str
    risk_grade: int
    risk_label: str
    category: str
    basis_date: str
    account_citation: str
    risk_citation: str
    category_citation: str
    account_excerpt: str
    risk_excerpt: str
    category_excerpt: str
    match_reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SuitabilityAssessment:
    version: str
    status: str
    profile: SuitabilityProfile
    required_conditions: tuple[str, ...]
    candidates: tuple[SuitabilityCandidate, ...]
    evaluated_products: int
    eligible_products: int
    policy_limits: tuple[str, ...]

    @property
    def candidate_codes(self) -> tuple[str, ...]:
        return tuple(item.product_code for item in self.candidates)

    def trace_fields(self) -> dict[str, object]:
        return {
            "name": "suitability_prefilter",
            "version": self.version,
            "status": self.status,
            "profile": self.profile.trace_fields(),
            "required_conditions": list(self.required_conditions),
            "evaluated_products": self.evaluated_products,
            "eligible_products": self.eligible_products,
            "shortlisted_products": len(self.candidates),
            "policy_limits": list(self.policy_limits),
        }

    def render_context(self) -> str:
        if not self.candidates:
            return ""
        lines = [
            "[SUITABILITY PREFILTER]",
            f"version={self.version}",
            "decision_scope=organizer-corpus conditional candidate shortlist; "
            "not a statutory suitability determination or universal product ranking",
            "selection_policy=account-eligible class + stated risk ceiling + stated objective; "
            "no return forecast or unstated customer facts",
        ]
        for candidate in self.candidates:
            lines.append(
                "[SUITABILITY CANDIDATE] "
                f"product_code={candidate.product_code} "
                f"canonical_source_id={candidate.canonical_source_id} "
                f"risk_grade={candidate.risk_grade} risk_label={candidate.risk_label} "
                f"category={candidate.category} basis_date={candidate.basis_date} "
                f"account_citation={candidate.account_citation} "
                f"risk_citation={candidate.risk_citation} "
                f"category_citation={candidate.category_citation} "
                f"match_reasons={','.join(candidate.match_reasons)}"
            )
            lines.extend(
                (
                    f"[SUITABILITY EVIDENCE] citation_id={candidate.account_citation}\n"
                    f"{candidate.account_excerpt}",
                    f"[SUITABILITY EVIDENCE] citation_id={candidate.risk_citation}\n"
                    f"{candidate.risk_excerpt}",
                    f"[SUITABILITY EVIDENCE] citation_id={candidate.category_citation}\n"
                    f"{candidate.category_excerpt}",
                )
            )
        lines.extend(f"policy_limit={item}" for item in self.policy_limits)
        return "\n".join(lines)


def _matches_any_unnegated(text: str, terms: Iterable[str]) -> bool:
    """Match a term only where it is not immediately denied.

    Every negative phrasing contains its positive form: "중도인출 계획은 없고"
    contains "중도인출 계획". Testing the positive list directly therefore made
    each denial look like both an assertion and its negation.
    """

    for term in terms:
        needle = _normalize(term)
        start = 0
        while True:
            position = text.find(needle, start)
            if position < 0:
                break
            following = text[position + len(needle) : position + len(needle) + 12]
            if not re.search(r"없|않|아니|불필요", following):
                return True
            start = position + len(needle)
    return False


def _single_value(
    matches: dict[str, bool],
    *,
    field: str,
    conflicts: list[str],
) -> str | None:
    values = tuple(name for name, matched in matches.items() if matched)
    if len(values) > 1:
        conflicts.append(f"{_FIELD_LABELS[field]} 진술 충돌")
        return None
    return values[0] if values else None


def parse_suitability_profile(question: str) -> SuitabilityProfile:
    """Extract only explicitly stated recommendation conditions.

    This parser never fills demographic, financial, or behavioral defaults.
    Its output is a competition-oriented prefilter input, not a legal customer
    suitability questionnaire.
    """

    text = _normalize(question)
    conflicts: list[str] = []
    account = _single_value(
        {
            "irp": "irp" in text,
            "dc": bool(re.search(r"(?<![a-z0-9])dc(?:형)?(?![a-z0-9])", text)),
            "db": bool(re.search(r"(?<![a-z0-9])db(?:형)?(?![a-z0-9])", text)),
            "pension_savings": _matches_any(text, ("연금저축", "개인연금")),
        },
        field="account",
        conflicts=conflicts,
    )
    if account is None and "퇴직연금" in text:
        conflicts.append("퇴직연금의 DB·DC·IRP 세부 유형 미확정")

    years = _explicit_horizon_years(text)
    horizon_years = years[0] if len(set(years)) == 1 else None
    if len(set(years)) > 1:
        conflicts.append("투자기간 진술 충돌")
    if horizon_years is not None:
        horizon = "short" if horizon_years <= 3 else "medium" if horizon_years <= 10 else "long"
    else:
        horizon = _single_value(
            {
                "short": _matches_any(text, ("단기 투자", "짧은 기간")),
                "long": _matches_any(text, ("장기 투자", "은퇴 시점까지")),
            },
            field="horizon",
            conflicts=conflicts,
        )

    # Negated tolerance must be matched as "conservative" first. ``aggressive``
    # keys on shorter fragments such as "손실은 감수", which are also contained
    # in "손실은 감수할 수 없습니다"; testing conservative first and gating
    # aggressive on ``not conservative`` keeps the negated form from flipping.
    conservative = _matches_any(
        text,
        (
            "안정형",
            "안정적",
            "보수적",
            "손실을 감내할 수 없",
            "손실 감내가 어려",
            "위험을 원하지 않",
            "원금 보전",
            "원금보전",
            "감내할 수 없",
            "감내하기 어려",
            "감수할 수 없",
            "감수하기 어려",
            "감수하고 싶지 않",
            "잃고 싶지 않",
            "원금은 지키",
            "원금을 지키",
            "손실은 피하",
            "손실을 피하",
        ),
    )
    aggressive = not conservative and _matches_any(
        text,
        (
            "공격형",
            "공격적",
            "적극적",
            "높은 위험",
            "손실 위험을 감수",
            "손실을 감내할 수 있",
            "변동성을 감수",
            # Colloquial acceptance of downside risk.
            "손실은 괜찮",
            "손실이 나도 괜찮",
            "어느 정도 손실",
            "원금손실은 감수",
            "원금 손실은 감수",
            "손실은 감수",
            "손실을 감수",
            "위험은 감수",
            "위험을 감수",
        ),
    )
    risk = _single_value(
        {
            "conservative": conservative,
            "balanced": _matches_any(text, ("중립형", "중립적", "보통 위험")),
            "aggressive": aggressive,
        },
        field="risk",
        conflicts=conflicts,
    )

    no_liquidity = _matches_any(
        text,
        (
            "중도인출 계획은 없",
            "중도 인출 계획은 없",
            "중도인출 계획이 없",
            "중도 인출 계획이 없",
            "유동성은 필요 없",
            "긴급자금은 필요 없",
            # Colloquial ways of saying the money will not be touched.
            "중간에 뺄 일은 없",
            "중간에 뺄 일이 없",
            "중간에 찾을 일은 없",
            "중간에 찾을 일이 없",
            "중간에 뺄 계획은 없",
            "중간에 찾을 계획은 없",
            "인출할 계획은 없",
            "인출할 계획이 없",
            "인출 계획은 없",
            "인출 계획이 없",
            # A bare noun phrase drops the topic particle: "중도인출 계획 없음"
            # carries the same meaning, but without these it matched the
            # "needed" list below on "중도인출 계획" and inverted the answer —
            # recommending liquid products to someone who said the opposite.
            "인출 계획 없",
            "인출계획 없",
            "중도인출 계획 없",
            "중도 인출 계획 없",
            "유동성 필요 없",
            "긴급자금 필요 없",
            "긴급 자금 필요 없",
        ),
    )
    needs_liquidity = _matches_any_unnegated(
        text,
        (
            "중도인출 계획",
            "중도 인출 계획",
            "유동성 필요",
            "긴급자금",
            "긴급 자금",
            "현금화 필요",
        ),
    )
    if no_liquidity and needs_liquidity:
        # "중도인출 계획은 없지만 긴급자금이 필요합니다" states both. Silently
        # keeping "not_needed" would steer the shortlist toward illiquid
        # products on a condition the customer contradicted, so ask instead.
        liquidity = None
        conflicts.append(f"{_FIELD_LABELS['liquidity']} 진술 충돌")
    else:
        liquidity = "not_needed" if no_liquidity else ("needed" if needs_liquidity else None)

    objective = _single_value(
        {
            "growth": _matches_any(
                text,
                (
                    "노후자금 성장",
                    "자산 성장",
                    "수익 추구",
                    "장기 성장",
                    "성장 목표",
                    "성장을 원",
                    # "노후에 쓸 돈을 불리고 싶다" states a growth objective
                    # without using the official term. Bare "노후 생활비" is
                    # deliberately excluded: it does not distinguish growth
                    # from income and must stay an unresolved condition.
                    "불리고 싶",
                    "불리려",
                    "늘리고 싶",
                    "키우고 싶",
                    "자산을 불리",
                ),
            ),
            "preservation": _matches_any(
                text,
                ("원금 보전", "원금보전", "자본 보전", "안정적 운용"),
            ),
            "income": _matches_any(
                text,
                ("현금흐름", "현금 흐름", "소득 창출", "월배당", "배당 소득"),
            ),
        },
        field="objective",
        conflicts=conflicts,
    )

    values = {
        "account": account,
        "horizon": horizon,
        "risk": risk,
        "liquidity": liquidity,
        "objective": objective,
    }
    missing = tuple(_FIELD_LABELS[name] for name, value in values.items() if value is None)
    return SuitabilityProfile(
        account=account,
        horizon=horizon,
        horizon_years=horizon_years,
        risk_tolerance=risk,
        liquidity=liquidity,
        objective=objective,
        missing_fields=missing,
        conflicts=tuple(dict.fromkeys(conflicts)),
    )


def _first_matching_block(
    blocks: tuple[EvidenceBlock, ...], terms: tuple[str, ...]
) -> EvidenceBlock | None:
    return next(
        (
            block
            for block in blocks
            if any(_normalize(term) in _normalize(block.raw_text) for term in terms)
        ),
        None,
    )


def _risk(record: ProductEvidenceRecord) -> tuple[int, EvidenceBlock] | None:
    for block in record.evidence["risk"]:
        match = _RISK_GRADE_RE.search(_normalize(block.raw_text))
        if match:
            return int(match.group(1)), block
    return None


def _category(record: ProductEvidenceRecord) -> tuple[str, EvidenceBlock] | None:
    for block in record.evidence["category"]:
        text = _normalize(f"{record.title}\n{block.raw_text}")
        if _matches_any(text, ("tdf", "타깃데이트", "타겟데이트")):
            return "target_date", block
        if _matches_any(text, ("혼합형", "채권혼합", "주식혼합")):
            return "mixed", block
        if _matches_any(text, ("주식형", "[주식]", "(주식)")):
            return "equity", block
        if _matches_any(text, ("채권형", "[채권]", "(채권)")):
            return "bond", block
    return None


def _risk_allowed(profile: SuitabilityProfile, grade: int) -> bool:
    return grade in {
        "conservative": {5, 6},
        "balanced": {3, 4, 5, 6},
        "aggressive": {1, 2, 3, 4, 5, 6},
    }.get(profile.risk_tolerance or "", set())


def _objective_allowed(profile: SuitabilityProfile, category: str, title: str) -> bool:
    if profile.objective == "growth":
        return category in {"equity", "mixed", "target_date"}
    if profile.objective == "preservation":
        return category == "bond"
    if profile.objective == "income":
        normalized = _normalize(title)
        return "배당" in normalized or "인컴" in normalized
    return False


def _account_block(
    profile: SuitabilityProfile, record: ProductEvidenceRecord
) -> EvidenceBlock | None:
    terms = (
        ("퇴직연금", "irp")
        if profile.account in {"irp", "dc"}
        else ("연금저축", "개인연금")
    )
    return _first_matching_block(record.evidence["class"], terms)


def _risk_label(grade: int) -> str:
    return {
        1: "very_high",
        2: "high",
        3: "moderately_high",
        4: "normal",
        5: "low",
        6: "very_low",
    }[grade]


def _basis_date_key(record: ProductEvidenceRecord) -> int:
    normalized = tuple(
        item
        for raw in record.basis_dates
        if (item := normalize_date(raw)) is not None
    )
    return max((int(item.replace("-", "")) for item in normalized), default=0)


def _excerpt(block: EvidenceBlock, *, terms: tuple[str, ...]) -> str:
    lines = tuple(line.strip() for line in block.raw_text.splitlines() if line.strip())
    matching = tuple(
        line
        for line in lines
        if any(_normalize(term) in _normalize(line) for term in terms)
    )
    selected = matching[:2] or lines[:2]
    rendered = " ".join(selected)
    return rendered[:400].rstrip()


def assess_suitability(
    *,
    question: str,
    index: ProductEvidenceIndex | None,
    requested_product_codes: tuple[str, ...] = (),
) -> SuitabilityAssessment:
    profile = parse_suitability_profile(question)
    limits = (
        "법정 적합성·적정성 판정이 아니며 사용자 진술과 주최 측 코퍼스만 사용",
        "과거 수익률로 미래 성과를 예측하지 않음",
        "상품코드-판매 클래스 매핑이 없어 후보 간 보수·성과 순위를 산출하지 않음",
        "판매 가능 여부·실제 투자자정보 확인·가입 실행을 수행하지 않음",
    )
    conditions = [*profile.missing_fields, *profile.conflicts]
    if profile.account == "db":
        conditions.append("DB형에서 개인이 운용상품을 선택하는 상황인지 확인")
    if profile.liquidity == "needed":
        conditions.append("필요 인출 시점과 법정 중도인출 사유 해당 여부")
    conditions = list(dict.fromkeys(conditions))
    if conditions:
        return SuitabilityAssessment(
            version=SUITABILITY_VERSION,
            status="needs_conditions",
            profile=profile,
            required_conditions=tuple(conditions),
            candidates=(),
            evaluated_products=0,
            eligible_products=0,
            policy_limits=limits,
        )
    if index is None:
        return SuitabilityAssessment(
            version=SUITABILITY_VERSION,
            status="out_of_corpus",
            profile=profile,
            required_conditions=(),
            candidates=(),
            evaluated_products=0,
            eligible_products=0,
            policy_limits=limits,
        )

    requested = set(requested_product_codes)
    records = tuple(
        record
        for record in index.records
        if not requested or record.product_code in requested
    )
    # Alias codes share the same evidence. Keep one canonical candidate so the
    # shortlist cannot present duplicate PDFs as independent alternatives.
    canonical: dict[str, ProductEvidenceRecord] = {}
    for record in records:
        canonical.setdefault(record.canonical_source_id, record)

    eligible: list[SuitabilityCandidate] = []
    target_grade = {"conservative": 6, "balanced": 4, "aggressive": 2}[
        profile.risk_tolerance or "balanced"
    ]
    for record in canonical.values():
        basis_key = _basis_date_key(record)
        account_block = _account_block(profile, record)
        risk = _risk(record)
        category = _category(record)
        if basis_key == 0 or account_block is None or risk is None or category is None:
            continue
        grade, risk_block = risk
        category_name, category_block = category
        if not _risk_allowed(profile, grade):
            continue
        if not _objective_allowed(profile, category_name, record.title):
            continue
        reasons = (
            f"account_class:{profile.account}",
            f"risk_within_stated_ceiling:{grade}",
            f"objective_category:{category_name}",
            f"horizon:{profile.horizon}",
            "liquidity:not_needed",
        )
        eligible.append(
            SuitabilityCandidate(
                product_code=record.product_code,
                canonical_source_id=record.canonical_source_id,
                title=record.title,
                risk_grade=grade,
                risk_label=_risk_label(grade),
                category=category_name,
                basis_date=record.basis_dates[-1] if record.basis_dates else "unknown",
                account_citation=(
                    f"product:{record.product_code}:class:page={account_block.page}"
                ),
                risk_citation=f"product:{record.product_code}:risk:page={risk_block.page}",
                category_citation=(
                    f"product:{record.product_code}:category:page={category_block.page}"
                ),
                account_excerpt=_excerpt(
                    account_block,
                    terms=("퇴직연금", "irp", "연금저축", "개인연금"),
                ),
                risk_excerpt=_excerpt(risk_block, terms=(f"{grade}등급", "위험")),
                category_excerpt=_excerpt(
                    category_block,
                    terms=("운용자산별", "주식형", "채권형", "혼합형"),
                ),
                match_reasons=reasons,
            )
        )
    eligible.sort(
        key=lambda item: (
            abs(item.risk_grade - target_grade),
            item.risk_grade,
            -int((normalize_date(item.basis_date) or "0000-00-00").replace("-", "")),
            item.product_code,
        )
    )
    candidates = tuple(eligible[:_MAX_CANDIDATES])
    return SuitabilityAssessment(
        version=SUITABILITY_VERSION,
        status="candidate_shortlist" if candidates else "out_of_corpus",
        profile=profile,
        required_conditions=(),
        candidates=candidates,
        evaluated_products=len(canonical),
        eligible_products=len(eligible),
        policy_limits=limits,
    )
