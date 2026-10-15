# 외부 자료 거버넌스

## 상태 모델

```text
discovered
  → catalog_only
  → reviewed
  → approved
  → indexed
  → retired
```

- `discovered`: 링크만 발견
- `catalog_only`: 출처와 기본 메타데이터 확인
- `reviewed`: 내용·기준일·권위·라이선스 검토
- `approved`: 대회 규정과 데이터 정책상 사용 승인
- `indexed`: 별도 외부 코퍼스 버전으로 생성
- `retired`: 개정·정정·만료로 사용 중단

## 주최 측 자료와 분리

외부 자료를 원본 자료 영역이나 기존 `data/knowledge/`에 바로 넣지 않는다. 승인된 경우에도
다음처럼 별도 source group과 manifest를 사용한다.

```text
external_materials/          # 승인 후에만 생성
data/external_knowledge/     # 별도 파생 코퍼스
```

현재 저장소에는 이 두 경로를 만들지 않는다. 대회 사용 승인 전 빈 구조가 존재하면 운영자가 이미
허용된 자료로 오해할 수 있기 때문이다.

## 검색 우선순위

1. 주최 측 과제 지시와 제공자료
2. 승인된 최신 공지·Q&A
3. 현행 법령·세무·감독기관 원문
4. 공식 통계
5. 공식 상품설명서·약관·공시
6. 연구·산업 리포트
7. 뉴스

상위 자료와 하위 자료가 충돌하면 상위를 사용하고 충돌을 기록한다. 적용일을 알 수 없는 자료는
세율·한도·수익률 같은 시간 민감 답변에 사용하지 않는다.
