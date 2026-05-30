# 고객 반응 인사이트 외부 레퍼런스 정리

> - **목적**: 파일럿 도메인 "토스 트래블카드"의 고객 반응 인사이트 리포트 설계를 위해, ① VoC·ABSA·만족도 지표 등 일반 방법론과 ② 트래블카드 실제 반응 데이터를 외부 자료로부터 수집·정리합니다.
> - **활용 범위**: `youtube_query_planner` · `youtube_collection` · `reaction_analysis` 노드에서 댓글·후기 수집과 분류·점수화 규약 설계 시 참조합니다(본 문서는 레퍼런스 정리만 다루며, 노드 구현 가이드는 별도 작성).
> - **문서 버전**: v1.0 | 작성일: 2026-05-19

---

## 1. 방법론 레퍼런스

### 1-1. Voice of Customer (VoC) — 고객 반응 분석의 상위 프레임워크

VoC는 고객 피드백을 수집·분석해 제품·서비스·경험을 정렬하는 전략 프레임워크입니다. Productlogz, Convin, Fullstory가 공통적으로 권장하는 운영 원칙은 다음과 같습니다.

- **다채널 수집**: 단일 소스(예: YouTube 댓글)만으로는 편향이 누적되므로 앱스토어 리뷰·커뮤니티·콜센터 텍스트·NPS 응답 등 최소 2개 채널을 교차 검증해야 합니다.
- **고객 여정 단계 매핑**: 발견·평가·구매·사용·재구매·이탈 단계별로 반응을 분리해야 "어느 단계에서 이탈이 발생하는가"를 식별할 수 있습니다.
- **정성 + 정량 결합**: 댓글의 raw quote(정성)와 sentiment score(정량)를 동시에 보존해 두지 않으면 의사결정자가 검증 가능성을 잃습니다.

