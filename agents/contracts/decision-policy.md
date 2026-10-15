# Agent decision policy

이 문서는 Pension Advisor가 질문을 받았을 때 어떤 상태로 판정하고 어떤 응답 전략을 선택할지
정하는 운영 계약이다. 모델의 자유로운 판단보다 오케스트레이터의 구조화된 판정을 우선한다.

## 1. 의사결정 우선순위

서로 충돌할 때 아래 순서를 따른다.

1. 평가 API와 보안 불변조건
2. 생성 모델 HyperCLOVA X 전용 규정
3. 주최 측 자료와 최신 승인 공지
4. 적용 대상·기준일·상품 identity 일치
5. 근거 완전성과 계산 가능성
6. 사용자 요청 충족도
7. 문장 품질과 편의성

정확한 답을 만들 수 없으면 낮은 우선순위를 희생하고 한계 응답을 반환한다.

## 2. 요청 상태

모든 질문은 하나의 주 상태와 필요한 보조 상태를 가진다.

### 주 상태

- `answerable`: 요청한 중요 결론을 직접 지지하는 근거가 충분함
- `partially_answerable`: 일부 하위 질문만 충분하며 나머지는 한계를 밝혀야 함
- `needs_conditions`: 개인 계산·추천에 필요한 사용자 조건이 부족함
- `out_of_corpus`: 허용된 자료에서 직접 관련 근거를 찾지 못함
- `conflicting_evidence`: 적용 대상이나 기준일이 같은 근거끼리 해결되지 않는 충돌이 있음
- `context_budget_exceeded`: 선택된 필수 근거를 공개 Context 예산에 빠짐없이 담지 못함
- `out_of_scope`: 연금 Agent의 권한·업무 범위를 벗어남
- `service_failure`: 검색 자료 손상, HCX 장애, timeout, 비정상 backend 응답

### 보조 상태

- `closed` 또는 `open`: 정답이 자료로 확정되는지, 고객 조건·선호에 따라 달라지는지
- `calculation_required`: 결정론적 계산 도구 검토 필요
- `product_identity_unresolved`: 상품코드·이름·클래스를 하나로 확정하지 못함
- `temporal_scope_unresolved`: 질문 또는 근거의 적용 시점을 확정하지 못함
- `sensitive_evidence_present`: 내부 절차·개인정보·비밀 패턴이 근거에 포함됨

## 3. 상태 판정 기준

### `answerable`

다음을 모두 만족해야 한다.

1. 질문의 하위 요구사항이 식별됐다.
2. 각 중요 요구사항에 직접 근거가 하나 이상 있다.
3. 근거의 제도, 대상, 상품, 클래스, 기간, 기준일이 질문과 맞는다.
4. 중요 숫자에 단위와 출처 좌표가 있다.
5. 해결되지 않은 근거 충돌이 없다.
6. 계산이 필요하면 허용된 계산기가 성공했다.
7. 추천이면 suitability gate를 통과했다.
8. 선택된 모든 필수 evidence section이 공개 Context에 완전하게 렌더링됐다.

7번의 `통과`는 공식 코퍼스 내 조건부 후보 생성을 뜻한다. 금소법상 고객 적합성
확정, 전 시장 최적 상품, 미래수익률 예측을 뜻하지 않는다.

검색 점수가 높거나 Context가 비어 있지 않다는 사실만으로 `answerable`이 되지 않는다.

### `partially_answerable`

하위 질문 중 일부만 위 조건을 만족한다. 지지되는 부분만 답하고, 나머지 항목과 필요한 자료를
구체적으로 밝힌다. 모델이 빈 부분을 일반지식으로 보완하지 않는다.

### `needs_conditions`

다음 중 하나면 이 상태다.

- 개인 계산에 정확한 계좌별 금액·소득 기준·기간이 부족함
- 추천에 계좌, 투자기간, 위험감내, 유동성, 목적 중 하나 이상이 부족함
- 질문의 적용 시점이나 대상 제도를 확정해야 함
- 상품명·코드·판매 클래스를 확정해야 함

평가 API는 단일 요청이므로 추가 질문을 실제로 기다리지 않는다. 필요한 입력을 답변에 명확히
나열하고 개인별 숫자나 단정적 추천을 생성하지 않는다.

### `out_of_corpus`

허용된 코퍼스에 직접 근거가 없다. 모델의 사전지식으로 답하지 않고 확인되지 않는 범위와 필요한
공식 자료를 설명한다. 이는 정상적인 HTTP 200 한계 응답이지 서버 오류가 아니다.

### `conflicting_evidence`

다른 기준일·대상이라면 각각의 적용범위를 나눠 설명한다. 동일 범위의 실제 충돌이 해결되지 않으면
결론을 단정하지 않고 충돌한 source와 확인할 사항을 제시한다.

