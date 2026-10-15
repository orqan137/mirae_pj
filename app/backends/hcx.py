from __future__ import annotations

import json
import hashlib
import re
import time
import uuid
from typing import Any

import httpx

from app.answer_validation import (
    attach_unambiguous_evidence_citations,
    drop_unsupported_lines,
    render_grounded_extractive_fallback,
    render_validation_failure,
    validate_answer,
)
from app.calculations import PensionTaxCreditCalculation, calculate_pension_tax_credit
from app.config import Settings
from app.errors import BackendUnavailable, InvalidBackendResponse
from app.evidence import (
    EvidenceAssessment,
    assess_evidence,
    render_limitation_answer,
    render_limitation_trace,
    with_context_budget_failure,
)
from app.evidence_rendering import render_public_evidence_result
from app.models import BackendOutput
from app.planning import QuestionPlan, plan_question
from app.product_identity import ProductIdentityResolver, ProductResolution
from app.product_comparison import ProductComparisonResult, compare_product_facts
from app.prompts import build_user_prompt, load_system_prompt
from app.product_facts import (
    ProductEvidenceIndex,
    explicit_requested_axes,
    requested_axes,
    requested_product_codes,
)
from app.retrieval import (
    LocalRetriever,
    evidence_citation_id,
    render_context,
    retrieve_for_queries,
)
from app.routing import QuestionRoute, classify_question
from app.suitability import SuitabilityAssessment, assess_suitability


HCX_TELEMETRY_VERSION = "hcx-safe-usage-telemetry-v1"
_ADDITIONAL_REQUEST_RE = re.compile(
    r"\?|설명|알려|비교|추천|차이|어떻게|왜|무엇|뭐|가능|조건|한도|세율|공제율|"
    r"절차|해\s*줘|해\s*주세요|인가요|나요|까요|습니까",
    flags=re.IGNORECASE,
)
_NON_CALCULATION_REQUEST_RE = re.compile(
    r"설명|알려|비교|추천|차이|어떻게|왜|무엇|뭐|가능|조건|한도|세율|공제율|절차",
    flags=re.IGNORECASE,
)


def _is_calculation_only_plan(
    plan: QuestionPlan,
    calculation: PensionTaxCreditCalculation | None,
) -> bool:
    """Return true only when non-calculation clauses are declarative inputs."""

    if calculation is None or not calculation.performed:
        return False
    calculation_steps = 0
    for subquestion in plan.subquestions:
        if _NON_CALCULATION_REQUEST_RE.search(subquestion.text):
            return False
        if subquestion.route.requires_calculation:
            calculation_steps += 1
            continue
        if _ADDITIONAL_REQUEST_RE.search(subquestion.text):
            return False
    return calculation_steps == 1


