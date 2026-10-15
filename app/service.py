from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Protocol

from app.cache import ResponseCache, request_cache_key
from app.errors import AgentTimedOut
from app.models import BackendOutput, EvaluationResponse
from app.normalization import normalize_response


logger = logging.getLogger(__name__)


class AgentBackend(Protocol):
    name: str

    def answer(self, *, question_id: str, question: str) -> Awaitable[BackendOutput]: ...


@dataclass(slots=True)
class _LockEntry:
    lock: asyncio.Lock
    users: int = 0


class AnswerService:
    def __init__(
        self,
        *,
        backend: AgentBackend,
        cache: ResponseCache,
        timeout_seconds: float,
        cache_namespace: str,
    ) -> None:
        self._backend = backend
        self._cache = cache
        self._timeout_seconds = timeout_seconds
        self._cache_namespace = cache_namespace
        self._locks: dict[str, _LockEntry] = {}
        self._locks_guard = asyncio.Lock()

    @property
    def backend_name(self) -> str:
        return self._backend.name

    @property
    def active_lock_count(self) -> int:
        return len(self._locks)

    async def is_ready(self) -> bool:
        return await asyncio.to_thread(self._cache.ping)

    @asynccontextmanager
    async def _lock_for(self, key: str) -> AsyncIterator[None]:
        async with self._locks_guard:
            entry = self._locks.get(key)
            if entry is None:
                entry = _LockEntry(lock=asyncio.Lock())
                self._locks[key] = entry
            entry.users += 1
        try:
            async with entry.lock:
                yield
        finally:
            async with self._locks_guard:
                entry.users -= 1
                if entry.users == 0 and self._locks.get(key) is entry:
                    del self._locks[key]

    async def answer(self, *, question_id: str, question: str) -> EvaluationResponse:
        try:
            async with asyncio.timeout(self._timeout_seconds):
                return await self._answer_within_budget(
                    question_id=question_id,
                    question=question,
                )
        except TimeoutError as exc:
            raise AgentTimedOut(
                f"Agent exceeded the {self._timeout_seconds:g}s internal timeout"
            ) from exc

    async def _answer_within_budget(
        self, *, question_id: str, question: str
    ) -> EvaluationResponse:
        key = request_cache_key(
            namespace=self._cache_namespace,
            question_id=question_id,
            question=question,
        )
        cached = await asyncio.to_thread(
            self._cache.get,
            key,
            question_id=question_id,
            question=question,
        )
        if cached is not None:
            logger.info("answer cache hit key=%s", key[:12])
            return cached

        async with self._lock_for(key):
            cached = await asyncio.to_thread(
                self._cache.get,
                key,
                question_id=question_id,
                question=question,
            )
            if cached is not None:
                return cached
            output = await self._backend.answer(
                question_id=question_id,
                question=question,
            )
            response = await asyncio.to_thread(
                normalize_response,
                question_id=question_id,
                question=question,
                output=output,
            )
            await asyncio.to_thread(self._cache.put, key, response)
            return response
