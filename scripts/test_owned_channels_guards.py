"""
scripts/test_owned_channels_guards.py (v0.13.3)
------------------------------------------------
url_discovery_owned_channels_node 의 도메인 가드 + 핸들 추출 보정 단위 테스트.

배경 (2026-06-07 사용자 보고):
- feature_selection 공식 채널 카드에 @MKCDPD7000M.web·@pconts 등 가짜 핸들 표시.
- 원인 1: LLM 검증이 official 사이트 페이지를 SNS 채널로 통과 (도메인 가드 부재).
- 원인 2: YouTube 구형 URL(/user/X)의 path 첫 segment("user")를 핸들로 오추출.

실행: pytest scripts/test_owned_channels_guards.py -q
"""

import pytest

from server.graph.nodes.url_discovery_owned_channels_node import (
    _extract_handle_from_url,
    _fetch_youtube_channel_meta,
    _platform_host_allowed,
)


# ─────────────────────────── _platform_host_allowed ───────────────────────────

@pytest.mark.parametrize("url,platform,expected", [
    # 정상 — platform 도메인 일치 (서브도메인 포함)
    ("https://www.instagram.com/toss.im/",            "instagram",        True),
    ("https://x.com/tossteam",                        "x",                True),
    ("https://twitter.com/tossteam",                  "x",                True),
    ("https://www.youtube.com/@TossBank",             "youtube_official", True),
    ("https://m.youtube.com/@TossBank",               "youtube_official", True),
    ("https://blog.naver.com/tossbank",               "blog_naver",       True),
    ("https://m.blog.naver.com/tossbank",             "blog_naver",       True),
    ("https://hanamoney.tistory.com/",                "blog_tistory",     True),
    # 오염 실물 사례 (2026-06-07 캐시 실사) — 전부 드롭되어야 함
    ("https://m.hanacard.co.kr/MKCDPD7000M.web",      "x",                False),
    ("https://www.shinhancard.com/pconts/html/card/travel/travel_check.html",
                                                      "youtube_official", False),
    ("https://m.hanacard.co.kr/MKPONT2030M.web",      "blog_naver",       False),
    ("https://www.shinhancard.com/pconts/html/card/apply/check/1225714_2206.html",
                                                      "blog_tistory",     False),
    # 유사 도메인 위장 차단 (suffix 검사는 '.도메인' 경계 기준)
    ("https://fakeinstagram.com/foo",                 "instagram",        False),
    ("https://youtube.com.evil.kr/@x",                "youtube_official", False),
    # press_release 는 제한 없음
    ("https://www.tossbank.com/articles/travelcard",  "press_release",    True),
    ("https://www.startuptoday.co.kr/news/articleView.html?idxno=569605",
                                                      "press_release",    True),
    # 빈 URL 은 제한 platform 에서 불통과
    ("",                                              "instagram",        False),
    # v0.13.4 — blog_self_hosted 역방향 가드: 타 platform 도메인은 불통과
    ("https://blog.hanabank.com/",                    "blog_self_hosted", True),
    ("https://www.shinhancardblog.com/",              "blog_self_hosted", True),
    ("https://blog.naver.com/travelwallet",           "blog_self_hosted", False),
    ("https://shinhancard-blog.tistory.com/m",        "blog_self_hosted", False),
    ("https://www.youtube.com/@TossBank",             "blog_self_hosted", False),
    ("",                                              "blog_self_hosted", False),
])
def test_platform_host_allowed(url, platform, expected):
    assert _platform_host_allowed(url, platform) is expected


def test_brand_query_templates():
    """v0.13.4 — 브랜드 site: 보조 쿼리: press_release 제외 전 platform 에 정의."""
    from server.graph.nodes.url_discovery_owned_channels_node import (
        _PLATFORM_BRAND_QUERIES,
        _PLATFORM_QUERIES,
    )
    assert "press_release" not in _PLATFORM_BRAND_QUERIES
    assert set(_PLATFORM_BRAND_QUERIES) == set(_PLATFORM_QUERIES) - {"press_release"}
    # site: 연산자 — Brave 일반 검색이 노출하지 않는 도메인(x.com 등)을 강제
    for p in ("instagram", "x", "youtube_official", "blog_naver", "blog_tistory"):
        assert _PLATFORM_BRAND_QUERIES[p].startswith("site:")
        assert "{candidate_brand}" in _PLATFORM_BRAND_QUERIES[p]
    assert "blog_self_hosted" in _PLATFORM_QUERIES


# ─────────────────────────── _extract_handle_from_url ─────────────────────────

