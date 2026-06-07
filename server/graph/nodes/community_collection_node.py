"""
server/graph/nodes/community_collection_node.py (v0.13 — reaction_insight 시리즈)
----------------------------------------------------------------------------------
reaction_insight 의 커뮤니티·블로그 채널 수집 노드.
설계: docs/design/reaction_insight_node_design.md §4 (D11 정책)

책임
----
analysis_features 의 blog_community origin URL(검증·선택 완료분)에서 게시글 본문을
수집한다. fetch 는 official_content_collection 의 `_fetch_content`(Trafilatura +
24h 전문 캐시) 를 재사용한다.

D11 정책
--------
- robots.txt 준수: host 별 1회 조회·캐시. disallow URL 은 수집 제외 (robots_disallowed).
  robots.txt 자체에 접근 불가(4xx·네트워크 오류)하면 허용으로 간주 (RFC 9309 관행).
- rate limit: 실제 네트워크 fetch 간 1초 대기 (캐시 적중은 대기 없음 — from_cache 플래그).
- 작성자 식별정보 비저장: 본문 텍스트만 보존.

선별 상한 (RI-D4 보완)
----------------------
feature당 최신(published_at, 부재 시 url 사전순) 상위 3 URL → candidate union →
candidate당 8 초과 시 feature 커버리지 우선 greedy 절단. 게시글 본문 발췌 상한 2,000자.

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

from server.graph.progress_store import set_progress
from server.graph.state import AgentStep, DomainAnalysisState
from server.graph.nodes.official_content_collection_node import _fetch_content

logger = logging.getLogger(__name__)

REPORT_TYPE = "reaction_insight"
_ORIGIN     = "blog_community"

# RI-D4 보완 — 커뮤니티 선별 상한
_URLS_PER_FEATURE   = 3
_URLS_PER_CANDIDATE = 8
_BODY_EXCERPT_CHARS = 2_000
_RATE_LIMIT_SEC     = 1.0     # D11 — 실제 네트워크 호출 간 대기

# robots.txt host 별 캐시 (프로세스 수명)
_robots_cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}


# ─── robots.txt (D11) ────────────────────────────────────────────────────────

def _robots_allowed(url: str) -> bool:
    """host 의 robots.txt 기준 수집 허용 여부. 조회 불가 시 허용으로 간주."""
    try:
        parsed = urlparse(url)
        host = f"{parsed.scheme}://{parsed.netloc}"
    except Exception:  # noqa: BLE001
        return True
    if host not in _robots_cache:
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(f"{host}/robots.txt")
        try:
            rp.read()
            _robots_cache[host] = rp
        except Exception as exc:  # noqa: BLE001 — 조회 불가 → 허용 간주
            logger.debug("robots.txt 조회 실패 (%s): %s", host, exc)
            _robots_cache[host] = None
    rp = _robots_cache[host]
    return True if rp is None else rp.can_fetch("*", url)


# ─── URL 선별 (순수 함수) ────────────────────────────────────────────────────

def select_community_urls(state: dict) -> dict[str, list[dict]]:
    """candidate_id → 선별 URL 목록. 필터 3단계 + RI-D4 보완 상한."""
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
                if u.get("origin") == _ORIGIN and (u.get("url") or "").strip()
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

def community_collection_node(
    state: DomainAnalysisState, config: dict | None = None
) -> dict:
    """blog_community origin URL 의 게시글 본문 수집 (D11 정책 적용)."""
    started_at = datetime.now(timezone.utc).isoformat()
    thread_id = (config or {}).get("configurable", {}).get("thread_id", "")
    if thread_id:
        try:
            set_progress(thread_id, "reaction_collection",
                         detail="커뮤니티·블로그 게시글 수집")
        except Exception as exc:  # noqa: BLE001
            logger.debug("set_progress 실패: %s", exc)

    selection = select_community_urls(dict(state))
    if not selection:
        return {"agent_steps": [_step("skipped", started_at)]}

    errors: list[dict] = []
    community_posts: list[dict] = []
    stats = {"ok": 0, "robots": 0, "dynamic": 0, "failed": 0}

    for cid in sorted(selection):
        for rec in selection[cid]:
            url = rec["url"]
            if not _robots_allowed(url):
                stats["robots"] += 1
                logger.info("community_collection: robots.txt disallow skip (%s)", url)
                continue

            result = _fetch_content(url)
            status = result.get("fetch_status", "fetch_failed")
            if not result.get("from_cache"):
                time.sleep(_RATE_LIMIT_SEC)   # D11 — 실제 네트워크 호출 간 대기

            if status == "ok":
                stats["ok"] += 1
                content = result.get("content", "")
                community_posts.append({
                    "url":          url,
                    "candidate_id": cid,
                    "feature_ids":  rec["feature_ids"],
                    "domain_class": rec["domain_class"],
                    "title":        _title_of(content),
                    "body_excerpt": content[:_BODY_EXCERPT_CHARS],
                    "published_at": rec["published_at"],
                    "fetch_status": status,
                })
            else:
                key = "dynamic" if status == "requires_dynamic_render" else "failed"
                stats[key] += 1
                errors.append({
                    "node": "community_collection_node",
                    "error": f"{status}: {url[:80]}",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

    step = _step("completed", started_at)
    skipped_n = stats["robots"] + stats["dynamic"] + stats["failed"]
    if skipped_n:
        step["error_message"] = (
            f"{skipped_n}건 수집 제외 (robots {stats['robots']} · "
            f"dynamic {stats['dynamic']} · 실패 {stats['failed']})")
    logger.info("community_collection: 게시글 %d건 수집 (%s)", len(community_posts), stats)

    out: dict = {"community_posts": community_posts, "agent_steps": [step]}
    if errors:
        out["errors"] = errors
    return out


def _step(status: str, started_at: str, error_message: str = "") -> AgentStep:
    step: AgentStep = {
        "step_name":   "CommunityCollection",
        "status":      status,
        "started_at":  started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    if error_message:
        step["error_message"] = error_message
    return step
