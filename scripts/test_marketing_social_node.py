"""
scripts/test_marketing_social_node.py (v1.0)
---------------------------------------------
marketing_social 리포트 노드 단위 테스트.
설계: docs/design/marketing_social_node_design.md §5 (MS-D4~D7·D10~D12)

실행: pytest scripts/test_marketing_social_node.py -q
"""

import pytest

from server.graph.nodes.marketing_social_node import (
    build_channels,
    build_coverage_gaps,
    build_engagement,
    build_frequency,
    build_peso_matrix,
    compute_rubric,
    dedup_blog_feeds,
    marketing_social_node,
    month_window,
    prejudge_related,
    product_tokens,
    sanitize_llm_output,
)

_WINDOW = month_window()
_M0, _M1 = _WINDOW[-1], _WINDOW[-2]   # 최신 2개월

_META = {
    "own_a": {
        "channel_url": "https://www.youtube.com/@toss", "channel_id": "UC1",
        "title": "토스", "subscriber_count": 1000, "video_total": 9,
        "recent_videos": [
            {"video_id": "v1", "title": "트래블카드 출시", "published_at": f"{_M0}-01T00:00:00Z",
             "description": "", "view_count": 100, "like_count": 8, "comment_count": 2},
            {"video_id": "v2", "title": "회사 소식", "published_at": f"{_M1}-15T00:00:00Z",
             "description": "채용", "view_count": 200, "like_count": 10, "comment_count": 0},
        ],
    },
}
_FEEDS = [
    {"candidate_id": "comp_b", "platform": "blog_tistory", "blog_url": "https://b.tistory.com",
     "fetch_status": "ok", "posts": [
         {"title": "글A", "published_at": f"{_M0}-02", "link": "https://b/1", "summary": "환전 혜택"},
         {"title": "글B", "published_at": f"{_M1}-05", "link": "https://b/2", "summary": ""},
     ]},
    {"candidate_id": "comp_b", "platform": "blog_self_hosted", "blog_url": "https://blog.b.com",
     "fetch_status": "ok", "posts": [
         {"title": "글A", "published_at": f"{_M0}-02", "link": "https://c/1", "summary": ""},
         {"title": "글B", "published_at": f"{_M1}-05", "link": "https://c/2", "summary": ""},
     ]},
    {"candidate_id": "comp_b", "platform": "blog_naver", "blog_url": "https://blog.naver.com/x",
     "fetch_status": "rss_unavailable", "posts": []},
]
_OWNED = {
    "own_a":  [{"platform": "youtube_official", "url": "https://yt"},
               {"platform": "press_release", "url": "https://pr"}],
    "comp_b": [{"platform": "blog_tistory", "url": "https://b.tistory.com"},
               {"platform": "blog_self_hosted", "url": "https://blog.b.com"},
               {"platform": "instagram", "url": "https://www.instagram.com/b"}],
}


# ─── 순수 함수 ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,brand,expected", [
    ("토스 트래블카드", "토스", ["트래블카드"]),
    ("하나 트래블로그 카드", "하나카드", ["트래블로그"]),
    ("신한 SOL트래블 체크카드", "신한카드", ["sol트래블"]),
    # 브랜드=상품 단일 회사 — 비브랜드 토큰 없으면 브랜드 토큰 유지
    ("트래블월렛 카드", "트래블월렛", ["트래블월렛"]),
])
def test_product_tokens(name, brand, expected):
    assert product_tokens(name, brand) == expected


def test_dedup_blog_feeds_merges_same_blog():
    """MS-D11 — 제목 중복 ≥50% 피드 병합 (신한 tistory↔self_hosted 사례)."""
    deduped = dedup_blog_feeds(_FEEDS)
    assert len(deduped) == 1                       # ok 2건 → 1건 병합 (non-ok 제외)
    assert deduped[0]["merged_platforms"] == ["blog_tistory", "blog_self_hosted"]


