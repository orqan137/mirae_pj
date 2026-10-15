from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.product_facts import EvidenceBlock, ProductEvidenceIndex, ProductEvidenceRecord


PRODUCT_VALUE_PARSER_VERSION = "pension-product-values-v2"
TYPED_VALUE_AXES = frozenset({"fees", "performance", "balance"})

_DATE_TOKEN_RE = re.compile(
    r"(?<!\d)(?:20)?\d{2}\s*(?:[./-]|년\s*)\s*\d{1,2}\s*"
    r"(?:[./-]|월\s*)\s*\d{1,2}(?:\s*일)?(?!\d)"
)
_NUMBER_RE = re.compile(
    r"(?<![\w.])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(?![\w.])"
)
_TABLE_TOKEN_RE = re.compile(
    r"(?<!\S)(?:[-+]?(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)|-)(?!\S)"
)
_INLINE_TOTAL_FEE_RE = re.compile(
    r"총\s*보수\s*[:：]?\s*([-+]?\d+(?:\.\d+)?)\s*%",
    flags=re.IGNORECASE,
)
_CLASS_LABEL_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:class\s*)?"
    r"([A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)+)(?![A-Za-z0-9])",
    flags=re.IGNORECASE,
)
_SIMPLE_CLASS_LABEL_RE = re.compile(
    r"(?:class|클래스|종류)\s*([A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*)"
    r"|([A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*)\s*클래스",
    flags=re.IGNORECASE,
)


def normalize_class_label(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).upper()


def requested_class_labels(value: str) -> tuple[str, ...]:
    """Return normalized sales-class labels named in user or answer text."""

    normalized = unicodedata.normalize("NFKC", value)
    labels = [
        normalize_class_label(match.group(1))
        for match in _CLASS_LABEL_RE.finditer(normalized)
    ]
    labels.extend(
        normalize_class_label(match.group(1) or match.group(2))
        for match in _SIMPLE_CLASS_LABEL_RE.finditer(normalized)
    )
    return tuple(dict.fromkeys(labels))


def normalize_date(value: str) -> str | None:
    normalized = unicodedata.normalize("NFKC", value)
    numbers = [int(part) for part in re.findall(r"\d+", normalized)]
    if len(numbers) != 3:
        return None
    year, month, day = numbers
    if year < 100:
        year += 2000
    if not (2000 <= year <= 2099 and 1 <= month <= 12 and 1 <= day <= 31):
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def _decimal(value: str) -> Decimal | None:
    try:
        return Decimal(value.replace(",", ""))
    except InvalidOperation:
        return None


