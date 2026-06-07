"""
server/graph/nodes/blog_rss_collection_node.py (v1.0 §6-6a — MS-D2 수집 ②)
---------------------------------------------------------------------------
marketing_social 의 공식 블로그(네이버·티스토리·자체 호스팅) RSS 수집 노드.
설계: docs/design/marketing_social_node_design.md §4-2

책임
----
`owned_channel_urls_by_candidate` 의 blog_naver / blog_tistory / blog_self_hosted
항목에서 RSS 피드를 수집해 게시일·제목 최근 50건을 산출한다 (본문 미수집 —
게시 빈도·키워드(제목) 전용. 참여 지표는 블로그에서 공식 산출 불가, §1-3).

RSS 경로 규약 (MS-D8 — stdlib xml.etree, 신규 의존성 없음)
----------------------------------------------------------
- blog_naver       : rss.blog.naver.com/{blogId}.xml  (blogId = handle)
- blog_tistory     : https://{host}/rss
- blog_self_hosted : {base}/rss → {base}/feed → {base}/atom.xml 순차 시도 (v0.13.4)
- 전부 실패 시 presence-only 강등 (fetch_status="rss_unavailable")

sitemap 폴백 (MS-D13 — blog_self_hosted 한정, 2026-06-07 사용자)
----------------------------------------------------------------
RSS 미제공 자체 호스팅 콘텐츠 허브(예: toss.im/tossfeed)는 sitemap.xml 에서
블로그 경로 하위 URL + lastmod 를 수집하고, 최신 상위 15건의 page meta
(_collect_page_meta 재사용 — 제목·설명·게시일)를 보강해 MS-D10 판정 소스를
확보한다. HTML 목록 파싱은 게시일 부재로 빈도 측정이 불가해 채택하지 않음.
- 게시일 우선순위: 페이지 published_at > sitemap lastmod (수정일 근사 — 한계 명기)
- 성공 시 fetch_status="ok" + collection_method="sitemap"

D11 정책 (community_collection 재사용)
--------------------------------------
robots.txt 준수 + 실제 네트워크 호출 간 1초 대기. 24h TTL agent 캐시.

write keys
----------
- blog_rss_posts : [{candidate_id, platform, blog_url, posts:
                     [{title, published_at, link, summary}], fetch_status}]
  (summary = RSS 동봉 요약 300자 발췌 — MS-D10 상품 관련성 판정용, 추가 fetch 없음)
- agent_steps / errors (누적 reducer)
"""

from __future__ import annotations

import logging
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import requests

from urllib.parse import unquote

from server.graph.agent_cache import load_agent_output, store_agent_output
from server.graph.progress_store import set_progress
from server.graph.state import AgentStep, DomainAnalysisState
from server.graph.nodes.community_collection_node import _robots_allowed
from server.graph.nodes.feature_url_mapper_node import _collect_page_meta

logger = logging.getLogger(__name__)

REPORT_TYPE    = "marketing_social"
_PLATFORMS     = ("blog_naver", "blog_tistory", "blog_self_hosted")
_MAX_POSTS     = 100   # v1.0.4 — 50→100 (12개월 윈도우 커버, 사용자 요청)
_SUMMARY_CHARS = 300   # MS-D10 — RSS 동봉 요약 발췌 상한
_RATE_LIMIT_S  = 1.0
_HTTP_TIMEOUT  = (3, 10)
_CACHE_TTL_H   = 24
_USER_AGENT    = "Mozilla/5.0 (compatible; competitive-intel/1.0)"

# MS-D13 — sitemap 폴백 상한
_SITEMAP_POSTS       = 15   # page meta 보강 대상 (개별 fetch 비용 상한)
_SITEMAP_CHILD_LIMIT = 3    # sitemapindex 의 하위 sitemap 추적 상한

_ATOM_NS = "{http://www.w3.org/2005/Atom}"


