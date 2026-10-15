from __future__ import annotations

import re
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _requirement_lines(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_python_runtime_is_locked_to_312() -> None:
    assert (PROJECT_ROOT / ".python-version").read_text(encoding="utf-8").strip() == "3.12"
    assert sys.version_info[:2] == (3, 12)
    first_line = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines()[0]
    assert re.fullmatch(
        r"FROM python:3\.12-slim@sha256:[0-9a-f]{64} AS app-base",
        first_line,
    )


def test_production_dependency_graph_is_exactly_pinned() -> None:
    requirements = _requirement_lines(PROJECT_ROOT / "requirements.txt")
    assert requirements
    assert all("==" in line and not line.startswith(("-r", "-e")) for line in requirements)


def test_development_requirements_reuse_the_production_graph() -> None:
    requirements = _requirement_lines(PROJECT_ROOT / "requirements-dev.txt")
    assert requirements[0] == "-r requirements.txt"
    assert all("==" in line for line in requirements[1:])


def test_hashed_locks_cover_every_pinned_requirement() -> None:
    from scripts.verify_runtime_environment import lock_pins, requirement_pins

    production = requirement_pins(PROJECT_ROOT / "requirements.txt")
    development = requirement_pins(PROJECT_ROOT / "requirements-dev.txt")
    assert lock_pins(PROJECT_ROOT / "requirements.lock") == production
    assert lock_pins(PROJECT_ROOT / "requirements-dev.lock") == development


def test_runtime_and_offline_tools_use_separate_docker_targets() -> None:
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "FROM app-base AS tools" in dockerfile
    assert "FROM app-base AS runtime" in dockerfile
    runtime = dockerfile.split("FROM app-base AS runtime", maxsplit=1)[1]
    assert all(term not in runtime for term in ("apt-get", "tesseract", "poppler"))
    assert "target: runtime" in compose
    assert compose.count("target: tools") == 2
