"""
server/graph/state.py
----------------------
LangGraph 파이프라인 공유 상태 스키마.

Design_Spec.md의 DomainAnalysisState를 기준으로 하되,
interrupt() Human-in-the-loop 흐름에 필요한 필드를 추가한다.

파이프라인 흐름과 상태 키 생성 순서:
  START
    → [query_intake_node]
        raw_query → query_intake_output
    → [human_review_node]  ← interrupt() #1 발생, 프런트엔드 폼 수정 후 재개
        resume(edited_form) → project_id, domain_name, own_product(+product_id), ...
    → [competitor_discovery_node]
        → own_product_summary, competition_axes,
          competitor_candidates(temp id), functional_competitors, excluded_or_deferred
    → [normalize_competitor_ids_node]
        competitor_candidates(temp id) → competitor_candidates(comp_* 슬러그 확정)
    → [competitor_selection_node]  ← interrupt() #2 발생, 프런트엔드 선택 UI 후 재개
        resume(selected_ids) → selected_competitor_ids
    → [official_source_resolver_node]  (JS / 후속 구현)
    → ...
    END

누적 필드 (Annotated reducer):
    agent_steps, errors  ← 각 노드가 항목을 추가하면 LangGraph가 기존 리스트에 이어붙인다.
    일반 필드            ← 노드가 반환한 값으로 대체(replace).
"""

from __future__ import annotations

import operator
from typing import Annotated, Any
from typing_extensions import TypedDict


# ── 중첩 타입 힌트 (Design_Spec.md §4-2-1 참고) ─────────────────────────────

class CompetitorCandidate(TypedDict, total=False):
    candidate_id:     str          # comp_* 슬러그 (normalize_competitor_ids_node가 확정)
    brand:            str
    product_name:     str          # 정규화된 공식 명칭 (normalize_competitor_ids_node가 갱신)
    competition_type: str          # "direct" | "indirect" | "substitute"
    category:         str
    why_competitor:   list[str]
    evidence_summary: str
    confidence:       float
    needs_validation: bool


class FunctionalCompetitor(TypedDict, total=False):
    """
    브랜드 상품이 아닌 전통적·기능적 대안 수단.

    candidate_id는 'func_' 접두사를 가진다. (예: func_local_atm)
    competitor_selection_node에서 competitor_candidates와 함께 사용자에게 제시된다.
    selected_competitor_ids에 포함될 경우 후속 분석 대상이 된다.
    """
    candidate_id:  str    # func_* 접두사
    method_name:   str    # 대안 수단 명칭. 예: "현지 ATM 현금 인출"
    provider_type: str    # 제공 주체 유형. 예: "시중은행", "공항 환전소"
    category:      str
    why_alternative: list[str]
    confidence:    float


class AgentStep(TypedDict, total=False):
    step_name:     str             # "CompetitorDiscoveryAgent" 등
    status:        str             # "pending" | "completed" | "failed" | "skipped"
    started_at:    str             # ISO 8601
    finished_at:   str
    error_message: str


class AnalysisFeature(TypedDict, total=False):
    """
    feature_url_mapper_node가 도출한 단일 비교 분석 항목.

    feature_id는 'feat_' 접두사를 가진 snake_case 식별자이다.
    candidate_coverage 항목 형식:
      {
        "candidate_id": str,                 # own_* / comp_* / func_*
        "coverage": str,                     # "sufficient" | "partial" | "not_found"
        "existing_urls": [                   # 기존 검증 URL 중 관련 있는 것
          {"url": str, "relevance_note": str}
        ],
        "additional_urls": [                 # 추가 탐색 후보 (coverage가 sufficient이면 [])
          {
            "url": str,
            "rationale": str,
            "url_confidence": float,         # LLM 사전지식 기반 0~1
            "validated": bool,               # feature_url_mapper_node Step3 검증 결과
            "http_status": int | None
          }
        ]
      }
    """
    feature_id:        str
    feature_name:      str
    description:       str
    priority:          str          # "high" | "medium" | "low"
    candidate_coverage: list[dict[str, Any]]


# ── 공유 상태 ────────────────────────────────────────────────────────────────

