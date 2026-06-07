# reaction_insight 노드 설계 — YouTube·커뮤니티 반응 수집 → ABSA → 고객 반응 인사이트

> - **상태**: DRAFT — 사용자 검토 후 확정 예정
> - **작성일**: 2026-06-06
> - **시리즈**: report generation 시리즈 3단계 (reaction 계열 수집 2종 → reaction_analysis → reaction_insight)
> - **선행 문서**:
>   - `docs/design/comparison_matrix_node_design.md` (CM-D1 코드/LLM 분리 · CM-D6 코드 채점 — 본 시리즈가 패턴 계승)
>   - `docs/design/feature_extraction_node_design.md` §2 (수집 노드 공통 패턴 — analysis_features origin 필터)
>   - `docs/reference/report_taxonomy.md` §2-2 (ABSA 7-tuple · 평가 루브릭) · `docs/reference/reaction_insight.md`
>   - `docs/design/pipeline_topology_redesign.md` §6-6a (community_collection · D11 채널 2종 확정)
> - **대상 파일**: `server/graph/nodes/youtube_reaction_collection_node.py` (신규),
>   `server/graph/nodes/community_collection_node.py` (신규),
>   `server/graph/nodes/reaction_analysis_node.py` (신규),
>   `server/graph/nodes/reaction_insight_node.py` (스켈레톤 → 구현),
>   `agents/reaction_analysis/*` · `agents/reaction_insight/*`, `server/llm/youtube_client.py` (commentThreads 추가),
>   `server/graph/graph.py`, `client/src/components/ReactionInsightReport.jsx`

---

## 1. 문서 목적과 범위

`analysis_features`의 youtube_reactions·blog_community 계열 산출로부터 **사용자 반응
원자료(댓글·게시글)를 수집**하고, `aspect_codebook` 기반 **ABSA(Aspect-Based Sentiment
Analysis)** 를 거쳐 `report_outputs["reaction_insight"]`를 생성하는 4개 노드를 설계합니다.

전제 사실 (2026-06-06 코드 실사): feature_url_mapper 단계는 YouTube **영상 링크 +
메타데이터**(video_id·조회수·좋아요·댓글수·게시일)의 수집·검증·feature 매핑까지 완료했고,
**댓글 본문은 미수집**(`commentThreads` 호출 없음)입니다. 분석 원자료 수집이 본 시리즈의
1차 책임입니다.

---

## 2. 토폴로지 — youtube_query_planner 폐기 (RI-D1)

### 2-1. 구설계 대비 변경

구설계(§6-7)의 `youtube_query_planner → youtube_collection`(검색어 설계 → 검색+수집)은
폐기합니다. 그 책임이 현행 토폴로지에서 이미 흡수되었기 때문입니다:

| 구설계 책임 | 현행 수행 주체 |
|---|---|
| YouTube 검색어 설계 | `domain_modeling`의 `search_query_hints` (source_hint=youtube_reactions) |
| 검색 실행·영상 선별 | `url_discovery_youtube_reactions` (Data API search) + `cross_reference` + `feature_mapping` + `additional_urls_validation` |
| 사용자 선택 반영 | `feature_selection` → `analysis_features` |

수집 노드는 검색하지 않고 **확정된 video_id 의 댓글만 수집**합니다 (수집 노드 공통 패턴).
부수 정리: `state.py`의 `query_plan` 키 폐기 대상.

### 2-2. TO-BE 엣지

```
feature_selection (#4)
  ├─→ official_content_collection ─→ comparison_matrix ──────────┐ (기존, 병렬)
  ├─→ youtube_reaction_collection ┐                              │
  └─→ community_collection        ┴─(list-fan-in)→ reaction_analysis
                                                       ↓          │
                                                 reaction_insight ┤
                                                                  ↓
                                                                 END (임시)
```

- comparison_matrix 와 **병렬** — merge reducer(CM-D3)·점진 탭(v0.12.4)이 이번 시리즈에서
  실효 발휘: 비교 매트릭스 탭이 먼저 활성화되고 반응 인사이트 탭이 나중에 켜진다.
