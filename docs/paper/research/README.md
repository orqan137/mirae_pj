# Agent research

이 디렉터리는 구현된 제품이 아니라 Agent와 근거 기반 질의응답을 개선할 연구를 정리한다.
현재 프로젝트에 중요한 연구 질문은 다음과 같다.

| 연구 주제 | 프로젝트의 현재 한계 | 검증할 개선 가설 |
|---|---|---|
| Agentic RAG | 질문당 한 번 검색하고 한 번 생성 | 복합질의를 분해해 검색하면 근거 누락이 감소한다 |
| 검색 품질 평가 | top-k 결과를 바로 Context로 사용 | 생성 전 관련성·충분성 게이트가 환각을 줄인다 |
| 자기검증 | 최종 주장의 근거 대조 단계 없음 | 주장-근거 검증 후 제한 응답으로 전환하면 정확성이 오른다 |
| 결정론적 도구 | 세액공제 한 시나리오만 코드화 | 상품 비교와 날짜/단위 정렬을 코드화하면 재현성이 오른다 |
| 추천 적합성 | 필요한 조건 탐지만 수행 | 명시적 suitability gate가 단정적 추천을 방지한다 |
| 평가 방법 | 계약 테스트 중심 | 대표 질의셋과 근거 정답셋이 품질 회귀를 조기에 잡는다 |

## 핵심 1차 자료

- Lewis et al., [Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks](https://arxiv.org/abs/2005.11401)
- Yao et al., [ReAct: Synergizing Reasoning and Acting in Language Models](https://arxiv.org/abs/2210.03629)
- Asai et al., [Self-RAG: Learning to Retrieve, Generate, and Critique through Self-Reflection](https://arxiv.org/abs/2310.11511)
- Yan et al., [Corrective Retrieval Augmented Generation](https://arxiv.org/abs/2401.15884)
- Singh et al., [Agentic Retrieval-Augmented Generation: A Survey on Agentic RAG](https://arxiv.org/abs/2501.09136)

검토일: 2026-08-18.

## 이 프로젝트에 맞는 연구 방향

대회에서는 외부 웹 검색보다 주최 측 제공 자료에 답변을 고정하는 것이 우선이다. 따라서 CRAG의
웹 검색 확장은 그대로 적용하지 않고, `검색 결과 평가 → 질의 재작성 → 허용된 코퍼스 재검색 →
근거 부족 응답`으로 제한한다.

ReAct의 공개 reasoning trace를 그대로 API에 싣지 않는다. 내부 계획 상태는 구조화된 라우트,
도구명, 문서 수, 성공 여부로만 기록하고 `think_trace`에는 안전한 감사 요약만 반환한다.

Self-RAG의 학습용 reflection token을 그대로 구현할 필요는 없다. 먼저 결정론적 게이트로 다음을
검증하는 것이 이 대회의 제한시간과 재현성에 더 적합하다.

1. 요청한 하위 질문별 근거 존재 여부
2. 숫자의 클래스·단위·기준일·출처 좌표
3. 답변 주장과 Context의 직접 지지 관계
4. 근거가 부족할 때 답변 대신 필요한 입력 또는 자료 한계 제시
