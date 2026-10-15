# 주장·인용·근거 검증 연구

검토일: 2026-08-30

금융 답변의 `근거 있음`은 하나의 점수가 아니다. 답변 주장이 맞는지, 검색 문서가 필요한
주장을 포함하는지, 인용한 문서가 바로 그 주장을 지지하는지, 필요한 주장에 빠짐없이 인용이
붙었는지를 분리해야 한다.

## 연구·구현에서 가져올 구분

- [RAGChecker](https://arxiv.org/abs/2408.08067)는 답변과 gold answer를 atomic claim으로
  나누고, response↔gold, response↔retrieved context, gold↔retrieved context를 각각 비교한다.
  이를 통해 정확성·완전성, retrieval claim recall/context precision, faithfulness·hallucination을
  분리한다.
- [ALCE](https://arxiv.org/abs/2305.14627)는 citation quality를 correctness와 completeness로
  나눈다. 인용 하나가 해당 주장을 지지하는지와, 인용이 필요한 주장에 실제 인용이 붙었는지는
  다른 실패다.
- [FActScore](https://aclanthology.org/2023.emnlp-main.741/)는 한 생성물에 지원·미지원 정보가
  함께 섞일 수 있으므로 문장 전체 이진판정보다 atomic fact별 지지율을 평가한다. 연금 규칙도
  한 문장 안의 접속된 조건·허용·필요성 claim을 분리하지 않으면 같은 문제가 생긴다.
- [TRUE](https://aclanthology.org/2022.naacl-main.287/)는 시스템 단위 상관만이 아니라 개별 예시의
  사실 일관성 판정을 표준화해야 한다고 지적하고, 대규모 NLI와 질문생성·질의응답 방식이 서로
  보완적임을 보고한다. 따라서 한 개의 평균 groundedness만으로 개별 금융 답변을 출시하지 않는다.
- [FinRank](https://arxiv.org/abs/2608.07400)는 금융 QA의 gold passage와 함께 같은 회사의
  다른 연도·서식, 같은 연도·서식, 동종 회사 문서를 hard negative로 둔다. 반복되는 금융문서에서는
  단순 의미 유사도가 시점·주체가 다른 문서를 쉽게 고른다는 점을 직접 시험한다.

RAGChecker 공개 구현은 claim 추출과 entailment 판정에 기본적으로 외부 LLM을 사용한다.
평가지표의 분해는 채택하지만 Bedrock/Llama 기본 경로는 사용할 수 없다. critical claim은
결정론적으로 검사하고, 자연어 의미 판정이 필요한 경우에만 모델·prompt·설정이 고정된 HCX
보조 evaluator를 별도 평가 과정에서 사용한다.

FinRank 데이터는 CC BY-NC 4.0이므로 대회 산출물이나 학습·평가 데이터에 복사하지 않는다.
hard-negative 분류 아이디어만 주최 측 원본에서 독립적으로 재구성한다.

## 네 가지 독립 판정

| 판정 | 질문 | 대표 실패 |
|---|---|---|
| answer correctness | 주장이 승인된 gold/규칙과 맞는가 | 문서에는 있지만 시점이 지난 규칙을 현행으로 답함 |
| retrieval sufficiency | 필요한 gold claim을 검색 결과가 포함하는가 | 세제와 상품 비용 중 상품 문서만 검색 |
| citation precision | 연결한 evidence가 바로 그 주장을 지지하는가 | 같은 표의 다른 클래스 숫자를 인용 |
| citation recall | 검증 가능한 중요 주장에 evidence 연결이 빠지지 않았는가 | 결론만 인용하고 핵심 수치·기준일은 무인용 |

`response↔context`만 보면 낡은 문서에 충실한 오답을 통과시킬 수 있다. 따라서 authority,
effective date, subject/product/class, measurement period를 포함한 gold policy와 함께 본다.

## 이 프로젝트의 ClaimRecord

```text
ClaimRecord
  claim_id
  claim_type: rule | date | number | product_identity | comparison | recommendation | limitation
  subject, predicate, object
  qualifiers: account_type, product_code, class, period, unit, as_of
  evidence_ids[]
  derivation_id?
  validation_status: supported | unsupported | conflicting | ambiguous | not_verifiable
  failures[]
```

검증기는 답변 문장을 임의로 다시 해석해 숫자를 붙이지 않는다. 생성 전에 사용한 evidence와
calculation result로 예상 ClaimRecord를 만들고, 생성 후 답변에서 critical token을 대조한다.

## 결정론 우선 검증

1. `question_id`, 질문 echo와 응답 JSON 계약을 확인한다.
2. 답변에 나온 상품코드·날짜·금액·비율·등급·기간을 추출한다.
3. 각 값이 연결된 evidence cell 또는 calculator output에 존재하는지 확인한다.
4. subject/product/class/period/unit/as-of qualifier가 모두 같은지 확인한다.
5. 계산값은 operand evidence와 typed operation을 재실행한다.
6. 추천·최상급·확정 표현은 suitability/evidence gate가 허용했는지 확인한다.
7. 반환 `retrieved_context`가 실제 사용 evidence의 최소 집합인지 확인한다.

### 비정량 규칙 스크리닝 v2

숫자가 없는 가입·납입·이전·중도인출·수령·세제 문장은 다음을 순서대로 검사한다.

1. 명시 접속형으로 결합된 규칙을 독립 검증 가능한 claim 단위로 보수적으로 나눈다.
2. 각 claim과 같은 문장 안에 직접 연결된 `[근거:citation_id]`가 있는가
3. citation ID가 실제 공개 `retrieved_context`에 존재하는가
4. 인용 excerpt가 claim의 계좌·사건·세제 주제를 포함하는가
5. `가능/불가`, `허용/금지`, `서류 필요/불필요` 극성이 뒤집히지 않았는가
6. `법정 사유에 한해`를 `아무 사유 없이 언제든`처럼 과도하게 일반화하지 않았는가

문장에 citation이 하나뿐이면 모든 원자 claim에 적용하되 각각 독립 검증한다. citation이 여러
개면 각 claim이 자기 구간의 citation을 가져야 하며, 다른 claim의 citation으로 누락을 숨길 수
없다. 이 검사는 한국어 의미 전체를 증명하는 NLI가 아니다. 주제어와 문장 어휘의 최소 겹침을 요구하고
고위험 극성·범위 반전을 차단하는 고정밀 스크리닝이다. 어휘가 크게 다른 올바른 paraphrase를
거절할 수 있으므로, 결정론 실패를 다른 모델이 자동으로 뒤집지 않고 독립 holdout으로 정밀도와
재현율을 먼저 측정한다. 운영 프롬프트도 서로 다른 규칙을 한 문장에 합치지 않고 원문 용어를
유지하도록 맞춘다.

숫자가 문서 어딘가에 존재하는 것만으로 통과시키지 않는다. 예를 들어 `1.41`이 같은 PDF에
여러 번 있어도 C-P2의 총보수인지, 다른 클래스·비용축인지 header path가 맞아야 한다.

## 주최 측 자료로 만들 hard negatives

- 같은 상품·다른 클래스
- 같은 클래스·다른 비용축(총보수/총보수·비용/동종유형 평균)
- 같은 상품·다른 측정기간 또는 작성기준일
- 기간말 잔고와 자산총계·모집한도·운용역 운용규모
- 현재 값처럼 보이는 역사적 기간말 잔고
- 동일 문구를 가진 목차·정의·본문
- 동일 PDF를 공유하는 code alias와 별도 판매 클래스
- `-`, 빈칸, 0, 신규설정 `해당사항 없음`
- 제도 개정 전후 규칙과 기준일 없는 문서

각 positive claim마다 최소 한 개의 confusable negative를 붙이고, retriever가 positive를 negative보다
높게 두는 pairwise accuracy도 측정한다.

## 통과 정책

- critical claim 하나라도 `unsupported/conflicting/ambiguous`이면 해당 주장을 제거하고 제한 답변으로 전환
- 숫자·날짜·코드·비교·추천과 제도·세제·절차의 조건·허용·금지·예외는 citation recall 100% 요구
- 자연어 배경설명과 비정량 규칙 스크리닝의 holdout 정밀·재현율은 critical claim 점수와 평균내지 않음
- refusal/근거부족 응답은 정확한 상태 표현인지 평가하며, 빈 답을 자동 만점 처리하지 않음
- 모델 judge 결과는 결정론 검사 실패를 뒤집을 수 없음

이 구조는 답변 문체를 제한하기 위한 것이 아니라, 어떤 근거로 어떤 주장이 만들어졌는지 재현하기
위한 것이다.