def _decimal_text(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def _remove_valid_dates(value: str) -> str:
    return _DATE_TOKEN_RE.sub(
        lambda match: " " if normalize_date(match.group(0)) is not None else match.group(0),
        value,
    )


@dataclass(frozen=True, slots=True)
class ValueProvenance:
    source_path: str
    page: int
    excerpt: str

    @property
    def ref(self) -> str:
        return f"{self.source_path}:page={self.page}"


@dataclass(frozen=True, slots=True)
class TypedProductValue:
    product_code: str
    axis: str
    metric: str
    value: Decimal
    unit: str
    class_label: str
    as_of_date: str
    period_years: int | None
    aggregation: str
    tax_basis: str
    provenance: ValueProvenance

    @property
    def alignment_key(self) -> tuple[object, ...]:
        return (
            self.axis,
            self.metric,
            self.unit,
            self.class_label,
            self.as_of_date,
            self.period_years,
            self.aggregation,
            self.tax_basis,
        )

    def render_row(self) -> str:
        period = str(self.period_years) if self.period_years is not None else ""
        return " | ".join(
            (
                self.product_code,
                self.class_label,
                self.metric,
                _decimal_text(self.value),
                self.unit,
                self.as_of_date,
                period,
                self.aggregation,
                self.tax_basis,
                self.provenance.ref,
            )
        )


@dataclass(frozen=True, slots=True)
class ProductValueConflict:
    alignment_key: tuple[object, ...]
    values: tuple[TypedProductValue, ...]

    @property
    def evidence_refs(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(value.provenance.ref for value in self.values))

    @property
    def independently_sourced(self) -> bool:
        return len(self.evidence_refs) > 1


@dataclass(frozen=True, slots=True)
class ProductValueParseResult:
    product_code: str
    axis: str
    class_label: str
    status: str
    values: tuple[TypedProductValue, ...]
    reason: str
    conflicts: tuple[ProductValueConflict, ...] = ()

    def trace_fields(self) -> dict[str, object]:
        return {
            "product_code": self.product_code,
            "axis": self.axis,
            "status": self.status,
            "values": len(self.values),
            "conflicts": len(self.conflicts),
        }


def _class_marker(line: str, requested_class: str) -> bool:
    compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", line)).upper()
    escaped = re.escape(requested_class)
    return bool(
        re.search(rf"\({escaped}\)", compact)
        or re.search(rf"(?:종류|CLASS){escaped}(?![A-Z0-9-])", compact)
    )


def _any_class_marker(line: str) -> bool:
    normalized = unicodedata.normalize("NFKC", line)
    return bool(
        re.search(r"\([A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*\)", normalized)
        or re.search(
            r"(?:종류|Class)\s*[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*",
            normalized,
            flags=re.IGNORECASE,
        )
    )


def _class_positions(lines: list[str], requested_class: str) -> list[int]:
    return [
        position
        for position, line in enumerate(lines)
        if _class_marker(line, requested_class)
    ]


def _nearest_unique_class(
    *,
    row_position: int,
    requested_positions: list[int],
    all_positions: list[int],
    maximum_distance: int,
) -> bool:
    if not requested_positions:
        return False
    nearest_requested = min(abs(row_position - item) for item in requested_positions)
    nearest_any = min(
        (abs(row_position - item) for item in all_positions),
        default=nearest_requested,
    )
    return nearest_requested <= maximum_distance and nearest_requested == nearest_any


def _normalized_basis_dates(record: ProductEvidenceRecord) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            date
            for raw in record.basis_dates
            if (date := normalize_date(raw)) is not None
        )
    )


def _provenance(record: ProductEvidenceRecord, block: EvidenceBlock, line: str) -> ValueProvenance:
    return ValueProvenance(
        source_path=record.source_path,
        page=block.page,
        excerpt=re.sub(r"\s+", " ", line).strip()[:500],
    )


