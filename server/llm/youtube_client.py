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

import hashlib
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
_SEARCH_ENDPOINT        = "https://www.googleapis.com/youtube/v3/search"
_VIDEOS_ENDPOINT        = "https://www.googleapis.com/youtube/v3/videos"
_COMMENTS_ENDPOINT      = "https://www.googleapis.com/youtube/v3/commentThreads"
_CHANNELS_ENDPOINT      = "https://www.googleapis.com/youtube/v3/channels"
_PLAYLIST_ITEMS_ENDPOINT = "https://www.googleapis.com/youtube/v3/playlistItems"

# Quota costs (Google 공식 문서 기준)
_SEARCH_LIST_COST    = 100   # units per call
_VIDEOS_LIST_COST    = 1     # unit per call (50 videos 까지 단일 호출 = 1 unit)
_COMMENTS_LIST_COST  = 1     # unit per call (100 comments 까지 단일 호출 = 1 unit)
_CHANNELS_LIST_COST  = 1     # unit per call (v1.0 §6-6a 채널 메타)
_PLAYLIST_ITEMS_COST = 1     # unit per call (50 items 까지 단일 호출 = 1 unit)

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


def youtube_videos_list(video_id: str) -> dict | None:
    """v0.10.25 신설 — 단일 video_id 의 메타 + statistics 조회.

    `additional_urls_validation_node` 가 youtube_reactions origin URL 의 정식 검증에
    사용. HEAD/GET 만으로는 삭제·비공개 영상 판정 불가하므로 본 함수가 정확한 메타
    조회를 수행 (1 unit/call).

    Parameters
    ----------
    video_id : str
        YouTube watch URL 의 v= 파라미터 값 (예: "dQw4w9WgXcQ").

    Returns
    -------
    dict | None
        영상 존재 + public/unlisted 시:
        {"video_id", "view_count", "like_count", "comment_count",
         "channel_id", "channel_title", "title", "published_at"}
        영상 부재 (삭제·private) 시 None.

    Raises
    ------
    YouTubeQuotaExceeded : 일일 quota 한도 초과
    YouTubeApiUnavailable : API key 미설정 또는 google API 응답 오류

    Note
    ----
    캐시 미적용 — 단건 조회의 캐시 hit 율이 낮고, 24h 안에 영상 메타 (view_count)
    가 빠르게 변동되므로 매번 신선한 값 수집이 정합. quota 부담은 호출당 1 unit 으로 미미.
    """
    if not YOUTUBE_API_KEY:
        logger.warning("YOUTUBE_API_KEY 미설정 — youtube_videos_list 빈 결과")
        raise YouTubeApiUnavailable("YOUTUBE_API_KEY 환경변수 미설정")
    if not video_id:
        return None

    # videos.list 는 statistics·snippet·status 함께 호출 (단일 호출, 1 unit)
    _check_and_consume_quota(_VIDEOS_LIST_COST)
    params = {
        "part": "snippet,statistics,status",
        "id":   video_id,
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

    items = resp.json().get("items") or []
    if not items:
        # 영상 부재 (삭제·private)
        return None
    item = items[0]
    status = item.get("status") or {}
    if status.get("privacyStatus") == "private":
        return None

    snippet = item.get("snippet") or {}
    stats   = item.get("statistics") or {}
    return {
        "video_id":      video_id,
        "view_count":    int(stats.get("viewCount", 0) or 0),
        "like_count":    int(stats.get("likeCount", 0) or 0),
        "comment_count": int(stats.get("commentCount", 0) or 0),
        "channel_id":    snippet.get("channelId", ""),
        "channel_title": snippet.get("channelTitle", ""),
        "title":         snippet.get("title", ""),
        "published_at":  snippet.get("publishedAt", ""),
    }


# ── 내부 헬퍼 ────────────────────────────────────────────────────────────────
def youtube_comment_threads(video_id: str, *, max_results: int = 100) -> list[dict]:
    """영상 1건의 최상위 댓글 수집 (commentThreads.list, 1 unit). 24h TTL 캐시.

    v0.13 (reaction_insight 시리즈) — youtube_reaction_collection_node 가 사용.
    order=relevance 1 page 수집 후 좋아요 상위 선별은 호출자(코드) 책임.

    Returns
    -------
    list[dict]
        각 댓글 항목 (작성자 식별정보 비저장 — D11):
        {"comment_id", "text"(textOriginal 원문), "like_count", "published_at",
         "author_hash"(중복 작성자 탐지용 sha256 prefix)}
        댓글 비활성(commentsDisabled) 영상은 빈 리스트 반환 (예외 아님 — 부분 실패 허용).

    Raises
    ------
    YouTubeQuotaExceeded / YouTubeApiUnavailable
    """
    if not YOUTUBE_API_KEY:
        raise YouTubeApiUnavailable("YOUTUBE_API_KEY 환경변수 미설정")

    cache_input = {"video_id": video_id, "max_results": max_results}
    cache_context = {"agent_id": "youtube_comments", "v": 1}
    cached = load_agent_output(
        agent_id="youtube_comments", cache_input=cache_input,
        context=cache_context, logger=logger, ttl_hours=YOUTUBE_CACHE_TTL_HOURS,
    )
    if cached is not None:
        return cached.get("items", []) or []

    _check_and_consume_quota(_COMMENTS_LIST_COST)
    params = {
        "part":       "snippet",
        "videoId":    video_id,
        "order":      "relevance",
        "maxResults": min(max_results, 100),     # API 제한 100
        "textFormat": "plainText",
        "key":        YOUTUBE_API_KEY,
    }
    try:
        resp = requests.get(_COMMENTS_ENDPOINT, params=params, timeout=_HTTP_TIMEOUT)
    except requests.RequestException as exc:
        raise YouTubeApiUnavailable(f"commentThreads.list 네트워크 오류: {exc}") from exc

    if resp.status_code == 403:
        err_msg = _extract_error_message(resp)
        if "quota" in err_msg.lower():
            raise YouTubeQuotaExceeded(f"commentThreads quota exceeded: {err_msg}")
        if "disabled" in err_msg.lower() or "commentsDisabled" in resp.text:
            # 댓글 비활성 영상 — 빈 결과 캐시 (반복 호출 방지)
            logger.info("youtube_comment_threads: 댓글 비활성 영상 skip (%s)", video_id)
            store_agent_output(agent_id="youtube_comments", cache_input=cache_input,
                               context=cache_context, output={"items": []}, logger=logger)
            return []
        raise YouTubeApiUnavailable(f"commentThreads 인증 오류(403): {err_msg}")
    if resp.status_code == 401:
        raise YouTubeApiUnavailable(f"commentThreads 인증 오류(401): {_extract_error_message(resp)}")
    if resp.status_code == 404:
        logger.info("youtube_comment_threads: 영상 없음 skip (%s)", video_id)
        store_agent_output(agent_id="youtube_comments", cache_input=cache_input,
                           context=cache_context, output={"items": []}, logger=logger)
        return []
    if resp.status_code >= 500:
        raise YouTubeApiUnavailable(f"commentThreads 서버 오류: {resp.status_code}")
    if not resp.ok:
        raise YouTubeApiUnavailable(
            f"commentThreads 오류 {resp.status_code}: {_extract_error_message(resp)}")

    items: list[dict] = []
    for thread in resp.json().get("items") or []:
        top = ((thread.get("snippet") or {}).get("topLevelComment") or {}).get("snippet") or {}
        text = (top.get("textOriginal") or top.get("textDisplay") or "").strip()
        if not text:
            continue
        author_channel = (top.get("authorChannelId") or {}).get("value", "")
        items.append({
            "comment_id":   thread.get("id", ""),
            "text":         text,
            "like_count":   int(top.get("likeCount", 0) or 0),
            "published_at": top.get("publishedAt", ""),
            # D11 — 작성자 식별정보 비저장. 동일 작성자 도배 탐지용 해시 prefix 만 보존.
            "author_hash":  hashlib.sha256(author_channel.encode("utf-8")).hexdigest()[:12]
                            if author_channel else "",
        })

    store_agent_output(agent_id="youtube_comments", cache_input=cache_input,
                       context=cache_context, output={"items": items}, logger=logger)
    logger.info("youtube_comment_threads: %d comments (quota_used=%d, video=%s)",
                len(items), current_quota_used(), video_id)
    return items


def youtube_videos_statistics(video_ids: list[str]) -> dict[str, dict]:
    """영상 통계 일괄 조회 (videos.list part=statistics, 50건까지 1 unit). 24h TTL 캐시.

    v0.13.1 (RI-D9) — feature_mapping 출력의 existing_urls 가 view_count 메타를
    carry 하지 않아(실측: 전부 0) RI-D4 조회수 정렬이 무력화되는 문제의 보강.
    수집 노드가 선별 전에 후보 풀 전체의 통계를 일괄 조회한다.

    Returns
    -------
    dict[str, dict]
        {video_id: {"view_count", "like_count", "comment_count"}}
        (비공개·삭제 영상은 _call_videos_list 에서 제외됨)
    """
    if not video_ids:
        return {}
    if not YOUTUBE_API_KEY:
        raise YouTubeApiUnavailable("YOUTUBE_API_KEY 환경변수 미설정")

    out: dict[str, dict] = {}
    ids_sorted = sorted(set(video_ids))
    for i in range(0, len(ids_sorted), 50):          # API 제한 50건/호출
        chunk = ids_sorted[i:i + 50]
        cache_input = {"video_ids": chunk, "part": "statistics"}
        cache_context = {"agent_id": "youtube_statistics", "v": 1}
        cached = load_agent_output(
            agent_id="youtube_statistics", cache_input=cache_input,
            context=cache_context, logger=logger, ttl_hours=YOUTUBE_CACHE_TTL_HOURS,
        )
        if cached is not None:
            out.update(cached.get("by_id", {}) or {})
            continue
        _check_and_consume_quota(_VIDEOS_LIST_COST)
        stats_by_id = _call_videos_list(chunk)
        by_id = {
            vid: {
                "view_count":    int(stats.get("viewCount", 0) or 0),
                "like_count":    int(stats.get("likeCount", 0) or 0),
                "comment_count": int(stats.get("commentCount", 0) or 0),
            }
            for vid, stats in stats_by_id.items()
        }
        store_agent_output(agent_id="youtube_statistics", cache_input=cache_input,
                           context=cache_context, output={"by_id": by_id}, logger=logger)
        out.update(by_id)
    return out


def youtube_videos_snippet(video_ids: list[str]) -> dict[str, dict]:
    """영상 제목·설명 일괄 조회 (videos.list part=snippet, 50건까지 1 unit). 24h TTL 캐시.

    v0.13 — 댓글 해석 맥락용 영상 메타 (RI-D2: 자막 대신 공식 snippet 수집).

    Returns
    -------
    dict[str, dict]
        {video_id: {"title", "description", "published_at", "channel_title"}}
    """
    if not video_ids:
        return {}
    if not YOUTUBE_API_KEY:
        raise YouTubeApiUnavailable("YOUTUBE_API_KEY 환경변수 미설정")

    ids_sorted = sorted(set(video_ids))[:50]
    cache_input = {"video_ids": ids_sorted}
    cache_context = {"agent_id": "youtube_snippets", "v": 1}
    cached = load_agent_output(
        agent_id="youtube_snippets", cache_input=cache_input,
        context=cache_context, logger=logger, ttl_hours=YOUTUBE_CACHE_TTL_HOURS,
    )
    if cached is not None:
        return cached.get("by_id", {}) or {}

    _check_and_consume_quota(_VIDEOS_LIST_COST)
    params = {"part": "snippet", "id": ",".join(ids_sorted), "key": YOUTUBE_API_KEY}
    try:
        resp = requests.get(_VIDEOS_ENDPOINT, params=params, timeout=_HTTP_TIMEOUT)
    except requests.RequestException as exc:
        raise YouTubeApiUnavailable(f"videos.list(snippet) 네트워크 오류: {exc}") from exc
    if resp.status_code in (401, 403):
        err_msg = _extract_error_message(resp)
        if "quota" in err_msg.lower():
            raise YouTubeQuotaExceeded(f"videos.list(snippet) quota exceeded: {err_msg}")
        raise YouTubeApiUnavailable(f"videos.list(snippet) 인증 오류: {err_msg}")
    if not resp.ok:
        raise YouTubeApiUnavailable(
            f"videos.list(snippet) 오류 {resp.status_code}: {_extract_error_message(resp)}")

    by_id: dict[str, dict] = {}
    for item in resp.json().get("items") or []:
        snippet = item.get("snippet") or {}
        if item.get("id"):
            by_id[item["id"]] = {
                "title":         snippet.get("title", ""),
                "description":   snippet.get("description", ""),
                "published_at":  snippet.get("publishedAt", ""),
                "channel_title": snippet.get("channelTitle", ""),
            }
    store_agent_output(agent_id="youtube_snippets", cache_input=cache_input,
                       context=cache_context, output={"by_id": by_id}, logger=logger)
    return by_id


def youtube_channel_info(channel_url: str) -> dict | None:
    """공식 채널 메타 조회 (channels.list, 1 unit). 24h TTL 캐시. v1.0 §6-6a.

    URL 형태별 조회 파라미터 (url_discovery_owned_channels 와 동일 규약):
    - /channel/UC…  → id
    - /user/X       → forUsername
    - /@handle      → forHandle
    - /c/X          → forHandle (@X 시도 — 실패 시 None)

    Returns
    -------
    dict | None
        {"channel_id", "title", "subscriber_count", "video_count",
         "uploads_playlist_id"} — 채널 미발견 시 None
    """
    if not YOUTUBE_API_KEY:
        raise YouTubeApiUnavailable("YOUTUBE_API_KEY 환경변수 미설정")

    rest = channel_url.split("//", 1)[-1]
    path = rest.split("/", 1)[1] if "/" in rest else ""
    segs = [s for s in path.split("?")[0].split("#")[0].split("/") if s]
    if segs and segs[0] == "channel" and len(segs) >= 2:
        lookup = {"id": segs[1]}
    elif segs and segs[0] == "user" and len(segs) >= 2:
        lookup = {"forUsername": segs[1]}
    elif segs and segs[0] == "c" and len(segs) >= 2:
        lookup = {"forHandle": f"@{segs[1]}"}
    elif segs and segs[0].startswith("@"):
        lookup = {"forHandle": segs[0]}
    else:
        return None

    cache_input = {"lookup": lookup}
    cache_context = {"agent_id": "youtube_channels", "v": 1}
    cached = load_agent_output(
        agent_id="youtube_channels", cache_input=cache_input,
        context=cache_context, logger=logger, ttl_hours=YOUTUBE_CACHE_TTL_HOURS,
    )
    if cached is not None:
        return cached.get("info") or None

    _check_and_consume_quota(_CHANNELS_LIST_COST)
    params = {
        "part": "snippet,statistics,contentDetails", **lookup, "key": YOUTUBE_API_KEY,
    }
    try:
        resp = requests.get(_CHANNELS_ENDPOINT, params=params, timeout=_HTTP_TIMEOUT)
    except requests.RequestException as exc:
        raise YouTubeApiUnavailable(f"channels.list 네트워크 오류: {exc}") from exc
    if resp.status_code in (401, 403):
        err_msg = _extract_error_message(resp)
        if "quota" in err_msg.lower():
            raise YouTubeQuotaExceeded(f"channels.list quota exceeded: {err_msg}")
        raise YouTubeApiUnavailable(f"channels.list 인증 오류: {err_msg}")
    if not resp.ok:
        raise YouTubeApiUnavailable(
            f"channels.list 오류 {resp.status_code}: {_extract_error_message(resp)}")

    items = resp.json().get("items") or []
    info = None
    if items:
        it = items[0]
        stats = it.get("statistics") or {}
        info = {
            "channel_id":          it.get("id", ""),
            "title":               (it.get("snippet") or {}).get("title", ""),
            "subscriber_count":    int(stats.get("subscriberCount", 0) or 0),
            "video_count":         int(stats.get("videoCount", 0) or 0),
            "uploads_playlist_id": ((it.get("contentDetails") or {})
                                    .get("relatedPlaylists") or {}).get("uploads", ""),
        }
    store_agent_output(agent_id="youtube_channels", cache_input=cache_input,
                       context=cache_context, output={"info": info}, logger=logger)
    return info


def youtube_playlist_items(playlist_id: str, *, max_results: int = 50) -> list[dict]:
    """업로드 playlist 의 최근 영상 목록 (playlistItems.list). 24h TTL 캐시.

    v1.0.4 — max_results > 50 시 nextPageToken 페이징 (페이지당 1 unit, 50건 단위).

    Returns
    -------
    list[dict]
        [{"video_id", "title", "published_at", "description"}] — 최신순 (API 기본 정렬)
        description 은 300자 발췌 (MS-D10 상품 관련성 판정용)
    """
    if not playlist_id:
        return []
    if not YOUTUBE_API_KEY:
        raise YouTubeApiUnavailable("YOUTUBE_API_KEY 환경변수 미설정")

    cache_input = {"playlist_id": playlist_id, "max_results": max_results}
    cache_context = {"agent_id": "youtube_playlist_items", "v": 3}   # v3: 페이징
    cached = load_agent_output(
        agent_id="youtube_playlist_items", cache_input=cache_input,
        context=cache_context, logger=logger, ttl_hours=YOUTUBE_CACHE_TTL_HOURS,
    )
    if cached is not None:
        return cached.get("items", []) or []

    items: list[dict] = []
    page_token = ""
    while len(items) < max_results:
        _check_and_consume_quota(_PLAYLIST_ITEMS_COST)
        params = {
            "part":       "snippet,contentDetails",
            "playlistId": playlist_id,
            "maxResults": min(max_results - len(items), 50),
            "key":        YOUTUBE_API_KEY,
        }
        if page_token:
            params["pageToken"] = page_token
        try:
            resp = requests.get(_PLAYLIST_ITEMS_ENDPOINT, params=params, timeout=_HTTP_TIMEOUT)
        except requests.RequestException as exc:
            raise YouTubeApiUnavailable(f"playlistItems.list 네트워크 오류: {exc}") from exc
        if resp.status_code == 404:
            break   # playlist 부재 (영상 0건 채널)
        if resp.status_code in (401, 403):
            err_msg = _extract_error_message(resp)
            if "quota" in err_msg.lower():
                raise YouTubeQuotaExceeded(f"playlistItems.list quota exceeded: {err_msg}")
            raise YouTubeApiUnavailable(f"playlistItems.list 인증 오류: {err_msg}")
        if not resp.ok:
            raise YouTubeApiUnavailable(
                f"playlistItems.list 오류 {resp.status_code}: {_extract_error_message(resp)}")

        data = resp.json()
        for it in data.get("items") or []:
            snippet = it.get("snippet") or {}
            vid = ((it.get("contentDetails") or {}).get("videoId")
                   or ((snippet.get("resourceId") or {}).get("videoId")))
            if vid:
                items.append({
                    "video_id":     vid,
                    "title":        snippet.get("title", ""),
                    "published_at": ((it.get("contentDetails") or {}).get("videoPublishedAt")
                                     or snippet.get("publishedAt", "")),
                    "description":  (snippet.get("description") or "")[:300],
                })
        page_token = data.get("nextPageToken", "")
        if not page_token:
            break

    store_agent_output(agent_id="youtube_playlist_items", cache_input=cache_input,
                       context=cache_context, output={"items": items}, logger=logger)
    return items


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