def rss_candidate_urls(blog_url: str, platform: str, handle: str = "") -> list[str]:
    """platform 별 RSS 피드 후보 URL 목록 (순수 함수)."""
    parsed = urlparse(blog_url if "//" in blog_url else f"https://{blog_url}")
    base = f"{parsed.scheme or 'https'}://{parsed.netloc}"
    if platform == "blog_naver":
        blog_id = handle or parsed.path.strip("/").split("/")[0]
        return [f"https://rss.blog.naver.com/{blog_id}.xml"] if blog_id else []
    if platform == "blog_tistory":
        return [f"{base}/rss"]
    if platform == "blog_self_hosted":
        return [f"{base}/rss", f"{base}/feed", f"{base}/atom.xml"]
    return []


def _to_iso(date_str: str) -> str:
    """RSS pubDate(RFC 822) 또는 Atom ISO 문자열 → ISO 8601. 실패 시 원문 유지."""
    s = (date_str or "").strip()
    if not s:
        return ""
    try:
        return parsedate_to_datetime(s).isoformat()
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return s


def parse_feed(xml_text: str) -> list[dict]:
    """RSS 2.0 / Atom 피드 → [{title, published_at, link}] (순수 함수).

    파싱 불가 시 빈 리스트 (호출부에서 강등 처리).
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    posts: list[dict] = []
    # RSS 2.0 — <rss><channel><item>
    for item in root.iter("item"):
        posts.append({
            "title":        (item.findtext("title") or "").strip(),
            "published_at": _to_iso(item.findtext("pubDate") or ""),
            "link":         (item.findtext("link") or "").strip(),
            # MS-D10 — RSS 동봉 요약 발췌 (상품 관련성 판정용, 추가 fetch 없음)
            "summary":      (item.findtext("description") or "").strip()[:_SUMMARY_CHARS],
        })
    # Atom — <feed><entry>
    if not posts and root.tag == f"{_ATOM_NS}feed":
        for entry in root.iter(f"{_ATOM_NS}entry"):
            link_el = entry.find(f"{_ATOM_NS}link")
            posts.append({
                "title":        (entry.findtext(f"{_ATOM_NS}title") or "").strip(),
                "published_at": _to_iso(
                    entry.findtext(f"{_ATOM_NS}published")
                    or entry.findtext(f"{_ATOM_NS}updated") or ""),
                "link": (link_el.get("href", "") if link_el is not None else ""),
                "summary": (entry.findtext(f"{_ATOM_NS}summary")
                            or entry.findtext(f"{_ATOM_NS}content") or "").strip()[:_SUMMARY_CHARS],
            })
    return [p for p in posts if p["title"]][:_MAX_POSTS]


def parse_sitemap(xml_text: str) -> tuple[list[dict], list[str]]:
    """sitemap XML → (urlset 항목, 하위 sitemap loc 목록) (순수 함수 — MS-D13).

    네임스페이스 무관 파싱 (tag 끝 이름 기준). urlset 항목: {"link", "lastmod"}.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return [], []
    entries: list[dict] = []
    children: list[str] = []
    is_index = root.tag.rsplit("}", 1)[-1] == "sitemapindex"
    for el in root:
        loc = lastmod = ""
        for sub in el:
            name = sub.tag.rsplit("}", 1)[-1]
            if name == "loc":
                loc = (sub.text or "").strip()
            elif name == "lastmod":
                lastmod = (sub.text or "").strip()
        if not loc:
            continue
        if is_index:
            children.append(loc)
        else:
            entries.append({"link": loc, "lastmod": lastmod})
    return entries, children


def _slug_title(url: str) -> str:
    """URL 마지막 경로 segment → 제목 근사 (퍼센트 인코딩 한글 디코딩)."""
    seg = url.split("?")[0].split("#")[0].rstrip("/").rsplit("/", 1)[-1]
    return unquote(seg).replace("-", " ").replace("_", " ").strip()[:120]


