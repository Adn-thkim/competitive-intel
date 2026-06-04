"""
server/graph/nodes/url_discovery_blog_community_node.py (v0.10.22b 실 구현)
---------------------------------------------------------------------------
5중 fan-out 중 source-type 2번 — 외부 후기·블로그·커뮤니티 URL 탐색.

v0.10.19 까지의 스켈레톤(`_discover_via_brave_with_hints` 그대로 재사용) 을 폐기하고
본 PR(v0.10.22b) 에서 §5-2 의 3가지 정밀화 책임을 도입합니다.

핵심 변경 (turn-54)
-------------------
1. **공식 도메인 제외 (D36 옵션 b)** — `official_sources` 의 `primary_url` host 를
   추출하여 Brave 결과에서 공식 도메인 일치 URL 제외. reaction_insight 가 자사·
   경쟁사 입장 인용으로 오염되는 것을 차단.
2. **외부 도메인 화이트리스트 우선 정렬 (D35 옵션 a)** — 한국 4 분류 17건 정적
   리스트 + tistory subdomain 패턴. 매칭 URL 을 상단, 나머지를 후순위로 정렬.
3. **`domain_class` 부착** — 4 분류 (`review_site`·`personal_blog`·`community`·
   `wiki`) + 미매칭 `other` 부착. LLM 매핑 단계의 reaction 가중치 적용 단서.
4. **`origin="blog_community"` 부착** — 기존 일률 `"brave_search"` → 명시.
   `_filter_candidates_for_report` 의 v0.10.20.1 일관 처리로 자동 통과.

위임 책임 (v0.10.22b 범위 외)
----------------------------
- **발행일 ≤ 36개월 검증 (D37 옵션 c)** — `_fetch_meta` 가 본문 미수집 + Brave
  결과 발행일 없음 → v0.10.24 (`_fetch_meta` body 보강) 시점에 도입.
- **본문 길이 ≥ 200자 검증 (D38 옵션 b)** — `page_meta_collect_node` 의 책임 (현재)
  또는 v0.10.27 통합 노드의 page_meta 단계 책임으로 위임.

위치 (v0.10.19 토폴로지 1차 fan-out)
------------------------------------
ab_join
  ├─→ url_discovery_official_node
  ├─→ [url_discovery_blog_community_node]   ← 이 노드 (v0.10.22b 실 구현)
  ├─→ url_discovery_youtube_reactions_node
  ├─→ url_discovery_owned_channels_node
  └─→ url_discovery_macro_node

입력 state 키
-------------
- domain_taxonomy           : `source_hint="blog_community"` hints 추출
- official_sources          : 공식 도메인 제외용 (D36 옵션 b)
- own_product / competitor_candidates / selected_competitor_ids
- domain_name

출력 state 키
-------------
- blog_community_urls_by_candidate : dict[candidate_id, list[dict]]
- agent_steps                      : 누적 reducer

graceful 종료
-------------
- BRAVE_SEARCH_API_KEY 미설정: _discover_via_brave_with_hints 빈 결과
- source_hint="blog_community" hint 부재: 빈 결과 + status="completed"
- 일부 쿼리 실패: status="completed" + errors 누적
"""

import logging
from datetime import datetime, timezone
from urllib.parse import urlparse

from server.graph.progress_store import set_progress
from server.graph.state import DomainAnalysisState, AgentStep
from server.graph.nodes.feature_url_mapper_node import (
    _discover_via_brave_with_hints,
    _extract_active_reports,
    _extract_hints_for_source,
    _error,
)

logger = logging.getLogger(__name__)

_SOURCE_TYPE = "blog_community"

# ─── 정적 화이트리스트 (D35 옵션 a — 한국 외부 도메인 4 분류) ──────────────────

# review_site (금융·카드·핀테크 비교 매체)
_BLOG_COMMUNITY_REVIEW_SITES: tuple[str, ...] = (
    "card-gorilla.com",      # 카드고릴라
    "banksalad.com",         # 뱅크샐러드
    "finda.co.kr",           # 핀다
    "thefirstmedia.net",     # 더퍼스트미디어
)

