from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

import app.backends.hcx as hcx_backend_module
from app.backends.hcx import (
    HyperClovaXBackend,
    _is_calculation_only_plan,
    _parse_clova_response,
)
from app.calculations import calculate_pension_tax_credit
from app.backends.http import HttpAgentBackend
from app.errors import BackendUnavailable, InvalidBackendResponse
from app.product_facts import AXIS_TERMS, EXTRACTOR_FINGERPRINT, ProductEvidenceError


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_calculation_fallback_scope_rejects_an_additional_explanation_request() -> None:
    pure = (
        "총급여가 5,500만원이고 IRP에 900만원을 납입했습니다. "
        "세액공제액을 계산해 주세요."
    )
    mixed = (
        "DB형과 DC형 차이를 설명하고, 총급여가 5,500만원이고 IRP에 900만원을 "
        "납입했을 때 세액공제액도 계산해 주세요."
    )

    pure_route = hcx_backend_module.classify_question(pure)
    mixed_route = hcx_backend_module.classify_question(mixed)
    assert _is_calculation_only_plan(
        hcx_backend_module.plan_question(pure, pure_route),
        calculate_pension_tax_credit(pure),
    ) is True
    assert _is_calculation_only_plan(
        hcx_backend_module.plan_question(mixed, mixed_route),
        calculate_pension_tax_credit(mixed),
    ) is False


