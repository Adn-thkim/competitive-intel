"""
server/graph/nodes/youtube_channel_metadata_collection_node.py (v1.0 §6-6a — MS-D2 수집 ①)
------------------------------------------------------------------------------------------
marketing_social 의 YouTube 공식 채널 운영 지표 수집 노드.
설계: docs/design/marketing_social_node_design.md §4-1

책임
----
`owned_channel_urls_by_candidate` 의 platform=youtube_official 항목에서 candidate 별
대표 채널 1개(confidence 최고)를 골라 운영 지표를 수집한다:
- channels.list  (1 unit) → 구독자·총 영상 수·uploads playlist
- playlistItems.list (1 unit) → 최근 영상 50개의 게시일·제목
- videos.list statistics (1 unit/50) → 최근 영상의 조회·좋아요·댓글 (engagement 산출용)

reaction 시리즈의 youtube_reaction_collection(제3자 리뷰 영상 댓글)과 단위가 다름 —
본 노드는 자사·경쟁사 **공식 채널의 공급 측 지표**다.

write keys
----------
- youtube_channel_metadata : {candidate_id: {channel_url, channel_id, title,
                              subscriber_count, video_total, recent_videos:
                              [{video_id, title, published_at, description,
                                view_count, like_count, comment_count}]}}
  (description = 영상 설명 300자 발췌 — MS-D10 상품 관련성 판정용, quota 추가 없음)
- agent_steps / errors (누적 reducer)

graceful 종료
-------------
- marketing_social 미선택 / youtube_official 채널 0건: status="skipped"
- quota 초과·API 불가: 해당 candidate errors 적재, 나머지 진행
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from server.graph.progress_store import set_progress
from server.graph.state import AgentStep, DomainAnalysisState
from server.llm.youtube_client import (
    YouTubeApiUnavailable,
    YouTubeQuotaExceeded,
    youtube_channel_info,
    youtube_playlist_items,
    youtube_videos_statistics,
)

logger = logging.getLogger(__name__)

REPORT_TYPE     = "marketing_social"
_PLATFORM       = "youtube_official"
_RECENT_VIDEOS  = 100  # MS-D4 — 최근 업로드 수집 상한 (v1.0.4: 50→100, 12개월 윈도우 커버)


def select_channel_urls(state: dict) -> dict[str, dict]:
    """candidate_id → 대표 youtube_official 채널 항목 (confidence 최고 1건).

    marketing_social 미선택 시 빈 dict (게이트 — MS-D1).
    """
    if REPORT_TYPE not in (state.get("selected_purposes") or []):
        return {}
    out: dict[str, dict] = {}
    for cid, urls in (state.get("owned_channel_urls_by_candidate") or {}).items():
        candidates = [
            u for u in (urls or [])
            if u.get("platform") == _PLATFORM and (u.get("url") or "").strip()
        ]
        if candidates:
            out[cid] = max(candidates, key=lambda u: float(u.get("confidence", 0) or 0))
    return out


def build_channel_record(entry: dict, info: dict, recent: list[dict],
                         stats_by_id: dict[str, dict]) -> dict:
    """채널 메타 + 최근 영상 + 통계를 결합한 레코드 (순수 함수)."""
    videos = [
        {
            "video_id":      v["video_id"],
            "title":         v.get("title", ""),
            "published_at":  v.get("published_at", ""),
            "description":   v.get("description", ""),   # MS-D10 상품 관련성 판정용
            "view_count":    (stats_by_id.get(v["video_id"]) or {}).get("view_count", 0),
            "like_count":    (stats_by_id.get(v["video_id"]) or {}).get("like_count", 0),
            "comment_count": (stats_by_id.get(v["video_id"]) or {}).get("comment_count", 0),
        }
        for v in recent
    ]
    return {
        "channel_url":      entry.get("url", ""),
        "channel_id":       info.get("channel_id", ""),
        "title":            info.get("title", ""),
        "subscriber_count": info.get("subscriber_count", 0),
        "video_total":      info.get("video_count", 0),
        "recent_videos":    videos,
    }


def youtube_channel_metadata_collection_node(
    state: DomainAnalysisState, config: dict | None = None
) -> dict:
    """candidate 별 YouTube 공식 채널 운영 지표 수집 (MS-D2 수집 ①)."""
    started_at = datetime.now(timezone.utc).isoformat()
    thread_id = (config or {}).get("configurable", {}).get("thread_id", "")
    if thread_id:
        try:
            set_progress(thread_id, "marketing_collection",
                         detail="YouTube 공식 채널 지표 수집")
        except Exception as exc:  # noqa: BLE001
            logger.debug("set_progress 실패: %s", exc)

    selection = select_channel_urls(dict(state))
    if not selection:
        return {"agent_steps": [_step("skipped", started_at)]}

    metadata: dict[str, dict] = {}
    errors: list[dict] = []

    for cid in sorted(selection):
        entry = selection[cid]
        try:
            info = youtube_channel_info(entry.get("url", ""))
            if not info:
                errors.append(_err(cid, f"채널 미해석: {entry.get('url','')[:80]}"))
                continue
            recent = youtube_playlist_items(
                info.get("uploads_playlist_id", ""), max_results=_RECENT_VIDEOS,
            )
            stats_by_id = youtube_videos_statistics([v["video_id"] for v in recent])
            metadata[cid] = build_channel_record(entry, info, recent, stats_by_id)
        except YouTubeQuotaExceeded as exc:
            errors.append(_err(cid, f"quota 초과: {str(exc)[:80]}"))
        except YouTubeApiUnavailable as exc:
            errors.append(_err(cid, f"API 불가: {str(exc)[:80]}"))

    step = _step("completed", started_at)
    if errors:
        step["error_message"] = f"{len(errors)}건 부분 실패"
    logger.info(
        "youtube_channel_metadata_collection: %d/%d candidate 수집",
        len(metadata), len(selection),
    )
    out: dict = {"youtube_channel_metadata": metadata, "agent_steps": [step]}
    if errors:
        out["errors"] = errors
    return out


def _err(cid: str, msg: str) -> dict:
    return {
        "node":      "youtube_channel_metadata_collection_node",
        "error":     f"({cid}) {msg}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _step(status: str, started_at: str) -> AgentStep:
    return {
        "step_name":   "YoutubeChannelMetadataCollection",
        "status":      status,
        "started_at":  started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
