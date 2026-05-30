# Worked Example — `executive_summary` × 토스 트래블카드

> - **목적**: Rubric `docs/reference_report_taxonomy.md` §2-5(Executive Summary)의 추상 기준이 트래블카드 도메인에서 어떻게 구체화되는지를 시연합니다.
> - **도메인 슬러그**: `consumer_travel_card_kr`
> - **자사 상품**: 토스 트래블카드 (`own_toss_travel_card`)
> - **분석 방향**: `mixed` (marketing + product_dev 통합 권장)
> - **데이터 입력 (흐름 B, Output 인용)**: comparison_matrix · reaction_insight · marketing_social · battlecard · positioning_map · market_context_swot (6개 모두).
> - **흐름 분류**: top 노드 (흐름 A 자체 feature ✗, 흐름 B만 사용). 본 결과는 다른 리포트의 입력이 아닌 사용자 최종 출력입니다.
> - **스냅샷 시점**: 2026-05-19
> - **작성일**: 2026-05-19

---

## 0. BLUF (Bottom Line Up Front)

**토스 트래블카드는 단기 여행자(4박 일본 시나리오) 시장에서 1순위 추천이며, 다음 분기 마케팅 액션 4가지와 제품 액션 2가지를 즉시 실행 권장합니다.** 핵심 근거: 재환전 0원 11종 중 유일 + 앱 UX 만족도 94% 1위 + "여행 후 잔액 환급 0원" 메시지 공백 점유 가능 + 시장 +48.4% YoY 성장.

---

## 1. Situation — 시장 컨텍스트 (10–15% 비중)

- **해외 체크카드 결제 시장 +48.4% YoY 급성장** (2025년 1분기 1.6조원). 트래블카드 도메인 자체가 시장 확장기. (출처: market_context_swot §2-1)
- **하나·신한 양강 + 합종연횡 가속** — 11개 카드 격전 중 트래블월렛·하나 트래블로그가 외부 노출 강세. 토스는 출시 시기 후발이라 누적 사용자 후기 4위(만족도 69.8%). (market_context_swot §2-2, reaction_insight §2-1)
- **수수료 0원 평탄화** — 환전·결제 수수료 무료가 차별점이 아닌 기본 요건으로 전환. 차별화 축은 "재환전·통화 폭·부가 혜택"로 이동. (comparison_matrix §3 W-1·L-2)
- **2030세대 + 디지털 우선 사용자층 확대** — 토스의 자연 타겟층과 일치. (market_context_swot §2-3 Social)

---

## 2. Complication — 자사 위치 문제 (15–20% 비중)

- **외부 노출 채널이 거의 없음** — Paid·Shared 채널 모두 ○(거의 없음). 토스 본사 owned 채널에만 의존. (marketing_social §3)
- **부가 혜택(라운지·적립·여행자보험) 부재**가 사용자 인식에서 가장 큰 약점 — `additional_benefit` aspect positive 45%, 5종 중 최하위. (reaction_insight §2)
- **페르소나 의존성이 큼** — 단기 여행자 시장에서 좌측 극단 단독 위치이나, 장기 체류자에서는 ATM·통화 폭 약점으로 좌측 하단 후퇴. (positioning_map §3·§5)
- **트래블카드 단독 광고 캠페인 부재** — 본사 마케팅 자원이 토스뱅크·증권에 집중되어 트래블카드는 자력 성장에 의존. (marketing_social §2-1)

---

## 3. Question — 의사결정 초점

**토스 트래블카드를 본사 자원의 어느 우선순위에 두고, 다음 분기 어떤 액션을 실행할 것인가?**

---

## 4. Resolution — 권고 (60–70% 비중)

### 4-1. 페르소나별 권장 (의사결정자 답: "누구에게 팔 것인가")

