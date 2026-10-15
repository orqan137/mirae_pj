from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest


def test_from_env_loads_project_dotenv_without_exporting_secrets(tmp_path, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.delenv("HCX_API_KEY", raising=False)
    monkeypatch.delenv("AGENT_UPSTREAM_BEARER_TOKEN", raising=False)
    (tmp_path / ".env").write_text(
        "HCX_API_KEY=local-test-value\nAGENT_UPSTREAM_BEARER_TOKEN='literal-${TOKEN}'\n"
    )
    settings = config.Settings.from_env()
    assert settings.hcx_api_key == "local-test-value"
    assert settings.agent_upstream_bearer_token == "literal-${TOKEN}"
    assert "HCX_API_KEY" not in config.os.environ


def test_from_env_deployment_environment_overrides_dotenv(tmp_path, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    (tmp_path / ".env").write_text("HCX_API_KEY=local-test-value\n")
    monkeypatch.setenv("HCX_API_KEY", "deployment-test-value")
    assert config.Settings.from_env().hcx_api_key == "deployment-test-value"
    monkeypatch.setenv("HCX_API_KEY", "")
    assert config.Settings.from_env().hcx_api_key == ""


def test_from_env_works_without_dotenv(tmp_path, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.delenv("HCX_API_KEY", raising=False)
    assert config.Settings.from_env().hcx_api_key == ""


def test_production_rejects_demo_backend(settings_factory: Any) -> None:
    settings = settings_factory(app_env="production", agent_backend="demo")
    with pytest.raises(ValueError, match="demo backend"):
        settings.validate()


def test_app_env_is_closed(settings_factory: Any) -> None:
    settings = settings_factory(app_env="prod", agent_backend="demo")
    with pytest.raises(ValueError, match="APP_ENV"):
        settings.validate()


def test_hcx_requires_endpoint_and_key(settings_factory: Any) -> None:
    settings = settings_factory(app_env="production", agent_backend="hcx")
    with pytest.raises(ValueError, match="HCX_API_URL"):
        settings.validate()


def test_timeout_must_leave_evaluator_headroom(settings_factory: Any) -> None:
    settings = settings_factory(request_timeout_seconds=300)
    with pytest.raises(ValueError, match="below 300"):
        settings.validate()


def test_hcx_context_budget_is_conservative(settings_factory: Any) -> None:
    settings = settings_factory(
        agent_backend="hcx",
        hcx_api_url="https://clova.example/v3/chat-completions/HCX-007",
        hcx_api_key="secret",
        hcx_model="HCX-007",
        max_context_chars=16_000,
        hcx_max_tokens=5_120,
    )
    with pytest.raises(ValueError, match="context budget"):
        settings.validate()


def test_production_http_requires_hcx_attestation_and_version(
    settings_factory: Any,
) -> None:
    settings = settings_factory(
        app_env="production",
        agent_backend="http",
        agent_upstream_url="http://agent.internal/answer",
    )
    with pytest.raises(ValueError, match="HCX_ONLY"):
        settings.validate()

    settings = settings_factory(
        app_env="production",
        agent_backend="http",
        agent_upstream_url="http://agent.internal/answer",
        agent_upstream_hcx_only=True,
    )
    with pytest.raises(ValueError, match="UPSTREAM_VERSION"):
        settings.validate()

    settings = settings_factory(
        app_env="production",
        agent_backend="http",
        agent_upstream_url="http://agent.internal/answer",
        agent_upstream_hcx_only=True,
        agent_upstream_version="submission-v1",
    )
    settings.validate()

    settings = settings_factory(
        app_env="production",
        agent_backend="http",
        agent_upstream_url="http://agent.internal/answer",
        agent_upstream_method="GET",
        agent_upstream_hcx_only=True,
        agent_upstream_version="submission-v1",
    )
    with pytest.raises(ValueError, match="METHOD=POST"):
        settings.validate()


def test_production_hcx_requires_auditable_v3_model(settings_factory: Any) -> None:
    settings = settings_factory(
        app_env="production",
        agent_backend="hcx",
        hcx_api_url="https://clova.example/v3/chat-completions/HCX-007",
        hcx_api_key="secret",
        hcx_api_style="clova-v3",
    )
    with pytest.raises(ValueError, match="HCX_MODEL"):
        settings.validate()


def test_production_hcx_accepts_official_provider_target(settings_factory: Any) -> None:
    settings_factory(
        app_env="production",
        agent_backend="hcx",
        hcx_api_url=(
            "https://clovastudio.stream.ntruss.com/"
            "v3/chat-completions/HCX-007"
        ),
        hcx_api_key="secret",
        hcx_model="HCX-007",
    ).validate()


def test_production_clova_v3_accepts_model_placeholder(settings_factory: Any) -> None:
    settings_factory(
        app_env="production",
        agent_backend="hcx",
        hcx_api_url=(
            "https://clovastudio.stream.ntruss.com/"
            "v3/chat-completions/{model}"
        ),
        hcx_api_key="secret",
        hcx_model="HCX-007",
        hcx_api_style="clova-v3",
    ).validate()


def test_production_clova_v3_rejects_path_model_mismatch(
    settings_factory: Any,
) -> None:
    settings = settings_factory(
        app_env="production",
        agent_backend="hcx",
        hcx_api_url=(
            "https://clovastudio.stream.ntruss.com/"
            "v3/chat-completions/HCX-005"
        ),
        hcx_api_key="secret",
        hcx_model="HCX-007",
        hcx_api_style="clova-v3",
    )

    with pytest.raises(ValueError, match="matching model"):
        settings.validate()


def test_production_openai_v3_accepts_generic_chat_completions_url(
    settings_factory: Any,
) -> None:
    settings_factory(
        app_env="production",
        agent_backend="hcx",
        hcx_api_url=(
            "https://clovastudio.stream.ntruss.com/"
            "v1/openai/chat/completions"
        ),
        hcx_api_key="secret",
        hcx_model="HCX-007",
        hcx_api_style="openai-v3",
    ).validate()


@pytest.mark.parametrize(
    "api_url",
    (
        "http://clovastudio.stream.ntruss.com/v3/chat-completions/HCX-007",
        "https://ntruss.com.example.org/v3/chat-completions/HCX-007",
        "https://evilntruss.com/v3/chat-completions/HCX-007",
        "https://example.org/v3/chat-completions/HCX-007",
        "https://user:secret@clovastudio.stream.ntruss.com/v3/chat-completions/HCX-007",
        "https://clovastudio.stream.ntruss.com:444/v3/chat-completions/HCX-007",
        "https://clovastudio.stream.ntruss.com/v3/chat-completions/HCX-007#ignored",
    ),
)
def test_production_hcx_rejects_non_provider_targets(
    settings_factory: Any, api_url: str
) -> None:
    settings = settings_factory(
        app_env="production",
        agent_backend="hcx",
        hcx_api_url=api_url,
        hcx_api_key="secret",
        hcx_model="HCX-007",
    )

    with pytest.raises(
        ValueError,
        match=(
            "HTTPS URL|CLOVA Studio host|credentials|port 443|"
            "params, query, or fragment"
        ),
    ):
        settings.validate()


@pytest.mark.parametrize(
    ("api_style", "path"),
    (
        ("clova-v3", "/v3/chat-completions/HCX-007"),
        ("openai-v3", "/v1/openai/chat/completions"),
    ),
)
def test_production_hcx_rejects_other_ntruss_services(
    settings_factory: Any, api_style: str, path: str
) -> None:
    settings = settings_factory(
        app_env="production",
        agent_backend="hcx",
        hcx_api_url=f"https://objectstorage.ntruss.com{path}",
        hcx_api_key="secret",
        hcx_model="HCX-007",
        hcx_api_style=api_style,
    )

    with pytest.raises(ValueError, match="approved CLOVA Studio host"):
        settings.validate()


@pytest.mark.parametrize(
    ("api_style", "path", "expected"),
    (
        ("clova-v3", "/v1/openai/chat/completions", "v3/chat-completions"),
        ("openai-v3", "/v3/chat-completions/HCX-007", "v1/openai"),
    ),
)
def test_production_hcx_rejects_wrong_style_path(
    settings_factory: Any, api_style: str, path: str, expected: str
) -> None:
    settings = settings_factory(
        app_env="production",
        agent_backend="hcx",
        hcx_api_url=f"https://clovastudio.stream.ntruss.com{path}",
        hcx_api_key="secret",
        hcx_model="HCX-007",
        hcx_api_style=api_style,
    )

    with pytest.raises(ValueError, match=expected):
        settings.validate()


def test_production_hcx_requires_hcx_model_prefix(settings_factory: Any) -> None:
    settings = settings_factory(
        app_env="production",
        agent_backend="hcx",
        hcx_api_url="https://clovastudio.stream.ntruss.com/v3/chat-completions/model",
        hcx_api_key="secret",
        hcx_model="other-model",
    )

    with pytest.raises(ValueError, match="valid HCX-"):
        settings.validate()


@pytest.mark.parametrize("model", ("HCX-", "HCX-../../models", "HCX-005?other"))
def test_production_hcx_rejects_malformed_hcx_model_identifier(
    settings_factory: Any, model: str
) -> None:
    settings = settings_factory(
        app_env="production",
        agent_backend="hcx",
        hcx_api_url="https://clovastudio.stream.ntruss.com/v3/chat-completions/{model}",
        hcx_api_key="secret",
        hcx_model=model,
    )

    with pytest.raises(ValueError, match="valid HCX-"):
        settings.validate()


def test_hcx_005_capabilities_are_enforced(settings_factory: Any) -> None:
    with pytest.raises(ValueError, match="at or below 4096"):
        settings_factory(
            agent_backend="hcx",
            hcx_api_url="https://clova.example/v3/chat-completions/HCX-005",
            hcx_api_key="secret",
            hcx_model="HCX-005",
            max_context_chars=7_500,
            hcx_max_tokens=5_120,
        ).validate()
    with pytest.raises(ValueError, match="THINKING_EFFORT=none"):
        settings_factory(
            agent_backend="hcx",
            hcx_api_url="https://clova.example/v3/chat-completions/HCX-005",
            hcx_api_key="secret",
            hcx_model="HCX-005",
            hcx_thinking_effort="low",
        ).validate()


def test_response_identity_tracks_prompt_corpus_and_model(
    tmp_path: Path,
    settings_factory: Any,
) -> None:
    prompt = tmp_path / "system.md"
    prompt.write_text("prompt-v1", encoding="utf-8")
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    (knowledge / "manifest.json").write_text("{}", encoding="utf-8")
    settings = settings_factory(
        agent_backend="hcx",
        system_prompt_path=prompt,
        knowledge_dir=knowledge,
        hcx_api_url="https://clova.example/v3/chat-completions/HCX-007",
        hcx_api_key="secret",
        hcx_model="HCX-007",
    )
    first = settings.response_identity(backend_name="hcx")
    prompt.write_text("prompt-v2", encoding="utf-8")
    assert settings.response_identity(backend_name="hcx") != first

    other_model = settings_factory(
        agent_backend="hcx",
        system_prompt_path=prompt,
        knowledge_dir=knowledge,
        hcx_api_url="https://clova.example/v3/chat-completions/{model}",
        hcx_api_key="secret",
        hcx_model="HCX-OTHER",
    )
    assert other_model.response_identity(backend_name="hcx") != first


def test_response_identity_tracks_retriever_version(
    tmp_path: Path,
    settings_factory: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.config as config_module

    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    (knowledge / "manifest.json").write_text("{}", encoding="utf-8")
    settings = settings_factory(knowledge_dir=knowledge)
    first = settings.response_identity(backend_name="demo")

    monkeypatch.setattr(config_module, "RETRIEVER_VERSION", "pension-local-retriever-next")

    assert settings.response_identity(backend_name="demo") != first


def test_response_identity_tracks_semantic_coverage_version(
    tmp_path: Path,
    settings_factory: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.config as config_module

    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    (knowledge / "manifest.json").write_text("{}", encoding="utf-8")
    settings = settings_factory(knowledge_dir=knowledge)
    first = settings.response_identity(backend_name="demo")

    monkeypatch.setattr(
        config_module,
        "SEMANTIC_COVERAGE_VERSION",
        "pension-semantic-coverage-next",
    )

    assert settings.response_identity(backend_name="demo") != first
