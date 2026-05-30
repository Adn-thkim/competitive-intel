# Report Taxonomy Rubric — 7개 분석 리포트의 통합 의미 기준

> - **목적**: DomainTaxonomyAgent가 `report_config[report_type]` 7종(D4 확정 v0.6 + v0.10 스키마 재구조화로 고정 키)을 채울 때 의미 기준이 되는 단일 Rubric을 정의합니다. 본 Rubric은 `agents/domain_modeling/system_prompt_kr.md`에 inline 인용되며(D9 확정 v0.8 — 방식 1), 7개 리포트의 목적·표준 feature 카테고리·좋은/나쁜 예시·평가 루브릭·anti-pattern을 통합 기술합니다.
> - **활용 범위**: (i) DomainTaxonomyAgent의 taxonomy 생성 가이드 — 각 `report_config[report_type]`의 `categories`·`features`·`search_query_hints` 추론 기준, (ii) FeatureUrlMapperAgent의 Brave 검색 후 LLM relevance 검증 기준, (iii) Feature Selection UI(§7, D6)의 카드 그룹핑 라벨, (iv) 각 리포트 노드의 출력 품질 평가 기준.
> - **출처 표기 규약**: 각 항목 옆에 `[ref:파일명 §x-x]` 형식으로 출처를 명시합니다. `reference_*.md` 7종이 1차 소스이며, 외부 자료(URL)는 reference 문서를 경유해 인용합니다. worked example은 `[ex:파일명 §x]` 형식으로 표기합니다.
> - **문서 버전**: v0.1 (초안) | 작성일: 2026-05-21 | 선행 문서: `docs/design/pipeline_topology_redesign.md` v0.10 §6-0·§6-3·§6-5
> - **분량 상한**: §11-8 (`pipeline_topology_redesign.md`) 기준 리포트당 25–35줄, 총 약 200–300줄. system_prompt inline 인용 시 약 1.5k 토큰 환산.

---

## 1. 개요 — 7개 리포트의 역할 분담

7개 리포트는 단일 DAG가 아닌 **공유 Feature Pool(흐름 A) + 선택적 Output 인용(흐름 B)** 이원 흐름으로 운영됩니다 [ref:pipeline_topology_redesign.md §11-10]. 각 리포트는 서로 다른 의사결정 질문에 답하며, 데이터 차원·대상 독자·핵심 액션이 모두 다릅니다.

|  #  | 리포트 (enum)            | 1줄 정의                                                                              |  흐름   | 대상 독자        |
| :-: | --------------------- | ---------------------------------------------------------------------------------- | :---: | ------------ |
|  1  | `comparison_matrix`   | 자사·경쟁사의 **정형 feature 정량/정성 비교** [ref:reference_comparison_matrix.md §1-1]          |   A   | PM, 마케터      |
|  2  | `reaction_insight`    | 사용자가 표현한 의견을 **ABSA aspect별로 정밀 분석** [ref:reference_reaction_insight.md §1-2]      |   A   | 마케터, CX      |
|  3  | `marketing_social`    | 자사·경쟁사의 **채널 운영·콘텐츠 전략**(공급 측) [ref:reference_marketing_social.md §1-1]            |   A   | 마케터, 콘텐츠 헤드  |
|  4  | `battlecard`          | 영업·마케팅이 **경쟁 메시지에 즉시 응대**할 수 있는 1페이지 카드 [ref:reference_battlecard.md §1-1]         | A + B | 영업, 마케터      |
|  5  | `positioning_map`     | 차별화 축 2개로 **카드 좌표 시각화** [ref:reference_positioning_map.md §1-1]                    |   B   | 마케터, CMO     |
|  6  | `market_context_swot` | 외부 시장 컨텍스트 + 자사 SWOT을 **TOWS 액션으로 통합** [ref:reference_market_context_swot.md §1-1] | A + B | CEO, CMO, PM |
|  7  | `executive_summary`   | 의사결정자가 **1–2분 안에 결론·권고 파악** [ref:reference_executive_summary.md §1-1]              |   B   | CEO/CMO/PM   |

**원칙**: 단일 feature는 복수 리포트의 카테고리에 매핑될 수 있으나, 동일 feature를 두 리포트가 동시에 dedicated로 가져서는 안 됩니다(중복 수집 회피). Feature Selection UI는 "가장 비중 큰 카드에 1회 표시 + 공유 항목 배지"로 처리합니다 [ref:pipeline_topology_redesign.md §7 D6 확정].

