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

## 변경 이력

| 일자 | 변경 |
|---|---|
| 2026-06-03 | 1번 항목 (`features` 객체 승격 옵션 B) 신설 |
| 2026-06-04 | 2번 항목 (`ClaudeApiAnalyzer` 활용 후보 노드 검토) 신설 — v0.10.21.1 turn-49 결정에 따른 보존 자산 활용 정책 |