def fetch_sitemap_posts(blog_url: str, fetcher=None, meta_collector=None) -> tuple[list[dict], bool]:
    """MS-D13 — sitemap 에서 블로그 하위 URL 수집 + page meta 보강.

    fetcher / meta_collector 는 테스트 주입용 (기본: _fetch_rss / _collect_page_meta).
    반환: (posts, reachable)
      - posts: [{title, published_at, link, summary}] — 최신 lastmod 우선
      - reachable: sitemap.xml 이 200 으로 도달했는가 (MS-D16 — 도달했으나 블로그
        글이 0건이면 "측정 완료, 게시물 없음"으로 구분하기 위함)
    RSS 피드·sitemap 은 공개 배포 자원이므로 robots 체크를 적용하지 않는다 (MS-D15).
    """
    fetch = fetcher or _fetch_rss
    collect_meta = meta_collector or _collect_page_meta

    rest = blog_url.split("//", 1)[-1]
    host = rest.split("/", 1)[0]
    base = f"https://{host}"
    path_prefix = "/" + rest.split("/", 1)[1].split("?")[0].strip("/") if "/" in rest else ""

    sitemap_url = f"{base}/sitemap.xml"
    result = fetch(sitemap_url)
    if not result.get("from_cache"):
        time.sleep(_RATE_LIMIT_S)
    if result.get("status") != 200 or not result.get("body"):
        return [], False
    reachable = True

    entries, children = parse_sitemap(result["body"])
    # sitemapindex — 블로그 경로 포함 하위 sitemap 우선, 상한 내 추적
    if children:
        ranked = sorted(children, key=lambda u: (path_prefix not in u, u))
        for child in ranked[:_SITEMAP_CHILD_LIMIT]:
            r = fetch(child)
            if not r.get("from_cache"):
                time.sleep(_RATE_LIMIT_S)
            if r.get("status") == 200 and r.get("body"):
                sub_entries, _ = parse_sitemap(r["body"])
                entries.extend(sub_entries)

    # 블로그 경로 하위 + 자기 자신 제외, link 중복 제거, lastmod 내림차순 상위 N
    norm_blog = blog_url.rstrip("/")
    seen: set[str] = set()
    candidates = []
    for e in entries:
        link = e["link"]
        if link in seen or host not in link or link.rstrip("/") == norm_blog:
            continue
        if path_prefix and path_prefix not in link:
            continue
        seen.add(link)
        candidates.append(e)
    candidates.sort(key=lambda e: e.get("lastmod") or "", reverse=True)
    top = candidates[:_SITEMAP_POSTS]
    if not top:
        return [], reachable   # sitemap 도달했으나 블로그 경로 글 0건

    meta_by_url = collect_meta({e["link"] for e in top}) or {}
    posts: list[dict] = []
    for e in top:
        m = meta_by_url.get(e["link"]) or {}
        posts.append({
            "title":        (m.get("page_title") or _slug_title(e["link"]))[:150],
            # 게시일 우선순위: 페이지 published_at > sitemap lastmod (수정일 근사)
            "published_at": m.get("published_at") or e.get("lastmod", ""),
            "link":         e["link"],
            "summary":      (m.get("meta_description") or m.get("body_excerpt") or "")[:_SUMMARY_CHARS],
        })
    return [p for p in posts if p["title"]], reachable


def select_blog_urls(state: dict) -> list[dict]:
    """수집 대상 [{candidate_id, platform, url, handle}] (게이트 — MS-D1)."""
    if REPORT_TYPE not in (state.get("selected_purposes") or []):
        return []
    out: list[dict] = []
    for cid, urls in sorted((state.get("owned_channel_urls_by_candidate") or {}).items()):
        seen: set[str] = set()
        for u in urls or []:
            p = u.get("platform", "")
            url = (u.get("url") or "").strip()
            if p in _PLATFORMS and url and p not in seen:
                seen.add(p)   # candidate × platform 당 대표 1건 (첫 항목 = 노드 산출 순서)
                out.append({"candidate_id": cid, "platform": p, "url": url,
                            "handle": u.get("handle", "")})
    return out


