from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Mapping

from app.calculations import CALCULATOR_VERSION, PensionTaxCreditCalculation
from app.planning import EvidenceRequirement, QuestionPlan
from app.product_comparison import ProductComparisonResult
from app.retrieval import RetrievedDocument
from app.routing import QuestionRoute, normalize_question_text
from app.source_governance import assess_source_applicability

if TYPE_CHECKING:
    from app.suitability import SuitabilityAssessment


EVIDENCE_ASSESSOR_VERSION = "pension-evidence-assessor-v12"

_GENERAL_TERMS: dict[str, tuple[str, ...]] = {
    "system": (
        "연금계좌",
        "연금저축",
        "퇴직연금",
        "개인연금",
        "디폴트옵션",
        "db",
        "dc",
        "irp",
    ),
    "tax": (
        "세액공제",
        "소득공제",
        "세금",
        "과세",
        "세율",
        "연금소득세",
        "퇴직소득세",
        "금융소득",
        "연금소득",
        "기타소득",
        "과세이연",
    ),
    "procedure": (
        "중도인출",
        "해지",
        "이전",
        "전환",
        "가입",
        "신청",
        "서류",
        "절차",
        "청구",
        "인출",
        "취소",
        "변경",
        "매매",
        "거래",
        "수령",
    ),
}
_TOPIC_TERM_ALIASES: dict[str, tuple[str, ...]] = {
    "연금개시": ("연금개시", "연금수령"),
}


@dataclass(frozen=True, slots=True)
class EvidenceCoverage:
    requirement_id: str
    label: str
    status: str
    evidence_refs: tuple[str, ...]
    subquestion_id: str = "sq1"
    minimum_evidence_count: int = 1


@dataclass(frozen=True, slots=True)
class EvidenceConflict:
    conflict_id: str
    label: str
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EvidenceAssessment:
    version: str
    status: str
    coverage: tuple[EvidenceCoverage, ...]
    missing_conditions: tuple[str, ...]
    conflicts: tuple[EvidenceConflict, ...] = ()
    scope_warnings: tuple[str, ...] = ()
    scope_exclusions: tuple[str, ...] = ()
    context_budget_missing: tuple[str, ...] = ()
    planned_tools: tuple[str, ...] = ()

    @property
    def missing_requirements(self) -> tuple[str, ...]:
        return tuple(item.label for item in self.coverage if item.status != "supported")

    @property
    def should_generate(self) -> bool:
        return self.status in {"answerable", "partially_answerable"}

    def prompt_fields(self) -> tuple[str, tuple[str, ...]]:
        return self.status, self.missing_requirements

    def trace_fields(self) -> dict[str, object]:
        supported = sum(item.status == "supported" for item in self.coverage)
        conflicting = sum(item.status == "conflicting" for item in self.coverage)
        subquestion_ids = tuple(
            dict.fromkeys(item.subquestion_id for item in self.coverage)
        )
        subquestion_coverage = [
            {
                "subquestion_id": subquestion_id,
                "requirements": sum(
                    item.subquestion_id == subquestion_id for item in self.coverage
                ),
                "supported": sum(
                    item.subquestion_id == subquestion_id and item.status == "supported"
                    for item in self.coverage
                ),
            }
            for subquestion_id in subquestion_ids
        ]
        return {
            "name": "evidence_assessor",
            "version": self.version,
            "status": self.status,
            "requirements": len(self.coverage),
            "supported": supported,
            "missing": len(self.coverage) - supported,
            "conflicting": conflicting,
            "conflict_ids": [item.conflict_id for item in self.conflicts],
            "missing_conditions": list(self.missing_conditions),
            "scope_warning_count": len(self.scope_warnings),
            "scope_exclusion_count": len(self.scope_exclusions),
            "scope_exclusions": list(self.scope_exclusions),
            "context_budget_missing": list(self.context_budget_missing),
            "planned_tools": list(self.planned_tools),
            "subquestion_count": len(subquestion_ids),
            "subquestion_coverage": subquestion_coverage,
        }


