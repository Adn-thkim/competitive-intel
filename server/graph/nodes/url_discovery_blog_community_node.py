"""
server/graph/nodes/url_discovery_blog_community_node.py (v0.14 — CE-D1 broad query 전면 대체)
--------------------------------------------------------------------------------------------
reaction_insight 의 커뮤니티 게시글 URL 탐색 노드.
설계: docs/design/community_collection_expansion_design.md (CE-D1·D2·D4·D5·D7·D8)

v0.14 전면 개정 (2026-06-12 실측 확정)
--------------------------------------
v0.10.22b 의 hint 기반 검색(feature 키워드 포함)을 폐기하고 broad query 로 대체:

1. **CE-D1 broad query** — `site:{community_domain} {candidate_name}`.
   feature·aspect 키워드를 넣지 않는다. 근거: ① 리콜 실측 (Google: "site:clien.net
   토스 트래블카드" 67건 vs +"후기" 9건), ② ABSA 가 aspect 단위로 분석 시점에
   주제를 늦게 할당하므로 검색 단계 주제 분류가 불필요.
2. **CE-D2 2단 화이트리스트** — config.COMMUNITY_SITES_FIXED(1군 6개, collection_mode
   포함) + taxonomy `community_sites`(2군 0~2개, registry 한정).
3. **CE-D1·D7 페이지네이션** — 쿼리당 최대 COMMUNITY_BRAVE_MAX_PAGES(6) 페이지.
   실측: Brave 깊이 한계 = 6페이지(120건) 포화. 직전 페이지 만석(20건)일 때만 다음
   페이지 호출. 페이지 간 동일 URL 반복이 실측 확인됨 → dedup 필수 (CE-D8).
4. **CE-D4 관련성 필터 (완화 모드)** — aspect_codebook label+definition 명사 토큰
   (범용어 제거) + 도메인 토큰이 title+스니펫에 **하나도 없을 때만** 제외.
   경계 사례는 통과 — 최종 거름망은 ABSA (RI-D10 사상).
5. **CE-D5 잠정 귀속** — 동일 URL 이 복수 candidate 쿼리에서 발견되면 1회만 수집,
   `matched_candidates` 병합. 최종 귀속은 ABSA target 재귀속(별도 PR)의 책임.
6. **CE-D8 dedup** — 스킴·www·추적 파라미터 제거. 모바일 호스트(m.*) 무차별 치환
   금지 (실측: m.ppomppu 경로는 PC 도메인에 부재 → 404).

폐기된 책임 (v0.10.22b → v0.14)
--------------------------------
- hint 기반 검색·blog 계열(personal_blog·review_site·wiki) 분류 — blog 수집 비활성
  확정(CE-D3)으로 소비 노드 부재. domain_modeling 프롬프트도 blog_community hint
  생성을 중단했다.
- 공식 도메인 제외(D36) — `site:` 한정 검색 + `_host_matches_site` 가드로 공식
  도메인이 결과에 진입할 수 없어 별도 제외가 불필요.

위치 (v0.14 CE-D9 — cross_reference 우회)
------------------------------------------
ab_join ─→ [url_discovery_blog_community] ─→ feature_mapping_blog_community (직결)
(다른 4개 discovery 노드는 기존대로 cross_reference 4-in barrier 로 fan-in)

write keys
----------
- blog_community_urls_by_candidate : {잠정 candidate_id: [entry, ...]}
  entry = {url, page_title, meta_description, published_at, site, collection_mode,
           domain_class="community", origin="blog_community", matched_candidates,
           feature_ids=[], matched_report_types=["reaction_insight"]}
- agent_steps / errors (누적 reducer)

graceful 종료
-------------
- BRAVE_SEARCH_API_KEY 미설정: _brave_search 가 빈 결과 → 빈 dict + completed
- reaction_insight 비활성: 탐색 생략 + completed
"""

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse

from server.config import (
    COMMUNITY_BRAVE_MAX_PAGES,
    COMMUNITY_REGISTRY_PATH,
    COMMUNITY_SITES_FIXED,
)
from server.graph.progress_store import set_progress
from server.graph.state import DomainAnalysisState
from server.graph.nodes.feature_url_mapper_node import (
    _brave_search,
    _candidate_name_map,
    _extract_active_reports,
    _error,
)

logger = logging.getLogger(__name__)

REPORT_TYPE  = "reaction_insight"
_SOURCE_TYPE = "blog_community"
_BRAVE_PAGE_SIZE = 20   # Brave count 상한 — 페이지 만석 판정 기준


# ─── CE-D4 — 관련성 필터 토큰 ────────────────────────────────────────────────

# 범용어 — 거의 모든 후기 글에 등장해 변별력이 없는 수식·추상 명사
_GENERIC_TOKENS = frozenset({
    "앱", "비용", "혜택", "품질", "경험", "편의", "편의성", "인식", "체감", "가치",
    "지원", "기능", "서비스", "사용", "사용자", "사용성", "과정", "상황", "능력",
    "속도", "수준", "제공", "이용", "대비", "처리", "확인", "관련", "대한", "대해",
    "위해", "통해", "경우", "정도", "이상", "이하", "시장", "고객",
})
_TOKEN_RE = re.compile(r"[가-힣A-Za-z0-9]{2,}")
# utm_* 는 접두 일치, 나머지는 전체 일치 (re.match 는 시작 앵커만 가짐)
_TRACKING_PARAM_RE = re.compile(r"^(utm_|fbclid$|gclid$|igshid$|ref$)")


