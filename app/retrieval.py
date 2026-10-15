from __future__ import annotations

import hashlib
import json
import math
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath

from app.source_governance import (
    SourceGovernanceError,
    SourceGovernanceRecord,
    analyze_question_scope,
    assess_source_applicability,
    load_source_governance,
)


SUPPORTED_SUFFIXES = frozenset({".txt", ".md"})
MAX_FILE_BYTES = 5 * 1024 * 1024
TRUSTED_EXTRACTOR_FINGERPRINT = "pension-corpus-v2"
RETRIEVER_VERSION = "pension-local-retriever-v5"
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_WORD_RE = re.compile(r"[가-힣]+|[a-z0-9]+")
_HANGUL_RUN = re.compile(r"[가-힣]+")
_HANGUL_COMPOUND = re.compile(r"[가-힣]{5,}")
_MAX_SEGMENT = 12
_MIN_SEGMENT_COVERAGE = 0.70
_MIN_QUERY_CORPUS_COVERAGE = 0.60
_MIN_SUPPORTED_QUERY_WORDS = 3
_PRODUCT_CODE_RE = re.compile(
    r"(?<![A-Za-z0-9])kr[0-9a-z]{8,}(?![A-Za-z0-9])",
    flags=re.IGNORECASE,
)
_PRODUCT_SOURCE_ID_RE = re.compile(r"[A-Z0-9]{12}")
_LOCATOR_RE = re.compile(
    r"(?m)^\[(?:(PAGE)\s+(\d+)|(SLIDE)\s+(\d+)|(SHEET)\s+([^\]\r\n]+))\]\s*$"
)
_REQUIRED_MANIFEST_FIELDS = frozenset(
    {
        "source_id",
        "source_group",
        "source_path",
        "output_path",
        "media_type",
        "sha256",
        "size_bytes",
        "page_count",
        "extraction_method",
        "extractor_fingerprint",
        "review_status",
        "ocr_requested",
        "ocr_dpi",
        "min_semantic_chars_per_page",
        "extracted_chars",
        "title_hint",
        "text_sha256",
        "canonical_source_id",
        "product_aliases",
    }
)
_REQUIRED_OUTPUT_METADATA = frozenset(
    {
        "source_scope",
        "source_id",
        "source_group",
        "source_path",
        "source_sha256",
        "extraction_method",
        "extractor_fingerprint",
        "review_status",
        "ocr_requested",
        "ocr_dpi",
        "min_semantic_chars_per_page",
        "canonical_source_id",
        "product_aliases",
        "page_count",
    }
)
_QUERY_STOPWORDS = frozenset(
    {
        "것",
        "관련",
        "대해",
        "무엇",
        "뭔가요",
        "어떤가요",
        # Several organizer documents are FAQs, so their headings are questions
        # too. "연금 수령을 개시하려면 어떻게 해야 하나요" put a heading reading
        # "과학기술인연금 시스템에 접속하려면 어떻게 해야 하나요" top of the
        # corpus on the interrogative wording alone, and the answer was written
        # about that system. `_QUERY_COVERAGE_SCAFFOLDING` already treats these
        # as carrying no subject; scoring now agrees.
        "어떻게",
        "해야",
        "하나요",
        "되나요",
        "인가요",
        "있나요",
        "없나요",
        "하는지",
        "되는지",
        "알려줘",
        "알려주세요",
        "설명",
        "설명해줘",
        "설명해주세요",
        "비교",
        "비교해줘",
        "비교해주세요",
        "해주세요",
        "은",
        "는",
        "이",
        "가",
        "을",
        "를",
        "의",
        "과",
        "와",
        "에",
        "에서",
        "로",
        "으로",
        "도",
        "만",
    }
)
_KOREAN_QUERY_POSTPOSITIONS = (
    "에서",
    "에게",
    "까지",
    "부터",
    "으로",
    "처럼",
    "보다",
    "로",
    "에",
    "은",
    "는",
    "이",
    "가",
    "을",
    "를",
    "의",
    "도",
    "만",
)
_QUERY_COVERAGE_POSTPOSITIONS = _KOREAN_QUERY_POSTPOSITIONS + ("과", "와")
_QUERY_COVERAGE_SCAFFOLDING = frozenset(
    {
        "알려",
        "계산해",
        "궁금합니다",
        "때",
        "방법",
        "비교해",
        "설명해",
        "어떻게",
    }
)
_QUERY_COVERAGE_SCAFFOLDING_ENDINGS = (
    "궁금합니다",
    "되나요",
    "인가요",
    "하나요",
    "한가요",
    "주세요",
    "해줘",
)
_QUERY_COVERAGE_SCAFFOLDING_PREFIXES = (
    "뜻",
    "뭐",
    "뭔",
    "뭘",
    "무슨",
    "무엇",
    "어떤",
    "어떻",
)
_AXIS_TERMS: dict[str, tuple[str, ...]] = {
    "category": ("상품분류", "자산유형", "주식형", "채권형", "혼합형"),
    "risk": ("위험등급", "위험 등급", "투자위험", "위험"),
    "class": ("판매클래스", "판매 클래스", "클래스"),
    "fees": ("총보수", "합성총보수", "총비용", "보수", "수수료", "비용", "ter"),
    "performance": ("수익률", "운용성과", "성과", "1년", "2년", "3년"),
    "balance": (
        "시장잔고",
        "시장 잔고",
        "기간말 잔고",
        "기간말잔고",
        "순자산",
        "자산총액",
        "펀드 규모",
        "aum",
        "설정액",
    ),
}


class CorpusLoadError(RuntimeError):
    """Raised when organizer-derived evidence cannot be verified safely."""


@dataclass(frozen=True, slots=True)
class TextChunk:
    source: str
    source_id: str
    source_path: str
    source_group: str
    canonical_source_id: str
    product_aliases: tuple[str, ...]
    title: str
    index: int
    page: int | None
    locator: str | None
    text: str
    tokens: Counter[str]
    normalized_text: str
    axes: frozenset[str]
    governance: SourceGovernanceRecord | None


@dataclass(frozen=True, slots=True)
class RetrievedDocument:
    source: str
    source_id: str
    source_path: str
    source_group: str
    title: str
    index: int
    page: int | None
    text: str
    score: float
    locator: str | None = None
    canonical_source_id: str | None = None
    product_aliases: tuple[str, ...] = ()
    axes: frozenset[str] = frozenset()
    governance: SourceGovernanceRecord | None = None
    matched_query_indexes: tuple[int, ...] = ()