def test_build_channels_and_prejudge():
    channels = build_channels(_META, dedup_blog_feeds(_FEEDS))
    assert set(channels) == {"own_a/youtube", "comp_b/blog_tistory"}
    assert channels["own_a/youtube"]["audience_size"] == 1000
    related = prejudge_related(channels, {"own_a": ["트래블카드"], "comp_b": ["환전"]})
    assert related == {"v1", "https://b/1"}        # 제목 / summary 매칭


def test_build_frequency_two_series():
    """MS-D10 — 전체/상품 관련 2계열 + 동일 윈도우."""
    channels = build_channels(_META, dedup_blog_feeds(_FEEDS))
    freq = build_frequency(channels, {"v1"}, _WINDOW)
    yt = freq["own_a/youtube"]
    assert yt["window_total"] == 2 and yt["related_total"] == 1
    assert yt["monthly"][_M0] == {"total": 1, "product_related": 1}
    assert yt["related_ratio"] == 0.5


def test_build_engagement_two_denominators():
    eng = build_engagement(_META)["own_a"]
    assert eng["per_view_median"] == round(((8 + 2) / 100 + (10 + 0) / 200) / 2, 5)
    assert eng["per_subscriber_median"] == round(((8 + 2) / 1000 + 10 / 1000) / 2, 6)
    assert eng["denominators"] == ["view_count", "subscriber_count"]


def test_peso_matrix_and_gaps():
    peso = build_peso_matrix(_OWNED, _META, dedup_blog_feeds(_FEEDS))
    assert peso["own_a"]["youtube_official"] == "measured"
    assert peso["own_a"]["press_release"] == "presence_only"      # MS-D12
    assert peso["comp_b"]["blog_tistory"] == "measured"
    assert peso["comp_b"]["blog_self_hosted"] == "measured"       # 병합분도 measured
    assert peso["comp_b"]["instagram"] == "presence_only"         # MS-D3a
    gaps = build_coverage_gaps(peso, "own_a")
    assert {"platform": "instagram", "held_by": ["comp_b"]} in gaps
    assert {"platform": "blog_tistory", "held_by": ["comp_b"]} in gaps


def test_rubric_boundaries():
    ch2 = {"a/youtube": {"channel_type": "youtube"}, "b/blog": {"channel_type": "blog"}}
    ch1 = {"a/youtube": {"channel_type": "youtube"}}
    assert compute_rubric(ch1, True, [{"p": 1}])[0] == 2          # 측정 1종
    assert compute_rubric(ch2, False, [{"p": 1}])[0] == 3        # degrade 상한
    assert compute_rubric(ch2, True, [])[0] == 4                  # 공백 0건
    assert compute_rubric(ch2, True, [{"p": 1}])[0] == 5


def test_sanitize_llm_output_guards():
    channels = build_channels(_META, dedup_blog_feeds(_FEEDS))
    llm = {
        "channel_keywords": [
            {"channel_key": "own_a/youtube",
             "keywords": [{"keyword": "환전", "example_ids": ["v1", "fake"]}]},
            {"channel_key": "ghost/youtube", "keywords": []},
        ],
        "product_related_ids": ["v2", "nonexistent"],
        "copy_tones": [{"candidate_id": "own_a", "tone_summary": "ok"},
                       {"candidate_id": "ghost", "tone_summary": "x"}],
        "influencer_signals": [{"candidate_id": "own_a", "evidence_ids": ["fake"], "note": ""}],
        "channel_insights": [{"channel_key": "comp_b/blog_tistory", "insight": "ok"}],
        "overall_summary": "s", "warnings": [],
    }
    cleaned, dropped = sanitize_llm_output(llm, channels)
    assert cleaned["product_related_ids"] == ["v2"]
    assert len(cleaned["channel_keywords"]) == 1
    assert cleaned["channel_keywords"][0]["keywords"][0]["example_ids"] == ["v1"]
    assert [c["candidate_id"] for c in cleaned["copy_tones"]] == ["own_a"]
    assert cleaned["influencer_signals"][0]["evidence_ids"] == []
    assert dropped >= 4


