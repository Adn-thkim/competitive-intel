# official_content_collection 노드 설계 — official 출처 수집 → comparison_matrix 리포트 경로

> - **상태**: DRAFT — 사용자 검토 후 확정 예정
> - **작성일**: 2026-06-04 (노드 명칭 확정 갱신: 2026-06-04)
> - **시리즈**: report generation 시리즈 1단계 (official_content_collection → comparison_matrix)
> - **명칭 주의**: 선행 문서(§6-6)·`state.py`에서 `feature_extraction`으로 지칭되던 노드의
>   확정 명칭. "feature_extraction"은 단계(stage)명으로만 유지한다 (FE-D7 참조).
> - **선행 문서**:
>   - `docs/design/pipeline_topology_redesign.md` §6-6 (D3 옵션 C 확정) · §6-6a · §6-7
>   - `docs/design/feature_url_mapper_redesign.md` (v2.1 — 5중 fan-out 토폴로지 완료)
>   - `docs/reference/comparison_matrix.md` · `docs/reference/report_taxonomy.md`
> - **대상 파일**: `server/graph/nodes/official_content_collection_node.py` (신규),
>   `agents/official_content_collection/*` (기존 `agents/feature_extraction/*` 전면 재작성·이관),
>   `server/graph/graph.py`, `server/graph/state.py`

---

## 1. 문서 목적과 범위

본 문서는 `feature_url_mapper` 5중 fan-out 토폴로지(v0.10.27) 완료 후의 첫 후속 단계인
**official_content_collection 노드**(feature_extraction 단계, §6-6)의 설계를 확정합니다.
official_content_collection은 5종
`url_discovery_<source>` 파이프라인 중 **official 계열의 최종 출력**으로부터 자사·경쟁사의
공식 페이지 데이터를 수집·구조화하여, `comparison_matrix` 리포트 노드가 직접 사용하는
**Feature Pool**을 산출합니다.

**다루는 범위**

- §2: 5종 source 파이프라인 → source별 수집 노드 → 리포트 노드의 TO-BE 토폴로지 개요
  (후속 4종 수집 노드 설계 문서의 공통 기준점)
- §3 이하: official → official_content_collection → comparison_matrix 경로의 상세 설계
  (입력 계약 · 처리 흐름 · 출력 계약 · 캐싱 · 에러 핸들링 · 검증)

**다루지 않는 범위** (별도 문서로 분리)

- `comparison_matrix_node`의 LLM 프롬프트·envelope 상세 — 본 문서는 feature_pool
  소비 계약까지만 정의
- 나머지 4종 수집 노드(blog_community·youtube_reactions·owned_channels·macro 계열)의
  상세 설계 — §2의 토폴로지 합의만 제공
- Playwright SPA fallback — v0.11 위임 (D19), 본 문서는 정적 fetch 한계의 상태값 처리만 정의

---

## 2. TO-BE 토폴로지 개요 — source별 수집 → 리포트 직결 구조

### 2-1. 설계 원칙

`feature_url_mapper` 단계에서 5종 source-type 노드가 분리되었으므로(v0.10.27), 수집 단계도
**동일한 source 축으로 분리**합니다. 각 source 파이프라인의 최종 출력(feature × candidate ×
검증된 URL)을 해당 source 전용 수집 노드가 read하고, 그 수집 노드의 출력이 대응
리포트 노드로 직결됩니다.

| url_discovery (1차 fan-out)        | 수집 노드 (feature_selection 이후)                                                            | 산출 리포트 (A-Only)                |
| --------------------------------- | --------------------------------------------------------------------------------------- | ------------------------------ |
| `url_discovery_official`          | **`official_content_collection`** ← 본 문서                                               | `comparison_matrix`            |
| `url_discovery_blog_community`    | `community_collection`                                                                  | `reaction_insight` (채널 1)      |
| `url_discovery_youtube_reactions` | `youtube_query_planner` → `youtube_collection`                                          | `reaction_insight` (채널 2)      |
| `url_discovery_owned_channels`    | `youtube_channel_metadata_collection` · `blog_rss_collection` · `pr_release_collection` | `marketing_social`             |
| `url_discovery_macro`             | `market_context_collection`                                                             | `market_context_swot` (매크로 입력) |

**명칭 규약**: 수집 노드는 `{source/데이터}_collection` 패턴을 따른다(§6-6a의
`community_collection`·`market_context_collection` 등과 정합). 선행 문서 §6-6이
`feature_extraction`으로 부르던 official 계열 수집 노드도 본 규약에 따라
**`official_content_collection`**으로 확정한다 — "공식 출처 콘텐츠를 수집·구조화"하는
책임을 노드명이 직접 드러내며, `official_source_resolver`(URL 확보)와도 혼동되지 않는다.

