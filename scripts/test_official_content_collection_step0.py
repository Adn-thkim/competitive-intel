"""
test_official_content_collection_step0.py
------------------------------------------
official_content_collection 노드 Step 0 (입력 게이트 + extraction_targets 구성) 단위 테스트.

설계 근거: docs/design/feature_extraction_node_design.md §4 (입력 계약) · §5-1 (Step 0)
검증 목표: §9-1 — 비공식 origin 통과 0건 · not_found 스킵 · URL 상한 5 우선순위 정렬.

실행: python -m pytest scripts/test_official_content_collection_step0.py -q
"""

import copy

from server.graph.nodes.official_content_collection_node import (
    _MAX_URLS_PER_PAIR,
    _official_domain_map,
    build_extraction_targets,
)


# ─── fixture ─────────────────────────────────────────────────────────────────

def _base_state() -> dict:
    """트래블카드 파일럿 형태의 합성 analysis_features fixture.

    comp_travel_wallet: origin 혼합 + validated 혼합 — 게이트 분기 전부 커버.
    own_toss_travel:    coverage="not_found" — fetch 스킵 분기 커버.
    """
    return {
        "selected_purposes":    ["comparison_matrix"],
        "selected_feature_ids": ["feat_exchange_fee"],
        "own_product": {"product_id": "own_toss_travel", "name": "토스 트래블카드"},
        "competitor_candidates": [
            {"candidate_id": "comp_travel_wallet", "product_name": "트래블월렛", "brand": "트래블월렛"},
        ],
        "selected_competitor_ids": ["comp_travel_wallet"],
        "official_sources": [
            {"candidate_id": "comp_travel_wallet", "source_type": "official",
             "validated": True, "primary_url": "https://www.travelwallet.com"},
            {"candidate_id": "own_toss_travel", "source_type": "official",
             "validated": True, "primary_url": "https://toss.im/travel"},
        ],
        "analysis_features": [
            {
                "report_type":  "comparison_matrix",
                "feature_id":   "feat_exchange_fee",
                "feature_name": "환전 수수료",
                "description":  "주요 통화 환전 수수료율과 우대 조건",
                "priority":     "high",
                "candidate_coverage": [
                    {
                        "candidate_id": "comp_travel_wallet",
                        "coverage": "partial",
                        "existing_urls": [
                            {"url": "https://www.travelwallet.com",
                             "origin": "official_source", "subpage_category": "", "page_title": ""},
                            {"url": "https://www.travelwallet.com/fees",
                             "origin": "official_subpage", "subpage_category": "수수료",
                             "page_title": "수수료 안내"},
                            {"url": "https://www.travelwallet.com/notice",
                             "origin": "official_subpage", "subpage_category": "공지사항",
                             "page_title": "공지사항"},
                            # 비공식 origin — 게이트에서 차단되어야 함
                            {"url": "https://blog.naver.com/review123",
                             "origin": "brave_search", "page_title": "트래블월렛 후기"},
                        ],
                        "additional_urls": [
                            # validated + official_domain 내부 → 통과
                            {"url": "https://www.travelwallet.com/terms.pdf",
                             "validated": True, "http_status": 200,
                             "source_origin": "official_subpage"},
                            # 미검증 → 차단
                            {"url": "https://www.travelwallet.com/dead",
                             "validated": False, "http_status": 404,
                             "source_origin": "official_subpage"},
                            # validated 이지만 제3자 도메인 → 차단
                            {"url": "https://thirdparty.com/travelwallet-fees",
                             "validated": True, "http_status": 200,
                             "source_origin": "official_subpage"},
                        ],
                    },
                    {
                        "candidate_id": "own_toss_travel",
                        "coverage": "not_found",
                        "existing_urls": [],
                        "additional_urls": [],
                    },
                ],
            },
            # 사용자가 선택하지 않은 feature — 전부 무시되어야 함
            {
                "report_type":  "comparison_matrix",
                "feature_id":   "feat_unselected",
                "feature_name": "지원 통화 수",
                "description":  "충전 가능 통화 종류",
                "priority":     "medium",
                "candidate_coverage": [
                    {
                        "candidate_id": "comp_travel_wallet",
                        "coverage": "sufficient",
                        "existing_urls": [
                            {"url": "https://www.travelwallet.com/currencies",
                             "origin": "official_subpage", "subpage_category": "이용안내",
                             "page_title": "통화 안내"},
                        ],
                        "additional_urls": [],
                    },
                ],
            },
            # 다른 report_type — 전부 무시되어야 함
            {
                "report_type":  "reaction_insight",
                "feature_id":   "feat_exchange_fee",
                "feature_name": "환전 수수료",
                "description":  "환전 수수료에 대한 사용자 반응",
                "priority":     "high",
                "candidate_coverage": [
                    {
                        "candidate_id": "comp_travel_wallet",
                        "coverage": "sufficient",
                        "existing_urls": [
                            {"url": "https://www.travelwallet.com/reaction-page",
                             "origin": "official_subpage", "subpage_category": "혜택",
                             "page_title": ""},
                        ],
                        "additional_urls": [],
                    },
                ],
            },
        ],
    }


