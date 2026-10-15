# Pension Agent benchmark suite

이 디렉터리는 주최 측 공식 코퍼스를 기준으로 `구축 → 검색 → 근거판정 → 제출검증`을 반복하는
개발용 평가 설정이다. 대회가 지정한 별도 공개 벤치마크는 없다. 아래 공개 벤치마크는 학계와
오픈소스에서 널리 쓰이는 평가 방법을 가져오는 참고 기준이며, 실제 출시 차단 기준은 미래에셋
공식 문서로 만든 로컬 골드셋이다.

## 평가 계층

```text
source compile + dependency check + hashed environment contract
  → official 58-document + 8-domain coverage audit
  → colloquial lifecycle retrieval + answerability + evidence-qrel + negative-anchor holdout
  → development scenario robustness + metamorphic relation + unsafe-generation gate
  → contract tests
  → question decomposition + evidence planning
  → route + exact tool-selection holdout
  → suitability profile + hard-constraint candidate filter
  → official-corpus grounding goldset
  → ranked retrieval metrics
  → official product typed-parser census
  → deterministic answer guard goldset
  → frozen rule-claim paraphrase/hard-negative holdout
  → mixed public-context coverage and budget gate
  → HCX precision plan validation (no call)
  → Docker runtime build + non-root/minimal container contract + production preflight
  → one-question public endpoint smoke (explicit opt-in only)
```

| 계층 | 현재 구현 | 채택한 공개 기준 | HCX 호출 |
|---|---|---|---:|
| 계획 | 8개 명시·암시 복합/생략/비분해 질의의 절·검색어·근거 요구사항 | HiREC, QReCC, question decomposition | 0 |
| 도구 선택 | 13개 route·source group·정확한 도구집합·계산/조건 제어 holdout | BFCL, ToolSandbox, τ-bench | 0 |
| 실행환경 | Python 3.12, 22+5 exact/hash pin, `.venv`, runtime/tools image 분리 | pip secure/repeatable installs, Docker multi-stage | 0 |
| 도메인 coverage | 공식 일반문서 58개를 8개 연금 생애주기·위험 영역에 매핑하고 대표 qrel 연결 | stratified evaluation, FinanceBench evidence schema | 0 |
| 구어체 생애주기 | 8영역 긍정 16개 retrieval·answerable·evidence-qrel 정렬과 근접 부정 8개 anchor 거부 | Query2doc, HiREC/LOFin | 0 |
| 시나리오 강건성 | base 35개, expansion 29개, safety 17개의 세 development 팩에서 MFT/INV/DIR, family·worst-axis·unsafe-generation을 검사 | CheckList식 behavioral test와 metamorphic evaluation 패턴 | 0 |
| 조건부 추천 | 5개 profile·누락·충돌·후보 필터 사례 | 금소법 적합성 원칙, explainable recommendation | 0 |
| 검색 | source-level binary qrels, Recall/MRR/nDCG/MAP@6 | BEIR, MTEB | 0 |
| 근거·상태 | 25개 공식 코퍼스 회귀 사례, 필수 근거·조건·계산·금지 주장 schema | RAGChecker, Ragas | 0 |
| 금융 수치 | typed fee/performance/balance, 정수 기반 세액공제 계산, 단위·기준일 정렬 | FinQA, TAT-QA 평가 관점 | 0 |
| 답변 주장 | 45개 정상/hard-negative의 상품·수치·인용·규칙 관계 판정 | RAGChecker, ALCE, TRUE 평가 관점; 공식 precision/recall 측정 아님 | 0 |
| 규칙 holdout | 개발 fixture와 분리한 paraphrase 8개·hard negative 8개, acceptance/rejection rate | FActScore, ALCE | 0 |
| Context 완전성 | 정상 3개 조합과 불가능 예산 1개의 marker·배제·예산·완전성 판정 | HiREC, multi-source attribution | 0 |
| HCX 정밀계획 | 9개 대표 범주 등록, 골드셋 연결·단일호출 예산 검사 | 단계별 agent evaluation 패턴 | 0 |
| 종단간 | 공개 `/answer` 계약, 단계 trace, token usage, 동일 요청 캐시 | 주최 측 API 공지 | 명시 선택한 1개 논리 질의 |