---

## 2. 리포트별 정의

### 2-1. `comparison_matrix` — 비교 매트릭스

**목적**: 자사·경쟁사 정형 feature를 카테고리화·가중치화하여 의사결정 가능한 표로 산출 [ref:reference_comparison_matrix.md §1-1].

**핵심 액션**: "이 영역에서 자사는 우위/열위/동등 중 무엇인가"에 즉답.

**표준 feature 카테고리 5–8개** (도메인 횡단):
1. **Pricing** — 가격·수수료·요금제 [ref:reference_comparison_matrix.md §2-1 항목 2·3·4]
2. **Core Capability** — 핵심 기능·성능·용량 [ref:reference_comparison_matrix.md §2-1 항목 1]
3. **Integration/Compatibility** — 외부 시스템·결제·연동 [ref:reference_comparison_matrix.md §2-1 항목 6]
4. **Additional Benefit** — 부가 혜택·로열티 [ref:reference_comparison_matrix.md §2-1 항목 7·8]
5. **Onboarding/Eligibility** — 가입·발급·조건 [ref:reference_comparison_matrix.md §2-1 항목 9]
6. **UX/Support** — 사용자 경험·고객지원 (도메인별 추가)

**좋은 feature 예시** [ex:comparison_matrix_toss_travel_card.md]: `re_exchange_fee_rate` — 정량 수치 + 단위(%) + 통화 범위 + 시점 + 공식 출처 URL.

**나쁜 feature 예시** [ref:reference_comparison_matrix.md §2-3]: "환전 수수료 무료" — 매수/매도 비대칭, 통화 범위, 시점 모두 미명시. 함정 항목.

**평가 루브릭 (1–5점, Gartner Critical Capabilities + Harvey Balls 차용 [ref:reference_comparison_matrix.md §1-3 §1-5])**:
- **1점**: 단순 binary "지원/미지원"만 표기.
- **2점**: 정량 수치만 있으나 단위·시점 누락.
- **3점**: 수치 + 단위 + 출처 명시. 페르소나 가중치 미적용.
- **4점**: 3점 요건 + Use case별 가중치 차별화 [ref:reference_comparison_matrix.md §1-3 §1-4].
- **5점**: 4점 요건 + footnote로 함정 항목 4종(매수/매도 비대칭, 한시 프로모션, ATM 실효성, 통화 우대 차이) 명시 [ref:reference_comparison_matrix.md §2-3].

---

### 2-2. `reaction_insight` — 고객 반응 인사이트

**목적**: 사용자가 자사·경쟁사에 대해 표현한 의견을 **ABSA(Aspect-Based Sentiment Analysis)** 로 정밀 분석 [ref:reference_reaction_insight.md §1-2].

**핵심 액션**: "사용자는 어느 aspect를 좋아하고 싫어하는가" 및 product_dev suggestion 후보 추출.

**표준 aspect 카테고리 5–8개** (도메인별 DomainTaxonomyAgent 자동 생성, `aspect_codebook` 필드 [ref:pipeline_topology_redesign.md §6-3]):
1. **Core Function Convenience** — 핵심 기능 사용성 [ref:reference_reaction_insight.md §2-3 항목 1]
2. **Pricing Perception** — 수수료/비용 체감 [ref:reference_reaction_insight.md §2-3 항목 2]
3. **Reliability/Availability** — 안정성·가용성 [ref:reference_reaction_insight.md §2-3 항목 3]
4. **App UX Quality** — 앱 사용성 [ref:reference_reaction_insight.md §2-3 항목 4]
5. **Customer Support** — 분실·문의 대응 [ref:reference_reaction_insight.md §2-3 항목 5]
6. **Additional Benefit Perception** — 부가 혜택 인식 [ref:reference_reaction_insight.md §2-3 항목 6]
7. **Domain-specific** — 도메인 차별 aspect (예: 트래블카드의 `social_features`) [ref:reference_reaction_insight.md §2-3 항목 7]

**좋은 출력 단위** [ref:reference_reaction_insight.md §3]: `(aspect, polarity, intensity, quote, source_url, channel, posted_at)` 7-tuple. 원문 quote 보존(번역·요약 금지).

