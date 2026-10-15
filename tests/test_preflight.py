from __future__ import annotations

import hashlib
from pathlib import Path

import pytest


def test_preflight_dotenv_preserves_literal_secrets_and_reads_endpoint(tmp_path):
    from scripts.preflight import parse_env

    env_file = tmp_path / ".env"
    env_file.write_text(
        "HCX_API_KEY='literal-${TOKEN}#value'\n"
        "EVALUATION_ENDPOINT=https://example.com/answer # deployment\n"
    )
    values = parse_env(env_file)
    assert values["HCX_API_KEY"] == "literal-${TOKEN}#value"
    assert values["EVALUATION_ENDPOINT"] == "https://example.com/answer"

from scripts.preflight import (
    REQUIRED_ORGANIZER_ARCHIVES,
    validate_direct_hcx_env,
    validate_evaluation_endpoint,
    validate_organizer_archives,
)


def _write_archive_set(tmp_path: Path) -> Path:
    root = tmp_path / "official"
    lines: list[str] = []
    for relative in sorted(REQUIRED_ORGANIZER_ARCHIVES):
        archive = root / relative
        archive.parent.mkdir(parents=True, exist_ok=True)
        content = f"fixture:{relative}".encode()
        archive.write_bytes(content)
        lines.append(f"{hashlib.sha256(content).hexdigest()}  {relative}")
    checksums = root / "SHA256SUMS"
    checksums.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return checksums


def test_organizer_archive_set_passes(tmp_path: Path) -> None:
    checksums = _write_archive_set(tmp_path)

    assert validate_organizer_archives(checksums) == []


def test_organizer_checksum_manifest_must_exist(tmp_path: Path) -> None:
    errors = validate_organizer_archives(tmp_path / "missing" / "SHA256SUMS")

    assert any("manifest is missing" in error for error in errors)


def test_organizer_checksum_manifest_must_not_be_empty(tmp_path: Path) -> None:
    checksums = tmp_path / "SHA256SUMS"
    checksums.write_text("\n", encoding="utf-8")

    assert validate_organizer_archives(checksums) == [
        "organizer checksum manifest is empty"
    ]


@pytest.mark.parametrize("remove_from_manifest", [False, True])
def test_required_organizer_archive_must_exist_and_be_listed(
    tmp_path: Path, remove_from_manifest: bool
) -> None:
    checksums = _write_archive_set(tmp_path)
    relative = "original/data.zip"
    if remove_from_manifest:
        remaining = [
            line
            for line in checksums.read_text(encoding="utf-8").splitlines()
            if not line.endswith(relative)
        ]
        checksums.write_text("\n".join(remaining) + "\n", encoding="utf-8")
    else:
        (checksums.parent / relative).unlink()

    errors = validate_organizer_archives(checksums)

    expected = "not checksummed" if remove_from_manifest else "archive is missing"
    assert any(expected in error and relative in error for error in errors)


def test_organizer_archive_digest_must_match(tmp_path: Path) -> None:
    checksums = _write_archive_set(tmp_path)
    relative = "original/nested/docs.zip"
    (checksums.parent / relative).write_bytes(b"tampered")

    errors = validate_organizer_archives(checksums)

    assert any("checksum mismatch" in error and relative in error for error in errors)


@pytest.mark.parametrize(
    "bad_line",
    (
        "not-a-checksum original/data.zip",
        f"{'0' * 64}",
        f"{'0' * 64}  ../outside.zip",
    ),
)
def test_organizer_checksum_manifest_rejects_malformed_or_unsafe_lines(
    tmp_path: Path, bad_line: str
) -> None:
    checksums = _write_archive_set(tmp_path)
    checksums.write_text(
        checksums.read_text(encoding="utf-8") + bad_line + "\n",
        encoding="utf-8",
    )

    errors = validate_organizer_archives(checksums)

    assert any("malformed" in error or "unsafe path" in error for error in errors)


@pytest.mark.parametrize(
    "endpoint",
    (
        "http://203.0.113.10/answer",
        "https://pension.example.com/answer",
        "http://203.0.113.10:80/answer",
        "https://pension.example.com:443/answer",
    ),
)
def test_evaluation_endpoint_accepts_absolute_standard_urls(endpoint: str) -> None:
    assert validate_evaluation_endpoint(endpoint) == []


