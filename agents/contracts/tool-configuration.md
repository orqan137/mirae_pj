# Tool configuration contract

기계 판독 설정은 [`tool-registry.yaml`](tool-registry.yaml)에 있다. 이 문서는 구성 원칙과 도구
호출 순서를 설명한다.

## 1. 제어 방식

`policy=orchestrator_controlled`다. HCX가 임의 JSON tool call을 생성하고 실행기가 그대로 수행하는
구조를 사용하지 않는다. 애플리케이션이 질문 상태에 따라 허용된 함수를 직접 호출하고, HCX에는
검증된 결과만 Context로 전달한다.

이유:

- 평가 요청은 순차적이며 범용 자율성이 필요하지 않다.
- 금융 답변은 같은 입력에 재현 가능한 검색·계산이 중요하다.
- 다른 LLM, 웹, shell 호출을 구조적으로 차단할 수 있다.
- 안전한 `think_trace`와 실제 실행 결과를 일치시키기 쉽다.

## 2. 표준 호출 순서

```text
cache lookup
  → classify_question
  → preliminary plan_question      # capability scope를 가장 먼저 판정
  ├─ unsupported scope → assess_evidence(out_of_scope) → limitation response
  └─ supported scope
       → resolve_product_identity
       → assess_suitability         # 추천 질의만, 후보코드를 계획에 전달
       → plan_question              # 해소된 상품과 필요 도구 집합을 명시
       → retrieve_general_evidence
       → retrieve_product_evidence
       → parse_product_axis_values
       → compare_product_facts
       → calculate_pension_tax_credit
       → assess_evidence
       → render_public_evidence      # assessment-selected 최소 근거, 생성 입력과 동일
       ├─ 불완전 Context 또는 생성 불가 → limitation response
       ├─ 검증된 계산 결과 → 결정론 답변 → validate_answer
       └─ 완전하고 생성 가능 → generate_answer
            → validate_answer       # 실패 시 인용 보수·불지원 줄 제거·근거 발췌 후 재검증
            → 재검증도 실패하면 한계 응답
            → normalize
            → cache
```

조건에 맞지 않는 도구는 호출하지 않는다. 예를 들어 정확한 상품코드가 없으면 현재 구현의 상품
원문 도구를 억지로 호출하지 않고 identity resolver 결과를 기다린다.

현재 question router, `plan_question`, `assess_evidence`는 절별 일반근거, 상품 identity, 요청
비교축, 결정론 계산과 추천 필수조건을 검사한다. `assess_suitability`는 계좌 적격
클래스, 진술된 위험 한도와 목표 유형으로 공식 상품 후보를 먼저 거른다. 이를 법정
적합성 판정이나 보편적 상품 순위로 표현하지 않는다. `needs_conditions`와 `out_of_corpus`는
HCX를 호출하지 않고 결정론적 한계 응답으로 종료한다.

router와 planner의 capability scope guard는 비공개 계좌·외부기관 직접조회,
고객의 직원용 내부 정보 요청을 상품 identity·suitability·검색·계산·HCX보다 먼저
차단한다. 이 경우 `unsupported_scope_reasons`를 `out_of_scope`로 평가하고 빈
`retrieved_context`의 한계 응답을 반환한다. 일반 절차 설명과 명시적 비조회 질의는 이 가드로
차단하지 않고 근거 충분성 판정으로 보낸다.

planner/query-anchor는 원문 절과 검색 전용 query를 분리하고 `general_retriever`, `product_retriever`,
`pension_tax_credit_calculator`, `product_comparator`, `suitability_prefilter` 중 최소 도구 집합을
명시한다. 두 상품을 “비교”라고 쓰지 않고 “둘 중 더 낮은 쪽”처럼 묻는 상대 선택 표현도
comparator로 보낸다. 설명만 요구하는 계산 방법 질의는 계산기를 선택하지 않는다.

검색 anchor는 실패가 재현된 좁은 구어체만 공식용어에 연결하고 사용자 원문·적용범위를 바꾸지
않는다. 일반 연금 규칙에서 ETF·펀드를 언급했다는 이유만으로 상품 투자설명서 검색을 켜지 않으며,
긍정 16개와 근접 부정 8개의 별도 gate를 모두 통과해야 한다.

