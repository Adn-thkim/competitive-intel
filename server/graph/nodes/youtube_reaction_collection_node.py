"""
server/graph/nodes/youtube_reaction_collection_node.py (v0.13 — reaction_insight 시리즈)
----------------------------------------------------------------------------------------
reaction_insight 의 YouTube 채널 수집 노드 — 확정된 영상의 댓글 + 영상 메타 수집.
설계: docs/design/reaction_insight_node_design.md §3 (RI-D1~D4)

책임 (RI-D1 — 검색하지 않는다)
------------------------------
검색어 설계·검색 실행·영상 선별·검증은 상류(taxonomy hints → url_discovery_youtube_
reactions → cross_reference → feature_mapping → additional_urls_validation →
feature_selection)에서 완료되었다. 본 노드는 analysis_features 에 확정된 video_id 의
**댓글(commentThreads)** 과 **영상 제목·설명(videos.list snippet)** 만 수집한다 (RI-D2).
자막은 보류 (RI-D3 — 공식 API 불가·ToS 리스크).

선별 규칙 (RI-D4)
-----------------
- 영상: feature당 조회수 상위 _VIDEOS_PER_FEATURE(2) → candidate 단위 union(dedup,
  feature_ids 병합) → 초과 시 feature 커버리지 우선 greedy 로 _VIDEOS_PER_CANDIDATE(6) 절단.
- 댓글: 영상당 1 page(100건) 수집 → 필터(10자 미만·이모지 전용·동일 텍스트 중복) →
  좋아요 상위 _COMMENTS_PER_VIDEO(30) → candidate 합산 _COMMENTS_PER_CANDIDATE(150)
  초과 시 전체 좋아요 하위부터 절단(영상당 최소 _COMMENTS_MIN_PER_VIDEO(15) 보장).

quota (실측 예산)
-----------------
candidate 4 × (영상 6 × commentThreads 1u + snippet 일괄 1u) ≈ 28 units/실행.
YouTubeQuotaExceeded → 부분 결과 + errors 누적 (graceful). API key 미설정 → skipped.

read keys
---------
- analysis_features / selected_purposes / selected_feature_ids

write keys
----------
- collected_videos   : [{video_id, url, candidate_id, feature_ids, title, description,
                         channel_title, view_count, like_count, comment_count, published_at}]
- selected_comments  : [{video_id, candidate_id, comment_id, text(원문), like_count,
                         published_at, author_hash}]
- agent_steps / errors (누적 reducer)
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from server.graph.progress_store import set_progress
from server.graph.state import AgentStep, DomainAnalysisState
from server.llm.youtube_client import (
    YouTubeApiUnavailable,
    YouTubeQuotaExceeded,
    youtube_comment_threads,
    youtube_videos_snippet,
    youtube_videos_statistics,
)

logger = logging.getLogger(__name__)

REPORT_TYPE = "reaction_insight"
_ORIGIN     = "youtube_reactions"

# RI-D4 — 선별 상한 (파일럿 측정 후 조정 여지)
_VIDEOS_PER_FEATURE     = 2
_VIDEOS_PER_CANDIDATE   = 6
_COMMENTS_PER_VIDEO     = 30
_COMMENTS_PER_CANDIDATE = 150
_COMMENTS_MIN_PER_VIDEO = 15
_COMMENT_MIN_CHARS      = 10

# video_id 추출 (additional_urls_validation 과 동일 패턴)
_VIDEO_ID_RE = re.compile(
    r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([A-Za-z0-9_-]{11})")

# 이모지·기호 전용 댓글 판정 — 한글·영문·숫자가 하나도 없으면 제외
_HAS_CONTENT_RE = re.compile(r"[0-9A-Za-z가-힣]")

# RI-D10 — 잡담(noise) 댓글 필터 (보수적: 명백한 패턴만. 최종 거름망은 ABSA 가 수행)
_PURE_FILLER_RE = re.compile(r"^[ㅋㅎㅠㅜ;~!?.\s\d]+$")     # ㅋㅋㅋ·ㅠㅠ 등 길이 무관
_NOISE_SHORT_PATTERNS = (                                    # 30자 미만에만 적용
    re.compile(r"^(?:좋아요|굿|최고(?:예요|에요|네요)?|대박|짱|와+우?|헐|미쳤다)[!~.\s]*$"),
    # 영상 자체에 대한 언급 (제품 의견 아님): "영상 잘 봤습니다", "목소리 좋네요" 등
    re.compile(r"^(?:영상|편집|목소리|썸네일|채널|구독|알림\s?설정|인트로)\S*\s*"
               r"(?:너무\s*)?(?:잘\s?봤|잘\s?보고|좋|감사|최고|예쁘|멋지)"),
    re.compile(r"^\d+\s*등[!~.\s]*$"),                       # "1등!"
)


def _is_noise(text: str) -> bool:
    if _PURE_FILLER_RE.fullmatch(text):
        return True
    return len(text) < 30 and any(p.search(text) for p in _NOISE_SHORT_PATTERNS)


def _order_comments(comments: list[dict]) -> list[dict]:
    """RI-D10 — 2단계 정렬: ① 좋아요 > 0 (좋아요 내림차순), ② 좋아요 0 은 최신순.

    좋아요 0 댓글이 다수인 영상에서 '상위 N' 선별의 변별력이 사라지는 문제 보완 —
    변별력 있는 좋아요 구간을 먼저 채우고, 잔여는 의미 필터 통과분을 최신순으로.
    """
    liked = sorted(
        (c for c in comments if int(c.get("like_count", 0) or 0) > 0),
        key=lambda c: (-int(c.get("like_count", 0) or 0), c.get("comment_id", "")))
    zero = sorted(
        (c for c in comments if int(c.get("like_count", 0) or 0) == 0),
        key=lambda c: (c.get("published_at", ""), c.get("comment_id", "")),
        reverse=True)   # 최신순
    return liked + zero


# ─── 영상 선별 (RI-D4 — 결정론) ──────────────────────────────────────────────

def _video_id_of(entry: dict) -> str:
    vid = entry.get("video_id", "")
    if vid:
        return vid
    m = _VIDEO_ID_RE.search(entry.get("url", "") or "")
    return m.group(1) if m else ""


def collect_pool_video_ids(state: dict) -> list[str]:
    """선별 전 후보 풀의 video_id 전체 (RI-D9 — statistics 일괄 보강 대상)."""
    if REPORT_TYPE not in (state.get("selected_purposes") or []):
        return []
    selected_fids = set(state.get("selected_feature_ids") or [])
    ids: set[str] = set()
    for feat in state.get("analysis_features") or []:
        if feat.get("report_type") != REPORT_TYPE \
                or feat.get("feature_id", "") not in selected_fids:
            continue
        for cov in feat.get("candidate_coverage") or []:
            for u in cov.get("existing_urls") or []:
                if u.get("origin") == _ORIGIN:
                    vid = _video_id_of(u)
                    if vid:
                        ids.add(vid)
    return sorted(ids)


def select_videos(state: dict, stats_by_id: dict[str, dict] | None = None
                  ) -> dict[str, list[dict]]:
    """candidate_id → 선별 영상 목록 (순수 함수, 네트워크 비호출).

    필터 3단계(수집 노드 공통 패턴) + RI-D4 선별:
      [1] report_type == reaction_insight ∧ reaction_insight ∈ selected_purposes
      [2] feature_id ∈ selected_feature_ids
      [3] existing_urls 중 origin == youtube_reactions (video_id·view_count 메타)

    stats_by_id (RI-D9): feature_mapping 출력이 view_count 를 carry 하지 않는
    실측 결함 보강 — 제공 시 entry 의 0/누락 값을 statistics 로 대체한다.
    """
    if REPORT_TYPE not in (state.get("selected_purposes") or []):
        return {}
    selected_fids = set(state.get("selected_feature_ids") or [])
    stats_by_id = stats_by_id or {}

    # candidate → video_id → {video 메타, feature_ids, per-feature 채택 여부}
    pool: dict[str, dict[str, dict]] = {}
    for feat in state.get("analysis_features") or []:
        fid = feat.get("feature_id", "")
        if feat.get("report_type") != REPORT_TYPE or fid not in selected_fids:
            continue
        for cov in feat.get("candidate_coverage") or []:
            cid = cov.get("candidate_id", "")
            if not cid:
                continue
            # feature당 조회수 상위 N (RI-D4 1단계 + RI-D9 statistics 보강)
            videos = []
            for u in cov.get("existing_urls") or []:
                if u.get("origin") != _ORIGIN:
                    continue
                vid = _video_id_of(u)
                if not vid:
                    continue
                stats = stats_by_id.get(vid, {})
                videos.append({
                    **u, "video_id": vid,
                    "view_count":    int(u.get("view_count", 0) or 0)
                                     or int(stats.get("view_count", 0) or 0),
                    "like_count":    int(u.get("like_count", 0) or 0)
                                     or int(stats.get("like_count", 0) or 0),
                    "comment_count": int(u.get("comment_count", 0) or 0)
                                     or int(stats.get("comment_count", 0) or 0),
                })
            videos.sort(key=lambda v: (-int(v.get("view_count", 0) or 0), v["video_id"]))
            for v in videos[:_VIDEOS_PER_FEATURE]:
                rec = pool.setdefault(cid, {}).setdefault(v["video_id"], {
                    "video_id":      v["video_id"],
                    "url":           v.get("url", ""),
                    "view_count":    int(v.get("view_count", 0) or 0),
                    "like_count":    int(v.get("like_count", 0) or 0),
                    "comment_count": int(v.get("comment_count", 0) or 0),
                    "published_at":  v.get("published_at", ""),
                    "channel_title": v.get("channel_title", ""),
                    "feature_ids":   set(),
                })
                rec["feature_ids"].add(fid)

    # candidate당 상한 — feature 커버리지 우선 greedy (FE-D5 v3 패턴)
    selected: dict[str, list[dict]] = {}
    for cid, by_vid in pool.items():
        records = sorted(by_vid.values(),
                         key=lambda r: (-r["view_count"], r["video_id"]))
        uncovered = set().union(*(r["feature_ids"] for r in records))
        chosen: list[dict] = []
        remaining = list(records)
        while uncovered and len(chosen) < _VIDEOS_PER_CANDIDATE:
            best = min(remaining, key=lambda r: (
                -len(r["feature_ids"] & uncovered), -r["view_count"], r["video_id"]))
            if not best["feature_ids"] & uncovered:
                break
            chosen.append(best)
            remaining.remove(best)
            uncovered -= best["feature_ids"]
        for r in remaining:
            if len(chosen) >= _VIDEOS_PER_CANDIDATE:
                break
            chosen.append(r)
        selected[cid] = [
            {**r, "feature_ids": sorted(r["feature_ids"])}
            for r in sorted(chosen, key=lambda r: (-r["view_count"], r["video_id"]))
        ]
    return selected


# ─── 댓글 필터·선별 (RI-D4 — 결정론) ─────────────────────────────────────────

def filter_comments(raw_comments: list[dict]) -> list[dict]:
    """짧은 댓글·이모지 전용·잡담(RI-D10)·중복 제거 후 2단계 정렬 상위 _COMMENTS_PER_VIDEO.

    정렬(RI-D10): 좋아요 > 0 구간을 좋아요순으로 먼저 채우고, 잔여는 의미 필터를
    통과한 좋아요 0 댓글을 최신순으로 채운다.
    """
    seen_text: set[str] = set()
    kept: list[dict] = []
    for c in raw_comments:
        text = (c.get("text") or "").strip()
        if len(text) < _COMMENT_MIN_CHARS or not _HAS_CONTENT_RE.search(text):
            continue
        if _is_noise(text):
            continue
        key = text[:200]
        if key in seen_text:
            continue
        seen_text.add(key)
        kept.append(c)
    return _order_comments(kept)[:_COMMENTS_PER_VIDEO]


def cap_candidate_comments(by_video: dict[str, list[dict]]) -> list[dict]:
    """candidate 합산 상한 — 초과분은 전체 좋아요 하위부터 절단, 영상당 최소 보장."""
    total = sum(len(v) for v in by_video.values())
    if total <= _COMMENTS_PER_CANDIDATE:
        return [c for vid in sorted(by_video) for c in by_video[vid]]

    # 1) 영상당 최소 보장분 확정 (각 영상의 좋아요 상위 _COMMENTS_MIN_PER_VIDEO)
    guaranteed: list[dict] = []
    surplus:    list[dict] = []
    for vid in sorted(by_video):
        comments = by_video[vid]
        guaranteed.extend(comments[:_COMMENTS_MIN_PER_VIDEO])
        surplus.extend(comments[_COMMENTS_MIN_PER_VIDEO:])
    # 2) 잔여 슬롯 충원 — RI-D10 2단계 정렬 (좋아요>0 우선, 좋아요 0 은 최신순)
    surplus = _order_comments(surplus)
    room = max(0, _COMMENTS_PER_CANDIDATE - len(guaranteed))
    return guaranteed + surplus[:room]


# ─── 메인 노드 ───────────────────────────────────────────────────────────────

def youtube_reaction_collection_node(
    state: DomainAnalysisState, config: dict | None = None
) -> dict:
    """RI-D1~D4 — 확정 영상의 댓글·메타 수집 (검색 없음)."""
    started_at = datetime.now(timezone.utc).isoformat()
    thread_id = (config or {}).get("configurable", {}).get("thread_id", "")
    if thread_id:
        try:
            set_progress(thread_id, "reaction_collection",
                         detail="YouTube 댓글 수집 (확정 영상)")
        except Exception as exc:  # noqa: BLE001
            logger.debug("set_progress 실패: %s", exc)

    # RI-D9 — 후보 풀 statistics 일괄 보강 (50건당 1 unit). 실패 시 mapping 메타로 진행.
    pool_ids = collect_pool_video_ids(dict(state))
    if not pool_ids:
        return {"agent_steps": [_step("skipped", started_at)]}
    stats_by_id: dict[str, dict] = {}
    try:
        stats_by_id = youtube_videos_statistics(pool_ids)
    except YouTubeApiUnavailable as exc:
        return {"agent_steps": [_step("skipped", started_at, str(exc)[:200])]}
    except YouTubeQuotaExceeded as exc:
        logger.warning("statistics 보강 quota 초과 — mapping 메타로 선별 진행: %s", exc)

    selection = select_videos(dict(state), stats_by_id)
    if not selection:
        return {"agent_steps": [_step("skipped", started_at)]}

    errors: list[dict] = []
    collected_videos: list[dict] = []
    selected_comments: list[dict] = []
    quota_hit = False

    for cid in sorted(selection):
        videos = selection[cid]
        # 영상 제목·설명 일괄 보강 (RI-D2 — candidate당 1 unit)
        snippets: dict[str, dict] = {}
        try:
            snippets = youtube_videos_snippet([v["video_id"] for v in videos])
        except YouTubeQuotaExceeded as exc:
            quota_hit = True
            errors.append(_err(f"snippet quota 초과 (candidate={cid}): {exc}"))
        except YouTubeApiUnavailable as exc:
            return {"agent_steps": [_step("skipped", started_at, str(exc)[:200])]}

        by_video: dict[str, list[dict]] = {}
        for v in videos:
            vid = v["video_id"]
            meta = snippets.get(vid, {})
            collected_videos.append({
                "video_id":      vid,
                "url":           v["url"],
                "candidate_id":  cid,
                "feature_ids":   v["feature_ids"],
                "title":         meta.get("title", ""),
                "description":   meta.get("description", "")[:2000],
                "channel_title": meta.get("channel_title", v.get("channel_title", "")),
                "view_count":    v["view_count"],
                "like_count":    v["like_count"],
                "comment_count": v["comment_count"],
                "published_at":  meta.get("published_at", v.get("published_at", "")),
            })
            if quota_hit:
                continue   # quota 소진 후에는 댓글 호출 생략 (영상 메타만 유지)
            try:
                raw = youtube_comment_threads(vid)
                # 댓글에 영상 연관 부착 (실측 발견 버그 수정 — video_id 누락)
                by_video[vid] = [{**c, "video_id": vid}
                                 for c in filter_comments(raw)]
            except YouTubeQuotaExceeded as exc:
                quota_hit = True
                errors.append(_err(f"댓글 quota 초과 (video={vid}): {exc}"))
            except YouTubeApiUnavailable as exc:
                errors.append(_err(f"댓글 수집 실패 (video={vid}): {str(exc)[:150]}"))

        for c in cap_candidate_comments(by_video):
            selected_comments.append({**c, "candidate_id": cid})

    status = "completed" if not quota_hit else "completed"
    step = _step(status, started_at)
    if errors:
        step["error_message"] = f"{len(errors)}건 부분 실패" + (" (quota)" if quota_hit else "")
    logger.info(
        "youtube_reaction_collection: %d candidates · 영상 %d · 댓글 %d (부분실패 %d)",
        len(selection), len(collected_videos), len(selected_comments), len(errors))

    out: dict = {
        "collected_videos":  collected_videos,
        "selected_comments": selected_comments,
        "agent_steps":       [step],
    }
    if errors:
        out["errors"] = errors
    return out


def _step(status: str, started_at: str, error_message: str = "") -> AgentStep:
    step: AgentStep = {
        "step_name":   "YoutubeReactionCollection",
        "status":      status,
        "started_at":  started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    if error_message:
        step["error_message"] = error_message
    return step


def _err(message: str) -> dict:
    return {"node": "youtube_reaction_collection_node", "error": message,
            "timestamp": datetime.now(timezone.utc).isoformat()}
