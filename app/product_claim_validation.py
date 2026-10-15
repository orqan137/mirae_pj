from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field, replace

from app.product_facts import PRODUCT_CODE_RE
from app.product_identity import ProductResolution, product_name_aliases
from app.product_values import normalize_class_label, normalize_date, requested_class_labels
from app.validation_primitives import (
    ANY_CITATION_RE,
    BALANCE_UNITS_RE,
    CURRENCY_RE,
    PERCENT_RE,
    date_inventory,
    first_sentence,
    normalize_number,
    resolve_citation_reference,
    sentence_spans,
    split_citation_references,
)
from app.validation_types import UnsupportedClaim


@dataclass(frozen=True, slots=True)
class TypedProductRow:
    product_code: str
    class_label: str
    metric: str
    value: str
    unit: str
    as_of_date: str
    period_years: int | None
    aggregation: str
    tax_basis: str
    source: str


TypedProductInventory = dict[str, dict[str, tuple[TypedProductRow, ...]]]


@dataclass(frozen=True, slots=True)
class _ProductQuestionScope:
    metric_hint: str | None
    metrics: frozenset[str]
    metric_family: str | None
    classes: frozenset[str]
    dates: frozenset[str]
    aggregation: str | None
    tax_basis: str | None
    unit: str | None


@dataclass(slots=True)
class _QuestionScopeTracker:
    matched: set[str | None] = field(default_factory=set)
    mismatches: dict[str | None, UnsupportedClaim] = field(default_factory=dict)
    metrics: dict[str | None, set[str]] = field(default_factory=dict)

    def record(
        self,
        *,
        product_code: str | None,
        mismatch: bool,
        text: str,
        normalized: str,
        metric: str | None = None,
    ) -> None:
        if mismatch:
            self.mismatches.setdefault(
                product_code,
                UnsupportedClaim(
                    kind="product_question_relation",
                    text=text,
                    normalized=normalized,
                ),
            )
        else:
            self.matched.add(product_code)
            if metric is not None:
                self.metrics.setdefault(product_code, set()).add(metric)

    def failures(self) -> tuple[UnsupportedClaim, ...]:
        return tuple(
            claim
            for product_code, claim in self.mismatches.items()
            if product_code not in self.matched
        )


def parse_typed_product_inventory(context: str) -> TypedProductInventory:
    """Parse the renderer's typed product comparison block."""

    mutable: dict[str, dict[str, list[TypedProductRow]]] = {}
    in_typed_table = False
    for line in context.splitlines():
        if line.strip() == "[TYPED PRODUCT COMPARISON]":
            in_typed_table = True
            continue
        if in_typed_table and line.startswith("["):
            break
        if not in_typed_table or " | " not in line or line.startswith("product_code |"):
            continue
        cells = [cell.strip() for cell in line.split("|")]
        if len(cells) != 10 or PRODUCT_CODE_RE.fullmatch(cells[0]) is None:
            continue
        normalized = normalize_number(cells[3])
        as_of_date = normalize_date(cells[5])
        try:
            period_years = int(cells[6]) if cells[6] else None
        except ValueError:
            continue
        if normalized is None or as_of_date is None:
            continue
        row = TypedProductRow(
            product_code=cells[0].upper(),
            class_label=cells[1],
            metric=cells[2],
            value=normalized,
            unit=cells[4],
            as_of_date=as_of_date,
            period_years=period_years,
            aggregation=cells[7],
            tax_basis=cells[8],
            source=cells[9],
        )
        mutable.setdefault(row.product_code, {}).setdefault(row.metric, []).append(row)
    return {
        code: {metric: tuple(rows) for metric, rows in metrics.items()}
        for code, metrics in mutable.items()
    }


def typed_citation_sources(inventory: TypedProductInventory) -> frozenset[str]:
    return frozenset(
        row.source
        for metrics in inventory.values()
        for rows in metrics.values()
        for row in rows
        if row.source
    )


