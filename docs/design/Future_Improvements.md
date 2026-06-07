# Future Improvements

본 문서는 현재 시점에는 채택하지 않았으나 향후 특정 조건 발생 시 재검토할 개선 항목을 기록합니다. 각 항목은 **재검토 트리거** 와 **검토 시점** 을 명시합니다.

---

## 1. `domain_modeling` 의 `features` 객체 승격 (옵션 B)

| 항목 | 값 |
|---|---|
| 최초 기록 일자 | 2026-06-03 |
| 관련 PR | v0.10.19.1 (대안 검토) |
| 현재 채택안 | `search_query_hints.items` 의 `oneOf: [string, object]` (옵션 A 후방 호환) |
| 대안 (보류) | `features` 를 객체로 승격하여 `feature_id`·`label`·`source_hint` 메타를 1차 시민으로 격상 |

### 재검토 트리거

- (T1) URL 탐색 정확도 (`coverage` 지표) 가 v0.10.20 ~ v0.10.27 시리즈 완료 후에도 지속적으로 < 70% 유지
- (T2) `feature_selection` UI 에서 사용자가 URL coverage 결손 feature 를 수동 보강하는 빈도가 30% 초과
- (T3) `cross_reference_node` 의 false negative (자사 영상 잘못 차단) 비율 > 10%
- (T4) 신규 source-type 추가 시 (예: `forum_qa`·`podcast`) `search_query_hints` 객체 양식이 5종 메타 차원으로 확장되는 경우

### 검토 시점

본 시리즈 (v0.10.18 ~ v0.10.27) 완료 후 v0.11 진입 직전 1회 통합 평가.

---

## 2. `ClaudeApiAnalyzer` 활용 후보 노드 검토

| 항목 | 값 |
|---|---|
| 최초 기록 일자 | 2026-06-04 |
| 관련 PR | v0.10.21.1 (turn-49) |
| 현재 채택 패턴 | `ProductIdResolver` 만 `ClaudeApiAnalyzer(temperature=0)` 사용. 나머지 전 노드는 `ClaudeCodeCliAnalyzer` |
| 보존 자산 | `server/llm/claude_api_analyzer.py` (180줄, 본 시리즈 사용 안 함) |

### 재검토 트리거

