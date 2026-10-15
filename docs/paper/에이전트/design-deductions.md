# Agent 연구에서 도출한 구현 우선순위

## 최종 도출

이 대회에서 필요한 것은 범용 자율 Agent가 아니라 `근거 계획기 + 제한된 검색기 + 결정론적 도구 +
검증기 + HCX 생성기`다. Agent 수를 늘리는 것보다 각 경계의 평가 가능성을 먼저 만든다.

## 목표 graph

```text
요청 계약 검증
  → 질문 분해·필수조건 탐지
  → source group/문서 후보 선택
  → 문서 내부 근거 검색·중복 제거
  → 근거 충분성 판정
  → 필요한 계산/상품정렬 실행
  → HCX 답변 생성
  → 주장-근거·숫자·날짜 검증
  → 성공 응답 또는 근거부족 응답
```

## P0: 반드시 먼저 구현

### 1. Evidence plan

하위 질문마다 다음 구조를 만든다.

```yaml
subquestion: 문자열
required_source_groups: [general, product]
required_fields: [effective_date, account_type]
evidence_status: missing | partial | sufficient | conflicting
evidence_ids: []
```

추천 질의는 별도의 `ProfileEvidence`를 만들고 각 suitability 필드의 값·출처·상태를 기록한다.
profile이 불완전하면 검색 근거가 충분해도 개인화 추천은 `missing`이다.

문서 수가 많다는 이유로 `sufficient`가 되지 않는다. 요청한 주장에 직접 필요한 필드가 있어야 한다.

### 2. 계층 검색

1. 질문 유형으로 source group을 고른다.
2. 문서 또는 상품코드 identity를 먼저 고른다.
3. 선택된 문서 안에서 passage를 찾는다.
4. 반복 boilerplate와 같은 표 머리말을 제거한다.
5. 빠진 하위 질문만 재작성해 한 번 더 검색한다.

### 3. 결정론적 검증

- 숫자: 답변의 수치가 Context 또는 계산 결과에 존재하는지 검사
- 날짜: 기준일·귀속연도·시행일이 연결됐는지 검사
- 상품: 코드와 클래스가 동일 record인지 검사
- 비교: 기간·단위·클래스가 정렬됐는지 검사
- 추천: suitability 필드 누락 시 순위·단정 표현 차단
- 편향: 재무조건을 고정하고 이름·성별 신호만 바꾼 결과 차이 검사
- 휴리스틱: 입력·계산 없이 10% 단위 저축률이나 4% 인출률이 생성되면 차단

검증 결과는 하나의 groundedness 점수로 합치지 않는다. `answer correctness/completeness`,
`retrieval sufficiency`, `citation precision/recall`을 독립 상태로 남긴다. 문서에 충실해도
시점·상품·클래스가 틀리면 오답이다.

### 4. 표 근거의 구조 보존

- 숫자는 주변 문자열에 존재하는지만 보지 않고 `class → period → row/column header → unit` 경로를 검증
- 검색용 평탄 text와 검증용 table cell record를 분리
- 원문 page+bbox, parser revision, native/OCR 일치 상태를 근거에 포함
- Markdown 표는 병합 셀 구조가 손실되므로 canonical evidence로 사용하지 않음
- cell 또는 header binding이 모호하면 비교·추천을 중단하고 `review_required`로 처리

## P1: 점수 개선

- 복합질의를 하위 질문으로 나눠 근거 recall 측정
- keyword/dense/hybrid별 retrieval recall@k 비교
- 생성 전 rerank와 evidence curation 실험
- 답변 후 중요 주장만 뽑아 support/unsupported/conflict 판정
- 근거 부족 시 재생성보다 제한 응답을 우선

## 평가 지표

| 계층 | 지표 |
|---|---|
| 요청 | API 계약 통과율, echo 정확도 |
| 문서 선택 | source group/document accuracy |
| 검색 | evidence recall@k, 중복률, 필수 하위질문 coverage |
| 문서 추출 | cell exact match, header binding, 표 누락·중복률, OCR/native 불일치율 |
| 계산 | executable answer accuracy, 단위·반올림 정확도 |
| 생성 | 중요 주장 precision, unsupported claim 수 |
| 인용 | critical claim citation recall, citation precision, qualifier mismatch 수 |
| 안전 | 조건 없는 추천률, 비밀·원시 reasoning 노출률 |
| 운영 | p50/p95, 270초 내 완료율, retry/cache 일관성 |

## 채택하지 않을 것

- 자유로운 웹 검색 fallback
- 모델이 임의로 도구 이름·인자를 발명하는 범용 loop
- 여러 모델 간 토론 또는 fallback
- 장기 사용자 기억과 자동 프로필 추정
- 평가 중 코드·프롬프트·지식을 수정하는 자기개선
