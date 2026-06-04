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

## 3. v0.10.28b — `marketing_social` 카드 UI 부분 채택 사양 (turn-62 사용자 명세)

| 항목 | 값 |
|---|---|
| 최초 기록 일자 | 2026-06-04 |
| 관련 PR | v0.10.28b (사양 사전 보관, 구현 후속 진행) |
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

## 변경 이력

| 일자 | 변경 |
|---|---|
| 2026-06-03 | 1번 항목 (`features` 객체 승격 옵션 B) 신설 |
| 2026-06-04 | 2번 항목 (`ClaudeApiAnalyzer` 활용 후보 노드 검토) 신설 — v0.10.21.1 turn-49 결정에 따른 보존 자산 활용 정책 |
| 2026-06-04 | 3번 항목 (v0.10.28b `marketing_social` 카드 UI 부분 채택 사양) 신설 — turn-62 사용자 이미지 + 명세 |