def typed_citation_excerpt_inventory(
    inventory: TypedProductInventory,
) -> dict[str, str]:
    """Render typed rows into citation-checkable, unit-bearing evidence."""

    by_source: dict[str, list[str]] = {}
    unit_templates = {
        "percent": "{value}%",
        "percent_per_year": "{value}%",
        "won": "{value}원",
        "million_won": "{value}백만원",
        "hundred_million_won": "{value}억원",
        "million_units": "{value}백만좌",
        "hundred_million_units": "{value}억좌",
    }
    for metrics in inventory.values():
        for rows in metrics.values():
            for row in rows:
                if not row.source:
                    continue
                rendered_value = unit_templates.get(
                    row.unit,
                    "{value} {unit}",
                ).format(value=row.value, unit=row.unit)
                by_source.setdefault(row.source, []).append(
                    " ".join(
                        (
                            row.product_code,
                            row.class_label,
                            row.metric,
                            rendered_value,
                            row.as_of_date,
                            row.aggregation,
                            row.tax_basis,
                        )
                    )
                )
    return {
        source: "\n".join(dict.fromkeys(lines))
        for source, lines in by_source.items()
    }


@dataclass(frozen=True, slots=True)
class _ProductMention:
    start: int
    end: int
    product_code: str | None
    label: str
    resolution_failure_kind: str | None = None


def _compact_with_offsets(value: str) -> tuple[str, tuple[int, ...]]:
    compact: list[str] = []
    offsets: list[int] = []
    for position, character in enumerate(unicodedata.normalize("NFKC", value).casefold()):
        if character.isalnum():
            compact.append(character)
            offsets.append(position)
    return "".join(compact), tuple(offsets)


def _inventory_code(
    *,
    product_code: str,
    canonical_source_id: str,
    aliases: tuple[str, ...],
    inventory: TypedProductInventory,
) -> str | None:
    preferred = product_code.upper()
    if preferred in inventory:
        return preferred
    candidates = {
        value.upper()
        for value in (canonical_source_id, *aliases)
        if value.upper() in inventory
    }
    return next(iter(candidates)) if len(candidates) == 1 else None


def _expected_inventory_codes(
    *,
    question: str,
    resolution: ProductResolution | None,
    inventory: TypedProductInventory,
) -> frozenset[str]:
    expected = {
        match.group(0).upper()
        for match in PRODUCT_CODE_RE.finditer(question)
        if match.group(0).upper() in inventory
    }
    if resolution is None:
        return frozenset(expected)
    for product in resolution.resolved:
        code = _inventory_code(
            product_code=product.product_code,
            canonical_source_id=product.canonical_source_id,
            aliases=product.aliases,
            inventory=inventory,
        )
        if code is not None:
            expected.add(code)
    return frozenset(expected)


def _name_mentions(
    *,
    answer: str,
    inventory: TypedProductInventory,
    resolution: ProductResolution | None,
) -> tuple[_ProductMention, ...]:
    if resolution is None or not resolution.resolved:
        return ()
    alias_bindings: dict[str, set[str | None]] = {}
    alias_labels: dict[str, str] = {}
    for product in resolution.resolved:
        code = _inventory_code(
            product_code=product.product_code,
            canonical_source_id=product.canonical_source_id,
            aliases=product.aliases,
            inventory=inventory,
        )
        for alias in product_name_aliases(product.official_name):
            alias_bindings.setdefault(alias, set()).add(code)
            alias_labels.setdefault(alias, product.official_name)

    compact, offsets = _compact_with_offsets(answer)
    candidates: list[tuple[int, int, str, str | None, str, str | None]] = []
    for alias, bindings in alias_bindings.items():
        start = compact.find(alias)
        while start >= 0:
            end = start + len(alias)
            usable_codes = {code for code in bindings if code is not None}
            code = next(iter(usable_codes)) if len(bindings) == 1 and usable_codes else None
            failure_kind = (
                None
                if code is not None
                else (
                    "unresolved_product_name_relation"
                    if bindings == {None}
                    else "ambiguous_product_name_relation"
                )
            )
            candidates.append(
                (start, end, alias, code, alias_labels[alias], failure_kind)
            )
            start = compact.find(alias, start + 1)

    selected: list[tuple[int, int, str, str | None, str, str | None]] = []
    for candidate in sorted(candidates, key=lambda item: (item[0], -(item[1] - item[0]))):
        if any(
            candidate[0] < existing[1] and candidate[1] > existing[0]
            for existing in selected
        ):
            continue
        selected.append(candidate)
    return tuple(
        _ProductMention(
            start=offsets[start],
            end=offsets[end - 1] + 1,
            product_code=code,
            label=label,
            resolution_failure_kind=failure_kind,
        )
        for start, end, _, code, label, failure_kind in sorted(selected)
        if start < len(offsets) and end - 1 < len(offsets)
    )