**공통 입력 패턴**: 모든 수집 노드는 raw `*_urls_by_candidate`가 아니라
`analysis_features`(additional_urls_validation 산출 + feature_selection 사용자 선택 반영)를
read하고, 자기 source의 `origin` 값으로 필터링합니다. 이로써 (i) 사용자가 선택하지 않은
feature의 URL 수집 차단, (ii) HTTP/API 검증을 통과한 URL만 수집 대상, 두 가지가 모든
수집 노드에서 일관되게 보장됩니다.

### 2-2. A-Only / A+B 의존 구조 — Battlecard 대기 규칙

A+B 흐름 리포트(`battlecard`)는 **A-Only 리포트 3종(`comparison_matrix` ·
`reaction_insight` · `marketing_social`)이 모두 작성 완료될 때까지 대기** 후 시작합니다.
LangGraph의 list-fan-in barrier(v0.10.7 `ab_join`과 동일 패턴)로 결정론적 대기를 구현합니다.

```python
# A-Only 3종 모두 완료되어야 battlecard 1회 발화 (list-edge barrier)
builder.add_edge(["comparison_matrix", "reaction_insight", "marketing_social"], "battlecard")
```

`positioning_map`(B-only)은 `comparison_matrix`에만 의존하므로 barrier 불필요 — 단일
edge로 충분합니다. `market_context_swot`·`executive_summary`의 fan-in은 §6-7의 기존
설계를 유지하되, 구현 시점에 동일한 list-edge barrier 패턴 적용을 검토합니다.

### 2-3. 1단계 구현 범위 (본 시리즈)

가장 먼저 `comparison_matrix` 경로만 활성화합니다. `graph.py` 변경은 다음 3개 edge에
한정하며, 나머지 수집·리포트 노드 edge는 주석 상태를 유지합니다.

```python
builder.add_edge("feature_selection",           "official_content_collection")
builder.add_edge("official_content_collection", "comparison_matrix")
builder.add_edge("comparison_matrix",           END)   # 임시 — 후속 시리즈에서 positioning_map 등으로 교체
```

---

## 3. official_content_collection 노드 — 역할과 위치

### 3-1. 역할 정의

검증·선택이 끝난 official 계열 URL(공식 사이트 본체 + 약관·수수료 등 sub-page)에서
페이지 본문을 수집하고, LLM으로 feature별 사실 값을 추출하여 **feature_pool**을
산출합니다. 핵심 사상은 기존 spec의 원칙을 계승합니다:

- "공식 문구를 근거로 추출 가능한 사실"만 정리. 추정 금지.
- 공식 출처에 없는 값은 `unknown` / `not_found` / `requires_manual_check` 상태값으로 명시.
- 입력에 없는 URL의 임의 fetch 금지 (§6-6 공통 사항).
- 상품 간 비교 **결론**은 내리지 않음 — 비교·평가는 `comparison_matrix_node`의 책임.

### 3-2. 기존 `agents/feature_extraction/*` 처리 — 전면 재작성 (확정)

기존 디렉토리의 spec.md·schema·config는 구버전 draft(2026-05 초기)로, 현행 파이프라인과
다음 지점에서 계약이 깨져 있어 **전면 재작성**합니다.

| 항목 | 구버전 draft | 본 설계 |
|---|---|---|
| 입력 | `resolution_targets` + `official_sources` | `analysis_features` 필터 경로 (§4) |
| 정규화 | `travel-card-v1` 고정 스키마 | `report_config["comparison_matrix"]` 기반 동적 feature 정의 (§5-3) |
| 모델 | `gpt-5.4` (config.yaml) | `ClaudeApiAnalyzer(temperature=0)` (§6-6 확정 사항) |
| 출력 | `product_profiles` + `normalized_features` + `normalized_feature_schema` | `feature_pool` 중심 + `product_profiles` 보조 (§6) |

재작성 산출물: `agents/official_content_collection/system_prompt_kr.md` +
`input.schema.json` + `output.schema.json` + `config.yaml` — 노드명 확정(FE-D7)에 따라
디렉토리도 `agents/feature_extraction/` → `agents/official_content_collection/`으로
이관합니다(파일 구조 규칙: `agents/{agent_id}/`). 구버전 `spec.md`·`system_prompt.md`(영문)·
`schema_reference.md`는 삭제하고 본 문서가 spec을 대체합니다.

---

## 4. 입력 계약 (확정 — analysis_features 필터 경로)

### 4-1. read keys

