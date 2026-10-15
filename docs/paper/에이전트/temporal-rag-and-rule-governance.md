# 시간 인식 RAG와 연금 규칙 적용범위

연금·세제·절차 문서는 같은 키워드를 포함해도 시행일, 적용 대상, 관할이 다르면 답변 근거가
아니다. 이 문서는 외부 연구를 대회 제공자료의 런타임 코퍼스에 섞지 않고 설계 근거로만 검토한다.

## 검토 자료

- [MRAG: A Modular Retrieval Framework for Time-Sensitive Question Answering](https://aclanthology.org/2025.findings-emnlp.167/)
  (Findings of EMNLP 2025): 질문 내용과 시간 제약을 분리하고 의미·시간 관련성을 별도로 평가하는
  retrieval 구조와 TempRAGEval을 제안한다.
- [It’s High Time: A Survey of Temporal Question Answering](https://aclanthology.org/2026.acl-long.1332/)
  (ACL 2026): corpus temporality, question temporality, time-expression 정규화와 변화하는 사실의
  평가를 하나의 문제로 정리한다.
- [It’s About Time: Incorporating Temporality in Retrieval Augmented Language Models](https://arxiv.org/abs/2401.13222)
  (preprint): 의미 유사도만으로 여러 시점의 문서를 선택하는 한계를 다루고 시간 관련성을 함께
  사용하는 TempRALM을 제안한다.
- [Temporal Misgrounding in Legal RAG](https://arxiv.org/abs/2608.09393)
  (2026-08 preprint): 날짜에 적용되는 법령 버전 대신 실제이지만 적용 불가능한 버전을 인용하는
  실패를 별도 평가한다. 최신 preprint이므로 수치는 설계 정답이 아니라 위험 신호로만 사용한다.

## 프로젝트 도출

1. 질문의 시간 표현을 의미 검색과 별도로 정규화한다.
2. `published_date`, 문서 `reference_date`, `effective_from/to`, 파일 수정일을 서로 대체하지 않는다.
3. 명시 연도·날짜 질의에는 유효기간이 확인된 문서만 결론 근거로 사용한다.
4. 날짜가 없는 질문은 고정된 `knowledge_as_of`를 사용하고, 시행기간 미확인 사실을 Context와
   답변에 보존한다.
5. 고객·회사 담당자·내부 업무담당자와 KR·외국 관할을 검색/근거판정 양쪽에서 검사한다.
6. 시간 관련성은 의미 관련성을 대체하지 않는다. 날짜가 맞아도 세액공제 질문에 다른 연금 규칙을
   승격하지 않도록 topic anchor를 함께 요구한다.
7. 모델이 날짜를 선택하게 하지 않고 deterministic gate가 선택한 근거만 HCX에 전달한다.
8. 평가에는 과거/미래·시행일 미상·관할 불일치·대상 불일치 hard negative를 둔다.

## 현재 반영

- `data/structured/general_source_governance.json`: 일반문서 58개 전수 metadata coverage
- `app/source_governance.py`: 질문 시간·대상·관할 분석과 applicability 판정
- planner v15/retrieval-only query-anchor v4 / assessor v10: 절별 topic·scope coverage, 최소 도구집합, 안전한 문맥·구어체 검색 query, 경고와 제외 사유 trace
- 검색: 명시 날짜에 적용되는 버전을 우선하되 부적합 후보는 assessor가 감사 가능하게 차단
- grounding: 시행기간 미상, 외국 관할, 시점 불명확, 3절 복합질의를 포함한 14개 회귀

## 남은 한계

- 주최 측 일반문서 대부분은 시행일이 명시되지 않아 숫자 연도 질의에 보수적으로
  `out_of_corpus`를 반환한다.
- 문서 단위 audience는 혼합 문서의 chunk별 고객/내부 범위를 완전히 대체하지 못한다.
- 동일 적용범위의 자연어 규칙 충돌은 정규화된 rule assertion 없이는 결정론적으로 판정할 수 없다.
- 외부 법령을 운영 코퍼스로 승격하려면 대회 허용 여부와 출처·라이선스·시행일 검수가 별도로
  필요하다.

검토일: 2026-08-30.
