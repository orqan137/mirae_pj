from __future__ import annotations

import json

from app.models import BackendOutput


class DemoBackend:
    name = "demo"

    async def answer(self, *, question_id: str, question: str) -> BackendOutput:
        return BackendOutput(
            answer=(
                "데모 모드입니다. 평가 제출 전 AGENT_BACKEND를 hcx 또는 http로 "
                "설정해 실제 연금 Agent를 연결하세요. 입력 질의: "
                f"{question}"
            ),
            retrieved_context="[DEMO] 실제 검색 문서가 연결되지 않았습니다.",
            trace_summary=json.dumps(
                {
                    "mode": "demo",
                    "steps": ["요청 규격 검증", "데모 응답 생성"],
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )
