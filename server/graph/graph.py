"""
server/graph/graph.py
----------------------
LangGraph 파이프라인 조립 모듈 (v0.10.9 CD-fanout + list-fan-in barrier + 4단계 분리).

이 모듈이 export하는 compiled_graph 는 api.py가 import해 사용한다.
노드를 추가할 때는 이 파일의 build_graph() 함수만 수정하면 된다.

v0.10.9 토폴로지 (pipeline_topology_redesign.md §6-2 v0.10.9 확정)
-----------------------------------------------------------------
  query_intake
    → human_review (#1)
    → competitor_discovery
        ├─→ normalize_competitor_ids                     (분기 A, 직렬)
        │     → competitor_selection (#2)
        │     → official_source_resolver
        │     → url_retry (#3)
        │           ├─(critical_error)→ END
        │           └─(정상)         → ab_join          ← conditional
        │
        └─→ domain_modeling                              (분기 B, 병렬)
              └─────────────────────→ ab_join            ← list-fan-in barrier 의 source
                                       ↓
                  url_discovery_brave  (Step 0 — Brave 검색)
                                       ↓
                  page_meta_collect    (Step 1 — page meta 수집)
                                       ↓
                  feature_mapping_llm  (Step 2 — LLM 호출, 가장 무거움)
                                       ↓
                  additional_urls_validation (Step 3 — HTTP 검증)
                                       ↓
                                  feature_selection (#4)
                                   → END  ← 임시

핵심 변경 의도 (v0.10.7 vs v0.10.5 / v0.10.6)
---------------------------------------------
- v0.10.5 문제: `url_retry --(conditional)--> ab_join` + `domain_modeling --(direct)--> ab_join`
  의 혼합 fan-in 이 LangGraph 이슈 #3249(2025-01-30, "Node with multiple incoming
  edges not executed correctly when combined with conditional edges") 의 trace에
  해당하여 ab_join 이 두 번 발화되는 race 가 발생하였다.
- v0.10.6 시도(폐기): 두 incoming 을 모두 direct edge 로 통일 + critical_error 분기를
  ab_join 이후로 이동. 그러나 LangGraph Pregel BSP 가 single-direct-edge 다중 fan-in
  의 AND-wait 를 보장하는지 결정론적으로 확인할 수 없었다.
- v0.10.7 해결: LangGraph 공식 list-based fan-in barrier API
  (`builder.add_edge(["A", "B"], "C")`) 를 도입한다. 이 API 는 C 의 incoming source 가
  단일 채널 barrier 로 묶이도록 LangGraph 에 명시적으로 선언하여 두 source 가 모두
  완료될 때까지 C 의 실행을 보류한다. critical_error 분기는 url_retry 직후 conditional
  로 유지하되, list-edge 의 url_retry 측 trigger 는 conditional 의 정상경로("join") 가
  발화한 후에만 등록된다. 본 환경(scripts/verify_fanin.py)에서 1회 발화가 검증되었다.

url_retry 이후 분기 규칙 (_route_after_url_retry):
  state.critical_error 있음 → END        (own_* URL 미검증 등 치명적 오류)
  state.critical_error 없음 → "join"     → ab_join (list-fan-in barrier 의 source 중 하나)

체크포인터: MemorySaver (인메모리)
  - 재시작 시 초기화되지만, 로컬 단일 사용자 환경에서는 충분하다.
  - interrupt() + MemorySaver 조합이 가능하려면 Python 프로세스가
    장기 실행 상태여야 한다. → api.py의 uvicorn 서버가 이를 보장한다.
  - 향후 내구성이 필요하면 SqliteSaver 로 교체한다.

참고
----
- LangGraph 이슈 #3249 (2025): conditional + direct 혼합 fan-in race
- LangGraph 이슈 #954  (2024): final 노드 incoming 대기 미보장 → list-edge 해결
- LangChain Changelog (2025-05-20): Deferred nodes in LangGraph
"""

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from server.graph.nodes.competitor_discovery_node import competitor_discovery_node
from server.graph.nodes.competitor_selection_node import competitor_selection_node
from server.graph.nodes.domain_modeling_node import domain_modeling_node
from server.graph.nodes.feature_selection_node import feature_selection_node
# v0.10.9 — feature_url_mapper 4단계 노드 분리 (옵션 A)
from server.graph.nodes.url_discovery_brave_node import url_discovery_brave_node
from server.graph.nodes.page_meta_collect_node import page_meta_collect_node
from server.graph.nodes.feature_mapping_llm_node import feature_mapping_llm_node
from server.graph.nodes.additional_urls_validation_node import additional_urls_validation_node
from server.graph.nodes.human_review_node import human_review_node
from server.graph.nodes.normalize_competitor_ids_node import normalize_competitor_ids_node
from server.graph.nodes.official_source_resolver_node import official_source_resolver_node
from server.graph.nodes.query_intake_node import query_intake_node
from server.graph.nodes.url_retry_node import url_retry_node
from server.graph.state import DomainAnalysisState


