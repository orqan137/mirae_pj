# 다중 근거 Context 예산과 완전성

검토일: 2026-08-30

복합 연금 질의는 일반 제도 문장, 상품 표의 정형 행, 결정론 계산 결과를 한 답변에서 함께 요구할
수 있다. 검색에 성공했더라도 고정 Context 예산을 앞 section이 선점하면 뒤 요구사항의 근거가
사라진다. 이 경우 evidence assessor의 `supported` 상태와 실제 공개 `retrieved_context`가
불일치한다.

## 확인한 연구

- [HiREC](https://aclanthology.org/2025.findings-acl.855/)은 반복 형식의 금융문서에서 유사한
  문서가 retrieval 정확성과 완전성을 떨어뜨린다고 보고, 문서→passage 계층검색과 evidence
  curation을 분리한다. 우리 구현은 외부 query generator 대신 결정론 planner와 requirement별
  선택 근거를 사용한다.
- [Towards Improved Multi-Source Attribution](https://aclanthology.org/2024.naacl-long.216/)은
  여러 출처를 종합하는 장문 답변의 attribution 자체가 별도 난점임을 다룬다. 따라서 여러 근거가
  검색됐다는 사실만으로 citation completeness를 가정하지 않는다.
- [Lost in the Middle](https://aclanthology.org/2024.tacl-1.9/)은 장문·다중문서 입력에서 관련
  정보의 위치에 따라 활용 성능이 달라질 수 있음을 보인다. 단순히 Context 한도를 키우는 것보다
  필요한 근거를 짧고 명시적인 section으로 보존한다.
- [More Documents, Same Length](https://aclanthology.org/2025.findings-emnlp.1064/)은 전체 길이와
  관련정보 위치를 통제해도 문서 수 증가 자체가 다수 모델의 RAG 성능을 낮출 수 있다고 보고한다.
  관련성이 낮은 후보를 더 넣는 방식은 완전성 개선책으로 사용하지 않는다.

## 프로젝트 적용

공개 Context는 다음 evidence family를 구분한다.

1. `calculation`: 입력·단위·율·결과·식·한계
2. `product`: 상품코드·판매 클래스·지표·값·기준일·source
3. `document`: 규칙·절차·세제의 citation header와 직접 excerpt

renderer는 한 family를 전부 넣은 뒤 남은 공간을 다음 family에 주지 않는다. 먼저 모든 필수
family에 예약 예산을 배정하고, 남는 예산만 세부 설명에 추가한다. 일반문서는 requirement별
`minimum_evidence_count`만 공개하고, structured 상품 근거가 있으면 중복 raw 상품 chunk를
제외한다.

`supported`로 판정된 근거가 실제 문자열에 남았는지는 별도 context-coverage 하네스에서 검사한다.
현재 스트레스 사례는 1,200자 안에 계산 결과, 두 상품 비교 행, 일반 규칙 citation을 함께 요구하며,
일반문서 3절과 structured+general 조합도 분리한다. 300자로 최소표현도 담을 수 없는 사례는
`complete=false`와 누락 section을 요구하고 backend가 `context_budget_exceeded`로 HCX 호출 전에
중단한다. required marker recall, forbidden marker rejection, budget adherence, completeness
decision accuracy를 각각 측정하고 평균으로 상쇄하지 않는다.

## 한계

- 문자 예산은 모델 tokenizer의 정확한 token 수가 아니다. HCX tokenizer를 외부 호출 없이
  사용할 수 없으므로 운영 한도보다 보수적인 문자 한도를 사용한다.
- 1,200자는 회귀용 스트레스 값이고 운영 기본은 7,500자다.
- 모든 요구사항의 최소 표현조차 예산보다 큰 경우 질문을 더 작은 답변 단위로 구성하거나 근거표를
  더 압축한다. 현재는 일부 근거로 생성하지 않고 `context_budget_exceeded` 한계응답을 반환한다.
- 현재 정상 3개와 실패 1개는 개발 회귀이며 독립 검수자가 작성한 더 큰 혼합질의 holdout이 필요하다.
