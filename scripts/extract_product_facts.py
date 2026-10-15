#!/usr/bin/env python3
"""Build a high-precision locator index for organizer product-comparison axes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.product_facts import (  # noqa: E402
    AXIS_TERMS,
    EXTRACTOR_FINGERPRINT,
    PRODUCT_CODE_RE,
    SCHEMA_VERSION,
    normalize_text,
)


DEFAULT_CORPUS = PROJECT_ROOT / "data" / "knowledge"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "structured" / "product_evidence.json"
PAGE_RE = re.compile(r"(?m)^\[PAGE (\d+)]\s*$")
DATE_RE = re.compile(
    r"(?:20\d{2}[./-]\s*\d{1,2}[./-]\s*\d{1,2}|20\d{2}\s*년\s*\d{1,2}\s*월\s*\d{1,2}\s*일)"
)
BASIS_DATE_RE = re.compile(
    r"작성\s*기준일\s*[:：]?\s*"
    r"(20\d{2}[./-]\s*\d{1,2}[./-]\s*\d{1,2}|20\d{2}\s*년\s*\d{1,2}\s*월\s*\d{1,2}\s*일)"
)
UNIT_RE = re.compile(
    r"(?:\[\s*|\(\s*)단위\s*[:：]?\s*([^\]\)\n]{1,32})\s*[\]\)]"
    r"|(?:^|\s)단위\s*[:：]\s*([^\n]{1,32})$",
    flags=re.MULTILINE,
)
CLASS_RE = re.compile(
    r"(?:Class|클래스|종류)\s*[-:：]?\s*([A-Za-z][A-Za-z0-9()\-._]{0,24})",
    flags=re.IGNORECASE,
)
PAREN_CLASS_RE = re.compile(r"\(([A-Za-z][A-Za-z0-9._-]{0,20})\)")
TABLE_CLASS_RE = re.compile(
    r"(?:종류\s*\(\s*Class\s*\)|Class)\s+([A-Za-z][A-Za-z0-9._-]{0,20})",
    flags=re.IGNORECASE,
)
EXTRACTION_TERMS: dict[str, tuple[str, ...]] = {
    "identity": ("집합투자기구 명칭", "<간이투자설명서>", "펀드명"),
    "category": (
        "집합투자기구의 종류 및 형태",
        "운용자산별 종류",
        "분류",
    ),
    "risk": ("투자 위험 등급", "투자위험등급"),
    "class": (
        "종류(Class)",
        "클래스 종류",
        "종류별 가입자격",
        "가입가능한 수익증권의 종류",
        "가입 가능한 수익증권의 종류",
    ),
    "fees": ("총보수", "총 보수", "투자비용", "보수 및 수수료에 관한 사항"),
    "performance": (
        "투자실적 추이",
        "연평균 수익률",
        "연도별 수익률",
        "집합투자기구의 운용실적",
        "최근 1년",
        "최근1년",
    ),
    "balance": (
        "기간말 잔고",
        "기간말잔고",
        "연도별 설정 및 환매 현황",
        "연도별 설정 및 환매현황",
    ),
}
WINDOW_LINES: dict[str, tuple[int, int]] = {
    "identity": (2, 6),
    "category": (3, 9),
    "risk": (3, 9),
    "class": (4, 14),
    "fees": (4, 16),
    "performance": (5, 12),
    "balance": (4, 26),
}
MAX_BLOCKS: dict[str, int] = {
    "identity": 2,
    "category": 3,
    "risk": 3,
    "class": 4,
    "fees": 5,
    "performance": 5,
    "balance": 4,
}
MIN_WINDOW_SCORE: dict[str, int] = {
    "identity": 8,
    "category": 8,
    "risk": 10,
    "class": 8,
    "fees": 10,
    "performance": 10,
    "balance": 10,
}


@dataclass(frozen=True, slots=True)
class EvidenceWindow:
    start_line: int
    end_line: int
    raw_text: str
    score: int


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def metadata_and_content(path: Path) -> tuple[dict[str, str], str]:
    raw = path.read_text(encoding="utf-8")
    raw_metadata, marker, content = raw.partition("[CONTENT]")
    if not marker:
        raise RuntimeError(f"Missing content envelope: {path}")
    metadata = dict(
        line.split("=", maxsplit=1)
        for line in raw_metadata.splitlines()[1:]
        if "=" in line
    )
    return metadata, content.lstrip("\r\n")


def pages(text: str) -> list[tuple[int, str]]:
    matches = list(PAGE_RE.finditer(text))
    result: list[tuple[int, str]] = []
    for position, match in enumerate(matches):
        end = matches[position + 1].start() if position + 1 < len(matches) else len(text)
        result.append((int(match.group(1)), text[match.end() : end].strip()))
    return result


def _line_bounded(value: str, *, max_chars: int = 4_000) -> str:
    if len(value) <= max_chars:
        return value
    lines: list[str] = []
    used = 0
    for line in value.splitlines():
        addition = len(line) + (1 if lines else 0)
        if used + addition > max_chars:
            break
        lines.append(line)
        used += addition
    return "\n".join(lines)


def _window_score(axis: str, raw: str, *, page_number: int) -> int:
    """Rank locators, never values; returned evidence always remains verbatim."""

    normalized = normalize_text(raw)
    score = 0
    strong_terms: dict[str, tuple[tuple[str, int], ...]] = {
        "identity": (
            ("집합투자기구 명칭", 10),
            ("작성기준일", 6),
            ("효력발생일", 3),
        ),
        "category": (
            ("집합투자기구의 종류 및 형태", 12),
            ("운용자산별 종류", 10),
            ("채권형", 5),
            ("주식형", 5),
            ("혼합형", 5),
        ),
        "risk": (
            ("투자 위험 등급", 12),
            ("투자위험등급", 12),
            ("등급", 3),
            ("매우높은위험", 2),
            ("매우 높은 위험", 2),
        ),
        "class": (
            ("종류(class)", 12),
            ("클래스 종류", 12),
            ("가입자격", 8),
            ("판매수수료", 3),
            ("퇴직연금", 2),
            ("개인연금", 2),
        ),
        "fees": (
            ("총보수", 12),
            ("판매보수", 5),
            ("투자비용", 6),
            ("단위", 2),
        ),
        "performance": (
            ("집합투자기구의 운용실적", 20),
            ("투자실적 추이", 18),
            ("투자실적추이", 18),
            ("연평균 수익률", 12),
            ("연도별 수익률", 12),
            ("최근 1년", 8),
            ("최근1년", 8),
            ("최근 3년", 5),
            ("최근3년", 5),
            ("단위", 2),
        ),
        "balance": (
            ("기간말 잔고", 14),
            ("기간말잔고", 14),
            ("기간초 잔고", 6),
            ("기간초잔고", 6),
            ("설정", 3),
            ("환매", 3),
            ("단위", 2),
        ),
    }
    for term, weight in strong_terms[axis]:
        if normalize_text(term) in normalized:
            score += weight
    if axis == "performance":
        # Prospectuses often place manager/peer performance next to the fund
        # table. It is not this product's 1--3 year performance axis.
        for term, penalty in (
            ("운용전문인력", 22),
            ("동종집합투자기구", 15),
            ("운용현황", 10),
            ("운용역", 8),
            ("경력년수", 8),
            ("생년", 5),
        ):
            if normalize_text(term) in normalized:
                score -= penalty
    if re.search(r"\d", raw):
        score += 2
    if DATE_RE.search(raw):
        score += 3
    if "%" in raw:
        score += 2
    # Covers and summary tables generally carry the authoritative current
    # identity/risk/category while later glossary mentions are less useful.
    if axis in {"identity", "category", "risk"}:
        score += max(0, 6 - min(page_number, 6))
    return score


def _is_axis_evidence(axis: str, raw: str) -> bool:
    """Reject headings, glossaries and manager tables that merely name an axis."""

    normalized = normalize_text(raw)
    if axis != "performance":
        return True
    has_summary_anchor = any(
        normalize_text(term) in normalized
        for term in (
            "투자실적 추이",
            "투자실적추이",
        )
    )
    has_period_table = (
        bool(re.search(r"최근\s*1\s*년", normalized))
        and bool(re.search(r"최근\s*3\s*년", normalized))
        and bool(re.search(r"\d", raw))
    )
    has_section_table = (
        normalize_text("3. 집합투자기구의 운용실적") in normalized
        and any(
            normalize_text(term) in normalized
            for term in ("가. 연평균 수익률", "가. 연평균수익률")
        )
    )
    has_explicit_no_data = (
        "해당사항" in normalized
        and (
            any(
                normalize_text(term) in normalized
                for term in ("연평균 수익률", "연평균수익률")
            )
            or (
                "설정 초" in normalized
                and normalize_text("3. 집합투자기구의 운용실적") in normalized
            )
        )
    )
    return (
        has_summary_anchor
        or has_period_table
        or has_section_table
        or has_explicit_no_data
    )


def evidence_windows(
    page_text: str,
    terms: tuple[str, ...],
    *,
    axis: str,
    page_number: int,
) -> list[EvidenceWindow]:
    lines = [line.rstrip() for line in page_text.splitlines()]
    normalized_lines = [normalize_text(line) for line in lines]
    normalized_terms = tuple(normalize_text(term) for term in terms)
    before, after = WINDOW_LINES[axis]
    ranges: list[tuple[int, int]] = []
    for position, line in enumerate(normalized_lines):
        if not any(term in line for term in normalized_terms):
            continue
        start = max(0, position - before)
        end = min(len(lines), position + after + 1)
        if ranges and start <= ranges[-1][1]:
            ranges[-1] = (ranges[-1][0], max(ranges[-1][1], end))
        else:
            ranges.append((start, end))
    windows: list[EvidenceWindow] = []
    for start, end in ranges:
        raw = "\n".join(value for value in lines[start:end] if value.strip()).strip()
        raw = _line_bounded(raw)
        if raw:
            windows.append(
                EvidenceWindow(
                    start_line=start,
                    end_line=end,
                    raw_text=raw,
                    score=_window_score(axis, raw, page_number=page_number),
                )
            )
    return windows


def annotations(raw: str, *, axis: str) -> tuple[list[str], list[str], list[str]]:
    dates = sorted(set(match.group(0) for match in DATE_RE.finditer(raw)))
    units = sorted(
        {
            (match.group(1) or match.group(2)).strip()
            for match in UNIT_RE.finditer(raw)
            if (match.group(1) or match.group(2)).strip()
        }
    )
    classes: list[str] = []
    if axis in {"class", "fees", "performance", "balance"}:
        candidates = [match.group(1) for match in CLASS_RE.finditer(raw)]
        candidates.extend(match.group(1) for match in PAREN_CLASS_RE.finditer(raw))
        candidates.extend(match.group(1) for match in TABLE_CLASS_RE.finditer(raw))
        classes = sorted(
            {
                candidate
                for candidate in candidates
                if candidate.upper() not in {"CLASS", "ETF", "PDF", "VAR"}
            }
        )
    return dates, units, classes


def extract_basis_dates(content: str) -> list[str]:
    """Extract only dates explicitly labelled as the document writing basis date."""

    cover_and_summary = "\n".join(
        page_text for page_number, page_text in pages(content) if page_number <= 6
    )
    return sorted(
        {
            re.sub(r"\s+", " ", match.group(1)).strip()
            for match in BASIS_DATE_RE.finditer(cover_and_summary)
        }
    )


def extract_axis_evidence(content: str) -> dict[str, list[dict[str, object]]]:
    result: dict[str, list[dict[str, object]]] = {}
    content_pages = pages(content)
    for axis in AXIS_TERMS:
        candidates: list[tuple[int, EvidenceWindow]] = []
        for page_number, page_text in content_pages:
            if axis in {"identity", "risk"} and page_number > 12:
                continue
            for window in evidence_windows(
                page_text,
                EXTRACTION_TERMS[axis],
                axis=axis,
                page_number=page_number,
            ):
                if (
                    window.score >= MIN_WINDOW_SCORE[axis]
                    and _is_axis_evidence(axis, window.raw_text)
                ):
                    candidates.append((page_number, window))
        # Select the strongest table/summary locators first, then restore page
        # order so the runtime cannot accidentally reorder historical periods.
        selected = sorted(
            sorted(
                candidates,
                key=lambda item: (-item[1].score, item[0], item[1].start_line),
            )[: MAX_BLOCKS[axis]],
            key=lambda item: (item[0], item[1].start_line),
        )
        blocks: list[dict[str, object]] = []
        seen: set[str] = set()
        for page_number, window in selected:
            digest = hashlib.sha256(
                normalize_text(window.raw_text).encode("utf-8")
            ).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            dates, units, classes = annotations(window.raw_text, axis=axis)
            blocks.append(
                {
                    "page": page_number,
                    "raw_text": window.raw_text,
                    "dates": dates,
                    "units": units,
                    "class_labels": classes,
                    "status": "raw_evidence_not_normalized",
                }
            )
        result[axis] = blocks
    return result


def build(corpus: Path) -> dict[str, object]:
    manifest_path = corpus / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("extractor_fingerprint") != "pension-corpus-v2":
        raise RuntimeError("Product evidence requires a validated pension-corpus-v2 build")
    build_options = manifest.get("build_options")
    if not isinstance(build_options, dict) or build_options.get("ocr") is not True:
        raise RuntimeError("Product evidence requires an OCR-enabled corpus build")
    documents = manifest.get("documents")
    if not isinstance(documents, list):
        raise RuntimeError("Corpus manifest documents must be an array")
    records: list[dict[str, object]] = []
    for item in documents:
        if not isinstance(item, dict):
            raise RuntimeError("Corpus manifest entries must be objects")
        if item.get("source_group") != "product":
            continue
        code = item.get("source_id")
        if not isinstance(code, str) or not PRODUCT_CODE_RE.fullmatch(code) or not code.startswith("KR"):
            raise RuntimeError(f"Invalid product source_id: {code!r}")
        raw_output_path = item.get("output_path")
        if not isinstance(raw_output_path, str):
            raise RuntimeError(f"Invalid output path for {code}")
        relative_output = PurePosixPath(raw_output_path)
        if (
            relative_output.is_absolute()
            or "\\" in raw_output_path
            or any(part in {"", ".", ".."} for part in relative_output.parts)
        ):
            raise RuntimeError(f"Unsafe output path for {code}: {raw_output_path!r}")
        output = corpus.joinpath(*relative_output.parts)
        if sha256_file(output) != item.get("text_sha256"):
            raise RuntimeError(f"Derived output hash mismatch: {output}")
        metadata, content = metadata_and_content(output)
        if metadata.get("review_status") != "raw":
            raise RuntimeError(f"Unexpected product review status: {code}")
        expected_metadata = {
            "source_id": code,
            "source_group": "product",
            "source_path": item.get("source_path"),
            "source_sha256": item.get("sha256"),
            "extractor_fingerprint": "pension-corpus-v2",
            "canonical_source_id": item.get("canonical_source_id"),
            "product_aliases": ",".join(item.get("product_aliases", [])),
        }
        for key, expected in expected_metadata.items():
            if not isinstance(expected, str) or metadata.get(key) != expected:
                raise RuntimeError(f"Output metadata mismatch for {code}: {key}")
        aliases = item.get("product_aliases")
        canonical = item.get("canonical_source_id")
        if (
            not isinstance(aliases, list)
            or aliases != sorted(set(aliases))
            or code not in aliases
            or not all(
                isinstance(alias, str)
                and PRODUCT_CODE_RE.fullmatch(alias)
                and alias.startswith("KR")
                for alias in aliases
            )
            or canonical != aliases[0]
        ):
            raise RuntimeError(f"Invalid product alias metadata for {code}")
        axis_evidence = extract_axis_evidence(content)
        records.append(
            {
                "product_code": code,
                "canonical_source_id": canonical,
                "aliases": aliases,
                "source_path": item["source_path"],
                "source_sha256": item["sha256"],
                "title": item["title_hint"],
                "basis_dates": extract_basis_dates(content),
                "evidence": axis_evidence,
            }
        )
    if len(records) != 100:
        raise RuntimeError(f"Expected 100 product records, got {len(records)}")
    return {
        "schema_version": SCHEMA_VERSION,
        "extractor_fingerprint": EXTRACTOR_FINGERPRINT,
        "corpus_manifest_sha256": sha256_file(manifest_path),
        "record_count": len(records),
        "records": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    payload = build(args.corpus.resolve())
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=output.parent,
        prefix=f".{output.name}.",
        suffix=".tmp",
        delete=False,
    ) as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, output)
    coverage = {
        axis: sum(bool(record["evidence"][axis]) for record in payload["records"])
        for axis in AXIS_TERMS
    }
    print(f"Wrote {payload['record_count']} product records to {output}")
    print("Evidence coverage: " + ", ".join(f"{k}={v}" for k, v in coverage.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
