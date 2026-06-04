"""
server/graph/nodes/feature_mapping_blog_community_node.py (v0.10.27)
--------------------------------------------------------------------
5중 fan-out (2차) 의 source-type 2번 — blog_community 통합 노드.

역할
----
blog_community_urls_by_candidate (공식 도메인 제외 + 화이트리스트 정렬) 입력으로
다음 report_type 의 feature × candidate × URL 커버리지 매핑:
  - reaction_insight

내부 동작은 `_feature_mapping_runner.run_source_mapping(source="blog_community", ...)`
가 처리. 본 파일은 LangGraph add_node 등록용 thin wrapper.
"""

from server.graph.state import DomainAnalysisState
from server.graph.nodes._feature_mapping_runner import run_source_mapping


def feature_mapping_blog_community_node(
    state: DomainAnalysisState, config: dict | None = None,
) -> dict:
    """v0.10.27 — blog_community source 단일 wrapper."""
    return run_source_mapping(source="blog_community", state=state, config=config)
