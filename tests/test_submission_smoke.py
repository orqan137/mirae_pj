from __future__ import annotations

import json
from email.message import Message
from urllib.parse import parse_qs, urlsplit

import pytest

from scripts import smoke_test


class Response:
    status = 200

    def __init__(self, payload):
        self.payload = payload
        self.headers = Message()
        self.headers["Content-Type"] = "application/json"
        self.headers["Cache-Control"] = "no-store"

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


@pytest.mark.parametrize("failure", [None, "third_changed", "empty_context", "third_schema"])
def test_submission_smoke_checks_three_identical_answerable_requests(monkeypatch, failure):
    requests = []

    def urlopen(request, timeout):
        if isinstance(request, str):
            assert request == "http://example.test/readyz"
            return Response({"status": "ready"})
        requests.append(request)
        query = parse_qs(urlsplit(request.full_url).query)
        question = query["question"][0]
        assert "IRP 세액공제" in question
        assert "& + % 🚀" in question
        assert request.method == "GET"
        payload = {
            "question_id": query["question_id"][0],
            "question": question,
            "retrieved_context": "근거 자료",
            "think_trace": "검색 및 생성 완료",
            "answer": "자료에 따른 세액공제 안내",
        }
        if failure == "empty_context":
            payload["retrieved_context"] = ""
        if len(requests) == 3:
            if failure == "third_changed":
                payload["answer"] = "다른 응답"
            if failure == "third_schema":
                payload["answer"] = None
        return Response(payload)

    monkeypatch.setattr(smoke_test.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr("sys.argv", ["smoke_test.py", "http://example.test/answer"])
    if failure:
        with pytest.raises(AssertionError):
            smoke_test.main()
    else:
        assert smoke_test.main() == 0
        assert len(requests) == 3
        assert len({request.full_url for request in requests}) == 1