BEIR/MTEB/RAGChecker 등의 데이터셋은 운영 코퍼스에 포함하지 않는다. 각 하위 데이터셋의
라이선스도 별도 확인해야 하며, 영어 금융보고서 점수는 한국 연금 답변의 출시 기준을 대신하지
않는다.

## 실행

빠른 로컬 회귀는 hashed environment, domain coverage, lifecycle paraphrase, 세 development scenario 팩, planning, tool selection, suitability, grounding, retrieval, product parser, answer guard,
rule-claim holdout, context coverage와
HCX precision plan 무호출 검증을 실행한다.

```bash
.venv/bin/python scripts/run_benchmark_suite.py --profile quick
```

기본 로컬 게이트는 compile, dependency, hashed environment, 전체 pytest, domain coverage, lifecycle paraphrase, 세 development scenario 팩, planning, tool selection, suitability, grounding, retrieval,
product parser, answer guard, rule-claim holdout, context coverage와 HCX precision plan 무호출
검증을 실행한다.

```bash
.venv/bin/python scripts/run_benchmark_suite.py \
  --output benchmarks/results/latest-local.json
```

Docker runtime 이미지 구축, non-root·dependency·OCR 도구 부재 검사와 production preflight까지
확인할 때는 `release`를 사용한다. README의 실제
공개 주소가 아직 없으면 preflight가 실패하는 것이 정상이다.

```bash
.venv/bin/python scripts/run_benchmark_suite.py --profile release
```

실제 공개 endpoint는 명시적인 opt-in 없이는 호출하지 않는다. 아래 명령은 readiness 1회와 같은
질의 3회를 보내지만, 완료 캐시가 정상이라면 HCX에 전달되는 논리 질의는 최대 1개다.

```bash
.venv/bin/python scripts/run_benchmark_suite.py \
  --profile submission \
  --endpoint http://PUBLIC_IP/answer \
  --allow-live-hcx \
  --output benchmarks/results/latest-submission.json
```

HCX 문체·생성 품질을 정밀 확인할 때도 전체 9개를 한꺼번에 호출하지 않는다. 먼저 아래 무호출
명령으로 설정을 검사한 뒤, 실패 위험이 가장 큰 case 하나를 명시해 실행한다.

```bash
.venv/bin/python scripts/evaluate_hcx_precision.py

.venv/bin/python scripts/evaluate_hcx_precision.py \
  --allow-live-hcx \
  --endpoint http://PUBLIC_IP/answer \
  --case-id GROUND-COMPOUND-001
```

실측 결과에는 질문·답변 원문을 중복 저장하지 않고 해시, 계약·route·decision·validator 판정,
latency, HCX token usage와 rate-limit 잔량만 남긴다. 무호출 설정 검사는 모든 정규 profile에
포함된다.

보호 시나리오 팩은 정규 profile에서 실행하지 않는다. validation 팩을 명시하면 evaluator가
base·expansion·safety 세 canonical development 팩을 자동으로 함께 로드해 semantic/template/
lineage 격리를 검사한다. sealed 팩은 세 development 팩 외에 별도 validation 팩을 하나 이상
`--isolation-pack`으로 명시해야 하며, 여러 validation 기준 팩은 옵션을 반복한다.

```bash
.venv/bin/python scripts/evaluate_scenario_robustness.py \
  --cases /RESTRICTED/validation.json

.venv/bin/python scripts/evaluate_scenario_robustness.py \
  --cases /RESTRICTED/sealed.json \
  --isolation-pack /RESTRICTED/validation-L.json \
  --isolation-pack /RESTRICTED/validation-S.json
```

보호 팩의 정상 출력은 전체·family macro·익명 worst 지표만 반환한다. schema, qrel 또는 격리
실패도 stderr에 case/family/template/lineage 내용을 내보내지 않고 일반 오류 코드로만 반환한다.

## 현재 골드셋과 해석

`tests/fixtures/question_planning_cases.json`은 명사 접속을 잘못 나누지 않는지, 복합
질의를 절별로 분해하는지, 각 절의 route·근거 종류·topic anchor·검색 전용 query를 따로
보존하는지를 검사한다. 쉼표 복합질의, 세액공제 생략, 절차 코어퍼런스와 명사목록 비분해도
포함한다.