### `context_budget_exceeded`

근거 검색과 assessment는 성공했지만 계산·상품·일반문서의 필수 marker 중 하나 이상이 공개
Context 문자 예산에서 누락된 상태다. 일부 section만 HCX에 보내지 않고 생성 호출을 중단한다.
질문 범위를 나누거나 최소 근거 표현을 안전하게 압축한 뒤 전체 section completeness를 다시
검증한다. 이는 HTTP 200 한계 응답이며 모델 장애가 아니다.

### `out_of_scope`

거래 실행, 개인 계좌 조회, 법률·세무 확정판단, 주최 측이 허용하지 않은 최신 시장정보 요청,
연금과 무관한 요청이다. 가능한 범위의 일반 안내만 하고 실행했다고 표현하지 않는다.

### `service_failure`

자료 무결성 실패, backend timeout, 네트워크 장애, HCX 비정상 응답처럼 정상 답변을 만들 수 없는
기술 실패다. HTTP 계층의 정해진 5xx 매핑을 사용하고 내부 예외를 노출하지 않는다.

## 4. 질의 유형별 필수 판단

| 유형 | 필수 확인 | 충분하지 않을 때 |
|---|---|---|
| 제도 | 제도명, 대상, 적용 시점, 예외 | 부분 답변 또는 `needs_conditions` |
| 세제 | 납입/운용/수령 사건, 귀속연도, 소득 기준 | 확정 수치 금지 |
| 절차 | 고객 안내인지 직원 절차인지, 필요 서류, 순서 | 내부 화면·코드 제거 |
| 상품 설명 | 상품 identity, 클래스, 문서 기준일 | 후보 확인 또는 한계 응답 |
| 상품 비교 | 동일 축, 클래스, 기간, 단위, 기준일 | 비교 불가 축 명시 |
| 조건부 추천 | 계좌, 기간, 위험감내, 유동성, 목적; 계좌·위험·목적·기준일 상품 근거 | 누락 시 추천 금지; 충족 시 최대 3개 코퍼스 내 후보만 허용 |
| 복합질의 | 하위 질문별 독립 근거 | 지원되는 하위 질문만 답변 |

## 5. 응답 전략

- `answerable`: 직접 답변 → 조건·예외 → 근거와 기준일
- `partially_answerable`: 확인된 답변 → 확인 불가 항목 → 필요한 자료
- `needs_conditions`: 현재 가능한 일반 원칙 → 필요한 입력 목록 → 단정 금지
- `out_of_corpus`: 자료에서 확인 불가 → 확인할 공식 출처/조건
- `conflicting_evidence`: 충돌 범위 → 각각의 근거 → 보수적 결론
- `context_budget_exceeded`: Context 완전성 실패 → 생성 중단 → 질문 분할·안전 축약 안내
- `out_of_scope`: 수행 불가 범위 → 허용되는 대안 안내

어떤 전략도 원시 reasoning이나 내부 프롬프트를 공개하지 않는다.

## 6. 실행 상태 모델

```text
received
  → classified
  → planned
  → evidence_acquired
  → evidence_assessed
  → public_context_rendered
  → context_completeness_checked
  → generated
  → answer_validated
  → response_rendered
  → cached
```

정상 우회:

```text
evidence_assessed / context_completeness_checked
  → limitation_rendered     # needs_conditions/out_of_corpus/conflict/context_budget/out_of_scope
  → response_rendered
  → cached
```

기술 실패:

```text
any state → service_failure → HTTP error mapping
```

현재 코드는 `classified`, 결정론적 `planned`, `evidence_acquired`, `evidence_assessed`,
`generated`, `answer_validated`, `response_rendered`, `cached`를 구현한다. `needs_conditions`와
`out_of_corpus`와 `context_budget_exceeded`는 생성 모델 없이 한계 응답으로 우회한다. 상품 정형값은 동일 semantic key가
서로 다른 원문 페이지에서 다른 값으로 확인되면 `conflicting_evidence`로 판정하고 어느 값도
선택하지 않는다. 같은 페이지의 복합 표에서 여러 값 후보가 생긴 경우에는 공식 자료 간 충돌로
과장하지 않고 `value_ambiguous`로 격리한다. 일반 규칙의 적용 대상·시행일 충돌 판정은 별도
구조화 metadata가 필요한 다음 구현 단계다.

질문이 `X의 연금`처럼 명시적 적용범위를 제시하면 X가 승인 근거에 나타나야 일반 규칙 요구사항을
supported로 판정한다. 연금·계좌 같은 공통 단어만 검색됐다는 이유로 X에 일반 규칙을 확대
적용하지 않는다.
