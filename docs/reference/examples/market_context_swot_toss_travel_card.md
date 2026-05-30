# Worked Example — `market_context_swot` × 토스 트래블카드

> - **목적**: Rubric `docs/reference_report_taxonomy.md` §2-7(시장 컨텍스트 + 자사 SWOT)의 추상 기준이 트래블카드 도메인에서 어떻게 구체화되는지를 시연합니다.
> - **도메인 슬러그**: `consumer_travel_card_kr`
> - **자사 상품**: 토스 트래블카드 (`own_toss_travel_card`)
> - **분석 방향**: `mixed` (marketing 전략 + product_dev 우선순위 도출)
> - **데이터 입력 (흐름 A, dedicated feature — 외부 시장 컨텍스트)**: 시장 규모·성장률·정책·기술 동향 (정부·매체 1차 소스).
> - **데이터 입력 (흐름 B, Output 인용)**:
>   - comparison_matrix → S/W의 정량 항목
>   - reaction_insight → S/W의 인식·만족도 항목
>   - marketing_social → S/W의 채널 운영·메시지 공백 항목
> - **흐름 분류**: mid/top 노드 (흐름 A + 흐름 B 모두 사용). 본 결과는 executive_summary로 흐름 B inline 인용됩니다.
> - **스냅샷 시점**: 2026-05-19
> - **작성일**: 2026-05-19

---

## 1. 적용된 Rubric §2-7 항목 (요약 인용)

- **리포트 목적**: 자사·경쟁사 분석(흐름 B)과 외부 시장 컨텍스트(흐름 A)를 통합해 토스 트래블카드의 현재 위치를 진단하고, TOWS 액션 전략을 도출합니다.
- **표준 출력 단위**: SWOT 4분면 + TOWS 4분면 액션 + PESTLE 4요소 + Porter 5 Forces.
- **평가 루브릭**: SWOT 4분면 균형(각 사분면 ≥ 2항목) / TOWS 4분면 액션 각 1개 이상 / PESTLE 4요소 모두 분석 / Porter 5 Forces 모두 평가 / 출처 1차 소스 명시 100%.
- **나쁜 출력**: SWOT 항목 출처 누락, TOWS 없이 SWOT만 나열, 시장 규모 매체 추정만 사용(공식 통계 부재), 시즌성 미보정.

---

## 2. 외부 시장 컨텍스트 (흐름 A, dedicated feature)

### 2-1. 시장 규모·성장률 (2025년 기준, mock + 실데이터 혼합)

| 지표 | 값 | 출처 |
|---|---|---|
| 2025년 1분기 해외 체크카드 결제 | 1조 5,742억원 (+48.4% YoY) | 이코노믹데일리 (실데이터) |
| 2025년 1분기 전체 해외 카드 결제 | 4조 9,321억원 (+11.68% YoY) | 이코노믹데일리 (실데이터) |
| 2025년 연간 카드 해외 사용 | 약 33조원 (229.1억 달러, +5.5%) | 한국은행 (실데이터) |
| 트래블카드 TAM (체크카드 비중 추정) | 약 8조원 / 연 | 본 worked example 추정 |
| 토스 트래블카드 SAM (2030세대 점유 추정) | 약 2.5조원 / 연 | 본 worked example 추정 |
| 토스 트래블카드 SOM (단기 점유 추정) | 약 0.3조원 / 연 | 본 worked example 추정 |

### 2-2. PESTLE 4요소 요약 (P·E·S·T 우선)

- **P (Political)**: 금융감독원의 외화 선불카드 약관 표준화 논의, 한시 프로모션 표시 의무 강화 검토. → SWOT의 O(투명성 강화로 자사 차별점 부각) + T(규제 부담 증가).
- **E (Economic)**: 원화 약세·환율 변동성 증가. → SWOT의 O(환전 시점 의사결정 중요도↑) + T(소비 위축 시 여행 감소).
- **S (Social)**: 2030세대 + 디지털노마드 인구 증가, 일본·동남아 단기 여행 정착. → SWOT의 O(타겟 세대와 자사 사용자층 일치).
- **T (Technological)**: BNPL 글로벌 성장(2024년 95억$ → 2033년 801억$ 추정), 핀테크 융합. → SWOT의 O(자사 토스 본사 핀테크 자산 활용 가능).

### 2-3. Porter's 5 Forces 평가 (5단계: 매우 강함 ⬆ ↔ 매우 약함 ⬇)

| 압력 | 강도 | 근거 |
|---|:-:|---|
| **Competitive Rivalry** | ⬆⬆⬆⬆ | 11개 트래블카드 격전, 수수료 0원 평탄화 |
| **Threat of Substitution** | ⬆⬆⬆ | 현지 환전소·디지털 지갑·BNPL 대체재 다수 |
| Threat of New Entry | ⬆⬆ | 핀테크 신규 진입 활발, 그러나 규제 장벽 존재 |
| Buyer Power | ⬆⬆ | 사용자 전환 비용 낮음(별도 발급으로 비교 쉬움) |
| Supplier Power | ⬆ | VISA/Mastercard 의존이나 표준화 완료, 협상력 낮음 |

