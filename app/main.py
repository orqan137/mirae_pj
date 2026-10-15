from __future__ import annotations

import hashlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app import __version__
from app.backends.factory import build_backend
from app.cache import ResponseCache
from app.config import Settings
from app.errors import AgentTimedOut, BackendUnavailable, InvalidBackendResponse
from app.models import EvaluationResponse
from app.service import AgentBackend, AnswerService


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)
NO_STORE_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
}


def _request_label(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _error_response(status_code: int, detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"detail": detail},
        headers=NO_STORE_HEADERS,
    )


def _rejected_fields(error: RequestValidationError) -> str:
    """Name the query fields that failed validation, without echoing their values."""

    names = []
    for item in error.errors():
        location = [str(part) for part in item.get("loc", ()) if part != "query"]
        name = ".".join(location) or "request"
        if name not in names:
            names.append(name)
    return ", ".join(names) if names else "request"


def _contract_response(request: Request, error: RequestValidationError) -> JSONResponse:
    """Answer a malformed request in the five-field evaluation contract.

    The evaluation notice retries only on timeout or 5xx, so a 422 would end the
    item permanently and hand the caller a body shaped ``{"detail": [...]}``
    instead of the five strings it parses.  Neither a retry nor a richer error
    would recover a request whose question never arrived, so the service states
    what is missing inside the contract and lets the caller record a parseable
    response.
    """

    params = request.query_params
    payload = EvaluationResponse(
        question_id=params.get("question_id", ""),
        question=params.get("question", ""),
        retrieved_context="",
        think_trace=(
            "평가 API 규격을 만족하지 않는 요청이라 질의 처리를 시작하지 못했습니다. "
            f"규격에 맞지 않는 항목: {_rejected_fields(error)}. "
            "검색과 생성은 수행하지 않았습니다."
        ),
        answer=(
            "요청 형식이 맞지 않아 답변을 생성하지 못했습니다. "
            "question_id는 1~4,096자, question은 1~64,000자로 입력해 주세요."
        ),
    )
    return JSONResponse(
        status_code=200,
        content=payload.model_dump(),
        headers=NO_STORE_HEADERS,
    )


def create_app(
    *,
    settings: Settings | None = None,
    backend: AgentBackend | None = None,
) -> FastAPI:
    resolved_settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        resolved_settings.validate()
        cache = ResponseCache(
            resolved_settings.cache_db_path,
            max_entries=resolved_settings.cache_max_entries,
        )
        cache.initialize()
        resolved_backend = backend or build_backend(resolved_settings)
        cache_namespace = resolved_settings.response_identity(
            backend_name=resolved_backend.name
        )
        app.state.settings = resolved_settings
        app.state.service = AnswerService(
            backend=resolved_backend,
            cache=cache,
            timeout_seconds=resolved_settings.request_timeout_seconds,
            cache_namespace=cache_namespace,
        )
        app.state.ready = True
        logger.info(
            "service started env=%s backend=%s",
            resolved_settings.app_env,
            resolved_backend.name,
        )
        try:
            yield
        finally:
            app.state.ready = False

    app = FastAPI(
        title="Mirae Asset Pension Agent Evaluation API",
        version=__version__,
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def prevent_storage(request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        response.headers.update(NO_STORE_HEADERS)
        return response

    @app.exception_handler(RequestValidationError)
    async def handle_validation(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        logger.warning("request rejected by contract validation")
        return _contract_response(request, error)

    @app.exception_handler(AgentTimedOut)
    async def handle_timeout(_: Request, __: AgentTimedOut) -> JSONResponse:
        return _error_response(
            504, "Agent did not finish within the internal time budget"
        )

    @app.exception_handler(BackendUnavailable)
    async def handle_backend(_: Request, __: BackendUnavailable) -> JSONResponse:
        return _error_response(503, "Agent backend is temporarily unavailable")

    @app.exception_handler(InvalidBackendResponse)
    async def handle_invalid_backend(
        _: Request, __: InvalidBackendResponse
    ) -> JSONResponse:
        return _error_response(502, "Agent backend returned an invalid response")

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, error: Exception) -> JSONResponse:
        question_id = request.query_params.get("question_id", "")
        logger.error(
            "unhandled request failure type=%s request=%s",
            type(error).__name__,
            _request_label(question_id) if question_id else "none",
        )
        return _error_response(500, "Internal server error")

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {"service": "pension-agent", "status": "ok"}

    @app.get("/livez", include_in_schema=False)
    async def liveness() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/readyz", include_in_schema=False, response_model=None)
    async def readiness(request: Request) -> dict[str, str] | JSONResponse:
        if not getattr(request.app.state, "ready", False):
            return JSONResponse(status_code=503, content={"status": "not_ready"})
        service: AnswerService = request.app.state.service
        try:
            ready = await service.is_ready()
        except Exception:
            ready = False
        if not ready:
            return JSONResponse(status_code=503, content={"status": "not_ready"})
        return {"status": "ready"}

    @app.get("/healthz", include_in_schema=False, response_model=None)
    @app.get("/health", include_in_schema=False, response_model=None)
    async def health(request: Request) -> dict[str, str] | JSONResponse:
        return await readiness(request)

    @app.get(
        "/answer",
        response_model=EvaluationResponse,
        response_model_exclude_none=False,
    )
    async def answer(
        request: Request,
        # Accept inputs well beyond the former 8,000/256 character limits used
        # during development.  These defensive bounds prevent an unbounded GET
        # query from consuming memory; the service does not silently truncate
        # either value, so an input beyond them is rejected explicitly.
        # The notice sends both, but the answer depends on the question alone.
        # Requiring the id would have turned every item into a non-answer had
        # it ever been omitted, so a missing id is echoed back empty instead.
        question_id: str = Query("", max_length=4_096),
        question: str = Query(..., min_length=1, max_length=64_000),
    ) -> EvaluationResponse:
        logger.info(
            "evaluation request received request=%s", _request_label(question_id)
        )
        service: AnswerService = request.app.state.service
        return await service.answer(question_id=question_id, question=question)

    return app


app = create_app()
