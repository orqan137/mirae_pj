from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any


SCHEMA_VERSION = 2
EXTRACTOR_FINGERPRINT = "pension-product-evidence-v2"
PRODUCT_CODE_RE = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z0-9]{12}(?![A-Za-z0-9])"
)
VALID_PRODUCT_CODE_RE = re.compile(r"KR[A-Z0-9]{10}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
AXIS_TERMS: dict[str, tuple[str, ...]] = {
    "identity": ("상품명", "집합투자기구", "투자신탁", "운용회사"),
    "category": (
        "상품분류",
        "상품 분류",
        "자산유형",
        "자산 유형",
        "집합투자기구의 종류",
        "운용자산별 종류",
        "투자대상",
    ),
    "risk": ("투자위험등급", "투자 위험 등급", "위험등급", "위험 등급"),
    "class": (
        "판매클래스",
        "판매 클래스",
        "클래스",
        "Class",
        "종류(Class)",
    ),
    "fees": (
        "총보수",
        "총 보수",
        "보수율",
        "보수ㆍ비용",
        "보수·비용",
        "총비용",
        "총 비용",
        "수수료",
    ),
    "performance": (
        "수익률",
        "운용성과",
        "연평균",
        "최근 1년",
        "최근1년",
        "최근 2년",
        "최근2년",
        "최근 3년",
        "최근3년",
        "1~3년",
        "1-3년",
    ),
    "balance": (
        "시장잔고",
        "시장 잔고",
        "기간말 잔고",
        "기간말잔고",
        "설정 및 환매",
        "설정ㆍ환매",
        "설정·환매",
    ),
}
COMPARISON_AXES = ("category", "risk", "class", "fees", "performance", "balance")
COMPARISON_TERMS = ("비교", "차이", "대비", "추천", "어느 상품", "어떤 상품")


class ProductEvidenceError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class EvidenceBlock:
    axis: str
    page: int
    raw_text: str
    dates: tuple[str, ...]
    units: tuple[str, ...]
    class_labels: tuple[str, ...]
    status: str


@dataclass(frozen=True, slots=True)
class ProductEvidenceRecord:
    product_code: str
    canonical_source_id: str
    aliases: tuple[str, ...]
    source_path: str
    source_sha256: str
    title: str
    basis_dates: tuple[str, ...]
    evidence: dict[str, tuple[EvidenceBlock, ...]]


def normalize_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def requested_product_codes(question: str) -> tuple[str, ...]:
    seen: set[str] = set()
    codes: list[str] = []
    for match in PRODUCT_CODE_RE.finditer(unicodedata.normalize("NFKC", question)):
        code = match.group(0).upper()
        if code.startswith("KR") and code not in seen:
            seen.add(code)
            codes.append(code)
    return tuple(codes)


def requested_axes(question: str) -> tuple[str, ...]:
    axes = explicit_requested_axes(question)
    if axes:
        return axes
    normalized = normalize_text(question)
    if any(normalize_text(term) in normalized for term in COMPARISON_TERMS):
        return COMPARISON_AXES
    return ("identity", "category", "risk")


def explicit_requested_axes(question: str) -> tuple[str, ...]:
    """Return only axes literally requested, without comparison defaults."""

    normalized = normalize_text(question)
    return tuple(
        axis
        for axis, terms in AXIS_TERMS.items()
        if any(normalize_text(term) in normalized for term in terms)
    )


def _string_tuple(value: Any, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ProductEvidenceError(f"{field} must be an array of strings")
    if any(not item.strip() for item in value):
        raise ProductEvidenceError(f"{field} must not contain empty strings")
    if value != sorted(set(value)):
        raise ProductEvidenceError(f"{field} must contain sorted unique strings")
    return tuple(value)


def _safe_source_path(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ProductEvidenceError(f"{field} must be a non-empty relative POSIX path")
    candidate = PurePosixPath(value)
    if (
        candidate.is_absolute()
        or "\\" in value
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        raise ProductEvidenceError(f"{field} must be a safe relative POSIX path")
    return value


def _render_raw_at_line_boundary(raw_text: str, *, max_chars: int) -> str:
    """Fit raw evidence without silently cutting a table cell or number."""

    if max_chars <= 0:
        return ""
    if len(raw_text) <= max_chars:
        return raw_text
    marker = "\n[EVIDENCE TRUNCATED AT LINE BOUNDARY]"
    available = max_chars - len(marker)
    if available <= 0:
        return ""
    lines: list[str] = []
    used = 0
    for line in raw_text.splitlines():
        addition = len(line) + (1 if lines else 0)
        if used + addition > available:
            break
        lines.append(line)
        used += addition
    if not lines:
        return ""
    return "\n".join(lines) + marker


class ProductEvidenceIndex:
    def __init__(self, records: dict[str, ProductEvidenceRecord]) -> None:
        self._records = records

    @classmethod
    def from_path(
        cls,
        path: Path,
        *,
        expected_manifest_sha256: str | None = None,
        expected_record_count: int | None = None,
    ) -> "ProductEvidenceIndex":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ProductEvidenceError(f"product evidence index is missing: {path}") from exc
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ProductEvidenceError(f"product evidence index is invalid: {exc}") from exc
        if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
            raise ProductEvidenceError("unsupported product evidence schema")
        if payload.get("extractor_fingerprint") != EXTRACTOR_FINGERPRINT:
            raise ProductEvidenceError("unsupported product evidence extractor")
        manifest_digest = payload.get("corpus_manifest_sha256")
        if not isinstance(manifest_digest, str) or not SHA256_RE.fullmatch(manifest_digest):
            raise ProductEvidenceError("product evidence has an invalid corpus manifest digest")
        if expected_manifest_sha256 is not None and manifest_digest != expected_manifest_sha256:
            raise ProductEvidenceError("product evidence was built from a different corpus")
        raw_records = payload.get("records")
        if not isinstance(raw_records, list):
            raise ProductEvidenceError("product evidence records must be an array")
        declared_count = payload.get("record_count")
        if (
            isinstance(declared_count, bool)
            or not isinstance(declared_count, int)
            or declared_count != len(raw_records)
        ):
            raise ProductEvidenceError("product evidence record_count is inconsistent")
        if expected_record_count is not None and declared_count != expected_record_count:
            raise ProductEvidenceError(
                f"product evidence must contain {expected_record_count} records"
            )
        records: dict[str, ProductEvidenceRecord] = {}
        for position, item in enumerate(raw_records):
            if not isinstance(item, dict):
                raise ProductEvidenceError(f"record[{position}] must be an object")
            code = item.get("product_code")
            if not isinstance(code, str) or not VALID_PRODUCT_CODE_RE.fullmatch(code):
                raise ProductEvidenceError(f"record[{position}] has an invalid product code")
            if code in records:
                raise ProductEvidenceError(f"duplicate product evidence record: {code}")
            evidence_value = item.get("evidence")
            if not isinstance(evidence_value, dict):
                raise ProductEvidenceError(f"record[{position}].evidence must be an object")
            if set(evidence_value) != set(AXIS_TERMS):
                raise ProductEvidenceError(
                    f"record[{position}].evidence must contain every supported axis"
                )
            evidence: dict[str, tuple[EvidenceBlock, ...]] = {}
            for axis, blocks in evidence_value.items():
                if axis not in AXIS_TERMS or not isinstance(blocks, list):
                    raise ProductEvidenceError(f"record[{position}] has an invalid evidence axis")
                parsed: list[EvidenceBlock] = []
                for block in blocks:
                    if not isinstance(block, dict):
                        raise ProductEvidenceError("evidence blocks must be objects")
                    page = block.get("page")
                    raw_text = block.get("raw_text")
                    status = block.get("status")
                    if (
                        isinstance(page, bool)
                        or not isinstance(page, int)
                        or page < 1
                        or not isinstance(raw_text, str)
                        or not raw_text.strip()
                        or status != "raw_evidence_not_normalized"
                    ):
                        raise ProductEvidenceError("evidence block has invalid required fields")
                    parsed.append(
                        EvidenceBlock(
                            axis=axis,
                            page=page,
                            raw_text=raw_text,
                            dates=_string_tuple(block.get("dates"), field="dates"),
                            units=_string_tuple(block.get("units"), field="units"),
                            class_labels=_string_tuple(
                                block.get("class_labels"), field="class_labels"
                            ),
                            status=status,
                        )
                    )
                if [block.page for block in parsed] != sorted(block.page for block in parsed):
                    raise ProductEvidenceError("evidence blocks must be ordered by page")
                evidence[axis] = tuple(parsed)
            aliases = _string_tuple(item.get("aliases"), field="aliases")
            if not aliases or any(not VALID_PRODUCT_CODE_RE.fullmatch(alias) for alias in aliases):
                raise ProductEvidenceError(f"record[{position}] has invalid aliases")
            canonical_source_id = item.get("canonical_source_id")
            if (
                not isinstance(canonical_source_id, str)
                or canonical_source_id != aliases[0]
                or code not in aliases
            ):
                raise ProductEvidenceError(f"record[{position}] has inconsistent aliases")
            source_path = _safe_source_path(
                item.get("source_path"), field=f"record[{position}].source_path"
            )
            source_sha256 = item.get("source_sha256")
            title = item.get("title")
            if (
                not isinstance(source_sha256, str)
                or not SHA256_RE.fullmatch(source_sha256)
                or not isinstance(title, str)
                or not title.strip()
            ):
                raise ProductEvidenceError(f"record[{position}] has invalid source metadata")
            records[code] = ProductEvidenceRecord(
                product_code=code,
                canonical_source_id=canonical_source_id,
                aliases=aliases,
                source_path=source_path,
                source_sha256=source_sha256,
                title=title,
                basis_dates=_string_tuple(
                    item.get("basis_dates"), field=f"record[{position}].basis_dates"
                ),
                evidence=evidence,
            )
        for code, record in records.items():
            missing_aliases = set(record.aliases) - records.keys()
            if missing_aliases:
                raise ProductEvidenceError(
                    f"record {code} references missing aliases: {', '.join(sorted(missing_aliases))}"
                )
            for alias in record.aliases:
                peer = records[alias]
                if (
                    peer.aliases != record.aliases
                    or peer.canonical_source_id != record.canonical_source_id
                    or peer.source_sha256 != record.source_sha256
                    or peer.basis_dates != record.basis_dates
                    or peer.evidence != record.evidence
                ):
                    raise ProductEvidenceError(f"record {code} has an inconsistent alias group")
        return cls(records)

    @property
    def record_count(self) -> int:
        return len(self._records)

    @property
    def records(self) -> tuple[ProductEvidenceRecord, ...]:
        """Expose immutable records to deterministic resolver/comparator tools."""

        return tuple(self._records[code] for code in sorted(self._records))

    def evidence_coverage(
        self,
        *,
        product_codes: tuple[str, ...],
        axes: tuple[str, ...],
    ) -> dict[str, frozenset[str]]:
        """Return typed axis availability without exposing evidence text."""

        coverage: dict[str, frozenset[str]] = {}
        selected_axes = tuple(axis for axis in axes if axis in AXIS_TERMS)
        for code in product_codes:
            record = self._records.get(code)
            if record is None:
                continue
            coverage[code] = frozenset(
                axis for axis in selected_axes if record.evidence.get(axis)
            )
        return coverage

    def canonical_identities(
        self,
        *,
        product_codes: tuple[str, ...],
    ) -> dict[str, str]:
        """Return organizer-derived alias identity for known product codes."""

        return {
            code: record.canonical_source_id
            for code in product_codes
            if (record := self._records.get(code)) is not None
        }

    def get_record(self, product_code: str) -> ProductEvidenceRecord | None:
        return self._records.get(product_code)

    def render_context(
        self,
        *,
        product_codes: tuple[str, ...],
        axes: tuple[str, ...],
        max_chars: int,
        blocks_per_axis: int = 1,
    ) -> str:
        if max_chars <= 0 or blocks_per_axis <= 0:
            return ""
        codes = tuple(code for code in product_codes if code in self._records)
        if not codes:
            return ""
        selected_axes = tuple(axis for axis in axes if axis in AXIS_TERMS)
        candidates: list[tuple[str, str, ProductEvidenceRecord, EvidenceBlock]] = []
        # Interleave by axis and product so a downstream character limit cannot
        # erase all evidence for the second product in a comparison.
        for axis in selected_axes:
            for code in codes:
                record = self._records[code]
                for block in record.evidence.get(axis, ())[:blocks_per_axis]:
                    candidates.append((axis, code, record, block))
        sections: list[str] = []
        used = 0
        for position, (axis, code, record, block) in enumerate(candidates):
            annotations = (
                f"dates={','.join(block.dates) or 'unresolved'} "
                f"units={','.join(block.units) or 'unresolved'} "
                f"classes={','.join(block.class_labels) or 'unresolved'}"
            )
            header = (
                f"[PRODUCT EVIDENCE] product_code={code} axis={axis} "
                f"source_path={record.source_path} page={block.page} "
                f"citation_id=product:{code}:{axis}:page={block.page} "
                f"status={block.status}\n{annotations}"
            )
            separators = 2 if sections else 0
            remaining = max_chars - used - separators
            candidate_count = len(candidates) - position
            fair_share = remaining // candidate_count if candidate_count else remaining
            raw = _render_raw_at_line_boundary(
                block.raw_text,
                max_chars=max(fair_share - len(header) - 1, 0),
            )
            # A locator without any verbatim evidence is not useful. Skip it
            # instead of emitting metadata that could be mistaken for a fact.
            if not raw:
                continue
            section = f"{header}\n{raw}"
            if len(section) > remaining:
                continue
            sections.append(section)
            used += len(section) + separators
        return "\n\n".join(sections)
