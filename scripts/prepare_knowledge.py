#!/usr/bin/env python3
"""Build a searchable text corpus without modifying organizer-provided files."""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import hashlib
import json
import os
import posixpath
import re
import shutil
import subprocess
import tempfile
import unicodedata
import uuid
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

try:
    from .corpus_integrity import validate_corpus
except ImportError:  # Direct execution: python3 scripts/prepare_knowledge.py
    from corpus_integrity import validate_corpus


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = PROJECT_ROOT / "docs" / "official" / "extracted" / "data"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "knowledge"
DEFAULT_GENERAL_CATALOG = (
    PROJECT_ROOT / "docs" / "official" / "GENERAL_DOCUMENT_CATALOG.md"
)
QUARANTINED_SOURCE_IDS = {"doc29"}
EXTRACTOR_FINGERPRINT = "pension-corpus-v2"
PRODUCT_CODE_PATTERN = re.compile(r"[A-Z0-9]{12}")
GENERAL_CATALOG_PATTERN = re.compile(
    r"^\s*-\s+`(?P<filename>doc\d+\.(?:pdf|docx|xlsx|pptx))`"
    r"\s+—\s+(?P<title>[^—]+?)\s+—\s+"
)
EXPECTED_GENERAL_SOURCE_IDS = frozenset(f"doc{number}" for number in range(1, 59))


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    source_id: str
    source_group: str
    source_path: str
    output_path: str
    media_type: str
    sha256: str
    size_bytes: int
    page_count: int | None
    extraction_method: str
    review_status: str
    ocr_requested: bool
    ocr_dpi: int | None
    min_semantic_chars_per_page: int | None
    extracted_chars: int
    title_hint: str
    text_sha256: str
    extractor_fingerprint: str
    canonical_source_id: str
    product_aliases: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExtractedDocument:
    text: str
    method: str
    page_count: int | None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def require_tool(name: str) -> None:
    if shutil.which(name) is None:
        raise RuntimeError(f"Required command is not installed: {name}")


def pdf_page_count(path: Path) -> int | None:
    try:
        result = run(["pdfinfo", str(path)])
    except (subprocess.CalledProcessError, RuntimeError):
        return None
    match = re.search(r"^Pages:\s+(\d+)$", result.stdout, flags=re.MULTILINE)
    return int(match.group(1)) if match else None


def semantic_chars(text: str) -> int:
    """Count meaningful OCR characters instead of punctuation/layout artifacts."""

    normalized = unicodedata.normalize("NFKC", text)
    return len(re.sub(r"[^0-9A-Za-z가-힣]", "", normalized))