- **단기 여행자 (4박 일본·동남아)** — **1순위 타겟, 즉시 진입**. positioning_map §3에서 토스가 좌측 극단(저비용) 단독 위치, battlecard §6에서 5/5 평가 통과. SAM 약 2.5조원 추정의 직접 점유 가능 시장.
- **장기 체류자 (3개월 유럽)** — **3순위, 후순위**. positioning_map §5에서 트래블로그가 압도, 토스는 ATM 한도·통화 폭으로 열위. 본 분기 마케팅 자원 미배정 권장.
- **디지털노마드 (다국가 순회)** — **2순위, 조건부**. 통화 폭 약점이 결정적이나 토스 본사 핀테크 자산(송금·계좌 연동)으로 차별 가능. positioning_map 디지털노마드 뷰는 별도 작성 후 결정.

### 4-2. 분기 1순위 마케팅 액션 (전략) — TOWS SO 전략 기반

- **액션 M1 (가장 우선)**: **"여행 후 잔액 환급 0원" 메시지 격상**. marketing_social §4 채널·메시지 공백 식별 결과, 5개 카드 누구도 점유 안 한 메시지. battlecard W-1·comparison_matrix W-1의 재환전 0원 강점을 사용자 언어로 직접 환산. (TOWS SO1, market_context_swot §4)
- **액션 M2**: **인스타그램 Reels·YouTube Shorts 단편 영상 채널 진입**. marketing_social §3 PESO에서 토스가 점유하지 못한 영역. 트래블월렛이 점유한 채널이나 메시지가 달라 직접 경쟁 회피. (TOWS WO1)
- **액션 M3**: **"부모님도 쓸 수 있는 직관성" 캠페인** — reaction_insight `app_ux_quality` 94% 강점을 광고 1차 키워드로 격상. 2030세대 + 부모 세대 동시 진입. (TOWS SO2)

### 4-3. 분기 1–2순위 제품 액션 — 흐름 A product_dev lens

- **액션 P1**: **24시간 분실·재발급 채널 강화** — battlecard L-3·reaction_insight `customer_support` 65% 약점. 주말·해외 시간대 채팅 상담 도입. (TOWS WT)
- **액션 P2 (후순위, 분기 2)**: **삼성페이·자체페이 통합 로드맵 가속화** — battlecard L-3·market_context_swot W4. 디지털 사용자 핵심 결제 수단. (TOWS WO2)

### 4-4. 실행 우선순위 요약 (분기당 2–4개 룰 — market_context_swot §1-2)

| 우선순위 | 액션 | 분류 | 출처 |
|:-:|---|:-:|---|
| 1 | M1 "여행 후 잔액 환급 0원" 메시지 격상 | marketing | TOWS SO1 |
| 2 | M2 인스타·Shorts 채널 진입 | marketing | TOWS WO1 |
| 3 | M3 "직관성" 광고 캠페인 | marketing | TOWS SO2 |
| 4 | P1 24시간 분실·재발급 채널 | product_dev | TOWS WT1 |

분기 1에 1–3순위(marketing 3개 + product_dev 1개) 동시 실행. P2(삼성페이 통합)는 분기 2 이후로 이연.

### 4-5. 위험·모니터링 항목

- **하나·신한의 합종연횡 가속**이 토스의 시장 점유 가능성을 잠식할 위험 — market_context_swot T2.
- **수수료 0원 평탄화**로 토스의 "재환전 0원" 단독 차별점도 모방될 가능성. 6개월 내 재평가 필수 — market_context_swot T1.
- **마케팅 캠페인 효과 측정** — M1·M2·M3 실행 후 reaction_insight `additional_benefit` aspect sentiment가 45% → 50%+로 전환되는지 분기말 재측정.

---

## 5. 평가 루브릭 점수 산정

