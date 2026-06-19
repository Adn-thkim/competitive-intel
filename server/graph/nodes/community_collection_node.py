"""
server/graph/nodes/community_collection_node.py (v0.13 — reaction_insight 시리즈)
----------------------------------------------------------------------------------
reaction_insight 의 커뮤니티 채널 수집 노드.
설계: docs/design/reaction_insight_node_design.md §4 (D11 정책)
※ 2026-06-11 분리: blog 계열(personal_blog/review_site/wiki)은 blog_collection_node
  (분리·미배선/휴면)가 담당하고, 본 노드는 domain_class="community" 만 활성 수집한다.
  수집 코어 `_collect_posts` 를 두 노드가 공유한다.

책임
----
analysis_features 의 blog_community origin URL 중 domain_class="community" 항목에서
게시글 본문을 수집한다. fetch 는 official_content_collection 의 `_fetch_content`
(Trafilatura + 24h 전문 캐시) 를 재사용한다.

D11 정책
--------
- robots.txt 준수: host 별 1회 조회·캐시. disallow URL 은 수집 제외 (robots_disallowed).
  robots.txt 는 본문 수집과 **동일 UA(requests)** 로 조회한다 — urllib 기본 UA 를
  쓰면 일부 사이트(예: clien)가 403 을 주고 robotparser 가 disallow_all 로 전면
  금지 처리해 정상 허용 경로까지 스킵되는 false-negative 가 발생하기 때문.
  401/403 만 진짜 차단으로 간주하고, 그 외 4xx/5xx·네트워크 오류는 허용으로 간주
  (RFC 9309 관행).
- rate limit: 실제 네트워크 fetch 간 1초 대기 (캐시 적중은 대기 없음 — from_cache 플래그).
- 작성자 식별정보 비저장: 본문 텍스트만 보존.

선별 상한 (RI-D4 보완)
----------------------
feature당 최신(published_at, 부재 시 url 사전순) 상위 3 URL → candidate union →
candidate당 8 초과 시 feature 커버리지 우선 greedy 절단. 본문은 절단 없이 전체 적재
(_fetch_content 내부 안전 상한 _FULLTEXT_CAP=50,000자 만 유지). 200자 SPA 임계도
적용하지 않는다 — content 가 있으면 fetch_status 무관하게 적재한다.

write keys
----------
- community_posts : [{url, candidate_id, feature_ids, domain_class, title,
                      body_excerpt, published_at, fetch_status}]
- agent_steps / errors (누적 reducer)
"""

from __future__ import annotations

import logging
import time
import urllib.robotparser
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests

from server.graph.progress_store import set_progress
from server.graph.state import AgentStep, DomainAnalysisState
from server.graph.nodes.official_content_collection_node import (
    _fetch_content,
    _FETCH_USER_AGENT,
    _FETCH_TIMEOUT,
)

logger = logging.getLogger(__name__)

REPORT_TYPE = "reaction_insight"
_ORIGIN     = "blog_community"

# RI-D4 보완 — 커뮤니티 선별 상한
_URLS_PER_FEATURE   = 3
_URLS_PER_CANDIDATE = 8
_RATE_LIMIT_SEC     = 1.0     # D11 — 실제 네트워크 호출 간 대기

# domain_class 분리 (blog/community 수집 노드 분리 — 사용자 2026-06-11)
# community 노드는 'community' 만 활성. blog 계열은 blog_collection_node(미배선) 담당.
_COMMUNITY_CLASSES = frozenset({"community"})
_BLOG_CLASSES      = frozenset({"personal_blog", "review_site", "wiki"})

# robots.txt host 별 캐시 (프로세스 수명)
_robots_cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}


# ─── robots.txt (D11) ────────────────────────────────────────────────────────

