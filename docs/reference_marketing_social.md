# 마케팅·소셜 분석 외부 레퍼런스 정리

> - **목적**: 파일럿 도메인 "토스 트래블카드"의 marketing_social 리포트 설계를 위해, ① PESO·engagement benchmark·키워드 추출 등 일반 방법론과 ② 트래블카드 자사·경쟁사 채널 운영 사례를 외부 자료로부터 수집·정리합니다.
> - **활용 범위**: marketing_social 노드의 채널 매트릭스·키워드 분석·공백 식별 양식 설계 시 참조합니다(본 문서는 레퍼런스 정리만 다루며, 노드 구현 가이드는 별도 작성).
> - **분석 대상**: **자사·경쟁사의 채널 운영 전략(공급 측)**. 사용자 반응(수요 측)은 `reference_reaction_insight.md`에서 별도 다룹니다
> - **문서 버전**: v1.0 | 작성일: 2026-05-19

---

## 1. 방법론 레퍼런스

### 1-1. reaction_insight와의 명시적 분리

본 분석은 reaction_insight와 분석 대상·데이터 소스·출력 단위가 모두 다른 별개 작업입니다. 사용자가 본 프로젝트의 4번째 리포트로 marketing_social을 채택할 때 두 분석을 혼동하지 않도록 다음 표를 baseline으로 둡니다.

| 비교 항목 | reaction_insight | marketing_social |
|---|---|---|
| 분석 대상 | **사용자**가 자사·경쟁사에 대해 표현한 의견 | **자사·경쟁사**가 채널별로 운영하는 영업·콘텐츠 전략 |
| 데이터 소스 | YouTube 댓글 · 커뮤니티 · 앱스토어 리뷰 | 자사·경쟁사 SNS 채널 · 블로그 RSS · YouTube Data API |
| 출력 단위 | `(aspect, polarity, intensity, quote, ...)` | `(channel, posting_frequency, audience_size, top_keywords, ...)` |
| 핵심 질문 | "사용자는 무엇을 좋아하고 싫어하는가" | "경쟁사는 어디서 어떻게 메시지를 노출하는가" |

같은 SNS 데이터를 보더라도, reaction_insight는 댓글(사용자 발화)을 보고, marketing_social은 게시물(자사/경쟁사 발화)을 봅니다.

### 1-2. PESO 모델 — 채널 분류의 4분면

Paid·Earned·Shared·Owned(PESO) 모델은 마케팅 채널을 구분하는 가장 보편적 표준입니다. Brandwatch, Sprout Social, Smart Insights, Northbeam이 공통 채택하는 정의는 다음과 같습니다.

- **Paid**: 광고비를 지불해 노출하는 채널. TV CF · 옥외 · 검색 광고 · SNS 광고.
- **Owned**: 자사가 직접 운영·통제하는 채널. 공식 홈페이지 · 자사 앱 알림 · 뉴스레터 · 공식 블로그.
- **Shared**: 사용자·파트너와 함께 만드는 채널. 공식 SNS 계정의 댓글·공유, 커뮤니티 활동.
- **Earned**: 자사가 통제하지 않는 자연 노출. PR · 사용자 후기 · 인플루언서 자발 언급.

본 프로젝트는 자사·경쟁사의 **Paid + Owned + Shared** 운영을 측정합니다. Earned는 reaction_insight가 별도 분석하므로 marketing_social에서는 보조 지표로만 활용합니다.