def _build_filter_tokens(domain_taxonomy: dict, domain_name: str) -> frozenset[str]:
    """CE-D4 — aspect_codebook label+definition + feature_labels + domain_name 토큰화.

    범용어(_GENERIC_TOKENS)·순수 숫자를 제거한 명사 토큰 집합. 파일럿 실측 예:
    해외결제·환율·환전·충전·외화·ATM·출금·수수료·한도·분실·도난·잠금·보험·라운지 등.
    """
    entry = ((domain_taxonomy.get("report_config") or {}).get(REPORT_TYPE) or {})
    texts: list[str] = [domain_name]
    for a in entry.get("aspect_codebook") or []:
        if isinstance(a, dict):
            texts.append(a.get("label", ""))
            texts.append(a.get("definition", ""))
        elif isinstance(a, str):
            texts.append(a)
    for label in (entry.get("feature_labels") or {}).values():
        texts.append(str(label))

    tokens = {
        t for text in texts for t in _TOKEN_RE.findall(text)
        if not t.isdigit()
    }
    return frozenset(tokens - _GENERIC_TOKENS)


def _passes_relevance(title: str, snippet: str, tokens: frozenset[str]) -> bool:
    """CE-D4 완화 모드 — 토큰이 하나도 없을 때만 제외. 토큰 집합이 비면 전부 통과."""
    if not tokens:
        return True
    text = f"{title} {snippet}"
    return any(t in text for t in tokens)


# ─── CE-D8 — URL 정규화 dedup ────────────────────────────────────────────────

def _normalize_for_dedup(url: str) -> str:
    """dedup 키 — 스킴·www·추적 파라미터 제거. 모바일 호스트 무차별 치환 금지.

    (실측 2026-06-12: m.ppomppu.co.kr/new/* 경로는 PC 도메인에 존재하지 않아
    'm.' 제거 시 404 URL 이 된다. 사이트별 모바일↔PC 매핑은 수집 노드의 책임.)
    """
    try:
        p = urlparse(url)
    except Exception:  # noqa: BLE001
        return url
    host = (p.hostname or "").lower().removeprefix("www.")
    query = urlencode([
        (k, v) for k, v in parse_qsl(p.query)
        if not _TRACKING_PARAM_RE.match(k)
    ])
    key = f"{host}{p.path.rstrip('/')}"
    return f"{key}?{query}" if query else key


def _host_matches_site(url: str, site: str) -> bool:
    """결과 URL 이 실제로 해당 커뮤니티 도메인인지 검증 (site: 연산자 누수 방어).

    서브도메인(m.* · gall.* 등) 은 일치로 간주한다.
    """
    try:
        host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    except Exception:  # noqa: BLE001
        return False
    return host == site or host.endswith("." + site)


# ─── CE-D2 — 사이트 목록 (1군 고정 + 2군 taxonomy 선정) ──────────────────────

