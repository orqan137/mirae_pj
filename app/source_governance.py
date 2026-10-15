from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any


SOURCE_GOVERNANCE_VERSION = "pension-source-governance-v4"
_DATE_RE = re.compile(
    r"(?<!\d)(?P<year>19\d{2}|20\d{2})"
    r"(?:[./-]|\s*년\s*)"
    r"(?P<month>0?[1-9]|1[0-2])?"
    r"(?:[./-]|\s*월\s*)?"
    r"(?P<day>0?[1-9]|[12]\d|3[01])?\s*일?"
)
_YEAR_RE = re.compile(r"(?<!\d)(19\d{2}|20\d{2})\s*년")
_UNRESOLVED_TIME_TERMS = ("당시", "그때", "개정 전", "개정 후")
_CURRENT_RULE_TERMS = ("지금", "현재", "현행", "요즘")
_FOREIGN_JURISDICTIONS = {
    "미국": "US",
    "일본": "JP",
    "영국": "GB",
    "프랑스": "FR",
    "독일": "DE",
    "캐나다": "CA",
    "호주": "AU",
    "oecd": "OECD",
}


class SourceGovernanceError(RuntimeError):
    """Raised when the source-governance registry is incomplete or malformed."""


@dataclass(frozen=True, slots=True)
class SourceGovernanceRecord:
    source_id: str
    authority: str
    jurisdiction: str
    audiences: tuple[str, ...]
    published_date: date | None
    reference_date: date | None
    effective_from: date | None
    effective_to: date | None
    date_precision: str | None
    required_query_terms_any: tuple[str, ...]
    knowledge_as_of: date

    @property
    def effective_date_known(self) -> bool:
        return self.effective_from is not None or self.effective_to is not None

    def context_fields(self) -> str:
        def value(item: date | None) -> str:
            return item.isoformat() if item is not None else "unknown"

        return (
            f" authority={self.authority} jurisdiction={self.jurisdiction}"
            f" audiences={','.join(self.audiences)}"
            f" reference_date={value(self.reference_date)}"
            f" effective_from={value(self.effective_from)}"
            f" effective_to={value(self.effective_to)}"
            f" knowledge_as_of={self.knowledge_as_of.isoformat()}"
        )


@dataclass(frozen=True, slots=True)
class QuestionScope:
    reference_date: date | None
    explicit_date: bool
    temporal_unresolved: bool
    jurisdiction: str
    audience: str


@dataclass(frozen=True, slots=True)
class ApplicabilityDecision:
    applicable: bool
    reason: str
    warning: str | None = None


@dataclass(frozen=True, slots=True)
class SourceGovernanceRegistry:
    version: str
    knowledge_as_of: date
    records: dict[str, SourceGovernanceRecord]

    def record_for(self, source_id: str) -> SourceGovernanceRecord | None:
        return self.records.get(source_id)