def _target_of(targets: list[dict], cid: str) -> dict:
    matches = [t for t in targets if t["candidate_id"] == cid]
    assert len(matches) == 1, f"{cid} target이 정확히 1개여야 함: {matches}"
    return matches[0]


# ─── §9-1: origin 게이트 ─────────────────────────────────────────────────────

class TestUrlGate:
    def test_non_official_origin_blocked(self):
        """비공식 origin(brave_search 등) 통과 0건."""
        targets = build_extraction_targets(_base_state())
        urls = [u["url"] for u in _target_of(targets, "comp_travel_wallet")["urls"]]
        assert "https://blog.naver.com/review123" not in urls

    def test_additional_validated_and_official_domain_only(self):
        """additional_urls는 validated=True + official_domain suffix 매칭만 통과."""
        targets = build_extraction_targets(_base_state())
        urls = [u["url"] for u in _target_of(targets, "comp_travel_wallet")["urls"]]
        assert "https://www.travelwallet.com/terms.pdf" in urls       # 통과
        assert "https://www.travelwallet.com/dead" not in urls        # validated=False
        assert "https://thirdparty.com/travelwallet-fees" not in urls  # 제3자 도메인

    def test_additional_blocked_when_official_domain_unknown(self):
        """official_domain 미확정 candidate의 additional_urls는 보수적으로 차단."""
        state = _base_state()
        state["official_sources"] = [
            s for s in state["official_sources"]
            if s["candidate_id"] != "comp_travel_wallet"
        ]
        targets = build_extraction_targets(state)
        urls = [u["url"] for u in _target_of(targets, "comp_travel_wallet")["urls"]]
        assert "https://www.travelwallet.com/terms.pdf" not in urls


# ─── §9-1: 필터 3단계 ────────────────────────────────────────────────────────

class TestFeatureFilter:
    def test_unselected_feature_excluded(self):
        """selected_feature_ids 밖의 feature는 URL·feature_ids 모두 제외."""
        targets = build_extraction_targets(_base_state())
        target = _target_of(targets, "comp_travel_wallet")
        assert target["feature_ids"] == ["feat_exchange_fee"]
        urls = [u["url"] for u in target["urls"]]
        assert "https://www.travelwallet.com/currencies" not in urls

    def test_other_report_type_excluded(self):
        """report_type != comparison_matrix 항목의 URL은 제외."""
        targets = build_extraction_targets(_base_state())
        urls = [u["url"] for u in _target_of(targets, "comp_travel_wallet")["urls"]]
        assert "https://www.travelwallet.com/reaction-page" not in urls

    def test_report_not_in_selected_purposes(self):
        """comparison_matrix 미선택 시 빈 결과 (활성 게이트)."""
        state = _base_state()
        state["selected_purposes"] = ["battlecard"]
        assert build_extraction_targets(state) == []

    def test_not_found_candidate_kept_without_urls(self):
        """coverage=not_found candidate는 urls=[] 로 유지 (Step 3에서 not_found 마킹)."""
        targets = build_extraction_targets(_base_state())
        own = _target_of(targets, "own_toss_travel")
        assert own["urls"] == []
        assert own["feature_ids"] == ["feat_exchange_fee"]