@pytest.mark.parametrize(
    ("endpoint", "expected"),
    (
        ("http:/answer", "absolute HTTP(S)"),
        ("ftp://example.com/answer", "absolute HTTP(S)"),
        ("http://user:secret@example.com/answer", "credentials"),
        ("http://example.com:8080/answer", "standard port 80"),
        ("https://example.com:444/answer", "standard port 443"),
        ("https://example.com/Answer", "exact /answer"),
        ("https://example.com/answer?x=1", "must not contain params"),
        ("http://YOUR_STATIC_PUBLIC_IP/answer", "placeholder"),
    ),
)
def test_evaluation_endpoint_rejects_non_submission_shapes(
    endpoint: str, expected: str
) -> None:
    assert any(expected in error for error in validate_evaluation_endpoint(endpoint))


def test_direct_hcx_preflight_accepts_official_provider_target() -> None:
    env = {
        "HCX_API_URL": (
            "https://clovastudio.stream.ntruss.com/"
            "v3/chat-completions/HCX-007"
        ),
        "HCX_API_KEY": "secret",
        "HCX_MODEL": "HCX-007",
        "HCX_API_STYLE": "clova-v3",
    }

    assert validate_direct_hcx_env(env) == []


def test_direct_hcx_preflight_accepts_openai_body_model() -> None:
    env = {
        "HCX_API_URL": (
            "https://clovastudio.stream.ntruss.com/"
            "v1/openai/chat/completions"
        ),
        "HCX_API_KEY": "secret",
        "HCX_MODEL": "HCX-007",
        "HCX_API_STYLE": "openai-v3",
    }

    assert validate_direct_hcx_env(env) == []


@pytest.mark.parametrize(
    ("api_url", "model", "expected"),
    (
        (
            "http://clovastudio.stream.ntruss.com/v3/chat-completions/HCX-007",
            "HCX-007",
            "HTTPS URL",
        ),
        (
            "https://ntruss.com.example.org/v3/chat-completions/HCX-007",
            "HCX-007",
            "CLOVA Studio host",
        ),
        (
            "https://evilntruss.com/v3/chat-completions/HCX-007",
            "HCX-007",
            "CLOVA Studio host",
        ),
        (
            "https://clovastudio.stream.ntruss.com/v3/chat-completions/model",
            "other-model",
            "HCX-",
        ),
        (
            "https://clovastudio.stream.ntruss.com/v3/chat-completions/HCX-005",
            "HCX-007",
            "matching model",
        ),
    ),
)
def test_direct_hcx_preflight_rejects_untrusted_target_or_model(
    api_url: str, model: str, expected: str
) -> None:
    errors = validate_direct_hcx_env(
        {
            "HCX_API_URL": api_url,
            "HCX_API_KEY": "secret",
            "HCX_MODEL": model,
            "HCX_API_STYLE": "clova-v3",
        }
    )

    assert any(expected in error for error in errors)


@pytest.mark.parametrize(
    ("api_style", "path"),
    (
        ("clova-v3", "/v3/chat-completions/HCX-007"),
        ("openai-v3", "/v1/openai/chat/completions"),
    ),
)
def test_direct_hcx_preflight_rejects_other_ntruss_services(
    api_style: str, path: str
) -> None:
    errors = validate_direct_hcx_env(
        {
            "HCX_API_URL": f"https://objectstorage.ntruss.com{path}",
            "HCX_API_KEY": "secret",
            "HCX_MODEL": "HCX-007",
            "HCX_API_STYLE": api_style,
        }
    )

    assert any("approved CLOVA Studio host" in error for error in errors)


@pytest.mark.parametrize(
    ("overrides", "expected"),
    (
        ({"HCX_MAX_TOKENS": "4097"}, "at or below 4096"),
        ({"HCX_THINKING_EFFORT": "low"}, "THINKING_EFFORT=none"),
        ({"HCX_TEMPERATURE": "1.1"}, "between 0 and 1"),
        (
            {"MAX_CONTEXT_CHARS": "16000", "HCX_MAX_TOKENS": "4096"},
            "context budget",
        ),
    ),
)
def test_direct_hcx_preflight_matches_runtime_capability_guards(
    overrides: dict[str, str], expected: str
) -> None:
    env = {
        "HCX_API_URL": (
            "https://clovastudio.stream.ntruss.com/"
            "v3/chat-completions/HCX-005"
        ),
        "HCX_API_KEY": "secret",
        "HCX_MODEL": "HCX-005",
        "HCX_API_STYLE": "clova-v3",
        **overrides,
    }

    assert any(expected in error for error in validate_direct_hcx_env(env))