def _fetch_rss(feed_url: str) -> dict:
    """RSS 피드 fetch (24h 캐시). 반환: {status, body, from_cache}."""
    cache_input = {"feed_url": feed_url}
    cache_context = {"agent_id": "blog_rss_fetch", "v": 1}
    cached = load_agent_output(
        agent_id="blog_rss_fetch", cache_input=cache_input,
        context=cache_context, logger=logger, ttl_hours=_CACHE_TTL_H,
    )
    if cached is not None:
        return {**cached, "from_cache": True}

    result = {"status": 0, "body": ""}
    try:
        resp = requests.get(feed_url, timeout=_HTTP_TIMEOUT,
                            headers={"User-Agent": _USER_AGENT})
        result = {"status": resp.status_code,
                  "body": resp.text if resp.ok else ""}
    except requests.RequestException as exc:
        logger.debug("RSS fetch 실패 (%s): %s", feed_url, exc)

    store_agent_output(agent_id="blog_rss_fetch", cache_input=cache_input,
                       context=cache_context, output=result, logger=logger)
    return {**result, "from_cache": False}


def blog_rss_collection_node(
    state: DomainAnalysisState, config: dict | None = None
) -> dict:
    """공식 블로그 RSS 수집 (MS-D2 수집 ②)."""
    started_at = datetime.now(timezone.utc).isoformat()
    thread_id = (config or {}).get("configurable", {}).get("thread_id", "")
    if thread_id:
        try:
            set_progress(thread_id, "marketing_collection", detail="공식 블로그 RSS 수집")
        except Exception as exc:  # noqa: BLE001
            logger.debug("set_progress 실패: %s", exc)

    targets = select_blog_urls(dict(state))
    if not targets:
        return {"agent_steps": [_step("skipped", started_at)]}

    blog_rss_posts: list[dict] = []

    for t in targets:
        posts: list[dict] = []
        # status: ok(글 ≥1) | measured_empty(피드 도달, 글 0) | rss_unavailable(미도달)
        # MS-D15 — RSS·sitemap 은 구독·배포 목적 공개 자원이라 robots 체크 면제.
        status = "rss_unavailable"
        method = "rss"
        for feed_url in rss_candidate_urls(t["url"], t["platform"], t["handle"]):
            result = _fetch_rss(feed_url)
            if not result.get("from_cache"):
                time.sleep(_RATE_LIMIT_S)
            if result.get("status") == 200:
                # MS-D16 — 200 도달 = 측정. 항목 0건이어도 "게시물 없음"으로 측정 처리.
                status = "measured_empty"
                if result.get("body"):
                    posts = parse_feed(result["body"])
                    if posts:
                        status = "ok"
                        break
        # MS-D13 — self_hosted 한정 sitemap 폴백 (RSS 부재 콘텐츠 허브, 예: 토스피드)
        if status != "ok" and t["platform"] == "blog_self_hosted":
            sm_posts, reachable = fetch_sitemap_posts(t["url"])
            if sm_posts:
                posts, status, method = sm_posts, "ok", "sitemap"
            elif reachable:
                status, method = "measured_empty", "sitemap"
        if status not in ("ok", "measured_empty"):
            logger.info("blog_rss_collection: presence-only 강등 (%s, %s) %s",
                        t["candidate_id"], t["platform"], status)
        blog_rss_posts.append({
            "candidate_id":      t["candidate_id"],
            "platform":          t["platform"],
            "blog_url":          t["url"],
            "posts":             posts,
            "fetch_status":      status,
            "collection_method": method,   # MS-D13 — rss | sitemap (lastmod 근사 명기용)
        })

    measured_n = sum(1 for b in blog_rss_posts
                     if b["fetch_status"] in ("ok", "measured_empty"))
    step = _step("completed", started_at)
    if measured_n < len(blog_rss_posts):
        step["error_message"] = f"{len(blog_rss_posts) - measured_n}건 presence-only 강등"
    logger.info("blog_rss_collection: %d/%d 측정", measured_n, len(blog_rss_posts))

    return {"blog_rss_posts": blog_rss_posts, "agent_steps": [step]}


def _step(status: str, started_at: str) -> AgentStep:
    return {
        "step_name":   "BlogRssCollection",
        "status":      status,
        "started_at":  started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
