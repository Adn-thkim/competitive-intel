"""
server/graph/graph.py
----------------------
LangGraph 파이프라인 조립 모듈.

이 모듈이 export하는 compiled_graph 는 api.py가 import해 사용한다.
노드를 추가할 때는 이 파일의 build_graph() 함수만 수정하면 된다.

현재 구현된 노드 (순서대로):
  query_intake             → QueryIntakeAgent
  human_review             → interrupt() #1 Human-in-the-loop (폼 검토)
  competitor_discovery     → CompetitorDiscoveryAgent
  domain_modeling          → DomainTaxonomyAgent: 도메인 분석 목적·feature·URL 유형 taxonomy 생성
  normalize_competitor_ids → comp_* 슬러그 확정
  competitor_selection     → interrupt() #2 Human-in-the-loop (경쟁사 선택)
  official_source_resolver → 자사·경쟁사 공식 URL / func_* 레퍼런스 탐색 (Strategy 1 분기)
  url_retry                → interrupt() #3 (선택적) URL 실패 시 수동 입력·재시도
  feature_url_mapper       → FeatureUrlMapperAgent: taxonomy 기반 feature × candidate URL 커버리지 매핑
  feature_selection        → interrupt() #4 Human-in-the-loop (purpose 그룹별 분석 항목 선택)
  [END]  ← 이후 노드(feature_extraction 등)는 구현 후 여기에 추가

url_retry 이후 분기 규칙 (_route_after_url_retry):
  state.critical_error 있음 → END               (own_* URL 미검증 등 치명적 오류, 파이프라인 강제 종료)
  state.critical_error 없음 → "feature_url_mapper"

체크포인터: MemorySaver (인메모리)
  - 재시작 시 초기화되지만, 로컬 단일 사용자 환경에서는 충분하다.
  - interrupt() + MemorySaver 조합이 가능하려면 Python 프로세스가
    장기 실행 상태여야 한다. → api.py의 uvicorn 서버가 이를 보장한다.
  - 향후 내구성이 필요하면 SqliteSaver 로 교체한다.
"""

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from server.graph.nodes.competitor_discovery_node import competitor_discovery_node
from server.graph.nodes.competitor_selection_node import competitor_selection_node
from server.graph.nodes.domain_modeling_node import domain_modeling_node
from server.graph.nodes.feature_selection_node import feature_selection_node
from server.graph.nodes.feature_url_mapper_node import feature_url_mapper_node
from server.graph.nodes.human_review_node import human_review_node
from server.graph.nodes.normalize_competitor_ids_node import normalize_competitor_ids_node
from server.graph.nodes.official_source_resolver_node import official_source_resolver_node
from server.graph.nodes.query_intake_node import query_intake_node
from server.graph.nodes.url_retry_node import url_retry_node
from server.graph.state import DomainAnalysisState


# ── 라우팅 함수 ───────────────────────────────────────────────────────────────

def _route_after_url_retry(state: DomainAnalysisState) -> str:
    """
    url_retry_node 이후 분기를 결정한다.

    critical_error가 설정된 경우 (own_* URL 미검증 등):
      → "end" : 파이프라인을 즉시 종료. 후속 분석 노드를 실행하지 않는다.
        신뢰할 수 없는 분석 결과는 분석을 하지 않는 것보다 치명적이다.

    critical_error가 없는 경우:
      → "feature_url_mapper" : FeatureUrlMapperAgent로 진행해 feature 정의 + URL 커버리지 매핑.
    """
    if state.get("critical_error"):
        return "end"
    return "feature_url_mapper"


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
    builder.add_node("feature_url_mapper",       feature_url_mapper_node)
    builder.add_node("feature_selection",        feature_selection_node)
    # TODO: 아래 노드는 구현 후 주석 해제
    # builder.add_node("feature_extraction",       feature_extraction_node)
    # builder.add_node("feature_comparison",       feature_comparison_node)
    # builder.add_node("youtube_query_planner",    youtube_query_planner_node)
    # builder.add_node("youtube_collection",       youtube_collection_node)
    # builder.add_node("reaction_analysis",        reaction_analysis_node)
    # builder.add_node("insight_report",           insight_report_node)

    # ── 엣지 연결 ─────────────────────────────────────────────────────────
    builder.add_edge(START,                        "query_intake")
    builder.add_edge("query_intake",               "human_review")
    builder.add_edge("human_review",               "competitor_discovery")
    builder.add_edge("competitor_discovery",       "domain_modeling")
    builder.add_edge("domain_modeling",            "normalize_competitor_ids")
    builder.add_edge("normalize_competitor_ids",   "competitor_selection")
    builder.add_edge("competitor_selection",       "official_source_resolver")
    builder.add_edge("official_source_resolver",   "url_retry")

    # url_retry 이후: critical_error 유무로 분기
    #   "end"                → END              (critical_error 있음: 파이프라인 강제 종료)
    #   "feature_url_mapper" → feature_url_mapper (정상 진행)
    builder.add_conditional_edges(
        "url_retry",
        _route_after_url_retry,
        {"end": END, "feature_url_mapper": "feature_url_mapper"},
    )

    builder.add_edge("feature_url_mapper",        "feature_selection")
    # feature_selection 이후: 현재 임시 END
    # TODO: feature_extraction 구현 후 아래 엣지로 교체
    builder.add_edge("feature_selection",         END)

    # TODO: 노드 추가 시 아래와 같이 엣지 이어붙이기
    # builder.add_edge("feature_selection",         "feature_extraction")
    # builder.add_edge("feature_extraction",        "feature_comparison")
    # builder.add_edge("feature_comparison",        "youtube_query_planner")
    # builder.add_edge("youtube_query_planner",     "youtube_collection")
    # builder.add_edge("youtube_collection",        "reaction_analysis")
    # builder.add_edge("reaction_analysis",         "insight_report")
    # builder.add_edge("insight_report",            END)

    # ── 컴파일 ────────────────────────────────────────────────────────────
    # interrupt()를 노드 내부에서 직접 호출하므로 interrupt_before 설정 불필요.
    checkpointer = MemorySaver()
    return builder.compile(checkpointer=checkpointer)


# 모듈 임포트 시 한 번만 빌드한다.
# api.py 가 `from server.graph.graph import compiled_graph` 로 참조한다.
compiled_graph = build_graph()