# personal_blog (개인 블로그 플랫폼)
_BLOG_COMMUNITY_PERSONAL_BLOGS: tuple[str, ...] = (
    "brunch.co.kr",          # 브런치
    "blog.naver.com",        # 네이버 블로그
    "velog.io",              # velog
    "medium.com",            # Medium
)

# community (사용자 토론 커뮤니티)
_BLOG_COMMUNITY_COMMUNITIES: tuple[str, ...] = (
    "clien.net",             # 클리앙
    "ppomppu.co.kr",         # 뽐뿌
    "mlbpark.donga.com",     # MLB파크
    "fmkorea.com",           # 에펨코리아
    "theqoo.net",            # 더쿠
    "dcinside.com",          # 디시인사이드
)

# wiki (위키 매체)
_BLOG_COMMUNITY_WIKIS: tuple[str, ...] = (
    "namu.wiki",             # 나무위키
    "ko.wikipedia.org",      # 한국어 위키백과
)

# tistory subdomain 패턴 (예: hanamoney.tistory.com)
_TISTORY_SUFFIX = "tistory.com"


# ─── 헬퍼 ────────────────────────────────────────────────────────────────────

def _extract_host(url: str) -> str:
    """URL 에서 host (스킴·path 없이, lowercase, www. strip) 추출."""
    if not url:
        return ""
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:  # noqa: BLE001
        return ""
    if not host:
        return ""
    return host[4:] if host.startswith("www.") else host


def _classify_domain(url: str) -> str:
    """URL 의 host 를 4 분류 enum 으로 매핑. 미매칭 시 'other'.

    Returns
    -------
    str
        'review_site' | 'personal_blog' | 'community' | 'wiki' | 'other'
    """
    host = _extract_host(url)
    if not host:
        return "other"

    def _match(host: str, whitelist: tuple[str, ...]) -> bool:
        return any(host == d or host.endswith("." + d) for d in whitelist)

    if _match(host, _BLOG_COMMUNITY_REVIEW_SITES):
        return "review_site"
    if _match(host, _BLOG_COMMUNITY_PERSONAL_BLOGS):
        return "personal_blog"
    # tistory subdomain (host 가 *.tistory.com 또는 tistory.com 인 경우)
    if host == _TISTORY_SUFFIX or host.endswith("." + _TISTORY_SUFFIX):
        return "personal_blog"
    if _match(host, _BLOG_COMMUNITY_COMMUNITIES):
        return "community"
    if _match(host, _BLOG_COMMUNITY_WIKIS):
        return "wiki"
    return "other"


def _extract_official_hosts(official_sources: list[dict]) -> set[str]:
    """official_sources 에서 공식 도메인 host 집합 추출 (D36 공식 도메인 제외 키).

    validated=True 인 official 항목의 primary_url 만 채택. reference 는 외부
    출처라 본 함수에서 제외하지 않음 (다른 source-type 노드의 책임).
    """
    hosts: set[str] = set()
    for src in official_sources:
        if src.get("source_type") != "official":
            continue
        if not src.get("validated"):
            continue
        primary = src.get("primary_url") or ""
        host = _extract_host(primary)
        if host:
            hosts.add(host)
    return hosts


def _host_in_official(url: str, official_hosts: set[str]) -> bool:
    """URL 의 host 가 공식 도메인 집합에 속하거나 그 sub-domain 인지."""
    host = _extract_host(url)
    if not host or not official_hosts:
        return False
    return any(host == d or host.endswith("." + d) for d in official_hosts)


# ─── 메인 노드 ───────────────────────────────────────────────────────────────

