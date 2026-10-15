# Agent·RAG·금융 QA 연구 지도

## 1. 기초 연구

| 연구 | 핵심 패턴 | 이 프로젝트의 제한된 채택 |
|---|---|---|
| Lewis et al., [RAG](https://arxiv.org/abs/2005.11401) (2020) | 외부 비모수 지식 검색과 생성 결합 | 주최 측 코퍼스 검색과 생성 분리 |
| Yao et al., [ReAct](https://arxiv.org/abs/2210.03629) (2022) | 추론과 행동의 반복 | 원시 추론 공개 없이 구조화 상태·도구 결과만 사용 |
| Asai et al., [Self-RAG](https://arxiv.org/abs/2310.11511) (2023) | 검색 필요성·근거·응답을 자기평가 | 학습 토큰 대신 결정론적 evidence gate로 축소 |
| Yan et al., [CRAG](https://arxiv.org/abs/2401.15884) (2024) | 검색결과 평가와 보정 검색 | 외부 웹 확장 없이 허용 코퍼스 내 재작성·재검색 |
| Singh et al., [Agentic RAG Survey](https://arxiv.org/abs/2501.09136) (2025) | 계획·도구·메모리를 포함한 Agentic RAG 분류 | 필요한 도구만 고정 graph로 구현 |
| Ammann et al., [Question Decomposition for RAG](https://aclanthology.org/2025.acl-srw.32/) (2025) | 하위질문별 검색과 후보 병합·rerank가 복합질의 근거 coverage를 개선 | 결정론적 절 분해, 절별 검색, 라운드로빈 병합 |
| Anantha et al., [QReCC](https://aclanthology.org/2021.naacl-main.44/) (2021) | 생략·코어퍼런스 질문의 rewrite, retrieval, reading을 분리 평가 | 요청 내부 생략절에 확인된 연금 주제만 검색용으로 승계 |
| Wang et al., [Query2doc](https://aclanthology.org/2023.emnlp-main.585/) (EMNLP 2023) | pseudo-document 확장으로 질의·문서 어휘 불일치 완화 | 별도 생성 호출 없이 재현된 구어체 실패에만 감사 가능한 공식용어 anchor 적용 |

## 2. 금융 문서 검색과 근거 정제

| 연구 | 확인된 핵심 | 프로젝트 적용 |
|---|---|---|
| Choe et al., [HiREC](https://aclanthology.org/2025.findings-acl.855/) (ACL Findings 2025) | 표준화된 금융문서의 반복 구조가 유사 passage 오검색을 만들며, 문서→passage 계층 검색과 evidence curation이 개선책 | `source group/문서 선택 → 문서 내부 블록 검색 → 중복 제거 → 누락 질의 재검색` |
| Islam et al., [FinanceBench](https://github.com/patronus-ai/financebench) (2023) | 공개 150문항에 질문·정답·근거 페이지를 제공하고 RAG의 금융 QA 취약성을 보임 | 질문별 gold source/page와 금지 주장을 가진 회귀셋 |
| Chen et al., [FinTextQA](https://aclanthology.org/2024.acl-long.328/) (ACL 2024) | 출처가 연결된 장문 금융 QA와 embedder/retriever/reranker/generator 평가 | 검색·rerank·생성 단계를 별도 측정 |
| Choi et al., [FinAgentBench](https://openreview.net/forum?id=BIyBik7J5u) (2025/2026) | 문서 종류 선택과 핵심 passage 선택을 나눠 agentic retrieval 평가 | `문서선택 정확도`와 `근거블록 recall`을 분리 |
| Choe et al., [HiREC](https://aclanthology.org/2025.findings-acl.855/) (ACL 2025) | 금융 표준문서의 계층검색과 evidence curation | requirement별 최소 근거와 중복 후보 제거 |
| Patel et al., [Multi-Source Attribution](https://aclanthology.org/2024.naacl-long.216/) (NAACL 2024) | 여러 출처를 합성하는 장문 답변의 attribution 난점 | 일반·상품·계산 근거 family별 공개 Context 완전성 검사 |

## 3. 수치·표 추론

| 연구/데이터셋 | 규모·특징 | 프로젝트 적용 |
|---|---|---|
| Chen et al., [FinQA](https://github.com/czyssrs/FinQA) (EMNLP 2021) | 8,281 QA와 supporting facts·실행 가능한 reasoning program | 답만 비교하지 말고 입력 숫자·연산식·실행결과 검증 |
| Zhu et al., [TAT-QA](https://github.com/NExTplusplus/TAT-QA) (ACL 2021) | 16,552문항, 표+텍스트, 산술·비교·정렬 | 상품 표와 본문을 함께 읽고 symbolic operator 사용 |
| Chen et al., [ConvFinQA](https://github.com/czyssrs/ConvFinQA) (EMNLP 2022) | 연속 질의에서 이전 계산 결과가 다음 질문에 연결 | 평가 API는 단일 질의지만 복합 하위 계산의 오류 전파 테스트에 활용 |
| Pan et al., [TableQuest](https://arxiv.org/abs/2412.09884) (NeurIPS Workshop 2024) | 실제 금융보고서 문맥의 표 찾기와 복합·다단계 계산을 함께 평가 | 직접 조회와 다단계 비교를 분리하고 원문 표 구조를 보존 |
| Horn & Keuper, [PDF table extraction benchmark](https://arxiv.org/abs/2603.18652) (2026) | cell 내용과 header 구조를 함께 보는 semantic 평가; 합성 PDF라는 한계 | 주최 측 한국어 PDF 골드셋에서 cell exact match와 header binding을 우선 측정 |

## 4. Agent 평가와 금융 안전

| 연구/자료 | 확인된 핵심 | 프로젝트 적용 |
|---|---|---|
| Bigeard et al., [Finance Agent Benchmark](https://arxiv.org/abs/2508.00828) (2025) | 537개 전문가 문항에서 당시 최고 시스템도 46.8% 정확도라는 한계를 보고 | 금융 Agent는 기본적으로 실패 가능하다고 보고 fail-closed 설계 |
| Microsoft, [Finance Agent Benchmark implementation](https://github.com/microsoft/FinanceBenchmark) | 설정 hash, 재실행 결과 재사용, rubric assertion, groundedness·citation 등 다축 평가 | 평가 설정·코퍼스·모델 지문과 결과를 함께 저장 |
| Ru et al., [RAGChecker](https://arxiv.org/abs/2408.08067) (NeurIPS 2024) | claim 단위로 답 정확성·완전성, retrieval, faithfulness를 분리 | 하나의 groundedness 대신 네 경계의 실패를 따로 기록 |
| Gao et al., [ALCE](https://arxiv.org/abs/2305.14627) (EMNLP 2023) | citation correctness와 completeness를 별도 평가 | 중요 claim에 인용이 있는지와 그 인용이 실제 지지하는지를 분리 |
| [FinRank](https://arxiv.org/abs/2608.07400) (2026) | 같은 회사·다른 연도 등 금융 hard negative를 수작업 구성 | 같은 상품의 다른 클래스·기간·비용축으로 독립 fixture 생성; 데이터 자체는 비상업 라이선스로 미사용 |
| [FinToolBench](https://arxiv.org/abs/2603.08262) (2026) | 실행 가능한 금융 도구학습 Agent의 현실적·감사 가능한 평가를 목표 | 계산 도구의 schema·호출 정확도·결과 검증을 독립 점수화 |
| [Benchmarking LLM Agents for Wealth-Management Workflows](https://arxiv.org/abs/2512.02230) (2025) | 수학보다 종단간 워크플로 신뢰성이 병목이며 결정론적 grader를 사용 | 단계별 성공률과 전체 conversation survival을 함께 측정 |
| 금융위원회, [금융분야 AI 가이드라인 개정](https://www.fsc.go.kr/po010101/87142) (2026) | 보조수단성, 신뢰성, 금융안정성, 소비자 이익, 보안성 등 7대 원칙 | 평가 Agent는 행동·거래를 하지 않고 근거 기반 보조수단으로 제한 |
| UC Berkeley, [BFCL](https://sky.cs.berkeley.edu/project/berkeley-function-calling-leaderboard/) | 함수 선택과 function relevance, 복수·다단계 호출을 분리 평가 | route/source group/정확한 도구집합과 비호출 음성 사례 |
| Apple, [ToolSandbox](https://arxiv.org/abs/2408.04682) | 정보 부족과 상태 의존성, 임의 trajectory의 milestone 평가 | 조건 부족·중간 단계·최종 답을 별도 gate로 측정 |
| Yao et al., [τ-bench](https://openreview.net/forum?id=roNSXZpUDN) | 도메인 정책을 따르는 도구 trajectory와 최종 상태 평가 | 정책 계약과 `/answer` 종단 평가를 함께 요구 |

## 5. 논문을 운영에 옮길 때의 주의

- 다른 LLM을 사용한 논문의 성능표는 HyperCLOVA X 성능으로 간주하지 않는다.
- SEC 보고서 벤치마크의 숫자 추론 패턴은 참고할 수 있지만 한국 연금 규칙으로 전이하지 않는다.
- LLM-as-judge만으로 통과시키지 않는다. schema, 숫자, 날짜, 상품코드, 근거 좌표는 코드로 검사한다.
- multi-agent의 역할 수가 정확성을 보장하지 않는다. 각 역할의 독립 입력·출력·실패 기준이 있어야 한다.
- 원시 chain-of-thought를 저장하거나 평가 API의 `think_trace`로 반환하지 않는다.