def evidence_citation_id(document: RetrievedDocument) -> str:
    """Return the citation the answer must quote for this chunk.

    The id is copied verbatim by the generation model, so every extra segment
    is another chance to mistype it. A missing locator used to be spelled out
    as `doc41:unavailable:chunk=1`, and models reliably abbreviated or
    reinvented that middle segment. Omitting it entirely yields
    `doc41:chunk=1`, which still identifies the chunk uniquely because the
    source id and chunk index already do.
    """

    if not document.locator:
        return f"{document.source_id}:chunk={document.index}"
    locator = re.sub(
        r"[^0-9A-Za-z가-힣_.=-]+",
        "_",
        document.locator,
    ).strip("_")
    if not locator:
        return f"{document.source_id}:chunk={document.index}"
    return f"{document.source_id}:{locator}:chunk={document.index}"


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def _words(text: str) -> list[str]:
    return _WORD_RE.findall(_normalize(text))


def _tokens(text: str) -> Counter[str]:
    """Return word and Korean trigram features after compatibility normalization."""

    result: Counter[str] = Counter()
    for word in _words(text):
        result[sys.intern(f"w:{word}")] += 1
        if re.fullmatch(r"[가-힣]+", word) and len(word) >= 3:
            for index in range(len(word) - 2):
                result[sys.intern(f"g3:{word[index:index + 3]}")] += 1
    return result


def _meaningful_query_words(
    text: str,
    *,
    excluded_words: set[str],
) -> list[str]:
    """Keep literal query words and add conservative Korean particle variants."""

    result: list[str] = []
    for word in _words(text):
        if word in _QUERY_STOPWORDS or word in excluded_words:
            continue
        result.append(word)
        if not re.fullmatch(r"[가-힣]+", word):
            continue
        for suffix in _KOREAN_QUERY_POSTPOSITIONS:
            if not word.endswith(suffix):
                continue
            stem = word[: -len(suffix)]
            if len(stem) >= 2 and stem not in _QUERY_STOPWORDS:
                result.append(stem)
            break
    return result


def _query_tokens(question: str, *, excluded_words: set[str]) -> Counter[str]:
    meaningful = _meaningful_query_words(
        question,
        excluded_words=excluded_words,
    )
    return _tokens(" ".join(meaningful))


def _semantic_units(text: str, *, max_chars: int) -> list[str]:
    """Split on document structure without cutting a word or a table row."""

    units: list[str] = []
    blocks = re.split(r"\n[ \t]*\n+", text)
    for raw_block in blocks:
        block = raw_block.strip(" \t\r\n")
        if not block:
            continue
        lines = [line.rstrip() for line in block.splitlines() if line.strip()]
        # Tabs/pipes and office extraction markers describe rows.  Keeping each
        # physical line intact is safer than slicing a row between its columns.
        if len(lines) > 1 or any("\t" in line or "|" in line for line in lines):
            candidates = lines
        else:
            candidates = [block]
        for candidate in candidates:
            if len(candidate) <= max_chars or "\t" in candidate or "|" in candidate:
                units.append(candidate)
                continue
            sentences = re.split(r"(?<=[.!?。！？])\s+", candidate)
            for sentence in sentences:
                if len(sentence) <= max_chars:
                    units.append(sentence)
                    continue
                words = sentence.split()
                if len(words) <= 1:
                    # A pathological token is kept intact.  It may exceed the
                    # target size, but inventing a word boundary is worse.
                    units.append(sentence)
                    continue
                current: list[str] = []
                size = 0
                for word in words:
                    extra = len(word) + (1 if current else 0)
                    if current and size + extra > max_chars:
                        units.append(" ".join(current))
                        current = []
                        size = 0
                    current.append(word)
                    size += len(word) + (1 if size else 0)
                if current:
                    units.append(" ".join(current))
    return units


def _paragraph_chunks(
    text: str,
    *,
    max_chars: int,
    overlap_chars: int = 220,
) -> list[str]:
    units = _semantic_units(text, max_chars=max_chars)
    if not units:
        return []
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for unit in units:
        separator = 1 if current else 0
        if current and size + separator + len(unit) > max_chars:
            chunks.append("\n".join(current))
            overlap: list[str] = []
            overlap_size = 0
            for previous in reversed(current):
                if overlap and overlap_size + 1 + len(previous) > overlap_chars:
                    break
                if len(previous) > overlap_chars:
                    break
                overlap.insert(0, previous)
                overlap_size += len(previous) + (1 if overlap_size else 0)
            current = overlap
            size = overlap_size
        current.append(unit)
        size += len(unit) + (1 if size else 0)
    if current:
        chunks.append("\n".join(current))
    return chunks


def _locator_sections(text: str) -> list[tuple[int | None, str | None, str]]:
    matches = list(_LOCATOR_RE.finditer(text))
    if matches:
        sections: list[tuple[int | None, str | None, str]] = []
        prefix = text[: matches[0].start()].strip(" \t\r\n")
        if prefix:
            sections.append((None, None, prefix))
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            content = text[match.end() : end].strip(" \t\r\n")
            if match.group(1):
                number = int(match.group(2))
                locator = f"page={number}"
                page = number
            elif match.group(3):
                number = int(match.group(4))
                locator = f"slide={number}"
                page = None
            else:
                name = match.group(5).strip()
                locator = f"sheet={name}"
                page = None
            sections.append((page, locator, content))
        return sections

    # Legacy outputs used form feeds.  Do not strip them before this point.
    if "\f" in text:
        return [
            (number, f"page={number}", page.strip(" \t\r\n"))
            for number, page in enumerate(text.split("\f"), start=1)
        ]
    return [(None, None, text.strip(" \t\r\n"))]


def _chunk_text(
    text: str,
    *,
    max_chars: int = 1_800,
) -> list[tuple[int | None, str | None, str]]:
    chunks: list[tuple[int | None, str | None, str]] = []
    for page, locator, section in _locator_sections(text):
        for chunk in _paragraph_chunks(section, max_chars=max_chars):
            chunks.append((page, locator, chunk))
    return chunks


