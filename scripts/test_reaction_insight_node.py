"""
test_reaction_insight_node.py
------------------------------
reaction_insight 노드 단위 테스트 (집계·가중치 RI-D7·루브릭 RI-D6·degrade).

LLM 비호출 — analyzer fake 주입. 캐시는 임시 경로 격리.
실행: python -m pytest scripts/test_reaction_insight_node.py -q
"""

import json

import pytest

import server.graph.agent_cache as agent_cache
from server.graph.nodes.reaction_insight_node import (
    build_aspect_matrix,
    build_suggestions,
    build_timeline,
    compute_rubric,
    reaction_insight_node,
    select_top_quotes,
)


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_cache, "AGENT_OUTPUT_CACHE_DIR", tmp_path)


def _tuple(aspect, polarity, intensity, channel, quote="원문 인용",
           posted_at="2026-05-10T00:00:00Z", is_suggestion=False):
    return {"aspect": aspect, "polarity": polarity, "intensity": intensity,
            "quote": quote, "source_url": f"https://src.example.com/{channel}",
            "channel": channel, "posted_at": posted_at,
            "is_suggestion": is_suggestion}


def _base_state():
    return {
        "selected_purposes": ["reaction_insight"],
        "own_product": {"product_id": "own_x"},
        "domain_taxonomy": {"report_config": {"reaction_insight": {
            "active": True, "label": "고객 반응 인사이트",
            "categories": ["Pricing Perception"],
            "aspect_codebook": [
                {"aspect_id": "fee", "label": "수수료 체감", "definition": ""},
                {"aspect_id": "ux", "label": "앱 UX", "definition": ""},
            ],
        }}},
        "reaction_analysis": {
            "own_x": {
                "tuples": [
                    _tuple("fee", "positive", 3, "youtube", quote="수수료 없어 좋아요"),
                    _tuple("fee", "negative", 1, "community", quote="재환전은 아쉽다"),
                    _tuple("ux", "negative", 2, "youtube", quote="앱이 튕겨요",
                           is_suggestion=True),
                ],
                "channel_counts": {"youtube": 2, "community": 1},
                "sample_size": 3, "collected_at": "2026-06-06T00:00:00Z",
                "dropped_by_guard": 0,
            },
            "comp_a": {
                "tuples": [_tuple("fee", "positive", 2, "youtube", quote="괜찮은 편")],
                "channel_counts": {"youtube": 1, "community": 0},
                "sample_size": 1, "collected_at": "2026-06-06T00:00:00Z",
                "dropped_by_guard": 0,
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
        out = {
            "aspect_insights": [
                {"aspect": "fee", "headline": "수수료는 강점",
                 "narrative": "긍정 우세 (가중 +0.66)."},
            ],
            "overall_summary": "수수료 칭찬, 앱 안정성 불만.",
            "warnings": [],
        }
        if self.inject:
            out["aspect_insights"].append(
                {"aspect": "ghost", "headline": "환각", "narrative": "없는 항목"})
        return out


# ─── 집계 (결정론) ───────────────────────────────────────────────────────────

class TestAggregation:
    def test_weighted_sentiment(self):
        """RI-D7 가중 검증 — fee/own_x: (+1*3*1.0 + -1*1*0.9) / (3*1.0 + 1*0.9) = 0.538."""
        matrix = build_aspect_matrix(_base_state()["reaction_analysis"])
        cell = matrix["fee"]["own_x"]
        assert cell["positive"] == 1 and cell["negative"] == 1
        assert cell["tuple_count"] == 2
        assert cell["weighted_sentiment"] == round((3.0 - 0.9) / (3.0 + 0.9), 3)

    def test_top_quotes_prefer_intensity_then_channel_weight(self):
        quotes = select_top_quotes(_base_state()["reaction_analysis"])
        fee_pos = next(q for q in quotes
                       if q["aspect"] == "fee" and q["polarity"] == "positive")
        assert fee_pos["quote"] == "수수료 없어 좋아요"   # intensity 3 > 2

    def test_suggestions_and_timeline(self):
        ra = _base_state()["reaction_analysis"]
        sugg = build_suggestions(ra)
        assert len(sugg) == 1 and sugg[0]["aspect"] == "ux"
        # v0.13.2 — candidate별 분리 (UI 드롭다운 필터)
        timeline = build_timeline(ra)
        assert timeline["own_x"]["2026-05"]["count"] == 3
        assert timeline["comp_a"]["2026-05"]["count"] == 1


class TestRubric:
    def test_score_5_when_all_met(self):
        score, _ = compute_rubric(_base_state()["reaction_analysis"],
                                  [{"aspect": "ux"}])
        assert score == 5

    def test_score_4_without_suggestion(self):
        ra = _base_state()["reaction_analysis"]
        for r in ra.values():
            for t in r["tuples"]:
                t["is_suggestion"] = False
        score, rationale = compute_rubric(ra, [])
        assert score == 4 and "suggestion 0건" in rationale

    def test_score_3_single_channel(self):
        ra = _base_state()["reaction_analysis"]
        for r in ra.values():
            r["tuples"] = [t for t in r["tuples"] if t["channel"] == "youtube"]
        score, _ = compute_rubric(ra, [])
        assert score == 3

    def test_score_2_no_tuples(self):
        score, _ = compute_rubric({"own_x": {"tuples": []}}, [])
        assert score == 2


# ─── 노드 ────────────────────────────────────────────────────────────────────

class TestNode:
    def test_envelope_and_code_score(self):
        out = reaction_insight_node(_base_state(), analyzer=FakeAnalyzer())
        env = out["report_outputs"]["reaction_insight"]
        assert out["agent_steps"][0]["status"] == "completed"
        assert env["evaluation_score"] == 5                     # 코드 채점 (RI-D6)
        assert env["content"]["channel_weights"] == {"youtube": 1.0, "community": 0.9}
        assert env["content"]["aspect_matrix"]["fee"]["own_x"]["tuple_count"] == 2
        assert env["content"]["channel_meta"]["own_x"]["sample_size"] == 3   # AP-3
        assert env["source_references"]
        # merge reducer 계약 — 자기 키만 반환
        assert set(out["report_outputs"]) == {"reaction_insight"}

    def test_hallucinated_aspect_insight_removed(self):
        out = reaction_insight_node(
            _base_state(), analyzer=FakeAnalyzer(inject_hallucination=True))
        insights = out["report_outputs"]["reaction_insight"]["content"]["aspect_insights"]
        assert all(i["aspect"] != "ghost" for i in insights)

    def test_cache_hit_second_run(self):
        a1, a2 = FakeAnalyzer(), FakeAnalyzer()
        reaction_insight_node(_base_state(), analyzer=a1)
        reaction_insight_node(_base_state(), analyzer=a2)
        assert a1.calls == 1 and a2.calls == 0

    def test_degrade_on_llm_failure(self):
        class BoomAnalyzer(FakeAnalyzer):
            def call_with_schema(self, prompt, output_schema):
                raise RuntimeError("CLI down")

        out = reaction_insight_node(_base_state(), analyzer=BoomAnalyzer())
        env = out["report_outputs"]["reaction_insight"]
        assert out["agent_steps"][0]["status"] == "completed"
        assert env["content"]["aspect_insights"] == []
        assert env["content"]["aspect_matrix"]                  # 집계는 유지
        assert env["evaluation_score"] == 5                     # 점수는 코드 채점이라 불변
        assert any("degrade" in w for w in env["warnings"])

    def test_skip_and_missing_input(self):
        state = _base_state()
        state["domain_taxonomy"]["report_config"]["reaction_insight"]["active"] = False
        assert reaction_insight_node(state, analyzer=FakeAnalyzer()) \
            ["agent_steps"][0]["status"] == "skipped"
        state2 = _base_state()
        state2["reaction_analysis"] = {}
        assert reaction_insight_node(state2, analyzer=FakeAnalyzer()) \
            ["agent_steps"][0]["status"] == "failed"