def _write_test_corpus(
    knowledge: Path,
    *,
    source_id: str | None = None,
    content: str = "",
) -> None:
    documents: list[dict[str, object]] = []
    if source_id is not None:
        output = knowledge / "general" / f"{source_id}.txt"
        output.parent.mkdir(parents=True, exist_ok=True)
        source_path = f"일반문서/{source_id}.docx"
        output.write_text(
            "\n".join(
                [
                    "[METADATA]",
                    "source_scope=organizer_provided",
                    f"source_id={source_id}",
                    "source_group=general",
                    f"source_path={source_path}",
                    f"source_sha256={'a' * 64}",
                    "extraction_method=test",
                    "extractor_fingerprint=pension-corpus-v2",
                    "review_status=raw",
                    "ocr_requested=false",
                    "ocr_dpi=",
                    "min_semantic_chars_per_page=",
                    f"canonical_source_id={source_id}",
                    "product_aliases=",
                    "page_count=",
                    "[CONTENT]",
                    content,
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        documents.append(
            {
                "source_id": source_id,
                "source_group": "general",
                "source_path": source_path,
                "output_path": f"general/{source_id}.txt",
                "media_type": "docx",
                "sha256": "a" * 64,
                "size_bytes": 1,
                "page_count": None,
                "extraction_method": "test",
                "extractor_fingerprint": "pension-corpus-v2",
                "review_status": "raw",
                "ocr_requested": False,
                "ocr_dpi": None,
                "min_semantic_chars_per_page": None,
                "extracted_chars": len(content),
                "title_hint": source_id,
                "text_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                "canonical_source_id": source_id,
                "product_aliases": [],
            }
        )
    (knowledge / "manifest.json").write_text(
        json.dumps(
            {
                "extractor_fingerprint": "pension-corpus-v2",
                "build_options": {
                    "ocr": False,
                    "ocr_dpi": 220,
                    "min_semantic_chars_per_page": 120,
                },
                "document_count": len(documents),
                "documents": documents,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _write_test_governance(path: Path, *, source_ids: tuple[str, ...]) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "registry_version": "pension-source-governance-v4",
                "knowledge_as_of": "2026-08-30",
                "source_scope": "organizer_provided",
                "default": {
                    "authority": "organizer_evidence",
                    "jurisdiction": "KR",
                    "audiences": ["customer"],
                    "published_date": None,
                    "reference_date": None,
                    "effective_from": None,
                    "effective_to": None,
                    "date_precision": None,
                    "required_query_terms_any": [],
                },
                "documents": list(source_ids),
                "overrides": {},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _write_test_product_evidence(path: Path, manifest: Path) -> str:
    target_code = "KR0000000000"
    records: list[dict[str, object]] = []
    for number in range(100):
        code = f"KR{number:010d}"
        evidence: dict[str, list[dict[str, object]]] = {
            axis: [] for axis in AXIS_TERMS
        }
        if code == target_code:
            evidence["fees"] = [
                {
                    "page": 4,
                    "raw_text": "종류(Class) C-P 총보수 0.31% [단위:%]",
                    "dates": [],
                    "units": ["%"],
                    "class_labels": ["C-P"],
                    "status": "raw_evidence_not_normalized",
                }
            ]
        records.append(
            {
                "product_code": code,
                "canonical_source_id": code,
                "aliases": [code],
                "source_path": f"투자설명서/{code}/R2.pdf",
                "source_sha256": f"{number:064x}",
                "title": f"테스트 상품 {number}",
                "basis_dates": ["2025-01-01"],
                "evidence": evidence,
            }
        )
    path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "extractor_fingerprint": EXTRACTOR_FINGERPRINT,
                "corpus_manifest_sha256": hashlib.sha256(
                    manifest.read_bytes()
                ).hexdigest(),
                "record_count": len(records),
                "records": records,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return target_code


def test_http_backend_normalizes_nested_payload_and_ignores_raw_reasoning(
    settings_factory: Any,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert json.loads(request.content) == {
            "question_id": "Q-1",
            "question": "연금 질문",
        }
        return httpx.Response(
            200,
            json={
                "result": {
                    "answer": "외부 Agent 답변",
                    "documents": [{"id": "doc41"}],
                    "trace_summary": "retriever ok",
                    "think_trace": "raw private reasoning api_key=secret",
                }
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    settings = settings_factory(
        agent_backend="http",
        agent_upstream_url="http://agent.internal/answer",
    )
    backend = HttpAgentBackend(settings, client=client)
    output = asyncio.run(backend.answer(question_id="Q-1", question="연금 질문"))
    asyncio.run(client.aclose())
    assert output.answer == "외부 Agent 답변"
    assert output.retrieved_context == [{"id": "doc41"}]
    assert output.trace_summary == "retriever ok"
    assert "private reasoning" not in str(output.trace_summary)


def test_http_backend_maps_upstream_5xx(settings_factory: Any) -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(500, text="secret stack"))
    )
    backend = HttpAgentBackend(
        settings_factory(
            agent_backend="http",
            agent_upstream_url="http://agent.internal/answer",
        ),
        client=client,
    )
    with pytest.raises(BackendUnavailable):
        asyncio.run(backend.answer(question_id="Q", question="질문"))
    asyncio.run(client.aclose())


def test_hcx_backend_retrieves_evidence_and_parses_clova_response(
    tmp_path: Path,
    settings_factory: Any,
) -> None:
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    _write_test_corpus(
        knowledge,
        source_id="doc41",
        content="연금저축과 IRP의 세액공제율은 16.5%라는 근거입니다.",
    )
    prompt = tmp_path / "system.md"
    prompt.write_text("근거에 기반해 답하라.", encoding="utf-8")
    governance = tmp_path / "governance.json"
    _write_test_governance(governance, source_ids=("doc41",))

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer test-secret"
        body = json.loads(request.content)
        assert body["messages"][0]["role"] == "system"
        assert "doc41" in body["messages"][1]["content"]
        assert "citation_id=doc41:chunk=1" in body["messages"][1]["content"]
        assert "[근거:citation_id]" in body["messages"][1]["content"]
        assert "effective_from=unknown" in body["messages"][1]["content"]
        assert "시행기간 미확인" in body["messages"][1]["content"]
        assert body["maxCompletionTokens"] == 4_096
        assert "maxTokens" not in body
        assert "thinking" not in body
        return httpx.Response(
            200,
            headers={
                "x-ratelimit-remaining-requests": "59",
                "x-ratelimit-remaining-tokens": "9462",
                "x-ncp-clovastudio-request-id": "must-not-be-traced",
            },
            json={
                "status": {"code": "20000", "message": "OK"},
                "result": {
                    "message": {
                        "role": "assistant",
                        "content": (
                            "세액공제율은 16.5%입니다 "
                            "[근거:doc41:chunk=1]."
                        ),
                    },
                    "finishReason": "stop",
                    "usage": {
                        "promptTokens": 39,
                        "completionTokens": 31,
                        "totalTokens": 70,
                    },
                    "aiFilter": [
                        {"name": "insult", "score": "2", "result": "OK"}
                    ],
                },
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    settings = settings_factory(
        agent_backend="hcx",
        knowledge_dir=knowledge,
        system_prompt_path=prompt,
        source_governance_path=governance,
        hcx_api_url="https://clova.example/v3/chat-completions/HCX",
        hcx_api_key="test-secret",
        hcx_model="HCX-test",
    )
    backend = HyperClovaXBackend(settings, client=client)
    output = asyncio.run(
        backend.answer(question_id="Q-HCX", question="IRP 세액공제 근거는?")
    )
    asyncio.run(client.aclose())
    assert output.answer == (
        "세액공제율은 16.5%입니다 [근거:doc41:chunk=1]."
    )
    assert "source_id=doc41" in output.retrieved_context
    assert "test-secret" not in str(output.trace_summary)
    assert "must-not-be-traced" not in str(output.trace_summary)
    assert '"grounded":true' in str(output.trace_summary)
    trace = json.loads(str(output.trace_summary))
    model_tool = next(
        tool for tool in trace["tools"] if tool["name"] == "hyperclova_x"
    )
    assert model_tool["telemetry"] == {
        "version": "hcx-safe-usage-telemetry-v1",
        "usage_observed": True,
        "usage": {
            "prompt_tokens": 39,
            "completion_tokens": 31,
            "total_tokens": 70,
        },
        "rate_limit_observed": True,
        "rate_limit": {"remaining_requests": 59, "remaining_tokens": 9462},
    }


def test_hcx_backend_skips_model_for_explicit_date_without_effective_period(
    tmp_path: Path,
    settings_factory: Any,
) -> None:
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    _write_test_corpus(
        knowledge,
        source_id="doc41",
        content="IRP 세액공제 기준과 한도",
    )
    prompt = tmp_path / "system.md"
    prompt.write_text("근거에 기반해 답하라.", encoding="utf-8")
    governance = tmp_path / "governance.json"
    _write_test_governance(governance, source_ids=("doc41",))

    def must_not_call(_: httpx.Request) -> httpx.Response:
        raise AssertionError("HCX must not fill an unknown effective period")

    client = httpx.AsyncClient(transport=httpx.MockTransport(must_not_call))
    backend = HyperClovaXBackend(
        settings_factory(
            agent_backend="hcx",
            knowledge_dir=knowledge,
            source_governance_path=governance,
            system_prompt_path=prompt,
            hcx_api_url="https://clova.example/v3/chat-completions/HCX-005",
            hcx_api_key="test-secret",
            hcx_model="HCX-005",
            hcx_thinking_effort="none",
        ),
        client=client,
    )
    output = asyncio.run(
        backend.answer(
            question_id="Q-DATE",
            question="2026년 IRP 세액공제 기준을 알려주세요",
        )
    )
    asyncio.run(client.aclose())
    trace = json.loads(str(output.trace_summary))
    assert trace["decision_status"] == "out_of_corpus"
    assert trace["hyperclova_x_called"] is False
    assessor = next(
        tool for tool in trace["tools"] if tool["name"] == "evidence_assessor"
    )
    assert assessor["scope_exclusions"] == ["doc41:effective_date_unknown"]


def test_hcx_backend_short_circuits_private_live_data_before_all_runtime_tools(
    tmp_path: Path,
    settings_factory: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    _write_test_corpus(knowledge)
    facts = tmp_path / "product_evidence.json"
    product_code = _write_test_product_evidence(facts, knowledge / "manifest.json")
    prompt = tmp_path / "system.md"
    prompt.write_text("근거에 기반해 답하라.", encoding="utf-8")

    def hcx_must_not_call(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("HCX must not be called for unsupported private access")

    client = httpx.AsyncClient(transport=httpx.MockTransport(hcx_must_not_call))
    backend = HyperClovaXBackend(
        settings_factory(
            agent_backend="hcx",
            knowledge_dir=knowledge,
            product_evidence_path=facts,
            system_prompt_path=prompt,
            hcx_api_url="https://clova.example/v3/chat-completions/HCX",
            hcx_api_key="test-secret",
            hcx_model="HCX-test",
        ),
        client=client,
    )

    def forbidden_runtime_tool(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("unsupported scope must stop before runtime tools")

    monkeypatch.setattr(
        hcx_backend_module.ProductIdentityResolver,
        "resolve",
        forbidden_runtime_tool,
    )
    monkeypatch.setattr(
        hcx_backend_module,
        "assess_suitability",
        forbidden_runtime_tool,
    )
    monkeypatch.setattr(
        hcx_backend_module,
        "calculate_pension_tax_credit",
        forbidden_runtime_tool,
    )
    monkeypatch.setattr(
        hcx_backend_module,
        "retrieve_for_queries",
        forbidden_runtime_tool,
    )
    monkeypatch.setattr(
        hcx_backend_module,
        "compare_product_facts",
        forbidden_runtime_tool,
    )

    output = asyncio.run(
        backend.answer(
            question_id="Q-PRIVATE-LIVE",
            question=(
                f"내 IRP 계좌의 오늘 실시간 잔고와 {product_code} 보유 내역을 "
                "조회해 주세요."
            ),
        )
    )
    asyncio.run(client.aclose())

    assert output.retrieved_context == ""
    assert "접속할 수 없습니다" in output.answer
    trace = json.loads(str(output.trace_summary))
    assert trace["decision_status"] == "out_of_scope"
    assert trace["hyperclova_x_called"] is False
    guard = trace["tools"][0]
    assert guard["name"] == "capability_scope_guard"
    assert guard["status"] == "blocked"
    assert guard["reason_codes"] == ["private_account_live_access_unavailable"]
    assert "local_retriever" in guard["skipped_tools"]
    assert "hyperclova_x" in guard["skipped_tools"]


def test_hcx_backend_blocks_unsupported_number_without_second_model_call(
    tmp_path: Path,
    settings_factory: Any,
) -> None:
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    _write_test_corpus(
        knowledge,
        source_id="doc41",
        content="연금저축 세액공제율은 공식 근거상 16.5%입니다.",
    )
    prompt = tmp_path / "system.md"
    prompt.write_text("근거에 기반해 답하라.", encoding="utf-8")
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={
                "status": {"code": "20000"},
                "result": {
                    "message": {
                        "role": "assistant",
                        "content": "세액공제율은 99.9%입니다.",
                    },
                    "finishReason": "stop",
                    "aiFilter": [{"score": "2", "result": "OK"}],
                },
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    backend = HyperClovaXBackend(
        settings_factory(
            agent_backend="hcx",
            knowledge_dir=knowledge,
            system_prompt_path=prompt,
            hcx_api_url="https://clova.example/v3/chat-completions/HCX",
            hcx_api_key="test-secret",
            hcx_model="HCX-test",
        ),
        client=client,
    )
    output = asyncio.run(
        backend.answer(question_id="Q-VALIDATE", question="연금저축 세액공제율은?")
    )
    asyncio.run(client.aclose())

    assert calls == 1
    assert "99.9" not in output.answer
    assert "16.5%" in output.answer
    assert "[근거:doc41:chunk=1]" in output.answer
    trace = json.loads(str(output.trace_summary))
    assert trace["answer_validation"] == "passed_after_grounded_extractive_fallback"
    validator = next(
        tool for tool in trace["tools"] if tool["name"] == "answer_validator"
    )
    assert validator["unsupported_claims"] == 0


def test_hcx_trim_cannot_replace_credit_limit_with_refund_example(
    tmp_path: Path,
    settings_factory: Any,
) -> None:
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    example = (
        "연금계좌 세액공제액은 납입한 금액에 세액공제율 16.5% 또는 13.2%를 곱해서 계산합니다. "
        "IRP에 900만원을 납입했을 때 최대 세액공제액은 148만 5천원입니다. "
        "세액공제액은 소득에 따른 세액공제율과 납입한 금액에 따라 달라지며, "
        "실제 납부할 세금이 작으면 세액공제액도 그 금액만큼 줄어듭니다."
    )
    _write_test_corpus(
        knowledge,
        source_id="doc41",
        content=(
            "[TABLE 1 ROW 1]\n구분\t세액공제 납입한도\tISA 추가 공제한도\n"
            "[TABLE 1 ROW 2]\nIRP 세액공제 한도\t900만 원\t300만 원\n\n"
            + example
        ),
    )
    prompt = tmp_path / "system.md"
    prompt.write_text("근거에 기반해 답하라.", encoding="utf-8")
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={
            "status": {"code": "20000"},
            "result": {
                "message": {
                    "role": "assistant",
                    "content": "세액공제 한도는 9999만원입니다 [근거:doc41:chunk=1].\n"
                    + example + " [근거:doc41:chunk=1].",
                },
                "finishReason": "stop",
                "aiFilter": [{"score": "2", "result": "OK"}],
            },
        })

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    backend = HyperClovaXBackend(
        settings_factory(
            agent_backend="hcx", knowledge_dir=knowledge, system_prompt_path=prompt,
            hcx_api_url="https://clova.example/v3/chat-completions/HCX",
            hcx_api_key="test-secret", hcx_model="HCX-test",
        ),
        client=client,
    )
    output = asyncio.run(backend.answer(question_id="Q-LIMIT-TRIM", question="IRP 세액공제 한도가 얼마인가요?"))
    asyncio.run(client.aclose())
    assert calls == 1
    assert "9999" not in output.answer
    assert "세액공제 납입한도: 900만 원" in output.answer
    trace = json.loads(str(output.trace_summary))
    assert trace["answer_validation"] == "passed_after_grounded_extractive_fallback"


def test_hcx_backend_recovers_pension_commencement_boundary_from_exact_evidence(
    tmp_path: Path,
    settings_factory: Any,
) -> None:
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    _write_test_corpus(
        knowledge,
        source_id="doc37",
        content=(
            "연금계좌의 연금수령 절차입니다. 만 55세 이상 가입자이고 "
            "가입기간 5년 이상이면 연금 개시가 가능합니다. "
            "퇴직금이 입금된 연금계좌는 가입기간 5년 이상 요건을 적용하지 않습니다. "
            "연금 수령액이 연간 1,500만원을 초과하면 과세될 수 있습니다."
        ),
    )
    prompt = tmp_path / "system.md"
    prompt.write_text("근거에 기반해 답하라.", encoding="utf-8")

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": {"code": "20000"},
                "result": {
                    "message": {
                        "role": "assistant",
                        "content": (
                            "연금 수령액이 연간 1,500만원을 초과하면 과세될 수 있습니다 "
                            "[근거:doc37:chunk=1]."
                        ),
                    },
                    "finishReason": "stop",
                    "aiFilter": [{"score": "2", "result": "OK"}],
                },
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    backend = HyperClovaXBackend(
        settings_factory(
            agent_backend="hcx",
            knowledge_dir=knowledge,
            system_prompt_path=prompt,
            hcx_api_url="https://clova.example/v3/chat-completions/HCX",
            hcx_api_key="test-secret",
            hcx_model="HCX-test",
        ),
        client=client,
    )
    output = asyncio.run(
        backend.answer(
            question_id="Q-COMMENCEMENT",
            question=(
                "IRP 가입 3년이고 만 55세면 연금을 개시할 수 있나요? "
                "퇴직금이 들어온 계좌도 같은 조건인가요?"
            ),
        )
    )
    asyncio.run(client.aclose())

    assert "가입 3년이면 됩니다" not in output.answer
    assert "가입기간 5년 이상" in output.answer
    assert "요건을 적용하지 않습니다" in output.answer
    assert "1,500만원" not in output.answer
    assert "[근거:doc37:chunk=1]" in output.answer
    trace = json.loads(str(output.trace_summary))
    assert trace["answer_validation"] == "passed_after_grounded_extractive_fallback"


def test_hcx_backend_repairs_unique_supported_numeric_citation_without_second_call(
    tmp_path: Path,
    settings_factory: Any,
) -> None:
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    _write_test_corpus(
        knowledge,
        source_id="doc41",
        content="연금저축 세액공제율은 공식 근거상 16.5%입니다.",
    )
    prompt = tmp_path / "system.md"
    prompt.write_text("근거에 기반해 답하라.", encoding="utf-8")
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={
                "status": {"code": "20000"},
                "result": {
                    "message": {
                        "role": "assistant",
                        "content": "연금저축 세액공제율은 16.5%입니다.",
                    },
                    "finishReason": "stop",
                    "aiFilter": [{"score": "2", "result": "OK"}],
                },
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    backend = HyperClovaXBackend(
        settings_factory(
            agent_backend="hcx",
            knowledge_dir=knowledge,
            system_prompt_path=prompt,
            hcx_api_url="https://clova.example/v3/chat-completions/HCX",
            hcx_api_key="test-secret",
            hcx_model="HCX-test",
        ),
        client=client,
    )
    output = asyncio.run(
        backend.answer(question_id="Q-REPAIR", question="연금저축 세액공제율은?")
    )
    asyncio.run(client.aclose())

    assert calls == 1
    # Tax-rate sentences are checked for both the exact value and the cited
    # rule relation, so a successful repair earns the full evidence mark.
    assert output.answer.endswith("[근거:doc41:chunk=1].")
    trace = json.loads(str(output.trace_summary))
    assert trace["answer_validation"] == "passed_after_citation_repair"
    validator = next(
        tool for tool in trace["tools"] if tool["name"] == "answer_validator"
    )
    assert validator["status"] == "ok"


def test_hcx_backend_omits_thinking_when_disabled(
    tmp_path: Path,
    settings_factory: Any,
) -> None:
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    _write_test_corpus(
        knowledge,
        source_id="doc1",
        content="퇴직연금 제도 연결 확인용 근거",
    )
    prompt = tmp_path / "system.md"
    prompt.write_text("근거에 기반해 답하라.", encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert "thinking" not in body
        return httpx.Response(
            200,
            json={
                "status": {"code": "20000", "message": "OK"},
                "result": {
                    "message": {"role": "assistant", "content": "HCX 최종 답변"},
                    "finishReason": "stop",
                    "aiFilter": [{"name": "insult", "score": "2", "result": "OK"}],
                },
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    backend = HyperClovaXBackend(
        settings_factory(
            agent_backend="hcx",
            knowledge_dir=knowledge,
            system_prompt_path=prompt,
            hcx_api_url="https://clova.example/v3/chat-completions/HCX-005",
            hcx_api_key="test-secret",
            hcx_model="HCX-005",
            hcx_thinking_effort="none",
        ),
        client=client,
    )
    output = asyncio.run(
        backend.answer(question_id="Q-HCX", question="퇴직연금 제도를 설명해 주세요")
    )
    asyncio.run(client.aclose())
    assert output.answer == "HCX 최종 답변"


def test_hcx_backend_injects_deterministic_tax_calculation(
    tmp_path: Path,
    settings_factory: Any,
) -> None:
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    _write_test_corpus(
        knowledge,
        source_id="doc41",
        content="연금저축 600만원, IRP 포함 900만원 한도와 16.5%, 13.2% 세액공제 근거",
    )
    prompt = tmp_path / "system.md"
    prompt.write_text("근거에 기반해 답하라.", encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        user_prompt = json.loads(request.content)["messages"][1]["content"]
        assert "[DETERMINISTIC_CALCULATOR]" in user_prompt
        assert "status=calculated" in user_prompt
        assert "source_id=doc41" in user_prompt
        assert "calculated_tax_credit_won=1485000" in user_prompt
        assert "실제 세금 감소액" in user_prompt
        return httpx.Response(
            200,
            json={
                "status": {"code": "20000"},
                "result": {
                    "message": {"role": "assistant", "content": "세액공제 계산 답변"},
                    "finishReason": "stop",
                    "aiFilter": [{"score": "2", "result": "OK"}],
                },
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    backend = HyperClovaXBackend(
        settings_factory(
            agent_backend="hcx",
            knowledge_dir=knowledge,
            system_prompt_path=prompt,
            hcx_api_url="https://clova.example/v3/chat-completions/HCX",
            hcx_api_key="test-secret",
            hcx_model="HCX-test",
        ),
        client=client,
    )
    output = asyncio.run(
        backend.answer(
            question_id="Q-CALC",
            question=(
                "총급여가 5,500만원이고 IRP에 900만원을 납입했습니다. "
                "세액공제액을 계산해 주세요."
            ),
        )
    )
    asyncio.run(client.aclose())

    assert "[DETERMINISTIC_CALCULATOR]" in output.retrieved_context
    assert "calculated_tax_credit_won=1485000" in output.retrieved_context
    trace = json.loads(str(output.trace_summary))
    calculator = next(
        tool for tool in trace["tools"] if tool["name"] == "pension_tax_credit_calculator"
    )
    assert calculator == {
        "name": "pension_tax_credit_calculator",
        "version": "pension-tax-credit-doc41-v9",
        "status": "calculated",
        "source_id": "doc41",
        "calculation_performed": True,
        "eligible_contribution_won": 9_000_000,
        "rate_percent": 16.5,
        "calculated_credit_won": 1_485_000,
        "tax_liability_limit_disclosed": True,
    }


def test_hcx_backend_uses_validated_calculation_without_model_call(
    tmp_path: Path,
    settings_factory: Any,
) -> None:
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    _write_test_corpus(
        knowledge,
        source_id="doc41",
        content="연금저축 600만원, IRP 포함 900만원 한도와 16.5%, 13.2% 세액공제 근거",
    )
    prompt = tmp_path / "system.md"
    prompt.write_text("근거에 기반해 답하라.", encoding="utf-8")
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={
                "status": {"code": "20000"},
                "result": {
                    "message": {
                        "role": "assistant",
                        "content": "세액공제액은 근거에 없는 99.9%입니다.",
                    },
                    "finishReason": "stop",
                    "aiFilter": [{"score": "2", "result": "OK"}],
                },
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    backend = HyperClovaXBackend(
        settings_factory(
            agent_backend="hcx",
            knowledge_dir=knowledge,
            system_prompt_path=prompt,
            hcx_api_url="https://clova.example/v3/chat-completions/HCX",
            hcx_api_key="test-secret",
            hcx_model="HCX-test",
        ),
        client=client,
    )
    output = asyncio.run(
        backend.answer(
            question_id="Q-CALC-FALLBACK",
            question=(
                "총급여가 5,500만원이고 IRP에 900만원을 납입했습니다. "
                "세액공제액을 계산해 주세요."
            ),
        )
    )
    asyncio.run(client.aclose())

    assert calls == 0
    assert "99.9" not in output.answer
    assert "1,485,000원" in output.answer
    assert "[근거:doc41:calculator]" in output.answer
    trace = json.loads(str(output.trace_summary))
    assert trace["answer_validation"] == "passed_deterministic_calculation"
    assert trace["hyperclova_x_called"] is False


def test_hcx_backend_stops_before_generation_when_public_context_is_incomplete(
    tmp_path: Path,
    settings_factory: Any,
) -> None:
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    _write_test_corpus(
        knowledge,
        source_id="doc41",
        content="연금저축과 IRP 세액공제 계산 한도·공제율·소득 기준 근거",
    )
    prompt = tmp_path / "system.md"
    prompt.write_text("근거에 기반해 답하라.", encoding="utf-8")
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        raise AssertionError("HCX must not be called with incomplete public context")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    backend = HyperClovaXBackend(
        settings_factory(
            agent_backend="hcx",
            knowledge_dir=knowledge,
            system_prompt_path=prompt,
            hcx_api_url="https://clova.example/v3/chat-completions/HCX",
            hcx_api_key="test-secret",
            hcx_model="HCX-test",
            max_context_chars=300,
        ),
        client=client,
    )
    output = asyncio.run(
        backend.answer(
            question_id="Q-CONTEXT-BUDGET",
            question=(
                "총급여가 5,500만원이고 IRP에 900만원을 납입했습니다. "
                "세액공제액을 계산해 주세요."
            ),
        )
    )
    asyncio.run(client.aclose())

    assert called is False
    assert "공식 근거를 제한된 응답 Context에 빠짐없이 담을 수 없어" in output.answer
    trace = json.loads(str(output.trace_summary))
    assert trace["decision_status"] == "context_budget_exceeded"
    assert trace["hyperclova_x_called"] is False
    renderer = next(
        tool for tool in trace["tools"] if tool["name"] == "public_evidence_renderer"
    )
    assert renderer["status"] == "context_budget_exceeded"
    assert renderer["missing_sections"]


def test_hcx_backend_prioritizes_manifest_bound_structured_product_evidence(
    tmp_path: Path,
    settings_factory: Any,
) -> None:
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    _write_test_corpus(knowledge)
    facts = tmp_path / "product_evidence.json"
    target_code = _write_test_product_evidence(facts, knowledge / "manifest.json")
    prompt = tmp_path / "system.md"
    prompt.write_text("근거에 기반해 답하라.", encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        user_prompt = json.loads(request.content)["messages"][1]["content"]
        assert f"product_code={target_code} axis=fees" in user_prompt
        assert "C-P 총보수 0.31%" in user_prompt
        return httpx.Response(
            200,
            json={
                "status": {"code": "20000"},
                "result": {
                    "message": {
                        "role": "assistant",
                        "content": "해당 상품의 총보수 자료를 확인했습니다.",
                    },
                    "finishReason": "stop",
                    "aiFilter": [{"score": "2", "result": "OK"}],
                },
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    backend = HyperClovaXBackend(
        settings_factory(
            agent_backend="hcx",
            knowledge_dir=knowledge,
            product_evidence_path=facts,
            system_prompt_path=prompt,
            hcx_api_url="https://clova.example/v3/chat-completions/HCX",
            hcx_api_key="test-secret",
            hcx_model="HCX-test",
        ),
        client=client,
    )
    output = asyncio.run(
        backend.answer(question_id="Q-PRODUCT", question=f"{target_code}의 총보수는?")
    )
    asyncio.run(client.aclose())
    assert output.answer == "해당 상품의 총보수 자료를 확인했습니다."
    assert f"product_code={target_code}" in output.retrieved_context
    assert '"structured_product_evidence":true' in str(output.trace_summary)


def test_hcx_backend_resolves_official_product_name_without_code(
    tmp_path: Path,
    settings_factory: Any,
) -> None:
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    _write_test_corpus(knowledge)
    facts = tmp_path / "product_evidence.json"
    target_code = _write_test_product_evidence(facts, knowledge / "manifest.json")
    prompt = tmp_path / "system.md"
    prompt.write_text("근거에 기반해 답하라.", encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        user_prompt = json.loads(request.content)["messages"][1]["content"]
        assert f"product_code={target_code} axis=fees" in user_prompt
        return httpx.Response(
            200,
            json={
                "status": {"code": "20000"},
                "result": {
                    "message": {
                        "role": "assistant",
                        "content": "테스트 상품 0의 총보수 자료를 확인했습니다.",
                    },
                    "finishReason": "stop",
                    "aiFilter": [{"score": "2", "result": "OK"}],
                },
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    backend = HyperClovaXBackend(
        settings_factory(
            agent_backend="hcx",
            knowledge_dir=knowledge,
            product_evidence_path=facts,
            system_prompt_path=prompt,
            hcx_api_url="https://clova.example/v3/chat-completions/HCX-005",
            hcx_api_key="test-secret",
            hcx_model="HCX-005",
            hcx_thinking_effort="none",
        ),
        client=client,
    )
    output = asyncio.run(
        backend.answer(question_id="Q-NAME", question="테스트 상품 0의 총보수는?")
    )
    asyncio.run(client.aclose())
    assert output.answer == "테스트 상품 0의 총보수 자료를 확인했습니다."
    trace = json.loads(str(output.trace_summary))
    resolver = next(
        tool for tool in trace["tools"] if tool["name"] == "product_identity_resolver"
    )
    assert resolver["resolved"] == 1


def test_hcx_backend_fails_closed_when_configured_product_index_is_missing(
    tmp_path: Path,
    settings_factory: Any,
) -> None:
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    _write_test_corpus(knowledge)
    with pytest.raises(ProductEvidenceError, match="missing"):
        HyperClovaXBackend(
            settings_factory(
                agent_backend="hcx",
                knowledge_dir=knowledge,
                product_evidence_path=tmp_path / "missing.json",
                hcx_api_url="https://clova.example/v3/chat-completions/HCX",
                hcx_api_key="test-secret",
                hcx_model="HCX-test",
            )
        )


def test_hcx_openai_v3_uses_completion_tokens_and_validates_finish(
    tmp_path: Path,
    settings_factory: Any,
) -> None:
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    _write_test_corpus(
        knowledge,
        source_id="doc1",
        content="퇴직연금 제도 설명 근거",
    )
    prompt = tmp_path / "system.md"
    prompt.write_text("근거에 기반해 답하라.", encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["max_completion_tokens"] == 5_120
        assert body["reasoning_effort"] == "low"
        assert "max_tokens" not in body
        return httpx.Response(
            200,
            headers={
                "x-ratelimit-remaining-requests": "not-an-integer",
                "x-ratelimit-remaining-tokens": "1234",
            },
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "호환 답변"},
                    }
                ],
                "usage": {
                    "prompt_tokens": 21,
                    "completion_tokens": 4,
                    "total_tokens": 25,
                },
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    backend = HyperClovaXBackend(
        settings_factory(
            agent_backend="hcx",
            knowledge_dir=knowledge,
            system_prompt_path=prompt,
            hcx_api_url="https://clova.example/v1/chat/completions",
            hcx_api_key="test-secret",
            hcx_api_style="openai-v3",
            hcx_model="HCX-007",
            hcx_max_tokens=5_120,
            hcx_thinking_effort="low",
        ),
        client=client,
    )
    output = asyncio.run(backend.answer(question_id="Q", question="퇴직연금 제도 설명"))
    asyncio.run(client.aclose())
    assert output.answer == "호환 답변"
    trace = json.loads(str(output.trace_summary))
    model_tool = next(
        tool for tool in trace["tools"] if tool["name"] == "hyperclova_x"
    )
    assert model_tool["telemetry"]["usage"] == {
        "prompt_tokens": 21,
        "completion_tokens": 4,
        "total_tokens": 25,
    }
    assert model_tool["telemetry"]["rate_limit"] == {
        "remaining_tokens": 1234
    }


@pytest.mark.parametrize(
    "result",
    [
        {
            "message": {"role": "assistant", "content": "잘린 답변"},
            "finishReason": "length",
            "aiFilter": [{"score": "2", "result": "OK"}],
        },
        {
            "message": {"role": "assistant", "content": "필터 답변"},
            "finishReason": "stop",
            "aiFilter": [{"score": "0", "result": "OK"}],
        },
        {
            "message": {"role": "assistant"},
            "finishReason": "stop",
            "aiFilter": [{"score": "2", "result": "OK"}],
        },
    ],
)
def test_hcx_rejects_incomplete_or_filtered_response(
    result: dict[str, object],
    tmp_path: Path,
    settings_factory: Any,
) -> None:
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    _write_test_corpus(
        knowledge,
        source_id="doc1",
        content="퇴직연금 제도 설명 근거",
    )
    prompt = tmp_path / "system.md"
    prompt.write_text("근거에 기반해 답하라.", encoding="utf-8")
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                headers={
                    "x-ratelimit-remaining-requests": "41",
                    "x-ncp-clovastudio-request-id": "must-not-be-traced",
                },
                json={
                    "status": {"code": "20000"},
                    "result": {
                        **result,
                        "usage": {
                            "promptTokens": 20,
                            "completionTokens": 3,
                            "totalTokens": 23,
                        },
                    },
                },
            )
        )
    )
    backend = HyperClovaXBackend(
        settings_factory(
            agent_backend="hcx",
            knowledge_dir=knowledge,
            system_prompt_path=prompt,
            hcx_api_url="https://clova.example/v3/chat-completions/HCX-007",
            hcx_api_key="test-secret",
            hcx_model="HCX-007",
        ),
        client=client,
    )
    with pytest.raises(InvalidBackendResponse) as error:
        asyncio.run(backend.answer(question_id="Q", question="퇴직연금 제도 설명"))
    assert "잘린 답변" not in str(error.value)
    assert "필터 답변" not in str(error.value)
    assert "must-not-be-traced" not in str(error.value)
    asyncio.run(client.aclose())


def test_hcx_invalid_json_raises_sanitized_service_failure(
    tmp_path: Path,
    settings_factory: Any,
) -> None:
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    _write_test_corpus(
        knowledge,
        source_id="doc1",
        content="퇴직연금 제도 설명 근거",
    )
    prompt = tmp_path / "system.md"
    prompt.write_text("근거에 기반해 답하라.", encoding="utf-8")
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                headers={"x-ratelimit-remaining-tokens": "8123"},
                text="{invalid-json api_key=must-not-leak",
            )
        )
    )
    backend = HyperClovaXBackend(
        settings_factory(
            agent_backend="hcx",
            knowledge_dir=knowledge,
            system_prompt_path=prompt,
            hcx_api_url="https://clova.example/v3/chat-completions/HCX-007",
            hcx_api_key="test-secret",
            hcx_model="HCX-007",
        ),
        client=client,
    )

    with pytest.raises(InvalidBackendResponse, match="returned invalid JSON") as error:
        asyncio.run(backend.answer(question_id="Q", question="퇴직연금 제도 설명"))
    asyncio.run(client.aclose())
    assert "must-not-leak" not in str(error.value)


@pytest.mark.anyio
@pytest.mark.parametrize("api_style", ["clova-v3", "openai-v3"])
@pytest.mark.parametrize("first_failure", ["invalid_json", "missing_answer"])
async def test_hcx_failure_returns_502_and_retry_caches_only_valid_answer(
    tmp_path: Path, settings_factory: Any, api_style: str, first_failure: str,
) -> None:
    from app.main import create_app

    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    _write_test_corpus(knowledge, source_id="doc1", content="퇴직연금 제도 설명 근거")
    prompt = tmp_path / "system.md"
    prompt.write_text("근거에 기반해 답하라.", encoding="utf-8")
    settings = settings_factory(
        agent_backend="hcx", knowledge_dir=knowledge, system_prompt_path=prompt,
        hcx_api_url="https://clova.example/v1/chat/completions",
        hcx_api_key="test-secret", hcx_api_style=api_style, hcx_model="HCX-007",
    )
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            if first_failure == "invalid_json":
                return httpx.Response(200, text="{invalid-json api_key=must-not-leak")
            return httpx.Response(200, json={"private": "must-not-leak"})
        if api_style == "openai-v3":
            payload = {"choices": [{"finish_reason": "stop", "message": {
                "role": "assistant", "content": "근거 기반 답변",
            }}]}
        else:
            payload = {"status": {"code": "20000"}, "result": {
                "finishReason": "stop", "aiFilter": [{"score": "2", "result": "OK"}],
                "message": {"role": "assistant", "content": "근거 기반 답변"},
            }}
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
        backend = HyperClovaXBackend(settings, client=upstream)
        app = create_app(settings=settings, backend=backend)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver",
            ) as client:
                params = {"question_id": "Q-RETRY", "question": "퇴직연금 제도 설명"}
                first = await client.get("/answer", params=params)
                second = await client.get("/answer", params=params)
                third = await client.get("/answer", params=params)

    assert first.status_code == 502
    assert "must-not-leak" not in first.text
    assert "test-secret" not in first.text
    assert second.status_code == third.status_code == 200
    assert second.json() == third.json()
    assert second.json()["answer"] == "근거 기반 답변"
    assert calls == 2


def test_hcx_backend_does_not_call_model_when_evidence_is_missing(
    tmp_path: Path,
    settings_factory: Any,
) -> None:
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    _write_test_corpus(knowledge)
    prompt = tmp_path / "system.md"
    prompt.write_text("근거에 기반해 답하라.", encoding="utf-8")

    def must_not_call(_: httpx.Request) -> httpx.Response:
        raise AssertionError("HCX must not be called without evidence")

    client = httpx.AsyncClient(transport=httpx.MockTransport(must_not_call))
    backend = HyperClovaXBackend(
        settings_factory(
            agent_backend="hcx",
            knowledge_dir=knowledge,
            system_prompt_path=prompt,
            hcx_api_url="https://clova.example/v3/chat-completions/HCX-005",
            hcx_api_key="test-secret",
            hcx_model="HCX-005",
            hcx_thinking_effort="none",
        ),
        client=client,
    )
    output = asyncio.run(
        backend.answer(question_id="Q-NONE", question="완전히무관한천문학")
    )
    asyncio.run(client.aclose())
    assert "근거를 찾지 못했습니다" in output.answer
    trace = json.loads(str(output.trace_summary))
    assert trace["decision_status"] == "out_of_corpus"
    assert trace["hyperclova_x_called"] is False


def test_hcx_backend_does_not_call_model_when_explicit_scope_is_unsupported(
    tmp_path: Path,
    settings_factory: Any,
) -> None:
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    _write_test_corpus(
        knowledge,
        source_id="doc1",
        content="연금 계좌의 일반 규칙과 가입 절차",
    )
    prompt = tmp_path / "system.md"
    prompt.write_text("근거에 기반해 답하라.", encoding="utf-8")

    def must_not_call(_: httpx.Request) -> httpx.Response:
        raise AssertionError("HCX must not generalize evidence to an absent scope")

    client = httpx.AsyncClient(transport=httpx.MockTransport(must_not_call))
    backend = HyperClovaXBackend(
        settings_factory(
            agent_backend="hcx",
            knowledge_dir=knowledge,
            system_prompt_path=prompt,
            hcx_api_url="https://clova.example/v3/chat-completions/HCX-005",
            hcx_api_key="test-secret",
            hcx_model="HCX-005",
            hcx_thinking_effort="none",
        ),
        client=client,
    )
    output = asyncio.run(
        backend.answer(
            question_id="Q-SCOPE",
            question="화성 기지의 연금 계좌 규칙을 알려주세요",
        )
    )
    asyncio.run(client.aclose())
    assert "근거를 찾지 못했습니다" in output.answer
    trace = json.loads(str(output.trace_summary))
    assert trace["decision_status"] == "out_of_corpus"
    assert trace["hyperclova_x_called"] is False


def test_hcx_backend_does_not_call_model_for_incomplete_recommendation(
    tmp_path: Path,
    settings_factory: Any,
) -> None:
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    _write_test_corpus(
        knowledge,
        source_id="doc1",
        content="퇴직연금 상품 추천 시 위험과 투자기간을 확인해야 합니다.",
    )
    prompt = tmp_path / "system.md"
    prompt.write_text("근거에 기반해 답하라.", encoding="utf-8")

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: (_ for _ in ()).throw(
                AssertionError("HCX must not fill missing suitability conditions")
            )
        )
    )
    backend = HyperClovaXBackend(
        settings_factory(
            agent_backend="hcx",
            knowledge_dir=knowledge,
            system_prompt_path=prompt,
            hcx_api_url="https://clova.example/v3/chat-completions/HCX-005",
            hcx_api_key="test-secret",
            hcx_model="HCX-005",
            hcx_thinking_effort="none",
        ),
        client=client,
    )
    output = asyncio.run(
        backend.answer(question_id="Q-REC", question="좋은 연금 상품을 추천해 주세요")
    )
    asyncio.run(client.aclose())
    assert "계좌 유형" in output.answer
    trace = json.loads(str(output.trace_summary))
    assert trace["decision_status"] == "needs_conditions"
    assert trace["hyperclova_x_called"] is False


def test_hcx_backend_runs_implicit_comparator_and_skips_model_without_supported_axis(
    tmp_path: Path,
    settings_factory: Any,
) -> None:
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    _write_test_corpus(knowledge)
    facts = tmp_path / "product_evidence.json"
    first_code = _write_test_product_evidence(facts, knowledge / "manifest.json")
    second_code = "KR0000000001"
    prompt = tmp_path / "system.md"
    prompt.write_text("근거에 기반해 답하라.", encoding="utf-8")

    def handler(_: httpx.Request) -> httpx.Response:
        raise AssertionError("HCX must not be called without a comparable axis")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    backend = HyperClovaXBackend(
        settings_factory(
            agent_backend="hcx",
            knowledge_dir=knowledge,
            product_evidence_path=facts,
            system_prompt_path=prompt,
            hcx_api_url="https://clova.example/v3/chat-completions/HCX-005",
            hcx_api_key="test-secret",
            hcx_model="HCX-005",
            hcx_thinking_effort="none",
        ),
        client=client,
    )
    output = asyncio.run(
        backend.answer(
            question_id="Q-PARTIAL",
            question=f"{first_code}과 {second_code} 중 C-P 총보수가 더 낮은 쪽을 알려주세요",
        )
    )
    asyncio.run(client.aclose())
    assert "근거를 찾지 못했습니다" in output.answer
    trace = json.loads(str(output.trace_summary))
    assert trace["decision_status"] == "out_of_corpus"
    assert trace["hyperclova_x_called"] is False
    assert any(tool["name"] == "product_comparator" for tool in trace["tools"])


def test_hcx_rejects_failed_body_status_even_after_http_200() -> None:
    with pytest.raises(InvalidBackendResponse, match="status"):
        _parse_clova_response(
            {
                "status": {"code": "50000", "message": "internal detail"},
                "result": {
                    "finishReason": "stop",
                    "message": {"role": "assistant", "content": "사용 금지"},
                    "aiFilter": [{"score": "2", "result": "OK"}],
                },
            }
        )


def test_hcx_backend_blocks_complete_recommendation_without_product_index(
    tmp_path: Path,
    settings_factory: Any,
) -> None:
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    _write_test_corpus(
        knowledge,
        source_id="doc1",
        content="IRP 장기 투자의 위험과 유동성을 확인해야 합니다.",
    )
    prompt = tmp_path / "system.md"
    prompt.write_text("근거에 기반해 답하라.", encoding="utf-8")
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: (_ for _ in ()).throw(
                AssertionError("HCX must not recommend without a product evidence index")
            )
        )
    )
    backend = HyperClovaXBackend(
        settings_factory(
            agent_backend="hcx",
            knowledge_dir=knowledge,
            product_evidence_path=None,
            system_prompt_path=prompt,
            hcx_api_url="https://clova.example/v3/chat-completions/HCX-005",
            hcx_api_key="test-secret",
            hcx_model="HCX-005",
        ),
        client=client,
    )
    output = asyncio.run(
        backend.answer(
            question_id="Q-REC-NO-INDEX",
            question=(
                "IRP 계좌에서 20년 투자하고 손실 위험을 감수할 수 있습니다. "
                "중도인출 계획은 없고 노후자금 성장 목표의 상품을 추천해 주세요."
            ),
        )
    )
    asyncio.run(client.aclose())
    trace = json.loads(str(output.trace_summary))
    assert trace["decision_status"] == "out_of_corpus"
    assert trace["hyperclova_x_called"] is False


def test_hcx_backend_sends_only_prefiltered_candidates_for_complete_profile(
    settings_factory: Any,
) -> None:
    seen_body: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen_body.update(body)
        content = body["messages"][1]["content"]
        assert "[SUITABILITY PREFILTER]" in content
        assert "universal product ranking" in content
        assert "product_code=KR5153450209" in content
        assert "citation_id=product:KR5153450209:class:page=" in content
        return httpx.Response(
            200,
            json={
                "status": {"code": "20000", "message": "OK"},
                "result": {
                    "message": {
                        "role": "assistant",
                        "content": "제공 자료에서 조건과 일치하는 후보군을 확인했습니다.",
                    },
                    "finishReason": "stop",
                    "aiFilter": [{"name": "insult", "score": "2", "result": "OK"}],
                },
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    backend = HyperClovaXBackend(
        settings_factory(
            agent_backend="hcx",
            knowledge_dir=PROJECT_ROOT / "data/knowledge",
            product_evidence_path=PROJECT_ROOT
            / "data/structured/product_evidence.json",
            source_governance_path=PROJECT_ROOT
            / "data/structured/general_source_governance.json",
            system_prompt_path=PROJECT_ROOT / "agents/prompts/system.md",
            hcx_api_url="https://clova.example/v3/chat-completions/HCX-005",
            hcx_api_key="test-secret",
            hcx_model="HCX-005",
        ),
        client=client,
    )
    output = asyncio.run(
        backend.answer(
            question_id="Q-REC-COMPLETE",
            question=(
                "IRP 계좌에서 20년 투자하고 손실 위험을 감수할 수 있습니다. "
                "중도인출 계획은 없고 노후자금 성장 목표의 상품을 추천해 주세요."
            ),
        )
    )
    asyncio.run(client.aclose())
    assert seen_body
    trace = json.loads(str(output.trace_summary))
    suitability = next(
        item for item in trace["tools"] if item["name"] == "suitability_prefilter"
    )
    assert suitability["status"] == "candidate_shortlist"
    assert suitability["shortlisted_products"] == 3
    assert trace["decision_status"] == "answerable"


def test_hcx_backend_uses_suitability_as_single_source_for_missing_conditions(
    settings_factory: Any,
) -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: (_ for _ in ()).throw(
                AssertionError("HCX must not run before withdrawal eligibility is known")
            )
        )
    )
    backend = HyperClovaXBackend(
        settings_factory(
            agent_backend="hcx",
            knowledge_dir=PROJECT_ROOT / "data/knowledge",
            product_evidence_path=PROJECT_ROOT
            / "data/structured/product_evidence.json",
            source_governance_path=PROJECT_ROOT
            / "data/structured/general_source_governance.json",
            system_prompt_path=PROJECT_ROOT / "agents/prompts/system.md",
            hcx_api_url="https://clova.example/v3/chat-completions/HCX-005",
            hcx_api_key="test-secret",
            hcx_model="HCX-005",
        ),
        client=client,
    )
    output = asyncio.run(
        backend.answer(
            question_id="Q-REC-WITHDRAWAL",
            question=(
                "IRP 계좌에서 20년 장기 투자로 적극적 성장을 원하지만 "
                "1년 안에 긴급자금으로 중도인출할 계획입니다. 상품을 추천해 주세요."
            ),
        )
    )
    asyncio.run(client.aclose())

    trace = json.loads(str(output.trace_summary))
    assessment = next(
        item for item in trace["tools"] if item["name"] == "evidence_assessor"
    )
    assert trace["decision_status"] == "needs_conditions"
    assert trace["hyperclova_x_called"] is False
    assert assessment["missing_conditions"] == [
        "필요 인출 시점과 법정 중도인출 사유 해당 여부"
    ]