# ── 라우팅 함수 ───────────────────────────────────────────────────────────────

def _route_after_url_retry(state: DomainAnalysisState) -> str:
    """
    url_retry_node 이후 분기를 결정한다 (v0.10.7).

    critical_error가 설정된 경우 (own_* URL 미검증 등):
      → "end" : 파이프라인을 즉시 종료. 후속 분석 노드를 실행하지 않는다.
        신뢰할 수 없는 분석 결과는 분석을 하지 않는 것보다 치명적이다.

    critical_error가 없는 경우:
      → "join" : list-fan-in barrier `add_edge(["url_retry", "domain_modeling"], "ab_join")`
        의 url_retry 측 trigger 로 진입. ab_join 은 domain_modeling 측 trigger 가 함께
        ready 된 시점에만 1회 발화된다.

    설계 의도: v0.10.5/v0.10.6 race 의 근본 원인은 conditional + direct 혼합 fan-in 이
    LangGraph 이슈 #3249 의 trace에 해당하기 때문이었다. v0.10.7 에서는 list-edge 로
    두 source 를 단일 barrier 에 묶고, critical_error 만 conditional 로 분기한다.
    """
    if state.get("critical_error"):
        return "end"
    return "join"


def _ab_join_node(state: DomainAnalysisState) -> dict:
    """
    분기 A·B 명시적 join 노드 (v0.10.7 — list-fan-in barrier).

    `add_edge(["url_retry", "domain_modeling"], "ab_join")` 에 의해 LangGraph 가 두 source
    의 완료를 단일 채널 barrier 로 대기한 뒤 본 노드를 1회만 실행한다. 본 노드는
    진단 print 외에는 state 를 변경하지 않으며, 이후 단일 direct edge
    (`ab_join → feature_url_mapper`) 로 흐름이 단일화된다.

    진단용 print 포함 — 두 분기가 모두 완료된 시점에 도달했음을 stdout 으로 확인.
    검증: scripts/verify_fanin.py 에서 본 패턴이 1회 발화함을 확인하였다.
    """
    from datetime import datetime, timezone
    has_official = bool(state.get("official_sources"))
    has_taxonomy = bool(state.get("domain_taxonomy"))
    print(
        f"🔗 [ab_join_node] reached at {datetime.now(timezone.utc).isoformat()} — "
        f"official_sources={'있음' if has_official else '❌없음'} "
        f"domain_taxonomy={'있음' if has_taxonomy else '❌없음'}",
        flush=True,
    )
    return {}