class DomainAnalysisState(TypedDict, total=False):
    """
    LangGraph StateGraph 전체가 공유하는 상태.

    total=False: 모든 키 optional. 각 노드는 자신이 쓰는 키만 반환한다.
    Annotated reducer 필드: agent_steps, errors.
        → 노드가 새 항목을 담은 리스트를 반환하면 기존 리스트에 이어붙여진다.
        → 일반 필드는 반환값으로 덮어쓴다.
    """

    # ── 파이프라인 메타 ──────────────────────────────────────────────────────
    raw_query:   str    # 사용자 원문 검색어 (orchestrator 주입)
    run_id:      str    # 실행 식별자 (orchestrator 주입 = LangGraph thread_id)
    request_id:  str    # 요청 추적 ID

    # ── query_intake_node 출력 (human_review_node가 interrupt 값으로 사용) ──
    query_intake_output: dict[str, Any]
    """
    QueryIntakeAgentOutput schema를 만족하는 dict.
    human_review_node의 interrupt() 호출 인자로 프런트엔드에 전달된다.
    사용자 승인 후에는 아래 flat 필드들로 분해되어 state에 저장된다.
    """

    # ── human_review_node 이후 확정되는 분석 컨텍스트 ───────────────────────
    # (CompetitorDiscoveryAgentInput schema 필드와 1:1 대응)
    project_id:          str
    domain_name:         str
    own_product:         dict[str, Any]   # product_id(own_*) 포함
    problem_statement:   str
    target_user:         list[str]
    core_value_props:    list[str]
    geography:           str
    known_keywords:      list[str]
    usage_context:       list[str]
    business_constraints: list[str]

    # ── competitor_discovery_node 출력 ───────────────────────────────────────
    own_product_summary:   dict[str, Any]
    competition_axes:      list[str]
    competitor_candidates: list[CompetitorCandidate]
    """
    normalize_competitor_ids_node 실행 전: LLM이 채운 임시 candidate_id.
    normalize_competitor_ids_node 실행 후: comp_* 슬러그로 교체.
    officialSourceResolverNode.js가 state.competitor_candidates를 직접 읽는다.
    """
    functional_competitors: list[FunctionalCompetitor]
    """
    브랜드 상품이 아닌 전통적·기능적 대안 수단 목록.
    competitor_selection_node에서 competitor_candidates와 함께 사용자에게 제시된다.
    func_* 접두사 candidate_id를 가진다.
    """
    excluded_or_deferred:  list[dict[str, Any]]

    # ── domain_taxonomy_node 출력 ───────────────────────────────────────────
    domain_taxonomy: dict[str, Any]
    """
    DomainTaxonomyAgent가 생성·로드한 도메인 taxonomy.
    domain_type, active_purposes, purpose_config(features + url_types)를 포함한다.
    feature_url_mapper_node가 URL 수집 전략을 결정할 때 이 필드를 참조한다.
    data/taxonomy/{domain_slug}.json에 캐시되며 7일 TTL로 관리된다.
    """

    # ── competitor_selection_node 출력 ───────────────────────────────────────
    selected_competitor_ids: list[str]
    """
    사용자가 선택한 경쟁사 candidate_id 목록.
    comp_* (브랜드 경쟁사) 및 func_* (기능적 대안) 혼합 가능.
    최소 1개, 최대 10개.
    """

    # ── official_source_resolver_node 출력 ──────────────────────────────────
    domain_discovery_results: list[dict[str, Any]]
    page_validation_results:  list[dict[str, Any]]
    official_sources:          list[dict[str, Any]]
    source_validation:         list[dict[str, Any]]

    # ── feature_url_mapper_node 출력 ────────────────────────────────────────
    analysis_features: list[AnalysisFeature]
    """
    domain_taxonomy 기반 purpose × feature × candidate URL 커버리지 매핑 결과.
    각 항목은 purpose_id, feature_id, feature_name, description, priority,
    candidate_coverage(coverage + existing_urls + additional_urls) 를 포함한다.

    purpose_id 필드로 feature_selection UI에서 purpose 단위 그룹핑이 가능하다.
    feature_selection_node(interrupt #4)에서 사용자가 purpose 단위로 목적을 선택하고
    개별 feature를 세부 조정하면, 선택 결과가 아래 두 필드에 저장된다.
    feature_extraction_node는 selected_feature_ids에 해당하는 항목만 크롤링한다.
    """

    selected_purposes: list[str]
    """
    feature_selection_node(interrupt #4) 이후 사용자가 선택한 분석 목적 ID 목록.
    taxonomy의 active_purposes 중 사용자가 이번 분석에 포함하기로 선택한 purpose ID.
    feature_extraction_node가 이 목적에 속하는 feature만 처리하는 필터로 사용한다.
    """

    selected_feature_ids: list[str]
    """
    feature_selection_node(interrupt #4) 이후 사용자가 선택한 feature_id 목록.
    feat_* 접두사를 가진다. selected_purposes 내에서 개별 feature를 세부 조정한 결과.
    """

    # ── feature_extraction_node / feature_comparison_node 출력 ───────────────
    product_profiles:     list[dict[str, Any]]
    normalized_features:  list[dict[str, Any]]
    feature_matrix:       dict[str, Any]

    # ── youtube_query_planner_node 출력 ──────────────────────────────────────
    query_plan:           dict[str, Any]

    # ── youtube_collection_node 출력 ─────────────────────────────────────────
    search_results:       list[dict[str, Any]]
    collected_videos:     list[dict[str, Any]]
    selected_comments:    list[dict[str, Any]]

    # ── reaction_analysis_node 출력 ──────────────────────────────────────────
    query_insights:       list[dict[str, Any]]

    # ── insight_report_node 출력 ─────────────────────────────────────────────
    report_brief:         dict[str, Any]
    final_report:         dict[str, Any]

    # ── 파이프라인 중단 플래그 ────────────────────────────────────────────────
    critical_error: str
    """
    파이프라인을 즉시 종료해야 하는 치명적 오류 메시지.
    설정되면 graph.py의 conditional edge가 END로 분기한다.
    후속 노드는 이 필드를 진입 시 가장 먼저 확인해야 한다.

    현재 발생 조건:
      - url_retry_node: own_* 항목이 Phase 2 종료 후에도 validated=False인 경우.
        자사 URL 없는 분석은 신뢰할 수 없는 결과를 생성하므로 분석을 중단한다.
    """

    # ── 공통 운영 필드 (누적 reducer) ─────────────────────────────────────────
    agent_steps: Annotated[list[AgentStep], operator.add]
    """
    각 노드가 완료·실패 시 AgentStep 항목을 append한다.
    LangGraph가 operator.add로 기존 리스트에 이어붙인다.
    """

    errors: Annotated[list[dict[str, str]], operator.add]
    """
    비치명적 오류 누적 목록. 각 항목: {"node": str, "error": str, "timestamp": str}
    LangGraph가 operator.add로 자동 누적한다.
    """