**나쁜 출력 예시**: 단일 sentiment 점수("긍정 70%"). aspect 분리 없이는 마케팅·product 액션 도출 불가 [ref:reference_reaction_insight.md §1-2].

**평가 루브릭 (1–5점)**:
- **1점**: 단일 sentiment 점수 (aspect 미분리).
- **2점**: aspect 분리되었으나 polarity만 표기, intensity 없음.
- **3점**: 7-tuple 단일 채널 (YouTube만).
- **4점**: 2채널 cross-validation (YouTube + 커뮤니티) [ref:reference_reaction_insight.md §1-1 §2-2, D11 확정 v0.8].
- **5점**: 4점 요건 + 채널별 가중치 + 여행지/시점 분리 뷰 + suggestion 카테고리 별도 분리(product_dev 후보) [ref:reference_reaction_insight.md §1-4 6 카테고리 표준].

---

### 2-3. `marketing_social` — 마케팅·소셜 분석

**목적**: 자사·경쟁사의 **채널 운영 전략(공급 측)** 을 PESO·engagement·키워드 차원에서 측정 [ref:reference_marketing_social.md §1-1].

**핵심 액션**: "경쟁사는 어디서 어떻게 메시지를 노출하는가" 및 자사 채널·메시지 공백 식별.

**표준 카테고리 5–8개**:
1. **PESO 분류** — Paid/Owned/Shared/Earned 4분면 [ref:reference_marketing_social.md §1-2]
2. **Channel Operations** — 주력 채널·게시 빈도·구독자 규모 [ref:reference_marketing_social.md §1-3 §1-4]
3. **Engagement** — `interactions ÷ followers` 표준 [ref:reference_marketing_social.md §1-3]
4. **Content Keywords/Topics** — 키워드 빈도 + 예시 게시물 URL [ref:reference_marketing_social.md §1-5]
5. **Channel × Keyword Cross-tab** — 채널별 메시지 분포
6. **Coverage Gap** — 자사 미점유 채널·메시지 공백
7. **Seasonality Correction** — 시즌성 보정 측정 기간 [ref:reference_marketing_social.md §2-3]

**좋은 출력 단위** [ref:reference_marketing_social.md §3]: `(channel, posting_frequency, audience_size, top_keywords[{keyword, frequency, top_examples}])` 4-tuple.

**나쁜 출력 예시** [ref:reference_marketing_social.md §1-3]: 단일 노출 수치("팔로워 N명") — 분모 정의 불명, 채널 간 비교 불가.

**평가 루브릭 (1–5점, AMEC Integrated Evaluation [ref:reference_marketing_social.md §1-6])**:
- **1점**: 단일 채널 게시물 수만.
- **2점**: 다채널 PESO 분류, engagement 분모 불명.
- **3점**: 3개 채널 + PESO + engagement 표준(`interactions ÷ followers`).
- **4점**: 3점 요건 + 채널 × 키워드 cross-tab.
- **5점**: 4점 요건 + 시즌성 보정(동일 기간 정렬) + 자사 공백 식별 + battlecard B-4와 정렬되는 채널 매트릭스 [ref:reference_marketing_social.md §4 결정 3].

---

### 2-4. `battlecard` — 배틀카드

**목적**: 영업·마케팅이 통화·필드 상황에서 즉시 대응 가능한 **1페이지(스크롤 1회) FIA 응축 카드** [ref:reference_battlecard.md §1-1].

**핵심 액션**: "이 경쟁사는 누구인가 / 어디서 이기는가 / 어디서 지는가 / 가격 / 응대 방법" 5개 질문 즉답 [ref:reference_battlecard.md §1-1].

**표준 카테고리 5–8개**:
1. **Winning Zone** — 자사 명확 우위 영역 [ref:reference_battlecard.md §1-3]
2. **Battling Zone** — 접전 영역, proof point 인용
3. **Losing Zone** — 자사 열위, 명시적 인정 + 우회 전략
4. **dedicated feature 4종** — `competitor_marketing_copy`·`competitor_promo_end_date`·`competitor_switch_story_quote`·`competitor_sales_objection` [ref:reference_battlecard.md §1-7]
5. **Persona Variation** — 단기 여행자/장기 체류자/디지털노마드(B2C) 또는 BDR/AE/SE/CS(B2B) [ref:reference_battlecard.md §1-5]
6. **Living Battlecard** — 자동 갱신 + 한시 프로모션 `valid_until` 추적 [ref:reference_battlecard.md §1-6]

