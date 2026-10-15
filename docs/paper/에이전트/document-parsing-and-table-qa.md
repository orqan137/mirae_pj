# 금융 PDF 표 추출·질의응답 연구

검토일: 2026-08-30

연금 Agent의 상품 비교 질문은 투자설명서의 비용·수익률·기간말 잔고 표를 읽어야 한다.
PDF에서 글자가 추출됐다는 사실만으로는 충분하지 않다. 각 값이 어느 클래스, 기간, 단위,
열 머리글에 속하는지를 복원할 수 있어야 한다.

## 연구가 확인한 문제

- [TableQuest](https://arxiv.org/abs/2412.09884)는 실제 금융보고서 문맥에서 표를 찾는
  능력과 복합·다단계 계산 능력을 분리해 평가한다. 실험 모델들은 사실 위치 찾기보다 복합
  추론과 다단계 계산에서 더 많이 실패했다.
- [FinanceBench](https://arxiv.org/abs/2311.11944)는 답과 evidence string/page를 함께
  둔 open-book 평가다. 관련 근거를 제공한 Oracle도 완벽하지 않았고, 연구진은 올바른 검색
  뒤에도 수치 추론 실패가 남는다고 보고했다.
- [금융 PDF parsing/chunking 실험](https://arxiv.org/abs/2604.12047)은 금융 QA를
  최종 과제로 두고 parser와 chunking의 결합을 비교한다. parser 평가는 문자 유사도가 아니라
  downstream 답 정확도까지 이어져야 한다.
- [PDF 표 추출 벤치마크](https://arxiv.org/abs/2603.18652)는 100개 합성 PDF의 451개
  표를 평가했다. 핵심 판정은 출력만 보고 모든 cell-to-header 대응을 모호하지 않게 복원할 수
  있는지다. 단, arXiv 표를 합성한 데이터이므로 한국어 투자설명서 성능을 그대로 뜻하지 않는다.

마지막 연구의 공개 구현은 LLM judge가 사람 평가와 높은 상관을 보였다고 보고하지만,
[`pdf-parse-bench`](https://github.com/phorn1/pdf-parse-bench)는 기본 실행에 OpenRouter
API를 요구한다. 이 프로젝트에서는 외부 judge를 바로 연결하지 않는다. 동일한 판정 원칙만
가져와 사람 검수·결정론 검사·허용된 HCX 평가로 재구성한다.

## 현재 코퍼스에서 직접 확인한 실패 양상

주최 측 원본은 외부로 전송하지 않고 로컬에서만 확인했다.

| 샘플 | 원본 구조 | 현재 평탄화 결과의 위험 |
|---|---|---|
| `KR510902511M` p.3 | 클래스별 판매수수료·총보수와 1/2/3/5/10년 비용, 병합 머리글 | 숫자는 남지만 열 경계가 형식 계약이 아니어서 비용 종류를 바꿔 붙일 수 있음 |
| `KR5118201004` p.51 | 한 페이지에 여러 클래스별 기간초·설정·환매·기간말 표 | 반복 머리글과 다음 섹션 제목이 한 page text에 섞임 |
| `KR5120450015` p.62 | 이미지형 표 4개, 병합 머리글, 클래스 C2/C3/C4/Ce | OCR이 클래스명을 `1355<2` 등으로 훼손하고 `금액`을 `극액`으로 인식; 구조화 index의 class label도 비어 있음 |
| `KR5147430065` p.42 | 신규 설정으로 실적 없음 | 빈 표를 0%·0원으로 바꾸지 않고 `not_available`로 보존해야 함 |
| `KR5123490013/16/17` | 하나의 동일 PDF에 상품코드 alias 3개 | parser 결과를 공유하되 세 코드 identity는 모두 보존해야 함 |

현재 `pdftotext -layout + selective Tesseract` 방식의 장점도 분명하다. 로컬 실행,
페이지 전수 보존, 원본 hash와 extractor fingerprint, 중복 PDF 재사용, 희소 페이지 OCR이
이미 구현돼 있다. 문제는 다음 네 가지다.

1. 좌표, cell, rowspan/colspan이 저장되지 않는다.
2. OCR 여부를 의미문자 수로만 판단하므로 글자는 많지만 표 구조가 깨진 페이지를 찾지 못한다.
3. OCR 후보 선택도 문자 수 비교라서 숫자·단위·클래스 정확성을 보장하지 않는다.
4. 이후 evidence extractor가 평탄화된 줄 주변을 잘라 쓰므로 잘못된 열 대응을 검증할 원본
   구조가 없다.

## 평가 단위

parser의 산출물 수나 추출 문자 수를 성공 지표로 쓰지 않는다. 골드 표마다 다음을 측정한다.

- cell value exact match: 쉼표, 소수점, 음수, `%`, `-`와 빈칸을 구분
- header binding: 각 값이 클래스·행·열·기간·단위에 유일하게 연결되는 비율
- span preservation: 병합 머리글의 범위가 보존되는지
- metadata: source hash, 상품코드/alias, 페이지, bbox, 표 번호, 추출 방식
- omission/duplication: 원본 cell 누락과 OCR/native text 이중 삽입
- downstream QA: 직접 조회, 클래스 비교, 기간 비교, 단위 환산, 계산 불가 판정
- 운영비용: 1페이지/1문서 처리시간, peak RAM/VRAM, model artifact와 이미지 증가량

수치 정확도와 header binding은 평균으로 상쇄하지 않는다. 추천이나 상품 비교에 쓰는 표는
둘 중 하나라도 기준 미달이면 `review_required`로 격리한다.

## 최소 골드셋

초기에는 100개 전체를 수작업 표기하지 않는다. 템플릿 13종과 실패 유형을 층화해 20~30쪽을
선정하고, 페이지마다 핵심 cell 10개 이상과 머리글 경로를 기록한다.

- 텍스트형 비용표와 병합 머리글
- 클래스가 여러 개인 기간말 잔고표
- 이미지형 표와 작은 한글·숫자 OCR
- 다음 페이지로 이어지는 표
- `-`, 0, 빈칸, 해당사항 없음
- 신규 설정이라 실적 자체가 없는 문서
- 동일 PDF를 공유하는 여러 상품코드

parser 선택 후에도 이 골드셋은 corpus revision마다 다시 실행한다. 원본 PDF hash 또는 parser
revision이 바뀌면 이전 합격 결과를 재사용하지 않는다.

## 도출

1. native text와 OCR을 문서 단위가 아니라 페이지·표 단위로 라우팅한다.
2. 원본 text layer와 OCR layer를 합쳐 버리지 말고 각각 보존해 cell 단위로 대조한다.
3. 검색용 text와 검증용 structured table을 분리한다. 답에 수치를 쓸 때는 structured cell을
   우선하고, 원문 페이지/bbox를 함께 반환한다.
4. Markdown은 표시용일 뿐 canonical 형식이 아니다. rowspan/colspan, cell 좌표, 원시 text를
   보존하는 JSON 또는 HTML을 canonical evidence로 둔다.
5. 표가 모호하면 LLM에게 복원을 맡기지 않는다. 원문 한계와 검수 상태를 노출하고 비교를
   중단한다.

구현 후보와 채택 판단은
[`../../devlop/architecture/document-ingestion-options.md`](../../devlop/architecture/document-ingestion-options.md)에 정리한다.
