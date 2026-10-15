# 복합질의 분해와 문맥 의존 검색어

검토일: 2026-08-30  
적용 범위: 한 번의 평가 GET 안에 여러 연금 의도 또는 생략·대명사가 함께 들어온 경우

## 확인한 연구

| 연구 | 확인된 설계 | 이 프로젝트의 채택 범위 |
|---|---|---|
| Ammann et al., [Question Decomposition for RAG](https://aclanthology.org/2025.acl-srw.32/) (ACL 2025) | 복합질문을 하위질문으로 나누고 각 질문별 passage를 검색한 뒤 후보를 합치고 rerank | 절별 검색과 라운드로빈 병합; LLM 분해기는 미사용 |
| Fu et al., [Decomposing Complex Questions Makes Multi-Hop QA Easier and More Interpretable](https://aclanthology.org/2021.findings-emnlp.17/) (EMNLP Findings 2021) | 분해·하위질문 처리·비교를 분리하면 근거 경로를 단계별로 볼 수 있음 | planner, retriever, deterministic comparator의 별도 계약 |
| Anantha et al., [QReCC](https://aclanthology.org/2021.naacl-main.44/) (NAACL 2021) | 생략과 코어퍼런스가 있는 질문을 standalone query로 바꾸고 rewriting·retrieval·reading을 분리 평가 | 한 요청 내부의 생략절에 검증된 주제 anchor만 승계 |
| Wu et al., [CONQRR](https://aclanthology.org/2022.emnlp-main.679/) (EMNLP 2022) | 문맥 의존 질의를 standalone query로 바꾸어 기존 retriever에 연결 | 운영 중 별도 rewrite LLM 없이 결정론적 query text 필드 사용 |
| Wang et al., [Query2doc](https://aclanthology.org/2023.emnlp-main.585/) (EMNLP 2023) | 생성된 pseudo-document로 질의와 문서 사이 어휘 불일치를 줄임 | 생성기는 도입하지 않고 실패가 확인된 구어체에 감사 가능한 공식용어 anchor만 사용 |
| Tang et al., [Sub-question Evaluation](https://aclanthology.org/2021.eacl-main.283/) (EACL 2021) | 최종 답이 맞아도 일부 하위질문을 실패할 수 있으므로 sub-question을 별도 평가 | 절 수·route·필수 근거·검색어 승계를 각 절에서 검사 |
| Choe et al., [HiREC](https://aclanthology.org/2025.findings-acl.855/) (ACL Findings 2025) | 금융 표준문서의 유사 boilerplate 때문에 문서→passage 계층 검색과 누락 근거 보완이 필요 | 주최 측 source group·상품 identity를 먼저 제한하고 절별 passage 검색 |

## 현재 코드로 도출한 규칙

`planner v12`는 사용자 원문 절 `text`와 검색 전용 `query_text`를 분리하고 최소 도구집합을 명시한다. 응답 echo와 적용범위
판정에는 원문을 유지하고, 검색에만 검증된 앞 절의 주제를 추가한다.

1. 마침표·물음표·명시적 접속사·세미콜론은 절 경계로 사용한다.
2. 쉼표와 `및`은 모든 조각이 독립적인 intent family를 갖고, 둘 이상의 서로 다른 family가
   확인될 때만 분리한다.
3. `연금저축, IRP` 같은 명사 목록과 두 상품코드 목록은 분리하지 않는다.
4. `세액공제 ... 한도도`처럼 현재 절에 주제가 없고 생략 표지가 있을 때만 앞의 알려진 일반
   주제를 검색어에 붙인다.
5. `그 상품`은 전체 질문에서 공식 상품 identity가 하나로만 해소됐을 때 그 코드만 승계한다.
6. 둘 이상의 상품이 해소된 뒤 `그 상품`이 나오면 어느 상품인지 추정하지 않고 정확한 공식명
   또는 코드를 조건으로 요구한다.
7. `그때`처럼 실제 과거 시점을 뜻할 수도 있는 표현은 시간 적용범위 gate를 우회하지 않는다.
8. 병합된 각 검색 결과에는 어떤 하위 query에서 회수됐는지 index를 보존한다. assessor는 해당
   절의 결과를 우선하고, 공개 renderer는 requirement의 최소 필요 개수만 골라 후보문서 중복이
   다른 절의 Context 예산을 소모하지 않게 한다.
9. query-anchor v4는 DC 부담금 지연, 실물이전, 연금수령한도, 주택·의료비 중도인출, ISA 만기
   전환, 직접청구처럼 실제 실패가 재현된 좁은 표현만 공식용어와 연결한다.
10. 부정문·비의도 근접표현에는 anchor를 붙이지 않고 별도 hard negative로 출시를 차단한다.
11. anchor는 retrieval query에만 들어가며 route와 evidence topic은 원문 및 검증된 상속어로만
    판정해 사용자가 요청하지 않은 의도를 만들지 않는다.

## 채택하지 않은 방식

- 분해·rewrite를 위해 요청마다 HCX를 한 번 더 호출하지 않는다. 비용과 latency가 늘고, 생성
  LLM이 사용자에게 없는 상품·시점·조건을 보충할 수 있다.
- 쉼표를 무조건 절 경계로 보지 않는다. 계좌·상품·숫자 목록을 훼손한다.
- 앞 절 전체를 뒤 검색어에 복사하지 않는다. 서로 다른 세제·절차 주제가 섞여 검색 precision이
  떨어질 수 있다.
- 여러 상품 중 대명사의 대상을 최근 언급 순서로 추정하지 않는다.

## 평가 계약

- planning fixture 8개: 명시 복합, 쉼표 복합, 세액공제 생략, 절차 코어퍼런스, 명사목록 비분해
- lifecycle fixture 24개: 8영역 긍정 검색 16개와 근접 부정 anchor 8개를 분리 측정
- grounding fixture 25개와 retrieval qrels 18개: 공식 source와 Recall/MRR/nDCG/MAP@6를 분리 측정
- 모든 단계 HCX 호출 0회

이 회귀셋은 구현 중 사용한 개발셋이다. 일반화 성능을 주장하려면 표현과 순서를 바꾼 별도
holdout을 독립 검수자가 작성해야 한다.
