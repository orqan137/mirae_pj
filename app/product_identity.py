from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.product_facts import ProductEvidenceIndex, ProductEvidenceRecord, requested_product_codes


PRODUCT_IDENTITY_RESOLVER_VERSION = "pension-product-identity-v2"
_MIN_FUZZY_ALIAS_LENGTH = 8

_INLINE_NAME = re.compile(
    r"(?:집합투자기구의?\s*명칭|집합투자기구\s*명칭)\s*[:：]?\s*(.+)"
)
_TITLE_PATTERNS = (
    re.compile(r"투자설명서는\s+(.+?)에\s+대한"),
    re.compile(r"따라서\s+(.+?)(?:의\s*)?수익증권"),
)
_SKIP_AFTER_HEADING = re.compile(
    r"^(?:명\s*칭|집합투자기구\s*명칭|금융투자협회\s*펀드코드|종류\s*\(class\))$",
    flags=re.IGNORECASE,
)
_TYPE_SUFFIX = re.compile(r"[\(\[（][^\)\]）]{1,30}[\)\]）]")
_COMMON_NAME_PARTS = re.compile(
    r"증권|자투자신탁|투자신탁|연금저축|퇴직연금|연금|전환형|모투자신탁|"
    r"제?\d+호|주식형|채권형|혼합형|파생형|주식|채권|혼합"
)
_LEGAL_NAME_PARTS = re.compile(
    r"증권|자투자신탁|투자신탁|전환형|모투자신탁|주식형|채권형|혼합형|파생형"
)
_LEADING_MANAGERS = (
    "미래에셋",
    "한국투자",
    "한국밸류",
    "브이아이",
    "하나",
    "우리",
    "삼성",
    "신한",
    "케이비",
    "kb",
)
_GENERIC_SHORT_ALIASES = frozenset(
    {"연금", "퇴직연금", "채권", "주식", "인덱스", "단기", "중기", "장기", "성장"}
)