def ocr_pdf_page(path: Path, *, page_number: int, dpi: int, psm: int = 3) -> str:
    require_tool("pdftoppm")
    require_tool("tesseract")
    with tempfile.TemporaryDirectory(prefix="pension-ocr-") as temporary:
        prefix = Path(temporary) / "page"
        subprocess.run(
            [
                "pdftoppm",
                "-f",
                str(page_number),
                "-l",
                str(page_number),
                "-singlefile",
                "-r",
                str(dpi),
                "-png",
                str(path),
                str(prefix),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        image = prefix.with_suffix(".png")
        result = run(
            [
                "tesseract",
                str(image),
                "stdout",
                "-l",
                "kor+eng",
                "--psm",
                str(psm),
            ]
        )
    return result.stdout.strip()


def extract_pdf(
    path: Path,
    *,
    enable_ocr: bool,
    min_chars_per_page: int,
    ocr_dpi: int,
    ocr_workers: int,
) -> ExtractedDocument:
    require_tool("pdftotext")
    pages = pdf_page_count(path)
    result = run(["pdftotext", "-layout", str(path), "-"])
    page_texts = result.stdout.split("\f")
    if page_texts and page_texts[-1] == "":
        page_texts.pop()
    if pages is not None:
        if len(page_texts) < pages:
            page_texts.extend("" for _ in range(pages - len(page_texts)))
        elif len(page_texts) > pages:
            raise RuntimeError(
                f"pdftotext page count mismatch for {path}: "
                f"pdfinfo={pages}, extracted={len(page_texts)}"
            )
    used_ocr = False
    prefer_uniform_layout = "일반문서" in {
        unicodedata.normalize("NFC", part) for part in path.parts
    }
    if enable_ocr:
        sparse_indexes = [
            index
            for index, page_text in enumerate(page_texts)
            if semantic_chars(page_text) < min_chars_per_page
        ]
        primary_psm = 6 if prefer_uniform_layout else 3

        def recognize(index: int) -> tuple[int, str]:
            return (
                index,
                ocr_pdf_page(
                    path,
                    page_number=index + 1,
                    dpi=ocr_dpi,
                    psm=primary_psm,
                ),
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=ocr_workers) as executor:
            recognized = dict(executor.map(recognize, sparse_indexes))
        for index in sparse_indexes:
            page_text = page_texts[index]
            ocr_text = recognized[index]
            if semantic_chars(ocr_text) >= max(10, semantic_chars(page_text)):
                embedded = page_text.strip("\r\n")
                ocr_layer = ocr_text.strip("\r\n")
                page_texts[index] = (
                    f"{embedded}\n[OCR LAYER]\n{ocr_layer}" if embedded else ocr_layer
                )
                used_ocr = True
    method = "pdftotext-layout+selective-tesseract" if used_ocr else "pdftotext-layout"
    text = "\n\n".join(
        f"[PAGE {page_number}]\n{page_text.strip(chr(10) + chr(13))}"
        for page_number, page_text in enumerate(page_texts, start=1)
    )
    return ExtractedDocument(text=text, method=method, page_count=pages)


def _archive_target(base: str, target: str) -> str:
    return posixpath.normpath(posixpath.join(posixpath.dirname(base), target))


def _ocr_image_bytes(data: bytes, *, suffix: str) -> str:
    require_tool("tesseract")
    with tempfile.TemporaryDirectory(prefix="pension-office-ocr-") as temporary:
        image = Path(temporary) / f"image{suffix or '.bin'}"
        image.write_bytes(data)
        try:
            result = run(
                ["tesseract", str(image), "stdout", "-l", "kor+eng", "--psm", "3"]
            )
        except subprocess.CalledProcessError:
            return ""
    value = result.stdout.strip()
    return value if semantic_chars(value) >= 10 else ""


def _paragraph_text(element: ET.Element, namespace: dict[str, str]) -> str:
    parts: list[str] = []
    word_ns = namespace["w"]
    for node in element.iter():
        if node.tag == f"{{{word_ns}}}t":
            parts.append(node.text or "")
        elif node.tag == f"{{{word_ns}}}tab":
            parts.append("\t")
        elif node.tag in {f"{{{word_ns}}}br", f"{{{word_ns}}}cr"}:
            parts.append("\n")
    return "".join(parts).strip()


def extract_docx(path: Path, *, enable_ocr: bool) -> tuple[str, bool]:
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    word_ns = namespace["w"]
    sections: list[str] = []
    used_ocr = False
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
        body = root.find("w:body", namespace)
        if body is not None:
            table_number = 0
            for child in body:
                if child.tag == f"{{{word_ns}}}p":
                    text = _paragraph_text(child, namespace)
                    if text:
                        sections.append(text)
                elif child.tag == f"{{{word_ns}}}tbl":
                    table_number += 1
                    for row_number, row in enumerate(
                        child.findall("w:tr", namespace), start=1
                    ):
                        cells = [
                            _paragraph_text(cell, namespace)
                            for cell in row.findall("w:tc", namespace)
                        ]
                        if any(cells):
                            sections.append(
                                f"[TABLE {table_number} ROW {row_number}]\n"
                                + "\t".join(cells)
                            )

        for name in sorted(archive.namelist()):
            if not re.fullmatch(r"word/(?:header|footer)\d+\.xml", name):
                continue
            extra = ET.fromstring(archive.read(name))
            text = "\n".join(
                value
                for paragraph in extra.findall(".//w:p", namespace)
                if (value := _paragraph_text(paragraph, namespace))
            )
            if text:
                sections.append(f"[{PurePosixPath(name).stem.upper()}]\n{text}")

        if enable_ocr:
            for name in sorted(archive.namelist()):
                if not name.startswith("word/media/") or name.endswith("/"):
                    continue
                ocr_text = _ocr_image_bytes(
                    archive.read(name), suffix=PurePosixPath(name).suffix
                )
                if ocr_text:
                    used_ocr = True
                    sections.append(
                        f"[EMBEDDED IMAGE {PurePosixPath(name).name}]\n{ocr_text}"
                    )
    return "\n\n".join(sections), used_ocr


def extract_pptx(path: Path, *, enable_ocr: bool) -> tuple[str, bool]:
    drawing_ns = "http://schemas.openxmlformats.org/drawingml/2006/main"
    presentation_ns = "http://schemas.openxmlformats.org/presentationml/2006/main"
    office_rel_ns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    package_rel_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
    slides: list[str] = []
    used_ocr = False
    with zipfile.ZipFile(path) as archive:
        presentation = ET.fromstring(archive.read("ppt/presentation.xml"))
        rels = ET.fromstring(archive.read("ppt/_rels/presentation.xml.rels"))
        targets = {
            node.attrib["Id"]: _archive_target("ppt/presentation.xml", node.attrib["Target"])
            for node in rels.findall(f"{{{package_rel_ns}}}Relationship")
        }
        slide_names = [
            targets[node.attrib[f"{{{office_rel_ns}}}id"]]
            for node in presentation.findall(f".//{{{presentation_ns}}}sldId")
        ]
        for position, name in enumerate(slide_names, start=1):
            root = ET.fromstring(archive.read(name))
            parts = [
                node.text.strip()
                for node in root.findall(f".//{{{drawing_ns}}}t")
                if node.text and node.text.strip()
            ]
            if enable_ocr:
                rel_name = posixpath.join(
                    posixpath.dirname(name), "_rels", posixpath.basename(name) + ".rels"
                )
                if rel_name in archive.namelist():
                    slide_rels = ET.fromstring(archive.read(rel_name))
                    for relation in slide_rels.findall(
                        f"{{{package_rel_ns}}}Relationship"
                    ):
                        if not relation.attrib.get("Type", "").endswith("/image"):
                            continue
                        image_name = _archive_target(name, relation.attrib["Target"])
                        if image_name not in archive.namelist():
                            continue
                        ocr_text = _ocr_image_bytes(
                            archive.read(image_name),
                            suffix=PurePosixPath(image_name).suffix,
                        )
                        if ocr_text:
                            used_ocr = True
                            parts.append(
                                f"[IMAGE {PurePosixPath(image_name).name}]\n{ocr_text}"
                            )
            slides.append(f"[SLIDE {position}]\n" + "\n".join(parts))
    return "\n\n".join(slides), used_ocr


def _xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    namespace = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    return [
        "".join(node.text or "" for node in item.findall(".//x:t", namespace))
        for item in root.findall("x:si", namespace)
    ]


_BUILTIN_DATE_NUMFMTS = frozenset({14, 15, 16, 17, 18, 19, 20, 21, 22, 45, 46, 47})


def _xlsx_style_formats(archive: zipfile.ZipFile) -> list[tuple[int, str]]:
    if "xl/styles.xml" not in archive.namelist():
        return []
    namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    root = ET.fromstring(archive.read("xl/styles.xml"))
    custom = {
        int(node.attrib["numFmtId"]): node.attrib.get("formatCode", "")
        for node in root.findall(f".//{{{namespace}}}numFmt")
    }
    cell_xfs = root.find(f"{{{namespace}}}cellXfs")
    if cell_xfs is None:
        return []
    return [
        (int(node.attrib.get("numFmtId", "0")), custom.get(int(node.attrib.get("numFmtId", "0")), ""))
        for node in cell_xfs.findall(f"{{{namespace}}}xf")
    ]


def _format_xlsx_number(
    raw: str,
    *,
    style_index: int | None,
    style_formats: list[tuple[int, str]],
    date_1904: bool,
) -> str:
    if style_index is None or not (0 <= style_index < len(style_formats)):
        return raw
    number_format_id, format_code = style_formats[style_index]
    lowered = re.sub(r'"[^"]*"|\\.', "", format_code).lower()
    is_date = number_format_id in _BUILTIN_DATE_NUMFMTS or bool(
        re.search(r"(?:^|[^a-z])[ymdhis]+", lowered)
    )
    try:
        number = float(raw)
    except ValueError:
        return raw
    if is_date:
        epoch = dt.datetime(1904, 1, 1) if date_1904 else dt.datetime(1899, 12, 30)
        value = epoch + dt.timedelta(days=number)
        if value.time() == dt.time.min:
            return value.date().isoformat()
        return value.isoformat(sep=" ", timespec="seconds")
    if "%" in format_code:
        decimals = 0
        decimal_match = re.search(r"0\.([0#]+)%", format_code)
        if decimal_match:
            decimals = len(decimal_match.group(1))
        return f"{number * 100:.{decimals}f}%"
    return raw


def extract_xlsx(path: Path) -> str:
    spreadsheet_ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    relationship_ns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    package_rel_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
    with zipfile.ZipFile(path) as archive:
        shared = _xlsx_shared_strings(archive)
        style_formats = _xlsx_style_formats(archive)
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        workbook_properties = workbook.find(f"{{{spreadsheet_ns}}}workbookPr")
        date_1904 = (
            workbook_properties is not None
            and workbook_properties.attrib.get("date1904", "0").lower() in {"1", "true"}
        )
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {
            node.attrib["Id"]: node.attrib["Target"]
            for node in relationships.findall(f"{{{package_rel_ns}}}Relationship")
        }
        sections: list[str] = []
        for sheet in workbook.findall(f".//{{{spreadsheet_ns}}}sheet"):
            sheet_name = sheet.attrib.get("name", "sheet")
            relation_id = sheet.attrib.get(f"{{{relationship_ns}}}id")
            target = targets.get(relation_id or "", "")
            if not target:
                continue
            normalized_target = str(PurePosixPath("xl") / target).replace("xl/../", "")
            root = ET.fromstring(archive.read(normalized_target))
            rows: list[str] = []
            for row in root.findall(f".//{{{spreadsheet_ns}}}row"):
                values: list[str] = []
                for cell in row.findall(f"{{{spreadsheet_ns}}}c"):
                    coordinate = cell.attrib.get("r", "?")
                    cell_type = cell.attrib.get("t")
                    style_index = (
                        int(cell.attrib["s"]) if cell.attrib.get("s", "").isdigit() else None
                    )
                    value_node = cell.find(f"{{{spreadsheet_ns}}}v")
                    if cell_type == "inlineStr":
                        value = "".join(
                            node.text or ""
                            for node in cell.findall(f".//{{{spreadsheet_ns}}}t")
                        )
                    elif value_node is None or value_node.text is None:
                        value = ""
                    elif cell_type == "s":
                        index = int(value_node.text)
                        value = shared[index] if 0 <= index < len(shared) else value_node.text
                    else:
                        value = _format_xlsx_number(
                            value_node.text,
                            style_index=style_index,
                            style_formats=style_formats,
                            date_1904=date_1904,
                        )
                    values.append(f"{coordinate}={value}")
                if any(value.strip() for value in values):
                    row_number = row.attrib.get("r", "?")
                    rows.append(f"[ROW {row_number}] " + "\t".join(values))
            sections.append(f"[SHEET {sheet_name}]\n" + "\n".join(rows))
    return "\n\n".join(sections)


def clean_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text.replace("\x00", ""))
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()


def load_general_document_titles(catalog_path: Path) -> dict[str, str]:
    """Parse the complete organizer-derived title catalog, failing closed.

    Keys are the exact delivered basenames (including extensions).  This keeps
    an accidental extension change or a second file for one ``docN`` from
    silently receiving a plausible-looking title.
    """

    try:
        lines = catalog_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"Cannot read general-document catalog {catalog_path}: {exc}") from exc

    titles: dict[str, str] = {}
    source_ids: set[str] = set()
    for line_number, line in enumerate(lines, start=1):
        if not re.match(
            r"^\s*-\s+`doc\d+\.(?:pdf|docx|xlsx|pptx)", line
        ):
            continue
        match = GENERAL_CATALOG_PATTERN.match(line)
        if match is None:
            raise ValueError(
                f"Malformed general-document catalog entry at line {line_number}: {line!r}"
            )
        filename = unicodedata.normalize("NFC", match.group("filename"))
        source_id = Path(filename).stem
        title = unicodedata.normalize("NFC", match.group("title").strip())
        if not title:
            raise ValueError(f"Empty title for {filename} at catalog line {line_number}")
        if filename in titles or source_id in source_ids:
            raise ValueError(f"Duplicate general-document catalog entry: {source_id}")
        titles[filename] = title
        source_ids.add(source_id)

    missing = sorted(EXPECTED_GENERAL_SOURCE_IDS - source_ids)
    unexpected = sorted(source_ids - EXPECTED_GENERAL_SOURCE_IDS)
    if missing or unexpected or len(titles) != len(EXPECTED_GENERAL_SOURCE_IDS):
        details: list[str] = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if unexpected:
            details.append("unexpected=" + ",".join(unexpected))
        details.append(f"count={len(titles)}, expected=58")
        raise ValueError("Invalid general-document catalog: " + "; ".join(details))
    return titles


