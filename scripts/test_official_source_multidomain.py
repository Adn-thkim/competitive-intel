"""
test_official_source_multidomain.py
------------------------------------
official_source_resolver._assemble_source 의 복수 공식 도메인(official_urls) 산출 검증.

검증 목표:
- LLM이 official 로 판정한 후보 중 HTTP 검증 통과분만 official_urls 로 수집(primary 포함).
- 서드파티(LLM 미판정)·HTTP 실패 URL 은 제외 (게이트 오염 방지).
- official_urls 부재(구 동작) 시 [primary_url] 로 하위호환.

실행: python -m pytest scripts/test_official_source_multidomain.py -q
"""

import os

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

import server.graph.nodes.official_source_resolver_node as mod
from server.graph.nodes.official_source_resolver_node import _assemble_source


def _patch_http(monkeypatch, status_by_url):
    monkeypatch.setattr(
        mod, "_validate_url_cached",
        lambda url: status_by_url.get(url, (404, None)))


_ITEM = {"candidate_id": "own_toss", "type": "official",
         "brand": "토스", "product_name": "토스트래블카드"}


class TestOfficialUrlsAssembly:
    def test_multi_domain_collected(self, monkeypatch):
        _patch_http(monkeypatch, {
            "https://toss.im/tossfeed": (200, "https://toss.im/tossfeed"),
            "https://www.tossbank.com/articles/fx2": (200, "https://www.tossbank.com/articles/fx2"),
        })
        candidates = [
            {"url": "https://toss.im/tossfeed", "rank": 0},
            {"url": "https://www.tossbank.com/articles/fx2", "rank": 1},
            {"url": "https://card-gorilla.com/x", "rank": 2},
        ]
        llm_val = {"selected_url": "https://toss.im/tossfeed", "is_official": True,
                   "confidence": 0.9, "validation_reason": "공식",
                   "official_urls": ["https://toss.im/tossfeed",
                                     "https://www.tossbank.com/articles/fx2"]}
        src = _assemble_source(item=_ITEM, candidates=candidates,
                               fast_path=None, llm_val=llm_val, http_futures={})
        assert src["primary_url"] == "https://toss.im/tossfeed"
        assert src["official_urls"] == [
            "https://toss.im/tossfeed", "https://www.tossbank.com/articles/fx2"]
        # LLM이 공식으로 지목하지 않은 서드파티는 제외
        assert "https://card-gorilla.com/x" not in src["official_urls"]

    def test_unvalidated_official_url_excluded(self, monkeypatch):
        """LLM이 공식이라 해도 HTTP 검증 실패면 official_urls 에서 제외 (날조 방지)."""
        _patch_http(monkeypatch, {
            "https://toss.im/tossfeed": (200, "https://toss.im/tossfeed"),
            "https://www.tossbank.com/dead": (404, None),
        })
        candidates = [
            {"url": "https://toss.im/tossfeed", "rank": 0},
            {"url": "https://www.tossbank.com/dead", "rank": 1},
        ]
        llm_val = {"selected_url": "https://toss.im/tossfeed", "is_official": True,
                   "confidence": 0.9, "validation_reason": "공식",
                   "official_urls": ["https://toss.im/tossfeed",
                                     "https://www.tossbank.com/dead"]}
        src = _assemble_source(item=_ITEM, candidates=candidates,
                               fast_path=None, llm_val=llm_val, http_futures={})
        assert src["official_urls"] == ["https://toss.im/tossfeed"]

    def test_backward_compat_no_official_urls(self, monkeypatch):
        """official_urls 미산출(구 LLM 출력)이라도 known_domains 클러스터 후보가
        발견되면 멀티도메인으로 보강된다(LLM 우회 경로 회수)."""
        _patch_http(monkeypatch, {
            "https://toss.im/tossfeed": (200, "https://toss.im/tossfeed"),
            "https://www.tossbank.com/articles/travelcard": (200, "https://www.tossbank.com/articles/travelcard"),
        })
        candidates = [
            {"url": "https://toss.im/tossfeed", "rank": 0},
            {"url": "https://www.tossbank.com/articles/travelcard", "rank": 1},
        ]
        llm_val = {"selected_url": "https://toss.im/tossfeed", "is_official": True,
                   "confidence": 0.9, "validation_reason": "공식"}  # official_urls 없음
        src = _assemble_source(item=_ITEM, candidates=candidates,
                               fast_path=None, llm_val=llm_val, http_futures={})
        assert src["official_urls"] == [
            "https://toss.im/tossfeed", "https://www.tossbank.com/articles/travelcard"]

    def test_fast_path_recovers_cluster_domains(self, monkeypatch):
        """fast-path(LLM 우회)에서도 발견된 클러스터 도메인이 official_urls 에 회수된다.

        실제 버그 재현: 토스가 fast-path 로 분류되어 official_urls=[toss.im] 단일이던 문제.
        """
        _patch_http(monkeypatch, {
            "https://toss.im/tossfeed": (200, "https://toss.im/tossfeed"),
            "https://www.tossbank.com/articles/travelcard": (200, "https://www.tossbank.com/articles/travelcard"),
            "https://info.heretravel.co.kr/board-post/2201": (200, "https://info.heretravel.co.kr/board-post/2201"),
        })
        candidates = [
            {"url": "https://toss.im/tossfeed", "rank": 0},
            {"url": "https://www.tossbank.com/articles/travelcard", "rank": 1},
            {"url": "https://info.heretravel.co.kr/board-post/2201", "rank": 2},  # 클러스터 외
        ]
        fast_path = {"selected_url": "https://toss.im/tossfeed",
                     "confidence": 0.85, "validation_reason": "브랜드 도메인 메인(fast-path)"}
        src = _assemble_source(item=_ITEM, candidates=candidates,
                               fast_path=fast_path, llm_val=None, http_futures={})
        assert src["official_urls"] == [
            "https://toss.im/tossfeed", "https://www.tossbank.com/articles/travelcard"]
        assert src.get("official_urls_v") == 2
