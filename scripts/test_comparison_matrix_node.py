"""
test_comparison_matrix_node.py
-------------------------------
comparison_matrix_node 단위 테스트 (CM-D1~CM-D5).

설계 근거: docs/design/comparison_matrix_node_design.md §7 검증 계획
LLM 비호출 — analyzer 는 fake 주입. 캐시는 임시 경로로 격리.

실행: python -m pytest scripts/test_comparison_matrix_node.py -q
"""

import copy
import json

import pytest

import server.graph.agent_cache as agent_cache
from server.graph.nodes.comparison_matrix_node import (
    _compute_rubric_score,
    _sanitize_llm_output,
    build_feature_table,
    comparison_matrix_node,
)


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_cache, "AGENT_OUTPUT_CACHE_DIR", tmp_path)


def _cell(value="값", numeric=None, unit="", as_of="", status="explicit",
          url="https://own.example.com/page", promo=False, until=""):
    return {"value": value, "value_numeric": numeric, "unit": unit, "as_of": as_of,
            "extraction_status": status, "evidence": "근거", "source_url": url,
            "source_origin": "official_subpage", "confidence": 0.9,
            "is_promotional": promo, "valid_until": until}


def _base_state() -> dict:
    return {
        "selected_purposes": ["comparison_matrix"],
        "selected_feature_ids": ["feat_fee", "feat_benefit"],
        "selected_competitor_ids": ["comp_a"],
        "own_product": {"product_id": "own_x", "name": "자사 카드"},
        "product_profiles": [
            {"candidate_id": "own_x", "product_name": "자사 카드"},
            {"candidate_id": "comp_a", "product_name": "경쟁 카드 A"},
        ],
        "domain_taxonomy": {"report_config": {"comparison_matrix": {
            "active": True, "label": "비교 매트릭스",
            "features": ["feat_fee", "feat_benefit"],
            "feature_labels": {"feat_fee": "수수료", "feat_benefit": "혜택"},
            "categories": ["Pricing", "Additional Benefit"],
        }}},
        "feature_pool": {
            "feat_fee": {
                "own_x":  _cell("무료", numeric=0, unit="%", as_of="2026-05"),
                "comp_a": _cell("0.5%", numeric=0.5, unit="%", as_of="",
                                url="https://comp.example.com/fee"),
            },
            "feat_benefit": {
                "own_x":  _cell("캐시백 2%", promo=True, until="2026-09-30",
                                status="partial"),
                "comp_a": _cell("", status="not_found", url=""),
            },
        },
    }


class FakeAnalyzer:
    model = "fake-cli"

    def __init__(self, inject_hallucination=False):
        self.calls = 0
        self.inject = inject_hallucination

    def call_with_schema(self, prompt, output_schema):
        self.calls += 1
        payload = json.loads(prompt.split("```json\n", 1)[1].rsplit("\n```", 1)[0])
        fids = [c["feature_id"] for c in payload["feature_table"]["columns"]]
        out = {
            "zone_summary": {
                "winning":  [{"feature_id": fids[0], "rationale": "무료 vs 0.5%"}],
                "battling": [{"feature_id": fids[1], "rationale": "비교 대상 미확인"}],
                "losing":   [],
                "overall_comment": "수수료 영역 우위.",
            },
            "harvey_balls": [{"feature_id": fids[1], "legend": "4=상시 다수, 1=한정",
                              "ratings": {"own_x": 3},
                              "interpretation": "자사가 가장 충실(◕)."}],
            "use_case_weights": [],
            "warnings": [],
        }
        if self.inject:
            out["zone_summary"]["winning"].append(
                {"feature_id": "feat_ghost", "rationale": "환각"})
            out["harvey_balls"].append(
                {"feature_id": fids[0], "legend": "x",
                 "ratings": {"comp_ghost": 4, "own_x": 2},
                 "interpretation": "환각 항목."})
        return out


# ─── §7-1·2: 표 구성·표기 규칙·AP 자동 경고 ──────────────────────────────────

class TestFeatureTable:
    def _build(self, state):
        entry = state["domain_taxonomy"]["report_config"]["comparison_matrix"]
        return build_feature_table(
            state["feature_pool"], entry, state["selected_feature_ids"],
            "own_x", {"own_x": "자사 카드", "comp_a": "경쟁 카드 A"})

    def test_own_first_and_display_rules(self):
        table, promos, traps = self._build(_base_state())
        assert table["rows"][0]["candidate_id"] == "own_x"        # own 첫 행
        comp_cells = table["rows"][1]["cells"]
        assert comp_cells["feat_benefit"]["display"] == "미확인"   # not_found 표기
        own_benefit = table["rows"][0]["cells"]["feat_benefit"]
        assert "[기간한정 ~2026-09-30]" in own_benefit["display"]  # AP-1 표기
        assert own_benefit["footnote_refs"] == [1]
        assert promos[0]["valid_until"] == "2026-09-30"

    def test_manual_check_display(self):
        state = _base_state()
        state["feature_pool"]["feat_fee"]["comp_a"]["extraction_status"] = \
            "requires_manual_check"
        table, _, _ = self._build(state)
        cell = table["rows"][1]["cells"]["feat_fee"]
        assert cell["manual_check_required"] is True
        assert "(수동 검토 필요)" in cell["display"]

    def test_auto_trap_warnings(self):
        table, _, traps = self._build(_base_state())
        # AP-3: comp_a 수수료는 정량인데 as_of 없음
        assert any("AP-3" in t and "comp_a" in t and "feat_fee" in t for t in traps)
        # AP-2: own 혜택은 partial 인데... 절대 단어 미포함 → 추가 케이스로 확인
        state = _base_state()
        state["feature_pool"]["feat_fee"]["own_x"]["extraction_status"] = "partial"
        _, _, traps2 = self._build(state)
        assert any("AP-2" in t and "own_x" in t for t in traps2)

    def test_deterministic(self):
        t1 = self._build(_base_state())
        t2 = self._build(copy.deepcopy(_base_state()))
        assert t1 == t2


