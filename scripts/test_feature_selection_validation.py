"""
test_feature_selection_validation.py
-------------------------------------
feature_selection_node 재개 검증 회귀 테스트.

회귀 대상 (2026-06-05 수정): B-only(positioning_map·executive_summary)·marketing_social
카드의 feature 는 domain_taxonomy 에서 파생되어 analysis_features 에 없으므로,
검증을 analysis_features 만으로 하면 정상 선택값이 오탐된다. 검증은 interrupt payload
(reports_payload) 전체를 기준으로 해야 한다.

실행: python -m pytest scripts/test_feature_selection_validation.py -q
"""

from langgraph.types import Command

from server.graph.nodes.feature_selection_node import feature_selection_node


def _state() -> dict:
    """comparison_matrix(흐름 A) + positioning_map(B-only) 혼합 taxonomy/analysis_features."""
    return {
        "domain_taxonomy": {"report_config": {
            "comparison_matrix": {
                "active": True, "source_flow": "A", "label": "비교 매트릭스",
                "features": ["feat_fee"], "feature_labels": {"feat_fee": "수수료"},
            },
            "positioning_map": {
                "active": True, "source_flow": "B", "label": "포지셔닝 맵",
                "features": ["positioning_axis_cost_efficiency"],
                "feature_labels": {"positioning_axis_cost_efficiency": "비용 효율 축"},
            },
        }},
        "analysis_features": [
            {"report_type": "comparison_matrix", "feature_id": "feat_fee",
             "feature_name": "수수료", "description": "수수료율", "priority": "high",
             "candidate_coverage": [
                 {"candidate_id": "comp_a", "coverage": "partial",
                  "existing_urls": [], "additional_urls": []}]},
        ],
    }


def _resume(state, selected_feature_ids, selected_purposes=None):
    """interrupt 통과 후 resume 값을 주입하여 노드를 1회 실행."""
    # 첫 호출: interrupt 발생까지 진행. LangGraph 없이 노드 함수를 직접 부르면
    # interrupt() 가 GraphInterrupt 를 raise 하므로, resume 경로를 직접 검증하기 위해
    # interrupt 를 monkeypatch 로 우회한다.
    import server.graph.nodes.feature_selection_node as mod
    payload = {"selected_feature_ids": selected_feature_ids}
    if selected_purposes is not None:
        payload["selected_purposes"] = selected_purposes
    orig = mod.interrupt
    mod.interrupt = lambda value: payload
    try:
        return feature_selection_node(state)
    finally:
        mod.interrupt = orig


def test_b_only_feature_ids_accepted():
    """B-only feature(positioning_axis_*)가 selected 에 포함돼도 검증 통과."""
    out = _resume(_state(), ["feat_fee", "positioning_axis_cost_efficiency"])
    assert "errors" not in out, out
    assert out["agent_steps"][0]["status"] == "completed"
    assert "feat_fee" in out["selected_feature_ids"]


def test_unknown_feature_id_still_rejected():
    """payload 에 없는 진짜 잘못된 feature_id 는 여전히 거부."""
    out = _resume(_state(), ["feat_fee", "feat_does_not_exist"])
    assert out["agent_steps"][0]["status"] == "failed"
    assert "feat_does_not_exist" in out["errors"][0]["error"]


def test_comparison_only_selection_derives_report():
    """comparison_matrix feature 만 선택 시 selected_purposes 역산에 포함."""
    out = _resume(_state(), ["feat_fee"])
    assert "comparison_matrix" in out["selected_purposes"]
