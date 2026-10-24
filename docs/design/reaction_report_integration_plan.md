# 고객 반응 인사이트 리포트 — 재설계 반영 통합 계획

> - **상태**: PLAN — PoC 검증 완료분을 프로덕션(리포트 생성 경로)에 이식하기 위한 파일별 조치 계획
> - **작성일**: 2026-07-18 · **갱신**: 2026-07-31 (§6 신설 — 07-18 이후 변경 반영: 수집 정제·title-member·dedup·channel·병합·YouTube↔Community 비대칭)
> - **선행 문서**: [`reaction_pipeline_resketch.md`](./reaction_pipeline_resketch.md) — 각 단계(①게이트 ~ ⑥군집·질문)의 설계·실측 근거. 본 문서는 그 설계를 **어느 프로덕션 파일에 어떻게 적용하는지**를 정리한다.
> - **핵심**: 지금까지 `scripts/poc_*`·`scripts/test_*` 에서 **검증만** 한 새 파이프라인을 `server/graph/` 노드로 **이식**하고, 낡은 방식(단일 라벨 관련성 + codebook 강제 ABSA)을 걷어낸다.

---

## 0. 한눈에 보기 — 무엇이 바뀌나

**현행(구조)**
```
youtube_reaction_collection ─┐
community_collection ─────────┤→ reaction_absa(reaction_analysis_node)  → reaction_insight
                                   · relevance_tagger 단일 라벨 관련성          · aspect×polarity 매트릭스
                                   · aspect_codebook 강제 ABSA(최근접 배치)     · LLM 서술
                                   · per-candidate MAX_ITEMS 캡
```

**신설계(reaction_pipeline_resketch 반영)**
```
수집 ─→ ①영상 게이트 ─→ ②Stage0(결정론) ─→ ③Stage1 번들 ─→ ④프리스크린(Sonnet)
        └→ ⑤Stage2① 추출(의견 units + 질문 questions)
             └→ ⑥Stage2② 군집:  Phase A(축 도출→human_review→고정 축)  +  Phase B(전체 배정)
                  · 반응: super_aspect × perspective × polarity
                  · 질문: super_aspect × question_type
                  └→ reaction_insight(리포트: 반응 히트맵 + 질문 불안/방법 집계)
```

**세 줄 요약**
1. **관련성 판정**을 키워드/단일 라벨(`relevance_tagger`)에서 **영상 게이트 + 프리스크린(LLM)** 으로 이관.
2. **aspect 분류**를 사전 codebook 강제배정에서 **상향식 군집 + 고정 축(human_review)** 으로 이관.
3. **질문(is_question)** 을 버리지 않고 별도 분석(super_aspect × question_type)으로 활용.

---

## 1. 파일별 조치 표

### 1-A. 수정 (MODIFY)

| 파일 | 현재 역할 | 바꿀 내용 | 근거(resketch) |
|---|---|---|---|
| `server/graph/nodes/youtube_reaction_collection_node.py` | 수집 + Stage0 필터 predicate | Stage0 로직 유지하되 **is_question 플래그 생성 제거**(질문 판정은 ⑤ P5로 이관). 수집·orphan 처리 유지 | §Stage0, ⑤ P5 |
| `server/graph/nodes/reaction_analysis_node.py` | codebook ABSA(7-tuple, 최근접 강제, MAX_ITEMS) | **핵심 교체** — 게이트→프리스크린→추출(units+questions)→군집으로 대체. 파일이 비대해지면 아래 §1-B로 노드 분할 | ①~⑥ 전체 |
| `server/graph/nodes/reaction_insight_node.py` | reaction_analysis tuples → aspect×polarity 매트릭스 + LLM 서술 | **입력 스키마 교체** — 고정 super_aspect 축 기반 반응 히트맵(super_aspect × perspective × polarity) + **질문 분석 섹션**(super_aspect × question_type) 추가 | ⑥ 산출 |
| `server/graph/state.py` | DomainAnalysisState | **키 추가**: `reaction_units`·`reaction_questions`(⑤ 산출), `reaction_axis`(고정 축), `reaction_clustered`(⑥ 산출). `reaction_analysis`(구 tuples)는 이행기 병행 후 제거 | ⑤·⑥ |
| `server/graph/graph.py` | 노드 등록·엣지 | 신설 노드 등록 + 엣지 재배선(§1-B). `reaction_absa`→신설 노드 체인으로 교체 | 파이프라인 개요 |
| `server/config.py` | REACTION_ABSA_* 파라미터 | **추가**: `REACTION_CHUNK_CHARS=1500`, `REACTION_CONTEXT_MODE=compact`, `REACTION_MAX_SUPERS`, `REACTION_AXIS_CACHE`(고정 축 경로). **timeout 600 유지**. 구 `REACTION_ABSA_MAX_ITEMS`(캡)·CHUNK_CHARS(12000)는 신경로에서 미사용 | ⑤ 청킹·컨텍스트 |
| `client/src/components/ReactionInsightReport.jsx` | 히트맵 + 대표반응 렌더 | **렌더 교체** — super_aspect × perspective 반응 히트맵 + **질문(불안/방법/원인) 집계 섹션** 추가 | ⑥ 교차표 |

