"""
test_reaction_analysis.py
--------------------------
reaction_analysis 노드 단위 테스트 (ABSA 입력 조립·환각 가드·캐시·부분 실패).

LLM 비호출 — analyzer fake 주입. 캐시는 임시 경로 격리.
실행: python -m pytest scripts/test_reaction_analysis.py -q
"""

import json
from pathlib import Path

import pytest
from jsonschema import validate

import server.graph.agent_cache as agent_cache
from server.config import AGENTS_DIR
from server.graph.nodes.reaction_analysis_node import (
    build_absa_inputs,
    reaction_analysis_node,
    sanitize_tuples,  # noqa: F401 — 공개 계약 존재 확인
)

_OUTPUT_SCHEMA = json.loads(
    (Path(AGENTS_DIR) / "reaction_analysis" / "output.schema.json").read_text())


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_cache, "AGENT_OUTPUT_CACHE_DIR", tmp_path)


def _base_state():
    return {
        "selected_purposes": ["reaction_insight"],
        "domain_taxonomy": {"report_config": {"reaction_insight": {
            "active": True,
            "aspect_codebook": [
                {"aspect_id": "fee_perception", "label": "수수료 체감",
                 "definition": "수수료에 대한 사용자 인식"},
                {"aspect_id": "app_ux", "label": "앱 UX", "definition": ""},
            ],
        }}},
        "collected_videos": [
            {"video_id": "vidA0000000", "candidate_id": "own_x",
             "url": "https://www.youtube.com/watch?v=vidA0000000"},
        ],
        "selected_comments": [
            {"video_id": "vidA0000000", "candidate_id": "own_x",
             "comment_id": "c1", "text": "환전 수수료 진짜 없어서 좋아요",
             "like_count": 9, "published_at": "2026-06-01T00:00:00Z"},
            {"video_id": "vidA0000000", "candidate_id": "own_x",
             "comment_id": "c2", "text": "앱이 자꾸 튕겨서 불편합니다 개선해 주세요",
             "like_count": 3, "published_at": "2026-06-02T00:00:00Z"},
        ],
        "community_posts": [
            {"url": "https://blog.example.com/p1", "candidate_id": "own_x",
             "feature_ids": ["feat_fee"], "domain_class": "personal_blog",
             "title": "한 달 사용기", "body_excerpt": "재환전 수수료가 아쉬웠다",
             "published_at": "2026-05-20", "fetch_status": "ok"},
        ],
        "product_profiles": [{"candidate_id": "own_x", "product_name": "자사 카드"}],
    }


class FakeAnalyzer:
    model = "fake-cli"

    def __init__(self, inject_hallucination=False):
        self.calls = 0
        self.inject = inject_hallucination

    def call_with_schema(self, prompt, output_schema):
        self.calls += 1
        payload = json.loads(prompt.split("```json\n", 1)[1].rsplit("\n```", 1)[0])
        yt = next(i for i in payload["items"] if i["channel"] == "youtube")
        cm = next(i for i in payload["items"] if i["channel"] == "community")
        out = {
            "candidate_id": payload["candidate_id"],
            "tuples": [
                {"aspect": "fee_perception", "polarity": "positive", "intensity": 2,
                 "quote": "환전 수수료 진짜 없어서 좋아요",
                 "source_url": yt["source_url"], "channel": "youtube",
                 "posted_at": yt["posted_at"], "is_suggestion": False},
                {"aspect": "app_ux", "polarity": "negative", "intensity": 2,
                 "quote": "앱이 자꾸 튕겨서 불편합니다",
                 "source_url": yt["source_url"], "channel": "youtube",
                 "posted_at": "", "is_suggestion": True},
                {"aspect": "fee_perception", "polarity": "negative", "intensity": 1,
                 "quote": "재환전 수수료가 아쉬웠다",
                 "source_url": cm["source_url"], "channel": "community",
                 "posted_at": cm["posted_at"], "is_suggestion": False},
            ],
        }
        if self.inject:
            out["tuples"] += [
                # 코드북 외 aspect
                {"aspect": "ghost_aspect", "polarity": "neutral", "intensity": 1,
                 "quote": "환전 수수료 진짜 없어서 좋아요", "source_url": yt["source_url"],
                 "channel": "youtube", "posted_at": "", "is_suggestion": False},
                # 입력에 없는 출처
                {"aspect": "fee_perception", "polarity": "neutral", "intensity": 1,
                 "quote": "환전 수수료 진짜 없어서 좋아요",
                 "source_url": "https://fake.example.com", "channel": "community",
                 "posted_at": "", "is_suggestion": False},
                # 비실존 quote (합성)
                {"aspect": "fee_perception", "polarity": "positive", "intensity": 3,
                 "quote": "수수료가 전 세계 최저 수준이라 감동했어요",
                 "source_url": yt["source_url"], "channel": "youtube",
                 "posted_at": "", "is_suggestion": False},
            ]
        return out