| state 키 | 용도 |
|---|---|
| `analysis_features` | 추출 대상 feature × candidate × URL의 원천 |
| `selected_purposes` | `"comparison_matrix"` 포함 여부 — 활성 게이트 |
| `selected_feature_ids` | 사용자가 interrupt #4에서 선택한 feature 필터 |
| `domain_taxonomy.report_config["comparison_matrix"]` | `features`·`feature_labels`·`categories` — 추출 스키마 정의 (§5-3) |
| `own_product` / `competitor_candidates` / `selected_competitor_ids` | candidate 명칭·official_domain 매핑 |
| `official_sources` | candidate별 official_domain 재계산 (additional_urls 게이트, §4-3) |

### 4-2. 추출 대상 필터 3단계

```
analysis_features
  → [1] report_type == "comparison_matrix"
        AND "comparison_matrix" in selected_purposes
  → [2] feature_id in selected_feature_ids
  → [3] candidate_coverage 내 URL 필터 (§4-3)
```

`selected_purposes`에 `comparison_matrix`가 없거나 필터 결과가 빈 경우, 노드는
`status="skipped"`로 graceful 종료하고 후속 `comparison_matrix_node`가
`make_skip_result`로 이어 처리합니다.

### 4-3. URL 채택 게이트 (candidate_coverage 단위)

각 `candidate_coverage` 항목에서 추출 대상 URL을 다음 규칙으로 확정합니다.

| URL 군 | 채택 조건 | 근거 |
|---|---|---|
| `existing_urls` | `origin ∈ {"official_source", "official_subpage"}` | url_discovery_official 산출 origin 2종만 통과. `brave_search` 등 비공식 origin 차단 |
| `additional_urls` | `validated == true` **AND** URL host가 해당 candidate의 official_domain suffix 매칭 (`_host_endswith` 재사용) | LLM 추천 URL 중 공식 도메인 내부 검증 통과분만. 제3자 도메인은 "기본 금지" 원칙으로 차단 |

- `coverage == "not_found"`인 candidate는 fetch 없이 `extraction_status="not_found"`로
  기록하고 건너뜁니다.
- URL 상한 (FE-D5 v3, 사용자 확정 2026-06-04): **(feature × candidate) 쌍당 최소 1 ·
  최대 5** (`_MAX_URLS_PER_PAIR`). 쌍별 (tier, url) 정렬 상위 5건 채택 후 candidate
  단위 union(dedup). 우선순위 tier는 `official_source`(primary) > `official_subpage`
  (subpage_category가 feature와 관련 — 예: feat_exchange_fee ↔ "수수료"·"환율") >
  무관 subpage > additional.
- candidate당 **안전 상한 25** (`_MAX_URLS_PER_CANDIDATE`, 비정상 입력 보호) 초과 시에만
  coverage-aware 2단계 trim(greedy set cover → tier 충원)으로 쌍별 최소 1을 유지하며 절단.
- Step 2 LLM 입력 비용은 URL 수가 아니라 **candidate당 발췌 총예산 30,000자**(§5-2a)로
  통제한다 — 페이지 수가 늘면 페이지당 발췌 예산을 `min(6,000, 30,000/페이지 수)`로 축소.

---

## 5. 처리 흐름 — 4단계

```
[Step 0] 활성 게이트 + 추출 대상 구성 (§4 필터 → extraction_targets)
[Step 1] 콘텐츠 수집 — URL fetch + 본문 추출 (결정론적, LLM 비호출)
[Step 2] LLM 추출 — candidate 단위 호출, ClaudeApiAnalyzer(temperature=0)
[Step 3] feature_pool 조립 + 상태값 정리
```

### 5-1. Step 0 — extraction_targets 구성

§4 필터를 통과한 결과를 candidate 단위로 피벗합니다.

```python
extraction_targets: list[dict] = [
  {
    "candidate_id": "comp_travel_wallet",
    "candidate_name": "트래블월렛",
    "feature_ids": ["feat_exchange_fee", "feat_supported_currencies", ...],  # 이 candidate에서 추출할 feature
    "urls": [{"url", "origin", "subpage_category", "page_title"}],           # §4-3 게이트 통과분, 상한 5
  },
  ...
]
```

candidate 축 피벗 이유: 동일 candidate의 여러 feature가 같은 페이지(예: 수수료 안내)에
함께 명시되는 경우가 일반적이므로, **fetch와 LLM 호출 모두 candidate 단위가 토큰·요청
효율이 가장 높습니다** (feature 단위 호출 대비 LLM 호출 수 1/N).

### 5-2. Step 1 — 콘텐츠 수집 (D3 옵션 C의 1차 경로 = 정적 어댑터)

