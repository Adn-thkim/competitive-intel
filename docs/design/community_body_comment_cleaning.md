# 커뮤니티 본문·댓글 수집 품질 개선 (Body/Comment Cleaning)

작성: 2026-07-28 · 대상: `reaction_insight` 커뮤니티 채널(clien·dcinside·theqoo·ppomppu·fmkorea·mlbpark·milemoa)

## 1. 배경 / 문제

도메인 게이트·의견 unit 추출에 앞서 커뮤니티 게시글의 **본문(body)과 댓글(comments)** 품질을
점검한 결과, 사이트별로 서로 다른 수집 결함이 확인됐다.

| 사이트 | 결함 유형 | 원인 |
|---|---|---|
| dcinside(m/gall) | 본문 대신 **목록·공지·갤러리소개** 추출 | m.dcinside URL 이 데스크톱 UA 로 요청(변환 정규식이 gall 만 매칭) + trafilatura 제네릭 추출 |
| theqoo | 본문 꼬리 잘림 | `.xe_content` 첫 노드만 추출 |
| ppomppu | **짧은 본문을 버리고 댓글을 본문으로** 오추출 | trafilatura `favor_precision=True` 가 짧은 main content 를 보일러플레이트로 폐기 |
| fmkorea | 브레드크럼·HTML 태그·©푸터·nav | trafilatura 가 페이지 크롬 포함 |
| mlbpark | 제목접두 + 푸터(`mlbpark@donga.com`) | 동일 |
| milemoa | 제목접두 + affiliate 고지 | 동일 |
| 공통 | 본문 내 **raw URL·이미지 짤 URL·OG 링크/영상 미리보기 카드** | 렌더된 링크 프리뷰가 텍스트로 유입 |
| 공통 | **본문 부재(짤·목록·초단문)** 를 추출 실패와 구분 못 함 | empty vs fail 미구분 |
| ppomppu 댓글 | `"0 0 닉네임 날짜"` 스캐폴딩 | Playwright 스크래퍼가 투표수·닉네임·날짜를 text 에 포함 |
| 댓글 이중경로 | PoC 는 content_fetch, 노드는 community_comments 로 서로 다른 출처 | 정본 불일치 |

## 2. 근본 원인 요약

- **추출 실패(재fetch 필요)** — dcinside/theqoo/ppomppu: 크롤러가 **엉뚱한 DOM**(목록·댓글·공지)을
  추출. 사이트별 본문 컨테이너 셀렉터로만 해결 가능.
- **텍스트 노이즈(재fetch 불필요)** — clien/ppomppu/fmkorea/mlbpark/milemoa 는 추출은 정상이나
  크롬·URL·HTML·`[댓글]` 병합 잔재가 텍스트에 남음. 기존 데이터 텍스트 정제로 충분.
- **본문 부재 ≠ 추출 실패** — 짤·목록·초단문 글은 "추출할 텍스트가 없는 것"이지 실패가 아님.
- **길이 임계 설정은 없음** — ppomppu 오추출은 코드의 길이 스킵이 아니라 trafilatura 휴리스틱.

## 3. 해결책

### 3.1 프로덕션 노드 변경 (`server/graph/nodes/community_collection_node.py`)

- `_DC_MOBILE_RE` 추가 + `_fetch_target_v14`: **m.dcinside 도 모바일 UA 로 정규화**, **ppomppu
  www/m URL 을 m 으로 정규화**(`.viAr` 컨테이너 일관).
- `_SITE_SELECTORS`(신설): 사이트별 본문 컨테이너 셀렉터
  - dcinside: `.thum-txtin` (외 폴백) — 실측 `--find` 확정
  - theqoo: `.rd_body` 우선(꼬리 잘림 해소)
  - ppomppu: `.viAr` — 실측 `--find` 확정(짧은 본문도 길이 무관 채택)
- `_extract_site_specific`(신설): 셀렉터 우선 추출 → 실패 시 trafilatura 폴백. 메타데이터
  전용 텍스트(IP·조회·추천·고정닉)는 거부. **길이 하한 없음**(초단문 "뭐가 남" 보존).
- `_sub_urls`(신설): **모든 URL → `[링크]`**(전 사이트). 짤-only 글은 치환 후 실질 텍스트 0 →
  empty_body 자동 분류.
- `_strip_embed_cards`(신설): **OG 링크/영상 미리보기 카드**(`[링크]\n제목·설명\n출처도메인`) 제거,
  `[링크]` 표식만 잔존.
- `_CHROME_RE` + `_real_body_len`(신설): 브레드크럼·푸터·제목접두·affiliate·`©에펨코리아` 푸터·
  `N군 메뉴` nav 를 제거한 **실질 본문 길이** 계산.
- `_ERROR_PAGE_SIGNS`: `취소된 게시물`·`종결된 게시물` 추가(ppomppu 삭제 안내 감지).
- `_collect_one`: **empty_body 구분** — 실질 본문 <1자면 `status="empty_body"`, `body=""`;
  <15자면 `body_is_thin=True`(본문 유지). empty_body 는 **제목·댓글이 신호이므로 레코드 유지**,
  실제 추출 실패는 기존대로 제외.

### 3.2 댓글 출처 단일화

PoC(`scripts/poc_opinion_units_community.py`)의 `_load_comment_cache` 를 **content_fetch →
community_comments(Playwright)** 로 전환. 프로덕션 노드와 동일 정본 사용(이중경로 해소).

## 4. 처리 스크립트

| 스크립트 | 역할 | 네트워크 |
|---|---|---|
| `scripts/community_extract_fix.py` | `--url` before/after 하니스 · `--inspect`/`--find` DOM 진단 | 필요 |
| `scripts/reextract_dcinside_theqoo.py` | dcinside/theqoo/**ppomppu** 재fetch+셀렉터 추출 → content_fetch 캐시 패치 · fetch 가능비율 출력 · 백업 | 필요 |
| `scripts/postclean_community_bodies.py` | clien/fmkorea/mlbpark/milemoa **텍스트 정제**(재fetch 없음) → content_fetch 캐시 패치 · 백업 | 불필요 |
| `scripts/clean_comment_scaffolding.py` | ppomppu 댓글 `"0 0 닉네임 날짜"` 스캐폴딩 제거 → community_comments 캐시 패치 · 백업 | 불필요 |
| `scripts/patch_state_bodies.py` | 3개 리포트 병합 → `reaction_state.json` body 외과적 동기화 · 백업 | 불필요 |

- 모든 패치 스크립트는 **dry-run 기본 · `--apply` 시 `./backup` 에 폴더/파일 백업** 후 반영.
- reextract 는 `--report` 로 산출 리포트 분리(dcinside/theqoo ↔ ppomppu).

## 5. 데이터 계층 & 패치 내역

두 계층을 모두 반영: **① 캐시**(프로덕션 파이프라인이 읽음) · **② `reaction_state.json` 스냅샷**
(테스트가 읽음).

| 패치 | 대상 계층 | 백업 |
|---|---|---|
| dcinside/theqoo body 재수집 | content_fetch 캐시 | `backup/community_content_fetch_20260728_180710` (298파일) |
| ppomppu body 재수집(.viAr) | content_fetch 캐시 | `backup/community_content_fetch_20260728_194945` |
| clien/fmkorea/mlbpark/milemoa 노이즈 제거 | content_fetch 캐시 | `backup/community_content_fetch_postclean_20260728_201621` |
| ppomppu 댓글 스캐폴딩 제거 | community_comments 캐시 | `backup/community_comments_20260728_201944` |
| 7개 사이트 body 스냅샷 동기화 | reaction_state.json | `backup/reaction_state_20260728_202733.json` |

병합 통계: 개선 body **478 URL**(dcinside 66 + theqoo 70 + ppomppu 61 + clien 100 + fmkorea 73 +
milemoa 70 + mlbpark 38), empty_body 29, 삭제 1(ppomppu 102105 제외).

## 6. 검증 결과

- **dcinside/theqoo/ppomppu**: 목록·공지·댓글 오추출 → 실제 OP 본문 복구(예: ppomppu 474922
  "앱에서 안나오네요?", 471002 칸쿤 여행 질문). 삭제글(102105)은 `deleted` 로 제외.
- **fmkorea/mlbpark/milemoa**: 브레드크럼·푸터·HTML·affiliate 제거, 짤/footer-only 글은 empty_body.
  `©에펨코리아` 푸터·`N군 메뉴` nav 추가 정제(잔존 "에펨코리아" 6→1, 남은 1은 실제 "공식 공지" 본문).
- **clien/ppomppu 실본문 보존**, URL→`[링크]`, 메타데이터 0.
- **ppomppu 댓글 843건 스캐폴딩 잔존 0**, 타 도메인 불변.
- **state 잔존 6건은 실제 노이즈 아님**: HTML 5 = ppomppu 본문의 리터럴 `<>`(사용자 `<순서>`·뉴스
  `<ⓒ…>`), `[댓글]` 1 = 삭제글 102105(패치 제외).

## 7. 프로덕션 일관성 (신규 수집분 적용)

초기엔 텍스트 크롬 제거·댓글 스캐폴딩 정제가 **오프라인 스크립트(캐시 패치)에만** 있었으나,
신규 수집분(새 URL·캐시 TTL 만료 후 재fetch)에도 적용되도록 **노드에 통합 완료**(2026-07-28):

- `_clean_body_text(body, title)`: 제목접두 → 크롬(`_CHROME_RE`) → HTML 순 정제. `_collect_one`
  에서 status=ok 본문에 적용. selector 사이트(dcinside/theqoo/ppomppu)는 no-op, trafilatura
  사이트(fmkorea/mlbpark/milemoa/clien)의 크롬·HTML 제거.
- `_clean_comment_text(text)`: 댓글 스캐폴딩('투표수·닉네임·날짜') 제거. `_load_cached_comments`
  (노드 read-time)에 적용 → community_comments 재수집분도 자동 정제.

## 8. 한계 / 후속

- ppomppu 삭제글(102105) 스냅샷 body 는 옛 값 유지(패치 제외) — 필요 시 별도 empty 처리.
- 댓글 정본을 community_comments 로 단일화했으나 content_fetch.comments 잔재 정리는 item 4 에서.

## 9. 다음 단계 (예정)

- **item 3+4**: 의견 unit 추출에 **제목을 인용 가능한 멤버(t0)로 추가** + **스레드 내 dedup 규칙**
  (target×aspect×polarity) + 게이트 **title-override**. `scripts/test_title_member_units.py`
  (site×candidate 균등 표본 ~120, YouTube PoC 규모)로 검증.
- **도메인 게이트 개선 완료**: 정제된 body/댓글로 게이트 재실행 → 컷률·미탐 재측정.