def _product_mentions(
    *,
    answer: str,
    inventory: TypedProductInventory,
    resolution: ProductResolution | None,
) -> tuple[_ProductMention, ...]:
    mentions: list[_ProductMention] = []
    for match in PRODUCT_CODE_RE.finditer(answer):
        literal = match.group(0).upper()
        code = literal if literal in inventory else None
        failure_kind: str | None = None
        if code is None and resolution is not None:
            targets = [
                product
                for product in resolution.resolved
                if literal == product.product_code.upper()
                or literal in {alias.upper() for alias in product.aliases}
            ]
            mapped = {
                candidate
                for product in targets
                if (
                    candidate := _inventory_code(
                        product_code=product.product_code,
                        canonical_source_id=product.canonical_source_id,
                        aliases=product.aliases,
                        inventory=inventory,
                    )
                )
                is not None
            }
            if len(mapped) == 1:
                code = next(iter(mapped))
            elif targets:
                failure_kind = "unresolved_product_code_relation"
        if code is not None or failure_kind is not None:
            mentions.append(
                _ProductMention(
                    start=match.start(),
                    end=match.end(),
                    product_code=code,
                    label=literal,
                    resolution_failure_kind=failure_kind,
                )
            )
    mentions.extend(_name_mentions(answer=answer, inventory=inventory, resolution=resolution))
    ordered = sorted(mentions, key=lambda item: (item.start, item.end))
    selected: list[_ProductMention] = []
    for mention in ordered:
        if (
            selected
            and mention.product_code is not None
            and selected[-1].product_code == mention.product_code
            and re.search(
                r"(?<!\d)[.!?。](?!\d)|\n",
                answer[selected[-1].end : mention.start],
            )
            is None
        ):
            continue
        selected.append(mention)
    return tuple(selected)


def _metric_hint(value: str) -> str | None:
    compact = re.sub(r"[\sㆍ·・]", "", unicodedata.normalize("NFKC", value)).casefold()
    if any(token in compact for token in ("총보수비용", "합성총보수", "총비용")):
        return "annual_total_expense_rate"
    if "총보수" in compact:
        return "annual_total_fee_rate"
    if "수익률" in compact:
        for years in (1, 2, 3, 5):
            if f"{years}년" in compact:
                return f"annualized_return_{years}y"
        if "설정이후" in compact:
            return "annualized_return_since_inception"
    if "기간말잔고" in compact or "잔고" in compact:
        if "좌수" in compact:
            return "period_end_balance_units"
        if "금액" in compact:
            return "period_end_balance_amount"
    return None


def _metric_family(value: str) -> str | None:
    compact = re.sub(r"[\sㆍ·・]", "", unicodedata.normalize("NFKC", value)).casefold()
    if "수익률" in compact or "운용성과" in compact:
        return "performance"
    if "잔고" in compact or "aum" in compact:
        return "balance"
    return None


_METRIC_ANCHOR_RE = re.compile(
    r"합성\s*총보수|총\s*보수\s*비용|총\s*비용|총\s*보수"
    r"|(?<!\d)[1235]\s*년(?!\s*\d\s*월)|설정\s*이후"
)