§6-6의 D3 옵션 C(어댑터 우선) 중 **1차 경로(정적 fetch)만 본 시리즈에서 구현**합니다.
code-gen actor fallback은 적용 사례가 누적된 뒤 별도 PR로 분리합니다(FE-D3 참조).

**책임 분리 (FE-D9)**: `_fetch_content`는 "URL → 전문(full text)" 변환 + 캐시만 담당하고,
LLM 입력 예산에 맞춘 절단은 후단의 `_build_excerpt` 헬퍼(키워드 근접 발췌, §5-2a)가
담당합니다. 전문 캐시는 URL 해시 키이므로 feature 집합·키워드 풀이 바뀌어도 재fetch 없이
재발췌만 수행됩니다.

- **HTML**: `requests` GET(timeout 10s — 기존 코드베이스의 HTTP 클라이언트 규약) → **Trafilatura**
  `extract(html, include_tables=True, favor_recall=True, output_format="markdown")`로
  본문·헤딩·표 추출 (FE-D10). 수수료표·한도표가 markdown 표 구조로 보존되어 정량
  feature 추출 정확도에 직결. Trafilatura가 None 반환 또는 빈약 추출(<200자) 시
  BeautifulSoup 폴백(`<script>`·`<style>`·nav·footer 제거 + 헤딩·본문 텍스트).
  v0.10.24 `_fetch_meta`(800자)는 변경하지 않음(별도 `_fetch_content` 헬퍼 신설).
- **PDF** (`Content-Type: application/pdf` 또는 `.pdf` 확장자): `pypdf` 텍스트 추출,
  안전 상한 **50페이지** (10페이지 → 완화. LLM 입력 크기는 `_build_excerpt`가 통제하므로
  fetch 측 상한은 파싱 시간·메모리 보호 목적만). 약관·수수료표 PDF 대응.
- **전문 안전 상한**: 50,000자 — 비정상 거대 문서 보호용이며 LLM 입력 상한과 무관.
- **SPA·동적 페이지**: 본문 추출 결과 < **200자**이면 해당 URL을
  `fetch_status="requires_dynamic_render"`로 기록하고 LLM 입력에서 제외. v0.11 Playwright
  fallback(D19)의 입력 목록이 되도록
  `data/collection/official_content_collection/{run_id}/dynamic_render_backlog.json`에 적재.
- 병렬 fetch: `ThreadPoolExecutor(max_workers=5)` — url_discovery_official과 동일 규약.
- fetch 결과 캐시: agent_cache **24h TTL**, 키 = URL 해시, 값 = **절단 전 전문**
  (`_brave_search` 캐시와 동일 사상).

### 5-2a. `_build_excerpt` — 키워드 근접 발췌 (Step 2 입력 조립, FE-D9)

LLM 입력 예산(페이지당 발췌 6,000자)을 맞추기 위해 단순 앞부분 절단 대신 다음
**결정론적** 발췌를 적용합니다. 정보가 문서 후반(약관 별표·수수료표 등)에 있는 경우의
누락을 방지합니다.

0. **예산 배분 (FE-D5 v3)**: candidate당 발췌 총예산 **30,000자**. 페이지당 예산 =
   `min(6,000, 30,000 / 페이지 수)` — URL 수가 늘어도(쌍당 상한 union, 실측 6~14건)
   LLM 입력 토큰 상한이 고정된다.
1. **항상 포함**: 문서 헤더(첫 500자) + 모든 헤딩 라인 — 문서 제목·기준일(as_of)·개정일
   정보가 발췌 밖으로 밀리지 않도록 보존.
2. **키워드 풀**: `report_config["comparison_matrix"]`의 `feature_labels`·`categories` +
   정적 sub-page 키워드 7종(약관·수수료·환율·한도·혜택·공지사항·이용안내) +
   `analysis_features`의 `feature_name`·`description` 명사. 각 매칭 지점 전후
   **±300자 윈도우** 추출, 중첩 윈도우는 병합.
3. **예산 초과 시**: 매칭 키워드 종류가 많은 윈도우 우선 채택. 생략 구간에는
   `[... 본문 일부 생략 ...]` 마커를 삽입해 LLM의 `partial` 판정 근거로 제공.
4. **결정론 보장**: 발췌 결과 해시가 LLM 캐시 키에 포함되므로(§5-3) 정규식 매칭 +
   고정 정렬만 사용. 키워드 풀 변경 시 cache miss는 의도된 동작.

알려진 한계 (파일럿에서 측정 후 보정): 키워드 동의어 누락 시 false negative
(명시된 값의 `not_found` 처리), 발췌 윈도우 분리로 인한 `conflicts` 검출 약화.
보정 손잡이는 윈도우 폭(±300자)과 키워드 풀 확장.