### 1-B. 신설 (CREATE) — PoC 로직을 프로덕션 노드로 이식

| 신설 파일 | 이식 원본(PoC) | 역할 |
|---|---|---|
| `server/graph/nodes/reaction_video_gate_node.py` | `scripts/test_gate_prescreen_v2.py`(gate 부분) | ① 영상 도메인 게이트(Haiku) |
| `server/graph/nodes/reaction_prescreen_node.py` | `scripts/poc_prescreen_with_questions.py` | ②Stage0 + ③Stage1 번들 + ④프리스크린(Sonnet) |
| `server/graph/nodes/reaction_extract_node.py` | `scripts/poc_opinion_units.py` | ⑤ 의견 units + 질문 questions 추출(원칙 P1~P5·reasoning·귀속·글자수 청킹) |
| `server/graph/nodes/reaction_cluster_node.py` | `scripts/poc_cluster_uq_v2.py` + `poc_bottomup_cluster.py` + `poc_cluster_units_questions.py` | ⑥ Phase A 축 도출 + Phase B 배정 + 질문 매핑·question_type·perspective |
| `server/graph/reaction_axis_store.py` | (신규 — `official_source_store.py` 패턴 참고) | 고정 축(super_aspect+정의) **저장/로드/버전** + human_review 반영 |

> **분할 판단**: 위처럼 4~5개 노드로 나누면 각 단계가 캐시·재시도·human_review interrupt를 독립적으로 갖는다(프로젝트의 노드 단위 캐싱·interrupt 패턴과 정합). 하나의 거대 `reaction_analysis_node` 재작성보다 유지보수가 쉽다.

### 1-C. 폐기 (DEPRECATE / 신경로에서 미사용)

| 파일/자산 | 이유 | 조치 |
|---|---|---|
| `server/graph/relevance_tagger.py` | 단일 라벨 관련성 → **영상 게이트 + 프리스크린**이 대체(resketch P3) | 반응 경로에서 참조 제거. **다른 경로에서 쓰이지 않는지 확인 후** 삭제 |
| `data/taxonomy/aspect_codebook*.json` (강제 codebook으로서) | 최근접 강제배정 폐기 → **상향식 고정 축**이 대체(resketch P4) | 강제 codebook 용도 폐기. **perspective 고정 목록 개념은 유지**(→ 코드 상수). `domain_modeling_node`가 생성하던 codebook 도 반응 리포트용은 불필요 |
| `reaction_analysis_node.py`의 codebook ABSA·MAX_ITEMS 청킹 로직 | 신 추출·군집이 대체 | §1-A에서 노드 교체 시 함께 제거 |
| `scripts/poc_*`·`scripts/test_*`·`load_*_df.py` | **개발 검증용** — 프로덕션 미배포 | 삭제하지 말고 **참조·재현용으로 보존**(레포 dev 자산). 정본만 노드로 이식 |

### 1-D. 유지 (KEEP)

`agent_cache.py`(캐시 유틸), `progress_store.py`(진행률), 수집 상류(url_discovery_*) 는 그대로 사용.

> ⚠️ **2026-07-31 정정**: `community_collection_node.py`는 더 이상 "그대로 KEEP"이 아니다.
> 본문·댓글 수집 품질 개선으로 **대폭 수정**됐다(→ §1-A로 이동, 상세는 §6 및
> [`community_body_comment_cleaning.md`](./community_body_comment_cleaning.md)).

---

## 2. 추가로 새로 만들어야 하는 것 (PoC 에도 아직 없는 것)

이식만으로 부족하고 **새로 설계·구현**이 필요한 부분:

1. **Phase B (전체 데이터 고정 축 배정)** — PoC 는 Phase A(축 도출)만 있다. 전체 units·questions 를 **고정 축에 분류**(재군집 아님) + '기타' 잔여 + '기타' 비율 모니터링을 구현해야 한다. → `reaction_cluster_node.py` 안에 `axis_mode=derive|assign` 분기.
2. **human_review 인터럽트** — 도출된 축을 사람이 검토·수정하는 단계. 프로젝트의 `interrupt()` 패턴(interrupt#1~4)과 동일하게 **interrupt#5(축 검토)** 를 추가. 프런트에 축 편집 UI 필요.
3. **고정 축 저장소** (`reaction_axis_store.py`) — 축(라벨+정의+버전+생성일) 저장/로드. 입력 도메인 해시로 캐시 히트 시 도출 생략(resketch §7 "캐시 우선").
4. **candidate ↔ 부속 서비스 컨텍스트의 프로덕션화** — PoC 는 `state.product_profiles` 에서 동적 생성(도메인 무관). 그대로 이식하되, compact 서비스 인덱스 캐시 버그(엔진 키 누락)는 이식 시 수정.
5. **리포트 2축 스키마** — 반응 히트맵(super_aspect × perspective)과 질문 집계(super_aspect × question_type)를 담는 리포트 산출 스키마 정의 + 프런트 렌더.
6. **오류 조기중단 이식** — PoC 의 세션한도(429)·인증(401) 조기중단, 실패 배치 쪼개 재시도, silent-fallback 방지를 프로덕션 LLM 어댑터 호출부에 반영.

---

## 3. state 키 변경 (state.py)

| 키 | 방향 | 산출 노드 | 소비 노드 |
|---|---|---|---|
| `reaction_units` | 신설 | reaction_extract | reaction_cluster |
| `reaction_questions` | 신설 | reaction_extract | reaction_cluster |
| `reaction_axis` | 신설 | reaction_cluster(Phase A)/store | reaction_cluster(Phase B), reaction_insight |
| `reaction_clustered` | 신설 | reaction_cluster(Phase B) | reaction_insight |
| `reaction_analysis` (구 tuples) | 제거(이행기 병행) | reaction_absa(폐기) | reaction_insight(교체) |

---

## 4. 마이그레이션 순서 (안전한 단계)

1. **state 키 추가**(비파괴) → 2. **신설 노드 5종 이식**(기존 경로와 병행, 새 out 키에만 기록) → 3. **reaction_insight 를 신 스키마로 전환**(구 tuples fallback 유지) → 4. **graph 엣지 전환**(reaction_absa → 신 체인) → 5. **human_review interrupt·프런트 축 편집 UI** → 6. **구 자산 제거**(relevance_tagger·codebook ABSA·reaction_analysis 키) → 7. **프런트 리포트 렌더 교체**.

> 각 단계는 독립 배포 가능(병행 → 전환 → 정리). 3까지는 롤백이 쉽고, 6에서 낡은 자산을 걷는다.

---

## 5. reaction_pipeline_resketch.md 와의 연결

| 본 문서 항목 | resketch 참조 |
|---|---|
| ①영상 게이트·②③④ 프리스크린 | §2 파이프라인 개요, §⑤ 이전 단계, §4 실측 퍼널 |
| ⑤ 추출(units+questions) | §⑤ Stage 2① |
| ⑥ 군집·질문(question_type) | §⑥ Stage 2②, §7 super_aspect 2단계 |
| 고정 축·human_review·캐시 | §7 Phase A/B 다이어그램 |

**요약**: resketch 는 *"무엇을 왜 이렇게 설계했는가"*, 본 문서는 *"그 설계를 어느 파일에 어떻게 넣고 무엇을 걷어내는가"* 를 담당한다. 구현 착수 시 두 문서를 함께 참조한다.

---

## 6. 2026-07-31 업데이트 — 07-18 이후 변경 반영

07-18 계획 작성 이후 PoC/노드에서 **검증·구현된 변경**을 반영한다. 아래 항목은 위 §1~§5의
누락·구식 부분을 보완한다.

### 6-1. 수집 본문·댓글 정제 (community_collection_node **대폭 수정** — §1-D KEEP → §1-A MODIFY)

상세: [`community_body_comment_cleaning.md`](./community_body_comment_cleaning.md). 프로덕션 노드에
반영 완료(신규 수집분 적용):

- **사이트별 본문 셀렉터**: dcinside `.thum-txtin`·theqoo `.rd_body`·ppomppu `.viAr`
  (trafilatura가 목록·공지·댓글을 본문으로 오추출하던 문제 수리). UA 정규화(m.dcinside·ppomppu www/m).
- **텍스트 정제**(`_clean_body_text`): 제목접두·사이트 크롬(브레드크럼/푸터/affiliate/©에펨/nav)·HTML 제거.
- **URL→`[링크]`**(`_sub_urls`)·**OG 미리보기 카드 제거**(`_strip_embed_cards`).
- **본문 부재(bodyless) 구분**: 실질 본문 <1자 → `empty_body`(추출 실패와 구분). `body_is_thin` 플래그.
- **삭제글 감지**: `_ERROR_PAGE_SIGNS`에 `취소된/종결된 게시물` 추가.
- **댓글 스캐폴딩 정제**(`_clean_comment_text`): ppomppu `"투표수 닉네임 날짜"` 제거(read-time).
- 오프라인 재처리 스크립트(기존 캐시 소급): `reextract_dcinside_theqoo.py`·`postclean_community_bodies.py`·
  `clean_comment_scaffolding.py`·`patch_state_bodies.py`. 각 `--apply` 시 `./backup` 백업.

### 6-2. YouTube ↔ Community 파이프라인 **비대칭** (게이트 ~ 병합 전) — 신규 문서화

두 채널은 ①~④에서 **경로가 다르다**. §0의 통합 다이어그램은 YouTube 기준이며, Community는 아래와 같이 축약된다:

| 단계 | YouTube | Community |
|---|---|---|
| 수집 | youtube_reaction_collection(영상별 댓글) | community_collection(**§6-1 정제 포함**) |
| ① 게이트 | 영상 도메인 게이트(제목+설명) | **도메인 게이트**(`community_gate_sys` — 게시글 제목+본문) + **title-override**(제목 카드신호 시 keep, item 3.1) |
| ② Stage 0 (필러 컷·orphan) | **있음** | **없음** — 1 게시글=1 스레드(본문=root·댓글=reply) |
| ③ Stage 1 (프리스크린) | **있음** | **없음**(상동) — 최종 의미 필터는 ⑤ 추출 P5에 위임 |
| ⑤ 추출 | units+questions | units+questions + **§6-3 title-member·dedup** |

→ **병합 지점은 ⑤ 추출 이후**: 양 채널이 동일 스키마 units/questions(+`channel` 태그)를 산출한 뒤
**concat → ⑥ 군집**. 이식 시 `reaction_prescreen_node`(Stage0/1)는 **community 경로에서 스킵**하도록
분기하고, 게이트 노드는 채널별 프롬프트/override를 갖는다.
(커뮤니티 경량 Stage 0(필러 컷) 도입은 선택 — 미결.)

### 6-3. ⑤ 추출 — title-member + 스레드 내 dedup (§1-B `reaction_extract_node` 보강)

검증: `scripts/test_title_member_units.py`.

- **제목 멤버화**: 스레드 멤버에 제목을 `(t0,"title","제목",title)`로 추가 → 본문이 thin/empty여도
  제목의 의견·질문이 unit/question으로 추출됨(특히 질문 회수 효과 큼).
- **dedup(스레드 내 한정)**: `target × aspect × polarity` 동일 시 병합, quote는 본문 우선. 구별·상충 보존.
  cross-post 병합 금지(집계 레이어의 몫). `_SYS`에 규칙 addendum.
- 이식 시 `build_threads`(제목 멤버)·`_SYS`(dedup 규칙)를 `reaction_extract_node`에 반영.
- **2026-08 옵션 B 적용**: dedup addendum 을 **양 채널 공통** 적용(유튜브도 dedup — 기존엔 유튜브 중복 방치).
  청크는 여전히 채널별 분리 추출(청크에 두 채널 미혼합). LLM dedup 은 의미 기반이라 파이썬 exact-key 대비
  과병합 위험이 없고 출력 토큰도 ~11% 적다(중복을 추출 중 병합). addendum 의 '[제목] 멤버' 지시는 유튜브엔
  해당 t0 가 없어 **자연어상 무효(inert)** — 코드가 t0 존재를 가정하지 않아(members 전체 순회) **에러 없음**.

### 6-4. channel / source_site 태깅 (조립 단계)

- 각 unit/question에 `channel`(youtube/community/blog) + 출처 식별자 부여. **추출 직후 조립 단계**에서 태깅
  (군집은 passthrough). 프로덕션 `reaction_analysis_node.sanitize_tuples`에 `source_url→channel` 매핑
  추가 완료. PoC는 `channel`+`source_site`(youtube="youtube"/community=도메인)+`video_id`.
- **주의**: 프로덕션은 `source_url`(youtube=영상 watch URL·community=게시글 URL)로 이미 출처 식별 가능.
  PoC(`source_site`+`video_id`)와 **필드명 정규화** 필요(병합·리포트 일관성).

### 6-5. ⑥ 군집 — 병합 + 필드 보존 (§1-B `reaction_cluster_node`·§2-1 보강)

검증: `scripts/poc_cluster_merged.py` + `poc_cluster_uq_v2.py`.

- **YT+community 병합 후 군집**(옵션 2 확정): 채널 독립 군집 시 택소노미 이동(예: 재환전 수수료)이
  채널 간 불일치를 유발 → **병합 군집으로 동일 aspect 일관 처리**. (실측: 재환전 수수료 = 환전 축 일관)
- **클러스터러 출력 필드 보존 수리**: `poc_cluster_uq_v2` 출력이 compact 투영이라 channel/source_site 등이
  드롭되던 문제 → 처리된 units/questions를 그대로 출력하도록 수정(입력 필드 전량 + 군집 필드 보존).
- **비결정성 유의**: 재실행마다 super_aspect 수가 소폭 변동(11↔13) — `--max-supers`/축 고정(Phase A
  human_review·`reaction_axis_store`)으로 안정화(§2-2·§2-3와 연결).
- df 로더: `load_cluster_uq_df.py`(`**u` spread로 전 필드 자동 포함).

### 6-6. 리포트 방향 — 자사 vs 경쟁사 히트맵 (§1-A `reaction_insight_node` 구체화)

- **채널 차이 축은 리포트 핵심이 아님**(채널별 불만 차이는 상품 개선 인사이트를 주지 않음).
- 핵심 축 = **자사(own_토스트래블카드) vs 경쟁사 × super_aspect × polarity 반응 히트맵** →
  우위/열위/기회 aspect로 **초점 좁히기** → 그 아래 **perspective 세분화** + **question_type 분석**.
- 유의: `candidate_source="service"`(느슨한 귀속, 예 토스뱅크)가 자사 부정을 과대계상할 수 있어
  explicit 분리 표기·가중치 검토.

### 6-7. 이 업데이트로 §1~§5에 반영할 조치 요약

- §1-A: `community_collection_node`(정제) 추가, `reaction_insight_node`에 자사 vs 경쟁사 히트맵 명시.
- §1-B: `reaction_extract_node`에 title-member·dedup·channel 태깅, `reaction_cluster_node`에 **병합 단계**·
  필드 보존 추가.
- §1-D: `community_collection_node` 삭제(→ §1-A).
- §2: "새로 만들 것"에 **채널 병합 로직**·**필드명 정규화(PoC↔프로덕션)**·**커뮤니티 Stage0/1 스킵 분기** 추가.

## 7. 2026-08 업데이트 — 비용·재현성 최적화

신 파이프라인 배선·실행 후 도출한 최적화. 온라인 실행 로그·오프라인 실측 근거.

### 7-1. thread_id 단축 (프롬프트 비용 절감, 무손실)

프리스크린/추출 프롬프트가 스레드마다 원본 `[thread_id=<id>]`를 반복하는데, **유튜브 댓글 ID가 ~26자**라
프롬프트의 큰 비중을 차지했다(실측: 프리스크린 프롬프트의 ~40~46%, 유튜브 추출의 ~28%가 thread_id 라벨).
→ 프롬프트에는 **짧은 인덱스 `t1..tN`**만 싣고, 결과 파싱 시 **동일 enumerate 로 실제 id 역매핑**한다.

- 적용 위치: `prescreen.prescreen_batch` + `extract._prompt`(+ `extract()` 결과 역매핑). `_prompt`는 **양 채널 공용**이라 한 곳 수정으로 커뮤니티·유튜브 동시 적용.
- 효과 비대칭: **유튜브 계열에 집중**(프리스크린 ~41%·유튜브 추출 ~23% 프롬프트 축소). **커뮤니티 추출은 2~3%**뿐 — 1 게시글=1 스레드라 스레드당 내용(본문+댓글)이 크고 ID 비중이 미미. 커뮤니티는 부수 이득만.
- **콘텐츠·품질 무손실**: 모델은 스레드 구분용 손잡이만 필요하고 실제 id 는 불필요.
- 주의: 프롬프트 내용이 바뀌어 기존 청크 캐시는 자동 미스 → 재실행 시 재생성.

### 7-2. 캐시·안정성 (동반 조치)

- **구 경로 비활성**: `reaction_absa→reaction_insight` 그래프 엣지 주석(가역). 신 경로가 단독 반응 경로.
- **429-삼킴 버그 수정**: `prescreen_batch`는 실패 시 예외 전파, `gate_videos`는 `(keep, ok)` 반환 →
  **실패분은 캐시하지 않고 재시도**, 성공분만 캐시(gate·prescreen·extract·cluster 전 구간). 과거 keep-all 오염 근절.
- **auto_drop(무손실)**: 프리스크린 전, 키워드 0 + 짧은 스레드는 LLM 없이 탈락(리콜 손실 0 실측).
- **thread_cap 25→40**: 배치 수↓·비용 −20.8%·판정 일치율 95.7%.
- **시간·토큰 계측**: CLI 어댑터에 usage 누적 + graph `_timed` 래퍼로 노드별 `[timing]` 터미널 출력.

### 7-3. 미채택 (근거)

- **prescreen Haiku 하향**: 과통과(통과율 34%→58%)로 하류 extract 비용 증가·정밀도 손실 → 미채택(Sonnet 유지).
- **글자 수 절단**: 댓글 원문 중간 절단은 의미 훼손 → 미채택.
- **2단계 auto_keep**: 어느 임계값에서도 정밀도 손실(오탐) 존재 → auto_drop 만 채택.

## 8. 채널별 단계 출력 스키마 (gate → cluster)

### 8-0. 노드 흐름 토폴로지 (수집 → cluster)

![[reaction_pipeline_topology.png]]
- **두 채널 병렬**: 유튜브(gate→prescreen)와 커뮤니티(community_gate)가 독립 실행 → threads 산출 후 **fan-in**으로 extract 합류.
- **캐시·TTL(7200h)**: reaction_video_gate·reaction_community_gate·reaction_prescreen·reaction_extract(채널별)·reaction_cluster. 실패는 미캐시(재시도).
- 병합(concat)은 **extract 이후**(§6-2). cluster·review·report 는 병합된 단일 스트림.


각 노드의 **입력 → 출력(state 키)** 계약. youtube 는 gate·prescreen 을 거치고, community 는 gate/Stage0·1 을
extract 내부에서 축약 수행(§6-2 비대칭). extract 이후는 두 채널 동일 스키마 → ⑥에서 concat.

> **2026-08 리팩터**: 커뮤니티 게이트를 **독립 노드(`reaction_community_gate`)로 분리**하고 스레드 조립까지
> 담당하게 해 **유튜브 prescreen 과 출력 스키마를 통일**했다. extract 는 두 채널 threads 를 **읽어 추출만** 한다.
> 그래프 반응 노드: `reaction_video_gate → reaction_prescreen ─┐` , `reaction_community_gate ─┤ (fan-in) →
> reaction_extract → reaction_cluster → reaction_review → reaction_report`. 스레드 조립·channel/source_url
> 태깅은 **상류 노드(prescreen·community_gate)** 가 완료한다.

### 8-1. ① gate — `reaction_video_gate_node` (**youtube 전용**)
- 입력: `state.collected_videos: [video]`

  | 필드 | 의미 | 예시 |
  |---|---|---|
  | `video_id` | 유튜브 영상 id | "abc123" |
  | `title` | 영상 제목 | "일본 여행 트래블카드 비교" |
  | `description` | 영상 설명 | "환전·해외결제 수수료 정리…" |
  | `url` | 영상 watch URL | "youtube.com/watch?v=abc123" |
- 출력: `reaction_gate: {"kept_video_ids": [video_id]}` — 트래블카드 리뷰/사용기인 영상 id만 통과.
  예: `["abc123","def456"]`(302→179 통과).

### 8-1b. ①' community gate — `reaction_community_gate_node` (**community 전용, 신설**)
- 입력: `state.community_posts` (+ community_comments 캐시)
- 처리: `gate_videos_graded(community_gate_sys)` 로 게시글별 `relevant`(keep) + `domain_score`(0~3) 산출
  → `build_threads`(제목 t0 멤버) → 스레드 조립 → **점수 컷**(§8-5).
- 출력: `reaction_community_prescreened: {"threads": [thread]}` — **유튜브 prescreen 과 동일 스키마**.
  각 thread 에 `channel="community"` + `source_url`(게시글 url) + `importance_score`(0~3) 태그.
- 캐시: `agent_cache`(agent_id=`reaction_community_gate`), **TTL=`cache_ttls.yaml.reaction_community_gate_hours`(7200h)**.
  게시글 pid 집합 해시 키(순서 불변). 저장값 `{keep, score}`. 실패(429 등)는 미캐시 → 재실행 재시도.
  (등급 스키마 전환으로 구 boolean 캐시는 미적중 — 최초 1회 재실행 필요, 오류 아님.)

### 8-2. ②③④ prescreen — `reaction_prescreen_node` (**youtube 전용**)
- 입력: `reaction_gate.kept_video_ids` + `youtube_comments` 캐시(원본 댓글)
- 처리: Stage0(노이즈·필러 컷) → Stage1(대댓글 orphan 해제) → auto_drop → **등급 프리스크린**(Sonnet,
  `relevant`=keep + `importance_score` 0~3) → **점수 컷**(§8-5, 전 영상 전역).
- 출력: `reaction_prescreened: {"threads": [thread]}` — 트래블카드 반응 스레드만.

  | 필드 | 의미 | 예시 |
  |---|---|---|
  | `thread_id` | 스레드(=최상위 댓글) id | "UgxAb…" |
  | `video_id` | 소속 영상 id | "abc123" |
  | `ctx.title` / `ctx.desc` | 영상 맥락(추출 프롬프트 헤더용) | "일본 트래블카드 비교" / "환전…" |
  | `members` | 스레드 구성원 `[(lid, comment_id, role, text)]` | `[("c1","Ugx…","root","토스 출금 막혀요"),("c2","Ugy…","reply","저도요")]` |
  | ↳ `lid` | 프롬프트용 짧은 멤버 id | "c1","c2" |
  | ↳ `role` | `∈{"root","reply"}` | root |
  | `importance_score` | 중요도 점수 0~3(랭킹·컷용, keep 판정과 분리) | 3 |
- 캐시: 배치별 `{graded:{tid:[rel,score]}}`(agent_id=`reaction_prescreen`, TTL 7200h). 등급 전환으로 구 캐시 미적중.
- community: 이 노드 미경유(1 게시글=1 스레드). community gate 노드가 `build_threads` 로 직접 조립(제목 t0 멤버 포함).

- 점수별 정의
```
0 = 도메인 무관(비카드 잡담·진행자·여행 안전 수다)
1 = 도메인 관련하나 특정 카드 언급 없음(일반론·질문만)
2 = 특정 카드 언급 + 사용 경험/평가 있음
3 = 특정 카드에 대한 구체적 비교·수치·상세 사용경험
```
### 8-3. ⑤ extract — `reaction_extract_node` (**양 채널**, "threads 읽어 추출만" 후 concat)
- 입력: `reaction_prescreened.threads`(youtube) + `reaction_community_prescreened.threads`(community).
  **조립·태깅은 상류가 완료** → 본 노드는 채널별 개별 추출(청크 미혼합)만. unit 은 스레드의 channel/source_url 상속.
- 컨텍스트: `candidate_context(compact)` + **dedup addendum(양 채널 공통, 옵션 B)**. thread_id 는 프롬프트에서 t1.. 로 단축.
- 출력: `reaction_units: [unit]` + `reaction_questions: [question]`. **unit 필드 의미·예시:**

| 필드                  | 의미                                            | 예시                           |
| ------------------- | --------------------------------------------- | ---------------------------- |
| `reasoning`         | LLM 판단 근거(진짜 대상+태도 한 문장)                      | "세븐뱅크 ATM 출금 실패에 대한 부정 경험"   |
| `target_term`       | 평가 대상(핵심 기능 명사구, ≤8자; 장소·기기·국가·카드명 제외)        | "ATM 출금"                     |
| `opinion`           | 평가 표현(중립이면 사실 요약)                             | "자꾸 막힌다"                     |
| `polarity`          | 극성 `∈{positive,negative,neutral}`             | negative                     |
| `intensity`         | 강도 1~3 (pos/neg 만)                            | 3                            |
| `quote`             | 근거 원문 발췌(요약·번역 금지)                            | "세븐뱅크 출금이 자꾸 막혀요"            |
| `source_comment_id` | 그 의견이 나온 댓글 id                                | "UgxAb…"                     |
| `target_candidate`  | 귀속 카드(정규화명) or 비트래블카드/기타_트래블카드/""             | "신한SOL"                      |
| `candidate_source`  | 귀속 근거 `∈{explicit,service,thread,video,none}` | explicit                     |
| `thread_id`         | 스레드 id                                        | yt "Ugx…" / community "cm_…" |
| `channel`           | 채널 `∈{youtube,community}`                     | youtube                      |
| `source_url`        | 정규 출처 URL(§6-4 조인키)                           | "youtube.com/watch?v=abc123" |

- `question` = unit 에서 `opinion·polarity·intensity` 를 뺀 것(태도 없이 "묻는 대상"만) + `question_type`(⑥에서 부여).
- 캐시: 채널별 분리(`reaction_extract_{channel}`), 성공분만.

**스키마 슬림화(적용 완료 2026-08):** 아래는 cluster/report 미소비 + **cluster 이후 재조립 가능**이라 출력에서 제거:
- `modifier`(하류 미사용 — target_term 이 이미 수식어 제외) · `source_site`(=`source_url` 도메인/채널에서 파생) ·
  `video_id`(youtube=`source_url` 파싱, community=`thread_id`와 동일). → 저장 필드 12개로 축소.
- 후보이나 **유지**: `reasoning`(감사·CoT 품질; 출력 토큰 비용은 별도 검토), `candidate_source`(귀속 근거 감사).

### 8-4. ⑥ cluster — `reaction_cluster_node` (**병합 후**)
- 입력: `reaction_units + reaction_questions`(concat). cluster 가 **읽는** 필드: `target_term`(군집)·`quote`(예시)·
  `polarity`(crosstab). 나머지(channel·source_url·target_candidate 등)는 **passthrough 보존**.
- 출력:
  - `reaction_axis` (고정 축, 저장소 연동)

    | 필드 | 의미 | 예시 |
    |---|---|---|
    | `key` | 도메인+candidate 해시 축 키 | "a1b2c3…" |
    | `version` | 축 버전(human_review 수정 시 증가) | 1 |
    | `super_to_sub` | 상위축→하위군집→빈도 | `{"ATM 이용":{"ATM 현금인출":12}}` |
    | `definitions` | 축 정의(선택) | `{}` |
  - `reaction_clustered: {units, questions, axis_source, summary, meta}`

    | 추가 필드 | 대상 | 의미 | 예시 |
    |---|---|---|---|
    | `sub_cluster` | unit | 하위군집(정규화 라벨) | "ATM 현금인출" |
    | `super_aspect` | unit·question | 상위 축 | "ATM 이용" |
    | `perspective` | unit | 관점 8종 `∈{cost,reliability,convenience,coverage,exchange_rate,benefit,overall,other}` | cost |
    | `question_type` | question | 질문 의도 9종 `∈{feasibility,method,cost,condition,comparison,recommendation,troubleshooting,safety_anxiety,other}` | troubleshooting |
    - `summary`: super_to_sub·crosstab(super_aspect × pos/neg/questions)·question_type 분포 등
    - `axis_source ∈ {"derived"(Phase A 도출), "cache"(Phase B 배정)}`

### 8-5. 점수 기반 물량 컷 (적용 완료 2026-08 — extract 앞단 토큰 제어)

**목적**: extract→cluster→report 의 토큰 폭주(429·30분 동기 한도)를 앞단에서 막는다. 무가치 스레드를
추출하기 전에 걷어내 낭비를 줄인다.

**설계 결론(선택지 B)**: keep 판정은 종전대로 `relevant`(boolean, 리콜 위주)로 유지하고, 점수는 **삭제가
아니라 랭킹·컷용 부가 필드**로 분리한다. 점수를 keep 대체로 쓰면(graded≥1) 공격적으로 잘려 위험하고, 과거
opinion_strength piggyback 처럼 keep 판정이 흔들린다. → keep(리콜)과 score(랭킹)를 **분리**한다.

**점수화 지점**(둘 다 스레드-단위): community=gate(`domain_score`), youtube=prescreen(`importance_score`).
루브릭(`gate.RUBRIC_BODY`, 양 채널 공통, 0~3): 도메인 관련성·구체적 비교·사용 경험·candidate 언급 정도 종합.

**컷 절차**(`gate.score_cut`, 결정론 후처리 — LLM/캐시 무효화 없음, 상한만 조정 가능):
1. `importance_score >= REACTION_SCORE_MIN`(기본 2) 필터.
2. 남은 스레드 수 ≤ 채널 상한이면 **전량 사용**.
3. 초과면 `importance_score` **내림차순 top-상한 절단**(동점은 입력 순서=수집순 안정 유지).

**채널 상한**(`config.py`, 보수 기본값): `REACTION_MAX_COMMUNITY=20`, `REACTION_MAX_YOUTUBE=75`(2026-08 하향).
- 배치 지점: youtube=`reaction_prescreen_node` 말미(전 영상 전역 컷), community=`reaction_community_gate_node` 말미.
- 예상 물량: 커뮤니티는 컷이 **밀도 높은 상위**를 남겨 실측 **10.8 units/thread** → 20·10.8 ≈ 216, 유튜브
  75·~1.7 ≈ 128 → **~345 units**(구 40/150=~520 대비 축소). extract 출력·cluster 입력을 이 규모로 상한 → 429 회피.
- **상한 조정과 캐시**: 컷은 score 내림차순 **접두**라 상한을 바꿔도 extract 청크 캐시가 보존된다 — 축소는
  재추출 0, 확대는 추가분만. (`importance_score` 는 컷 전용, unit 으로 전파 안 함 — 하류 top-N 필요 시 §Future 승격.)

**PoC 근거**(`scripts/poc_gate_score.py`, 층화 표본): score↔카드가치 Spearman **커뮤니티 0.78·유튜브 0.74**(통과분),
버킷 단조 증가. score≥2 필터 제외율 — **커뮤니티 통과분 ~12%·유튜브 ~60%**(제외분은 카드가치 0에 집중, 무손실).
A(등급이 관련 스레드를 떨어뜨리는가): 불일치는 전부 "boolean 통과→등급 무가치 제외" 방향, cval=0 → **가치 손실 0**.

> 유의: PoC 는 검증용이라 자체 루브릭 사본을 쓴다(프로덕션 정본은 `gate.RUBRIC_BODY`). 상한(40/150)은 단일
> 세션 안전 기준의 보수값 — 전량 커버리지가 필요하면 상향 후 **캐시 워밍 분할 실행**(extract 청크 캐시)이 전제.

### 8-6. cluster 배치별 증분 캐시 (적용 완료 2026-08 — 429 완주 보장)

**문제**: `reaction_cluster_node` 는 노드 출력을 **all-or-nothing** 으로만 캐시했다(모든 서브태스크 성공 시에만
저장). cluster 는 다수 배치로 fan-out 되는데(`cluster_terms`·`normalize`·`super`·`perspective`·`map_questions`·
`question_type`), 한 배치라도 429 나면 **성공분까지 통째로 폐기** → 재실행이 매번 처음부터 → 세션 한도에 계속 막힘.
실측: 한 실행에서 `{cluster:4, normalize:1, super:1, perspective:15, question_map:3, question_type:3}` 실패 → 전량 폐기.

**해결(`cluster._llm_cached`)**: 6개 LLM 호출부를 **프롬프트 body 문자열 키**로 개별 캐시(extract 청크 캐시와 동일
원리). body 가 출력을 완전히 결정하므로 content-address 안전. 동작:
- 성공 배치 → 저장. 재실행 시 그 배치는 **LLM 미호출 replay**.
- 실패(429 등) 배치 → **미저장**(예외 전파, 기존 분할 재시도·실패 카운트 유지) → 재실행에서 재시도.
- 여러 세션에 걸쳐 실패분이 줄며 결국 전량 캐시 → 노드 레벨 `if ok` 캐시까지 성사.

**캐시 키**: `agent_id`(태스크별: `reaction_cluster_terms`/`_normalize`/`_super`/`_perspective`/`_qmap`/`_qtype`)
+ body 해시 + context(system_prompt+schema+model). `map_questions`·`super` 는 축(super_labels/max_supers)이
system_prompt 에 포함되어 축이 바뀌면 자동 무효(정합). TTL 없음(내용 결정론 → 시간 만료 불필요).

**효과**: Phase A(run_clustering)·Phase B(노드 고정축 배정) 양쪽에 적용. cluster 가 extract 처럼 세션을 넘나들며
증분 완주 가능. 여전히 남는 전제: 축 도출(cluster_terms→normalize→super)은 순차 의존이라 **축이 한 번은 성공**해야
Phase B(배정)가 안정 — 축이 무거우면 그 세션엔 배정을 미뤄 축부터 확정.