def _explicit_metrics(value: str) -> frozenset[str]:
    metrics: set[str] = set()
    performance = _metric_family(value) == "performance"
    anchors = tuple(_METRIC_ANCHOR_RE.finditer(value))
    for index, anchor in enumerate(anchors):
        end = anchors[index + 1].start() if index + 1 < len(anchors) else len(value)
        following = re.sub(r"\s+", "", value[anchor.end():end])
        if re.match(
            r"(?:수익률)?(?:이|가|은|는|을|를)?"
            r"(?:아니라|아닌|아니고|말고|제외(?:하고|한|해)|빼고)",
            following,
        ):
            continue
        token = anchor.group()
        hint = _metric_hint(token + (" 수익률" if performance else ""))
        if hint is not None:
            metrics.add(hint)
    return frozenset(metrics)


def _value_claim_segment(segment: str, position: int) -> str:
    """Keep the metric next to a value, retaining shared date/class qualifiers."""

    anchors = tuple(_METRIC_ANCHOR_RE.finditer(segment))
    if len(anchors) < 2:
        return segment
    preceding = tuple(anchor for anchor in anchors if anchor.start() < position)
    selected = preceding[-1] if preceding else anchors[0]
    values = sorted(
        (
            *PERCENT_RE.finditer(segment),
            *CURRENCY_RE.finditer(segment),
            *BALANCE_UNITS_RE.finditer(segment),
        ),
        key=lambda match: match.start(),
    )
    respectively = re.search(r"각각", segment)
    if (
        respectively is not None
        and len(anchors) == len(values)
        and anchors[-1].end() <= respectively.start()
        and respectively.end() <= values[0].start()
    ):
        # Explicit ordered lists bind one metric to one value. Do not infer
        # this pairing from punctuation alone or from lists of unequal size.
        selected = next(
            (anchor for anchor, value in zip(anchors, values) if value.start() == position),
            selected,
        )
    scoped = "".join(
        segment[start:end]
        for start, end in zip(
            (0, *(anchor.end() for anchor in anchors)),
            (*(anchor.start() for anchor in anchors), len(segment)),
        )
    ) + " " + selected.group()
    if _metric_hint(selected.group()) in {
        "annual_total_fee_rate", "annual_total_expense_rate"
    } and _metric_family(segment) == "performance":
        scoped = re.sub(r"연환산|연평균|누적|세전|세후|수익률|운용성과", "", scoped)
    return scoped


def _aggregation_hint(value: str) -> str | None:
    compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).casefold()
    if "누적" in compact:
        return "cumulative"
    if "연환산" in compact or "연평균" in compact:
        return "annualized"
    return None


def _tax_basis_hint(value: str) -> str | None:
    compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).casefold()
    if "세후" in compact:
        return "post_tax"
    if "세전" in compact:
        return "pre_tax"
    return None


def _unit_hint(value: str) -> str | None:
    compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).casefold()
    for token, unit in (
        ("백만좌", "million_units"),
        ("억좌", "hundred_million_units"),
        ("백만원", "million_won"),
        ("억원", "hundred_million_won"),
    ):
        if token in compact:
            return unit
    return None


def _product_question_scope(question: str) -> _ProductQuestionScope:
    metrics = _explicit_metrics(question)
    hint = next(iter(metrics)) if len(metrics) == 1 else _metric_hint(question)
    family = _metric_family(question)
    if len(metrics) == 1:
        family = "performance" if hint.startswith("annualized_return_") else None
    return _ProductQuestionScope(
        metric_hint=hint if len(metrics) <= 1 else None,
        metrics=metrics,
        metric_family=family,
        classes=frozenset(requested_class_labels(question)),
        dates=date_inventory(question),
        aggregation=_aggregation_hint(question),
        tax_basis=_tax_basis_hint(question),
        unit=_unit_hint(question),
    )


