from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from dotenv import dotenv_values

from app import __version__
from app.answer_validation import ANSWER_VALIDATOR_VERSION
from app.calculations import CALCULATOR_VERSION
from app.evidence import EVIDENCE_ASSESSOR_VERSION
from app.evidence_rendering import PUBLIC_EVIDENCE_RENDERER_VERSION
from app.planning import PLANNER_VERSION, QUERY_ANCHOR_VERSION
from app.product_identity import PRODUCT_IDENTITY_RESOLVER_VERSION
from app.product_comparison import PRODUCT_COMPARATOR_VERSION
from app.product_values import PRODUCT_VALUE_PARSER_VERSION
from app.routing import ROUTER_VERSION
from app.semantic_coverage import SEMANTIC_COVERAGE_VERSION
from app.retrieval import RETRIEVER_VERSION
from app.suitability import SUITABILITY_VERSION


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VALID_APP_ENVS = frozenset({"development", "test", "production"})
VALID_HCX_API_STYLES = frozenset({"clova-v3", "openai-v3"})
HCX_PROVIDER_HOSTS = frozenset({"clovastudio.stream.ntruss.com"})
HCX_CLOVA_V3_PATH_PREFIX = "/v3/chat-completions/"
HCX_OPENAI_V3_PATH = "/v1/openai/chat/completions"
HCX_MODEL_PATTERN = re.compile(r"HCX-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*\Z")


def validate_production_hcx_target(
    api_url: str, model: str, *, api_style: str
) -> None:
    """Fail closed unless a direct production target is a NAVER HCX endpoint."""

    if HCX_MODEL_PATTERN.fullmatch(model) is None:
        raise ValueError("HCX_MODEL must be a valid HCX-* identifier in production")
    try:
        resolved_url = api_url.format(model=model) if "{model}" in api_url else api_url
        parsed = urlparse(resolved_url)
        hostname = parsed.hostname
        port = parsed.port
    except (KeyError, IndexError, ValueError) as exc:
        raise ValueError("HCX_API_URL must be a valid HTTPS URL in production") from exc

    if parsed.scheme.lower() != "https" or not hostname:
        raise ValueError("HCX_API_URL must be a valid HTTPS URL in production")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("HCX_API_URL must not contain credentials in production")
    if port is not None and port != 443:
        raise ValueError("HCX_API_URL must use standard HTTPS port 443 in production")
    if parsed.params or parsed.query or parsed.fragment:
        raise ValueError(
            "HCX_API_URL must not contain params, query, or fragment in production"
        )

    normalized_hostname = hostname.rstrip(".").lower()
    if normalized_hostname not in HCX_PROVIDER_HOSTS:
        raise ValueError(
            "HCX_API_URL hostname must be an approved CLOVA Studio host in production"
        )
    if api_style == "clova-v3":
        if parsed.path != f"{HCX_CLOVA_V3_PATH_PREFIX}{model}":
            raise ValueError(
                "HCX_API_URL must use /v3/chat-completions/{HCX_MODEL} "
                "with a matching model for clova-v3"
            )
    elif api_style == "openai-v3":
        if parsed.path != HCX_OPENAI_V3_PATH:
            raise ValueError(
                "HCX_API_URL must use /v1/openai/chat/completions for openai-v3"
            )
    else:
        raise ValueError("HCX_API_STYLE is not supported in production")


def _get_bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value")


def _get_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name)
    return default if raw is None else int(raw)


def _get_float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name)
    return default if raw is None else float(raw)


def _get_json_object(env: Mapping[str, str], name: str) -> dict[str, str]:
    raw = env.get(name, "").strip()
    if not raw:
        return {}
    parsed: Any = json.loads(raw)
    if not isinstance(parsed, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in parsed.items()
    ):
        raise ValueError(f"{name} must be a JSON object with string values")
    return parsed