- battlecard 의 A-Only 3종 대기(list-fan-in)는 marketing_social 구현 후 후속 시리즈.
- **graph 노드명 주의 (배선 시 발견, 2026-06-06)**: LangGraph 는 노드명이 state 키와
  동일하면 거부한다. `reaction_analysis` 는 state 키(ABSA 산출)로 선점되어 있어
  **노드명은 `reaction_absa`** 로 등록한다 (파일·함수명은 reaction_analysis_node 유지).
  구설계 §6-7 의 "노드명 = state 키 동명" 충돌이 해소된 것.

---

## 3. `youtube_reaction_collection` 노드 (RI-D2 — 명칭·수집 범위)

### 3-1. 수집 범위 결정 (RI-D2·RI-D3)

- **수집**: 영상별 댓글(commentThreads) + 영상 제목·설명(videos.list snippet) —
  설명은 리뷰 요지·타임스탬프 등 댓글 해석의 맥락 제공. 모두 공식 Data API.
- **자막 보류 (RI-D3)**: 제3자 영상 자막은 공식 API 로 다운로드 불가(captions.download
  는 소유자 OAuth 필요). 비공식 라이브러리는 스크래핑이라 차단·ToS 리스크 → D11 합법성
  원칙과 충돌. 또한 자막은 리뷰어 1인의 장문 의견이라 다수 사용자 댓글과 동급 집계 시
  왜곡 — 도입 시 화자 구분 설계 필요. 재검토 트리거: 댓글만으로 aspect 커버리지 미달.
- 명칭: 수집물이 댓글+영상 메타이므로 `youtube_comment_collection` 이 아닌
  **`youtube_reaction_collection`** (자막 확장에도 유지 가능).

### 3-2. 입력 계약 (수집 노드 공통 패턴)

```
analysis_features
  → [1] report_type == "reaction_insight" AND reaction_insight ∈ selected_purposes
  → [2] feature_id ∈ selected_feature_ids
  → [3] candidate_coverage 의 existing_urls 중 origin == "youtube_reactions"
        (video_id·view_count·like_count 메타 보유 — url_discovery 산출)
```

### 3-3. 영상·댓글 선별 (RI-D4 — 사용자 확정 방침: 조회수·좋아요 상위 N)

| 항목 | 규칙 | 근거 |
|---|---|---|
| 영상 선별 | **feature당 조회수 상위 2개** 채택 후 candidate 단위 union(dedup), **candidate당 상한 6개** | FE-D5 v3 패턴 — feature 커버리지 우선 + 총량 통제 |
| 댓글 수집 | 영상당 commentThreads 1page(100건, order=relevance) 수집 → 필터(10자 미만·이모지 전용·중복 제외) → **좋아요 상위 30건** 채택 | 좋아요 = 다수 공감 신호. 직접 정렬 미지원이라 수집 후 코드 정렬 |
| 총량 | candidate당 댓글 최대 150건 — 영상 6 × 30 = 최대 180건에서 초과분은 전체 좋아요 하위부터 절단(영상당 최소 15건 보장) ≈ CLI 입력 10~15k자 | reaction_analysis candidate 단위 1회 호출 적정 규모 |

**quota 예산**: 영상당 commentThreads 1 unit + videos.list(snippet) 1 unit ≈
candidate 4 × 6영상 × 2 ≈ **48 units/실행** (일일 한도 10,000 의 0.5%). 기존
`YouTubeQuotaExceeded` graceful 패턴 재사용. 캐시: agent_cache 24h TTL, 키 = video_id.

### 3-4. 출력 (기존 state 키 재사용)

- `collected_videos`: [{video_id, url, candidate_id, feature_ids, title, description,
  view_count, like_count, comment_count, published_at}]
- `selected_comments`: [{video_id, candidate_id, comment_id, text(원문 보존),
  like_count, published_at, author_hash(작성자 식별정보 비저장 — D11)}]
- 부분 실패 허용(§7 사상): 댓글 비활성 영상·quota 초과 시 해당 영상 skip + errors 누적.

---

## 4. `community_collection` 노드

- **입력**: analysis_features 중 origin == "blog_community" (domain_class 메타 보유).
- **수집**: 기존 `_fetch_content`(Trafilatura·24h 캐시) 재사용 — 게시글 본문 + (정적
  렌더 가능한 경우) 댓글 텍스트. URL당 본문 상한은 FE-D5 기존 상수 재사용.
