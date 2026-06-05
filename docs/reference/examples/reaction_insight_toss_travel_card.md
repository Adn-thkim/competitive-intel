# Worked Example — `reaction_insight` × 토스 트래블카드

> - **목적**: Rubric `docs/reference/report_taxonomy.md` §2-2(고객 반응 인사이트)의 추상 기준이 트래블카드 도메인에서 어떻게 구체화되는지를 시연합니다.
> - **도메인 슬러그**: `consumer_travel_card_kr`
> - **자사 상품**: 토스 트래블카드 (`own_toss_travel_card`)
> - **분석 방향**: `mixed` (marketing + product_dev 양쪽 라벨링)
> - **데이터 소스 (다채널, mock data)**:
>   - YouTube 영상 요약 + 댓글: 영상 30개 / 댓글 약 1,800건 (가중치 1.0)
>   - 커뮤니티(클리앙·디시): 게시글 본문 60건 + 댓글 약 700건 (가중치 1.2)
>   - 앱스토어 리뷰(토스·트래블월렛만 수집 가능): 별점 + 본문 약 1,200건 (가중치 0.8)
>   - 통합 표본: 약 3,700 unit (영상은 1 unit, 게시글은 본문 1 + 댓글 1로 분리)
> - **출처 자료 기준일**: 2026-05-19
> - **작성일**: 2026-05-19 (v0.2 — 다채널 데이터 소스 반영)

---

## 1. 적용된 Rubric §2-2 항목 (요약 인용)

- **리포트 목적**: 자사·경쟁사 상품에 대한 자연 발생(unsolicited) 사용자 텍스트를 ABSA로 분해해 마케팅 메시지 강화 포인트와 product_dev 개선 포인트를 도출합니다.
- **aspect codebook**: 도메인 횡단 고정 목록이 아닌 `DomainTaxonomyAgent`가 본 도메인(트래블카드)에 맞춰 자동 생성한 7개 aspect. 사용자가 Feature Selection interrupt #4에서 추가·제거·재명명 가능.
- **출력 단위**: `(aspect, polarity, intensity, quote, source_url, channel, posted_at)` **7-tuple** (다채널 결정 반영).
- **평가 루브릭**: aspect 카테고리 모두 1개 이상 quote로 채움(필수) / suggestion 카테고리 별도 분리율 ≥ 95% / 좋은 quote 비율(원문 보존·번역 없음) = 100% / **3개 채널 모두에서 1개 이상 quote 수집** ≥ 90% aspect.
- **나쁜 feature 조건**: aspect 미라벨링 raw text, 번역·요약된 quote, frequency만 보고하고 intensity 누락, 단일 채널 quote만으로 sentiment 산출.

---

## 2. 도출된 aspect 분석 결과 (7개 카테고리)

| # | aspect_id | 카테고리 | action_lens | 카드별 sentiment(positive 비율) | 대표 quote (원문 보존) |
|:-:|---|---|:-:|---|---|
| 1 | `exchange_convenience` | 환전·충전 편의성 | marketing | 토스 88% · 월렛 84% · 로그 76% | "토스 앱에서 5초 만에 환전 끝. 다른 카드들은 별도 앱 깔아야 함." |
| 2 | `fee_perception` | 수수료 체감 | marketing | 토스 92% · 월렛 89% · 로그 85% | "달러 환전 진짜 0원 맞아요. 인천공항 환전소 가던 제가 바보였음." |
| 3 | `atm_availability` | 현지 ATM 호환성 | product_dev | 토스 71% · 월렛 78% · 로그 82% | "일본 세븐일레븐에서 한 번에 됐는데, 패밀리마트는 안 됨. 카드 따라 다른가?" |
| 4 | `app_ux_quality` | 앱 UX | both | 토스 94% · 월렛 80% · 로그 73% | "토스 앱이 진짜 직관적. 부모님도 쓰실 수 있을 정도." |
| 5 | `customer_support` | 고객지원 | product_dev | 토스 65% · 월렛 70% · 로그 72% | "주말에 카드 잃어버렸는데 토스는 대응이 느림. 트래블로그는 24시간 콜센터 됨." |
| 6 | `additional_benefit` | 부가 혜택 | marketing | 토스 45% · 월렛 52% · 로그 81% | "라운지 못 들어가는 게 아쉬움. 그래도 수수료 0원이라 못 갈아탐." |
| 7 | `social_features` | 소셜 기능 | both | 토스 N/A · 월렛 79% · 로그 N/A | "트래블월렛 모임 기능으로 친구들이랑 환전 같이 했더니 편했어요." |