`tests/fixtures/suitability_cases.json`은 계좌·기간·위험감내·유동성·목적을 질문에
명시한 경우만 받아들이고, 누락·충돌·DB 개인 선택 요청·중도인출 적격 미확인을
HCX 호출 전에 차단하는지 검사한다. 추천이 가능한 사례도 공식 상품 코퍼스의 계좌
적격성·위험등급·목적·기준일을 통과한 최대 3개 후보만 허용한다.

`data/structured/general_topic_inventory.json`은 공식 일반문서 58개를 납입·가입, 제도·관리,
운용, 이전, 중도인출, 연금수령, 과세, 보호·구제의 8개 영역에 전수 매핑한다. evaluator는 모든
문서가 적어도 한 영역에 속하고, 각 영역의 대표 grounding case와 qrel이 실제 해당 문서군에
연결되는지 검사한다. 이 coverage는 문서별 정답 58개를 뜻하지 않고 위험 strata의 최소 회귀층이다.
`general_source_governance.json` v2는 같은 58/58 source의 관할·audience·시행기간·구상품 scope를
전수 등록하고 명시 기준일 또는 적용범위가 맞지 않는 근거를 fail-closed로 제외한다.

`tests/fixtures/lifecycle_paraphrase_holdout_cases.json`은 같은 8개 영역에 공식 용어를 직접 쓰지
않은 긍정 질의 16개와, 의미가 가깝지만 검색 anchor가 생기면 안 되는 부정 질의 8개를 고정한다.
evaluator는 긍정 top-6 회수, 필수 route, source group, 검색 전용 공식용어, 영역별 검색 coverage와
부정 anchor rejection을 분리한다. 긍정 사례는 이어서 evidence assessor의 `answerable` 상태와
generation eligibility, 실제 선택 evidence source와 retrieval qrel의 overlap, 8개 영역 evidence
coverage까지 모두 1.0으로 요구한다. 부정 사례는
anchor 통제만 평가하고 evidence status를 `not_evaluated`로 고정한다. 이 fixture도 실패 분석자가
작성한 개발 회귀층이므로 독립 일반화 표본으로 해석하지 않는다.
evaluator v3의 `positive_evidence_qrel_alignment_rate`는 assessor가 실제 선택한 source ID와 각
긍정 사례의 retrieval qrel이 겹치는 비율이며 threshold는 1.0이다.

`benchmarks/scenarios/development-v1.json`은 이 24개를 holdout이 아닌 development regression으로
명시하고, 같은 의미군을 다른 표현으로 공격한 7개 invariance 사례와 4개 directional control을
더한 35개 팩이다. MFT는 독립 최소기능 기대를, INV는 같은 semantic family 안에서 route·source
   group·상태·생성결정의 불변성을 검사한다. 현재 DIR control v1은 부정·범위 전환에서 금지 route와 검색
anchor가 제거되는지만 검사하며, 최종 답변 claim의 모든 방향성을 평가한다고 해석하지 않는다.

신규 긍정 7개의 qrel은 `source_id + locator + chunk_index`와 실제 LocalRetriever chunk 본문의
SHA-256을 함께 고정한다. 기존 16개 긍정 qrel은 source 수준으로만 검수됐으므로 locator 증거로
과장하지 않고 `granularity=source`로 남긴다. 보고서는 case/axis/family all-critical과 최악 axis,
unsafe generation 수를 함께 표시해 다수의 쉬운 변형이 한 취약군을 가리지 못하게 한다.
locator qrel은 pack load 때뿐 아니라 실제 평가 retriever를 만든 뒤에도 runtime manifest와 chunk
본문 SHA-256을 다시 대조한다. case/family/template/lineage 식별자는 NFKC·공백 규칙과 casefold
중복 검사를 통과해야 하며 INV/DIR 관계의 부모는 같은 팩의 MFT여야 한다.

