"""
server/graph/nodes/feature_mapping_owned_channels_node.py (v0.10.27)
--------------------------------------------------------------------
5중 fan-out (2차) 의 source-type 4번 — owned_channels 통합 노드.

역할
----
owned_channel_urls_by_candidate (6 platforms × candidate) 입력으로 다음 2종
report_type 의 feature × candidate × 운영 채널 URL 커버리지 매핑:
  - marketing_social
  - battlecard (광고 카피 부분)

내부 동작은 `_feature_mapping_runner.run_source_mapping(source="owned_channels", ...)`
가 처리. 본 파일은 LangGraph add_node 등록용 thin wrapper.
"""

from server.graph.state import DomainAnalysisState
from server.graph.nodes._feature_mapping_runner import run_source_mapping


def feature_mapping_owned_channels_node(
    state: DomainAnalysisState, config: dict | None = None,
) -> dict:
    """v0.10.27 — owned_channels source 단일 wrapper."""
    return run_source_mapping(source="owned_channels", state=state, config=config)