**좋은 출력 단위** [ref:reference_battlecard.md §1-2]: 모든 항목이 **FIA 3-tuple(Fact + Impact + Act)** — Fact는 출처 URL + 인용, Impact는 "so what" 답, Act는 talk track/discovery question/follow-up 중 1개 이상.

**나쁜 출력 예시** [ref:reference_battlecard.md §1-2 §1-7]: 단순 비교 표만 (Impact·Act 누락). comparison_matrix·reaction_insight의 단순 재가공 — dedicated feature 4종 중 2종 이상 누락 시 본 anti-pattern.

**평가 루브릭 (1–5점)**:
- **1점**: 단순 비교 표 (FIA 미적용).
- **2점**: Winning/Losing Zone 2구분, Battling 없음.
- **3점**: Zone 3구분 + FIA 3-tuple 일부 적용.
- **4점**: 3점 요건 + dedicated feature 4종 + 페르소나 변형 1개.
- **5점**: 4점 요건 + 페르소나 변형 3개 + 흐름 B(comparison + reaction + marketing_social) inline 인용 [ref:reference_battlecard.md §1-7, ref:pipeline_topology_redesign.md §11-10] + 한시 프로모션 `valid_until` 자동 추적 [ref:reference_battlecard.md §1-6].

---

### 2-5. `positioning_map` — 포지셔닝 맵

**목적**: 차별화 축 2개로 카드 좌표 시각화 — Perceptual(인식) + Positioning(실제) 동시 산출 [ref:reference_positioning_map.md §1-1].

**핵심 액션**: "실제 우위 vs 인식 우위의 격차"에서 마케팅 액션 기회 식별 + white space 발견.

**표준 카테고리 5–8개**:
1. **Axis Selection** — 고객 의사결정 기준의 빈도·중요도 가중 [ref:reference_positioning_map.md §1-4]
2. **Coordinate Calculation** — comparison_matrix(X) + reaction_insight(Y) 흐름 B 인용
3. **Quadrant Interpretation** — Porter Generic Strategies 매핑 [ref:reference_positioning_map.md §1-5]
4. **White Space** — 시장 빈 공간 식별 + 진입 가능성 평가
5. **Persona Multi-view** — 최소 2개 페르소나 뷰(단기 + 장기) [ref:reference_positioning_map.md §4 결정 2]
6. **Aaker 3 Conditions** — Resonate / Differentiate / Reflect 점수 [ref:reference_positioning_map.md §1-3]
7. **Reframing Candidate** — 후발 브랜드용 재정의 카테고리 [ref:reference_positioning_map.md §1-2 Ries & Trout]

**좋은 출력 예시** [ex:positioning_map_toss_travel_card.md, ref:reference_positioning_map.md §4 결정 3]: 동일 5개 카드를 (i) positioning map과 (ii) perceptual map 두 평면에 동시 배치 → 격차 큰 영역을 마케팅 우선순위로 표기.

**나쁜 출력 예시** [ref:reference_positioning_map.md §2-4]: 단일 시점 + 페르소나 평균 + 축 자의성(고객 의사결정 기준 미검증). misleading map의 전형.

**평가 루브릭 (1–5점)**:
- **1점**: 단일 맵, 축 근거 없음.
- **2점**: 2축 + 4사분면 해석.
- **3점**: 4사분면 + white space 식별 + 페르소나 1개.
- **4점**: 페르소나 2개 이상 + Aaker 3조건 점수.
- **5점**: 4점 요건 + perceptual/positioning 동시 + 격차 marker + reframing 후보 1–2개 제시 + 시점 명시.

---

### 2-6. `market_context_swot` — 시장 컨텍스트·SWOT

**목적**: 외부 macro 컨텍스트(PESTLE·Porter)와 내부 강·약점(흐름 B 인용)을 SWOT로 통합 → **TOWS 4종 액션**으로 변환 [ref:reference_market_context_swot.md §1-1 §1-2].

**핵심 액션**: 분기당 실행 가능한 SO/WO/ST/WT 액션 2–4개 도출 [ref:reference_market_context_swot.md §4 결정 4].