# ─── §9-1: 우선순위 · 상한 · 결정론 ──────────────────────────────────────────

class TestPriorityCapDeterminism:
    def test_priority_order(self):
        """official_source > 관련 subpage(수수료) > 무관 subpage(공지사항) > additional."""
        targets = build_extraction_targets(_base_state())
        urls = [u["url"] for u in _target_of(targets, "comp_travel_wallet")["urls"]]
        assert urls.index("https://www.travelwallet.com") \
            < urls.index("https://www.travelwallet.com/fees") \
            < urls.index("https://www.travelwallet.com/notice") \
            < urls.index("https://www.travelwallet.com/terms.pdf")

    def test_pair_cap_drops_lowest_tier_first(self):
        """FE-D5 v3: 쌍당 상한 5 초과 시 낮은 우선순위(무관 subpage·additional)부터 탈락."""
        state = _base_state()
        cov = state["analysis_features"][0]["candidate_coverage"][0]
        for i in range(4):  # 무관 subpage 4건 추가 → 단일 쌍의 게이트 통과 총 8건
            cov["existing_urls"].append({
                "url": f"https://www.travelwallet.com/etc{i}",
                "origin": "official_subpage", "subpage_category": "공지사항",
                "page_title": f"기타 {i}",
            })
        targets = build_extraction_targets(state)
        urls = [u["url"] for u in _target_of(targets, "comp_travel_wallet")["urls"]]
        assert len(urls) == _MAX_URLS_PER_PAIR  # 단일 feature → 쌍당 상한이 곧 결과 수
        # 상위 tier 2건은 반드시 생존
        assert "https://www.travelwallet.com" in urls
        assert "https://www.travelwallet.com/fees" in urls
        # 최하위 tier(additional)는 무관 subpage 6건에 밀려 탈락
        assert "https://www.travelwallet.com/terms.pdf" not in urls

    def test_deterministic_output(self):
        """동일 입력 반복 호출 시 결과 완전 동일 (LLM 캐시 키 안정성 전제)."""
        s1, s2 = _base_state(), copy.deepcopy(_base_state())
        assert build_extraction_targets(s1) == build_extraction_targets(s2)

    def test_pair_cap_protects_minor_feature(self):
        """FE-D5 v3: 상한이 쌍 단위이므로, 다른 feature의 고tier URL이 많아도
        특정 feature의 유일한 근거 URL(저tier additional)은 반드시 생존."""
        state = _base_state()
        # feature A(환전 수수료)에 무관 subpage 5건 추가 (tier 2 — terms.pdf보다 높은 우선순위)
        cov = state["analysis_features"][0]["candidate_coverage"][0]
        cov["existing_urls"] = [u for u in cov["existing_urls"]
                                if u.get("origin") != "official_source"]  # primary 제거로 단순화
        for i in range(5):
            cov["existing_urls"].append({
                "url": f"https://www.travelwallet.com/etc{i}",
                "origin": "official_subpage", "subpage_category": "공지사항",
                "page_title": f"기타 {i}",
            })
        # feature B: 유일한 근거 URL이 tier 3 (additional)
        state["selected_feature_ids"].append("feat_annual_fee")
        state["analysis_features"].append({
            "report_type":  "comparison_matrix",
            "feature_id":   "feat_annual_fee",
            "feature_name": "연회비",
            "description":  "연회비 및 발급 비용",
            "priority":     "medium",
            "candidate_coverage": [{
                "candidate_id": "comp_travel_wallet",
                "coverage": "partial",
                "existing_urls": [],
                "additional_urls": [
                    {"url": "https://www.travelwallet.com/annual-fee",
                     "validated": True, "http_status": 200,
                     "source_origin": "official_subpage"},
                ],
            }],
        })
        targets = build_extraction_targets(state)
        urls = [u["url"] for u in _target_of(targets, "comp_travel_wallet")["urls"]]
        # 쌍 A: 게이트 통과 7건 중 쌍당 상한 5건 + 쌍 B: 1건 = 6건 (candidate 합산)
        assert len(urls) == _MAX_URLS_PER_PAIR + 1
        # candidate 총량 절단(구 v2)이라면 탈락했을 feature B의 유일 URL이 생존해야 함
        assert "https://www.travelwallet.com/annual-fee" in urls

    def test_url_dedup_across_features(self):
        """복수 feature가 같은 URL을 가리켜도 candidate당 1회만 등장."""
        state = _base_state()
        state["selected_feature_ids"].append("feat_atm_limit")
        state["analysis_features"].append({
            "report_type":  "comparison_matrix",
            "feature_id":   "feat_atm_limit",
            "feature_name": "ATM 출금 한도",
            "description":  "해외 ATM 출금 한도와 수수료",
            "priority":     "high",
            "candidate_coverage": [{
                "candidate_id": "comp_travel_wallet",
                "coverage": "partial",
                "existing_urls": [
                    {"url": "https://www.travelwallet.com/fees",   # 중복 URL
                     "origin": "official_subpage", "subpage_category": "수수료",
                     "page_title": "수수료 안내"},
                ],
                "additional_urls": [],
            }],
        })
        targets = build_extraction_targets(state)
        target = _target_of(targets, "comp_travel_wallet")
        urls = [u["url"] for u in target["urls"]]
        assert urls.count("https://www.travelwallet.com/fees") == 1
        assert target["feature_ids"] == ["feat_exchange_fee", "feat_atm_limit"]
        # URL 항목에 연관 feature가 노출되어 추적 가능 (union)
        fees_entry = next(u for u in target["urls"]
                          if u["url"] == "https://www.travelwallet.com/fees")
        assert fees_entry["feature_ids"] == ["feat_atm_limit", "feat_exchange_fee"]