# ─── §7-3: LLM 출력 가드 ─────────────────────────────────────────────────────

class TestSanitize:
    def test_hallucinated_ids_removed(self):
        state = _base_state()
        out = comparison_matrix_node(state, analyzer=FakeAnalyzer(inject_hallucination=True))
        content = out["report_outputs"]["comparison_matrix"]["content"]
        zone_fids = [z["feature_id"] for z in content["zone_summary"]["winning"]]
        assert "feat_ghost" not in zone_fids
        for h in content["harvey_balls"]:
            assert "comp_ghost" not in h["ratings"]


# ─── §7-4~6: 캐시·degrade·skip ───────────────────────────────────────────────

class TestNode:
    def test_envelope_structure_and_completed(self):
        out = comparison_matrix_node(_base_state(), analyzer=FakeAnalyzer())
        env = out["report_outputs"]["comparison_matrix"]
        assert env["evaluation_score"] == 3
        assert env["content"]["feature_table"]["columns"]
        assert env["source_references"]                       # 출처 집계
        assert out["agent_steps"][0]["status"] == "completed"

    def test_cache_hit_second_run(self):
        a1, a2 = FakeAnalyzer(), FakeAnalyzer()
        comparison_matrix_node(_base_state(), analyzer=a1)
        comparison_matrix_node(_base_state(), analyzer=a2)
        assert a1.calls == 1 and a2.calls == 0                # §9-3 사상

    def test_feature_pool_change_invalidates_cache(self):
        comparison_matrix_node(_base_state(), analyzer=FakeAnalyzer())
        state = _base_state()
        state["feature_pool"]["feat_fee"]["comp_a"]["value"] = "0.7%"
        a2 = FakeAnalyzer()
        comparison_matrix_node(state, analyzer=a2)
        assert a2.calls == 1

    def test_degrade_on_llm_failure(self):
        class BoomAnalyzer(FakeAnalyzer):
            def call_with_schema(self, prompt, output_schema):
                raise RuntimeError("CLI down")

        out = comparison_matrix_node(_base_state(), analyzer=BoomAnalyzer())
        env = out["report_outputs"]["comparison_matrix"]
        assert out["agent_steps"][0]["status"] == "completed"  # fail 아님 (CM-D5)
        assert env["content"]["zone_summary"]["winning"] == []
        assert env["evaluation_score"] in (2, 3)
        assert any("degrade" in w for w in env["warnings"])
        assert out["errors"]

    def test_skip_when_inactive(self):
        state = _base_state()
        state["domain_taxonomy"]["report_config"]["comparison_matrix"]["active"] = False
        out = comparison_matrix_node(state, analyzer=FakeAnalyzer())
        assert out["agent_steps"][0]["status"] == "skipped"
        assert "report_outputs" not in out

    def test_error_when_feature_pool_empty(self):
        state = _base_state()
        state["feature_pool"] = {}
        out = comparison_matrix_node(state, analyzer=FakeAnalyzer())
        assert out["agent_steps"][0]["status"] == "failed"


class TestRubricScore:
    """CM-D6 — 점수는 코드가 결정론적으로 채점 (LLM 자기평가 폐기, 표류 방지)."""

    def _table(self, state):
        entry = state["domain_taxonomy"]["report_config"]["comparison_matrix"]
        return build_feature_table(
            state["feature_pool"], entry, state["selected_feature_ids"], "own_x", {})

    def test_score_2_when_unit_missing(self):
        state = _base_state()
        state["feature_pool"]["feat_fee"]["comp_a"]["unit"] = ""
        table, promos, traps = self._table(state)
        score, rationale = _compute_rubric_score(table, [], promos, traps)
        assert score == 2 and "단위" in rationale

    def test_score_3_without_weights(self):
        table, promos, traps = self._table(_base_state())
        score, rationale = _compute_rubric_score(table, [], promos, traps)
        assert score == 3 and "가중치 부재" in rationale

    def test_score_4_with_weights_but_missing_asof(self):
        # comp_a 수수료 셀은 as_of "" → 5점 미달
        table, promos, traps = self._table(_base_state())
        weights = [{"use_case": "단기 여행자", "weights": {"feat_fee": 1.0}}]
        score, rationale = _compute_rubric_score(table, weights, promos, traps)
        assert score == 4 and "as_of" in rationale

    def test_score_5_when_all_requirements_met(self):
        state = _base_state()
        state["feature_pool"]["feat_fee"]["comp_a"]["as_of"] = "2026-05"
        table, promos, traps = self._table(state)
        weights = [{"use_case": "단기 여행자", "weights": {"feat_fee": 1.0}}]
        score, _ = _compute_rubric_score(table, weights, promos, traps)
        assert score == 5

    def test_deterministic(self):
        table, promos, traps = self._table(_base_state())
        assert _compute_rubric_score(table, [], promos, traps) == \
               _compute_rubric_score(table, [], promos, traps)