def build_graph() -> object:
    """
    StateGraph를 조립하고 MemorySaver 체크포인터로 컴파일해 반환한다.

    Returns
    -------
    CompiledStateGraph
        compiled_graph.invoke() / compiled_graph.get_state() 로 사용한다.
    """
    builder = StateGraph(DomainAnalysisState)

    # ── 노드 등록 ─────────────────────────────────────────────────────────
    builder.add_node("query_intake",             query_intake_node)
    builder.add_node("human_review",             human_review_node)
    builder.add_node("competitor_discovery",     competitor_discovery_node)
    builder.add_node("domain_modeling",          domain_modeling_node)
    builder.add_node("normalize_competitor_ids", normalize_competitor_ids_node)
    builder.add_node("competitor_selection",     competitor_selection_node)
    builder.add_node("official_source_resolver", official_source_resolver_node)
    builder.add_node("url_retry",                url_retry_node)
    builder.add_node("ab_join",                  _ab_join_node)
    # v0.10.9 — feature_url_mapper 4단계 분리 (옵션 A)
    builder.add_node("url_discovery_brave",          url_discovery_brave_node)
    builder.add_node("page_meta_collect",            page_meta_collect_node)
    builder.add_node("feature_mapping_llm",          feature_mapping_llm_node)
    builder.add_node("additional_urls_validation",   additional_urls_validation_node)
    builder.add_node("feature_selection",            feature_selection_node)
    # TODO (v0.10 + §6-6a): 아래 노드는 구현 후 주석 해제
    # ── feature_extraction + 신규 수집 노드 6종 ─────────────────────────
    # builder.add_node("feature_extraction",                 feature_extraction_node)
    # builder.add_node("community_collection",               community_collection_node)
    # builder.add_node("app_store_review_collection",        app_store_review_collection_node)  # D11 비활성
    # builder.add_node("youtube_query_planner",              youtube_query_planner_node)
    # builder.add_node("youtube_collection",                 youtube_collection_node)
    # builder.add_node("reaction_analysis",                  reaction_analysis_node)
    # builder.add_node("youtube_channel_metadata_collection",youtube_channel_metadata_collection_node)
    # builder.add_node("blog_rss_collection",                blog_rss_collection_node)
    # builder.add_node("pr_release_collection",              pr_release_collection_node)
    # builder.add_node("market_context_collection",          market_context_collection_node)
    # ── 7개 리포트 노드 (D1=B 분리형) ────────────────────────────────────
    # builder.add_node("comparison_matrix",     comparison_matrix_node)
    # builder.add_node("reaction_insight",      reaction_insight_node)
    # builder.add_node("marketing_social",      marketing_social_node)
    # builder.add_node("battlecard",            battlecard_node)
    # builder.add_node("positioning_map",       positioning_map_node)
    # builder.add_node("market_context_swot",   market_context_swot_node)
    # builder.add_node("executive_summary",     executive_summary_node)

    # ── 엣지 연결 (v0.10.7 CD-fanout + list-fan-in barrier) ──────────────
    builder.add_edge(START,                        "query_intake")
    builder.add_edge("query_intake",               "human_review")
    builder.add_edge("human_review",               "competitor_discovery")

    # fan-out: competitor_discovery 종료 직후 두 분기 시작
    builder.add_edge("competitor_discovery",       "normalize_competitor_ids")  # 분기 A
    builder.add_edge("competitor_discovery",       "domain_modeling")            # 분기 B

    # 분기 A: interrupts #2·#3 포함, 공식 출처 탐색·검증 직렬 진행
    builder.add_edge("normalize_competitor_ids",   "competitor_selection")
    builder.add_edge("competitor_selection",       "official_source_resolver")
    builder.add_edge("official_source_resolver",   "url_retry")

    # v0.10.7 — list-fan-in barrier 토폴로지
    #
    # 1) url_retry 직후 conditional: critical_error 만 분기, 정상경로는 ab_join 진입 trigger.
    #      "end"  → END     (own_* URL 미검증 등 치명적 오류 — 파이프라인 강제 종료)
    #      "join" → ab_join (list-fan-in barrier 의 url_retry 측 source trigger)
    builder.add_conditional_edges(
        "url_retry",
        _route_after_url_retry,
        {"end": END, "join": "ab_join"},
    )

    # 2) list-fan-in barrier: 두 source(url_retry, domain_modeling) 가 모두 ready 되어야
    #    LangGraph 가 ab_join 을 단 1회 발화한다. 본 환경에서 scripts/verify_fanin.py 로
    #    1회 발화 동작이 검증됨.
    builder.add_edge(["url_retry", "domain_modeling"], "ab_join")

    # 3) ab_join → feature_url_mapper 4단계 직렬 (v0.10.9 옵션 A)
    #    각 단계가 독립 노드로 분리되어 timeout 격리 + UI stage 세분화가 가능해진다.
    builder.add_edge("ab_join",                       "url_discovery_brave")
    builder.add_edge("url_discovery_brave",           "page_meta_collect")
    builder.add_edge("page_meta_collect",             "feature_mapping_llm")
    builder.add_edge("feature_mapping_llm",           "additional_urls_validation")
    builder.add_edge("additional_urls_validation",    "feature_selection")
    # feature_selection 이후: 현재 임시 END
    # TODO (§6-7 v0.6): 7중 fan-out + 6중 fan-in 엣지 적용
    builder.add_edge("feature_selection",          END)

    # TODO (§6-7 v0.6 v0.10 D11 반영) — 노드 구현 후 아래 엣지 활성화
    # ── 1) feature_selection 이후: feature_extraction + 신규 수집 노드 fan-out ──
    # builder.add_edge("feature_selection", "feature_extraction")
    # builder.add_edge("feature_selection", "community_collection")
    # # builder.add_edge("feature_selection", "app_store_review_collection")  # D11 비활성 v0.8
    # builder.add_edge("feature_selection", "youtube_query_planner")
    # builder.add_edge("feature_selection", "youtube_channel_metadata_collection")
    # builder.add_edge("feature_selection", "blog_rss_collection")
    # builder.add_edge("feature_selection", "pr_release_collection")
    # builder.add_edge("feature_selection", "market_context_collection")
    #
    # ── 2) YouTube 댓글 파이프라인 ──────────────────────────────────────
    # builder.add_edge("youtube_query_planner", "youtube_collection")
    #
    # ── 3) reaction_insight: 2채널 fan-in → reaction_analysis → reaction_insight ──
    # builder.add_edge("youtube_collection",          "reaction_analysis")
    # builder.add_edge("community_collection",        "reaction_analysis")
    # # builder.add_edge("app_store_review_collection", "reaction_analysis")  # D11 비활성 v0.8
    # builder.add_edge("reaction_analysis",           "reaction_insight")
    #
    # ── 4) marketing_social: 3채널 fan-in ───────────────────────────────
    # builder.add_edge("youtube_channel_metadata_collection", "marketing_social")
    # builder.add_edge("blog_rss_collection",                 "marketing_social")
    # builder.add_edge("pr_release_collection",               "marketing_social")
    #
    # ── 5) comparison_matrix: feature_extraction 의존 ───────────────────
    # builder.add_edge("feature_extraction", "comparison_matrix")
    #
    # ── 6) mid-tier 흐름 B 의존 ─────────────────────────────────────────
    # builder.add_edge("comparison_matrix", "positioning_map")
    # builder.add_edge("comparison_matrix", "battlecard")
    # builder.add_edge("reaction_insight",  "battlecard")
    # builder.add_edge("marketing_social",  "battlecard")
    # builder.add_edge("comparison_matrix",         "market_context_swot")
    # builder.add_edge("reaction_insight",          "market_context_swot")
    # builder.add_edge("marketing_social",          "market_context_swot")
    # builder.add_edge("market_context_collection", "market_context_swot")
    #
    # ── 7) top-tier: 6개 리포트 fan-in → executive_summary → END ──────────
    # builder.add_edge("comparison_matrix",   "executive_summary")
    # builder.add_edge("reaction_insight",    "executive_summary")
    # builder.add_edge("marketing_social",    "executive_summary")
    # builder.add_edge("battlecard",          "executive_summary")
    # builder.add_edge("positioning_map",     "executive_summary")
    # builder.add_edge("market_context_swot", "executive_summary")
    # builder.add_edge("executive_summary",   END)

    # ── 컴파일 ────────────────────────────────────────────────────────────
    # interrupt()를 노드 내부에서 직접 호출하므로 interrupt_before 설정 불필요.
    checkpointer = MemorySaver()
    return builder.compile(checkpointer=checkpointer)


