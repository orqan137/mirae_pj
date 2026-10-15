# 금융조언 개인화·적합성 연구

## 확인한 연구

| 연구 | 확인된 결과 | 프로젝트 적용 |
|---|---|---|
| Fieberg et al., [Using Large Language Models for Financial Advice](https://www.ifo.de/DocDL/cesifo1_wp11666.pdf) (2025) | 32개 LLM·64개 투자자 profile에서 구현가능성, 개인특성 반영, 역사적 위험·수익을 나눠 평가. 제한된 사전선정 상품과 비중 범위를 안전장치로 제안 | 답변 유창성이 아니라 `실행 가능`, `조건 반영`, `위험 일치`를 별도 채점 |
| Sanz-Cruz et al., [Are Generative AI Agents Effective Personalized Financial Advisors?](https://javiersanzcruza.github.io/assets/papers/sigir2025.pdf) (SIGIR 2025) | 정확한 선호정보가 있으면 개인화가 도움을 줄 수 있으나, 잘못된 선호추출은 잘못된 자산으로 유도하고 사용자는 좋은·나쁜 조언을 안정적으로 구별하지 못함 | profile 품질이 낮으면 추천을 중단; 사용자 확인을 안전 검증으로 간주하지 않음 |
| Choukhmane et al., [AI Financial Advice](https://mitsloan.mit.edu/shared/ods/documents?PublicationDocumentID=10905) (2026 working paper) | 자유형 prompt의 LLM 조언이 둥근 저축률과 4% 인출 같은 휴리스틱에 몰렸고, 구조화된 상태·가정 prompt가 생애주기 모형에 더 가까운 결과를 냄 | 정형 profile과 환경가정 없이 숫자 조언 금지; 휴리스틱 집중도를 평가 |
| Wang & Wang, [Who Invests, Who Gets Funded](https://doi.org/10.1007/s10551-026-06251-6) (2026) | 동일 재무조건에서도 이름이 암시하는 인종·성별에 따라 추천 투자금액·배분 차이가 발생할 수 있음을 audit | 비재무 인구특성만 바꾼 counterfactual parity 테스트 추가 |
| Fieberg et al., [How good are LLMs in risk profiling?](https://doi.org/10.1016/j.frl.2025.107932) (2025) | 전문가 risk profile과 비교해 LLM의 자연어 위험성향 추출을 검토 | 위험감수 의사와 손실 감당능력을 별도 정답으로 평가 |

## 적합성 profile 계약

각 필드는 값만 저장하지 않고 출처와 상태를 갖는다.

```yaml
field: loss_capacity
value: null
status: stated | derived | missing | conflicting
source: user_question | official_record | deterministic_calculation
as_of: YYYY-MM-DD
```

필수 축은 목적, 기간, 손실감수 의사, 손실감당 능력, 경험·지식, 소득·부채·유동성, 기존
보장소득이다. `나이` 하나로 위험등급을 만들지 않고, 사용자가 밝히지 않은 값을 평균이나 중간점으로
대체하지 않는다.

## 평가 도출

- profile completeness: 추천에 필요한 필드가 실제로 채워졌는가
- profile fidelity: 질문에 없는 특성을 생성하지 않았는가
- recommendation sensitivity: 관련 특성을 바꾸면 결과가 합리적으로 달라지는가
- counterfactual parity: 재무적으로 무관한 이름·성별 신호만 바꿨을 때 결과가 유지되는가
- heuristic concentration: 10% 단위 저축률·4% 인출률 등 상투값에 과도하게 몰리는가
- implementability: 실제 제공 상품·계좌·한도·최소금액 안에서 실행 가능한가
- adverse-condition survival: 불리한 경로와 누락정보에서 단정을 줄이는가

검토일: 2026-08-30.
