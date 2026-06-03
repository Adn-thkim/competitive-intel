"""
server/llm/youtube_client.py (v0.10.20)
----------------------------------------
YouTube Data API v3 호출 추상화 + quota 추적 + 24h TTL agent_cache 캐싱.

본 모듈은 `url_discovery_youtube_reactions_node` 가 사용하는 단일 함수
`youtube_search_videos(query, max_results, region_code)` 를 export 한다.

API surface
------------
- `search.list?q={query}&type=video&order=relevance&regionCode={region}&maxResults={N}`
  → 영상 ID + channelId + title + publishedAt 메타 수집 (100 units/call)
- `videos.list?id={ids}&part=statistics,snippet,status`
  → viewCount + likeCount + commentCount + 비공개·삭제 여부 확인 (1 unit per N videos)

캐싱 (v0.10.12 B 캐시 정책 + v0.10.20 신설)
-------------------------------------------
- agent_id = "youtube_search"
- cache_input = {query, region_code, max_results}
- TTL = config.YOUTUBE_CACHE_TTL_HOURS (기본 24h)
- 캐시 hit 시 0 quota 소비. miss 시 search.list 1회 + videos.list 1회.

Quota 관리
----------
- daily_used 카운터를 모듈 글로벌로 유지 (서버 재시작 시 초기화 — daily quota 도 일일 단위 리셋이므로 정합)
- 호출 직전 잔여 quota 확인. 남은 quota < YOUTUBE_QUOTA_SAFETY_MARGIN 시 quota_exceeded 예외
- url_discovery_youtube_reactions_node 가 본 예외를 잡아 agent_steps[*].status="quota_skip" 처리

후방 호환
---------
- YOUTUBE_API_KEY 미설정 시 모든 호출은 빈 결과 + 로그 1회 출력
- 본 모듈은 외부 의존성 없음 (requests 만 사용 — 이미 프로젝트 의존성)
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any

import requests

from server.config import (
    YOUTUBE_API_KEY,
    YOUTUBE_DAILY_QUOTA,
    YOUTUBE_QUOTA_SAFETY_MARGIN,
    YOUTUBE_MAX_RESULTS,
    YOUTUBE_REGION_CODE,
    YOUTUBE_CACHE_TTL_HOURS,
)
from server.graph.agent_cache import load_agent_output, store_agent_output

logger = logging.getLogger(__name__)

# YouTube Data API v3 endpoints
_SEARCH_ENDPOINT = "https://www.googleapis.com/youtube/v3/search"
_VIDEOS_ENDPOINT = "https://www.googleapis.com/youtube/v3/videos"

# Quota costs (Google 공식 문서 기준)
_SEARCH_LIST_COST = 100   # units per call
_VIDEOS_LIST_COST = 1     # unit per call (50 videos 까지 단일 호출 = 1 unit)

# HTTP timeouts
_CONNECT_TIMEOUT = 3
_READ_TIMEOUT    = 10
_HTTP_TIMEOUT    = (_CONNECT_TIMEOUT, _READ_TIMEOUT)


class YouTubeQuotaExceeded(Exception):
    """일일 quota 한도 초과 또는 safety margin 미달 시 발생."""


class YouTubeApiUnavailable(Exception):
    """API key 미설정 또는 google API 응답 5xx 시 발생."""


# ── 모듈 글로벌 quota 추적 ────────────────────────────────────────────────────
_quota_lock = threading.Lock()
_daily_used: int = 0
_daily_used_date: str = ""   # YYYY-MM-DD UTC, 날짜 변경 시 자동 리셋


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _check_and_consume_quota(units: int) -> None:
    """quota 잔여량 확인 후 차감. 미달 시 YouTubeQuotaExceeded 발생.

    safety_margin 을 두어 quota 가 한도에 너무 근접하지 않도록 보호.
    """
    global _daily_used, _daily_used_date
    with _quota_lock:
        today = _today_utc()
        if today != _daily_used_date:
            # 날짜 변경 — 카운터 리셋
            _daily_used = 0
            _daily_used_date = today

        remaining = YOUTUBE_DAILY_QUOTA - _daily_used
        if remaining - units < YOUTUBE_QUOTA_SAFETY_MARGIN:
            raise YouTubeQuotaExceeded(
                f"일일 quota 한도 근접: used={_daily_used}, requested={units}, "
                f"remaining={remaining}, safety_margin={YOUTUBE_QUOTA_SAFETY_MARGIN}"
            )
        _daily_used += units


def current_quota_used() -> int:
    """오늘(UTC) 현재까지 소비한 quota units 반환 (관측·로깅용)."""
    with _quota_lock:
        return _daily_used if _today_utc() == _daily_used_date else 0


# ── public API ───────────────────────────────────────────────────────────────
def youtube_search_videos(
    query: str,
    *,
    max_results: int = YOUTUBE_MAX_RESULTS,
    region_code: str = YOUTUBE_REGION_CODE,
) -> list[dict]:
    """YouTube 영상 검색 + 통계 메타 수집. 24h TTL 캐시 + quota 추적.

    Parameters
    ----------
    query : str
        검색 쿼리 (한국어 자연어 권장).
    max_results : int
        반환 영상 수 (default config.YOUTUBE_MAX_RESULTS, max 50).
    region_code : str
        ISO 3166-1 alpha-2 (default "KR").

    Returns
    -------
    list[dict]
        각 영상 항목:
        {
          "url":              str (https://www.youtube.com/watch?v={video_id}),
          "video_id":         str,
          "channel_id":       str,
          "channel_title":    str,
          "view_count":       int,
          "like_count":       int,
          "comment_count":    int,
          "published_at":     str (ISO 8601),
          "title":            str,
          "description":      str,
        }

    Raises
    ------
    YouTubeQuotaExceeded : 일일 quota 한도 초과
    YouTubeApiUnavailable : API key 미설정 또는 google API 응답 5xx
    """
    if not YOUTUBE_API_KEY:
        logger.warning("YOUTUBE_API_KEY 미설정 — youtube_search_videos 가 빈 결과 반환")
        raise YouTubeApiUnavailable("YOUTUBE_API_KEY 환경변수 미설정")

    # ── 캐시 조회 ───────────────────────────────────────────────────────────
    cache_input = {
        "query":       query,
        "region_code": region_code,
        "max_results": max_results,
    }
    cache_context = {"agent_id": "youtube_search", "v": 1}
    cached = load_agent_output(
        agent_id="youtube_search",
        cache_input=cache_input,
        context=cache_context,
        logger=logger,
        ttl_hours=YOUTUBE_CACHE_TTL_HOURS,
    )
    if cached is not None:
        items = cached.get("items", []) or []
        logger.info("youtube_search_videos: 캐시 hit (%d videos): %s", len(items), query[:50])
        return items

    # ── 캐시 미스 — 실제 API 호출 ────────────────────────────────────────────
    # 1) search.list 호출 (100 units)
    _check_and_consume_quota(_SEARCH_LIST_COST)
    search_resp = _call_search_list(query, max_results, region_code)
    video_ids = [
        item["id"]["videoId"]
        for item in (search_resp.get("items") or [])
        if isinstance(item.get("id"), dict) and item["id"].get("videoId")
    ]
    if not video_ids:
        logger.info("youtube_search_videos: search.list 결과 0건: %s", query[:50])
        # 빈 결과도 캐시 저장하여 동일 쿼리 반복 호출 방지
        store_agent_output(
            agent_id="youtube_search",
            cache_input=cache_input,
            context=cache_context,
            output={"items": []},
            logger=logger,
        )
        return []

    # 2) videos.list 호출 (1 unit per call, 50 videos 까지 단일 호출)
    _check_and_consume_quota(_VIDEOS_LIST_COST)
    stats_by_id = _call_videos_list(video_ids)

    # 3) search 결과 + videos 통계 머지
    merged: list[dict] = []
    for item in search_resp.get("items") or []:
        vid_obj = item.get("id") or {}
        vid     = vid_obj.get("videoId")
        if not vid:
            continue
        snippet = item.get("snippet") or {}
        stats   = stats_by_id.get(vid) or {}
        merged.append({
            "url":           f"https://www.youtube.com/watch?v={vid}",
            "video_id":      vid,
            "channel_id":    snippet.get("channelId", ""),
            "channel_title": snippet.get("channelTitle", ""),
            "title":         snippet.get("title", ""),
            "description":   snippet.get("description", ""),
            "published_at":  snippet.get("publishedAt", ""),
            "view_count":    int(stats.get("viewCount", 0) or 0),
            "like_count":    int(stats.get("likeCount", 0) or 0),
            "comment_count": int(stats.get("commentCount", 0) or 0),
        })

    # 4) 결과 캐시 저장
    store_agent_output(
        agent_id="youtube_search",
        cache_input=cache_input,
        context=cache_context,
        output={"items": merged},
        logger=logger,
    )
    logger.info(
        "youtube_search_videos: API 호출 완료 (%d videos, quota_used=%d): %s",
        len(merged), current_quota_used(), query[:50],
    )
    return merged


# ── 내부 헬퍼 ────────────────────────────────────────────────────────────────
def _call_search_list(query: str, max_results: int, region_code: str) -> dict[str, Any]:
    """search.list HTTP 호출. 5xx 또는 API key 오류 시 YouTubeApiUnavailable 발생."""
    params = {
        "part":        "snippet",
        "q":           query,
        "type":        "video",
        "order":       "relevance",
        "regionCode":  region_code,
        "maxResults":  min(max_results, 50),       # API 제한 50
        "key":         YOUTUBE_API_KEY,
    }
    try:
        resp = requests.get(_SEARCH_ENDPOINT, params=params, timeout=_HTTP_TIMEOUT)
    except requests.RequestException as exc:
        raise YouTubeApiUnavailable(f"search.list 네트워크 오류: {exc}") from exc

    if resp.status_code in (401, 403):
        # quota exceeded 또는 invalid key
        err_msg = _extract_error_message(resp)
        if "quota" in err_msg.lower():
            raise YouTubeQuotaExceeded(f"search.list quota exceeded: {err_msg}")
        raise YouTubeApiUnavailable(f"search.list 인증 오류({resp.status_code}): {err_msg}")
    if resp.status_code >= 500:
        raise YouTubeApiUnavailable(f"search.list 서버 오류: {resp.status_code}")
    if not resp.ok:
        raise YouTubeApiUnavailable(
            f"search.list 오류 {resp.status_code}: {_extract_error_message(resp)}"
        )
    return resp.json()


def _call_videos_list(video_ids: list[str]) -> dict[str, dict]:
    """videos.list HTTP 호출. video_id → statistics dict 매핑 반환."""
    if not video_ids:
        return {}
    params = {
        "part": "statistics,status",
        "id":   ",".join(video_ids[:50]),    # API 제한 50
        "key":  YOUTUBE_API_KEY,
    }
    try:
        resp = requests.get(_VIDEOS_ENDPOINT, params=params, timeout=_HTTP_TIMEOUT)
    except requests.RequestException as exc:
        raise YouTubeApiUnavailable(f"videos.list 네트워크 오류: {exc}") from exc

    if resp.status_code in (401, 403):
        err_msg = _extract_error_message(resp)
        if "quota" in err_msg.lower():
            raise YouTubeQuotaExceeded(f"videos.list quota exceeded: {err_msg}")
        raise YouTubeApiUnavailable(f"videos.list 인증 오류({resp.status_code}): {err_msg}")
    if resp.status_code >= 500:
        raise YouTubeApiUnavailable(f"videos.list 서버 오류: {resp.status_code}")
    if not resp.ok:
        raise YouTubeApiUnavailable(
            f"videos.list 오류 {resp.status_code}: {_extract_error_message(resp)}"
        )

    by_id: dict[str, dict] = {}
    for item in resp.json().get("items") or []:
        vid    = item.get("id")
        status = item.get("status") or {}
        # 비공개·삭제 영상 제외 (privacyStatus="public" 또는 "unlisted" 만 채택)
        if status.get("privacyStatus") == "private":
            continue
        if vid:
            by_id[vid] = item.get("statistics") or {}
    return by_id


def _extract_error_message(resp: requests.Response) -> str:
    """YouTube API 의 error 응답 파싱."""
    try:
        body = resp.json()
        return body.get("error", {}).get("message", "") or str(body)[:200]
    except Exception:  # noqa: BLE001
        return resp.text[:200]