def _product_question_scopes(
    question: str,
    inventory: TypedProductInventory,
    resolution: ProductResolution | None,
    expected_codes: frozenset[str],
) -> dict[str, _ProductQuestionScope]:
    shared = _product_question_scope(question)
    mentions = _product_mentions(answer=question, inventory=inventory, resolution=resolution)
    local: dict[str, _ProductQuestionScope] = {}
    local_text: dict[str, list[str]] = {}
    for index, mention in enumerate(mentions):
        if mention.product_code is None:
            continue
        end = mentions[index + 1].start if index + 1 < len(mentions) else len(question)
        text = question[mention.end:end]
        if _explicit_metrics(text):
            local_text.setdefault(mention.product_code, []).append(text)
    for code, fragments in local_text.items():
        scope = _product_question_scope(" ".join(fragments))
        local[code] = replace(
            scope,
            classes=scope.classes or shared.classes,
            dates=scope.dates or (
                shared.dates if len(shared.dates) == 1 else frozenset()
            ),
        )
    # "A and B total fee" has a shared trailing request. Only bind distinct
    # local requests when every named product has its own explicit metric.
    if len(expected_codes) > 1 and expected_codes.issubset(local):
        return local
    return {code: shared for code in expected_codes}


def _claim_request_scope(
    value: str,
    question_scope: _ProductQuestionScope,
    answer_dates: frozenset[str],
) -> tuple[str | None, str | None, bool]:
    metric_hint = _metric_hint(value)
    metric_family = _metric_family(value)
    if metric_hint is not None:
        metric_family = (
            "performance" if metric_hint.startswith("annualized_return_")
            else "balance" if metric_hint.startswith("period_end_balance_")
            else None
        )
    dates = date_inventory(value) or (
        answer_dates if len(answer_dates) == 1 else frozenset()
    )
    aggregation = _aggregation_hint(value)
    tax_basis = _tax_basis_hint(value)
    unit = _unit_hint(value)
    mismatch = any(
        (
            metric_hint is not None
            and (
                bool(question_scope.metrics) and metric_hint not in question_scope.metrics
                or not question_scope.metrics
                and question_scope.metric_hint is not None
                and metric_hint != question_scope.metric_hint
            ),
            question_scope.metric_family is not None
            and metric_family is not None
            and metric_hint not in question_scope.metrics
            and question_scope.metric_family != metric_family,
            bool(
                question_scope.dates
                and dates
                and question_scope.dates.isdisjoint(dates)
            ),
            question_scope.aggregation is not None
            and aggregation is not None
            and question_scope.aggregation != aggregation,
            question_scope.tax_basis is not None
            and tax_basis is not None
            and question_scope.tax_basis != tax_basis,
            question_scope.unit is not None
            and unit is not None
            and question_scope.unit != unit,
        )
    )
    return (
        metric_hint or question_scope.metric_hint,
        metric_family if metric_hint is not None else metric_family or question_scope.metric_family,
        mismatch,
    )


def _is_historical_balance_claimed_as_current(value: str) -> bool:
    compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).casefold()
    return any(token in compact for token in ("현재", "실시간", "현재aum", "시장aum"))


def _claim_class_scope(
    value: str,
    expected_classes: frozenset[str],
) -> tuple[frozenset[str], bool]:
    local_classes = frozenset(requested_class_labels(value))
    if local_classes:
        return local_classes, bool(
            expected_classes and not local_classes.issubset(expected_classes)
        )
    if len(expected_classes) == 1:
        return expected_classes, False
    return frozenset(), False


def _rows_in_classes(
    rows: tuple[TypedProductRow, ...],
    classes: frozenset[str],
) -> tuple[TypedProductRow, ...]:
    if not classes:
        return rows
    return tuple(
        row for row in rows if normalize_class_label(row.class_label) in classes
    )


