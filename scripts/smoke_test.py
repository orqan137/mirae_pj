#!/usr/bin/env python3
"""Validate a running endpoint against the organizer's success contract."""

from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
import uuid


EXPECTED_KEYS = {
    "question_id",
    "question",
    "retrieved_context",
    "think_trace",
    "answer",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="Complete URL ending in /answer")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument(
        "--allow-demo",
        action="store_true",
        help="Allow the local demo backend while checking only the HTTP contract",
    )
    args = parser.parse_args()

    parsed_url = urllib.parse.urlsplit(args.url)
    answer_path = parsed_url.path.rstrip("/")
    if not answer_path.endswith("/answer"):
        parser.error("url path must end in /answer")
    parent_path = answer_path[: -len("/answer")]
    readiness_url = urllib.parse.urlunsplit(
        (
            parsed_url.scheme,
            parsed_url.netloc,
            f"{parent_path}/readyz",
            "",
            "",
        )
    )
    with urllib.request.urlopen(readiness_url, timeout=min(args.timeout, 10.0)) as response:
        readiness = json.loads(response.read().decode("utf-8"))
        assert response.status == 200, f"readiness expected 200, got {response.status}"
        assert readiness == {"status": "ready"}, readiness

    question_id = f"SMOKE-{uuid.uuid4()}"
    question = "IRP 세액공제 한도가 얼마인가요? & + % 🚀"
    query = urllib.parse.urlencode(
        {"question_id": question_id, "question": question}
    )
    normalized_url = urllib.parse.urlunsplit(
        (
            parsed_url.scheme,
            parsed_url.netloc,
            answer_path,
            parsed_url.query,
            "",
        )
    )
    separator = "&" if parsed_url.query else "?"
    request = urllib.request.Request(
        f"{normalized_url}{separator}{query}", method="GET"
    )
    payloads: list[dict[str, object]] = []
    for attempt in range(3):
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            content_type = response.headers.get_content_type()
            status = response.status
            cache_control = response.headers.get("Cache-Control", "")
            payload = json.loads(response.read().decode("utf-8"))
        assert status == 200, f"attempt {attempt + 1}: expected 200, got {status}"
        assert content_type == "application/json", content_type
        assert "no-store" in cache_control.lower(), cache_control
        assert isinstance(payload, dict), type(payload)
        assert set(payload) == EXPECTED_KEYS, set(payload)
        assert all(isinstance(value, str) for value in payload.values()), payload
        assert payload["question_id"] == question_id
        assert payload["question"] == question
        payloads.append(payload)

    payload = payloads[0]
    assert payload["answer"].strip(), "answer must not be empty"
    assert payload["retrieved_context"].strip(), "retrieved_context must not be empty"
    if not args.allow_demo:
        diagnostic = "\n".join(
            str(payload[key]) for key in ("retrieved_context", "think_trace", "answer")
        ).casefold()
        assert "demo" not in diagnostic and "데모" not in diagnostic, (
            "submission smoke test reached the demo backend; configure production HCX first"
        )
    assert all(item == payload for item in payloads[1:]), (
        "identical retry did not reuse the response"
    )
    mode = "contract-only demo" if args.allow_demo else "submission"
    print(f"PASS: {mode} readiness, response contract, and identical retry are valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