def url_discovery_blog_community_node(
    state: DomainAnalysisState, config: dict | None = None,
) -> dict:
    """v0.10.22b 실 구현 — 공식 도메인 제외 + 화이트리스트 정렬 + domain_class 부착."""
    started_at = datetime.now(timezone.utc).isoformat()
    thread_id  = (config or {}).get("configurable", {}).get("thread_id", "")

    print(
        f"📝 [url_discovery_blog_community_node] ENTRY at {started_at} thread_id={thread_id!r}",
        flush=True,
    )

    if thread_id:
        try:
            set_progress(
                thread_id, "feature_mapping_brave",
                detail="블로그·커뮤니티 후기 URL 탐색 (공식 제외 + 화이트리스트 정렬)",
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("set_progress(blog_community) 실패: %s", exc)

    # ── 입력 수집 ───────────────────────────────────────────────────────────
    domain_taxonomy: dict       = state.get("domain_taxonomy") or {}
    domain_name: str            = state.get("domain_name") or ""
    own_product: dict           = state.get("own_product") or {}
    competitor_candidates: list = state.get("competitor_candidates") or []
    selected_ids: list[str]     = state.get("selected_competitor_ids") or []
    official_sources: list      = state.get("official_sources") or []

    if not domain_taxonomy:
        return _error(started_at, "domain_taxonomy 가 state 에 없습니다.")
    if not domain_name:
        return _error(started_at, "domain_name 이 state 에 없습니다.")

    # ── Step 1: source_hint="blog_community" hints 추출 ─────────────────────
    all_active = _extract_active_reports(domain_taxonomy)
    hints_with_meta = _extract_hints_for_source(all_active, _SOURCE_TYPE)

    if not hints_with_meta:
        logger.info(
            "url_discovery_blog_community_node: source_hint='blog_community' hint 부재 — 빈 결과",
        )
        finished_at = datetime.now(timezone.utc).isoformat()
        return {
            "blog_community_urls_by_candidate": {},
            "agent_steps": [{
                "step_name":   "UrlDiscoveryBlogCommunity",
                "status":      "completed",
                "started_at":  started_at,
                "finished_at": finished_at,
            }],
        }

    # ── Step 2: 공식 도메인 host 집합 추출 (D36 제외 키) ────────────────────
    official_hosts = _extract_official_hosts(official_sources)
    logger.info(
        "url_discovery_blog_community_node: 공식 도메인 %d개 제외 대상 (%s)",
        len(official_hosts), sorted(official_hosts),
    )

    # ── Step 3: Brave 검색 (기존 헬퍼 그대로) ───────────────────────────────
    raw_urls = _discover_via_brave_with_hints(
        hints_with_meta=hints_with_meta,
        own_product=own_product,
        competitor_candidates=competitor_candidates,
        selected_ids=selected_ids,
        domain_name=domain_name,
    )

    # ── Step 4: 공식 도메인 제외 + domain_class 부착 + origin 변경 ──────────
    filtered_by_candidate: dict[str, list[dict]] = {}
    excluded_count = 0
    domain_class_dist: dict[str, int] = {}

    for cid, urls in raw_urls.items():
        kept: list[dict] = []
        for u in urls:
            url = u.get("url") or ""
            if _host_in_official(url, official_hosts):
                excluded_count += 1
                continue
            domain_class = _classify_domain(url)
            domain_class_dist[domain_class] = domain_class_dist.get(domain_class, 0) + 1
            kept.append({
                "url":              url,
                "page_title":       u.get("page_title", ""),
                "meta_description": u.get("meta_description", ""),
                "origin":           "blog_community",   # 기존 'brave_search' → 명시
                "domain_class":     domain_class,
                "feature_ids":      u.get("feature_ids", []),
                "matched_report_types": u.get("matched_report_types") or ["reaction_insight"],
            })
        if kept:
            filtered_by_candidate[cid] = kept

    # ── Step 5: 화이트리스트 매칭 URL 우선 정렬 (candidate 별) ──────────────
    # 정렬 key: domain_class 가 화이트리스트(other 외)면 0, other 면 1
    for cid in filtered_by_candidate:
        filtered_by_candidate[cid].sort(
            key=lambda u: 0 if u["domain_class"] != "other" else 1,
        )

    total = sum(len(v) for v in filtered_by_candidate.values())
    logger.info(
        "url_discovery_blog_community_node: 완료 — %d candidate · %d URL "
        "(공식 제외 %d건, domain_class 분포 %s)",
        len(filtered_by_candidate), total, excluded_count, domain_class_dist,
    )

    finished_at = datetime.now(timezone.utc).isoformat()
    step: AgentStep = {
        "step_name":   "UrlDiscoveryBlogCommunity",
        "status":      "completed",
        "started_at":  started_at,
        "finished_at": finished_at,
    }
    return {
        "blog_community_urls_by_candidate": filtered_by_candidate,
        "agent_steps":                      [step],
    }
