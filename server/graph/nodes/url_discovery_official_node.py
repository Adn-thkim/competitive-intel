"""
server/graph/nodes/url_discovery_official_node.py (v0.10.22a 실 구현)
---------------------------------------------------------------------
5중 fan-out 중 source-type 1번 — 자사·경쟁사 공식 사이트 + sub-page URL 탐색.

v0.10.19 까지의 스켈레톤(`_discover_via_brave_with_hints` 그대로 재사용) 을 폐기하고
본 PR(v0.10.22a) 에서 §5-1 의 5가지 정밀화 책임을 일괄 도입합니다.

핵심 변경 (turn-52)
-------------------
1. **`official_sources` carry-through** — `official_source_resolver_node` 가 검증한
   `primary_url` (validated=True) 을 1차 후보로 통과. origin="official_source".
2. **`site:` 한정 검색** — carry-through URL 의 host 를 `official_domain` 으로 추출 후
   Brave 검색에 `site:{official_domain}` 부착. 검색 대상이 candidate 자체 도메인에
   제한되어 광고·블로그 혼입 방지.
3. **`source_hint="official"` hints + 정적 sub-page 키워드 보강 (D33 옵션 a)** —
   domain_taxonomy 의 hint query 에 정적 한국어 sub-page 키워드 7종 (약관·수수료·환율·
   한도·혜택·공지사항·이용안내) 을 보강.
4. **`origin` 2종 분리** — official_source (carry) / official_subpage (Brave) 부착으로
   후속 _build_candidates_with_meta 가 두 origin 을 구분 가능.
5. **`subpage_category` 필드 부착** — Brave 검색에 사용된 sub-page 키워드를 결과 URL
   에 부착. LLM 매핑 단계가 카테고리 단서로 활용.
6. **`_check_url_status` 도달성 검증 (병렬)** — Brave 발견 URL 모두 HEAD/GET 검증.
   200~399 통과만 결과 진입. 도달 불가 URL 의 LLM 진입 0건 보장 → token 비용 절감.

D33·D34 결정 항목 (turn-52)
---------------------------
- D33 (a): source_hint="official" hints + 정적 sub-page 키워드 보강
- D34 (a): official_domain 부재 candidate 는 site: 검색 스킵 + carry-through 만 유지

위치 (v0.10.19 토폴로지 1차 fan-out)
------------------------------------
ab_join
  ├─→ [url_discovery_official_node]   ← 이 노드 (v0.10.22a 실 구현)
  ├─→ url_discovery_blog_community_node
  ├─→ url_discovery_youtube_reactions_node
  ├─→ url_discovery_owned_channels_node
  └─→ url_discovery_macro_node

입력 state 키
-------------
- official_sources         : official_source_resolver_node 산출 (carry 입력)
- domain_taxonomy          : DomainTaxonomyAgent 산출
- own_product / competitor_candidates / selected_competitor_ids
- domain_name

출력 state 키
-------------
- official_urls_by_candidate : dict[candidate_id, list[dict]]
- agent_steps                : 누적 reducer

graceful 종료
-------------
- BRAVE_SEARCH_API_KEY 미설정: _brave_search 가 빈 리스트 — site: 검색 0건
- official_sources 빈 입력: Brave 스킵 + 빈 결과 (status="completed")
- 부분 실패 (일부 URL HEAD fail): status="completed" + errors 누적
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import urlparse

from server.graph.progress_store import set_progress
from server.graph.state import DomainAnalysisState, AgentStep
from server.graph.nodes.feature_url_mapper_node import (
    _brave_search,
    _candidate_name_map,
    _check_url_status,
    _extract_active_reports,
    _extract_hints_for_source,
    _substitute_tokens,
    _error,
)

logger = logging.getLogger(__name__)

_SOURCE_TYPE = "official"

# v0.10.22a — 정적 한국어 sub-page 키워드 (D33 옵션 a)
_OFFICIAL_SUBPAGE_KEYWORDS: tuple[str, ...] = (
    "약관",       # 이용약관·서비스약관
    "수수료",     # 수수료표
    "환율",       # 환율 안내
    "한도",       # 결제·이체 한도
    "혜택",       # 혜택 안내
    "공지사항",   # 공지·업데이트
    "이용안내",   # 이용 가이드
)

_BRAVE_COUNT_PER_QUERY = 5
_MAX_WORKERS           = 5     # site: 검색·_check_url_status 공통 worker 수
_STATUS_OK_MIN         = 200
_STATUS_OK_MAX         = 399


# ─── 헬퍼 ────────────────────────────────────────────────────────────────────

def _extract_official_domain(primary_url: str) -> str:
    """primary_url 에서 host (스킴·path 없이) 추출.

    예: "https://www.travelwallet.com/about" → "travelwallet.com"
        "https://travelwallet.com"            → "travelwallet.com"
        "" 또는 invalid                       → ""
    """
    if not primary_url:
        return ""
    try:
        host = (urlparse(primary_url).hostname or "").lower()
    except Exception:  # noqa: BLE001
        return ""
    if not host:
        return ""
    return host[4:] if host.startswith("www.") else host


def _build_subpage_query(candidate_name: str, official_domain: str, subpage_keyword: str) -> str:
    """candidate × domain × sub-page 키워드 → Brave site: 한정 쿼리.

    예: ("트래블월렛", "travelwallet.com", "약관") →
        "트래블월렛 약관 site:travelwallet.com"
    """
    return f"{candidate_name} {subpage_keyword} site:{official_domain}"


def _is_status_ok(status: int | None) -> bool:
    """_check_url_status 결과가 도달 가능(2xx·3xx) 인지."""
    return status is not None and _STATUS_OK_MIN <= status <= _STATUS_OK_MAX


# ─── 메인 노드 ───────────────────────────────────────────────────────────────

def url_discovery_official_node(state: DomainAnalysisState, config: dict | None = None) -> dict:
    """v0.10.22a 실 구현 — carry + site: 검색 + origin 분리 + subpage_category + 검증."""
    started_at = datetime.now(timezone.utc).isoformat()
    thread_id  = (config or {}).get("configurable", {}).get("thread_id", "")

    print(
        f"🏛️  [url_discovery_official_node] ENTRY at {started_at} thread_id={thread_id!r}",
        flush=True,
    )

    if thread_id:
        try:
            set_progress(
                thread_id, "feature_mapping_brave",
                detail="공식 사이트 URL 탐색 (carry + site: 한정 + 검증)",
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("set_progress(official) 실패: %s", exc)

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

    name_map = _candidate_name_map(own_product, competitor_candidates, selected_ids)
    own_product_name = own_product.get("name") or own_product.get("product_name") or ""

    # ── Step 1: carry-through (official_sources → origin="official_source") ──
    # candidate_id → list of {url, origin, primary} 기록. 복수 공식 URL 운반.
    carried_by_candidate: dict[str, list[dict]] = {}
    # candidate_id → 공식 도메인 집합 (복수 도메인 허용) — Step 2 site: 검색에 사용
    domain_by_candidate: dict[str, set[str]] = {}

    for src in official_sources:
        cid   = src.get("candidate_id", "")
        stype = src.get("source_type")
        if not cid:
            continue

        if stype == "official":
            if not src.get("validated"):
                continue
            # official_urls(복수, 신규) 우선, 부재 시 primary_url 단일 (하위호환)
            official_urls = [u for u in (src.get("official_urls") or []) if u] or (
                [src["primary_url"]] if src.get("primary_url") else []
            )
            for url in official_urls:
                carried_by_candidate.setdefault(cid, []).append({
                    "url":                  url,
                    "page_title":           "",
                    "meta_description":     "",
                    "origin":               "official_source",
                    "subpage_category":     "",
                    "matched_report_types": ["comparison_matrix", "battlecard", "market_context_swot"],
                })
                domain = _extract_official_domain(url)
                if domain:
                    domain_by_candidate.setdefault(cid, set()).add(domain)
        elif stype == "reference":
            # reference 의 reference_sources 중 validated 항목만 carry. domain 추출은 생략
            # (reference candidate 는 공식 도메인 부재 → site: 검색 대상 아님)
            for ref in src.get("reference_sources", []):
                if ref.get("validated"):
                    url = ref.get("final_url") or ref.get("url", "")
                    if url:
                        carried_by_candidate.setdefault(cid, []).append({
                            "url":                  url,
                            "page_title":           "",
                            "meta_description":     "",
                            "origin":               "official_source",
                            "subpage_category":     "",
                            "matched_report_types": ["comparison_matrix", "battlecard", "market_context_swot"],
                        })

    logger.info(
        "url_discovery_official_node: carry-through %d candidate (official_domain %d개 확정)",
        len(carried_by_candidate), len(domain_by_candidate),
    )

    # ── Step 2: source_hint="official" hints 추출 ───────────────────────────
    all_active      = _extract_active_reports(domain_taxonomy)
    hints_with_meta = _extract_hints_for_source(all_active, _SOURCE_TYPE)

    # ── Step 3: site: 한정 검색 (candidate × domain × sub-page 키워드) ──────
    # (candidate_id, query, subpage_category) 작업 목록
    subpage_tasks: list[tuple[str, str, str]] = []
    for cid, official_domains in domain_by_candidate.items():
        cand_name = name_map.get(cid) or name_map.get("own", "")
        if not cand_name:
            continue
        for official_domain in sorted(official_domains):
            for keyword in _OFFICIAL_SUBPAGE_KEYWORDS:
                query = _build_subpage_query(cand_name, official_domain, keyword)
                subpage_tasks.append((cid, query, keyword))

    # source_hint="official" hint 가 있으면 site: 부착하여 추가 보강
    # (D33 옵션 a: domain_taxonomy 의 LLM 추천 hint 도 활용)
    for query_template, feature_id, rt in hints_with_meta:
        for cid, official_domains in domain_by_candidate.items():
            cand_name = name_map.get(cid) or name_map.get("own", "")
            if not cand_name:
                continue
            base_query = _substitute_tokens(
                query_template, name_map, cand_name, own_product_name, domain_name,
            )
            if not base_query or "{" in base_query:
                continue
            # hint 자체에 site: 가 있으면 그대로 사용, 없으면 도메인별로 부착
            if "site:" in base_query.lower():
                subpage_tasks.append((cid, base_query, "hint"))
            else:
                for official_domain in sorted(official_domains):
                    subpage_tasks.append(
                        (cid, f"{base_query} site:{official_domain}", "hint"))

    # 동일 쿼리 dedup
    stage_dedup: dict[str, tuple[str, str, str]] = {}
    for task in subpage_tasks:
        stage_dedup.setdefault(task[1], task)

    logger.info(
        "url_discovery_official_node: site: 검색 작업 %d건 (dedup 후 %d건)",
        len(subpage_tasks), len(stage_dedup),
    )

    # ── Step 4: 병렬 Brave 검색 ────────────────────────────────────────────
    errors: list[dict[str, str]] = []
    subpage_results: dict[str, list[dict]] = {}  # candidate_id → [{url, ...}]

    def _search(cid: str, query: str, keyword: str) -> tuple[str, list[dict], str]:
        try:
            return cid, _brave_search(query, count=_BRAVE_COUNT_PER_QUERY), keyword
        except Exception as exc:  # noqa: BLE001
            errors.append({
                "node":      "url_discovery_official_node",
                "error":     f"brave site: query={query[:60]}: {exc}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            return cid, [], keyword

    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        futures = [pool.submit(_search, *t) for t in stage_dedup.values()]
        for fut in as_completed(futures):
            cid, results, keyword = fut.result()
            domains = domain_by_candidate.get(cid) or set()
            for r in results:
                url = (r.get("url") or "").strip()
                if not url:
                    continue
                # site: 누락 대비 host suffix 재검증 (복수 공식 도메인 ANY 매칭)
                if domains and not _host_endswith_any(url, domains):
                    continue
                subpage_results.setdefault(cid, []).append({
                    "url":              url,
                    "page_title":       (r.get("title") or "").strip(),
                    "meta_description": (r.get("description") or "").strip(),
                    "origin":           "official_subpage",
                    "subpage_category": keyword,
                    "matched_report_types": ["comparison_matrix", "battlecard", "market_context_swot"],
                })

    # ── Step 5: _check_url_status 도달성 검증 (병렬) ────────────────────────
    all_urls_to_check = sorted({u["url"] for urls in subpage_results.values() for u in urls})
    logger.info(
        "url_discovery_official_node: 도달성 검증 시작 (%d URLs)", len(all_urls_to_check),
    )

    status_by_url: dict[str, int | None] = {}
    if all_urls_to_check:
        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
            fut_map = {pool.submit(_check_url_status, url): url for url in all_urls_to_check}
            for fut in as_completed(fut_map):
                url = fut_map[fut]
                try:
                    status_by_url[url] = fut.result()
                except Exception as exc:  # noqa: BLE001
                    logger.debug("url_status 예외 (%s): %s", url, exc)
                    status_by_url[url] = None

    # 도달 가능(2xx·3xx) URL 만 통과 — 도달 불가 URL 의 LLM 입력 진입 0건 보장
    filtered_subpage: dict[str, list[dict]] = {}
    dropped_count = 0
    for cid, urls in subpage_results.items():
        kept = [u for u in urls if _is_status_ok(status_by_url.get(u["url"]))]
        dropped_count += (len(urls) - len(kept))
        if kept:
            filtered_subpage[cid] = kept

    logger.info(
        "url_discovery_official_node: 검증 통과 %d URL · 도달 불가 %d URL 제외",
        sum(len(v) for v in filtered_subpage.values()), dropped_count,
    )

    # ── Step 6: carry + subpage 머지 ────────────────────────────────────────
    final_results: dict[str, list[dict]] = {}
    all_cids = set(carried_by_candidate) | set(filtered_subpage)
    for cid in all_cids:
        merged: list[dict] = list(carried_by_candidate.get(cid, []))
        seen_urls = {u["url"] for u in merged}
        for u in filtered_subpage.get(cid, []):
            if u["url"] not in seen_urls:
                merged.append(u)
                seen_urls.add(u["url"])
        if merged:
            final_results[cid] = merged

    total = sum(len(v) for v in final_results.values())
    logger.info(
        "url_discovery_official_node: 완료 — %d candidate · 총 %d URL "
        "(carry %d + subpage %d, dropped %d, errors %d)",
        len(final_results), total,
        sum(len(v) for v in carried_by_candidate.values()),
        sum(len(v) for v in filtered_subpage.values()),
        dropped_count, len(errors),
    )

    finished_at = datetime.now(timezone.utc).isoformat()
    step: AgentStep = {
        "step_name":   "UrlDiscoveryOfficial",
        "status":      "completed",
        "started_at":  started_at,
        "finished_at": finished_at,
    }
    if errors:
        step["error_message"] = f"{len(errors)}건 부분 실패"

    out: dict = {
        "official_urls_by_candidate": final_results,
        "agent_steps":                [step],
    }
    if errors:
        out["errors"] = errors
    return out


def _host_endswith(url: str, domain: str) -> bool:
    """URL 의 host 가 domain (또는 그 sub-domain) 인지.

    site: 검색 결과 신뢰성 강화 (Brave 의 site: 누락 시 결과 차원에서 재검증).
    예: ("https://www.travelwallet.com/x", "travelwallet.com") → True
        ("https://sub.travelwallet.com/x", "travelwallet.com") → True
        ("https://blog.naver.com/x",       "travelwallet.com") → False
    """
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:  # noqa: BLE001
        return False
    if not host or not domain:
        return False
    if host.startswith("www."):
        host = host[4:]
    return host == domain or host.endswith("." + domain)


def _host_endswith_any(url: str, domains) -> bool:
    """URL host 가 허용 도메인 집합 중 하나라도 매칭하는지 (복수 공식 도메인 게이트).

    한 상품이 복수 공식 도메인(예: tossbank.com + toss.im)을 가질 수 있으므로,
    단일 도메인 검사 _host_endswith 를 집합으로 확장한 ANY 매칭 헬퍼.
    """
    return any(_host_endswith(url, d) for d in (domains or ()) if d)
