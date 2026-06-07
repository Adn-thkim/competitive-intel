# marketing_social 노드 설계 — 운영 채널 수집 3종 → 마케팅·소셜 분석 리포트

> - **상태**: DRAFT — 사용자 검토 후 확정 예정
> - **작성일**: 2026-06-06
> - **시리즈**: report generation 시리즈 4단계 (owned 채널 수집 3종 → marketing_social).
>   완료 시 A-Only 3종이 갖춰져 battlecard 의 list-fan-in 이 가능해진다.
> - **선행 문서**:
>   - `docs/design/reaction_insight_node_design.md` (시리즈 playbook — 본 문서가 패턴 계승)
>   - `docs/reference/report_taxonomy.md` §2-3 (4-tuple · 평가 루브릭) · `docs/reference/marketing_social.md`
>   - `docs/design/pipeline_topology_redesign.md` §6-6a (구설계 수집 3노드) · feature_url_mapper_redesign.md (D45)
> - **대상 파일**: `server/graph/nodes/youtube_channel_metadata_collection_node.py` (신규),
>   `server/graph/nodes/blog_rss_collection_node.py` (신규),
>   `server/graph/nodes/pr_release_collection_node.py` (신규),
>   `server/graph/nodes/marketing_social_node.py` (스켈레톤 → 구현),
>   `agents/marketing_social/*`, `server/llm/youtube_client.py` (channels.list 확장),
>   `server/graph/graph.py`, `client/` (탭 활성화 → 전용 화면)

---

## 1. 문서 목적과 범위

자사·경쟁사의 **운영 채널(Owned)** 데이터를 수집해 PESO·게시 빈도·engagement·키워드
차원의 `report_outputs["marketing_social"]` 을 생성하는 4개 노드를 설계합니다.

reaction_insight(수요 측 — 사용자 반응)와 명시적으로 분리된 **공급 측 분석**입니다:
"경쟁사는 어디서 어떻게 메시지를 노출하는가" (reference/marketing_social.md §1-1).

---

## 2. 입력 계약 — 수집 노드 공통 패턴의 예외 (MS-D1)

### 2-1. 실사 결과 (2026-06-06)

D45(v0.10.28b) 결정으로 `feature_mapping_owned_channels` 는 marketing_social feature
정의만 carry 하고 **URL 매핑을 생략**한다 — 실사로 확인: analysis_features 의
marketing_social coverage 는 `existing_urls` 가 전부 빈 배열(origins·platforms 공집합).

따라서 수집 입력은 analysis_features 가 아니라 **`owned_channel_urls_by_candidate`**
(url_discovery_owned_channels 산출, LLM 검증 완료)이다. 항목별 `platform` 메타 보유:
`youtube_official | blog_naver | blog_tistory | instagram | x | press_release`.

### 2-2. 게이트·platform 라우팅

```
게이트: "marketing_social" ∈ selected_purposes (feature 단위 선택은 없음 — D45 B-only 형식)
owned_channel_urls_by_candidate[cid] 의 각 항목을 platform 으로 라우팅:
  youtube_official        → 수집 ① youtube_channel_metadata_collection
  blog_naver/blog_tistory → 수집 ② blog_rss_collection
  blog_self_hosted        → 수집 ② blog_rss_collection (v0.13.4 — RSS 경로 규약이
                            없으므로 /rss·/feed·/atom.xml 순차 시도, 실패 시 presence-only)
  press_release           → 수집 ③ pr_release_collection
  instagram               → 1차 보류 (MS-D3a — Graph API 절차 충족 시 활성)
  x                       → 존재 여부만 기록 (MS-D3b — read API 권한 부재)
```

### 2-3. 토폴로지 (MS-D2 — 구설계 3노드 분리 유지, 사용자 확정)

```
feature_selection (#4)
  ├─→ (기존 official·reaction 경로, 병렬)
  ├─→ youtube_channel_metadata_collection ┐
  ├─→ blog_rss_collection                 ┼─(list-fan-in)→ marketing_social → END(임시)
  └─→ pr_release_collection               ┘
```

- reaction 과 달리 **중간 분석 노드 없음** (MS-D7): 게시 빈도·engagement·커버리지는
  결정론 산출이므로, LLM 판정(키워드 토픽·캠페인 카피·인플루언서 협업 흔적 + 서술)은
  marketing_social 노드 내 1회 호출로 통합한다 (CM-D1 분리 사상 유지).