### 5-3. Step 2 — LLM 추출 (candidate 단위 호출)

- **어댑터**: `ClaudeApiAnalyzer(temperature=0)` + `call_with_schema(prompt, output_schema)`
  — 결정론적 출력 필수 (§6-6 확정). API 키는 `config.py`의 `ANTHROPIC_API_KEY` 경유.
- **호출 단위**: candidate 1건당 1회. 입력 = (candidate 메타) + (feature 정의 목록) +
  (URL별 본문 발췌). 병렬도 **1** (FE-D8 v2, 2026-06-06): candidate 입력 ~22k tok 이
  조직 ITPM 30k 의 대부분을 차지하므로 동시 발사는 429 를 즉발한다. 직렬 처리 +
  `ClaudeApiAnalyzer` 의 retry-after 백오프(429 시 헤더 지정 시간만큼 대기 후 동일
  시도 재발사, schema 재시도 예산 미소모)로 한도 내 결정론적 완료를 보장한다.
  4 candidate 약 2~3분 소요. (ITPM 상향·발췌 예산 축소 시 병렬도 재상향 검토.)
- **feature 정의 주입**: `report_config["comparison_matrix"]`의 `features` +
  `feature_labels` + `categories`에 `analysis_features`의 `description`을 결합하여
  "무엇을 찾아야 하는지"를 명시. 구버전 travel-card-v1 같은 고정 필드 목록은 사용하지
  않으며, 도메인이 바뀌어도 taxonomy가 추출 스키마를 결정합니다.
- **출력 스키마** (`agents/official_content_collection/output.schema.json` 핵심부):

```json
{
  "required": ["candidate_id", "extracted_features", "profile_summary"],
  "properties": {
    "candidate_id": { "type": "string" },
    "extracted_features": {
      "type": "array",
      "items": {
        "required": ["feature_id", "value", "extraction_status", "evidence"],
        "properties": {
          "feature_id":        { "type": "string" },
          "value":             { "type": "string", "description": "비교 가능한 축약값. 예: '17종', '매수 무료/매도 0.5%'" },
          "value_numeric":     { "type": ["number", "null"], "description": "정량 비교 가능 시 수치" },
          "unit":              { "type": "string" },
          "as_of":             { "type": "string", "description": "페이지에 명시된 기준 시점. 없으면 ''" },
          "extraction_status": { "enum": ["explicit", "partial", "inferred", "unknown", "not_found", "requires_manual_check"] },
          "evidence":          { "type": "string", "maxLength": 300, "description": "근거 원문 발췌" },
          "source_url":        { "type": "string" },
          "confidence":        { "type": "number", "minimum": 0, "maximum": 1 }
        }
      }
    },
    "profile_summary": { "type": "string", "maxLength": 600 },
    "conflicts": {
      "type": "array",
      "items": { "properties": { "feature_id": {}, "detail": {}, "urls": {} } },
      "description": "공식 문구 간 충돌 — requires_manual_check 승격 근거"
    }
  }
}
```

- **사실성 규칙** (구버전 spec 계승, system_prompt_kr.md에 명시):
  페이지 명시 = `explicit`, 부분 문맥 = `partial`, 분류 해석 = `inferred`,
  미확인 = `unknown`/`not_found`. 충돌 문구는 `conflicts`에 기록하고 해당 feature를
  `requires_manual_check`로 승격. 값을 비워서 숨기지 않습니다.
- **agent 캐시**: `agent_id="official_content_collection"`. 캐시 키 = candidate_id +
  feature_ids 정렬 해시 + URL별 **발췌 본문 해시**(§5-2a 산출) + 컨텍스트 해시
  (system_prompt + schema + 모델). 발췌 해시 포함으로 페이지 내용·키워드 풀 변경 시
  자동 cache miss.

### 5-4. Step 3 — feature_pool 조립

candidate별 LLM 출력을 feature 축으로 재피벗하여 `feature_pool`을 만듭니다(§6-1).
`extraction_status ∈ {unknown, not_found}` + fetch 전량 실패 candidate는
`unresolved_targets`로 집계하여 `product_profiles`의 `needs_manual_review`와 `errors`에
반영합니다.

---

## 6. 출력 계약 (write keys)

### 6-1. `feature_pool` — 주 출력 (comparison_matrix가 직접 read)

`state.py` 현행 docstring(`{feature_id: {"value", "source_url", "evidence"}}`)은 candidate
차원이 없어 비교 매트릭스(행=경쟁사, 열=feature)를 구성할 수 없으므로, **2단계 키 구조로
확정**하고 docstring을 갱신합니다.

