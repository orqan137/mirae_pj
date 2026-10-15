# Query routing contract

상태 판정과 응답 전략은 [`decision-policy.md`](decision-policy.md), 허용 행동과 도구 호출은
[`action-boundaries.md`](action-boundaries.md)와 [`tool-configuration.md`](tool-configuration.md)를
함께 따른다.

## Route labels

Every question receives one or more labels:

- `system`: DB/DC/IRP/pension-savings concepts and rules
- `tax`: deductions, pension income tax, deferred tax, and calculations
- `combined`: multiple systems, accounts, or tax stages
- `procedure`: enrollment, transfer, withdrawal, claim, or required documents
- `product_explanation`: one product's structure, risks, fees, or strategy
- `product_comparison`: two or more products on aligned axes
- `conditional_recommendation`: recommendation that depends on customer conditions

Also record `closed` versus `open`, `answerable` versus `needs_conditions` versus
`out_of_corpus`, and whether deterministic calculation is required.

## Retrieval plan

- System/tax/procedure: search the general-document index with product/account, event, and
  effective-date filters.
- Product explanation: exact-match product code/name, then retrieve cover, summary information,
  strategy, risk, fee, and relevant detailed sections.
- Product comparison: resolve product identities first; query structured fee/performance/balance
  tables on the same class, date, and period; retrieve narrative risk/strategy evidence afterward.
- Combined: run each relevant branch and join only after checking compatible scope and dates.
- Conditional recommendation: identify missing account, horizon, risk tolerance, liquidity, and
  objective, then apply account-class/risk/objective hard filters before producing a bounded
  candidate shortlist. Do not call it a statutory suitability decision or universal ranking.

## Evidence sufficiency gate

Before answer generation, verify:

1. every requested sub-question has evidence or an explicit information-limit response;
2. every consequential number has class, unit, basis date, and source coordinates;
3. no evidence concerns a different pension system, old product, customer audience, or period;
4. all cited passages are actually used in the answer;
5. a misleading premise is identified rather than inherited.

## Generation and validation

Use only HyperCLOVA X for LLM stages. Prefer deterministic code for arithmetic, schema checks,
exact filtering, and table comparison. Validate claims against retrieved evidence, then render
the five-string API response. If evidence is insufficient, state the boundary and request the
specific missing condition instead of making a generic or unconditional recommendation.

The deterministic tax calculator is intentionally limited to the doc41 pension-savings/IRP
credit rule. It accepts only explicit annual account-by-account contributions written with
Arabic numerals and `원`/`만원`, plus exactly one explicit `총급여` or `종합소득금액`. Monthly
amounts, an unallocated combined contribution, threshold ranges, Korean number words, employer
contributions, and other tax scenarios must return the specific missing inputs without a
personal calculation.