참조: [Sprout Social — Paid, Owned and Earned Media](https://sproutsocial.com/insights/paid-owned-and-earned-media/), [Brandwatch — PESO 정의·측정](https://www.brandwatch.com/blog/define-measure-paid-owned-earned-media/), [Smart Insights — PESO 모델 가이드](https://www.smartinsights.com/digital-marketing-strategy/customer-acquisition-strategy/new-media-options/), [Northbeam — PESO 통합 전략](https://www.northbeam.io/blog/peso-model-integrating-paid-earned-shared-and-owned-media)

### 1-3. 채널별 engagement 벤치마크 (2024–2026 기준)

채널별 평균 engagement rate는 공정 비교를 위해 산업별 베이스라인을 필요로 합니다. Improvado·Rival IQ·Sprout Social·Hootsuite가 보고한 2024–2026 평균치는 다음과 같습니다.

| 플랫폼 | 평균 engagement | 비고 |
|---|:-:|---|
| TikTok | 3.70% | 가장 높음, 단편 영상 중심 |
| LinkedIn | 2.05% | B2B 강세 |
| Instagram | 0.48–0.98% | Reels는 micro-account에서 45–65% reach |
| Facebook | 0.15% | 지속 하락 추세 |

**중요한 주의 사항**: engagement rate는 분모 정의에 따라 같은 계정이 5–20배까지 다른 수치를 보일 수 있습니다. `interactions ÷ followers`, `interactions ÷ impressions`, `interactions ÷ reach` 중 어느 것인지 반드시 명시해야 비교 가능합니다.

본 프로젝트는 **`interactions ÷ followers`** 를 기본 지표로 채택합니다(데이터 접근성과 비교 가능성이 가장 높음).

참조: [Improvado — 2026 Social Media Benchmarks by Industry](https://improvado.io/blog/social-media-benchmarking), [Rival IQ — 2024 Social Media Industry Benchmark Report](https://www.rivaliq.com/blog/social-media-industry-benchmark-report-2024/), [Sprout Social — Benchmarks by Industry 2025](https://sproutsocial.com/insights/social-media-benchmarks-by-industry/), [Hootsuite — 2026 Benchmarks](https://blog.hootsuite.com/social-media-benchmarks/)

### 1-4. 게시 주기 best practice

채널별 최적 게시 주기는 알고리즘과 사용자 피로도의 균형점입니다. 2024–2026 권장값은 다음과 같습니다.

- **LinkedIn**: 주 2–5회
- **Instagram**: 주 4–7회
- **TikTok**: 일 1–3회 (일관성 우선)
- **YouTube 긴 영상**: 주 1–2회
- **YouTube Shorts**: 주 5–10회
- **공식 블로그**: 주 2–3회

본 프로젝트는 채널별 게시 빈도를 위 권장값과의 격차로 측정합니다 — "경쟁사가 권장값의 50%만 운영하는가, 200%를 운영하는가"의 비율 지표가 단순 횟수보다 의미를 가집니다.

참조: [Sprinklr — Social Media Competitor Analysis Step-by-Step](https://www.sprinklr.com/blog/social-media-competitor-analysis/), [Dash Social — Competitive Analysis Template](https://www.dashsocial.com/blog/social-media-competitive-analysis), [Sociality.io — Top 5 Competitor Analysis Tools 2024](https://sociality.io/blog/social-media-competitor-analysis-tools/)

### 1-5. 키워드·토픽 추출 방법론

콘텐츠의 핵심 메시지는 키워드·토픽 추출로 정량화합니다. 2024 표준은 다음과 같습니다.

- **빈도 기반(TF-IDF)**: 가장 단순. 빠르나 의미 깊이가 얕음.
- **Latent Dirichlet Allocation (LDA)**: 비지도 토픽 모델. 잠재 토픽을 1024차원 등으로 추출.
- **LLM 기반(GPT-4·Claude)**: zero/few-shot으로 키워드와 토픽을 함께 추출. 정확도 가장 높으나 비용 발생.

본 프로젝트는 Claude API zero-shot을 1차 채택합니다. 출력 형식은 `[{keyword, frequency, top_examples: [post_url]}]` 4-tuple로 통일하여 channel × keyword cross-tab을 산출 가능하게 합니다.

참조: [Springer — Social Media CI Framework for Brand Topic Identification (2024)](https://pmc.ncbi.nlm.nih.gov/articles/PMC11588230/), [MDPI — Mapping Digital Marketing Research Using NLP](https://www.mdpi.com/2078-2489/16/11/942), [Navla — SEO와 Topic Modeling 2024](https://www.navla.ai/search-engine-optimization-and-topic-modeling-in-2024/)

### 1-6. 측정 framework 통일 — AMEC Integrated Evaluation

여러 채널의 지표를 단일 framework로 통합하지 않으면 비교가 불가능합니다. AMEC(International Association for Measurement and Evaluation of Communication)의 Integrated Evaluation Framework는 Inputs · Activities · Outputs · Outtakes · Outcomes · Impact 6단계로 구성됩니다.

본 프로젝트는 단순화하여 **Outputs(게시물 생산량) + Outtakes(노출·도달) + Outcomes(반응·전환 추정)** 3단계만 측정합니다. Impact(매출·시장점유율) 측정은 본 프로젝트 범위 밖.

참조: [Determ — Track and Measure PESO Media](https://determ.com/blog/track-measure-paid-earned-shared-owned-media/), [Cobloom — Converged Media Strategy](https://www.cobloom.com/blog/converged-media-strategy-paid-earned-owned)

### 1-7. 데이터 소스 신뢰성·법적 고려

채널 운영 데이터는 수집 방식에 따라 신뢰도와 법적 위험이 다릅니다.

- **YouTube Data API v3 (공식)**: 채널 통계·영상 메타데이터 합법 수집. 일일 quota 10,000 units 제한.
- **Instagram Graph API (Business 계정 한정)**: 자사 계정 또는 공개 Business 계정만 합법 수집.
- **공개 SNS 스크래핑**: ToS 위반 위험. 본 프로젝트 1차 범위에서 제외.
- **공식 블로그 RSS·sitemap**: 합법 수집. 게시 빈도·키워드 추출에 충분.
- **TV CF·옥외 광고**: 자동 수집 불가. PR 보도자료·자사 보도자료 페이지에서 수동 보완.

본 프로젝트 1차 데이터 소스는 **YouTube Data API + 자사·경쟁사 공식 블로그 RSS + PR 보도자료**로 한정합니다. SNS 스크래핑은 후속 도입.

---

## 2. 도메인 레퍼런스 — 트래블카드 채널 운영 사례

### 2-1. 자사 토스의 광고 전략 일반

토스는 광고에서 두 가지 차별 전략을 운영합니다.

- **YouTube 캠페인 강세**: Think with Google 사례에서 토스는 예고편 시청자에게 본편 광고를 리타겟팅하는 2단계 광고 전략으로 본편 시청률을 끌어올렸습니다. 18–44세 남녀 + 금융·보안 관심 카테고리 타겟팅.
- **자사 광고 플랫폼(토스애즈)**: 퍼스트 파티 결제 데이터 기반 타겟팅. 자사 앱 내 광고가 owned media의 핵심.

단, 토스의 마케팅 자원은 토스뱅크·토스증권 등 본사 상품에 집중되며, **트래블카드 단독 광고 캠페인은 본 검색 시점(2026-05) 기준 노출이 약합니다**. 이는 marketing_social 분석의 핵심 발견이 될 수 있습니다.

참조: [Think with Google — 토스 YouTube 캠페인 사례](https://www.thinkwithgoogle.com/intl/ko-kr/marketing-strategies/video/youtube-campaign-success-case-toss/), [토스애즈 공식](https://tossads.toss.im/), [OpenAds — 2025년 5월 토스 광고 핵심 요소](https://openads.co.kr/content/contentDetail?contsId=16274)

### 2-2. 경쟁사 채널 운영 추정 (공개 정보 기반)

본 절은 매체 자료·공식 페이지에서 확인된 채널 운영 추정치입니다. 실제 marketing_social worked example에서는 YouTube Data API로 정량 측정합니다.

- **트래블월렛**: 인스타그램·YouTube Shorts 중심. 핵심 메시지 "One card, 46 currencies" + "친구와 환전" 반복 노출. 자체 앱 onboarding이 owned media.
- **하나 트래블로그**: 하나금융그룹 자행 채널 + 공항·기차역 옥외 광고. 분기 단위 캠페인. 메시지는 "58개국 통화 + 3% 적립" 중심.
- **신한 SOL트래블**: TV CF + 뉴스 PR 활용. 신한카드 owned 채널 + 신한금융그룹 통합 캠페인. "공항 라운지" 메시지 강조.
- **KB국민 트래블러스**: KB Pay 앱 owned + 한시 프로모션 PR. 정시 광고 운영은 약함.
- **우리카드 위비트래블**: 우리은행 자행 채널 중심. SNS 운영 규모 작음.

위 5개 카드 중 정시 외부 노출(Paid·Earned) 강도는 **트래블월렛 > 신한 > 하나 ≈ KB > 우리 ≈ 토스** 순으로 추정됩니다.

참조: [Travel Wallet 공식](https://www.travel-wallet.com/en), [홀라플라이 — 트래블월렛·트래블로그 비교](https://esim.holafly.com/ko/travel-tips/travel-log-travel-wallet-comparison/), [헤어트래블 — 해외여행 카드 비교](https://info.heretravel.co.kr/board-post/2201)

### 2-3. 시즌성 — 여행 시기와 콘텐츠 생산 주기

트래블카드 도메인은 강한 시즌성을 가지므로 measurement 기간 정렬이 중요합니다.

- **여름 휴가철(6–8월)**: 콘텐츠 생산량 평소 대비 2–3배 증가. 광고 노출도 정점.
- **연말(11–12월)**: 신년 여행 계획 콘텐츠 증가.
- **명절 전후(설·추석)**: 단기 일본·동남아 여행 광고 집중.
- **비수기(3–4월, 10–11월)**: 채널 운영 강도 평균 대비 30–50%.

본 프로젝트는 비교 시 동일 기간(예: 2025-12 ~ 2026-04, 5개월)으로 정렬해야 합니다. 단일 시점 cross-section 비교는 시즌 편향에 취약합니다.

### 2-4. 도메인 특유의 분석 함정

- **자사 광고 부재 ≠ 자사 약점**: 토스 트래블카드 단독 광고가 약한 이유가 본사의 전략적 자원 배분일 수 있음. "광고 노출이 적으니 마케팅 약함"으로 단순 결론하면 오류.
- **인플루언서 광고 식별 누락**: 트래블월렛은 인플루언서 마케팅이 활발하나 공식 계정 운영 데이터로는 보이지 않음. PR·인플루언서 협찬 정보는 별도 보완 필요.
- **owned 채널의 측정 편향**: 자사 앱·뉴스레터는 사용자에게 도달하나 외부에서 측정 불가. 자사·경쟁사 간 비교에서 owned 측정 비대칭 주의.
- **TV CF의 디지털 미측정**: 신한·하나는 TV CF·옥외 광고가 강점이나 GRP·도달 측정이 본 프로젝트의 디지털 데이터 소스로 불가. 보조 출처 필수.

---

## 3. 종합 — marketing_social 권장안 요약

방법론과 도메인 레퍼런스를 종합한 권장 양식은 다음과 같습니다(본 문서는 권장안 요약까지만 다루며, 실제 스키마·노드 구현은 별도 작업).

- **포함 요소**: ① 채널 운영 매트릭스(주력 채널·게시 주기·소비 규모·핵심 키워드) — battlecard B-4와 정렬, ② PESO 4분면 분류(Paid·Owned·Shared의 각 채널 강도), ③ 채널 × 키워드 cross-tab, ④ 자사 미점유 채널·메시지 공백 식별, ⑤ 시즌성 보정 측정 기간 명시.
- **표기 규약**: engagement rate는 `interactions ÷ followers`로 통일. 게시 주기는 권장값 대비 비율(%)로 표기. 핵심 키워드는 빈도·예시 게시물 URL과 함께 보존.
- **양식**: 5개 카드의 채널 매트릭스(주력) + PESO 4분면 도식 + 키워드 cross-tab + 자사 공백 분석의 4단 구성. battlecard·executive_summary로 흐름 B 인용.

---

## 4. 결정된 사항 (사용자 확정)

- **결정 1**: marketing_social의 데이터 소스는 **YouTube Data API + 자사·경쟁사 공식 블로그 RSS + PR 보도자료** 1차 범위. SNS 스크래핑은 후속 도입.
- **결정 2**: marketing_social은 흐름 A leaf 노드이나 battlecard·market_context_swot·executive_summary가 marketing_social 결과를 흐름 B로 inline 인용합니다(§11-10 "Inline 인용 > Forward Reference" 원칙).
- **결정 3**: 채널 매트릭스 구조는 `battlecard_toss_travel_card.md` B-4의 4개 컬럼(주력 채널·게시 주기·소비·핵심 키워드)과 동일하게 유지하여 두 worked example이 정렬됩니다.

---

## 5. 사용자가 추가로 검토할 만한 꼬리 질문

1. 인플루언서 마케팅·PR 데이터를 본 프로젝트 1차 범위에 포함하시겠습니까? 트래블월렛·하나 트래블로그가 운영하는 인플루언서 협찬은 공식 SNS 계정 데이터로 직접 노출되지 않아, 별도 수집 채널(PR 모니터링 도구·구글 알리미 등)이 필요합니다.
2. TV CF·옥외 광고 같은 디지털 미측정 채널을 marketing_social 분석에 포함하시겠습니까, 아니면 디지털 채널만으로 한정하시겠습니까? 신한·하나의 강점이 TV·옥외이므로 제외 시 비교가 편향됩니다.
3. 채널별 engagement rate의 정의(interactions ÷ followers)를 본 프로젝트가 강제할지, 도메인에 따라 자동 선택하게 할지 결정이 필요합니다. 본 프로젝트 1차 범위는 트래블카드 단일이라 강제가 단순하나, 도메인이 확장되면 채널별 데이터 가용성에 따라 선택이 필요할 수 있습니다.
