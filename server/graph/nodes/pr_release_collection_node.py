"""
server/graph/nodes/pr_release_collection_node.py (v1.0 §6-6a — MS-D2 수집 ③)
-----------------------------------------------------------------------------
marketing_social 의 보도자료 목록 수집 노드.
설계: docs/design/marketing_social_node_design.md §4-3

책임
----
`owned_channel_urls_by_candidate` 의 platform=press_release URL(목록 페이지)을
`_fetch_content`(Trafilatura + 24h 캐시)로 수집하고, 본문에서 날짜 패턴·제목 라인을
정규식으로 추출한다 (결정론 — LLM 미사용).

목록 페이지 구조가 제각각이므로 추출 실패 시 presence-only 강등 (부분 실패 허용).

날짜 패턴
---------
YYYY.MM.DD / YYYY-MM-DD / YYYY/MM/DD / YYYY년 M월 D일 — 같은 줄 또는 직전 줄의
텍스트를 제목으로 결합.

write keys
----------
- pr_releases : [{candidate_id, page_url, releases: [{title, published_at}],
                  fetch_status}]
- agent_steps / errors (누적 reducer)
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from server.graph.progress_store import set_progress
from server.graph.state import AgentStep, DomainAnalysisState
from server.graph.nodes.official_content_collection_node import _fetch_content

logger = logging.getLogger(__name__)

REPORT_TYPE   = "marketing_social"
_PLATFORM     = "press_release"
_MAX_RELEASES = 50
_MIN_TITLE    = 8     # 날짜 주변 텍스트가 이보다 짧으면 제목으로 보지 않음

# 일(day) 그룹은 두 자리 대안을 앞에 둔다 — 정규식 alternation 은 순서 우선이라
# "12"에서 "1"만 매칭되는 오류 방지 (테스트 검증)
_DATE_RE = re.compile(
    r"(?P<y>20\d{2})[.\-/년]\s*(?P<m>1[0-2]|0?[1-9])[.\-/월]\s*(?P<d>3[01]|[12]\d|0?[1-9])일?"
)


def extract_releases(content: str) -> list[dict]:
    """본문 텍스트에서 (제목, 게시일) 목록 추출 (순수 함수).

    날짜가 포함된 줄에서 날짜를 제거한 나머지를 제목으로, 비어 있으면 직전
    비어있지 않은 줄을 제목으로 사용한다. 중복 제목 제거, 최신 _MAX_RELEASES 건.
    """
    releases: list[dict] = []
    seen: set[str] = set()
    lines = [ln.strip() for ln in content.splitlines()]
    for i, line in enumerate(lines):
        m = _DATE_RE.search(line)
        if not m:
            continue
        iso = f"{m.group('y')}-{int(m.group('m')):02d}-{int(m.group('d')):02d}"
        title = _DATE_RE.sub("", line).strip(" -–—|·[]()#*\t")
        if len(title) < _MIN_TITLE:
            # 날짜 단독 줄 — 직전 비어있지 않은 줄을 제목으로
            for j in range(i - 1, max(-1, i - 4), -1):
                prev = lines[j].strip(" -–—|·[]()#*\t")
                if len(prev) >= _MIN_TITLE and not _DATE_RE.search(prev):
                    title = prev
                    break
        if len(title) < _MIN_TITLE or title in seen:
            continue
        seen.add(title)
        releases.append({"title": title[:150], "published_at": iso})
        if len(releases) >= _MAX_RELEASES:
            break
    return releases


def select_pr_urls(state: dict) -> list[dict]:
    """수집 대상 [{candidate_id, url}] — candidate 당 confidence 최고 1건 (게이트 — MS-D1)."""
    if REPORT_TYPE not in (state.get("selected_purposes") or []):
        return []
    out: list[dict] = []
    for cid, urls in sorted((state.get("owned_channel_urls_by_candidate") or {}).items()):
        candidates = [
            u for u in (urls or [])
            if u.get("platform") == _PLATFORM and (u.get("url") or "").strip()
        ]
        if candidates:
            best = max(candidates, key=lambda u: float(u.get("confidence", 0) or 0))
            out.append({"candidate_id": cid, "url": best["url"]})
    return out


def pr_release_collection_node(
    state: DomainAnalysisState, config: dict | None = None
) -> dict:
    """보도자료 목록 수집 (MS-D2 수집 ③)."""
    started_at = datetime.now(timezone.utc).isoformat()
    thread_id = (config or {}).get("configurable", {}).get("thread_id", "")
    if thread_id:
        try:
            set_progress(thread_id, "marketing_collection", detail="보도자료 목록 수집")
        except Exception as exc:  # noqa: BLE001
            logger.debug("set_progress 실패: %s", exc)

    targets = select_pr_urls(dict(state))
    if not targets:
        return {"agent_steps": [_step("skipped", started_at)]}

    pr_releases: list[dict] = []
    errors: list[dict] = []

    for t in targets:
        result = _fetch_content(t["url"])
        if result.get("fetch_status") == "ok":
            releases = extract_releases(result.get("content", ""))
            status = "ok" if releases else "extract_failed"
        else:
            releases = []
            status = result.get("fetch_status", "fetch_failed")
        if status != "ok":
            errors.append({
                "node":      "pr_release_collection_node",
                "error":     f"({t['candidate_id']}) {status}: {t['url'][:80]}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        pr_releases.append({
            "candidate_id": t["candidate_id"],
            "page_url":     t["url"],
            "releases":     releases,
            "fetch_status": status,
        })

    ok_n = sum(1 for p in pr_releases if p["fetch_status"] == "ok")
    step = _step("completed", started_at)
    if ok_n < len(pr_releases):
        step["error_message"] = f"{len(pr_releases) - ok_n}건 presence-only 강등"
    logger.info("pr_release_collection: %d/%d 페이지 추출", ok_n, len(pr_releases))

    out: dict = {"pr_releases": pr_releases, "agent_steps": [step]}
    if errors:
        out["errors"] = errors
    return out


def _step(status: str, started_at: str) -> AgentStep:
    return {
        "step_name":   "PrReleaseCollection",
        "status":      status,
        "started_at":  started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