def _registry_modes() -> dict[str, str]:
    """registry {domain: collection_mode}. 부재 시 빈 dict (2군 미선정 graceful)."""
    try:
        data = json.loads(Path(COMMUNITY_REGISTRY_PATH).read_text(encoding="utf-8"))
        return {
            s["domain"]: s.get("collection_mode", "snippet_only")
            for s in data.get("sites", []) if s.get("domain")
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("community_registry 로드 실패: %s", exc)
        return {}


def _resolve_sites(domain_taxonomy: dict) -> dict[str, str]:
    """1군 고정 + taxonomy community_sites(registry 검증) → {domain: collection_mode}."""
    sites = dict(COMMUNITY_SITES_FIXED)
    registry = _registry_modes()
    for domain in domain_taxonomy.get("community_sites") or []:
        if domain in COMMUNITY_SITES_FIXED:
            continue
        if domain in registry:
            sites[domain] = registry[domain]
        else:
            logger.warning("community_sites registry 외 도메인 무시: %s", domain)
    return sites


# ─── 메인 노드 ───────────────────────────────────────────────────────────────

def url_discovery_blog_community_node(
    state: DomainAnalysisState, config: dict | None = None,
) -> dict:
    """v0.14 — broad query 커뮤니티 URL 탐색 (CE-D1·D2·D4·D5·D8)."""
    started_at = datetime.now(timezone.utc).isoformat()
    thread_id  = (config or {}).get("configurable", {}).get("thread_id", "")

    print(
        f"📝 [url_discovery_blog_community_node] ENTRY at {started_at} "
        f"thread_id={thread_id!r} (v0.14 broad)",
        flush=True,
    )

    if thread_id:
        try:
            set_progress(
                thread_id, "feature_mapping_brave",
                detail="커뮤니티 게시글 URL 탐색 (broad site: 검색)",
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("set_progress(blog_community) 실패: %s", exc)

    # ── 입력 수집 ───────────────────────────────────────────────────────────
    domain_taxonomy: dict       = state.get("domain_taxonomy") or {}
    domain_name: str            = state.get("domain_name") or ""
    own_product: dict           = state.get("own_product") or {}
    competitor_candidates: list = state.get("competitor_candidates") or []
    selected_ids: list[str]     = state.get("selected_competitor_ids") or []

    if not domain_taxonomy:
        return _error(started_at, "domain_taxonomy 가 state 에 없습니다.")
    if not domain_name:
        return _error(started_at, "domain_name 이 state 에 없습니다.")

    def _done(urls_by_candidate: dict) -> dict:
        return {
            "blog_community_urls_by_candidate": urls_by_candidate,
            "agent_steps": [{
                "step_name":   "UrlDiscoveryBlogCommunity",
                "status":      "completed",
                "started_at":  started_at,
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }],
        }

    # reaction_insight 비활성 시 탐색 생략 (본 source 의 유일 소비 리포트 — CE-D3)
    if REPORT_TYPE not in _extract_active_reports(domain_taxonomy):
        logger.info("url_discovery_blog_community_node: %s 비활성 — 탐색 생략", REPORT_TYPE)
        return _done({})

    # ── 쿼리 구성 — 사이트 × candidate ──────────────────────────────────────
    sites = _resolve_sites(domain_taxonomy)
    name_map = _candidate_name_map(own_product, competitor_candidates, selected_ids)
    name_map.pop("own", None)   # fallback 키 중복 회피 (youtube_reactions 노드와 동일)
    tokens = _build_filter_tokens(domain_taxonomy, domain_name)

    if not name_map:
        logger.info("url_discovery_blog_community_node: candidate 0건 — 탐색 생략")
        return _done({})

    logger.info(
        "url_discovery_blog_community_node: broad 탐색 시작 — 사이트 %d × candidate %d "
        "(최대 %d페이지/쿼리, 필터 토큰 %d개)",
        len(sites), len(name_map), COMMUNITY_BRAVE_MAX_PAGES, len(tokens),
    )

    # ── 검색 + dedup + matched_candidates 병합 (CE-D5·D8) ───────────────────
    # posts[dedup_key] = entry. 잠정 귀속 = 최초 발견 candidate (결정론 순회 순서).
    posts: dict[str, dict] = {}
    stats = {"calls": 0, "raw": 0, "off_site": 0, "filtered": 0}

    for site in sorted(sites):
        mode = sites[site]
        for cid in sorted(name_map):
            query = f"site:{site} {name_map[cid]}"
            for page in range(COMMUNITY_BRAVE_MAX_PAGES):
                results = _brave_search(query, count=_BRAVE_PAGE_SIZE, offset=page)
                stats["calls"] += 1
                stats["raw"]   += len(results)
                for r in results:
                    url = r.get("url") or ""
                    if not url or not _host_matches_site(url, site):
                        stats["off_site"] += 1
                        continue
                    title   = r.get("title", "") or r.get("page_title", "")
                    snippet = r.get("description", "") or r.get("meta_description", "")
                    if not _passes_relevance(title, snippet, tokens):
                        stats["filtered"] += 1
                        continue
                    key = _normalize_for_dedup(url)
                    entry = posts.setdefault(key, {
                        "url":              url,
                        "page_title":       title,
                        "meta_description": snippet,
                        "published_at":     r.get("page_age", ""),
                        "site":             site,
                        "collection_mode":  mode,
                        "domain_class":     "community",
                        "origin":           _SOURCE_TYPE,
                        "feature_ids":      [],
                        "matched_report_types": [REPORT_TYPE],
                        "_primary_cid":     cid,
                        "_matched":         set(),
                    })
                    entry["_matched"].add(cid)
                if len(results) < _BRAVE_PAGE_SIZE:
                    break   # 페이지 미만석 — 다음 페이지 없음 (조건부 페이지네이션)

    # ── 잠정 candidate 별 그룹화 (CE-D5) ────────────────────────────────────
    urls_by_candidate: dict[str, list[dict]] = {}
    for entry in posts.values():
        cid = entry.pop("_primary_cid")
        entry["matched_candidates"] = sorted(entry.pop("_matched"))
        urls_by_candidate.setdefault(cid, []).append(entry)
    for cid in urls_by_candidate:
        urls_by_candidate[cid].sort(key=lambda e: e["url"])   # 결정론 순서

    total = sum(len(v) for v in urls_by_candidate.values())
    multi = sum(
        1 for v in urls_by_candidate.values() for e in v
        if len(e["matched_candidates"]) >= 2
    )
    logger.info(
        "url_discovery_blog_community_node: 완료 — %d candidate · 고유 %d URL "
        "(Brave %d호출, raw %d, off_site %d, 필터 제외 %d, 다중 발견 %d)",
        len(urls_by_candidate), total, stats["calls"], stats["raw"],
        stats["off_site"], stats["filtered"], multi,
    )
    return _done(urls_by_candidate)
