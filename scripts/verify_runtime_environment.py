#!/usr/bin/env python3
"""Verify Python, dependency locks, venv and container runtime boundaries."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VERIFIER_VERSION = "pension-runtime-environment-v1"
_PIN_RE = re.compile(r"^([A-Za-z0-9_.-]+)(?:\[[A-Za-z0-9_,.-]+\])?==([^\s;]+)")
_HASH_RE = re.compile(r"--hash=sha256:([0-9a-f]{64})(?:\s|$)")
_BASE_RE = re.compile(
    r"^FROM python:3\.12-slim@sha256:[0-9a-f]{64} AS app-base$"
)


def _canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _logical_lines(path: Path) -> tuple[str, ...]:
    combined = path.read_text(encoding="utf-8").replace("\\\n", " ")
    return tuple(
        line.strip()
        for line in combined.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


def requirement_pins(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in _logical_lines(path):
        if line.startswith("-"):
            continue
        match = _PIN_RE.match(line)
        if match is None:
            raise ValueError(f"{path.name} contains an unpinned requirement: {line}")
        name = _canonical(match.group(1))
        if name in pins:
            raise ValueError(f"{path.name} duplicates requirement: {name}")
        pins[name] = match.group(2)
    return pins


def lock_pins(path: Path) -> dict[str, str]:
    lines = _logical_lines(path)
    if "--only-binary=:all:" not in lines or "--require-hashes" not in lines:
        raise ValueError(f"{path.name} must require binary artifacts and hashes")
    pins: dict[str, str] = {}
    for line in lines:
        if line.startswith("-"):
            continue
        match = _PIN_RE.match(line)
        if match is None:
            raise ValueError(f"{path.name} contains an invalid requirement: {line}")
        if not _HASH_RE.search(line):
            raise ValueError(f"{path.name} has an unhashed requirement: {line}")
        name = _canonical(match.group(1))
        if name in pins:
            raise ValueError(f"{path.name} duplicates requirement: {name}")
        pins[name] = match.group(2)
    return pins


def validate_repository_contract() -> tuple[list[str], dict[str, object]]:
    errors: list[str] = []
    files = {
        "requirements": PROJECT_ROOT / "requirements.txt",
        "requirements_lock": PROJECT_ROOT / "requirements.lock",
        "requirements_dev": PROJECT_ROOT / "requirements-dev.txt",
        "requirements_dev_lock": PROJECT_ROOT / "requirements-dev.lock",
        "dockerfile": PROJECT_ROOT / "Dockerfile",
        "compose": PROJECT_ROOT / "docker-compose.yml",
    }
    try:
        production = requirement_pins(files["requirements"])
        production_lock = lock_pins(files["requirements_lock"])
        development = requirement_pins(files["requirements_dev"])
        development_lock = lock_pins(files["requirements_dev_lock"])
    except (OSError, UnicodeError, ValueError) as exc:
        errors.append(str(exc))
        production = {}
        production_lock = {}
        development = {}
        development_lock = {}
    else:
        if production != production_lock:
            errors.append("requirements.lock does not match requirements.txt")
        dev_only = {
            name: version
            for name, version in development.items()
            if name not in production
        }
        if development_lock != dev_only:
            errors.append("requirements-dev.lock does not match development-only pins")
        dev_lock_text = files["requirements_dev_lock"].read_text(encoding="utf-8")
        if "-r requirements.lock" not in dev_lock_text.splitlines():
            errors.append("requirements-dev.lock must include requirements.lock")

    dockerfile = files["dockerfile"].read_text(encoding="utf-8")
    docker_lines = dockerfile.splitlines()
    if not docker_lines or _BASE_RE.fullmatch(docker_lines[0]) is None:
        errors.append("Docker base must pin Python 3.12 slim by digest as app-base")
    if "FROM app-base AS tools" not in dockerfile:
        errors.append("Dockerfile has no separate tools target")
    if "FROM app-base AS runtime" not in dockerfile:
        errors.append("Dockerfile has no separate runtime target")
    runtime = dockerfile.split("FROM app-base AS runtime", maxsplit=1)[-1]
    if any(token in runtime for token in ("apt-get", "tesseract", "poppler")):
        errors.append("production runtime target contains offline extraction packages")
    if "pip install --no-cache-dir --require-hashes -r requirements.lock" not in dockerfile:
        errors.append("Dockerfile does not install the hashed production lock")

    compose = files["compose"].read_text(encoding="utf-8")
    for target in ("target: runtime", "target: tools"):
        if target not in compose:
            errors.append(f"Compose is missing {target}")
    return errors, {
        "production_pin_count": len(production),
        "development_only_pin_count": len(development_lock),
        "file_sha256": {name: _sha256(path) for name, path in files.items()},
    }


def validate_installed_environment(profile: str) -> tuple[list[str], dict[str, object]]:
    errors: list[str] = []
    python_version_file = PROJECT_ROOT / ".python-version"
    expected_python = (
        python_version_file.read_text(encoding="utf-8").strip()
        if python_version_file.is_file()
        else "3.12"
    )
    actual_python = f"{sys.version_info.major}.{sys.version_info.minor}"
    if actual_python != expected_python:
        errors.append(f"Python expected={expected_python} actual={actual_python}")
    if platform.machine().lower() not in {"x86_64", "amd64"}:
        errors.append("hashed lock target requires Linux x86_64")

    production = requirement_pins(PROJECT_ROOT / "requirements.txt")
    expected = dict(production)
    if profile == "development":
        development = requirement_pins(PROJECT_ROOT / "requirements-dev.txt")
        expected.update(development)
        expected_prefix = (PROJECT_ROOT / ".venv").resolve()
        if sys.prefix == sys.base_prefix:
            errors.append("development verifier must run inside the project .venv")
        elif Path(sys.prefix).resolve() != expected_prefix:
            errors.append("development verifier is using a different virtual environment")
    else:
        if sys.prefix != sys.base_prefix:
            errors.append("container runtime must use the image boundary, not a nested venv")
        if os.geteuid() == 0:
            errors.append("production container must run as a non-root user")
        if any(shutil.which(command) for command in ("tesseract", "pdftotext", "pdfinfo")):
            errors.append("production container contains offline OCR/PDF executables")
        if not os.access(PROJECT_ROOT / "data" / "knowledge", os.R_OK):
            errors.append("production knowledge directory is not readable")
        if not os.access(PROJECT_ROOT / "data" / "runtime", os.W_OK):
            errors.append("production runtime directory is not writable")

    mismatches: list[str] = []
    for name, version in expected.items():
        try:
            installed = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            mismatches.append(f"{name}:missing")
        else:
            if installed != version:
                mismatches.append(f"{name}:expected={version}:actual={installed}")
    errors.extend(f"installed dependency mismatch: {item}" for item in mismatches)
    return errors, {
        "profile": profile,
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "machine": platform.machine(),
        "prefix_is_venv": sys.prefix != sys.base_prefix,
        "expected_dependency_count": len(expected),
        "dependency_mismatch_count": len(mismatches),
        "uid": os.geteuid(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        choices=("development", "production"),
        default="development",
    )
    parser.add_argument(
        "--environment-only",
        action="store_true",
        help="Skip host repository checks when verifying the built container image.",
    )
    args = parser.parse_args()
    if args.environment_only:
        repository_errors, repository = [], {"skipped": True}
    else:
        repository_errors, repository = validate_repository_contract()
    environment_errors, environment = validate_installed_environment(args.profile)
    errors = [*repository_errors, *environment_errors]
    print(
        json.dumps(
            {
                "verifier_version": VERIFIER_VERSION,
                "passed": not errors,
                "repository": repository,
                "environment": environment,
                "errors": errors,
                "hcx_calls": 0,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
