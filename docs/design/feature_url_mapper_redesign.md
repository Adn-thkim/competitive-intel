# Feature URL Mapper 재설계 — Source-Type 5분리 + 흐름 A 정합 + 검색 기반 채널 발견

> - **상태**: DRAFT — 사용자 검토 후 확정 예정
> - **작성일**: 2026-06-02 (v0.1 초안)
> - **대상 노드**: `feature_url_mapper_node` 4단계 분리(`url_discovery_brave_node`·`page_meta_collect_node`·`feature_mapping_llm_node`·`additional_urls_validation_node`) 전반과 후속 §6-6a 수집 노드 6종 인계 경계
> - **선행 문서**: `docs/design/pipeline_topology_redesign.md` (특히 §6-5 v0.10.13, §11-10 이원 흐름, §6-6a 수집 노드 6종)
> - **트리거**: v0.10.17 §6-5 "알려진 한계" 4건 + 파일럿 도메인(`핀테크 / 해외여행 특화 카드`) 실측 결과 + turn-3~5 분석 합의
>
> ### ⚠ Supersede 안내
>
> 본 문서는 `pipeline_topology_redesign.md` **§6-5 (v0.10.13 이후)** 의 후속 설계로, 다음 항목은 본 문서를 단일 진실원(SSOT)으로 참조합니다.
>
> - feature_url_mapper 노드 분리 단위(단일 → 4단계 → **5+1+1+1** 8개 노드 구조)
> - URL 탐색 노드의 source-type 분리 정책
> - `domain_taxonomy.report_config` 에 `source_flow` 메타데이터 추가
> - Owned channels 발견 방식(패턴 매칭 폐기 → Brave 검색 + LLM 검증)
> - SPA·동적 페이지의 page meta 한계 처방
>
> 향후 `pipeline_topology_redesign.md` §6-5 본문은 본 문서 참조로 단순화될 예정입니다.

---

## 1. 문서 목적과 범위

본 문서는 v0.10.17 시점에 진단된 `feature_url_mapper` 4가지 구조적 결함을 결정론적으로 해소하기 위한 토폴로지·노드 재설계를 정리합니다. 본 문서가 다루는 범위는 다음과 같습니다.

