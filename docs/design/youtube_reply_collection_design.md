# YouTube 대댓글(parent 연결) 수집 설계

> - **상태**: DRAFT — 사용자 검토 후 확정 예정
> - **작성일**: 2026-06-18
> - **시리즈**: reaction_insight 수집 계층 보강 — 스레드 맥락 보존
> - **선행 문서**:
>   - `docs/design/youtube_collection_redesign.md` (CONFIRMED — 본 설계의 baseline. Phase 4 댓글 전량 수집·pre-filter v2)
>   - `docs/design/reaction_analysis_chunking_design.md` (DRAFT — CH-D3 스레드 원자 chunk 경계가 본 설계의 `thread_id`를 소비)
> - **대상 파일**: `server/llm/youtube_client.py`(`youtube_comment_threads` 수정),
>   `server/graph/nodes/youtube_reaction_collection_node.py`(스키마·필터),
>   `server/graph/nodes/reaction_analysis_node.py`(`build_absa_inputs`에서 `thread_id` 전달)

---

## 0. 문서 위치 판단 (왜 신규 문서인가)

대댓글 수집은 YouTube 수집 계층 변경이므로 `youtube_collection_redesign.md`와 주제가
같다. 그러나 그 문서는 **상태: CONFIRMED**의 확정 결정 기록이다. 확정 문서에 신규 DRAFT
결정을 덧붙이면 (가) 문서 단위 상태 헤더 컨벤션이 깨지고, (나) 검토·확정 이력이 뒤섞인다.
하우스 패턴(예: `reaction_analysis_chunking_design.md`가 `reaction_insight_node_design.md`를
수정하지 않고 별도 문서로 분리)에 따라, 본 변경도 **신규 DRAFT 문서로 분리**하고
`youtube_collection_redesign.md`를 baseline 선행 문서로 참조한다. 확정 시 본 문서를
CONFIRMED로 승격하고, 필요하면 baseline 문서에 한 줄 상호참조만 추가한다.

---

## 1. 변경 동기

`reaction_analysis_chunking_design.md`의 CH-D3은 chunk 경계를 **스레드 원자 단위**로 두어
cross-comment 맥락 손실을 최소화한다. 이 설계가 성립하려면 ABSA 입력 item이 어느 스레드에
속하는지(`thread_id`)를 알아야 하고, 대댓글이 corpus에 존재해야 한다. 그러나 현행 수집은
이 전제를 충족하지 못한다.

---

## 2. 현행 상태 (코드 실사 — 2026-06-18)

- `youtube_comment_threads`(`server/llm/youtube_client.py` L346)는 **최상위 댓글만**
  수집한다. `_parse_thread`(L377)가 `topLevelComment`만 추출하고, 요청 `part="snippet"`
  (L399)이라 `replies`를 받지 않는다. → **대댓글이 corpus에 아예 없다.**
- `selected_comments` 항목(L31 docstring)은
  `{video_id, candidate_id, comment_id, text, like_count, published_at, author_hash}`로,
  모두 최상위라 `parent_id`/`thread_id`가 없다.
- `reaction_analysis_node.build_absa_inputs`(L105)는 `selected_comments`에서
  `{channel, source_url, posted_at, text}`만 item으로 만들고 `thread_id`를 싣지 않는다.

귀결: 서로 참조가 집중되는 대댓글이 버려지고 있어, 현재 chunk 분할로 잃는 상호 참조
맥락은 제한적이다. 동시에 CH-D3의 스레드 원자성은 폴백(item=thread)으로만 동작한다.

---

## 3. 설계 결정

### 3-1. 단계적 수집 — 인라인 우선 (YR-D1)

**결정 YR-D1**: 2단계로 나눈다.

- **Phase A (기본)**: `commentThreads.list`의 `part`를 `"snippet,replies"`로 변경한다.
  같은 quota(호출당 1 unit)로 스레드당 인라인 대댓글 **최대 5개**를
  `thread.replies.comments`에서 함께 받는다. 추가 quota 0.
- **Phase B (옵션)**: 5개 초과 스레드의 전량 대댓글이 필요하면 `comments.list?parentId`를
  스레드별로 추가 호출한다. 호출당 1 unit의 **추가 quota**가 발생하므로, 대상 스레드를
  제한(예: 대댓글 수 상위 N, 또는 최상위 좋아요 상위 N)한다.

근거: 인라인 5개만으로도 가장 참여도 높은 스레드의 핵심 반응을 0 추가 비용으로 회수한다.
효과 측정 후 부족하면 Phase B로 확장하는 보수적 접근.

### 3-2. 스키마 확장 (YR-D2)

**결정 YR-D2**: `_parse_thread`가 최상위 댓글과 인라인 대댓글을 모두 방출하고, 각 항목에
다음을 부여한다.

| 필드 | 최상위 | 대댓글 |
|---|---|---|
| `comment_id` | thread.id | reply.id |
| `thread_id` | thread.id | thread.id (부모와 동일) |
| `is_reply` | false | true |
| `parent_id` | "" | thread.id |
| `text`/`like_count`/`published_at`/`author_hash` | 기존 동일 | reply.snippet 기준 |

`selected_comments`는 `{**c, video_id, candidate_id}` 복제 구조(L259)라, `_parse_thread`가
`thread_id`/`is_reply`를 넣으면 자동 전파된다. D11 작성자 비저장 원칙(`author_hash`)은
대댓글에도 동일 적용한다.

