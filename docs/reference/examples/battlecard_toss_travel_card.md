# Worked Example — `battlecard` × 토스 트래블카드

> - **목적**: Rubric `docs/reference/report_taxonomy.md` §2-4(배틀카드)의 추상 기준이 트래블카드 도메인에서 어떻게 구체화되는지를 시연합니다.
> - **도메인 슬러그**: `consumer_travel_card_kr`
> - **자사 상품**: 토스 트래블카드 (`own_toss_travel_card`)
> - **분석 방향**: `mixed` (marketing 우위 + product_dev 약점 보강)
> - **데이터 입력 (흐름 B, Output 인용)**: comparison_matrix(정량 비교) + reaction_insight(사용자 quote, 다채널) + marketing_social(채널 운영·메시지 공백)
> - **데이터 입력 (흐름 A, dedicated feature)**: `competitor_marketing_copy` · `competitor_promo_end_date` · `competitor_switch_story_quote` · `competitor_sales_objection`
> - **페르소나 적용**: 단기 여행자(4박 일본 가정)
> - **출처 자료 기준일**: 2026-05-19
> - **작성일**: 2026-05-19

---

## 1. 적용된 Rubric §2-4 항목 (요약 인용)

- **리포트 목적**: 영업·마케팅이 경쟁 대응 멘트·차별점 메시지·이탈 방어 액션을 한 페이지에서 즉시 참조하도록 합니다.
- **표준 구성(3 Zone)**: Winning Zone(자사 우위) · Battling Zone(접전) · Losing Zone(자사 열위 + 우회 전략).
- **항목 단위**: FIA 3-tuple — Fact(검증 가능한 사실 + 출처 URL + 인용) / Impact(so-what, 1–2문장) / Act(talk track 또는 discovery question 또는 follow-up).
- **평가 루브릭**: 3개 Zone 모두 채움(필수) / Zone당 FIA 3-tuple 2개 이상 / 한시 혜택은 `valid_until` 필수 / Losing Zone에 우회 전략 동반 ≥ 100%.
- **나쁜 항목**: Fact만 있고 Impact·Act 누락, 한시 혜택 영구 강점으로 오인, Losing Zone 우회 전략 부재, 페르소나 미고려.

---

## 2. 경쟁사 1줄 개요 + 핵심 메시징 인용

| 경쟁사 | 1줄 포지셔닝 | 핵심 메시지 (공식·매체 인용) |
|---|---|---|
| 트래블월렛 | 통화 폭 강조 + 소셜 기능 차별화 | "One card, 46 currencies" (USD/EUR/JPY 무료, 그 외 0.5–2.5%) |
| 하나 트래블로그 | 통화 최다 + 부가 혜택 패키지 | "58개국 통화 + 3% 결제 적립 + 라운지 분기 N회" |
| 신한 SOL트래블 | 부가 혜택 강조형 체크카드 | "공항라운지 연 2회 + 해외 컨택리스 1% 할인" |
| KB국민 트래블러스 | 한시 프로모션 강조 | "33개 통화 + ~2025-12-31 환전 수수료 면제" (`valid_until: 2025-12-31`) |

위 메시징을 1차 Fact 소스로 활용합니다.

---

## 3. Winning Zone — 자사 우위 영역 (녹색)

### W-1. 재환전 수수료 완전 무료 (트래블카드 11종 중 유일)

