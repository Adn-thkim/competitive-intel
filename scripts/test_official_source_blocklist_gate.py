"""
test_official_source_blocklist_gate.py
---------------------------------------
official URL source 다층 방어 검증:
  ① 수집 쿼리 'NOT site:' 부착   ② _discover_with_brave host 원천 차단
  ③ HTTP 검증(기존)             ④ 조립부 양성 게이트(known_domains 클러스터/토큰)
미등록 브랜드 탈락분은 검토용 JSON 에 기록되는지까지 확인.

실행: python -m pytest scripts/test_official_source_blocklist_gate.py -q
"""

import json
import os

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

import server.graph.nodes.official_source_resolver_node as mod
from server.graph.nodes.official_source_resolver_node import (
    _assemble_source,
    _blocklist_query_suffix,
    _build_brave_queries,
    _filter_official_urls,
    _host_blocklisted,
    _known_domains_for_item,
    _load_blocklist,
)

_ITEM = {"candidate_id": "own_toss", "type": "official",
         "brand": "토스", "product_name": "토스트래블카드"}


# ─── ① 차단 목록 로더 / 쿼리 부착 ────────────────────────────────────────────

class TestBlocklistCollection:
    def test_loader_has_observed_offenders(self):
        bl = _load_blocklist()
        assert "card-gorilla.com" in bl and "namu.wiki" in bl

    def test_host_blocklisted_exact_and_subdomain(self):
        assert _host_blocklisted("https://card-gorilla.com/contents/1")
        assert _host_blocklisted("https://m.blog.naver.com/x")     # 서브도메인 매칭
        assert not _host_blocklisted("https://www.tossbank.com/articles/fx2")

    def test_query_suffix_and_build_queries(self):
        suffix = _blocklist_query_suffix()
        assert " NOT site:" in suffix
        assert suffix.count("NOT site:") <= mod._BLOCKLIST_QUERY_MAX
        qs = _build_brave_queries(_ITEM)
        # 기본 쿼리(앞 2개)에만 'NOT site:' 부착, site: 보조 쿼리에는 미부착(도메인 한정이므로)
        assert all("NOT site:" in q for q in qs[:2])
        assert qs[0].startswith("토스 토스트래블카드 공식 사이트")


# ─── ② 수집 단계 host 원천 차단 ──────────────────────────────────────────────

class TestDiscoverFilters:
    def test_blocklisted_hosts_dropped_from_candidates(self, monkeypatch):
        monkeypatch.setattr(mod, "BRAVE_SEARCH_API_KEY", "test")
        canned = [
            {"url": "https://www.tossbank.com/x", "description": "d", "title": "t"},
            {"url": "https://namu.wiki/w/토스", "description": "d", "title": "t"},
            {"url": "https://card-gorilla.com/c/1", "description": "d", "title": "t"},
        ]
        monkeypatch.setattr(mod, "_brave_query", lambda q, count: list(canned))
        monkeypatch.setattr(mod, "_fetch_page_meta", lambda url: {})
        cands = mod._discover_with_brave(_ITEM)
        urls = {c["url"] for c in cands}
        assert "https://www.tossbank.com/x" in urls
        assert "https://namu.wiki/w/토스" not in urls
        assert "https://card-gorilla.com/c/1" not in urls


# ─── ④ 양성 게이트 (known_domains 클러스터 / 브랜드 토큰) ─────────────────────