def _fee_values(
    record: ProductEvidenceRecord,
    block: EvidenceBlock,
    requested_class: str,
) -> list[TypedProductValue]:
    raw = unicodedata.normalize("NFKC", block.raw_text)
    if "총보수" not in raw.replace(" ", "") or "%" not in raw:
        return []
    basis_dates = _normalized_basis_dates(record)
    if len(basis_dates) != 1:
        return []
    lines = [line for line in raw.splitlines() if line.strip()]
    requested_positions = _class_positions(lines, requested_class)
    all_positions = [i for i, line in enumerate(lines) if _any_class_marker(line)]
    values: list[TypedProductValue] = []

    # Compact fixtures and unusually clean source rows sometimes state the
    # class and total fee directly. This is authoritative only on the same line.
    for line in lines:
        if not _class_marker(line, requested_class):
            continue
        match = _INLINE_TOTAL_FEE_RE.search(line)
        if match is None:
            continue
        parsed = _decimal(match.group(1))
        if parsed is None or not Decimal("0") <= parsed <= Decimal("20"):
            continue
        values.append(
            TypedProductValue(
                product_code=record.product_code,
                axis="fees",
                metric="annual_total_fee_rate",
                value=parsed,
                unit="percent_per_year",
                class_label=requested_class,
                as_of_date=basis_dates[0],
                period_years=None,
                aggregation="stated_rate",
                tax_basis="not_applicable",
                provenance=_provenance(record, block, line),
            )
        )

    for position, line in enumerate(lines):
        if not _nearest_unique_class(
            row_position=position,
            requested_positions=requested_positions,
            all_positions=all_positions,
            maximum_distance=3,
        ):
            continue
        tokens = _NUMBER_RE.findall(_remove_valid_dates(line))
        parsed_tokens = [_decimal(token) for token in tokens]
        if any(item is None for item in parsed_tokens) or not 8 <= len(parsed_tokens) <= 10:
            continue
        numbers = [item for item in parsed_tokens if item is not None]
        costs = numbers[-5:]
        percent_columns = numbers[:-5]
        if (
            len(percent_columns) not in {3, 4, 5}
            or any(item < 0 or item > 20 for item in percent_columns)
            or any(item < 0 for item in costs)
            or any(left > right for left, right in zip(costs, costs[1:]))
        ):
            continue
        total_fee = percent_columns[1] if len(percent_columns) == 5 else percent_columns[0]
        total_expense = percent_columns[-1]
        for metric, parsed in (
            ("annual_total_fee_rate", total_fee),
            ("annual_total_expense_rate", total_expense),
        ):
            values.append(
                TypedProductValue(
                    product_code=record.product_code,
                    axis="fees",
                    metric=metric,
                    value=parsed,
                    unit="percent_per_year",
                    class_label=requested_class,
                    as_of_date=basis_dates[0],
                    period_years=None,
                    aggregation="stated_rate",
                    tax_basis="not_applicable",
                    provenance=_provenance(record, block, line),
                )
            )
    return values


def _table_tokens_without_dates(line: str) -> list[str]:
    without_dates = _remove_valid_dates(unicodedata.normalize("NFKC", line))
    return _TABLE_TOKEN_RE.findall(without_dates)


def _dominant_table_date(raw: str) -> str | None:
    dates = [
        date
        for match in _DATE_TOKEN_RE.finditer(unicodedata.normalize("NFKC", raw))
        if (date := normalize_date(match.group(0))) is not None
    ]
    if not dates:
        return None
    counts = Counter(dates)
    frequency = max(counts.values())
    candidates = [date for date, count in counts.items() if count == frequency]
    # Period-end dates repeat once per performance column. Ties are resolved to
    # the latest date, which is the end rather than the start of a period.
    return max(candidates)


def _performance_values(
    record: ProductEvidenceRecord,
    block: EvidenceBlock,
    requested_class: str,
) -> list[TypedProductValue]:
    raw = unicodedata.normalize("NFKC", block.raw_text)
    compact = re.sub(r"\s+", "", raw)
    if (
        "연평균수익률" not in compact
        or "최근1년" not in compact
        or "최근3년" not in compact
        or "%" not in raw
        or not ("세전" in raw or "과세전" in raw)
    ):
        return []
    lines = [line for line in raw.splitlines() if line.strip()]
    section_starts = [
        position
        for position, line in enumerate(lines)
        if (
            "연평균수익률" in re.sub(r"\s+", "", line)
            and re.search(r"(?:^|\s)가\s*[.．]", line)
        )
    ]
    if not section_starts:
        return []
    section_start = section_starts[0]
    section_ends = [
        position
        for position, line in enumerate(lines[section_start + 1 :], section_start + 1)
        if (
            "연도별수익률" in re.sub(r"\s+", "", line)
            and re.search(r"(?:^|\s)나\s*[.．]", line)
        )
    ]
    section_end = section_ends[0] if section_ends else len(lines)
    lines = lines[section_start:section_end]
    as_of_date = _dominant_table_date("\n".join(lines))
    if as_of_date is None:
        return []
    requested_positions = _class_positions(lines, requested_class)
    all_positions = [i for i, line in enumerate(lines) if _any_class_marker(line)]
    values: list[TypedProductValue] = []
    metrics = (
        ("annualized_return_1y", 1),
        ("annualized_return_2y", 2),
        ("annualized_return_3y", 3),
        ("annualized_return_5y", 5),
        ("annualized_return_since_inception", None),
    )
    for position, line in enumerate(lines):
        if not _nearest_unique_class(
            row_position=position,
            requested_positions=requested_positions,
            all_positions=all_positions,
            maximum_distance=3,
        ):
            continue
        tokens = _table_tokens_without_dates(line)
        if len(tokens) != 5:
            continue
        for (metric, period_years), token in zip(metrics, tokens):
            if token == "-":
                continue
            parsed = _decimal(token)
            if parsed is None or not Decimal("-1000") <= parsed <= Decimal("1000"):
                continue
            values.append(
                TypedProductValue(
                    product_code=record.product_code,
                    axis="performance",
                    metric=metric,
                    value=parsed,
                    unit="percent",
                    class_label=requested_class,
                    as_of_date=as_of_date,
                    period_years=period_years,
                    aggregation="annualized",
                    tax_basis="pre_tax",
                    provenance=_provenance(record, block, line),
                )
            )
    return values


