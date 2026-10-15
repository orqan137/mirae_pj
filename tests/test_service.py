from __future__ import annotations

import asyncio
from pathlib import Path

import uvloop

from app.cache import ResponseCache
from app.errors import AgentTimedOut
from app.models import BackendOutput
from app.service import AnswerService


class SlowBackend:
    name = "slow"

    async def answer(self, *, question_id: str, question: str) -> BackendOutput:
        await asyncio.sleep(0.2)
        return BackendOutput(answer="too late")


def test_timeout_covers_lock_wait_and_lock_entries_are_released(tmp_path: Path) -> None:
    async def scenario() -> tuple[list[object], int]:
        cache = ResponseCache(tmp_path / "answers.sqlite3")
        cache.initialize()
        service = AnswerService(
            backend=SlowBackend(),
            cache=cache,
            timeout_seconds=0.05,
            cache_namespace="test-v1",
        )
        first = asyncio.create_task(
            service.answer(question_id="Q", question="동일 질문")
        )
        await asyncio.sleep(0.01)
        second = asyncio.create_task(
            service.answer(question_id="Q", question="동일 질문")
        )
        results = await asyncio.gather(first, second, return_exceptions=True)
        await asyncio.sleep(0)
        return results, service.active_lock_count

    results, lock_count = uvloop.run(scenario())
    assert all(isinstance(result, AgentTimedOut) for result in results)
    assert lock_count == 0