**표준 카테고리 5–8개**:
1. **SWOT 4분면** — S/W는 흐름 B(comparison + reaction + marketing_social), O/T는 흐름 A(외부 수집) [ref:reference_market_context_swot.md §1-1]
2. **TOWS Matrix** — SO/WO/ST/WT 4종 액션 + 우선순위 [ref:reference_market_context_swot.md §1-2]
3. **PESTLE 4요소** — P/E/S/T 우선 (Legal/Environmental은 보조) [ref:reference_market_context_swot.md §4 결정 3]
4. **Porter's 5 Forces** — Rivalry·Substitution 우선 [ref:reference_market_context_swot.md §1-4]
5. **Market Sizing** — TAM/SAM/SOM + CAGR, 정부·협회 1차 소스 [ref:reference_market_context_swot.md §1-6 §4 결정 2]
6. **Seasonality** — 분기별·월별 분해 [ref:reference_market_context_swot.md §2-4]

**좋은 출력 예시** [ex:market_context_swot_toss_travel_card.md, ref:reference_market_context_swot.md §1-2]: TOWS 4분면별 액션 2개 + 다중 S-O/W-T 페어로 뒷받침 + 정량 시장 데이터 [ref:reference_market_context_swot.md §2-1].

**나쁜 출력 예시** [ref:reference_market_context_swot.md §1-2 §2-4]: SWOT 4분면 단순 나열, 액션 도출 없음. PESTLE 6요소 모두 동등 분석으로 트래블카드와 무관한 Environmental 과대 추정.

**평가 루브릭 (1–5점)**:
- **1점**: SWOT 4분면 나열만, 액션 없음.
- **2점**: SWOT + PESTLE (4요소) 요약.
- **3점**: SWOT + PESTLE + TOWS 액션 1–2개.
- **4점**: 3점 요건 + Porter's 5 Forces + 정량 시장 데이터(TAM/SAM/SOM).
- **5점**: 4점 요건 + TOWS 액션 2–4개 + 다중 S-O/W-T 페어 우선순위 + 시즌성 보정 + 1차 출처 우선(매체 추정치는 1차 소스 부재 시만, "추정" 명시) [ref:reference_market_context_swot.md §3 §4 결정 2].

---

### 2-7. `executive_summary` — 임원 요약

**목적**: 의사결정자가 **1–2 페이지(슬라이드)에서 1–2분 안에 결론과 권고**를 파악 [ref:reference_executive_summary.md §1-1].

**핵심 액션**: 3개 페르소나(CEO/CMO/PM) 모두 "무엇을 해야 하는가"의 답을 한 페이지에서 찾음.

**표준 카테고리 5–8개**:
1. **BLUF** — 첫 문장 = 단일 결론, 둘째 문장 = 핵심 근거 [ref:reference_executive_summary.md §1-4]
2. **SCR/SCQA Structure** — Situation 10–15% / Complication 15–20% / Question 5% / Resolution 60–70% [ref:reference_executive_summary.md §1-3]
3. **Pyramid Principle** — Top assertion + 3–5 MECE 논거 [ref:reference_executive_summary.md §1-2]
4. **Bold-Bullet** — bold만 스캔해도 논지 파악, bullet은 정량 데이터 + 출처 inline [ref:reference_executive_summary.md §1-5]
5. **"So What?" Test** — 각 문장은 의사결정자의 "그래서?"에 답 [ref:reference_executive_summary.md §1-6]
6. **Persona Branching** — CEO/CMO/PM 3개 페르소나 답 [ref:reference_executive_summary.md §2-1 §4 결정 4]
7. **Cross-link** — 6개 상류 리포트로의 출처 링크 (인용 그 자체는 본문 노출 최소)

**좋은 출력 예시** [ex:executive_summary_toss_travel_card.md, ref:reference_executive_summary.md §1-6]: "토스 만족도 4위는 라운지 부재의 인식 편향에서 비롯되며, 라운지 점유 없이도 만족도를 끌어올릴 메시지 공백이 존재합니다" — 사실 + Impact + Action 통합.

**나쁜 출력 예시** [ref:reference_executive_summary.md §2-3]: 6개 상류 리포트 결론 단순 나열, 페르소나 평균화, 시점 표기 누락, 세부 데이터 과다 노출로 분량 폭증.

