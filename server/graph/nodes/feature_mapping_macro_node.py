"""
server/graph/nodes/feature_mapping_macro_node.py (v0.10.27)
-----------------------------------------------------------
5중 fan-out (2차) 의 source-type 5번 — macro 통합 노드.

역할
----
macro_urls_by_candidate (candidate_id='macro' 단일 키 + Tier 화이트리스트) 입력으로
다음 report_type 의 feature × URL 커버리지 매핑:
  - market_context_swot (매크로 부분)

내부 동작은 `_feature_mapping_runner.run_source_mapping(source="macro", ...)` 가
처리. 본 파일은 LangGraph add_node 등록용 thin wrapper.
"""

from server.graph.state import DomainAnalysisState
from server.graph.nodes._feature_mapping_runner import run_source_mapping


def feature_mapping_macro_node(
    state: DomainAnalysisState, config: dict | None = None,
) -> dict:
    """v0.10.27 — macro source 단일 wrapper."""
    return run_source_mapping(source="macro", state=state, config=config)