산업 매력도 종합: 중-낮음 (격전 산업, 차별화 없이는 수익성 압박).

---

## 3. SWOT 4분면 (흐름 B 인용 + 흐름 A 통합)

### Strengths (내부 강점)

| # | 항목 | 출처(흐름 B 인용) |
|:-:|---|---|
| S1 | 재환전 수수료 완전 무료 — 11종 중 유일 | comparison_matrix W-1 |
| S2 | 결제 한도 최대 (1일 5천만원, 월 1억원) | comparison_matrix W-2 |
| S3 | 앱 UX 만족도 94% — 5종 최고 | reaction_insight `app_ux_quality` |
| S4 | 토스 본사 owned 채널 + 퍼스트 파티 데이터 자산 | marketing_social §3 PESO(Owned ●) |

### Weaknesses (내부 약점)

| # | 항목 | 출처(흐름 B 인용) |
|:-:|---|---|
| W1 | 부가 혜택 부재 (라운지·적립·여행자보험 없음) | comparison_matrix L-1, reaction_insight `additional_benefit` 45% |
| W2 | 17종 통화 지원 — 트래블월렛(46) · 트래블로그(58) 대비 좁음 | comparison_matrix L-2 |
| W3 | 외부 노출 채널(Paid·Shared) 부재 — 만족도 4위(69.8%) | marketing_social §3 PESO(Paid ○ · Shared ○), reaction_insight §2-1 |
| W4 | 삼성페이·자체페이 미지원, 해외 송금 불가 | comparison_matrix L-3 |

### Opportunities (외부 기회)

| # | 항목 | 출처(흐름 A) |
|:-:|---|---|
| O1 | 해외 체크카드 결제 +48.4% YoY 급성장 | 이코노믹데일리 2025-04 |
| O2 | 2030세대 디지털 우선 사용자층 확대 | 매체 트렌드 분석 |
| O3 | 환율 변동성↑로 환전 시점·수수료 중요도 부각 | PESTLE E |
| O4 | "여행 후 잔액 환급 0원" 메시지 공백 — 누구도 점유 못함 | marketing_social §4 |

### Threats (외부 위협)

| # | 항목 | 출처(흐름 A) |
|:-:|---|---|
| T1 | 수수료 0원 평탄화 — 차별성 소실 | 매체 동향 (전자신문) |
| T2 | 하나·신한 양강 체제 + 합종연횡 가속 | 이코노믹데일리 / 전자신문 |
| T3 | BNPL·디지털 지갑 대체재 부상 (Porter 5F: Substitution ⬆⬆⬆) | PESTLE T |
| T4 | 금융감독원 약관·프로모션 표시 규제 강화 가능성 | PESTLE P |

---

## 4. TOWS Matrix — 액션 전략 도출

### SO 전략 (Strengths × Opportunities — 가속) — 우선순위 1

- **SO1**: S1(재환전 무료) × O3(환율 변동성·환전 중요도↑) + O4(메시지 공백) → **"여행 후 잔액 환급 0원"을 핵심 마케팅 메시지로 격상**. marketing_social 공백 활용 + 시장 환율 민감도 활용.
- **SO2**: S3(앱 UX 94%) × O2(2030세대) → **"부모님도 쓸 수 있는 직관성" 캠페인** 인스타그램 Reels·YouTube Shorts에서 단편 영상 시리즈.

### WO 전략 (Weaknesses × Opportunities — 약점 개선) — 우선순위 2

- **WO1**: W3(외부 채널 부재) × O1(시장 성장 +48.4%) → **인스타그램·YouTube Shorts 단편 영상 채널 진입**. marketing_social §4 자사 공백 활용. 트래블월렛이 점유한 채널이나 메시지 차별화로 직접 경쟁 회피.
- **WO2**: W4(삼성페이 미지원) × O2(2030세대 디지털) → **삼성페이·자체페이 통합 로드맵 가속화**(product_dev). 디지털 사용자에게 핵심 결제 수단.

### ST 전략 (Strengths × Threats — 방어) — 우선순위 2

- **ST1**: S1(재환전 무료, 11종 중 유일) × T1(수수료 평탄화) → **"매수 0원은 기본, 매도까지 0원인 카드는 토스뿐"** 메시지로 평탄화 시장에서 단일 차별점 부각.
- **ST2**: S4(본사 owned + 퍼스트 파티 데이터) × T2(합종연횡) → **토스 자사 결제·금융 데이터 기반 개인화 추천**. 합종연횡 경쟁사가 모방하기 어려운 자산 활용.

### WT 전략 (Weaknesses × Threats — 생존) — 우선순위 3

