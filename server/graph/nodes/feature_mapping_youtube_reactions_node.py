"""
[DEPRECATED — youtube_collection_redesign.md Phase 3]

graph.py 에서 import·add_node·add_edge 모두 제거됨. 파일 삭제 예정.
youtube_reactions feature mapping 단계는 폐기되었음.
"""

from server.graph.state import DomainAnalysisState
from server.graph.nodes._feature_mapping_runner import run_source_mapping


def feature_mapping_youtube_reactions_node(
    state: DomainAnalysisState, config: dict | None = None,
) -> dict:
    """v0.10.27 — youtube_reactions source 단일 wrapper."""
    return run_source_mapping(source="youtube_reactions", state=state, config=config)
