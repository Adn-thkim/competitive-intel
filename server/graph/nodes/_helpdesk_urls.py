"""
고객센터/FAQ URL 탐색 — official_content_collection 보조 소스 주입용 (step 1 + 루트→leaf 확장).
------------------------------------------------------------------------------------
candidate 별로 "자사 도메인 + 공용 헬프데스크 SaaS"를 site: OR 로 묶은 단일 Brave 쿼리로
고객센터/FAQ 진입점(루트/카테고리)을 찾은 뒤, 공용 SaaS(예: docs.channel.io/{brand}) 는
**루트/카테고리 페이지의 링크를 파싱해 값이 담긴 leaf 아티클로 확장**한다.
설계: docs/design/helpdesk_collection_design.md §4.

배경(step 1 관찰): Brave 상위 결과는 고객센터 메인/연락/카테고리 페이지라 값이 없었다.
값(예: "해외 ATM 출금 한도 1일 US$1,000")은 leaf 아티클(/articles/...)에 있으므로,
루트 HTML 에서 아티클 링크를 추출해 feature 키워드 관련도로 상위만 주입한다(추가 Brave quota 0).

  - 반환 URL 은 official_content_collection 의 기존 fetch(trafilatura)가 소비한다.
    링크 추출은 trafilatura(본문만)로 불가하므로 여기서 raw HTML 을 별도로 가져온다.
  - 충돌 해소(§5)는 step 2. 여기서는 origin="helpdesk" provenance 만 남긴다.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import requests

from server.config import BRAVE_SEARCH_API_KEY, HELPDESK_MAX_URLS_PER_CANDIDATE
from server.graph.url_cache import get_brave_results, set_brave_results

logger = logging.getLogger(__name__)

_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
_KEYWORDS = "고객센터 FAQ"
_FALLBACK_SAAS = ["channel.io", "zendesk.com", "intercom.help"]   # 국내 실측 상위
_BRAVE_COUNT = 10
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")
_HTML_TIMEOUT = (3, 6)
_MAX_EXPAND_PAGES = 6          # 링크 추출용 raw HTML fetch 상한(루트+카테고리)
_KW_PER_QUERY = 4             # feature 키워드 재쿼리: 쿼리당 키워드 수
_MAX_KW_QUERIES = 4          # feature 키워드 재쿼리: candidate당 쿼리 상한(quota 보호)
_SAAS_JSON = (Path(__file__).resolve().parents[2]
              / "agents" / "official_source_resolver" / "helpdesk_domains.json")


def _load_saas() -> list[str]:
    try:
        import json
        return json.loads(_SAAS_JSON.read_text(encoding="utf-8"))["hosts"]
    except Exception:  # noqa: BLE001
        return list(_FALLBACK_SAAS)


def _host(url: str) -> str:
    return (urlparse(url).netloc or "").lower().lstrip("www.").replace("m.", "", 1)


def _brand_tokens(brand: str, own_site: str) -> set[str]:
    """브랜드 귀속 판정용 토큰. 한글 브랜드 + own_site 도메인 라벨(하이픈 유무)."""
    toks: set[str] = set()
    b = re.sub(r"\s+", "", (brand or "")).lower()
    if len(b) >= 2:
        toks.add(b)
    label = (own_site or "").split(".")[0].lower()
    for t in (label, label.replace("-", "")):
        if len(t) >= 2:
            toks.add(t)
    return toks


def _build_query(brand: str, own_site: str, saas: list[str]) -> str:
    sites = [own_site] + saas
    return f"{brand} {_KEYWORDS} " + " OR ".join(f"site:{d}" for d in sites)


def _brave(query: str) -> list[dict]:
    cached = get_brave_results(query)
    if cached is not None:
        return cached
    if not BRAVE_SEARCH_API_KEY:
        return []
    try:
        resp = requests.get(
            _ENDPOINT,
            headers={"Accept": "application/json", "Accept-Encoding": "gzip",
                     "X-Subscription-Token": BRAVE_SEARCH_API_KEY},
            params={"q": query, "count": _BRAVE_COUNT}, timeout=(3, 10))
        resp.raise_for_status()
        results = resp.json().get("web", {}).get("results", [])
    except Exception as exc:  # noqa: BLE001
        logger.warning("helpdesk Brave 쿼리 실패(무시): %s", str(exc)[:160])
        return []
    set_brave_results(query, results)
    return results


def _raw_html(url: str) -> str:
    """링크 추출용 raw HTML (trafilatura 는 본문만 남겨 링크가 사라지므로 별도 fetch)."""
    try:
        r = requests.get(url, headers={"User-Agent": _UA},
                        timeout=_HTML_TIMEOUT, allow_redirects=True)
        return r.text or "" if r.status_code == 200 else ""
    except Exception:  # noqa: BLE001
        return ""


def _same_host_links(page_url: str, html: str) -> tuple[set[str], set[str]]:
    """page 와 동일 host 의 (아티클, 카테고리) 링크 집합. #앵커 제거."""
    host = _host(page_url)
    arts: set[str] = set()
    cats: set[str] = set()
    for m in re.finditer(r'href=["\']([^"\']+)', html):
        full = urljoin(page_url, m.group(1)).split("#")[0]
        if _host(full) != host:
            continue
        if "/articles/" in full:
            arts.add(full)
        elif "/categories/" in full:
            cats.add(full)
    return arts, cats