# 모듈 임포트 시 한 번만 빌드한다.
# api.py 가 `from server.graph.graph import compiled_graph` 로 참조한다.
compiled_graph = build_graph()

# ─────────────────────────────────────────────────────────────────────────────
# 진단 출력 — 서버 시작 시 v0.10.9 토폴로지 검증
#   (1) list-fan-in barrier(ab_join) — v0.10.7 도입 유지
#   (2) feature_url_mapper 4단계 직렬 분리 (옵션 A) — v0.10.9 신설
# list-edge 는 LangGraph 내부에서 (source, target) 페어로 평탄화 되어 노출된다.
# draw_ascii 는 grandalf 의존이 있어 사용하지 않는다(import 실패 차단).
# 디버깅 종료 후 본 블록은 제거 가능.
# ─────────────────────────────────────────────────────────────────────────────
try:
    _g_view = compiled_graph.get_graph()
    _edge_pairs = {
        (getattr(e, "source", None), getattr(e, "target", None))
        for e in _g_view.edges
    }
    # v0.10.7 barrier 검증
    _has_branch_b   = ("competitor_discovery", "domain_modeling") in _edge_pairs
    _has_list_a     = ("url_retry",            "ab_join")         in _edge_pairs
    _has_list_b     = ("domain_modeling",      "ab_join")         in _edge_pairs
    # v0.10.9 4단계 직렬 검증
    _e1 = ("ab_join",                    "url_discovery_brave")        in _edge_pairs
    _e2 = ("url_discovery_brave",        "page_meta_collect")          in _edge_pairs
    _e3 = ("page_meta_collect",          "feature_mapping_llm")        in _edge_pairs
    _e4 = ("feature_mapping_llm",        "additional_urls_validation") in _edge_pairs
    _e5 = ("additional_urls_validation", "feature_selection")          in _edge_pairs
    if all([_has_branch_b, _has_list_a, _has_list_b, _e1, _e2, _e3, _e4, _e5]):
        print(
            "[graph.py] ✅ v0.10.9 토폴로지 확인 — "
            "list-fan-in barrier + feature_url_mapper 4단계 직렬 정상",
            flush=True,
        )
    else:
        _missing = []
        if not _has_branch_b: _missing.append("competitor_discovery → domain_modeling")
        if not _has_list_a:   _missing.append("url_retry → ab_join")
        if not _has_list_b:   _missing.append("domain_modeling → ab_join")
        if not _e1: _missing.append("ab_join → url_discovery_brave")
        if not _e2: _missing.append("url_discovery_brave → page_meta_collect")
        if not _e3: _missing.append("page_meta_collect → feature_mapping_llm")
        if not _e4: _missing.append("feature_mapping_llm → additional_urls_validation")
        if not _e5: _missing.append("additional_urls_validation → feature_selection")
        print(f"[graph.py] ❌ v0.10.9 토폴로지 엣지 누락: {_missing}", flush=True)
except Exception as _diag_exc:  # noqa: BLE001
    print(f"[graph.py] 진단 출력 실패: {_diag_exc}", flush=True)