def _metadata_and_content(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("[METADATA]") or "[CONTENT]" not in text:
        raise CorpusLoadError("derived output lacks the required metadata envelope")
    raw_metadata, content = text.split("[CONTENT]", maxsplit=1)
    metadata: dict[str, str] = {}
    for line in raw_metadata.splitlines()[1:]:
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not key or key in metadata:
            raise CorpusLoadError(f"malformed or duplicate output metadata line: {line!r}")
        metadata[key] = value.strip()
    missing = _REQUIRED_OUTPUT_METADATA - metadata.keys()
    if missing:
        raise CorpusLoadError(
            "derived output metadata is incomplete: " + ", ".join(sorted(missing))
        )
    # Remove only the separator newline.  lstrip()/strip() would erase a
    # leading form feed and shift legacy PDF page numbers.
    if content.startswith("\r\n"):
        content = content[2:]
    elif content.startswith("\n"):
        content = content[1:]
    return metadata, content


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_relative(value: object, *, field: str) -> PurePosixPath:
    if not isinstance(value, str) or not value:
        raise CorpusLoadError(f"{field} must be a non-empty relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or "\\" in value or any(part in {"", ".", ".."} for part in path.parts):
        raise CorpusLoadError(f"{field} is unsafe: {value!r}")
    return path


def _manifest(directory: Path) -> tuple[list[dict[str, object]], dict[str, object]]:
    manifest_path = directory / "manifest.json"
    if manifest_path.is_symlink():
        raise CorpusLoadError(f"knowledge manifest must not be a symlink: {manifest_path}")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CorpusLoadError(f"knowledge manifest is missing: {manifest_path}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CorpusLoadError(f"knowledge manifest is not valid readable JSON: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("documents"), list):
        raise CorpusLoadError("knowledge manifest must contain a documents array")
    if payload.get("extractor_fingerprint") != TRUSTED_EXTRACTOR_FINGERPRINT:
        raise CorpusLoadError("knowledge manifest has an untrusted extractor fingerprint")
    build_options = payload.get("build_options")
    if not isinstance(build_options, dict):
        raise CorpusLoadError("knowledge manifest build_options must be an object")
    if not isinstance(build_options.get("ocr"), bool):
        raise CorpusLoadError("knowledge manifest OCR build option must be boolean")
    ocr_dpi = build_options.get("ocr_dpi")
    if isinstance(ocr_dpi, bool) or not isinstance(ocr_dpi, int) or not 100 <= ocr_dpi <= 600:
        raise CorpusLoadError("knowledge manifest OCR DPI build option is invalid")
    minimum = build_options.get("min_semantic_chars_per_page")
    if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 1:
        raise CorpusLoadError(
            "knowledge manifest minimum semantic characters build option is invalid"
        )
    documents = payload["documents"]
    count = payload.get("document_count")
    if isinstance(count, bool) or not isinstance(count, int) or count != len(documents):
        raise CorpusLoadError("knowledge manifest document_count is inconsistent")
    if not all(isinstance(item, dict) for item in documents):
        raise CorpusLoadError("knowledge manifest documents must be objects")
    source = payload.get("source")
    if source is not None:
        if source != "organizer-provided pension track data":
            raise CorpusLoadError("knowledge manifest has an unexpected corpus source")
        group_counts = Counter(
            item.get("source_group") for item in documents if isinstance(item, dict)
        )
        if group_counts != Counter({"general": 58, "product": 100}):
            raise CorpusLoadError(
                "organizer corpus must contain exactly 58 general and 100 product documents"
            )
    return documents, build_options


def _axes(text: str) -> frozenset[str]:
    normalized = _normalize(text)
    return frozenset(
        axis
        for axis, terms in _AXIS_TERMS.items()
        if any(term in normalized for term in terms)
    )


class LocalRetriever:
    def __init__(self, chunks: list[TextChunk]) -> None:
        self._chunks = chunks
        self._source_count = len(
            {(chunk.source_group, chunk.source_id) for chunk in chunks}
        )
        self._source_ids = tuple(
            dict.fromkeys(
                chunk.source_id for chunk in chunks if chunk.source_group == "product"
            )
        )
        self._source_id_lookup = {_normalize(value): value for value in self._source_ids}
        self._document_frequency: Counter[str] = Counter()
        self._document_frequency_by_group: dict[str, Counter[str]] = defaultdict(
            Counter
        )
        for chunk in chunks:
            self._document_frequency.update(chunk.tokens.keys())
            self._document_frequency_by_group[chunk.source_group].update(
                chunk.tokens.keys()
            )
        # Korean word forms actually observed in the corpus. Used to split
        # unspaced compounds in a question without a morphological analyser or
        # any hand-maintained term list.
        self._vocabulary: frozenset[str] = frozenset(
            term[2:]
            for term, frequency in self._document_frequency.items()
            if term.startswith("w:")
            and frequency >= 2
            and len(term) >= 4
            and _HANGUL_RUN.fullmatch(term[2:])
        )
        self._average_length = (
            sum(sum(chunk.tokens.values()) for chunk in chunks) / len(chunks)
            if chunks
            else 1.0
        )

    @classmethod
    def from_directory(
        cls,
        directory: Path,
        *,
        source_governance_path: Path | None = None,
    ) -> "LocalRetriever":
        directory = directory.resolve()
        if not directory.is_dir():
            raise CorpusLoadError(f"knowledge directory is missing: {directory}")
        documents, build_options = _manifest(directory)
        governance_records: dict[str, SourceGovernanceRecord] = {}
        if source_governance_path is not None:
            expected_general_ids = {
                str(item.get("source_id"))
                for item in documents
                if item.get("source_group") == "general"
            }
            try:
                governance_records = load_source_governance(
                    source_governance_path.resolve(),
                    expected_source_ids=expected_general_ids,
                ).records
            except SourceGovernanceError as exc:
                raise CorpusLoadError(str(exc)) from exc
        chunks: list[TextChunk] = []
        expected_outputs: set[str] = set()
        seen_identities: set[tuple[str, str]] = set()
        product_alias_groups: dict[str, tuple[tuple[str, ...], str]] = {}
        product_source_ids: set[str] = set()

        for position, item in enumerate(documents):
            missing = _REQUIRED_MANIFEST_FIELDS - item.keys()
            if missing:
                raise CorpusLoadError(
                    f"manifest document[{position}] is incomplete: {', '.join(sorted(missing))}"
                )
            source_id = item["source_id"]
            source_group = item["source_group"]
            source_path_value = item["source_path"]
            title = item["title_hint"]
            if not all(
                isinstance(value, str) and value
                for value in (source_id, source_path_value, title)
            ):
                raise CorpusLoadError(f"manifest document[{position}] has invalid identity fields")
            if not isinstance(source_group, str) or source_group not in {"general", "product"}:
                raise CorpusLoadError(f"manifest document[{position}] has invalid source_group")
            if source_group == "product" and not _PRODUCT_SOURCE_ID_RE.fullmatch(source_id):
                raise CorpusLoadError(f"manifest document[{position}] has invalid product ID")
            identity = (source_group, source_id)
            if identity in seen_identities:
                raise CorpusLoadError(
                    f"duplicate manifest source identity: {source_group}/{source_id}"
                )
            seen_identities.add(identity)

            output_relative = _safe_relative(
                item["output_path"], field=f"document[{position}].output_path"
            )
            source_relative = _safe_relative(
                source_path_value, field=f"document[{position}].source_path"
            )
            expected_source_root = "일반문서" if source_group == "general" else "투자설명서"
            if source_relative.parts[0] != expected_source_root:
                raise CorpusLoadError(
                    f"source path for {source_id} must be below {expected_source_root}"
                )
            if source_group == "product" and (
                len(source_relative.parts) < 2 or source_relative.parts[1] != source_id
            ):
                raise CorpusLoadError(f"product source path does not match ID: {source_id}")
            output_name = output_relative.as_posix()
            if output_name in expected_outputs:
                raise CorpusLoadError(f"duplicate manifest output: {output_name}")
            expected_outputs.add(output_name)
            output_group = "general" if source_group == "general" else "products"
            expected_output = f"{output_group}/{source_id}.txt"
            if output_name != expected_output:
                raise CorpusLoadError(
                    f"non-canonical output path for {source_group}/{source_id}: {output_name}"
                )
            output = directory.joinpath(*output_relative.parts)
            has_symlink_parent = any(
                parent != directory and parent.is_symlink()
                for parent in output.parents
                if parent.is_relative_to(directory)
            )
            if (
                not output.is_file()
                or output.is_symlink()
                or has_symlink_parent
                or not output.resolve().is_relative_to(directory)
            ):
                raise CorpusLoadError(f"manifest output is missing or unsafe: {output_name}")
            if (
                output.suffix.lower() not in SUPPORTED_SUFFIXES
                or output.stat().st_size > MAX_FILE_BYTES
            ):
                raise CorpusLoadError(f"manifest output is unsupported or oversized: {output_name}")
            text_digest = item["text_sha256"]
            if not isinstance(text_digest, str) or not _SHA256_RE.fullmatch(text_digest):
                raise CorpusLoadError(f"invalid output digest for {output_name}")
            if _sha256(output) != text_digest:
                raise CorpusLoadError(f"derived output SHA-256 mismatch: {output_name}")

            try:
                raw_text = output.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                raise CorpusLoadError(
                    f"derived output is not valid readable UTF-8: {output_name}"
                ) from exc
            metadata, text = _metadata_and_content(raw_text)
            review_status = item["review_status"]
            source_digest = item["sha256"]
            page_count = item["page_count"]
            ocr_requested = item["ocr_requested"]
            item_ocr_dpi = item["ocr_dpi"]
            item_minimum = item["min_semantic_chars_per_page"]
            extractor = item["extractor_fingerprint"]
            canonical_source_id = item["canonical_source_id"]
            product_aliases = item["product_aliases"]
            if review_status not in {"raw", "quarantined"}:
                raise CorpusLoadError(f"invalid review status for {source_id}")
            expected_review_status = (
                "quarantined"
                if source_group == "general" and source_id == "doc29"
                else "raw"
            )
            if review_status != expected_review_status:
                raise CorpusLoadError(
                    f"unexpected review status for {source_group}/{source_id}"
                )
            size_bytes = item["size_bytes"]
            if isinstance(size_bytes, bool) or not isinstance(size_bytes, int) or size_bytes < 1:
                raise CorpusLoadError(f"invalid source size for {source_id}")
            if not isinstance(source_digest, str) or not _SHA256_RE.fullmatch(source_digest):
                raise CorpusLoadError(f"invalid source digest for {source_id}")
            if extractor != TRUSTED_EXTRACTOR_FINGERPRINT:
                raise CorpusLoadError(f"untrusted extractor fingerprint for {source_id}")
            if not isinstance(canonical_source_id, str) or not canonical_source_id:
                raise CorpusLoadError(f"invalid canonical source ID for {source_id}")
            if not isinstance(product_aliases, list) or not all(
                isinstance(alias, str) and alias for alias in product_aliases
            ):
                raise CorpusLoadError(f"invalid product aliases for {source_id}")
            if source_group == "product":
                product_source_ids.add(source_id)
                aliases = tuple(product_aliases)
                if (
                    len(set(aliases)) != len(aliases)
                    or any(not _PRODUCT_SOURCE_ID_RE.fullmatch(alias) for alias in aliases)
                    or source_id not in aliases
                    or canonical_source_id != aliases[0]
                ):
                    raise CorpusLoadError(f"inconsistent product aliases for {source_id}")
                expected_alias_group = product_alias_groups.setdefault(
                    canonical_source_id,
                    (aliases, source_digest),
                )
                if expected_alias_group != (aliases, source_digest):
                    raise CorpusLoadError(
                        f"conflicting duplicate-product metadata for {canonical_source_id}"
                    )
            elif product_aliases or canonical_source_id != source_id:
                raise CorpusLoadError(f"general document has product alias metadata: {source_id}")
            if not isinstance(ocr_requested, bool):
                raise CorpusLoadError(f"invalid OCR flag for {source_id}")
            if ocr_requested != build_options["ocr"]:
                raise CorpusLoadError(f"OCR flag differs from build options for {source_id}")
            expected_ocr_dpi = build_options["ocr_dpi"] if ocr_requested else None
            expected_minimum = (
                build_options["min_semantic_chars_per_page"]
                if ocr_requested
                else None
            )
            if item_ocr_dpi != expected_ocr_dpi or item_minimum != expected_minimum:
                raise CorpusLoadError(
                    f"OCR parameters differ from build options for {source_id}"
                )
            if page_count is not None and (
                isinstance(page_count, bool) or not isinstance(page_count, int) or page_count < 1
            ):
                raise CorpusLoadError(f"invalid page count for {source_id}")
            media_type = item["media_type"]
            expected_media_type = source_relative.suffix.lower().removeprefix(".")
            if (
                media_type not in {"pdf", "docx", "xlsx", "pptx"}
                or media_type != expected_media_type
            ):
                raise CorpusLoadError(f"media type mismatch for {source_id}")
            extraction_method = item["extraction_method"]
            if not isinstance(extraction_method, str) or not extraction_method:
                raise CorpusLoadError(f"invalid extraction method for {source_id}")
            expected_metadata = {
                "source_scope": "organizer_provided",
                "source_id": source_id,
                "source_group": source_group,
                "source_path": source_relative.as_posix(),
                "source_sha256": source_digest,
                "extraction_method": item["extraction_method"],
                "extractor_fingerprint": extractor,
                "review_status": review_status,
                "ocr_requested": "true" if ocr_requested else "false",
                "ocr_dpi": "" if item_ocr_dpi is None else str(item_ocr_dpi),
                "min_semantic_chars_per_page": (
                    "" if item_minimum is None else str(item_minimum)
                ),
                "canonical_source_id": canonical_source_id,
                "product_aliases": ",".join(product_aliases),
                "page_count": "" if page_count is None else str(page_count),
            }
            for key, expected in expected_metadata.items():
                if metadata.get(key) != expected:
                    raise CorpusLoadError(
                        f"output metadata mismatch for {source_id}: {key}"
                    )
            extracted_chars = item["extracted_chars"]
            if (
                isinstance(extracted_chars, bool)
                or not isinstance(extracted_chars, int)
                or extracted_chars != len(text.strip())
            ):
                raise CorpusLoadError(f"extracted character count mismatch for {source_id}")

            # doc29 is excluded even if a future manifest bug labels it as raw.
            if source_group == "general" and source_id == "doc29":
                if review_status != "quarantined":
                    raise CorpusLoadError("general/doc29 must remain quarantined")
                continue
            if review_status == "quarantined":
                continue

            located_sections = _locator_sections(text)
            if media_type == "pdf" and page_count is not None:
                pages = [page for page, _, _ in located_sections]
                if pages != list(range(1, page_count + 1)):
                    raise CorpusLoadError(
                        f"PDF page markers are incomplete or out of order for {source_id}"
                    )
            source = output_name
            for index, (page, locator, chunk) in enumerate(_chunk_text(text), start=1):
                if not chunk.strip():
                    continue
                normalized_chunk = _normalize(chunk)
                chunks.append(
                    TextChunk(
                        source=source,
                        source_id=source_id,
                        source_path=source_relative.as_posix(),
                        source_group=source_group,
                        canonical_source_id=canonical_source_id,
                        product_aliases=tuple(product_aliases),
                        title=title,
                        index=index,
                        page=page,
                        locator=locator,
                        text=chunk,
                        tokens=_tokens(chunk),
                        normalized_text=normalized_chunk,
                        axes=_axes(chunk),
                        governance=governance_records.get(source_id),
                    )
                )

        for canonical_source_id, (aliases, _) in product_alias_groups.items():
            if not set(aliases) <= product_source_ids:
                raise CorpusLoadError(
                    f"product alias group references an absent source: {canonical_source_id}"
                )

        actual_outputs = {
            path.relative_to(directory).as_posix()
            for path in directory.rglob("*")
            if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
        }
        if actual_outputs != expected_outputs:
            missing = sorted(expected_outputs - actual_outputs)
            extra = sorted(actual_outputs - expected_outputs)
            raise CorpusLoadError(
                f"manifest/output set mismatch; missing={missing!r}, extra={extra!r}"
            )
        return cls(chunks)

    @property
    def document_count(self) -> int:
        """Number of indexed, non-quarantined source documents."""

        return self._source_count

    @property
    def chunk_count(self) -> int:
        return len(self._chunks)

    def _segment_compound(self, run: str) -> tuple[str, ...]:
        """Greedy longest-match split of an unspaced Korean run."""

        pieces: list[str] = []
        index = 0
        while index < len(run):
            for end in range(min(len(run), index + _MAX_SEGMENT), index + 1, -1):
                candidate = run[index:end]
                if len(candidate) >= 2 and candidate in self._vocabulary:
                    pieces.append(candidate)
                    index = end
                    break
            else:
                index += 1
        return tuple(pieces)

    def _segmented_words(self, words: tuple[str, ...]) -> tuple[str, ...]:
        """Return extra query words recovered from unspaced Korean compounds.

        Korean is routinely written without spaces between a noun and what
        follows it, so "연금수령한도를알려주세요" arrives as one token that
        matches no indexed word. Splitting it on vocabulary the corpus already
        contains restores the exact-match signal that BM25 depends on.
        """

        segments: list[str] = []
        for word in words:
            for run in _HANGUL_COMPOUND.findall(word):
                if run in self._vocabulary:
                    continue
                pieces = self._segment_compound(run)
                if len(pieces) < 2:
                    continue
                # A run that only partly resolves to corpus vocabulary is more
                # likely out-of-domain wording than a compound worth splitting.
                # Splitting it anyway would manufacture matches for questions
                # the corpus cannot answer, so require most of the run to be
                # covered before the segments are trusted.
                covered = sum(len(piece) for piece in pieces)
                if covered / len(run) < _MIN_SEGMENT_COVERAGE:
                    continue
                segments.extend(
                    piece for piece in pieces if piece not in _QUERY_STOPWORDS
                )
        return tuple(dict.fromkeys(segments))

    def _idf(self, term: str) -> float:
        total = len(self._chunks)
        frequency = self._document_frequency.get(term, 0)
        return math.log1p((total - frequency + 0.5) / (frequency + 0.5))

    def _term_frequency(
        self,
        term: str,
        *,
        source_groups: set[str] | None,
    ) -> int:
        if source_groups is None:
            return self._document_frequency.get(term, 0)
        return sum(
            self._document_frequency_by_group[group].get(term, 0)
            for group in source_groups
        )

    @staticmethod
    def _is_coverage_scaffolding(word: str) -> bool:
        """Return whether a word expresses the request rather than its topic."""

        return (
            word in _QUERY_COVERAGE_SCAFFOLDING
            or any(
                word.endswith(ending)
                for ending in _QUERY_COVERAGE_SCAFFOLDING_ENDINGS
            )
            or any(
                word.startswith(prefix)
                for prefix in _QUERY_COVERAGE_SCAFFOLDING_PREFIXES
            )
        )

    @staticmethod
    def _is_scoring_scaffolding(word: str) -> bool:
        """Ignore interrogatives that can create a false exact-hit burden."""

        return any(
            word.startswith(prefix)
            for prefix in _QUERY_COVERAGE_SCAFFOLDING_PREFIXES
        )

    def _coverage_words(
        self,
        query_words: tuple[str, ...],
        *,
        source_groups: set[str] | None,
    ) -> tuple[str, ...]:
        """Collapse surface variants into corpus-comparable semantic units."""

        units: list[str] = []
        for word in dict.fromkeys(query_words):
            # ``_meaningful_query_words`` keeps both a particle-bearing form
            # and its stem for scoring. Count the concept only once here.
            explicit_stem = next(
                (
                    stem
                    for stem in query_words
                    if word != stem
                    and word.startswith(stem)
                    and word[len(stem) :] in _QUERY_COVERAGE_POSTPOSITIONS
                ),
                None,
            )
            if explicit_stem is not None:
                continue
            # The tokenizer separates ``DC형이`` into ``dc`` and ``형이``.
            # The ordinary stemmer deliberately excludes one-character stems,
            # so recover one here only when the surface form is absent and the
            # stripped form is actually present in the selected corpus.
            inferred_stem = next(
                (
                    word[: -len(suffix)]
                    for suffix in _QUERY_COVERAGE_POSTPOSITIONS
                    if word.endswith(suffix)
                    and word[: -len(suffix)]
                    and self._term_frequency(
                        f"w:{word}", source_groups=source_groups
                    ) == 0
                    and self._term_frequency(
                        f"w:{word[: -len(suffix)]}", source_groups=source_groups
                    ) > 0
                ),
                None,
            )
            if inferred_stem is not None:
                candidates = (inferred_stem,)
            else:
                # Segment first: a spacing-collapsed question can end in a
                # polite request expression while still containing several
                # supported topic words before it.
                segmented = self._segmented_words((word,))
                candidates = segmented or (
                    () if self._is_coverage_scaffolding(word) else (word,)
                )
            for candidate in candidates:
                if (
                    self._is_coverage_scaffolding(candidate)
                    or candidate in units
                ):
                    continue
                units.append(candidate)
        return tuple(units)

    def _query_has_corpus_support(
        self,
        query_words: tuple[str, ...],
        *,
        source_groups: set[str] | None,
    ) -> bool:
        """Reject a query supported only by generic words in the corpus.

        Exact word hits are strongest. For an unseen Korean surface form,
        observed trigrams provide bounded tolerance for particles and a single
        typo. Coverage is length-weighted so an absent compound such as an
        unsupported pension type cannot be hidden by two short request words.
        Three distinct exact concepts are independently sufficient; this
        preserves colloquial in-domain questions without letting one known
        pension word override several unsupported relation words.
        """

        units = tuple(word for word in query_words if not word.isdigit())
        if not units:
            return False
        supported_exact = 0
        supported_weight = 0.0
        total_weight = 0
        for word in units:
            weight = len(word)
            total_weight += weight
            exact = self._term_frequency(
                f"w:{word}",
                source_groups=source_groups,
            ) > 0
            if exact:
                support = 1.0
                supported_exact += 1
            else:
                support = 0.0
                if _HANGUL_RUN.fullmatch(word) and len(word) >= 3:
                    trigrams = tuple(
                        f"g3:{word[index:index + 3]}"
                        for index in range(len(word) - 2)
                    )
                    support = sum(
                        self._term_frequency(term, source_groups=source_groups) > 0
                        for term in trigrams
                    ) / len(trigrams)
                # An inflected word is supported by however much of it the
                # corpus actually holds: "압류될" is "압류" plus an ending, and
                # its single trigram matches nothing, so the corpus was read as
                # having no word for the question at all. Splitting on indexed
                # vocabulary measures the same thing directly, and the piece
                # must occur in the groups being searched.
                # Only a known stem carrying an ending. Korean puts a modifier
                # in front and an ending behind, so a single piece that starts
                # the word is an inflection of that word — "압류될" is "압류"
                # plus 될. A piece that starts later is the tail of a compound
                # whose head the corpus does not have, which is what this gate
                # exists to catch: reading "농지연금" as "연금" would hide it.
                pieces = self._segment_compound(word)
                if len(pieces) == 1 and word.startswith(pieces[0]):
                    covered = sum(
                        len(piece)
                        for piece in pieces
                        if self._term_frequency(
                            f"w:{piece}", source_groups=source_groups
                        )
                        > 0
                    )
                    support = max(support, covered / len(word))
            supported_weight += weight * support
        coverage = supported_weight / total_weight
        return (
            coverage >= _MIN_QUERY_CORPUS_COVERAGE
            or supported_exact >= _MIN_SUPPORTED_QUERY_WORDS
        )

    def _requested_ids(self, normalized_question: str) -> tuple[str, ...]:
        matches: list[tuple[int, str]] = []
        for normalized_id, source_id in self._source_id_lookup.items():
            match = re.search(
                rf"(?<![A-Za-z0-9]){re.escape(normalized_id)}(?![A-Za-z0-9])",
                normalized_question,
                flags=re.IGNORECASE,
            )
            if match:
                matches.append((match.start(), source_id))
        return tuple(source_id for _, source_id in sorted(matches))

    def _score(
        self,
        chunk: TextChunk,
        query: Counter[str],
        *,
        query_words: tuple[str, ...],
        requested_axes: frozenset[str],
    ) -> float | None:
        if not query:
            return None
        length = max(sum(chunk.tokens.values()), 1)
        bm25 = 0.0
        exact_hits = 0
        trigram_hits = 0
        exact_total = sum(1 for term in query if term.startswith("w:"))
        trigram_total = sum(1 for term in query if term.startswith("g3:"))
        for term, query_count in query.items():
            frequency = chunk.tokens.get(term, 0)
            if frequency <= 0:
                continue
            if term.startswith("w:"):
                exact_hits += 1
                kind_weight = 1.8
            else:
                trigram_hits += 1
                kind_weight = 0.32
            denominator = frequency + 1.2 * (
                0.25 + 0.75 * length / max(self._average_length, 1.0)
            )
            bm25 += (
                self._idf(term)
                * ((frequency * 2.2) / denominator)
                * kind_weight
                * min(query_count, 2)
            )

        exact_coverage = exact_hits / max(exact_total, 1)
        trigram_coverage = trigram_hits / max(trigram_total, 1)
        normalized_title = _normalize(chunk.title)
        title_boost = 0.0
        for word in query_words:
            if word in normalized_title:
                title_boost += self._idf(f"w:{word}") * 2.7 + 1.0
        phrase_boost = 0.0
        if len(query_words) >= 2:
            for first, second in zip(query_words, query_words[1:]):
                pattern = rf"{re.escape(first)}[\s\W_]*{re.escape(second)}"
                if re.search(pattern, chunk.normalized_text):
                    phrase_boost += 1.5
        axis_hits = len(requested_axes & chunk.axes)

        # A code match is a filter, never evidence by itself.  At least one
        # meaningful word, or substantial Korean-trigram coverage, is required.
        # A stricter variant briefly required two matching content words for
        # longer questions. It cost recall (KorQuAD R@6 0.963 → 0.942) and
        # bought nothing: the scope guard rejects out-of-corpus questions
        # before retrieval matters, and abstention measured 1.0 either way.
        body_relevant = (
            exact_hits >= 1
            or (trigram_hits >= 2 and trigram_coverage >= 0.38)
            or axis_hits > 0
        )
        if not body_relevant:
            return None
        score = (
            bm25
            + exact_coverage * 2.0
            + trigram_coverage * 0.8
            + title_boost
            + phrase_boost
            + axis_hits * 2.0
        )
        return score if score >= 1.25 else None

    @staticmethod
    def _diversified(items: list[RetrievedDocument], limit: int) -> list[RetrievedDocument]:
        by_source: dict[str, list[RetrievedDocument]] = defaultdict(list)
        seen_evidence: dict[str, set[tuple[str | None, str]]] = defaultdict(set)
        for item in items:
            diversity_key = (
                f"{item.source_group}:{item.canonical_source_id}"
                if item.canonical_source_id
                else item.source
            )
            evidence_key = (item.locator, item.text)
            if evidence_key in seen_evidence[diversity_key]:
                continue
            seen_evidence[diversity_key].add(evidence_key)
            by_source[diversity_key].append(item)
        ordered_sources = sorted(
            by_source,
            key=lambda source: (-by_source[source][0].score, source),
        )
        selected: list[RetrievedDocument] = []
        depth = 0
        while len(selected) < limit:
            added = False
            for source in ordered_sources:
                source_items = by_source[source]
                if depth < len(source_items):
                    selected.append(source_items[depth])
                    added = True
                    if len(selected) >= limit:
                        break
            if not added:
                break
            depth += 1
        return selected

    @staticmethod
    def _axis_diverse(
        items: list[RetrievedDocument],
        chunks_by_key: dict[tuple[str, int], TextChunk],
        *,
        axes: tuple[str, ...],
        limit: int,
    ) -> list[RetrievedDocument]:
        selected: list[RetrievedDocument] = []
        keys: set[tuple[str, int]] = set()
        covered: set[str] = set()
        for axis in axes:
            if axis in covered or len(selected) >= limit:
                continue
            candidate = next(
                (
                    item
                    for item in items
                    if (item.source, item.index) not in keys
                    and axis in chunks_by_key[(item.source, item.index)].axes
                ),
                None,
            )
            if candidate is not None:
                selected.append(candidate)
                key = (candidate.source, candidate.index)
                keys.add(key)
                covered.update(chunks_by_key[key].axes)
        for item in items:
            if len(selected) >= limit:
                break
            key = (item.source, item.index)
            if key not in keys:
                selected.append(item)
                keys.add(key)
        return selected

    def retrieve(
        self,
        question: str,
        *,
        top_k: int,
        source_groups: set[str] | None = None,
        resolved_product_codes: tuple[str, ...] = (),
    ) -> list[RetrievedDocument]:
        if top_k <= 0:
            return []
        normalized_question = _normalize(question)
        explicit_requested_ids = self._requested_ids(normalized_question)
        requested_ids = tuple(
            dict.fromkeys(
                (
                    *explicit_requested_ids,
                    *(
                        code
                        for code in resolved_product_codes
                        if _normalize(code) in self._source_id_lookup
                    ),
                )
            )
        )
        requested_id_set = set(requested_ids)
        explicit_code_literals = set(_PRODUCT_CODE_RE.findall(normalized_question))
        normalized_explicit_ids = {
            _normalize(source_id) for source_id in explicit_requested_ids
        }
        has_unknown_explicit_code = bool(
            {value.casefold() for value in explicit_code_literals}
            - normalized_explicit_ids
        )
        excluded = {_normalize(source_id) for source_id in requested_ids}
        excluded.update(value.casefold() for value in explicit_code_literals)
        surface_query_words = tuple(
            _meaningful_query_words(
                question,
                excluded_words=excluded,
            )
        )
        semantic_query_words = self._coverage_words(
            surface_query_words,
            source_groups=source_groups,
        )
        if requested_ids == () and not self._query_has_corpus_support(
            semantic_query_words,
            source_groups=source_groups,
        ):
            return []
        # Preserve the surface and spacing-recovery features used by ranking.
        # Coverage and ranking intentionally serve different purposes, but
        # both remove interrogative scaffolding so it cannot inflate the exact
        # hit requirement for short queries such as ``DC는 무슨 뜻이죠``.
        # Other surface request words remain ranking features for compatibility
        # with the measured spacing-collapse baseline.
        query_words = tuple(
            word
            for word in surface_query_words
            if not self._is_scoring_scaffolding(word)
        )
        query = _tokens(
            " ".join(query_words + self._segmented_words(surface_query_words))
        )
        requested_axes = tuple(
            axis
            for axis, terms in _AXIS_TERMS.items()
            if any(term in normalized_question for term in terms)
        )
        if (
            requested_ids
            and not requested_axes
            and any(term in normalized_question for term in ("비교", "차이"))
        ):
            requested_axes = tuple(_AXIS_TERMS)
            axis_query = "상품분류 위험등급 판매클래스 총보수 수익률 기간말 잔고"
            query.update(_tokens(axis_query))
            query_words += tuple(_words(axis_query))
        if not query:
            return []
        requested_axis_set = frozenset(requested_axes)
        question_scope = analyze_question_scope(question)

        scored: list[RetrievedDocument] = []
        chunks_by_key: dict[tuple[str, int], TextChunk] = {}
        for chunk in self._chunks:
            if source_groups is not None and chunk.source_group not in source_groups:
                continue
            if chunk.source_group == "product" and explicit_code_literals:
                # Unknown or non-exact codes must not fall through to a similar product.
                if has_unknown_explicit_code or chunk.source_id not in requested_id_set:
                    continue
            elif requested_id_set and chunk.source_group == "product":
                if chunk.source_id not in requested_id_set:
                    continue
            score = self._score(
                chunk,
                query,
                query_words=query_words,
                requested_axes=requested_axis_set,
            )
            if score is None:
                continue
            if chunk.source_group == "general" and chunk.governance is not None:
                applicability = assess_source_applicability(
                    record=chunk.governance,
                    scope=question_scope,
                    question=question,
                )
                if applicability.applicable:
                    # Semantic relevance remains primary for undated questions.
                    # A date-applicable interval receives a stronger boost so a
                    # correct version cannot be pushed below top_k by an
                    # inapplicable but lexically similar document.
                    score += (
                        3.0
                        if question_scope.explicit_date
                        and chunk.governance.effective_date_known
                        else 2.0
                    )
                else:
                    # Keep a mismatched candidate observable when no applicable
                    # source exists; the assessor will record and block it.
                    # Applicability is a tie-breaker, not a license for a
                    # wrong-topic dated document to outrank direct evidence.
                    score -= 3.0
            document = RetrievedDocument(
                source=chunk.source,
                source_id=chunk.source_id,
                source_path=chunk.source_path,
                source_group=chunk.source_group,
                title=chunk.title,
                index=chunk.index,
                page=chunk.page,
                locator=chunk.locator,
                text=chunk.text,
                score=score,
                canonical_source_id=chunk.canonical_source_id,
                product_aliases=chunk.product_aliases,
                axes=chunk.axes,
                governance=chunk.governance,
            )
            scored.append(document)
            chunks_by_key[(chunk.source, chunk.index)] = chunk
        scored.sort(key=lambda item: (-item.score, item.source, item.index))
        if not requested_ids:
            return self._diversified(scored, top_k)

        by_id: dict[str, list[RetrievedDocument]] = {
            source_id: [
                item
                for item in scored
                if item.source_group == "product" and item.source_id == source_id
            ]
            for source_id in requested_ids
        }
        base, remainder = divmod(top_k, len(requested_ids))
        per_id: dict[str, list[RetrievedDocument]] = {}
        quotas: dict[str, int] = {}
        for position, source_id in enumerate(requested_ids):
            quota = base + (1 if position < remainder else 0)
            quotas[source_id] = quota
            per_id[source_id] = self._axis_diverse(
                by_id[source_id],
                chunks_by_key,
                axes=requested_axes,
                limit=quota,
            )

        # Interleave codes so truncation downstream cannot erase one side of a
        # comparison.  With two codes/top_k=6 this yields a strict 3:3 quota.
        selected: list[RetrievedDocument] = []
        depth = 0
        while len(selected) < top_k:
            added = False
            for source_id in requested_ids:
                items = per_id[source_id]
                if depth < len(items):
                    selected.append(items[depth])
                    added = True
                    if len(selected) >= top_k:
                        break
            if not added:
                break
            depth += 1

        selected_keys = {(item.source, item.index) for item in selected}
        selected_product_counts = Counter(
            item.source_id for item in selected if item.source_group == "product"
        )
        # Fill an evidence shortage with remaining requested-product chunks,
        # then relevant general documents.  Never admit a different product.
        for item in scored:
            if len(selected) >= top_k:
                break
            key = (item.source, item.index)
            if key in selected_keys:
                continue
            if item.source_group == "product" and item.source_id not in requested_id_set:
                continue
            if (
                item.source_group == "product"
                and selected_product_counts[item.source_id] >= quotas[item.source_id]
            ):
                continue
            selected.append(item)
            selected_keys.add(key)
            if item.source_group == "product":
                selected_product_counts[item.source_id] += 1
        return selected


def retrieve_for_queries(
    retriever: LocalRetriever,
    queries: tuple[tuple[str, frozenset[str]], ...],
    *,
    top_k: int,
    resolved_product_codes: tuple[str, ...] = (),
) -> list[RetrievedDocument]:
    """Retrieve evidence per planned clause and interleave the results.

    A single whole-question ranking can let the lexically strongest clause use
    every top-k slot.  Each clause therefore gets an independent search and the
    result lists are merged round-robin.  This is retrieval orchestration, not
    an LLM call, and the one-clause path preserves ``LocalRetriever.retrieve``
    exactly.
    """

    usable = tuple((text, groups) for text, groups in queries if text.strip())
    if top_k <= 0 or not usable:
        return []
    if len(usable) == 1:
        text, groups = usable[0]
        return [
            replace(item, matched_query_indexes=(1,))
            for item in retriever.retrieve(
                text,
                top_k=top_k,
                source_groups=set(groups),
                resolved_product_codes=resolved_product_codes,
            )
        ]

    target = max(top_k, len(usable))
    ranked = tuple(
        retriever.retrieve(
            text,
            top_k=target,
            source_groups=set(groups),
            resolved_product_codes=resolved_product_codes,
        )
        for text, groups in usable
    )
    selected: list[RetrievedDocument] = []
    seen: set[tuple[str, int]] = set()
    selected_positions: dict[tuple[str, int], int] = {}
    cursors = [0 for _ in ranked]
    while len(selected) < target:
        added = False
        for list_index, results in enumerate(ranked):
            while cursors[list_index] < len(results):
                candidate = results[cursors[list_index]]
                cursors[list_index] += 1
                key = (candidate.source, candidate.index)
                if key not in seen:
                    selected.append(
                        replace(
                            candidate,
                            matched_query_indexes=(list_index + 1,),
                        )
                    )
                    seen.add(key)
                    selected_positions[key] = len(selected) - 1
                    added = True
                    break
                position = selected_positions[key]
                existing = selected[position]
                query_indexes = tuple(
                    dict.fromkeys(
                        (*existing.matched_query_indexes, list_index + 1)
                    )
                )
                selected[position] = replace(
                    existing,
                    matched_query_indexes=query_indexes,
                )
                # The same evidence may correctly serve two clauses. Skip its
                # duplicate, but continue looking at this clause's next result.
            if len(selected) >= target:
                break
        if not added:
            break
    # The output budget can be reached before a later ranked list encounters a
    # document already selected for an earlier clause. Backfill provenance from
    # every per-clause top-k list so shared evidence remains eligible for each
    # requirement it actually matched.
    for position, existing in enumerate(selected):
        key = (existing.source, existing.index)
        query_indexes = tuple(
            list_index + 1
            for list_index, results in enumerate(ranked)
            if any((item.source, item.index) == key for item in results)
        )
        selected[position] = replace(
            existing,
            matched_query_indexes=query_indexes,
        )
    return selected


def _truncate_at_boundary(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    candidate = text[:limit]
    # Evidence lines may be table rows.  Returning less context is safer than
    # presenting a partial row/value as if it were complete.
    boundary = candidate.rfind("\n")
    if boundary > 0:
        return candidate[:boundary].rstrip()
    return ""


def render_context(documents: list[RetrievedDocument], *, max_chars: int) -> str:
    if not documents or max_chars <= 0:
        return ""
    sections: list[str] = []
    used = 0
    for position, document in enumerate(documents, start=1):
        locator = f" {document.locator}" if document.locator else ""
        alias_metadata = ""
        if document.source_group == "product" and document.canonical_source_id:
            aliases = ",".join(document.product_aliases)
            alias_metadata = (
                f" canonical_source_id={document.canonical_source_id}"
                f" product_aliases={aliases}"
            )
        governance_metadata = (
            document.governance.context_fields()
            if document.governance is not None
            else ""
        )
        header = (
            f"[DOC {position}] source_id={document.source_id} "
            f"source_path={document.source_path}{locator} chunk={document.index} "
            f"citation_id={evidence_citation_id(document)} title={document.title}"
            f"{alias_metadata}{governance_metadata}"
        )
        section = f"{header}\n{document.text}"
        remaining = max_chars - used
        if remaining <= 0:
            break
        section = _truncate_at_boundary(section, remaining)
        if not section or "\n" not in section:
            break
        sections.append(section)
        used += len(section) + 2
    return "\n\n".join(sections)
