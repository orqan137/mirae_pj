# Runtime agent resources

실행 코드와 분리한 연금 Agent의 프롬프트와 계약을 보관한다.
설계 근거가 된 논문·연구 자료는 [`../docs/paper/`](../docs/paper/)에 있다.

```text
agents/
├── prompts/system.md
└── contracts/
    ├── data-model.md
    ├── query-routing.md
    ├── decision-policy.md
    ├── action-boundaries.md
    ├── tool-configuration.md
    └── tool-registry.yaml
```

- `prompts/system.md`: HyperCLOVA X에 전달하는 운영 시스템 지시문
- `contracts/data-model.md`: 원본·청크·상품 클래스/비용/성과/잔고의 데이터 계약
- `contracts/query-routing.md`: 제도·세제·절차·상품·조건부 추천 라우팅과 근거 충분성 게이트
- `contracts/decision-policy.md`: 답변 가능·조건 부족·근거 부족·충돌·범위 밖 판정 기준
- `contracts/action-boundaries.md`: 허용·금지 행동과 데이터·네트워크 경계
- `contracts/tool-configuration.md`: 도구 호출·실패·버전 규칙
- `contracts/tool-registry.yaml`: 도구와 구현 모듈의 기계 판독 설정

모든 LLM 단계는 HyperCLOVA X만 사용하며, `think_trace`는 원시 사고과정이 아닌
감사 가능한 실행 요약만 담는다.