- `feature_url_mapper` 4단계 분리(v0.10.9~v0.10.13) 이후의 후속 분리 — Step 0(URL 탐색)을 source-type 단위로 5개 노드로 재분할
- `domain_taxonomy.report_config` 의 스키마 확장(`source_flow` 메타데이터 추가)으로 흐름 B-only 리포트의 URL 매핑 자동 제외
- Owned channels(자사·경쟁사 운영 SNS·블로그·보도자료) 발견 방식의 패턴 매칭 → Brave 검색 + LLM 검증 전환
- `feature_mapping_llm_node` 의 system_prompt 를 report_type 별 출처 정책 블록으로 분기
- `page_meta_collect_node` 의 정적 HTML 한계(SPA·동적 페이지) 처방 우선순위 정의
- `additional_urls_validation_node` 의 source-type 별 검증 분기
- `feature_selection_node` (interrupt #4) UI 에 source-type 별 coverage 표시 보강
- §6-6a 수집 노드 6종(미구현) 과의 책임 경계 사전 합의

본 문서가 **다루지 않는** 범위는 다음과 같습니다.

- LangGraph 전체 파이프라인 토폴로지·HITL #1~#4 흐름 — `pipeline_topology_redesign.md` 가 단일 진실원
- §11-10 흐름 A·B 의존 관계 모델 자체 — 본 문서는 §11-10 의 흐름 A 4개 리포트에 대해서만 적용
- §6-6a 수집 노드 6종의 내부 구현 상세 — 본 문서는 인계 경계만 명시, 내부 구현은 별도 PR

---

## 2. AS-IS — v0.10.17 시점의 흐름과 결함

### 2-1. 현재 노드 구성과 흐름

`feature_url_mapper` 는 v0.10.9 부터 4개 노드로 분리되어 있습니다.

```
ab_join (분기 A·B fan-in)
  ↓
url_discovery_brave_node          (Step 0 — 7종 리포트 단일 Brave 검색)
  ↓
page_meta_collect_node            (Step 1 — 정적 HTML <title> + <meta description>)
  ↓
feature_mapping_llm_node          (Step 2 — report_type 별 병렬 LLM, 단일 system_prompt)
  ↓
additional_urls_validation_node   (Step 3 — HEAD/GET 단순 검증)
  ↓
feature_selection_node (#4)
```

### 2-2. 진단된 결함 4건

| # | 결함 | 위치 | 영향 |
|:-:|---|---|---|
| 1 | SPA·동적 페이지 본문 식별 불능 | `page_meta_collect_node._fetch_meta` (정적 GET + `<title>`/`<meta>` 만) | SPA 사이트(예: `travel-wallet.com`) 의 `partial`/`not_found` 비율 100% |
| 2 | 외부 출처가 LLM 에게 신뢰받지 못함 | `agents/feature_url_mapper/system_prompt_kr.md` §3·§5 ("동일 도메인 sub-path 만 추정") | reaction_insight 의 `additional_urls` 가 공식 sub-page 만 추정 → 대부분 ✗ 404 |
| 3 | 파일럿 실측에서 comp_* 의 외부 후기 미수집 | `domain_modeling` 의 `search_query_hints` 자사 편향(own_only=4, both=1, comp_only=1) | reaction_insight comp_* `coverage="not_found"` 비율 75% |
| 4 | B-only 리포트(positioning_map·executive_summary) 까지 URL 매핑 강제 수행 | `_extract_active_reports` 가 `source_flow` 미확인 | LLM 호출 4배 증가 + UI 노이즈 + 사용자가 의미 없는 항목 선택 |

본 결함의 원인 위치는 코드·프롬프트·설계 차원에 분산되어 있으며, 단일 함수 수정으로 해소되지 않습니다.

### 2-3. 본 노드들이 동시에 source-type-blind 한 구간

| 단계 | source-type 차별화 | 본 재설계 후 |
|---|:-:|:-:|
| `url_discovery_brave_node` | ✗ (모든 source 동일 Brave 패턴) | ✓ (5개 source-type 노드로 분리) |
| `page_meta_collect_node` | ✗ (HTML 만) | △ (1차 정합 머지 + heading/body snippet 보강) |
| `feature_mapping_llm_node` | △ (report_type 병렬은 됨, 룰은 동일) | ✓ (system_prompt 정책 분기) |
| `additional_urls_validation_node` | ✗ (HEAD/GET 만) | ✓ (source-type 별 검증 분기) |

source-type 차별화가 필요한 4개 단계 모두에서 일률 처리가 일어나고 있음이 본 점검의 핵심 결론입니다.

---

## 3. TO-BE — 목표 토폴로지

### 3-1. 5개 source-type URL 탐색 노드 + 후속 머지·매핑·검증

```
ab_join
  │
  ├─→ url_discovery_official_node              ┐
  ├─→ url_discovery_blog_community_node        │
  ├─→ url_discovery_youtube_reactions_node     │  5중 fan-out (1차)
  ├─→ url_discovery_owned_channels_node        │  (LangGraph list-edge barrier)
  └─→ url_discovery_macro_node                 ┘
                  │
                  ↓  builder.add_edge([5 url_discovery 노드], "cross_reference")
                  ▼
         cross_reference_node                  (youtube_reactions × owned_channels(youtube_official) 결정론적 후처리 필터링)
                  │
                  ▼
  ┌─→ feature_mapping_official_node            ┐
  ├─→ feature_mapping_blog_community_node       │
  ├─→ feature_mapping_youtube_reactions_node    │  5중 fan-out (2차)
  ├─→ feature_mapping_owned_channels_node       │  각 노드 내부: page meta 수집 → report_type 별 병렬 LLM 매핑
  └─→ feature_mapping_macro_node                ┘
                  │
                  ↓  builder.add_edge([5 feature_mapping 노드], "additional_urls_validation")
                  ▼
         additional_urls_validation_node       (단일, source-type 별 validator 함수 분기)
                  ▼
         feature_selection_node (#4)
```

### 3-2. 노드 수 변화

v0.10.9: 4개 (`url_discovery_brave` + `page_meta_collect` + `feature_mapping_llm` + `additional_urls_validation`)

본 재설계 (turn-11 옵션 (e) 채택): **13개**
- 5개 URL 탐색 노드 (`url_discovery_official` · `url_discovery_blog_community` · `url_discovery_youtube_reactions` · `url_discovery_owned_channels` · `url_discovery_macro`)
- 1개 cross-reference 노드 (`cross_reference_node` — `youtube_reactions × owned_channels(youtube_official)` 결정론적 필터링)
- 5개 통합 feature mapping 노드 (`feature_mapping_official` · `feature_mapping_blog_community` · `feature_mapping_youtube_reactions` · `feature_mapping_owned_channels` · `feature_mapping_macro` — 각 노드 내부에 page meta 수집 + LLM 매핑 직렬 수행)
- 1개 validation 노드 (`additional_urls_validation_node` — 단일, source-type 별 validator 함수 분기)
- 1개 feature selection (`feature_selection_node` — interrupt #4, 기존 유지)

**옵션 (d) — page_meta 와 feature_mapping 별도 노드(18개)** 대비 5개 통합으로 13개로 응집. 옵션 (e) 채택 근거는 (a) source-type 책임 응집, (b) `compiled_graph.draw_ascii()` 가독성, (c) §6-6a 인계 경계의 source-type 단위 명확화, (d) 캐싱은 통합 노드 내부에서 page_meta · LLM 별도 호출로 결정론성 동일 유지.

### 3-3. 호출 횟수 영향

| 항목 | v0.10.9 | 본 재설계 (옵션 e) |
|---|:-:|:-:|
| Brave API 호출 (cache miss) | 7종 리포트 × N hints × M candidates | 5개 URL 탐색 노드 × source-type 별 hints × M candidates (합산 동일하거나 감소) |
| YouTube Data API 호출 | 0 | reaction_insight 3rd-party 영상 검색(`url_discovery_youtube_reactions_node`) + owned channels 의 YouTube 공식 채널 식별(`channels.list` 1u/candidate) — 약 1,600~2,000 units/일일 (cache miss 첫 실행) |
| LLM 호출 (URL 매핑) | report_type 별 7회 (`parallel=4`) | **5개 통합 노드 × 자기 source 가 담당하는 report_type 1~3종 = 합 8회**. 각 노드 내부 ThreadPoolExecutor 병렬. wall-clock 약 50% 단축 (LLM 단계 가장 무거운 single source 가 official 3 report = 1 배치 ≈ 17분) |
| LLM 호출 (Owned channel 검증) | 0 | candidate × platform 5종(Instagram·X·블로그·보도자료·**YouTube 공식 채널**) 별 1회 (신규) |
| LLM cache_input 결정론성 | 단일 A-4 키 | **5개 source-type 별 분산** — 부분 hit 활용 가능 (한 source 결과만 변경 시 다른 4개 source 의 LLM cache 그대로 hit) |

본 재설계는 LLM 호출 수를 약간 늘리는(7회 → 8회) 대신 wall-clock 50% 단축 + source-type 별 cache 부분 hit + source-type 별 system_prompt 단순화 세 가지 이득을 동시에 확보합니다.

---

## 4. 핵심 변경 의도 (turn-3 ~ turn-5 결정 통합)

### 4-1. P0 — `source_flow` 메타데이터 도입 (B-only 리포트 자동 제외)

§11-10 흐름 A·B 모델에 따르면 7종 리포트 중 일부만 자체 데이터 수집(흐름 A) 이 필요합니다.

| 리포트 | 흐름 분류 | 본 노드 영역 |
|---|:-:|:-:|
| comparison_matrix | A | ✓ |
| reaction_insight | A | ✓ |
| marketing_social | A | ✓ |
| battlecard | A+B | ✓ (A 부분만) |
| positioning_map | B only | ✗ |
| market_context_swot | A+B | ✓ (A 부분만 — 매크로) |
| executive_summary | B only | ✗ |

현재 `domain_modeling` 은 7종 리포트 모두에 `features` 를 채우고 `_extract_active_reports` 는 `source_flow` 를 확인하지 않아 B-only 리포트도 URL 매핑이 강제 수행됩니다.

**처방**: `agents/domain_modeling/output.schema.json` 의 `$defs.reportEntry` 에 `source_flow` 필드 추가.

```json
"source_flow": {
  "type": "string",
  "enum": ["A", "B", "A+B"],
  "description": "§11-10. A=자체 feature URL 수집, B=다른 리포트 Output 인용, A+B=혼합"
}
```

`feature_url_mapper_node._extract_active_reports` 에 한 줄 필터 추가:

```python
def _extract_active_reports(domain_taxonomy: dict) -> dict[str, dict]:
    report_config = domain_taxonomy.get("report_config") or {}
    return {
        rt: entry
        for rt, entry in report_config.items()
        if isinstance(entry, dict)
        and entry.get("active") is True
        and entry.get("source_flow", "A") in ("A", "A+B")  # B-only 제외
    }
```

`market_context_swot` 의 흐름 A 부분(매크로) 은 §6-6a `market_context_collection_node` 가 들어오기 전까지 본 노드에서 `url_discovery_macro_node` 가 1차 처리합니다.

#### 4-1-1. "B-only 제외" 의 정확한 의미 (turn-15 + turn-16 명확화)

`_extract_active_reports` 필터는 **`feature_url_mapper` 의 5중 통합 노드 + `cross_reference_node` + `additional_urls_validation_node` 까지의 URL 매핑 영역에만 적용**됩니다. 다음 layer 들은 영향받지 않습니다.

| Layer | 단계 | B-only 리포트 처리 |
|---|---|---|
| Layer 1 | `domain_modeling` → `report_config[*].features` 생성 | 정상 수행 (변경 없음) — 7종 features 그대로 |
| Layer 2 | `feature_url_mapper` → 5중 통합 노드 → `analysis_features` 산출 | **생략** — v0.10.18 필터 적용, B-only 미진입 |
| Layer 3 | `feature_selection_node` UI | **5종(흐름 A·A+B) features 카드 + 2종(B-only) features 카드 별도 표시** — `feature_selection_node` 가 `analysis_features` (5종) + `domain_taxonomy.report_config` 의 B-only (2종) 결합 (v0.10.18a 신설) |
| Layer 4 | v1.0 의 리포트 노드(`positioning_map_node`·`executive_summary_node`) | `domain_taxonomy.report_config[<rt>].features` 직접 read → 다른 리포트 출력으로부터 derived 추출 |

핵심: B-only 리포트의 features 는 **Layer 1 에서 생성·Layer 3 에서 노출·Layer 4 에서 derived 사용** 됩니다. Layer 2 의 URL 수집·검증만 생략됩니다.

### 4-2. P1 — URL 탐색 5분리 + 통합 feature mapping 5분리 (turn-11 옵션 (e) 채택)

`url_discovery_brave_node` 를 5개로 분리하는 데 더해, `page_meta_collect_node` 와 `feature_mapping_llm_node` 를 source-type 단위 **5개 통합 노드** 로 합칩니다. 각 통합 노드는 자기 source-type 에 대해 page meta 수집 + LLM 매핑을 직렬 수행합니다. 분리의 단위는 **API surface · quota 모델 · 검증 방법 · 캐싱 단위 · LLM system_prompt 정책이 동일한가** 입니다.

| 신규 노드 | 담당 리포트 | API surface | quota 모델 | 검증 방법 | 캐싱 단위 |
|---|---|---|---|---|---|
| `url_discovery_official_node` | comparison_matrix, battlecard(A), market_context_swot(규제) | Brave HTTP + `official_source` 재사용 | Brave 무료 2,000/월 | HEAD/GET + 본문 키워드 | URL 단위 24h |
| `url_discovery_blog_community_node` | reaction_insight | Brave HTTP | Brave 무료 2,000/월 | 발행일 + 본문 길이 + 반응 시그널 | URL 단위 24h |
| `url_discovery_youtube_reactions_node` | reaction_insight (3rd-party 후기 영상 **만**) | YouTube Data API v3 (`search.list?type=video`) | 일일 10,000 units (호출당 100u) | viewCount + likeCount + commentCount + **owned channel ID 제외** | `{candidate_id, query, region_code}` 24h |
| `url_discovery_owned_channels_node` | marketing_social, battlecard(광고 카피) | Brave HTTP + LLM 검증 + YouTube `channels.list`(1u/candidate) | Brave + Claude API + 미미한 YouTube quota | bio·about 의 브랜드명 매칭 + verified + last_post_at | `{candidate_id, platform, query}` 7일 — platform ∈ {instagram·x·blog_naver·blog_tistory·press_release·**youtube_official**} |
| `url_discovery_macro_node` | market_context_swot(매크로) | Brave HTTP + 도메인 화이트리스트 | Brave 무료 2,000/월 | 발행일 ≤ 24개월 + 권위 도메인 | `{domain_name, query}` 30일 |

각 노드의 상세 설계는 §5 에서 정의합니다.

### 4-3. P1 — Owned channels 발견: 패턴 매칭 폐기, Brave 검색 + LLM 검증 채택

#### 4-3-1. 패턴 매칭의 실측 한계

핀테크/해외여행 카드 도메인 4사의 Instagram 핸들 실측:

| 브랜드 | 카드 상품명 | 실제 Instagram 핸들 | 패턴 추정과의 괴리 |
|---|---|---|---|
| 신한카드 | 신한 SOL트래블 체크카드 | `instagram.com/shinhanbank_official` | 모회사(은행) 통합 계정. `_` 구분자 |
| 하나카드 | 하나 트래블로그 | `instagram.com/hanamoney_official` | 서브브랜드(hanamoney) 명. 본사 명(hanacard) 아님 |
| 트래블월렛 | 트래블월렛 | `instagram.com/travelwallet.official` | `.` 구분자 (다른 브랜드는 `_`) |
| 토스 | 토스 트래블카드 | `instagram.com/toss.im` 외 다중 | 다중 공식 계정. 상품별 별도 여부 불확실 |

패턴 매칭(`instagram.com/{brand_lower}_official`) 의 실패 원인:

1. **모회사·서브브랜드·상품 단위 계정 혼재** — 신한은 은행 통합, 하나는 서브브랜드 hanamoney
2. **구분자 비표준화** — `_`·`.`·하이픈·붙임 모두 사용
3. **다중 공식 계정** — 본사·상품·지역·언어별 분리 운영
4. **로컬라이제이션** — 영문 transliteration(`travelwallet`) vs 한글(`트래블월렛`) 모두 등장

#### 4-3-2. Brave 검색 + LLM 검증 패턴 (`official_source_resolver` 사상 재사용)

```
1. Brave 쿼리: "{candidate_name} instagram 공식 계정"
              "{candidate_name} 공식 X 트위터"
              "{candidate_name} 공식 블로그"
              "{candidate_name} 보도자료"
              "{candidate_name} 공식 유튜브 채널"          ← 신설 (turn-7)
2. Brave 결과 상위 N개 URL을 LLM 에 후보 제시
3. LLM 검증 (ClaudeCodeCliAnalyzer — v0.7 turn-49 결정):
   - 각 URL이 {candidate_name} 의 공식 계정인가
   - 모회사·서브브랜드 계정은 "공식"으로 인정하되 account_scope 로 구분
   - 다중 계정 발견 시 "상품과 가장 직접적 연관" 선택
   - confidence 0.7 미만 시 needs_validation=True
   - 결정론성은 system_prompt 의 명확한 판정 시그널로 자연어 수준에서 흡수
4. HTTP 검증: 핸들 URL GET → 페이지 메타·about/bio 추출 → 브랜드명 명시 확인
5. YouTube platform 한정 추가 단계: 발견된 채널 URL 에 대해 `channels.list?forHandle=...`
   (1 unit) 호출로 channel_id·구독자 수·verified 확인.
```

이 방식은 명명 불규칙성에 무관, 서브브랜드 계정 발견, 다중 계정 트레이드오프 처리, 비공식 fan 계정 배제를 모두 결정론적으로 처리합니다.

### 4-4. P2 — system_prompt 의 source-type 별 5종 분배 (turn-11 옵션 (e) 적용)

옵션 (e) 채택 으로 단일 `feature_url_mapper/system_prompt_kr.md` 의 report_type 분기는 폐기되고, **5개 통합 노드 각각이 자기 source-type 의 단순화된 system_prompt 를 보유**합니다(`agents/feature_mapping_official/system_prompt_kr.md` 등). 각 prompt 는 자기 source 가 담당하는 report_type 1~3종에 대한 정책만 포함합니다.

| report_type | existing_urls 정책 | additional_urls 정책 |
|---|---|---|
| `comparison_matrix`·`battlecard`(A Fact 부분) | 공식·매체 비교 양쪽 채택 | 공식 sub-path 우선 |
| `reaction_insight` | 외부 후기·커뮤니티·YouTube 도메인 적극 채택 (`card-gorilla.com`·`brunch.co.kr`·`clien.net`·`tistory.com` 등) | 외부 후기 검색 결과 도메인 |
| `marketing_social` | 회사 운영 채널(YouTube·Instagram·X·블로그) 도메인 채택 | 동일 채널의 사용 게시물·캠페인 |
| `market_context_swot` | 정부·협회 통계 도메인 채택 (`kosis.kr`·`bok.or.kr`·`nia.or.kr`·`kiet.re.kr` 등) | 산업 보고서 페이지 |

§"해서는 안 되는 일" 의 "입력에 없는 도메인 제안 금지" 는 `reaction_insight`·`marketing_social`·`market_context_swot` 에서 면제로 명시.

`prompt_version` 을 `feature_url_mapper:v0.11` 로 bump 하여 옛 캐시 강제 미스 처리.

### 4-5. P2~중기 — SPA·동적 페이지의 page meta 한계 처방

본 처방은 우선순위 순으로 단계별 적용합니다.

| 순서  | 처방                                                                                     | 변경량 | 효과                                              |
| :-: | -------------------------------------------------------------------------------------- | :-: | ----------------------------------------------- |
|  1  | `_fetch_meta` 에 `<h1>`/`<h2>` + 본문 첫 500~800자 수집 추가                                    | 작음  | 정적 사이트(신한·하나·card-gorilla 등) 정확도 향상. SPA 효과 제한적 |
|  2  | `agents/feature_url_mapper/system_prompt_kr.md` §3 판정 근거에 `headings`·`body_snippet` 추가 | 작음  | LLM 이 풍부한 컨텍스트로 `sufficient` 판정 가능              |
|  3  | `playwright` headless 브라우저 fallback (도메인 화이트리스트 트리거)                                   | 중간  | SPA·동적 페이지 해결. 페이지당 +3~10초, +200~500MB          |
|  4  | §6-6a 수집 노드 6종 구현 → `reaction_insight` 본 노드에서 분리                                       |  큼  | 근본 해결. SPA 의존 자체 제거                             |

playwright 도입 정책:

```python
def _fetch_meta(url: str) -> dict:
    # 1차 정적 GET
    meta = _fetch_static_meta(url)
    # 트리거 조건: <title> 부재 + HTML 길이 < 1KB → SPA 의심
    if not meta.get("page_title") and meta.get("html_length", 0) < 1024:
        if _is_spa_whitelisted_domain(url):  # 화이트리스트 기반
            meta = _fetch_via_playwright(url)
    return meta
```

`context["renderer"]="playwright"` 를 캐시 키에 분리 추가하여 정적·동적 결과를 별도 보관합니다.

---

## 5. 노드별 상세 설계

### 5-1. `url_discovery_official_node` (v0.10.22a 실 구현)

**역할**: 자사·경쟁사 공식 사이트와 그 sub-page 를 발견하여 comparison_matrix · battlecard(A 부분) · market_context_swot(규제 부분) 의 입력으로 제공.

**핵심 변경 (v0.10.22a turn-52)**

v0.10.19 까지의 스켈레톤(`_discover_via_brave_with_hints` 그대로 재사용) 을 폐기하고 §5-1 의 5가지 정밀화 책임을 일괄 도입했습니다.

| 결정 | 채택 옵션 | 효과 |
|---|---|---|
| D33 | (a) `source_hint="official"` hints + 정적 sub-page 키워드 7건 보강 | LLM 추천 + 한국 일반 카테고리 (약관·수수료·환율·한도·혜택·공지사항·이용안내) 결합 |
| D34 | (a) `official_domain` 부재 candidate 는 site: 검색 스킵 + carry-through 만 유지 | official_source_resolver 의 책임 분리 (URL 발견 실패는 resolver 책임) |

**입력 state 키**
- `official_sources` — `official_source_resolver_node` 산출. `[{source_type, candidate_id, primary_url, validated, reference_sources}, ...]`
- `domain_taxonomy.report_config` — `source_hint="official"` hint 추출용
- `own_product` · `competitor_candidates` · `selected_competitor_ids`
- `domain_name`

**처리 흐름 (6단계)**

1. **Carry-through** — `official_sources` 의 `validated=True` + `primary_url` 항목을 origin=`"official_source"` 로 통과. `urlparse(primary_url).hostname` 으로 `official_domain` 추출.
2. **hints 추출** — `_extract_hints_for_source(active_reports, "official")` 로 source_hint="official" 인 hint 의 (query, feature_id, report_type) 튜플.
3. **site: 한정 검색 작업 목록 생성**:
   - 정적 sub-page 키워드 7건 × candidate (carry 보유) → `"{cand_name} {keyword} site:{official_domain}"`
   - LLM 추천 hints × candidate → `_substitute_tokens` 치환 후 `site:{official_domain}` 부착 (hint 에 이미 site: 가 있으면 그대로)
4. **병렬 Brave 검색** — `_brave_search` 24h TTL 캐시 활용 + `_host_endswith(url, domain)` 으로 결과 host 재검증 (site: 누락 대비).
5. **`_check_url_status` 도달성 검증 (병렬)** — Brave 발견 URL 모두 HEAD/GET. status 2xx·3xx 만 통과 — 도달 불가 URL 의 LLM 입력 진입 0건 보장.
6. **머지** — carry (origin=`"official_source"`) + 검증 통과 subpage (origin=`"official_subpage"`) union. URL dedup 후 candidate 별 dict 산출.

**출력 state 키**: `official_urls_by_candidate: dict[candidate_id, list[dict]]`

각 dict 항목:
```json
{
  "url":                  str,
  "page_title":           str,
  "meta_description":     str,
  "origin":               "official_source" | "official_subpage",
  "subpage_category":     "" | "약관" | "수수료" | "환율" | "한도" | "혜택" | "공지사항" | "이용안내" | "hint",
  "matched_report_types": ["comparison_matrix", "battlecard", "market_context_swot"]
}
```

**검증 게이트**

- 도달 불가 URL (status ≥ 400 또는 None) 의 결과 진입 0건
- `subpage_category` 부착률 ≥ 80% (Brave 결과 URL)
- `origin` 2종 분리 정상 — `official_source` (carry) + `official_subpage` (Brave)
- `official_domain` 추출 정확률 100%
- `_host_endswith` 재검증 통과율 ≥ 90% (Brave site: 정상 동작 시)

**캐싱**: `_brave_search` 24h TTL + `_check_url_status` 24h TTL agent_cache 그대로 활용 (별도 노드 캐시 미도입 — `_filter_candidates_for_report` 의 origin 기반 통과 로직과 정합 유지).

**graceful 종료**:
- `official_sources` 빈 입력 → site: 검색 0건, 빈 결과 산출
- `BRAVE_SEARCH_API_KEY` 미설정 → `_brave_search` 빈 리스트 → carry 만 유지
- 일부 URL HEAD 실패 → status="completed" + errors 누적

---

### 5-2. `url_discovery_blog_community_node`

**역할**: 블로그·커뮤니티·후기 사이트의 외부 도메인 글을 발견하여 reaction_insight 의 입력으로 제공.

**입력 state 키**
- `domain_taxonomy.report_config["reaction_insight"]` (active=true, source_flow ∈ {A, A+B} 확인)
- `own_product`·`competitor_candidates`·`selected_competitor_ids`
- `domain_name`

**탐색 전략**

1. report_config 의 `search_query_hints` 중 외부 도메인 지향 hint 만 추출 (예: "후기"·"커뮤니티"·"리뷰" 키워드 포함)
2. candidate 별 토큰 치환 후 Brave 검색 (외부 도메인 강조):
   - `{candidate_name} 후기` (Brave 자동 필터링)
   - `{candidate_name} 사용 경험`
   - `{candidate_name} 단점`
   - `{own_product} vs {candidate_name} 비교`
3. 결과 URL 중 공식 도메인 일치 항목 제외(`-site:공식도메인` 효과)
4. 외부 도메인 화이트리스트 우선 정렬: `card-gorilla.com`·`brunch.co.kr`·`clien.net`·`*.tistory.com`·`namu.wiki`·`bizhankook.com`·`millionairefrom24.thinkmblog.com`·`info.heretravel.co.kr` 등

**출력 state 키**: `blog_community_urls_by_candidate: dict[candidate_id, list[dict]]`

각 dict 항목:
```json
{
  "url":              str,
  "page_title":       str,
  "meta_description": str,
  "origin":           "blog_community",
  "domain_class":     "review_site" | "personal_blog" | "community" | "wiki",
  "published_at":     str (ISO 8601, 가능 시),
  "matched_report_types": ["reaction_insight"]
}
```

**검증 방법**: 발행일 ≤ 36개월(가능 시), 본문 길이 ≥ 200자, 도메인 화이트리스트 매칭.

**캐싱**: `agent_id="url_discovery_blog_community"`, cache_input `{candidate_id, query}`, TTL 24h.

---

### 5-3. `url_discovery_youtube_reactions_node`

**역할 (turn-7 재정의)**: **reaction_insight 의 3rd-party 후기 영상 탐색에만** 집중합니다. 자사·경쟁사가 직접 운영하는 공식 YouTube 채널 발견은 본 노드 범위가 아니며 `url_discovery_owned_channels_node`(§5-4)의 `platform="youtube_official"` 멤버가 담당합니다.

**범위 분리 근거 (turn-7)**

- "candidate 명으로 3rd-party 영상 검색"(다수 쿼리 × 다수 결과) 과 "candidate 가 운영하는 채널 식별"(소수 쿼리 × 1 정답) 은 검색 키워드 패턴·API 호출 시퀀스·검증 시그널·캐시 단위·quota 사용 패턴 모두 다릅니다.
- `intent` 필드로 두 동작을 한 노드에 묶으면 캐시 키 충돌·quota 추적 모호·§6-6a 인계 경계 흐림이 발생합니다(turn-7 §1-1 분석).
- 본 노드는 `intent` 필드를 받지 않고 단일 동작만 수행합니다.

**입력 state 키**
- `domain_taxonomy.report_config["reaction_insight"]` (active=true, source_flow ∈ {A, A+B} 확인)
- `own_product`·`competitor_candidates`·`selected_competitor_ids`
- `domain_name`
- `YOUTUBE_API_KEY` (`server/config.py` 신설)

**탐색 전략 (단일 동작)**

candidate 별로 다음 쿼리를 YouTube Data API 에 호출:

| 쿼리 패턴 | API | quota | 후속 동작 |
|---|---|:-:|---|
| `search.list?q="{candidate_name} 후기"&type=video&order=relevance&regionCode=KR` | `search.list` 1회 | 100 units | 영상 ID·channel_id·channel_title·viewCount 등 metadata 만 저장 |
| `search.list?q="{candidate_name} 사용 경험"` | 동일 | 100 units | 동일 |
| `search.list?q="{candidate_name} 단점"` | 동일 | 100 units | 동일 |

댓글·자막 수집은 본 노드 범위가 아니며 §6-6a `youtube_collection_node` 가 후속 수집합니다.

**출력 state 키**: `youtube_reactions_urls_by_candidate: dict[candidate_id, list[dict]]`

각 dict 항목:
```json
{
  "url":              str (https://www.youtube.com/watch?v={video_id}),
  "video_id":         str,
  "channel_id":       str,     // cross-reference 키 (§5-6 머지에서 사용)
  "channel_title":    str,
  "channel_verified": bool,
  "view_count":       int,
  "like_count":       int,
  "comment_count":    int,
  "published_at":     str (ISO 8601),
  "origin":           "youtube_reactions",
  "matched_report_types": ["reaction_insight"]
}
```

**검증 방법**

- `videos.list` 로 영상 비공개·삭제 여부 확인
- viewCount ≥ 도메인별 임계치(기본 1,000)
- commentCount ≥ 10
- **owned channel ID 자동 제외**: `channel_id` 가 `url_discovery_owned_channels_node` 의 `platform="youtube_official"` 결과의 channel_id 집합에 포함되면 제외 (§5-6 cross-reference 단계에서 결정론적 처리)

**캐싱**: `agent_id="url_discovery_youtube_reactions"`, cache_input `{candidate_id, query, region_code}`, TTL 24h.

**Quota 관리**

- 일일 quota = 10,000 units
- candidate 4명 × 3 쿼리 = 약 1,200 units (cache miss 첫 실행)
- 동일 도메인 재실행 시 24h TTL 캐시 hit 으로 0 units
- 잔여 quota 모니터링: `agent_cache.py` 에 `quota_budget` 로깅 추가, 호출 직전 잔여 확인 후 미달 시 `agent_steps[*].status="quota_skip"` 으로 우회 기록
- API key 발급: `YOUTUBE_API_KEY` 환경변수, `server/config.py` 에서 로드

---

### 5-4. `url_discovery_owned_channels_node`

**역할 (turn-7 범위 확대)**: 자사·경쟁사가 직접 운영하는 **모든 공식 채널** — Instagram·X·블로그·보도자료에 더해 **YouTube 공식 채널** — 의 핸들·URL 을 발견하여 marketing_social·battlecard(광고 카피 부분) 의 입력으로 제공.

**범위 확대 근거 (turn-7)**

- Instagram·X·블로그·보도자료·YouTube 공식 채널은 모두 "회사가 직접 운영하는 공식 채널" 이라는 동일 개념의 멤버이며, 발견 방식(Brave 검색 + LLM 검증) 과 검증 시그널(브랜드명 매칭·verified·last_post_at) 이 동일합니다.
- YouTube 공식 채널에 한해 LLM 검증 후 추가 `channels.list?forHandle=...` 호출(1 unit) 로 channel_id·구독자 수를 확정합니다. quota 영향 미미.
- 영상 metadata 수집(`search.list?channelId=...&order=date`) 은 본 노드 범위가 아니며 §6-6a `youtube_channel_metadata_collection_node` 가 후속 수집합니다 — 다른 platform(Instagram·블로그) 과 동일한 인계 사상.

**입력 state 키**
- `domain_taxonomy.report_config["marketing_social"]`
- `own_product`·`competitor_candidates`·`selected_competitor_ids`
- `domain_name`
- `YOUTUBE_API_KEY` (YouTube platform 한정 `channels.list` 호출용)

**탐색 전략 (Brave 검색 + LLM 검증, `official_source_resolver` 사상 재사용)**

1. candidate 별로 platform 별 Brave 쿼리:
   - Instagram: `"{candidate_name} instagram 공식 계정"`
   - X: `"{candidate_name} 공식 X 트위터"`
   - 블로그: `"{candidate_name} 공식 블로그"`
   - 보도자료: `"{candidate_name} 보도자료"`
   - **YouTube 공식 채널**: `"{candidate_name} 공식 유튜브 채널"`  ← 신설 (turn-7)
2. Brave 결과 상위 5개 URL 을 후보로 수집
3. LLM 검증 (`ClaudeCodeCliAnalyzer` — v0.7 turn-49 결정):
   - 입력: `{candidate_name, platform, candidate_urls: [...]}`
   - 출력: `[{url, is_official: bool, account_scope: "parent_company"|"sub_brand"|"product_specific"|"regional", confidence: float, rationale: str}, ...]`
   - confidence ≥ 0.7 인 항목 채택
   - 결정론성은 system_prompt 의 명확한 판정 기준(URL 의 `official` 접미사·snippet 의 "공식" 키워드·도메인 일치 등) 으로 자연어 수준에서 흡수. ProductIdResolver 같은 완전 결정론(slug 생성) 영역과 달리 confidence 미세 변동(예: 0.85↔0.87) 은 임계 0.7 판정에 영향 없음.
4. HTTP 검증: 핸들 URL GET → bio/about 섹션에 브랜드명 명시 + verified 시그널 확인
5. **YouTube platform 한정 추가 단계**: 확정된 채널 URL 에 대해 `channels.list?forHandle=@{handle}` (1 unit) 호출로 `channel_id`·`subscriber_count`·`verified` 확정. 이 `channel_id` 는 §5-6 cross-reference 머지에서 reactions 영상 필터링 키로 사용됩니다.

**출력 state 키**: `owned_channel_urls_by_candidate: dict[candidate_id, list[dict]]`

각 dict 항목:
```json
{
  "url":              str,
  "platform":         "instagram" | "x" | "blog_naver" | "blog_tistory" | "press_release" | "youtube_official",
  "handle":           str (e.g., "shinhanbank_official", "@TossBank"),
  "channel_id":       str | null,    // platform="youtube_official" 시 채움 (cross-reference 키, §5-6)
  "account_scope":    "parent_company" | "sub_brand" | "product_specific" | "regional",
  "is_verified":      bool,
  "follower_count":   int (가능 시),  // YouTube: subscriber_count
  "last_post_at":     str (ISO 8601, 가능 시),
  "confidence":       float (LLM 판정),
  "origin":           "owned_channel_search",
  "matched_report_types": ["marketing_social", ...]
}
```

**검증 방법**: bio/about 의 브랜드명 매칭, verified badge 확인(가능 시), `last_post_at ≤ 90일`(채널 활성도 검증). YouTube platform 은 `channels.list` 응답의 `snippet.title`·`statistics.subscriberCount`·`status` 활용.

**캐싱**: `agent_id="url_discovery_owned_channels"`, cache_input `{candidate_id, platform, query}`, TTL 7일 (공식 핸들은 자주 변경되지 않음 + LLM 검증 비용 절감).

**플랫폼별 한계**

| 플랫폼 | 발견 가능성 | metadata 수집 가능성 | 비고 |
|---|:-:|:-:|---|
| Instagram | 높음 (Brave 검색) | 낮음 (Graph API 비즈니스 계정 인증 필요) | URL·핸들·verified 까지만 |
| X (트위터) | 중간 (Brave 검색) | 매우 낮음 (무료 read 제거, 2023년) | URL 까지만, 본문 fetch 불가 |
| Blog (Naver/Tistory/Brunch) | 높음 (Brave 검색) | 높음 (HTTP 정적 fetch) | RSS 가능 |
| 보도자료 페이지 | 높음 (Brave 검색) | 높음 (HTTP 정적 fetch) | PDF·HTML 혼재 |
| **YouTube 공식 채널** | 높음 (Brave + `channels.list` 1u) | URL·channel_id·subscriber_count·verified 까지 본 노드. 영상 목록·통계는 §6-6a `youtube_channel_metadata_collection_node` | turn-7 신설. cross-reference 키 제공 |

X 는 1차 범위에서 핸들 발견까지만 가능하고, 본문 metadata 는 v0.11 playwright fallback 또는 1차 제외(D11 앱스토어 사상 평행) 정책으로 처리합니다 — §10 D14 결정 항목.

---

### 5-5. `url_discovery_macro_node` (v0.10.22 실 구현)

**역할**: 정부 통계·산업 보고서·트레이드 미디어의 매크로 데이터를 발견하여 market_context_swot 의 입력으로 제공.

**입력 state 키**
- `domain_taxonomy.report_config["market_context_swot"]` (특히 `macro_data_sources`·`search_query_hints`)
- `domain_name`
- (`own_product`·`competitor_candidates` 는 매크로 검색에 사용하지 않음 — 도메인 단위)

**핵심 변경 (v0.10.22 turn-50)**

`url_discovery_macro_node` 는 다른 4개 source-type 노드(official·blog_community·youtube_reactions·owned_channels)와 달리 candidate 차원이 없는 산업·시장 수준 데이터를 다룹니다. 따라서 4개 결정(D29~D32)을 다음과 같이 정합:

| 결정 | 채택 옵션 | 효과 |
|---|---|---|
| D29 | (c) 2-layer 화이트리스트 (정적 코어 + 동적 도메인 의존) | 한국 정부·통계 보편 사이트는 정적 코어로 강제, 도메인 특화 출처는 domain_modeling 의 LLM 추천 |
| D30 | (c) Tier 그룹 site: 3쿼리 | hint 1건당 Tier1(통계)·Tier2(정책)·Tier3(동적) 각 1쿼리 → 우선순위 + Brave 토큰 한계 회피 |
| D31 | (b) feature 별 < 2건 → Stage 2 진입 | 공식 출처 부족 시 뉴스 화이트리스트로 보강 (사용자 요구) |
| D32 | (a) `candidate_id="macro"` 단일 키 | 매크로 데이터의 candidate 비종속성 코드 차원 표현 |

**도메인 화이트리스트 (2-layer)**

| Tier | 도메인 | 출처 |
|---|---|---|
| Tier 1 (통계 핵심, 정적) | `kosis.kr` · `ecos.bok.or.kr` · `index.go.kr` | 통계청·한국은행 ECOS·e-나라지표 |
| Tier 2 (정책·규제·연구, 정적) | `fsc.go.kr` · `mosf.go.kr` · `fss.or.kr` · `bok.or.kr` · `kdi.re.kr` · `kiet.re.kr` · `nia.or.kr` · `kotra.or.kr` | 금융위·기재부·금감원·한은·KDI·산업연·NIA·KOTRA |
| Tier 3 (도메인 의존, 동적) | `domain_taxonomy.report_config.market_context_swot.macro_data_sources` | domain_modeling 의 LLM 추천 (TLD 필터: `*.go.kr`·`*.or.kr`·`*.re.kr`·`*.ac.kr`·`*.kr` 만) |
| 뉴스 보강 (Stage 2 fallback, 정적) | `yna.co.kr` · `hankyung.com` · `mk.co.kr` · `mt.co.kr` · `etnews.com` · `dt.co.kr` | 연합·한경·매경·머니투데이·전자·디지털타임스 |

**처리 흐름**

1. `_extract_hints_for_source(active_reports, "macro")` 로 `search_query_hints` 중 `source_hint="macro"` 인 hint 만 추출.
2. `_substitute_domain_only(query_template, domain_name)` — `{domain_name}` 만 치환. `{candidate_name}` 등 candidate 토큰이 잔존하면 치환 실패로 처리 (domain_modeling 의 LLM 작성 오류 신호).
3. Stage 1 — 각 hint × Tier1·Tier2·Tier3 = 최대 3쿼리. `(site:a OR site:b OR ...)` 형식 + `_brave_search` 24h TTL 캐시.
4. `_host_matches` 로 결과 URL 의 host 가 Tier 화이트리스트의 suffix 인지 재검증 (Brave site: 누락 대비).
5. Stage 2 진입 판정 — `feature_counts[fid] < 2` 인 feature 가 1건 이상 있으면 진입.
6. Stage 2 — 결손 feature 의 hint × 뉴스 화이트리스트 1쿼리 (병렬). 결과에 `source_tier="news_supplement"` 부착.
7. 결과 집계 — `candidate_id="macro"` 단일 키 dict 으로 출력.

**출력 state 키**: `macro_urls_by_candidate: dict[str, list[dict]]` — 항상 `"macro"` 단일 키.

각 dict 항목:
```json
{
  "url":                  str,
  "page_title":           str,
  "meta_description":     str,
  "origin":               "macro_search",
  "source_tier":          "official_statistics" | "news_supplement",
  "tier_group":           "tier1_statistics" | "tier2_policy" | "tier3_dynamic" | "news",
  "feature_ids":          [str, ...],
  "matched_report_types": ["market_context_swot"]
}
```

**검증 게이트**

- 화이트리스트 매칭률 (Stage 1 URL 의 host 가 코어/동적 화이트리스트에 속함) ≥ 80%
- feature 별 URL 발견 수 (Stage 1 + 2 통합) ≥ 2건
- 뉴스 보강 trigger rate ≤ 30% (초과 시 화이트리스트 보강 필요 신호)
- `candidate_id` 키 = `"macro"` 단일 키
- `source_tier` 분포 (official_statistics : news_supplement) ≥ 7 : 3

**캐싱**: `_brave_search` 의 24h TTL agent_cache 그대로 활용 (쿼리 단위). 본 노드 자체의 별도 TTL 7일 캐시는 도입하지 않음 — domain_taxonomy 가 바뀌면 새 hint·새 macro_data_sources 로 자동 invalidate.

**`_build_candidates_with_meta` 와의 정합**

`urls_merge_node` 가 `macro_urls_by_candidate["macro"]` 을 일반 candidate dict 로 머지하면, `_build_candidates_with_meta` 가 `cid="macro"` 항목을 생성하며 `source_type="macro"` (v0.10.22 신설 분기) 로 부착됩니다. 후속 v0.10.23 의 `feature_mapping_macro` LLM 호출이 본 source_type 으로 macro candidate 를 자사·경쟁사 candidate 와 분기 처리 가능. `_filter_candidates_for_report("market_context_swot")` 는 v0.10.20.1 의 matched_report_types 일관 처리로 macro_search origin 을 자동 통과.

**§6-6a `market_context_collection_node` 와의 인계 경계**

본 노드는 URL 만 발견하고, 실제 매크로 데이터 본문·통계 수치는 §6-6a `market_context_collection_node` 가 수집합니다. v1.0 PR 에서 §6-6a 가 구현되면 본 노드는 URL 발견까지만 책임지고 그 이후는 인계합니다.

---

### 5-6. `cross_reference_node` (turn-11 옵션 (e) 신설)

**역할**: 5개 URL 탐색 노드 fan-in 직후, `youtube_reactions_urls_by_candidate` 의 영상 중 `owned_channel_urls_by_candidate` 의 `platform="youtube_official"` 항목의 `channel_id` 와 일치하는 항목을 **결정론적·LLM 미사용** 으로 제외합니다. 자사·경쟁사 공식 YouTube 채널이 자체 상품을 직접 리뷰하는 영상이 reaction_insight 분석 풀에 잘못 포함되는 edge case 를 차단합니다.

본 노드는 **머지가 아닌 후처리 필터링**이며, owned_channels 결과는 변경 없이 그대로 통과합니다. youtube_reactions 결과만 축소됩니다.

**입력 state 키**: 5개 `*_urls_by_candidate` (`official` · `blog_community` · `youtube_reactions` · `owned_channel` · `macro`)

**처리 로직 (turn-7 함수, 본 노드로 이관)**:

```python
def cross_reference_node(state, config=None):
    started_at = datetime.now(timezone.utc).isoformat()
    reactions = state.get("youtube_reactions_urls_by_candidate") or {}
    owned     = state.get("owned_channel_urls_by_candidate") or {}

    # owned_channel 의 youtube_official channel_id 집합 추출
    owned_yt_channel_ids: set[str] = {
        it["channel_id"]
        for items in owned.values()
        for it in items
        if it.get("platform") == "youtube_official" and it.get("channel_id")
    }

    # reactions 영상 중 owned channel_id 일치 항목 제외
    filtered = {
        cand_id: [v for v in items if v.get("channel_id") not in owned_yt_channel_ids]
        for cand_id, items in reactions.items()
    }

    return {
        "youtube_reactions_urls_by_candidate": filtered,
        "agent_steps": [{
            "step_name":   "CrossReference",
            "status":      "completed",
            "started_at":  started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }],
    }
```

**출력 state 키**: `youtube_reactions_urls_by_candidate` 갱신(replace), 다른 4개 키는 변경 없음.

**캐싱**: 본 노드는 캐시 미사용 — 입력 state 가 두 노드 결과의 함수이므로 별도 캐싱 가치 없음. wall-clock 약 10ms.

**향후 확장**: 다른 source-type 간 cross-reference 룰이 추가되면 본 노드에 함수 분기 누적. 별도 cross_reference 노드 추가 금지.

---

### 5-6a. 통합 feature mapping 노드 5종 (turn-11 옵션 (e) 신설)

`page_meta_collect_node` 와 `feature_mapping_llm_node` 가 폐기되고, 5개 source-type 통합 노드 (`feature_mapping_official` · `feature_mapping_blog_community` · `feature_mapping_youtube_reactions` · `feature_mapping_owned_channels` · `feature_mapping_macro`) 가 신설됩니다. 각 노드는 자기 source-type 에 대해 **page meta 수집 + report_type 별 병렬 LLM 매핑** 을 한 노드 내부에서 직렬 수행합니다.

**5개 노드의 책임 영역**:

| 노드 | 입력 state 키 | 자기 source 가 담당하는 report_type | 출력 state 키 |
|---|---|---|---|
| `feature_mapping_official` | `official_urls_by_candidate` | comparison_matrix · battlecard(A Fact 부분) · market_context_swot(규제 부분) | `official_raw_features: list[dict]` |
| `feature_mapping_blog_community` | `blog_community_urls_by_candidate` | reaction_insight | `blog_community_raw_features: list[dict]` |
| `feature_mapping_youtube_reactions` | `youtube_reactions_urls_by_candidate` (cross_reference 후처리 후) | reaction_insight | `youtube_reactions_raw_features: list[dict]` |
| `feature_mapping_owned_channels` | `owned_channel_urls_by_candidate` | marketing_social · battlecard(광고 카피 부분) | `owned_channel_raw_features: list[dict]` |
| `feature_mapping_macro` | `macro_urls_by_candidate` | market_context_swot(매크로 부분) | `macro_raw_features: list[dict]` |

**노드 내부 동작 (공통 패턴)**

```python
def feature_mapping_<source>_node(state, config=None):
    started_at = datetime.now(timezone.utc).isoformat()
    thread_id  = (config or {}).get("configurable", {}).get("thread_id", "")

    # 단계 1: page meta 수집 (자기 source 의 URL 만, 캐시 24h)
    if thread_id:
        set_progress(thread_id, f"feature_mapping_<source>_meta",
                     detail="페이지 메타 수집 중")
    source_urls = state.get(f"<source>_urls_by_candidate") or {}
    candidates_with_meta = _collect_page_meta(source_urls)
    # 내부 _fetch_meta 호출, agent_id=f"page_meta_<source>", cache_input={url}, TTL 24h

    # 단계 2: report_type 별 병렬 LLM 매핑 (자기 source 가 담당하는 1~3 report)
    if thread_id:
        set_progress(thread_id, f"feature_mapping_<source>_llm",
                     detail="LLM 매핑 중", total=len(active_reports_for_source))
    active_reports_for_source = _filter_reports_for_source(
        state.get("domain_taxonomy"), source="<source>",
    )

    analyzer       = ClaudeCodeCliAnalyzer(model=CLI_MODEL,
                                            timeout=FEATURE_MAPPING_LLM_TIMEOUT,
                                            system_prompt=_load_source_prompt("<source>"))
    relaxed_schema = _strip_schema_patterns(output_schema)

    def _call_for_report(rt):
        r_input = {"domain": ..., "report_type": rt,
                   "active_report": active_reports_for_source[rt],
                   "candidates": candidates_with_meta}
        return analyzer.call_with_schema(prompt=..., output_schema=relaxed_schema)

    results: dict[str, list[dict]] = {}
    with ThreadPoolExecutor(max_workers=FEATURE_URL_MAPPER_PARALLEL) as pool:
        future_map = {pool.submit(_call_for_report, rt): rt
                      for rt in active_reports_for_source}
        for future in as_completed(future_map):
            rt = future_map[future]
            try:
                results[rt] = future.result().get("features", [])
            except Exception as exc:
                logger.error(f"feature_mapping_<source>: {rt} 실패 — {exc}")

    raw_features = _aggregate(results)

    finished_at = datetime.now(timezone.utc).isoformat()
    return {
        f"<source>_raw_features": raw_features,
        "agent_steps": [{
            "step_name":   f"FeatureMapping<Source>",
            "status":      "completed",
            "started_at":  started_at,
            "finished_at": finished_at,
        }],
    }
```

**캐싱 (노드 내부 2단계)**:

| 단계 | agent_id | cache_input | TTL |
|---|---|---|:-:|
| 1단계 page meta | `page_meta_<source>` | `{url}` | 24h |
| 2단계 LLM 매핑 | `feature_mapping_<source>` | `{domain, own_product, active_reports_for_source, sorted(candidate_ids)}` | 무한 |

본 노드는 두 단계가 별도 캐시 키를 갖습니다. URL 변동 시 page meta 만 미스, LLM 결과는 hit. LLM 정책 변경 시(`prompt_version` bump) LLM 만 미스, page meta 는 hit. 캐시 결정론성은 옵션 (d) 의 별도 노드 분리와 동일합니다.

**`prompt_version` 5종**:

- `feature_mapping_official:v0.12`
- `feature_mapping_blog_community:v0.12`
- `feature_mapping_youtube_reactions:v0.12`
- `feature_mapping_owned_channels:v0.12`
- `feature_mapping_macro:v0.12`

한 source-type 의 system_prompt 변경 시 그 source 의 prompt_version 만 bump → 영향 범위 명확.

**노드 내부 단계 가시화**

각 통합 노드는 `set_progress` 를 두 번 emit 합니다.

- `set_progress(stage="feature_mapping_<source>_meta")` — 1단계 진입
- `set_progress(stage="feature_mapping_<source>_llm")` — 2단계 진입

총 5 × 2 = 10개 stage 가 progress_store 에 emit 되어 UI 4단계 stage 표시(v0.10.9 패턴) 가 source-type 별 2단계로 확장됩니다. `STAGE_MESSAGES` 에 10종 추가 필요.

**부분 실패 처리**

| 시점 | 정책 |
|---|---|
| page meta 단계 일부 URL HTTP fail | 해당 URL 의 `page_title`·`meta_description` 빈 값으로 carry-through. LLM 단계 진입 |
| page meta 단계 전체 URL HTTP fail (모두 빈 값) | LLM 단계 진입하되, `agent_steps` 에 `partial: page_meta_all_empty` 기록 |
| LLM 단계 일부 report_type 실패 | 다른 report_type 결과로 부분 진행. `errors` 누적 |
| LLM 단계 전체 실패 | `_error()` 반환, 다른 4개 source 노드는 정상 진행 |

본 정책은 D24 (turn-11 신설 결정 항목) 의 권장안 — "부분 실패 허용 + LLM 입력에 빈 메타 carry-through" — 와 정합.

**body 보강 로직 (v0.10.24 P3 처방, 통합 노드 내부)**

각 통합 노드의 1단계 page meta 수집은 `_fetch_meta` 헬퍼(공유 모듈)를 호출하며, 다음 보강을 적용합니다.

```python
def _fetch_meta(url: str) -> dict:
    # 1차 정적 GET
    meta = _fetch_static_meta(url, body_limit=12_000)
    parsed = {
        "page_title":       parser.title or "",
        "meta_description": parser.meta_desc or "",
        "headings":         parser.headings[:6],   # h1·h2 최대 6개
        "body_snippet":     parser.body_text[:800],  # 본문 첫 800자
    }
    # 트리거: SPA 의심 시 playwright fallback (v0.11)
    if not parsed["page_title"] and len(html_text) < 1024:
        if _is_spa_whitelisted_domain(url):
            parsed = _fetch_via_playwright(url)
    return parsed
```

본 헬퍼는 `feature_url_mapper_node.py` (헬퍼 모듈) 에 위치하며 5개 통합 노드가 공유 import 합니다. 캐시는 `agent_id="page_meta_<source>"` 로 source-type 별 분리되어 `{url, renderer}` 단위 24h(정적) / 7일(playwright) TTL.

**source-type 별 system_prompt 5종 (P2 v0.10.27)**

기존 `agents/feature_url_mapper/system_prompt_kr.md` 단일 prompt 의 report_type 분기를 5개 source-type 별 prompt 로 분배합니다.

```
agents/
├── feature_mapping_official/system_prompt_kr.md
│     - 정책: 공식 도메인 우선, 매체 비교(card-gorilla 등) 보조 채택
│             additional_urls = 동일 공식 도메인 sub-path 만
│     - 담당 report: comparison_matrix · battlecard(A Fact) · market_context_swot(규제)
│
├── feature_mapping_blog_community/system_prompt_kr.md
│     - 정책: 외부 후기·커뮤니티·블로그 도메인 적극 채택
│             additional_urls = 외부 도메인 허용
│     - 담당 report: reaction_insight
│
├── feature_mapping_youtube_reactions/system_prompt_kr.md
│     - 정책: YouTube 영상 metadata 기반 reaction 판정
│             cross_reference 후처리로 owned channel 영상은 입력에서 제외됨
│     - 담당 report: reaction_insight
│
├── feature_mapping_owned_channels/system_prompt_kr.md
│     - 정책: 회사 운영 채널 도메인 채택
│             additional_urls = 동일 채널의 캠페인·게시물
│     - 담당 report: marketing_social · battlecard(광고 카피)
│
└── feature_mapping_macro/system_prompt_kr.md
      - 정책: 정부·협회 통계 도메인 채택
              additional_urls = 산업 보고서 페이지
      - 담당 report: market_context_swot(매크로)
```

5개 prompt 의 공통 schema(`agents/feature_url_mapper/output.schema.json`)는 그대로 유지되어 후속 단계의 union 처리가 정합합니다.

**`battlecard` 의 다중 source 매핑 통합 (D23 결정 의존)**

A+B 흐름의 battlecard 는 `feature_mapping_official` (A Fact 부분) 과 `feature_mapping_owned_channels` (광고 카피 부분) 양쪽에서 별도 매핑됩니다. `additional_urls_validation_node` 가 5개 `*_raw_features` 를 합쳐 단일 `analysis_features` 로 변환할 때 동일 `feature_id` 가 두 source 에서 등장하면 `candidate_coverage` 의 union 으로 통합합니다(LLM 미사용 결정론적 후처리). 본 규칙은 D23 권장안 — "feature_id 동일 시 candidate_coverage union" — 와 정합.

---

### 5-8. `additional_urls_validation_node` (단일, source-type 별 검증 분기 + 5 source 결과 union)

**역할 (turn-11 옵션 (e) 보강)**: 5개 통합 노드가 산출한 `*_raw_features` 를 union 으로 합치고, LLM 이 제안한 `additional_urls` 에 대해 source-type 별로 다른 검증 절차를 적용. 기존 단일 HEAD/GET 만 사용하던 v0.10.9 의 한계를 해소.

**입력 state 키**: 5개 신규 `*_raw_features` (`official_raw_features` · `blog_community_raw_features` · `youtube_reactions_raw_features` · `owned_channel_raw_features` · `macro_raw_features`)

**Union 단계 (D23 권장안 적용)**

```python
def _union_raw_features(state) -> list[dict]:
    """5개 source-type 의 raw_features 를 합치되, 동일 feature_id 는 candidate_coverage union."""
    by_feature_id: dict[str, dict] = {}
    for source in ("official", "blog_community", "youtube_reactions",
                   "owned_channel", "macro"):
        for raw in state.get(f"{source}_raw_features") or []:
            fid = raw["feature_id"]
            if fid not in by_feature_id:
                by_feature_id[fid] = dict(raw)
            else:
                # battlecard 등 다중 source feature 의 candidate_coverage union
                existing_cov = {c["candidate_id"]: c
                                for c in by_feature_id[fid].get("candidate_coverage", [])}
                for c in raw.get("candidate_coverage", []):
                    cid = c["candidate_id"]
                    if cid in existing_cov:
                        # 동일 candidate 의 existing_urls·additional_urls union
                        existing_cov[cid]["existing_urls"]   = _dedup_by_url(
                            existing_cov[cid].get("existing_urls", []) + c.get("existing_urls", []))
                        existing_cov[cid]["additional_urls"] = _dedup_by_url(
                            existing_cov[cid].get("additional_urls", []) + c.get("additional_urls", []))
                    else:
                        existing_cov[cid] = c
                by_feature_id[fid]["candidate_coverage"] = list(existing_cov.values())
    return list(by_feature_id.values())
```

**검증 분기**

```python
def _validate_additional_url(url: str, origin_hint: str | None) -> dict:
    if _is_youtube_url(url):
        return _validate_youtube_video(url)         # videos.list 호출
    elif _is_owned_channel_url(url):
        return _validate_owned_channel(url)         # HTTP GET + bio 추출
    elif _is_macro_authority_domain(url):
        return _validate_macro_doc(url)             # HEAD/GET + 발행일 추출
    else:
        return _validate_http(url)                  # 기존 HEAD/GET (블로그·공식 sub-page)
```

각 검증 함수의 산출 필드:

| 검증 함수 | validated | http_status | 추가 필드 |
|---|:-:|:-:|---|
| `_validate_http` | bool | int|None | — |
| `_validate_youtube_video` | bool | — | `view_count`, `comment_count`, `is_private` |
| `_validate_owned_channel` | bool | int|None | `is_brand_match`, `is_verified`, `last_post_at` |
| `_validate_macro_doc` | bool | int|None | `published_at`, `authority_class` |

**출력 state 키**: `analysis_features: list[AnalysisFeature]` (기존 유지, `candidate_coverage[*].additional_urls[*]` 의 검증 필드 확장).

**캐싱**: `agent_id="url_validation"`, cache_input `{url, validator}` (validator = `http`|`youtube`|`owned_channel`|`macro`), TTL 24h.

---

### 5-9. `feature_selection_node` (interrupt #4) — source_flow 별 UI 차별화 (v0.10.18a 신설)

**역할 (turn-16 신설)**: `analysis_features` (흐름 A·A+B 5종) + `domain_taxonomy.report_config` 의 B-only 2종을 결합하여 7종 리포트 카드를 UI 에 노출. 각 카드에 source_flow 별 안내 문구를 표시하고 B-only 카드는 URL 영역을 미렌더.

**디자인 결정 (D27 v0.4 확정)**

안내 문구 데이터 위치는 **D27 옵션 (b) — `feature_selection_node` 의 정적 dict** 채택. 결정론적·도메인 무관·변경 표면적 작음의 3가지 이유. 향후 도메인 다양성 증가 시 옵션 (a) `domain_modeling` schema 의 `intro_text` 필드로 이전 가능 (별도 PR v0.10.18b).

**노드 내부 정적 dict**

```python
# server/graph/nodes/feature_selection_node.py 신설 상수
_REPORT_INTRO_TEXTS: dict[str, str] = {
    "comparison_matrix":
        "이 리포트는 아래 선택된 feature 데이터를 자사·경쟁사 공식 사이트에서 수집하여 작성합니다.",
    "reaction_insight":
        "이 리포트는 아래 선택된 feature 데이터를 외부 후기·블로그·YouTube 영상·"
        "커뮤니티 게시글에서 수집하여 작성합니다.",
    "marketing_social":
        "이 리포트는 아래 선택된 feature 데이터를 자사·경쟁사 운영 SNS"
        "(Instagram·X·YouTube 공식 채널)·블로그·보도자료에서 수집하여 작성합니다.",
    "battlecard":
        "이 리포트는 아래 선택된 feature 데이터를 수집한 뒤, "
        "비교 매트릭스·고객 반응 인사이트·마케팅·소셜 분석 결과를 종합하여 작성합니다.",
    "market_context_swot":
        "이 리포트는 아래 선택된 매크로 feature 데이터를 정부 통계·산업 보고서·"
        "트레이드 미디어에서 수집한 뒤, 비교 매트릭스·고객 반응 인사이트·"
        "마케팅·소셜 분석 결과와 종합하여 작성합니다.",
    "positioning_map":
        "이 리포트는 아래 표시된 feature 를 기반으로 비교 매트릭스 결과로부터 "
        "자동 도출됩니다. URL 수집은 발생하지 않습니다.",
    "executive_summary":
        "이 리포트는 아래 표시된 feature 를 기반으로 6개 분석 리포트 결과를 통합하여 "
        "자동 도출됩니다. URL 수집은 발생하지 않습니다.",
}
```

**reports_payload 구성 로직**

```python
def _build_reports_payload(state) -> dict:
    """analysis_features(5종 흐름 A·A+B) + domain_taxonomy(2종 B-only) 결합."""
    from collections import defaultdict
    analysis_features = state.get("analysis_features") or []
    domain_taxonomy   = state.get("domain_taxonomy") or {}
    report_config     = (domain_taxonomy.get("report_config") or {})

    # 1차: analysis_features 의 report_type 별 그룹핑
    features_by_rt: dict[str, list] = defaultdict(list)
    for f in analysis_features:
        rt = f.get("report_type")
        if rt:
            features_by_rt[rt].append(_build_feature_item(f))

    # 2차: 7종 리포트 카드 구성 (B-only 포함)
    reports: list[dict] = []
    for rt, entry in report_config.items():
        if not entry.get("active"):
            continue
        source_flow = entry.get("source_flow", "A")
        is_b_only   = source_flow == "B"

        if not is_b_only:
            # 흐름 A · A+B: analysis_features 의 결과 그대로
            features = features_by_rt.get(rt, [])
        else:
            # B-only: domain_taxonomy 의 features 만 (URL 영역 없음)
            feature_labels = entry.get("feature_labels", {}) or {}
            features = [
                {
                    "feature_id":         fid,
                    "feature_name":       feature_labels.get(fid, fid),
                    "description":        "",
                    "priority":           "medium",
                    "candidate_coverage": None,   # B-only 표식
                    "coverage_summary":   None,
                    "coverage_details":   None,
                }
                for fid in (entry.get("features") or [])
            ]

        reports.append({
            "report_type":          rt,
            "report_label":         entry.get("label", rt),
            "source_flow":          source_flow,                     # v0.10.18a 신설
            "intro_text":           _REPORT_INTRO_TEXTS.get(rt, ""), # v0.10.18a 신설
            "url_coverage_visible": not is_b_only,                   # v0.10.18a 신설
            "features":             features,
        })
    return {"reports": reports}
```

**interrupt value schema (v0.10.18a 보강)**

```json
{
  "reports": [
    {
      "report_type":          str,
      "report_label":         str,
      "source_flow":          "A" | "B" | "A+B",
      "intro_text":           str,
      "url_coverage_visible": bool,
      "features":             [...]
    }, ...
  ]
}
```

**client UI 사양 (`client/src/components/FeatureSelectionPage.jsx` 변경)**

| source_flow | 안내 박스 색상 | feature 카드 | URL 영역 | 체크박스 |
|:-:|:-:|---|:-:|:-:|
| A | 중성(`intro-box-neutral`) | 정상 | ✓ (충분/부분/미확보 + URL 상세 보기) | 활성, 사용자 선택 |
| A+B | 호박색(`intro-box-amber`) — 흐름 B 인용 강조 | 정상 (A 부분 feature 만) | ✓ | 활성, 사용자 선택 |
| B-only | 파란색(`intro-box-blue`) — URL 수집 부재 명시 | 정상 (체크박스 자동 선택 + 비활성) | ✗ (영역 미렌더) | **비활성 + 자동 포함** |

B-only 카드의 체크박스 정책은 D27a 결정 — 자동 포함(turn-16 꼬리질문 3 의 권장안 (a) "사용자가 가장 정보가 풍부한 feature_selection 시점에 결정 가능" 채택 시 활성으로 전환).

**상세 PR 단위는 §9 v0.10.18a 참조**. 본 절은 사양만 정의.

---

## 6. state 키 · 캐시 키 · 토폴로지

### 6-1. `state.py` 신설·변경 키

```python
class DomainAnalysisState(TypedDict, total=False):
    ...
    # ── feature_url_mapper 재설계: 5개 source-type URL 탐색 결과 ─────────────
    official_urls_by_candidate:           dict[str, list[dict]]
    blog_community_urls_by_candidate:     dict[str, list[dict]]
    youtube_reactions_urls_by_candidate:  dict[str, list[dict]]   # turn-7: youtube → youtube_reactions 개명
    owned_channel_urls_by_candidate:      dict[str, list[dict]]   # turn-7: platform ∈ {instagram·x·blog_*·press_release·youtube_official}
    macro_urls_by_candidate:              dict[str, list[dict]]   # domain 단위

    # ── 5개 source-type 통합 노드 출력 (turn-11 옵션 (e) 신설) ───────────────
    official_raw_features:           list[dict]
    blog_community_raw_features:     list[dict]
    youtube_reactions_raw_features:  list[dict]   # cross_reference_node 후처리 적용 후
    owned_channel_raw_features:      list[dict]
    macro_raw_features:              list[dict]

    # ── 최종 출력 (additional_urls_validation_node 가 5종 union + 검증 후 산출) ─
    analysis_features:    list[AnalysisFeature]   # additional_urls 검증 필드 확장 + D23 union 처리

    # ── 폐기 키 ──────────────────────────────────────────────────────────────
    # brave_urls_by_candidate:  5개 *_urls_by_candidate 키로 대체. 옛 캐시는 자연 폐기.
    # candidates_with_meta:     통합 노드 내부 변수로 흡수. 더 이상 state 키 아님.
    # raw_features:             5개 *_raw_features 로 대체.
```

`AnalysisFeature` 의 `candidate_coverage[*].additional_urls[*]` 구조:

```python
{
    "url":            str,
    "rationale":      str,
    "url_confidence": float,
    "validated":      bool,
    "http_status":    int | None,
    "validator":      "http" | "youtube" | "owned_channel" | "macro",  # 신설
    "extra":          dict,  # 검증기별 추가 필드 (view_count·published_at 등)
}
```

### 6-2. 캐시 키 일람

| agent_id | cache_input | TTL | 비고 |
|---|---|:-:|---|
| `url_discovery_official` | `{candidate_id, query}` | 24h | URL 단위 |
| `url_discovery_blog_community` | `{candidate_id, query}` | 24h | URL 단위 |
| `url_discovery_youtube_reactions` | `{candidate_id, query, region_code}` | 24h | turn-7: intent 폐기, reactions-only |
| `url_discovery_owned_channels` | `{candidate_id, platform, query}` | 7일 | turn-7: platform 에 `youtube_official` 포함. LLM 검증 + `channels.list`(1u) 비용 절감 |
| `url_discovery_macro` | `{domain_name, query}` | 30일 | 매크로 데이터 갱신 주기 |
| `page_meta_official` · `page_meta_blog_community` · `page_meta_youtube_reactions` · `page_meta_owned_channels` · `page_meta_macro` | `{url, renderer}` | 24h(static) / 7일(playwright) | turn-11 옵션 (e): 통합 노드 1단계 캐시. source-type 별 5종 분리. renderer 분리 |
| `feature_mapping_official` · `feature_mapping_blog_community` · `feature_mapping_youtube_reactions` · `feature_mapping_owned_channels` · `feature_mapping_macro` | `{domain, own_product, active_reports_for_source, sorted(candidate_ids)}` (A-4 유지) | 무한 | turn-11 옵션 (e): 통합 노드 2단계 캐시. source-type 별 5종 분리. 각 prompt_version 독립 bump (`feature_mapping_<source>:v0.12`). 부분 hit 활용 가능 |
| `url_validation` | `{url, validator}` | 24h | validator 분리 (`http`·`youtube`·`owned_channel`·`macro`) |

옛 `url_discovery_brave` 캐시는 cache_input 키 변경(`candidate_id`·`intent` 등 신규 필드 추가) 으로 자연 미스. 마이그레이션 스크립트 불필요.

### 6-3. 토폴로지 (`graph.py` 갱신)

```python
# ── 5중 URL 탐색 fan-out (1차) — turn-11 옵션 (e) ────────────────────────
builder.add_node("url_discovery_official",           url_discovery_official_node)
builder.add_node("url_discovery_blog_community",     url_discovery_blog_community_node)
builder.add_node("url_discovery_youtube_reactions",  url_discovery_youtube_reactions_node)
builder.add_node("url_discovery_owned_channels",     url_discovery_owned_channels_node)
builder.add_node("url_discovery_macro",              url_discovery_macro_node)

builder.add_edge("ab_join", "url_discovery_official")
builder.add_edge("ab_join", "url_discovery_blog_community")
builder.add_edge("ab_join", "url_discovery_youtube_reactions")
builder.add_edge("ab_join", "url_discovery_owned_channels")
builder.add_edge("ab_join", "url_discovery_macro")

# ── list-fan-in barrier (1차) → cross_reference_node ─────────────────────
builder.add_node("cross_reference", cross_reference_node)
builder.add_edge(
    ["url_discovery_official", "url_discovery_blog_community",
     "url_discovery_youtube_reactions", "url_discovery_owned_channels",
     "url_discovery_macro"],
    "cross_reference",
)

# ── 5중 통합 feature mapping fan-out (2차) — page_meta + LLM 매핑 통합 ───
builder.add_node("feature_mapping_official",           feature_mapping_official_node)
builder.add_node("feature_mapping_blog_community",     feature_mapping_blog_community_node)
builder.add_node("feature_mapping_youtube_reactions",  feature_mapping_youtube_reactions_node)
builder.add_node("feature_mapping_owned_channels",     feature_mapping_owned_channels_node)
builder.add_node("feature_mapping_macro",              feature_mapping_macro_node)

builder.add_edge("cross_reference", "feature_mapping_official")
builder.add_edge("cross_reference", "feature_mapping_blog_community")
builder.add_edge("cross_reference", "feature_mapping_youtube_reactions")
builder.add_edge("cross_reference", "feature_mapping_owned_channels")
builder.add_edge("cross_reference", "feature_mapping_macro")

# ── list-fan-in barrier (2차) → additional_urls_validation ───────────────
builder.add_edge(
    ["feature_mapping_official", "feature_mapping_blog_community",
     "feature_mapping_youtube_reactions", "feature_mapping_owned_channels",
     "feature_mapping_macro"],
    "additional_urls_validation",
)

# ── 후속 흐름 ────────────────────────────────────────────────────────────
builder.add_edge("additional_urls_validation",   "feature_selection")
```

list-edge barrier 는 v0.10.7 `scripts/verify_fanin.py` 로 이미 검증된 패턴 — 두 차례의 5중 fan-in 이 각각 결정론적으로 1회 발화됩니다.

---

## 7. `domain_modeling` 영향 — `report_config` 스키마 확장

### 7-1. `source_flow` 필드 추가 (P0)

`agents/domain_modeling/output.schema.json` 의 `$defs.reportEntry` 에 추가:

```json
"source_flow": {
  "type": "string",
  "enum": ["A", "B", "A+B"],
  "description": "§11-10. A=자체 feature URL 수집, B=다른 리포트 Output 인용, A+B=혼합"
}
```

7종 리포트의 기본값:

| report_type | source_flow |
|---|:-:|
| comparison_matrix | A |
| reaction_insight | A |
| marketing_social | A |
| battlecard | A+B |
| positioning_map | B |
| market_context_swot | A+B |
| executive_summary | B |

`market_context_swot` 의 A 부분은 §6-6a `market_context_collection_node` 가 구현되기 전까지 `url_discovery_macro_node` 가 1차 처리.

### 7-2. `search_query_hints` 의 feature-hint 명시 연결 + 토큰 중립화 (v0.6 재정의)

> **재정의 배경 (turn-33 ~ turn-36)**
>
> v0.5 본문은 본 절을 "토큰 분포 own_only:both:comp_only = 1:1:1 비율 균형" 으로 정의했으나, 사용자 의도(turn-35)는 토큰 비율의 통계적 균형이 아니라 **각 feature 가 자사·경쟁 상품 모두에 적용되도록 hint 구조 자체를 개선** 하는 것임이 확인되었습니다. v0.6 에서 본 절을 재정의하고, v0.5 의 비율 균형 규칙은 폐기합니다.

#### 7-2-1. 사용자 의도 — 각 feature 가 own + comp 모두에서 정보 수집

`comparison_matrix` 의 features(`overseas_payment_fee_rate` · `atm_withdrawal_fee_benefit` · `card_structure_type` 등) 각각에 대한 query 가 own_product 와 selected 경쟁사 모두에 적용되어야 합니다. 현재 schema 의 한계:

1. **features 배열과 search_query_hints 배열이 별도** — 어느 hint 가 어느 feature 를 위한 검색인지 명시 없음
2. **`{competitor_name}` 토큰의 own 적용 시 의미 모순** — own_* candidate 처리 시 자사명으로 치환됨
3. **`{own_product}` 토큰의 comp 적용 시 자사명 검색** — comp 시점에 자사명만 검색되어 comp 정보 0건

#### 7-2-2. 해결책 — 두 차원의 구조 개선 (v0.10.19.1 적용)

**차원 1 — feature-hint 명시 연결**: search_query_hints 의 각 항목이 `feature_id` 필드로 features 배열의 ID 중 하나를 명시. `active=true` 리포트의 각 feature 는 최소 1개 이상의 hint 를 보유.

**차원 2 — 토큰 중립화**: `{candidate_name}` 토큰 신설 — `_substitute_tokens` 가 own + selected comp 모든 candidate 에 대해 candidate 의 product_name 으로 치환. 한 hint 가 (1 own + N comp) = N+1 개 Brave 쿼리를 자연 생성. `{competitor_name}` 은 alias 후방 호환.

차원 1·2 가 충족되면 토큰 분포 균형은 부수적으로 자연 해소됩니다(각 feature 가 own + comp 양쪽 검색되므로 own:comp = 50:50 자동).

#### 7-2-3. 구현 위치 (v0.10.19.1)

- `agents/domain_modeling/output.schema.json` 의 `search_query_hints.items` 를 `oneOf: [string, object]` 로 확장(객체 양식 = `{feature_id, query, source_hint}`)
- `agents/domain_modeling/system_prompt_kr.md` 의 §"search_query_hints 설계 원칙" 절 전면 재작성 — 객체 양식 + 토큰 중립화 + 검증 체크리스트 신설
- `server/graph/nodes/feature_url_mapper_node.py` 의 `_substitute_tokens` 에 `{candidate_name}` 추가 + `{competitor_name}` alias 후방 호환
- `prompt_version="domain_modeling:v0.12"` bump → 기존 도메인 taxonomy 자연 미스 처리

본 절은 §7-3(= D18 source_hint 라우팅) 과 **직교 차원** 의 개선이며, 동일 PR(v0.10.19.1) 에서 통합 도입됩니다(turn-36 사용자 결정).

### 7-3. source-type 별 hint 라우팅 규칙 (P1)

`url_discovery_blog_community_node` 등이 source-type 별로 다른 hints 를 사용하려면 hint 자체에 source-type 메타데이터가 필요합니다. v0.10.20 P1a-2 PR 에서 `search_query_hints` 항목을 객체로 승격하여 source-type 부여:

```json
"search_query_hints": [
  {
    "query":      "{candidate_name} 후기 환전 수수료",
    "source_hint": "blog_community"
  },
  {
    "query":      "{candidate_name} 트래블카드 유튜브 리뷰",
    "source_hint": "youtube_reactions"
  },
  ...
]
```

본 변경은 후방 호환을 위해 문자열 hint 도 지원하도록 schema 에 `oneOf` 로 정의.

---

## 8. §6-6a 수집 노드 6종과의 책임 경계

§6-6a 수집 노드(`community_collection`·`youtube_collection`·`youtube_channel_metadata_collection`·`blog_rss_collection`·`pr_release_collection`·`market_context_collection`) 와 본 재설계의 URL 탐색 노드는 시점·산출물이 다릅니다.

| 층 | 시점 | 입력 | 산출물 | 사용자가 보는 것 |
|---|---|---|---|---|
| URL 탐색 (본 재설계) | pre-feature_selection | candidates + search_query_hints | URL 목록 + 1차 메타데이터 + coverage 추정 | `feature_selection_node` UI 카드의 URL 상세 |
| 데이터 수집 (§6-6a) | post-feature_selection | URL + feature_id | 실제 본문·댓글·통계·후기 | 최종 리포트 본문 |

**책임 경계 표**

| §6-6a 수집 노드 | 1차 URL 발견 (본 재설계) | 책임 분리 |
|---|---|---|
| `community_collection_node` | `url_discovery_blog_community_node` (커뮤니티 도메인 한정 검색) | URL 발견 까지 본 재설계, 본문·게시글·반응 시그널은 §6-6a |
| `youtube_collection_node` | `url_discovery_youtube_reactions_node` | 영상 metadata 까지 본 재설계 (3rd-party 후기 영상만), 댓글·자막은 §6-6a |
| `youtube_channel_metadata_collection_node` | `url_discovery_owned_channels_node` (platform=`youtube_official`) | 채널 URL·channel_id·subscriber_count·verified 까지 본 재설계, 영상 목록·시계열 통계는 §6-6a |
| `blog_rss_collection_node` | `url_discovery_owned_channels_node` (platform=`blog_*`) | RSS feed URL 발견 까지 본 재설계, sitemap 파싱·전체 글 수집은 §6-6a |
| `pr_release_collection_node` | `url_discovery_owned_channels_node` (platform=`press_release`) | 보도자료 페이지 발견 까지 본 재설계, 개별 발표 본문은 §6-6a |
| `market_context_collection_node` | `url_discovery_macro_node` | 매크로 문서 URL·발행일 까지 본 재설계, PDF·표·통계 수치는 §6-6a |

**디렉토리 분리 사상**: `data/projects/{slug}/sources/{source_type}/urls.json` (URL 탐색 산출) vs `data/projects/{slug}/collection/{source_type}/data.json` (§6-6a 수집 산출). 두 층이 중복 작업하지 않도록 명확히 분리합니다.

---

## 9. 변경 단위 — PR 시리즈

| 버전                                              | 변경                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |                                     변경량                                     | 검증 게이트                                                                                                                                                                                                                                                                                                                 |
| ----------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | :-------------------------------------------------------------------------: | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **v0.10.18** (P0)                               | `source_flow` 필드 추가 + `_extract_active_reports` B-only 제외                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |                                   약 +10줄                                    | `analysis_features.report_type` 이 A·A+B 만 포함, positioning_map·executive_summary 제외 확인                                                                                                                                                                                                                                  |
| **v0.10.18a** *(turn-16 신설, P0-UI)*             | `feature_selection_node` 의 `_REPORT_INTRO_TEXTS` 정적 dict + reports_payload 가 B-only 카드 결합 + `FeatureSelectionPage.jsx` source_flow 별 안내 박스·URL 영역 조건부 렌더 (`url_coverage_visible`)·체크박스 비활성 분기                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |                     약 +190줄 (server +80 + client +110)                      | (i) 5종 흐름 A·A+B 카드는 v0.10.16 UI 유지 + 안내 문구 추가, (ii) 2종 B-only 카드 신규 노출 + 체크박스 자동 선택 비활성 + URL 영역 부재, (iii) interrupt value 의 `source_flow`·`intro_text`·`url_coverage_visible` 필드 정상 전달                                                                                                                                |
| **v0.10.19.1** *(turn-36 신설, §7-2 + D18 통합)*    | (1) `output.schema.json` `search_query_hints.items` 를 `oneOf: [string, object]` 로 확장 (객체 양식 = `{feature_id, query, source_hint}`). maxItems 8 → 30. (2) `system_prompt_kr.md` 의 §"search_query_hints 설계 원칙" 전면 재작성 — 객체 양식 + 토큰 중립화(`{candidate_name}` 권장) + 5종 source_hint enum + report_type 별 권장 분포 + LLM 자체 검증 체크리스트. (3) `feature_url_mapper_node.py` 에 `_LEGACY_SOURCE_TO_REPORT_TYPES` 상수 + `_extract_hints_for_source(active_reports, source_type)` 헬퍼 + `_discover_via_brave_with_hints` 신설. `_substitute_tokens` 에 `{candidate_name}` 신설 + `{competitor_name}` alias 후방 호환. (4) 5개 URL 탐색 노드 중 3개 실 구현(official · blog_community · macro) 의 hints 추출을 `_extract_hints_for_source` 호출로 교체. 발견 URL 메타에 `feature_ids` 부착. (5) `prompt_version="domain_modeling:v0.12"` bump → 옛 taxonomy 자연 미스. | 약 +250줄 (schema +35 + prompt +90 + 헬퍼 +95 + 3 노드 ×약 +10 + 진단·docstring +10) | (i) 신규 taxonomy 의 search_query_hints 가 객체 양식 + 3종 메타 모두 채워짐, (ii) 각 active feature 가 hints 에 ≥ 1회 등장(feature_id 기준), (iii) `{candidate_name}` 토큰 비율 ≥ 50%, (iv) 옛 string hints 가 `_LEGACY_SOURCE_TO_REPORT_TYPES` fallback 으로 정상 동작 검증, (v) URL 결과의 `feature_ids` 필드가 객체 hints 에서는 채워지고 string hints fallback 에서는 빈 배열 |
| **v0.10.19** (P1a-1)                            | 5개 source-type URL 탐색 노드 분리 + state 키 분리 + 토폴로지 1차 list-edge barrier                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |                                   약 +200줄                                   | 5개 신규 `*_urls_by_candidate` state 키 산출 + 각 source-type 캐시 file 생성 + entries ≥ 1                                                                                                                                                                                                                                        |
| **v0.10.20** (P1a-2)                            | `url_discovery_youtube_reactions_node` YouTube Data API v3 통합 — **reactions 단일 동작** (intent 분기 폐기). quota 관리                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |                                   약 +200줄                                   | YouTube API 호출 수 ≤ 일일 quota 절반(5,000 units), cache miss 첫 실행에서 reaction 영상 metadata 정상 수집                                                                                                                                                                                                                              |
| **v0.10.21** (P1a-3)                            | `url_discovery_owned_channels_node` Brave 검색 + LLM 검증 (`official_source_resolver` 패턴 재사용) + **YouTube 공식 채널 platform 흡수** (`channels.list` 1u 호출 포함)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |                                   약 +320줄                                   | candidate 4명 × 5 플랫폼(Instagram·X·블로그·보도자료·`youtube_official`) = 20개 핸들 중 80% 이상 발견 + confidence ≥ 0.7. `youtube_official` 항목은 `channel_id` 채워짐                                                                                                                                                                         |
| **v0.10.22** (P1b)                              | `url_discovery_macro_node` 전면 재작성 — 2-layer 화이트리스트 + Tier 그룹 site: + 2단계 fallback + candidate_id="macro" 단일 키 (D29~D32)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |                                   약 +290줄                                   | 화이트리스트 매칭률 ≥80% + feature 별 ≥2건 + 뉴스 보강 ≤30% + KOSIS·BoK·금융위 등 정부 통계 URL ≥ 3개                                                                                                                                                                                                                                          |
| **v0.10.22.1** (cleanup, turn-51)               | 옛 `url_discovery_brave_node.py` 삭제 + `feature_url_mapper_node.py`·`page_meta_collect_node.py`·`graph.py` docstring 의 옛 노드 언급 정리. 캐시 키(`agent_id="url_discovery_brave"`)·state 키(`brave_urls_by_candidate`) 는 호환성 위해 유지                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |                                 약 +35/-160                                  | grep -rn url_discovery_brave_node 결과 0건 + ast.parse 통과 + 분석 흐름 무회귀                                                                                                                                                                                                                                                     |
| **v0.10.22a** (P1a-4 신설, turn-51)               | `url_discovery_official_node` 정밀화 — `official_sources` carry-through + `site:` 한정 검색 + `origin` 분리 (`official_source`/`official_subpage`) + `subpage_category` 부착 + `_check_url_status` 도달성 검증 + 별도 캐시                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |                                   약 +250줄                                   | 도달 불가 URL 의 LLM 입력 진입 0건 + subpage_category 부착률 ≥ 80% + 토큰 비용 ≥ 15% 절감                                                                                                                                                                                                                                                 |
| **v0.10.22b** (P1a-5 신설, turn-51)               | `url_discovery_blog_community_node` 정밀화 — 공식 도메인 제외 (`-site:` 또는 결과 필터) + 외부 도메인 화이트리스트 우선 정렬 + `domain_class` 부착 (`review_site`·`personal_blog`·`community`·`wiki`) + 발행일 ≤ 36개월 + 본문 길이 ≥ 200자 검증 + 별도 캐시                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |                                   약 +200줄                                   | 공식 도메인 혼입 0건 + 화이트리스트 매칭률 ≥ 70% + reaction_insight `comp_*` `existing_urls` 외부 host 비율 ≥ 85%                                                                                                                                                                                                                           |
| ~~**v0.10.21a**~~ *(turn-11 폐기)*                | ~~`page_meta_collect_node` cross-reference 머지~~                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |                                      —                                      | v0.10.26 의 `cross_reference_node` 로 흡수                                                                                                                                                                                                                                                                                 |
| **v0.10.23** (P2 분배) *(turn-11 축소)*             | `agents/feature_mapping_<source>/system_prompt_kr.md` 5종 신설 (단일 prompt → source-type 별 5종 분배) + `agents/feature_url_mapper/system_prompt_kr.md` 유지 (공통 schema·예외 정책 referencing)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |                         약 +200줄 (5 prompt 평균 +40줄)                          | 5개 prompt 모두 jsonschema validate 통과. reaction_insight `comp_*` `existing_urls` 외부 host 비율 ≥ 75%                                                                                                                                                                                                                        |
| **v0.10.24** (P3)                               | `_fetch_meta` 에 `<h1>`/`<h2>` + 본문 첫 800자 수집 + 5개 `page_meta_<source>` 캐시 schema bump                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |                                   약 +40줄                                    | 정적 사이트 5개에서 본문 추출 성공률 100%                                                                                                                                                                                                                                                                                             |
| **v0.10.25** (P3)                               | `additional_urls_validation_node` source-type 별 검증 분기 (`videos.list`·`owned_channel` 검증) + D23 union 처리 (`_union_raw_features`)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |                                   약 +200줄                                   | YouTube `additional_urls` `view_count` 채움, owned_channel `is_brand_match` 채움, battlecard feature_id 의 candidate_coverage union 정상                                                                                                                                                                                      |
| **v0.10.26** *(turn-11 신설, P1c-1)*              | `cross_reference_node` 신설 + `page_meta_collect_node` 폐기 (헬퍼만 잔존)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |                                   약 +80줄                                    | mock owned channel ID 주입 시 reactions 결과에서 0건 잔존                                                                                                                                                                                                                                                                        |
| **v0.10.27** *(turn-11 신설, P1c-2)*              | `feature_mapping_<source>_node` 5종 신설 (`page_meta_collect_node` 와 `feature_mapping_llm_node` 의 동작이 통합 노드 내부로 이관). 각 노드 내부에 page_meta + LLM 매핑 직렬. `feature_mapping_llm_node` 단일 폐기                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |                                   약 +400줄                                   | 5개 통합 노드 각각 `*_raw_features` 산출, LLM 호출 수 8회(parallel=4), wall-clock 약 17분(cache miss)                                                                                                                                                                                                                                 |
| **v0.11** (중기)                                  | `playwright` SPA fallback (도메인 화이트리스트 트리거)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |                                   약 +150줄                                   | SPA 1건 fallback 본문 ≥ 200자                                                                                                                                                                                                                                                                                              |
| **v0.10.18.1** *(turn-26 진단 후속, 본 시리즈 완료 후 진행)* | `data/taxonomy/{id}_slug.json` 7일 TTL 캐시 가 `domain_modeling` 의 schema/prompt 변경 sha256 에 무관하게 hit 되어 신규 source_flow 등 schema 변경이 LLM 호출까지 도달하지 못하는 결함 해소. `server/graph/nodes/domain_modeling_node.py` 의 taxonomy 캐시 로딩 로직에 `schema_sha256` + `prompt_version` 비교 추가 + 변경 시 자동 무효화 + enrichment 트리거                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |                                   약 +25줄                                    | mock 시나리오 — output.schema 변경 후 분석 재실행 시 taxonomy 자동 무효화 + LLM 재호출 발생 + 신규 schema 필드가 새 taxonomy 에 반영                                                                                                                                                                                                                   |
| **v1.0** (근본)                                   | §6-6a 수집 노드 6종 구현 + URL 탐색 노드와의 인계 경계 확정                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |                               큰 PR (별도 문서 분리)                               | reaction_insight·marketing_social·market_context_swot 출력의 정합성                                                                                                                                                                                                                                                          |

> **v0.10.18.1 진행 시점 (사용자 결정 — turn-29)**
>
> 본 PR 은 turn-26 에서 진단된 구조적 결함이지만 즉시 진행하지 않고 **feature_url_mapper 시리즈(v0.10.19 ~ v0.10.27) 완료 후 v0.11 진입 전** 시점에 진행합니다. 이유:
>
> 1. v0.10.18 ~ v0.10.27 의 모든 schema/prompt 변경 시점에 동일 결함이 재발하지만, **3-1 옵션의 수동 처방** (`mv data/taxonomy/<id>_slug.json backup/taxonomy/`) 으로 우회 가능 — 운영 부담은 있으나 개발 진행에 차단되지 않음.
> 2. v0.10.18.1 의 변경이 `domain_modeling_node.py` 의 캐시 로직에 닿아 있어, feature_url_mapper 시리즈 진행 중에 도입하면 회귀 시 source 가 v0.10.18.1 인지 본 시리즈인지 추적이 어려움.
> 3. 본 시리즈 완료 시점에 D28 (taxonomy 캐시 schema-aware 정책) 의 정확한 운영 패턴이 확정되어 PR 설계가 더 안전.

### 9-1. PR 의존 그래프

```
v0.10.18 (source_flow)
   │
   ├─→ v0.10.18a (feature_selection UI 차별화)   ← v0.10.18 완료 후
   │
   ├─→ v0.10.19 (5 URL 탐색 분리)
   │       │
   │       ├─→ v0.10.19.1 (§7-2 + D18 통합 — 객체 hints + 토큰 중립 + source 라우팅)   ← v0.10.20 ~ v0.10.22 선행
   │       │       │
   │       │       ├─→ v0.10.20 (YouTube reactions API)
   │       │       ├─→ v0.10.21 (Owned channels LLM 검증 + youtube_official platform)
   │       │       └─→ v0.10.22 (Macro 화이트리스트)
   │              │
   │              ▼
   │       v0.10.26 (cross_reference_node 신설)    ← v0.10.20 + v0.10.21 양쪽 완료 후
   │              │
   │              ▼
   │       v0.10.27 (5 feature_mapping 통합 노드)   ← v0.10.26 + v0.10.23 완료 후
   │
   ├─→ v0.10.23 (5 source-type system_prompt 분배)  ← v0.10.19 완료 후, v0.10.27 의 선행
   ├─→ v0.10.24 (body 보강)                         ← 5 page_meta 캐시 모두에 적용
   └─→ v0.10.25 (validation 분기 + D23 union)        ← v0.10.27 완료 후
              │
              └─→ v0.11 (playwright)
                     │
                     └─→ v1.0 (§6-6a 수집 노드)
```

v0.10.18 가 모든 후속 PR 의 선행 조건. v0.10.19 가 source-type 별 PR(v0.10.20~v0.10.22) 의 선행 조건. v0.10.26 (cross_reference_node) 는 v0.10.20·v0.10.21 양쪽 완료 후. v0.10.27 (통합 노드 5종) 는 v0.10.26 + v0.10.23 완료 후. v0.10.25 는 v0.10.27 완료 후(union 처리 의존).

turn-11 옵션 (e) 채택으로 v0.10.21a 가 폐기되어 v0.10.26 의 `cross_reference_node` 신설로 흡수됩니다.

---

## 10. 위험·열린 결정

### 10-1. 신규 결정 항목

|                ID                 | 항목                                                        | 권장안                                                                                                                                                                        | 트레이드오프                                                                                                                                                                                                                                                                              |
| :-------------------------------: | --------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
|                D14                | X (트위터) 처리 정책                                             | 1차 핸들 발견까지만, 본문 metadata 미수집                                                                                                                                               | (a) 1차 제외 — D11 앱스토어 사상, (b) URL 발견만, (c) playwright 본문 fetch                                                                                                                                                                                                                       |
|                D15                | YouTube API quota 한도 도달 시 동작                              | `agent_steps[*].status="quota_skip"` 우회 + 부분 결과로 진행                                                                                                                        | (a) 한도 도달 시 전체 노드 fail, (b) 부분 결과로 진행 — 사용자 명시, (c) Brave 검색 fallback                                                                                                                                                                                                               |
|                D16                | Owned channels LLM 검증 confidence 임계                       | 0.7 미만 → `needs_validation=True` 로 UI 노출                                                                                                                                   | (a) 자동 채택 0.5 / 자동 거부 0.3 / 사이는 사용자, (b) 단일 임계 0.7, (c) 모두 사용자 검토                                                                                                                                                                                                                   |
|                D17                | 다중 공식 계정 발견 시 처리                                          | 모든 발견 핸들 반환 + `account_scope` 분류                                                                                                                                           | (a) primary 자동 선택, (b) 모두 반환 + UI 토글, (c) 사용자 매번 선택                                                                                                                                                                                                                                 |
|    **D18** *(turn-36 [x] 확정)*     | source-type 별 hint 라우팅 schema + §7-2 통합 (feature-hint 연결) | **(a) 객체 승격 + 문자열 후방 호환** — `search_query_hints.items` 가 `oneOf: [string, object]`. 객체 양식 = `{feature_id, query, source_hint}` 3종 메타. v0.10.19.1 에서 §7-2 와 통합 도입 (turn-36) | (a) **객체 승격 + 문자열 후방 호환 (채택)** — 변경 표면적 작음·점진 마이그레이션·LLM 부담 작음, (b) 별도 키 신설(`search_query_hints_typed`) — 두 키 병존 복잡도                                                                                                                                                                |
|                D19                | playwright 트리거 조건                                         | 도메인 화이트리스트 + `<title>` 부재 + HTML 길이 < 1KB                                                                                                                                  | (a) 도메인 화이트리스트만, (b) HTML heuristic 만, (c) 모든 비-OK 페이지                                                                                                                                                                                                                              |
|                D20                | `market_context_swot` 의 A 부분 처리                           | v1.0 까지 `url_discovery_macro_node` 가 1차 처리, v1.0 에서 `market_context_collection_node` 로 인계                                                                                  | (a) v1.0 까지 미처리(빈 결과), (b) `url_discovery_macro_node` 1차 처리                                                                                                                                                                                                                         |
| ~~D21~~ *(turn-7 신설, turn-11 폐기)* | ~~cross-reference 시점~~                                    | turn-11 옵션 (e) 채택으로 cross-reference 가 머지 시점이 아닌 별도 `cross_reference_node` 로 분리됨 → 본 항목 선택지 자체가 무효화. v0.10.26 PR 로 결정 종료                                                    | —                                                                                                                                                                                                                                                                                   |
|       **D22** *(turn-7 신설)*       | cross-reference 신선도 보장 방식                                 | `prompt_version` 동시 bump + `updated_at` 비교 후 stale 시 미스 처리. turn-11 옵션 (e) 채택 후 `cross_reference_node` 가 동일 그래프 실행 내 1회 발화되어 단순화                                           | (a) 두 노드 캐시 TTL 합산 최소값 강제, (b) `prompt_version` 동시 bump, (c) `updated_at` 비교, (d) (b) + (c) 결합                                                                                                                                                                                      |
|      **D23** *(turn-11 신설)*       | `battlecard` 등 A+B 흐름 feature 의 다중 source 매핑 통합 정책        | `feature_id` 동일 시 `candidate_coverage` union 처리 (`additional_urls_validation_node._union_raw_features`)                                                                    | (a) union 처리 — 다중 source 의 existing·additional URL 모두 보존, (b) 우선 source 단일 채택 — 정보 손실, (c) feature_selection UI 에서 두 source 카드 별도 노출 — UX 부담                                                                                                                                        |
|      **D24** *(turn-11 신설)*       | 통합 노드 내부 page meta 단계 부분 실패 정책                            | "부분 실패 허용 + LLM 입력에 빈 메타 carry-through" (개별 URL HTTP fail 은 `page_title=""` 로 전달, LLM 이 not_found 판정)                                                                      | (a) 부분 허용 + carry-through, (b) 임계 기반 노드 fail (예: page_meta fail 비율 ≥ 50% 시 노드 전체 fail), (c) 모든 URL 성공 시에만 LLM 진입 — 너무 보수적                                                                                                                                                           |
|      **D25** *(turn-11 신설)*       | 5개 통합 노드의 헬퍼 모듈 위치                                        | 현재 위치(`server/graph/nodes/feature_url_mapper_node.py`) 유지 + 통합 노드 5종 공유 import. v1.0 시점에 명시적 helper 파일 분리 검토                                                               | (a) `feature_url_mapper_node.py` 유지, (b) `_feature_url_mapper_helpers.py` 명시적 helper 파일로 분리 (책임 경계 명확), (c) 5개 통합 노드 각각에 동일 헬퍼 복제 — 코드 중복 위험                                                                                                                                        |
|      **D26** *(turn-11 신설)*       | 통합 노드 명명                                                  | `feature_mapping_<source>_node` 유지 (turn-11 답변 권장)                                                                                                                         | (a) `feature_mapping_<source>_node` 유지 — 변경 표면적 작음. "mapping" 표현이 page_meta 단계까지 포함하는지 모호, (b) `source_pipeline_<source>_node` 로 개명 — 책임 정확하나 mapping 표현 손실, (c) `meta_and_mapping_<source>_node` 로 개명 — 길어짐                                                                        |
|  **D27** *(turn-16 신설, [x] 확정)*   | feature_selection UI 의 source_flow 별 안내 문구 데이터 위치         | **(b) `server/graph/nodes/feature_selection_node.py` 의 `_REPORT_INTRO_TEXTS` 정적 dict**                                                                                     | (a) `agents/domain_modeling/output.schema.json` 의 `intro_text` 필드 — 도메인 특수성 반영 가능하나 LLM 변동 + cache invalidation 부담, (b) **정적 dict — 결정론적·도메인 무관·변경 표면적 작음 (채택)**, (c) `client/src/components/FeatureSelectionPage.jsx` 의 상수 dict — server 가 source_flow 만 전달하면 되나 client 도메인 의존성 발생 |
| **D28** *(turn-26 진단·turn-29 신설)* | `data/taxonomy/{id}_slug.json` 7일 TTL 캐시의 schema-aware 정책 | **(a) `domain_modeling_node` 의 taxonomy 캐시 로딩에 `output_schema_sha256` + `system_prompt_sha256` + `prompt_version` 비교 추가 + 변경 감지 시 자동 무효화** + enrichment 트리거                | (a) **taxonomy 파일 헤더에 schema_sha256·prompt_version 메타데이터 저장 후 로딩 시 비교 (채택)** — 변경 표면적 작음·결정론적, (b) taxonomy 파일을 schema 별 디렉토리로 분리 (`data/taxonomy/{schema_v}/{id}_slug.json`) — 디스크 비대, (c) taxonomy 캐시 자체 폐기 — domain_modeling LLM 호출이 매 분석마다 발생 (비용 증가)                           |
| **D29** *(turn-50 신설, [x] 확정)* | `url_discovery_macro_node` 의 도메인 화이트리스트 위치 | **(c) 2-layer 혼합 (정적 코어 + 동적 도메인 의존)** | (a) 정적 코드 하드코딩만 — 도메인 의존 출처 부재, (b) `domain_taxonomy` 동적 생성만 — 정적 코어 부재 시 LLM 누락 가능, (c) **2-layer 혼합 — 안정성 + 도메인 정합 (채택)** |
| **D30** *(turn-50 신설, [x] 확정)* | Brave `site:` 연산자 적용 방식 | **(c) Tier 그룹화 3쿼리 (Tier1·Tier2·Tier3)** | (a) 전체 도메인 OR 조합 1쿼리 — Brave `site:` OR 토큰 한계 (~5건), (b) 도메인별 1쿼리 — hint 1건당 14 호출 비용 증가, (c) **Tier 그룹화 3쿼리 — 호출 수 (hint × 3) + 우선순위 (채택)** |
| **D31** *(turn-50 신설, [x] 확정)* | 뉴스 보강 진입 임계 | **(b) feature 별 < 2건 → Stage 2 진입** | (a) Stage 1 결과 0건 → 진입 — 1~2건 발견 시 뉴스 보강 누락, (b) **feature 별 < 2건 → 진입 (채택)** — 이미지의 "미확보 2~3" 빈도 해소, (c) 항상 진입 — 비용·중복 증가 |
| **D32** *(turn-50 신설, [x] 확정)* | macro URL 의 candidate_id 키 형식 | **(a) `"macro"` 단일 키** | (a) **`"macro"` 단일 키 — 매크로 본질 정합 (채택)**, (b) `"own"` 키 사용 — own_product 와 macro 가 같은 키 의미 혼란, (c) feature_id 별 키 — 후속 노드 union 처리 복잡 |
| **D33** *(turn-52 신설, [x] 확정)* | `url_discovery_official_node` 의 hint 활용 방식 | **(a) `source_hint="official"` hints + 정적 sub-page 키워드 7건 보강** | (a) **LLM 추천 hint + 정적 한국 카테고리 (약관·수수료·환율·한도·혜택·공지사항·이용안내) 결합 — 채택**, (b) `categories` 직접 부착 — Rubric 카테고리가 영문이라 site: 검색에 비효율, (c) 자체 정적 query template 만 — domain_taxonomy 의 LLM 추천 의도 무시 |
| **D34** *(turn-52 신설, [x] 확정)* | `official_domain` 부재 candidate 처리 | **(a) site: 검색 스킵 + carry-through 만 유지** | (a) **resolver 의 책임 분리 — URL 발견 실패는 `official_source_resolver_node` 책임 (채택)**, (b) `site:` 없이 일반 검색 — 다른 source-type 노드와 책임 중첩, (c) `official_source_resolver` 재실행 — 비용 증가 + 결정론성 저하 |

본 결정 항목은 사용자 검토 후 본 문서의 §10 에 [x] 체크로 확정합니다. turn-11 옵션 (e) 채택으로 **D21 은 폐기**되었습니다 (cross-reference 가 머지 시점이 아닌 별도 노드로 분리되어 선택 폭 자체가 무효화).

### 10-2. 운영 리스크

**Brave API rate limit (1 req/sec 무료)**: 5중 fan-out 시 순간 호출량이 5배. 각 노드 `ThreadPoolExecutor max_workers=2` 제한 + 모듈 글로벌 `threading.Semaphore(1)` 직렬화 권장.

**YouTube API 일일 quota 10,000 units**: cache miss 첫 실행 시 candidate 4명 × 2 intent × 평균 호출 = 약 1,600 units. 동일 도메인 재실행 시 24h TTL 캐시 hit 으로 0 units. 일일 한도 도달 위험은 낮으나 quota_budget 모니터링 필수.

**LLM 검증 비용**: `url_discovery_owned_channels_node` 의 LLM 검증은 candidate × platform 단위로 호출되어 candidate 4명 × 6 플랫폼 = 24회. v0.7 turn-49 결정으로 `ClaudeCodeCliAnalyzer` 채택 — Claude Pro/Max 구독 토큰 사용 → API 과금 0 + 7일 TTL 캐시로 재실행 비용 절감.

**X (트위터) 무료 read 제거**: 2023년 이후 무료 tier read 권한 거의 제거 — D14 결정 항목.

**Instagram Graph API 비즈니스 계정 요구**: 본문 metadata 수집은 사실상 불가 — 핸들 발견 + bio HTTP GET 까지만.

**다중 공식 계정 모호성**: 모회사·서브브랜드·상품·지역별 별도 계정 — D17 결정 항목.

**`prompt_version` bump 시 캐시 무효화 범위**: feature_url_mapper LLM (v0.11) bump 시 옛 캐시 11건이 자연 미스. 디스크는 점진 폐기 또는 30일 mtime 기반 cron 정리.

**§6-6a 인계 경계의 사전 합의**: v1.0 PR 진행 시 URL 탐색과 데이터 수집의 책임 경계 모호 시 두 층이 중복 작업할 위험. §8 의 디렉토리 분리 사상 + 책임 표를 v1.0 PR 의 디자인 결정에 명시적으로 포함.

---

## 11. 검증 게이트

### 11-1. PR 별 검증 게이트 (요약, §9 의 검증 게이트 재정리)

| PR | 검증 기준 | 측정 방법 |
|---|---|---|
| v0.10.18 | B-only 리포트 제외 | `analysis_features.report_type` 분포에 `positioning_map`·`executive_summary` 0건 |
| **v0.10.18a** *(turn-16 신설)* | feature_selection UI 차별화 | (i) interrupt value 의 7종 리포트 카드 모두 노출 + 각 카드의 `source_flow`·`intro_text`·`url_coverage_visible` 필드 채워짐, (ii) 흐름 A·A+B 5종 카드는 v0.10.16 의 URL coverage 렌더 유지, (iii) B-only 2종 카드는 features 만 표시 + URL 영역 미렌더 + 체크박스 자동 선택 비활성, (iv) 안내 박스 색상 source_flow 별 차별화 (A=neutral·A+B=amber·B=blue) |
| v0.10.19 | 5 URL 탐색 분리 동작 | 5개 신규 `*_urls_by_candidate` state 키 산출 + 각 source-type 캐시 file 생성 + entries ≥ 1 |
| **v0.10.19.1** *(turn-36 신설)* | §7-2 + D18 통합 — 객체 hints + 토큰 중립 + source 라우팅 | (i) 신규 `data/taxonomy/{id}_slug.json` 의 search_query_hints 가 모두 객체 양식 (string 0건), (ii) 모든 active feature 가 hints 에서 ≥ 1회 등장 (feature_id 기준), (iii) `{candidate_name}` 토큰을 포함한 hint 비율 ≥ 50%, (iv) source_hint 분포가 권장 비율 ±10% 범위, (v) URL 결과의 `feature_ids` 필드가 객체 hints 시점에 ≥ 1개 채워짐 |
| v0.10.20 | YouTube reactions 영상 수집 | candidate 4명 × 3 쿼리 = 영상 ≥ 30개 + view/like/comment count 모두 채워짐. `channel_id` 100% 채움 |
| v0.10.21 | Owned channels 발견 (5 platforms) | 4 candidates × 5 platforms = 20 핸들 중 16개(80%) ≥ confidence 0.7. `youtube_official` 항목 100% `channel_id` 채움 |
| v0.10.22 | Macro URL 수집 | 트래블카드 도메인에서 KOSIS·BoK·금융위·NIA 등 화이트리스트 도메인 ≥ 3개 + feature 별 ≥ 2건 + 화이트리스트 매칭률 ≥ 80% + 뉴스 보강 trigger rate ≤ 30% |
| v0.10.23 | 5 source-type system_prompt 분배 | 5개 prompt 모두 jsonschema validate 통과. reaction_insight `comp_*` 외부 host 비율 ≥ 75%, not_found 비율 < 50% |
| v0.10.24 | body 보강 효과 | 정적 사이트 5개 추출 성공률 100%, `body_snippet` 평균 ≥ 500자 |
| v0.10.25 | validation 분기 + D23 union | YouTube 검증 `view_count` 100% 채움, Owned channel 검증 `is_brand_match` 100% 채움, battlecard `feature_id` 의 `candidate_coverage` union 정상 (official + owned_channels 양쪽 URL 포함) |
| **v0.10.26** *(turn-11 신설)* | `cross_reference_node` 동작 | mock owned channel ID 주입 시 `youtube_reactions_urls_by_candidate` 에서 0건 잔존. 토폴로지 검증에서 1차 5중 fan-in 평탄화(`cross_reference` 노드 출현) 확인 |
| **v0.10.27** *(turn-11 신설)* | 5 통합 노드 동작 | 5개 `*_raw_features` state 키 산출. LLM 호출 수 8회. wall-clock cache miss ≤ 20분 (옵션 d 시 17분 추정). 각 통합 노드의 `set_progress` 가 source-type 별 2 stage 정상 emit. 토폴로지 검증에서 2차 5중 fan-in 평탄화 확인 |
| v0.11 | playwright SPA | `travel-wallet.com` body_snippet ≥ 200자 + headings ≥ 1개 |
| **v0.10.18.1** *(turn-26 진단 후속)* | taxonomy 캐시 schema-aware 무효화 | mock 시나리오 — `agents/domain_modeling/output.schema.json` 의 한 필드 추가(예: dummy 필드) 후 분석 재실행 시 (i) `data/taxonomy/{id}_slug.json` 의 `schema_sha256` 가 변경 감지 (ii) domain_modeling LLM 재호출 발생 (iii) 신규 taxonomy 의 dummy 필드 반영 — 3건 모두 만족 |
| v1.0 | §6-6a 인계 | URL 탐색 산출 → 수집 산출 디렉토리 분리 + 중복 호출 0건 |

### 11-2. 통합 회귀 테스트

`scripts/verify_feature_url_mapper_redesign.py` 신설:

1. 토폴로지 검증 — `compiled_graph.get_graph().draw_ascii()` 의 **2 차례 5중 fan-out + list-fan-in 평탄화** 확인 (`url_discovery_*` 5종 + `cross_reference` + `feature_mapping_*` 5종 노드명 모두 출현)
2. 부분 실패 시뮬레이션 — `url_discovery_youtube_reactions_node` 의도적 실패 시 다른 4개 URL 탐색 노드 정상 진행 + `cross_reference_node` 빈 youtube_reactions 입력 정상 통과 + `feature_mapping_youtube_reactions_node` skip 또는 빈 결과 산출 + `feature_selection` 빈 reactions 카드 정상 렌더
3. 캐시 hit 결정론성 — 동일 domain + selected_competitor_ids 재실행 시 5개 URL 탐색 노드 + 5 page_meta 캐시 + 5 LLM 캐시 모두 hit + LLM 호출 0회
4. **Cross-reference 검증 (turn-11 갱신)** — owned_channels 의 `youtube_official` 채널 ID 가 reactions 결과의 `channel_id` 와 일치하는 mock 영상을 주입한 후 `cross_reference_node` 후처리 결과에서 0건 잔존 확인
5. **통합 노드 내부 2 stage 가시화 (turn-11 신설)** — 5 통합 노드 각각의 `set_progress(stage="<source>_meta")` → `set_progress(stage="<source>_llm")` emit 순서 확인 (총 10 stage)
6. **D23 union 검증 (turn-11 신설)** — `feature_id="feat_battlecard_xxx"` 가 `official_raw_features` 와 `owned_channel_raw_features` 양쪽에 존재하는 mock 입력에서 `additional_urls_validation_node._union_raw_features` 출력의 `candidate_coverage` 가 두 source 의 URL 모두 포함하는지 확인

---

## 12. 변경 이력

| 버전 | 일자 | 변경 내용 | 비고 |
|:-:|---|---|---|
| 1.0 | 2026-06-04 | **v0.10.22a — `url_discovery_official_node` 정밀화 실 구현 (turn-52)** — (1) §5-1 의 5가지 정밀화 책임 일괄 도입 (carry-through · site: 한정 · origin 분리 · subpage_category · `_check_url_status` 검증). (2) **D33·D34 결정 항목 [x] 확정** — D33 (a) `source_hint="official"` hints + 정적 sub-page 키워드 7건 보강 · D34 (a) `official_domain` 부재 candidate 는 site: 검색 스킵 + carry 만 유지. (3) **정적 한국어 sub-page 키워드 7건** — 약관·수수료·환율·한도·혜택·공지사항·이용안내. (4) **처리 흐름 6단계** — carry-through (official_sources → origin="official_source") → hints 추출 → site: 검색 작업 목록 (정적 키워드 + LLM hints) → 병렬 Brave + `_host_endswith` 재검증 → `_check_url_status` 병렬 도달성 검증 (2xx·3xx 만 통과) → carry+subpage 머지 (URL dedup). (5) **헬퍼 3종 신설** — `_extract_official_domain(primary_url)` (`urlparse(...).hostname` + `www.` strip) · `_build_subpage_query(name, domain, keyword)` · `_host_endswith(url, domain)`. (6) `_filter_candidates_for_report` 의 origin 주석 — `"official_subpage" 예정"` → `"확정 — site: 한정 + 도달성 검증 + subpage_category 부착"` 갱신. (7) **§5-1 전면 재작성** — 처리 흐름 6단계 + 검증 게이트 5건 + graceful 종료. (8) **§10 D33·D34 신설 [x] 확정**. (9) 영향 파일: `server/graph/nodes/url_discovery_official_node.py` (전면 재작성, 320줄) · `server/graph/nodes/feature_url_mapper_node.py` (+1, 주석 갱신) · 본 문서 (+80). 합계 약 +400/-50. | DRAFT, §10 D14~D17·D19·D20·D22~D26 결정 대기 (D18·D21·D27~D34 [x] 확정) |
| 0.9 | 2026-06-04 | **v0.10.22.1 cleanup + v0.10.22a/v0.10.22b 신설 (turn-51)** — (1) **사용자 점검 (turn-51)**: 5중 fan-out (1차) 의 5개 노드 중 `url_discovery_official_node` · `url_discovery_blog_community_node` 가 v0.10.19 스켈레톤 단계로 정밀화 미진행 상태임을 확인. 설계 문서 §5-1·§5-2 의 정밀화 책임 (site: 한정·`_check_url_status`·subpage_category·domain_class·외부 도메인 화이트리스트) 이 통합 노드 (§5-6a) 에서 흡수되지 않음을 의존성 표로 확인. (2) **진행 옵션 (A) 3 PR 분리 채택** — v0.10.22.1 cleanup + v0.10.22a (official 정밀화) + v0.10.22b (blog_community 정밀화). (3) **v0.10.22.1 cleanup 진행** — 옛 `server/graph/nodes/url_discovery_brave_node.py` 파일 삭제 (graph.py 호출 없음 확인). `feature_url_mapper_node.py`·`page_meta_collect_node.py`·`graph.py` docstring 의 옛 노드 언급 정리. 캐시 키 `agent_id="url_discovery_brave"` 와 state 키 `brave_urls_by_candidate` 는 기존 24h TTL 캐시 호환성 + `urls_merge_node` 의 활성 매개체이므로 **변경 금지**. (4) **§9 PR 시리즈 갱신** — v0.10.22.1 (cleanup) + v0.10.22a (official 정밀화 약 +250줄) + v0.10.22b (blog_community 정밀화 약 +200줄) 3 entry 신설. (5) **v0.10.22a 책임** — `official_sources` carry + `site:` 한정 + `origin` 분리 + `subpage_category` + `_check_url_status` + 별도 캐시. (6) **v0.10.22b 책임** — 공식 도메인 제외 + 외부 도메인 화이트리스트 정렬 + `domain_class` + 발행일/본문 검증 + 별도 캐시. (7) 변경 파일: `server/graph/nodes/url_discovery_brave_node.py` (삭제) · `server/graph/nodes/feature_url_mapper_node.py` (docstring +25/-15, line 11 5중 fan-out 갱신·line 85 본 모듈 import 노드 목록 갱신·line 709 origin 주석 갱신) · `server/graph/nodes/page_meta_collect_node.py` (docstring +5/-3 line 9·15 갱신) · `server/graph/graph.py` (docstring +3/-2 line 24·77·228 갱신) · 본 문서 (+30) | DRAFT, §10 D14~D17·D19·D20·D22~D26 결정 대기 (D18·D21·D27~D32 [x] 확정) |
| 0.8 | 2026-06-04 | **`url_discovery_macro_node` 전면 재작성 — 2-layer 화이트리스트 + Tier 그룹 + 2단계 fallback + candidate_id="macro" (turn-50)** — (1) **사용자 지적 (turn-50)**: macro feature 는 산업·시장 수준 데이터이므로 candidate 비종속. 다른 노드의 `{candidate_name}` 치환 hint 로직을 적용하면 잘못된 URL 탐색. (2) **D29~D32 4개 결정 일괄 확정** — D29 (c) 2-layer 화이트리스트 (정적 코어 11건 + 동적 도메인 의존) · D30 (c) Tier 그룹 3쿼리 · D31 (b) feature 별 <2건 → Stage 2 진입 · D32 (a) `candidate_id="macro"` 단일 키. (3) **정적 화이트리스트 (한국 정부·통계·연구 17건)** — Tier 1 (통계 핵심 3): `kosis.kr`·`ecos.bok.or.kr`·`index.go.kr` / Tier 2 (정책·연구 8): `fsc.go.kr`·`mosf.go.kr`·`fss.or.kr`·`bok.or.kr`·`kdi.re.kr`·`kiet.re.kr`·`nia.or.kr`·`kotra.or.kr` / 뉴스 보강 (6): `yna.co.kr`·`hankyung.com`·`mk.co.kr`·`mt.co.kr`·`etnews.com`·`dt.co.kr`. (4) **동적 화이트리스트 — `agents/domain_modeling/output.schema.json` 의 `reportEntry.macro_data_sources` 신설** — 0~8건, TLD 패턴 강제 (`*.go.kr`·`*.or.kr`·`*.re.kr`·`*.ac.kr`·`*.kr`). `system_prompt_kr.md` 의 macro 출처 추천 규칙 절 신설 (정적 코어 중복 기재 금지 + TLD 가이드 + 도메인별 예시 4건 — 해외여행/핀테크/헬스케어/모빌리티). (5) **Brave `site:` 연산자 Tier 그룹화** — hint 1건당 Tier1·Tier2·Tier3 각 1쿼리 = 최대 3쿼리. `(site:a OR site:b OR ...)` 형식. Tier 3 도메인 없으면 스킵. (6) **2단계 fallback** — Stage 1 (공식) 완료 후 feature 별 < 2건 결손 시 Stage 2 (뉴스 보강) 진입. 결손 feature 의 hint 만 뉴스 화이트리스트 대상 재검색. `source_tier` 필드 (`official_statistics` / `news_supplement`) 부착. (7) **화이트리스트 매칭 검증** — Brave 의 `site:` 누락 대비 `_host_matches` 헬퍼로 결과 host 재검증. 미매칭 URL 제외. (8) **`candidate_id="macro"` 단일 키 집계** — `_substitute_domain_only` 헬퍼로 `{domain_name}` 만 치환, candidate 차원 폐기. `_build_candidates_with_meta` 에 `source_type="macro"` 분기 신설 — 후속 v0.10.23 LLM 매핑이 macro candidate 를 자사·경쟁사와 분기 처리 가능. `_filter_candidates_for_report` 본체는 v0.10.20.1 일관 처리로 macro_search origin 자동 통과 (변경 불필요, 주석만 "예정"→"확정" 갱신). (9) 영향 절: §5-5 전면 재작성 (Tier 표·처리 흐름 7단계·검증 게이트·캐싱·`_build_candidates_with_meta` 정합 절 신설) · §9 v0.10.22 entry 갱신 (약 +120줄 → +290줄) · §10 D29~D32 신설 [x] 확정 · §11 검증 게이트 보강 · §12 v0.8 entry. (10) 변경 파일: `agents/domain_modeling/output.schema.json` (+20) · `agents/domain_modeling/system_prompt_kr.md` (+30) · `server/graph/nodes/url_discovery_macro_node.py` (전면 재작성, 350줄) · `server/graph/nodes/feature_url_mapper_node.py` (+15, `_build_candidates_with_meta.source_type` 분기 + 주석 갱신) · 본 문서 (+150). 합계 약 +565줄. | DRAFT, §10 D14~D17·D19·D20·D22~D26 결정 대기 (D18·D21·D27·D28·D29·D30·D31·D32 [x] 확정) |
| 0.7 | 2026-06-04 | **owned_channels LLM 어댑터 CLI 전환 + marketing_social 안내 문구 강화 (turn-49)** — (1) `url_discovery_owned_channels_node` 의 LLM 검증 단계를 `ClaudeApiAnalyzer(temperature=0)` 에서 `ClaudeCodeCliAnalyzer` 로 전환. 본 노드의 LLM 검증은 `ProductIdResolver` 같은 완전 결정론(slug 생성) 영역이 아니고 URL 의 `official` 접미사·snippet 의 "공식" 키워드 등 명확한 시그널 기반이라 CLI 의 자연어 수준 결정론으로 흡수 가능. 시리즈 전체 일관 패턴 유지 + API 과금 회피(약 $0.50/분석 → $0). (2) 영향 파일: `server/graph/nodes/url_discovery_owned_channels_node.py` import + `ANTHROPIC_API_KEY` graceful 분기 제거 + `ClaudeCodeCliAnalyzer(model=CLI_MODEL, timeout=CLI_TIMEOUT)` 인스턴스 + `cache_context.prompt_version` `v0.10.21.1` 로 갱신, `agents/url_discovery_owned_channels/system_prompt_kr.md` 첫 단락 분석기 표현 변경 (`ClaudeApiAnalyzer(temperature=0)` → `ClaudeCodeCliAnalyzer` + 자연어 결정론 완화 전략 설명). (3) `feature_selection_node._REPORT_INTRO_TEXTS["marketing_social"]` 강화 — URL 발견 단계와 §6-6a feature 값 수집 단계를 분리 명시 ("※ feature 값 수집은 v1.0 §6-6a 도입 후 자동 진행"). 사용자가 v0.10.21 ~ v1.0 사이 `marketing_social` 카드 coverage 의미 명확화. (4) `Future_Improvements.md` 신설 — "ClaudeApiAnalyzer 활용 후보 노드 검토" 항목 추가. (5) §4-3·§5-4·§10 비용 절 — LLM 검증 어댑터 표기 `ClaudeApiAnalyzer` → `ClaudeCodeCliAnalyzer` + 비용 0 + 시리즈 일관 패턴 명시. (6) `server/llm/claude_api_analyzer.py` 보존 (향후 결정론 필수 노드 활용 가능). 변경량 약 +35줄(node) +10줄(prompt) +5줄(intro_text) +60줄(Future_Improvements.md) +설계 문서 30줄. | DRAFT, §10 D14~D17·D19·D20·D22~D26 결정 대기 (D18·D21·D27·D28 [x] 확정) |
| 0.1 | 2026-06-02 | 초안 작성 — turn-3 ~ turn-5 결정 통합. AS-IS 결함 4건·TO-BE 5중 fan-out·핵심 변경 5건(P0·P1·P1·P2·P2~중기)·노드별 상세 8건·state·캐시·토폴로지·domain_modeling 영향·§6-6a 인계 경계·PR 시리즈 10개·결정 항목 D14~D20 7개·운영 리스크·검증 게이트 일괄 정리 | DRAFT, §10 D14~D20 결정 대기 |
| 0.2 | 2026-06-02 | **노드 책임 재정의 (turn-7)** — (1) `url_discovery_youtube_node` → **`url_discovery_youtube_reactions_node` 개명·축소** (reaction_insight 3rd-party 영상 탐색 단일 동작, `intent` 분기 폐기). (2) `url_discovery_owned_channels_node` 범위 확대 — Instagram·X·블로그·보도자료에 더해 **YouTube 공식 채널** platform(`youtube_official`) 흡수. `channels.list?forHandle=...`(1u) 추가 호출로 `channel_id` 확정. (3) **`reactions × owned_channels` cross-reference 머지 신설** (`page_meta_collect_node._filter_reactions_excluding_owned_channels`) — 결정론적 LLM 미사용 채널 ID 매칭으로 자사·경쟁사 공식 채널이 자체 상품을 직접 리뷰하는 edge case 차단. (4) 영향 절: §3-1 토폴로지 다이어그램·§3-3 호출 횟수 영향·§4-2 5분리 표·§4-3-2 Brave 쿼리 목록·§5-3 전면 재작성·§5-4 platform 6종 보강·§5-6 머지 정책 cross-reference·§5-7 `_origin_matches_report_type` 표·§6-1 state 키 개명·§6-2 캐시 키 일람·§6-3 토폴로지 코드·§8 책임 경계 표·§9 PR 시리즈 (v0.10.20 축소·v0.10.21 확장·**v0.10.21a 신설**)·§9-1 의존 그래프·§10 결정 항목 **D21·D22 신설**·§11 검증 게이트 v0.10.21a 추가·§11-2 cross-reference 검증 항목 추가 | DRAFT, §10 D14~D22 결정 대기 |
| 0.6 | 2026-06-03 | **§7-2 재정의 + D18 [x] 확정 + v0.10.19.1 통합 PR 신설 (turn-33 ~ turn-36)** — (1) §7-2 재정의: v0.5 의 "토큰 분포 own/comp/both = 1:1:1 균형" 규칙 폐기. 사용자 의도 (turn-35) — 각 feature 가 자사·경쟁 상품 모두에 적용되도록 hint 구조 자체를 개선 — 으로 재작성. 두 차원의 구조 개선(feature-hint 명시 연결 + 토큰 중립화). (2) D18 [x] 확정: 옵션 (a) 객체 승격 + 문자열 후방 호환. `search_query_hints.items` 가 `oneOf: [string, object]`. 객체 양식 = `{feature_id, query, source_hint}` 3종 메타. (3) §9 v0.10.19.1 entry 신설 — §7-2 + D18 을 단일 PR 로 통합 도입. v0.10.20·v0.10.21·v0.10.22 의 선행 PR. 변경량 약 +250줄. (4) §9-1 의존 그래프 갱신 — v0.10.19 → v0.10.19.1 → (v0.10.20·v0.10.21·v0.10.22). (5) §11 v0.10.19.1 검증 게이트 5건 추가. (6) `Future_Improvements.md` 신설 (옵션 B `features` 객체 승격을 향후 URL 탐색 정확도 문제 지속 시 재검토 사항으로 기록). | DRAFT, §10 D14~D20·D22~D26 결정 대기 (D21 폐기, D27·D28·D18 [x] 확정) |
| 0.5 | 2026-06-03 | **v0.10.18.1 PR 신설 — taxonomy 캐시 schema-aware 보강 (turn-26 진단 후속)** — (1) `data/taxonomy/{id}_slug.json` 7일 TTL 캐시가 `domain_modeling` 의 schema/prompt 변경 sha256 에 무관하게 hit 되어 v0.10.18 source_flow 적용 시 mv backup 수동 처방이 필요했던 결함의 영구 해소 PR. (2) **§9 v0.10.18.1 entry 신설** — 진행 시점은 **feature_url_mapper 시리즈(v0.10.19 ~ v0.10.27) 완료 후 v0.11 진입 전** (사용자 결정 turn-29). 변경량 약 +25줄. 본 시리즈 진행 중에는 mv backup 수동 처방으로 우회. (3) **§10 D28 신설** — taxonomy 캐시 schema-aware 정책 옵션 (a)·(b)·(c) 비교 + (a) 채택 권장 (`domain_modeling_node` 캐시 로딩에 sha256·prompt_version 비교 + 자동 무효화). (4) §11 검증 게이트 v0.10.18.1 항목 추가 — mock schema 변경 시나리오로 3건 정합 검증. | DRAFT, §10 D14~D20 + D22~D26 결정 대기 (D21 폐기, D27·D28 [x] 확정) |
| 0.4 | 2026-06-02 | **feature_selection UI 차별화 + B-only 리포트 카드 노출 (turn-16)** — (1) §4-1-1 신설 — "B-only 제외" 의 정확한 의미를 Layer 1·2·3·4 매트릭스로 명확화 (Layer 2 만 생략, Layer 1·3·4 는 정상 처리). (2) **§5-9 신설** — `feature_selection_node` UI 사양 정의. `_REPORT_INTRO_TEXTS` 정적 dict (7종 안내 문구) + `_build_reports_payload` 가 `analysis_features` (5종) + `domain_taxonomy.report_config` 의 B-only (2종) 결합 + interrupt value 에 `source_flow`·`intro_text`·`url_coverage_visible` 추가. client UI 사양 (안내 박스 색상 분기·URL 영역 조건부 렌더·B-only 체크박스 비활성). (3) **§9 v0.10.18a PR 신설** — server +80줄 + client +110줄 = 약 +190줄. v0.10.18 의존. (4) **§10 D27 신설 [x] 확정** — 옵션 (b) 정적 dict 채택. 결정론적·도메인 무관·변경 표면적 작음. (5) §11 검증 게이트 v0.10.18a 4건 추가. | DRAFT, §10 D14~D20 + D22~D26 결정 대기 (D21 폐기, D27 [x] 확정) |
| 0.3 | 2026-06-02 | **옵션 (e) 채택 — page_meta + LLM 통합 5개 노드 + cross_reference 별도 노드 (turn-11)** — (1) `page_meta_collect_node` **폐기** + `feature_mapping_llm_node` **폐기** → **5개 source-type 통합 노드 신설** (`feature_mapping_official` · `feature_mapping_blog_community` · `feature_mapping_youtube_reactions` · `feature_mapping_owned_channels` · `feature_mapping_macro`). 각 노드 내부에 page meta 수집 + report_type 별 병렬 LLM 매핑 직렬 수행. (2) `cross_reference_node` **별도 노드로 분리** — 5중 URL 탐색 fan-in 직후 1회 발화, youtube_reactions × owned_channels(youtube_official) 결정론적 후처리 필터링. v0.10.21a 의 머지 함수가 본 노드로 이관. (3) state 키 재구성 — `candidates_with_meta`·`raw_features` 폐기, 5종 `*_raw_features` (`official_raw_features` · `blog_community_raw_features` · `youtube_reactions_raw_features` · `owned_channel_raw_features` · `macro_raw_features`) 신설. (4) 캐시 키 분산 — `page_meta_collect` 단일 → 5종 `page_meta_<source>`, `feature_url_mapper` LLM 단일 → 5종 `feature_mapping_<source>`. source-type 별 부분 hit 활용 가능. (5) system_prompt 5종 분배 — `agents/feature_mapping_<source>/system_prompt_kr.md` 5개 신설. (6) `additional_urls_validation_node` 보강 — 5종 `*_raw_features` union 처리 (`_union_raw_features`) + battlecard 등 다중 source feature 의 `candidate_coverage` 결정론적 통합 (D23). (7) PR 시리즈 갱신 — v0.10.21a 폐기, v0.10.23 축소 (5종 prompt 분배), v0.10.26 신설 (`cross_reference_node`), v0.10.27 신설 (5 통합 노드). v0.10.25 가 v0.10.27 의존으로 이동. (8) 결정 항목 — D21 폐기, **D23·D24·D25·D26 4건 신설** (battlecard union 정책·부분 실패 정책·헬퍼 모듈 위치·통합 노드 명명). (9) 영향 절: §3-1 다이어그램·§3-2 노드 수 13개·§3-3 호출 횟수·§4-2 5분리 표·§4-4 prompt 분배·§5-6 cross_reference_node + 5 통합 노드 전면 재작성·§5-7 폐기·§5-8 union 처리 보강·§6-1 state 키·§6-2 캐시 키 일람·§6-3 토폴로지 코드 (2 차례 list-fan-in)·§9 PR 시리즈·§9-1 의존 그래프·§10 D21 폐기 + D23~D26 신설·§11 검증 게이트 + §11-2 통합 회귀 테스트 6건으로 확장 | DRAFT, §10 D14~D20 + D22~D26 결정 대기 |

---

## 13. 부록 — 본 문서가 대체 또는 보완하는 선행 문서 항목

| 선행 문서 위치 | 본 문서에서의 대체·보완 |
|---|---|
| `pipeline_topology_redesign.md` §6-5 v0.10.13 4단계 노드 매트릭스 | §3·§5·§6 — 5분리 + 머지·매핑·검증 노드 매트릭스로 확장 |
| `pipeline_topology_redesign.md` §6-5 v0.10.17 "알려진 한계" 5가지 처방 | §2-2 결함 4건 + §4 핵심 변경 5건 으로 재정리 |
| `pipeline_topology_redesign.md` §11-10 흐름 A·B 모델 | §4-1 source_flow 메타데이터 도입으로 코드 차원 반영 |
| `pipeline_topology_redesign.md` §6-6a 신규 수집 노드 6종 | §8 인계 경계 표로 책임 분리 |
| `agents/domain_modeling/output.schema.json` `$defs.reportEntry` | §7-1 `source_flow` 필드 + §7-3 `search_query_hints` 객체 승격 |
| `agents/feature_url_mapper/system_prompt_kr.md` §3·§5 단일 규칙 | §4-4·§5-6a 5개 source-type 별 system_prompt 5종으로 분배 (`agents/feature_mapping_<source>/system_prompt_kr.md`) |
| `brave_api_url_discovery_redesign.md` Brave + LLM 검증 패턴 | §4-3·§5-4 `url_discovery_owned_channels_node` 에서 사상 재사용 |

본 문서가 확정되면 위 선행 문서 항목은 본 문서 참조로 단순화될 예정입니다.
