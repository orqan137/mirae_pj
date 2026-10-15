# 공개 벤치마크 방법론과 연금 Agent 적용

## 결론

이 프로젝트에는 하나의 공개 종합점수보다 계층별 평가가 적합하다. 검색기는 BEIR/MTEB 방식,
RAG 진단은 RAGChecker/Ragas 방식, 금융 수치 추론은 FinQA/TAT-QA 방식으로 분리한다. 대회 정답
근거는 오직 미래에셋 공식 자료로 독립 구축한 골드셋을 사용한다.

## 공식 논문·저장소 확인

| 기준 | 공식 자료 | 적합한 평가축 | 채택 상태 |
|---|---|---|---|
| BEIR | [NeurIPS 2021 논문](https://openreview.net/forum?id=wCu6T5xFjeJ), [공식 저장소](https://github.com/beir-cellar/beir) | heterogeneous retrieval, nDCG/Recall/MRR | 지표 채택 |
| MTEB/MMTEB | [EACL 2023 논문](https://aclanthology.org/2023.eacl-main.148/), [공식 저장소](https://github.com/embeddings-benchmark/mteb) | embedding/retrieval/reranking 비교 | embedding 도입 시 보조평가 |
| RAGChecker | [NeurIPS 2024 논문](https://arxiv.org/abs/2408.08067), [Amazon Science 저장소](https://github.com/amazon-science/RAGChecker) | claim recall, context precision, faithfulness, hallucination | 축 채택, 외부 judge 미도입 |
| Ragas | [공식 metric 문서](https://docs.ragas.io/en/latest/concepts/metrics/available_metrics/), [공식 저장소](https://github.com/explodinggradients/ragas) | context precision/recall, faithfulness, answer relevancy | 축 채택, 외부 judge 미도입 |
| FinQA | [EMNLP 2021 논문](https://aclanthology.org/2021.emnlp-main.300/), [공식 저장소](https://github.com/czyssrs/FinQA) | supporting evidence와 수치 program 분리 | typed 계산 설계에 채택 |
| TAT-QA | [ACL 2021 논문](https://aclanthology.org/2021.acl-long.254/), [공식 저장소](https://github.com/NExTplusplus/TAT-QA) | 표+본문, operator, scale, answer type | typed 표 평가에 채택 |
| ALCE | [EMNLP 2023 논문](https://aclanthology.org/2023.emnlp-main.398/), [공식 저장소](https://github.com/princeton-nlp/ALCE) | citation correctness/completeness | 주장-근거 축 채택 |
| BFCL | [UC Berkeley 프로젝트](https://sky.cs.berkeley.edu/project/berkeley-function-calling-leaderboard/), [공개 구현](https://github.com/ShishirPatil/gorilla/tree/main/berkeley-function-call-leaderboard) | 함수 선택·복수 호출·function relevance | 13개 tool-selection holdout 축 채택 |
| ToolSandbox | [논문](https://arxiv.org/abs/2408.04682), [공개 구현](https://github.com/apple/ToolSandbox) | 상태 의존·정보 부족·중간 milestone | 단계별 상태 gate 원칙 채택 |
| τ-bench | [ICLR 2025 논문](https://openreview.net/forum?id=roNSXZpUDN), [공개 구현](https://github.com/sierra-research/tau-bench) | 정책 준수 trajectory·최종 상태·반복 신뢰성 | 정책 계약+종단 평가 원칙 채택 |
| CheckList | [ACL 2020 논문](https://aclanthology.org/2020.acl-main.442/) | MFT·INV·DIR 행동검사, 평균 정확도 밖의 취약성 | 시나리오 유형·관계검사 채택 |
| Dynabench | [NAACL 2021 논문](https://aclanthology.org/2021.naacl-main.324/) | human/model-in-the-loop adversarial 수집과 별도 유효성 검수 | 공개 실패를 dev 회귀로 이관하는 순환 채택 |
| HELM | [공식 논문](https://arxiv.org/abs/2211.09110), [Stanford CRFM](https://crfm.stanford.edu/helm/) | scenario taxonomy와 정확도·강건성·효율 등 다중 지표 | 시나리오·지표·누락축 명시 채택 |
| WILDS | [ICML 2021 논문](https://proceedings.mlr.press/v139/koh21a.html) | 실제 분포 이동과 subpopulation/worst-group 평가 | family·필수 stratum 최저 성능 채택 |
| Adaptive holdout | [ICML 2015 Ladder 논문](https://proceedings.mlr.press/v37/blum15.html) | 반복 test 피드백에 대한 adaptive overfitting | validation/sealed 반복 노출 금지 |

2026-08-30 확인 HEAD: BEIR `ef83d293`, MTEB `86079bf7`, Ragas `298b6827`, RAGChecker
`6091f08c`. BEIR/MTEB/RAGChecker 코드 라이선스는 Apache-2.0으로 확인했지만, 포함된 개별
데이터셋의 이용조건은 별도다. 이 저장소는 해당 코드나 데이터를 복사하지 않고 지표 정의와 설계
패턴만 독립 구현한다.

## 적용 원칙

### 행동 강건성·과적합 통제

고정 fixture를 여러 번 실행해 실패를 보고 규칙을 고친 순간 그 fixture는 독립 holdout이 아니라
개발 회귀셋이다. 따라서 기존 lifecycle 24건과 공개 adversarial 11건은 모두 development로
표시한다. 이 35건의 통과는 알려진 취약성이 다시 생기지 않았다는 뜻이지 비공개 질문 일반화율이
아니다.

CheckList의 MFT/INV/DIR 구조를 연금 질의에 맞춰 다음처럼 사용한다.

- MFT: 한 제도 판단·route·근거 선택의 최소 기능을 직접 검사한다.
- INV: 존댓말, 문장부호, 검수된 동의표현처럼 의미를 보존하는 변형에서 route·상태·qrel 결속이
  유지되는지 본다.
- DIR: 부정, 계좌·대상·시점·관할·필수조건을 바꿀 때 금지 route/anchor나 evidence status가
  정해진 방향으로 달라지는지 본다.

행 단위 무작위 분할은 같은 제도 claim과 문장 template가 양쪽에 새어 들어가므로 사용하지 않는다.
동일 semantic family의 base와 변형은 한 split에 둔다. 같은 claim/evidence lineage에서 표현만
바꾸는 L 패널과, claim과 evidence lineage까지 바꾸는 S 패널을 분리해 독립성 수준을 함께
표시한다. 공식 자료가 희소한 이전 영역에 전 split source-disjoint를 강제하지 않고 그 한계를
분모·coverage와 함께 공개한다.

case micro 평균으로 같은 의미 가족의 변형 수를 성능처럼 부풀리지 않는다. `family`는 critical
관계가 하나라도 실패하면 전체 실패이며, scenario axis와 domain×axis×status strata의 분자·분모,
family macro와 worst group을 함께 본다. 특히 기대상 생성 금지인데 생성했거나, 필수 locator qrel과
assessor가 선택한 근거가 어긋난 채 생성 가능한 상태가 된 경우는 평균 없이 1건으로도 실패시킨다.

validation은 quick/local/release에 넣지 않는다. 제한된 aggregate 결과라도 동작 수정에 사용하면
그 버전은 development로 강등하고 새 family로 회전한다. sealed는 구현·코퍼스·평가기 fingerprint를
동결한 최종 후보에서 별도 관리자가 실행하며, case 본문과 qrel은 구현자에게 공개하지 않는다.
이 절차는 공유 파일시스템에서 암호학적 봉인을 보장하는 기능이 아니라 접근권한과 실행 횟수에
대한 운영 규약이다. 실제 대회 비공개 질의만이 최종 외부 타당성 증거다.

합성 질문은 공식 문서의 사실·정답을 새로 만들지 않고 표현과 명시 조건만 변형한다. 생성자 외
의미·qrel 검수가 없으면 development fuzz로만 분류한다. 외부 실제 질의 패턴은 라이선스와 PII를
검토한 뒤 평가 형태 참고에만 쓰며, 그 답이 주최 측 자료에 없으면 외부 사실을 가져오지 않고
`out_of_corpus` 기대상태로 평가한다.

### 검색

검색 qrels는 관련 source ID 집합이며 순위를 유지한다. 현재 구현은 binary relevance 기준으로
Recall@6, MRR@6, nDCG@6, MAP@6를 계산한다. 상품 비교에서는 두 상품이 모두 검색되어야 recall이
1이 된다. `out_of_corpus` hard negative는 검색 결과가 비어 있는지를 별도 비율로 집계한다.

### RAG와 답변

LLM judge 하나로 종합 점수를 만들지 않는다. 검색 recall, 근거 coverage, 상태 판정, 계산 성공,
금지 주장, 답변의 숫자·날짜·상품 관계를 각각 검사한다. RAGChecker/Ragas의 claim-level 축은
유효하지만, 한국어 금융 판단에 외부 judge를 추가하면 비용·재현성·데이터 반출 문제가 생긴다.
따라서 현재는 결정론 validator를 출시 게이트로 쓰고 HCX judge는 향후 소량 보조평가로만 둔다.
현재 answer-guard 골드셋은 정상·hard-negative 45개에서 상품명/코드 값 교환,
metric 혼동, 동명이상품 모호성, typed 행 누락, qualifier·citation 오류와 비정량 규칙의
무인용·다른 주제 인용·허용/금지·예외범위 반전을 독립 검사한다.

### 금융 수치

FinQA/TAT-QA처럼 최종 숫자 외에 supporting evidence, operand, operator, scale/unit, 기준일을
분리한다. 현재 상품 비교기는 class, metric, date, unit, aggregation/tax basis가 맞을 때만 값을
비교 가능으로 판정한다. 공식 상품 전수 parser 게이트는 확정 결과 수가 현재 기준보다 감소하거나,
독립 원문 좌표에서 같은 semantic key의 값이 하나라도 충돌하면 실패한다. 동일 페이지의 복합표
후보는 모호성으로 따로 집계한다. 영어 재무보고서 데이터의 직접 점수는 한국 연금 상품 설명
능력과 다른 과제이므로 회귀 차단 기준으로 쓰지 않는다.

## 남은 평가 공백

- planning 8개, tool selection 13개, suitability 5개, grounding 25개와 answer guard 45개는 회귀용
  최소셋이며 통계적 대표성이 없다.
- 현재 qrels는 source 단위다. 페이지·표·셀 단위 relevance가 더 필요하다.
- HCX 실응답을 소량 검토했으나 독립 대규모 의미 평가나 반복 신뢰도 추정은 수행하지 않았다.
  초안 통과, 인용 보수, 발췌 복구를 구분해야 하며 API 계약 통과만으로 정답률을 주장하지 않는다.
- embedding backend가 없으므로 MTEB 실행은 현재 아키텍처 비교에 실익이 없다.
- 공개 endpoint는 정해졌으며 외부 노드의 HTTP 접근과 별도 JSON 계약 검사를 통과했다.
  실제 300초 장애 주입·주최 측 재시도까지 포함한 공개망 종단간 검증은 하지 않았다.
- development 시나리오와 분리된 validation/sealed family는 별도 curator와 qrel 이중 검수가
  필요하다. 같은 모델 계열 subagent의 역할 분리는 공격 검토에는 유용하지만 통계적 독립 검수로
  간주하지 않는다.
- [다중 Agent 토론 연구](https://proceedings.mlr.press/v235/du24e.html)는 일부 수학·전략·사실성
  과제의 개선을 보였지만 한국 연금 RAG, 300초 제한과 HCX 1회 예산을 보장하지 않는다. 현재는
  오프라인 설계자→반대감사→구현검토에만 사용하고, 운영 경로는 단일 Agent 대비 품질·unsafe
  generation·p95·호출량 ablation을 통과하기 전 도입하지 않는다.

구현과 명령은 [`../../../benchmarks/README.md`](../../../benchmarks/README.md)를 따른다.
검토일: 2026-09-06.

## KorQuAD 데이터 활용 범위

[KorQuAD v1.0 공식 설명](https://korquad.github.io/category/1.0_KOR.html)의 과제는 주어진 문맥에서
정답을 추출하는 독해이며 공식 지표는 EM/F1이다. 이 프로젝트는 dev 데이터의 964문단을 검색
후보로, 5,774질의 중 seed 0으로 뽑은 1,500질의를 검색 입력으로 사용했다. `k=10`에서 원문
P@1 `0.8247`, Recall@10 `0.9607`, MRR@10 `0.8779`를 재현했다. 공백 제거 질의는 각각
`0.4393`, `0.5367`, `0.4781`이다.

이는 **공식 데이터를 변형해 수행한 문단 검색 평가**이며 공식 KorQuAD 점수, 연금 답변 정확도,
미사용 평가셋 성능이 아니다. BEIR·MTEB·RAGChecker·FinQA·TAT-QA 원형 평가를 실행한 결과도
아니다. 반복 개발에 사용한 데이터임을 기록하고 데이터·표본·검색기·평가기 hash를 보고서에 남긴다.

```bash
.venv/bin/python scripts/evaluate_external_korquad.py \
  --dataset /tmp/korquad_dev.json --sample 1500 --k 10 --seed 0 \
  --output benchmarks/results/korquad-retrieval-20260906.json
```

공식 dev 파일 SHA-256:
`25ffeb51e6c51ec02c071b60a10188e10005c144110f0d876b26079d80a35bdf`.
데이터는 운영 코퍼스와 Git 제출물에 포함하지 않는다. 데이터가 없거나 표본 수가 유효하지 않으면
이 명령은 실패한다. 검색 결과의 유무만 보는 `abstention` 게이트를 답변 기권 정확도로 해석하지
않는다. 내부 검색 qrel의 일반문서 범위는 58개 중 10개이므로 나머지 문서의 답변 품질은 별도 검토가
필요하다.