쉼표 목록은 모든 조각에 서로 다른 독립 intent가 있을 때만 나눈다. 문장부호가 빠진 키워드식
질문은 서로 겹치지 않는 세 intent 구간이 모두 확인될 때만 복구한다. 생략절에는 앞에서 확인된 일반 주제 또는 유일하게 해소된 상품코드만
승계한다. 둘 이상의 상품 뒤 `그 상품`은 대상을 추정하지 않고 정확한 공식명·코드를 요구한다.
연금 핵심 합성어는 단어 내부 공백만 정규화해 `세액공제/세액 공제`, `연금저축/연금 저축`처럼
의미가 같은 띄어쓰기에서 route·계산·조건 판정이 달라지지 않게 한다.
검색 결과에는 절별 query index를 보존하고 renderer는 각 requirement의 최소 근거 수와
calculation·product·general evidence family별 예약 예산을 먼저 배정해 복합질의의 뒤 절이
Context 문자 한도에서 사라지지 않게 한다.
필수 marker가 실제 공개 문자열에서 하나라도 누락되면 `context_budget_exceeded`로 HCX를 호출하지
않고 질문 분할·안전 축약을 안내한다.

`resolve_product_identity`는 공식 명칭 표, 고유 단축명과 경로 상품코드를 사용한다. 8자 이상
별칭의 단일 글자 삽입·삭제·치환은 후보가 하나일 때만 복구하고, 후보가 둘 이상이면 모호한 명칭으로
처리해 추정하지 않는다. `parse_product_axis_values`는 수수료·연환산 세전 성과·기간말 잔고를
provenance가 있는 typed 값으로 변환한다. `compare_product_facts`는 판매 클래스, metric,
기준일, 단위, aggregation/tax basis가 양쪽 상품에서 같은 값만 comparable로 판정한다.
`validate_answer`는 근거 없는 중요 token과 조건 없는 추천을 차단한다. 상품코드와 질문에서
확정한 공식명·고유 단축명을 typed 상품-값 관계에 결속하고, 성과의 기간·aggregation·tax basis·
기준일과 잔고의 좌수/금액·단위·기준일·역사적 시점도 검사한다. 중요 수치·날짜·상품코드 문장은
실제 공개 Context의 citation ID 또는 해당 typed 행 source를 인용해야 한다. 숫자가 없는 제도·세제·
절차 규칙도 원자 claim별 citation을 요구하고, 인용 excerpt의 주제·주행위 극성·필요성·
제한/무제한 범위를 결정론적으로 검사한다. 과거 수익률 기반 추천과 동일 판매 클래스 typed comparison이 없는 비용
우위 추천도 차단한다. 이는 완전한 의미 entailment가 아니라 고정밀 모순 스크리닝이며, 수정 생성은
하지 않는다.

## 3. 시간과 호출 제한

- 전체 요청: 270초 미만
- HCX upstream: 최대 250초
- HCX 생성 호출: 현재 요청당 최대 1회
- 다른 단계: 전체 시간에 포함
- 자동 재생성: 별도 세부 시간예산을 구현하기 전 금지

정상 완료 요청의 재시도는 완료 캐시가 처리한다. HCX 형식 실패 등 5xx 응답은 완료 캐시에 넣지 않아
다음 요청에서 다시 실행할 수 있다. Agent가 동일 요청 안에서 무제한 재검색·재생성하지 않는다.

## 4. 도구 결과 공통 envelope

새 도구는 다음 공통 필드를 가진 구조화 결과를 반환해야 한다.

```yaml
tool_id: string
status: ok | needs_input | no_evidence | conflict | error
version: string
duration_ms: integer
data: object
evidence_refs: []
warnings: []
```

- `data`에는 내부 reasoning이 아니라 입력에서 결정적으로 계산된 결과만 둔다.
- `evidence_refs`는 source ID, path, page/locator, 기준일을 가진다.
- 오류 객체에 credential, header, 원문 질문, stack trace를 넣지 않는다.
- `think_trace`에는 envelope 전체가 아니라 tool ID, status, count, duration만 사용한다.

## 5. 도구별 versioning

검색기, 계산기, 비교기, validator의 동작이 답을 바꾸면 version을 올린다. cache identity에는 다음을
포함해야 한다.

- 애플리케이션 버전
- 시스템 프롬프트 hash
- 코퍼스 manifest hash
- 상품근거 hash
- 계산기 version
- router/planner/evidence assessor/comparator/suitability/validator version
- HCX 모델과 생성 설정의 비밀이 아닌 식별자

## 6. 구현 상태

Registry의 `implemented`만 현재 사용할 수 있다. `planned`는 설계 목표이며 런타임이 이미
지원한다고 표시하면 안 된다. planned 도구가 구현될 때는 다음이 필요하다.

1. typed input/output model
2. 단위 테스트와 실패 테스트
3. trace redaction 테스트
4. 전체 시간예산 테스트
5. cache identity 반영
6. registry 상태를 `implemented`로 변경
