from __future__ import annotations

from pathlib import Path


def load_system_prompt(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def build_user_prompt(
    *,
    question_id: str,
    question: str,
    context: str,
    route_labels: tuple[str, ...] = (),
    needs_conditions: bool = False,
    requires_calculation: bool = False,
    evidence_status: str = "unassessed",
    missing_evidence: tuple[str, ...] = (),
    scope_warnings: tuple[str, ...] = (),
) -> str:
    evidence = context if context else "[검색된 근거 없음]"
    route = ", ".join(route_labels) if route_labels else "unclassified"
    flags = (
        f"추가 조건 확인 필요: {'yes' if needs_conditions else 'no'}\n"
        f"결정론적 계산 검토 필요: {'yes' if requires_calculation else 'no'}\n"
        f"근거 판정: {evidence_status}\n"
        f"미지원 요구사항: {'; '.join(missing_evidence) if missing_evidence else 'none'}\n"
        f"적용범위 경고: {'; '.join(scope_warnings) if scope_warnings else 'none'}"
    )
    return (
        f"질의 ID: {question_id}\n"
        f"질의: {question}\n"
        f"질의 분류: {route}\n{flags}\n\n"
        f"검색 근거:\n{evidence}\n\n"
        "검색 근거를 우선 사용해 한국어로 최종 답변만 작성하세요. "
        "[DETERMINISTIC_CALCULATOR]가 있으면 status=calculated의 결과를 재계산하지 말고 "
        "그대로 사용하세요. status=needs_input이면 개인별 계산값을 추정하지 말고 "
        "표시된 missing_inputs를 확인하세요. "
        "근거 판정이 partially_answerable이면 지원되는 요구사항만 답하고 미지원 요구사항을 "
        "일반지식으로 보충하지 마세요. "
        "적용범위 경고가 있거나 effective_from/effective_to가 unknown이면 현행 법령을 보장하지 "
        "말고, 제공 문서와 knowledge_as_of 기준의 한계를 밝혀 주세요. "
        "중요 수치·날짜·상품 결론과 가입·납입·이전·중도인출·수령·세제의 조건·허용·금지·예외를 "
        "설명하는 각 독립 규칙 문장에는 그 규칙을 직접 지지하는 [근거:citation_id]를 같은 "
        "문장 끝에 붙이세요. 서로 다른 규칙을 한 문장에 합치지 마세요. "
        "일반 문서와 계산기는 Context의 citation_id를, [TYPED PRODUCT COMPARISON]은 해당 행의 "
        "source 열을 정확히 사용하고 존재하지 않는 citation을 만들지 마세요. "
        "근거가 부족하면 부족한 점을 명시하고 확인되지 않은 수치나 제도를 만들지 마세요."
    )