- 노드명·state 키 충돌 점검 완료: 노드 `*_collection` vs state 키
  `youtube_channel_metadata`·`blog_rss_posts`·`pr_releases` — 충돌 없음 (LangGraph 제약).

---

## 3. Instagram·X 처리 (MS-D3, 사용자 확정)

### 3-1. Instagram — 공식 API 수집 절차 안내 (MS-D3a: 절차 충족 전 1차 보류)

Instagram Basic Display API 는 2024-12-04 종료되었고, 현재 공식 경로는
**Instagram Graph API** 뿐이다. 경쟁사 계정 데이터는 **Business Discovery** 엔드포인트
(타 비즈니스/크리에이터 계정의 팔로워 수·미디어 수·공개 게시물 메타를 OAuth 동의 없이
조회 — 경쟁사 모니터링을 지원하는 유일한 공식 엔드포인트)로 가능하다.

**수집 활성화에 필요한 절차 체크리스트**:

| # | 절차 | 비고 |
|---|---|---|
| 1 | Meta 개발자 계정 + 앱 생성 | developers.facebook.com |
| 2 | 자사 Instagram 을 **비즈니스/크리에이터 계정**으로 전환 | 개인 계정은 Graph API 접속 불가 |
| 3 | 자사 계정을 **Facebook 페이지와 연결** | 권한이 페이지를 경유해 부여됨 |
| 4 | OAuth 토큰 발급 + `instagram_basic` (+insights) 권한 | 장기 토큰 60일 주기 갱신 운영 필요 |
| 5 | **Meta App Review 승인** | 통상 4–6주 소요 — 본 시리즈 1차 범위에서 보류하는 직접 사유 |
| 6 | Business Discovery 로 경쟁사 username 조회 검증 | 경쟁사도 비즈니스/크리에이터 계정이어야 조회 가능 |
| 7 | rate limit 운영 설계 | 200 calls/hour/user token |

1차 구현: instagram platform URL 은 **presence(채널 운영 여부) + URL 만 기록**하고
PESO 매트릭스에 "측정 보류(API 절차 미충족)"로 표기. 절차 1~7 완료 시
`instagram_collection` 노드를 추가하는 것으로 확장 (별도 시리즈).

### 3-2. X(구 Twitter) — presence-only (MS-D3b)

X API 의 무료 read 권한 폐지로 게시물 수집은 유료 tier 가 필요하다. **운영 채널 존재
여부 + URL 만 기록**하고 세부 수집(게시 빈도·engagement)은 생략한다. PESO 매트릭스에
"운영 중(세부 측정 생략)"으로 표기.

---

## 4. 수집 노드 3종

### 4-1. `youtube_channel_metadata_collection`

- **입력**: platform=youtube_official URL → channel_id 추출 (URL 패턴 `/channel/UC…`,
  `/@handle` 은 search 비용 회피 위해 `channels.list?forHandle=` 사용).
- **수집** (youtube_client 확장, 모두 1 unit·24h 캐시):
  - `channels.list(part=statistics,snippet,contentDetails)` — 구독자·총 영상 수·
    uploads playlist ID
  - `playlistItems.list(uploads, maxResults=50)` — 최근 영상 50개의 게시일·제목
  - `videos.list(statistics)` — 최근 영상의 조회·좋아요·댓글 (engagement 산출)
- **산출** → state `youtube_channel_metadata`:
  `{candidate_id: {channel_url, subscriber_count, video_total, recent_videos:
  [{video_id, title, published_at, description(300자), view, like, comment}]}}`
  — description 은 playlistItems 응답에 동봉 (MS-D10, quota 추가 없음)
- **quota 견적**: candidate당 3 units × 4 ≈ 12 units.
- ※ reaction 시리즈의 `youtube_reaction_collection`(제3자 리뷰 영상 댓글)과 단위가
  다름 — 본 노드는 **자사·경쟁사 공식 채널의 운영 지표**.

### 4-2. `blog_rss_collection`

- **입력**: platform=blog_naver / blog_tistory URL.
- **수집**: RSS 우선 — naver `rss.blog.naver.com/{blogId}.xml`, tistory `{blog}/rss`,
  self_hosted(v0.13.4)는 `{blog}/rss` → `/feed` → `/atom.xml` 순차 시도.
  stdlib `xml.etree` 파싱(신규 의존성 없음 — MS-D8). 실패 시 sitemap → 그것도 실패 시
  presence-only 강등. 게시일·제목 최근 50건.