```python
feature_pool: dict[str, dict[str, dict]] = {
  "feat_exchange_fee": {
    "own_toss_travel_card": {
      "value": "17종 통화 환전 수수료 무료",
      "value_numeric": 0, "unit": "%", "as_of": "2026-05",
      "extraction_status": "explicit",
      "evidence": "...원문 발췌...",
      "source_url": "https://...", "source_origin": "official_subpage",
      "confidence": 0.95,
      "is_promotional": false, "valid_until": ""   # FE-D12 — 이벤트성 조건 구분
    },
    "comp_travel_wallet": { ... },
  },
  ...
}
```

- 키 1차 = `feature_id`(feat_*), 2차 = `candidate_id`(own_*/comp_*/func_*) —
  ID 네임스페이스 규칙 준수.
- `comparison_matrix_node`는 `selected_competitor_ids` × `report_config.features`로
  순회하며 누락 셀은 `extraction_status` 기반으로 "미확인" 표기 (AP 함정 방지 —
  빈 셀을 열위로 단정하지 않음).

### 6-2. `product_profiles` — 보조 출력

```python
product_profiles: list[dict] = [
  {
    "candidate_id": str,
    "product_name": str,
    "profile_summary": str,           # LLM profile_summary
    "sources_used": list[str],        # 실제 추출에 사용된 URL
    "fetch_failures": list[str],      # fetch 실패·dynamic_render 제외 URL
    "needs_manual_review": bool,      # conflicts 존재 또는 explicit 비율 < 50%
  }, ...
]
```

### 6-3. `normalized_features` — 본 시리즈 폐기 (FE-D2)

구버전 spec의 별도 `normalized_features` 키는 feature_pool의 `value`/`value_numeric`/`unit`
필드로 흡수되어 중복입니다. 단순성 원칙에 따라 본 시리즈에서 채우지 않으며, `state.py`의
키 선언은 후속 노드 영향 검토 후 별도 PR에서 제거합니다(미사용 선언 유지는 무해).

### 6-4. 운영 필드

- `agent_steps`: `{"step_name": "OfficialContentCollection", "status": ...}` append.
- `errors`: fetch 실패·LLM 부분 실패를 `{"node", "error", "timestamp"}` 형식 누적.
- raw 응답 보존: `data/collection/official_content_collection/{run_id}/{candidate_id}.json`
  (디버깅용, gitignore — §6-6a 관측성 규약 `data/collection/{node_name}/{run_id}/` 준수).

---

## 7. 에러 핸들링 — 부분 실패 허용

| 상황 | 처리 |
|---|---|
| 일부 URL fetch 실패 | 해당 URL 제외 후 진행. `errors` 누적 + `product_profiles.fetch_failures` 기록 |
| candidate 전체 URL fetch 실패 | 해당 candidate의 모든 feature를 `not_found`로 채우고 진행 (matrix에 "미확인" 행 유지) |
| LLM 호출 실패 (1 candidate) | 재시도 1회 → 실패 시 candidate 단위 `not_found` 처리 + `errors` 누적 |
| LLM 전 candidate 실패 / 추출 대상 0건 | `feature_pool={}` + status="completed" — comparison_matrix_node가 빈 입력 graceful 처리 |
| `critical_error` | **설정하지 않음** — own_* URL 검증은 url_retry에서 이미 보장되었고, 추출 실패는 리포트 신뢰도 강등으로 표현 |

모든 실패 응답은 기존 `_error(started_at, message)` 헬퍼 규약을 따릅니다.

---

## 8. graph.py·state.py 변경 요약

| 파일 | 변경 |
|---|---|
| `server/graph/graph.py` | §2-3의 3개 edge 활성화 (`feature_selection→official_content_collection→comparison_matrix→END`). 기존 `feature_selection→END` 임시 edge 제거. §6-7 주석 블록의 `feature_extraction` 표기를 확정 노드명으로 갱신 |
| `server/graph/state.py` | `feature_pool` docstring을 §6-1 2단계 구조로 갱신 + 출력 주석의 `feature_extraction_node` 표기를 확정 노드명으로 갱신. 신규 키 추가 없음 (`product_profiles` 기존 선언 재사용) |
| `server/graph/nodes/official_content_collection_node.py` | 신규 — §5 처리 흐름 구현 |
| `server/graph/nodes/comparison_matrix_node.py` | 스켈레톤의 TODO 해소 (별도 PR — feature_pool 계약 §6-1 기준) |
| `agents/official_content_collection/*` | 기존 `agents/feature_extraction/*` 전면 재작성·이관 (§3-2) |