@dataclass(frozen=True, slots=True)
class Settings:
    app_env: str = "development"
    agent_backend: str = "demo"
    request_timeout_seconds: float = 270.0
    upstream_timeout_seconds: float = 250.0
    cache_db_path: Path = PROJECT_ROOT / "data" / "runtime" / "answers.sqlite3"
    cache_namespace: str = "pension-agent-v1"
    cache_max_entries: int = 1_000
    knowledge_dir: Path = PROJECT_ROOT / "data" / "knowledge"
    product_evidence_path: Path | None = None
    source_governance_path: Path | None = None
    system_prompt_path: Path = PROJECT_ROOT / "agents" / "prompts" / "system.md"
    retrieval_top_k: int = 4
    max_context_chars: int = 7_500
    agent_upstream_url: str = ""
    agent_upstream_method: str = "POST"
    agent_upstream_bearer_token: str = ""
    agent_upstream_headers: dict[str, str] | None = None
    agent_upstream_hcx_only: bool = False
    agent_upstream_version: str = ""

    hcx_api_url: str = ""
    hcx_api_key: str = ""
    hcx_model: str = ""
    hcx_api_style: str = "clova-v3"
    hcx_api_key_header: str = "Authorization"
    hcx_api_key_prefix: str = "Bearer"
    hcx_request_id_header: str = "X-NCP-CLOVASTUDIO-REQUEST-ID"
    hcx_extra_headers: dict[str, str] | None = None
    hcx_max_tokens: int = 4_096
    hcx_temperature: float = 0.1
    hcx_thinking_effort: str = "none"

    @classmethod
    def from_env(cls) -> "Settings":
        # Load only this project's .env; deployment environment takes precedence.
        # Keep values local to Settings instead of mutating the process environment.
        file_values = {
            key: value
            for key, value in dotenv_values(PROJECT_ROOT / ".env", interpolate=False).items()
            if value is not None
        }
        return cls.from_mapping({**file_values, **os.environ})

    @classmethod
    def from_mapping(cls, env: Mapping[str, str]) -> "Settings":
        """Build settings from a supplied environment mapping."""

        return cls(
            app_env=env.get("APP_ENV", "development").strip().lower(),
            agent_backend=env.get("AGENT_BACKEND", "demo").strip().lower(),
            request_timeout_seconds=_get_float(env, "REQUEST_TIMEOUT_SECONDS", 270.0),
            upstream_timeout_seconds=_get_float(env, "UPSTREAM_TIMEOUT_SECONDS", 250.0),
            cache_db_path=Path(
                env.get(
                    "CACHE_DB_PATH",
                    str(PROJECT_ROOT / "data" / "runtime" / "answers.sqlite3"),
                )
            ),
            cache_namespace=env.get("CACHE_NAMESPACE", "pension-agent-v1").strip(),
            cache_max_entries=_get_int(env, "CACHE_MAX_ENTRIES", 1_000),
            knowledge_dir=Path(
                env.get("KNOWLEDGE_DIR", str(PROJECT_ROOT / "data" / "knowledge"))
            ),
            product_evidence_path=(
                Path(env["PRODUCT_EVIDENCE_PATH"])
                if env.get("PRODUCT_EVIDENCE_PATH", "").strip()
                else PROJECT_ROOT / "data" / "structured" / "product_evidence.json"
            ),
            source_governance_path=(
                Path(env["SOURCE_GOVERNANCE_PATH"])
                if env.get("SOURCE_GOVERNANCE_PATH", "").strip()
                else PROJECT_ROOT
                / "data"
                / "structured"
                / "general_source_governance.json"
            ),
            system_prompt_path=Path(
                env.get(
                    "SYSTEM_PROMPT_PATH",
                    str(PROJECT_ROOT / "agents" / "prompts" / "system.md"),
                )
            ),
            retrieval_top_k=_get_int(env, "RETRIEVAL_TOP_K", 4),
            max_context_chars=_get_int(env, "MAX_CONTEXT_CHARS", 7_500),
            agent_upstream_url=env.get("AGENT_UPSTREAM_URL", "").strip(),
            agent_upstream_method=env.get("AGENT_UPSTREAM_METHOD", "POST")
            .strip()
            .upper(),
            agent_upstream_bearer_token=env.get(
                "AGENT_UPSTREAM_BEARER_TOKEN", ""
            ).strip(),
            agent_upstream_headers=_get_json_object(env, "AGENT_UPSTREAM_HEADERS_JSON"),
            agent_upstream_hcx_only=_get_bool(env, "AGENT_UPSTREAM_HCX_ONLY", False),
            agent_upstream_version=env.get("AGENT_UPSTREAM_VERSION", "").strip(),
            hcx_api_url=env.get("HCX_API_URL", "").strip(),
            hcx_api_key=env.get("HCX_API_KEY", "").strip(),
            hcx_model=env.get("HCX_MODEL", "").strip(),
            hcx_api_style=env.get("HCX_API_STYLE", "clova-v3").strip().lower(),
            hcx_api_key_header=env.get(
                "HCX_API_KEY_HEADER", "Authorization"
            ).strip(),
            hcx_api_key_prefix=env.get("HCX_API_KEY_PREFIX", "Bearer").strip(),
            hcx_request_id_header=env.get(
                "HCX_REQUEST_ID_HEADER", "X-NCP-CLOVASTUDIO-REQUEST-ID"
            ).strip(),
            hcx_extra_headers=_get_json_object(env, "HCX_EXTRA_HEADERS_JSON"),
            hcx_max_tokens=_get_int(env, "HCX_MAX_TOKENS", 4_096),
            hcx_temperature=_get_float(env, "HCX_TEMPERATURE", 0.1),
            hcx_thinking_effort=env.get("HCX_THINKING_EFFORT", "none")
            .strip()
            .lower(),
        )

    def validate(self) -> None:
        if self.app_env not in VALID_APP_ENVS:
            raise ValueError(
                "APP_ENV must be one of: development, test, production"
            )
        if self.agent_backend not in {"demo", "http", "hcx"}:
            raise ValueError("AGENT_BACKEND must be one of: demo, http, hcx")
        if not 0 < self.request_timeout_seconds < 300:
            raise ValueError("REQUEST_TIMEOUT_SECONDS must be greater than 0 and below 300")
        if not 0 < self.upstream_timeout_seconds < 300:
            raise ValueError("UPSTREAM_TIMEOUT_SECONDS must be greater than 0 and below 300")
        if self.upstream_timeout_seconds > self.request_timeout_seconds:
            raise ValueError(
                "UPSTREAM_TIMEOUT_SECONDS must not exceed REQUEST_TIMEOUT_SECONDS"
            )
        if self.retrieval_top_k < 0:
            raise ValueError("RETRIEVAL_TOP_K must be zero or greater")
        if self.max_context_chars < 0:
            raise ValueError("MAX_CONTEXT_CHARS must be zero or greater")
        if not self.cache_namespace:
            raise ValueError("CACHE_NAMESPACE must not be empty")
        if not 1 <= self.cache_max_entries <= 1_000_000:
            raise ValueError("CACHE_MAX_ENTRIES must be between 1 and 1000000")
        if self.app_env == "production" and self.agent_backend == "demo":
            raise ValueError(
                "The demo backend is disabled in production. Configure http or hcx."
            )
        if self.agent_backend == "http":
            if not self.agent_upstream_url:
                raise ValueError("AGENT_UPSTREAM_URL is required for the http backend")
            if self.agent_upstream_method not in {"GET", "POST"}:
                raise ValueError("AGENT_UPSTREAM_METHOD must be GET or POST")
            if self.app_env == "production":
                if self.agent_upstream_method != "POST":
                    raise ValueError(
                        "Production http backend requires AGENT_UPSTREAM_METHOD=POST"
                    )
                if not self.agent_upstream_hcx_only:
                    raise ValueError(
                        "Production http backend requires "
                        "AGENT_UPSTREAM_HCX_ONLY=true"
                    )
                if not self.agent_upstream_version:
                    raise ValueError("Production http backend requires AGENT_UPSTREAM_VERSION")
        if self.agent_backend == "hcx":
            if not self.hcx_api_url or not self.hcx_api_key:
                raise ValueError("HCX_API_URL and HCX_API_KEY are required for hcx")
            if "{model}" in self.hcx_api_url and not self.hcx_model:
                raise ValueError("HCX_MODEL is required when HCX_API_URL contains {model}")
            if self.hcx_api_style not in VALID_HCX_API_STYLES:
                raise ValueError(
                    "HCX_API_STYLE must be one of: clova-v3, openai-v3"
                )
            if self.app_env == "production":
                if not self.hcx_model:
                    raise ValueError("HCX_MODEL is required in production")
                validate_production_hcx_target(
                    self.hcx_api_url,
                    self.hcx_model,
                    api_style=self.hcx_api_style,
                )
            if not self.hcx_api_key_header:
                raise ValueError("HCX_API_KEY_HEADER must not be empty")
            if not 1 <= self.hcx_max_tokens <= 32_768:
                raise ValueError("HCX_MAX_TOKENS must be between 1 and 32768")
            # HCX-003 has an 8,192-token total window; a conservative character
            # budget keeps Korean context plus prompt/output below that family.
            # Teams using a larger-window model may raise both values after
            # verifying the exact console limit.
            if self.max_context_chars + self.hcx_max_tokens * 2 > 18_000:
                raise ValueError(
                    "MAX_CONTEXT_CHARS and HCX_MAX_TOKENS exceed the conservative context budget"
                )
            if not 0 <= self.hcx_temperature <= 1:
                raise ValueError("HCX_TEMPERATURE must be between 0 and 1")
            if self.hcx_thinking_effort not in {"none", "low", "medium", "high"}:
                raise ValueError(
                    "HCX_THINKING_EFFORT must be one of: none, low, medium, high"
                )
            if self.hcx_model.upper() == "HCX-005":
                if self.hcx_max_tokens > 4_096:
                    raise ValueError("HCX-005 requires HCX_MAX_TOKENS at or below 4096")
                if self.hcx_thinking_effort != "none":
                    raise ValueError("HCX-005 requires HCX_THINKING_EFFORT=none")

    def response_identity(self, *, backend_name: str) -> str:
        """Return a non-secret identity for completed-response cache isolation."""

        def file_sha256(path: Path) -> str:
            if not path.is_file():
                return "missing"
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            return digest.hexdigest()

        identity: dict[str, Any] = {
            "application_version": __version__,
            "cache_namespace": self.cache_namespace,
            "backend": backend_name,
            "system_prompt": file_sha256(self.system_prompt_path),
            "knowledge_manifest": file_sha256(self.knowledge_dir / "manifest.json"),
            "product_evidence": (
                file_sha256(self.product_evidence_path)
                if self.product_evidence_path is not None
                else "disabled"
            ),
            "source_governance": (
                file_sha256(self.source_governance_path)
                if self.source_governance_path is not None
                else "disabled"
            ),
            "retrieval_top_k": self.retrieval_top_k,
            "max_context_chars": self.max_context_chars,
            "planner": PLANNER_VERSION,
            "query_anchor": QUERY_ANCHOR_VERSION,
            "router": ROUTER_VERSION,
            "retriever": RETRIEVER_VERSION,
            "evidence_assessor": EVIDENCE_ASSESSOR_VERSION,
            "product_identity_resolver": PRODUCT_IDENTITY_RESOLVER_VERSION,
            "product_comparator": PRODUCT_COMPARATOR_VERSION,
            "suitability": SUITABILITY_VERSION,
            "product_value_parser": PRODUCT_VALUE_PARSER_VERSION,
            "answer_validator": ANSWER_VALIDATOR_VERSION,
            "semantic_coverage": SEMANTIC_COVERAGE_VERSION,
            "public_evidence_renderer": PUBLIC_EVIDENCE_RENDERER_VERSION,
        }
        if backend_name == "hcx":
            identity["deterministic_calculation"] = CALCULATOR_VERSION
            identity["hcx"] = {
                "api_url": self.hcx_api_url,
                "model": self.hcx_model,
                "style": self.hcx_api_style,
                "max_tokens": self.hcx_max_tokens,
                "temperature": self.hcx_temperature,
                "thinking_effort": self.hcx_thinking_effort,
            }
        elif backend_name == "http":
            identity["http"] = {
                "api_url": self.agent_upstream_url,
                "method": self.agent_upstream_method,
                "version": self.agent_upstream_version,
            }

        serialized = json.dumps(
            identity,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(serialized).hexdigest()
