from __future__ import annotations

import stat
import sqlite3
from pathlib import Path

from app.cache import ResponseCache, request_cache_key
from app.models import EvaluationResponse


def _response(question_id: str, question: str) -> EvaluationResponse:
    return EvaluationResponse(
        question_id=question_id,
        question=question,
        retrieved_context="근거",
        think_trace="요약",
        answer="답변",
    )


def test_cache_storage_is_owner_only(tmp_path: Path) -> None:
    path = tmp_path / "runtime" / "answers.sqlite3"
    cache = ResponseCache(path)

    cache.initialize()

    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_request_cache_key_is_length_framed_and_namespaced() -> None:
    first = request_cache_key(
        namespace="release-a", question_id="a", question="\0b"
    )
    second = request_cache_key(
        namespace="release-a", question_id="a\0", question="b"
    )
    other_release = request_cache_key(
        namespace="release-b", question_id="a", question="\0b"
    )
    assert first != second
    assert first != other_release


def test_cache_evicts_invalid_or_mismatched_rows(tmp_path: Path) -> None:
    path = tmp_path / "answers.sqlite3"
    cache = ResponseCache(path)
    cache.initialize()
    key = request_cache_key(namespace="v1", question_id="Q", question="질문")
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            INSERT INTO answer_cache (
                cache_key, question_id, question_sha256, response_json, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (key, "Q", "bad", "{not-json", "2026-01-01T00:00:00+00:00"),
        )
    assert cache.get(key, question_id="Q", question="질문") is None

    cache.put(key, _response("OTHER", "질문"))
    assert cache.get(key, question_id="Q", question="질문") is None
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM answer_cache").fetchone() == (0,)


def test_cache_prunes_oldest_completed_rows(tmp_path: Path) -> None:
    path = tmp_path / "answers.sqlite3"
    cache = ResponseCache(path, max_entries=2)
    cache.initialize()
    for index in range(3):
        question_id = f"Q-{index}"
        question = f"질문 {index}"
        key = request_cache_key(
            namespace="v1", question_id=question_id, question=question
        )
        cache.put(key, _response(question_id, question))

    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM answer_cache").fetchone() == (2,)