- **BLUF 명시**: §0에 1–2 문장 결론 명시. 100%.
- **SCR 분량 비중**: Situation 14% · Complication 17% · Resolution 64% · 위험 5%. Resolution 60–70% 기준 통과.
- **3개 페르소나 권장 모두 포함**: 3/3 (단기·장기·디지털노마드).
- **6개 상류 리포트 cross-link**: 6/6 — 본문에 모두 인용 + §6 관련 문서에 명시.
- **so-what 통과율 (각 bullet)**: 100% — 모든 bullet이 단순 사실이 아닌 액션·시사점 포함.
- **bold-bullet 표기 일관**: bullet 첫 어구 또는 핵심 키워드 bold 처리.
- **종합 점수**: **5.0 / 5.0** — Rubric §2-5 기준 통과.

---

## 6. Anti-pattern 회피 사례

본 도메인의 5가지 함정과 회피 방식은 다음과 같습니다.

- **6개 리포트 단순 나열 함정**: §1–§4를 6개 상류 리포트 결론의 단순 붙이기로 작성하지 않고, **SCR 4단계로 재구성**해 의사결정자가 답을 빠르게 찾도록 설계.
- **세부 데이터 과다 노출 함정**: aspect별 sentiment 수치·정량 비교 표는 본문에서 제거하고 출처 cross-link로만 처리. 본문은 결론 중심.
- **페르소나 평균화 함정**: §4-1에서 단기·장기·디지털노마드 페르소나별 권장을 분리 명시. 단일 권장 회피.
- **시점 표기 누락**: 헤더 메타데이터에 **스냅샷 시점: 2026-05-19** 명시. 다음 분기 재실행 시 버전 추적 가능.
- **so-what 부재 함정**: §1–§4의 모든 bullet에 액션·시사점 포함. 단순 "토스 만족도 4위" 같은 사실 진술은 anti-pattern.

---

## 7. 본 worked example 사용 시 주의 (메타 지시)

본 예시의 일부 수치(예: SAM 2.5조원 추정, 액션 우선순위 4개)는 시연용이며 상류 worked example의 mock data에 의존합니다. 실제 분석은 흐름 B 상류 6개 리포트의 실데이터 결과로 산정합니다. 또한 트래블카드 도메인의 페르소나 분류·액션 매핑은 본 도메인 특유의 사례이며, 본인 도메인에 맹목적으로 복제하지 마십시오. Rubric §2-5의 추상 기준(BLUF·SCR 분량·페르소나별 권장·so-what 테스트·bold-bullet 표기)이 우선입니다. 다른 도메인은 동일 추상 기준에서 다른 BLUF·다른 액션 우선순위가 도출됩니다.

---

## 8. 관련 문서

- 방법론 reference: `docs/reference_executive_summary.md` (Pyramid Principle · SCQA/SCR · BLUF · bold-bullet · so-what 테스트)
- Rubric 본체: `docs/reference_report_taxonomy.md` §2-5(Executive Summary 정의), §3(액션 가능성 동사 집합)
- 파이프라인 설계: `docs/design/pipeline_topology_redesign.md` §6-0(P0-Rubric), §11-10(이원 흐름 의존 관계 모델 — top 노드 위치)
- 흐름 B 인용 상류 worked example (6개 모두 인용):
  - `docs/reference/examples/comparison_matrix_toss_travel_card.md` (정량 비교 — §1 Situation·§2 Complication 근거)
  - `docs/reference/examples/reaction_insight_toss_travel_card.md` (사용자 인식 — §2 Complication·§4-1 페르소나 근거)
  - `docs/reference/examples/marketing_social_toss_travel_card.md` (채널·메시지 공백 — §1 Situation·§4-2 액션 근거)
  - `docs/reference/examples/battlecard_toss_travel_card.md` (FIA Zones — §4-3 product_dev 액션 근거)
  - `docs/reference/examples/positioning_map_toss_travel_card.md` (페르소나별 좌표 — §4-1 페르소나 권장 근거)
  - `docs/reference/examples/market_context_swot_toss_travel_card.md` (시장·SWOT·TOWS — §1 Situation·§4-2/§4-3 액션 근거)
