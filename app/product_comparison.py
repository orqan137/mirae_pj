from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.product_facts import EvidenceBlock, ProductEvidenceIndex
from app.product_values import (
    TYPED_VALUE_AXES,
    ProductValueParseResult,
    TypedProductValue,
    common_aligned_values,
    normalize_class_label,
    normalize_date,
    parse_product_axis_values,
    requested_class_labels,
)


PRODUCT_COMPARATOR_VERSION = "pension-product-comparator-v6"
CLASS_DEPENDENT_AXES = frozenset({"class", "fees", "performance", "balance"})
DATE_REQUIRED_AXES = frozenset({"fees", "performance", "balance"})
UNIT_REQUIRED_AXES = frozenset({"fees", "performance", "balance"})

def _normalize_unit(value: str) -> str:
    normalized = re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).casefold()
    return normalized.removeprefix("연")


@dataclass(frozen=True, slots=True)
class AxisComparison:
    axis: str
    status: str
    reason: str
    evidence_refs: tuple[str, ...]
    aligned_classes: tuple[str, ...] = ()
    aligned_dates: tuple[str, ...] = ()
    aligned_units: tuple[str, ...] = ()
    typed_values: tuple[TypedProductValue, ...] = ()
    parser_results: tuple[ProductValueParseResult, ...] = ()


@dataclass(frozen=True, slots=True)
class ProductComparisonResult:
    version: str
    product_codes: tuple[str, ...]
    requested_classes: tuple[str, ...]
    axes: tuple[AxisComparison, ...]

    @property
    def comparable_axes(self) -> frozenset[str]:
        return frozenset(item.axis for item in self.axes if item.status == "comparable")

    @property
    def stated_only_axes(self) -> frozenset[str]:
        """Comparable axes the typed table cannot carry.

        위험등급 and 상품분류 are classifications the prospectus states, so they
        compare without producing a typed row. The rendered table then holds
        only the measured axes, and a question asking for both got a context
        with no 위험등급 in it at all.
        """

        return frozenset(
            item.axis
            for item in self.axes
            if item.status == "comparable" and not item.typed_values
        )

    @property
    def required_conditions(self) -> tuple[str, ...]:
        conditions: list[str] = []
        if any(item.status == "needs_class" for item in self.axes):
            conditions.append("비교할 동일 판매 클래스(예: C-P, C-PE)")
        if any(item.status == "multiple_classes" for item in self.axes):
            conditions.append("비교 기준으로 사용할 하나의 판매 클래스")
        return tuple(conditions)

    def status_for(self, axis: str) -> str | None:
        return next((item.status for item in self.axes if item.axis == axis), None)

    def trace_fields(self) -> dict[str, object]:
        conflict_axes = sum(item.status == "value_conflict" for item in self.axes)
        ambiguous_axes = sum(item.status == "value_ambiguous" for item in self.axes)
        if self.required_conditions:
            status = "needs_input"
        elif conflict_axes:
            status = "conflict"
        elif len(self.comparable_axes) == len(self.axes):
            status = "ok"
        else:
            status = "partial"
        return {
            "name": "product_comparator",
            "version": self.version,
            "status": status,
            "products": len(self.product_codes),
            "requested_axes": len(self.axes),
            "comparable_axes": len(self.comparable_axes),
            "incomparable_axes": len(self.axes) - len(self.comparable_axes),
            "typed_values": sum(len(item.typed_values) for item in self.axes),
            "conflict_axes": conflict_axes,
            "ambiguous_axes": ambiguous_axes,
        }

    def render_context(self, *, max_chars: int) -> str:
        if max_chars <= 0:
            return ""
        typed_values = tuple(value for axis in self.axes for value in axis.typed_values)
        conflict_values = tuple(
            value
            for axis in self.axes
            if axis.status == "value_conflict"
            for result in axis.parser_results
            for conflict in result.conflicts
            for value in conflict.values
        )
        rows: list[str] = []
        header = (
            "product_code | class | metric | value | unit | as_of_date | "
            "period_years | aggregation | tax_basis | source"
        )
        if typed_values:
            rows.extend(
                (
                    "[TYPED PRODUCT COMPARISON]",
                    f"comparator_version={self.version}",
                    header,
                )
            )
            rows.extend(value.render_row() for value in typed_values)
        if conflict_values:
            rows.extend(
                (
                    "[PRODUCT VALUE CONFLICT]",
                    f"comparator_version={self.version}",
                    header,
                )
            )
            rows.extend(value.render_row() for value in conflict_values)
        if not rows:
            return ""
        rendered: list[str] = []
        used = 0
        for row in rows:
            addition = len(row) + (1 if rendered else 0)
            if used + addition > max_chars:
                break
            rendered.append(row)
            used += addition
        return "\n".join(rendered)