def _score(url: str, keywords: list[str] | None) -> int:
    """디코딩한 URL 에 포함된 feature 키워드 수(관련도). 키워드 없으면 1(동점)."""
    if not keywords:
        return 1
    dec = unquote(url).lower()
    return sum(1 for k in keywords if k and k.lower() in dec)


def _expand_articles(roots: list[str], keywords: list[str] | None,
                     tokens: set[str], cap: int) -> list[str]:
    """루트/카테고리 페이지에서 leaf 아티클 링크를 추출해 관련도 상위 cap 개 반환.

    - 카테고리는 키워드 관련도 높은 것부터 1레벨 확장(전체 fetch 는 _MAX_EXPAND_PAGES 로 제한).
    - 아티클은 브랜드 토큰이 URL 에 있는 것만(타 브랜드 오인 방지).
    """
    articles: dict[str, int] = {}
    pages = list(roots)
    fetched: set[str] = set()
    idx = 0
    while idx < len(pages) and len(fetched) < _MAX_EXPAND_PAGES:
        page = pages[idx]
        idx += 1
        if page in fetched:
            continue
        fetched.add(page)
        html = _raw_html(page)
        if not html:
            continue
        arts, cats = _same_host_links(page, html)
        for a in arts:
            dec = unquote(a).lower()
            if tokens and not any(t in dec for t in tokens):
                continue
            articles[a] = max(articles.get(a, 0), _score(a, keywords))
        for c in sorted(cats, key=lambda u: -_score(u, keywords)):
            if c not in fetched and c not in pages:
                pages.append(c)
    return sorted(articles, key=lambda u: (-articles[u], u))[:cap]


def _feature_keyword_requery(hosts: list[str], brand: str, tokens: set[str],
                             keywords: list[str] | None, cap: int) -> list[str]:
    """helpdesk 도메인 한정 feature 키워드 쿼리로 '값 아티클'을 직접 탐색.

    링크 스크래핑(_expand_articles)이 JS 렌더링 카테고리의 아티클 링크를 못 잡는 문제를
    우회한다. 예: `site:docs.channel.io 트래블월렛 해외 ATM 수수료 한도` → ATM 아티클 직접 회수.
    브랜드 토큰이 URL 경로에 있는 /articles/ 만 채택(타 브랜드 오인 방지).
    """
    kws = [k for k in dict.fromkeys(keywords or []) if k and len(k) >= 2]
    if not kws:
        return []
    articles: dict[str, int] = {}
    queries = 0
    for host in hosts:
        if queries >= _MAX_KW_QUERIES:
            break
        for i in range(0, len(kws), _KW_PER_QUERY):
            if queries >= _MAX_KW_QUERIES:
                break
            cluster = kws[i:i + _KW_PER_QUERY]
            q = f"site:{host} {brand} " + " ".join(cluster)
            queries += 1
            for r in _brave(q):
                url = (r.get("url") or "").strip()
                if not url or "/articles/" not in url:
                    continue
                h = _host(url)
                if h != host:
                    continue
                full = unquote(h + (urlparse(url).path or "")).lower()
                if tokens and not any(t in full for t in tokens):
                    continue
                articles[url] = max(articles.get(url, 0), _score(url, keywords))
    return sorted(articles, key=lambda u: (-articles[u], u))[:cap]


def discover_helpdesk_urls(brand: str, own_site: str,
                           feature_keywords: list[str] | None = None,
                           saas: list[str] | None = None) -> list[str]:
    """candidate 의 고객센터/FAQ URL 목록(최대 HELPDESK_MAX_URLS_PER_CANDIDATE).

    우선순위: 공용 SaaS leaf 아티클(값 페이지) > 자사 도메인 FAQ > 남은 SaaS 루트.
    """
    if not own_site:
        return []
    all_saas = saas if saas is not None else _load_saas()
    tokens = _brand_tokens(brand, own_site)
    cap = HELPDESK_MAX_URLS_PER_CANDIDATE

    results = _brave(_build_query(brand, own_site, list(_FALLBACK_SAAS)))
    own_hits: list[str] = []
    saas_roots: list[str] = []
    seen: set[str] = set()
    for r in results:
        url = (r.get("url") or "").strip()
        if not url or url in seen:
            continue
        host = _host(url)
        full = host + (urlparse(url).path or "").lower()
        if host == own_site or host.endswith("." + own_site):
            own_hits.append(url); seen.add(url)
        elif any(host == d or host.endswith("." + d) for d in all_saas) \
                and any(t in full for t in tokens):
            saas_roots.append(url); seen.add(url)

    hosts = list(dict.fromkeys(_host(u) for u in saas_roots))
    # 1순위: feature 키워드 도메인 재쿼리(값 아티클 직접 회수). 2순위: 링크 스크래핑 확장.
    requery_leaf = _feature_keyword_requery(hosts, brand, tokens, feature_keywords, cap) \
        if hosts else []
    expand_leaf = _expand_articles(saas_roots, feature_keywords, tokens, cap) \
        if saas_roots else []

    ordered: list[str] = []
    for u in requery_leaf + expand_leaf + own_hits + saas_roots:  # 값 아티클 최우선
        if u not in ordered:
            ordered.append(u)
    return ordered[:cap]