**aspect codebook 자동 생성**: 위 7개 aspect 전체가 `DomainTaxonomyAgent`가 트래블카드 도메인 컨텍스트(자사 상품 요약·경쟁 축·target_user)로부터 자동 생성한 결과입니다. 도메인 횡단 baseline aspect(`exchange_convenience`, `fee_perception`, `atm_availability`, `app_ux_quality`, `customer_support`, `additional_benefit`) 6종에 트래블카드 도메인 특화 aspect(`social_features`, 트래블월렛 모임 기능에서 도출) 1종이 추가된 형태입니다. 다른 도메인(예: SaaS, 교육)에서는 동일 매커니즘으로 다른 aspect 집합이 자동 생성됩니다.

---

## 2-A. 채널별 표본 분포와 aspect × channel cross-tab

aspect별 sentiment positive 비율을 채널별로 분리하면, 채널 편향을 정량 확인할 수 있습니다. 토스 트래블카드 기준 cross-tab의 예시(positive 비율 %):

| aspect | YouTube (가중 1.0) | 커뮤니티 (가중 1.2) | 앱스토어 (가중 0.8) | 통합 |
|---|:-:|:-:|:-:|:-:|
| `exchange_convenience` | 90 | 85 | 88 | 88 |
| `fee_perception` | 94 | 90 | 89 | 92 |
| `atm_availability` | 78 | 64 | 67 | 71 |
| `app_ux_quality` | 96 | 92 | 91 | 94 |
| `customer_support` | 71 | 58 | 62 | 65 |
| `additional_benefit` | 52 | 41 | 38 | 45 |

채널 간 편차가 큰 aspect(`additional_benefit`: YouTube 52% vs 앱스토어 38%)는 채널별 분리 뷰를 본문에 명시해야 하며, 단일 통합 점수만 보고하면 의사결정자가 편향을 인지하지 못합니다. 본 예시에서는 §2의 통합 표가 의사결정 1차 자료, 본 §2-A 표가 검증 자료 역할을 합니다.

**대표 quote 채널 다양성 (3개 채널 모두에서 인용)**

- YouTube: "토스 앱에서 5초 만에 환전 끝. 다른 카드들은 별도 앱 깔아야 함." (`exchange_convenience`)
- 커뮤니티(클리앙): "주말에 카드 잃어버렸는데 토스는 대응이 느림. 트래블로그는 24시간 콜센터 됨." (`customer_support`)
- 앱스토어 (별점 2점): "환전은 좋은데 일본 패밀리마트 ATM 호환 안 되어 별점 깎습니다." (`atm_availability`, 별점-본문 polarity 모순 사례로 본문 우선)

---

## 3. 액션 우선순위 분석 (frequency × intensity 가중)

aspect별 언급 빈도와 sentiment 강도를 결합해 액션 우선순위를 산정합니다. 토스 트래블카드 기준 다음과 같습니다.

| 우선순위 | aspect | 빈도 | 평균 intensity | 종합 | action_lens | 권고 액션 |
|:-:|---|:-:|:-:|:-:|:-:|---|
| 1 | `additional_benefit` | 412회 | −0.42 | 매우 부정 | marketing | "수수료 0원 강조 + 라운지 부재의 의식적 재포지셔닝(공항 시간을 절약하는 카드)" |
| 2 | `customer_support` | 287회 | −0.31 | 부정 | product_dev | "주말·해외 시간대 24시간 채팅 상담 채널 도입 검토" |
| 3 | `atm_availability` | 198회 | −0.18 | 약한 부정 | product_dev | "패밀리마트·로손 호환 ATM 목록을 앱 내 지도로 노출" |
| 4 | `app_ux_quality` | 654회 | +0.51 | 강한 긍정 | marketing | "직관성을 광고 메시지의 1차 키워드로 격상" |
| 5 | `fee_perception` | 832회 | +0.46 | 강한 긍정 | marketing | "기존 메시지 유지·강화. 사용자 후기 quote 인용 광고 검토" |

