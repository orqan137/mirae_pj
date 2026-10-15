from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation

from app.product_values import normalize_date


PERCENT_RE = re.compile(
    r"(?<![\w.])([-+]?(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?))\s*%"
)
CURRENCY_RE = re.compile(
    r"(?<![A-Za-z0-9_.])([-+]?(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?))\s*"
    r"(백\s*만\s*원|천\s*만\s*원|만\s*원|억\s*원|조\s*원|원)(?![A-Za-z0-9])"
)
BALANCE_UNITS_RE = re.compile(
    r"(?<![\w.])([-+]?(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?))\s*"
    r"(백만|억)\s*좌(?![A-Za-z0-9])"
)
CITATION_RE = re.compile(r"\[근거:([^\]\r\n]+)\]")
ANY_CITATION_RE = re.compile(r"\[(?:근거|수치출처):([^\]\r\n]+)\]")
CONTEXT_CITATION_RE = re.compile(r"(?m)(?:^|\s)citation_id=([^\s\r\n]+)")
RISK_GRADE_RE = re.compile(r"(?<!\d)(\d{1,2})\s*등급")
DATE_RE = re.compile(
    r"(?<!\d)(?:20)?\d{2}\s*(?:[./-]|년\s*)\s*\d{1,2}\s*"
    r"(?:[./-]|월\s*)\s*\d{1,2}(?:\s*일)?(?!\d)"
)
ANY_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9.])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(?![A-Za-z0-9.])"
)
# Sentence selection and numeric evidence signatures must recognize exactly
# the same amounts; otherwise spaced units bypass the citation check entirely.
SIGNATURE_CURRENCY_RE = CURRENCY_RE
_CURRENCY_UNIT_MULTIPLIERS = {
    "원": Decimal(1),
    "만원": Decimal(10_000),
    "천만원": Decimal(10_000_000),
    "백만원": Decimal(1_000_000),
    "억원": Decimal(100_000_000),
    "조원": Decimal(1_000_000_000_000),
}