def _identity_dates(index: ProductEvidenceIndex, code: str) -> frozenset[str]:
    record = index.get_record(code)
    if record is None:
        return frozenset()
    explicit_basis = frozenset(
        date
        for raw in record.basis_dates
        if (date := normalize_date(raw)) is not None
    )
    if explicit_basis:
        return explicit_basis
    return frozenset(
        normalized
        for block in record.evidence.get("identity", ())
        for raw in block.dates
        if (normalized := normalize_date(raw)) is not None
    )


def _blocks_for_class(
    blocks: tuple[EvidenceBlock, ...],
    requested_class: str,
) -> tuple[EvidenceBlock, ...]:
    return tuple(
        block
        for block in blocks
        if requested_class
        in {normalize_class_label(label) for label in block.class_labels}
    )


def _shared_class_labels(
    raw_blocks: list[tuple[EvidenceBlock, ...]],
) -> tuple[str, ...]:
    """Return sales classes documented for *every* product on this axis.

    Comparing a class-dependent metric across products is only sound when both
    sides are read from the same sales class. When the question does not name
    one, an intersection of the classes each prospectus actually documents is
    still evidence-derived: it picks a real, shared class rather than assuming
    a mapping the organizer never provided. An empty intersection keeps the
    original fail-closed behaviour.
    """

    per_product = [
        frozenset(
            normalize_class_label(label)
            for block in blocks
            for label in block.class_labels
            if normalize_class_label(label)
        )
        for blocks in raw_blocks
    ]
    return _intersection(per_product)


def _auto_aligned_class(
    *,
    index: ProductEvidenceIndex,
    codes: tuple[str, ...],
    axis: str,
    raw_blocks: list[tuple[EvidenceBlock, ...]],
) -> str | None:
    """Pick a shared sales class that actually yields an aligned comparison.

    Auto-alignment must never downgrade the answer. Asking which class to use
    is more useful than reporting "no evidence", so a shared class is adopted
    only when it resolves to aligned typed values on every product; otherwise
    the caller keeps the original ``needs_class`` clarification.
    """

    shared = _shared_class_labels(raw_blocks)
    if not shared:
        return None
    if axis not in TYPED_VALUE_AXES:
        return shared[0]
    for candidate in shared:
        parser_results = tuple(
            parse_product_axis_values(
                index=index,
                product_code=code,
                axis=axis,
                class_label=candidate,
            )
            for code in codes
        )
        if any(item.conflicts for item in parser_results):
            continue
        if common_aligned_values(parser_results):
            return candidate
    return None


def _intersection(values: list[frozenset[str]]) -> tuple[str, ...]:
    if not values or any(not value for value in values):
        return ()
    return tuple(sorted(set.intersection(*(set(value) for value in values))))


def _axis_refs(
    *,
    index: ProductEvidenceIndex,
    codes: tuple[str, ...],
    axis: str,
    selected: list[tuple[EvidenceBlock, ...]],
) -> tuple[str, ...]:
    refs: list[str] = []
    for code, blocks in zip(codes, selected):
        record = index.get_record(code)
        if record is None:
            continue
        refs.extend(
            f"{code}:{axis}:{record.source_path}:page={block.page}"
            for block in blocks
        )
    return tuple(refs)