def _row_failure_kind(
    *,
    rows: tuple[TypedProductRow, ...],
    metric_hint: str | None,
    metric_family: str | None,
    segment: str,
    answer_dates: frozenset[str],
) -> str | None:
    if not rows:
        return "product_value_relation"
    performance = metric_family == "performance"
    balance = metric_family == "balance"
    aggregation = _aggregation_hint(segment)
    tax_basis = _tax_basis_hint(segment)
    unit = _unit_hint(segment)
    local_dates = date_inventory(segment)
    dates = local_dates or (answer_dates if len(answer_dates) == 1 else frozenset())

    if balance and _is_historical_balance_claimed_as_current(segment):
        return "historical_balance_as_current"
    if performance and (
        metric_hint is None or aggregation is None or tax_basis is None or not dates
    ):
        return "product_qualifier_missing"
    if balance and (metric_hint is None or unit is None or not dates):
        return "product_qualifier_missing"

    matching = _qualified_rows(rows, segment, answer_dates)
    return None if matching else "product_qualifier_relation"


def _qualified_rows(
    rows: tuple[TypedProductRow, ...],
    segment: str,
    answer_dates: frozenset[str],
) -> tuple[TypedProductRow, ...]:
    aggregation = _aggregation_hint(segment)
    tax_basis = _tax_basis_hint(segment)
    unit = _unit_hint(segment)
    dates = date_inventory(segment) or (
        answer_dates if len(answer_dates) == 1 else frozenset()
    )
    return tuple(
        row
        for row in rows
        if (aggregation is None or row.aggregation == aggregation)
        and (tax_basis is None or row.tax_basis == tax_basis)
        and (unit is None or row.unit == unit)
        and (not dates or row.as_of_date in dates)
    )


def _unattributed_typed_relation_claims(
    *,
    answer: str,
    inventory: TypedProductInventory,
    mentions: tuple[_ProductMention, ...],
    answer_dates: frozenset[str],
    expected_codes: frozenset[str],
    question_scope: _ProductQuestionScope,
    question_scopes: dict[str, _ProductQuestionScope],
    question_tracker: _QuestionScopeTracker,
) -> tuple[int, tuple[UnsupportedClaim, ...]]:
    """Validate typed metric sentences that use pronouns instead of product names."""

    all_rows = tuple(
        row
        for metrics in inventory.values()
        for rows in metrics.values()
        for row in rows
    )
    allowed_sources = frozenset(row.source for row in all_rows if row.source)
    unsupported: list[UnsupportedClaim] = []
    checked = 0
    for start, end in sentence_spans(answer):
        if any(start <= mention.start < end for mention in mentions):
            continue
        sentence = answer[start:end]
        prior_codes: set[str | None] = set()
        for previous_start, previous_end in sentence_spans(answer[:start]):
            sentence_codes = {
                mention.product_code for mention in mentions
                if previous_start <= mention.start < previous_end
            }
            if sentence_codes:
                prior_codes = sentence_codes
        claim_codes = expected_codes
        if len(prior_codes) == 1 and None not in prior_codes:
            claim_codes = frozenset(code for code in prior_codes if code is not None)
        product_code = next(iter(claim_codes)) if len(claim_codes) == 1 else None
        local_question_scope = question_scopes.get(product_code, question_scope)
        hint, family, request_mismatch = _claim_request_scope(
            sentence,
            local_question_scope,
            answer_dates,
        )
        class_scope, class_mismatch = _claim_class_scope(
            sentence,
            local_question_scope.classes,
        )
        if hint is None and family is None:
            continue
        written_refs = tuple(
            part
            for citation in ANY_CITATION_RE.finditer(sentence)
            for part in split_citation_references(citation.group(1).strip())
        )
        cited_sources = frozenset(
            resolved
            for reference in written_refs
            if (resolved := resolve_citation_reference(reference, allowed_sources))
            is not None
        )
        quantitative = [
            *PERCENT_RE.finditer(sentence),
            *CURRENCY_RE.finditer(sentence),
            *BALANCE_UNITS_RE.finditer(sentence),
        ]
        for match in quantitative:
            raw = match.group(1)
            claim_segment = _value_claim_segment(sentence, match.start())
            hint, family, request_mismatch = _claim_request_scope(
                claim_segment, local_question_scope, answer_dates
            )
            normalized = normalize_number(raw)
            if normalized is None:
                continue
            checked += 1
            value_rows = tuple(
                row
                for row in all_rows
                if row.value == normalized
                and (not claim_codes or row.product_code in claim_codes)
                and (hint is None or row.metric == hint)
                and (
                    hint is not None
                    or family is None
                    or (
                        family == "performance"
                        and row.metric.startswith("annualized_return_")
                    )
                    or (
                        family == "balance"
                        and row.metric.startswith("period_end_balance_")
                    )
                )
            )
            candidate_rows = _rows_in_classes(value_rows, class_scope)
            if class_mismatch or (class_scope and value_rows and not candidate_rows):
                failure_kind = "product_qualifier_relation"
            else:
                failure_kind = _row_failure_kind(
                    rows=candidate_rows,
                    metric_hint=hint,
                    metric_family=family,
                    segment=claim_segment,
                    answer_dates=answer_dates,
                )
            if failure_kind is not None:
                unsupported.append(
                    UnsupportedClaim(
                        kind=failure_kind,
                        text=raw,
                        normalized=f"unattributed:{hint or family}:{normalized}",
                    )
                )
                continue
            candidate_rows = _qualified_rows(candidate_rows, claim_segment, answer_dates)
            if written_refs and (
                not cited_sources
                or not any(row.source in cited_sources for row in candidate_rows)
            ):
                unsupported.append(
                    UnsupportedClaim(
                        kind="product_citation_relation",
                        text=raw,
                        normalized=f"unattributed:{hint or family}:citation",
                    )
                )
                continue
            question_tracker.record(
                product_code=product_code,
                mismatch=request_mismatch,
                text=raw,
                normalized=f"unattributed:{hint or family}:question_axis",
                metric=hint,
            )
    return checked, tuple(unsupported)