def _line(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


def _compact(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _within_one_edit(left: str, right: str) -> bool:
    """Return whether two compact strings differ by exactly one edit."""

    if left == right or abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        return sum(a != b for a, b in zip(left, right, strict=True)) == 1
    shorter, longer = (left, right) if len(left) < len(right) else (right, left)
    short_index = 0
    long_index = 0
    skipped = False
    while short_index < len(shorter) and long_index < len(longer):
        if shorter[short_index] == longer[long_index]:
            short_index += 1
            long_index += 1
            continue
        if skipped:
            return False
        skipped = True
        long_index += 1
    return True


def _one_edit_spans(text: str, alias: str) -> tuple[tuple[int, int], ...]:
    """Find bounded typo matches without a general approximate search."""

    spans: list[tuple[int, int]] = []
    for width in (len(alias) - 1, len(alias), len(alias) + 1):
        if width < _MIN_FUZZY_ALIAS_LENGTH or width > len(text):
            continue
        for start in range(len(text) - width + 1):
            end = start + width
            if _within_one_edit(text[start:end], alias):
                spans.append((start, end))
    return tuple(dict.fromkeys(spans))


def _overlaps(
    left: tuple[int, int],
    right: tuple[int, int],
) -> bool:
    return left[0] < right[1] and right[0] < left[1]


def _plausible_name(value: str) -> bool:
    compact = _compact(value)
    return (
        len(compact) >= 6
        and "펀드코드" not in compact
        and "금융투자협회" not in compact
        and "판매회사" not in compact
        and "종류class" not in compact
    )


def official_product_name(record: ProductEvidenceRecord) -> str:
    """Extract the declared fund name from immutable identity evidence."""

    for block in record.evidence.get("identity", ()):
        lines = [_line(raw) for raw in block.raw_text.splitlines() if _line(raw)]
        for position, line in enumerate(lines):
            match = _INLINE_NAME.search(line)
            if match and _plausible_name(match.group(1)):
                return match.group(1).strip()
            if "집합투자기구" not in line or "명칭" not in line:
                continue
            for candidate in lines[position + 1 : position + 5]:
                if _SKIP_AFTER_HEADING.fullmatch(candidate):
                    continue
                if _plausible_name(candidate):
                    return candidate.strip()

    title = _line(record.title)
    for pattern in _TITLE_PATTERNS:
        match = pattern.search(title)
        if match and _plausible_name(match.group(1)):
            return match.group(1).strip()
    if _plausible_name(title):
        return title
    raise ValueError(
        "product identity evidence has no usable official name: "
        f"{record.product_code}"
    )


def product_name_aliases(name: str) -> tuple[str, ...]:
    """Return deterministic compact aliases that are safe for identity matching."""

    display = _line(name)
    without_type = _TYPE_SUFFIX.sub("", display)
    legal_simplified = _LEGAL_NAME_PARTS.sub("", without_type)
    simplified = _COMMON_NAME_PARTS.sub("", without_type)
    candidates = [display, without_type, legal_simplified, simplified]
    compact_simplified = _compact(simplified)
    for manager in _LEADING_MANAGERS:
        compact_manager = _compact(manager)
        if compact_simplified.startswith(compact_manager):
            candidates.append(compact_simplified[len(compact_manager) :])
    aliases = tuple(
        dict.fromkeys(
            compact
            for value in candidates
            if (
                len(compact := _compact(value)) >= 6
                or (len(compact) >= 4 and compact not in _GENERIC_SHORT_ALIASES)
            )
        )
    )
    return aliases


@dataclass(frozen=True, slots=True)
class ResolvedProduct:
    product_code: str
    canonical_source_id: str
    aliases: tuple[str, ...]
    official_name: str
    matched_by: str


@dataclass(frozen=True, slots=True)
class AmbiguousProductMention:
    mention: str
    candidates: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProductResolution:
    version: str
    resolved: tuple[ResolvedProduct, ...]
    unknown_codes: tuple[str, ...]
    ambiguous: tuple[AmbiguousProductMention, ...]

    @property
    def product_codes(self) -> tuple[str, ...]:
        return tuple(item.product_code for item in self.resolved)

    @property
    def canonical_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(item.canonical_source_id for item in self.resolved))

    def required_conditions(self, *, comparison: bool) -> tuple[str, ...]:
        conditions: list[str] = []
        if self.unknown_codes:
            conditions.append("제공 자료에서 확인되는 정확한 상품코드")
        if self.ambiguous:
            conditions.append("모호하지 않은 공식 상품명 또는 상품코드")
        if comparison and len(self.resolved) < 2:
            conditions.append("비교할 두 상품의 정확한 코드 또는 공식 상품명")
        return tuple(dict.fromkeys(conditions))

    def trace_fields(self) -> dict[str, object]:
        return {
            "name": "product_identity_resolver",
            "version": self.version,
            "status": (
                "needs_input"
                if self.unknown_codes or self.ambiguous
                else "ok"
            ),
            "resolved": len(self.resolved),
            "unknown_codes": len(self.unknown_codes),
            "ambiguous_mentions": len(self.ambiguous),
        }


@dataclass(frozen=True, slots=True)
class _Identity:
    canonical_source_id: str
    aliases: tuple[str, ...]
    official_name: str
    name_aliases: tuple[str, ...]


class ProductIdentityResolver:
    def __init__(self, records: tuple[ProductEvidenceRecord, ...]) -> None:
        by_canonical: dict[str, ProductEvidenceRecord] = {}
        for record in records:
            by_canonical.setdefault(record.canonical_source_id, record)
        identities = tuple(
            _Identity(
                canonical_source_id=record.canonical_source_id,
                aliases=record.aliases,
                official_name=official_product_name(record),
                name_aliases=product_name_aliases(official_product_name(record)),
            )
            for _, record in sorted(by_canonical.items())
        )
        self._identities = identities
        self._code_to_identity = {
            code: identity
            for identity in identities
            for code in identity.aliases
        }
        alias_map: dict[str, set[str]] = {}
        for identity in identities:
            for alias in identity.name_aliases:
                alias_map.setdefault(alias, set()).add(identity.canonical_source_id)
        self._alias_map = {
            alias: tuple(sorted(canonical_ids))
            for alias, canonical_ids in alias_map.items()
        }
        self._by_canonical = {
            identity.canonical_source_id: identity for identity in identities
        }

    @classmethod
    def from_index(cls, index: ProductEvidenceIndex) -> "ProductIdentityResolver":
        return cls(index.records)

    @property
    def identity_count(self) -> int:
        return len(self._identities)

    def resolve(self, question: str) -> ProductResolution:
        explicit_codes = requested_product_codes(question)
        unknown_codes = tuple(
            code for code in explicit_codes if code not in self._code_to_identity
        )
        resolved_by_key: dict[str, tuple[int, ResolvedProduct]] = {}
        for code in explicit_codes:
            identity = self._code_to_identity.get(code)
            if identity is None:
                continue
            resolved_by_key[f"code:{code}"] = (
                question.upper().find(code),
                ResolvedProduct(
                    product_code=code,
                    canonical_source_id=identity.canonical_source_id,
                    aliases=identity.aliases,
                    official_name=identity.official_name,
                    matched_by="code",
                ),
            )

        compact_question = _compact(question)
        ambiguous_hits: list[tuple[int, int, str, tuple[str, ...]]] = []
        name_hits: list[tuple[int, int, str, str]] = []
        for alias, canonical_ids in self._alias_map.items():
            start = compact_question.find(alias)
            if start < 0:
                continue
            if len(canonical_ids) > 1:
                ambiguous_hits.append(
                    (start, start + len(alias), alias, canonical_ids)
                )
                continue
            name_hits.append((start, start + len(alias), alias, canonical_ids[0]))

        # Prefer the longest match when a short alias is contained in a full
        # official name. Non-overlapping hits remain separate products.
        selected: list[tuple[int, int, str, str]] = []
        for hit in sorted(name_hits, key=lambda item: (-(item[1] - item[0]), item[0])):
            if any(
                hit[0] >= existing[0]
                and hit[1] <= existing[1]
                and hit[3] != existing[3]
                for existing in selected
            ):
                continue
            selected.append(hit)

        # Recover one insertion, deletion, or substitution only for long name
        # aliases. Exact matches always win, overlapping exact mentions are
        # never reinterpreted, and an equally plausible second product turns
        # the mention into an input condition instead of a guess.
        exact_ranges = tuple(
            (start, end)
            for start, end, _, _ in (*selected, *ambiguous_hits)
        )
        fuzzy_hits: list[tuple[int, int, str, tuple[str, ...]]] = []
        exact_canonical_ids = {canonical_id for _, _, _, canonical_id in selected}
        for alias, canonical_ids in self._alias_map.items():
            if (
                len(alias) < _MIN_FUZZY_ALIAS_LENGTH
                or alias in compact_question
                or set(canonical_ids) <= exact_canonical_ids
            ):
                continue
            for start, end in _one_edit_spans(compact_question, alias):
                if any(_overlaps((start, end), exact_range) for exact_range in exact_ranges):
                    continue
                fuzzy_hits.append((start, end, alias, canonical_ids))

        used_fuzzy_ranges: list[tuple[int, int]] = []
        for start, end, alias, canonical_ids in sorted(
            fuzzy_hits,
            key=lambda item: (-(item[1] - item[0]), item[0], item[2]),
        ):
            span = (start, end)
            if any(_overlaps(span, used) for used in used_fuzzy_ranges):
                continue
            peers = tuple(
                hit
                for hit in fuzzy_hits
                if hit[0] == start and abs(hit[1] - end) <= 1
            )
            best_length = max(len(hit[2]) for hit in peers)
            best = tuple(hit for hit in peers if len(hit[2]) == best_length)
            candidate_ids = tuple(
                sorted(
                    {
                        canonical_id
                        for hit in best
                        for canonical_id in hit[3]
                        if canonical_id not in exact_canonical_ids
                    }
                )
            )
            if not candidate_ids:
                continue
            used_fuzzy_ranges.append(span)
            if len(candidate_ids) > 1:
                ambiguous_hits.append((start, end, alias, candidate_ids))
                continue
            canonical_id = candidate_ids[0]
            identity = self._by_canonical[canonical_id]
            selected.append((start, end, f"fuzzy:{alias}", canonical_id))
            exact_canonical_ids.add(canonical_id)
        for start, _, alias, canonical_id in sorted(selected):
            if any(
                product.canonical_source_id == canonical_id
                for _, product in resolved_by_key.values()
            ):
                continue
            identity = self._by_canonical[canonical_id]
            resolved_by_key[f"name:{canonical_id}"] = (
                start,
                ResolvedProduct(
                    product_code=canonical_id,
                    canonical_source_id=canonical_id,
                    aliases=identity.aliases,
                    official_name=identity.official_name,
                    matched_by=(
                        f"fuzzy_name:{alias.removeprefix('fuzzy:')}"
                        if alias.startswith("fuzzy:")
                        else f"name:{alias}"
                    ),
                ),
            )

        ambiguous = [
            AmbiguousProductMention(mention=alias, candidates=candidates)
            for start, end, alias, candidates in ambiguous_hits
            if not any(
                start >= hit_start and end <= hit_end
                for hit_start, hit_end, _, _ in selected
            )
        ]

        resolved = tuple(
            item
            for _, item in sorted(
                resolved_by_key.values(),
                key=lambda value: (value[0], value[1].product_code),
            )
        )
        unique_ambiguous = tuple(
            dict.fromkeys((item.mention, item.candidates) for item in ambiguous)
        )
        return ProductResolution(
            version=PRODUCT_IDENTITY_RESOLVER_VERSION,
            resolved=resolved,
            unknown_codes=unknown_codes,
            ambiguous=tuple(
                AmbiguousProductMention(mention=mention, candidates=candidates)
                for mention, candidates in unique_ambiguous
            ),
        )