- (T1) 미래 신규 노드가 **완전 결정론** 요건을 가지는 경우 — 예: state 키 prefix 가 되는 ID 생성 (`comp_*`·`feat_*`·`func_*` 슬러그) · cache_key 의 핵심 구성 요소
- (T2) `ClaudeCodeCliAnalyzer` 의 비결정성이 production 모니터링에서 동일 입력의 출력 변동 > 5% 로 측정되는 경우
- (T3) 최종 `InsightReportAgent` 도입 시 결정론적 출력 요구 (사용자 재실행 시 동일 리포트 보장)
- (T4) Anthropic 의 Claude Code CLI 가 `--temperature` 공식 지원 (관련 이슈: https://github.com/anthropics/claude-code/issues/6096) 시 본 검토 항목 자동 해소

### 검토 시점

- 신규 노드 도입 시 PR 진입 전 1회 (트리거 T1)
- v1.0 진입 전 production 모니터링 데이터 기반 1회 통합 평가 (트리거 T2·T3)
- CLI temperature 지원 announce 즉시 (트리거 T4)

### 현재 비-사용 결정 근거 (turn-49)

`url_discovery_owned_channels_node` 의 LLM 검증을 ClaudeCodeCliAnalyzer 로 전환한 결정의 근거:

- 본 노드의 LLM 검증은 **명확한 시그널 기반** (URL 의 `official` 접미사·snippet 의 "공식"·"공식 채널" 키워드·도메인 일치) → CLI 의 자연어 수준 결정론으로 동일 출력 기대
- confidence 미세 변동 (예: 0.85 ↔ 0.87) 은 임계 0.7 판정에 영향 없음
- 비용 절감 (API 약 $0.50/분석 → $0, Claude Pro/Max 구독 토큰)
- 시리즈 전체 (query_intake·competitor_discovery·domain_modeling·feature_url_mapper 등) 가 CLI 사용한 일관 패턴 유지

`ProductIdResolver` 와의 차이:

| 항목 | `ProductIdResolver` (API 필수) | `url_discovery_owned_channels` (CLI 채택) |
|---|---|---|
| 작업 | `comp_*` slug 결정 | 5개 후보 URL 중 공식 판정 |
| 결정론 필수 이유 | slug 가 cache_key + 후속 모든 state 키 prefix → 단 1bit 변동도 회귀 | confidence 0.85 vs 0.87 같은 미세 변동은 임계 0.7 결정에 무관 |
| 판정 시그널 명확도 | "트래블월렛 카드" → `comp_트래블월렛` (slug 규칙 모호) | URL 의 `_official` 접미사·snippet 의 "공식" 키워드 (매우 명확) |

---

---

## 3. v0.10.28b — `marketing_social` 카드 UI 부분 채택 사양 (turn-62 사용자 명세) ✅ 구현 완료 (turn-64)

| 항목 | 값 |
|---|---|
| 최초 기록 일자 | 2026-06-04 |
| 관련 PR | **v0.10.28b 완료 (turn-64)** — D45 (a) 채택. `_carry_owned_channels` + `_build_owned_channels_card` + `OwnedChannelCard.jsx` 신설 |
| 트리거 | v0.10.27.1 hotfix + 분석 재실행 후 `marketing_social` 카드 UI 확인 결과 |
| 사용자 의도 | owned_channels 의 책임 (채널 URL 식별) 과 marketing_social 의 feature (게시물 빈도·콘텐츠 키워드 등 정량 분석) 의 단계 분리 시각화 |

### 부분 채택 사양 (사용자 이미지 + turn-62 메시지)

**A. 타이틀 하단 안내 카드 문구 개선**

```
이 리포트는 자사·경쟁사 운영 SNS·블로그·보도자료의 공식 채널 URL 을 식별한 뒤,
아래 채널 활성도·게시물 빈도·콘텐츠 키워드·광고 정보 등의 feature 값을 수집·
분석하여 작성합니다. (※ feature 값 수집은 v1.0 §6-6a 도입 후 자동 진행)
```

옛 문구 (v0.10.21.1 turn-49):
> "이 리포트는 자사·경쟁사 운영 SNS(Instagram·X·YouTube 공식 채널)·블로그·보도자료의 URL 을 발견한 뒤, 채널 활성도·게시물 빈도·콘텐츠 키워드·광고 정보 등의 feature 값을 수집·분석하여 작성합니다. (※ feature 값 수집은 v1.0 §6-6a 도입 후 자동 진행)"

→ "공식 채널 URL 을 식별한 뒤, 아래 ... feature 값" 표현으로 단계 분리 명확화.

**B. 안내 카드 아래 별도 "공식 채널" 카드 신설** (사용자 이미지)

```
┌─ 공식 채널                                            URL 상세보기
│   candidate별 own_channel별 URL List-up
│
│   own_트래블월렛 (자사)
│     • Instagram        @travelwallet.official      [Brave + LLM 검증]
│     • X                @travelwallet               [Brave + LLM 검증]
│     • 네이버 블로그     blog.naver.com/travelwallet
│     • YouTube 공식 채널 @TravelWallet (UC_xxx, 구독자 12,000)
│     • 보도자료          travelwallet.com/news/
│
│   comp_토스뱅크 (경쟁사)
│     • Instagram        @tossbank
│     • YouTube 공식 채널 @TossBank (UC_yyy, 구독자 87,000)
│     • 보도자료          tossbank.com/press
│     ❌ 네이버 블로그      미발견
└
```

- candidate 별 grouped 카드
- platform 별 row (Instagram·X·blog_naver·blog_tistory·press_release·youtube_official)
- `account_scope` (`parent_company`·`sub_brand`·`product_specific`·`regional`) 라벨 chip
- YouTube 공식 채널은 `channel_id` + `subscriber_count` 메타 보강 표시

**C. feature 카드들은 B-only 리포트 형식으로 변경**

`positioning_map`·`executive_summary` 와 동일한 시각적 처리:
- 체크박스 비활성 + 회색 chip 표시
- `URL 커버리지` 영역 숨김 (`url_coverage_visible=false`)
- `▼ URL 상세 보기` 버튼 회색 + 클릭 비활성
- 또는 클릭 시 "URL 수집은 v1.0 §6-6a 도입 후 진행 예정" 안내 표시

### 구현 영향 분석

| 영역 | 변경 | 라인 |
|---|---|--:|
| `feature_selection_node._build_reports_payload` | `marketing_social` 의 `url_coverage_visible=false` 처리 + `owned_channel_card` 별도 payload 항목 산출 | +30 |
| `feature_selection_node._REPORT_INTRO_TEXTS["marketing_social"]` | 안내 문구 사용자 사양으로 갱신 | +5/-5 |
| `feature_selection_node._build_feature_items_from_analysis` | marketing_social feature 의 `coverage_summary`·`coverage_details` 산출 생략 | +10 |
| `FeatureSelectionPage.jsx` | `marketing_social` 카드 분기 — IntroBox 아래 `OwnedChannelCard` 컴포넌트 렌더링 + feature 카드를 B-only 시각으로 처리 | +80 |
| `OwnedChannelCard.jsx` (신설) | candidate × platform 매트릭스 렌더링 컴포넌트 | +100 |
| 합계 | | 약 +225 |

### feature_mapping_owned_channels_node LLM 호출 정책 변경 (D45 후속)

본 사양 채택 시 `feature_mapping_owned_channels_node` 의 책임 재정의 필요:
- LLM 호출 생략 (feature × candidate × URL 매핑 무의미)
- `owned_channel_urls_by_candidate` 를 `owned_channel_raw_features` 로 carry-through (mock raw_features)
- 또는 별도 state 키 `owned_channels_summary` (candidate × platform list) 산출

이 부분은 v0.10.28b 진입 시 별도 결정 (D45) 으로 확정.

### 검토 시점

분석 재실행 후 v0.10.27.1 hotfix 의 youtube_reactions URL 통합 효과 확인 직후. 사용자 결정 (D45 정확한 옵션) 후 본격 진입.

---

## 4. `normalize_competitor_ids`(ProductIdResolver) 상품명 정규화 오염 — 브랜드=상품 candidate의 own 명칭 흡수

| 항목 | 값 |
|---|---|
| 최초 기록 일자 | 2026-06-04 |
| 발견 경위 | official_content_collection Step 0 실데이터 검증 (`scripts/verify_step0_with_cache.py`) — `comp_토스트래블카드`의 official_domain이 `travel-wallet.com`으로 확인 |
| 증상 | 경쟁사 "트래블월렛"이 own 상품명 "토스 트래블카드"로 정규화되어 `comp_토스트래블카드` 슬러그 생성. own 행과 동명의 경쟁사 행이 매트릭스에 공존 |
| 원인 (확정) | `competitor_discovery`는 `brand='트래블월렛', product_name='트래블월렛 카드'`로 정상 산출(2026-05-13 캐시). 그 직후 `normalize_competitor_ids`의 ProductIdResolver가 `data/cache/product_name_normalization.json`에 **"트래블월렛카드" → "토스 트래블카드"** 오매핑을 기록 — 브랜드=상품(별도 상품명 부재)인 candidate를 도메인 대표 상품명(own)으로 붕괴시킴. `official_source_resolver`는 오염된 입력의 하류 피해자(brand 필드는 '트래블월렛'으로 보존된 채 travel-wallet.com을 정상 해소) |
| 임시 조치 (2026-06-04 완료) | 캐시 101건 `comp_토스트래블카드` → `comp_트래블월렛` 일괄 치환 + `official_sources.json` product_name 교정 + normalization 캐시 오매핑 교정("트래블월렛카드" → "트래블월렛 카드", 재오염 방지). 백업: `backup/cache_rename_20260604/` |

### 근본 해결 방향 (재검토 시 적용)

- ProductIdResolver system_prompt에 "브랜드=상품(단독 상품명 부재) candidate는 브랜드명을 정규 상품명으로 유지하고, **own_product 명칭으로 정규화 금지**" 제약 추가.
- 정규화 결과가 own_product 명칭 또는 기존 comp_* 정규명과 충돌하면 reject + 원본 명칭 유지하는 결정론적 후처리 가드.
- 회귀 테스트: "트래블월렛 카드" 입력이 "토스 트래블카드"로 정규화되지 않는지 fixture 고정.

### 부록 — 동일 노드의 추가 데이터 품질 이슈 (2026-06-04, Step 2 실측 중 발견)

- **트래블월렛 primary_url 영문판 해소**: `official_sources.json`의 `comp_트래블월렛` `primary_url`이
  한글판이 아닌 영문 랜딩(`https://www.travel-wallet.com/en`)으로 해소되어 있다. 영문 마케팅
  카피 기반 추출은 한글 약관 대비 정보 밀도가 낮아 explicit 비율 하락의 한 원인
  (Step 2 실측: 트래블월렛 explicit 1/8 vs own_토스 6/8). 근본 해결 시
  official_source_resolver에 "동일 도메인 내 한국어 버전 우선" 규칙 추가 검토.

### 재검토 트리거

- (T1) 신규 도메인 파일럿에서 브랜드=상품형 candidate(예: Wise·Revolut) 추가 시
- (T2) normalization 캐시에서 동일 정규명으로 수렴하는 서로 다른 브랜드 2건 이상 발견 시

### 검토 시점

report generation 시리즈(official_content_collection → comparison_matrix) 완료 후, youtube 계열 수집 노드 착수 전.

---

## 5. `official_source_resolver` 의 인메모리 Brave 캐시 → 파일 캐시 전환

### 배경 (2026-06-07, Brave 월 크레딧 소진 사고 후속)

Brave Search API 가 2026-02 무료 tier 를 폐지(월 $5 크레딧 ≈ 1,000쿼리)하면서 모든
Brave 호출이 과금 자원이 됐다. v0.13.5 에서 `_brave_search` 파일 캐시
(`url_discovery_brave`)의 TTL 을 24h → 168h(7일, config E-1b)로 연장해 1차 fan-out
5개 노드의 반복 소모를 ~1/7 로 줄였다.

그러나 `official_source_resolver_node` 는 별도 경로인 `server/graph/url_cache.py` 의
**인메모리** `_TTLCache`(`BRAVE_RESULT_CACHE_TTL_HOURS`, E-1)를 사용한다. 프로세스
수명에 묶여 있어 **서버(uvicorn) 재시작 때마다 소멸** — 재시작 후 첫 실행은 항상
실제 Brave 호출이 발생한다. 개발 중 서버 재시작이 잦아 누수가 누적된다.

### 개선안

- `official_source_resolver` 의 Brave 조회를 `_brave_search` 와 동일한
  `agent_cache`(파일) 경로로 통합하거나, `url_cache.py` 에 파일 영속 백엔드를 추가.
- 통합 시 전역 rate limiter(`_brave_throttle`)·429 재시도도 자동 공유되는 부수 이점.

### 검토 시점

marketing_social 시리즈 완료 후. 작업량 작음(헬퍼 치환 1곳 + 캐시 키 정합 확인).

---

## 6. owned_channels — 모회사·계열 브랜드 채널 탐지 확장 (하나은행 케이스)

### 배경 (2026-06-07 실사)

candidate `comp_하나트래블로그카드` 의 brand 는 "하나카드"이지만, 실존 운영 채널 중
X(`x.com/HanaBank_KR`)·자체 블로그(`blog.hanabank.com`)는 **하나은행(계열 모회사)**
명의다. v0.13.4 브랜드 site: 보조 쿼리(`site:x.com 하나카드`)는 site: 연산자가 정상
작동함에도(무관 계정 5건 반환) 운영 주체 브랜드가 달라 도달하지 못했다 — 검색어
자체가 못 미치는 구조적 한계.

### 개선안 (우선순위순)

1. **수동 채널 등록 (HITL)**: feature_selection 공식 채널 카드에 "채널 직접 추가"
   입력 → 도메인 가드 검증 후 `owned_channel_urls_by_candidate` 병합. 검색 개선으로
   풀 수 없는 케이스의 확실한 해결책이며 interrupt 패턴과 정합.
2. **parent_brand 메타 도입**: competitor_discovery 가 모회사/계열 브랜드를
   candidate 메타로 제공하면 `site:x.com {parent_brand}` 보조 쿼리 확장 가능.
   상위 노드 스키마 변경 필요 — 단독 진행 불가.
3. **도메인 기반 추정 프로브** (트래블월렛 네이버 블로그 케이스와 공용):
   primary_url 도메인 slug(예: travel-wallet.com → travelwallet)로
   `blog.naver.com/{slug}` 등 패턴 URL 을 직접 HTTP 검증. Brave 비용 0,
   단 오탐 방지 검증 로직 필요.

### 참고 — Brave vs Google 랭킹 격차 (2026-06-07 확정)

`site:blog.naver.com 트래블월렛 공식`: Google 은 공식 블로그
(`blog.naver.com/travelwallet`)를 1위로 반환하나, Brave 상위 5건은 전부 개인 리뷰
글이며 공식 블로그 홈이 부재. Brave 인덱스/랭킹 품질 한계로, count 상향(5→10)
시도 또는 3번 프로브 방식이 대안.

### 검토 시점

marketing_social 시리즈 완료 후 수동 등록(1번)부터.

---

## 변경 이력

| 일자 | 변경 |
|---|---|
| 2026-06-03 | 1번 항목 (`features` 객체 승격 옵션 B) 신설 |
| 2026-06-04 | 2번 항목 (`ClaudeApiAnalyzer` 활용 후보 노드 검토) 신설 — v0.10.21.1 turn-49 결정에 따른 보존 자산 활용 정책 |
| 2026-06-04 | 3번 항목 (v0.10.28b `marketing_social` 카드 UI 부분 채택 사양) 신설 — turn-62 사용자 이미지 + 명세 |
| 2026-06-04 | 4번 항목 (ProductIdResolver 상품명 정규화 오염) 신설 — Step 0 실데이터 검증 중 발견, 임시 조치 완료 |
| 2026-06-07 | 5번 항목 (인메모리 Brave 캐시 파일 전환) 신설 — Brave 월 크레딧 소진 사고 후속 |
| 2026-06-07 | 6번 항목 (모회사·계열 브랜드 채널 탐지 확장) 신설 — owned_channels 실사 미발견 3건 원인 분석 후속 |