def compare_product_facts(
    *,
    index: ProductEvidenceIndex,
    product_codes: tuple[str, ...],
    axes: tuple[str, ...],
    question: str,
) -> ProductComparisonResult:
    """Align deterministic facts before allowing a product comparison."""

    codes = tuple(dict.fromkeys(product_codes))
    requested_classes = requested_class_labels(question)
    results: list[AxisComparison] = []
    for axis in axes:
        records = [index.get_record(code) for code in codes]
        if len(codes) < 2 or any(record is None for record in records):
            results.append(
                AxisComparison(
                    axis=axis,
                    status="missing_product",
                    reason="비교할 두 개 이상의 상품 identity가 필요함",
                    evidence_refs=(),
                )
            )
            continue
        raw_blocks = [record.evidence.get(axis, ()) for record in records if record is not None]
        if any(not blocks for blocks in raw_blocks):
            results.append(
                AxisComparison(
                    axis=axis,
                    status="missing_evidence",
                    reason="한쪽 이상의 상품에 요청 축 원문 근거가 없음",
                    evidence_refs=(),
                )
            )
            continue

        axis_classes = requested_classes
        if axis in CLASS_DEPENDENT_AXES:
            if not requested_classes:
                auto_class = _auto_aligned_class(
                    index=index,
                    codes=codes,
                    axis=axis,
                    raw_blocks=raw_blocks,
                )
                if auto_class is None:
                    results.append(
                        AxisComparison(
                            axis=axis,
                            status="needs_class",
                            reason="판매 클래스를 지정하지 않아 같은 클래스를 정렬할 수 없음",
                            evidence_refs=(),
                        )
                    )
                    continue
                # Evidence-derived shared class. It is reported in
                # ``aligned_classes`` so the answer states the basis instead of
                # implying a class-free figure.
                axis_classes = (auto_class,)
            if len(axis_classes) != 1:
                results.append(
                    AxisComparison(
                        axis=axis,
                        status="multiple_classes",
                        reason="한 비교에서 여러 판매 클래스를 동시에 지정함",
                        evidence_refs=(),
                    )
                )
                continue
            selected = [
                _blocks_for_class(blocks, axis_classes[0]) for blocks in raw_blocks
            ]
            if any(not blocks for blocks in selected):
                results.append(
                    AxisComparison(
                        axis=axis,
                        status="class_evidence_missing",
                        reason="요청한 판매 클래스의 근거가 한쪽 이상의 상품에 없음",
                        evidence_refs=(),
                        aligned_classes=axis_classes,
                    )
                )
                continue
        else:
            selected = raw_blocks

        if axis in TYPED_VALUE_AXES:
            parser_results = tuple(
                parse_product_axis_values(
                    index=index,
                    product_code=code,
                    axis=axis,
                    class_label=axis_classes[0],
                )
                for code in codes
            )
            conflicts = tuple(
                conflict
                for item in parser_results
                for conflict in item.conflicts
            )
            if conflicts:
                has_independent_conflict = any(
                    item.status == "conflict" for item in parser_results
                )
                results.append(
                    AxisComparison(
                        axis=axis,
                        status=(
                            "value_conflict"
                            if has_independent_conflict
                            else "value_ambiguous"
                        ),
                        reason=(
                            "서로 다른 원문 좌표의 같은 의미·기준 값이 충돌하여 비교를 중단함"
                            if has_independent_conflict
                            else "같은 페이지 복합 표의 값 의미를 하나로 확정할 수 없음"
                        ),
                        evidence_refs=tuple(
                            dict.fromkeys(
                                ref
                                for conflict in conflicts
                                for ref in conflict.evidence_refs
                            )
                        ),
                        aligned_classes=axis_classes,
                        parser_results=parser_results,
                    )
                )
                continue
            typed_values = common_aligned_values(parser_results)
            if not typed_values:
                results.append(
                    AxisComparison(
                        axis=axis,
                        status="value_unresolved",
                        reason=(
                            "양쪽 상품에서 같은 클래스·지표·기준일·단위로 확정된 "
                            "정형 값이 없음"
                        ),
                        evidence_refs=tuple(
                            value.provenance.ref
                            for item in parser_results
                            for value in item.values
                        ),
                        aligned_classes=axis_classes,
                        parser_results=parser_results,
                    )
                )
                continue
            results.append(
                AxisComparison(
                    axis=axis,
                    status="comparable",
                    reason="같은 클래스·지표·기준일·단위의 공식 원문 값이 정렬됨",
                    evidence_refs=tuple(
                        dict.fromkeys(value.provenance.ref for value in typed_values)
                    ),
                    aligned_classes=axis_classes,
                    aligned_dates=tuple(
                        sorted({value.as_of_date for value in typed_values})
                    ),
                    aligned_units=tuple(sorted({value.unit for value in typed_values})),
                    typed_values=typed_values,
                    parser_results=parser_results,
                )
            )
            continue

        common_units: tuple[str, ...] = ()
        if axis in UNIT_REQUIRED_AXES:
            unit_sets = [
                frozenset(
                    _normalize_unit(unit)
                    for block in blocks
                    for unit in block.units
                )
                for blocks in selected
            ]
            common_units = _intersection(unit_sets)
            if not common_units:
                results.append(
                    AxisComparison(
                        axis=axis,
                        status="unit_unresolved",
                        reason="양쪽 상품에 공통으로 확인되는 단위가 없음",
                        evidence_refs=_axis_refs(
                            index=index,
                            codes=codes,
                            axis=axis,
                            selected=selected,
                        ),
                        aligned_classes=axis_classes,
                    )
                )
                continue

        # `DATE_REQUIRED_AXES` names the axes whose values are measured at a
        # point in time — a fee actually charged over a period, a return, a
        # period-end balance. Those stay incomparable unless both products
        # report the same basis date.
        #
        # Everything else on this path is a classification the prospectus
        # states outright: 위험등급 1~6, 상품분류/자산유형, 판매 클래스. A
        # grade does not become a different grade because the two documents
        # were written on different days. Requiring a shared 작성기준일 for
        # them contradicted their exclusion from `DATE_REQUIRED_AXES` and left
        # only 1.09% of product pairs comparable on two of the six axes the
        # task names. Each row still carries its own `as_of_date`, so the
        # answer reports the basis of every value it quotes.
        date_sets: list[frozenset[str]] = []
        dates_required = axis in DATE_REQUIRED_AXES
        if dates_required:
            for code, blocks in zip(codes, selected):
                block_dates = frozenset(date for block in blocks for date in block.dates)
                date_sets.append(block_dates or _identity_dates(index, code))
        else:
            date_sets = [_identity_dates(index, code) for code in codes]
        common_dates = _intersection(date_sets)
        if dates_required and not common_dates:
            results.append(
                AxisComparison(
                    axis=axis,
                    status="date_unresolved",
                    reason="양쪽 상품에 공통으로 확인되는 기준일이 없음",
                    evidence_refs=_axis_refs(
                        index=index,
                        codes=codes,
                        axis=axis,
                        selected=selected,
                    ),
                    aligned_classes=axis_classes if axis in CLASS_DEPENDENT_AXES else (),
                    aligned_units=common_units,
                )
            )
            continue

        results.append(
            AxisComparison(
                axis=axis,
                status="comparable",
                reason=(
                    "상품 identity, 클래스, 기준일과 단위가 정렬됨"
                    if dates_required
                    else "상품 identity와 클래스가 정렬된 명시 분류값"
                ),
                evidence_refs=_axis_refs(
                    index=index,
                    codes=codes,
                    axis=axis,
                    selected=selected,
                ),
                aligned_classes=axis_classes if axis in CLASS_DEPENDENT_AXES else (),
                aligned_dates=common_dates,
                aligned_units=common_units,
            )
        )
    return ProductComparisonResult(
        version=PRODUCT_COMPARATOR_VERSION,
        product_codes=codes,
        requested_classes=requested_classes,
        axes=tuple(results),
    )