def _balance_units(raw: str) -> tuple[str, str] | None:
    compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", raw))
    if "억좌" in compact and "억원" in compact:
        return "hundred_million_units", "hundred_million_won"
    if "백만좌" in compact and "백만원" in compact:
        return "million_units", "million_won"
    if re.search(r"단위[:：]?백만(?:[)\]])?", compact):
        return "million_units", "million_won"
    return None


def _period_dates(lines: list[str], position: int) -> tuple[str, str] | None:
    window = " ".join(lines[max(0, position - 2) : position + 3])
    dates = [
        date
        for match in _DATE_TOKEN_RE.finditer(window)
        if (date := normalize_date(match.group(0))) is not None
    ]
    if len(dates) < 2:
        return None
    # The local window may also touch the preceding period. The two dates
    # nearest the value row are still ordered start then end in extracted text.
    return dates[-2], dates[-1]


def _balance_values(
    record: ProductEvidenceRecord,
    block: EvidenceBlock,
    requested_class: str,
) -> list[TypedProductValue]:
    raw = unicodedata.normalize("NFKC", block.raw_text)
    if "기간말잔고" not in re.sub(r"\s+", "", raw):
        return []
    units = _balance_units(raw)
    if units is None:
        return []
    lines = [line for line in raw.splitlines() if line.strip()]
    requested_positions = _class_positions(lines, requested_class)
    if not requested_positions:
        return []
    all_positions = [i for i, line in enumerate(lines) if _any_class_marker(line)]
    candidates: list[tuple[str, list[str], str, int]] = []
    for class_position in requested_positions:
        following_markers = [item for item in all_positions if item > class_position]
        section_end = min(following_markers) if following_markers else len(lines)
        for position in range(class_position, section_end):
            tokens = _table_tokens_without_dates(lines[position])
            if len(tokens) not in {8, 9}:
                continue
            period = _period_dates(lines, position)
            if period is None:
                continue
            _, period_end = period
            candidates.append((period_end, tokens, lines[position], block.page))
    if not candidates:
        return []
    period_end, tokens, line, _ = max(candidates, key=lambda item: item[0])
    core = tokens[:8]
    values: list[TypedProductValue] = []
    for metric, token, unit in (
        ("period_end_balance_units", core[6], units[0]),
        ("period_end_balance_amount", core[7], units[1]),
    ):
        if token == "-":
            continue
        parsed = _decimal(token)
        if parsed is None or parsed < 0:
            continue
        values.append(
            TypedProductValue(
                product_code=record.product_code,
                axis="balance",
                metric=metric,
                value=parsed,
                unit=unit,
                class_label=requested_class,
                as_of_date=period_end,
                period_years=None,
                aggregation="period_end",
                tax_basis="not_applicable",
                provenance=_provenance(record, block, line),
            )
        )
    return values


