"""
server/graph/nodes/feature_mapping_official_node.py (v0.10.27)
--------------------------------------------------------------
5중 fan-out (2차) 의 source-type 1번 — official 통합 노드.

역할
----
official_urls_by_candidate (carry + subpage) 입력으로 다음 3종 report_type 의 feature
× candidate × URL 커버리지 매핑:
  - comparison_matrix
  - battlecard (A Fact 부분)
  - market_context_swot (규제 부분)

내부 동작은 `_feature_mapping_runner.run_source_mapping(source="official", ...)` 가
처리. 본 파일은 LangGraph add_node 등록용 thin wrapper.

위치 (v0.10.27 토폴로지)
------------------------
cross_reference
        ↓
   5중 fan-out (2차):
     ├─→ [feature_mapping_official_node]   ← 이 노드 (v0.10.27 신설)
     ├─→ feature_mapping_blog_community_node
     ├─→ feature_mapping_youtube_reactions_node
     ├─→ feature_mapping_owned_channels_node
     └─→ feature_mapping_macro_node
        ↓  list-fan-in barrier (2차)
   additional_urls_validation_node
"""

from server.graph.state import DomainAnalysisState
from server.graph.nodes._feature_mapping_runner import run_source_mapping


def feature_mapping_official_node(
    state: DomainAnalysisState, config: dict | None = None,
) -> dict:
    """v0.10.27 — official source 단일 wrapper."""
    return run_source_mapping(source="official", state=state, config=config)
