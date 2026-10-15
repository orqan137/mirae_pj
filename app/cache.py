from __future__ import annotations

import hashlib
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from app.models import EvaluationResponse


def request_cache_key(*, namespace: str, question_id: str, question: str) -> str:
    """Hash an unambiguous length-framed request identity."""

    digest = hashlib.sha256(b"pension-agent-answer-cache-v1\0")
    for value in (namespace, question_id, question):
        encoded = value.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, byteorder="big", signed=False))
        digest.update(encoded)
    return digest.hexdigest()


class ResponseCache:
    def __init__(self, path: Path, *, max_entries: int = 1_000) -> None:
        self._path = path
        self._max_entries = max_entries

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=30)
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _secure_storage(self) -> None:
        """Keep cached evaluation questions and answers owner-readable only."""

        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.parent.chmod(0o700)
        if not self._path.exists():
            descriptor = os.open(self._path, os.O_CREAT | os.O_WRONLY, 0o600)
            os.close(descriptor)
        self._path.chmod(0o600)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{self._path}{suffix}")
            if sidecar.exists():
                sidecar.chmod(0o600)

    def initialize(self) -> None:
        self._secure_storage()
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA journal_size_limit=33554432")
            connection.execute("PRAGMA wal_autocheckpoint=1000")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS answer_cache (
                    cache_key TEXT PRIMARY KEY,
                    question_id TEXT NOT NULL,
                    question_sha256 TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS answer_cache_created_at_idx "
                "ON answer_cache(created_at)"
            )
        self._secure_storage()

    def ping(self) -> bool:
        with self._connect() as connection:
            return connection.execute("SELECT 1").fetchone() == (1,)

    def get(
        self,
        cache_key: str,
        *,
        question_id: str,
        question: str,
    ) -> EvaluationResponse | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT question_id, question_sha256, response_json
                FROM answer_cache
                WHERE cache_key = ?
                """,
                (cache_key,),
            ).fetchone()
            if row is None:
                return None
            expected_question_hash = hashlib.sha256(
                question.encode("utf-8")
            ).hexdigest()
            if row[0] != question_id or row[1] != expected_question_hash:
                connection.execute(
                    "DELETE FROM answer_cache WHERE cache_key = ?", (cache_key,)
                )
                return None
            try:
                response = EvaluationResponse.model_validate_json(row[2])
            except (ValidationError, ValueError, TypeError):
                connection.execute(
                    "DELETE FROM answer_cache WHERE cache_key = ?", (cache_key,)
                )
                return None
            if response.question_id != question_id or response.question != question:
                connection.execute(
                    "DELETE FROM answer_cache WHERE cache_key = ?", (cache_key,)
                )
                return None
            return response

    def put(self, cache_key: str, response: EvaluationResponse) -> None:
        question_hash = hashlib.sha256(response.question.encode("utf-8")).hexdigest()
        created_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO answer_cache (
                    cache_key, question_id, question_sha256, response_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    cache_key,
                    response.question_id,
                    question_hash,
                    response.model_dump_json(),
                    created_at,
                ),
            )
            connection.execute(
                """
                DELETE FROM answer_cache
                WHERE cache_key IN (
                    SELECT cache_key
                    FROM answer_cache
                    ORDER BY created_at DESC, rowid DESC
                    LIMIT -1 OFFSET ?
                )
                """,
                (self._max_entries,),
            )
