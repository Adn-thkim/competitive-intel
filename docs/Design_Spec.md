# 설계 문서 — Local 분석 버전

> - **적용 환경**: Mac 로컬 전용
> - **현재 단계**: 도메인 기반 경쟁 분석 + 공식 홈페이지 비교 + YouTube 사용자 반응 분석 설계
> - **현재 저장 방식**: JSON 파일 캐시 (data/cache/, data/taxonomy/)
> - **향후 저장 방식**: SQLite 연동 예정
> - **문서 버전**: v2.0 | 작성일: 2026-05-05 (v1.1에서 실제 구현 반영 업데이트)

## 1. 문서 목적

이 문서는 실제 구현 현황을 반영해 설계를 재정비한 문서다.

현재 설계의 중심은 다음과 같다.

- 사용자가 짧은 검색어를 입력하면 QueryIntakeAgent가 분석 입력 초안을 자동 생성한다.
- Human-in-the-loop(interrupt) 패턴을 활용해 사용자가 핵심 단계에서 검토·조정할 수 있다.
- DomainTaxonomyAgent가 도메인 분석 목적(purpose)과 비교 feature를 자동 생성·캐시한다.
- CompetitorDiscoveryAgent가 도메인 기준 경쟁사 및 기능적 대안 수단을 식별한다.
- OfficialSourceResolverAgent가 공식 홈페이지 URL을 탐색·검증하고, 실패 시 url_retry_node가 사용자 개입 또는 Brave Search 기반 재탐색을 처리한다.
- FeatureUrlMapperAgent가 도메인 taxonomy 기반으로 feature × candidate URL 커버리지를 매핑한다.
- 이후 FeatureExtractionAgent, FeatureComparisonAgent, YouTube 수집·분석 agent를 LangGraph로 연결해 인사이트 리포트를 생성한다.
- 최종적으로 사용자가 검색어를 입력하면 인사이트 리포트를 생성하는 웹 프롬프트를 개발한다.

현재 파일럿 도메인은 `토스 트래블카드`다.

---

## 2. 프로젝트 목표

### 2-1. 전체 프로젝트 목표

1. 최초 분석 도메인을 설정하고, 해당 도메인을 기준으로 분석 범위를 정의한다.
2. 설정한 도메인에 대해 경쟁사 및 경쟁 상품을 식별하는 agent를 개발한다.
3. 자사 상품과 경쟁 상품의 공식 홈페이지를 수집하고, 상품 설명 및 기능을 비교하는 agent를 개발한다.
4. 동일한 자사/경쟁 상품군을 기준으로 YouTube 영상과 댓글을 수집하고 사용자 반응을 분석한다.
5. 개별 agent를 LangGraph multi-agent orchestration으로 연결한다.
6. 최종적으로 사용자가 검색어나 도메인을 입력하면 인사이트 리포트를 생성하는 웹 프롬프트를 개발한다.

### 2-2. 현재 파일럿 도메인

- 파일럿 도메인: `토스 트래블카드`
- 자사 상품 기준점: `토스 트래블카드`
- 1차 공식 비교 대상: 해외 결제, 환전, 여행 특화 혜택 관점에서 경쟁하는 카드/핀테크/환전 상품
- 1차 공식 데이터 소스: 각 상품의 공식 홈페이지, 공식 상품 소개 페이지, 공식 FAQ
- 2차 반응 데이터 소스: YouTube 검색 결과 상위 영상과 댓글

### 2-3. 현재 단계의 직접 구현 목표

1. 검색어 입력 → QueryIntakeAgent → Human-in-the-loop 검토 흐름을 구현한다.
2. DomainTaxonomyAgent로 도메인 분석 목적·feature·URL 유형 taxonomy를 자동 생성·캐시한다.
3. 경쟁사/경쟁 상품 및 기능적 대안 수단 식별 agent를 구현한다.
4. 공식 홈페이지 URL 식별 및 검증 agent를 구현한다.
5. URL 검증 실패 시 Two-Phase interrupt로 사용자 개입 또는 Brave Search 재탐색을 처리한다.
6. taxonomy 기반 feature × candidate URL 커버리지 매핑 agent를 구현한다.
7. 상품 설명 및 기능 추출 agent를 구현한다. (미구현)
8. 추출 결과를 표준화해 비교하는 agent를 구현한다. (미구현)
9. 비교 결과를 바탕으로 YouTube 검색어를 생성하거나 검토하는 흐름을 설계한다. (미구현)
10. YouTube 영상 및 댓글 수집 흐름을 구현한다. (미구현)
11. 영상별 사용자 반응 분석과 검색어별 종합 인사이트 생성을 구현한다. (미구현)
12. 위 결과를 orchestration layer에서 연결해 최종 리포트로 이어지게 한다. (미구현)

### 2-4. 장기 목표

- YouTube 외에 커뮤니티, 앱스토어 후기, 리뷰 사이트 등 외부 반응 데이터로 비교 범위를 확장한다.
- 상품 기능 비교와 사용자 반응 비교를 결합한 통합 인사이트 리포트를 생성한다.
- 도메인별 프롬프트와 feature schema를 재사용 가능한 템플릿으로 일반화한다.

---

## 3. 범위 정의

### 3-1. 현재 구현 범위 (완료)

