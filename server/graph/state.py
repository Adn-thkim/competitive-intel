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
    feature_url_mapper_node가 도출한 단일 비교 분석 항목 (v0.10).

    feature_id는 'feat_' 접두사를 가진 snake_case 식별자이다.
    report_type은 D4 enum 7종 중 하나(`comparison_matrix`·`reaction_insight`·
    `marketing_social`·`battlecard`·`positioning_map`·`market_context_swot`·
    `executive_summary`)로, Feature Selection UI(D6) 카드 그룹핑 기준이다.

    candidate_coverage 항목 형식:
      {
        "candidate_id": str,                 # own_* / comp_* / func_*
        "coverage": str,                     # "sufficient" | "partial" | "not_found"
        "existing_urls": [                   # official_source + Brave 검색 발견 URL
          {"url": str, "relevance_note": str, "origin": "official_source|brave_search"}
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
    report_type:       str          # D4 enum 7종 중 하나 (v0.10)
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

    # ── domain_taxonomy_node 출력 (v0.10 스키마) ────────────────────────────
    domain_taxonomy: dict[str, Any]
    """
    DomainTaxonomyAgent가 생성·로드한 도메인 taxonomy.
    v0.10 이후: domain_slug · domain_type · report_config(7종 enum × features ·
    feature_labels · categories · search_query_hints · (조건부) aspect_codebook ·
    action_lens)를 포함한다.
    feature_url_mapper_node가 search_query_hints로 Brave 검색을 수행할 때 이 필드를
    참조한다. data/taxonomy/{domain_id}_slug.json에 캐시되며 7일 TTL로 관리된다.
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

    # ── feature_url_mapper 노드간 브릿지 키 (v0.10.9 → v0.10.22.1 갱신) ────
    # 기존 단일 feature_url_mapper_node 가 4단계 노드로 분리되고(v0.10.9), 이후 v0.10.19
    # 에서 URL 탐색 단계가 5개 source-type 노드로 추가 분리되었다. 단계간 데이터 전달용:
    #   5종 url_discovery_<source>_node → *_urls_by_candidate (5종 키)
    #   urls_merge_node                  → brave_urls_by_candidate (5종 union, v0.10.19 임시 어댑터)
    #   page_meta_collect_node           → candidates_with_meta
    #   feature_mapping_llm_node         → raw_features
    #   additional_urls_validation_node  → analysis_features (기존 최종 출력)
    # v0.10.22.1 cleanup: 옛 url_discovery_brave_node.py 파일 삭제 완료.
    brave_urls_by_candidate: dict[str, list[dict[str, Any]]]
    """
    `urls_merge_node` (v0.10.19 임시 어댑터) 가 5종 url_discovery 노드의 결과를 union 머지한
    Brave Search 발견 URL 후보. 본 키는 옛 단일 url_discovery_brave_node 와의 후방 호환을
    위해 동일 이름이 유지되고 있다. v0.10.26 에서 urls_merge_node 폐기 및 본 키 폐기 예정.

    구조: {candidate_id: [{url, page_title, meta_description, origin, matched_report_types}]}
    page_meta_collect_node 가 official_sources 의 URL meta 와 병합한다.
    """

    # ── feature_url_mapper 5중 fan-out: source-type 별 URL 탐색 결과 (v0.10.19) ──
    # 옛 url_discovery_brave_node 폐기 → 5개 source-type 노드로 분리. 각 노드가 자기
    # source-type 의 search_query_hints 만 사용해 Brave 검색 수행. urls_merge_node 가
    # 5개 결과를 단일 brave_urls_by_candidate 로 임시 union 머지(v0.10.26 에서 cross_reference
    # 노드로 책임 분리되며 본 5개 키는 v0.10.27 통합 노드 입력으로 그대로 사용됨).

    official_urls_by_candidate: dict[str, list[dict[str, Any]]]
    """
    url_discovery_official_node 산출. comparison_matrix · battlecard(A Fact) ·
    market_context_swot(규제 부분) 의 hints 로 Brave 검색하여 발견한 공식 사이트 + 매체
    URL 후보. 구조는 brave_urls_by_candidate 와 동일. v0.10.27 의 feature_mapping_official_node
    가 본 키를 직접 read.
    """

    blog_community_urls_by_candidate: dict[str, list[dict[str, Any]]]
    """
    url_discovery_blog_community_node 산출. reaction_insight 의 hints 중 외부 도메인
    지향 hint 로 Brave 검색하여 발견한 블로그·커뮤니티·매체 후기 URL. v0.10.27 의
    feature_mapping_blog_community_node 가 본 키를 직접 read.
    """

    youtube_reactions_urls_by_candidate: dict[str, list[dict[str, Any]]]
    """
    url_discovery_youtube_reactions_node 산출. reaction_insight 의 3rd-party 영상.
    v0.10.19 단계에서는 스켈레톤 (빈 dict). v0.10.20 에서 YouTube Data API v3 실 통합.
    각 영상 항목: {url, video_id, channel_id, channel_title, view_count, like_count,
                  comment_count, published_at, origin="youtube_reactions", matched_report_types}.
    """

    owned_channel_urls_by_candidate: dict[str, list[dict[str, Any]]]
    """
    url_discovery_owned_channels_node 산출. marketing_social 의 자사·경쟁사 운영 채널
    (Instagram · X · 블로그 · 보도자료 · YouTube 공식 채널). v0.10.19 단계에서는 스켈레톤
    (빈 dict). v0.10.21 에서 Brave 검색 + LLM 검증 실 구현. 각 항목 platform 필드 보유:
    'instagram' | 'x' | 'blog_naver' | 'blog_tistory' | 'press_release' | 'youtube_official'.
    """

    macro_urls_by_candidate: dict[str, list[dict[str, Any]]]
    """
    url_discovery_macro_node 산출. market_context_swot 의 매크로 데이터 — 정부 통계 ·
    산업 보고서 · 트레이드 미디어 URL. v0.10.19 단계에서는 기존 _discover_via_brave 헬퍼
    재사용. v0.10.22 에서 도메인 화이트리스트(kosis.kr · bok.or.kr · nia.or.kr 등) 추가.
    candidate 단위가 아닌 domain 단위 캐싱이지만 key 호환을 위해 candidate_id 키 dict
    구조 유지(키는 'domain' 단일 또는 candidate_id 들이 모두 같은 값을 공유).
    """

    candidates_with_meta: list[dict[str, Any]]
    """
    Step 1(page_meta_collect_node)이 생성한 candidate별 validated URL + page meta 통합 목록.
    구조: [{candidate_id, source_type, validated_urls: [{url, page_title, meta_description,
                                                          origin, [matched_report_types]}]}]
    feature_mapping_llm_node가 report_type 별 LLM 호출의 입력으로 사용한다.
    """

    raw_features: list[dict[str, Any]]
    """
    Step 2(feature_mapping_llm_node)가 LLM 호출로 도출한 정규화 전 feature 목록.
    additional_urls_validation_node가 각 항목의 additional_urls를 HTTP 검증한 뒤
    analysis_features로 변환한다.
    """

    # ── feature_url_mapper 최종 출력 (additional_urls_validation_node 산출) ──
    analysis_features: list[AnalysisFeature]
    """
    domain_taxonomy 기반 report_type × feature × candidate URL 커버리지 매핑 결과.
    v0.10 이후: 각 항목은 report_type(D4 enum 7종 중 하나), feature_id, feature_name,
    description, priority, candidate_coverage(coverage + existing_urls + additional_urls)
    + (Brave 검색 결과 메타데이터)를 포함한다.

    report_type 필드로 feature_selection UI에서 카드 단위 그룹핑이 가능하다 (D6).
    feature_selection_node(interrupt #4)에서 사용자가 리포트 카드 단위로 목적을 선택하고
    개별 feature를 세부 조정하면, 선택 결과가 아래 두 필드에 저장된다.
    feature_extraction_node는 selected_feature_ids에 해당하는 항목만 크롤링한다.
    """

    selected_purposes: list[str]
    """
    feature_selection_node(interrupt #4) 이후 사용자가 선택한 리포트 ID 목록.
    v0.10 이후: report_config 7종 enum(`comparison_matrix` 등) 중 사용자가 이번
    분석에 포함하기로 선택한 report_type 목록. feature_extraction_node가 해당
    리포트에 속하는 feature만 처리하는 필터로 사용한다.
    ※ 키 이름은 호환을 위해 selected_purposes 유지(다음 §6-5 작업에서 정리 검토).
    """

    selected_feature_ids: list[str]
    """
    feature_selection_node(interrupt #4) 이후 사용자가 선택한 feature_id 목록.
    feat_* 접두사를 가진다. selected_purposes 내에서 개별 feature를 세부 조정한 결과.
    """

    # ── feature_extraction_node 출력 (§6-6 D3 옵션 C — 미구현) ───────────────
    product_profiles:     list[dict[str, Any]]
    normalized_features:  list[dict[str, Any]]
    feature_pool:         dict[str, Any]
    """
    §11-10 흐름 A의 공유 Feature Pool. feature_extraction_node가 채우며 7개 리포트 노드가 read.
    구조: {feature_id: {"value": Any, "source_url": str, "evidence": str, ...}}
    """

    # ── 신규 수집 노드 6종 출력 (§6-6a v0.6 신설, D11 비활성 1종 포함) ────────
    # community_collection_node      → community_posts: list[dict]
    # app_store_review_collection    → app_store_reviews: list[dict]  (D11 비활성)
    # youtube_query_planner_node     → query_plan: dict[str, Any]
    # youtube_collection_node        → collected_videos / selected_comments
    # reaction_analysis_node         → reaction_analysis: dict
    # youtube_channel_metadata_collection → youtube_channel_metadata: dict
    # blog_rss_collection            → blog_rss_posts: list[dict]
    # pr_release_collection          → pr_releases: list[dict]
    # market_context_collection      → market_context: dict
    query_plan:                dict[str, Any]
    search_results:            list[dict[str, Any]]
    collected_videos:          list[dict[str, Any]]
    selected_comments:         list[dict[str, Any]]
    community_posts:           list[dict[str, Any]]
    app_store_reviews:         list[dict[str, Any]]
    reaction_analysis:         dict[str, Any]
    youtube_channel_metadata:  dict[str, Any]
    blog_rss_posts:            list[dict[str, Any]]
    pr_releases:               list[dict[str, Any]]
    market_context:            dict[str, Any]

    # ── 7개 리포트 노드 출력 (§6-4 D1=B 분리형, v0.10) ───────────────────────
    report_outputs: dict[str, dict[str, Any]]
    """
    D4 enum 7종(`comparison_matrix`·`reaction_insight`·`marketing_social`·
    `battlecard`·`positioning_map`·`market_context_swot`·`executive_summary`)을 키로 하는
    리포트 산출물 dict. 각 리포트 노드가 자신의 키에 write하며, 흐름 B 의존 리포트는
    상류 리포트의 산출물을 read.

    구조 (모든 리포트 공통 필드):
      {
        "<report_type>": {
          "rubric_version": str,              # 적용된 Rubric 버전
          "categories": list[str],            # Rubric §2-x 표준 카테고리 중 채택 항목
          "content": dict[str, Any],          # 리포트별 산출 본문 (구조는 §2-x 명세)
          "evaluation_score": int,            # 자체 평가 1–5점 (Rubric §2-x 루브릭)
          "generated_at": str,                # ISO 8601 timestamp
          "source_references": list[dict],    # 출처 URL·feature_id·인용 등
          "warnings": list[str]               # AP-1~AP-10 위반 후보 경고
        }, ...
      }

    fan-in semantics: replace per key (각 리포트 노드는 자기 키만 write).
    LangGraph reducer 미설정 — 기본 replace 동작 사용.
    """

    # ── executive_summary 종합 출력 (§6-4 + §11-10 top 노드) ─────────────────
    final_report: dict[str, Any]
    """
    executive_summary 노드의 최종 통합 산출물. report_outputs["executive_summary"]와
    동일 내용을 별도 키로 노출하여 프런트엔드 / API 응답에서 즉시 접근 가능하게 한다.
    구조: {bluf, situation, complication, resolution, persona_recommendations, cross_links}.
    """

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