**평가 루브릭 (1–5점)**:
- **1점**: 6개 리포트 결론 단순 나열.
- **2점**: BLUF + bullet 구조, SCR 분량 비중 미준수.
- **3점**: BLUF + SCR (Resolution 60–70%) + Pyramid 3–5 논거.
- **4점**: 3점 요건 + bold-bullet 일관 적용 + "so what?" 답 명시.
- **5점**: 4점 요건 + 페르소나 3개(CEO/CMO/PM) 모두 답 + 시점 명시 + 1–2 페이지 분량 제약 준수 [ref:reference_executive_summary.md §4 결정 1·2·3·4].

---

## 3. 액션 가능성(Actionability) 기준 — D7 확정 (v0.7)

**원칙**: 모든 리포트는 "관찰"이 아닌 "행동 지시"로 끝나야 합니다. DomainTaxonomyAgent는 Query Intake에서 사용자가 명시한 `analysis_direction`(`marketing` / `product_dev` / `mixed`)에 따라 본 절의 동사 집합으로 액션을 분기합니다 [ref:pipeline_topology_redesign.md §11-9 D7 확정].

**동사 집합**:

| analysis_direction | 동사 4종 | 적용 리포트 (주) |
|---|---|---|
| `marketing` | **추가하라**(메시징/혜택) · **유지·강화하라** · **재포지셔닝하라** · **방어하라** | battlecard, marketing_social, positioning_map |
| `product_dev` | **기능 추가** · **기능 개선** · **기능 제거** · **우선순위 재조정** | comparison_matrix, reaction_insight |
| `mixed` | 위 8개 동사 모두 사용. 각 feature·액션에 `action_lens: "marketing" \| "product_dev" \| "both"` 라벨 부여 [ref:pipeline_topology_redesign.md §11-9 §6-3] | 전 7개 리포트 |

**적용 규칙**:
- 각 리포트의 출력 항목은 위 동사 집합 중 **1개 이상**의 동사로 끝나는 액션을 포함해야 함. 액션이 없는 항목은 anti-pattern.
- battlecard의 FIA Act, market_context_swot의 TOWS 액션, executive_summary의 Resolution은 본 동사 집합으로 작성.
- `mixed` 채택 시 Feature Selection UI는 `action_lens` 라벨로 grouping·filtering 가능 [ref:pipeline_topology_redesign.md §11-9].

---

## 4. 도메인 횡단 Anti-pattern

본 절의 anti-pattern은 7개 reference 문서 §2-4(도메인 특유 분석 함정)에서 도메인을 추상화한 결과입니다. DomainTaxonomyAgent는 taxonomy 생성 시 다음 함정을 회피해야 합니다.

**AP-1. 한시 프로모션을 영구 강점으로 오인** [ref:reference_battlecard.md §2-4, reference_comparison_matrix.md §2-3]
- 증상: 경쟁사의 한시 면제 혜택을 자사 정시 혜택과 동일 선상에서 비교.
- 회피: 모든 Fact에 `valid_until` 필드 의무 기록. comparison_matrix에 footnote.

**AP-2. "수수료 0%·무료" 같은 절대 단어의 범위 누락** [ref:reference_battlecard.md §2-4]
- 증상: 매수/매도 비대칭, 통화 범위, 시점 의존성 미명시.
- 회피: 정량 항목은 단위 + 범위 + 시점 3종 필수. comparison_matrix 1–5점 루브릭 3점 이상 조건.

**AP-3. 표본 크기·시점 미표기** [ref:reference_reaction_insight.md §2-4, reference_comparison_matrix.md §2-3]
- 증상: 만족도·sentiment 단일 평균만 노출, 표본 크기·수집 시점 누락.
- 회피: 모든 정량 보고에 `sample_size` + `collected_at` 메타데이터. ABSA 7-tuple의 `posted_at` 필수.

**AP-4. 시즌성·시점 변동 누락** [ref:reference_marketing_social.md §2-3, reference_market_context_swot.md §2-4]
- 증상: 시즌 의존 데이터를 단일 시점 cross-section으로 비교(여행·환율·프로모션).
- 회피: 채널·시장·반응 데이터는 동일 기간 정렬(예: 5개월 이동 평균). 비수기/성수기 명시.