def _deduplicate_with_conflicts(
    values: list[TypedProductValue],
) -> tuple[tuple[TypedProductValue, ...], tuple[ProductValueConflict, ...]]:
    by_key: dict[tuple[object, ...], list[TypedProductValue]] = {}
    for value in values:
        by_key.setdefault(value.alignment_key, []).append(value)
    deduplicated: list[TypedProductValue] = []
    conflicts: list[ProductValueConflict] = []
    for key, candidates in by_key.items():
        by_value: dict[Decimal, TypedProductValue] = {}
        for candidate in candidates:
            by_value.setdefault(candidate.value, candidate)
        if len(by_value) > 1:
            conflicts.append(
                ProductValueConflict(
                    alignment_key=key,
                    values=tuple(
                        sorted(
                            by_value.values(),
                            key=lambda item: (item.value, item.provenance.ref),
                        )
                    ),
                )
            )
        else:
            deduplicated.append(next(iter(by_value.values())))
    return (
        tuple(sorted(deduplicated, key=lambda item: (item.metric, item.as_of_date))),
        tuple(sorted(conflicts, key=lambda item: repr(item.alignment_key))),
    )


def parse_product_axis_values(
    *,
    index: ProductEvidenceIndex,
    product_code: str,
    axis: str,
    class_label: str,
) -> ProductValueParseResult:
    requested_class = normalize_class_label(class_label)
    record = index.get_record(product_code)
    if record is None:
        return ProductValueParseResult(
            product_code=product_code,
            axis=axis,
            class_label=requested_class,
            status="unsupported",
            values=(),
            reason="상품 identity가 근거 색인에 없음",
        )
    parser = {
        "fees": _fee_values,
        "performance": _performance_values,
        "balance": _balance_values,
    }.get(axis)
    if parser is None:
        return ProductValueParseResult(
            product_code=product_code,
            axis=axis,
            class_label=requested_class,
            status="unsupported",
            values=(),
            reason="정형 값 파서가 지원하지 않는 축",
        )
    parsed: list[TypedProductValue] = []
    for block in record.evidence.get(axis, ()):
        normalized_labels = {
            normalize_class_label(label) for label in block.class_labels
        }
        if requested_class not in normalized_labels and not _class_marker(
            block.raw_text, requested_class
        ):
            continue
        parsed.extend(parser(record, block, requested_class))
    deduplicated, conflicts = _deduplicate_with_conflicts(parsed)
    if conflicts:
        independently_sourced = any(
            conflict.independently_sourced for conflict in conflicts
        )
        return ProductValueParseResult(
            product_code=product_code,
            axis=axis,
            class_label=requested_class,
            status="conflict" if independently_sourced else "ambiguous",
            values=(),
            reason=(
                "서로 다른 원문 좌표에서 같은 상품·클래스·지표·기준일·단위의 값이 충돌함"
                if independently_sourced
                else "같은 원문 좌표의 복합 표에서 동일 의미 후보를 하나로 확정할 수 없음"
            ),
            conflicts=conflicts,
        )
    if not deduplicated:
        return ProductValueParseResult(
            product_code=product_code,
            axis=axis,
            class_label=requested_class,
            status="unsupported",
            values=(),
            reason="열 의미·클래스·단위·기준일을 함께 확정할 수 있는 표 행이 없음",
        )
    return ProductValueParseResult(
        product_code=product_code,
        axis=axis,
        class_label=requested_class,
        status="parsed",
        values=deduplicated,
        reason="공식 원문 표의 열 의미와 행을 결정적으로 파싱함",
    )


def common_aligned_values(
    results: tuple[ProductValueParseResult, ...],
) -> tuple[TypedProductValue, ...]:
    if not results or any(result.status != "parsed" for result in results):
        return ()
    key_sets = [{value.alignment_key for value in result.values} for result in results]
    common = set.intersection(*key_sets) if key_sets else set()
    if not common:
        return ()
    aligned: list[TypedProductValue] = []
    for result in results:
        aligned.extend(value for value in result.values if value.alignment_key in common)
    return tuple(aligned)
