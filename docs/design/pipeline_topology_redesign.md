# 파이프라인 토폴로지 재설계 — Domain Modeling 병렬화 및 리포트 단위 출력 전환

> - **상태**: DRAFT — 사용자 검토 후 확정 예정
> - **작성일**: 2026-05-19 (v0.10.3 갱신: 2026-05-21)
> - **대상 노드**: `graph.py`, `domain_modeling_node`, `feature_url_mapper_node`, `feature_selection_node`, 그리고 미구현 노드 13종 — `feature_extraction`, `youtube_query_planner`, `youtube_collection`, `reaction_analysis`, 신규 수집 노드 6종(`community_collection`, `app_store_review_collection`, `youtube_channel_metadata_collection`, `blog_rss_collection`, `pr_release_collection`, `market_context_collection`), 리포트 노드 7종(`comparison_matrix`, `reaction_insight`, `marketing_social`, `battlecard`, `positioning_map`, `market_context_swot`, `executive_summary`)
> - **선행 문서**: `docs/Design_Spec.md` (v2.0, 2026-05-05), `docs/storyboard/_assets/00_overview.excalidraw`
> - **트리거**: 스토리보드의 단계별 흐름·6단계 설계 의도와 현재 graph.py 구현 사이의 불일치 8건

---

## 1. 문서 목적과 범위

본 문서는 사용자가 제시한 6단계 설계 의도(검색 → AI 초안 검토 → 경쟁사 선택 → 분석 항목 선택 → 자료 수집 → 분석 → 인사이트 리포트)와 현재 구현 사이의 차이를 해소하기 위한 토폴로지 변경안을 정리합니다. 이 문서가 다루는 범위는 다음과 같습니다.

- LangGraph 파이프라인의 엣지 구조 변경(직렬 → 병렬 fan-out/fan-in)
- `domain_modeling`의 입출력 계약 변경(`competition_axes` 의존 제거, `report_types` 차원 추가)
- 후속 미구현 노드(`feature_extraction` 이후) 5종의 구조 결정
- 캐시 마이그레이션·테스트·옵저버빌리티·롤백 계획

본 문서가 **다루지 않는** 범위는 다음과 같습니다(별도 문서로 분리).

- 각 노드의 LLM 프롬프트 세부 수정 — 노드별 system_prompt_kr.md 작업으로 분리
- 프런트엔드(`client/`)의 UI 컴포넌트 상세 — `docs/storyboard/`의 화면 설계와 연계
- Brave API + LLM Validation 재설계 — 이미 `docs/design/brave_api_url_discovery_redesign.md`에서 다룸

---

## 2. AS-IS 토폴로지

### 2-1. 현재 엣지 구조 (`graph.py` 실사)

```
START
  → query_intake
  → human_review (interrupt #1)
  → competitor_discovery               ── competition_axes 생성 ──┐
  → domain_modeling                    ←── competition_axes 입력 ─┘
  → normalize_competitor_ids
  → competitor_selection (interrupt #2)
  → official_source_resolver
  → url_retry (interrupt #3, critical_error → END)
  → feature_url_mapper
  → feature_selection (interrupt #4)
  → END  (임시)
```

### 2-2. 현재 토폴로지의 핵심 제약

- `domain_modeling`이 `competitor_discovery`의 출력(`competition_axes`)에 데이터 의존합니다. 두 노드를 병렬화하려면 이 의존을 끊어야 합니다.
- `feature_url_mapper`와 `feature_selection`이 `purpose_config` 단위로 동작하며, 사용자가 의도한 "분석 리포트 단위" 그룹핑이 명시적으로 표현되지 않습니다.
- `url_retry` 이후 노드들은 모두 직렬 TODO 주석으로만 존재합니다. 후속 분석 노드의 병렬화 의도는 코드에 반영되어 있지 않습니다.

---

## 3. TO-BE 토폴로지

### 3-1. 목표 엣지 구조 (v0.10.9+ — feature_url_mapper 4단계 분리 반영)

```
START
  → query_intake
  → human_review (interrupt #1)
       → competitor_discovery
           ├─→ normalize_competitor_ids                                       (분기 A)
           │     → competitor_selection (interrupt #2)
           │     → official_source_resolver
           │     → url_retry (interrupt #3)
           │           ├─(critical_error)→ END   ─── conditional ─┐
           │           └─(정상)            ─── direct ────────────┤
           │                                                       │
           └─→ domain_modeling (단일 호출, competition_axes 입력)   ┤ (분기 B, 병렬)
                                                                   ↓
                                                              ab_join          ← list-fan-in barrier (v0.10.7)
                                                                   ↓
                                                  url_discovery_brave_node     (Step 0 — Brave 검색, B-1 24h TTL 캐시)
                                                                   ↓
                                                  page_meta_collect_node       (Step 1 — page meta, B-2 24h TTL 캐시)
                                                                   ↓
                                                  feature_mapping_llm_node     (Step 2 — LLM, A-4 cache_input + parallel=4)
                                                                   ↓
                                                  additional_urls_validation   (Step 3 — HTTP 검증, B-3 24h TTL 캐시)
                                                                   ↓
           → feature_selection (interrupt #4)
           → feature_extraction + 신규 수집 노드 6종 (§6-6a)
           ├─→ comparison_matrix ─→ positioning_map ─┐
           ├─→ reaction_insight ──┐                  │
           ├─→ marketing_social ──┼─→ battlecard ───┤
           │                      └─→ market_context_swot ─┐
           │                                                ├─→ executive_summary → END
           └────────────────────────────────────────────────┘
```

### 3-2. 핵심 변경 의도 (v0.9 → v0.10.7 → v0.10.9)