class TestSiteBoostRecall:
    def test_known_domains_lookup_multi(self):
        assert set(_known_domains_for_item(_ITEM)) >= {"toss.im", "tossbank.com"}

    def test_build_queries_includes_per_domain_site(self):
        qs = _build_brave_queries(_ITEM)
        assert any("site:toss.im" in q for q in qs)
        assert any("site:tossbank.com" in q for q in qs)

    def test_roundrobin_surfaces_boost_domain_despite_cap(self, monkeypatch):
        """일반 쿼리가 toss.im 으로 상한(5)을 채워도, site:tossbank.com 보조 결과가
        라운드로빈 병합으로 후보에 포함된다(회수율 보강 회귀 방지)."""
        monkeypatch.setattr(mod, "BRAVE_SEARCH_API_KEY", "test")
        base_results = [{"url": f"https://toss.im/p{i}", "description": "d", "title": "t"}
                        for i in range(5)]

        def fake_brave(q, count):
            if "site:tossbank.com" in q:
                return [{"url": "https://www.tossbank.com/articles/travelcard",
                         "description": "d", "title": "t"}]
            if "site:toss.im" in q:
                return [{"url": "https://toss.im/tossfeed", "description": "d", "title": "t"}]
            if "site:tossinvest.com" in q:
                return []
            return list(base_results)

        monkeypatch.setattr(mod, "_brave_query", fake_brave)
        monkeypatch.setattr(mod, "_fetch_page_meta", lambda url: {})
        cands = mod._discover_with_brave(_ITEM)
        hosts = {mod._host_of(c["url"]) for c in cands}
        assert "tossbank.com" in hosts   # 보조 쿼리 도메인이 상한에 밀리지 않음
        assert "toss.im" in hosts


class TestPositiveGate:
    def test_cluster_admits_same_brand_domain(self):
        # primary=toss.im → known_domains["토스"] 클러스터에 tossbank.com 포함 → 통과
        kept, rejected = _filter_official_urls(
            ["https://www.tossbank.com/articles/fx2", "https://card-gorilla.com/x"],
            _ITEM, primary_url="https://toss.im/tossfeed")
        assert "https://www.tossbank.com/articles/fx2" in kept
        assert "https://card-gorilla.com/x" in rejected

    def test_brand_token_admits_latin_brand(self):
        item = {"candidate_id": "comp_wise", "type": "official",
                "brand": "Wise", "product_name": "Wise Card"}
        kept, rejected = _filter_official_urls(
            ["https://wise.co.kr/card", "https://review-site.com/wise"],
            item, primary_url="https://wise.com")
        assert "https://wise.co.kr/card" in kept          # 'wise' 토큰 일치
        assert "https://review-site.com/wise" in rejected  # host 에 토큰 없음

    def test_unregistered_brand_rejected(self):
        item = {"candidate_id": "comp_x", "type": "official",
                "brand": "민트페이", "product_name": "민트카드"}
        kept, rejected = _filter_official_urls(
            ["https://some-aggregator.com/mint"], item,
            primary_url="https://mintpay.example/")  # known_domains 미적중
        assert kept == []
        assert rejected == ["https://some-aggregator.com/mint"]


# ─── ④ 검토용 JSON 기록 + _assemble_source 종단 ──────────────────────────────

class TestAssembleAndReview:
    def test_assemble_applies_gate_and_records_review(self, monkeypatch, tmp_path):
        monkeypatch.setattr(mod, "BASE_DIR", tmp_path)
        monkeypatch.setattr(
            mod, "_validate_url_cached",
            lambda url: (200, url))   # 모든 URL HTTP 통과로 가정
        candidates = [
            {"url": "https://toss.im/tossfeed", "rank": 0},
            {"url": "https://www.tossbank.com/articles/fx2", "rank": 1},
            {"url": "https://card-gorilla.com/c/1", "rank": 2},
        ]
        llm_val = {"selected_url": "https://toss.im/tossfeed", "is_official": True,
                   "confidence": 0.9, "validation_reason": "공식",
                   "official_urls": ["https://toss.im/tossfeed",
                                     "https://www.tossbank.com/articles/fx2",
                                     "https://card-gorilla.com/c/1"]}
        src = _assemble_source(item=_ITEM, candidates=candidates,
                               fast_path=None, llm_val=llm_val, http_futures={})
        # primary + 클러스터 통과(tossbank) 만, card-gorilla 는 게이트 탈락
        assert src["official_urls"] == [
            "https://toss.im/tossfeed", "https://www.tossbank.com/articles/fx2"]
        # 탈락분이 검토용 JSON 에 기록됨
        review = tmp_path / "data" / "review" / "official_url_gate_review.json"
        assert review.exists()
        recs = json.loads(review.read_text("utf-8"))["records"]
        assert any(r["url"] == "https://card-gorilla.com/c/1"
                   and r["candidate_id"] == "own_toss" for r in recs)
