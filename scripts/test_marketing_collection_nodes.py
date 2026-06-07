"""
scripts/test_marketing_collection_nodes.py (v1.0 §6-6a)
--------------------------------------------------------
marketing_social 수집 3노드 단위 테스트.
설계: docs/design/marketing_social_node_design.md §6 (검증 1~5)

실행: pytest scripts/test_marketing_collection_nodes.py -q
"""

import pytest

import server.graph.nodes.blog_rss_collection_node as brc
import server.graph.nodes.pr_release_collection_node as prc
import server.graph.nodes.youtube_channel_metadata_collection_node as ycm
from server.graph.nodes.blog_rss_collection_node import (
    blog_rss_collection_node,
    parse_feed,
    rss_candidate_urls,
    select_blog_urls,
)
from server.graph.nodes.pr_release_collection_node import (
    extract_releases,
    pr_release_collection_node,
    select_pr_urls,
)
from server.graph.nodes.youtube_channel_metadata_collection_node import (
    build_channel_record,
    select_channel_urls,
    youtube_channel_metadata_collection_node,
)

_URLS = {
    "own_a": [
        {"platform": "youtube_official", "url": "https://www.youtube.com/@toss_official",
         "confidence": 0.95, "handle": "toss_official"},
        {"platform": "youtube_official", "url": "https://www.youtube.com/user/TOSSservice",
         "confidence": 0.82, "handle": "TOSSservice"},
        {"platform": "blog_self_hosted", "url": "https://toss.im/tossfeed",
         "confidence": 0.92, "handle": ""},
        {"platform": "press_release", "url": "https://www.tossbank.com/articles/travelcard",
         "confidence": 0.82, "handle": ""},
    ],
    "comp_b": [
        {"platform": "blog_naver", "url": "https://blog.naver.com/travelwallet",
         "confidence": 0.9, "handle": "travelwallet"},
        {"platform": "blog_tistory", "url": "https://shinhancard-blog.tistory.com/m",
         "confidence": 0.95, "handle": "shinhancard-blog"},
        {"platform": "x", "url": "https://x.com/toss__official", "confidence": 0.8},
    ],
}

_STATE = {
    "selected_purposes": ["marketing_social", "comparison_matrix"],
    "owned_channel_urls_by_candidate": _URLS,
}


# ─────────────────────────── 게이트 (MS-D1 공통) ─────────────────────────────

@pytest.mark.parametrize("selector", [select_channel_urls, select_blog_urls, select_pr_urls])
def test_gate_skips_without_purpose(selector):
    state = {**_STATE, "selected_purposes": ["comparison_matrix"]}
    assert not selector(state)


@pytest.mark.parametrize("node", [
    youtube_channel_metadata_collection_node,
    blog_rss_collection_node,
    pr_release_collection_node,
])
def test_node_skipped_status(node):
    out = node({"selected_purposes": [], "owned_channel_urls_by_candidate": {}})  # type: ignore[arg-type]
    assert out["agent_steps"][0]["status"] == "skipped"


# ─────────────────────────── 수집 ① YouTube 채널 ────────────────────────────

def test_select_channel_picks_highest_confidence():
    sel = select_channel_urls(_STATE)
    assert set(sel) == {"own_a"}                       # comp_b 에는 youtube 없음
    assert sel["own_a"]["url"].endswith("@toss_official")   # conf 0.95 > 0.82


def test_build_channel_record_joins_stats():
    rec = build_channel_record(
        {"url": "https://www.youtube.com/@x"},
        {"channel_id": "UC1", "title": "토스", "subscriber_count": 100, "video_count": 7,
         "uploads_playlist_id": "UU1"},
        [{"video_id": "v1", "title": "t1", "published_at": "2026-05-01T00:00:00Z",
          "description": "트래블카드 출시 영상"}],
        {"v1": {"view_count": 10, "like_count": 2, "comment_count": 1}},
    )
    assert rec["subscriber_count"] == 100 and rec["video_total"] == 7
    assert rec["recent_videos"][0]["view_count"] == 10
    assert rec["recent_videos"][0]["description"] == "트래블카드 출시 영상"   # MS-D10


def test_channel_node_partial_failure(monkeypatch):
    """채널 해석 실패 candidate 는 errors 적재, 파이프라인은 계속."""
    monkeypatch.setattr(ycm, "youtube_channel_info", lambda url: None)
    out = youtube_channel_metadata_collection_node(dict(_STATE))  # type: ignore[arg-type]
    assert out["youtube_channel_metadata"] == {}
    assert any("채널 미해석" in e["error"] for e in out["errors"])
    assert out["agent_steps"][0]["status"] == "completed"


# ─────────────────────────── 수집 ② 블로그 RSS ──────────────────────────────

@pytest.mark.parametrize("url,platform,handle,expected", [
    ("https://blog.naver.com/travelwallet", "blog_naver", "travelwallet",
     ["https://rss.blog.naver.com/travelwallet.xml"]),
    ("https://blog.naver.com/travelwallet", "blog_naver", "",          # handle 부재 → path
     ["https://rss.blog.naver.com/travelwallet.xml"]),
    ("https://shinhancard-blog.tistory.com/m", "blog_tistory", "",
     ["https://shinhancard-blog.tistory.com/rss"]),
    ("https://blog.hanabank.com/", "blog_self_hosted", "",
     ["https://blog.hanabank.com/rss", "https://blog.hanabank.com/feed",
      "https://blog.hanabank.com/atom.xml"]),
])
def test_rss_candidate_urls(url, platform, handle, expected):
    assert rss_candidate_urls(url, platform, handle) == expected