class TestBuildInputs:
    def test_two_channels_merged_per_candidate(self):
        inputs = build_absa_inputs(_base_state())
        items = inputs["own_x"]
        assert {i["channel"] for i in items} == {"youtube", "community"}
        assert len(items) == 3
        yt = [i for i in items if i["channel"] == "youtube"]
        assert all("watch?v=vidA0000000" in i["source_url"] for i in yt)

    def test_gate(self):
        state = _base_state()
        state["selected_purposes"] = ["comparison_matrix"]
        assert build_absa_inputs(state) == {}


class TestSanitize:
    def test_hallucinations_dropped(self):
        state = _base_state()
        out = reaction_analysis_node(state, analyzer=FakeAnalyzer(inject_hallucination=True))
        result = out["reaction_analysis"]["own_x"]
        aspects = {t["aspect"] for t in result["tuples"]}
        quotes = {t["quote"] for t in result["tuples"]}
        urls = {t["source_url"] for t in result["tuples"]}
        assert "ghost_aspect" not in aspects
        assert "https://fake.example.com" not in urls
        assert "수수료가 전 세계 최저 수준이라 감동했어요" not in quotes
        assert result["dropped_by_guard"] == 3
        assert len(result["tuples"]) == 3                      # 정상 3건 유지


class TestNode:
    def test_full_run_with_meta(self):
        out = reaction_analysis_node(_base_state(), analyzer=FakeAnalyzer())
        assert out["agent_steps"][0]["status"] == "completed"
        result = out["reaction_analysis"]["own_x"]
        assert result["sample_size"] == 3                      # AP-3
        assert result["channel_counts"] == {"youtube": 2, "community": 1}
        assert any(t["is_suggestion"] for t in result["tuples"])
        # 출력이 agent schema 를 만족하는지 (tuples 단위)
        validate({"candidate_id": "own_x", "tuples": result["tuples"]}, _OUTPUT_SCHEMA)

    def test_cache_hit_second_run(self):
        a1, a2 = FakeAnalyzer(), FakeAnalyzer()
        reaction_analysis_node(_base_state(), analyzer=a1)
        reaction_analysis_node(_base_state(), analyzer=a2)
        assert a1.calls == 1 and a2.calls == 0

    def test_partial_failure(self):
        class BoomAnalyzer(FakeAnalyzer):
            def call_with_schema(self, prompt, output_schema):
                raise RuntimeError("CLI down")

        out = reaction_analysis_node(_base_state(), analyzer=BoomAnalyzer())
        assert out["agent_steps"][0]["status"] == "completed"
        assert out["reaction_analysis"] == {}
        assert out["errors"]

    def test_skip_without_codebook(self):
        state = _base_state()
        state["domain_taxonomy"]["report_config"]["reaction_insight"]["aspect_codebook"] = []
        out = reaction_analysis_node(state, analyzer=FakeAnalyzer())
        assert out["agent_steps"][0]["status"] == "failed"
