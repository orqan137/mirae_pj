#!/usr/bin/env python3
"""Fail closed when submission-critical configuration is still incomplete."""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import Settings, VALID_HCX_API_STYLES
from app.product_facts import ProductEvidenceError, ProductEvidenceIndex
from app.source_governance import SourceGovernanceError, load_source_governance

try:
    from .corpus_integrity import validate_corpus
except ImportError:  # Direct execution: python3 scripts/preflight.py
    from corpus_integrity import validate_corpus


REQUIRED_ORGANIZER_ARCHIVES = frozenset(
    {
        "original/data.zip",
        "original/과제소개자료.zip",
        "original/nested/docs.zip",
        "original/nested/연금.zip",
        "original/nested/투자설명서.zip",
    }
)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def parse_env(path: Path) -> dict[str, str]:
    return {
        key: value
        for key, value in dotenv_values(path, interpolate=False).items()
        if value is not None
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_evaluation_endpoint(endpoint: str) -> list[str]:
    """Validate the submitted URL shape before any external reachability test."""

    errors: list[str] = []
    if "YOUR_" in endpoint:
        errors.append("EVALUATION_ENDPOINT still contains a placeholder")
    parsed = urlparse(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        errors.append("EVALUATION_ENDPOINT must be an absolute HTTP(S) URL")
        return errors
    if parsed.username is not None or parsed.password is not None:
        errors.append("EVALUATION_ENDPOINT must not contain credentials")
    if parsed.path != "/answer":
        errors.append("EVALUATION_ENDPOINT must end with the exact /answer path")
    if parsed.params or parsed.query or parsed.fragment:
        errors.append("EVALUATION_ENDPOINT must not contain params, query, or fragment")
    try:
        port = parsed.port
    except ValueError:
        errors.append("EVALUATION_ENDPOINT contains an invalid port")
    else:
        expected_port = 80 if parsed.scheme == "http" else 443
        if port is not None and port != expected_port:
            errors.append(
                f"EVALUATION_ENDPOINT must use standard port {expected_port} for {parsed.scheme}"
            )
    return errors


def validate_direct_hcx_env(env: dict[str, str]) -> list[str]:
    """Validate submission-critical settings for a direct HCX connection."""

    errors: list[str] = []
    for name in ("HCX_API_URL", "HCX_API_KEY", "HCX_MODEL"):
        if not env.get(name):
            errors.append(f"{name} is required for the hcx backend")
    if env.get("HCX_API_STYLE") not in VALID_HCX_API_STYLES:
        errors.append("HCX_API_STYLE must be clova-v3 or openai-v3")

    if not errors:
        production_env = dict(env)
        production_env["APP_ENV"] = "production"
        production_env["AGENT_BACKEND"] = "hcx"
        try:
            Settings.from_mapping(production_env).validate()
        except (TypeError, ValueError) as exc:
            errors.append(str(exc))
    return errors


def validate_organizer_archives(checksums: Path) -> list[str]:
    """Verify the complete organizer archive set without silently skipping files."""

    errors: list[str] = []
    if not checksums.is_file():
        return [f"organizer checksum manifest is missing: {checksums}"]
    try:
        lines = [
            line.strip()
            for line in checksums.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, UnicodeError) as exc:
        return [f"organizer checksum manifest is unreadable: {exc}"]
    if not lines:
        return ["organizer checksum manifest is empty"]

    archive_root = checksums.parent.resolve()
    declared: dict[str, str] = {}
    for line_number, line in enumerate(lines, start=1):
        parts = line.split(maxsplit=1)
        if len(parts) != 2 or not SHA256_PATTERN.fullmatch(parts[0]):
            errors.append(
                f"organizer checksum manifest line {line_number} is malformed"
            )
            continue
        expected, raw_relative = parts
        relative = raw_relative.removeprefix("*")
        candidate = (checksums.parent / relative).resolve()
        if (
            not relative
            or Path(relative).is_absolute()
            or not candidate.is_relative_to(archive_root)
        ):
            errors.append(
                f"organizer checksum manifest line {line_number} has an unsafe path"
            )
            continue
        if relative in declared:
            errors.append(f"organizer checksum entry is duplicated: {relative}")
            continue
        declared[relative] = expected

    missing_entries = sorted(REQUIRED_ORGANIZER_ARCHIVES - declared.keys())
    for relative in missing_entries:
        errors.append(f"required organizer archive is not checksummed: {relative}")
    for relative, expected in declared.items():
        source = checksums.parent / relative
        if not source.is_file():
            errors.append(f"organizer archive is missing: {relative}")
        elif sha256_file(source) != expected:
            errors.append(f"organizer archive checksum mismatch: {relative}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=PROJECT_ROOT / ".env")
    args = parser.parse_args()
    errors: list[str] = []

    if not args.env_file.exists():
        errors.append(f"production env file is missing: {args.env_file}")
    else:
        env = parse_env(args.env_file)
        errors.extend(validate_evaluation_endpoint(env.get("EVALUATION_ENDPOINT", "")))
        if env.get("APP_ENV") != "production":
            errors.append("APP_ENV must be production")
        backend = env.get("AGENT_BACKEND")
        if backend not in {"hcx", "http"}:
            errors.append("AGENT_BACKEND must be hcx or http")
        if backend == "hcx":
            errors.extend(validate_direct_hcx_env(env))
        if backend == "http":
            if not env.get("AGENT_UPSTREAM_URL"):
                errors.append("AGENT_UPSTREAM_URL is required for the http backend")
            if env.get("AGENT_UPSTREAM_HCX_ONLY", "").lower() != "true":
                errors.append("AGENT_UPSTREAM_HCX_ONLY must be true in production")
            if not env.get("AGENT_UPSTREAM_VERSION"):
                errors.append("AGENT_UPSTREAM_VERSION is required in production")
            if env.get("AGENT_UPSTREAM_METHOD", "POST").upper() != "POST":
                errors.append("production upstream Agent must use POST to avoid query logging")

    corpus_report = validate_corpus(
        PROJECT_ROOT / "data" / "knowledge",
        PROJECT_ROOT / "docs" / "official" / "extracted" / "data",
    )
    errors.extend(f"knowledge corpus: {error}" for error in corpus_report.errors)

    corpus_manifest = PROJECT_ROOT / "data" / "knowledge" / "manifest.json"
    product_evidence = PROJECT_ROOT / "data" / "structured" / "product_evidence.json"
    source_governance = (
        PROJECT_ROOT / "data" / "structured" / "general_source_governance.json"
    )
    if not product_evidence.exists():
        errors.append("structured product evidence index is missing")
    elif corpus_manifest.exists():
        try:
            ProductEvidenceIndex.from_path(
                product_evidence,
                expected_manifest_sha256=sha256_file(corpus_manifest),
                expected_record_count=100,
            )
        except ProductEvidenceError as exc:
            errors.append(f"structured product evidence index is invalid: {exc}")
    try:
        load_source_governance(
            source_governance,
            expected_source_ids={f"doc{number}" for number in range(1, 59)},
        )
    except SourceGovernanceError as exc:
        errors.append(f"general source governance registry is invalid: {exc}")

    checksums = PROJECT_ROOT / "docs" / "official" / "SHA256SUMS"
    errors.extend(validate_organizer_archives(checksums))

    if errors:
        print("PREFLIGHT FAILED")
        for error in errors:
            print(f"- {error}")
        return 1

    print("PREFLIGHT PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