- **Fact**: 토스 트래블카드는 매수·매도 모두 환전 수수료 0%. 트래블월렛·트래블로그·KB·우리 등 다른 카드는 매수 0%여도 매도에서 0.5~1% 부과. ([bdoginfo — 11종 비교](https://bdoginfo.com/entry/%ED%8A%B8%EB%9E%98%EB%B8%94%EC%B9%B4%EB%93%9C-11%EC%A2%85-%EB%B9%84%EA%B5%90-%EC%B4%9D%EC%A0%95%EB%A6%AC-%ED%86%A0%EC%8A%A4%EB%B1%85%ED%81%AC-%ED%98%9C%ED%83%9D%EC%9D%B4-%EC%A0%9C%EC%9D%BC-%EC%A2%8B%EC%9D%84%EA%B9%8C))
- **Impact**: 여행 후 잔액 원화 환급 시 다른 카드는 수수료를 1회 더 부담. 환테크 용도(원화 → 외화 → 원화 반복)로도 토스만 비용 없음.
- **Act (marketing talk track)**: "여행 다녀와서 환전한 돈 그대로 남아도 손해 안 보는 카드는 토스뿐입니다."

### W-2. 결제 한도 최대 (1일 5천만원, 월 1억원)

- **Fact**: 토스 1일 5,000만원 / 월 1억원. 트래블월렛·트래블로그는 1일·월 한도가 현저히 낮음. ([나무위키 — 토스뱅크카드](https://namu.wiki/w/%ED%86%A0%EC%8A%A4%EB%B1%85%ED%81%AC%EC%B9%B4%EB%93%9C))
- **Impact**: 해외 호텔 prepay·항공권·고가 쇼핑 결제 시 한도 초과로 인한 결제 실패 위험이 없음.
- **Act (discovery question)**: "이번 여행 일정에 호텔이나 항공권 사전 결제 계획이 있으신가요? 한 번에 큰 금액 결제하시는 경우엔 한도 차이가 중요합니다."

### W-3. 앱 UX·통합 환경 (reaction_insight `app_ux_quality` 94%)

- **Fact**: reaction_insight worked example §2 기준 토스 `app_ux_quality` positive 94% (트래블월렛 80% · 트래블로그 73%). YouTube 댓글 원문: "토스 앱이 진짜 직관적. 부모님도 쓰실 수 있을 정도." ([reaction_insight worked example §2](../examples/reaction_insight_toss_travel_card.md))
- **Impact**: 트래블월렛·트래블로그는 별도 앱 설치 필요. 토스는 기존 앱 내 통합으로 진입 마찰이 없음.
- **Act (follow-up)**: 광고 캠페인 1차 키워드를 "직관성"으로 격상. 사용자 quote 인용 광고 제작 검토.

---

## 4. Battling Zone — 접전 영역 (황색)

### B-1. ATM 출금 한도 vs 면제 횟수

- **Fact**: 토스 1일 USD 한도는 트래블월렛(USD 1,000)보다 크나 트래블로그(USD 6,000)보다 작음. 면제 ATM 네트워크 호환성은 트래블로그가 더 넓다는 사용자 후기 다수. ([홀라플라이 — 비교 2026](https://esim.holafly.com/ko/travel-tips/travel-log-travel-wallet-comparison/))
- **Impact**: 장기 체류자·고액 인출자에게는 토스가 불리. 단기 일본 여행객에게는 차이 미미.
- **Act (talk track)**: "단기 여행이시면 토스로 충분하지만, 장기 체류 계획이 있으시면 트래블로그 한도가 유리할 수 있습니다."

### B-2. 매수 환전 수수료 (전 카드 0%로 평탄화)

- **Fact**: 주요 통화(USD/EUR/JPY)는 모든 트래블카드가 매수 수수료 0%. 차별점은 재환전(W-1)과 통화 범위(L-2 참조). ([카드고릴라 — 환전 서비스 비교 2026](https://m.card-gorilla.com/contents/detail/2867))
- **Impact**: 환전 비용 단일 항목으로는 차별화 어려움. 재환전·통화 폭·부가 혜택의 조합에서 가치 도출.
- **Act (discovery question)**: "환전 수수료 외에 라운지·적립 같은 다른 혜택 중에 어떤 것을 중요하게 보시나요?"

### B-3. 카드 간 전환 사례 — Switch Story (dedicated feature: `competitor_switch_story_quote`)

- **Fact**: 클리앙·디시인사이드 커뮤니티에서 "트래블월렛 쓰다가 토스로 바꿨다" 또는 그 반대 방향의 전환 narrative가 활발히 공유됩니다. 대표 quote(원문): "트래블월렛 잘 쓰다가 일본 다녀와서 잔액 남은 거 환급하는데 0.5% 수수료 떼이는 거 보고 다음 여행 전에 토스로 갈아탔습니다." ([클리앙 — 외화 선불카드 비교](https://www.clien.net/service/board/lecture/18806850))
- **Impact**: 전환 동인은 단일 정량 비교에 잘 드러나지 않는 "사용 후 발견하는 결점"인 경우가 많습니다. comparison_matrix는 0.5% 매도 수수료를 단일 셀로 표시하지만, switch story는 "왜 그 0.5%가 이탈로 이어지는가"의 narrative를 제공합니다.
- **Act (marketing talk track)**: "이미 다른 트래블카드를 쓰고 계신가요? 여행 후 잔액 환급에서 수수료를 부담하셨던 경험이 있다면, 토스의 재환전 0%가 직접적인 해결책입니다."

### B-4. 경쟁사 채널·메시지 공백 (흐름 B: marketing_social Output 인용)

본 항목은 dedicated feature가 아닌 **marketing_social 리포트 결과를 inline으로 인용**한 항목입니다. battlecard 사용자가 별도 리포트로 이동하지 않고 한 페이지에서 의사결정 근거를 완결할 수 있도록 직접 결과를 노출합니다.

- **Fact (marketing_social 결과 인용)** — 채널 운영 매트릭스 (mock, marketing_social 산출 가정):

  | 카드 | 주력 채널 | 게시 주기 | 콘텐츠 평균 소비 | 핵심 키워드 |
  |---|---|---|---|---|
  | 토스 트래블카드 | 토스 앱 알림·뉴스레터 | 비정기 | 앱 내 노출 (외부 측정 불가) | "환전 0원" |
  | 트래블월렛 | 인스타그램·YouTube Shorts | 주 3–4회 | 인스타 평균 좋아요 2k+ | "One card, 46 currencies" · "친구와 환전" |
  | 하나 트래블로그 | 공항·기차역 옥외 + 하나금융 자행 채널 | 분기 캠페인 | 옥외 광고 노출 추정 (지역별 상이) | "58개국 통화" · "3% 적립" |
  | 신한 SOL트래블 | TV CF + 뉴스 PR | 분기 CF | TV 광고 GRP 50+ | "공항 라운지" · "컨택리스 1%" |

- **Impact**: 토스는 외부 노출 채널(인스타그램·YouTube Shorts·옥외·TV)이 모두 약하고, 자사 앱·뉴스레터 채널에 의존합니다. 경쟁사 4종 중 3종은 SNS 또는 옥외 채널을 운영 중이므로, 토스는 신규 사용자가 카드를 처음 인지하는 채널에서 노출량이 현저히 부족합니다. 한편 경쟁사 핵심 키워드는 "통화 수"·"적립"·"라운지"·"친구와 환전"으로 수렴하며, **"여행 후 잔액 환급 0원" 메시지를 점유한 카드는 없습니다**.
- **Act (marketing talk track + 채널 전략)**:
  - 메시지 공백 활용: "여행 후 잔액 환급 0원"을 토스 핵심 메시지로 격상. W-1(재환전 무료)의 직접 환산이며, 다른 카드가 점유하지 못한 차별 영역.
  - 채널 공백 활용: 인스타그램 Reels · YouTube Shorts 단편 영상에서 "환전 다녀와서 잔액 환급할 때 손해 본 적 있나요?" 형식의 후크 영상으로 신규 진입. 트래블월렛이 점유한 채널이지만 메시지가 달라 직접 경쟁 회피.

---

## 5. Losing Zone — 자사 열위 영역 (적색)

### L-1. 공항 라운지 혜택 부재

- **Fact**: 토스 라운지 혜택 없음. 신한 SOL트래블 연 2회 무료 · 하나 트래블로그 분기 N회 무료. ([millionaire24 블로그 비교](https://millionairefrom24.thinkmblog.com/entry/%ED%95%B4%EC%99%B8%EC%97%AC%ED%96%89-%ED%95%84%EC%88%98-%EC%B9%B4%EB%93%9C-%EC%8B%A0%ED%95%9C-sol-vs-%ED%86%A0%EC%8A%A4%EB%B1%85%ED%81%AC-vs-%ED%8A%B8%EB%9E%98%EB%B8%94%EC%9B%94%EB%A0%9B))
- **Impact**: 공항 대기 시간이 긴 사용자에게는 명확한 약점.
- **Act (우회 전략, marketing)**: "라운지 혜택을 원하시면 카드 발급 비용·연회비 부담이 있을 수 있습니다. 토스는 연회비 없이 환전·재환전·결제 수수료 0원으로 같은 가치(약 5–10만원 절약)를 다른 방식으로 돌려드립니다."

### L-2. 지원 통화 범위 (17종 vs 트래블로그 58종)

- **Fact**: 토스 17개 외화통장 지원. 트래블월렛 46종 · 트래블로그 58종. ([Travel Wallet 공식](https://www.travel-wallet.com/en), [카드고릴라](https://m.card-gorilla.com/contents/detail/2867))
- **Impact**: 동남아·중남미·아프리카 여행객에게는 통화 미지원 문제 발생.
- **Act (우회 전략, marketing)**: "주요 여행지인 일본·미국·유럽은 토스에서 모두 0원으로 사용 가능합니다. 그 외 통화는 현지 USD/EUR 결제 또는 자동 환산으로 처리하시면 비용 차이가 거의 없습니다."

### L-3. 삼성페이·자체페이 등록 불가

- **Fact**: 토스 트래블카드는 삼성페이 등록 불가. 해외에서 실물카드 필수. ([나무위키 — 토스뱅크카드](https://namu.wiki/w/%ED%86%A0%EC%8A%A4%EB%B1%85%ED%81%AC%EC%B9%B4%EB%93%9C))
- **Impact**: 분실·도난 시 결제 수단 단절 위험.
- **Act (product_dev lens, 내부 액션)**: 삼성페이 통합을 다음 분기 로드맵 우선순위로 검토. 외부 메시지로는 "여행 중 실물카드 분실 대응 매뉴얼" 동봉.

### L-4. 경쟁사가 자주 제기하는 반박 멘트 — Sales Objection (dedicated feature: `competitor_sales_objection`)

- **Fact**: 커뮤니티·고객지원 응답에서 트래블월렛·하나 트래블로그 사용자가 토스에 대해 자주 제기하는 반박 멘트: "토스는 외화통장 만들어야 해서 귀찮다", "타은행 계좌랑 연동 안 돼서 자금 옮기는 게 번거롭다", "삼성페이 안 되니까 카드 잃어버리면 끝이다". ([millionaire24 — 신한 SOL vs 토스 vs 트래블월렛](https://millionairefrom24.thinkmblog.com/entry/%ED%95%B4%EC%99%B8%EC%97%AC%ED%96%89-%ED%95%84%EC%88%98-%EC%B9%B4%EB%93%9C-%EC%8B%A0%ED%95%9C-sol-vs-%ED%86%A0%EC%8A%A4%EB%B1%85%ED%81%AC-vs-%ED%8A%B8%EB%9E%98%EB%B8%94%EC%9B%94%EB%A0%9B))
- **Impact**: 이 3가지 반박은 토스 신규 사용자가 발급 전 의사결정 단계에서 가장 자주 마주치는 객션입니다. 사전 응답 매뉴얼이 없으면 마케팅 전환율이 떨어지고 영업·CS가 같은 답을 반복합니다.
- **Act (객션별 사전 응답)**:
  - "외화통장 귀찮다" → "외화통장은 토스 앱에서 5초 만에 자동 개설됩니다. 별도 방문·서류 없음."
  - "타은행 연동 안 된다" → "재환전 수수료 0%이므로 한 번 충전 후 잔액 그대로 활용하시면 됩니다. 잔액 환급도 토스 잔고로 즉시 가능."
  - "삼성페이 안 된다" → "여행 중 실물카드 분실에 대비한 24시간 분실 신고·재발급 매뉴얼을 동봉합니다. (product_dev: 삼성페이 통합 로드맵 검토 중)"

---

## 6. 평가 루브릭 점수 산정

- **3개 Zone 커버리지**: 3/3 (Winning 3개 · Battling 4개 · Losing 4개 항목).
- **Zone당 FIA 3-tuple 2개 이상**: 3/3 Zones 충족 (Winning 3 · Battling 4 · Losing 4).
- **한시 혜택 `valid_until` 명시**: §2 표에서 KB 트래블러스 `valid_until: 2025-12-31` 명시. 100%.
- **Losing Zone 우회 전략 동반**: 4/4 (L-1·L-2은 marketing 우회 멘트, L-3은 product_dev 내부 액션 + 외부 안내, L-4는 객션별 사전 응답 3종). 100%.
- **Dedicated feature 4종 시연**: 4/4 — `competitor_marketing_copy`(§2 표) · `competitor_promo_end_date`(§2 KB 트래블러스 `valid_until`) · `competitor_switch_story_quote`(B-3) · `competitor_sales_objection`(L-4) 모두 포함. 단순 재가공 anti-pattern 회피.
- **흐름 B 인용 3종 (inline 완결)**: comparison_matrix(§2 표 정량값) · reaction_insight(W-3 `app_ux_quality` 94%) · marketing_social(B-4 채널 매트릭스). forward reference 없이 한 페이지에서 완결.
- **종합 점수**: **5.0 / 5.0** — Rubric §2-4 기준 통과.

---

## 7. Anti-pattern 회피 사례

본 도메인의 5가지 함정과 회피 방식은 다음과 같습니다.

- **단순 재가공 함정 (배틀카드 고유 함정)**: comparison_matrix·reaction_insight의 결과만 인용하여 배틀카드를 작성하면 영업·마케팅 현장 액션이 빈약해집니다. 본 worked example은 dedicated feature 4종(`competitor_marketing_copy`·`competitor_promo_end_date`·`competitor_switch_story_quote`·`competitor_sales_objection`)을 §2·§4·§5에 시연하고, 추가로 marketing_social 결과를 흐름 B로 inline 인용(B-4)하여 단순 재가공 anti-pattern을 회피했습니다.
- **Forward Reference 함정 (UX 마찰)**: "자세한 내용은 marketing_social 리포트 참조" 같은 forward reference는 사용자가 페이지를 오가며 정보를 조립해야 해 의사결정 마찰을 유발합니다. 본 worked example의 B-4는 marketing_social 결과를 채널 운영 매트릭스로 inline 인용해 단일 페이지에서 의사결정 근거를 완결시켰습니다(§11-10 "Inline 인용 > Forward Reference" 원칙).
- **한시 프로모션 영구화 오인**: §2 표의 KB 트래블러스 `valid_until: 2025-12-31` 명시로 한시 혜택을 영구 강점으로 비교하지 않았습니다. Fact 항목에 `valid_until` 필드가 누락된 비교는 anti-pattern입니다.
- **"수수료 0%"의 통화 범위 누락**: §3 W-1과 §4 B-2에서 토스의 0%는 전 통화, 트래블월렛의 0%는 3종에 한정임을 명시 분리하였습니다.
- **Losing Zone 우회 전략 부재**: §5의 모든 L 항목에 우회 멘트 또는 내부 product_dev 액션을 함께 제시했습니다. 약점만 노출하고 대응 멘트가 없는 배틀카드는 영업이 현장에서 회피해 결국 미사용되는 anti-pattern입니다.
- **페르소나 평균화**: 본 worked example은 "단기 여행자(4박 일본)" 페르소나로 명시 제한했습니다. 동일 도메인의 "장기 체류자" 페르소나에서는 B-1(ATM 한도)이 Losing Zone으로 이동, "디지털노마드"에서는 L-2(통화 범위)가 Losing Zone 1순위로 격상됩니다.

---

## 8. 본 worked example 사용 시 주의 (메타 지시)

본 예시는 트래블카드 도메인 · 단기 여행자 페르소나의 사례이며, 본인 도메인·페르소나에 맹목적으로 복제하지 마십시오. Rubric §2-4의 추상 기준(3 Zone, FIA 3-tuple, 우회 전략 의무화)이 우선입니다. 다른 도메인·페르소나는 동일 추상 기준에서 다른 Zone 매핑·다른 Fact 집합이 도출됩니다.

---

## 9. 관련 문서

- 방법론 reference: `docs/reference/battlecard.md` (1페이지 원칙 · FIA · Winning/Battling/Losing Zones · Win/Loss Analysis · Role-based Variations · Living Battlecard)
- Rubric 본체: `docs/reference/report_taxonomy.md` §2-4(배틀카드 정의), §3(액션 가능성 동사 집합)
- 파이프라인 설계: `docs/design/pipeline_topology_redesign.md` §6-0(P0-Rubric), §11-10(worked example 작성·제공 방식)
- 데이터 입력 worked example:
  - `docs/reference/examples/comparison_matrix_toss_travel_card.md` (정량 비교)
  - `docs/reference/examples/reaction_insight_toss_travel_card.md` (사용자 quote, 다채널)