- **v0.9 (CD-fanout)**: `competitor_discovery` **종료 직후** `{normalize_competitor_ids, domain_modeling}` 두 분기를 fan-out합니다. `domain_modeling`은 `competition_axes`(competitor_discovery 산출)를 즉시 입력으로 받아 단일 호출로 진행되며, 1차/2차 phase 분리는 폐기되었습니다(§6-1 v0.9).
- **v0.9 분기 A**는 interrupt #2·#3을 순차 통과해 `url_retry`까지 진행되고, **분기 B**는 `domain_modeling`이 단독으로 진행됩니다.
- **v0.10.7 (list-fan-in barrier)**: 두 분기는 명시적 `ab_join` 노드에서 fan-in 됩니다. `builder.add_edge(["url_retry", "domain_modeling"], "ab_join")` 의 LangGraph list-edge barrier 로 두 source 가 모두 ready 되어야 ab_join 이 단 1회 발화합니다. v0.10.5/v0.10.6 의 conditional + direct 혼합 fan-in race(이슈 #3249) 가 결정론적으로 해소되었습니다.
- **v0.10.9 (feature_url_mapper 4단계 분리)**: 단일 `feature_url_mapper_node` 가 4개 노드(`url_discovery_brave` → `page_meta_collect` → `feature_mapping_llm` → `additional_urls_validation`) 로 분리되었습니다. 단계별 timeout 격리(`FEATURE_MAPPING_LLM_TIMEOUT=600s` 별도 분리, v0.10.10) + 단계별 24h TTL 캐시(v0.10.12) + LLM 입력 cache_input 거시 축약(v0.10.13 A-4) 으로 결정론적 캐시 hit 가 보장됩니다.
- **v0.10.11 (Express dispatcher)**: Node.js native fetch 의 기본 300s headersTimeout 이 Python invoke 의 동기 호출 wall-clock 을 초과하던 race 를 undici `Agent` long timeout dispatcher(`PYTHON_INVOKE_TIMEOUT_MS=30분 기본`) 로 차단합니다.
- `feature_extraction` 이후 7개 리포트 노드는 D1=B 분리형(§6-7 v0.6)의 fan-out/fan-in 구조로 진행됩니다.

---

## 4. 차이 8건 요약 (이전 검토 결과)

| # | 항목 | 변경 성격 | 우선순위 |
|:-:|---|---|:-:|
| 1 | Domain Modeling 위치·병렬화 | 엣지 구조 + 입력 계약 | P0 |
| 2 | Domain Modeling 출력 단위(리포트 차원 추가) | 출력 스키마 | P0 |
| 3 | Feature URL Mapper 입력 어댑터 | 입력 계약 | P1 |
| 4 | Feature Selection UI 그룹핑 | 프런트엔드 표현 | P3 |
| 5 | Feature Extraction 동작 방식(코드 생성·실행) | 노드 구현 | P2 |
| 6 | 후속 분석 노드 병렬화 | 엣지 구조 | P2 |
| 7 | 분석 리포트 유형 5종 확장 | 노드 구조 결정 | P1 |
| 8 | HITL 번호 일관성 | 변경 없음(확인 완료) | — |

---

## 5. 우선순위 분류 근거

- **P0** 항목은 후속 모든 노드의 입력 계약이 종속되므로 먼저 확정하지 않으면 P1·P2 작업이 두 번 깨집니다.
- **P1** 항목은 `feature_extraction` 구현 **전에** 결정해야 추출 대상이 명확해집니다.
- **P2** 항목은 가장 큰 구현 작업이지만 P0/P1이 안정화된 뒤 착수해야 재작업이 없습니다.
- **P3** 항목은 백엔드가 정해지면 가벼운 프런트 변경이며, 다른 작업과 병행 가능합니다.

---

## 6. 권장 변경 순서(8단계) — 상세 설계

### 6-0. [P0-Rubric] Report Taxonomy Rubric 문서 작성 및 system_prompt 통합

**근거**

- DomainTaxonomyAgent는 도메인당 1회 호출되어 후속 모든 노드의 입력 계약을 결정합니다. 단일 호출의 품질이 파이프라인 전체 품질의 상한을 정합니다.
- 현재 `agents/domain_modeling/system_prompt_kr.md`는 4개 도메인 예시 표(소비자 금융·B2B SaaS·온라인 교육·전자상거래)를 보유하나 "어떤 feature가 어떤 리포트 유형에 기여하는가"에 대한 의미 기준이 없습니다. §6-3에서 `report_types` 필드를 도입해도 LLM이 그 필드에 무엇을 채워야 하는지에 대한 가이드가 부재합니다.
- 별도의 "도메인 × 리포트" reference 문서를 디스크에만 두는 형태(7개 × N도메인)는 LLM 호출 시점에 자동 주입되지 않아 ROI가 낮습니다. 통합 Rubric 1개 + 프롬프트 직접 인용이 ROI가 가장 높습니다.

**작업 산출물**

- `docs/reference/report_taxonomy.md` (신규, 200–300줄): 7개 리포트의 목적·표준 feature 카테고리·평가 루브릭·anti-pattern을 통합한 단일 Rubric.
- `agents/domain_modeling/system_prompt_kr.md` 갱신: Rubric §2(리포트별 정의)와 §3(액션 가능성 기준)을 inline 인용 또는 빌드 첨부.
- `scripts/build_prompts.py` (신규, 선택): Rubric 변경 시 system_prompt를 자동 재생성하는 빌드 스크립트.
- `docs/reference/examples/{report_type}_toss_travel_card.md` (D8 결정에 따라 7개 worked example): Rubric의 추상 기준이 트래블카드 도메인에서 구체화된 사례. 디스크 위치에 따라 LLM 컨텍스트 주입 여부 결정.

**Rubric 문서 구조**

```
docs/reference/report_taxonomy.md
├─ 1. 개요 — 7개 리포트의 역할 분담
├─ 2. 리포트별 정의 (7개 섹션)
│   ├─ 목적 · 대상 독자 · 핵심 액션
│   ├─ 표준 feature 카테고리 5–8개
│   ├─ 좋은 feature 예시 / 나쁜 feature 예시
│   └─ 평가 루브릭 (1–5점)
├─ 3. 액션 가능성(actionability) 기준 → D7에서 확정
└─ 4. 도메인 횡단 anti-pattern
```

**통합 메커니즘 — 방식 1 채택 (D9 확정, v0.8)**

- **방식 1 — inline 인용 + 빌드 스크립트 (채택)**: Rubric §2/§3을 system_prompt에 inline 인용. 문서 변경 시 `scripts/build_prompts.py`로 system_prompt를 자동 재생성. 채택 근거는 §10 D9 참조(캐시 키 안정성·§11-8 Rubric 버전 캐시 키 정합).
- 참고 — 검토 후 기각된 대안:
  - 방식 2(런타임 동적 첨부): 매 호출마다 첨부 토큰 비용 발생 + Rubric 변경 시 캐시 무효화 범위 추적 난해.
  - 방식 3(RAG / vector DB): 현재 도메인 규모(트래블카드 단일 파일럿)에서는 과도한 인프라.

**검증 방법**

- Rubric 적용 전/후로 동일 도메인(트래블카드)에 DomainTaxonomyAgent를 호출하여 `report_types` 필드의 채움 비율·정확도 비교.
- LLM-as-judge 평가: 두 출력 중 어느 것이 7개 리포트 작성에 더 적합한지 Claude API로 평가(20회 반복, bootstrap CI).
- 회귀 방지: 기존 도메인(소비자 금융·B2B SaaS 등) 4종에 대해 Rubric 도입 전후 taxonomy diff 검토.

**우선순위 근거**

- P0 항목 중 가장 선행되어야 합니다. Rubric이 없으면 §6-2(엣지 재구성)와 §6-3(스키마 확장)이 빈 껍데기로 동작합니다. Rubric은 §6-1 ~ §6-7의 모든 후속 작업의 **의미 기준**을 제공합니다.

### 6-1. [P0a] `domain_modeling_node` 입력 계약 (v0.9 — 단일 호출 모드, 1차/2차 분리 폐기)

**변경 대상 파일**

- `server/graph/nodes/domain_modeling_node.py`
- `agents/domain_modeling/input.schema.json`
- `agents/domain_modeling/system_prompt_kr.md` (필수 키 목록)

**현재 입력 계약 (v0.5 이전)**

```python
# domain_modeling_node.py:103
required_keys = ["domain_name", "own_product", "problem_statement",
                 "target_user", "core_value_props", "competition_axes"]
```

**v0.9 확정 입력 계약 — 단일 호출, `competition_axes` required 유지**

```python
# v0.9: 1차/2차 phase 분리 폐기. competitor_discovery 종료 후 단일 호출.
required_keys = ["domain_name", "own_product", "problem_statement",
                 "target_user", "core_value_props", "competition_axes"]
```

**변경 의도 (v0.9)**

- v0.6 ~ v0.8에서 검토된 "1차(axes 없이) + 2차(enrichment) 분리" 안은 §11-6 UX 인라인 미리보기 의도가 폐기되면서(v0.9 §11-6 참조) 사라졌습니다. 1차 분리의 유일한 의미였던 "human_review 직후 competition_axes 없이 미리 시작"이 불필요해졌으므로 단일 호출로 회귀합니다.
- §6-2 v0.9 토폴로지에서 `domain_modeling`은 `competitor_discovery` **종료 직후** 시작됩니다. 이 시점에 `competition_axes`가 이미 확보되어 있으므로, 입력 계약을 v0.5 이전과 동일하게 유지하면 됩니다.
- `_decide_mode()` 내부 phase 분기 로직(1차/2차) 폐기. 단일 모드로 단순화. `_needs_enrichment()` 함수는 더 이상 호출되지 않으므로 제거 또는 사용처 정리 필요.

**검증 방법**

- 단위 테스트: `test_domain_modeling_single_call.py`에 `competition_axes`를 포함한 입력으로 호출 시 정상 taxonomy 생성 검증.
- 회귀 테스트: 기존 캐시(`competition_axes` 포함된 v0.5 이전 결과)와 v0.9 결과의 taxonomy diff가 의미 있는 차이만 가지는지 확인. 캐시 키 변경 없음 → 자연 재사용.

**주의**

- 1차/2차 분리를 위한 임시 코드(만약 v0.6~v0.8 사이에 작성되었다면)는 v0.9에서 제거해야 합니다. 미작성 상태라면 본 절은 단순히 "현행 유지 + phase 분리 검토 종료"로 의미가 축소됩니다.
- 캐시 키는 도메인 단위(`domain_name + own_product_summary + ...`)로 유지되며 `selected_competitor_ids`에 의존하지 않습니다. 동일 도메인 재실행 시 사용자가 다른 경쟁사 부분집합을 선택해도 캐시 적중.

### 6-2. [P0b] `graph.py` 엣지 재구성 (v0.9 — CD-fanout + FUM fan-in)

**변경 대상 파일**

- `server/graph/graph.py`

**현재 엣지 (v0.5 이전 구현)**

```python
builder.add_edge("human_review",           "competitor_discovery")
builder.add_edge("competitor_discovery",   "domain_modeling")
builder.add_edge("domain_modeling",        "normalize_competitor_ids")
```

**변경 후 엣지 (v0.9 확정)**

```python
# ── fan-out: competitor_discovery 종료 직후 두 분기 시작 ─────────────────
builder.add_edge("human_review",             "competitor_discovery")
builder.add_edge("competitor_discovery",     "normalize_competitor_ids")  # 분기 A
builder.add_edge("competitor_discovery",     "domain_modeling")           # 분기 B (병렬)

# ── 분기 A: interrupts #2·#3 포함, 공식 출처 탐색·검증 직렬 진행 ───────
builder.add_edge("normalize_competitor_ids", "competitor_selection")      # interrupt #2
builder.add_edge("competitor_selection",     "official_source_resolver")
builder.add_edge("official_source_resolver", "url_retry")                 # interrupt #3
builder.add_conditional_edges(
    "url_retry",
    _route_after_url_retry,
    {"end": END, "feature_url_mapper": "feature_url_mapper"},
)

# ── 분기 B: domain_modeling이 단독으로 진행, feature_url_mapper에서 fan-in ─
builder.add_edge("domain_modeling",          "feature_url_mapper")
```

**핵심 변경 의도 (v0.9)**

- fan-out 시점을 `human_review`에서 `competitor_discovery` **종료 직후**로 이동. `domain_modeling`이 `competition_axes`(competitor_discovery 산출물)를 입력으로 받으므로 이 시점에 시작하는 것이 자연스럽고, `competition_axes`를 옵셔널로 둘 필요가 없어 §6-1의 1차/2차 phase 분리가 폐기됩니다.
- fan-in 시점을 `normalize_competitor_ids`에서 `feature_url_mapper`로 이동. `normalize_competitor_ids`는 `domain_modeling` 출력을 사용하지 않으므로(§9-1 매트릭스 + 코드 실사 검증), 두 분기를 그 시점에 묶을 의미상 이유가 없습니다. `feature_url_mapper`는 `domain_taxonomy`와 `official_sources`를 모두 read하므로 자연스러운 join point입니다.
- `domain_modeling`은 interrupt #2·#3 대기 시간과 `official_source_resolver`·`url_retry` 진행 시간을 모두 흡수합니다. `domain_modeling`이 먼저 완료되더라도 LangGraph가 fan-in 노드 도달 시 두 incoming edge 완료를 자동 대기하므로 별도 동기화 코드 불필요.

LangGraph는 동일 노드로의 다중 incoming 엣지가 있으면 모든 선행 노드 완료를 기다린 뒤 실행하므로, 별도 join 노드를 두지 않아도 됩니다.

**검증 방법**

- `compiled_graph.get_graph().draw_ascii()`로 토폴로지 시각 확인 — fan-out 1개 + fan-in 1개의 단순 마름모 형태.
- 통합 테스트 1: `domain_modeling`이 의도적으로 5초 지연되어도 분기 A가 그 사이에 정상 진행되고, `feature_url_mapper` 진입 시점에 `domain_taxonomy`가 state에 존재하는지 확인.
- 통합 테스트 2: `domain_modeling`이 5초 만에 빨리 완료되어도 분기 A의 interrupt #2·#3 응답 후에야 `feature_url_mapper`가 실행되는지 확인 (fan-in 자동 대기 동작 검증).
- 통합 테스트 3: `url_retry`에서 `critical_error` 발생 시 conditional edge가 END로 분기하고, 이미 완료된 `domain_modeling` 결과는 state에 보존되나 후속 노드 미실행 확인.

**리스크**

- `agent_steps`(`Annotated[list, operator.add]`)는 안전하나 두 분기가 동일 일반 키를 동시에 쓰면 충돌. 사전 점검 매트릭스는 §9-1에 v0.9 기준으로 재정리(분기 A: normalize + #2 + official_source + url_retry; 분기 B: domain_modeling).
- `critical_error`로 종료 시 `domain_modeling` LLM 호출(약 1회, 입력 ~4k tokens) 비용 무효. 단 캐시 키가 도메인 단위이므로 다음 실행에서 재사용 가능(§11-3 비용 분석 v0.9 참조).
- Anthropic API rate limit: `competitor_discovery` 직후 `normalize_competitor_ids`(`ProductIdResolver` 후보별 호출)와 `domain_modeling`(단일 호출)이 동시 burst. 직전 §11-? 신설 절(P0 limiter)로 관리.

### 6-3. [P0c] `domain_modeling` 출력 스키마 재구조화 (v0.10 — `report_config` 직접 매핑 + `search_query_hints`)

**변경 대상 파일**

- `agents/domain_modeling/output.schema.json`
- `agents/domain_modeling/system_prompt_kr.md`
- `agents/domain_modeling/input.schema.json` (analysis_direction 필드 추가)

**v0.10 핵심 변경 — `active_purposes`·`purpose_config` 폐기**

v0.6의 `purpose_config[purpose_id].report_types` 옵셔널 구조가 v0.10에서 다음과 같이 단순화됩니다.

- `active_purposes` 필드 **폐기** (도메인 의존 1차 분류 불필요 — UI·노드·캐시 키 모두 `report_types` 7종 단위로 운영)
- `purpose_config` 필드 → **`report_config`** 로 명명 변경. 키는 7종 enum 고정.
- `url_types`·`url_type_priority` 필드 **폐기** (Brave 검색 패러다임 도입으로 사전 분류 불요)
- `search_query_hints` 필드 **신설** (`feature_url_mapper`의 Brave 검색 쿼리 템플릿)

**v0.10 스키마 (`output.schema.json` 발췌)**

```json
{
  "required": ["domain_slug", "domain_type", "report_config"],
  "properties": {
    "report_config": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "comparison_matrix", "reaction_insight", "marketing_social",
        "battlecard", "positioning_map", "market_context_swot", "executive_summary"
      ],
      "properties": {
        "comparison_matrix": { "$ref": "#/$defs/reportEntry" },
        "reaction_insight":  { "$ref": "#/$defs/reportEntry" },
        "...": "나머지 5종"
      }
    }
  },
  "$defs": {
    "reportEntry": {
      "required": ["label", "active", "features", "feature_labels", "categories", "search_query_hints"],
      "properties": {
        "label": { "type": "string" },
        "active": { "type": "boolean" },
        "features": { "type": "array", "minItems": 0, "maxItems": 12 },
        "feature_labels": { "type": "object" },
        "categories": { "type": "array", "description": "Rubric §2-x 표준 카테고리에서 본 도메인 채택분 + 도메인 특수 카테고리" },
        "search_query_hints": {
          "type": "array",
          "description": "Brave 검색 쿼리 템플릿. {competitor_name}·{own_product} 치환 토큰 포함."
        },
        "aspect_codebook": { "type": "array", "description": "reaction_insight 한정" },
        "action_lens": { "type": "object", "description": "D7 mixed 채택 시 옵셔널" }
      }
    }
  }
}
```

**원칙**:
- 7종 enum 모두를 `report_config`에 포함 (누락 금지). 도메인에서 의미 없는 리포트는 `active: false` + 빈 features.
- 최소 1개 리포트는 `active: true`, active 리포트의 features는 최소 3개.
- `report_config["reaction_insight"].active`가 true면 `aspect_codebook` 필수.

**호환성·캐시 마이그레이션**

- v0.6~v0.9 캐시 파일(`active_purposes`·`purpose_config` 구조)은 v0.10에서 cache miss로 자연 폐기. 입력 키 집합 변경(`analysis_direction` 추가)으로 캐시 해시가 자동 변경되어 강제 마이그레이션 스크립트 불필요.
- 기존 `feature_url_mapper` 입력 어댑터는 v0.6 `purpose_config` 기준이므로 §6-5에서 v0.10 `report_config` 입력으로 재작성 필요.

**프롬프트 변경**

- `system_prompt_kr.md`는 v0.10에서 전면 재작성. "주요 목표" 절을 `report_config` 7종 직접 매핑으로 단순화. "URL 유형 설계 원칙" → "`search_query_hints` 설계 원칙"으로 교체.

**D4 확정 enum 7종 — 명칭 정합성 매핑 (v0.6 표 유지)**

| # | enum 머신 ID | 스토리보드 한국어 라벨 | worked example 파일 | reference 문서 | 흐름 분류 (§11-10) |
|---|---|---|---|---|---|
| 1 | `comparison_matrix` | 비교 매트릭스 | `comparison_matrix_toss_travel_card.md` | `reference/comparison_matrix.md` | A (leaf) |
| 2 | `reaction_insight` | 고객 반응 인사이트 | `reaction_insight_toss_travel_card.md` | `reference/reaction_insight.md` | A (leaf) |
| 3 | `marketing_social` | 마케팅·소셜 분석 | `marketing_social_toss_travel_card.md` | `reference/marketing_social.md` | A (leaf) |
| 4 | `battlecard` | 배틀카드 | `battlecard_toss_travel_card.md` | `reference/battlecard.md` | A + B (mid) |
| 5 | `positioning_map` | 포지셔닝 맵 | `positioning_map_toss_travel_card.md` | `reference/positioning_map.md` | B only (mid) |
| 6 | `market_context_swot` | 시장 컨텍스트·SWOT | `market_context_swot_toss_travel_card.md` | `reference/market_context_swot.md` | A + B (mid/top) |
| 7 | `executive_summary` | 임원 요약 | `executive_summary_toss_travel_card.md` | `reference/executive_summary.md` | B only (top) |

v0.10에서는 본 7종이 `report_config`의 **고정 키**가 되어 enum 정합성이 스키마 차원에서 강제됩니다(`additionalProperties: false` + `required` 7종).

**D4 확정 enum 7종 — 명칭 정합성 매핑 (v0.6)**

| # | enum 머신 ID | 스토리보드 한국어 라벨 | worked example 파일 | reference 문서 | 흐름 분류 (§11-10) |
|---|---|---|---|---|---|
| 1 | `comparison_matrix` | 비교 매트릭스 | `comparison_matrix_toss_travel_card.md` | `reference/comparison_matrix.md` | A (leaf) |
| 2 | `reaction_insight` | 고객 반응 인사이트 | `reaction_insight_toss_travel_card.md` | `reference/reaction_insight.md` | A (leaf) |
| 3 | `marketing_social` | 마케팅·소셜 분석 | `marketing_social_toss_travel_card.md` | `reference/marketing_social.md` | A (leaf) |
| 4 | `battlecard` | 배틀카드 | `battlecard_toss_travel_card.md` | `reference/battlecard.md` | A + B (mid) |
| 5 | `positioning_map` | 포지셔닝 맵 | `positioning_map_toss_travel_card.md` | `reference/positioning_map.md` | B only (mid) |
| 6 | `market_context_swot` | 시장 컨텍스트·SWOT | `market_context_swot_toss_travel_card.md` | `reference/market_context_swot.md` | A + B (mid/top) |
| 7 | `executive_summary` | 임원 요약 | `executive_summary_toss_travel_card.md` | `reference/executive_summary.md` | B only (top) |

명칭 선정 원칙: (i) snake_case lowercase 머신 ID, (ii) worked example 파일명(`{enum}_{domain_slug}.md`)·reference 문서명(`reference_{enum}.md`)과 1:1 일치, (iii) 스토리보드 한국어 라벨과 1:1 매핑이 유지되도록 합니다. 본 7종이 모든 후속 노드의 `report_types` 옵셔널 필드 enum 값으로 사용됩니다.

**reaction_insight용 aspect_codebook 필드 추가**

`purpose_config["reaction_insight"]`(또는 동등 키)에 `aspect_codebook` 필드를 추가합니다. `reference/reaction_insight.md` §2-3의 결정에 따라 ABSA codebook은 DomainTaxonomyAgent가 도메인 특성에 맞춰 자동 생성합니다.

```json
"aspect_codebook": {
  "type": "array",
  "description": "reaction_insight용 ABSA aspect 목록. DomainTaxonomyAgent가 도메인 특성에 맞춰 자동 생성한다.",
  "items": {
    "type": "object",
    "additionalProperties": false,
    "required": ["aspect_id", "label", "definition", "domain_specific"],
    "properties": {
      "aspect_id":       { "type": "string", "pattern": "^[a-z][a-z0-9_]*$" },
      "label":           { "type": "string", "minLength": 1 },
      "definition":      { "type": "string", "minLength": 5 },
      "domain_specific": { "type": "boolean" }
    }
  },
  "minItems": 3,
  "maxItems": 12
}
```

`aspect_codebook` 필드는 `report_types`에 `reaction_insight`가 포함된 purpose에서만 요구됩니다. Feature Selection interrupt #4에서 사용자가 추가·제거·재명명 가능합니다.

### 6-4. [P1a] 리포트 7종 → 노드 매핑 결정

**열린 결정 사항**(§10에 재정리)

- **옵션 A — 통합형**: `insight_report_node` 단일 노드가 7종 리포트를 모두 생성. 노드 수 최소, LLM 호출 1회로 일관성 확보, 캐시 키 단순.
- **옵션 B — 분리형**: 리포트마다 별도 노드 (`battlecard_node`, `positioning_map_node` 등). 노드 캐시 재실행 유연, 단 노드 수 12개 이상으로 증가.
- **옵션 C — 하이브리드**: 데이터 가공이 무거운 3종(`comparison_matrix`, `reaction_insight`, `positioning_map`)만 분리 노드, 나머지 4종은 `insight_report_node`가 통합 생성.

**권장 검토 기준**

- 토큰 비용: 옵션 A는 1회 호출에 모든 컨텍스트를 주입하므로 입력 토큰이 누적됩니다(20k~40k tokens 추정). 옵션 B/C는 노드당 컨텍스트가 작아 비용은 분산되나 호출 수가 증가합니다.
- 재실행 단위: 사용자가 특정 리포트만 재생성하길 원할 때 옵션 B/C가 유리합니다.
- 일관성: Executive Summary가 다른 리포트의 결론과 모순되지 않아야 하므로, 옵션 A의 단일 호출이 가장 안전합니다.

### 6-5. [P1b] `feature_url_mapper` 토폴로지 변경 (v0.10.18 ~ v0.10.28c 간략 요약)

> **📘 단일 진실원 (SSOT) 안내 (2026-06-04)**
>
> 본 §6-5 의 상세 설계·결정 항목 (D14~D50)·PR 시리즈 변경 이력은 별도 문서로 통합되었습니다.
> **상세 사항 확인 시 다음 문서를 참조하십시오**:
>
> 📄 **`docs/design/feature_url_mapper_redesign.md`** (v2.1, 본 시리즈 완료)
>
> 본 문서의 §6-5 는 변경 사항의 **간략 요약** 만 유지합니다.

---

**v0.10.18 ~ v0.10.28c 본 시리즈 변경 사항 — 간략 요약**

| 영역 | 변경 |
|---|---|
| 토폴로지 | 단일 노드 → 4단계 분리 (v0.10.9) → **5중 fan-out 1차 + cross_reference + 5중 fan-out 2차 + additional_urls_validation 의 5+1+5+1 구조 (v0.10.27)** |
| URL 탐색 단계 | 5 source-type 노드 (`url_discovery_official` · `_blog_community` · `_youtube_reactions` · `_owned_channels` · `_macro`) 분리. 각자 자기 source 의 `search_query_hints` 만 사용 + source 특수 정밀화 (`subpage_category` · `domain_class` · `view_count` · `platform` · `source_tier` 등) |
| Cross-reference | `cross_reference_node` (v0.10.26) — `youtube_reactions × owned_channels(youtube_official)` 결정론적 후처리 필터링 |
| LLM 매핑 단계 | 5 통합 노드 (`feature_mapping_<source>_node`) 신설 (v0.10.27). 각 노드 내부에 page meta 수집 + report_type 별 병렬 LLM 매핑 직렬. owned_channels 는 LLM 호출 생략 + URL carry-through (v0.10.28b D45 a) |
| `additional_urls_validation` | source-type 별 검증 분기 (v0.10.25) — YouTube `videos.list` API · owned_channel `is_brand_match` · blog_community 발행일 36개월 · macro 화이트리스트 매칭 + 정식 `_union_raw_features` (D23) |
| `domain_taxonomy` schema | `source_flow` (A·B·A+B, v0.10.18) + `macro_data_sources` (v0.10.22) + `search_query_hints` 객체 양식 `{feature_id, query, source_hint}` (v0.10.19.1) |
| `feature_selection` UI | source_flow 별 안내 박스 + URL 영역 조건부 렌더 + B-only 카드 결합 (v0.10.18a) + macro 라벨 + origin chip 7종 (v0.10.28a) + `OwnedChannelCard` candidate × platform 매트릭스 (v0.10.28b) + youtube_reactions URL truncate (v0.10.28c) |
| page meta 수집 | `_fetch_meta` body 보강 — `<h1>`/`<h2>` + 본문 첫 800자 + 발행일 (v0.10.24) |

**5중 fan-out 토폴로지 (v0.10.27 정식)**

```
ab_join
  ├─→ url_discovery_official                ┐
  ├─→ url_discovery_blog_community          │  5중 fan-out 1차
  ├─→ url_discovery_youtube_reactions       │  (list-fan-in barrier)
  ├─→ url_discovery_owned_channels          │
  └─→ url_discovery_macro                   ┘
                  ↓
        cross_reference                  (youtube × owned 결정론적 필터)
                  ↓
  ┌─→ feature_mapping_official              ┐
  ├─→ feature_mapping_blog_community        │  5중 fan-out 2차
  ├─→ feature_mapping_youtube_reactions     │  (각 노드 내부: page meta + LLM 매핑)
  ├─→ feature_mapping_owned_channels        │
  └─→ feature_mapping_macro                 ┘
                  ↓
        additional_urls_validation       (source-type 별 검증 + 정식 _union_raw_features)
                  ↓
        feature_selection (#4)
```

**SPA·동적 페이지 한계 처방 (v0.10.17 명시 한계 → 본 시리즈 처방 적용 현황)**

| # | 처방 | 본 시리즈 적용 |
|:-:|---|---|
| 1 | §6-6a 수집 노드 6종 구현 + reaction_insight 외부 출처 분리 | ⏸ v1.0 위임 |
| 2 | `_fetch_meta` 확장 — `<h1>`/`<h2>` 헤딩 + 본문 첫 N자 수집 | ✅ **v0.10.24 적용** |
| 3 | Playwright/Puppeteer headless browser 도입 | ⏸ **v0.11 위임** (D19 확정, 실 구현 별도) |
| 4 | `reaction_insight` `search_query_hints` 가이드 강화 | ✅ **v0.10.19.1 적용** (객체 양식 + source_hint 라우팅) |
| 5 | feature_mapping LLM system_prompt 보강 | ✅ **v0.10.23 적용** (5종 source-type 별 prompt 분배) |

**후속 작업 (별도 시리즈 — feature_extraction·4종 리포트 작성 후 진행)**

- **v0.10.18.1**: `data/taxonomy/{id}_slug.json` 7일 TTL 캐시 schema-aware 보강 (D28 부채 해소)
- **v0.11**: Playwright SPA fallback (D19 실 구현)

상세는 `feature_url_mapper_redesign.md` 의 §9 PR 시리즈 + §10 결정 항목 + §12 변경 이력 참조.

---

**v0.10.9 ~ v0.10.17 시점 (옛 4단계 분리) — history 보존**

본 시리즈 (v0.10.18+) 진입 전 토폴로지:

- `server/graph/nodes/url_discovery_brave_node.py`        (Step 0, **v0.10.22.1 cleanup PR 에서 파일 삭제**)
- `server/graph/nodes/page_meta_collect_node.py`           (Step 1, **v0.10.27 PR 에서 파일 삭제**)
- `server/graph/nodes/feature_mapping_llm_node.py`        (Step 2, **v0.10.27 PR 에서 파일 삭제**)
- `server/graph/nodes/additional_urls_validation_node.py` (Step 3, **v0.10.25 PR 에서 전면 재작성**)
- `server/graph/nodes/feature_url_mapper_node.py`         (헬퍼 모듈로 유지, **v0.10.25 임시 헬퍼 폐기**)
- `agents/feature_url_mapper/system_prompt_kr.md` + `output.schema.json` (**v0.10.27 PR 에서 디렉토리 삭제 — agents/feature_mapping_<source>/ 5종 분배**)

옛 4단계 분리 (v0.10.9) 의 상세 매트릭스·캐시 정책·해소 처방 분석은 git history 의 `pipeline_topology_redesign.md` v0.10.17 commit 또는 `feature_url_mapper_redesign.md` 의 §2 (AS-IS 결함 4건) 참조.

### 6-6. [P2a] `feature_extraction_node` 구현 (D3 확정 — 옵션 C 점진, v0.7)

**확정 사항** (D3 결정)

- **옵션 C — 점진 채택**: 옵션 B(Adapter-first)로 시작하여 사전 정의된 Playwright/BeautifulSoup 어댑터를 1차 경로로 사용하고, 어댑터 미지원 사이트에 한해 옵션 A(Code-gen actor)를 격리 샌드박스(서브프로세스 + 타임아웃 + 디스크 quota)에서 실행합니다.
- 채택 근거:
  - 트래블카드 파일럿 도메인의 공식 출처(은행 약관 PDF·카드사 안내 페이지)는 정형성이 낮아 순수 옵션 B로는 커버리지가 제한적입니다.
  - 옵션 A 단독은 안전성·재현성 리스크가 높아 MVP 단계에 부적합합니다.
  - 옵션 C는 어댑터 우선 → 실패 시 LLM fallback 구조로, 안전한 경로를 디폴트로 두면서 커버리지를 확장합니다.

**참고 — 검토 후 기각된 대안**

- 옵션 A 단독: 격리 샌드박스 운영 비용 + 비결정적 출력 리스크로 인해 1차 범위에서 제외.
- 옵션 B 단독: 어댑터 미보유 사이트가 발생할 때마다 사람 개입이 필요하므로 자동화 목적과 충돌.

**공통 사항**

- `agent_id="feature_extraction"`으로 `agent_cache` 캐싱 적용.
- 결정론적 출력이 필요하므로 `ClaudeApiAnalyzer(temperature=0)` 사용(`ProductIdResolver`와 동일 사상).
- `extraction_targets[*].selected_source_ids`에 명시된 URL만 추출 대상. 외부 URL 임의 fetch 금지.

**관측성**

- 각 사이트별 추출 결과를 `data/extraction/{target_id}/raw_response.json`에 저장(디버깅용, gitignore).
- 추출 실패는 `state["errors"]`에 누적, `unresolved_targets`에 등록 후 다음 노드는 정상 진행(부분 실패 허용).

### 6-6a. [P2a-2] 신규 데이터 수집 노드 6종 (v0.6 신설)

**도입 근거**

§6-6 `feature_extraction`은 자사·경쟁사의 **공식 출처(official source)** 정형 feature 추출에 한정됩니다. 4개 worked example과 reference 문서를 교차 점검한 결과, 7개 리포트 중 3종(`reaction_insight`, `marketing_social`, `market_context_swot`)이 공식 출처가 아닌 별도 데이터 소스를 요구합니다. 이 데이터를 수집할 노드가 현행 토폴로지에 부재하므로 본 절에서 6개 신규 수집 노드를 정의합니다.

**신규 노드 일람**

| 노드 ID                                                   | 기여 리포트              | 데이터 출처                                                                                                                         | 출처 reference                                          |
| ------------------------------------------------------- | ------------------- | ------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------- |
| `community_collection_node`                             | reaction_insight    | 클리앙·디시인사이드 등 익명 커뮤니티                                                                                                           | `reference/reaction_insight.md` §2-2 채널 2             |
| `app_store_review_collection_node` ⚠ **D11 비활성 (v0.8)** | reaction_insight    | App Store·Play Store 별점·리뷰 본문 (※ D11 옵션 c 채택으로 1차 범위 제외 — 코드 스켈레톤만 유지, conditional edge로 비활성)                                  | `reference/reaction_insight.md` §2-2 채널 3             |
| `youtube_channel_metadata_collection_node`              | marketing_social    | YouTube Data API v3 — 채널 통계·영상 메타데이터 (※ `youtube_collection`의 댓글 수집과 단위가 다름)                                                   | `reference/marketing_social.md` §1-7                  |
| `blog_rss_collection_node`                              | marketing_social    | 자사·경쟁사 공식 블로그 RSS·sitemap                                                                                                      | `reference/marketing_social.md` §1-7                  |
| `pr_release_collection_node`                            | marketing_social    | 공식 보도자료 페이지 (TV CF·옥외의 디지털 보조)                                                                                                 | `reference/marketing_social.md` §1-7                  |
| `market_context_collection_node`                        | market_context_swot | 산업 보고서·정부 통계·시장 규모/트렌드 매크로 검색 (Brave Search + LLM 요약). **캐시: 도메인 단위 + TTL 30일 + 사용자 force-refresh 트리거 (D13 확정 v0.8, §8-4 참조)** | `pipeline_topology_redesign.md` §11-10 (흐름 A 외부 컨텍스트) |

**공통 사항**

- `agent_id="{collection_node_name}"`으로 `agent_cache` 캐싱 적용. 캐시 키에 수집 대상 URL/검색어 해시 포함.
- 결정론적 정형 파싱이 가능한 항목(RSS·App Store 별점)은 LLM 호출 없이 직접 파싱. 자유 텍스트(커뮤니티 게시글·뉴스 본문)는 `ClaudeCodeCliAnalyzer`로 발췌·요약.
- 부분 실패 허용: 개별 채널/URL 수집 실패는 `state["errors"]`에 누적, `unresolved_targets`에 등록 후 다음 노드는 정상 진행. 한 채널 장애가 전체 리포트 입력 부재로 이어지지 않도록 합니다.
- 합법성 점검(앱스토어 리뷰·커뮤니티 스크래핑): D11 확정(v0.8, 옵션 c 채택) — 앱스토어 1차 제외, 커뮤니티는 robots.txt 준수 + rate limit + 사용자 식별 정보 제외 정책으로 운영. `reference/reaction_insight.md` §2-2 채널 가중치 재산정 필요(현행 YouTube 1.0 / 커뮤니티 1.2 / 앱스토어 0.8 → 2채널 기준 재정의).

**관측성**

- 수집 raw 데이터는 `data/collection/{node_name}/{run_id}/*.json`에 저장(디버깅용, gitignore).
- `agent_steps`에 `parallel_group: "data_collection"` 메타데이터 부여하여 진행 패널이 6개 노드의 동시 실행을 시각화할 수 있도록 합니다(§11-2와 정합).

### 6-7. [P2b] 후속 분석 노드 fan-out / fan-in (v0.6 — 신규 수집 노드 6종 + D1=B 분리형 반영)

**변경 대상 파일**

- `server/graph/graph.py`

**선행 단계 — feature_url_mapper 4단계 노드 그룹 (v0.10.9 갱신)**

`feature_selection` 의 직전 노드는 v0.10.9 부터 `additional_urls_validation_node` 입니다 (이전: 단일 `feature_url_mapper_node`). 4단계 분리 후 흐름:

```
ab_join → url_discovery_brave → page_meta_collect → feature_mapping_llm
        → additional_urls_validation → feature_selection (interrupt #4) → [본 절의 fan-out]
```

**엣지 정의** (D1=B 분리형 + 신규 수집 노드 6종 반영)

```python
# ── 1) feature_selection 이후: feature_extraction과 신규 수집 노드 fan-out ──
# ※ D11 확정(v0.8): app_store_review_collection은 1차 범위 제외, conditional로 비활성.
builder.add_edge("feature_selection", "feature_extraction")
builder.add_edge("feature_selection", "community_collection")
# builder.add_edge("feature_selection", "app_store_review_collection")  # D11 비활성 (v0.8) — 후속 단계 활성화 시 주석 해제
builder.add_edge("feature_selection", "youtube_query_planner")
builder.add_edge("feature_selection", "youtube_channel_metadata_collection")
builder.add_edge("feature_selection", "blog_rss_collection")
builder.add_edge("feature_selection", "pr_release_collection")
builder.add_edge("feature_selection", "market_context_collection")

# ── 2) YouTube 댓글 파이프라인 (기존) ──
builder.add_edge("youtube_query_planner", "youtube_collection")

# ── 3) reaction_insight: 2개 채널 fan-in → reaction_analysis → reaction_insight ──
# ※ D11 확정(v0.8): app_store_review_collection 채널 제외로 2채널 운영.
builder.add_edge("youtube_collection",          "reaction_analysis")
builder.add_edge("community_collection",        "reaction_analysis")
# builder.add_edge("app_store_review_collection", "reaction_analysis")  # D11 비활성 (v0.8)
builder.add_edge("reaction_analysis",           "reaction_insight")

# ── 4) marketing_social: 3개 채널 fan-in ──
builder.add_edge("youtube_channel_metadata_collection", "marketing_social")
builder.add_edge("blog_rss_collection",                 "marketing_social")
builder.add_edge("pr_release_collection",               "marketing_social")

# ── 5) comparison_matrix: feature_extraction만 의존 ──
builder.add_edge("feature_extraction", "comparison_matrix")

# ── 6) mid-tier 흐름 B 의존 (§11-10) ──
builder.add_edge("comparison_matrix", "positioning_map")
builder.add_edge("comparison_matrix", "battlecard")
builder.add_edge("reaction_insight",  "battlecard")
builder.add_edge("marketing_social",  "battlecard")
builder.add_edge("comparison_matrix",          "market_context_swot")
builder.add_edge("reaction_insight",           "market_context_swot")
builder.add_edge("marketing_social",           "market_context_swot")
builder.add_edge("market_context_collection",  "market_context_swot")

# ── 7) top-tier: 모든 6개 리포트 fan-in → executive_summary → END ──
builder.add_edge("comparison_matrix",   "executive_summary")
builder.add_edge("reaction_insight",    "executive_summary")
builder.add_edge("marketing_social",    "executive_summary")
builder.add_edge("battlecard",          "executive_summary")
builder.add_edge("positioning_map",     "executive_summary")
builder.add_edge("market_context_swot", "executive_summary")
builder.add_edge("executive_summary",   END)
```

**검증 방법**

- 토폴로지 시각 확인: `compiled_graph.get_graph().draw_ascii()`로 fan-out 7중 분기·fan-in 6중 통합이 정확한지 검증.
- 통합 테스트(부분 실패 허용): 신규 수집 노드 중 하나(예: `community_collection`)가 의도적으로 실패해도 (i) `reaction_analysis`가 나머지 채널로 부분 결과를 산출하고, (ii) `executive_summary`가 reaction_insight를 부분 신뢰도로 인용하는지 확인. (※ D11 비활성 채택으로 `app_store_review_collection`은 기본 비활성이며, 후속 단계에서 활성화 시 동일 테스트 적용.)
- 의존 누락 회귀 테스트: `positioning_map`이 `comparison_matrix` 미완료 상태로 실행되지 않는지 확인.

**리스크**

- `feature_selection`에서 7개 노드로 동시 fan-out 시 LLM·HTTP 동시 호출 부하 증가. `httpx.AsyncClient` 풀 설정과 LLM 호출 rate limit 사전 검증 필요.
- `executive_summary` fan-in 시 6개 리포트의 부분 실패 조합을 모두 처리해야 함. `reaction_insight` 부분 실패는 신뢰도 표기 강등, `comparison_matrix` 실패는 hard fail(SWOT·positioning_map·battlecard 의존 차단) 등 정책 결정 필요.

---

## 7. 차이 4 — Feature Selection UI 그룹핑 (v0.10.15 의도 명확화)

`feature_selection_node` 의 출력은 차이 3 적용 시 자동으로 `report_type` 메타데이터를 포함합니다(D6 — D4 enum 7종 단위). 본 UI 단계의 목적은 사용자가 **분석 진행 전에 다음 2가지를 확인**할 수 있도록 하는 것입니다.

**§7-1. UI 의 사용자 확인 목적 (v0.10.15)**

1. **리포트별로 작성에 사용되는 feature 와 해당 feature 를 수집할 URL 들이 정상 수집되었는지** 확인.
2. **리포트별 작성에 필요한 feature 가 정상 식별되었는지** 확인.

본 단계는 검증·디버깅 UX 이며, 사용자는 7종 리포트 카드별로 feature 목록과 URL 커버리지를 시각적으로 확인한 뒤 분석 항목을 선택합니다.

**§7-1a. Coverage 상세 표시 (v0.10.16 적용)**

§7-1 의 확인 목적 1 을 충족하기 위해 각 FeatureCard 에 **"URL 상세 보기" 확장 토글** 을 추가합니다. 확장 시 candidate × coverage × URL 상세가 노출되어 사용자가 어떤 URL 이 `sufficient`/`partial`/`not_found` 인지 직접 확인할 수 있습니다.

- **카드 기본 상태**: `coverage_summary` 칩 3종(`충분 N`/`부분 N`/`미확보 N`) 만 표시 (기존 v0.10.14 유지).
- **확장 시**: candidate 별 sub-block 렌더. 각 sub-block 에 (a) candidate_id + coverage 배지, (b) 기존 URL 목록(origin=공식/Brave 라벨), (c) 추가 URL 목록(validated ✓/✗ + http_status).
- **server 데이터 구조**: `feature_selection_node.py` 의 `feature_items[*]` 에 `coverage_details: list[{candidate_id, coverage, existing_urls, additional_urls}]` 신설 (기존 `coverage_summary` 카운트 호환 유지).
- **client 컴포넌트**: `CoverageDetails` 신설 + `FeatureCard` 에 `expanded` state + 토글 버튼. 클릭 시 `stopPropagation` 으로 체크박스 토글과 충돌 차단.

본 §7-1a 는 의도 1 (URL 정상 수집 확인) 의 결정론적 충족이며, 의도 2 (feature 정상 식별) 는 기존 카드 헤더 + description 으로 이미 충족됩니다.

**§7-2. 그룹핑·중복 표시 규칙 (v0.10.15 확정)**

- 좌측 패널 또는 단일 컬럼: 리포트 유형 7종(D4 enum) 을 카드 헤더로 노출.
- 각 카드 내부: 해당 리포트에 기여하는 feature 목록 + 각 feature 의 URL 커버리지 요약(`sufficient`/`partial`/`not_found`).
- **중복 표기 허용**: 동일 `feature_id` 가 복수 리포트에 기여할 경우 **모든 해당 카드에 시각적으로 표시**합니다. 사용자가 어느 리포트에 어떤 feature 가 쓰이는지를 직관적으로 확인 가능.
- **체크박스는 최초 등장 시에만 활성화**: 같은 `feature_id` 가 카드 순서상 두 번째 이상 등장하면 체크박스를 비활성화(disabled) + "공유 항목 — 다른 카드에서 선택" 보조 텍스트로 표시. 중복 선택을 방지하면서 시각적 정보는 보존합니다.

**§7-3. 구현 영향 분석 (v0.10.15)**

| 영역                                                | 현재 상태 (v0.10.14)                                                                            | §7 의도 (v0.10.15)                         |                              변경 필요                               |
| ------------------------------------------------- | ------------------------------------------------------------------------------------------- | ---------------------------------------- | :--------------------------------------------------------------: |
| server `feature_selection_node` 의 interrupt value | `reports: [{report_type, report_label, features: [...]}]` — feature 1회만 노출 (단일 report_type) | 동일 feature 가 복수 카드에 노출되어야 함              |     **변경 필요** — server 측이 한 feature 를 여러 카드에 포함하도록 데이터 구조 보강     |
| `AnalysisFeature.report_type` (단일 enum)           | 단일 enum 값                                                                                   | `report_types: list[str]` 또는 노출 매핑 별도 필드 |                **D4 결정 보강** — 단일 매핑에서 다중 매핑으로 확장                 |
| client `FeatureSelectionPage.jsx`                 | 단일 컬럼, 카드 내부 정상 렌더, 체크박스 모두 활성                                                              | 좌측 패널(선택) + 카드별 중복 표시 + 최초 등장만 활성 체크박스   | **변경 필요** — 카드 순회 시 "이미 등장한 feature_id 집합" 추적 + disabled 체크박스 분기 |

**§7-4. 구현 가능성 평가 (v0.10.15)**

| 항목           | 평가                                                                                                                                                                     |
| ------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| server 측 변경량 | 작음 (~20줄) — `feature_mapping_llm_node` 출력에 `report_types: list[str]` 또는 `feature_to_reports` 매핑 추가. D4 결정 보강 1줄.                                                       |
| client 측 변경량 | 작음 (~30줄) — `FeatureSelectionPage.jsx` 의 `ReportSection` 렌더 시 부모에서 `seenFeatureIds` Set 누적, FeatureCard 에 `firstOccurrence` prop 전달, 두 번째 이상은 `disabled` + "공유 항목" 배지. |
| 회귀 위험        | 낮음 — `report_type` 단일 enum 은 옛 호환 차원에서 유지하고 `report_types` 만 추가하면 v0.10.14 코드와 정합.                                                                                     |
| LLM 단계 영향    | 없음 — `feature_mapping_llm_node` 의 매핑 로직 자체는 변경 X. 후처리에서 동일 feature 를 여러 report 에 라벨링하는 방식.                                                                             |

**§7-5. 권장 적용 순서**

1. **server 측**: `AnalysisFeature` TypedDict 에 `report_types: list[str]` 필드 추가 (옛 단일 `report_type` 은 호환 유지 — `report_types[0]` 값으로 대응). `feature_mapping_llm_node` 의 LLM 호출 후처리에서 동일 `feature_id` 를 복수 report 에 라벨링.
2. **server `feature_selection_node`**: `reports_payload` 구성 시 한 feature 가 여러 카드에 등장하도록 그룹핑 로직 갱신.
3. **client `FeatureSelectionPage.jsx`**: `seenFeatureIds` Set 추가 + `FeatureCard` 에 `firstOccurrence` prop + disabled 분기.

본 §7 구현은 본 PR(v0.10.15) 의 범위에는 포함하지 않습니다 — 별도 PR(v0.10.16) 로 분리 권장. v0.10.15 는 본문 의도 명확화에만 집중합니다.

---

## 8. 캐시·스키마 마이그레이션 전략

### 8-1. `data/taxonomy/*.json`

- `report_types` 옵셔널 도입으로 강제 마이그레이션은 불필요합니다.
- 신규 호출은 `report_types`를 채우므로 7일 TTL 자연 만료 후 전체 갱신됩니다.
- 명시적 일괄 갱신이 필요한 경우 `scripts/refresh_taxonomy.py`(신규)로 모든 캐시를 force-enrichment 모드로 재호출합니다.

### 8-2. `data/cache/agent_cache/` (D10 확정 반영, v0.8 + v0.10.12/13 추가)

- `agent_id="domain_modeling"` 캐시는 입력 해시에 의존합니다. 입력 키 집합이 변경되면 자동으로 새 해시가 생성되므로 기존 캐시는 사용되지 않고 점진적으로 폐기됩니다.
- **흐름 B 의존 캐시 무효화 (D10 확정)**: 흐름 B(다른 리포트 Output 인용) 의존이 있는 노드의 캐시 키에는 **`upstream_outputs_hash`** 를 명시 포함합니다. 구성: `frozenset({(upstream_node_id, output_hash) for upstream in §11-10 의존 표})`. 상류 리포트 출력이 변경되면 하류 리포트 캐시가 자동 무효화되어 stale 인용 위험을 차단합니다.
- 적용 대상 노드(§11-10 표 기준): `battlecard`(comparison_matrix + reaction_insight + marketing_social), `positioning_map`(comparison_matrix), `market_context_swot`(comparison_matrix + reaction_insight + marketing_social), `executive_summary`(6개 리포트 전부).
- 기각된 대안: (b) 상태 전체 해시 — 무관 필드 변경에도 over-invalidation. (c) TTL만 — stale 결과로 잘못된 의사결정 위험.
- 디스크 절약이 필요하면 `find data/cache -name "*.json" -mtime +30 -delete` 형식의 정리 cron 권장.

**v0.10.12/v0.10.13 — feature_url_mapper 4단계 노드 캐시 정책**

`agent_cache.py` 에 `ttl_hours: float | None = None` 옵션이 추가되어(`load_agent_output`) v0.10.12 의 4개 노드 캐시가 24h TTL 로 동작합니다.

| agent_id | cache_input 키 | TTL | 비고 |
|---|---|:-:|---|
| `url_discovery_brave` | `{query, count}` (Brave 쿼리 단위) | 24h | 빈 결과는 미저장(일시 실패 재시도 보존) |
| `page_meta_collect` | `{url}` (URL 단위) | 24h | 빈 결과도 저장(반복 fetch 방지) |
| **`feature_url_mapper`** (Step 2 LLM) | **A-4: `{domain, own_product, active_reports, sorted(candidate_ids)}`** | **무한** | URL list 제외 — 결정론적 보장 |
| `url_validation` | `{url}` (URL 단위) | 24h | None status 도 저장(죽은 링크 재시도 방지) |

**A-4 cache_input 설계 원칙 (v0.10.13)**

- 변동 위험 필드(URL list·page_title·meta_description·matched_report_types·URL 순서) 모두 cache_input 에서 제외.
- 안정 필드(도메인 · own_product · active_reports keys · candidate_ids) 만 사용.
- LLM 실제 입력(`llm_input`) 은 그대로 사용 → 매핑 품질 보존.

**캐시 마이그레이션 패턴 (v0.10.13)**

`scripts/migrate_cache_v0_10_12.py` — schema 변경 시 옛 cache_input 으로 저장된 엔트리를 신규 cache_input 형식으로 변환 후 같은 output 을 신규 cache_key 로 재저장. 옛 엔트리는 보존(회귀 위험 없음). 동일 패턴을 향후 schema 변경 시 `scripts/migrate_cache_vX_Y_Z.py` 로 재사용 권장.

### 8-3. `data/extraction/` (신규)

- `feature_extraction_node` 도입 시 신규 디렉토리. `.gitignore`에 추가 필요.

### 8-4. `data/market_context/` (D13 확정, v0.8 신설)

- `market_context_collection_node` 전용 디렉토리. 파일명 규약: `{domain_id}.json` (예: `travelcard_kr.json`).
- 캐시 단위: **도메인 단위 (자사·경쟁사 조합과 무관)**. 매크로 데이터 특성상 동일 도메인의 모든 분석 세션이 동일 캐시를 공유합니다.
- TTL: **30일**. 매크로 데이터 갱신 주기(분기·반기)와 정합.
- **사용자 force-refresh 트리거**: 규제 변화·시장 사건 발생 시 사용자가 명시적으로 갱신을 요청할 수 있는 UI 트리거 제공(트리거 위치는 UI 결정 사항).
- 기각: 옵션 (a) TTL 단독 — 사건 발생 시 stale 위험. 옵션 (b) 매 실행 갱신 — 동일 도메인 반복 분석 시 비용 낭비.
- `.gitignore`에 추가 필요.

---

## 9. 위험 요소·열린 검증

### 9-1. State 키 충돌 사전 점검 매트릭스 (v0.9 — 분기 A vs 분기 B)

v0.9 토폴로지(§6-2)에서 `competitor_discovery` 종료 후 두 분기가 동시 진행됩니다. 분기 A(`normalize_competitor_ids` → `competitor_selection` → `official_source_resolver` → `url_retry`)와 분기 B(`domain_modeling` 단일)의 동시 쓰기 키를 점검합니다.

| 키 | 분기 A (4개 노드 누적) | 분기 B (domain_modeling) | 충돌 여부 |
|---|:-:|:-:|:-:|
| `agent_steps` | append | append | 안전(`operator.add`) |
| `errors` | append | append | 안전(`operator.add`) |
| `domain_taxonomy` | 미수정 | 갱신 | 안전 |
| `competition_axes` | 읽기 전용 | 읽기 전용 | 안전 |
| `competitor_candidates` | normalize에서 replace, 이후 미수정 | 미수정 | 안전 |
| `selected_competitor_ids` | competitor_selection에서 생성 | 미수정 | 안전 |
| `official_sources` | official_source_resolver/url_retry에서 갱신 | 미수정 | 안전 |
| `critical_error` | url_retry에서 설정 가능 | 미수정 | 안전 |
| `own_product_summary` | 미수정 | 읽기 전용 | 안전 |

점검 결과 **충돌 없음**. 단, 본 매트릭스는 코드 변경 시점에 재검증해야 하며, 향후 분기 A의 어떤 노드라도 `domain_taxonomy`를 read/write하게 되면 본 매트릭스 갱신이 필수입니다.

**v0.10.9 추가 — feature_url_mapper 4단계 분리 후 브릿지 state 키** (ab_join 이후 직렬 단계이므로 분기 충돌과 무관, 직렬 read/write 만 발생)

| 브릿지 키 | 생성 노드 | 소비 노드 | 충돌 여부 |
|---|---|---|:-:|
| `brave_urls_by_candidate` | url_discovery_brave_node | page_meta_collect_node | 안전(직렬) |
| `candidates_with_meta` | page_meta_collect_node | feature_mapping_llm_node | 안전(직렬) |
| `raw_features` | feature_mapping_llm_node | additional_urls_validation_node | 안전(직렬) |
| `analysis_features` | additional_urls_validation_node (기존) | feature_selection_node | 안전(직렬) |

4단계 노드 모두 `ab_join` 이후 직렬이므로 분기 A·B 충돌 매트릭스와 별개의 안전 영역입니다. 다만 향후 §6-6/§6-6a 의 신규 수집 노드 6종이 `feature_selection` 이후 fan-out 되며, 그 시점에는 다시 분기 충돌 검토가 필요합니다.

### 9-2. `domain_modeling` 호출 정책 (D2 재해석 v0.9 — 단일 호출)

v0.6~v0.8에서 D2는 "enrichment 2차 호출 자동 실행"으로 표기되었으나, v0.9 토폴로지 채택(§6-2)으로 1차/2차 phase 분리가 폐기되었습니다. `domain_modeling`은 `competitor_discovery` **종료 직후 단일 호출**로 실행되며, 이 시점에 `competition_axes`가 이미 확보되어 있으므로 별도 enrichment 단계가 불필요합니다.

- **단일 호출 시점**: `competitor_discovery` 완료 직후. `normalize_competitor_ids`와 병렬 진행(§6-2 분기 A·B).
- **자동 호출 보장**: D2의 "무조건 자동 실행" 의도는 유지됨. v0.9에서는 단일 호출이 이를 충족합니다.
- **임계치 분기 없음**: v0.7 기준 "competition_axes 미대응 30% 초과 시 enrichment 자동 호출" 논의는 v0.9에서 의미가 사라짐. 단일 호출 시점에 axes가 이미 결정되어 있으므로 미대응율 측정 자체가 불가능.
- **비용 영향**: §11-3 v0.9 비용 분석 참조. 1회 호출만 발생, 2-pass 가능성 폐기.

### 9-3. LangGraph 병렬 실행과 interrupt 상호작용

`competitor_selection`(interrupt #2)이 fan-in 직후에 위치하므로, `competitor_discovery`와 `domain_modeling` 두 분기가 모두 완료된 뒤 interrupt가 발생합니다. 사용자의 경쟁사 선택 화면이 표시되기 전까지 `domain_modeling` 결과는 화면에 노출되지 않지만, 백그라운드 1차 본은 이미 완성된 상태이므로 후속 진행이 빠릅니다. 이 거동이 스토리보드의 "Domain Modeling 박스가 Competitor Selection의 인라인 패널 옆에 위치" 의도와 부합하는지 확인이 필요합니다.

---

## 10. 확정 필요 항목 — 사용자 결정 대기

다음 항목은 본 문서 검토 후 결정 후 진행합니다.

- [x] **D1 (확정)**: 리포트 7종 매핑 방식 — 옵션 B(분리형) 선택. 반영 위치: §3-1 토폴로지 의도, §6-7 v0.6 엣지 정의, §11-4 롤백 계획, §11-10 다이어그램.
- [x] **D2 (재해석 확정 v0.9)**: `domain_modeling` 호출 정책 — `competitor_discovery` 종료 직후 **단일 호출**(v0.6~v0.8의 1차/2차 분리 폐기). v0.9 토폴로지(§6-2)에서 `competition_axes`가 이미 확보된 상태로 진입하므로 enrichment 단계 불필요. 반영 위치: §6-1 입력 계약(required 환원), §6-2 토폴로지(CD-fanout), §9-2 (v0.9 재해석 완료), §11-3 비용 분석(2-pass 폐기).
- [x] **D3 (확정)**: `feature_extraction` 구현 방식 — 옵션 C(점진). 옵션 B(Adapter-first) 1차 + 미지원 사이트는 옵션 A(Code-gen actor) 격리 fallback. 반영 위치: §6-6 (v0.7 동기화 완료).
- [x] **D4 (확정, v0.6)**: `report_types` enum 7종 확정 — `comparison_matrix`, `reaction_insight`, `marketing_social`, `battlecard`, `positioning_map`, `market_context_swot`, `executive_summary`. 명칭 정합성 매핑 표는 §6-3 D4 절 참조. worked example 7개 파일명·reference 문서 7개 파일명과 1:1 일치.
- [x] **D5 (확정)**: 캐시 마이그레이션 정책 — `scripts/refresh_taxonomy.py`로 일괄 force-enrichment. 반영 위치: §8-1 (`data/taxonomy/*.json`).
- [x] **D6 (확정)**: `feature_selection` UI에서 동일 feature가 복수 리포트에 기여하는 경우의 표시 방식 — 가장 비중 큰 리포트 카드에만 1회 표시 + 공유되는 다른 카드에는 체크 박스가 회색으로 비활성된 채로 표시되며 "공유 항목" 배지. 반영 위치: §7 (차이 4 UI 명세), §11-10 흐름 A 사용자 인터페이스 절.
- [x] **D7 (확정)**: 액션 가능성(actionability) 기준의 적용 방식 — 옵션 b: Query Intake 단계에서 사용자가 분석 방향(`marketing` / `product_dev` / `mixed`)을 명시 → Rubric이 대응 동사 집합으로 분기. 반영 위치: §11-9 (analysis_direction 흐름·동사 집합·출력 스키마 영향), §12 체크리스트의 §6-3 `action_lens` 옵셔널 필드 추가 항목.
- [x] **D8 (확정, 2026-05-21)**: 7개 리포트 worked-example reference 문서 — 옵션 b 채택 + 1–2개 예시를 Rubric §2에 발췌 인용해 LLM 컨텍스트에 함께 제공. **worked example 7종 작성 완료** (`docs/reference/examples/{enum}_toss_travel_card.md` 7개 파일 모두 생성). 반영 위치: §6-0 작업 산출물 절, §6-3 D4 매핑 표, §11-11 worked-example 작성·제공 방식.
- [x] **D9 (확정, v0.8)**: Rubric → system_prompt 통합 방식 — **방식 1 (inline 인용 + 빌드 스크립트) 채택**. 근거: (i) 빌드 시점에 prompt가 확정되어 모든 호출이 동일 prompt를 사용 → 캐시 키 안정성 확보(§11-8의 Rubric 버전 캐시 키 정책과 정합), (ii) 방식 2(런타임 동적 첨부) 기각 — 매 호출마다 첨부 토큰 비용 + Rubric 변경 시 캐시 무효화 범위 추적 난해, (iii) 방식 3(RAG/vector DB) 기각 — 현재 도메인 규모에서 과도. 반영 위치: §6-0 통합 메커니즘 절(v0.8 동기화 완료). 후속 작업: `scripts/build_prompts.py` 구현이 §12 체크리스트의 §6-0 항목으로 추가.
- [x] **D10 (확정, v0.8)**: 흐름 B(Output 인용) 의존 캐시 무효화 정책 — **옵션 (a) `agent_cache.py` 캐시 키에 `upstream_outputs_hash` 명시 포함 채택**. 근거: §11-10 표의 흐름 B 의존 결정론적 식별 + "Inline 인용 > Forward Reference" 원칙의 hard invalidation 요건. 적용 대상 4개 노드(`battlecard`·`positioning_map`·`market_context_swot`·`executive_summary`). 반영 위치: §8-2(캐시 키 정책 상세), §11-10 캐시 무효화 절(v0.8 동기화 완료).
- [x] **D11 (확정, v0.8)**: §6-6a 신규 수집 노드 6종의 데이터 출처 합법성·1차 범위 — **옵션 (c) 1차 범위 제외 후 reaction_insight를 YouTube + 커뮤니티 2채널로 운영 채택**. 근거: VoC "최소 2개 채널" 요건 충족 + ToS 리스크/유료 API 비용 회피 + 후속 점진 활성화 여지 보존. 반영 위치: §6-6a 노드 일람·공통 사항(`app_store_review_collection_node` 비활성 표기), §6-7 v0.6 엣지(conditional 주석 처리, 2채널 fan-in으로 갱신), v0.8 동기화 완료. **후속 작업**: (i) `reference/reaction_insight.md` §2-2 채널 가중치 2채널 기준 재산정 — 사용자 승인 후 별도 작업으로 분리, (ii) `app_store_review_collection_node` 코드 스켈레톤만 작성하여 후속 활성화 시 conditional edge 주석 해제로 즉시 가동 가능하도록 유지.
- [x] **D12 (확정, v0.8)**: §6-6a 신규 수집 노드들의 통합·분리 사상 — **옵션 (a) 6개 분리 노드 유지 채택** (현행 §6-7과 정합). 근거: fan-out 병렬화 이점·부분 실패 격리·캐시 키 분리·D11과의 정합성 모두 우위. 기각: 옵션 (b) 2개 통합·(c) 단일 통합. 반영 위치: §6-7 v0.6 엣지가 이미 분리형으로 정의되어 있어 본문 변경 불필요. v0.8에서는 §10 체크 처리만 수행.
- [x] **D13 (확정, v0.8)**: `market_context_collection_node`의 캐시 단위 — **옵션 (c) 도메인 단위 캐싱 + TTL 30일 + 사용자 force-refresh 트리거 채택**. 근거: 매크로 데이터의 도메인 단위 안정성 + 갱신 주기 정합 + 사건 대응 가능성. 반영 위치: §6-6a 노드 일람(캐시 정책 표기), §8-4 신설(상세 정책), v0.8 동기화 완료. **사용자 결정 대기 항목**: force-refresh UI 트리거 위치 — Query Intake / 결과 화면 / Feature Selection #4 중 선택(별도 UX 결정).

---

## 11. 추가 고려사항(이전 검토 미포함)

### 11-1. 테스트 전략

- **단위 테스트**: 각 노드는 `state` 슬라이스를 입력으로 받는 순수 함수에 가깝게 설계되어 있으므로, mock LLM(`ClaudeCodeCliAnalyzer.call_with_schema` 패치)로 결정론적 테스트가 가능합니다. 변경 노드마다 (a) 정상 입력 / (b) `competition_axes` 부재 / (c) 캐시 적중 3개 시나리오 최소 보강.
- **통합 테스트**: `compiled_graph.invoke()`로 end-to-end 흐름을 stub LLM과 함께 실행. 병렬 분기 시 두 노드의 `agent_steps`가 모두 누적되는지, fan-in 시 누락이 없는지 검증.
- **회귀 테스트**: 기존 캐시 디렉토리를 보존한 채 새 그래프를 실행하여 동일 입력에 대한 taxonomy 출력이 의미적으로 후방 호환인지 비교.
- **Rubric 기반 품질 평가**(§6-0 도입 시): 트래블카드·B2B SaaS·온라인 교육 3개 도메인에 대해 Rubric §2의 5점 척도로 DomainTaxonomyAgent 출력을 LLM-as-judge로 평가합니다. Rubric 도입 전 평균 점수와 도입 후 평균 점수의 paired t-test로 유의미성을 보고합니다.

### 11-2. 옵저버빌리티(LangGraph 진행 패널 연동)

- 스토리보드의 "파이프라인 진행 패널(인라인)"이 `agent_steps`의 누적 상태를 실시간 표시합니다. 병렬 노드 도입 시 패널 UI는 두 노드의 진행 상태를 동시 표시해야 하므로, `agent_steps` 항목에 `parallel_group: str` 필드 추가 검토(예: `"discovery_and_taxonomy"`).
- 현재 `progress_store.py`는 단일 active 노드를 가정하고 있을 수 있어, 다중 active를 허용하는지 확인이 필요합니다(별도 검증 작업으로 분리).

### 11-3. 비용 분석 (v0.9 → v0.10.13 갱신)

- **현행 v0.5 이전 (직렬 1-pass)**: `domain_modeling` LLM 1회 + `competitor_discovery` 1회 + 후속 노드들.
- **v0.9 토폴로지 채택 후**: `domain_modeling` 1회 + `competitor_discovery` 1회. 호출 수 동일, 토폴로지 변경 + 단일 호출로 비용 변동 없음. 단 분기 A·B 동시 시작 시 Anthropic API 동시 호출 1+ProductIdResolver burst가 발생하여 rate limit P0 limiter로 관리(§? 신설).
- **2-pass 항목 폐기 (v0.9)**: v0.6~v0.8의 "변경 후 2-pass(D2-a 선택 시) `domain_modeling` enrichment 1회 추가, 4k~8k input tokens 추정" 분석은 §6-1 phase 분리 폐기 + §9-2 D2 재해석으로 무관해짐.
- **`critical_error` 시 비용 영향 (v0.9 신설)**: `url_retry`가 `critical_error`로 END 분기 시 분기 B의 `domain_modeling` 호출(약 4k~12k tokens)이 무효 사용됨. 도메인 단위 캐시(`selected_competitor_ids` 미의존)로 다음 실행에서 재사용 가능하므로 단일 도메인 기준 평균 손실은 첫 회 실행 1회로 한정.
- **옵션 A(통합 insight_report)**: 1회 호출에 20k~40k tokens. 옵션 B(분리형, D1 확정)는 호출 수 7회로 분산.

**v0.10.9 ~ v0.10.13 feature_url_mapper 4단계 분리 및 캐시 효과 (실측 기준)**

| 시나리오 | feature_url_mapper 단계 wall-clock | LLM 호출 수 | 비고 |
|---|:-:|:-:|---|
| 캐시 미스 첫 실행 (v0.10.13 적용 후) | 약 30분 | 7회(parallel=4 → 2배치) | Step 2 단일 호출 약 1000초 |
| 캐시 hit (동일 domain + 동일 selected_competitor_ids 재실행) | **약 2–6초** | **0회** | A-4 cache_input + 4개 노드 24h TTL 캐시 |
| 부분 hit (URL 캐시 일부 만료) | 약 5–10초 | 0회 (feature_mapping_llm 은 hit, 다른 노드 부분 호출) | url_discovery_brave 일부 새 호출 |

- **결정론적 캐시 적중 보장 (v0.10.13 A-4)**: cache_input 에서 URL list 자체를 제외하고 `sorted(candidate_ids)` 만 사용 → 동일 domain + own_product + active_reports + selected_competitor_ids 조합이면 cache_key sha256 결정론적 동일. Brave 결과 변동 무관.
- **A-4 trade-off**: cache_input 이 매우 거시적이지만 사용자 우선순위(캐시 hit 가 매우 중요) 부합. 같은 candidate set 에서는 URL 도 결국 같은 brand 페이지들이므로 LLM 결과 의미상 동일.
- **timeout 분리 (v0.10.10) 비용 영향**: `FEATURE_MAPPING_LLM_TIMEOUT=600s` 로 Step 2 단일 호출이 600초까지 허용됨 → 단일 LLM 호출 비용은 변동 없지만 첫 회 실행의 wall-clock 안정성 향상.

### 11-4. 롤백 계획

- 본 변경은 코드 단위로는 `graph.py` 엣지 5줄 + `domain_modeling_node.py` `_decide_mode()` + 스키마 1개 필드입니다. git revert 1회로 원복 가능합니다.
- 캐시 측면에서는 `report_types` 옵셔널 필드가 도입되어도 기존 코드는 무시하므로, 데이터 단방향성 위반은 없습니다.
- 단, 옵션 D1-B(분리형 노드)를 채택해 신규 노드를 추가한 뒤 롤백하면 캐시에 사용되지 않는 항목이 남으나, 영향 없음.

### 11-5. 프런트엔드 영향 범위 (v0.10.4 → v0.10.14 갱신)

- **v0.6 시점 원안**: (i) Competitor Selection 화면에 Domain Modeling 1차 본 미리보기 인라인 패널 추가(스토리보드 의도), (ii) Feature Selection 화면의 리포트별 카드 그룹핑(차이 4).
- **v0.9 폐기**: 인라인 미리보기 의도 폐기 (§11-6). Competitor Selection 화면은 분기 A 만 표시.
- **v0.10.4 신설 — 진행 패널 4단계 + branches 트래킹**: `CompetitorSelectionPage.jsx` 의 `PIPELINE_STAGES` 를 4단계로 확장(URL 탐색·검증 / 실패 URL 재시도 / 도메인 분석 / 분석 항목 매핑). `progress_store.branches` 신설로 분기 B(domain_modeling) 상태를 stage 트랙과 독립적으로 emit. `branches[s.branch] === "done"|"running"` 으로 UI 분기 판정.
- **v0.10.9 신설 — UI 4-stage 분리**: `STAGE_INDEX` 에 `feature_mapping_brave` / `feature_mapping_meta` / `feature_mapping_llm` / `feature_mapping_validate` 4종 추가. 모두 idx=3 active 매핑 (PIPELINE_STAGES 4단계 유지 + detail 메시지로 sub-stage 표시).
- **v0.10.14 신설 — FeatureSelectionPage.jsx 키 정합**: server interrupt value 의 v0.10 키(`reports`/`report_type`/`report_label`) 와 client 의 옛 v0.6 키(`purposes`/`purpose_id`/`purpose_label`) 사이 dead-link 해소. 함수명 `PurposeSection → ReportSection`. server 호환 키 `selected_purposes` 자체는 보존(state.py 호환 결정), 값으로 `selected_report_types` 의 내용 전달.
- **v0.10.16 예정 — §7 의도 명확화 반영**: 동일 feature 가 복수 카드에 중복 표시 + 체크박스 최초 등장 시만 활성. server 측 `report_types: list[str]` 다중 매핑 + client 측 `seenFeatureIds` Set + disabled 분기. 별도 PR 로 분리.

### 11-6. 스토리보드와의 시각적 정합성 (v0.9 갱신)

- 스토리보드 다이어그램에서 `Domain Modeling`이 가로폭이 넓은 박스(`Competitor Discovery` 박스 위에 걸친 형태)로 그려진 것은 "두 노드가 동시에 동작한다"는 시각적 표현입니다. v0.9 토폴로지의 `competitor_discovery → {normalize_competitor_ids, domain_modeling}` fan-out 구조와 정합합니다.
- **인라인 미리보기 UX 의도 폐기 (v0.9 확정)**: v0.6~v0.8에서 검토되었던 "`competitor_selection` 화면에서 `domain_taxonomy` 1차 본을 인라인 패널로 노출" UX 의도는 사용자 결정으로 **폐기**되었습니다. 따라서 v0.9 토폴로지는 fan-in 시점을 `competitor_selection`(인라인 미리보기 의도)이나 `normalize_competitor_ids`(이전 v0.6 코드)가 아니라 **`feature_url_mapper`** 로 늦춰, interrupt #2·#3 대기 시간 + `official_source_resolver`·`url_retry` 진행 시간을 모두 `domain_modeling` 분기에 흡수할 수 있게 합니다.
- 스토리보드의 HITL 번호(1, 2, 3, 4)는 현재 코드 주석 번호와 일치합니다. 변경 없음.

### 11-7. 비기능 요구사항

- **응답 지연**: 병렬화 적용 시 사용자 체감 대기 시간은 `max(competitor_discovery, domain_modeling)`로 단축됩니다(현재는 합).
- **장애 격리**: 한 분기 실패가 다른 분기 결과에 영향을 주지 않도록 각 노드의 try-except 패턴을 유지합니다. `_error()` 헬퍼는 그대로 사용 가능합니다.
- **재실행성**: MemorySaver 체크포인터는 thread_id 기준으로 상태를 저장하므로, 사용자가 동일 검색어로 재실행 시 캐시 적중 + 직전 상태 복원이 가능합니다.

### 11-8. Report Taxonomy Rubric의 유지·확장 정책

- **분량 상한**: Rubric §2(7개 리포트 정의)는 리포트당 25–35줄로 제한, 총 약 200줄을 넘기지 않도록 합니다. system_prompt에 inline 인용 시 약 1.5k 토큰으로 환산되며, Claude Sonnet 컨텍스트 한계 대비 무시할 수준입니다.
- **갱신 주기**: 리포트 종류 추가/제거, 액션 동사 집합 변경 시에만 Rubric을 수정합니다. 도메인이 추가되어도 Rubric은 변경하지 않으며, 도메인별 worked example(D8 옵션 b/c)을 별도 파일로 추가합니다.
- **버전 관리**: Rubric 변경 시 §13 변경 이력에 기록하고, `agents/domain_modeling/system_prompt_kr.md`의 footer에 `# Rubric: vX.Y` 주석을 남깁니다. 캐시 키에 Rubric 버전을 포함하면 Rubric 변경 시 자동으로 캐시 무효화가 가능합니다.
- **다국어**: 향후 영문 도메인 확장 시 `reference/report_taxonomy_en.md` 분리 작성하고 system_prompt 언어와 일치하는 Rubric을 선택 주입합니다.

### 11-9. 액션 가능성(actionability) 기준의 분기 처리

- **D7 옵션 b 채택 시 흐름**: Query Intake가 `analysis_direction: "marketing" | "product_dev" | "mixed"`를 출력하고, Human Review interrupt #1에서 사용자가 확인·수정합니다. DomainTaxonomyAgent는 이 값을 입력으로 받아 Rubric §3의 해당 동사 집합으로 분기합니다.
- **동사 집합 안 (현행 초안)**:
  - `marketing`: 추가하라(어떤 신규 메시징/혜택을 제안할 것인가) · 유지·강화하라 · 재포지셔닝하라 · 방어하라.
  - `product_dev`: 기능 추가 · 기능 개선 · 기능 제거 · 우선순위 재조정.
  - `mixed`: 위 8개 동사를 모두 사용하되, 각 feature에 `action_lens: "marketing" | "product_dev" | "both"`를 부여.
- **출력 스키마 영향**: `purpose_config[*].features[*]`에 `action_lens` 필드 옵셔널 추가. UI/리포트가 이 라벨로 grouping·filtering 가능.
- **D7 옵션 c와의 비교**: 양쪽 라벨링은 토큰 비용이 약 20% 증가하나 사용자 결정 부담이 줄어듭니다. 옵션 b는 결정 부담이 늘지만 비용 효율적.

### 11-10. 7개 리포트 간 의존 관계 — 이원 흐름 모델

7개 리포트는 단일 DAG형 데이터 흐름이 아니라, **공유 Feature Pool**과 **선택적 Output 인용**이라는 두 흐름이 동시에 작동하는 구조로 운영됩니다. 직전 설계 검토에서 단순 DAG로 가정한 부분은 부정확하며, 본 절이 정확한 모델을 정립합니다.

**흐름 A — Feature Pool 사용 (모든 리포트가 참여)**

- `feature_extraction_node`가 수집한 모든 feature는 단일 Feature Pool에 저장됩니다.
- 각 리포트는 Pool에서 자신에게 필요한 feature 부분집합을 선택합니다.
- 공유 feature: 여러 리포트가 동시에 참조(예: `re_exchange_fee_rate`는 comparison_matrix·battlecard·positioning_map가 동시 사용).
- Dedicated feature: 단일 리포트만 사용(예: battlecard의 `competitor_marketing_copy`·`competitor_switch_story_quote`, marketing_social의 `channel_posting_frequency`).
- 사용자가 §10 D6에서 결정한 Feature Selection UI("가장 비중 큰 리포트 카드에만 1회 표시 + 공유 항목 배지")가 이 흐름의 사용자 인터페이스입니다.

**흐름 B — 다른 리포트 Output 인용 (선택적, 일부 리포트만)**

- 일부 리포트는 흐름 A 외에 다른 리포트의 **출력**을 추가 입력으로 사용합니다.
- 예: battlecard의 FIA Fact는 자체 feature(광고 카피) + comparison_matrix 결과(정량 비교) + reaction_insight 결과(사용자 quote)를 동시 인용.
- 예: executive_summary는 흐름 A를 거의 사용하지 않고 흐름 B만으로 6개 리포트 결론을 통합.

**원칙 — Inline 인용 > Forward Reference**

흐름 B를 채택하는 리포트는 다른 리포트로의 forward reference("자세한 내용은 X 리포트 참조")가 아니라, 상류 리포트 결과를 **inline으로 직접 인용**해야 합니다. 단일 리포트 페이지에서 의사결정 근거가 완결되지 않으면 사용자는 페이지를 오가며 정보를 조립해야 하고, 이는 리포트의 본래 목적("1페이지 의사결정 보조")을 훼손합니다. 본 원칙은 의존 리포트 목록을 정확히 정의하는 출발점이며, "필요한 결과는 모두 상류로 인용"이라는 단순 규칙으로 환원됩니다.

**리포트별 흐름 의존성 분류**

| 리포트                 |    흐름 A (자체 feature)     |    흐름 B (다른 Output 인용)    | LangGraph 배치                 |
| ------------------- | :----------------------: | :-----------------------: | ---------------------------- |
| comparison_matrix   |            ✓             |             ✗             | leaf, 독립 실행                  |
| reaction_insight    |            ✓             |             ✗             | leaf, 독립 실행                  |
| marketing_social    |      ✓ (채널 운영 데이터)       |             ✗             | leaf, 독립 실행                  |
| battlecard          | ✓ (광고 카피·switch story 등) | ✓ (comparison + reaction + marketing_social) | mid, **3개 리포트 완료 대기** |
| positioning_map     |            ✗             | ✓ (comparison_matrix 주로)  | mid, comparison_matrix 완료 대기 |
| market_context_swot |      ✓ (외부 시장 컨텍스트, **`market_context_collection_node` 신설 — §6-6a**)      |        ✓ (다수 리포트)         | mid/top, 의존 결정 필요            |
| executive_summary   |            ✗             |       ✓ (6개 리포트 통합)       | top, 모든 리포트 완료 대기            |

**다이어그램 (흐름 A·B 혼합)**

```
              ┌───────────────────────────────────────────┐
              │       Feature Pool (feature_extraction)    │
              │   공유 feature + 리포트별 dedicated feature │
              └────────────────┬──────────────────────────┘
                               │ (흐름 A)
        ┌──────────┬───────────┼───────────┬───────────┐
        ▼          ▼           ▼           ▼           ▼
  comparison    reaction    battlecard  marketing   market_context
   _matrix     _insight                 _social     _swot
        │          │           ▲           │           ▲
        │          │           │ (흐름 B)  │           │
        │          │           │           │           │
        └──────────┴───────────┘           └───────────┘
                                                  ▲
        positioning_map ◄── (흐름 B)               │
                                                  │
        executive_summary ◄── (흐름 B, 6개 통합) ──┘
```

**캐시 무효화 정책에 미치는 영향**

흐름 B 의존 관계는 캐시 invalidate가 전파되어야 합니다. 상류 리포트 출력이 변경되면 하류 리포트 캐시도 자동 무효화되어야 하므로, `agent_cache.py`의 캐시 키에 **상류 리포트 출력 해시**가 포함되어야 합니다. **D10 확정(v0.8)**: 옵션 (a) `upstream_outputs_hash` 명시 포함 채택. 구체적 캐시 키 구성·적용 대상 노드 목록은 §8-2를 참조합니다.

**LangGraph 엣지 구조에 미치는 영향**

v0.6에서 §6-7로 일원화되었습니다. 본 절의 초기 엣지 초안(leaf 3종이 `feature_extraction`에 직접 의존하던 단순 모델)은 §6-6a 신규 수집 노드 6종 도입으로 다음과 같이 보강되었습니다.

- `reaction_insight`는 `feature_extraction` 의존이 아니라 `reaction_analysis` 의존(3채널 fan-in 후 분석 → 리포트).
- `marketing_social`은 `feature_extraction` 의존이 아니라 신규 3채널(`youtube_channel_metadata_collection`·`blog_rss_collection`·`pr_release_collection`) fan-in 의존.
- `market_context_swot`는 흐름 A 의존이 `feature_extraction`이 아니라 `market_context_collection`.

정확한 엣지 정의는 §6-7 v0.6 코드 블록을 단일 진실원(SSOT)으로 참조합니다. D1 통합형(`insight_report_node` 단일 노드)을 선택하면 6개 mid/top 엣지가 노드 내부 로직으로만 존재하나, 의존 관계와 신규 수집 노드 6종은 통합 여부와 무관하게 동일하게 유지됩니다.

### 11-11. 7개 리포트 worked-example 문서의 작성·제공 방식

- **공통 구조 (리포트당 약 50–80줄)**:
  - 적용된 Rubric 항목 인용(목적·표준 카테고리·평가 기준)
  - 트래블카드 도메인에서 도출된 실제 feature 목록(8–12개)
  - 각 feature의 근거(공식 출처 URL + 인용)
  - 평가 루브릭 점수와 산정 근거
  - Anti-pattern 회피 사례(이 도메인에서 흔히 빠지는 함정과 어떻게 비켰는지)
- **LLM 제공 방식**(D8 옵션별):
  - Rubric 발췌 인용: 트래블카드 비교 매트릭스 예시 중 "표준 feature 카테고리 도출 예 1개" + "anti-pattern 회피 예 1개"를 Rubric §2-1에 ~10줄로 발췌 삽입. LLM이 few-shot 학습 효과를 얻으나 도메인 편향 위험이 존재합니다.
- **권장**: 옵션 c. 단, 발췌 인용 시 "이 예시는 트래블카드 도메인의 사례이며, 본인 도메인에 맹목적으로 복제하지 말 것"이라는 메타 지시를 함께 삽입해 편향을 완화합니다.

---

## 12. 변경 적용 체크리스트(요약)

본 체크리스트는 사용자가 §10의 결정을 확정한 뒤 실제 작업 진행 시 참조합니다.

- [x] §6-0 (완료, 2026-05-21): `docs/reference/report_taxonomy.md` v0.1 작성 — §1 개요 + §2 7개 리포트 정의 + §3 액션 가능성 동사 집합 + §4 anti-pattern 10종. 7개 reference 문서 + 4개 worked example을 출처로 inline 마크.
- [x] §6-0 (완료, 2026-05-21): D9 방식 1 채택에 따라 `scripts/build_prompts.py` 작성 + 1회 실행 → `agents/domain_modeling/system_prompt_kr.md`의 RUBRIC marker 영역에 §1·§2·§3 자동 inline 인용 완료. footer에 `<!-- Rubric: v0.1 -->` 부착.
- [x] §6-0 (완료, 사용자 작성): D8 옵션 b 채택에 따라 `docs/reference/examples/` 7개 worked example 모두 작성 완료(`{enum}_toss_travel_card.md` × 7).
- [ ] §6-0 (스캐폴드 완료, 실행 대기): Rubric 적용 전/후 LLM-as-judge 평가 — `scripts/eval_rubric.py` 스캐폴드 작성 + dry-run 검증 통과. 실제 LLM 호출·통계 처리는 5개 TODO 함수(`generate_taxonomy` · `call_judge` · `paired_t_test` · `bootstrap_ci` · `write_report`) 구현 후 `python scripts/eval_rubric.py --domain "토스 트래블카드" --iterations 20`으로 실행.
- [x] §6-1 (완료, 2026-05-21, v0.9): `domain_modeling_node.py` 전면 재작성 — `_decide_mode()`·`_needs_enrichment()` 제거, `ENRICHMENT_TRIGGER_THRESHOLD` 상수 제거, `REPORT_TYPES` 상수 신설, `analysis_direction` 입력 수신, 캐시 분기 단순화(3-mode → 2-mode), `_normalize_taxonomy_output()` v0.10 스키마 기준 재작성, `prompt_version` v0.10으로 갱신. `competition_axes`는 required 유지.
- [x] §6-1 (완료, 2026-05-21, v0.10): `agents/domain_modeling/input.schema.json`에 `analysis_direction` enum 필드 추가(D7 옵션 b 정합, default `mixed`). `agents/domain_modeling/config.yaml` `enrichment_trigger_threshold` 폐기 + description v0.10 정합.
- [x] §6-2 (완료, 2026-05-21, v0.9): `server/graph/graph.py` 토폴로지 재구성 완료 — `competitor_discovery → {normalize_competitor_ids, domain_modeling}` fan-out + `domain_modeling → feature_url_mapper` direct fan-in + `url_retry` conditional 분기 유지. 모듈 docstring v0.9 토폴로지 다이어그램으로 재작성. 미구현 노드 TODO 주석을 v0.10 + §6-7 v0.6 기준 13종(7개 리포트 + 6개 수집 + feature_extraction, `app_store_review_collection`은 D11 비활성)으로 확장. 정적 엣지 파싱으로 9개 + conditional 1개 검증 통과.
- [x] §6-3 (완료, 2026-05-21, v0.10 — D4 enum 7종 + 사용자 의문 1·2 채택 반영): `agents/domain_modeling/output.schema.json` 전면 재구조화 완료 — `active_purposes`·`purpose_config`·`url_types`·`url_type_priority` 폐기, `report_config` + `$defs.reportEntry`(label·active·features·feature_labels·categories·search_query_hints·aspect_codebook·action_lens) 신설, 7종 enum을 `required` 고정 키로 강제, `additionalProperties: false`. jsonschema self-validation + 샘플 v0.10 출력 검증 통과.
- [x] §6-3 (완료, 2026-05-21, v0.10): `agents/domain_modeling/system_prompt_kr.md` v0.10 전면 재작성 — `report_config` 7종 직접 매핑·`search_query_hints` 설계 원칙(url_types 대체)·`categories` Rubric §2-x 매핑표·`aspect_codebook` 채움 지시·anti-pattern AP-1~AP-10 회피 지시. `scripts/build_prompts.py` 재실행으로 RUBRIC §1·§2·§3 자동 inline 인용 + footer `<!-- Rubric: v0.1 -->` 부착.
- [x] §6-3 (완료, 2026-05-21, D7 옵션 b): `report_config[*].action_lens`를 옵셔널 필드로 신설. `mixed` 채택 시 `feature_id → "marketing"|"product_dev"|"both"` 매핑. `_normalize_taxonomy_output()`에서 키 동기화 처리 포함.
- [x] §6-1·§6-2·§6-3 부수 동기화 (완료, 2026-05-21): `server/config.py` FeatureUrlMapper 주석 v0.10 정합. `server/graph/state.py` docstring 3건(`domain_taxonomy`·`analysis_features`·`selected_purposes`) v0.10 스키마 정합. 폐기 키워드(`active_purposes`·`purpose_config`·`url_types`) 잔존 0건(폐기 명시 컨텍스트 제외).
- [x] §6-4 (스켈레톤 완료, 2026-05-21, v0.10.2): D1=B 분리형 채택 코드 차원 확정. (i) **7개 리포트 노드 스켈레톤 파일 작성** — `comparison_matrix_node.py`·`reaction_insight_node.py`·`marketing_social_node.py`·`battlecard_node.py`·`positioning_map_node.py`·`market_context_swot_node.py`·`executive_summary_node.py`. 각 노드 docstring에 §11-10 흐름 분류(A/B)·read·write keys·content 구조·Rubric §2-x 정합·캐시 키(D10)·상태 명시. (ii) **공통 helper `_report_node_common.py` 신설** — `REPORT_TYPES` 상수(domain_modeling_node와 정합)·`is_report_active`·`build_report_envelope`·`make_skip_result`·`make_error_result`. (iii) **state.py 보강** — `report_outputs: dict[str, dict[str, Any]]` 신설 + `feature_pool`·6종 수집 노드 출력 키·`final_report` 의미 갱신. 폐기 키(`query_insights`·`report_brief`) 제거. (iv) **`agents/{report_type}/` 7개 placeholder** — `config.yaml` + `README.md`(system_prompt·schema는 §6-5/§6-6 산출 형식 확정 후 작성). (v) **정합 검증 7건 통과** — 7개 노드 import + REPORT_TYPE 7종 정합 + output.schema.json required 정합 + skip/error 동작 + envelope 형식. **실제 LLM 호출 로직은 §6-5/§6-6/§6-6a 산출 형식 확정 후 각 노드에 구현 예정.**
- [x] §6-5 (완료, 2026-05-21, v0.10.3): `feature_url_mapper_node` 전면 재작성 완료 — (i) **노드 코드 v0.10**: Brave Search 패턴 4단계(Step 0 Brave 검색 + Step 1 Page Meta + Step 2 LLM(report_type별 병렬) + Step 3 additional_urls 검증) 도입, `search_query_hints` 토큰 치환(`{competitor_name}`·`{own_product}`·`{domain_name}`), `_extract_active_reports()`로 active=true 리포트만 처리, `prompt_version` `v0.10`으로 갱신. (ii) **`agents/feature_url_mapper/` v0.10 재작성**: input.schema.json에 `active_reports` + `origin`·`matched_report_types` 필드 신설, output.schema.json에 `report_type` enum 7종 명시(D4 정합), system_prompt v0.10 Brave 검색 패턴·report_config 단위 처리 규칙 명시. (iii) **`feature_selection_node.py` 동반 정리**: `purpose_id` → `report_type`, `purpose_config` → `report_config`, payload 변수 `purposes_payload` → `reports_payload`, interrupt 값 구조 v0.10(`reports: [{report_type, report_label, features: [...]}]`). (iv) **`AnalysisFeature` TypedDict 보강**: `report_type` 필드 명시 + docstring v0.10 갱신. (v) **정합 검증 5건 통과**: 4중 REPORT_TYPES 정합(domain_modeling·feature_url_mapper·feature_selection·_report_node_common), schemas self-validation + 샘플 input/output 통과, `_extract_active_reports`·`_substitute_tokens` 단위 동작. (vi) 폐기 키워드 잔존: 1건(docstring v0.6 변경 명시용, 의도된 잔존). **graph 실행 차단점 해소.**
- [ ] §6-6: D3 결정 후 `feature_extraction_node` 구현 착수
- [ ] §6-6a: 신규 수집 노드 6종 구현 (v0.6 신설, D11·D12·D13 결정 후 착수)
  - [ ] `community_collection_node` (reaction_insight 채널 2)
  - [ ] `app_store_review_collection_node` (reaction_insight 채널 3, D11 결정 의존)
  - [ ] `youtube_channel_metadata_collection_node` (marketing_social)
  - [ ] `blog_rss_collection_node` (marketing_social)
  - [ ] `pr_release_collection_node` (marketing_social)
  - [ ] `market_context_collection_node` (market_context_swot, D13 캐시 단위 결정 의존)
- [ ] §6-7 (v0.10 정합 갱신): `graph.py`에 후속 노드 fan-out/fan-in 엣지 적용 — v0.6 §6-7 정의(`feature_selection` 7중 fan-out + `executive_summary` 6중 fan-in)에 v0.10 D11(`app_store_review_collection` conditional 비활성) 반영. 현재 `graph.py`에는 TODO 주석으로 모든 엣지가 표기되어 있음(§6-2 작업 시 함께 작성). 노드 코드(§6-4·§6-6·§6-6a) 구현 완료 후 주석 해제로 활성화.
- [ ] §7: 프런트엔드 Feature Selection 카드 그룹핑
- [x] §6-2 v0.10.7 (완료, 2026-05-23): `graph.py` list-fan-in barrier 단일 재구성 — `add_edge(["url_retry", "domain_modeling"], "ab_join")` + `_route_after_url_retry` conditional. `scripts/verify_fanin.py` 로 1회 발화 검증.
- [x] §6-5 v0.10.9 (완료, 2026-05-23): feature_url_mapper 4단계 노드 분리 — `url_discovery_brave_node` / `page_meta_collect_node` / `feature_mapping_llm_node` / `additional_urls_validation_node` 신설. `state.py` 브릿지 키 3개 추가. 옛 노드 함수 폐기 + 헬퍼 모듈로 전환.
- [x] §6-5 v0.10.10 (완료, 2026-05-23): `CLI_TIMEOUT=300s` 상향 + `FEATURE_MAPPING_LLM_TIMEOUT=600s` 분리(env override). `.env` 동기화.
- [x] v0.10.11 (완료, 2026-05-23): Express fetch long-timeout `Agent` dispatcher 적용 — Python invoke 의 30분 wall-clock 흡수. `package.json` 에 undici 의존성 추가.
- [x] §8-2 v0.10.12 (완료, 2026-05-23): `agent_cache.py` 에 `ttl_hours` 옵션 추가 + feature_url_mapper 4개 노드에 24h TTL 캐시 신설(url_discovery_brave / page_meta_collect / url_validation).
- [x] §8-2 v0.10.13 (완료, 2026-05-24): A-4 `_make_stable_cache_input` 거시 축약 — `feature_url_mapper.json` cache_input 에서 URL list 제외, `sorted(candidate_ids)` 만 사용. `scripts/migrate_cache_v0_10_12.py` 1회 마이그레이션.
- [x] §11-5 v0.10.14 (완료, 2026-05-24): `FeatureSelectionPage.jsx` v0.10 키 정합 dead-link 수정 — `purposes/purpose_id/purpose_label → reports/report_type/report_label`. `selected_purposes` 호환 키 보존(state.py 호환).
- [x] §3-1·§3-2·§6-5·§6-7·§7·§8-2·§9-1·§11-3·§11-5 v0.10.15 (완료, 2026-05-24): 본 PR 본문 정합 갱신 — v0.10.7 ~ v0.10.14 변경의 본문 반영 + §7 UI 의도 명확화(중복 표기 + 최초 등장 체크박스만 활성).
- [ ] §7 v0.10.16 (계획): server `AnalysisFeature.report_types: list[str]` 다중 매핑 + `feature_selection_node` 의 reports_payload 중복 노출 + client `FeatureSelectionPage.jsx` 의 `seenFeatureIds` Set + disabled 체크박스 분기. 별도 PR.
- [ ] §11-1: 단위·통합·회귀 테스트 보강
- [ ] §11-2: `progress_store.py` 다중 active 노드 지원 검증
- [△] §11-8 (부분 완료, 2026-05-21): Rubric 캐시 키에 버전 포함 검증 — `domain_modeling_node`의 `prompt_version`을 `"domain_modeling:v0.10"`으로 갱신하여 v0.10 스키마 변경 시 캐시 자동 무효화. `system_prompt_kr.md`의 RUBRIC 영역 자체가 캐시 키의 `system_prompt` 해시에 반영되므로 Rubric 텍스트 변경 시 자동 무효화. 단 `<!-- Rubric: vX.Y -->` footer 버전을 캐시 키에 별도 명시 포함시키는 작업은 미수행(향후 Rubric 버전 마이크로 변경 시 추적 강화 차원).

---

## 13. 변경 이력

|   버전    | 일자         | 변경 내용                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | 비고               |
| :-----: | ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------- |
|   0.1   | 2026-05-19 | 초안 작성                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | DRAFT, §10 결정 대기 |
|   0.2   | 2026-05-19 | §6-0 [P0-Rubric] 추가, §10 D7/D8/D9 결정 항목 추가, §11-8/11-9/11-10 Rubric·액션 가능성·worked-example 정책 보강, §12 체크리스트 확장                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | DRAFT, §10 결정 대기 |
|   0.3   | 2026-05-19 | §6-3에 `aspect_codebook` 필드 추가(reaction_insight ABSA codebook 자동 생성). reference/reaction_insight.md §2-3·결정 1과 정합                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | DRAFT            |
|   0.4   | 2026-05-19 | §11-10 신설(7개 리포트 간 이원 흐름 의존 관계 모델 — Feature Pool 사용 흐름 A + Output 인용 흐름 B). 기존 §11-10 worked-example 작성·제공 방식은 §11-11로 번호 이동. §10 D10(흐름 B 캐시 무효화 정책) 추가. battlecard worked example 검토에서 도출                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | DRAFT            |
|   0.5   | 2026-05-19 | §11-10 battlecard 의존을 marketing_social 포함 3개 리포트로 확장. "Inline 인용 > Forward Reference" 원칙 명시. battlecard B-4의 UX 마찰 해소                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | DRAFT            |
|   0.6   | 2026-05-21 | D4(report_types enum 7종) 확정 — §6-3에 명칭 정합성 매핑 표 추가. §6-6a 신설 — 신규 수집 노드 6종(`community_collection`, `app_store_review_collection`, `youtube_channel_metadata_collection`, `blog_rss_collection`, `pr_release_collection`, `market_context_collection`) 명세. §6-7 v0.6 — D1=B 분리형 + 신규 수집 노드 6종 fan-out/fan-in 엣지 재정의(7중 fan-out + 6중 fan-in). §10 D11·D12·D13 신설(앱스토어 합법성·통합/분리 사상·매크로 캐시 단위). §11-10 LangGraph 엣지 §6-7로 일원화. §12 체크리스트 확장. 4개 worked example + 4개 reference 문서 §2(데이터 소스) 절 교차 점검 결과 반영                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | DRAFT            |
|   0.7   | 2026-05-21 | §10 결정 동기화 — worked example 7종 작성 완료 + D4 확정 후 후속 점검. D1/D2/D3/D5/D6/D7/D8 [x] 체크 처리(각 항목별 본문 반영 위치 명시). D2 후속 — §9-2 "결정 열려 있다" 표현 제거, 자동 호출 정책 확정으로 재작성. D3 후속 — §6-6에 옵션 C 채택 명기, 기각된 옵션 A·B 단독안 사유 기록. D9·D10·D11·D12·D13 미결정 5종에 권장안·근거·트레이드오프 inline 작성(사용자 최종 결정 대기)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | DRAFT            |
|   0.8   | 2026-05-21 | D9·D10·D11·D12·D13 5종 권장안 채택 확정 + 본문 동기화. **D9** 방식 1(inline 인용 + 빌드 스크립트) — §6-0 통합 메커니즘 절 재작성. **D10** upstream_outputs_hash 캐시 키 — §8-2·§11-10 캐시 무효화 절 재작성. **D11** 앱스토어 1차 제외 — §6-6a 노드 일람·공통 사항·§6-7 엣지 conditional 비활성 + 통합 테스트 예시 갱신. **D12** 6개 분리 노드 유지 — 본문 변경 없음(현행과 정합). **D13** 도메인 단위 + TTL 30일 + force-refresh — §6-6a 캐시 정책 표기 + §8-4 신설. §10 D9~D13 모두 [x] 체크 처리. v0.8 완료로 **§10 13개 항목 모두 결정 확정** — 권장 변경 순서(§6-0 → §6-7) 실행 가능 상태 진입                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | DRAFT            |
|   0.9   | 2026-05-21 | **토폴로지 재구성 — CD-fanout + FUM fan-in 채택**. §11-6 인라인 미리보기 UX 의도 폐기 결정에 따라: **§6-2** `competitor_discovery` 직후 `{normalize_competitor_ids, domain_modeling}` fan-out + `feature_url_mapper`에서 fan-in으로 재작성, 분기 A·B 정의·검증 방법·리스크 갱신. **§6-1** 1차/2차 phase 분리 폐기 → 단일 호출 모드, `competition_axes` required 환원, `_decide_mode()` 단순화 명시. **§9-2** D2 재해석 — enrichment 2차 호출 정책에서 "단일 호출" 정책으로 전환. **§10 D2** 동기화 — "재해석 확정 v0.9" 표기. **§11-3** 비용 분석 갱신 — 2-pass 항목 폐기, `critical_error` 시 LLM 낭비 + 도메인 단위 캐시 적중 분석 추가. **§11-6** 스토리보드 정합성에 UX 의도 폐기 명시. 토폴로지 변경 의도: interrupt #2·#3 대기 시간 + `official_source_resolver`·`url_retry` 진행 시간을 `domain_modeling` 분기에 흡수, 캐시 키 도메인 단위 유지(`selected_competitor_ids` 미의존)로 적중률 최대화                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | DRAFT            |
|  0.10   | 2026-05-21 | **DomainTaxonomyAgent 스키마 재구조화 — `report_config` 직접 매핑 + `search_query_hints` 도입**. 사용자 의문 2건 채택에 따라: (1) **`active_purposes`·`purpose_config` 폐기** → `report_config[report_type]` 직접 매핑(7종 enum 고정 키). 중간 추상화 layer 제거로 UI(D6)·노드(D1=B)·캐시 키(D10) 모두 단일 단위 정합. (2) **`url_types`·`url_type_priority` 폐기 → `search_query_hints` 신설**. `feature_url_mapper`가 `official_source_resolver`와 동일한 Brave API 검색 패턴 채택, DomainTaxonomyAgent는 사전 url_types 추측이 아닌 한국어 자연어 검색 쿼리 템플릿 제공. **§6-3** 스키마 예시 전면 재작성 + 호환성·캐시 마이그레이션 절 갱신. **§6-5** `feature_url_mapper` Brave 검색 패턴 + `report_config` 단위 입력 어댑터 명시. **`output.schema.json`** $defs.reportEntry 신설(label·active·features·feature_labels·categories·search_query_hints·aspect_codebook·action_lens). **`input.schema.json`** `analysis_direction` enum 필드 추가(D7 옵션 b 정합). **`system_prompt_kr.md`** v0.10 전면 재작성 + `scripts/build_prompts.py` 재실행 완료                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | DRAFT            |
| 0.10.1  | 2026-05-21 | **§6-1·§6-2·§6-3 코드 적용 완료 + §12 체크리스트 동기화**. (i) `server/graph/nodes/domain_modeling_node.py` 전면 재작성(623줄 → 442줄, 약 -29%) — `_decide_mode`·`_needs_enrichment`·`ENRICHMENT_TRIGGER_THRESHOLD` 폐기, `REPORT_TYPES` 상수 신설, `analysis_direction` 입력 수신, `_normalize_taxonomy_output` v0.10 정합. (ii) `server/graph/graph.py` v0.9 CD-fanout + FUM fan-in 적용(정적 엣지 파싱 9개 + conditional 1개 검증 통과). (iii) `output.schema.json` jsonschema self-validation + 샘플 출력 검증 통과. (iv) 부수 동기화 — `server/config.py` 주석·`server/graph/state.py` docstring 3건 v0.10 정합. **§12 체크리스트** §6-1·§6-2·§6-3 항목 [x] 처리(8개 sub-item) + §6-5·§6-7 표현을 v0.10 정합으로 갱신 + §11-8 부분 완료(△) 표기. 잔존 작업: §6-5(feature_url_mapper 재작성)·§6-6(feature_extraction)·§6-6a(수집 노드 6종)·§6-7(엣지 활성화)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | DRAFT            |
| 0.10.2  | 2026-05-21 | **§6-4 P1a 스켈레톤 적용 — D1=B 분리형 코드 차원 확정**. (i) 7개 리포트 노드 스켈레톤 파일 작성(`comparison_matrix_node.py` ~ `executive_summary_node.py`) — 각 노드 docstring에 §11-10 흐름 분류·read/write keys·content 구조·Rubric §2-x 정합·캐시 키(D10) 명시. (ii) 공통 helper `_report_node_common.py` 신설 — `REPORT_TYPES` 상수(domain_modeling_node와 정합) + `is_report_active`·`build_report_envelope`·`make_skip_result`·`make_error_result`. (iii) `server/graph/state.py` 보강 — `report_outputs: dict[str, dict[str, Any]]` 신설(7종 리포트 산출 누적, envelope 형식 명시) + `feature_pool` + 6종 수집 노드 출력 키 + `final_report` 의미 갱신. 폐기 키(`query_insights`·`report_brief`) 제거. (iv) `agents/{report_type}/` 7개 placeholder — `config.yaml` + `README.md`(system_prompt·schema는 §6-5/§6-6 산출 형식 확정 후 작성). (v) 정합 검증 7건 통과 — 7개 노드 import + REPORT_TYPE 정합 + output.schema.json required 정합 + skip/error 동작 + envelope 형식. **§12 체크리스트** §6-4 항목 [x] 처리. 잔존 작업: §6-5(feature_url_mapper)·§6-6(feature_extraction)·§6-6a(수집 노드 6종)·§6-7(엣지 활성화 + 노드 LLM 호출 로직)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | DRAFT            |
| 0.10.3  | 2026-05-21 | **§6-5 P1b 완료 — feature_url_mapper v0.10 Brave 검색 패턴 전면 적용 (graph 실행 차단점 해소)**. (i) `server/graph/nodes/feature_url_mapper_node.py` 전면 재작성 — 4단계 처리 흐름(Step 0 Brave 검색 + Step 1 Page Meta + Step 2 LLM report_type별 병렬 + Step 3 additional_urls 검증). `_extract_active_reports`로 active=true 리포트만 처리, `_substitute_tokens`로 `{competitor_name}`·`{own_product}`·`{domain_name}` 치환, `_discover_via_brave`로 Brave Search API 호출(`_BRAVE_MAX_QUERIES=3` per 리포트, ThreadPoolExecutor 병렬). `prompt_version` `v0.10` 갱신. (ii) `agents/feature_url_mapper/` v0.10 전면 재작성 — input.schema.json `active_reports` + `origin`·`matched_report_types` 필드, output.schema.json `report_type` enum 7종(D4 정합), system_prompt Brave 패턴·report_config 단위 처리 규칙. (iii) `feature_selection_node.py` 동반 정리 — `purpose_id` → `report_type`, payload 변수 갱신, interrupt 값 `reports` 구조 v0.10. (iv) `AnalysisFeature` TypedDict에 `report_type` 필드 명시 + docstring v0.10. (v) 정합 검증 통과 — 4중 REPORT_TYPES 정합(domain_modeling·feature_url_mapper·feature_selection·_report_node_common), schemas + 샘플 input/output 검증, 단위 동작 테스트. **§12 §6-5 항목 [x]. graph 실행 시 v0.6 가정으로 인한 차단점 해소.** 잔존 작업: §6-6(feature_extraction)·§6-6a(수집 노드 6종)·§6-4 노드 LLM 호출 로직·§6-7(엣지 활성화)                                                                                                                                                                                                                                                                                                                                                                                                                                   | DRAFT            |
| 0.10.4  | 2026-05-22 | **UI 4단계 확장 + 분기 B 상태 emit**. `server/graph/progress_store.py`에 `branches: dict[str, str]` 신설 + `set_branch_status(thread_id, branch, status)` 함수 추가(`pending`/`running`/`done`/`failed`). `domain_modeling_node` 진입·완료·실패 시점에 `set_branch_status("domain_modeling", ...)` emit. `CompetitorSelectionPage.jsx` `PIPELINE_STAGES`를 4단계로 확장(idx=2 `domain_modeling` 신설, `feature_mapping`이 idx=3로 이동), `s.branch` 정의 단계는 `progress.branches[s.branch]` 값으로 active/done 판정. `set_progress`/`init_candidates`/`update_candidate`가 `branches` 보존하도록 수정                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | DRAFT            |
| 0.10.5  | 2026-05-22 | **명시적 `ab_join` no-op 노드 도입 — fan-in race 1차 차단 시도(미해결)**. `competitor_discovery → {normalize_competitor_ids, domain_modeling}` 분기 후 `domain_modeling`이 캐시 적중으로 5ms 만에 완료되어 `feature_url_mapper`가 분기 A 완료 전에 발화되는 race를 차단하기 위해 `ab_join` 명시적 join 노드 도입. `url_retry --(conditional)--> ab_join` + `domain_modeling --(direct)--> ab_join` 구조. 그러나 conditional + direct 혼합 edge가 LangGraph의 채널 기반 AND-fanin을 깨뜨려 ab_join이 두 번 발화되는 race가 잔존(`feature_url_mapper` 1차 실행 시 `official_sources=없음` 실패)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | DRAFT            |
| 0.10.6  | 2026-05-23 | **fan-in race 최종 해소 + UI polling 보존**. (i) **`graph.py` v0.10.6** — `url_retry → ab_join` 을 **direct edge** 로 변경, critical_error 분기를 `ab_join → conditional` 로 이동(`_route_after_ab_join`). 두 incoming edge가 모두 direct edge 이므로 LangGraph Pregel 채널 기반 AND-fanin이 보장됨 — 분기 A·B 모두 완료되어야 `ab_join` 실행. `_route_after_url_retry` 제거. 진단 출력 v0.10.6 토폴로지 검증 키 추가. (ii) **`api.py` interrupt 시 `clear_progress` 스킵** — interrupt 발생 시점에 progress_store 전체를 폐기하지 않고 `is_interrupted=False`(END 도달) 시에만 clear. 이전(v0.10.5 이하)에는 매 interrupt 마다 `branches.domain_modeling="done"` 이 사라져 UI 에 도메인 분석 단계가 항상 ○ pending 으로 표시되는 문제 해소. (iii) **§13** v0.10.4·v0.10.5·v0.10.6 변경 이력 누적 기록                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | DRAFT            |
| 0.10.7  | 2026-05-23 | **list-fan-in barrier 단일 재구성 — race 의 근본 원인 제거 (v0.10.5·v0.10.6 폐기)**. 외부 1차 자료 조사 결과 LangGraph 이슈 #3249(2025-01-30) 가 conditional + direct 혼합 fan-in race 의 trace 임을 확인. v0.10.6 의 양방향 direct edge 도 single-direct-edge 다중 fan-in 의 AND-wait 보장 여부를 결정론적으로 확인할 수 없었음. **해결책으로 LangGraph 공식 list-based fan-in barrier API** (`builder.add_edge(["A", "B"], "C")`) **를 채택**. 본 환경에서 `scripts/verify_fanin.py` 로 1회 발화(join_count=1, a=True b=True) 검증 완료. (i) **`graph.py` v0.10.7** — `_route_after_ab_join` 제거, `_route_after_url_retry` 부활(정상경로 키 `"join"`). 엣지 구성: `add_conditional_edges("url_retry", _route_after_url_retry, {"end": END, "join": "ab_join"})` + `add_edge(["url_retry", "domain_modeling"], "ab_join")` + `add_edge("ab_join", "feature_url_mapper")`. ab_join 노드는 진단 print + barrier 의미 부여 목적으로 유지. 진단 블록의 토폴로지 검증 키를 list-edge 평탄화 페어(url_retry/domain_modeling → ab_join) 검증으로 갱신. (ii) **api.py `clear_progress` interrupt-skip 패치 유지** — race 와 무관한 별개 개선으로 v0.10.6 적용분 유지. (iii) **검증 근거**: LangGraph 공식 docs(Graph API multiple-source fan-in), LangChain Changelog "Deferred nodes in LangGraph"(2025-05-20), LangGraph 이슈 #3249·#954, `scripts/verify_fanin.py` 로컬 실행 결과. **잔존 작업**: §6-6·§6-6a·§6-4 LLM 호출·§6-7 엣지 활성화 (이전과 동일)                                                                                                                                                                                                                                                                                                                                                                                                          | DRAFT            |
| 0.10.8  | 2026-05-23 | **feature_url_mapper A안 단독 적용 — Step 2 LLM 입력 슬림화 (timeout 완화 1차)**. 진단(직전 응답 §3) 결과 timeout 의 가장 유력한 원인은 Step 2 단일 LLM 호출의 입력 토큰 과대 (10K–22K) 임을 확인. v0.10.7 까지는 `_call_for_report` 가 매 report_type 호출에 `candidates_with_meta` 전체(자사 + 모든 선택 경쟁사 × validated_urls 전체) 를 통째로 LLM 입력에 포함하여 활성 리포트 7개 호출 시 동일 brave_search URL 이 최대 6회 중복 전송됨. **A안 적용**: `server/graph/nodes/feature_url_mapper_node.py` 에 `_filter_candidates_for_report(candidates_with_meta, report_type)` 헬퍼 신설(Step 2 헬퍼 section). 규칙: (a) `origin="official_source"` URL 은 모든 report_type 에 공통이므로 유지, (b) `origin="brave_search"` URL 은 `matched_report_types` 에 본 report_type 이 포함된 항목만 유지, (c) 유지 URL 이 0개인 candidate 는 결과에서 제외. `_call_for_report` 의 `r_input["candidates"]` 를 슬림화된 값으로 교체. **예상 효과**: candidate 영역 토큰 약 50–60% 감소, 활성 리포트 7개 합산 LLM 호출 총 토큰 약 30–40% 절감, Step 2 wall-clock 비례 감소. **검증**: ast.parse 통과, mock 데이터 5종 단위 테스트(comparison_matrix·market_context_swot·reaction_insight·빈입력·빈URL) 통과. **부수 영향**: `cache_input = llm_input` (L207) 구조 변경으로 기존 v0.10 캐시 1건(2026-05-23 11:03:33 생성, 토스트래블카드) 자동 미스 처리 — 첫 실행 시 LLM 재호출 발생(의도된 동작). **D안 prompt caching 은 별개 PR 로 분리**. 잔존 작업: 이전과 동일 + 1회 실행 후 토큰·wall-clock 측정                                                                                                                                                                                                                                                                                                                                                                                                                                                          | DRAFT            |
| 0.10.9  | 2026-05-23 | **feature_url_mapper 4단계 노드 분리 + parallel 2→4 + UI 4단계 stage 분리 (옵션 A + 옵션 2)**. A안(v0.10.8) 적용 후 실측에서 timeout 재발 → 추가 처방으로 (a) parallel 증가, (b) 단계별 timeout 격리, (c) UI 진단성 향상의 3중 변경을 단일 PR 로 적용. **(1) parallel 2→4** (server/config.py L75): 활성 리포트 7개 배치 4→2 로 감소, 이론적 wall-clock 약 50% 절감. **(2) 4개 노드 분리** (옵션 A): 단일 feature_url_mapper_node 를 url_discovery_brave_node / page_meta_collect_node / feature_mapping_llm_node / additional_urls_validation_node 4개 노드로 분리. 각각 자체 set_progress emit + 단계별 timeout 격리. state.py 에 브릿지 키 3개 신설(brave_urls_by_candidate · candidates_with_meta · raw_features). 옛 feature_url_mapper_node.py 는 헬퍼 모듈로 전환(노드 함수 폐기, 헬퍼 13개 + 상수 1종 유지). graph.py 토폴로지 갱신: `ab_join → url_discovery_brave → page_meta_collect → feature_mapping_llm → additional_urls_validation → feature_selection`. **(3) 4단계 stage 분리** (옵션 2): progress_store.STAGE_MESSAGES 에 feature_mapping_brave / feature_mapping_meta / feature_mapping_llm / feature_mapping_validate 4종 추가. 각 신규 노드가 해당 stage emit. CompetitorSelectionPage.jsx STAGE_INDEX 에 4종 매핑 추가(모두 idx=3 active). **검증**: 9개 파일 ast.parse 통과, 신규 노드 4개의 10개 import 심볼 모두 feature_url_mapper_node 에 정의 확인, graph.py 토폴로지 진단 블록 8개 엣지 검증 추가. **부수 영향**: 노드 분리로 LLM 호출 노드의 단독 timeout 격리 가능 — 향후 feature_mapping_llm_node 만 별도 CLI_TIMEOUT 부여하거나 D안(prompt caching) 적용 시 단일 노드만 수정. 잔존 작업: 1회 실행으로 4단계 wall-clock 분포 측정 + 단일 LLM 호출 timeout 발생 여부 재진단                                                                                                                                                                                                                                    | DRAFT            |
| 0.10.10 | 2026-05-23 | **CLI_TIMEOUT 300s 상향 + FEATURE_MAPPING_LLM_TIMEOUT 분리 (timeout 응급 처방)**. v0.10.9 실측에서 `feature_mapping_llm_node: report_type=comparison_matrix 실패 — Claude CLI timeout (180s 초과)` 확인 → 입력 토큰 약 13K(system_prompt 5K + active_reports 2.5K + 슬림화된 candidates 3–4K + 출력 schema 2.3K) + 출력 약 6–10K 의 단일 LLM 호출이 사용자 환경의 `CLI_TIMEOUT=180` 환경변수도 초과. 추가 데이터 확인: `data/taxonomy/3_slug.json` 의 comparison_matrix features=10 (활성 7개 리포트 총 features 55개) 가 가장 무거운 호출. **외과적 변경 (server/config.py 2건 + feature_mapping_llm_node.py 2건)**: (i) `CLI_TIMEOUT` 기본값 120→300s 상향 — 본 변수는 query_intake / competitor_discovery / domain_modeling / official_source_resolver / url_retry 등 모든 일반 LLM 호출에 공통 적용되므로 안전 마진 확보. (ii) `FEATURE_MAPPING_LLM_TIMEOUT` 신규 상수 신설 — `os.getenv("FEATURE_MAPPING_LLM_TIMEOUT", str(CLI_TIMEOUT))` 로 기본값은 CLI_TIMEOUT 과 동일(300s) 이지만 환경변수로 별도 override 가능. 실측에 따라 운영자가 본 값만 더 늘릴 수 있음. (iii) `feature_mapping_llm_node` 의 ClaudeCodeCliAnalyzer 호출이 `CLI_TIMEOUT` → `FEATURE_MAPPING_LLM_TIMEOUT` 사용으로 교체. **부수 검증**: 다른 5개 노드(domain_modeling / query_intake / competitor_discovery / official_source_resolver / url_retry) 의 CLI_TIMEOUT 사용은 그대로 유지(영향 없음 확인). config.py + feature_mapping_llm_node.py 둘 다 ast.parse 통과. **본 변경은 응급 처방이며 근본 해소 아님** — 사용자 stdout 의 timeout 이 180s 초과로 측정되었으므로 300s 도 위태로울 수 있음. D안(Anthropic Prompt Caching) 적용을 다음 PR 로 진행 권장                                                                                                                                                                                                                                                                                   | DRAFT            |
| 0.10.11 | 2026-05-23 | **Express fetch long-timeout dispatcher 적용 — Node.js native fetch 300s race 차단**. v0.10.10 적용 후 실측에서 timeout 자체는 해소되었으나 새로운 race `[POST /api/approve] 오류: Python LangGraph 서버에 연결할 수 없습니다. fetch failed` 발생. 진단 결과 Node.js native fetch(undici 기반) 의 기본 `headersTimeout`·`bodyTimeout` 이 300초로 고정 → Python invoke 의 동기 호출이 그 한도를 초과하면 Express 측이 일찍 끊는 패턴 확정. **외과적 변경 (server/routes/analysisRouter.js 1건)**: (i) `const { Agent } = require('undici')` import 추가. (ii) `pythonInvokeAgent` 신설 — `headersTimeout`·`bodyTimeout` 을 `PYTHON_INVOKE_TIMEOUT_MS`(기본 30분=1,800,000 ms, env override) 로 확장. `connectTimeout=10s` 유지. (iii) `callInvoke` 의 fetch 호출에 `dispatcher: pythonInvokeAgent` 추가. **부수 영향 없음**: `/api/progress` (1.5s polling) 와 `/api/state` (단순 조회) 의 fetch 는 기본 dispatcher 그대로 사용. /invoke 전용 dispatcher 분리. **검증**: `node --check` syntax 통과, dispatcher 가 `/invoke` 호출에만 부착됨 확인. **잔존 우려**: 본 PR 은 Python 측 wall-clock 이 30분 안에 끝난다는 가정. 30분 초과 시 D안(prompt caching) 또는 비동기 invoke 패턴 필요                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | DRAFT            |
| 0.10.12 | 2026-05-23 | **feature_url_mapper 4단계 노드 결정론적 캐시 적용 — A-3 + B (cache_input 슬림화 + 4 노드 24h TTL 캐시)**. v0.10.11 race 분석 후 사용자 결정 "동일 도메인 입력에 대한 동작은 저장된 캐시 데이터를 사용하도록" 에 따라 단일 PR 로 결정론적 캐시 시스템 구축. **(0) agent_cache.py 보강**: `load_agent_output` 에 `ttl_hours: float \| None = None` 파라미터 추가. None(기본) 시 기존 동작 유지, 양수 시 entry.updated_at 기준 TTL 검사로 만료 시 미스 처리. datetime 기반 결정론적 만료. **(1) A-3 적용** (`feature_mapping_llm_node.py`): `_make_stable_cache_input(llm_input)` 헬퍼 신설. candidates 를 `(candidate_id, source_type, sorted(urls))` 만으로 축약하여 cache_key 안정화. page_title·meta_description·matched_report_types·URL 순서 변동 모두 흡수. LLM 실제 입력은 그대로 유지(매핑 품질 보존). **(2) B-1 url_discovery_brave 캐시** (`feature_url_mapper_node.py` `_brave_search`): agent_id="url_discovery_brave", cache_input={query, count}, 24h TTL. Brave API 응답을 `{results: [...]}` 형식으로 캐시. 빈 결과는 미저장(일시 실패 재시도 보존). **(3) B-2 page_meta_collect 캐시** (`_fetch_meta`): agent_id="page_meta_collect", cache_input={url}, 24h TTL. {page_title, meta_description} 캐시. 빈 결과도 저장(반복 fetch 방지). **(4) B-3 additional_urls_validation 캐시** (`_check_url_status`): agent_id="url_validation", cache_input={url}, 24h TTL. {status} 캐시. None 도 저장(죽은 링크 재시도 방지). **(5) `_NODE_CACHE_TTL_HOURS=24` 공통 상수** 도입. **검증**: 6개 파일 ast.parse 통과, load/store 호출 각 3회(예상 일치), agent_cache TTL 옵션·_make_stable_cache_input 동작 단위 시뮬레이션 통과(동일 URL set + 다른 title/description → 동일 cache_key, URL 순서 변동 → 동일 cache_key). **결정론적 캐시 적중 보장**: 동일 도메인 + 동일 selected_competitor_ids 재실행 시 4개 노드 모두 캐시 hit → LLM 호출 0회 + Brave API 호출 0회 + HTTP GET 0회 → 수 초 내 완료. **잔존 작업**: D안(prompt caching) 은 캐시 미스 첫 실행 시 latency 단축용으로 별도 PR 가능 | DRAFT            |
| 0.10.13 | 2026-05-24 | **A-4 cache_input 거시 축약 — URL list 제외, candidate_ids 만 사용 (결정론적 캐시 hit 보장)**. v0.10.12 적용 후 실측에서 캐시 미스 재발. 진단 결과 stable cache_input 의 candidates.urls 가 candidate 당 평균 23개·최대 64개로 매우 커서 단 1개 URL 변동(Brave 결과 순위 변동, 신규 URL 추가) 만으로도 cache_key sha256 이 달라짐을 확인. **외과적 변경 (`feature_mapping_llm_node.py` 의 `_make_stable_cache_input` 1개 함수, 약 30줄 재작성)**: candidates 의 URL list 자체를 cache_input 에서 제외. 새 cache_input = `{domain, own_product, active_reports, candidate_ids: sorted([...])}` 만 사용. LLM 실제 입력(llm_input) 은 candidates_with_meta 전체 유지 → 매핑 품질 영향 없음. **마이그레이션 재실행** (`scripts/migrate_cache_v0_10_12.py`): 옛 v0.10 엔트리들을 새 A-4 cache_key `99853be2...` 로 재저장. **검증**: URL 완전 변동 시뮬레이션(URL 수·순서·도메인 모두 다름) 에서도 candidate_ids 만 같으면 cache_key 동일 확인. 마이그레이션된 key 와 시뮬레이션 cache_key 일치 확인. **결정론적 보장**: 동일 domain + own_product + active_reports keys + selected_competitor_ids 조합이면 cache_key 결정론적 동일 → feature_mapping_llm 항상 캐시 hit → ~10ms 내 완료. **trade-off**: 매우 거시적 키이지만 사용자 우선순위(캐시 hit 가 매우 중요) 부합. 동일 candidate set 이면 URL 도 결국 같은 brand 페이지들이므로 LLM 결과 의미상 동일. **잔존**: url_discovery_brave / page_meta_collect / additional_urls_validation 의 24h TTL 캐시는 v0.10.12 그대로 유지(URL 단위라 정상 동작)                                                                                                                                                                                                                                                                                                                                                                                                                                                         | DRAFT            |
| 0.10.14 | 2026-05-24 | **FeatureSelectionPage.jsx v0.10 키 정합 dead-link 수정**. v0.10.13 적용 후 feature_selection 단계에 진입했으나 UI 가 "분석 항목이 없습니다 (0/0개)" 로 표시되는 회귀 발견. 진단 결과 server 측은 v0.10 에서 interrupt value 를 `{reports: [{report_type, report_label, features: [...]}]}` 로 변경했으나 client 측 `FeatureSelectionPage.jsx` 는 옛 `{purposes: [{purpose_id, purpose_label, features: [...]}]}` 키를 그대로 참조 중 — 즉 v0.10 마이그레이션의 client 측 dead-link. (캐시 hit 자체는 정상 동작 — hit_count=1 확인). **외과적 변경 (client/src/components/FeatureSelectionPage.jsx 1개 파일)**: (i) 함수명 `PurposeSection → ReportSection`, props `purpose → report`. (ii) 필드 `purpose.purpose_id → report.report_type`, `purpose.purpose_label → report.report_label`, `purpose.features → report.features`. (iii) `iv.purposes → iv.reports`, `purposes.flatMap → reports.flatMap`. (iv) 함수 `deriveSelectedPurposes → deriveSelectedReportTypes` (server 측 호환 키 `selected_purposes` 자체는 state.py 코멘트에 따라 유지 — server `state.selected_purposes` 가 호환 키로 명시되어 있으므로 client→server payload 의 `selected_purposes` 키는 보존, 값으로 report_type 목록 전달). (v) docstring v0.10 정합 갱신. **검증**: 잔존 purpose 참조 1건은 docstring 마이그레이션 설명 텍스트(의도된 보존), 그 외 17곳 reports/report_type/report_label/ReportSection/deriveSelectedReportTypes 신규 사용 확인. selected_purposes 호환 키 4곳 유지(state.py 호환). **기대 동작**: 다음 분석 진입 시 feature_selection 단계에서 7종 리포트 × 55 features 가 정상 표시됨. 캐시 hit + UI 정합 모두 정상 → wall-clock 약 2–6초 + UI 정상 렌더                                                                                                                                                                                                                                            | DRAFT            |
| 0.10.15 | 2026-05-24 | **본문 9개 섹션 v0.10.7 ~ v0.10.14 정합 갱신 + §7 UI 의도 명확화 (dead-link 일괄 해소)**. v0.10.7 ~ v0.10.14 변경이 §13 변경 이력에만 누적되어 본문(§3-1·§6-5·§7 등) 과 dead-link 가 누적되어 있던 문제 해소. **본문 갱신 8건**: (i) **§3-1 다이어그램** v0.10.7 ab_join + v0.10.9 4단계 직렬 노드 반영. (ii) **§3-2 핵심 변경 의도** v0.10.7/v0.10.9/v0.10.11 항목 추가. (iii) **§6-5 본문 전면 재작성** — 단일 노드 시점 설계에서 4개 노드 그룹(timeout·캐시·A-4 cache_input·마이그레이션) 으로 확장. 4단계 노드 매트릭스 표·state 브릿지 키 3종·v0.10 → v0.10.13 진화 의도 6단계·LLM 호출 흐름·실측 결과 추가. (iv) **§6-7** feature_selection 의 이전 노드를 additional_urls_validation_node 로 갱신. (v) **§8-2** v0.10.12/v0.10.13 4개 노드 캐시 정책 표 + A-4 cache_input 설계 원칙 + 마이그레이션 스크립트 패턴 추가. (vi) **§9-1** v0.10.9 브릿지 state 키 3종 매트릭스 추가(직렬 안전). (vii) **§11-3** 캐시 hit 률 향상 실측 표 + A-4 trade-off + timeout 분리 비용 영향 추가. (viii) **§11-5** v0.10.4·v0.10.9·v0.10.14·v0.10.16(예정) 프런트엔드 변경 이력 추가. (ix) **§12 체크리스트** v0.10.7 ~ v0.10.15 항목 8건 추가 + §7 v0.10.16 예정 항목 1건 추가. **§7 UI 의도 명확화**: 사용자 결정으로 "중복 표기 허용 + 체크박스 최초 등장 시만 활성" 명확화. §7-1 사용자 확인 목적 2가지(URL 수집·feature 식별), §7-2 그룹핑 규칙, §7-3 구현 영향 분석, §7-4 구현 가능성 평가, §7-5 권장 적용 순서 신설. server `report_types: list[str]` 다중 매핑 + client `seenFeatureIds` Set + disabled 분기. **별도 PR(v0.10.16) 로 분리** — 본 PR(v0.10.15) 는 본문 의도 명확화만 진행                                                                                                                                                                                                                                                                                                                                                                                                                          | DRAFT            |
| 0.10.16 | 2026-05-24 | **§7-1a Coverage 상세 표시 — FeatureCard 확장 토글 + candidate × URL 가시화**. v0.10.15 적용 후 사용자 확인 — 각 feature 의 `coverage_summary` 가 카운트(`충분 N`/`부분 N`/`미확보 N`) 만 표시되어 어떤 URL 이 어떤 coverage 상태인지 식별 불가. **외과적 변경 (2개 파일)**: (i) **server `feature_selection_node.py`**: `feature_items[*]` 에 `coverage_details: list[{candidate_id, coverage, existing_urls, additional_urls}]` 신설. 각 URL 항목에서 UI 노출 최소 필드만 추출(`existing_urls`: url + relevance_note + origin; `additional_urls`: url + rationale + validated + http_status). 기존 `coverage_summary` 호환 유지. (ii) **client `FeatureSelectionPage.jsx`**: `CoverageDetails` 컴포넌트 신설(candidate 별 sub-block 렌더 — 헤더 + 기존 URL 목록 + 추가 URL 목록). `FeatureCard` 에 `expanded` state + "▼ URL 상세 보기" 토글 버튼. 토글 클릭 시 `stopPropagation` 으로 체크박스 토글과 충돌 차단. 외부 링크 `target="_blank" rel="noopener noreferrer"`. **검증**: server ast.parse 통과, client 6개 추가 항목(CoverageDetails 컴포넌트 정의·COVERAGE_META 상수·FeatureCard 확장 토글·coverage_details 사용·CoverageDetails 호출·Fragment 부재) 모두 확인. **§7-1a 신설** — coverage 상세 표시 의도 본문 명시. **잔존**: 별도 v0.10.17 로 "동일 feature 복수 카드 중복 표기 + 체크박스 최초 등장 시만 활성" 처방 분리(v0.10.15 §7-5 의 미구현 부분)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | DRAFT            |
| 0.10.17 | 2026-05-24 | **이슈 1·2 진단 보고 + §6-5 한계 명시 + 캐시 직접 수정**. 사용자 분석 결과에서 두 가지 회귀 발견. **이슈 1 (candidate_id 라벨링 오류)**: competitor_discovery 는 트래블월렛을 `comp_트래블월렛` 으로 정상 발견했으나 feature_url_mapper 의 모든 v0.10 캐시 엔트리(5건) 가 `comp_토스트래블카드` 로 잘못 라벨링되어 자사 `own_토스트래블카드` 와 cid 충돌. 추정 원인 — `normalize_competitor_ids` 단계의 LLM 정규화에서 "트래블월렛 카드" → 자사명 "토스 트래블카드" slug 와 충돌하는 cid 생성. **해결**: `scripts/fix_cache_v0_10_17_candidate_id.py` 작성·실행 — feature_url_mapper.json 의 5건 엔트리에서 travel-wallet.com 도메인 보유 candidate(237건 coverage) 를 `comp_트래블월렛` 으로 정정 + 신규 cache_key `d4a2cba9...` 로 통합. 옛 엔트리 보존(회귀 위험 0). **이슈 2 (reaction_insight URL 출처 오류)**: 외부 후기·블로그·YouTube 가 본질 출처여야 하는 reaction_insight 리포트의 comp candidate 들이 모두 공식 sub-page 추정 (✗ 404/403 발생). 진단 결과 두 가지 원인 — (a) domain_taxonomy 의 `reaction_insight.search_query_hints` 가 자사 위주(6개 중 own_only 4 + comp_token 1 + both 1) — comp 단독 후기 검색 부족. 결과: Brave 검색에서 트래블월렛 후기 결과 0건. (b) feature_mapping_llm 의 system_prompt 가 외부 출처(블로그/커뮤니티) 도메인을 existing_urls 로 신뢰하지 않고 공식 sub-page 만 additional_urls 로 추정. 본 한계는 §6-6a 의 `youtube_collection`·`community_collection`·`blog_rss_collection` 노드가 구현되어야 결정론적 해소. **§6-5 본문 갱신**: "알려진 한계 (v0.10.17 명시)" 절 신설 — (1) SPA·동적 페이지의 100% partial/not_found 판정, (2) additional_urls sub-page 추정 실패, (3) reaction_insight 외부 출처 매핑 실패. 5가지 해소 처방 후보 정리(우선순위 1=§6-6a 수집 노드, 2=헤딩/본문 수집 확장, 3=Playwright headless, 4=search_query_hints 가이드 강화, 5=feature_mapping_llm system_prompt 보강). **본 PR 코드 변경 없음** — 한계 명시 + 캐시 직접 수정만. 향후 처방 1·4·5 는 별도 PR                                                                                                                                           | DRAFT            |