@pytest.mark.parametrize("url,platform,expected", [
    # 기존 동작 유지
    ("https://www.instagram.com/travelwallet.official/", "instagram",     "travelwallet.official"),
    ("https://x.com/tossteam",                        "x",                "tossteam"),
    ("https://blog.naver.com/tossbank/123",           "blog_naver",       "tossbank"),
    ("https://hanamoney.tistory.com/",                "blog_tistory",     "hanamoney"),
    ("https://www.youtube.com/@TossBank",             "youtube_official", "TossBank"),
    ("https://www.tossbank.com/articles/travelcard",  "press_release",    ""),
    ("https://blog.hanabank.com/category/travel",     "blog_self_hosted", ""),
    # v0.13.3 보정 — YouTube 구형 URL
    ("https://www.youtube.com/user/TOSSservice",      "youtube_official", "TOSSservice"),
    ("https://www.youtube.com/c/TossBank",            "youtube_official", "TossBank"),
    ("https://www.youtube.com/channel/UCab12cd34ef",  "youtube_official", ""),
])
def test_extract_handle(url, platform, expected):
    assert _extract_handle_from_url(url, platform) == expected


# ─────────────────────── _fetch_youtube_channel_meta 조회 파라미터 ─────────────

class _FakeResp:
    ok = True

    @staticmethod
    def json():
        return {"items": [{
            "id": "UCfake",
            "statistics": {"subscriberCount": "1234"},
            "status": {"isLinked": True},
        }]}


@pytest.mark.parametrize("url,handle,expected_key,expected_value", [
    ("https://www.youtube.com/channel/UCab12cd34ef", "",            "id",          "UCab12cd34ef"),
    ("https://www.youtube.com/user/TOSSservice",     "TOSSservice", "forUsername", "TOSSservice"),
    ("https://www.youtube.com/@TossBank",            "TossBank",    "forHandle",   "@TossBank"),
])
def test_youtube_meta_lookup_params(monkeypatch, url, handle, expected_key, expected_value):
    captured = {}

    def fake_get(endpoint, params=None, timeout=None):
        captured.update(params or {})
        return _FakeResp()

    monkeypatch.setattr(
        "server.graph.nodes.url_discovery_owned_channels_node.requests.get", fake_get,
    )
    monkeypatch.setattr(
        "server.graph.nodes.url_discovery_owned_channels_node.YOUTUBE_API_KEY", "test-key",
    )
    meta = _fetch_youtube_channel_meta(url, handle)
    assert captured.get(expected_key) == expected_value
    assert meta == {"channel_id": "UCfake", "title": "",
                    "subscriber_count": 1234, "verified": True}


def test_youtube_meta_skip_without_handle_or_id(monkeypatch):
    """핸들도 channel_id/username 경로도 없으면 호출 skip."""
    monkeypatch.setattr(
        "server.graph.nodes.url_discovery_owned_channels_node.YOUTUBE_API_KEY", "test-key",
    )
    assert _fetch_youtube_channel_meta("https://www.youtube.com/", "") is None


# ─────────────────── YouTube 핸들 프로브 (MS-D14, v1.0.2) ─────────────────────

def test_youtube_handle_candidates_derivation():
    from server.graph.nodes.url_discovery_owned_channels_node import (
        _youtube_handle_candidates,
    )
    found = [
        {"platform": "instagram", "handle": "travelwallet.official",
         "url": "https://www.instagram.com/travelwallet.official/"},
        {"platform": "press_release", "handle": "",
         "url": "https://www.travel-wallet.com/en"},
    ]
    cands = _youtube_handle_candidates(found)
    assert cands[0] == "travelwallet.official"
    assert "travelwallet" in cands                  # .official 제거형 + 도메인 slug 합류
    assert "travel-wallet" in cands                 # SLD 원형
    assert len(cands) <= 4


def test_probe_accepts_on_title_match_and_rejects_mismatch():
    from server.graph.nodes.url_discovery_owned_channels_node import (
        _probe_youtube_handles,
    )
    metas = {
        "wrongname":    {"channel_id": "UC0", "title": "여행꿀팁TV", "subscriber_count": 9, "verified": False},
        "travelwallet": {"channel_id": "UC1", "title": "트래블월렛", "subscriber_count": 1000, "verified": True},
    }
    probed = _probe_youtube_handles(
        ["missing", "wrongname", "travelwallet"], "트래블월렛 카드", "트래블월렛",
        fetch_meta=lambda url, h: metas.get(h))
    assert probed is not None
    assert probed["handle"] == "travelwallet"       # 실존+채널명 일치만 채택
    assert probed["url"] == "https://www.youtube.com/@travelwallet"
    assert probed["origin"] == "youtube_handle_probe"
    assert probed["channel_id"] == "UC1" and probed["confidence"] == 0.75

    none = _probe_youtube_handles(
        ["wrongname"], "트래블월렛 카드", "트래블월렛",
        fetch_meta=lambda url, h: metas.get(h))
    assert none is None                              # 채널명 불일치 → 기각
