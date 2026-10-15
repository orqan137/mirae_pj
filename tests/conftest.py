from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from app.config import PROJECT_ROOT, Settings


@pytest.fixture
def base_settings(tmp_path: Path) -> Settings:
    return Settings(
        app_env="test",
        agent_backend="demo",
        request_timeout_seconds=1.0,
        upstream_timeout_seconds=0.8,
        cache_db_path=tmp_path / "runtime" / "answers.sqlite3",
        knowledge_dir=tmp_path / "knowledge",
        system_prompt_path=PROJECT_ROOT / "agents" / "prompts" / "system.md",
    )


@pytest.fixture
def settings_factory(base_settings: Settings):
    def factory(**changes: object) -> Settings:
        return replace(base_settings, **changes)

    return factory


@pytest.fixture
def anyio_backend() -> tuple[str, dict[str, bool]]:
    return "asyncio", {"use_uvloop": True}