def normalize_number(value: str) -> str | None:
    try:
        parsed = Decimal(value.replace(",", ""))
    except InvalidOperation:
        return None
    rendered = format(parsed, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def normalize_currency_won(number: str, unit: str) -> str | None:
    """Return a Korean currency expression as a canonical won amount."""

    normalized_number = normalize_number(number)
    normalized_unit = re.sub(r"\s+", "", unit)
    multiplier = _CURRENCY_UNIT_MULTIPLIERS.get(normalized_unit)
    if normalized_number is None or multiplier is None:
        return None
    return normalize_number(format(Decimal(normalized_number) * multiplier, "f"))


def number_inventory(value: str) -> frozenset[str]:
    return frozenset(
        normalized
        for match in ANY_NUMBER_RE.finditer(unicodedata.normalize("NFKC", value))
        if (normalized := normalize_number(match.group(0))) is not None
    )


def date_inventory(value: str) -> frozenset[str]:
    return frozenset(
        normalized
        for match in DATE_RE.finditer(unicodedata.normalize("NFKC", value))
        if (normalized := normalize_date(match.group(0))) is not None
    )


def citation_excerpt_inventory(context: str) -> dict[str, str]:
    """Bind each public citation ID to the exact following evidence block."""

    matches = tuple(CONTEXT_CITATION_RE.finditer(context))
    grouped: dict[str, list[str]] = {}
    for position, match in enumerate(matches):
        reference = match.group(1).strip()
        line_end = context.find("\n", match.end())
        start = len(context) if line_end < 0 else line_end + 1
        if position + 1 < len(matches):
            # The regex deliberately consumes the whitespace before every
            # marker except the first. Its start is therefore already the
            # exact end boundary of the preceding evidence block. Searching
            # backwards for another newline drops a one-line excerpt.
            end = matches[position + 1].start()
        else:
            end = len(context)
        excerpt = context[start:end].strip()
        if reference and excerpt:
            grouped.setdefault(reference, []).append(excerpt)
    return {reference: "\n".join(blocks) for reference, blocks in grouped.items()}


def citation_spans(value: str) -> tuple[tuple[int, int], ...]:
    return tuple((match.start(), match.end()) for match in ANY_CITATION_RE.finditer(value))


def inside_span(position: int, spans: tuple[tuple[int, int], ...]) -> bool:
    return any(start <= position < end for start, end in spans)


def sentence_span(value: str, position: int) -> tuple[int, int]:
    citation_ranges = citation_spans(value)
    boundaries = tuple(
        match
        # A period sitting inside a token is not a sentence boundary. Splitting
        # "R2_KR510902511M.pdf:page=3" left ".pdf:page=3" behind as its own
        # sentence, which the recovery steps then had to carry or strip.
        for match in re.finditer(r"(?<!\d)\.(?![\dA-Za-z])|[!?。]|\n", value)
        if not inside_span(match.start(), citation_ranges)
    )
    start = 0
    end = len(value)
    for boundary in boundaries:
        if boundary.end() <= position:
            start = boundary.end()
        elif boundary.start() >= position:
            end = boundary.start()
            break
    return start, end


def first_sentence(value: str) -> str:
    citation_ranges = citation_spans(value)
    boundary = next(
        (
            match
            # A period sitting inside a token is not a sentence boundary. Splitting
        # "R2_KR510902511M.pdf:page=3" left ".pdf:page=3" behind as its own
        # sentence, which the recovery steps then had to carry or strip.
        for match in re.finditer(r"(?<!\d)\.(?![\dA-Za-z])|[!?。]|\n", value)
            if not inside_span(match.start(), citation_ranges)
        ),
        None,
    )
    return value if boundary is None else value[: boundary.start()]


_TRAILING_CITATION_RE = re.compile(
    r"[ \t]*(?:\[(?:근거|수치출처):[^\]\r\n]*\][ \t]*)+"
)


def sentence_spans(value: str) -> tuple[tuple[int, int], ...]:
    citation_ranges = citation_spans(value)
    boundaries = tuple(
        match
        # A period sitting inside a token is not a sentence boundary. Splitting
        # "R2_KR510902511M.pdf:page=3" left ".pdf:page=3" behind as its own
        # sentence, which the recovery steps then had to carry or strip.
        for match in re.finditer(r"(?<!\d)\.(?![\dA-Za-z])|[!?。]|\n", value)
        if not inside_span(match.start(), citation_ranges)
    )
    spans: list[tuple[int, int]] = []
    start = 0
    for boundary in boundaries:
        if value[start : boundary.start()].strip():
            # "…입니다. [근거:doc20:chunk=1]" places the marker after the stop,
            # which is how the instruction to put it at the end of the sentence
            # reads. Splitting there left the claim with no citation and the
            # citation with no claim, so a fully grounded answer was discarded
            # over where its punctuation sat. The marker is absorbed into the
            # sentence it follows; anything after it starts the next one.
            end = boundary.start()
            trailing = _TRAILING_CITATION_RE.match(value, boundary.end())
            if trailing is not None:
                spans.append((start, trailing.end()))
                start = trailing.end()
                continue
            spans.append((start, end))
        start = boundary.end()
    if value[start:].strip():
        spans.append((start, len(value)))
    return tuple(spans)


def split_citation_references(written: str) -> tuple[str, ...]:
    """Split a citation bracket that names several sources."""

    return tuple(part.strip() for part in re.split(r"[,;]", written) if part.strip()) or (
        written.strip(),
    )


def resolve_citation_reference(reference: str, allowed: frozenset[str]) -> str | None:
    """Resolve exact and unambiguous abbreviated citation identifiers.

    A citation identifier never contains whitespace, so a stray space inside
    one is a typing slip rather than a different source. "[근거: product:
    KR5152420028:risk:page=1]" named the right prospectus page and was rejected
    over that single space, taking a correct answer with it.
    """

    reference = re.sub(r"\s+", "", reference)
    if reference in allowed:
        return reference
    prefix = f"{reference}:"
    matches = tuple(item for item in allowed if item.startswith(prefix))
    if len(matches) == 1:
        return matches[0]
    source = reference.split(":", maxsplit=1)[0]
    if not source:
        return None
    same_source = tuple(
        item for item in allowed if item.split(":", maxsplit=1)[0] == source
    )
    return same_source[0] if len(same_source) == 1 else None


def critical_citation_signatures(value: str) -> frozenset[str]:
    """Return exact consequential values that can bind a claim to an excerpt."""

    normalized = unicodedata.normalize("NFKC", value)
    signatures: set[str] = set()
    for match in PERCENT_RE.finditer(normalized):
        number = normalize_number(match.group(1))
        if number is not None:
            signatures.add(f"percent:{number}")
    for match in SIGNATURE_CURRENCY_RE.finditer(normalized):
        won = normalize_currency_won(match.group(1), match.group(2))
        if won is not None:
            signatures.add(f"currency_won:{won}")
    for match in BALANCE_UNITS_RE.finditer(normalized):
        number = normalize_number(match.group(1))
        if number is not None:
            signatures.add(f"balance:{number}:{match.group(2)}")
    for match in DATE_RE.finditer(normalized):
        date = normalize_date(match.group(0))
        if date is not None:
            signatures.add(f"date:{date}")
    # 위험등급 is one of the axes the task names, and it is the whole answer to
    # "위험등급이 몇 등급인가요". Carrying no signature, a graded claim could be
    # neither bound to its excerpt by repair nor checked against it, so the
    # sentence holding the grade was dropped and the answer kept only the
    # commentary around it.
    for match in RISK_GRADE_RE.finditer(normalized):
        signatures.add(f"grade:{int(match.group(1))}")
    return frozenset(signatures)
