"""
server/graph/nodes/url_discovery_youtube_reactions_node.py (v0.10.20 실 구현)
----------------------------------------------------------------------------
5중 fan-out 중 source-type 3번 — reaction_insight YouTube 영상 탐색.

v0.10.19 까지의 스켈레톤(빈 결과 반환) 을 폐기하고 본 PR(v0.10.20) 에서 YouTube
Data API v3 실 구현을 도입합니다.

역할 (v0.10.20)
----------------
1. domain_taxonomy 의 `search_query_hints` 중 `source_hint="youtube_reactions"`
   인 hint 만 `_extract_hints_for_source` 헬퍼로 추출 (v0.10.19.1 적용).
2. 각 hint × candidate(own + selected comp) 모든 조합에 대해 토큰 치환 후
   `youtube_search_videos` 호출. search.list + videos.list 머지 결과 수집.
3. 발견된 각 영상에 `feature_ids` 메타 부착 (v0.10.19.1 의 객체 양식 hints).
4. viewCount + commentCount 필터 적용 (config 임계치).
5. cross_reference (owned channel 영상 제외) 는 v0.10.26 의 별도 노드에서 처리.

quota 관리
----------
- 일일 한도 10,000 units. search.list = 100 units, videos.list = 1 unit/call.
- 예상 호출(cache miss 첫 실행): candidate 4명 × 평균 3 쿼리 = 12 호출 × 101u
  ≈ 1,212 units (전체 quota 의 12%).
- 동일 도메인 재실행 시 24h TTL agent_cache 로 0 units.
- `YouTubeQuotaExceeded` 발생 시 본 노드는 부분 수집 결과 + status="quota_skip"
  agent_step 으로 graceful 종료. 다른 4개 source-type 노드는 정상 진행.

API key 미설정
---------------
`YOUTUBE_API_KEY` 미설정 시 `YouTubeApiUnavailable` 발생. 본 노드는 빈 결과 +
status="skipped" agent_step 으로 종료 (v0.10.19 스켈레톤과 동일한 graceful 동작).

위치 (v0.10.19 토폴로지 1차 fan-out)
------------------------------------
ab_join
  ├─→ url_discovery_official_node
  ├─→ url_discovery_blog_community_node
  ├─→ [url_discovery_youtube_reactions_node]   ← 이 노드 (v0.10.20 실 구현)
  ├─→ url_discovery_owned_channels_node
  └─→ url_discovery_macro_node
        ↓ list-fan-in
      urls_merge_node
        ↓ ...
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from server.config import (
    YOUTUBE_API_KEY,
    YOUTUBE_MAX_RESULTS,
    YOUTUBE_MIN_VIEW_COUNT,
    YOUTUBE_MIN_COMMENT_COUNT,
    YOUTUBE_REGION_CODE,
)
from server.graph.progress_store import set_progress
from server.graph.state import DomainAnalysisState, AgentStep
from server.graph.nodes.feature_url_mapper_node import (
    _candidate_name_map,
    _extract_active_reports,
    _extract_hints_for_source,
    _substitute_tokens,
    _error,
)
from server.llm.youtube_client import (
    YouTubeApiUnavailable,
    YouTubeQuotaExceeded,
    current_quota_used,
    youtube_search_videos,
)

logger = logging.getLogger(__name__)

_SOURCE_TYPE = "youtube_reactions"
_YOUTUBE_MAX_WORKERS = 3   # quota safety + Brave 와 동일 패턴


def url_discovery_youtube_reactions_node(
    state: DomainAnalysisState, config: dict | None = None,
) -> dict:
    """v0.10.20 실 구현 — YouTube Data API v3 로 reaction_insight 영상 검색.

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

    # ── 입력 수집 + hints 추출 ───────────────────────────────────────────────
    domain_taxonomy: dict       = state.get("domain_taxonomy") or {}
    domain_name: str            = state.get("domain_name") or ""
    own_product: dict           = state.get("own_product") or {}
    competitor_candidates: list = state.get("competitor_candidates") or []
    selected_ids: list[str]     = state.get("selected_competitor_ids") or []

    if not domain_taxonomy:
        return _error(started_at, "domain_taxonomy 가 state 에 없습니다.")
    if not domain_name:
        return _error(started_at, "domain_name 이 state 에 없습니다.")

    all_active = _extract_active_reports(domain_taxonomy)
    hints_with_meta = _extract_hints_for_source(all_active, _SOURCE_TYPE)

    if not hints_with_meta:
        logger.info(
            "url_discovery_youtube_reactions_node: source_hint='youtube_reactions' 인 "
            "hint 가 없습니다 — 빈 결과 반환",
        )
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

    # ── candidate × hint 토큰 치환 + 작업 목록 생성 ──────────────────────────
    name_map = _candidate_name_map(own_product, competitor_candidates, selected_ids)
    own_product_name = own_product.get("name") or own_product.get("product_name") or ""

    # (candidate_id, query, feature_id, report_type) 작업 목록
    tasks: list[tuple[str, str, str, str]] = []
    query_meta: dict[str, list[tuple[str, str]]] = {}   # query → [(feature_id, report_type), ...]

    for query_template, feature_id, rt in hints_with_meta:
        for cand_id, cand_name in name_map.items():
            if cand_id == "own":
                continue   # fallback 키 중복 회피
            query = _substitute_tokens(
                query_template, name_map, cand_name, own_product_name, domain_name,
            )
            if not query or "{" in query:
                continue   # 치환 실패 스킵
            tasks.append((cand_id, query, feature_id, rt))
            query_meta.setdefault(query, []).append((feature_id, rt))

    if not tasks:
        logger.info("url_discovery_youtube_reactions_node: 토큰 치환 후 작업 0건")
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

    # 동일 query dedup (query → 첫 등장 candidate_id)
    deduped: dict[str, str] = {}
    for cand_id, query, _, _ in tasks:
        deduped.setdefault(query, cand_id)

    # ── YouTube API 호출 (병렬, quota_skip graceful) ─────────────────────────
    logger.info(
        "url_discovery_youtube_reactions_node: API 호출 시작 (unique queries=%d, hints=%d)",
        len(deduped), len(hints_with_meta),
    )
    results_by_candidate: dict[str, list[dict]] = {}
    errors: list[dict[str, str]] = []
    quota_skipped = False
    api_unavailable = False

    def _search(q: str) -> tuple[str, list[dict] | None, str | None]:
        """단일 쿼리 호출. 실패 시 (q, None, err_msg) 반환."""
        try:
            return q, youtube_search_videos(q, max_results=YOUTUBE_MAX_RESULTS,
                                            region_code=YOUTUBE_REGION_CODE), None
        except YouTubeQuotaExceeded as exc:
            return q, None, f"quota_exceeded: {exc}"
        except YouTubeApiUnavailable as exc:
            return q, None, f"api_unavailable: {exc}"
        except Exception as exc:  # noqa: BLE001
            return q, None, f"unexpected: {exc}"

    with ThreadPoolExecutor(max_workers=_YOUTUBE_MAX_WORKERS) as pool:
        futures = {pool.submit(_search, q): q for q in deduped}
        for fut in as_completed(futures):
            q = futures[fut]
            try:
                _q, videos, err = fut.result()
            except Exception as exc:  # noqa: BLE001
                err     = f"future exception: {exc}"
                videos  = None
            if err:
                if "quota_exceeded" in err:
                    quota_skipped = True
                elif "api_unavailable" in err:
                    api_unavailable = True
                errors.append({
                    "node":      "url_discovery_youtube_reactions_node",
                    "error":     f"query={q[:60]}: {err}",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                continue
            if not videos:
                continue

            metas       = query_meta.get(q, [])
            feature_ids = sorted({fid for fid, _ in metas if fid})
            cand_id     = deduped[q]

            # viewCount + commentCount 임계치 필터
            for v in videos:
                if v.get("view_count", 0) < YOUTUBE_MIN_VIEW_COUNT:
                    continue
                if v.get("comment_count", 0) < YOUTUBE_MIN_COMMENT_COUNT:
                    continue
                results_by_candidate.setdefault(cand_id, []).append({
                    "url":              v["url"],
                    "video_id":         v["video_id"],
                    "channel_id":       v["channel_id"],
                    "channel_title":    v["channel_title"],
                    "title":            v.get("title", ""),
                    "page_title":       v.get("title", ""),       # page_meta_collect 호환
                    "meta_description": v.get("description", ""),  # 동일
                    "view_count":       v["view_count"],
                    "like_count":       v["like_count"],
                    "comment_count":    v["comment_count"],
                    "published_at":     v.get("published_at", ""),
                    "origin":           "youtube_reactions",
                    "feature_ids":      feature_ids,                # v0.10.19.1
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