- **정책**: robots.txt·rate limit 1초 (community_collection 의 D11 헬퍼 재사용).
- **산출** → state `blog_rss_posts`: `[{candidate_id, platform, blog_url,
  posts: [{title, published_at, link, summary(300자)}], fetch_status}]`
  — summary 는 RSS 동봉 description 발췌 (MS-D10, 추가 fetch 없음)
- ※ 본문 전문은 수집하지 않음 — 게시 빈도·키워드(제목+요약)만. 참여 지표는
  블로그에서 공식 산출 불가(분모 부재 — §1-3 나쁜 예 회피).

### 4-3. `pr_release_collection`

- **입력**: platform=press_release URL (보도자료 목록 페이지).
- **수집**: `_fetch_content` 재사용 → 본문에서 날짜 패턴(`YYYY.MM.DD` 등)·제목 라인
  추출(정규식, 결정론). 목록 페이지 구조가 제각각이라 **추출 실패 시 presence-only
  강등** (부분 실패 허용).
- **산출** → state `pr_releases`: `[{candidate_id, page_url, releases:
  [{title, published_at}], fetch_status}]`

---

## 5. `marketing_social` 리포트 노드

### 5-1. 코드 파트 (결정론)

- **PESO·채널 커버리지 매트릭스**: candidate × platform — `measured`(수집 완료) /
  `presence_only`(instagram·x·강등분) / `none`. Owned 4분면 중심 + 자사 공백
  식별(경쟁사 운영·자사 미운영 채널 — 루브릭 5점 요건).
- **게시 빈도 (2계열 — MS-D10)**: 채널별 월간 게시 수를 **전체 / 분석 대상 상품
  관련**으로 분리 산출하고 "상품 관련 비중(%)"을 병기 — **동일 기간 윈도우 최근
  6개월** (MS-D5, AP-4 시즌성 보정: 전 candidate·전 채널 같은 구간으로 정렬,
  측정 기간 명기). 법인 채널(하나카드·신한카드)은 다수 상품을 다루므로 전체
  빈도만으로는 해당 상품의 마케팅 강도를 측정할 수 없다 (2026-06-07 사용자 지적).
- **engagement** (YouTube 한정): `(좋아요+댓글) ÷ 조회수` per 최근 영상 → 채널 중앙값.
  §1-3 표준 분모(followers) 대신 조회수 분모를 쓰는 사유 명기: 구독자 분모는 영상별
  도달 변동을 왜곡 — 두 값 모두 산출해 표기 (subscriber 분모·view 분모).
- **4-tuple 산출** (Rubric §2-3): `(channel, posting_frequency, audience_size,
  top_keywords)` — top_keywords 는 LLM 산출(§5-2)을 코드가 검증 후 결합.
- **루브릭 코드 채점 (MS-D6 — CM-D6 패턴)**:
  - 2점: 측정 채널 1종 또는 PESO 분류 없음
  - 3점: 측정 채널 ≥ 2종(youtube·blog·pr 중) + PESO 분류 + engagement 분모 명기
  - 4점: 3점 + 채널 × 키워드 cross-tab 산출
  - 5점: 4점 + 동일 기간 정렬 명기 + 자사 공백 식별 ≥ 1건
  - ※ 루브릭 원문의 "3개 채널"은 측정 가능 채널 2종(인스타·X 보류)인 현실을 반영해
    "2종 + presence 표기"로 보정 — 보정 사실을 score_rationale 에 명시.

### 5-2. LLM 파트 (CLI 1회 — CM-D1 분리)

- 입력(읽기 전용): 채널별 최근 게시물 **제목 + 발췌(YouTube description ·
  RSS summary, 각 300자)** 목록 + 코드 집계 표.
- 산출: ① 채널별 top_keywords(키워드 빈도 + 예시 제목 — §1-5), ② 캠페인 메시지
  카피 톤 분석, ③ 인플루언서 협업 흔적(제목의 협찬·광고 표기) 판정, ④ 서술
  (channel_insights·overall_summary·자사 공백 해설), ⑤ **게시물별
  `is_product_related` 판정 (MS-D10 — 코드 선판정 제외분만)**.
