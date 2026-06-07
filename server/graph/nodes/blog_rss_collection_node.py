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

from server.graph.agent_cache import load_agent_output, store_agent_output
from server.graph.progress_store import set_progress
from server.graph.state import AgentStep, DomainAnalysisState
from server.graph.nodes.community_collection_node import _robots_allowed

logger = logging.getLogger(__name__)

REPORT_TYPE    = "marketing_social"
_PLATFORMS     = ("blog_naver", "blog_tistory", "blog_self_hosted")
_MAX_POSTS     = 50
_SUMMARY_CHARS = 300   # MS-D10 — RSS 동봉 요약 발췌 상한
_RATE_LIMIT_S  = 1.0
_HTTP_TIMEOUT  = (3, 10)
_CACHE_TTL_H   = 24
_USER_AGENT    = "Mozilla/5.0 (compatible; competitive-intel/1.0)"

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
        status = "rss_unavailable"
        for feed_url in rss_candidate_urls(t["url"], t["platform"], t["handle"]):
            if not _robots_allowed(feed_url):
                status = "robots_disallowed"
                continue
            result = _fetch_rss(feed_url)
            if not result.get("from_cache"):
                time.sleep(_RATE_LIMIT_S)
            if result.get("status") == 200 and result.get("body"):
                posts = parse_feed(result["body"])
                if posts:
                    status = "ok"
                    break
        # v1.0.1 — 강등은 errors 에 적재하지 않는다 (설계된 presence-only 경로).
        # 정보는 fetch_status(state) + step.error_message + PESO 매트릭스 3곳에 이미
        # 기록되며, errors 적재 시 UI 가 "오류 발생" 배너로 오표출 (2026-06-07 E2E).
        if status != "ok":
            logger.info("blog_rss_collection: presence-only 강등 (%s, %s) %s",
                        t["candidate_id"], t["platform"], status)
        blog_rss_posts.append({
            "candidate_id": t["candidate_id"],
            "platform":     t["platform"],
            "blog_url":     t["url"],
            "posts":        posts,
            "fetch_status": status,
        })

    ok_n = sum(1 for b in blog_rss_posts if b["fetch_status"] == "ok")
    step = _step("completed", started_at)
    if ok_n < len(blog_rss_posts):
        step["error_message"] = f"{len(blog_rss_posts) - ok_n}건 presence-only 강등"
    logger.info("blog_rss_collection: %d/%d 피드 수집", ok_n, len(blog_rss_posts))

    return {"blog_rss_posts": blog_rss_posts, "agent_steps": [step]}


def _step(status: str, started_at: str) -> AgentStep:
    return {
        "step_name":   "BlogRssCollection",
        "status":      status,
        "started_at":  started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
