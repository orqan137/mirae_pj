from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


KOREAN_QUERY_NORMALIZER_VERSION = "pension-korean-query-normalizer-v1"

# Project-authored lexical rules, not copied corpus sentences. Only pension
# compounds whose spacing does not change their meaning are canonicalized.
_DOMAIN_COMPOUNDS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"개인\s*형\s*퇴직\s*연금", re.IGNORECASE), "개인형퇴직연금"),
    (re.compile(r"연금\s*수령\s*한도", re.IGNORECASE), "연금수령한도"),
    (re.compile(r"연금\s*저축", re.IGNORECASE), "연금저축"),
    (re.compile(r"퇴직\s*연금", re.IGNORECASE), "퇴직연금"),
    (re.compile(r"국민\s*연금", re.IGNORECASE), "국민연금"),
    (re.compile(r"개인\s*연금", re.IGNORECASE), "개인연금"),
    (re.compile(r"연금\s*계좌", re.IGNORECASE), "연금계좌"),
    (re.compile(r"세액\s*공제", re.IGNORECASE), "세액공제"),
    (re.compile(r"소득\s*공제", re.IGNORECASE), "소득공제"),
    (re.compile(r"중도\s*인출", re.IGNORECASE), "중도인출"),
    (re.compile(r"실물\s*이전", re.IGNORECASE), "실물이전"),
    (re.compile(r"디폴트\s*옵션", re.IGNORECASE), "디폴트옵션"),
    (re.compile(r"퇴직\s*소득세", re.IGNORECASE), "퇴직소득세"),
    (re.compile(r"연금\s*소득세", re.IGNORECASE), "연금소득세"),
    (re.compile(r"확정\s*급여", re.IGNORECASE), "확정급여"),
    (re.compile(r"확정\s*기여", re.IGNORECASE), "확정기여"),
    (re.compile(r"아이\s*알\s*피", re.IGNORECASE), "irp"),
)

_PARTICLES = tuple(
    sorted(
        (
            "으로부터",
            "에서의",
            "에서는",
            "에게서",
            "으로써",
            "으로는",
            "으로만",
            "에게",
            "에서",
            "부터",
            "까지",
            "처럼",
            "에는",
            "에만",
            "이라도",
            "이나",
            "으로",
            "은",
            "는",
            "이",
            "가",
            "을",
            "를",
            "의",
            "도",
            "만",
            "로",
            "에",
            "와",
            "과",
        ),
        key=len,
        reverse=True,
    )
)


@dataclass(frozen=True, slots=True)
class KoreanQuerySurface:
    """Layer-1/2 forms used without changing the submitted question."""

    original: str
    normalized: str
    compact: str


def normalize_korean_query(text: str) -> KoreanQuerySurface:
    """Normalize Unicode, spacing and a closed set of pension compounds."""

    normalized = re.sub(
        r"\s+", " ", unicodedata.normalize("NFKC", text).casefold()
    ).strip()
    for pattern, replacement in _DOMAIN_COMPOUNDS:
        normalized = pattern.sub(replacement, normalized)
    return KoreanQuerySurface(
        original=text,
        normalized=normalized,
        compact=re.sub(r"\s+", "", normalized),
    )


def strip_final_particle(token: str) -> str:
    """Remove one known final particle from a token for controlled matching."""

    for particle in _PARTICLES:
        if len(token) > len(particle) + 1 and token.endswith(particle):
            return token[: -len(particle)]
    return token


def contains_predicate_stem(text: str, stems: tuple[str, ...]) -> bool:
    """Match project-authored predicate stems across Korean inflections."""

    normalized = normalize_korean_query(text).normalized
    return any(stem in normalized for stem in stems)