# ─── 복수 공식 도메인 허용 목록 (multi-domain allow-list) ─────────────────────

class TestMultiOfficialDomain:
    def test_domain_map_backward_compat_single(self):
        """official_urls 부재 시 primary_url 단일 도메인으로 폴백 (집합 1원소)."""
        dmap = _official_domain_map(_base_state()["official_sources"])
        assert dmap["comp_travel_wallet"] == {"travelwallet.com"}
        assert dmap["own_toss_travel"] == {"toss.im"}

    def test_domain_map_multi_from_official_urls(self):
        """official_urls 가 복수 도메인이면 집합으로 산출."""
        sources = [
            {"candidate_id": "own_toss_travel", "source_type": "official",
             "validated": True, "primary_url": "https://toss.im/tossfeed",
             "official_urls": ["https://toss.im/tossfeed",
                               "https://www.tossbank.com/articles/fx2"]},
        ]
        assert _official_domain_map(sources)["own_toss_travel"] == {"toss.im", "tossbank.com"}

    def test_additional_url_on_second_official_domain_passes(self):
        """2차 공식 도메인(tossbank.com) 위의 additional_url 이 게이트를 통과한다.

        단일 도메인(primary=toss.im) 구조였다면 차단됐을 URL — 회귀 방지 핵심.
        """
        state = _base_state()
        # own 을 게이트 대상으로: 복수 공식 도메인 + 2차 도메인 위 additional_url 부여
        for s in state["official_sources"]:
            if s["candidate_id"] == "own_toss_travel":
                s["official_urls"] = ["https://toss.im/travel",
                                      "https://www.tossbank.com"]
        cov_own = state["analysis_features"][0]["candidate_coverage"][1]
        cov_own["coverage"] = "partial"
        cov_own["additional_urls"] = [
            {"url": "https://www.tossbank.com/articles/fx2",   # 2차 공식 도메인
             "validated": True, "http_status": 200,
             "source_origin": "official_subpage"},
            {"url": "https://thirdparty.com/toss-review",      # 제3자 → 차단 유지
             "validated": True, "http_status": 200,
             "source_origin": "official_subpage"},
        ]
        targets = build_extraction_targets(state)
        urls = [u["url"] for u in _target_of(targets, "own_toss_travel")["urls"]]
        assert "https://www.tossbank.com/articles/fx2" in urls
        assert "https://thirdparty.com/toss-review" not in urls