- **MS-D10 하이브리드 판정**: 상품명·정규화 별칭이 제목/발췌에 직접 포함된
  게시물은 코드가 선판정·고정(결정론). 나머지 애매 건만 LLM 이 문맥 판정.
  코드 선판정을 LLM 이 뒤집으면 가드가 기각.
- 가드: 키워드 예시 제목·판정 대상 게시물 ID 가 입력에 실존하는지 검증
  (quote 가드 패턴), 채널 ID 검증.
- degrade(CM-D5): LLM 실패 시 코드 집계만 제공, 점수는 코드 채점이라 불변
  (단 4점 요건인 keyword cross-tab 이 LLM 산출이므로 degrade 시 3점 상한 — 규칙에 반영).

### 5-3. envelope

`build_report_envelope` — content = {peso_matrix, channel_operations(빈도·규모 표),
engagement_table, keyword_crosstab, coverage_gaps, channel_insights, overall_summary,
measurement_window, presence_only_channels}. `report_outputs["marketing_social"]`
자기 키만 반환 (merge reducer).

---

## 6. 검증·실측 계획

1. 단위 — platform 라우팅(6종 분기·instagram/x presence 처리)·channel_id 추출 패턴.
2. 단위 — RSS 파싱(naver·tistory fixture)·강등 경로·robots/rate limit 재사용.
3. 단위 — PR 날짜 추출·실패 강등.
4. 단위 — 빈도 윈도우(6개월 정렬)·engagement 분모 2종·루브릭 경계(2~5점, degrade 3점 상한).
5. 단위 — LLM 가드(비실존 예시 제목 제거)·캐시 결정성·skip 게이트.
6. 실측 — profile 스크립트 `marketing` 서브커맨드: 채널 URL 실물 분포(platform별)·
   RSS 발견율·PR 추출 성공률·quota. 수치로 MS-D 보정 후 배선.
7. 배선 후 E2E — 탭 `implemented: true`, GenericReportView 로 확인 → 전용 화면.

## 7. 결정 항목

| ID | 결정 | 상태 |
|---|---|---|
| MS-D1 | 입력 = `owned_channel_urls_by_candidate` + selected_purposes 게이트 — D45 로 analysis_features 의 URL carry 가 생략된 구조적 사유 (수집 노드 공통 패턴의 예외) | **확정** (2026-06-06, 실사) |
| MS-D2 | 수집 3노드 분리 유지 (youtube_channel_metadata · blog_rss · pr_release — 채널별 책임 명확) | **확정** (2026-06-06, 사용자) |
| MS-D3a | Instagram — Graph API Business Discovery 가 유일 공식 경로. 앱 리뷰(4–6주)·비즈니스 계정 전환·페이지 연결 등 §3-1 절차 충족 전까지 **presence-only 보류** | **확정** (2026-06-06, 사용자 — 절차 안내 §3-1) |
| MS-D3b | X — read API 권한 부재로 **운영 존재 여부만 수집**, 세부 생략 | **확정** (2026-06-06, 사용자) |
| MS-D4 | 지표 — 게시 빈도(월간)·engagement(YouTube 한정, 분모 2종 병기)·audience_size(구독자) | 제안 |
| MS-D5 | 측정 기간 — 전 채널·전 candidate **최근 6개월 동일 윈도우** (AP-4) | 제안 |
| MS-D6 | 루브릭 코드 채점 — §5-1 규칙 (측정 채널 2종 보정 + degrade 3점 상한) | 제안 |
| MS-D7 | 중간 분석 노드 없음 — LLM 판정·서술은 marketing_social 노드 내 1회 통합 | 제안 |
| MS-D8 | RSS 파싱 stdlib `xml.etree` (신규 의존성 없음) | 제안 |
| MS-D9 | platform 분류에 `blog_self_hosted` 추가 (blog.hanabank.com 류) — 탐지 입력은 url_discovery_owned_channels 의 브랜드 site: 보조 쿼리(v0.13.4)와 함께 도입, 수집은 blog_rss_collection 이 담당 | **확정** (2026-06-07, 사용자) |
| MS-D10 | 게시 빈도 2계열(전체 + 상품 관련) — 수집은 제목 + 무비용 발췌(YouTube description·RSS summary, 300자)로 보강하고, 관련성 판정은 **하이브리드**(상품명 직접 포함 = 코드 선판정·고정, 애매 건만 LLM, 뒤집기 기각 가드) | **확정** (2026-06-07, 사용자) |
