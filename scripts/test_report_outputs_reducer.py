"""
test_report_outputs_reducer.py
-------------------------------
report_outputs merge reducer (CM-D3) 회귀 테스트.

검증: 병렬 분기의 두 리포트 노드가 각자 자기 키만 반환해도 LangGraph 가
merge_report_outputs 로 병합하여 두 키가 모두 보존된다 (기본 replace 였다면
InvalidUpdateError 또는 덮어쓰기 발생).

실행: python -m pytest scripts/test_report_outputs_reducer.py -q
"""

from langgraph.graph import END, START, StateGraph

from server.graph.state import DomainAnalysisState, merge_report_outputs


def test_merge_function_semantics():
    assert merge_report_outputs(None, {"a": 1}) == {"a": 1}
    assert merge_report_outputs({"a": 1}, None) == {"a": 1}
    # 동일 키 재작성 시 우측(최신) 우선
    assert merge_report_outputs({"a": 1, "b": 2}, {"b": 3}) == {"a": 1, "b": 3}


def test_parallel_report_nodes_merge_without_conflict():
    """START 에서 fan-out 한 두 리포트 노드의 동시 write 가 병합되는지 (CM-D3 핵심)."""

    def report_a(state):
        return {"report_outputs": {"comparison_matrix": {"content": "A"}}}

    def report_b(state):
        return {"report_outputs": {"reaction_insight": {"content": "B"}}}

    builder = StateGraph(DomainAnalysisState)
    builder.add_node("report_a", report_a)
    builder.add_node("report_b", report_b)
    builder.add_edge(START, "report_a")
    builder.add_edge(START, "report_b")          # 병렬 fan-out (동일 super-step)
    builder.add_edge(["report_a", "report_b"], END)

    result = builder.compile().invoke({})
    outputs = result["report_outputs"]
    assert outputs["comparison_matrix"] == {"content": "A"}
    assert outputs["reaction_insight"] == {"content": "B"}


def test_sequential_update_preserves_existing_keys():
    """후속 노드가 자기 키만 반환해도 기존 리포트 키가 유실되지 않는다."""

    def first(state):
        return {"report_outputs": {"comparison_matrix": {"v": 1}}}

    def second(state):
        return {"report_outputs": {"battlecard": {"v": 2}}}

    builder = StateGraph(DomainAnalysisState)
    builder.add_node("first", first)
    builder.add_node("second", second)
    builder.add_edge(START, "first")
    builder.add_edge("first", "second")
    builder.add_edge("second", END)

    outputs = builder.compile().invoke({})["report_outputs"]
    assert set(outputs) == {"comparison_matrix", "battlecard"}