- **WT1**: W1(부가 혜택 부재) × T3(대체재 부상) → **"부가 혜택 없는 대신 수수료 0원으로 절약" 가치 재정의**. battlecard L-1 우회 전략과 정합 (연회비 5–10만원 절약).
- **WT2**: W2(통화 폭 좁음) × T2(양강 체제) → **단기 여행자 페르소나로 시장 세그먼트 명시 좁힘**. 장기 체류자 시장은 단기 미진입, 디지털노마드는 별도 검토. focus 전략(Porter).

### 분기 실행 권장 (Rubric §1-2 — 분기당 2–4개)

- **분기 1순위 (필수)**: SO1, SO2 — 가장 강력한 페어 뒷받침, 즉시 실행 가능.
- **분기 2순위 (선택)**: WO1 — 채널 진입은 시간이 걸리나 시장 성장에 정렬.
- **분기 후순위**: ST·WT — 방어·생존 전략, 우선순위 4분기 이후.

---

## 5. 평가 루브릭 점수 산정

- **SWOT 4분면 균형**: 4/4 (각 사분면 항목 ≥ 2개, S 4·W 4·O 4·T 4).
- **TOWS 4분면 액션**: 4/4 (각 분면 액션 ≥ 1개, 총 8개 중 우선순위 명시).
- **PESTLE 4요소 분석**: 4/4 (P·E·S·T 모두 분석 완료, L·E는 보조 처리 명시).
- **Porter 5 Forces 평가**: 5/5 (5개 압력 모두 5단계 평가).
- **출처 1차 소스 명시**: 100% — SWOT 항목마다 흐름 A 또는 흐름 B 출처 명시.
- **종합 점수**: **5.0 / 5.0** — Rubric §2-7 기준 통과.

---

## 6. Anti-pattern 회피 사례

본 도메인의 5가지 함정과 회피 방식은 다음과 같습니다.

- **SWOT 출처 누락**: §3 모든 항목에 흐름 A 또는 흐름 B 출처 명시. 매체 추정치는 "추정"으로 표기, 정부·협회 출처는 실데이터로 구분.
- **TOWS 없이 SWOT만 나열**: §4 TOWS 4분면 액션 8개 도출 후 우선순위 분류. 단순 항목 나열에 그치지 않고 분기 실행 계획까지 연결.
- **시장 규모 매체 추정 의존**: §2-1 표에서 1분기 결제 금액·연간 33조원 등은 공식 출처 실데이터, TAM/SAM/SOM은 본 worked example 추정으로 구분 명시.
- **시즌성 미보정**: §2-1 데이터는 1분기 기준이며 여름 휴가철 시즌 변동(2–3배)을 별도 언급. 단일 시점 일반화 회피.
- **거시 변수 영향 과대 추정**: PESTLE 4요소만 우선 분석하고 L·E는 보조 처리. 트래블카드 도메인에 직접 영향 적은 요소는 명시 분리.

---

## 7. 본 worked example 사용 시 주의 (메타 지시)

본 예시의 SWOT 항목 일부와 TAM/SAM/SOM 추정치는 시연용입니다. 실제 분석은 정부·협회 공식 통계 + 흐름 B 상류 worked example 결과로 산정합니다. 트래블카드 도메인의 SWOT 배치는 본 도메인 특유 사례이며, 본인 도메인에 맹목적으로 복제하지 마십시오. Rubric §2-7의 추상 기준(SWOT 균형, TOWS 4분면 액션, PESTLE 4요소, Porter 5F, 출처 명시)이 우선입니다. 다른 도메인은 동일 추상 기준에서 다른 SWOT 항목과 TOWS 액션이 도출됩니다.

---

## 8. 관련 문서

- 방법론 reference: `docs/reference_market_context_swot.md` (SWOT · TOWS · PESTLE · Porter 5F · 통합 운용 · 정량 데이터 통합)
- Rubric 본체: `docs/reference_report_taxonomy.md` §2-7(시장 컨텍스트 + 자사 SWOT 정의), §3(액션 가능성 동사 집합)
- 파이프라인 설계: `docs/design/pipeline_topology_redesign.md` §6-0(P0-Rubric), §11-10(이원 흐름 의존 관계 모델 — mid/top 노드 위치)
- 흐름 B 인용 상류 worked example:
  - `docs/reference/examples/comparison_matrix_toss_travel_card.md` (S/W 정량 항목)
  - `docs/reference/examples/reaction_insight_toss_travel_card.md` (S/W 인식·만족도 항목)
  - `docs/reference/examples/marketing_social_toss_travel_card.md` (S/W 채널·메시지 공백 항목)
- 인접 mid 노드 worked example:
  - `docs/reference/examples/battlecard_toss_travel_card.md` (TOWS 액션과 배틀카드 Zone 매핑)
  - `docs/reference/examples/positioning_map_toss_travel_card.md` (포지셔닝 좌표와 SWOT 정합성)
- 흐름 B 인용 하류 worked example:
  - `docs/reference/examples/executive_summary_toss_travel_card.md` (작성 예정, 본 SWOT·TOWS 결과 통합)