def validate_general_document_sources(
    paths: list[Path], catalog_titles: dict[str, str]
) -> None:
    """Require the delivered general-document basenames to match the catalog."""

    actual_names = [unicodedata.normalize("NFC", path.name) for path in paths]
    duplicate_names = sorted(
        name for name in set(actual_names) if actual_names.count(name) > 1
    )
    actual = set(actual_names)
    expected = set(catalog_titles)
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    if duplicate_names or missing or unexpected or len(actual_names) != 58:
        details: list[str] = []
        if duplicate_names:
            details.append("duplicates=" + ",".join(duplicate_names))
        if missing:
            details.append("missing=" + ",".join(missing))
        if unexpected:
            details.append("unexpected=" + ",".join(unexpected))
        details.append(f"count={len(actual_names)}, expected=58")
        raise ValueError("General sources do not match the title catalog: " + "; ".join(details))


def title_hint(text: str, fallback: str) -> str:
    candidates: list[tuple[float, str]] = []
    for position, raw_line in enumerate(text.splitlines()[:80]):
        line = re.sub(r"\s+", " ", raw_line).strip(" -|•·")
        if re.fullmatch(
            r"\[(?:PAGE|SLIDE|SHEET|ROW|TABLE|OCR|IMAGE|EMBEDDED).*]",
            line,
            flags=re.IGNORECASE,
        ):
            continue
        if not (4 <= len(line) <= 160 and re.search(r"[가-힣A-Za-z]", line)):
            continue
        meaningful = semantic_chars(line)
        if meaningful < 4:
            continue
        keyword = bool(
            re.search(
                r"연금|퇴직|투자|펀드|신탁|업무매뉴얼|안내|가이드|FAQ|개요|세액|인출",
                line,
                flags=re.IGNORECASE,
            )
        )
        score = (100.0 if keyword else 0.0) + min(meaningful, 100) - position * 0.1
        candidates.append((score, line))
    return max(candidates, default=(0.0, fallback))[1]


