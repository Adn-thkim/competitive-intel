"""
test_community_collection.py
-----------------------------
community_collection 노드 단위 테스트 (D11 정책 · RI-D4 보완 상한).

네트워크 비호출 — _fetch_content·robots·sleep 전부 monkeypatch.
실행: python -m pytest scripts/test_community_collection.py -q
"""

import pytest

import server.graph.nodes.community_collection_node as cc
from server.graph.nodes.community_collection_node import (
    _URLS_PER_CANDIDATE,
    community_collection_node,
    select_community_urls,
)


def _url_entry(url, domain_class="community", published_at=""):
    return {"url": url, "origin": "blog_community",
            "domain_class": domain_class, "published_at": published_at}


def _feature(fid, cid, urls):
    return {
        "report_type": "reaction_insight", "feature_id": fid,
        "feature_name": fid, "description": "", "priority": "high",
        "candidate_coverage": [{
            "candidate_id": cid, "coverage": "sufficient",
            "existing_urls": urls, "additional_urls": [],
        }],
    }


def _base_state():
    return {
        "selected_purposes": ["reaction_insight"],
        "selected_feature_ids": ["feat_fee", "feat_ux"],
        "analysis_features": [
            _feature("feat_fee", "own_x", [
                _url_entry("https://c.example.com/post1", published_at="2026-05-01"),
                _url_entry("https://c.example.com/post2", published_at="2026-04-01"),
                _url_entry("https://blog.example.com/a", "personal_blog"),
                # 비대상 origin — 제외돼야 함
                {"url": "https://www.youtube.com/watch?v=vidA0000000",
                 "origin": "youtube_reactions"},
            ]),
            _feature("feat_ux", "own_x", [
                _url_entry("https://c.example.com/ux-post"),
            ]),
        ],
    }


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """robots 허용·fetch 성공·sleep 무시를 기본값으로."""
    self_calls = {"fetch": [], "sleep": []}
    monkeypatch.setattr(cc, "_robots_allowed", lambda url: True)
    monkeypatch.setattr(cc.time, "sleep", lambda s: self_calls["sleep"].append(s))
    monkeypatch.setattr(
        cc, "_fetch_content",
        lambda url: self_calls["fetch"].append(url) or {
            "url": url, "fetch_status": "ok",
            "content": f"# 게시글 제목\n\n{url} 본문 " * 50, "from_cache": False})
    cc._robots_cache.clear()
    return self_calls


class TestSelect:
    def test_origin_filter_and_feature_union(self):
        sel = select_community_urls(_base_state())
        urls = {r["url"] for r in sel["own_x"]}
        assert "https://www.youtube.com/watch?v=vidA0000000" not in urls
        assert "https://c.example.com/ux-post" in urls       # feat_ux 커버 보장

    def test_candidate_cap(self):
        state = _base_state()
        state["analysis_features"][0]["candidate_coverage"][0]["existing_urls"] = [
            _url_entry(f"https://c.example.com/p{i}") for i in range(12)
        ]
        # feature당 3 → 후보 3 + feat_ux 1 = 4 ≤ 8. 상한 검증을 위해 feature 추가
        state["selected_feature_ids"] += [f"feat_{i}" for i in range(4)]
        state["analysis_features"] += [
            _feature(f"feat_{i}", "own_x",
                     [_url_entry(f"https://c{i}.example.com/q{j}") for j in range(3)])
            for i in range(4)
        ]
        sel = select_community_urls(state)
        assert len(sel["own_x"]) <= _URLS_PER_CANDIDATE

    def test_gate(self):
        state = _base_state()
        state["selected_purposes"] = ["comparison_matrix"]
        assert select_community_urls(state) == {}


class TestNode:
    def test_collects_posts_with_excerpt_and_title(self, _no_network):
        out = community_collection_node(_base_state())
        assert out["agent_steps"][0]["status"] == "completed"
        posts = out["community_posts"]
        assert posts and all(p["body_excerpt"] for p in posts)
        assert all(len(p["body_excerpt"]) <= 2000 for p in posts)
        assert posts[0]["title"] == "게시글 제목"
        assert all(p["candidate_id"] == "own_x" for p in posts)

    def test_rate_limit_only_on_network(self, _no_network, monkeypatch):
        """캐시 적중(from_cache)이면 sleep 하지 않는다 (D11 rate limit 의미론)."""
        monkeypatch.setattr(
            cc, "_fetch_content",
            lambda url: {"url": url, "fetch_status": "ok",
                         "content": "# t\n본문 " * 60, "from_cache": True})
        community_collection_node(_base_state())
        assert _no_network["sleep"] == []

    def test_robots_disallow_skips(self, _no_network, monkeypatch):
        monkeypatch.setattr(cc, "_robots_allowed",
                            lambda url: "post1" not in url)
        out = community_collection_node(_base_state())
        urls = [p["url"] for p in out["community_posts"]]
        assert all("post1" not in u for u in urls)
        assert "robots 1" in out["agent_steps"][0]["error_message"]

    def test_dynamic_render_partial(self, _no_network, monkeypatch):
        monkeypatch.setattr(
            cc, "_fetch_content",
            lambda url: {"url": url, "fetch_status": "requires_dynamic_render",
                         "content": "", "from_cache": False})
        out = community_collection_node(_base_state())
        assert out["community_posts"] == []
        assert out["agent_steps"][0]["status"] == "completed"   # 부분 실패 허용
        assert out["errors"]