def _load_robots(host: str) -> urllib.robotparser.RobotFileParser | None:
    """robots.txt 를 본문 수집과 동일 UA(requests) 로 조회·파싱.

    urllib.robotparser.read() 는 기본 UA(Python-urllib)로 robots.txt 를 받는데,
    일부 사이트(예: clien)는 이 UA 에 403 을 주고 robotparser 는 이를
    disallow_all=True 로 전면 금지 처리한다. 그러면 실제 robots 가 허용하는 경로까지
    스킵되는 false-negative 가 생긴다. 이를 막기 위해 _fetch_content 와 동일 UA 로
    robots.txt 를 받아 정상 규칙을 적용한다.

    Returns
    -------
    RobotFileParser | None
        None  : robots.txt 부재/조회불가 → 호출부에서 허용으로 간주.
        parser: 401/403 이면 disallow_all=True 파서, 200 이면 파싱된 규칙.
    """
    rp = urllib.robotparser.RobotFileParser()
    try:
        resp = requests.get(
            f"{host}/robots.txt", timeout=_FETCH_TIMEOUT,
            headers={"User-Agent": _FETCH_USER_AGENT},
        )
    except Exception as exc:  # noqa: BLE001 — 조회 불가 → 허용 간주
        logger.debug("robots.txt 조회 실패 (%s): %s", host, exc)
        return None
    if resp.status_code in (401, 403):
        rp.disallow_all = True          # 진짜 인증 차단 → 전면 금지
        return rp
    if resp.status_code >= 400:
        return None                     # 기타 4xx/5xx → robots 부재 간주 → 허용
    rp.parse(resp.text.splitlines())
    return rp


def _robots_allowed(url: str) -> bool:
    """host 의 robots.txt 기준 수집 허용 여부. 조회 불가 시 허용으로 간주."""
    try:
        parsed = urlparse(url)
        host = f"{parsed.scheme}://{parsed.netloc}"
    except Exception:  # noqa: BLE001
        return True
    if host not in _robots_cache:
        _robots_cache[host] = _load_robots(host)
    rp = _robots_cache[host]
    return True if rp is None else rp.can_fetch("*", url)


# ─── URL 선별 (순수 함수) ────────────────────────────────────────────────────

def select_community_urls(
    state: dict, accept_classes: frozenset[str] = _COMMUNITY_CLASSES,
) -> dict[str, list[dict]]:
    """candidate_id → 선별 URL 목록. 필터 3단계 + RI-D4 보완 상한.

    accept_classes: 채택할 domain_class 집합. community 노드는 {'community'} 만,
    blog 노드는 {'personal_blog','review_site','wiki'} 를 넘긴다.
    """
    if REPORT_TYPE not in (state.get("selected_purposes") or []):
        return {}
    selected_fids = set(state.get("selected_feature_ids") or [])

    pool: dict[str, dict[str, dict]] = {}
    for feat in state.get("analysis_features") or []:
        fid = feat.get("feature_id", "")
        if feat.get("report_type") != REPORT_TYPE or fid not in selected_fids:
            continue
        for cov in feat.get("candidate_coverage") or []:
            cid = cov.get("candidate_id", "")
            if not cid:
                continue
            urls = [
                u for u in (cov.get("existing_urls") or [])
                if u.get("origin") == _ORIGIN
                and u.get("domain_class") in accept_classes
                and (u.get("url") or "").strip()
            ]
            # feature당 상위 N — 발행일 최신순 (부재 시 url 사전순 뒤로)
            urls.sort(key=lambda u: (-(len(u.get("published_at") or "") > 0),
                                     u.get("published_at", ""), u["url"]))
            urls.reverse()   # published_at 내림차순 근사 (있음 우선 + 최신 우선)
            for u in urls[:_URLS_PER_FEATURE]:
                rec = pool.setdefault(cid, {}).setdefault(u["url"], {
                    "url":          u["url"],
                    "domain_class": u.get("domain_class", ""),
                    "published_at": u.get("published_at", ""),
                    "feature_ids":  set(),
                })
                rec["feature_ids"].add(fid)

    selected: dict[str, list[dict]] = {}
    for cid, by_url in pool.items():
        records = sorted(by_url.values(), key=lambda r: r["url"])
        uncovered = set().union(*(r["feature_ids"] for r in records))
        chosen: list[dict] = []
        remaining = list(records)
        while uncovered and len(chosen) < _URLS_PER_CANDIDATE:
            best = min(remaining, key=lambda r: (
                -len(r["feature_ids"] & uncovered), r["url"]))
            if not best["feature_ids"] & uncovered:
                break
            chosen.append(best)
            remaining.remove(best)
            uncovered -= best["feature_ids"]
        for r in remaining:
            if len(chosen) >= _URLS_PER_CANDIDATE:
                break
            chosen.append(r)
        selected[cid] = [
            {**r, "feature_ids": sorted(r["feature_ids"])}
            for r in sorted(chosen, key=lambda r: r["url"])
        ]
    return selected


def _title_of(content: str) -> str:
    """markdown 본문 첫 헤딩(또는 첫 줄)을 제목으로 사용."""
    for line in content.splitlines():
        line = line.strip()
        if line:
            return line.lstrip("# ").strip()[:120]
    return ""