---

## 9. 검증 계획 (목표 주도형)

1. **단위 — URL 게이트**: `analysis_features` fixture(origin 혼합 + validated 혼합)에 대해
   §4-3 게이트가 official 계열·도메인 매칭분만 통과시키는지 → 검증: 비공식 origin 통과 0건.
2. **단위 — 본문 추출**: HTML(표 포함)/PDF/SPA(본문<200자) 3종 fixture → 검증: SPA가
   `requires_dynamic_render`로 분류되고 LLM 입력 진입 0건 + 수수료표가 markdown 표로
   보존(Trafilatura) + Trafilatura 실패 시 BS4 폴백 동작.
2a. **단위 — 발췌**: 동일 입력 반복 호출 시 발췌 결과 바이트 동일(결정론) + 문서 헤더·
   헤딩 포함 + 후반부 키워드(예: 50,000자 문서 끝의 "수수료") 윈도우 포함 확인.
3. **단위 — 캐시**: 동일 입력 2회 호출 → 검증: 2회차 LLM 호출 0건(cache hit). 본문 1자
   변경 → cache miss.
4. **통합 — 파이프라인**: 트래블카드 파일럿으로 interrupt #4 재개 후 `feature_pool`이
   `selected_feature_ids` × `selected_competitor_ids` 셀을 모두 보유(값 또는 상태값)하는지
   → 검증: 누락 셀 0건, 모든 `value`에 `source_url` 연결.
5. **통합 — 부분 실패**: candidate 1종의 URL을 의도적으로 404 처리 → 검증: 해당
   candidate만 `not_found` 행으로 남고 나머지 candidate 추출 정상 + status="completed".
6. **품질 게이트 (FE-D11 — candidate별 운영, 사용자 확정 2026-06-04)**:
   - 측정 단위 = **candidate별** `explicit` 비율 ≥ 60% (전체 평균 단일 기준 폐기 —
     평균은 candidate 데이터 품질에 지배되어 추출기 품질 신호가 희석됨).
   - **미달 candidate 처리**: 추출기 결함이 아니라 입력 데이터 이슈(전용 수수료 페이지
     부재·primary_url 언어판 오류 등)로 우선 분류하고, 해당 원인을
     `Future_Improvements.md`(예: 4번 — resolver 데이터 품질) 항목에 연계 기록한다.
     동일 데이터 조건에서 추출 판정 자체가 의심될 때만 프롬프트 보정 검토.
   - `evidence` 비어있는 explicit 항목 0건 (LLM-as-judge 표본 검토 20건) — 유지.

   **baseline 실측 (2026-06-04 최종, Sonnet 4.6, v1.1 프롬프트 = 나열형 축약 + FE-D12 필드판)**:
   4 candidates × 8 features = 32셀 전수 성공(스키마 위반·재시도 0건, not_found 0건).
   candidate별 explicit: **own_토스 6/8(75%) 통과** · 하나 4/8 · 신한 2/8 · 트래블월렛
   2/8 — 미달 3건은 데이터 이슈 분류(전용 수수료 페이지 부재 / 트래블월렛 영문판
   primary → Future_Improvements 4번 부록). evidence 빈 explicit 0건(통과).
   비용: 전체 1회 $0.45(약 630원, 입력 90.4k + 출력 12.0k tok) · 58초(병렬 4).
   추가 검증 2건: (i) **FE-D12 수용 통과** — own_토스 ATM 월 5회 무료·2% 캐시백이
   `is_promotional=true` + `valid_until="2026-09-30"`(본문 "기간 2026.04.01.~09.30."
   근거)로 정확 판정, 상시 조건 30셀은 전부 false. (ii) **충돌 감지 첫 사례** — 신한
   해외결제 수수료율이 페이지별 상이(0.2% vs 0.18%)하여 `requires_manual_check` 승격.
   참고: 프롬프트가 바뀌면 판정도 일부 이동(이력: 하나 4→3→4 등) — temperature=0이어도
   프롬프트는 캐시 키·판정의 일부라는 §5-3 설계의 실증.

---

## 10. 결정 항목

