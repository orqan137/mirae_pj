from __future__ import annotations

import json
from typing import Any

import httpx

from app.config import Settings
from app.errors import BackendUnavailable, InvalidBackendResponse
from app.models import BackendOutput


ANSWER_ALIASES = ("answer", "final_answer", "output", "text", "response")
CONTEXT_ALIASES = (
    "retrieved_context",
    "context",
    "contexts",
    "documents",
    "sources",
)
TRACE_ALIASES = ("trace_summary", "tool_trace")


def _unwrap(payload: Any) -> Any:
    current = payload
    for _ in range(2):
        if not isinstance(current, dict):
            break
        nested = next(
            (
                current[key]
                for key in ("data", "result")
                if isinstance(current.get(key), dict)
            ),
            None,
        )
        if nested is None:
            break
        current = nested
    return current


def _first(payload: dict[str, Any], aliases: tuple[str, ...]) -> Any:
    for alias in aliases:
        if alias in payload:
            return payload[alias]
    return None


class HttpAgentBackend:
    name = "http"

    def __init__(self, settings: Settings, *, client: httpx.AsyncClient | None = None):
        self._settings = settings
        self._client = client

    async def answer(self, *, question_id: str, question: str) -> BackendOutput:
        headers = dict(self._settings.agent_upstream_headers or {})
        if self._settings.agent_upstream_bearer_token:
            headers["Authorization"] = (
                f"Bearer {self._settings.agent_upstream_bearer_token}"
            )
        timeout = httpx.Timeout(
            self._settings.upstream_timeout_seconds,
            connect=min(10.0, self._settings.upstream_timeout_seconds),
        )
        own_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=timeout)
        try:
            if self._settings.agent_upstream_method == "GET":
                response = await client.get(
                    self._settings.agent_upstream_url,
                    params={"question_id": question_id, "question": question},
                    headers=headers,
                    timeout=timeout,
                )
            else:
                response = await client.post(
                    self._settings.agent_upstream_url,
                    json={"question_id": question_id, "question": question},
                    headers=headers,
                    timeout=timeout,
                )
            response.raise_for_status()
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise BackendUnavailable("Upstream Agent is unreachable or timed out") from exc
        except httpx.HTTPStatusError as exc:
            raise BackendUnavailable(
                f"Upstream Agent returned HTTP {exc.response.status_code}"
            ) from exc
        finally:
            if own_client:
                await client.aclose()

        content_type = response.headers.get("content-type", "")
        if "json" not in content_type.lower():
            text = response.text.strip()
            if not text:
                raise InvalidBackendResponse("Upstream Agent returned an empty response")
            return BackendOutput(answer=text)

        try:
            payload = _unwrap(response.json())
        except (json.JSONDecodeError, ValueError) as exc:
            raise InvalidBackendResponse("Upstream Agent returned invalid JSON") from exc
        if isinstance(payload, str):
            return BackendOutput(answer=payload)
        if not isinstance(payload, dict):
            raise InvalidBackendResponse("Upstream Agent JSON must contain an object")

        answer = _first(payload, ANSWER_ALIASES)
        if answer is None:
            raise InvalidBackendResponse("Upstream Agent response has no answer field")
        return BackendOutput(
            answer=answer,
            retrieved_context=_first(payload, CONTEXT_ALIASES),
            # Raw reasoning/think_trace fields are deliberately ignored.
            trace_summary=_first(payload, TRACE_ALIASES),
        )