# ─── 노드 통합 (FakeAnalyzer) ────────────────────────────────────────────────

class _FakeAnalyzer:
    model = "fake"

    def call_with_schema(self, prompt, output_schema):
        return {
            "channel_keywords": [{"channel_key": "own_a/youtube",
                                  "keywords": [{"keyword": "트래블", "example_ids": ["v1"]}]}],
            "product_related_ids": ["v2"],
            "copy_tones": [{"candidate_id": "own_a", "tone_summary": "직설형"}],
            "influencer_signals": [],
            "channel_insights": [{"channel_key": "own_a/youtube", "insight": "활발"}],
            "overall_summary": "요약",
            "warnings": [],
        }


class _FailAnalyzer:
    model = "fake"

    def call_with_schema(self, prompt, output_schema):
        raise RuntimeError("LLM down")


def _state():
    return {
        "selected_purposes": ["marketing_social"],
        "domain_taxonomy": {"report_config": {"marketing_social": {
            "active": True, "label": "마케팅·소셜 분석", "categories": ["PESO"]}}},
        "own_product": {"product_id": "own_a", "name": "토스 트래블카드", "brand": "토스"},
        "competitor_candidates": [
            {"candidate_id": "comp_b", "product_name": "트래블월렛 카드", "brand": "트래블월렛"}],
        "youtube_channel_metadata": _META,
        "blog_rss_posts": _FEEDS,
        "owned_channel_urls_by_candidate": _OWNED,
    }


def test_node_full_envelope(monkeypatch, tmp_path):
    import server.graph.nodes.marketing_social_node as msn
    monkeypatch.setattr(msn, "load_agent_output", lambda **kw: None)
    monkeypatch.setattr(msn, "store_agent_output", lambda **kw: None)
    out = marketing_social_node(_state(), analyzer=_FakeAnalyzer())  # type: ignore[arg-type]
    env = out["report_outputs"]["marketing_social"]
    assert env["evaluation_score"] == 5            # 2종 측정 + crosstab + 공백 식별
    c = env["content"]
    # MS-D10: prejudged(v1 트래블카드) + LLM(v2) → related 2건
    assert c["frequency_table"]["own_a/youtube"]["related_total"] == 2
    assert c["related_judgement"]["prejudged"] == 1
    assert c["related_judgement"]["llm_added"] == 1
    assert c["peso_matrix"]["own_a"]["press_release"] == "presence_only"
    assert any("MS-D12" in w for w in env["warnings"])


def test_node_degrade_caps_score(monkeypatch):
    import server.graph.nodes.marketing_social_node as msn
    monkeypatch.setattr(msn, "load_agent_output", lambda **kw: None)
    monkeypatch.setattr(msn, "store_agent_output", lambda **kw: None)
    out = marketing_social_node(_state(), analyzer=_FailAnalyzer())  # type: ignore[arg-type]
    env = out["report_outputs"]["marketing_social"]
    assert env["evaluation_score"] == 3            # degrade 상한 (crosstab 부재)
    assert out["errors"]
    # 관련 빈도는 코드 선판정 하한치
    assert env["content"]["frequency_table"]["own_a/youtube"]["related_total"] == 1


def test_node_skips_without_purpose():
    state = {**_state(), "selected_purposes": ["comparison_matrix"]}
    out = marketing_social_node(state)  # type: ignore[arg-type]
    assert out["agent_steps"][0]["status"] == "skipped"
    assert "report_outputs" not in out


def test_node_error_without_data():
    state = {**_state(), "youtube_channel_metadata": {}, "blog_rss_posts": []}
    out = marketing_social_node(state)  # type: ignore[arg-type]
    assert out["agent_steps"][0]["status"] == "failed"