def with_context_budget_failure(
    assessment: EvidenceAssessment,
    *,
    missing_sections: tuple[str, ...],
) -> EvidenceAssessment:
    """Fail closed when approved evidence cannot fit the public context."""

    return replace(
        assessment,
        status="context_budget_exceeded",
        context_budget_missing=missing_sections,
    )


def _normalize(value: str) -> str:
    return normalize_question_text(value)


def _document_ref(document: RetrievedDocument) -> str:
    locator = f":{document.locator}" if document.locator else ""
    return f"{document.source_id}{locator}:chunk={document.index}"


def _general_support(
    requirement: EvidenceRequirement,
    documents: tuple[RetrievedDocument, ...],
    *,
    plan: QuestionPlan,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    candidates = tuple(item for item in documents if item.source_group == "general")
    if not candidates:
        return (), (), ()
    query_index = (
        int(requirement.subquestion_id[2:])
        if requirement.subquestion_id.startswith("sq")
        and requirement.subquestion_id[2:].isdigit()
        else None
    )
    query_candidates = tuple(
        item
        for item in candidates
        if query_index is not None and query_index in item.matched_query_indexes
    )
    if query_candidates:
        candidates = query_candidates
    terms = _GENERAL_TERMS.get(requirement.route_label or "", ())
    matched = (
        tuple(
            item
            for item in candidates
            if any(term in _normalize(item.text) for term in terms)
        )
        if terms
        else candidates
    )
    if requirement.topic_terms:
        matched = tuple(
            item
            for item in matched
            if all(
                any(
                    alias in _normalize(item.text)
                    for alias in _TOPIC_TERM_ALIASES.get(term, (term,))
                )
                for term in requirement.topic_terms
            )
        )
    if requirement.required_terms:
        combined = "\n".join(_normalize(item.text) for item in matched)
        if any(term not in combined for term in requirement.required_terms):
            return (), (), ()
    applicable: list[RetrievedDocument] = []
    warnings: list[str] = []
    exclusions: list[str] = []
    subquestion_scope = next(
        (
            subquestion.scope
            for subquestion in plan.subquestions
            if subquestion.subquestion_id == requirement.subquestion_id
        ),
        plan.scope,
    )
    applicability_question = requirement.query_text or plan.question
    for item in matched:
        if item.governance is None:
            applicable.append(item)
            continue
        decision = assess_source_applicability(
            record=item.governance,
            scope=subquestion_scope,
            question=applicability_question,
        )
        if decision.applicable:
            applicable.append(item)
            if decision.warning:
                warnings.append(f"{item.source_id}:{decision.warning}")
        else:
            exclusions.append(f"{item.source_id}:{decision.reason}")
    return (
        tuple(_document_ref(item) for item in applicable),
        tuple(dict.fromkeys(warnings)),
        tuple(dict.fromkeys(exclusions)),
    )


def _product_sources(
    documents: tuple[RetrievedDocument, ...],
) -> dict[str, list[RetrievedDocument]]:
    by_identity: dict[str, list[RetrievedDocument]] = {}
    for document in documents:
        if document.source_group != "product":
            continue
        identity = document.canonical_source_id or document.source_id
        by_identity.setdefault(identity, []).append(document)
    return by_identity


def _product_support(
    requirement: EvidenceRequirement,
    documents: tuple[RetrievedDocument, ...],
    structured_axes: Mapping[str, frozenset[str]],
    comparable_axes: frozenset[str],
) -> tuple[str, ...]:
    product_documents = tuple(item for item in documents if item.source_group == "product")
    if requirement.product_code is not None:
        code = requirement.product_code
        matching = tuple(
            item
            for item in product_documents
            if item.source_id == code or code in item.product_aliases
        )
        if requirement.kind == "product_identity":
            refs = tuple(_document_ref(item) for item in matching)
            if code in structured_axes:
                refs += (f"structured:{code}:identity",)
            return tuple(dict.fromkeys(refs))
        assert requirement.axis is not None
        if requirement.kind == "product_comparison_axis":
            return (
                (f"comparison:{requirement.axis}",)
                if requirement.axis in comparable_axes
                else ()
            )
        refs = tuple(
            _document_ref(item) for item in matching if requirement.axis in item.axes
        )
        if requirement.axis in structured_axes.get(code, frozenset()):
            refs += (f"structured:{code}:{requirement.axis}",)
        return tuple(dict.fromkeys(refs))

    by_identity = _product_sources(documents)
    if requirement.kind == "product_identity":
        if requirement.explicit_identity_required:
            return ()
        refs = tuple(
            _document_ref(items[0])
            for _, items in sorted(by_identity.items())
            if items
        )
        return refs if len(refs) >= requirement.minimum_count else ()

    assert requirement.axis is not None
    if requirement.kind == "product_comparison_axis":
        return (
            (f"comparison:{requirement.axis}",)
            if requirement.axis in comparable_axes
            else ()
        )
    refs_by_identity: list[str] = []
    for _, items in sorted(by_identity.items()):
        supporting = next((item for item in items if requirement.axis in item.axes), None)
        if supporting is not None:
            refs_by_identity.append(_document_ref(supporting))
    return (
        tuple(refs_by_identity)
        if len(refs_by_identity) >= requirement.minimum_count
        else ()
    )


def assess_evidence(
    *,
    plan: QuestionPlan,
    route: QuestionRoute,
    documents: list[RetrievedDocument],
    structured_axes: Mapping[str, frozenset[str]],
    structured_identities: Mapping[str, str],
    calculation: PensionTaxCreditCalculation | None,
    identity_conditions: tuple[str, ...] = (),
    comparison: ProductComparisonResult | None = None,
    suitability: SuitabilityAssessment | None = None,
) -> EvidenceAssessment:
    """Evaluate deterministic coverage before any generation-model call."""

    immutable_documents = tuple(documents)
    coverage: list[EvidenceCoverage] = []
    scope_warnings: list[str] = []
    scope_exclusions: list[str] = []
    comparison_axes = {
        item.axis: item for item in comparison.axes
    } if comparison is not None else {}
    for requirement in plan.requirements:
        comparison_axis = (
            comparison_axes.get(requirement.axis)
            if requirement.kind == "product_comparison_axis"
            else None
        )
        if comparison_axis is not None and comparison_axis.status == "value_conflict":
            refs = (
                f"comparison:{requirement.axis}:conflict",
                *comparison_axis.evidence_refs,
            )
            coverage_status = "conflicting"
        elif requirement.kind == "general_rule":
            refs, warnings, exclusions = _general_support(
                requirement,
                immutable_documents,
                plan=plan,
            )
            scope_warnings.extend(warnings)
            scope_exclusions.extend(exclusions)
            coverage_status = "supported" if refs else "missing"
        elif requirement.kind in {
            "product_identity",
            "product_axis",
            "product_comparison_axis",
        }:
            refs = _product_support(
                requirement,
                immutable_documents,
                structured_axes,
                comparison.comparable_axes if comparison is not None else frozenset(),
            )
            coverage_status = "supported" if refs else "missing"
        elif requirement.kind == "calculation":
            refs = (
                (f"calculator:{CALCULATOR_VERSION}",)
                if calculation is not None and calculation.performed
                else ()
            )
            coverage_status = "supported" if refs else "missing"
        else:
            refs = ()
            coverage_status = "missing"
        coverage.append(
            EvidenceCoverage(
                requirement_id=requirement.requirement_id,
                subquestion_id=requirement.subquestion_id,
                label=requirement.label,
                status=coverage_status,
                evidence_refs=refs,
                minimum_evidence_count=requirement.minimum_count,
            )
        )

    recommendation_conditions = (
        suitability.required_conditions
        if suitability is not None
        else route.missing_conditions
    )
    conditions = [
        *recommendation_conditions,
        *identity_conditions,
        *(comparison.required_conditions if comparison is not None else ()),
    ]
    subquestion_scopes = {
        subquestion.subquestion_id: subquestion.scope
        for subquestion in plan.subquestions
    }
    if any(
        requirement.kind == "general_rule"
        and subquestion_scopes.get(requirement.subquestion_id, plan.scope).temporal_unresolved
        for requirement in plan.requirements
    ):
        conditions.append("적용 기준일")
    if calculation is not None and not calculation.performed:
        conditions.extend(calculation.missing_inputs)

    identity_missing = any(
        item.status == "missing"
        and requirement.kind == "product_identity"
        for item, requirement in zip(coverage, plan.requirements)
    )
    if identity_missing and not (
        suitability is not None and suitability.status == "needs_conditions"
    ):
        conditions.extend(
            item.label
            for item, requirement in zip(coverage, plan.requirements)
            if item.status == "missing" and requirement.kind == "product_identity"
        )

    comparison_codes = tuple(
        requirement.product_code
        for requirement in plan.requirements
        if requirement.kind == "product_identity"
        and requirement.product_code is not None
    )
    known_canonical = tuple(
        structured_identities[code]
        for code in comparison_codes
        if code in structured_identities
    )
    class_dependent_comparison = any(
        requirement.kind == "product_comparison_axis"
        and requirement.axis in {"class", "fees", "performance", "balance"}
        for requirement in plan.requirements
    )
    if (
        "product_comparison" in route.labels
        and class_dependent_comparison
        and len(comparison_codes) >= 2
        and len(known_canonical) == len(comparison_codes)
        and len(set(known_canonical)) < len(known_canonical)
    ):
        conditions.append(
            "비교 상품코드가 같은 투자설명서를 공유하므로 각 코드의 판매 클래스 매핑"
        )

    unique_conditions = tuple(dict.fromkeys(conditions))
    conflicts = tuple(
        EvidenceConflict(
            conflict_id=f"comparison:{axis.axis}",
            label=f"상품 비교축 {axis.axis}의 동일 조건 값 충돌",
            evidence_refs=(f"comparison:{axis.axis}:conflict", *axis.evidence_refs),
        )
        for axis in comparison_axes.values()
        if axis.status == "value_conflict"
    )
    supported = sum(item.status == "supported" for item in coverage)
    missing = len(coverage) - supported
    comparison_coverage = tuple(
        item
        for item, requirement in zip(coverage, plan.requirements)
        if requirement.kind == "product_comparison_axis"
    )
    if plan.unsupported_scope_reasons:
        status = "out_of_scope"
        scope_exclusions.extend(
            f"request:{reason}" for reason in plan.unsupported_scope_reasons
        )
    elif suitability is not None and suitability.status == "out_of_corpus":
        # Missing a trusted product index or finding no eligible corpus
        # candidate is an evidence limitation, not a customer field to invent.
        status = "out_of_corpus"
    elif unique_conditions:
        status = "needs_conditions"
    elif conflicts:
        status = "conflicting_evidence"
    elif comparison_coverage and not any(
        item.status == "supported" for item in comparison_coverage
    ):
        # Product identities alone do not justify paying for a model call that
        # cannot answer the requested comparison. Keep the failure local.
        status = "out_of_corpus"
    elif supported == 0:
        status = "out_of_corpus"
    elif missing:
        status = "partially_answerable"
    else:
        status = "answerable"
    return EvidenceAssessment(
        version=EVIDENCE_ASSESSOR_VERSION,
        status=status,
        coverage=tuple(coverage),
        missing_conditions=unique_conditions,
        conflicts=conflicts,
        scope_warnings=tuple(dict.fromkeys(scope_warnings)),
        scope_exclusions=tuple(dict.fromkeys(scope_exclusions)),
        planned_tools=plan.tools,
    )


# The eight lifecycle domains the 58 official general documents were classified
# into. Stated in refusals so the reader can tell what is in scope.
CORPUS_SCOPE_SUMMARY = (
    "퇴직연금(DB·DC·IRP)과 연금저축의 납입·가입, 제도·관리, 운용, 이전, "
    "중도인출, 연금수령, 과세, 보호·구제"
)


def render_limitation_answer(
    assessment: EvidenceAssessment, *, settled: str = ""
) -> str:
    """Render a safe successful answer without asking HCX to fill missing facts."""

    if assessment.status == "needs_conditions":
        needed = assessment.missing_conditions or assessment.missing_requirements
        listed = ", ".join(needed)
        # What the question already settles is stated before what it does not.
        # "총급여 7천만원이면 세액공제율이 몇 퍼센트인가요" fixes the rate by
        # itself, and answering only with a request for the contribution left
        # the question unanswered although the rate was already determined.
        opening = f"{settled} " if settled else ""
        return (
            f"{opening}"
            "현재 정보만으로 개인별 계산·비교 또는 추천을 확정할 수 없습니다. "
            f"추가로 확인할 항목은 {listed}입니다. "
            "확인되지 않은 조건이나 상품 identity를 임의로 가정하지 않겠습니다."
        )
    if assessment.status == "conflicting_evidence":
        labels = ", ".join(item.label for item in assessment.conflicts)
        refs = ", ".join(
            dict.fromkeys(
                ref
                for conflict in assessment.conflicts
                for ref in conflict.evidence_refs
                if not ref.startswith("comparison:")
            )
        )
        location = f" 확인된 원문 좌표는 {refs}입니다." if refs else ""
        return (
            f"동일한 적용 조건에서 서로 다른 공식 값이 확인되어 {labels}을 확정할 수 없습니다."
            f"{location} 어느 값을 임의로 선택하지 않고 원문 정정 또는 최신본 확인이 필요합니다."
        )
    if assessment.status == "context_budget_exceeded":
        return (
            "질문에 필요한 공식 근거를 제한된 응답 Context에 빠짐없이 담을 수 없어 답변 생성을 "
            "중단했습니다. 일부 근거만으로 결론을 만들지 않으며, 질문 범위를 나누거나 근거 표현을 "
            "안전하게 축약한 뒤 다시 확인해야 합니다."
        )
    if assessment.status == "out_of_scope":
        return (
            "이 API는 주최 측 제공 자료에 근거한 설명만 수행하며 개인 계좌, 외부기관 조회 "
            "또는 직원용 내부 시스템에 접속할 수 없습니다. 실제 조회나 내부 처리를 한 것처럼 "
            "답하지 않겠습니다."
        )
    # A refusal is graded on how well it explains the limit, so name the
    # requirement that went unsupported and state the boundary of the supplied
    # corpus. Both come from the assessment and the document classification, so
    # nothing here asserts a fact the corpus does not carry.
    missing = assessment.missing_requirements
    unmet = f" 뒷받침되지 않은 항목은 {', '.join(missing[:3])}입니다." if missing else ""
    return (
        "주최 측 제공 자료에서 이 질문의 결론을 직접 뒷받침할 근거를 찾지 못했습니다."
        f"{unmet}"
        f" 제공된 자료가 다루는 범위는 {CORPUS_SCOPE_SUMMARY}입니다."
        " 자료에 없는 수치·제도·상품 정보를 추정해 답하지 않습니다."
        " 계좌·상품코드·적용 시점과 묻는 사건을 구체화해 확인해야 합니다."
    )


def render_limitation_trace(
    *,
    assessment: EvidenceAssessment,
    route: QuestionRoute,
    document_count: int,
    calculation: PensionTaxCreditCalculation | None,
    identity_trace: dict[str, object] | None = None,
    comparison_trace: dict[str, object] | None = None,
    suitability_trace: dict[str, object] | None = None,
    context_render_trace: dict[str, object] | None = None,
) -> str:
    tools: list[dict[str, object]] = [
        {
            "name": "local_retriever",
            "status": "ok" if document_count else "no_evidence",
            "documents": document_count,
        },
    ]
    if identity_trace is not None:
        tools.append(identity_trace)
    if comparison_trace is not None:
        tools.append(comparison_trace)
    if suitability_trace is not None:
        tools.append(suitability_trace)
    if calculation is not None:
        tools.append(calculation.trace_fields())
    if context_render_trace is not None:
        tools.append(context_render_trace)
    tools.append(assessment.trace_fields())
    return json.dumps(
        {
            "summary": "근거 충분성 판정에 따라 생성 모델 호출 없이 한계 응답을 생성함",
            "tools": tools,
            "route": list(route.labels),
            "decision_status": assessment.status,
            "hyperclova_x_called": False,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