**AP-5. 페르소나 평균화** [ref:reference_executive_summary.md §2-3, reference_positioning_map.md §1-6]
- 증상: 단기·장기·디지털노마드 페르소나를 평균낸 단일 권장. 누구에게도 적합하지 않음.
- 회피: positioning_map은 페르소나 다중 뷰 필수. executive_summary는 페르소나 3개(CEO/CMO/PM) 분기 권장.

**AP-6. 단순 나열 — 액션 미도출** [ref:reference_executive_summary.md §2-3, reference_market_context_swot.md §1-2]
- 증상: SWOT 4분면 항목 나열만, 6개 리포트 결론 단순 나열. TOWS·FIA·Pyramid 미적용.
- 회피: §3 동사 집합으로 끝나는 액션이 모든 항목에 포함. battlecard FIA·SWOT TOWS·executive_summary Pyramid 의무 적용.

**AP-7. 매크로 환경 → 자사 영향 과대 추정** [ref:reference_market_context_swot.md §2-4]
- 증상: PESTLE 6요소 중 도메인 무관 항목(예: 트래블카드의 Environmental)에 분량·비중 과대 부여.
- 회피: PESTLE 6요소 중 4요소(P·E·S·T) 우선 분석, 나머지 2요소는 영향 사전 점검 후 보조 처리.

**AP-8. 단일 채널 의존** [ref:reference_reaction_insight.md §1-1, reference_marketing_social.md §1-1]
- 증상: reaction_insight를 YouTube 댓글만으로, marketing_social을 단일 채널 게시물만으로 산출.
- 회피: VoC "최소 2개 채널" 원칙. marketing_social PESO 분류 4분면 동시.

**AP-9. 분석 단위 혼동 — reaction vs marketing** [ref:reference_marketing_social.md §1-1]
- 증상: 사용자 발화(reaction_insight)와 자사·경쟁사 발화(marketing_social)를 동일 분석 단위로 처리.
- 회피: 데이터 소스·분석 대상·출력 단위가 다름을 명시. 같은 SNS라도 댓글(reaction)과 게시물(marketing) 구분.

**AP-10. Forward Reference 의존** [ref:pipeline_topology_redesign.md §11-10 "Inline 인용 > Forward Reference"]
- 증상: battlecard·executive_summary 등이 "자세한 내용은 X 리포트 참조" 형태로 작성되어 단일 페이지에서 의사결정 미완결.
- 회피: 상류 리포트 결과를 **inline 직접 인용**. 페이지 이동 없이 의사결정 근거 완결.

---

## 5. Rubric 유지·확장 정책 (요약)

- **분량 상한**: 본 Rubric §2의 7개 리포트 정의는 리포트당 25–35줄, 총 약 200줄 이내 유지 [ref:pipeline_topology_redesign.md §11-8]. system_prompt inline 인용 시 약 1.5k 토큰.
- **갱신 주기**: 리포트 종류 추가/제거, 액션 동사 집합 변경 시에만 본 Rubric 수정. 도메인이 추가되어도 본 Rubric은 변경하지 않고 `docs/reference/examples/{enum}_{domain_slug}.md` worked example만 추가 [ref:pipeline_topology_redesign.md §11-8].
- **버전 관리**: Rubric 변경 시 `agents/domain_modeling/system_prompt_kr.md`의 footer에 `# Rubric: vX.Y` 주석 부착. 캐시 키에 Rubric 버전 포함하여 자동 무효화.
- **빌드 자동화**: D9 방식 1 채택(v0.8)에 따라 `scripts/build_prompts.py`가 본 Rubric §2·§3을 system_prompt에 자동 inline 인용 [ref:pipeline_topology_redesign.md §6-0 D9 확정].

---

## 6. 변경 이력

| 버전 | 일자 | 변경 내용 | 비고 |
|:-:|---|---|---|
| 0.1 | 2026-05-21 | 초안 작성 — §1 개요, §2 7개 리포트 정의(목적·표준 카테고리·좋은/나쁜 예시·평가 루브릭), §3 액션 가능성 동사 집합(D7 확정), §4 도메인 횡단 anti-pattern 10종, §5 유지 정책. 7개 reference 문서 + 4개 worked example을 1차 소스로 활용, 모든 항목에 `[ref:파일명 §x-x]` / `[ex:파일명 §x]` inline 출처 표기 | DRAFT — 사용자 검토 대기 |