def _normalized(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _date(value: Any, *, field: str) -> date | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise SourceGovernanceError(f"{field} must be an ISO date or null")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise SourceGovernanceError(f"{field} is not a valid ISO date") from exc


def _string_tuple(value: Any, *, field: str, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise SourceGovernanceError(f"{field} must be an array of non-empty strings")
    result = tuple(value)
    if not allow_empty and not result:
        raise SourceGovernanceError(f"{field} must not be empty")
    if len(set(result)) != len(result):
        raise SourceGovernanceError(f"{field} contains duplicates")
    return result


def load_source_governance(
    path: Path,
    *,
    expected_source_ids: set[str] | None = None,
) -> SourceGovernanceRegistry:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SourceGovernanceError(f"source governance registry is missing: {path}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SourceGovernanceError(f"source governance registry is unreadable: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise SourceGovernanceError("unsupported source governance schema")
    if payload.get("registry_version") != SOURCE_GOVERNANCE_VERSION:
        raise SourceGovernanceError("unexpected source governance version")
    if payload.get("source_scope") != "organizer_provided":
        raise SourceGovernanceError("source governance scope must be organizer_provided")
    default = payload.get("default")
    documents = payload.get("documents")
    overrides = payload.get("overrides")
    if not isinstance(default, dict) or not isinstance(overrides, dict):
        raise SourceGovernanceError("source governance default/overrides must be objects")
    source_ids = _string_tuple(documents, field="documents")
    if expected_source_ids is not None and set(source_ids) != expected_source_ids:
        missing = sorted(expected_source_ids - set(source_ids))
        extra = sorted(set(source_ids) - expected_source_ids)
        raise SourceGovernanceError(
            f"source governance coverage mismatch; missing={missing!r}, extra={extra!r}"
        )
    if not set(overrides) <= set(source_ids):
        raise SourceGovernanceError("source governance override references an unknown source")

    allowed_fields = {
        "authority",
        "jurisdiction",
        "audiences",
        "published_date",
        "reference_date",
        "effective_from",
        "effective_to",
        "date_precision",
        "required_query_terms_any",
    }
    if set(default) != allowed_fields:
        raise SourceGovernanceError("source governance default fields are incomplete")
    knowledge_as_of = _date(payload.get("knowledge_as_of"), field="knowledge_as_of")
    if knowledge_as_of is None:
        raise SourceGovernanceError("knowledge_as_of must not be null")
    records: dict[str, SourceGovernanceRecord] = {}
    for source_id in source_ids:
        override = overrides.get(source_id, {})
        if not isinstance(override, dict) or not set(override) <= allowed_fields:
            raise SourceGovernanceError(f"invalid governance override for {source_id}")
        item = {**default, **override}
        authority = item["authority"]
        jurisdiction = item["jurisdiction"]
        precision = item["date_precision"]
        if not isinstance(authority, str) or not authority:
            raise SourceGovernanceError(f"invalid authority for {source_id}")
        if not isinstance(jurisdiction, str) or not jurisdiction:
            raise SourceGovernanceError(f"invalid jurisdiction for {source_id}")
        if precision not in {None, "day", "month", "year"}:
            raise SourceGovernanceError(f"invalid date precision for {source_id}")
        effective_from = _date(item["effective_from"], field=f"{source_id}.effective_from")
        effective_to = _date(item["effective_to"], field=f"{source_id}.effective_to")
        if effective_from and effective_to and effective_from > effective_to:
            raise SourceGovernanceError(f"inverted effective interval for {source_id}")
        records[source_id] = SourceGovernanceRecord(
            source_id=source_id,
            authority=authority,
            jurisdiction=jurisdiction,
            audiences=_string_tuple(item["audiences"], field=f"{source_id}.audiences"),
            published_date=_date(item["published_date"], field=f"{source_id}.published_date"),
            reference_date=_date(item["reference_date"], field=f"{source_id}.reference_date"),
            effective_from=effective_from,
            effective_to=effective_to,
            date_precision=precision,
            required_query_terms_any=_string_tuple(
                item["required_query_terms_any"],
                field=f"{source_id}.required_query_terms_any",
                allow_empty=True,
            ),
            knowledge_as_of=knowledge_as_of,
        )
    return SourceGovernanceRegistry(
        version=SOURCE_GOVERNANCE_VERSION,
        knowledge_as_of=knowledge_as_of,
        records=records,
    )


def analyze_question_scope(question: str) -> QuestionScope:
    normalized = _normalized(question)
    dates: list[date] = []
    for match in _DATE_RE.finditer(normalized):
        year = int(match.group("year"))
        month = int(match.group("month") or 1)
        day = int(match.group("day") or 1)
        try:
            dates.append(date(year, month, day))
        except ValueError:
            continue
    if not dates:
        dates.extend(date(int(year), 1, 1) for year in _YEAR_RE.findall(normalized))
    unique_dates = tuple(dict.fromkeys(dates))
    # A historical figure can be the premise being challenged rather than the
    # requested answer date: "2023년에는 ... 지금도 그런가요?" asks for the
    # current corpus view.  Present-rule language therefore takes precedence,
    # while the old date remains part of the query for premise correction.
    asks_current_rule = any(term in normalized for term in _CURRENT_RULE_TERMS)
    scoped_dates = () if asks_current_rule else unique_dates
    jurisdiction = "KR"
    for term, code in _FOREIGN_JURISDICTIONS.items():
        if term in normalized:
            jurisdiction = code
            break
    customer_self_identified = bool(
        re.search(
            r"(?:가입자|고객)(?:인데|입니다|예요|이에요|입장에서|로서)",
            normalized,
        )
    )
    operations_self_identified = bool(
        re.search(
            r"(?:사무담당자|업무담당자|창구직원)"
            r"(?:인데|입니다|예요|이에요|입장에서|로서)",
            normalized,
        )
        or re.search(
            r"(?:증권사|금융사|은행)(?:에서|에\s*소속되어).{0,30}"
            r"(?:퇴직연금|연금|irp).{0,30}"
            r"(?:담당(?:합니다|하고\s*있)|맡고\s*있|업무를\s*하고\s*있)",
            normalized,
        )
    )
    employer_self_identified = bool(
        re.search(
            r"(?:사업주|회사 담당자|퇴직연금 담당자)"
            r"(?:인데|입니다|예요|이에요|입장에서|로서)",
            normalized,
        )
        or re.search(
            r"(?:회사|사업장|인사팀|총무팀)(?:에서|에\s*소속되어).{0,30}"
            r"(?:퇴직연금|연금).{0,30}"
            r"(?:담당(?:합니다|하고\s*있)|맡고\s*있|업무를\s*하고\s*있)",
            normalized,
        )
    )
    if customer_self_identified:
        audience = "customer"
    elif operations_self_identified:
        audience = "operations_staff"
    elif employer_self_identified:
        audience = "employer_admin"
    else:
        audience = "customer"
    return QuestionScope(
        reference_date=scoped_dates[0] if len(scoped_dates) == 1 else None,
        explicit_date=bool(scoped_dates),
        temporal_unresolved=(
            len(scoped_dates) > 1
            or (
                not scoped_dates
                and not asks_current_rule
                and any(term in normalized for term in _UNRESOLVED_TIME_TERMS)
            )
        ),
        jurisdiction=jurisdiction,
        audience=audience,
    )


def assess_source_applicability(
    *,
    record: SourceGovernanceRecord,
    scope: QuestionScope,
    question: str,
) -> ApplicabilityDecision:
    if scope.jurisdiction != record.jurisdiction:
        return ApplicabilityDecision(False, "jurisdiction_mismatch")
    if scope.audience not in record.audiences:
        return ApplicabilityDecision(False, "audience_mismatch")
    normalized = _normalized(question)
    if record.required_query_terms_any and not any(
        _normalized(term) in normalized for term in record.required_query_terms_any
    ):
        return ApplicabilityDecision(False, "legacy_scope_mismatch")
    if scope.temporal_unresolved:
        return ApplicabilityDecision(False, "temporal_scope_unresolved")
    if scope.explicit_date:
        assert scope.reference_date is not None
        if not record.effective_date_known:
            return ApplicabilityDecision(False, "effective_date_unknown")
        target = scope.reference_date
    else:
        target = record.knowledge_as_of
    if record.effective_from is not None and target < record.effective_from:
        return ApplicabilityDecision(False, "before_effective_period")
    if record.effective_to is not None and target > record.effective_to:
        return ApplicabilityDecision(False, "after_effective_period")
    warning = None
    if not record.effective_date_known:
        warning = "시행기간 미확인: 코퍼스 검토일 기준 자료 설명만 가능"
    return ApplicabilityDecision(True, "applicable", warning)