이 35개도 실패 사례를 본 개발·검토 Agent가 작성한 stress set이다. 역할을 나눠 교차 검토했지만
통계적으로 독립된 validation은 아니다. 정규 quick/local/release/submission profile에는 development
팩만 들어간다. `development-expansion-v1.json`은 공식 문서의 별도 개념 29개/10 family,
`development-safety-v1.json`은 capability·audience·부정·provider/domain scope 17개/9 family를
추가한다. 세 팩의 현재 총합은 81개이며 모두 튜닝에 사용한 development 회귀다. 향후 validation과
sealed 팩은 semantic family 누출 lint를 거친 별도 custodian
입력으로만 실행하고, 두 split의 evaluator 출력은 사례 ID·질문·qrel·source·anchor·실패 상세를
제외한 aggregate로 제한한다. evaluator v3는 소수 group 역추론을 막기 위해 protected report의 axis·family별
행과 명명된 anchor-control slice도 숨기고 전체 비율, family 수, 익명 최악 축 수치만 반환한다.
validation 결과를 보고 튜닝하면 해당 팩을 retire하고 새 팩으로
교체해야 하며, 1회 실행과 접근권한은 로컬 코드가 아니라 운영 절차가 통제한다.

`tests/fixtures/pension_grounding_cases.json` schema v2가 정답 상태, route, 상품 identity, 비교축,
필수 근거, 조건, 계산 여부와 검색 qrels를 함께 고정한다. 현재 검색 평가는 qrels가 있는 18개
질의와 검색 결과 없음 1개 질의를 사용한다. 별도로 검색 후보가 있더라도 질문의 명시적 적용범위가
근거에 없는 1개 hard negative를 evidence gate에서 차단한다.

`tests/fixtures/answer_validation_cases.json`은 생성 초안의 정상 상품명/코드 관계, 값 교환,
총보수와 총보수·비용 혼동, 같은 공식명을 가진 두 코드, typed 행 누락, 성과의 기간·연환산·세전,
잔고의 좌수/금액·단위·기준일·역사적 시점, citation 누락·위조·교차상품 source와 숫자가 없는
규칙의 무인용·다른 주제 인용·허용/금지·예외범위 반전을 45개 사례로 검사한다. 이 단계도 저장된
초안과 공개 evidence만 사용하므로 HCX를 호출하지 않는다.

`tests/fixtures/rule_claim_holdout_cases.json`은 위 개발 fixture와 별도로 규칙 paraphrase 8개와
범위·주행위 극성·잘못된 citation·무인용 hard negative 8개를 고정한다. evaluator는 지원 문장의
acceptance와 위험 문장의 rejection을 별도 1.0 기준으로 검사하며 두 지표를 평균내지 않는다.

`tests/fixtures/context_coverage_cases.json`은 1,200자 혼합 Context, 일반 규칙 3절,
structured 상품+일반 규칙 조합에서 assessor가 선택한 필수 marker가 실제 공개 문자열에 남는지,
중복·미선택 marker가 빠지는지와 최종 길이가 예산 이내인지 각각 검사한다. 최소표현도 담을 수 없는
300자 사례는 `complete=false`를 요구하고 backend가 HCX 전에 중단하는 경로와 연결한다.

`scripts/evaluate_product_parser.py`는 공식 상품 100개의 class label을 전수 순회한다. 현재 출시
하한은 fees 309, performance 43, balance 342개의 확정 결과이며, 서로 다른 원문 페이지에서 같은
semantic key의 값이 충돌하면 1건이라도 게이트를 실패시킨다. 같은 페이지 복합 표의 후보는
`ambiguous`로 별도 집계해 공식 자료 충돌로 오탐하지 않는다.

현재 1.0 점수는 소수의 대표 회귀 사례가 깨지지 않았다는 의미일 뿐 일반화 성능을 뜻하지 않는다.
출시 판단력을 높이려면 다음 순서로 확대한다.

1. 독립 검수자가 작성한 제도·세제·절차 paraphrase와 hard negative를 추가한다.
2. 상품별 class/metric/date/unit 혼동 사례를 표 단위 qrels로 늘린다.
3. 작성자와 검토자를 분리해 relevance와 required claim을 이중 검수한다.
4. 개발 중 보지 않는 holdout 세트를 별도 보관한다.
5. 로컬 결정론 게이트를 통과한 소수 사례에만 HCX 생성 정밀평가를 수행한다.

설정 파일은 `config/benchmark-suite.json`이며, 보고서에는 설정·코퍼스·상품근거·골드셋 SHA-256을
남긴다. `results/`의 실행 결과는 재생성 가능한 산출물이므로 Git 추적 대상이 아니다.