- **정책 (D11)**: robots.txt 준수·요청 간 rate limit(1초)·작성자 식별정보 비저장.
  SPA·차단 URL 은 `requires_dynamic_render`/`fetch_failed`로 skip (Playwright v0.11 백로그).
- **출력**: `community_posts`: [{url, candidate_id, feature_ids, domain_class,
  title, body_excerpt, published_at}]
- **선행 실측 권장 (RI-D8)**: 커뮤니티는 정적 fetch 차단이 흔함 — 구현 직후 실데이터
  URL 통과율을 profile 스크립트로 측정해 채널 가중치 재산정(§6)에 반영.

---

## 5. `reaction_analysis` 노드 — ABSA (RI-D5 어댑터 = CLI, 사용자 확정)

CM-D1 분리 적용:

- **코드**: 채널 2종 통합·중복 제거·candidate 연관·입력 조립(댓글 150 + 게시글 발췌)
  · LLM 출력의 source_url/channel 검증 가드(입력에 없는 출처 제거).
- **LLM** (`ClaudeCodeCliAnalyzer`, candidate당 1회 — 댓글 수백 건 입력은 구독 경로가
  비용 적합. schema 안정성은 어댑터 재시도 schema 재주입 수정(v0.12.3)으로 보강됨):
  `aspect_codebook`(taxonomy) 기반 **7-tuple** 추출 —
  `(aspect, polarity, intensity, quote, source_url, channel, posted_at)`.
  **quote 는 원문 보존(번역·요약 금지)**, 입력에 실존하는 문장만 (evidence 강제 패턴).
  suggestion 성 발언은 `is_suggestion` 플래그로 분리 (5점 요건 대비).
- **출력**: `reaction_analysis` (기존 state 키): {candidate_id: {"tuples": [...],
  "channel_counts": {youtube, community}, "sample_size", "collected_at"}} — AP-3
  (표본 크기·시점 의무) 충족.
- 캐시: 키 = candidate + 입력 텍스트 해시 + 컨텍스트 해시.

## 6. `reaction_insight` 리포트 노드

- **코드**: aspect × polarity 집계 매트릭스, 채널별 가중치 적용(2채널 기준 재산정 —
  기존 YouTube 1.0/커뮤니티 1.2 는 3채널 가정이라 §6-6a 지적대로 재정의 필요, RI-D7),
  대표 quote 선정(채널·극성별 상위), suggestion 목록 분리, **루브릭 코드 채점(RI-D6,
  CM-D6 패턴)**: 3점 = 7-tuple 단일 채널 / 4점 = 2채널 모두 sample_size > 0 /
  5점 = 4점 + 가중치 적용 + posted_at 시점 분리 뷰 + suggestion 분리 제공.
- **LLM** (CLI, 1회): aspect 별 인사이트 서술·페르소나 함의 — 집계 결과를 읽기 전용
  입력으로 받아 서술만 (수치 재생성 금지).
- **envelope**: `build_report_envelope` — content = {aspect_matrix, weighted_summary,
  top_quotes, suggestions, timeline_view, channel_meta}. `report_outputs["reaction_insight"]`
  에 자기 키만 반환 (merge reducer).

## 7. UI — `ReactionInsightReport.jsx`

GenericReportView(임시 JSON 뷰)를 전용 컴포넌트로 교체: aspect(행) × polarity(열)
히트맵 + 셀 클릭 시 해당 quote 카드(원문·채널 배지·게시일·링크) 전개 + suggestion
섹션 + 채널·표본 메타(AP-3) 표시. 탭은 v0.12.4 점진 활성화에 자동 편입.

## 8. 검증 계획 (목표 주도형)

1. 단위 — 영상·댓글 선별: feature당 2·candidate당 6·좋아요 상위 30·필터 규칙 (fixture).
2. 단위 — quota graceful: `YouTubeQuotaExceeded` 시 부분 결과 + 정상 진행.
3. 단위 — ABSA 가드: 입력에 없는 source_url/quote 제거, 7-tuple schema 검증.
4. 단위 — 루브릭 채점: 채널 1/2종·가중치·suggestion 유무별 3/4/5점 경계.
5. 통합 — reducer·점진 탭: comparison_matrix 와 병렬 실행 시 두 리포트 키 병합 +
   탭 순차 활성화 (graph 컴파일 + 엣지 검증).
