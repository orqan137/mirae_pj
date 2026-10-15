# 인출·장수·유동성 위험 연구

## 연구 지도

| 연구 | 핵심 | 우리 설계에 주는 제한 |
|---|---|---|
| OECD, [Designing the payout phase](https://www.oecd.org/en/publications/oecd-pensions-outlook-2024_51510909-en/full-report/component-8.html) (2024) | 관리형 인출은 투자·장수위험에 맞춰 지급액을 조정할 수 있으나 유연성이 줄 수 있고, 보장소득이 부족한 경우 생애소득 옵션이 default가 될 수 있음 | 종신소득·분할인출·일시금을 `소득 안정성 대 유동성` 축에서 비교 |
| Clare et al., [Measuring sequence of returns risk](https://openaccess.city.ac.uk/id/eprint/25138/) (2020/2021) | 평균수익률·Sharpe/Sortino만으로 인출기 순서위험을 직접 측정하기 어려우며 인출경험에 맞춘 지표가 필요 | 같은 평균수익률의 순서를 바꾼 스트레스 경로를 별도 평가 |
| Horan, [Optimal withdrawal frequency](https://doi.org/10.1002/cfp2.1183) (2024) | 인출 빈도와 동적·조건부 전략이 지속가능성에 영향을 줌 | 연간 고정 인출 하나를 보편 규칙으로 제시하지 않음 |
| OECD, [Liquidity in Retirement Savings Systems](https://www.nber.org/papers/w21168) (2015) | 노후계정 유동성은 자기통제·비상수요·제도목표 간 절충 | 중도인출을 무조건 오류로 분류하지 않고 일반 유동자산과 함께 평가 |
| Horneff et al., [Variable annuities with liquidity and downside protection](https://www.nber.org/papers/w19206) (2013) | 유동성·하방보호가 있는 연금상품의 생애주기 선택이 개인별 시장·소득 경로에 따라 달라짐 | 상품 이름만으로 보장효과를 추정하지 않고 보장조건·비용·현금화 조건 확인 |

## 시나리오 구조

인출 계산기는 단일 `성공률` 대신 다음을 함께 반환해야 한다.

- 필수지출 중 국민연금·DB·종신연금으로 충당되는 비율
- 초기 인출률과 최저 실질소득
- 고갈 연령 분포와 고갈 전 감액 횟수
- 최악 초기수익률 경로에서의 필수·재량지출
- 즉시 사용 가능한 유동자산과 비상지출 후 잔액
- 사망 시점별 잔여자산과 유산목표 충족 여부
- 사용한 생존율·물가·수익률·비용 가정

## 도출된 안전 규칙

1. `4%`, 특정 연령 또는 평균 기대수명을 보편 기본값으로 자동 적용하지 않는다.
2. 기대수익률 하나로 projection하지 않고 결정론 base, 불리한 고정 경로, seed 고정 확률경로를 분리한다.
3. 필수지출과 재량지출을 분리하지 않은 동적 인출 결과는 적합성 판단에 사용하지 않는다.
4. 보장소득이 필수지출을 충당하는지 보기 전 전액 연금화 또는 전액 투자 인출을 권하지 않는다.
5. 시뮬레이션 성공률에는 데이터 기간, seed, 경로 수와 실패 정의를 함께 표시한다.

검토일: 2026-08-30.