| ID     | 결정                                                                                                                                                                                                                                                                                                                                                                                                                        | 상태                                                                 |
| ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| FE-D1  | 입력 계약 = `analysis_features` 필터 경로 (raw `official_urls_by_candidate` 직접 read 기각 — 사용자 선택·검증 우회 방지)                                                                                                                                                                                                                                                                                                                         | **확정** (2026-06-04)                                                |
| FE-D2  | `normalized_features` 별도 키 폐기, feature_pool에 흡수                                                                                                                                                                                                                                                                                                                                                                           | 제안 — 본 문서 검토 시 확정                                                  |
| FE-D3  | D3 옵션 C의 code-gen actor fallback은 본 시리즈 제외, 정적 어댑터 + `requires_dynamic_render` backlog만 구현 (Playwright는 v0.11)                                                                                                                                                                                                                                                                                                            | 제안                                                                 |
| FE-D4  | LLM 호출 단위 = candidate당 1회 (feature 단위 기각 — 호출 수·토큰 효율)                                                                                                                                                                                                                                                                                                                                                                    | 제안                                                                 |
| FE-D5  | **v3 (사용자 확정 2026-06-04)**: URL 상한 = **(feature × candidate) 쌍당 최소 1 · 최대 5** + candidate당 안전 상한 25 (초과 시에만 coverage-aware trim). LLM 입력은 candidate당 발췌 총예산 30,000자(페이지당 `min(6,000, 30,000/페이지 수)`)로 통제 · fetch 전문 안전 상한 50,000자/PDF 50페이지 · SPA 판정 임계 200자. 실데이터 검증: 쌍 커버리지 29/29 완전 보장, fetch 43건(candidate별 6~14). — 이력: v1 candidate당 총 5 단순 절단 → v2 coverage-aware(커버 28/29, 상한 5 내 수학적 한계 1쌍) → v3 쌍 단위 상한으로 한계 해소 | **확정** (2026-06-04, v3) — 수치는 파일럿 측정 후 조정 여지 유지                    |
| FE-D6  | 기존 `agents/feature_extraction/*` 전면 재작성 (`agents/official_content_collection/`으로 이관)                                                                                                                                                                                                                                                                                                                                      | **확정** (2026-06-04)                                                |
| FE-D7  | 노드 명칭 = `official_content_collection` — 수집 노드 `{source/데이터}_collection` 네이밍 규약(§6-6a) 정합. `feature_extraction`은 단계명·선행 문서(§6-6) 참조용으로만 유지                                                                                                                                                                                                                                                                                 | **확정** (2026-06-04)                                                |
| FE-D8  | LLM 어댑터 = `ClaudeApiAnalyzer(temperature=0)` 우선 적용. **비용 실측 완료 (2026-06-04, Sonnet 4.6, 4 candidates 전체, FE-D12판)**: 입력 90,393 tok + 출력 12,015 tok = **$0.45/1회 분석** (약 630원, candidate당 $0.113) · 병렬 4로 58초 · 재시도 0건 — 하이브리드 절충 **불필요 확정**. 출력 최대 ~3k tok ≪ max_tokens 8k                                                                                                                                                       | **확정** (2026-06-04, 실측 반영)                                         |
| FE-D9  | LLM 입력 절단 = `_fetch_content`(전문 수집·캐시)와 분리된 `_build_excerpt` 키워드 근접 발췌(§5-2a). 단순 앞부분 절단 기각 — 문서 후반 정보 누락 방지. 캐시 키 정합(전문=URL 해시, 발췌=LLM 캐시 키 구성요소)                                                                                                                                                                                                                                                                        | **확정** (2026-06-04)                                                |
| FE-D10 | HTML 본문 추출 = Trafilatura v2.x (`include_tables=True`·`favor_recall=True`·markdown 출력) + BS4 폴백. 근거: Apache 2.0 (v1.8.0+), 본문 추출 벤치마크 우위, 표 구조 보존. JS 미실행 한계는 SPA 처리(§5-2)와 동일                                                                                                                                                                                                                                             | **확정** (2026-06-04) — `requirements.txt`에 `trafilatura>=2.0` 추가 완료 |
| FE-D11 | §9 품질 게이트 운영 단위 = **candidate별** explicit ≥ 60% (전체 평균 단일 기준 폐기). 미달 candidate는 추출기 결함이 아닌 입력 데이터 이슈로 우선 분류 + Future_Improvements 연계 기록 — §7 부분 실패 정책과 정합                                                                                                                                                                                                                                                                 | **확정** (2026-06-04)                                                |
| FE-D12 | 이벤트성(기간 한정) 조건 구분 = **데이터 차원 보존(옵션 a)** — output.schema·feature_pool 에 `is_promotional: bool` + `valid_until: str`(본문 명시 종료일) 필드 추가. LLM 이 발췌 근거로 판정(불명확하면 false — 추정 금지). 리포트 노드는 이 필드로 footnote 등 표현 자유 선택. 옵션 b(comparison_matrix footnote 전용 처리) 기각 — battlecard 등 후속 리포트 재사용 불가                                                                                                                                      | **확정** (2026-06-04)                                                |
