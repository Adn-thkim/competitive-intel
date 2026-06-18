#!/usr/bin/env python3
"""
scripts/measure_youtube_collection.py  (v0.1 — 수집 품질 관측용)
---------------------------------------------------------------
Phase 1 전환 전 YouTube 수집 품질 측정 스크립트.

목적
----
- candidate_name 기반 검색 (hint 없음, publishedAfter 5년, order=date)
- 댓글 전량 수집 (production 필터 없음)
- youtube-transcript-api 로 자막 추출
- ABSA codebook aspect 히트율 통계 출력

실행
----
  cd competitive-intel
  python -m scripts.measure_youtube_collection
  python -m scripts.measure_youtube_collection --taxonomy data/taxonomy/3_slug.json
  python -m scripts.measure_youtube_collection --candidates "토스트래블카드,트래블월렛" --years 3
  python -m scripts.measure_youtube_collection --no-transcript --no-comments

출력
----
  data/measurement/youtube_collection_{slug}_{ts}.json
  stdout: per-candidate 요약 테이블
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

# ── 경로 ─────────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from server.config import YOUTUBE_API_KEY, YOUTUBE_REGION_CODE  # noqa: E402

# ── YouTube API 엔드포인트 ────────────────────────────────────────────────────
_SEARCH_URL   = "https://www.googleapis.com/youtube/v3/search"
_VIDEOS_URL   = "https://www.googleapis.com/youtube/v3/videos"
_COMMENTS_URL = "https://www.googleapis.com/youtube/v3/commentThreads"
_HTTP_TIMEOUT = 20

# ── 기본값 ────────────────────────────────────────────────────────────────────
DEFAULT_YEARS          = 5
DEFAULT_MAX_VIDEOS     = 0    # 0 = search 페이징 전량 (YouTube 내부 소진까지, ~200-500건)
DEFAULT_MAX_COMMENTS   = 0    # 0 = 전량 수집 (페이징 무제한)
DEFAULT_MAX_VIDEO_PAGES = 10  # search.list 최대 페이지 수 (페이지당 50건, 100 units)
DEFAULT_TAXONOMY_GLOB  = "data/taxonomy/*.json"

# ── ABSA 히트 판정 키워드 (aspect_codebook label / definition 기반) ────────────
# 측정 전용 — production 분류는 LLM ABSA 가 수행.
_ASPECT_KW: dict[str, list[str]] = {
    "overseas_payment_convenience": ["해외결제", "해외 결제", "결제 안됨", "결제오류", "결제 오류", "해외에서"],
    "exchange_rate_fairness":       ["환율", "환전 수수료", "수수료"],
    "atm_withdrawal_ux":           ["ATM", "atm", "현금인출", "현금 인출", "출금"],
    "app_ux_quality":              ["앱", "어플", "앱이", "앱에서", "인터페이스"],
    "emergency_card_lock":         ["잠금", "분실", "카드 정지", "카드잠금", "분실신고"],
    "pricing_perception":          ["수수료", "비용", "요금", "이용료", "연회비"],
    "travel_benefit_value":        ["혜택", "마일리지", "마일", "포인트", "라운지"],
    "customer_support":            ["고객센터", "상담", "CS", "문의"],
    "fx_reload_convenience":       ["충전", "환전하기", "재충전", "잔액"],
}

# ─── YouTube API 호출 (캐시 없음 — 측정 목적) ─────────────────────────────────


def _search_videos(query: str, published_after: str,
                   max_videos: int = 0, max_pages: int = DEFAULT_MAX_VIDEO_PAGES,
                   min_views: int = 0,
                   ) -> list[dict]:
    """search.list 페이징 → videos.list 통계 머지.

    max_videos=0 이면 YouTube 검색 결과 소진(nextPageToken 없음)까지 전량 수집.
    페이지당 100 units 소모. max_pages 로 quota 상한 제어.
    """
    all_items: list[dict] = []
    page_token: str | None = None
    page = 0

    while page < max_pages:
        params: dict[str, Any] = {
            "part":           "snippet",
            "q":              query,
            "type":           "video",
            "order":          "date",
            "regionCode":     YOUTUBE_REGION_CODE,
            "maxResults":     50,
            "publishedAfter": published_after,
            "key":            YOUTUBE_API_KEY,
        }
        if page_token:
            params["pageToken"] = page_token
        try:
            resp = requests.get(_SEARCH_URL, params=params, timeout=_HTTP_TIMEOUT)
            resp.raise_for_status()
        except requests.RequestException as exc:
            print(f"  [ERROR] search.list 실패 (page={page}): {exc}", file=sys.stderr)
            break

        data = resp.json()
        all_items.extend(data.get("items") or [])
        page += 1
        page_token = data.get("nextPageToken")
        print(f"  search page {page}: {len(data.get('items') or [])}건 "
              f"(누적 {len(all_items)}건)"
              + (" → 소진" if not page_token else ""), flush=True)
        if not page_token:
            break
        if max_videos and len(all_items) >= max_videos:
            break
        time.sleep(0.3)

    if max_videos:
        all_items = all_items[:max_videos]

    # video_id 추출 + dedup (search 페이징 중복 방지)
    # ※ min_views 필터는 videos.list 통계 보강 후 적용
    seen: set[str] = set()
    video_ids: list[str] = []
    snippet_by_id: dict[str, dict] = {}
    for it in all_items:
        vid = (it.get("id") or {}).get("videoId", "")
        if vid and vid not in seen:
            seen.add(vid)
            video_ids.append(vid)
            snippet_by_id[vid] = it.get("snippet") or {}

    if not video_ids:
        return []

    # videos.list — statistics + snippet 보강 (50건 chunk, 1 unit/chunk)
    stats_by_id: dict[str, dict] = {}
    for chunk_start in range(0, len(video_ids), 50):
        chunk = video_ids[chunk_start: chunk_start + 50]
        vp = {
            "part": "statistics,snippet",
            "id":   ",".join(chunk),
            "key":  YOUTUBE_API_KEY,
        }
        try:
            vresp = requests.get(_VIDEOS_URL, params=vp, timeout=_HTTP_TIMEOUT)
            vresp.raise_for_status()
            for vi in vresp.json().get("items") or []:
                stats = vi.get("statistics") or {}
                snip  = vi.get("snippet") or {}
                stats_by_id[vi["id"]] = {
                    "title":         snip.get("title", ""),
                    "description":   snip.get("description", "")[:1000],
                    "channel_title": snip.get("channelTitle", ""),
                    "channel_id":    snip.get("channelId", ""),
                    "published_at":  snip.get("publishedAt", ""),
                    "view_count":    int(stats.get("viewCount", 0) or 0),
                    "like_count":    int(stats.get("likeCount", 0) or 0),
                    "comment_count": int(stats.get("commentCount", 0) or 0),
                }
        except requests.RequestException as exc:
            print(f"  [WARN] videos.list 실패 (chunk): {exc}", file=sys.stderr)

    results = []
    filtered_count = 0
    for vid in video_ids:
        snip = snippet_by_id.get(vid, {})
        meta = stats_by_id.get(vid, {})
        view_count = meta.get("view_count", 0)
        if min_views and view_count < min_views:
            filtered_count += 1
            continue
        results.append({
            "video_id":      vid,
            "url":           f"https://www.youtube.com/watch?v={vid}",
            "title":         meta.get("title") or snip.get("title", ""),
            "description":   meta.get("description", ""),
            "channel_title": meta.get("channel_title") or snip.get("channelTitle", ""),
            "channel_id":    meta.get("channel_id") or snip.get("channelId", ""),
            "published_at":  meta.get("published_at") or snip.get("publishedAt", ""),
            "view_count":    view_count,
            "like_count":    meta.get("like_count", 0),
            "comment_count": meta.get("comment_count", 0),
        })
    if filtered_count:
        print(f"  min_views({min_views:,}) 미달 {filtered_count}건 제외 → {len(results)}건")
    return results


def _fetch_comments(video_id: str, max_total: int = 0) -> tuple[list[dict], str]:
    """commentThreads.list 페이징. max_total=0 이면 전량 수집.

    Returns
    -------
    (comments, status)
      status: "ok" | "disabled" | "quota_exceeded" | "error"
    """
    # YR-D1 Phase A — 생산 파서 재사용으로 대댓글+parent 필드 일관 수집.
    from server.llm.youtube_client import _parse_comment_thread

    comments: list[dict] = []
    page_token: str | None = None
    page = 0

    while True:
        if max_total and len(comments) >= max_total:
            break
        params: dict[str, Any] = {
            "part":       "snippet,replies",
            "videoId":    video_id,
            "order":      "relevance",
            "maxResults": 100,
            "key":        YOUTUBE_API_KEY,
        }
        if page_token:
            params["pageToken"] = page_token
        try:
            resp = requests.get(_COMMENTS_URL, params=params, timeout=_HTTP_TIMEOUT)
            if resp.status_code == 403:
                data = resp.json()
                reason = (data.get("error", {}).get("errors") or [{}])[0].get("reason", "")
                if reason == "commentsDisabled":
                    return comments, "disabled"
                return comments, "quota_exceeded"
            resp.raise_for_status()
        except requests.RequestException as exc:
            print(f"  [WARN] commentThreads 실패 (video={video_id}): {exc}", file=sys.stderr)
            return comments, "error"

        data = resp.json()
        page += 1
        for item in data.get("items") or []:
            comments.extend(_parse_comment_thread(item))
        page_token = data.get("nextPageToken")
        if not page_token:
            break
        time.sleep(0.2)

    if max_total:
        comments = comments[:max_total]
    return comments, "ok"


# ─── Transcript 수집 ──────────────────────────────────────────────────────────

def _fetch_transcript(video_id: str) -> tuple[str | None, str]:
    """youtube-transcript-api 로 자막 추출.

    Returns
    -------
    (transcript_text | None, status)
      status: "ok_manual" | "ok_generated" | "no_korean" | "disabled" | "unavailable"
    """
    try:
        from youtube_transcript_api import (  # noqa: PLC0415
            NoTranscriptFound,
            TranscriptsDisabled,
            YouTubeTranscriptApi,
        )
    except ImportError:
        return None, "unavailable"

    api = YouTubeTranscriptApi()
    try:
        tl = api.list(video_id)
        # 수동 한국어 우선, 없으면 자동 생성 한국어
        for lang_codes, label in (
            (["ko"], "ok_manual"),
            (["ko"], "ok_generated"),
        ):
            try:
                tr = tl.find_manually_created_transcript(lang_codes) \
                    if label == "ok_manual" \
                    else tl.find_generated_transcript(lang_codes)
                entries = tr.fetch().to_raw_data()
                text = " ".join(e["text"] for e in entries if e.get("text"))
                return text, label
            except NoTranscriptFound:
                continue
        return None, "no_korean"
    except TranscriptsDisabled:
        return None, "disabled"
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "403" in msg or "Forbidden" in msg or "proxy" in msg.lower():
            return None, "proxy_blocked"
        if "RequestBlocked" in type(e).__name__ or "IpBlocked" in type(e).__name__:
            return None, "ip_blocked"
        if "ConnectionError" in type(e).__name__ or "Timeout" in type(e).__name__:
            return None, "network_error"
        return None, f"error:{type(e).__name__}"


# ─── Candidate 자동 탐지 ──────────────────────────────────────────────────────

def _detect_candidates(cache_dir: Path) -> dict[str, str]:
    """official_content_collection 캐시에서 candidate_id → search_name 매핑 추출.

    동일 제품이 두 슬러그로 분리된 경우(예: comp_트래블월렛 / comp_트래블월렛카드)
    한 슬러그의 이름이 다른 슬러그 이름의 접두사이면 짧은 슬러그를 제거한다.
    """
    cache_file = cache_dir / "agent_outputs" / "official_content_collection.json"
    if not cache_file.exists():
        return {}
    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        entries = data.get("entries") or {}
        cids: set[str] = set()
        for v in entries.values():
            out = v.get("output", {})
            if isinstance(out, dict) and "candidate_id" in out:
                cids.add(out["candidate_id"])

        # 접두사 제거 후 이름 매핑
        name_map = {cid: re.sub(r"^(own_|comp_|func_)", "", cid) for cid in sorted(cids)}

        # 중복 제거: name_A가 name_B의 접두사이면 name_A(짧은 쪽) 제거
        names = sorted(name_map.values(), key=len)
        dominated: set[str] = set()
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                if b.startswith(a):
                    dominated.add(a)
                    break

        return {cid: name for cid, name in name_map.items() if name not in dominated}
    except Exception:  # noqa: BLE001
        return {}


def _load_aspect_codebook(taxonomy_path: Path) -> list[dict]:
    """taxonomy JSON에서 reaction_insight.aspect_codebook 추출."""
    try:
        data = json.loads(taxonomy_path.read_text(encoding="utf-8"))
        return (data.get("report_config") or {}) \
                    .get("reaction_insight", {}) \
                    .get("aspect_codebook") or []
    except Exception:  # noqa: BLE001
        return []


def _find_taxonomy(root: Path, slug_hint: str | None = None) -> Path | None:
    """가장 최근 수정된 taxonomy JSON 반환."""
    candidates = sorted(
        (root / "data" / "taxonomy").glob("*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if slug_hint:
        for p in candidates:
            if slug_hint in p.name:
                return p
    return candidates[0] if candidates else None


# ─── ABSA 히트율 통계 ─────────────────────────────────────────────────────────

def _build_corpus(video: dict, include_transcript: bool) -> str:
    """title + description + transcript + comments 전체 텍스트 합성."""
    parts = [video.get("title", ""), video.get("description", "")]
    if include_transcript and video.get("transcript"):
        parts.append(video["transcript"])
    for c in video.get("comments") or []:
        parts.append(c.get("text", ""))
    return " ".join(parts)


def _aspect_hits(videos: list[dict], codebook: list[dict]) -> tuple[dict[str, dict], dict]:
    """codebook aspect별 히트율 + 전체 유효 데이터 비율.

    Returns
    -------
    (per_aspect_stats, overall_validity)
      overall_validity: {
        "valid_video_count":   int,   # 1개 이상 aspect 히트된 영상 수
        "valid_video_rate":    float,
        "valid_comment_count": int,   # 1개 이상 aspect 히트된 댓글 수
        "valid_comment_rate":  float,
        "total_videos":        int,
        "total_comments":      int,
      }
    """
    aspect_ids = [a["aspect_id"] for a in codebook] if codebook else list(_ASPECT_KW.keys())
    all_kws: list[str] = []
    kw_by_aid: dict[str, list[str]] = {}
    for aid in aspect_ids:
        kws = _ASPECT_KW.get(aid) or []
        if not kws:
            label = next((a.get("label", "") for a in codebook if a["aspect_id"] == aid), "")
            if label:
                kws = [label]
        kw_by_aid[aid] = kws
        all_kws.extend(kws)

    total_videos   = len(videos)
    total_comments = sum(len(v.get("comments") or []) for v in videos)

    per_aspect: dict[str, dict] = {}
    valid_video_ids:   set[str] = set()
    valid_comment_ids: set[str] = set()

    for aid in aspect_ids:
        kws = kw_by_aid[aid]
        hit_videos = hit_comments = 0
        for v in videos:
            title_body = (f"{v.get('title','')} "
                          f"{v.get('description','')} "
                          f"{v.get('transcript','') or ''}")
            if any(kw in title_body for kw in kws):
                hit_videos += 1
                valid_video_ids.add(v["video_id"])
            for c in v.get("comments") or []:
                text = c.get("text", "")
                if any(kw in text for kw in kws):
                    hit_comments += 1
                    valid_comment_ids.add(c.get("comment_id", text[:40]))
        per_aspect[aid] = {
            "hit_videos":    hit_videos,
            "hit_comments":  hit_comments,
            "video_rate":    round(hit_videos / total_videos, 3) if total_videos else 0.0,
            "comment_rate":  round(hit_comments / total_comments, 3) if total_comments else 0.0,
        }

    overall = {
        "valid_video_count":   len(valid_video_ids),
        "valid_video_rate":    round(len(valid_video_ids) / total_videos, 3) if total_videos else 0.0,
        "valid_comment_count": len(valid_comment_ids),
        "valid_comment_rate":  round(len(valid_comment_ids) / total_comments, 3) if total_comments else 0.0,
        "total_videos":        total_videos,
        "total_comments":      total_comments,
    }
    return per_aspect, overall


# ─── 출력 헬퍼 ────────────────────────────────────────────────────────────────

def _bar(rate: float, width: int = 20) -> str:
    filled = round(rate * width)
    return "█" * filled + "░" * (width - filled)


def _print_summary(candidate_id: str, result: dict, codebook: list[dict]) -> None:
    name     = result.get("search_query", candidate_id)
    overall  = result.get("overall_validity") or {}
    tr_ok    = result.get("transcript_ok", 0)
    tr_total = tr_ok + result.get("transcript_fail", 0)

    print(f"\n{'─'*60}")
    print(f"  {candidate_id}  /  검색어: {name!r}")
    print(f"  영상 {overall.get('total_videos',0)}건  "
          f"댓글 {overall.get('total_comments',0)}건  "
          + (f"자막 성공 {tr_ok}/{tr_total}건" if tr_total else "자막 수집 스킵"))
    print()

    vvr = overall.get("valid_video_rate", 0)
    vcr = overall.get("valid_comment_rate", 0)
    print(f"  {'[유효 데이터 비율]':<22}  "
          f"영상 {vvr:.0%} {_bar(vvr,15)}  댓글 {vcr:.0%} {_bar(vcr,15)}")
    print(f"  {'':22}  "
          f"  ({overall.get('valid_video_count',0)}/{overall.get('total_videos',0)}건)"
          f"              ({overall.get('valid_comment_count',0)}/{overall.get('total_comments',0)}건)")
    print()

    hits      = result.get("aspect_hit_rate") or {}
    label_map = {a["aspect_id"]: a.get("label", a["aspect_id"]) for a in codebook}
    for aid, s in hits.items():
        label = label_map.get(aid, aid)
        vr = s["video_rate"]
        cr = s["comment_rate"]
        print(f"  {label:<22}  영상 {vr:.0%} {_bar(vr,15)}  댓글 {cr:.0%} {_bar(cr,15)}")
    print()


# ─── 메인 ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="YouTube 수집 품질 측정 (Phase 1 전환 전 관측용)")
    parser.add_argument("--taxonomy", type=Path,
                        help="taxonomy JSON 경로 (default: 최신 data/taxonomy/*.json)")
    parser.add_argument("--candidates", type=str,
                        help="후보 쉼표 목록 (예: '토스트래블카드,트래블월렛'). "
                             "미지정 시 캐시에서 자동 탐지.")
    parser.add_argument("--years", type=int, default=DEFAULT_YEARS,
                        help=f"검색 기간 (년, default={DEFAULT_YEARS})")
    parser.add_argument("--max-videos", type=int, default=DEFAULT_MAX_VIDEOS,
                        help="candidate당 최대 영상 수 (default=0=전량)")
    parser.add_argument("--min-views", type=int, default=0,
                        help="조회수 하한 필터 (default=0=필터 없음, 예: 1000)")
    parser.add_argument("--max-video-pages", type=int, default=DEFAULT_MAX_VIDEO_PAGES,
                        help=f"search.list 최대 페이지 수 (default={DEFAULT_MAX_VIDEO_PAGES}, "
                             f"페이지당 50건·100 units)")
    parser.add_argument("--max-comments", type=int, default=DEFAULT_MAX_COMMENTS,
                        help="영상당 최대 댓글 수 (default=0=전량)")
    parser.add_argument("--no-transcript", action="store_true",
                        help="자막 수집 스킵")
    parser.add_argument("--no-comments", action="store_true",
                        help="댓글 수집 스킵 (영상 메타만)")
    args = parser.parse_args()

    if not YOUTUBE_API_KEY:
        sys.exit("[ERROR] YOUTUBE_API_KEY 환경변수 미설정.")

    root = PROJECT_ROOT

    # taxonomy 로드
    taxonomy_path = args.taxonomy or _find_taxonomy(root)
    if not taxonomy_path or not taxonomy_path.exists():
        sys.exit("[ERROR] taxonomy 파일을 찾을 수 없습니다. --taxonomy 로 지정하세요.")
    slug = taxonomy_path.stem
    codebook = _load_aspect_codebook(taxonomy_path)
    print(f"taxonomy: {taxonomy_path.name}  (aspect {len(codebook)}개)")

    # candidate 목록
    cache_dir = root / "data" / "cache"
    if args.candidates:
        # CLI 입력: "토스트래블카드,트래블월렛" 형식 (candidate_id 없이 이름만)
        names = [n.strip() for n in args.candidates.split(",") if n.strip()]
        candidate_map = {n: n for n in names}
    else:
        candidate_map = _detect_candidates(cache_dir)
        if not candidate_map:
            sys.exit("[ERROR] candidate를 자동 탐지하지 못했습니다. --candidates 로 지정하세요.")

    published_after = (
        datetime.now(timezone.utc) - timedelta(days=365 * args.years)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    max_videos_label = str(args.max_videos) if args.max_videos else "전량(소진까지)"
    max_comments_label = str(args.max_comments) if args.max_comments else "전량"
    print(f"candidates: {list(candidate_map.keys())}")
    print(f"published_after: {published_after}  "
          f"max_videos: {max_videos_label}  max_comments/video: {max_comments_label}")
    if args.no_transcript:
        print("자막 수집: 스킵")
    if args.no_comments:
        print("댓글 수집: 스킵")

    # ── 수집 루프 ────────────────────────────────────────────────────────────
    all_results: dict[str, dict] = {}

    for cid, search_name in candidate_map.items():
        print(f"\n[{cid}] 검색 중… 쿼리={search_name!r}")

        videos = _search_videos(
            search_name, published_after,
            max_videos=args.max_videos,
            max_pages=args.max_video_pages,
            min_views=args.min_views,
        )
        print(f"  → 영상 {len(videos)}건 (dedup 후)")

        comment_total   = 0
        transcript_ok   = 0
        transcript_fail = 0

        for i, v in enumerate(videos, 1):
            vid = v["video_id"]
            sys.stdout.write(f"  [{i:>3}/{len(videos)}] {vid}  "
                             f"view={v['view_count']:,}  ")

            # 자막
            if not args.no_transcript:
                transcript_text, tr_status = _fetch_transcript(vid)
                v["transcript"]        = transcript_text
                v["transcript_status"] = tr_status
                if transcript_text:
                    transcript_ok += 1
                    sys.stdout.write(f"자막=OK({tr_status})  ")
                else:
                    transcript_fail += 1
                    sys.stdout.write(f"자막={tr_status}  ")
            else:
                v["transcript"]        = None
                v["transcript_status"] = "skipped"

            # 댓글 (0 = 전량)
            if not args.no_comments:
                comments, c_status = _fetch_comments(vid, args.max_comments)
                v["comments"]       = comments
                v["comment_status"] = c_status
                comment_total      += len(comments)
                sys.stdout.write(f"댓글={len(comments)}건({c_status})")
            else:
                v["comments"]       = []
                v["comment_status"] = "skipped"

            sys.stdout.write("\n")
            sys.stdout.flush()
            time.sleep(0.1)

        # ABSA 히트율 + 유효 데이터 비율
        hit_stats, overall = _aspect_hits(videos, codebook)

        all_results[cid] = {
            "search_query":     search_name,
            "video_count":      len(videos),
            "comment_total":    comment_total,
            "transcript_ok":    transcript_ok,
            "transcript_fail":  transcript_fail,
            "overall_validity": overall,
            "aspect_hit_rate":  hit_stats,
            "videos":           videos,
        }
        _print_summary(cid, all_results[cid], codebook)

    # ── 저장 ─────────────────────────────────────────────────────────────────
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    out_dir = root / "data" / "measurement"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"youtube_collection_{slug}_{ts}.json"

    output = {
        "meta": {
            "slug":           slug,
            "run_at":         datetime.now(timezone.utc).isoformat(),
            "years_back":     args.years,
            "max_videos":     args.max_videos,
            "max_comments":   args.max_comments,
            "no_transcript":  args.no_transcript,
            "no_comments":    args.no_comments,
            "candidates":     list(candidate_map.keys()),
        },
        "aspect_codebook": codebook,
        "results":         all_results,
    }
    out_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    total_v = sum(r["video_count"] for r in all_results.values())
    total_c = sum(r["comment_total"] for r in all_results.values())
    valid_v = sum((r.get("overall_validity") or {}).get("valid_video_count", 0)
                  for r in all_results.values())
    valid_c = sum((r.get("overall_validity") or {}).get("valid_comment_count", 0)
                  for r in all_results.values())
    print(f"\n{'='*60}")
    print(f"  전체 요약")
    print(f"  영상  {total_v}건  (유효 {valid_v}건 / {valid_v/total_v:.0%})" if total_v else "")
    print(f"  댓글  {total_c}건  (유효 {valid_c}건 / {valid_c/total_c:.0%})" if total_c else "")
    print(f"  저장: {out_path}")


if __name__ == "__main__":
    main()