언급 빈도가 높고 intensity가 강할수록 우선순위가 올라갑니다. `app_ux_quality`(빈도 654)와 `fee_perception`(빈도 832)은 토스의 강점으로 강화 액션, `additional_benefit`(빈도 412, 매우 부정)은 가장 큰 약점이므로 재포지셔닝이 1순위입니다.

---

## 4. 평가 루브릭 점수 산정

- **aspect 카테고리 커버리지**: 7/7 — DomainTaxonomyAgent 자동 생성 codebook 전 항목에서 1개 이상 quote 수집.
- **3개 채널 quote 커버리지**: 7/7 aspect (100%) — 모든 aspect에서 YouTube·커뮤니티·앱스토어 3개 채널 모두로부터 quote가 1개 이상 수집됨.
- **suggestion 분리율**: 96% (총 153건의 suggestion 댓글 중 147건이 product_dev lens action 후보로 별도 리스트에 분리됨).
- **좋은 quote 비율**: 100% — 모든 quote가 원문 보존, 번역·요약 없음.
- **종합 점수**: **5.0 / 5.0** — Rubric §2-2 기준 통과.

---

## 5. Anti-pattern 회피 사례

본 도메인에서 흔히 빠지는 5가지 함정과 본 worked example의 회피 방식은 다음과 같습니다.

- **출시 시기 편향**: 토스 트래블카드는 댓글 누적량이 다른 카드 대비 적습니다. 본 분석은 카드별 댓글 절대 수가 아닌 **각 카드 댓글 풀 내 비율(%)**로 비교해, 표본 크기 차이를 정규화했습니다.
- **단일 채널 의존 함정**: YouTube 댓글만으로 sentiment를 산출하면 영상 주제 편향이 누적됩니다. 본 worked example은 §2-A의 채널 cross-tab으로 채널 간 편차를 명시 노출했습니다.
- **국가·여행지 편향**: 일본 여행 댓글이 약 60%를 차지하나, 여행지별 분리 뷰를 별도 산출해 "일본 사례를 전체로 일반화"하지 않도록 했습니다. 표 §3은 전체 통합 뷰이며, 여행지별 뷰는 부록(생략)에서 동일 양식으로 제공됩니다.
- **시점별 변동**: 환율 급등기·프로모션 시기 댓글이 sentiment에 강한 영향을 줍니다. 댓글 작성일(`posted_at`)을 보존하고, 최근 6개월 댓글에 1.5배 가중치를 부여했습니다.
- **계절성**: 여름 휴가철 댓글이 집중되므로, 카드 간 비교는 동일 기간(예: 2025-12 ~ 2026-04)으로 정렬했습니다.
- **앱스토어 별점-본문 모순**: 앱스토어 리뷰에서 별점과 본문 ABSA 결과가 모순되면(§2-A 마지막 quote 예시) 본문을 우선해 aspect별 polarity를 부여했습니다. 별점은 본문 ABSA 검증의 보조 신호로만 사용합니다.

---

## 6. 본 worked example 사용 시 주의 (메타 지시)

본 예시의 sentiment 비율과 빈도 수치는 시연용 mock data입니다. 실제 분석은 `youtube_collection_node`(임시 노드명, 데이터 수집 노드명 미정) 출력 데이터로 산정합니다. 또한 트래블카드 도메인의 7개 aspect는 본 도메인 특유의 codebook이며, 본인 도메인에 맹목적으로 복제하지 마십시오. Rubric §2-2의 추상 기준(7-tuple 출력 단위, 평가 루브릭, suggestion 분리 원칙)이 우선입니다. 다른 도메인은 동일 추상 기준에서 다른 aspect 집합이 도출됩니다.

---

## 7. 관련 문서

- 방법론 reference: `docs/reference/reaction_insight.md` (VoC · ABSA · NPS/CSAT · YouTube 댓글 분류 · LLM 적용 · Thematic Coding 일반 framework)
- Rubric 본체: `docs/reference/report_taxonomy.md` §2-2(고객 반응 인사이트 정의), §3(액션 가능성 동사 집합)
- 파이프라인 설계: `docs/design/pipeline_topology_redesign.md` §6-0(P0-Rubric), §11-10(worked example 작성·제공 방식)
- 인접 worked example: `docs/reference/examples/comparison_matrix_toss_travel_card.md` (비교 매트릭스, cross-link 대상)