6. 실측 — quota·비용·시간 baseline, 커뮤니티 fetch 통과율(RI-D8), 7-tuple 표본 20건 검토.

## 9. 결정 항목

| ID | 결정 | 상태 |
|---|---|---|
| RI-D1 | `youtube_query_planner` 폐기 — 검색어 설계·검색 실행·선별이 상류(taxonomy hints + url_discovery 체인)에 흡수됨. 수집 노드는 확정 video_id 의 댓글만 수집. `state.query_plan` 키 폐기 대상 | **확정** (2026-06-06) |
| RI-D2 | 노드명 `youtube_reaction_collection` — 수집 범위 = 댓글 + 영상 제목·설명(공식 API) | **확정** (2026-06-06) |
| RI-D3 | 자막 수집 보류 — 공식 API 불가(소유자 OAuth)·비공식 스크래핑 ToS 리스크(D11 충돌)·화자 구분 필요. 재검토 트리거: aspect 커버리지 미달 | **확정** (2026-06-06) |
| RI-D4 | 선별 상한 — 영상 feature당 조회수 상위 2·candidate당 6, 댓글 영상당 좋아요 상위 30(필터 후)·candidate당 150 | **확정** (2026-06-06) — 수치는 파일럿 후 조정 여지 |
| RI-D5 | ABSA LLM 어댑터 = `ClaudeCodeCliAnalyzer` (댓글 대량 입력 — 구독 경로 비용 적합) | **확정** (2026-06-06, 사용자) |
| RI-D6 | 루브릭 채점 = 코드 결정론 (CM-D6 패턴 이식) | 제안 |
| RI-D7 | 채널 가중치 2채널 재산정 — 실측 결과 커뮤니티 채널의 실체가 **개인 블로그 후기 중심**(personal_blog 다수 + review_site, 익명 커뮤니티 0건). **YouTube 1.0 / blog_community 0.9** (다수 대중 반응·좋아요 신호 vs 장문·단일 화자) | **확정** (2026-06-06, 사용자) |
| RI-D8 | 커뮤니티 정적 fetch 통과율 실측 — **2026-06-06 완료: 18 URL 중 15건 ok = 83%** (tistory·brunch·naver mobile 등 전부 통과, 제외 3건은 robots/기타). 판정 기준(≥50%) 충족 → **2채널 운영 확정**(루브릭 4점 요건 성립), Playwright 우선순위 상향 불요 | **확정** (2026-06-06, 실측) |
| RI-D9 | 영상 조회수 statistics 일괄 보강 — 실측에서 feature_mapping 출력의 existing_urls 가 view_count 를 carry 하지 않음(전부 0)이 확인되어 RI-D4 조회수 정렬이 무력화. 수집 노드가 선별 전 후보 풀 전체의 `videos.list(part=statistics)` 를 일괄 조회(50건당 1 unit)해 보강. quota 영향 +1~2 units. (근본 원인 — mapping LLM 출력 schema 의 메타 echo 손실 — 은 feature_mapping 시리즈 부채로 별도 기록) | **확정** (2026-06-06, 실측 반영) |

| RI-D10 | 댓글 선별 2단계 정렬 (사용자 제안, 2026-06-06) — ① 좋아요 > 0 구간을 좋아요순으로 우선 채택, ② 잔여 슬롯은 잡담 필터(순수 ㅋㅋ/ㅎㅎ·단순 감탄·영상 자체 언급·"N등" — 보수적 패턴, 30자 미만 한정) 통과분을 **최신순**으로 충원. 좋아요 0 다수 영상에서 '상위 N' 변별력 소실 보완. 최종 거름망은 ABSA(영상 잡담 무시 규칙)가 수행 | **확정** (2026-06-06) |

**실측 baseline (2026-06-06, youtube 채널)**: 영상 23건(candidate 4) · 채택 댓글 524건
· quota 27 units · 9.4초. 댓글 video_id 누락 버그 발견·수정(회귀 테스트 추가).
