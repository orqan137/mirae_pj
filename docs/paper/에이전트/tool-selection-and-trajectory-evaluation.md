# 도구 선택과 Agent 단계 평가

연금 Agent의 핵심은 도구 수가 아니라, 질의에 필요한 최소 도구를 정확히 고르고 불필요한 도구를
호출하지 않는 것이다. 이 문서는 외부 연구와 공개 구현의 평가 원칙만 참고하며 데이터나 다른 LLM을
운영 코퍼스·런타임에 포함하지 않는다.

## 검토한 논문·공개 구현

| 자료 | 확인한 평가 원칙 | 이 프로젝트의 제한된 채택 |
|---|---|---|
| UC Berkeley [BFCL](https://sky.cs.berkeley.edu/project/berkeley-function-calling-leaderboard/), [공개 구현](https://github.com/ShishirPatil/gorilla/tree/main/berkeley-function-call-leaderboard) | 단일·복수·병렬 함수 선택과 적절한 함수가 없을 때 호출을 보류하는 function relevance를 분리 평가 | route, source group, 정확한 도구 집합과 계산기 비호출 음성 사례를 별도 holdout으로 측정 |
| Apple [ToolSandbox](https://arxiv.org/abs/2408.04682), [공개 구현](https://github.com/apple/ToolSandbox) | 상태 의존성, 정보 부족, 임의 trajectory의 중간·최종 milestone 평가 | 조건 부족을 생성 전 상태로 판정하고 planning·검색·근거·Context·답변을 각각 gate로 평가 |
| Yao et al. [τ-bench](https://openreview.net/forum?id=roNSXZpUDN), [공개 구현](https://github.com/sierra-research/tau-bench) | 도메인 정책과 API가 있는 환경에서 최종 상태 및 반복 실행 신뢰성을 평가 | 금융 정책 준수는 결정론 계약으로, 최종 API는 종단 smoke로 검사; 거래 상태변경 도구는 도입하지 않음 |
| Liu et al. [AgentBench](https://arxiv.org/abs/2308.03688), [공개 구현](https://github.com/THUDM/AgentBench) | 서로 다른 환경에서 장기 계획·의사결정·지시 준수 실패를 분리 관찰 | 범용 환경 점수 대신 연금의 제도·세제·절차·상품·추천 strata를 유지 |

## 프로젝트 도출

1. 모듈마다 LLM Agent를 만들지 않는다. 결정론 router와 planner가 최소 도구 집합을 만들고 HCX는
   검증된 근거의 설명에만 사용한다.
2. “비교”라는 단어가 없어도 두 상품 중 더 낮거나 높은 값을 고르는 질문은 comparator가 필요하다.
3. “계산 방식 설명”과 “내 금액 계산”을 분리한다. 전자는 공식 규칙 검색만, 후자는 검색과
   결정론 계산기를 함께 선택한다.
4. 추천 문장의 `중도인출 계획은 없음`은 suitability 입력이지 중도인출 절차 요청이 아니다.
5. 평가에는 route label, source group, 정확한 도구 집합, 조건부 추천 차단과 계산 여부를 별도로 둔다.
6. 단계별 gate와 실제 `/answer` 종단 평가는 서로 대체하지 않는다. 전자는 실패 위치를, 후자는
   제출 계약과 전체 trajectory를 검증한다.

## 현재 반영

- question router v7: 일반 연금 ETF·펀드 규칙과 특정 상품 의도를 분리하고, 확정급여/확정기여,
  세제혜택, 이전·인출·수령 paraphrase와 상대 비교 표현을 인식
- planner v15/retrieval-only query-anchor v4: `general_retriever`, `product_retriever`, `pension_tax_credit_calculator`,
  `product_comparator`, `suitability_prefilter`의 최소 집합 명시
- tool-selection evaluator v1: 13개 frozen 사례의 route/source/tool/control exact match
- lifecycle evaluator v3: 8영역 긍정 16개와 근접 부정 8개의 route/source/query/retrieval 분리 평가
- benchmark suite v14: 모든 profile에서 HCX 0회 environment/domain/lifecycle/tool-selection stage 실행
- evidence assessor v10 trace: 실제 생성 전 계획 도구 목록을 안전한 감사 필드로 보존

## 한계

- 13개 fixture는 같은 개발자가 작성한 회귀 holdout이며 통계적으로 독립된 일반화 표본이 아니다.
- lifecycle 24개도 최초 실패를 분석한 개발자가 작성했으므로 독립 표현 평가가 더 필요하다.
- BFCL·ToolSandbox·τ-bench 데이터 점수를 한국 연금 성능으로 해석하지 않는다.
- 현재 평가는 단일 평가 요청 안의 도구 trajectory다. 주최 측 API는 대화 세션을 요구하지 않으므로
  다중 턴 상태변경 환경은 구현하지 않았다.
- 실제 HCX 생성 품질과 반복 일관성은 고정 Public endpoint에서 대표 정밀질의에만 측정한다.

검토일: 2026-08-30.