### 3-3. pre-filter v2와 대댓글 (YR-D3)

**결정 YR-D3**: `_prefilter_v2`는 대댓글에도 동일 적용하되, **스레드 보존 규칙**을 둔다.
최상위 댓글이 통과(aspect 키워드 보유)했고 대댓글이 그 맥락을 잇는 경우, 대댓글이
aspect 키워드를 직접 갖지 않아도 1단계(키워드) 필터에서 탈락시키지 않는다. 단 기본
노이즈(`_filter_basic`)·순수 의문문 필터는 대댓글에도 적용한다.

근거: 대댓글의 가치는 부모 맥락에 대한 응답이라, 키워드 자족성을 요구하면 맥락 보존
목적과 충돌한다. 다만 노이즈·순수 의문 제거는 품질상 유지한다. **결정 필요**: 이 완화가
오제외율/유지율(현 40%)을 어떻게 바꾸는지 `validate_youtube_prefilter.py` 재측정 필요(§5).

### 3-4. 다운스트림 thread_id 전달 (YR-D4)

**결정 YR-D4**: `build_absa_inputs`가 item에 `thread_id`를 포함시킨다. dedup 키
(`_norm(text)[:200]`)는 유지하되, 동일 텍스트라도 서로 다른 thread는 별개로 보존할지
여부는 §5 결정. CH-D3 chunk 경계가 이 `thread_id`를 소비한다.

### 3-5. quota·캐시 영향 (YR-D5)

**결정 YR-D5**: Phase A는 `commentThreads.list` 호출당 unit 변화가 없어 quota 중립이다.
캐시(`youtube_comments`, 24h TTL)는 키 입력 변화가 없으나 **응답 스키마가 바뀌므로**,
배포 시 기존 캐시는 무효화(키 컨텍스트에 `part`/스키마 버전 반영)하여 stale 최상위-only
데이터를 재사용하지 않게 한다. Phase B 도입 시 quota 예산을 별도 산정한다.

---

## 4. 영향 파일·변경 지점

- `server/llm/youtube_client.py`: `params["part"]` `"snippet"`→`"snippet,replies"`(L399);
  `_parse_thread`(L377)를 thread→다건 방출로 확장(최상위+인라인 reply), `thread_id`/
  `is_reply`/`parent_id` 부여; `youtube_comments` 캐시 컨텍스트에 스키마 버전 추가.
- `server/graph/nodes/youtube_reaction_collection_node.py`: `_prefilter_v2`에 스레드 보존
  규칙(YR-D3); `selected_comments` 항목에 신규 필드 전파 확인(복제 구조라 자동).
- `server/graph/nodes/reaction_analysis_node.py`: `build_absa_inputs`가 `thread_id`를 item에
  포함(YR-D4).
- (Phase B 시) `server/llm/youtube_client.py`: `comments.list?parentId` 신규 함수 + quota
  예산.

상태 키 `selected_comments`는 필드가 추가될 뿐(append-only) 기존 소비자는 영향받지 않는다.

---

## 5. 결정 필요 항목

| ID | 항목 | 선택지 | 비고 |
|---|---|---|---|
| R1 | 수집 범위 | Phase A(인라인 5)만 vs A+B(전량) | quota 여유 대비 맥락 필요도 |
| R2 | pre-filter 완화(YR-D3) | 부모 통과 시 대댓글 키워드 면제 vs 동일 적용 | 유지율/오제외 재측정 선행 |
| R3 | 동일 텍스트 dedup(YR-D4) | thread 무관 전역 dedup vs thread별 보존 | 도배 vs 맥락 트레이드오프 |
| R4 | 캐시 무효화 방식 | 스키마 버전 키 vs 수동 purge | 배포 운영 편의 |

---

## 6. 테스트·검증 계획

1. **단위**: `_parse_thread` — `part="snippet,replies"` 응답에서 최상위 1 + 인라인 reply n
   방출, 각 항목 `thread_id` 동일·`is_reply` 정확.
2. **단위**: 대댓글 없는 스레드 → 최상위 1건만 방출(회귀: 기존 동작 보존).
3. **통합**: `selected_comments`에 `thread_id`/`is_reply` 전파 확인.
4. **필터 재측정**: `validate_youtube_prefilter.py`로 YR-D3 완화 전후 유지율·오제외율 비교.
5. **quota**: Phase A 전후 `commentThreads.list` unit 동일(중립) 확인.
6. **연동**: 본 산출 `thread_id`로 `reaction_analysis_chunking_design.md` CH-D3 스레드
   원자 chunk 경계가 발효되어, 한 스레드가 단일 chunk에 유지되는지 확인.

---

## 7. baseline 문서와의 관계

`youtube_collection_redesign.md` Phase 4(댓글 전량 수집)는 "최상위 댓글 전량"을 확정했고,
본 설계는 그 위에 "대댓글 + parent 연결"을 **추가**한다. baseline의 §5(자막 잠정 중단)와는
무관하다(자막=영상 본문, 본 설계=댓글 스레드). 확정 시 baseline Phase 4에 "대댓글은
youtube_reply_collection_design.md 참조" 한 줄 상호참조 추가를 권고한다.