def validate_typed_product_relations(
    *,
    answer: str,
    question: str = "",
    inventory: TypedProductInventory,
    resolution: ProductResolution | None,
) -> tuple[int, tuple[UnsupportedClaim, ...]]:
    """Validate value, metric, qualifier, product and source as one relation."""

    if not inventory:
        return 0, ()
    mentions = _product_mentions(answer=answer, inventory=inventory, resolution=resolution)
    unsupported: list[UnsupportedClaim] = []
    checked = 0
    answer_dates = date_inventory(answer)
    allowed_sources = typed_citation_sources(inventory)
    answer_sentence_spans = sentence_spans(answer)
    question_scope = _product_question_scope(question)
    question_tracker = _QuestionScopeTracker()
    expected_codes = _expected_inventory_codes(
        question=question,
        resolution=resolution,
        inventory=inventory,
    )
    question_scopes = _product_question_scopes(
        question, inventory, resolution, expected_codes
    )
    for mention in mentions:
        if (
            expected_codes
            and mention.product_code is not None
            and mention.product_code not in expected_codes
        ):
            checked += 1
            unsupported.append(
                UnsupportedClaim(
                    kind="product_identity_relation",
                    text=mention.label,
                    normalized=(
                        f"expected:{'|'.join(sorted(expected_codes))}:"
                        f"actual:{mention.product_code}"
                    ),
                )
            )
    for position, mention in enumerate(mentions):
        end = next(
            (
                later.start
                for later in mentions[position + 1 :]
                if (
                    later.product_code != mention.product_code
                    or (mention.product_code is None and later.label != mention.label)
                )
            ),
            len(answer),
        )
        segment = first_sentence(answer[mention.end : end])
        local_references = tuple(
            part
            for match in ANY_CITATION_RE.finditer(segment)
            for part in split_citation_references(match.group(1).strip())
        )
        sentence = next(
            (
                answer[start:stop]
                for start, stop in answer_sentence_spans
                if start <= mention.start < stop
            ),
            segment,
        )
        sentence_markers = tuple(ANY_CITATION_RE.finditer(sentence))
        sentence_references = tuple(
            part
            for match in sentence_markers
            for part in split_citation_references(match.group(1).strip())
        )
        claim_references = (
            local_references
            if local_references
            else sentence_references if len(sentence_markers) == 1 else ()
        )
        cited_sources = frozenset(
            resolved
            for reference in claim_references
            if (resolved := resolve_citation_reference(reference, allowed_sources))
            is not None
        )
        citation_present = bool(local_references or sentence_markers)
        quantitative = [
            *PERCENT_RE.finditer(segment),
            *CURRENCY_RE.finditer(segment),
            *BALANCE_UNITS_RE.finditer(segment),
        ]
        if mention.product_code is None:
            for match in quantitative:
                raw = match.group(1)
                normalized = normalize_number(raw)
                if normalized is None:
                    continue
                checked += 1
                unsupported.append(
                    UnsupportedClaim(
                        kind=mention.resolution_failure_kind or "unresolved_product_name_relation",
                        text=mention.label,
                        normalized=(
                            f"{mention.resolution_failure_kind or 'unresolved'}:{normalized}"
                        ),
                    )
                )
            continue
        code = mention.product_code
        metrics = inventory[code]
        local_question_scope = question_scopes.get(code, question_scope)
        hint, family, request_mismatch = _claim_request_scope(
            segment,
            local_question_scope,
            answer_dates,
        )
        class_scope, class_mismatch = _claim_class_scope(
            segment,
            local_question_scope.classes,
        )
        for match in quantitative:
            raw = match.group(1)
            claim_segment = _value_claim_segment(segment, match.start())
            hint, family, request_mismatch = _claim_request_scope(
                claim_segment, local_question_scope, answer_dates
            )
            normalized = normalize_number(raw)
            if normalized is None:
                continue
            checked += 1
            value_rows = tuple(
                row
                for metric, rows in metrics.items()
                if hint is None or metric == hint
                if (
                    hint is not None
                    or family is None
                    or (family == "performance" and metric.startswith("annualized_return_"))
                    or (family == "balance" and metric.startswith("period_end_balance_"))
                )
                for row in rows
                if row.value == normalized
            )
            candidate_rows = _rows_in_classes(value_rows, class_scope)
            if class_mismatch or (class_scope and value_rows and not candidate_rows):
                failure_kind = "product_qualifier_relation"
            else:
                failure_kind = _row_failure_kind(
                    rows=candidate_rows,
                    metric_hint=hint,
                    metric_family=family,
                    segment=claim_segment,
                    answer_dates=answer_dates,
                )
            if failure_kind is not None:
                unsupported.append(
                    UnsupportedClaim(
                        kind=failure_kind,
                        text=f"{mention.label}:{raw}",
                        normalized=f"{code}:{hint or 'any'}:{normalized}",
                    )
                )
                continue
            candidate_rows = _qualified_rows(candidate_rows, claim_segment, answer_dates)
            if citation_present and not any(
                row.source in cited_sources for row in candidate_rows
            ):
                unsupported.append(
                    UnsupportedClaim(
                        kind="product_citation_relation",
                        text=f"{mention.label}:{raw}",
                        normalized=f"{code}:{hint or 'any'}:citation",
                    )
                )
                continue
            question_tracker.record(
                product_code=code,
                mismatch=request_mismatch,
                text=f"{mention.label}:{raw}",
                normalized=f"{code}:{hint or 'any'}:question_axis",
                metric=hint,
            )
    unattributed_checked, unattributed_unsupported = (
        _unattributed_typed_relation_claims(
            answer=answer,
            inventory=inventory,
            mentions=mentions,
            answer_dates=answer_dates,
            expected_codes=expected_codes,
            question_scope=question_scope,
            question_scopes=question_scopes,
            question_tracker=question_tracker,
        )
    )
    missing_axes = tuple(
        UnsupportedClaim(
            kind="product_question_relation",
            text=code,
            normalized=f"{code}:missing_metrics:{'|'.join(sorted(missing))}",
        )
        for code, scope in question_scopes.items()
        if len(question_scope.metrics) > 1
        if (
            missing := (scope.metrics & inventory[code].keys())
            - question_tracker.metrics.get(code, set())
        )
    )
    return (
        checked + unattributed_checked,
        tuple(unsupported)
        + unattributed_unsupported
        + question_tracker.failures()
        + missing_axes,
    )