def _prepend_priority_context(*, priority: str, context: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    if len(priority) >= max_chars:
        return priority[:max_chars]
    remaining = max_chars - len(priority)
    if context and remaining > 2:
        return f"{priority}\n\n{context[: remaining - 2]}"
    return priority


def _render_unsupported_scope_trace(
    *,
    route: QuestionRoute,
    assessment: EvidenceAssessment,
    reason_codes: tuple[str, ...],
) -> str:
    """Describe a capability-boundary stop without implying tool execution.

    Capability checks run before retrieval, product processing, deterministic
    calculation, and model generation. Keep the public trace explicit about
    that short-circuit while exposing only stable reason codes, never request
    data or private reasoning.
    """

    return json.dumps(
        {
            "summary": (
                "지원하지 않는 비공개 조회 또는 내부 처리 요청을 범위 가드가 "
                "차단해 외부 도구 호출 없이 한계 응답을 생성함"
            ),
            "tools": [
                {
                    "name": "capability_scope_guard",
                    "status": "blocked",
                    "reason_codes": list(reason_codes),
                    "skipped_tools": [
                        "local_retriever",
                        "product_evidence",
                        "product_comparator",
                        "suitability_assessor",
                        "pension_tax_credit_calculator",
                        "hyperclova_x",
                    ],
                },
                assessment.trace_fields(),
            ],
            "route": list(route.labels),
            "decision_status": assessment.status,
            "hyperclova_x_called": False,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _validate_status(payload: dict[str, Any], *, required: bool) -> None:
    if payload.get("error") is not None:
        raise InvalidBackendResponse("HyperCLOVA X returned an error payload")
    status = payload.get("status")
    if status is None and not required:
        return
    if not isinstance(status, dict) or str(status.get("code")) != "20000":
        raise InvalidBackendResponse("HyperCLOVA X response status is not successful")


def _validate_ai_filters(value: Any) -> None:
    if not isinstance(value, list) or not value:
        raise InvalidBackendResponse("HyperCLOVA X response has no AI filter result")
    for item in value:
        if not isinstance(item, dict):
            raise InvalidBackendResponse("HyperCLOVA X AI filter result is malformed")
        if item.get("result") not in {None, "OK"}:
            raise InvalidBackendResponse("HyperCLOVA X AI filter failed")
        if str(item.get("score")) not in {"1", "2"}:
            raise InvalidBackendResponse("HyperCLOVA X AI filter rejected the answer")


def _parse_clova_response(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise InvalidBackendResponse("HyperCLOVA X response must be an object")
    _validate_status(payload, required=True)
    result = payload.get("result")
    if not isinstance(result, dict):
        raise InvalidBackendResponse("HyperCLOVA X response has no result object")
    if result.get("finishReason") != "stop":
        raise InvalidBackendResponse("HyperCLOVA X answer did not finish normally")
    _validate_ai_filters(result.get("aiFilter"))
    message = result.get("message")
    if not isinstance(message, dict) or message.get("role") != "assistant":
        raise InvalidBackendResponse("HyperCLOVA X response has no assistant message")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise InvalidBackendResponse("HyperCLOVA X response has no answer content")
    return content


def _parse_openai_response(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise InvalidBackendResponse("HyperCLOVA X response must be an object")
    _validate_status(payload, required=False)
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise InvalidBackendResponse("HyperCLOVA X response has no choices")
    choice = choices[0]
    if choice.get("finish_reason") != "stop":
        raise InvalidBackendResponse("HyperCLOVA X answer did not finish normally")
    message = choice.get("message")
    if not isinstance(message, dict) or message.get("role") != "assistant":
        raise InvalidBackendResponse("HyperCLOVA X response has no assistant message")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise InvalidBackendResponse("HyperCLOVA X response has no answer content")
    return content


def _nonnegative_int(value: Any) -> int | None:
    """Return a safe counter value without accepting booleans or loose strings."""

    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _extract_hcx_telemetry(
    payload: Any,
    headers: httpx.Headers,
    *,
    openai_format: bool,
) -> dict[str, Any]:
    """Extract only non-secret usage counters from an HCX response.

    Prompt/answer content, request identifiers, arbitrary headers, and credentials
    are deliberately excluded from this structure.
    """

    usage_source: Any = None
    if isinstance(payload, dict):
        if openai_format:
            usage_source = payload.get("usage")
        else:
            result = payload.get("result")
            if isinstance(result, dict):
                usage_source = result.get("usage")

    usage: dict[str, int] = {}
    if isinstance(usage_source, dict):
        source_keys = (
            ("prompt_tokens", "prompt_tokens" if openai_format else "promptTokens"),
            (
                "completion_tokens",
                "completion_tokens" if openai_format else "completionTokens",
            ),
            ("total_tokens", "total_tokens" if openai_format else "totalTokens"),
        )
        for output_key, source_key in source_keys:
            value = _nonnegative_int(usage_source.get(source_key))
            if value is not None:
                usage[output_key] = value

    rate_limit: dict[str, int] = {}
    for output_key, header_name in (
        ("remaining_requests", "x-ratelimit-remaining-requests"),
        ("remaining_tokens", "x-ratelimit-remaining-tokens"),
    ):
        raw_value = headers.get(header_name)
        if raw_value is None or not raw_value.isdecimal():
            continue
        value = _nonnegative_int(int(raw_value))
        if value is not None:
            rate_limit[output_key] = value

    return {
        "version": HCX_TELEMETRY_VERSION,
        "usage_observed": bool(usage),
        "usage": usage,
        "rate_limit_observed": bool(rate_limit),
        "rate_limit": rate_limit,
    }


class HyperClovaXBackend:
    name = "hcx"

    def __init__(self, settings: Settings, *, client: httpx.AsyncClient | None = None):
        self._settings = settings
        self._client = client
        self._retriever = LocalRetriever.from_directory(
            settings.knowledge_dir,
            source_governance_path=settings.source_governance_path,
        )
        self._system_prompt = load_system_prompt(settings.system_prompt_path)
        self._product_evidence: ProductEvidenceIndex | None = None
        self._product_identity: ProductIdentityResolver | None = None
        if settings.product_evidence_path is not None:
            manifest = settings.knowledge_dir / "manifest.json"
            digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
            self._product_evidence = ProductEvidenceIndex.from_path(
                settings.product_evidence_path,
                expected_manifest_sha256=digest,
                expected_record_count=100,
            )
            self._product_identity = ProductIdentityResolver.from_index(
                self._product_evidence
            )

    def _url(self) -> str:
        if "{model}" in self._settings.hcx_api_url:
            return self._settings.hcx_api_url.format(model=self._settings.hcx_model)
        return self._settings.hcx_api_url

    def _uses_openai_format(self) -> bool:
        return self._settings.hcx_api_style == "openai-v3"

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            **(self._settings.hcx_extra_headers or {}),
        }
        credential = self._settings.hcx_api_key
        if self._settings.hcx_api_key_prefix:
            credential = f"{self._settings.hcx_api_key_prefix} {credential}"
        headers[self._settings.hcx_api_key_header] = credential
        if self._settings.hcx_request_id_header:
            headers[self._settings.hcx_request_id_header] = str(uuid.uuid4())
        return headers

    def _body(
        self,
        *,
        question_id: str,
        question: str,
        context: str,
        route: QuestionRoute,
        evidence_status: str,
        missing_evidence: tuple[str, ...],
        scope_warnings: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        messages = [
            {"role": "system", "content": self._system_prompt},
            {
                "role": "user",
                "content": build_user_prompt(
                    question_id=question_id,
                    question=question,
                    context=context,
                    route_labels=route.labels,
                    needs_conditions=evidence_status == "needs_conditions",
                    requires_calculation=route.requires_calculation,
                    evidence_status=evidence_status,
                    missing_evidence=missing_evidence,
                    scope_warnings=scope_warnings,
                ),
            },
        ]
        if self._uses_openai_format():
            body: dict[str, Any] = {
                "messages": messages,
                "temperature": self._settings.hcx_temperature,
                "max_completion_tokens": self._settings.hcx_max_tokens,
                "repetition_penalty": 1.1,
            }
            if self._settings.hcx_thinking_effort != "none":
                body["reasoning_effort"] = self._settings.hcx_thinking_effort
            if self._settings.hcx_model:
                body["model"] = self._settings.hcx_model
            return body
        body = {
            "messages": messages,
            "topP": 0.8,
            "topK": 0,
            "maxCompletionTokens": self._settings.hcx_max_tokens,
            "temperature": self._settings.hcx_temperature,
            "repetitionPenalty": 1.1,
            "includeAiFilters": True,
        }
        if self._settings.hcx_thinking_effort != "none":
            body["thinking"] = {"effort": self._settings.hcx_thinking_effort}
        return body

    async def answer(self, *, question_id: str, question: str) -> BackendOutput:
        route = classify_question(question)
        # Capability boundaries must be enforced before any data access or
        # expensive local/model work. The preliminary plan is deterministic
        # and is also the evidence assessor's auditable input for the stop.
        plan = plan_question(question, route)
        if plan.unsupported_scope_reasons:
            assessment = assess_evidence(
                plan=plan,
                route=route,
                documents=[],
                structured_axes={},
                structured_identities={},
                calculation=None,
            )
            return BackendOutput(
                answer=render_limitation_answer(assessment),
                retrieved_context="",
                trace_summary=_render_unsupported_scope_trace(
                    route=route,
                    assessment=assessment,
                    reason_codes=plan.unsupported_scope_reasons,
                ),
            )

        resolution: ProductResolution | None = (
            self._product_identity.resolve(question)
            if self._product_identity is not None
            else None
        )
        resolved_product_codes = (
            resolution.product_codes if resolution is not None else ()
        )
        suitability: SuitabilityAssessment | None = None
        if "conditional_recommendation" in route.labels:
            suitability = assess_suitability(
                question=question,
                index=self._product_evidence,
                requested_product_codes=resolved_product_codes,
            )
        planned_product_codes = resolved_product_codes or (
            suitability.candidate_codes if suitability is not None else ()
        )
        plan = plan_question(
            question,
            route,
            resolved_product_codes=planned_product_codes,
        )
        calculation: PensionTaxCreditCalculation | None = None
        if route.requires_calculation:
            calculation = calculate_pension_tax_credit(question)
        documents = retrieve_for_queries(
            self._retriever,
            tuple(
                (subquestion.query_text, frozenset(subquestion.route.source_groups))
                for subquestion in plan.subquestions
            ),
            top_k=self._settings.retrieval_top_k,
            resolved_product_codes=planned_product_codes,
        )
        context = render_context(
            documents,
            max_chars=self._settings.max_context_chars,
        )
        product_codes = planned_product_codes or requested_product_codes(question)
        product_axes = requested_axes(question)
        if "conditional_recommendation" in route.labels:
            product_axes = explicit_requested_axes(question) or (
                "identity",
                "category",
                "risk",
                "class",
            )
        structured_context = ""
        comparison_context = ""
        calculation_context = ""
        structured_axes: dict[str, frozenset[str]] = {}
        structured_identities: dict[str, str] = {}
        if self._product_evidence is not None and product_codes:
            structured_axes = self._product_evidence.evidence_coverage(
                product_codes=product_codes,
                axes=product_axes,
            )
            structured_identities = self._product_evidence.canonical_identities(
                product_codes=product_codes,
            )
            suitability_context = (
                suitability.render_context() if suitability is not None else ""
            )
            remaining_for_product = max(
                self._settings.max_context_chars
                - len(suitability_context)
                - (2 if suitability_context else 0),
                0,
            )
            product_context = self._product_evidence.render_context(
                product_codes=product_codes,
                axes=product_axes,
                # Product-code comparisons need two records across as many as
                # six fixed axes. Let structured evidence use the whole context
                # budget first; unstructured retrieval fills only the remainder.
                max_chars=remaining_for_product,
            )
            structured_context = "\n\n".join(
                item for item in (suitability_context, product_context) if item
            )
            if structured_context:
                remaining = max(
                    self._settings.max_context_chars - len(structured_context) - 2,
                    0,
                )
                context = (
                    f"{structured_context}\n\n{context[:remaining]}"
                    if remaining and context
                    else structured_context
                )
        if calculation is not None:
            calculation_context = calculation.render_context()
            context = _prepend_priority_context(
                priority=calculation_context,
                context=context,
                max_chars=self._settings.max_context_chars,
            )
        comparison: ProductComparisonResult | None = None
        if (
            self._product_evidence is not None
            and "product_comparison" in route.labels
            and len(product_codes) >= 2
        ):
            comparison = compare_product_facts(
                index=self._product_evidence,
                product_codes=product_codes,
                axes=product_axes,
                question=question,
            )
            comparison_context = comparison.render_context(
                max_chars=self._settings.max_context_chars
            )
            if comparison_context:
                context = _prepend_priority_context(
                    priority=comparison_context,
                    context=context,
                    max_chars=self._settings.max_context_chars,
                )
        identity_conditions = (
            resolution.required_conditions(
                comparison="product_comparison" in route.labels
            )
            if resolution is not None
            else ()
        )
        assessment = assess_evidence(
            plan=plan,
            route=route,
            documents=documents,
            structured_axes=structured_axes,
            structured_identities=structured_identities,
            calculation=calculation,
            identity_conditions=identity_conditions,
            comparison=comparison,
            suitability=suitability,
        )
        public_render = render_public_evidence_result(
            question=question,
            documents=documents,
            assessment=assessment,
            structured_context=structured_context,
            comparison_context=comparison_context,
            calculation_context=calculation_context,
            stated_only_axes=(
                comparison.stated_only_axes if comparison is not None else frozenset()
            ),
            max_chars=self._settings.max_context_chars,
        )
        public_context = public_render.context
        if assessment.should_generate and not public_render.complete:
            assessment = with_context_budget_failure(
                assessment,
                missing_sections=public_render.missing_sections,
            )
        if not assessment.should_generate:
            return BackendOutput(
                answer=render_limitation_answer(
                    assessment,
                    settled=(
                        calculation.render_settled_rate()
                        if calculation is not None
                        else ""
                    ),
                ),
                retrieved_context=public_context,
                trace_summary=render_limitation_trace(
                    assessment=assessment,
                    route=route,
                    document_count=len(documents),
                    calculation=calculation,
                    identity_trace=(
                        resolution.trace_fields() if resolution is not None else None
                    ),
                    comparison_trace=(
                        comparison.trace_fields() if comparison is not None else None
                    ),
                    suitability_trace=(
                        suitability.trace_fields() if suitability is not None else None
                    ),
                    context_render_trace=public_render.trace_fields(),
                ),
            )
        if _is_calculation_only_plan(plan, calculation) and calculation is not None:
            deterministic_answer = calculation.render_answer()
            validation = validate_answer(
                answer=deterministic_answer,
                question=question,
                evidence_context=public_context,
                route=route,
                assessment=assessment,
                product_resolution=resolution,
                suitability=suitability,
            )
            if validation.valid:
                trace = {
                    "summary": "검증된 결정론 계산 결과로 답변해 모델 호출을 생략함",
                    "tools": [
                        {
                            "name": "local_retriever",
                            "status": "ok",
                            "documents": len(documents),
                            "structured_product_evidence": bool(structured_context),
                        },
                        calculation.trace_fields(),
                        public_render.trace_fields(),
                        assessment.trace_fields(),
                        validation.trace_fields(),
                    ],
                    "grounded": bool(context),
                    "route": list(route.labels),
                    "needs_conditions": False,
                    "decision_status": assessment.status,
                    "hyperclova_x_called": False,
                    "answer_validation": "passed_deterministic_calculation",
                    "elapsed_ms": 0,
                }
                return BackendOutput(
                    answer=deterministic_answer,
                    retrieved_context=public_context,
                    trace_summary=json.dumps(
                        trace,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                )
        timeout = httpx.Timeout(
            self._settings.upstream_timeout_seconds,
            connect=min(10.0, self._settings.upstream_timeout_seconds),
        )
        own_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=timeout)
        started = time.monotonic()
        try:
            response = await client.post(
                self._url(),
                headers=self._headers(),
                json=self._body(
                    question_id=question_id,
                    question=question,
                    # The model may see only the evidence that survived the
                    # same gate and redaction used for the public response.
                    # Broad retrieval candidates remain observable to the
                    # assessor but cannot influence generation implicitly.
                    context=public_context,
                    route=route,
                    evidence_status=assessment.status,
                    missing_evidence=assessment.missing_requirements,
                    scope_warnings=assessment.scope_warnings,
                ),
                timeout=timeout,
            )
            response.raise_for_status()
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise BackendUnavailable("HyperCLOVA X is unreachable or timed out") from exc
        except httpx.HTTPStatusError as exc:
            raise BackendUnavailable(
                f"HyperCLOVA X returned HTTP {exc.response.status_code}"
            ) from exc
        finally:
            if own_client:
                await client.aclose()

        # Invalid generations are service failures, not completed answers.
        # Let the HTTP layer return its sanitized 502 without caching a failure;
        # the evaluator may retry the same request up to twice.
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise InvalidBackendResponse("HyperCLOVA X returned invalid JSON") from exc
        if self._uses_openai_format():
            answer = _parse_openai_response(payload)
        else:
            answer = _parse_clova_response(payload)
        hcx_telemetry = _extract_hcx_telemetry(
            payload,
            response.headers,
            openai_format=self._uses_openai_format(),
        )

        elapsed_ms = round((time.monotonic() - started) * 1000)
        validation = validate_answer(
            answer=answer,
            question=question,
            # Validate against the exact assessment-selected evidence that is
            # returned to the evaluator, not the broader model-input candidates.
            evidence_context=public_context,
            route=route,
            assessment=assessment,
            product_resolution=resolution,
            suitability=suitability,
        )
        # One uncited bullet used to discard an otherwise well-grounded
        # answer. Drop just the unsupported sentences and re-validate; the
        # result is accepted only if it passes the same checks unchanged.
        repair_mode: str | None = None
        if not validation.valid:
            cited = attach_unambiguous_evidence_citations(
                answer,
                public_context,
                validation,
            )
            if cited is not None:
                answer = cited
                validation = validate_answer(
                    answer=answer,
                    question=question,
                    evidence_context=public_context,
                    route=route,
                    assessment=assessment,
                    product_resolution=resolution,
                    suitability=suitability,
                )
                if validation.valid:
                    repair_mode = "citation_repair"
        if not validation.valid:
            trimmed = drop_unsupported_lines(answer, validation)
            if trimmed is not None:
                revalidated = validate_answer(
                    answer=trimmed,
                    question=question,
                    evidence_context=public_context,
                    route=route,
                    assessment=assessment,
                    product_resolution=resolution,
                    suitability=suitability,
                )
                if revalidated.valid:
                    answer = trimmed
                    validation = revalidated
                    repair_mode = (
                        "citation_repair_and_trim"
                        if cited is not None
                        else "trim"
                    )
        if (
            not validation.valid
            and _is_calculation_only_plan(plan, calculation)
            and calculation is not None
        ):
            deterministic_answer = calculation.render_answer()
            revalidated = validate_answer(
                answer=deterministic_answer,
                question=question,
                evidence_context=public_context,
                route=route,
                assessment=assessment,
                product_resolution=resolution,
                suitability=suitability,
            )
            if revalidated.valid:
                answer = deterministic_answer
                validation = revalidated
                repair_mode = "deterministic_calculation_fallback"
        if not validation.valid:
            grounded_fallback = render_grounded_extractive_fallback(
                question=question,
                evidence_context=public_context,
                assessment=assessment,
                route=route,
                document_titles={
                    evidence_citation_id(document): document.title
                    for document in documents
                    if document.title
                },
            )
            if grounded_fallback is not None:
                revalidated = validate_answer(
                    answer=grounded_fallback,
                    question=question,
                    evidence_context=public_context,
                    route=route,
                    assessment=assessment,
                    product_resolution=resolution,
                    suitability=suitability,
                )
                if revalidated.valid:
                    answer = grounded_fallback
                    validation = revalidated
                    repair_mode = "grounded_extractive_fallback"

        trace = {
            "summary": (
                "HCX 초안이 근거 검증을 통과하지 못해 검증된 결정론 계산 답변으로 교체함"
                if repair_mode == "deterministic_calculation_fallback"
                else "HCX 초안이 근거 검증을 통과하지 못해 검증된 근거 발췌 답변으로 교체함"
                if repair_mode == "grounded_extractive_fallback"
                else "로컬 문서를 검색하고 HyperCLOVA X로 생성한 답변을 근거 검증함"
                if validation.valid
                else "HyperCLOVA X 초안의 미지원 주장을 검증기가 차단하고 한계 답변으로 교체함"
            ),
            "tools": [
                {
                    "name": "local_retriever",
                    "status": "ok",
                    "documents": len(documents),
                    "structured_product_evidence": bool(structured_context),
                },
                *([resolution.trace_fields()] if resolution is not None else []),
                *([comparison.trace_fields()] if comparison is not None else []),
                *([suitability.trace_fields()] if suitability is not None else []),
                *([calculation.trace_fields()] if calculation is not None else []),
                public_render.trace_fields(),
                assessment.trace_fields(),
                {
                    "name": "hyperclova_x",
                    "status": "ok",
                    "telemetry": hcx_telemetry,
                },
                validation.trace_fields(),
            ],
            "grounded": bool(context),
            "route": list(route.labels),
            "needs_conditions": assessment.status == "needs_conditions",
            "decision_status": assessment.status,
            "hyperclova_x_called": True,
            "answer_validation": (
                f"passed_after_{repair_mode}"
                if repair_mode is not None
                else "passed"
                if validation.valid
                else "blocked"
            ),
            "elapsed_ms": elapsed_ms,
        }
        if self._settings.hcx_model:
            trace["model"] = self._settings.hcx_model
        return BackendOutput(
            answer=answer if validation.valid else render_validation_failure(validation),
            retrieved_context=public_context,
            trace_summary=json.dumps(
                trace,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )
