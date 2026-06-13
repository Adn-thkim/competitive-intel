# 커뮤니티 데이터 수집 확장 설계 — broad query + 2단 화이트리스트

> - **상태**: DRAFT — 사용자 제안(2026-06-12) 기반, 검토 후 확정 예정
> - **작성일**: 2026-06-12
> - **선행 문서**:
>   - `docs/design/reaction_insight_node_design.md` (RI-D1 검색하지 않는 수집 노드 · RI-D4 선별 상한 · RI-D10 잡담 필터)
>   - `docs/design/pipeline_topology_redesign.md` §6-6a (community_collection · D11 robots/rate limit 정책)
>   - `_feature_mapping_runner.py` 의 `_carry_owned_channels` (D45a — LLM 생략 carry-through 선례)
> - **대상 파일**: `server/graph/nodes/url_discovery_blog_community_node.py`,
>   `server/graph/nodes/community_collection_node.py`,
>   `server/graph/nodes/_feature_mapping_runner.py` (blog_community 분기),
>   `agents/domain_modeling/system_prompt_kr.md` + `output.schema.json`,
>   `data/community_registry.json` (신규), `server/config.py`
> - **범위 외**: blog 계열(personal_blog·review_site·wiki) 수집 — `blog_collection_node`(휴면) 별도.
>   YouTube 표본 증설(상수 조정·댓글 페이지네이션)과 ABSA target 재귀속·배치 분할 — 별도 PR
>   (본 문서 §7에서 의존성만 명시).

---

## 1. 배경 — 실측 문제

파일럿(토스 트래블카드, candidate 4) 실측:

| 채널 | 표본 | 비고 |
|---|---|---|
| YouTube 댓글 | candidate당 121~150건 | 정상 동작 |
| **커뮤니티 게시글** | **candidate당 0~1건** | 채널 사실상 부재 — 루브릭 4점(2채널 교차) 미충족 위험 |

원인 분해 (코드 실사 + 검색 실측):

1. **feature 단위 검색어의 리콜 붕괴** — Google 실측: `site:clien.net 토스 트래블카드` 67건
   (무관 ~10건) vs `site:clien.net 토스 트래블카드 후기` 9건. 현행 hint 쿼리는 feature
   키워드까지 포함해 더 좁다. 그러나 ABSA가 aspect 단위로 **분석 시점에 주제를 할당**하므로
   검색 단계에서 주제를 좁힐 필요가 없다.
2. **일반 Brave 쿼리에서 커뮤니티 글이 상위에 안 잡힘** — `site:` 한정 없이는 블로그·뉴스가
   상위를 점유.
3. 수집 실패 — SPA(네이버 계열)·robots — 기존 D11 정책 범위.

목표: **candidate당 커뮤니티 게시글 20건 이상 + 댓글 포함 표본 수십~수백 건**,
히트맵 community tuple 공백 해소, 루브릭 4점 안정 충족.

---

## 2. 결정사항 (CE-D1 ~ CE-D8)

### CE-D1 — broad query: `site:{community_domain} {candidate_alias}`

feature·aspect 키워드를 검색어에 넣지 않는다. 근거: §1-1 리콜 실측 + aspect 늦은 할당 구조.
candidate_alias 는 product_profiles 의 정식 명칭 1개로 시작 (변형 별칭 확대는 실측 후 —
쿼리 수 × 비용 통제).

### CE-D2 — 2단 화이트리스트

- **1군 (고정, 도메인 무관)**: clien.net · ppomppu.co.kr · mlbpark.donga.com ·
  fmkorea.com · theqoo.net · dcinside.com — 기존 `_BLOG_COMMUNITY_COMMUNITIES` 6개 유지.
- **`collection_mode` 필드 (사용자 확정 2026-06-12)**: 1군 상수는 `{domain:
  collection_mode}` dict 로 정의하고, 수집 노드가 이 값으로 분기한다 —
  `full`(본문+댓글: clien · dcinside[모바일 변환+모바일 UA 필수]) ·
  `body_only`(ppomppu[CE-D6 2단계 파서 구현 시 full 승격] · theqoo[확정 — 댓글 동적
  로딩]) · `snippet_only`(fmkorea·mlbpark, CE-D10). 실측 근거는 CE-D7 표.
- **2군 (도메인 특화, 0~2개)**: `domain_modeling` 이 taxonomy 에 `community_sites` 필드로
  선정. **자유 생성 금지** — `data/community_registry.json` 의 큐레이션 목록에서만 선택
  (LLM 환각 도메인 차단). 레지스트리 항목: `{domain, label, topics[], crawl_note,
  collection_mode}` (신규 등재 시 측정 스크립트로 mode 실측 후 기재).
  초기 등재: milemoa.com(카드·마일리지), bobaedream.co.kr(자동차), quasarzone.com ·
  coolenjoy.net(PC·하드웨어), pann.nate.com · instiz.net · 82cook.com(여초·생활 소비),
  ruliweb.com · inven.co.kr(게임), dogdrip.net(종합). taxonomy 7일 TTL 캐시가 그대로 적용.

### CE-D3 — feature 매핑 제거, placeholder carry-through

커뮤니티 URL 은 feature 단위 LLM 매핑을 하지 않는다 (리포트가 aspect 축이므로 무가치).
파이프라인 호환은 D45a 패턴으로 유지: `run_source_mapping(source="blog_community")` 에
community 분기를 추가해 LLM 없이 단일 placeholder feature 로 변환한다.

```python
{
  "report_type":  "reaction_insight",
  "feature_id":   "feat_community_reactions_pool",   # placeholder
  "feature_name": "커뮤니티 반응 풀",
  "candidate_coverage": [
    {"candidate_id": cid, "coverage": <건수 임계 규칙>,
     "existing_urls": [...], "additional_urls": []}
  ],
}
```

- coverage 규칙(결정론): URL ≥ 10 → sufficient, ≥ 3 → partial, else not_found.
- `feature_selection` UI 는 이 placeholder 를 커뮤니티별로 "{커뮤니티 alias} 수집 예정 N건" 요약 카드로 표시.
- `select_community_urls` 는 placeholder 에 한해 `selected_feature_ids` 게이트를 적용하지
  않고 **report 게이트(reaction_insight ∈ selected_purposes)만** 적용한다.
  (이유: broad 수집 풀은 feature 단위 선택 대상이 아님. 사용자 통제는 리포트 선택으로 유지.)
- **blog_community source 의 LLM 경로는 전면 제거한다** (2026-06-12 사용자 확정 — blog
  계열은 수집하지 않음). 본 source 의 LLM 매핑은 reaction_insight 전용인데 활성 소비자가
  community_collection 뿐이므로, blog 계열 URL 매핑은 소비 노드 없는 죽은 비용이다.
  `run_source_mapping("blog_community")` 는 carry-through 전용이 되며, domain_class 가
  blog 계열인 URL 은 placeholder 에 포함하지 않는다. blog_collection 재활성 시에도
  LLM 매핑 복원이 아니라 동일 placeholder 방식 확장을 권장.

### CE-D4 — 관련성 필터: 규칙 기반, LLM 없음 (완화 모드 — 제외 조건으로만 동작)

§1 실측상 무관 글은 소수(~15%)이며 대부분 "동일 브랜드의 다른 상품" 류.

- 판정 입력: Brave 결과의 title + description(스니펫). 본문 fetch 전에 적용 (rate limit 절약).
- **키워드 소스**: aspect_codebook 의 label + definition 에서 명사 토큰 추출, 범용어
  (앱·비용·혜택·품질·경험·편의성 등) 제거 + 도메인 상품 토큰(domain_name·feature_labels).
  파일럿 실측 토큰: 해외결제·환율·환전·충전·외화·ATM·출금·수수료·한도·분실·도난·잠금·
  보험·라운지·고객센터 + 트래블카드·트래블.
- **규칙 (제외 조건으로만)**: 토큰이 **하나도 없을 때만** 제외, 1개 이상이면 통과.
  경계 사례는 전부 통과 — 최종 거름망은 ABSA (RI-D10 과 동일 사상: aspect 미배정
  텍스트는 tuple 0건으로 자연 탈락). 과대 필터링이 과소 필터링보다 위험 (사용자 확정
  2026-06-12).
- 타 candidate 언급 글은 **제거하지 않는다** — 비교글은 고가치 표본 (§7 target 재귀속 의존).

### CE-D5 — candidate 귀속은 잠정 메타

broad query 는 비교글을 다수 수집하므로 게시글의 candidate 단일 귀속은 부정확하다.
`matched_candidates: [cid, ...]` (해당 글을 발견한 쿼리들의 candidate 합집합) 로 보존하고,
ABSA 입력 시 잠정 cid 로 투입하되 **최종 귀속은 7-tuple `target_candidate_id`** (별도 PR,
§7) 가 담당한다. 동일 URL 이 복수 candidate 쿼리에서 발견되면 **1회만 수집** (URL dedup)
하고 matched_candidates 를 병합한다.

### CE-D6 — 댓글 수집: 단계적

커뮤니티 댓글은 사이트별 DOM 이 달라 전용 파서가 필요하다 (유지보수 부담).

- **1단계 (본 설계)**: Trafilatura `include_comments=True` 일반 추출만. 본문+댓글이 한
  텍스트로 적재되어도 ABSA item 1건으로 취급 (게시글 = item).
- **2단계 (실측 후)**: 댓글 누락률이 높은 상위 1~2개 사이트만 전용 파서 추가. 선행 조건:
  §6-1 측정에서 "본문만으로 표본 부족" 확인.
- **2단계 대상 확정 (probe 실측 2026-06-12, `scripts/probe_comments.py`)**:
  ppomppu **1순위** — 댓글이 HTML 에 서버 렌더링(작성자·추천·본문·날짜 규칙 구조,
  글당 최대 77개 노드 확인)되어 있으나 trafilatura 휴리스틱이 미탐지 → BeautifulSoup
  셀렉터 파서로 수집 가능. clien·dcinside(모바일)는 1단계 일반 추출로 충분.
  theqoo 는 동적 로딩 확정으로 대상 제외.

### CE-D7 — Brave 리콜 측정 게이트 (구현 전 필수)

§1 실측은 **Google** 기준이다. Brave 의 한국 커뮤니티 인덱스 커버리지는 미검증이며,
67건이 Brave 에서 한 자릿수일 수 있다 — 본 설계의 최대 리스크.

- 구현 착수 전 측정 스크립트로 `site:{1군 6개} × {파일럿 candidate 4}` 24개 쿼리의
  Brave 결과 수를 실측한다. **측정 단계에서는 페이지네이션 무제한**(offset 최대 9 =
  쿼리당 최대 10페이지·200건)으로 candidate별 수집 가능 URL 총량을 확인하고, 그 결과로
  운영 페이지 상한을 확정한다 (사용자 확정 2026-06-12).
- **스크립트**: `scripts/measure_brave_recall.py` — 운영 코드와 분리된 일회성
  (사용자 확정 2026-06-12. 운영 의존은 config 의 API 키 1개로 최소화).
- 측정 기록 항목: ① 사이트(community domain별 분리)×candidate 결과 수 표,
  ② **페이지별 결과 수·발행일 분포** (후순위 페이지 품질 검증),
  ③ 게시글 글자수 분포 (§3-4 chunk 상한 근거),
  ④ 댓글 수 분포 (Trafilatura 일반 추출 한계로 문단 수 기반 **근사치** — 정밀 카운트는
  CE-D6 2단계 전용 파서 영역), ⑤ 스니펫 기반 필터 판정 vs 본문 기준 실제 관련성
  오제외율 표본 검사 20건 (제외분 전수 우선 + 통과분 보충, manual_label 수동 기입).
- **다중 candidate 게시글 표기 (3층)**: 메인 표는 발견 기준 — 2개 이상 candidate 쿼리에서
  발견된 게시글은 **각 candidate 셀에 모두 집계** (중복 허용, 리콜 왜곡 방지). 보조 행에
  사이트별 `고유 URL 수` + `다중 발견 수`(≥2 쿼리 발견, CE-D5 matched_candidates 대응)
  병기 — 발견 합계 − 고유 URL = 중복 발견량. fetch 표본 한정으로 본문 alias 매칭 기반
  `언급 candidate 수 분포(1/2/3+)`를 별도 산출 (target 재귀속 PR 우선순위의 정량 근거).
- 판정: 사이트×candidate 평균 ≥ 10건 → 본 설계 진행. 미달 → 커뮤니티 내부 검색 크롤
  fallback 검토 (보류 — robots·ToS 개별 검토 필요, 본 문서 범위 외).

**실측 결과 (2026-06-12, 파일럿 candidate 4 × 1군 6)**

- 리콜 게이트 **통과**: 고유 게시글 501건 (필터 통과 171건 ≈ candidate당 43건 — 선별
  상한 40 충족). 다중 발견 26건 (2개 18 · 3개 8).
- **Brave 페이지 깊이 한계 = 6페이지(120건) 포화** + 페이지 간 동일 URL 반복(셀당 실효
  고유 ~40건) → 운영 페이지 상한 6 확정, CE-D8 dedup 이 페이지 간 반복을 반드시 흡수.
- 스니펫 필터 제외율 66% (Google 예상 ~15% 대비 높음 — site: 잡음·느슨한 토큰 매칭).
  잠정 오제외 6건은 수동 검토 결과 **전원 진짜 무관 글** (excerpt 의 메뉴 텍스트는 측정
  스크립트의 trafilatura UA 결함 — 수정 완료) → **fetch 전 필터 위치 확정**.
- **사이트별 수집 모드 확정 (재측정 2026-06-12, 브라우저 UA + favor_precision 적용 후)**:

| 사이트 | 본문 | 댓글 (일반 추출) | 운영 수집 모드 |
|---|---|---|---|
| clien.net | ✅ 95~1,747자 | ✅ 최대 80개·5,580자 (대댓글 HTML 포함) | **full** (본문+댓글) |
| ppomppu.co.kr | ✅ 158~891자 (모바일 URL) | ⚠️ HTML 에 서버 렌더링 존재 확인(probe 2026-06-12) — trafilatura 휴리스틱만 실패 | **body_only** → CE-D6 2단계 전용 파서 구현 시 **full 승격** (1순위) |
| theqoo.net | ✅ 83~1,253자 | ❌ **동적 로딩 확정**(probe: 댓글 527개 글의 HTML 33k자 — 본문 댓글 부재, 개수 헤더만 존재) | **body_only 확정** — 댓글 XHR 직접 호출은 ToS 기준(CE-D10 과 동일)으로 보류 |
| dcinside.com | ✅ 25~533자 (m.dcinside 변환 + 모바일 UA — 3차 재측정 2026-06-12 검증. gall PC 직접 접근은 보일러플레이트) | ✅ 최대 25개·1,050자 | **full** — 단, 수집 노드에 모바일 URL 변환 + 모바일 UA 필수 |
| fmkorea.com | ❌ robots 차단 | ❌ | **snippet_only** (CE-D10-a) |
| mlbpark.donga.com | ❌ robots 차단 | ❌ | **snippet_only** (CE-D10-a) |

- 오제외율 최종 0/20 — 재측정의 잠정 오제외 2건(전기차 "충전" 다의어 매칭)도 수동 검토
  결과 진짜 무관 글. **CE-D4 필터의 fetch 전 위치·완화 모드 확정.**

### CE-D9 — community 경로의 cross_reference 우회 (사용자 확정 2026-06-12)

cross_reference 는 youtube_reactions × owned_channels(youtube_official) 필터 전용이므로
community URL 에 실질 역할이 없다. 엣지 변경:

- `url_discovery_blog_community → feature_mapping_blog_community` 직결 (barrier 비대기)
- cross_reference fan-in barrier: 5-in → **4-in** (blog_community 제외)
- cross_reference 의 blog_community 키 carry 제거

효과: carry-through(LLM 0회)가 다른 4개 discovery 완료를 기다리지 않음 — 병렬성 소폭 개선.

### CE-D10 — robots 차단 사이트(fmkorea·mlbpark) 대응: snippet_only 강등 (a 채택 — 사용자 확정 2026-06-12)

실측(2026-06-12): 두 사이트는 robots.txt 요청에 401/403 — D11 정책상 전면 차단 판정.
추가 검증: fmkorea 는 robots.txt 요청 자체가 응답 보류(타임아웃) — 봇 차단 계층 추정.
UA 위장 강화·헤드리스 브라우저·IP 로테이션 등 우회는 기술적으로 가능하나 **자체 정책
(D11 robots 준수) 위반 + ToS 리스크로 비권장**. 양 사이트 공식 API·RSS 부재.

- **대응 a (권장)**: snippet_only 강등 — Brave 가 이미 반환한 title+snippet 을 본문
  fetch **없이** ABSA item 으로 사용 (사이트 방문 0회 — robots 준수와 충돌 없음).
  item 에 `fetch_status="snippet_only"` 마킹. 발견량(fmkorea 399·mlbpark 264) 을
  버리지 않음. 한계: 텍스트 2~3줄·quote 짧음 → 채널 가중치 하향 검토(예: 0.7).
- **대응 b**: 1군 제외 + 2군 registry 후보(pann.nate.com·82cook.com 등) 크롤 가능성을
  측정 스크립트로 검증 후 1군 보충.
- **채택 (사용자 확정 2026-06-12)**: a — fmkorea·mlbpark 는 snippet_only 로 운영.
  b(1군 보충 측정)는 선택 과제로 보류.
- **top_quotes 제외 정책 (사용자 확정 2026-06-12)**: snippet_only 유래 tuple 은
  **집계 전용** — aspect_matrix·timeline 의 건수·감성 수치에는 100% 반영하되,
  `select_top_quotes()`·`build_suggestions()` 후보에서 제외한다 (스니펫은 문장 중간
  절단·맥락 부재로 대표 인용 부적격). 구현: item `fetch_status="snippet_only"` →
  ABSA tuple 전파 → 두 함수 진입부 필터. suggestions 포함 절충안은 보류 (필요시 재검토).

### CE-D8 — URL 정규화 dedup

dedup 키는 정규화 URL: 스킴·www 제거, 모바일 변형 통일(`m.dcinside.com` ↔
`gall.dcinside.com` 등 사이트별 규칙), 추적 파라미터(utm_* 등) 제거.

---

## 3. 수집 파이프라인 (사용자 제안 1~6 의 수정 확정안)

| #   | 단계                                                   | 책임                                         | 사용자 제안과의 차이                                    |
| --- | ---------------------------------------------------- | ------------------------------------------ | ---------------------------------------------- |
| 1   | `site:{domain} {candidate_alias}` 검색 (1군 6 + 2군 0~2) | url_discovery_blog_community (broad 모드 신설) | 동일                                             |
| 2   | 결과 병합 + URL 정규화 dedup + matched_candidates 병합        | 〃                                          | CE-D5·D8 구체화                                   |
| 3   | 관련성 필터 (title+스니펫 규칙, fetch 전)                       | 〃                                          | **feature url mapping 아님** (CE-D3·D4) — LLM 0회 |
| 3a  | placeholder carry-through → analysis_features        | _feature_mapping_runner (community 분기)     | 신설 (파이프라인 호환층)                                 |
| 4   | 본문 수집 + **문장 경계 chunking** (3,000자 단위, 게시글당 최대 3 chunk, 동일 source_url 공유 → ABSA item 1~3건) | community_collection (D11 유지)              | **요약·단순 절단 모두 금지** (사용자 확정 2026-06-12) — 정보 손실 0 + quote 실존 검증 보존. 절대 상한(3 chunk)은 초장문·도배 방어용 |
| 5   | 댓글: Trafilatura 일반 추출 (1단계)                          | 〃                                          | 전용 파서는 실측 후 (CE-D6)                            |
| 6   | 사이트(host)별 직렬 + host 간 병렬 3                          | 〃                                          | rate limit 1s/fetch 는 host 단위로 유지              |

선별 상한 (RI-D4 보완 개정): 사이트당 candidate당 10건(최신순) → candidate당 총 40건.
feature greedy 선별은 폐기 (feature 축 부재), 사이트 다양성 우선 round-robin 으로 대체.

---

## 4. 파일별 변경점

| 파일 | 변경 | 규모 |
|---|---|---|
| `data/community_registry.json` | 신규 — 2군 큐레이션 목록 | 데이터 |
| `agents/domain_modeling/*` | `community_sites`(0~2, registry 도메인 enum) 필드 추가 | 프롬프트+스키마 |
| `url_discovery_blog_community_node.py` | broad 모드(site: 쿼리·offset 페이지네이션 최대 3p·dedup·관련성 필터)로 **대체**. 기존 hint 쿼리 폐기 (blog 비활성으로 소비 노드 부재 — Brave ~20회/실행 절감) | 중 |
| `_feature_mapping_runner.py` | `blog_community` 분기 전면 대체: LLM 경로 제거 → placeholder carry-through (D45a 패턴). `agents/feature_mapping_blog_community/` 휴면 처리 | 소 |
| `community_collection_node.py` | 선별 로직 교체(round-robin)·`_BODY_CAP_CHARS` 절단·host 병렬 3 | 중 |
| `feature_selection_node.py` | placeholder → "커뮤니티 수집 예정" 요약 카드 | 소 |
| `config.py` | `COMMUNITY_SITES_FIXED`, chunk 상수(`_CHUNK_CHARS=3000`·`_MAX_CHUNKS=3`), 선별 상한 상수 | 소 |
| `graph.py` | CE-D9 엣지 변경: blog_community 직결 + cross_reference barrier 4-in 축소 | 소 |

---

## 5. 비용·시간 추정 (파일럿: candidate 4, 1군 6 + 2군 2)

| 항목 | 추정 | 근거 |
|---|---|---|
| Brave 호출 | 기본 8 사이트 × 4 candidate = 32회. 페이지 2·3 은 직전 페이지 20건 만석 시에만 추가 → 상한 ≤96회. 실측 기대 32~50회 ≈ **$0.16~0.25/실행** | $5/1,000 요청, 월 $5 무료 크레딧 내. 7일 검색 캐시로 재실행 0원 |
| 본문 fetch | ≤160건 × 1s ÷ host 병렬 3 ≈ **1~2분** | D11 rate limit, 24h 전문 캐시 |
| LLM 호출 증가 | **0회** (필터 규칙 기반 + carry-through) | CE-D3·D4 |
| ABSA 입력 증가 | candidate당 +20~40 item | 배치 분할 PR(§7)과 합산 설계 필요 |
| YouTube quota | 영향 없음 | — |

---

## 6. 구현 순서 (목표 주도 — 단계별 검증 기준)

1. **Brave 리콜 측정 스크립트** (CE-D7) → 검증: 사이트×candidate 결과 수 표 산출,
   평균 ≥ 10건 확인. **미달 시 이후 단계 착수 보류.**
2. registry + taxonomy `community_sites` → 검증: 파일럿 taxonomy 재생성 시 milemoa.com 이
   2군으로 선정되고 registry 외 도메인이 나오지 않음.
3. discovery broad 모드 + dedup + 필터 → 검증: candidate당 필터 통과 URL ≥ 15,
   무관 글 비율 ≤ 20% (수동 표본 검사 20건).
4. carry-through + collection 개편 → 검증: candidate당 community_posts ≥ 10,
   fetch 성공률 ≥ 60%, 기존 graph 전체 실행 무중단.
5. 파일럿 end-to-end → 검증: 히트맵에서 community channel tuple 이 candidate 4 전원
   ≥ 5건, 루브릭 4점 이상.

---

## 7. 의존성·리스크·보류

- **의존 PR ①** — ABSA `target_candidate_id` 재귀속: CE-D5 의 잠정 귀속을 교정하는 전제.
  본 설계와 병행 권장 (비교글 비중이 커질수록 오귀속 절대량 증가).
- **chunk 도입의 통계 표기 (사용자 확정 2026-06-12)** — §3-4 chunking 으로
  `sample_size`(item 수)가 원 게시글 수보다 커진다. `reaction_analysis` 산출의
  `channel_meta` 에 **`post_count`(원 게시글 수) 필드를 추가**해 item 수와 구분 표기한다
  (UI 표본 뱃지는 post_count 기준 권장). 대상: `reaction_analysis_node.py`
  (집계) + `reaction_insight_node.py` (channel_meta carry).
- **의존 PR ②** — ABSA 배치 분할(~120 item/호출, 병렬 4): 커뮤니티+YouTube 표본 증설 합산
  시 단일 호출 context·timeout(600s) 초과.
- **리스크 1 (최대)** — Brave 한국 커뮤니티 인덱스 리콜 미검증 → CE-D7 게이트로 통제.
- **리스크 2** — fmkorea 반봇 차단(403 빈발 알려짐) → 수집 성공률 stats 를 노드가 이미
  집계하므로 사이트별 성공률 로그 누적, 2회 연속 0% 시 화이트리스트 제외 검토.
- **리스크 3** — 커뮤니티 어뷰징(동일인 반복 게시) → 현행 200자 prefix dedup 유지,
  강화(작성자 단위)는 보류 (작성자 식별정보 비저장 D11 원칙과 충돌).
- **보류** — 네이버 카페(검색 API + cafe.naver.com 화이트리스트), 커뮤니티 내부 검색 크롤,
  사이트별 댓글 전용 파서(CE-D6 2단계), candidate_alias 변형 확대.
