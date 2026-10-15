from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest

from app.errors import BackendUnavailable, InvalidBackendResponse
from app.main import create_app
from app.models import BackendOutput


class StubBackend:
    name = "stub"

    def __init__(self, output: BackendOutput | None = None, error: Exception | None = None):
        self.output = output or BackendOutput(
            answer="근거 기반 답변",
            retrieved_context=[{"source": "doc41", "text": "세액공제 안내"}, "두 번째 문서"],
            trace_summary={"summary": "검색 후 답변", "documents": 2},
        )
        self.error = error
        self.calls = 0

    async def answer(self, *, question_id: str, question: str) -> BackendOutput:
        self.calls += 1
        if self.error:
            raise self.error
        return self.output


class SlowBackend:
    name = "slow"

    async def answer(self, *, question_id: str, question: str) -> BackendOutput:
        await asyncio.sleep(0.2)
        return BackendOutput(answer="too late")


@asynccontextmanager
async def running_client(
    app: Any,
    *,
    raise_app_exceptions: bool = True,
) -> AsyncIterator[httpx.AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(
            app=app,
            raise_app_exceptions=raise_app_exceptions,
        )
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            yield client


@pytest.mark.anyio
async def test_answer_contract_preserves_korean_and_special_characters(
    base_settings: Any,
) -> None:
    backend = StubBackend()
    app = create_app(settings=base_settings, backend=backend)
    question = "연금저축 & IRP + 세액공제는? 100%\n가능한가요 🚀"
    async with running_client(app) as client:
        response = await client.get(
            "/answer",
            params={"question_id": "Q-001", "question": question},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    payload = response.json()
    assert set(payload) == {
        "question_id",
        "question",
        "retrieved_context",
        "think_trace",
        "answer",
    }
    assert all(isinstance(value, str) for value in payload.values())
    assert payload["question_id"] == "Q-001"
    assert payload["question"] == question
    assert "[DOC 1]" in payload["retrieved_context"]
    assert backend.calls == 1


@pytest.mark.anyio
async def test_identical_retry_uses_persistent_cache(base_settings: Any) -> None:
    first_backend = StubBackend()
    first_app = create_app(settings=base_settings, backend=first_backend)
    params = {"question_id": "Q-CACHE", "question": "동일 질의"}
    async with running_client(first_app) as client:
        first = await client.get("/answer", params=params)
        second = await client.get("/answer", params=params)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert first_backend.calls == 1

    restarted_backend = StubBackend(answer := BackendOutput(answer="달라지면 안 됨"))
    restarted_app = create_app(settings=base_settings, backend=restarted_backend)
    async with running_client(restarted_app) as client:
        after_restart = await client.get("/answer", params=params)
    assert after_restart.json() == first.json()
    assert restarted_backend.calls == 0
    assert answer.answer == "달라지면 안 됨"


@pytest.mark.anyio
async def test_same_id_with_different_question_does_not_collide(base_settings: Any) -> None:
    backend = StubBackend()
    app = create_app(settings=base_settings, backend=backend)
    async with running_client(app) as client:
        one = await client.get(
            "/answer", params={"question_id": "Q-1", "question": "첫 질문"}
        )
        two = await client.get(
            "/answer", params={"question_id": "Q-1", "question": "둘째 질문"}
        )
    assert one.status_code == two.status_code == 200
    assert backend.calls == 2


@pytest.mark.anyio
async def test_missing_parameters_keep_the_five_field_contract(
    base_settings: Any,
) -> None:
    """A malformed request still answers in the contract the evaluator parses.

    The evaluation notice retries only on timeout or 5xx, so a 422 ends the item
    permanently and returns ``{"detail": [...]}`` instead of the five strings.
    Nothing recovers a question that never arrived, so the service says so
    inside the contract instead of handing back an unparseable body.
    """

    backend = StubBackend()
    app = create_app(settings=base_settings, backend=backend)
    async with running_client(app) as client:
        missing = await client.get("/answer")
        assert missing.headers["cache-control"] == "no-store"
        no_question = await client.get("/answer", params={"question_id": "Q"})
        empty = await client.get(
            "/answer", params={"question_id": "Q", "question": ""}
        )

    for response in (missing, no_question, empty):
        assert response.status_code == 200
        payload = response.json()
        assert sorted(payload) == [
            "answer",
            "question",
            "question_id",
            "retrieved_context",
            "think_trace",
        ]
        assert all(isinstance(value, str) for value in payload.values())
        assert payload["answer"]
    assert no_question.json()["question_id"] == "Q"
    assert backend.calls == 0


@pytest.mark.anyio
async def test_long_questions_are_answered_not_rejected(base_settings: Any) -> None:
    """The organizer states no length limit, so a long question must be answered."""

    app = create_app(settings=base_settings, backend=StubBackend())
    async with running_client(app) as client:
        long_question = await client.get(
            "/answer", params={"question_id": "Q", "question": "a" * 8_001}
        )
        long_id = await client.get(
            "/answer", params={"question_id": "I" * 257, "question": "질문"}
        )
    assert long_question.status_code == 200
    assert long_id.status_code == 200


@pytest.mark.anyio
async def test_unbounded_public_inputs_never_reach_the_pipeline(
    base_settings: Any,
) -> None:
    """Bounds still exist so the endpoint cannot be driven with unbounded input.

    The bound is enforced where it costs something: retrieval and generation
    never run for an oversized input.  The reply itself stays in the evaluation
    contract, so an oversized request cannot produce an unparseable body.
    """

    backend = StubBackend()
    app = create_app(settings=base_settings, backend=backend)
    async with running_client(app) as client:
        long_id = await client.get(
            "/answer", params={"question_id": "I" * 4_097, "question": "질문"}
        )
        long_question = await client.get(
            "/answer", params={"question_id": "Q", "question": "a" * 64_001}
        )
    assert long_id.status_code == 200
    assert long_question.status_code == 200
    assert long_id.json()["question_id"] == "I" * 4_097
    assert long_id.json()["question"] == "질문"
    assert long_question.json()["question_id"] == "Q"
    assert long_question.json()["question"] == "a" * 64_001
    assert "1~64,000자" in long_question.json()["answer"]
    assert backend.calls == 0


@pytest.mark.anyio
async def test_expected_failures_are_sanitized(base_settings: Any) -> None:
    backend = StubBackend(error=BackendUnavailable("api_key=SHOULD_NOT_LEAK"))
    app = create_app(settings=base_settings, backend=backend)
    async with running_client(app) as client:
        response = await client.get(
            "/answer", params={"question_id": "Q", "question": "질문"}
        )
    assert response.status_code == 503
    assert "SHOULD_NOT_LEAK" not in response.text

    invalid_app = create_app(
        settings=base_settings,
        backend=StubBackend(error=InvalidBackendResponse("secret stack")),
    )
    async with running_client(invalid_app) as client:
        invalid = await client.get(
            "/answer", params={"question_id": "Q2", "question": "질문"}
        )
    assert invalid.status_code == 502
    assert "secret stack" not in invalid.text

    unexpected_app = create_app(
        settings=base_settings,
        backend=StubBackend(error=RuntimeError("api_key=UNEXPECTED_SECRET")),
    )
    async with running_client(
        unexpected_app,
        raise_app_exceptions=False,
    ) as client:
        unexpected = await client.get(
            "/answer", params={"question_id": "Q3", "question": "질문"}
        )
    assert unexpected.status_code == 500
    assert unexpected.json() == {"detail": "Internal server error"}
    assert "UNEXPECTED_SECRET" not in unexpected.text
    assert unexpected.headers["cache-control"] == "no-store"


@pytest.mark.anyio
async def test_internal_timeout_finishes_before_evaluator_budget(
    settings_factory: Any,
) -> None:
    settings = settings_factory(
        request_timeout_seconds=0.02,
        upstream_timeout_seconds=0.01,
    )
    app = create_app(settings=settings, backend=SlowBackend())
    async with running_client(app) as client:
        response = await client.get(
            "/answer", params={"question_id": "Q-TIME", "question": "느린 질문"}
        )
    assert response.status_code == 504
    assert response.headers["cache-control"] == "no-store"
    assert app.state.service.active_lock_count == 0


@pytest.mark.anyio
async def test_liveness_and_readiness_are_distinct(base_settings: Any) -> None:
    app = create_app(settings=base_settings, backend=StubBackend())
    async with running_client(app) as client:
        live = await client.get("/livez")
        ready = await client.get("/readyz")
        legacy = await client.get("/healthz")
    assert live.status_code == 200
    assert live.json() == {"status": "alive"}
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready"}
    assert legacy.json() == {"status": "ready"}
    assert ready.headers["cache-control"] == "no-store"


def test_openapi_declares_get_and_required_query_parameters(base_settings: Any) -> None:
    app = create_app(settings=base_settings, backend=StubBackend())
    schema = app.openapi()
    operation = schema["paths"]["/answer"]["get"]
    parameters = {item["name"]: item for item in operation["parameters"]}
    assert set(parameters) == {"question_id", "question"}
    # The answer depends on the question alone. The notice sends an id with
    # every call, but requiring it would turn an omitted id into a non-answer
    # for every item, so only the question is required.
    assert parameters["question"]["required"]
    assert not parameters["question_id"].get("required", False)


@pytest.mark.anyio
async def test_a_question_without_an_id_is_still_answered(base_settings: Any) -> None:
    """The answer depends on the question alone."""

    backend = StubBackend()
    app = create_app(settings=base_settings, backend=backend)
    async with running_client(app) as client:
        response = await client.get("/answer", params={"question": "IRP 세액공제 한도"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["question_id"] == ""
    assert payload["question"] == "IRP 세액공제 한도"
    assert payload["answer"]
    assert backend.calls == 1
