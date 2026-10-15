"""Fail-closed integrity validation for the organizer-derived text corpus.

The runtime retriever recursively indexes every ``.txt`` and ``.md`` file in
the knowledge directory.  This validator therefore treats both the manifest
and the filesystem as inputs: neither an unmanifested searchable file nor a
manifest entry without a matching file is allowed.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping


SEARCHABLE_SUFFIXES = frozenset({".txt", ".md"})
SOURCE_SUFFIXES = frozenset({".pdf", ".docx", ".xlsx", ".pptx"})
EXPECTED_GROUP_COUNTS: Mapping[str, int] = {"general": 58, "product": 100}
MIN_VISIBLE_CHARS: Mapping[str, int] = {"general": 100, "product": 10_000}
MAX_SEARCHABLE_FILE_BYTES = 5 * 1024 * 1024
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
TRUSTED_EXTRACTOR_FINGERPRINT = "pension-corpus-v2"


@dataclass(frozen=True, slots=True)
class CorpusIntegrityReport:
    """Result returned instead of stopping at the first corpus defect."""

    errors: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors

    def require_valid(self) -> None:
        if self.errors:
            details = "\n".join(f"- {error}" for error in self.errors)
            raise CorpusIntegrityError(f"knowledge corpus integrity check failed:\n{details}")


class CorpusIntegrityError(RuntimeError):
    """Raised by :meth:`CorpusIntegrityReport.require_valid`."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_relative_path(value: object, *, field: str, errors: list[str]) -> PurePosixPath | None:
    if not isinstance(value, str) or not value:
        errors.append(f"{field} must be a non-empty relative POSIX path")
        return None
    candidate = PurePosixPath(value)
    if (
        candidate.is_absolute()
        or "\\" in value
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        errors.append(f"{field} is unsafe: {value!r}")
        return None
    return candidate


def _parse_output(path: Path, *, label: str, errors: list[str]) -> tuple[dict[str, str], str] | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        errors.append(f"{label}: output is not valid readable UTF-8: {exc}")
        return None
    if not raw.startswith("[METADATA]\n"):
        errors.append(f"{label}: output must start with [METADATA]")
        return None
    marker = "[CONTENT]"
    if marker not in raw:
        errors.append(f"{label}: output has no [CONTENT] marker")
        return None
    raw_metadata, content = raw.split(marker, maxsplit=1)
    metadata: dict[str, str] = {}
    for line in raw_metadata.splitlines()[1:]:
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not key:
            errors.append(f"{label}: malformed metadata line: {line!r}")
            continue
        if key in metadata:
            errors.append(f"{label}: duplicate metadata key: {key}")
            continue
        metadata[key] = value.strip()
    return metadata, content.lstrip("\r\n")


def _valid_int(value: object, *, minimum: int = 0) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def validate_corpus(
    corpus_root: Path,
    source_root: Path,
    *,
    expected_group_counts: Mapping[str, int] = EXPECTED_GROUP_COUNTS,
    min_visible_chars: Mapping[str, int] = MIN_VISIBLE_CHARS,
) -> CorpusIntegrityReport:
    """Validate raw sources, manifest entries, output metadata and searchable files.

    Defaults encode the organizer delivery contract (58 general documents and
    100 product documents).  Count and size mappings are injectable only to
    support small, representative test fixtures.
    """

    corpus_root = corpus_root.resolve()
    source_root = source_root.resolve()
    errors: list[str] = []
    manifest_path = corpus_root / "manifest.json"
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return CorpusIntegrityReport((f"manifest is missing: {manifest_path}",))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return CorpusIntegrityReport((f"manifest is not valid readable JSON: {exc}",))

    if not isinstance(payload, dict):
        return CorpusIntegrityReport(("manifest root must be a JSON object",))
    if payload.get("extractor_fingerprint") != TRUSTED_EXTRACTOR_FINGERPRINT:
        errors.append(
            "manifest extractor_fingerprint is not the trusted revision: "
            f"{payload.get('extractor_fingerprint')!r}"
        )
    build_options = payload.get("build_options")
    if not isinstance(build_options, dict):
        errors.append("manifest build_options must be an object")
    else:
        if build_options.get("ocr") is not True:
            errors.append("submission corpus must be generated with OCR enabled")
        dpi = build_options.get("ocr_dpi")
        if isinstance(dpi, bool) or not isinstance(dpi, int) or not 100 <= dpi <= 600:
            errors.append("manifest build_options.ocr_dpi is invalid")
        minimum = build_options.get("min_semantic_chars_per_page")
        if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 1:
            errors.append("manifest build_options.min_semantic_chars_per_page is invalid")
    documents = payload.get("documents")
    if not isinstance(documents, list):
        return CorpusIntegrityReport(("manifest documents must be a JSON array",))
    declared_count = payload.get("document_count")
    if not _valid_int(declared_count) or declared_count != len(documents):
        errors.append(
            f"manifest document_count mismatch: declared={declared_count!r}, actual={len(documents)}"
        )

    actual_outputs: set[str] = set()
    if not corpus_root.is_dir():
        errors.append(f"corpus directory is missing: {corpus_root}")
    else:
        for path in corpus_root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in SEARCHABLE_SUFFIXES:
                continue
            relative = path.relative_to(corpus_root).as_posix()
            actual_outputs.add(relative)
            if path.is_symlink():
                errors.append(f"searchable output must not be a symlink: {relative}")

    actual_sources: set[str] = set()
    if not source_root.is_dir():
        errors.append(f"raw source directory is missing: {source_root}")
    else:
        for path in source_root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in SOURCE_SUFFIXES:
                continue
            relative = path.relative_to(source_root).as_posix()
            actual_sources.add(relative)
            if path.is_symlink():
                errors.append(f"raw source must not be a symlink: {relative}")

    required_fields = {
        "source_id",
        "source_group",
        "source_path",
        "output_path",
        "media_type",
        "sha256",
        "size_bytes",
        "page_count",
        "extraction_method",
        "review_status",
        "ocr_requested",
        "ocr_dpi",
        "min_semantic_chars_per_page",
        "extracted_chars",
        "title_hint",
        "text_sha256",
        "extractor_fingerprint",
        "canonical_source_id",
        "product_aliases",
    }
    expected_outputs: set[str] = set()
    expected_sources: set[str] = set()
    seen_source_ids: set[tuple[str, str]] = set()
    seen_source_paths: set[str] = set()
    fingerprints: set[str] = set()
    group_counts = {group: 0 for group in expected_group_counts}

    for index, item in enumerate(documents):
        label = f"document[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{label} must be a JSON object")
            continue
        missing = sorted(required_fields - item.keys())
        if missing:
            errors.append(f"{label} missing required fields: {', '.join(missing)}")

        source_id = item.get("source_id")
        source_group = item.get("source_group")
        if not isinstance(source_id, str) or not source_id:
            errors.append(f"{label}.source_id must be a non-empty string")
            source_id = f"<invalid-{index}>"
        group_key = source_group if isinstance(source_group, str) else None
        valid_group = group_key in expected_group_counts
        if not valid_group:
            errors.append(f"{label}.source_group is invalid: {source_group!r}")
        else:
            group_counts[source_group] += 1
        source_key = (str(source_group), source_id)
        if source_key in seen_source_ids:
            errors.append(f"duplicate source identity: {source_group}/{source_id}")
        seen_source_ids.add(source_key)

        source_relative = _safe_relative_path(
            item.get("source_path"), field=f"{label}.source_path", errors=errors
        )
        output_relative = _safe_relative_path(
            item.get("output_path"), field=f"{label}.output_path", errors=errors
        )
        if source_relative is not None:
            source_name = source_relative.as_posix()
            if source_name in seen_source_paths:
                errors.append(f"duplicate source_path: {source_name}")
            seen_source_paths.add(source_name)
            expected_sources.add(source_name)
            expected_source_prefix = {
                "general": "일반문서",
                "product": "투자설명서",
            }.get(group_key)
            if expected_source_prefix and source_relative.parts[0] != expected_source_prefix:
                errors.append(
                    f"{label}.source_path must be under {expected_source_prefix}: {source_name}"
                )

        output_name: str | None = None
        if output_relative is not None:
            output_name = output_relative.as_posix()
            if output_name in expected_outputs:
                errors.append(f"duplicate output_path: {output_name}")
            expected_outputs.add(output_name)
            expected_directory = {"general": "general", "product": "products"}.get(
                group_key
            )
            expected_name = (
                f"{expected_directory}/{source_id}.txt" if expected_directory else None
            )
            if expected_name is not None and output_name != expected_name:
                errors.append(
                    f"{label}.output_path mismatch: expected={expected_name!r}, actual={output_name!r}"
                )
            if output_relative.suffix.lower() not in SEARCHABLE_SUFFIXES:
                errors.append(f"{label}.output_path is not searchable: {output_name}")

        source_sha = item.get("sha256")
        if not isinstance(source_sha, str) or not SHA256_PATTERN.fullmatch(source_sha):
            errors.append(f"{label}.sha256 must be a lowercase SHA-256 digest")
        text_sha = item.get("text_sha256")
        if not isinstance(text_sha, str) or not SHA256_PATTERN.fullmatch(text_sha):
            errors.append(f"{label}.text_sha256 must be a lowercase SHA-256 digest")
        fingerprint = item.get("extractor_fingerprint")
        if not isinstance(fingerprint, str) or not fingerprint.strip():
            errors.append(f"{label}.extractor_fingerprint must be a non-empty string")
        else:
            fingerprints.add(fingerprint)
            if fingerprint != TRUSTED_EXTRACTOR_FINGERPRINT:
                errors.append(
                    f"{label}.extractor_fingerprint is not the trusted revision: "
                    f"expected={TRUSTED_EXTRACTOR_FINGERPRINT!r}, actual={fingerprint!r}"
                )
        if not _valid_int(item.get("size_bytes"), minimum=1):
            errors.append(f"{label}.size_bytes must be a positive integer")
        page_count = item.get("page_count")
        if page_count is not None and not _valid_int(page_count, minimum=1):
            errors.append(f"{label}.page_count must be null or a positive integer")
        if not _valid_int(item.get("extracted_chars")):
            errors.append(f"{label}.extracted_chars must be a non-negative integer")
        for field in ("media_type", "extraction_method", "review_status", "title_hint"):
            if not isinstance(item.get(field), str) or not item.get(field).strip():
                errors.append(f"{label}.{field} must be a non-empty string")
        expected_review_status = (
            "quarantined"
            if source_id == "doc29" and source_group == "general"
            else "raw"
        )
        if item.get("review_status") != expected_review_status:
            errors.append(
                f"{label}.review_status mismatch: expected={expected_review_status!r}, "
                f"actual={item.get('review_status')!r}"
            )
        if not isinstance(item.get("ocr_requested"), bool):
            errors.append(f"{label}.ocr_requested must be boolean")
        ocr_requested = item.get("ocr_requested") is True
        for option in ("ocr_dpi", "min_semantic_chars_per_page"):
            value = item.get(option)
            if ocr_requested:
                if not _valid_int(value, minimum=1):
                    errors.append(f"{label}.{option} must be a positive integer when OCR is requested")
            elif value is not None:
                errors.append(f"{label}.{option} must be null when OCR is not requested")

        canonical_source_id = item.get("canonical_source_id")
        aliases = item.get("product_aliases")
        if not isinstance(canonical_source_id, str) or not canonical_source_id:
            errors.append(f"{label}.canonical_source_id must be a non-empty string")
        if not isinstance(aliases, list) or not all(
            isinstance(alias, str) and alias for alias in aliases
        ):
            errors.append(f"{label}.product_aliases must be an array of non-empty strings")
            aliases = []
        elif aliases != sorted(set(aliases)):
            errors.append(f"{label}.product_aliases must be unique and sorted")
        if source_group == "general":
            if aliases:
                errors.append(f"{label}: general documents must not have product aliases")
            if canonical_source_id != source_id:
                errors.append(f"{label}: general canonical_source_id must equal source_id")
        elif source_group == "product":
            if source_id not in aliases:
                errors.append(f"{label}: product aliases must include source_id")
            if aliases and canonical_source_id != aliases[0]:
                errors.append(f"{label}: canonical_source_id must be the first sorted alias")

        if source_relative is not None:
            source = source_root.joinpath(*source_relative.parts)
            if not source.is_file() or source.is_symlink():
                errors.append(f"{label}: raw source is missing or not a regular file: {source_relative}")
            else:
                try:
                    actual_source_sha = sha256_file(source)
                except OSError as exc:
                    errors.append(f"{label}: cannot hash raw source {source_relative}: {exc}")
                else:
                    if actual_source_sha != source_sha:
                        errors.append(
                            f"{label}: raw source SHA-256 mismatch: {source_relative}"
                        )
                if item.get("size_bytes") != source.stat().st_size:
                    errors.append(f"{label}: raw source size mismatch: {source_relative}")
                expected_media_type = source.suffix.lower().removeprefix(".")
                if item.get("media_type") != expected_media_type:
                    errors.append(
                        f"{label}: media_type mismatch: expected={expected_media_type!r}, "
                        f"actual={item.get('media_type')!r}"
                    )

        if output_name is None:
            continue
        output = corpus_root.joinpath(*output_relative.parts)
        if not output.is_file() or output.is_symlink():
            errors.append(f"{label}: output is missing or not a regular file: {output_name}")
            continue
        if output.stat().st_size > MAX_SEARCHABLE_FILE_BYTES:
            errors.append(
                f"{label}: output exceeds retriever size limit ({MAX_SEARCHABLE_FILE_BYTES} bytes): "
                f"{output_name}"
            )
        try:
            actual_text_sha = sha256_file(output)
        except OSError as exc:
            errors.append(f"{label}: cannot hash output {output_name}: {exc}")
        else:
            if actual_text_sha != text_sha:
                errors.append(f"{label}: derived text SHA-256 mismatch: {output_name}")

        parsed = _parse_output(output, label=label, errors=errors)
        if parsed is None:
            continue
        metadata, content = parsed
        expected_metadata = {
            "source_scope": "organizer_provided",
            "source_id": source_id,
            "source_group": source_group,
            "source_path": source_relative.as_posix() if source_relative is not None else None,
            "source_sha256": source_sha,
            "extraction_method": item.get("extraction_method"),
            "extractor_fingerprint": fingerprint,
            "review_status": item.get("review_status"),
            "ocr_requested": (
                "true" if item.get("ocr_requested") is True else "false"
                if item.get("ocr_requested") is False
                else None
            ),
            "ocr_dpi": "" if item.get("ocr_dpi") is None else str(item.get("ocr_dpi")),
            "min_semantic_chars_per_page": (
                ""
                if item.get("min_semantic_chars_per_page") is None
                else str(item.get("min_semantic_chars_per_page"))
            ),
            "canonical_source_id": canonical_source_id,
            "product_aliases": ",".join(aliases),
            "page_count": "" if page_count is None else str(page_count),
        }
        for key, expected_value in expected_metadata.items():
            if metadata.get(key) != expected_value:
                errors.append(
                    f"{label}: metadata {key} mismatch: expected={expected_value!r}, "
                    f"actual={metadata.get(key)!r}"
                )

        actual_chars = len(content.strip())
        if item.get("extracted_chars") != actual_chars:
            errors.append(
                f"{label}: extracted_chars mismatch: declared={item.get('extracted_chars')!r}, "
                f"actual={actual_chars}"
            )
        visible_chars = len(re.sub(r"\s+", "", content))
        minimum = min_visible_chars.get(str(source_group))
        if minimum is None:
            errors.append(f"no content threshold configured for source group: {source_group!r}")
        elif visible_chars < minimum:
            errors.append(
                f"{label}: extracted content too short for {source_group}: "
                f"visible_chars={visible_chars}, minimum={minimum}"
            )

    for group, expected_count in expected_group_counts.items():
        actual_count = group_counts.get(group, 0)
        if actual_count != expected_count:
            errors.append(
                f"manifest group count mismatch: {group}={actual_count}, expected={expected_count}"
            )
    expected_total = sum(expected_group_counts.values())
    if len(documents) != expected_total:
        errors.append(
            f"manifest total count mismatch: actual={len(documents)}, expected={expected_total}"
        )
    if len(fingerprints) > 1:
        errors.append(
            "manifest contains mixed extractor_fingerprint values: "
            + ", ".join(sorted(fingerprints))
        )

    missing_outputs = sorted(expected_outputs - actual_outputs)
    unexpected_outputs = sorted(actual_outputs - expected_outputs)
    if missing_outputs:
        errors.append("manifest outputs missing from corpus: " + ", ".join(missing_outputs))
    if unexpected_outputs:
        errors.append("unmanifested searchable outputs: " + ", ".join(unexpected_outputs))
    missing_sources = sorted(expected_sources - actual_sources)
    unexpected_sources = sorted(actual_sources - expected_sources)
    if missing_sources:
        errors.append("manifest raw sources missing from delivery: " + ", ".join(missing_sources))
    if unexpected_sources:
        errors.append("unmanifested organizer raw sources: " + ", ".join(unexpected_sources))

    doc29 = [
        item
        for item in documents
        if isinstance(item, dict)
        and item.get("source_group") == "general"
        and item.get("source_id") == "doc29"
    ]
    if len(doc29) != 1 or doc29[0].get("review_status") != "quarantined":
        errors.append("general/doc29 must appear exactly once with review_status=quarantined")

    return CorpusIntegrityReport(tuple(errors))
