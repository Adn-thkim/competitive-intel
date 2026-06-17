"""
server/graph/nodes/url_discovery_youtube_reactions_node.py
----------------------------------------------------------
5중 fan-out 중 source-type 3번 — reaction_insight YouTube 영상 탐색.

역할 (youtube_collection_redesign.md Phase 1)
---------------------------------------------
1. candidate 이름을 검색 쿼리로 직접 사용 (hint 기반 로직 폐기).
2. publishedAfter 최근 2년 필터 적용 (_PUBLISHED_AFTER_YEARS=2, RFC 3339).
3. viewCount + commentCount 임계치 필터 (config.YOUTUBE_MIN_* 상수).
4. 동일 video_id intra-candidate 중복 제거.
5. owned 채널 영상 제외는 cross_reference_node 에서 담당.

quota 관리
----------
- 일일 한도 10,000 units. search.list = 100 units, videos.list = 1 unit/call.
- 예상 호출(cache miss 첫 실행): candidate 수 × 101u.
- 동일 도메인 재실행 시 24h TTL agent_cache 로 0 units.
- `YouTubeQuotaExceeded` 발생 시 부분 수집 결과 + status="quota_skip" 으로 graceful 종료.

위치 (1차 fan-out)
--------------------
ab_join
  ├─→ url_discovery_official_node
  ├─→ url_discovery_blog_community_node
  ├─→ [url_discovery_youtube_reactions_node]
  ├─→ url_discovery_owned_channels_node
  └─→ url_discovery_macro_node
        ↓ list-fan-in
      urls_merge_node
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

from server.config import (
    YOUTUBE_API_KEY,
    YOUTUBE_MIN_VIEW_COUNT,
    YOUTUBE_MIN_COMMENT_COUNT,
    YOUTUBE_REGION_CODE,
)
from server.graph.progress_store import set_progress
from server.graph.state import DomainAnalysisState, AgentStep
from server.graph.nodes.feature_url_mapper_node import (
    _candidate_name_map,
    _error,
)
from server.llm.youtube_client import (
    YouTubeApiUnavailable,
    YouTubeQuotaExceeded,
    current_quota_used,
    youtube_search_videos,
)

_PUBLISHED_AFTER_YEARS = 2   # 최근 N년 이내 영상만 수집

logger = logging.getLogger(__name__)

_SOURCE_TYPE = "youtube_reactions"
_YOUTUBE_MAX_WORKERS = 3   # quota safety + Brave 와 동일 패턴


def url_discovery_youtube_reactions_node(
    state: DomainAnalysisState, config: dict | None = None,
) -> dict:
    """youtube_collection_redesign.md Phase 1 — candidate_name 직접 검색.

    hint 기반 로직을 폐기하고 candidate 이름을 검색 쿼리로 사용한다.
    publishedAfter 최근 2년 필터를 적용하며, 동일 video_id 중복은 candidate별로 제거한다.

    Returns
    -------
    dict
        {youtube_reactions_urls_by_candidate, agent_steps[+ errors 누적]}
    """
    started_at = datetime.now(timezone.utc).isoformat()
    thread_id  = (config or {}).get("configurable", {}).get("thread_id", "")

    print(
        f"▶️  [url_discovery_youtube_reactions_node] ENTRY at {started_at} "
        f"thread_id={thread_id!r}",
        flush=True,
    )

    if thread_id:
        try:
            set_progress(
                thread_id, "feature_mapping_brave",
                detail="YouTube reactions 영상 탐색 (Data API v3)",
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("set_progress(youtube_reactions) 실패: %s", exc)

    # ── API key 미설정 graceful 처리 ─────────────────────────────────────────
    if not YOUTUBE_API_KEY:
        logger.warning(
            "url_discovery_youtube_reactions_node: YOUTUBE_API_KEY 미설정 — "
            "빈 결과 반환 (skipped)",
        )
        finished_at = datetime.now(timezone.utc).isoformat()
        return {
            "youtube_reactions_urls_by_candidate": {},
            "agent_steps": [{
                "step_name":     "UrlDiscoveryYoutubeReactions",
                "status":        "skipped",
                "started_at":    started_at,
                "finished_at":   finished_at,
                "error_message": "YOUTUBE_API_KEY 미설정",
            }],
        }

    # ── 입력 수집 ────────────────────────────────────────────────────────────
    own_product: dict           = state.get("own_product") or {}
    competitor_candidates: list = state.get("competitor_candidates") or []
    selected_ids: list[str]     = state.get("selected_competitor_ids") or []

    # candidate_id → 검색 쿼리(= candidate 이름) 매핑. "own" fallback 키는 제외.
    name_map = _candidate_name_map(own_product, competitor_candidates, selected_ids)
    search_targets: list[tuple[str, str]] = [
        (cid, name) for cid, name in name_map.items()
        if cid != "own" and name
    ]

    if not search_targets:
        logger.info("url_discovery_youtube_reactions_node: 검색 대상 candidate 없음")
        finished_at = datetime.now(timezone.utc).isoformat()
        return {
            "youtube_reactions_urls_by_candidate": {},
            "agent_steps": [{
                "step_name":   "UrlDiscoveryYoutubeReactions",
                "status":      "completed",
                "started_at":  started_at,
                "finished_at": finished_at,
            }],
        }

    # publishedAfter: 최근 _PUBLISHED_AFTER_YEARS년 이내 (RFC 3339).
    # ISO 주 시작(월요일 00:00 UTC)으로 절사 → 같은 주 재실행 시 캐시 키 동일.
    cutoff = datetime.now(timezone.utc) - timedelta(days=365 * _PUBLISHED_AFTER_YEARS)
    week_start = cutoff - timedelta(days=cutoff.weekday())
    published_after = week_start.strftime("%Y-%m-%dT00:00:00Z")

    # ── YouTube API 호출 (병렬, quota_skip graceful) ─────────────────────────
    logger.info(
        "url_discovery_youtube_reactions_node: API 호출 시작 (candidates=%d, "
        "published_after=%s)",
        len(search_targets), published_after,
    )
    results_by_candidate: dict[str, list[dict]] = {}
    errors: list[dict[str, str]] = []
    quota_skipped = False
    api_unavailable = False

    def _search(cid: str, cand_name: str) -> tuple[str, list[dict] | None, str | None]:
        """단일 candidate 이름 검색 (전량 수집). 실패 시 (cid, None, err_msg) 반환."""
        try:
            return cid, youtube_search_videos(
                cand_name,
                region_code=YOUTUBE_REGION_CODE,
                published_after=published_after,
            ), None
        except YouTubeQuotaExceeded as exc:
            return cid, None, f"quota_exceeded: {exc}"
        except YouTubeApiUnavailable as exc:
            return cid, None, f"api_unavailable: {exc}"
        except Exception as exc:  # noqa: BLE001
            return cid, None, f"unexpected: {exc}"

    with ThreadPoolExecutor(max_workers=_YOUTUBE_MAX_WORKERS) as pool:
        futures = {pool.submit(_search, cid, name): cid for cid, name in search_targets}
        for fut in as_completed(futures):
            cand_id = futures[fut]
            try:
                _cid, videos, err = fut.result()
            except Exception as exc:  # noqa: BLE001
                err    = f"future exception: {exc}"
                videos = None
            if err:
                if "quota_exceeded" in err:
                    quota_skipped = True
                elif "api_unavailable" in err:
                    api_unavailable = True
                errors.append({
                    "node":      "url_discovery_youtube_reactions_node",
                    "error":     f"candidate={cand_id}: {err}",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                continue
            if not videos:
                continue

            # viewCount + commentCount 임계치 필터 + intra-candidate video_id dedup
            seen_ids: set[str] = set()
            for v in videos:
                if v.get("view_count", 0) < YOUTUBE_MIN_VIEW_COUNT:
                    continue
                if v.get("comment_count", 0) < YOUTUBE_MIN_COMMENT_COUNT:
                    continue
                vid = v["video_id"]
                if vid in seen_ids:
                    continue    # 동일 영상 중복 제거 (intra-candidate)
                seen_ids.add(vid)
                results_by_candidate.setdefault(cand_id, []).append({
                    "url":              v["url"],
                    "video_id":         vid,
                    "channel_id":       v["channel_id"],
                    "channel_title":    v["channel_title"],
                    "title":            v.get("title", ""),
                    "page_title":       v.get("title", ""),       # page_meta_collect 호환
                    "meta_description": v.get("description", ""),
                    "view_count":       v["view_count"],
                    "like_count":       v["like_count"],
                    "comment_count":    v["comment_count"],
                    "published_at":     v.get("published_at", ""),
                    "origin":           "youtube_reactions",
                    "feature_ids":      [],   # hint 폐기 — feature_mapping 노드 제거 (Phase 3)
                    "matched_report_types": ["reaction_insight"],
                })

    total = sum(len(v) for v in results_by_candidate.values())
    quota_used = current_quota_used()
    logger.info(
        "url_discovery_youtube_reactions_node: 완료 (%d candidate, %d videos, "
        "quota_used=%d units, errors=%d)",
        len(results_by_candidate), total, quota_used, len(errors),
    )

    # ── agent_step 상태 결정 ─────────────────────────────────────────────────
    if api_unavailable and not results_by_candidate:
        status = "skipped"   # API 자체 사용 불가 — 다른 노드는 진행
    elif quota_skipped and not results_by_candidate:
        status = "quota_skip"
    elif errors and results_by_candidate:
        status = "completed"   # 부분 성공 — graceful
    else:
        status = "completed"

    finished_at = datetime.now(timezone.utc).isoformat()
    step: AgentStep = {
        "step_name":   "UrlDiscoveryYoutubeReactions",
        "status":      status,
        "started_at":  started_at,
        "finished_at": finished_at,
    }
    if errors:
        step["error_message"] = f"{len(errors)}건 부분 실패 (quota_skipped={quota_skipped})"

    out: dict = {
        "youtube_reactions_urls_by_candidate": results_by_candidate,
        "agent_steps":                          [step],
    }
    if errors:
        out["errors"] = errors
    return out