def output_location(path: Path, input_root: Path) -> tuple[str, str, Path]:
    relative = path.relative_to(input_root)
    root_name = unicodedata.normalize("NFC", relative.parts[0])
    if root_name == "일반문서":
        if not re.fullmatch(r"doc\d+", path.stem):
            raise ValueError(f"Unexpected general-document name: {relative}")
        return path.stem, "general", Path("general") / f"{path.stem}.txt"
    if root_name != "투자설명서" or len(relative.parts) != 3:
        raise ValueError(f"Unexpected organizer-data path: {relative}")
    product_code = relative.parts[1]
    if not PRODUCT_CODE_PATTERN.fullmatch(product_code):
        raise ValueError(f"Invalid product code in organizer path: {relative}")
    return product_code, "product", Path("products") / f"{product_code}.txt"


def extract_source(
    path: Path,
    *,
    enable_ocr: bool,
    min_chars_per_page: int,
    ocr_dpi: int,
    ocr_workers: int,
) -> ExtractedDocument:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return extract_pdf(
            path,
            enable_ocr=enable_ocr,
            min_chars_per_page=min_chars_per_page,
            ocr_dpi=ocr_dpi,
            ocr_workers=ocr_workers,
        )
    if suffix == ".docx":
        text, used_ocr = extract_docx(path, enable_ocr=enable_ocr)
        method = "office-open-xml+embedded-tesseract" if used_ocr else "office-open-xml"
        return ExtractedDocument(text=text, method=method, page_count=None)
    if suffix == ".xlsx":
        return ExtractedDocument(
            text=extract_xlsx(path), method="office-open-xml", page_count=None
        )
    if suffix == ".pptx":
        text, used_ocr = extract_pptx(path, enable_ocr=enable_ocr)
        method = "office-open-xml+embedded-tesseract" if used_ocr else "office-open-xml"
        return ExtractedDocument(text=text, method=method, page_count=None)
    raise ValueError(f"Unsupported document: {path}")


