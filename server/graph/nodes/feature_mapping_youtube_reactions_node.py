"""
server/graph/nodes/feature_mapping_youtube_reactions_node.py (v0.10.27)
----------------------------------------------------------------------
5중 fan-out (2차) 의 source-type 3번 — youtube_reactions 통합 노드.

역할
----
youtube_reactions_urls_by_candidate (cross_reference 후처리 후 — owned channel 영상
제외) 입력으로 다음 report_type 의 feature × candidate × YouTube 영상 커버리지 매핑:
  - reaction_insight

내부 동작은 `_feature_mapping_runner.run_source_mapping(source="youtube_reactions", ...)`
가 처리. 본 파일은 LangGraph add_node 등록용 thin wrapper.
"""

from server.graph.state import DomainAnalysisState
from server.graph.nodes._feature_mapping_runner import run_source_mapping


def feature_mapping_youtube_reactions_node(
    state: DomainAnalysisState, config: dict | None = None,
) -> dict:
    """v0.10.27 — youtube_reactions source 단일 wrapper."""
    return run_source_mapping(source="youtube_reactions", state=state, config=config)