# ─── 메인 노드 ───────────────────────────────────────────────────────────────

def _collect_posts(
    state: DomainAnalysisState, config: dict | None, *,
    accept_classes: frozenset[str], write_key: str,
    step_name: str, node_name: str, progress_detail: str,
) -> dict:
    """blog_community origin URL 본문 수집 공통 코어 (D11 정책).

    community_collection / blog_collection 두 노드가 domain_class 집합과 출력 키만
    바꿔 재사용한다.
    """
    started_at = datetime.now(timezone.utc).isoformat()
    thread_id = (config or {}).get("configurable", {}).get("thread_id", "")
    if thread_id:
        try:
            set_progress(thread_id, "reaction_collection", detail=progress_detail)
        except Exception as exc:  # noqa: BLE001
            logger.debug("set_progress 실패: %s", exc)

    selection = select_community_urls(dict(state), accept_classes)
    if not selection:
        return {"agent_steps": [_step("skipped", started_at, step_name)]}

    errors: list[dict] = []
    posts: list[dict] = []
    stats = {"ok": 0, "robots": 0, "dynamic": 0, "failed": 0}

    for cid in sorted(selection):
        for rec in selection[cid]:
            url = rec["url"]
            if not _robots_allowed(url):
                stats["robots"] += 1
                logger.info("%s: robots.txt disallow skip (%s)", node_name, url)
                continue

            result = _fetch_content(url)
            status = result.get("fetch_status", "fetch_failed")
            content = result.get("content", "") or ""
            if not result.get("from_cache"):
                time.sleep(_RATE_LIMIT_SEC)   # D11 — 실제 네트워크 호출 간 대기

            # 200자 SPA 임계 미적용 — content 가 있으면 fetch_status 무관하게 적재.
            # 본문은 절단 없이 전체 보존 (_fetch_content 의 _FULLTEXT_CAP 만 적용됨).
            if content.strip():
                stats["ok"] += 1
                posts.append({
                    "url":          url,
                    "candidate_id": cid,
                    "feature_ids":  rec["feature_ids"],
                    "domain_class": rec["domain_class"],
                    "title":        _title_of(content),
                    "body_excerpt": content,
                    "published_at": rec["published_at"],
                    "fetch_status": status,
                })
            else:
                key = "dynamic" if status == "requires_dynamic_render" else "failed"
                stats[key] += 1
                errors.append({
                    "node": node_name,
                    "error": f"{status}: {url[:80]}",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

    step = _step("completed", started_at, step_name)
    skipped_n = stats["robots"] + stats["dynamic"] + stats["failed"]
    if skipped_n:
        step["error_message"] = (
            f"{skipped_n}건 수집 제외 (robots {stats['robots']} · "
            f"dynamic {stats['dynamic']} · 실패 {stats['failed']})")
    logger.info("%s: 게시글 %d건 수집 (%s)", node_name, len(posts), stats)

    out: dict = {write_key: posts, "agent_steps": [step]}
    if errors:
        out["errors"] = errors
    return out


# ═══ v0.14 — CE-D3·D6·D10 + §3 collection_mode 분기 수집 ═════════════════════
# 설계: docs/design/community_collection_expansion_design.md
# 기존 _collect_posts·select_community_urls 는 blog_collection_node(휴면) 호환을
# 위해 유지하고, community 경로는 아래 신규 구현으로 대체한다.

import re as _re
from concurrent.futures import ThreadPoolExecutor as _Pool

from server.cache_ttl import get_ttl_hours
from server.config import (
    COMMUNITY_CHUNK_CHARS,
    COMMUNITY_MAX_CHUNKS,
    COMMUNITY_SITES_FIXED,
    COMMUNITY_URLS_PER_CANDIDATE,
    COMMUNITY_URLS_PER_SITE,
)
from server.graph.agent_cache import load_agent_output, store_agent_output
from server.graph.nodes._feature_mapping_runner import COMMUNITY_POOL_FEATURE_ID

_HOST_PARALLEL = 3   # §3-6 — host 간 병렬 (host 내부는 직렬 + 1s rate limit)

# 브라우저/모바일 UA — CE-D7 실측: 비브라우저 UA 는 clien 메뉴 오염·dcinside
# 보일러플레이트를 유발. dcinside 는 모바일 URL 변환 + 모바일 UA 필수.
_BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
               "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_MOBILE_UA  = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
               "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
               "Mobile/15E148 Safari/604.1")
_DC_VIEW_RE = _re.compile(
    r"gall\.dcinside\.com/(?:mgallery/|mini/)?board/view/?\?id=([^&]+)&no=(\d+)")
_DC_PATH_RE = _re.compile(r"gall\.dcinside\.com/board/([^/?]+)/(\d+)")

_ERROR_PAGE_SIGNS = ("찾을 수 없", "없는 게시물", "삭제된 게시물", "삭제되었",
                     "존재하지 않는")
_NAV_SIGNS = ("본문 바로가기", "메뉴 바로가기")

_FETCH_AGENT_ID_V14  = "community_content_fetch"
_FETCH_CACHE_CTX_V14 = {"agent_id": _FETCH_AGENT_ID_V14, "v": 1}
_FETCH_CACHE_TTL_H   = get_ttl_hours("community_fetch_hours", 720)  # cache_ttls.yaml
# 문장 경계 분할 — 한국어 종결어미·구두점 뒤 공백
_SENTENCE_SPLIT_RE = _re.compile(r"(?<=[.!?…다요죠임함됨])\s+")


def _fetch_target_v14(url: str) -> tuple[str, str]:
    """fetch 대상 URL·UA 결정. dcinside 는 모바일 변환 + 모바일 UA (CE-D7 실측)."""
    m = _DC_VIEW_RE.search(url) or _DC_PATH_RE.search(url)
    if m:
        return f"https://m.dcinside.com/board/{m.group(1)}/{m.group(2)}", _MOBILE_UA
    return url, _BROWSER_UA


def _fetch_community_content(url: str) -> dict:
    """본문+댓글 추출 (브라우저 UA + trafilatura favor_precision + 24h 캐시).

    official 경로의 _fetch_content 를 쓰지 않는 이유 (의도된 분리):
    ① UA 정책이 다름 (브라우저/모바일 UA 필수 — CE-D7 실측), ② 댓글 추출
    (include_comments) 필요, ③ 캐시 키·산출 형태가 다름.

    반환: {body, comments, fetch_status, from_cache}
    """
    cached = load_agent_output(
        agent_id=_FETCH_AGENT_ID_V14, cache_input={"url": url},
        context=_FETCH_CACHE_CTX_V14, ttl_hours=_FETCH_CACHE_TTL_H, logger=logger,
    )
    if cached is not None:
        return {**cached, "from_cache": True}

    import trafilatura  # 지연 import

    target, ua = _fetch_target_v14(url)
    try:
        resp = requests.get(target, timeout=_FETCH_TIMEOUT,
                            headers={"User-Agent": ua})
    except Exception as exc:  # noqa: BLE001
        return {"body": "", "comments": "",
                "fetch_status": f"fetch_failed: {type(exc).__name__}",
                "from_cache": False}
    if resp.status_code >= 400:
        return {"body": "", "comments": "",
                "fetch_status": f"http_{resp.status_code}", "from_cache": False}

    try:
        doc = trafilatura.bare_extraction(
            resp.text, include_comments=True, url=target, favor_precision=True)
    except Exception as exc:  # noqa: BLE001
        return {"body": "", "comments": "",
                "fetch_status": f"extract_failed: {type(exc).__name__}",
                "from_cache": False}
    if doc is None:
        return {"body": "", "comments": "", "fetch_status": "extract_failed",
                "from_cache": False}

    body = getattr(doc, "text", "") or (doc.get("text", "") if isinstance(doc, dict) else "")
    comments = getattr(doc, "comments", "") or (doc.get("comments", "") if isinstance(doc, dict) else "")
    status = "ok"
    if any(s in body[:300] for s in _ERROR_PAGE_SIGNS):
        status = "error_page"
    elif any(s in body[:100] for s in _NAV_SIGNS):
        status = "nav_contaminated"

    result = {"body": body or "", "comments": comments or "", "fetch_status": status}
    if status == "ok":   # 실패류는 캐시하지 않음 (재시도 보존)
        try:
            store_agent_output(
                agent_id=_FETCH_AGENT_ID_V14, cache_input={"url": url},
                context=_FETCH_CACHE_CTX_V14, output=result, logger=logger)
        except Exception as exc:  # noqa: BLE001
            logger.debug("community fetch 캐시 저장 실패: %s", exc)
    return {**result, "from_cache": False}


def _chunk_text(text: str, chunk_chars: int = COMMUNITY_CHUNK_CHARS,
                max_chunks: int = COMMUNITY_MAX_CHUNKS) -> list[str]:
    """§3-4 — 문장 경계 chunking. 요약·문장 중간 절단 금지 (quote 실존 검증 보존).

    chunk_chars 단위로 문장 경계에서 분할, 최대 max_chunks (초과분은 tail drop —
    초장문·도배 방어용 절대 상한).
    """
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= chunk_chars:
        return [text]
    sentences = _SENTENCE_SPLIT_RE.split(text)
    chunks: list[str] = []
    buf = ""
    for s in sentences:
        if buf and len(buf) + len(s) + 1 > chunk_chars:
            chunks.append(buf)
            if len(chunks) >= max_chunks:
                return chunks
            buf = s
        else:
            buf = f"{buf} {s}".strip() if buf else s
        # 단일 문장이 chunk_chars 를 초과하는 비정상 입력 — 강제 절단 (방어)
        while len(buf) > chunk_chars:
            chunks.append(buf[:chunk_chars])
            if len(chunks) >= max_chunks:
                return chunks
            buf = buf[chunk_chars:]
    if buf and len(chunks) < max_chunks:
        chunks.append(buf)
    return chunks


def select_community_pool(state: dict) -> dict[str, list[dict]]:
    """CE-D3 — placeholder feature 의 수집 풀 선별 (report 게이트만 적용).

    selected_feature_ids 게이트를 적용하지 않는다 — broad 수집 풀은 feature 단위
    선택 대상이 아니며 사용자 통제는 리포트 선택(selected_purposes)으로 유지.

    선별 (§3): 사이트당 published_at 최신순 → round-robin 사이트 다양성 우선,
    사이트당 COMMUNITY_URLS_PER_SITE(10) · candidate당 COMMUNITY_URLS_PER_CANDIDATE(40).
    """
    if REPORT_TYPE not in (state.get("selected_purposes") or []):
        return {}

    pool_feature = next(
        (f for f in state.get("analysis_features") or []
         if f.get("feature_id") == COMMUNITY_POOL_FEATURE_ID
         and f.get("report_type") == REPORT_TYPE),
        None,
    )
    if pool_feature is None:
        return {}

    selected: dict[str, list[dict]] = {}
    for cov in pool_feature.get("candidate_coverage") or []:
        cid = cov.get("candidate_id", "")
        if not cid:
            continue
        by_site: dict[str, list[dict]] = {}
        for u in cov.get("existing_urls") or []:
            if not (u.get("url") or "").strip():
                continue
            by_site.setdefault(u.get("site", ""), []).append(u)
        # 사이트 내 정렬: published_at 보유 우선 + 최신순 (부재 시 url 사전순 뒤)
        for site_urls in by_site.values():
            site_urls.sort(key=lambda u: (
                -(len(u.get("published_at") or "") > 0),
                u.get("published_at", ""), u["url"]))
            site_urls.reverse()

        # round-robin — 사이트 다양성 우선
        chosen: list[dict] = []
        idx = {site: 0 for site in by_site}
        sites_order = sorted(by_site)
        while len(chosen) < COMMUNITY_URLS_PER_CANDIDATE:
            progressed = False
            for site in sites_order:
                if len(chosen) >= COMMUNITY_URLS_PER_CANDIDATE:
                    break
                i = idx[site]
                if i < min(COMMUNITY_URLS_PER_SITE, len(by_site[site])):
                    chosen.append(by_site[site][i])
                    idx[site] = i + 1
                    progressed = True
            if not progressed:
                break
        if chosen:
            selected[cid] = chosen
    return selected


def _collect_one(cid: str, rec: dict) -> tuple[list[dict], dict | None]:
    """URL 1건 수집 → (post 레코드 목록[chunk 단위], error|None)."""
    url  = rec.get("url", "")
    site = rec.get("site", "")
    mode = rec.get("collection_mode") or COMMUNITY_SITES_FIXED.get(site, "snippet_only")
    base = {
        "url":                url,
        "candidate_id":       cid,
        "matched_candidates": rec.get("matched_candidates") or [cid],
        "site":               site,
        "collection_mode":    mode,
        "domain_class":       "community",
        "feature_ids":        [],
        "title":              rec.get("page_title", ""),
        "published_at":       rec.get("published_at", ""),
    }

    # CE-D10 — snippet_only: fetch 없이 검색 스니펫을 item 으로 사용 (robots 무관)
    if mode == "snippet_only":
        text = f"{rec.get('page_title', '')}\n{rec.get('meta_description', '')}".strip()
        if not text:
            return [], {"node": "community_collection_node",
                        "error": f"snippet 부재: {url[:80]}",
                        "timestamp": datetime.now(timezone.utc).isoformat()}
        return [{**base, "body_excerpt": text, "fetch_status": "snippet_only",
                 "chunk_index": 0}], None

    # body_only · full — robots 준수 + fetch (D11)
    if not _robots_allowed(url):
        return [], {"node": "community_collection_node",
                    "error": f"robots_disallowed: {url[:80]}",
                    "timestamp": datetime.now(timezone.utc).isoformat()}

    result = _fetch_community_content(url)
    if not result.get("from_cache"):
        time.sleep(_RATE_LIMIT_SEC)   # D11 — 실제 네트워크 호출 간 대기 (host 직렬)

    body     = result.get("body", "")
    comments = result.get("comments", "")
    status   = result.get("fetch_status", "fetch_failed")
    # CE-D6 1단계 — 본문+댓글 결합 후 chunking (게시글 1건 = ABSA item 1~3건)
    combined = body if not comments else f"{body}\n\n[댓글]\n{comments}"
    if status != "ok" or not combined.strip():
        return [], {"node": "community_collection_node",
                    "error": f"{status}: {url[:80]}",
                    "timestamp": datetime.now(timezone.utc).isoformat()}

    posts = [
        {**base, "body_excerpt": chunk, "fetch_status": status, "chunk_index": i}
        for i, chunk in enumerate(_chunk_text(combined))
    ]
    return posts, None


def community_collection_node(
    state: DomainAnalysisState, config: dict | None = None
) -> dict:
    """v0.14 — collection_mode 분기 커뮤니티 수집 (CE-D3·D6·D10 + §3).

    placeholder 풀(broad discovery 산출)에서 round-robin 선별 후, 사이트별
    collection_mode 로 분기: snippet_only(fetch 없음) / body_only·full(브라우저·
    모바일 UA fetch + 댓글 일반 추출 + 문장 경계 chunking).
    host 간 병렬 3 · host 내 직렬 + 1s rate limit (D11).
    """
    started_at = datetime.now(timezone.utc).isoformat()
    thread_id = (config or {}).get("configurable", {}).get("thread_id", "")
    if thread_id:
        try:
            set_progress(thread_id, "reaction_collection",
                         detail="커뮤니티 게시글 수집 (collection_mode 분기)")
        except Exception as exc:  # noqa: BLE001
            logger.debug("set_progress 실패: %s", exc)

    selection = select_community_pool(dict(state))
    if not selection:
        return {"agent_steps": [_step("skipped", started_at)]}

    # host(site) 단위 그룹화 — site 내 직렬(rate limit), site 간 병렬 3 (§3-6)
    by_site: dict[str, list[tuple[str, dict]]] = {}
    for cid in sorted(selection):
        for rec in selection[cid]:
            by_site.setdefault(rec.get("site", ""), []).append((cid, rec))

    posts: list[dict] = []
    errors: list[dict] = []

    def _collect_site(items: list[tuple[str, dict]]) -> tuple[list[dict], list[dict]]:
        site_posts: list[dict] = []
        site_errors: list[dict] = []
        for cid, rec in items:
            recs, err = _collect_one(cid, rec)
            if err:
                site_errors.append(err)
            site_posts.extend(recs)
        return site_posts, site_errors

    with _Pool(max_workers=_HOST_PARALLEL) as pool:
        for site_posts, site_errors in pool.map(
                _collect_site, [by_site[s] for s in sorted(by_site)]):
            posts.extend(site_posts)
            errors.extend(site_errors)

    n_snippet = sum(1 for p in posts if p["fetch_status"] == "snippet_only")
    unique_posts = len({p["url"] for p in posts})
    step = _step("completed", started_at)
    if errors:
        step["error_message"] = f"{len(errors)}건 수집 제외"
    logger.info(
        "community_collection_node (v0.14): 고유 게시글 %d · item %d건 "
        "(fetch %d · snippet %d · 제외 %d)",
        unique_posts, len(posts), len(posts) - n_snippet, n_snippet, len(errors))

    out: dict = {"community_posts": posts, "agent_steps": [step]}
    if errors:
        out["errors"] = errors
    return out


def _step(status: str, started_at: str, step_name: str = "CommunityCollection",
          error_message: str = "") -> AgentStep:
    step: AgentStep = {
        "step_name":   step_name,
        "status":      status,
        "started_at":  started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    if error_message:
        step["error_message"] = error_message
    return step