def extract_one(
    path: Path,
    *,
    input_root: Path,
    output_root: Path,
    enable_ocr: bool,
    min_chars_per_page: int,
    ocr_dpi: int,
    ocr_workers: int,
    source_digest: str,
    product_aliases: tuple[str, ...],
    extracted: ExtractedDocument | None = None,
    general_title_hint: str | None = None,
) -> ManifestEntry:
    source_id, group, relative_output = output_location(path, input_root)
    suffix = path.suffix.lower()
    document = extracted or extract_source(
        path,
        enable_ocr=enable_ocr,
        min_chars_per_page=min_chars_per_page,
        ocr_dpi=ocr_dpi,
        ocr_workers=ocr_workers,
    )
    text = clean_text(document.text)
    if semantic_chars(text) == 0:
        raise RuntimeError(f"Extraction produced no meaningful content: {path}")
    method = document.method
    pages = document.page_count
    review_status = "quarantined" if source_id in QUARANTINED_SOURCE_IDS else "raw"
    relative_source = path.relative_to(input_root)
    aliases = product_aliases if group == "product" else ()
    canonical_source_id = aliases[0] if aliases else source_id
    if group == "general":
        if not general_title_hint:
            raise ValueError(f"Catalog title is required for general document: {relative_source}")
        derived_title_hint = general_title_hint
    else:
        if general_title_hint is not None:
            raise ValueError(f"Catalog title must not be applied to product: {relative_source}")
        derived_title_hint = title_hint(text, source_id)
    header = "\n".join(
        [
            "[METADATA]",
            "source_scope=organizer_provided",
            f"source_id={source_id}",
            f"source_group={group}",
            f"source_path={relative_source.as_posix()}",
            f"source_sha256={source_digest}",
            f"extraction_method={method}",
            f"extractor_fingerprint={EXTRACTOR_FINGERPRINT}",
            f"review_status={review_status}",
            f"ocr_requested={'true' if enable_ocr else 'false'}",
            f"ocr_dpi={ocr_dpi if enable_ocr else ''}",
            f"min_semantic_chars_per_page={min_chars_per_page if enable_ocr else ''}",
            f"canonical_source_id={canonical_source_id}",
            f"product_aliases={','.join(aliases)}",
            f"page_count={pages if pages is not None else ''}",
            "[CONTENT]",
        ]
    )
    destination = output_root / relative_output
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(f"{header}\n{text}\n", encoding="utf-8")
    return ManifestEntry(
        source_id=source_id,
        source_group=group,
        source_path=relative_source.as_posix(),
        output_path=relative_output.as_posix(),
        media_type=suffix.removeprefix("."),
        sha256=source_digest,
        size_bytes=path.stat().st_size,
        page_count=pages,
        extraction_method=method,
        review_status=review_status,
        ocr_requested=enable_ocr,
        ocr_dpi=ocr_dpi if enable_ocr else None,
        min_semantic_chars_per_page=min_chars_per_page if enable_ocr else None,
        extracted_chars=len(text.strip()),
        title_hint=derived_title_hint,
        text_sha256=sha256_file(destination),
        extractor_fingerprint=EXTRACTOR_FINGERPRINT,
        canonical_source_id=canonical_source_id,
        product_aliases=aliases,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--general-catalog",
        type=Path,
        default=DEFAULT_GENERAL_CATALOG,
        help="authoritative derived titles for doc1 through doc58",
    )
    parser.add_argument("--ocr", action="store_true", help="OCR sparse PDF pages and Office images")
    parser.add_argument("--min-chars-per-page", type=int, default=120)
    parser.add_argument("--ocr-dpi", type=int, default=220)
    parser.add_argument("--ocr-workers", type=int, default=2)
    return parser.parse_args()