_RSS_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>공식 블로그</title>
<item><title>5월 이벤트 안내</title><link>https://b/1</link>
<description>트래블카드 환전 이벤트 상세 내용입니다.</description>
<pubDate>Thu, 01 May 2026 09:00:00 +0900</pubDate></item>
<item><title>신규 카드 출시</title><link>https://b/2</link>
<pubDate>Mon, 21 Apr 2026 10:00:00 +0900</pubDate></item>
<item><title></title><link>https://b/3</link></item>
</channel></rss>"""

_ATOM_FIXTURE = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>blog</title>
<entry><title>아톰 글</title><link href="https://a/1"/>
<summary>아톰 요약</summary>
<published>2026-05-02T00:00:00Z</published></entry></feed>"""


def test_parse_feed_rss_and_atom():
    posts = parse_feed(_RSS_FIXTURE)
    assert [p["title"] for p in posts] == ["5월 이벤트 안내", "신규 카드 출시"]
    assert posts[0]["published_at"].startswith("2026-05-01")
    # MS-D10 — RSS 동봉 요약 발췌 (없으면 빈 문자열)
    assert posts[0]["summary"] == "트래블카드 환전 이벤트 상세 내용입니다."
    assert posts[1]["summary"] == ""
    atom = parse_feed(_ATOM_FIXTURE)
    assert atom == [{"title": "아톰 글", "published_at": "2026-05-02T00:00:00+00:00",
                     "link": "https://a/1", "summary": "아톰 요약"}]
    assert parse_feed("not xml") == []


def test_blog_node_degrades_to_presence_only(monkeypatch):
    """모든 피드 경로 실패 → fetch_status=rss_unavailable. errors 미적재 (v1.0.1 —
    설계된 강등을 UI 오류 배너로 오표출하지 않도록 step.error_message 만 기록)."""
    monkeypatch.setattr(brc, "_robots_allowed", lambda url: True)
    monkeypatch.setattr(brc, "_fetch_rss",
                        lambda url: {"status": 404, "body": "", "from_cache": True})
    out = blog_rss_collection_node(dict(_STATE))  # type: ignore[arg-type]
    assert len(out["blog_rss_posts"]) == 3       # own_a self_hosted + comp_b naver·tistory
    assert all(b["fetch_status"] == "rss_unavailable" for b in out["blog_rss_posts"])
    assert "errors" not in out
    assert "3건 presence-only 강등" in out["agent_steps"][0]["error_message"]


def test_blog_node_collects(monkeypatch):
    monkeypatch.setattr(brc, "_robots_allowed", lambda url: True)
    monkeypatch.setattr(brc, "_fetch_rss",
                        lambda url: {"status": 200, "body": _RSS_FIXTURE, "from_cache": True})
    out = blog_rss_collection_node(dict(_STATE))  # type: ignore[arg-type]
    ok = [b for b in out["blog_rss_posts"] if b["fetch_status"] == "ok"]
    assert len(ok) == 3 and all(len(b["posts"]) == 2 for b in ok)
    assert "errors" not in out


# ─────────────────────────── 수집 ③ 보도자료 ────────────────────────────────

def test_extract_releases_patterns():
    content = (
        "토스뱅크, 트래블카드 누적 100만장 발급\n2026.05.12\n\n"
        "2026-04-01 외화통장 신규 혜택 발표 안내\n"
        "2026년 3월 5일 — 해외 ATM 수수료 면제 연장 결정\n"
        "짧음 2026.01.01\n"   # 제목 8자 미만 + 직전 줄도 부적합 → 제외
    )
    rel = extract_releases(content)
    assert {"title": "토스뱅크, 트래블카드 누적 100만장 발급", "published_at": "2026-05-12"} in rel
    assert any(r["published_at"] == "2026-04-01" and "외화통장" in r["title"] for r in rel)
    assert any(r["published_at"] == "2026-03-05" and "ATM" in r["title"] for r in rel)
    assert len(rel) == 3


def test_extract_releases_empty_on_no_dates():
    assert extract_releases("날짜 없는 본문입니다.\n그냥 텍스트.") == []


def test_pr_node_extract_failed_degrades(monkeypatch):
    monkeypatch.setattr(prc, "_fetch_content",
                        lambda url: {"fetch_status": "ok", "content": "날짜 없는 페이지"})
    out = pr_release_collection_node(dict(_STATE))  # type: ignore[arg-type]
    assert out["pr_releases"][0]["fetch_status"] == "extract_failed"
    assert out["pr_releases"][0]["releases"] == []
    assert out["errors"]


def test_pr_node_ok(monkeypatch):
    monkeypatch.setattr(prc, "_fetch_content", lambda url: {
        "fetch_status": "ok",
        "content": "신규 보도자료 제목입니다\n2026.05.01\n",
    })
    out = pr_release_collection_node(dict(_STATE))  # type: ignore[arg-type]
    assert out["pr_releases"][0]["fetch_status"] == "ok"
    assert out["pr_releases"][0]["releases"][0]["published_at"] == "2026-05-01"
    assert "errors" not in out