- 검색어 → 구조화 입력 자동 변환 (QueryIntakeAgent)
- 분석 입력 초안 사용자 검토·수정 (Human-in-the-loop #1)
- 도메인 분석 목적·feature·URL 유형 taxonomy 자동 생성·캐시 (DomainTaxonomyAgent)
- 도메인 기준 경쟁사/경쟁 상품 식별, 기능적 대안 수단 식별 (CompetitorDiscoveryAgent)
- 경쟁사 ID comp_* 슬러그 정규화 (NormalizeCompetitorIds)
- 분석 대상 경쟁사 사용자 선택 (Human-in-the-loop #2)
- 공식 홈페이지 URL 탐색 및 HTTP 검증 (OfficialSourceResolverAgent)
- URL 검증 실패 Two-Phase 재시도 (url_retry_node, Human-in-the-loop #3)
- taxonomy 기반 feature × candidate URL 커버리지 매핑 (FeatureUrlMapperAgent)
- 분석 항목(purpose/feature) 사용자 선택 (Human-in-the-loop #4)
- 에이전트 출력 JSON 캐시 (data/cache/agent_outputs/)
- 도메인 taxonomy 파일 캐시 (data/taxonomy/)

### 3-2. 현재 단계에서 미구현 범위 (TODO)

아래 항목은 graph.py에 주석으로 예약되어 있으며 구현 후 엣지를 연결한다.

- FeatureExtractionAgent (공식 페이지에서 설명/기능/조건 추출)
- FeatureComparisonAgent (공통 schema 기반 기능 비교 + feature matrix 생성)
- YouTubeQueryPlannerAgent (공식 비교 결과 기반 검색어 설계)
- YouTubeCollectionAgent (영상/댓글 수집 및 선별)
- ReactionAnalysisAgent (영상별 반응 분석 + 검색어별 종합 인사이트)
- InsightReportAgent (공식 비교 + YouTube 반응 통합 리포트 생성)

### 3-3. 현재 단계에서 제외하는 범위

- SQLite 기반 영구 DB 완성
- Excel/PDF 정식 리포트 출력
- 쇼핑몰 후기, 커뮤니티, SNS 등 비공식 데이터 소스 수집
- 완전 자동 브라우저 크롤링의 고도화

---

## 4. 현재 단계 아키텍처

```text
┌────────────────────────────────────────────────────────────────────┐
│ Mac 로컬 환경                                                       │
│                                                                    │
│ Web Prompt UI (React + Vite)                                        │
│    ↕ HTTP REST (localhost:4000)                                     │
│ Express.js 로컬 서버                                                │
│    ├── LangGraph Pipeline (Python / uvicorn :8000)                 │
│    │    ├── QueryIntakeAgent                                        │
│    │    ├── [interrupt #1] Human Review                            │
│    │    ├── CompetitorDiscoveryAgent                               │
│    │    ├── DomainTaxonomyAgent                                    │
│    │    ├── NormalizeCompetitorIds                                  │
│    │    ├── [interrupt #2] CompetitorSelection                     │
│    │    ├── OfficialSourceResolverAgent                            │
│    │    ├── [interrupt #3] UrlRetry (Two-Phase)                    │
│    │    ├── FeatureUrlMapperAgent                                  │
│    │    ├── [interrupt #4] FeatureSelection                        │
│    │    ├── [TODO] FeatureExtractionAgent                          │
│    │    ├── [TODO] FeatureComparisonAgent                          │
│    │    ├── [TODO] YouTubeQueryPlannerAgent                        │
│    │    ├── [TODO] YouTubeCollectionAgent                          │
│    │    ├── [TODO] ReactionAnalysisAgent                           │
│    │    └── [TODO] InsightReportAgent                              │
│    ├── External Access Layer                                       │
│    │    ├── Official website fetch (HTTP)                          │
│    │    ├── Brave Search API (URL 재탐색)                          │
│    │    └── YouTube Data API                                       │
│    ├── LLM 호출 계층                                                │
│    │    ├── 기본안: Claude Code CLI (ClaudeCodeCliAnalyzer)        │
│    │    └── 결정론적 처리: Claude API (ProductIdResolver 등)       │
│    └── JSON 파일 저장소                                             │
│         ├── data/cache/agent_outputs/                              │
│         └── data/taxonomy/                                         │
└────────────────────────────────────────────────────────────────────┘
```

### 4-1. 설계 원칙

- Human-in-the-loop interrupt를 핵심 설계 패턴으로 채택한다. 사용자가 4개 체크포인트에서 검토·조정할 수 있어야 한다.
- agent별 입력과 출력을 명확히 분리해 디버깅 가능성을 높인다.
- 공식 홈페이지 기반 비교와 YouTube 반응 분석을 별도 레이어로 유지한다.
- 각 단계 결과를 구조화 JSON으로 캐시해 재실행과 검증이 가능하도록 한다.
- LLM 기본 호출 경로는 Claude Code CLI로 두고, temperature=0 결정론적 처리가 필요한 경우에만 Claude API를 직접 사용한다.
- own_* URL이 검증되지 않으면 critical_error로 파이프라인을 즉시 종료한다. 신뢰할 수 없는 분석 결과는 분석을 하지 않는 것보다 치명적이다.

### 4-2. 구현된 agent 및 node 구성

| Node | Agent | 역할 | 주요 입력 | 주요 출력 |
|------|-------|------|---------|---------|
| `query_intake_node` | `QueryIntakeAgent` | raw_query → 구조화 입력 초안 자동 생성 | raw_query | query_intake_output |
| `human_review_node` | interrupt #1 | 사용자가 분석 입력 초안 검토·수정 | query_intake_output | project_id, domain_name, own_product, ... |
| `competitor_discovery_node` | `CompetitorDiscoveryAgent` | 도메인 기준 브랜드 경쟁사 + 기능적 대안 수단 식별 | domain_name, own_product, ... | competitor_candidates, functional_competitors, competition_axes |
| `domain_modeling_node` | `DomainTaxonomyAgent` | 도메인 분석 목적·feature·URL 유형 taxonomy 자동 생성·캐시 | domain_name, competition_axes, own_product | domain_taxonomy |
| `normalize_competitor_ids_node` | `NormalizeCompetitorIds` | LLM 임시 ID → comp_* 슬러그 확정, product_name 정규화 | competitor_candidates | competitor_candidates (ID 확정) |
| `competitor_selection_node` | interrupt #2 | 사용자가 분석 대상 경쟁사 선택 (1~10개) | competitor_candidates, functional_competitors | selected_competitor_ids |
| `official_source_resolver_node` | `OfficialSourceResolverAgent` | own_*/comp_* 공식 URL 탐색·검증, func_* 레퍼런스 URL 탐색·검증 | own_product, competitor_candidates (selected) | official_sources |
| `url_retry_node` | interrupt #3 (Two-Phase) | URL 검증 실패 항목 재시도: 수동 URL 입력 or Brave Search 재탐색, critical_error 판단 | official_sources | official_sources (갱신), critical_error |
| `feature_url_mapper_node` | `FeatureUrlMapperAgent` | taxonomy 기반 feature × candidate URL 커버리지 매핑 + additional_urls 검증 | domain_taxonomy, official_sources | analysis_features |
| `feature_selection_node` | interrupt #4 | 사용자가 분석할 purpose 단위 + 개별 feature 선택 | analysis_features | selected_purposes, selected_feature_ids |

#### 4-2-1. LangGraph state schema

```python
from __future__ import annotations

import operator
from typing import Annotated, Any
from typing_extensions import TypedDict


class CompetitorCandidate(TypedDict, total=False):
    candidate_id:     str    # comp_* 슬러그 (normalize_competitor_ids_node가 확정)
    brand:            str
    product_name:     str    # 정규화된 공식 명칭
    competition_type: str    # "direct" | "indirect" | "substitute"
    category:         str
    why_competitor:   list[str]
    evidence_summary: str
    confidence:       float
    needs_validation: bool


class FunctionalCompetitor(TypedDict, total=False):
    """브랜드 상품이 아닌 전통적·기능적 대안 수단. candidate_id는 func_* 접두사."""
    candidate_id:    str    # func_* 접두사. 예: func_local_atm
    method_name:     str    # 대안 수단 명칭. 예: "현지 ATM 현금 인출"
    provider_type:   str    # 제공 주체 유형. 예: "시중은행"
    category:        str
    why_alternative: list[str]
    confidence:      float


class AnalysisFeature(TypedDict, total=False):
    """feature_url_mapper_node가 도출한 단일 비교 분석 항목. feature_id는 feat_* 접두사."""
    feature_id:         str
    feature_name:       str
    description:        str
    priority:           str    # "high" | "medium" | "low"
    candidate_coverage: list[dict[str, Any]]


class AgentStep(TypedDict, total=False):
    step_name:     str    # "CompetitorDiscoveryAgent" 등
    status:        str    # "pending" | "completed" | "failed" | "skipped"
    started_at:    str    # ISO 8601
    finished_at:   str
    error_message: str


class DomainAnalysisState(TypedDict, total=False):

    # ── 파이프라인 메타 ──────────────────────────────────────────────────────
    raw_query:  str    # 사용자 원문 검색어
    run_id:     str    # 실행 식별자 (= LangGraph thread_id)
    request_id: str    # 요청 추적 ID

    # ── query_intake_node 출력 ───────────────────────────────────────────────
    query_intake_output: dict[str, Any]

    # ── human_review_node 이후 확정되는 분석 컨텍스트 ───────────────────────
    project_id:           str
    domain_name:          str
    own_product:          dict[str, Any]    # product_id(own_*) 포함
    problem_statement:    str
    target_user:          list[str]
    core_value_props:     list[str]
    geography:            str
    known_keywords:       list[str]
    usage_context:        list[str]
    business_constraints: list[str]

    # ── competitor_discovery_node 출력 ───────────────────────────────────────
    own_product_summary:    dict[str, Any]
    competition_axes:       list[str]
    competitor_candidates:  list[CompetitorCandidate]
    functional_competitors: list[FunctionalCompetitor]
    excluded_or_deferred:   list[dict[str, Any]]

    # ── domain_modeling_node 출력 ────────────────────────────────────────────
    domain_taxonomy: dict[str, Any]
    # domain_type, active_purposes, purpose_config(features + url_types) 포함
    # data/taxonomy/{domain_id}_slug.json 에 7일 TTL 캐시

    # ── competitor_selection_node 출력 ───────────────────────────────────────
    selected_competitor_ids: list[str]
    # comp_* 및 func_* 혼합 가능. 최소 1개, 최대 10개

    # ── official_source_resolver_node 출력 ──────────────────────────────────
    domain_discovery_results: list[dict[str, Any]]
    page_validation_results:  list[dict[str, Any]]
    official_sources:         list[dict[str, Any]]
    source_validation:        list[dict[str, Any]]

    # ── feature_url_mapper_node 출력 ────────────────────────────────────────
    analysis_features: list[AnalysisFeature]

    # ── feature_selection_node 출력 ─────────────────────────────────────────
    selected_purposes:    list[str]    # 사용자가 선택한 purpose_id 목록
    selected_feature_ids: list[str]   # 사용자가 선택한 feat_* 목록

    # ── feature_extraction / feature_comparison 출력 (미구현) ────────────────
    product_profiles:    list[dict[str, Any]]
    normalized_features: list[dict[str, Any]]
    feature_matrix:      dict[str, Any]

    # ── youtube 수집·분석 출력 (미구현) ──────────────────────────────────────
    query_plan:        dict[str, Any]
    search_results:    list[dict[str, Any]]
    collected_videos:  list[dict[str, Any]]
    selected_comments: list[dict[str, Any]]
    query_insights:    list[dict[str, Any]]

    # ── insight_report 출력 (미구현) ─────────────────────────────────────────
    report_brief: dict[str, Any]
    final_report: dict[str, Any]

    # ── 파이프라인 중단 플래그 ────────────────────────────────────────────────
    critical_error: str
    # 설정되면 url_retry 이후 conditional edge가 END로 분기
    # 발생 조건: own_* URL이 Phase 2 종료 후에도 validated=False인 경우

    # ── 공통 운영 필드 (누적 reducer) ─────────────────────────────────────────
    agent_steps: Annotated[list[AgentStep], operator.add]
    errors:      Annotated[list[dict[str, str]], operator.add]
```

#### 4-2-2. node별 reads / writes

| Node | Reads | Writes |
|------|-------|--------|
| `query_intake_node` | `raw_query`, `run_id`, `request_id` | `query_intake_output`, `agent_steps` |
| `human_review_node` | `query_intake_output` | `project_id`, `domain_name`, `own_product`, `problem_statement`, `target_user`, `core_value_props`, `geography`, `known_keywords`, `usage_context`, `business_constraints` |
| `competitor_discovery_node` | `domain_name`, `own_product`, `problem_statement`, `target_user`, `core_value_props`, `geography` | `own_product_summary`, `competition_axes`, `competitor_candidates`, `functional_competitors`, `excluded_or_deferred`, `agent_steps` |
| `domain_modeling_node` | `domain_name`, `own_product`, `problem_statement`, `target_user`, `core_value_props`, `competition_axes` | `domain_taxonomy`, `agent_steps` |
| `normalize_competitor_ids_node` | `competitor_candidates` | `competitor_candidates` (ID 확정), `agent_steps` |
| `competitor_selection_node` | `competitor_candidates`, `functional_competitors` | `selected_competitor_ids`, `agent_steps` |
| `official_source_resolver_node` | `own_product`, `competitor_candidates`, `selected_competitor_ids` | `official_sources`, `agent_steps` |
| `url_retry_node` | `official_sources` | `official_sources` (갱신), `critical_error`, `agent_steps` |
| `feature_url_mapper_node` | `domain_taxonomy`, `official_sources`, `competitor_candidates`, `own_product` | `analysis_features`, `agent_steps` |
| `feature_selection_node` | `analysis_features`, `domain_taxonomy` | `selected_purposes`, `selected_feature_ids`, `agent_steps` |

#### 4-2-3. 실제 graph 연결

```python
from langgraph.graph import START, END, StateGraph
from langgraph.checkpoint.memory import MemorySaver

builder = StateGraph(DomainAnalysisState)

builder.add_node("query_intake",             query_intake_node)
builder.add_node("human_review",             human_review_node)
builder.add_node("competitor_discovery",     competitor_discovery_node)
builder.add_node("domain_modeling",          domain_modeling_node)
builder.add_node("normalize_competitor_ids", normalize_competitor_ids_node)
builder.add_node("competitor_selection",     competitor_selection_node)
builder.add_node("official_source_resolver", official_source_resolver_node)
builder.add_node("url_retry",                url_retry_node)
builder.add_node("feature_url_mapper",       feature_url_mapper_node)
builder.add_node("feature_selection",        feature_selection_node)
# TODO: 아래 노드 구현 후 주석 해제
# builder.add_node("feature_extraction",    feature_extraction_node)
# builder.add_node("feature_comparison",    feature_comparison_node)
# builder.add_node("youtube_query_planner", youtube_query_planner_node)
# builder.add_node("youtube_collection",    youtube_collection_node)
# builder.add_node("reaction_analysis",     reaction_analysis_node)
# builder.add_node("insight_report",        insight_report_node)

builder.add_edge(START,                        "query_intake")
builder.add_edge("query_intake",               "human_review")
builder.add_edge("human_review",               "competitor_discovery")
builder.add_edge("competitor_discovery",       "domain_modeling")
builder.add_edge("domain_modeling",            "normalize_competitor_ids")
builder.add_edge("normalize_competitor_ids",   "competitor_selection")
builder.add_edge("competitor_selection",       "official_source_resolver")
builder.add_edge("official_source_resolver",   "url_retry")

builder.add_conditional_edges(
    "url_retry",
    lambda s: "end" if s.get("critical_error") else "feature_url_mapper",
    {"end": END, "feature_url_mapper": "feature_url_mapper"},
)

builder.add_edge("feature_url_mapper", "feature_selection")
builder.add_edge("feature_selection",  END)    # 임시: feature_extraction 구현 후 교체
# TODO:
# builder.add_edge("feature_selection",     "feature_extraction")
# builder.add_edge("feature_extraction",    "feature_comparison")
# builder.add_edge("feature_comparison",    "youtube_query_planner")
# builder.add_edge("youtube_query_planner", "youtube_collection")
# builder.add_edge("youtube_collection",    "reaction_analysis")
# builder.add_edge("reaction_analysis",     "insight_report")
# builder.add_edge("insight_report",        END)

compiled_graph = builder.compile(checkpointer=MemorySaver())
```

#### 4-2-4. Human-in-the-loop 설계

파이프라인에는 4개의 interrupt() 체크포인트가 있다. 각 interrupt에서 Express.js가 LangGraph thread를 일시 중단하고, 프런트엔드가 사용자 입력을 받아 재개(resume)한다.

| # | 노드 | interrupt type | 사용자 액션 | resume 값 |
|---|------|----------------|-------------|-----------|
| 1 | `human_review_node` | `"human_review"` | LLM 초안 수정·승인 | 수정된 분석 입력 전체 |
| 2 | `competitor_selection_node` | `"competitor_selection"` | 경쟁사 목록에서 1~10개 선택 | `{"selected_ids": [...]}` |
| 3 | `url_retry_node` | `"url_retry"` | URL 실패 항목 처리 | `{"manual_urls": {...}, "remove_ids": [...], "remove_ref_urls": {...}}` |
| 4 | `feature_selection_node` | `"feature_selection"` | purpose·feature 선택 | `{"selected_purposes": [...], "selected_feature_ids": [...]}` |

#### 4-2-5. url_retry_node Two-Phase 설계

URL 검증 실패 항목이 있을 때 단순 재시도가 아니라 두 단계 interrupt로 처리한다.

**Phase 1 (`is_final=False`)**: 실패 항목을 사용자에게 제시한다.
- 사용자가 `manual_url`을 직접 입력하면 해당 URL을 즉시 검증한다.
- `manual_url`을 비워두면 LLM + Brave Search API로 새 URL을 탐색한 뒤 검증한다.
- 재시도 후에도 실패 항목이 남으면 Phase 2로 진입한다.

**Phase 2 (`is_final=True`)**: 남은 실패 항목과 처리 옵션(`action_case`)을 함께 제시한다.

| action_case | 대상 | 처리 옵션 |
|-------------|------|-----------|
| `None` | `own_*` | 수동 URL 입력만 허용. 미입력 시 `critical_error` 설정 후 파이프라인 종료 |
| `"case1"` | `comp_*` 전체 실패 | 해당 경쟁사 전체 제거 |
| `"case2_1"` | `func_*` 부분 실패 | 특정 reference URL source만 제거 |
| `"case2_2"` | `func_*` 전체 실패 | func_* 항목 전체 제거 |

#### 4-2-6. 운영 규칙

- interrupt()를 노드 내부에서 직접 호출하므로 `interrupt_before` 설정은 사용하지 않는다.
- 체크포인터는 MemorySaver(인메모리)를 사용한다. 내구성이 필요하면 SqliteSaver로 교체한다.
- interrupt + MemorySaver 조합이 가능하려면 Python 프로세스가 장기 실행 상태여야 한다. uvicorn 서버가 이를 보장한다.
- 각 node는 자기 산출물만 state에 기록하고, 공통 운영 필드(`agent_steps`, `errors`)는 Annotated reducer로 자동 누적한다.
- `critical_error`가 설정된 상태에서는 url_retry 이후 conditional edge가 END로 분기한다.

---

## 5. 데이터 저장 전략

### 5-1. 현재 저장 구조

현재 단계는 두 가지 JSON 캐시 경로를 운영한다.

**에이전트 출력 캐시** (`data/cache/agent_outputs/`): 각 agent의 LLM 출력을 input hash 기반으로 캐시한다. 동일한 입력에 대해 재실행 시 LLM 호출 없이 캐시 결과를 반환한다. TTL은 `ANALYSIS_CACHE_TTL_HOURS`(기본 48시간) 환경변수로 제어한다.

**도메인 taxonomy 캐시** (`data/taxonomy/`): DomainTaxonomyAgent가 생성한 taxonomy를 도메인 ID 기반으로 캐시한다. `domains.json`이 domain_name → ID 레지스트리를 관리하며, taxonomy 파일은 `{id}_slug.json` 형식이다. TTL은 7일이며, competition_axes 미대응 비율이 30% 이상이면 자동 보강(enrich)이 트리거된다.

### 5-2. 향후 data/projects/ 구조 연동 계획

FeatureExtractionAgent 이후 단계를 구현할 때 아래 `data/projects/` 저장 구조를 연동한다.

```text
data/
├── cache/
│   ├── agent_outputs/           ← 에이전트 출력 캐시 (현재 사용)
│   └── product_name_normalization.json
├── taxonomy/
│   ├── domains.json             ← domain_name → ID 레지스트리
│   └── {id}_slug.json           ← 도메인별 taxonomy 캐시 (7일 TTL)
└── projects/                    ← FeatureExtraction 이후 단계에서 연동 예정
    └── {project_slug}/
        ├── project.json
        ├── domain/
        │   ├── domain.json
        │   ├── own_product.json
        │   └── competitor_candidates.json
        ├── sources/
        │   ├── official_sources.json
        │   └── source_validation.json
        ├── extracted/
        │   ├── product_profiles.json
        │   ├── normalized_features.json
        │   └── feature_matrix.json
        ├── youtube/
        │   ├── query_plan.json
        │   ├── search_results/
        │   └── videos/
        ├── runs/
        └── reports/
```

### 5-3. JSON 설계 원칙

- 모든 레코드는 `id`, `created_at`, `updated_at` 또는 실행 시각 필드를 가진다.
- 시간은 ISO 8601 문자열로 저장한다.
- agent 출력은 반드시 구조화 JSON으로 저장한다.
- 원본 추출 데이터와 정규화된 데이터는 별도 필드 또는 별도 파일로 분리한다.
- 공식 출처 URL과 판별 근거를 함께 저장한다.

### 5-4. data/projects/ JSON 스키마 참고 (백업)

> **출처**: `Design_Spec 2.md` v0.9-draft (2026-04-16) 섹션 7에서 이관.
> FeatureExtractionAgent(14단계) 이후 구현 시 참고용으로 보존한다.
> 일부 필드명·구조는 실제 구현 시 state.py의 TypedDict 정의에 맞게 조정이 필요하다.

#### `project.json`

```json
{
  "project_id": "proj_smartphone_2026q2",
  "name": "스마트폰 라인업 사용자 반응 조사",
  "own_brand": "자사브랜드명",
  "description": "YouTube 기반 자사/경쟁 상품 반응 조사",
  "scope": {
    "current_phase": "youtube_reaction_research",
    "future_phases": [
      "comparison_factor_definition",
      "multi_source_data_collection",
      "lineup_level_strategy_report"
    ]
  },
  "created_at": "2026-04-16T10:00:00+09:00",
  "updated_at": "2026-04-16T10:00:00+09:00"
}
```

#### `catalog/products.json`

```json
{
  "project_id": "proj_smartphone_2026q2",
  "updated_at": "2026-04-16T10:10:00+09:00",
  "products": [
    {
      "product_id": "own_galaxy_s25_ultra",
      "name": "갤럭시 S25 Ultra",
      "brand": "삼성전자",
      "line_name": "Galaxy S",
      "category": "프리미엄 스마트폰",
      "entity_type": "own",
      "status": "active",
      "competitor_candidates": ["comp_iphone_16_pro_max", "comp_pixel_9_pro"]
    },
    {
      "product_id": "comp_iphone_16_pro_max",
      "name": "아이폰 16 Pro Max",
      "brand": "Apple",
      "line_name": "iPhone Pro",
      "category": "프리미엄 스마트폰",
      "entity_type": "competitor",
      "status": "active"
    }
  ]
}
```

#### `catalog/query_plan.json`

```json
{
  "project_id": "proj_smartphone_2026q2",
  "generated_at": "2026-04-16T10:15:00+09:00",
  "queries": [
    {
      "query_id": "q_own_galaxy_s25_ultra_review",
      "product_id": "own_galaxy_s25_ultra",
      "query": "갤럭시 S25 Ultra 리뷰",
      "query_type": "review",
      "objective": "사용자 반응 조사",
      "top_video_limit": 9,
      "comment_collection": {
        "max_pages": 5,
        "min_valid_comments": 150,
        "max_selected_comments": 200,
        "min_text_length": 15
      }
    }
  ]
}
```

#### `runs/{run_id}/run.json`

```json
{
  "run_id": "run_2026-04-16_101500_kst",
  "project_id": "proj_smartphone_2026q2",
  "started_at": "2026-04-16T10:15:00+09:00",
  "finished_at": null,
  "status": "running",
  "storage_mode": "json",
  "llm_mode": {
    "provider": "claude_cli",
    "model": "claude-sonnet-4-6",
    "agent_mode": true
  }
}
```

#### `youtube/search_results/{query_slug}.json`

```json
{
  "run_id": "run_2026-04-16_101500_kst",
  "query_id": "q_own_galaxy_s25_ultra_review",
  "product_id": "own_galaxy_s25_ultra",
  "query": "갤럭시 S25 Ultra 리뷰",
  "executed_at": "2026-04-16T10:16:00+09:00",
  "result_count": 9,
  "videos": [
    {
      "rank": 1,
      "video_id": "abc123",
      "title": "갤럭시 S25 Ultra 실사용 후기",
      "channel": "테크채널",
      "view_count": 120340,
      "like_count": 4211,
      "comment_count": 683,
      "published_at": "2026-03-28T09:00:00Z",
      "thumbnail_url": "https://i.ytimg.com/..."
    }
  ]
}
```

#### `youtube/videos/{video_id}/video.json`

```json
{
  "run_id": "run_2026-04-16_101500_kst",
  "query_id": "q_own_galaxy_s25_ultra_review",
  "product_id": "own_galaxy_s25_ultra",
  "video_id": "abc123",
  "rank": 1,
  "title": "갤럭시 S25 Ultra 실사용 후기",
  "channel": "테크채널",
  "view_count": 120340,
  "like_count": 4211,
  "comment_count": 683,
  "published_at": "2026-03-28T09:00:00Z",
  "url": "https://www.youtube.com/watch?v=abc123",
  "created_at": "2026-04-16T10:16:10+09:00"
}
```

#### `youtube/videos/{video_id}/raw_comments.json`

```json
{
  "video_id": "abc123",
  "collected_at": "2026-04-16T10:17:00+09:00",
  "collection_config": {
    "max_pages": 5,
    "min_valid_comments": 150,
    "max_selected_comments": 200,
    "min_text_length": 15
  },
  "pages_fetched": 3,
  "raw_comment_count": 286,
  "comments": [
    {
      "comment_id": "yt_comment_001",
      "author": "user123",
      "text": "배터리는 아쉽지만 카메라는 진짜 좋네요.",
      "like_count": 18,
      "published_at": "2026-04-01T11:20:00Z",
      "reply_count": 0
    }
  ]
}
```

#### `youtube/videos/{video_id}/selected_comments.json`

```json
{
  "video_id": "abc123",
  "selection_version": "comment-quality-v1",
  "selected_at": "2026-04-16T10:17:30+09:00",
  "selected_comment_count": 173,
  "comments": [
    {
      "comment_id": "yt_comment_001",
      "text": "배터리는 아쉽지만 카메라는 진짜 좋네요.",
      "like_count": 18,
      "quality_score": 4.2,
      "selection_reason": [
        "sufficient_length",
        "contains_product_feedback",
        "high_like_count"
      ]
    }
  ]
}
```

#### `youtube/videos/{video_id}/analysis.json`

```json
{
  "video_id": "abc123",
  "analysis_version": "video-analysis-v1",
  "requested_at": "2026-04-16T10:18:00+09:00",
  "completed_at": "2026-04-16T10:18:12+09:00",
  "llm": {
    "provider": "claude_cli",
    "model": "claude-sonnet-4-6"
  },
  "result": {
    "sentiment": {
      "positive": 63,
      "negative": 37,
      "sum_check": 100
    },
    "video_summary_one_line": "고성능 카메라와 S펜 활용성을 강조한 실사용 후기 영상.",
    "comment_reaction_summary": "댓글에서는 카메라 성능과 화면 품질에 대한 만족도가 높게 나타났다. 반면 배터리 지속 시간과 발열에 대한 불만이 반복적으로 언급됐다. 전반적으로 고성능은 인정하지만 가격 대비 완성도에는 아쉬움이 있다는 반응이다.",
    "product_mentions": [
      {
        "aspect": "카메라",
        "polarity": "positive",
        "evidence_count": 21,
        "summary": "줌 성능과 야간 촬영 품질 호평"
      },
      {
        "aspect": "배터리",
        "polarity": "negative",
        "evidence_count": 17,
        "summary": "실사용 배터리 지속 시간이 기대 이하라는 의견 다수"
      }
    ],
    "strengths": ["카메라 줌", "디스플레이", "S펜"],
    "weaknesses": ["배터리", "발열", "가격"],
    "improvement_requests": ["배터리 용량 확대", "충전 속도 개선"]
  }
}
```

#### `youtube/query_insights/{query_slug}.json`

```json
{
  "run_id": "run_2026-04-16_101500_kst",
  "query_id": "q_own_galaxy_s25_ultra_review",
  "product_id": "own_galaxy_s25_ultra",
  "generated_at": "2026-04-16T10:40:00+09:00",
  "video_count": 9,
  "aggregated_sentiment": {
    "positive_avg": 66.4,
    "negative_avg": 33.6
  },
  "top_strengths": [
    { "item": "카메라", "count": 7 },
    { "item": "디스플레이", "count": 5 }
  ],
  "top_weaknesses": [
    { "item": "배터리", "count": 6 },
    { "item": "발열", "count": 4 }
  ],
  "top_improvement_requests": [
    { "item": "배터리 용량 확대", "count": 5 },
    { "item": "충전 속도 개선", "count": 3 }
  ],
  "cross_video_insight": "조회수 상위 9개 영상의 반응을 종합하면 제품의 카메라와 디스플레이 경쟁력은 높게 평가된다. 반면 배터리와 발열은 반복적으로 지적되는 약점이며, 가격에 대한 방어력은 충분하지 않다. 차기 모델에서는 배터리 효율과 체감 발열 개선이 우선 과제로 보인다."
}
```

---

## 6. 디렉토리 구조

```text
competitive-intel/
├── agents/
│   ├── query_intake/
│   ├── competitor_discovery/
│   ├── domain_modeling/
│   ├── official_source_resolver/
│   ├── feature_extraction/
│   └── feature_url_mapper/
├── client/
│   └── src/components/
│       ├── SearchPage.jsx
│       ├── ReviewForm.jsx              ← interrupt #1 UI
│       ├── CompetitorSelectionPage.jsx ← interrupt #2 UI
│       ├── UrlRetryPage.jsx            ← interrupt #3 UI
│       ├── FeatureSelectionPage.jsx    ← interrupt #4 UI
│       └── ResultView.jsx
├── server/
│   ├── index.js
│   ├── pythonServer.js
│   ├── graph/
│   │   ├── graph.py
│   │   ├── state.py
│   │   ├── api.py
│   │   ├── agent_cache.py
│   │   ├── progress_store.py
│   │   └── nodes/
│   │       ├── query_intake_node.py
│   │       ├── human_review_node.py
│   │       ├── competitor_discovery_node.py
│   │       ├── domain_modeling_node.py
│   │       ├── normalize_competitor_ids_node.py
│   │       ├── competitor_selection_node.py
│   │       ├── official_source_resolver_node.py
│   │       ├── url_retry_node.py
│   │       ├── feature_url_mapper_node.py
│   │       └── feature_selection_node.py
│   ├── llm/
│   │   └── claude_cli_analyzer.py
│   └── utils/
│       └── slug.py                     ← ProductIdResolver
├── data/
│   ├── cache/
│   │   ├── agent_outputs/
│   │   └── product_name_normalization.json
│   └── taxonomy/
│       ├── domains.json
│       └── {id}_slug.json
├── docs/
└── package.json
```

---

## 7. DomainTaxonomyAgent 상세 설계

### 7-1. 역할

CompetitorDiscoveryAgent 직후 실행되어, 도메인의 분석 목적(purpose)과 각 목적에 필요한 비교 feature·URL 유형(url_types)을 LLM이 추론한 taxonomy를 생성 또는 보강(enrich)한다.

### 7-2. 도메인 ID 레지스트리

```json
// data/taxonomy/domains.json
{ "1": "토스 트래블카드", "2": "B2B HR SaaS" }
```

동일 domain_name이 재입력되면 기존 ID를 재사용한다. 신규 domain_name은 순번 ID를 부여하고 레지스트리를 갱신한다.

### 7-3. 캐시 전략

1. `domains.json`에서 `domain_name` → ID 조회 (없으면 신규 등록)
2. `data/taxonomy/{id}_slug.json` 존재 여부 확인
3. 존재 + TTL(7일) 이내 + enrichment 불필요 → 캐시 로드, LLM 호출 생략
4. 존재 + (TTL 초과 또는 enrichment 트리거) → LLM에 기존 taxonomy 전달, add-only 보강
5. 존재하지 않음 → LLM이 taxonomy 최초 생성

### 7-4. enrichment 트리거

`competition_axes` 중 기존 taxonomy의 `active_purposes`에 대응되지 않는 항목 비율이 30% 이상이면 enrich 모드로 실행된다.

---

## 8. FeatureUrlMapperAgent 상세 설계

### 8-1. 역할

FeatureExtractionAgent 전 단계에서 "어떤 URL에서 어떤 feature를 추출할 수 있는가"를 매핑한다. 실제 크롤링 없이 URL 커버리지를 먼저 파악해 불필요한 크롤링을 방지한다.

### 8-2. 처리 흐름 (3단계)

**Step 1 — Page Meta 수집 (HTTP)**: `official_sources`에서 validated URL을 추출하고 ThreadPoolExecutor로 병렬 GET → `<title>` + `<meta name="description">` 수집.

**Step 2 — LLM 호출**: 도메인 컨텍스트 + `domain_taxonomy` + URL 메타데이터를 조립해 1회 호출. LLM은 taxonomy feature 목록을 수신해 feature × candidate URL 커버리지 매핑과 `additional_urls` 제안에 집중한다.

**Step 3 — Additional URL HTTP 검증**: LLM이 제안한 `additional_urls`를 ThreadPoolExecutor로 병렬 검증. 각 URL에 `validated`, `http_status` 필드를 추가.

### 8-3. taxonomy → feature_id 변환 규칙

```
taxonomy feature ID (접두사 없음): "transaction_fee_rate"
출력 feature_id (feat_ 접두사):   "feat_transaction_fee_rate"
```

---

## 9. LLM 분석 설계

### 9-1. 기본 경로: Claude Code CLI

대부분의 agent는 `ClaudeCodeCliAnalyzer`를 통해 Claude Code CLI를 호출한다.

장점으로는 구독 기반으로 별도 과금이 없고, 로컬 환경에서 제어하기 쉽다. 단점으로는 temperature 설정을 지원하지 않고 CLI 프로세스 생성 오버헤드가 있다.

### 9-2. 예외 경로: Claude API 직접 호출

temperature=0 결정론적 처리가 필요한 경우에만 Claude API(`ANTHROPIC_API_KEY`)를 사용한다.
적용 위치: `normalize_competitor_ids_node`의 `ProductIdResolver` (comp_* 슬러그 생성 일관성 필요)

### 9-3. Brave Search API

URL 검증 실패 항목 재탐색(`url_retry_node` Phase 1)에 사용한다.
환경변수: `BRAVE_SEARCH_API_KEY`. 무료 크레딧: $5/월 → Search 플랜 기준 약 1,000 쿼리/월.

### 9-4. 인터페이스 추상화

```text
AnalysisEngine
 ├── ClaudeCodeCliAnalyzer  ← 기본 경로 (대부분의 agent)
 └── ClaudeApiAnalyzer      ← 결정론적 처리 필요 시

AgentLayer (구현 완료)
 ├── QueryIntakeAgent
 ├── CompetitorDiscoveryAgent
 ├── DomainTaxonomyAgent
 ├── NormalizeCompetitorIds (ProductIdResolver)
 ├── OfficialSourceResolverAgent
 └── FeatureUrlMapperAgent

AgentLayer (미구현)
 ├── FeatureExtractionAgent
 ├── FeatureComparisonAgent
 ├── YouTubeQueryPlannerAgent
 ├── YouTubeCollectionAgent
 ├── ReactionAnalysisAgent
 └── InsightReportAgent
```

---

## 10. 향후 SQLite 마이그레이션 방향

| 현재 저장 | 향후 테이블 |
|-----------|------------|
| `taxonomy/domains.json` | `domains` |
| `taxonomy/{id}_slug.json` | `domain_taxonomies` |
| `cache/product_name_normalization.json` | `product_name_cache` |
| `cache/agent_outputs/query_intake.json` | `agent_run_steps` |
| `cache/agent_outputs/competitor_discovery.json` | `agent_run_steps`, `competitor_candidates` |
| `cache/agent_outputs/official_source_resolver.json` | `product_sources`, `source_validations` |
| 향후 `projects/.../product_profiles.json` | `product_profiles` |
| 향후 `projects/.../feature_matrix.json` | `feature_comparisons` |
| 향후 `projects/.../videos/*/analysis.json` | `video_analyses` |
| 향후 `projects/.../reports/{id}.json` | `insight_reports` |

---

## 11. 구현 우선순위

| 단계 | 내용 | 상태 |
|------|------|------|
| 1 | `QueryIntakeAgent` | ✅ 완료 |
| 2 | Human-in-the-loop #1 (human_review) | ✅ 완료 |
| 3 | `CompetitorDiscoveryAgent` | ✅ 완료 |
| 4 | `DomainTaxonomyAgent` | ✅ 완료 |
| 5 | `NormalizeCompetitorIds` | ✅ 완료 |
| 6 | Human-in-the-loop #2 (competitor_selection) | ✅ 완료 |
| 7 | `OfficialSourceResolverAgent` | ✅ 완료 |
| 8 | `url_retry_node` (Two-Phase, interrupt #3) | ✅ 완료 |
| 9 | `FeatureUrlMapperAgent` | ✅ 완료 |
| 10 | Human-in-the-loop #4 (feature_selection) | ✅ 완료 |
| 11 | `FeatureExtractionAgent` | 🔲 미구현 |
| 12 | `FeatureComparisonAgent` | 🔲 미구현 |
| 13 | `YouTubeQueryPlannerAgent` | 🔲 미구현 |
| 14 | `YouTubeCollectionAgent` | 🔲 미구현 |
| 15 | `ReactionAnalysisAgent` | 🔲 미구현 |
| 16 | `InsightReportAgent` | 🔲 미구현 |
| 17 | `data/projects/` 저장 구조 연동 | 🔲 미구현 |
| 18 | JSON → SQLite 마이그레이션 | 🔲 미구현 |

---

## 12. 결론

현재 단계의 구현은 단순 순차 실행에서 **Human-in-the-loop 기반 대화형 파이프라인**으로 설계가 발전했다. 4개 interrupt 체크포인트를 통해 사용자가 경쟁사 선택, URL 검증, feature 선택 등 핵심 결정에 참여하는 구조다.

현재 고정된 구조:
- interrupt 4개를 포함한 10노드 파이프라인
- DomainTaxonomyAgent 기반 도메인 taxonomy 자동 생성·캐시
- Two-Phase URL 재시도 + critical_error 강제 종료 정책
- Claude Code CLI 기본 경로 + Claude API 선택 경로

다음 고정 대상:
- FeatureExtractionAgent의 크롤링 대상 범위 (selected_feature_ids 기반)
- feature_matrix schema 및 FeatureComparisonAgent 출력 구조
- YouTube 수집·분석 파이프라인 설계
- data/projects/ 저장 구조와 현재 캐시 구조의 통합 방안