def _publish_staged_directory(staging: Path, output_root: Path) -> None:
    backup: Path | None = None
    if output_root.exists():
        backup = output_root.parent / f".{output_root.name}.previous-{uuid.uuid4().hex}"
        os.replace(output_root, backup)
    try:
        os.replace(staging, output_root)
    except BaseException:
        if backup is not None and backup.exists() and not output_root.exists():
            os.replace(backup, output_root)
        raise
    if backup is not None:
        shutil.rmtree(backup)


def main() -> int:
    args = parse_args()
    input_root = args.input.resolve()
    output_root = args.output.resolve()
    if not input_root.is_dir():
        raise SystemExit(f"Input directory does not exist: {input_root}")
    if args.min_chars_per_page < 0:
        raise SystemExit("--min-chars-per-page must be zero or greater")
    if not 100 <= args.ocr_dpi <= 600:
        raise SystemExit("--ocr-dpi must be between 100 and 600")
    if not 1 <= args.ocr_workers <= 8:
        raise SystemExit("--ocr-workers must be between 1 and 8")
    supported = {".pdf", ".docx", ".xlsx", ".pptx"}
    paths = sorted(
        path for path in input_root.rglob("*") if path.is_file() and path.suffix.lower() in supported
    )
    general_titles = load_general_document_titles(args.general_catalog.resolve())
    general_paths = [
        path for path in paths if output_location(path, input_root)[1] == "general"
    ]
    validate_general_document_sources(general_paths, general_titles)
    digests = {path: sha256_file(path) for path in paths}
    product_alias_map: dict[str, list[str]] = {}
    for path in paths:
        source_id, group, _ = output_location(path, input_root)
        if group == "product":
            product_alias_map.setdefault(digests[path], []).append(source_id)
    aliases_by_digest = {
        digest: tuple(sorted(aliases)) for digest, aliases in product_alias_map.items()
    }

    output_root.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{output_root.name}.build-", dir=output_root.parent)
    )
    entries: list[ManifestEntry] = []
    # Reuse extraction for exact duplicate product prospectuses while emitting a
    # separate alias-aware output for every organizer path.
    extracted_products: dict[str, ExtractedDocument] = {}
    try:
        for position, path in enumerate(paths, start=1):
            source_id, group, _ = output_location(path, input_root)
            digest = digests[path]
            extracted = extracted_products.get(digest) if group == "product" else None
            print(
                f"[{position}/{len(paths)}] "
                f"{'alias' if extracted is not None else 'build'} "
                f"{path.relative_to(input_root)}",
                flush=True,
            )
            if extracted is None:
                extracted = extract_source(
                    path,
                    enable_ocr=args.ocr,
                    min_chars_per_page=args.min_chars_per_page,
                    ocr_dpi=args.ocr_dpi,
                    ocr_workers=args.ocr_workers,
                )
                if group == "product":
                    extracted_products[digest] = extracted
            entries.append(
                extract_one(
                    path,
                    input_root=input_root,
                    output_root=staging,
                    enable_ocr=args.ocr,
                    min_chars_per_page=args.min_chars_per_page,
                    ocr_dpi=args.ocr_dpi,
                    ocr_workers=args.ocr_workers,
                    source_digest=digest,
                    product_aliases=aliases_by_digest.get(digest, ()),
                    extracted=extracted,
                    general_title_hint=(
                        general_titles[path.name] if group == "general" else None
                    ),
                )
            )
        manifest = {
            "source": "organizer-provided pension track data",
            "extractor_fingerprint": EXTRACTOR_FINGERPRINT,
            "build_options": {
                "ocr": args.ocr,
                "ocr_dpi": args.ocr_dpi,
                "ocr_workers": args.ocr_workers,
                "min_semantic_chars_per_page": args.min_chars_per_page,
                "general_title_catalog_sha256": sha256_file(
                    args.general_catalog.resolve()
                ),
            },
            "document_count": len(entries),
            "documents": [asdict(entry) for entry in entries],
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        report = validate_corpus(staging, input_root)
        report.require_valid()
        _publish_staged_directory(staging, output_root)
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    print(f"Published {len(entries)} validated documents to {output_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