참조: [Productlogz — 6 Best Voice of Customer Frameworks](https://www.productlogz.com/blog/6-best-voice-of-customer-frameworks-for-product-companies), [Convin — 2024 VoC Analysis Trends](https://convin.ai/blog/voice-customer-analysis-trends-2024), [Fullstory — Customer Sentiment Analysis](https://www.fullstory.com/blog/sentiment-analysis/)

### 1-2. Aspect-Based Sentiment Analysis (ABSA) — 본 프로젝트의 핵심 양식

ABSA는 "이 리뷰는 긍정/부정/중립"이라는 거친 분류 대신, "제품 품질은 긍정, 고객지원은 부정"처럼 **속성(aspect)별로 감성을 분리**하는 정밀 분석 기법입니다. 2024년 Springer Nature의 systematic review는 ABSA가 본격적인 비즈니스 인텔리전스 도구로 자리잡았음을 보고합니다.

- **표준 출력 단위**: `(aspect, polarity, intensity, quote)` 4-tuple. 예: `(환전 수수료, positive, 0.85, "달러 환전이 진짜 0원이라서 놀랐어요")`.
- **고객 액션 직결**: 단일 sentiment 점수보다 "환전 수수료=긍정, 앱 UX=부정"이 마케팅·제품 액션으로 즉시 전환됩니다 — 본 프로젝트의 "마케터에게 명확한 액션 제시" 요구와 직접 부합합니다.
- **LLM 적용 가능성**: GPT-3.5 fine-tuned 모델이 SemEval-2014 ABSA task에서 F1 83.8%를 달성(2024년 보고). LLM 호출 비용을 고려해 zero/few-shot vs fine-tuned 선택을 결정해야 합니다.

참조: [Springer Nature — Systematic Review of ABSA (2024)](https://link.springer.com/article/10.1007/s10462-024-10906-z), [MDPI — Sentiment Analysis with LLM (2024)](https://www.mdpi.com/2504-2289/8/12/199), [arXiv — Retail-Corpus for ABSA with LLMs (2024)](https://arxiv.org/html/2508.17994v1)

### 1-3. NPS · CSAT · CES — 정량 만족도 지표

세 지표는 측정 대상과 시점이 다르므로 혼용하면 안 됩니다. Bain & Company의 Fred Reichheld가 2003년에 개발한 NPS의 정의가 가장 표준화되어 있습니다.

- **NPS (Net Promoter Score)**: "지인에게 추천하시겠습니까?" 0–10점. **Promoters(9–10) − Detractors(0–6)** = NPS. 브랜드와의 장기 관계 측정. 정기 측정(분기·연간).
- **CSAT (Customer Satisfaction)**: 특정 상호작용 직후의 단기 만족도. "매우 만족–매우 불만"의 5점 척도. **상위 2점(4–5점) 응답 비율**로 산출. 거래·구매 직후에 즉시 측정.
- **CES (Customer Effort Score)**: 사용자가 목표를 달성하기까지의 노력 정도. "쉬웠다–어려웠다" 7점 척도. 고객지원·온보딩에 특화.

본 프로젝트는 YouTube 댓글이라는 **자연 발생(unsolicited) 텍스트**를 다루므로 NPS·CSAT 점수를 직접 산출할 수는 없으나, ABSA aspect별 sentiment의 positive 비율을 **NPS-proxy**로 변환해 비교 매트릭스와 결합할 수 있습니다.

참조: [Qualtrics — CSAT vs NPS](https://www.qualtrics.com/articles/customer-experience/csat-vs-nps/), [SurveyMonkey — CSAT vs NPS Methodology](https://www.surveymonkey.com/learn/customer-feedback/csat-vs-nps-similarities-and-differences/), [Formaloo — CSAT vs NPS Comparison](https://www.formaloo.com/blog/csat-vs-nps-whats-the-difference-which-is-a-better-metric)

### 1-4. YouTube 댓글 분류 체계 — 6 카테고리 표준

2024년 발표된 Enhanced YouTube Comments Classifier 연구는 댓글을 다음 6개 카테고리로 분류하는 표준 체계를 제시합니다.

| # | 카테고리 | 의미 | 본 프로젝트 처리 |
|:-:|---|---|---|
| 1 | appreciation | 영상·제품에 대한 칭찬 | sentiment positive 후보 |
| 2 | normal | 일상 의견 | aspect 추출 후 분류 |
| 3 | suggestion | 개선 제안 | product_dev 액션 lens 후보 |
| 4 | question | 질문 | FAQ 신호로 분리, sentiment 분석 제외 |
| 5 | trolling | 비방·도배 | 사전 필터링으로 제거 |
| 6 | other_languages | 비주 언어 | 별도 채널로 분리 |

본 프로젝트에서는 `question`과 `trolling`을 **사전 필터링**하고, `suggestion`은 product_dev lens 액션 후보로 별도 분류해야 합니다. 단순 sentiment 분석이 이 두 카테고리를 함께 처리하면 정확도가 떨어집니다.

참조: [IJERT — Advanced Techniques for YouTube Comment Analysis (2024)](https://www.ijert.org/unveiling-insights-advanced-techniques-for-youtube-comment-analysis), [Sage Journals — Identifying Relevant YouTube Comments (Möller et al., 2024)](https://journals.sagepub.com/doi/10.1177/08944393231173895)

### 1-5. LLM 기반 분석의 비용·정확도 trade-off

본 프로젝트는 Claude Code CLI 기반이므로 zero/few-shot 프롬프트로 ABSA를 수행할 가능성이 높습니다. 2024년 비교 연구의 시사점은 다음과 같습니다.

- **Zero/few-shot LLM**: 도메인 적응 비용 0, 구현 단순. 단 GPT-3.5 기준 fine-tuned 모델보다 inference cost가 약 1,000배 높습니다.
- **Fine-tuned 소규모 모델**: 비용 효율적이나 도메인 적응 비용 발생. 학습 데이터 1,000건 이상 필요.
- **하이브리드**: LLM이 aspect 추출 → 소규모 모델이 polarity 분류. 본 프로젝트 규모에서는 과도한 복잡성.

**권장 1차 안**: Claude API zero-shot으로 시작, 결과 품질이 부족하면 few-shot(트래블카드 도메인 예시 5–10개) 추가. fine-tuning은 도메인 횟수 누적 후 재검토합니다.

참조: [MDPI — ML and Pre-Trained LLM Sentiment Analysis (2024)](https://www.mdpi.com/2504-2289/8/12/199), [arXiv — End-to-End Aspect-Guided Review Summarization (2024)](https://arxiv.org/pdf/2509.26103)

### 1-6. Thematic Coding — 대용량 댓글의 주제 도출

수만 개 댓글에서 주제(theme)를 도출할 때, manual coding은 비용·재현성 문제로 한계가 있습니다. 2024년 권장 방식은 다음과 같습니다.

- **사전 코드북(codebook) 작성**: ABSA aspect 목록을 사전 정의하고, LLM이 each 댓글을 1개 이상의 aspect에 할당합니다. 코드북 없이 자유 분류하면 결과가 비결정적입니다.
- **빈도 + 강도 동시 보고**: aspect별 언급 횟수(frequency)와 평균 intensity를 함께 보고합니다. "1회 언급에 strong negative"보다 "30회 언급에 mild positive"가 액션 우선순위가 높을 수 있습니다.
- **재현성 검증**: 동일 댓글 셋에 대해 2회 실행한 결과의 aspect 할당 일치율(Cohen's κ)을 0.7 이상 유지해야 합니다.

참조: [ResearchGate — Classification Scheme for YouTube Comments](https://www.researchgate.net/publication/263068183_A_classification_scheme_for_content_analyses_of_YouTube_video_comments), [AWS Tech Blog — LG전자 소셜미디어 분석 (Amazon Bedrock 사례)](https://aws.amazon.com/ko/blogs/tech/lg-social-media-analysis-with-amazon-bedrock/)

---

## 2. 도메인 레퍼런스 — 트래블카드 사용자 반응

### 2-1. 정량 만족도 데이터 (2025년 기준)

매체 조사에 따르면 트래블카드별 이용 경험 만족도는 다음과 같습니다.

| 카드 | 만족도 | 비고 |
|---|:-:|---|
| 트래블월렛 | 82.3% | 1위 |
| 하나 트래블로그 | 82.1% | 트래블월렛과 동률 수준 |
| 신한 SOL트래블 | 72.2% | 라운지 혜택 강점 |
| 토스 트래블카드 | 69.8% | 출시 시기 영향 가능성 |

토스의 만족도가 4종 중 가장 낮다는 점은 본 프로젝트의 핵심 분석 포인트가 되어야 합니다. 단, 이 점수는 매체 표집의 한계가 있으므로 **표본 크기와 수집 시점**을 별도 컬럼으로 보존해야 합니다.

참조: [브런치 — 트래블월렛 분석](https://brunch.co.kr/@bydot/8), [헤어트래블 — 해외여행 카드 비교 추천 총정리](https://info.heretravel.co.kr/board-post/2201)

### 2-2. 다채널 데이터 소스 구성 (본 프로젝트 채택 채널)

본 프로젝트는 §1-1 VoC의 다채널 원칙에 따라 다음 3개 채널을 표준 데이터 소스로 채택합니다. 채널별 성격이 다르므로 가중치·전처리·라벨링 규약을 다르게 둡니다.

**채널 1 — YouTube 영상 요약 + 댓글 (필수)**

- 수집 단위: 영상 자막·설명 요약(영상 단위) + 상위 N개 영상의 댓글(댓글 단위).
- 성격: 영상 콘텐츠 컨텍스트가 댓글 해석의 단서가 됩니다. 댓글당 분량이 짧고 영상 주제에 종속되므로, 댓글을 그 영상의 요약과 함께 컨텍스트 페어로 저장해야 합니다.
- 처리 규약: `(comment, parent_video_summary)` 페어로 저장. 영상 요약은 영상 단위로 1회만 생성해 토큰 비용을 절감합니다.
- 가중치 안: 1.0 (기준 채널).

**채널 2 — 커뮤니티 (클리앙·디시인사이드 갤러리)**

- 수집 단위: 게시글 본문 + 댓글. 본문 길이가 길어 ABSA 정밀도가 가장 높습니다.
- 성격: 익명성으로 인한 격한 표현·과장 가능성이 있으나 "실사용 비교 시나리오"가 풍부합니다.
- 처리 규약: 본문 + 댓글을 함께 하나의 unit으로 처리하되 본문은 1.5배, 댓글은 1.0배 intensity 가중.
- 가중치 안: 1.2 (시나리오 풍부도 보정).
- 출처 예시: [클리앙 — 외화 선불카드 간단 비교](https://www.clien.net/service/board/lecture/18806850), [디시인사이드 — 트래블카드 5종 사용 후기](https://gall.dcinside.com/mgallery/board/view/?id=nokanto&no=552186)

**채널 3 — 앱스토어 리뷰 (도메인 수집 가능 시)**

- 수집 단위: 별점(1–5) + 리뷰 본문. 별점이 polarity의 보조 신호로 사용 가능합니다.
- 성격: 사용 직후 작성이 많아 즉시성이 강하나 이탈 직전 부정 편향이 존재합니다. 별점 1점과 5점에 응답이 양극화되는 경향(U-shape).
- 처리 규약: 별점을 polarity의 강한 prior로 사용하되 본문 ABSA 결과와 모순되면 본문을 우선합니다(예: 별점 5점인데 본문이 "환전은 좋은데 ATM 안 됨"이면 ATM aspect는 negative).
- 수집 가능성 분기: 본 프로젝트 도메인이 모바일 앱을 갖는 경우에만 활성화. 자사·경쟁사 중 일부만 앱이 있는 경우 해당 카드만 부분 수집하고 그 사실을 채널 메타데이터에 명시.
- 가중치 안: 0.8 (부정 편향 보정).

**채널 미채택 — 블로그·신문 기사**

- 광고성·협찬 포함 가능성이 있어 자연 발생(unsolicited) 텍스트로 보기 어려움. ABSA 입력에서 제외하고 §1-1 다채널 cross-validation의 보조 자료로만 활용합니다.

**다채널 정합성 규약**

- 동일 aspect에 대해 채널 간 sentiment가 큰 차이를 보이면(예: YouTube positive 90% vs 커뮤니티 positive 40%) 그 사실을 리포트에 명시합니다. 채널 편향을 숨기지 않습니다.
- 채널별 표본 크기를 산출물에 보존하여 매체 단일 출처 의존도를 추적 가능하게 합니다.

### 2-3. ABSA aspect codebook의 자동 생성 (DomainTaxonomyAgent 책임)

aspect codebook은 도메인 횡단 고정 목록이 아니라 **`DomainTaxonomyAgent`가 도메인 특성에 맞춰 자동 생성**합니다. 즉 코드북은 도메인의 통제 가능한 출력물이며, 도메인이 추가될 때마다 사람이 손으로 코드북을 작성할 필요가 없습니다.

- **생성 시점**: `domain_modeling_node` 실행 시 `purpose_config["reaction_insight"]`의 하위 필드 `aspect_codebook`로 출력합니다(스키마 변경은 `pipeline_topology_redesign.md` §6-3에서 추적).
- **생성 입력**: `domain_name`, `own_product_summary`, `target_user`, `core_value_props`, 그리고 (가능한 경우) competition_axes에서 도출된 도메인 특성.
- **생성 출력 형식**: `[{aspect_id, label, definition, domain_specific: bool}]` 형태. `domain_specific`이 true인 항목은 해당 도메인에서만 의미를 가집니다(예: 트래블카드의 `social_features`).
- **재사용 정책**: 동일 도메인 재실행 시 캐시된 codebook을 재사용합니다(TTL 7일). 새 경쟁사 추가로 인해 누락된 aspect가 의심되면 enrichment 2차 호출로 코드북에 추가(add-only)합니다.
- **사람 검토**: Feature Selection interrupt #4에서 사용자가 aspect를 추가·제거·재명명 가능. 사용자 결정이 우선합니다.

도메인 횡단 baseline aspect 6종(`exchange_convenience`, `fee_perception`, `atm_availability`, `app_ux_quality`, `customer_support`, `additional_benefit`)은 시연 목적의 예시이며, DomainTaxonomyAgent가 다른 도메인에서는 다른 aspect 집합을 생성합니다. 예컨대 SaaS 도메인이라면 `onboarding_friction`, `integration_complexity`, `pricing_clarity` 같은 aspect가 생성될 것입니다.

참조: [Springer Nature — Systematic Review of ABSA (2024)](https://link.springer.com/article/10.1007/s10462-024-10906-z) (도메인 적응의 중요성), [카드고릴라 — 트래블GO vs 트래블로그 비교](https://m.card-gorilla.com/contents/detail/3432) (도메인 특유 aspect의 발견 예시)

### 2-3. 카드별 사용자 인식 차이 (반복되는 aspect)

매체들이 공통적으로 인용하는 사용자 반응 aspect는 다음 7가지입니다. 이는 ABSA codebook 초안으로 사용 가능합니다.

1. `exchange_convenience` — 환전·충전의 편의성 ("앱으로 5초 만에 환전")
2. `fee_perception` — 수수료 체감 ("진짜 0원이 맞나?")
3. `atm_availability` — 현지 ATM 호환성 ("일본 세븐일레븐에서 됐다/안 됐다")
4. `app_ux_quality` — 앱 사용성 ("토스 앱이 가장 직관적")
5. `customer_support` — 분실·문의 대응 ("주말에 카드 분실하니 막막")
6. `additional_benefit` — 라운지·적립 등 부가 혜택 ("SOL 라운지 들어가니 새로움")
7. `social_features` — 소셜 기능 ("트래블월렛 모임 기능이 의외로 좋음")

`social_features`는 트래블월렛의 차별점으로 자주 언급되며, 다른 카드 비교에는 등장하지 않습니다. 카드별 차별점 aspect를 사전에 식별해 두면 분석 깊이가 높아집니다.

참조: [헤어트래블 — 트래블월렛 사용자 후기 종합](https://info.heretravel.co.kr/board-post/2201), [홀라플라이 — 트래블로그·트래블월렛 비교 2026](https://esim.holafly.com/ko/travel-tips/travel-log-travel-wallet-comparison/)

### 2-4. 도메인 특유의 분석 함정

- **출시 시기 편향**: 토스 트래블카드는 비교적 늦은 출시로 댓글 누적량이 적습니다. "댓글 수가 적음 ≠ 만족도가 낮음"이며 단순 댓글 수 비교는 오해를 부릅니다.
- **국가·여행지 편향**: 일본·동남아 여행 댓글이 압도적으로 많고, 유럽·미주 여행 댓글은 소수입니다. aspect 분석 결과를 여행지별로 분리하지 않으면 일본 사례가 전체 사례로 일반화됩니다.
- **시점별 변동**: 환율·프로모션 시기에 따라 반응이 급변합니다. 댓글 작성일을 보존해 `retrieved_at`과 별도로 `comment_posted_at` 필드를 두어야 합니다.
- **계절성**: 여름 휴가철·연말 여행 시즌에 댓글이 집중됩니다. 동일 표본을 비교 전제로 두려면 수집 기간을 정렬해야 합니다.

---

## 3. 종합 — 고객 반응 인사이트 권장안 요약

방법론과 도메인 레퍼런스를 종합한 권장 양식은 다음과 같습니다(본 문서는 권장안 요약까지만 다루며, 실제 스키마·노드 구현은 별도 작업).

- **포함 요소**: ① ABSA aspect codebook(DomainTaxonomyAgent 자동 생성, §2-3) + 사용자 검토 단계에서 조정, ② `(aspect, polarity, intensity, quote, source_url, channel, posted_at)` 7-tuple 저장 — channel 필드는 §2-2의 3개 채널 중 하나, ③ aspect × channel 교차 sentiment 매트릭스 + 단일 통합 점수 동시 보고, ④ NPS-proxy(positive 비율)와 비교 매트릭스 점수의 상관 분석, ⑤ 여행지·시점별 분리 뷰, ⑥ suggestion 카테고리 별도 분리(product_dev 액션 후보).
- **표기 규약**: polarity는 5단계(`strong_negative`, `negative`, `neutral`, `positive`, `strong_positive`), intensity는 0.0–1.0 실수. quote는 원문 그대로 보존(번역·요약 금지). channel은 `youtube`, `community`, `app_store` 중 하나.
- **양식**: aspect × 채널 cross-tab + 대표 quote 5–10개(각 채널 1–2개씩) + suggestion 분리 리스트의 3단 구성. battlecard·executive_summary 리포트와 cross-link.

---

## 4. 결정된 사항 (사용자 확정)

다음 항목은 본 reference 문서의 초안 작성 후 사용자 결정이 완료되었습니다.

- **결정 1 — ABSA codebook 생성 방식**: 도메인별 자동 생성(`DomainTaxonomyAgent`가 `purpose_config["reaction_insight"].aspect_codebook` 필드로 출력, §2-3 참조). 도메인 횡단 고정 코드북 방식은 채택하지 않습니다.
- **결정 2 — 데이터 소스 채널**: YouTube 영상 요약 + 댓글, 커뮤니티(클리앙·디시), 앱스토어 리뷰(도메인 수집 가능 시) 3개 채널을 표준 채택합니다(§2-2 참조). 블로그·신문 기사는 cross-validation 보조용으로만 활용합니다.

---

## 5. 사용자가 추가로 검토할 만한 꼬리 질문

1. **suggestion 카테고리 댓글**을 별도 `product_dev_suggestions` 리스트로 분리해 product_dev lens action으로 직접 연결할지, ABSA aspect의 한 polarity로 통합 처리할지 결정이 필요합니다. 전자는 액션 직결성이 높고, 후자는 데이터 모델이 단순합니다.
2. 만족도 정량 비교(매체 보고 점수, §2-1)와 ABSA 산출 NPS-proxy가 충돌하는 경우 어느 쪽을 신뢰하시겠습니까? 매체 점수는 표집 신뢰도가 보고되지 않는 경우가 많고, ABSA는 자연 발생 텍스트라 표본 편향이 다르게 작용합니다.
3. 채널별 가중치 안(YouTube 1.0, 커뮤니티 1.2, 앱스토어 0.8)을 그대로 채택하시겠습니까, 아니면 도메인·자사 상품 특성에 따라 `DomainTaxonomyAgent`가 가중치도 자동 산정하게 하시겠습니까? 후자는 적응성이 높으나 가중치 변동성으로 리포트 비교성이 일부 떨어집니다.
