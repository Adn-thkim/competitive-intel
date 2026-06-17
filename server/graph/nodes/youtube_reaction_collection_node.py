"""
server/graph/nodes/youtube_reaction_collection_node.py
------------------------------------------------------
reaction_insight YouTube 댓글 수집 노드 (youtube_collection_redesign.md Phase 3·4).

역할 변경 (Phase 3·4)
----------------------
- **Phase 3**: analysis_features / feature_mapping 의존 제거. video_candidate_index 직접 소비.
- **Phase 4**: 영상·댓글 수집 상한 폐기. 전량 수집 후 pre-filter v2 적용.

수집 흐름
---------
1. `video_candidate_index` ({video_id: [candidate_ids]}) 을 순회.
2. 영상당 1회 `youtube_comment_threads` 호출 (dedup — quota 낭비 방지).
3. 수집 댓글에 pre-filter v2 적용:
   - 1단계: aspect 키워드 매칭 (ASPECT_KW)
   - 2단계: 순수 의문문 제거 (≤100자 + 모든 문장이 의문형)
4. multi-tagging: 통과 댓글을 video_candidate_index의 모든 candidate_id에 복제.
5. `collected_videos`: 영상당 1건 (candidate_id 없음).

read keys
---------
- video_candidate_index          : {video_id: [candidate_ids]}
- youtube_reactions_urls_by_candidate : 영상 메타 조회용

write keys
----------
- collected_videos   : [{video_id, url, title, description, channel_title,
                         view_count, like_count, comment_count, published_at}]
                       (candidate_id 없음 — multi-tagging 은 selected_comments 로)
- selected_comments  : [{video_id, candidate_id, comment_id, text, like_count,
                         published_at, author_hash}]
- agent_steps / errors (누적 reducer)
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from server.graph.progress_store import set_progress
from server.graph.state import AgentStep, DomainAnalysisState
from server.llm.youtube_client import (
    YouTubeApiUnavailable,
    YouTubeQuotaExceeded,
    youtube_comment_threads,
    youtube_videos_snippet,
)

logger = logging.getLogger(__name__)

_COMMENT_MIN_CHARS = 10

# 이모지·기호 전용 댓글 판정 — 한글·영문·숫자가 하나도 없으면 제외
_HAS_CONTENT_RE = re.compile(r"[0-9A-Za-z가-힣]")

# 잡담(noise) 댓글 필터 — 보수적, 명백한 패턴만
_PURE_FILLER_RE = re.compile(r"^[ㅋㅎㅠㅜ;~!?.\s\d]+$")
_NOISE_SHORT_PATTERNS = (
    re.compile(r"^(?:좋아요|굿|최고(?:예요|에요|네요)?|대박|짱|와+우?|헐|미쳤다)[!~.\s]*$"),
    re.compile(r"^(?:영상|편집|목소리|썸네일|채널|구독|알림\s?설정|인트로)\S*\s*"
               r"(?:너무\s*)?(?:잘\s?봤|잘\s?보고|좋|감사|최고|예쁘|멋지)"),
    re.compile(r"^\d+\s*등[!~.\s]*$"),
)

# ── pre-filter v2: aspect 키워드 사전 ─────────────────────────────────────────
# validate_youtube_prefilter.py 검증 기준 (유지율 40%, 오제외 0건)
ASPECT_KW: dict[str, list[str]] = {
    "overseas_payment_convenience": [
        "해외결제", "해외 결제", "결제 안됨", "결제오류", "결제 오류", "해외에서",
        "애플페이", "구글페이", "삼성페이", "GLN", "gln", "QR결제", "비자", "마스터",
        "페이", "결제", "overseas",
    ],
    "exchange_rate_fairness": [
        "환율", "환전 수수료", "수수료", "환전",
        "달러", "엔화", "유로", "위안", "원화", "외화",
        "실시간 환율", "우대환율", "환율 우대",
    ],
    "atm_withdrawal_ux": [
        "ATM", "atm", "현금인출", "현금 인출", "출금",
        "현금", "인출", "CD기", "자동화기기",
    ],
    "app_ux_quality": [
        "앱", "어플", "앱이", "앱에서", "인터페이스",
        "토스", "앱 오류", "앱 버그", "앱 업데이트", "UI", "UX",
        "알림", "설정", "로그인",
    ],
    "emergency_card_lock": [
        "잠금", "분실", "카드 정지", "카드잠금", "분실신고",
        "도난", "카드 해지", "일시정지", "해외 분실",
    ],
    "pricing_perception": [
        "수수료", "비용", "요금", "이용료", "연회비",
        "무료", "유료", "과금", "청구", "가격", "요금제",
    ],
    "travel_benefit_value": [
        "혜택", "마일리지", "마일", "포인트", "라운지",
        "캐시백", "적립", "할인", "무료 제공", "특전",
        "여행자 보험", "보험",
    ],
    "customer_support": [
        "고객센터", "상담", "CS", "문의",
        "콜센터", "전화", "답변", "응대", "처리",
    ],
    "fx_reload_convenience": [
        "충전", "환전하기", "재충전", "잔액",
        "계좌", "이체", "입금", "송금", "환전소",
    ],
}
_ALL_KW: list[str] = [kw for kws in ASPECT_KW.values() for kw in kws]

# ── pre-filter v2: 순수 의문문 패턴 ──────────────────────────────────────────
_PURE_QUESTION_MAX_LEN = 100
_QUESTION_END = re.compile(
    r'나요\s*\??$|인가요\s*\??$|될까요\s*\??$|건가요\s*\??$|'
    r'하나요\s*\??$|있나요\s*\??$|있을까요\s*\??$|[까]\s*요?\s*\??$|'
    r'ㄴ가요\s*\??$|\?\s*$',
)
_SENT_SPLIT = re.compile(r'[.!。\n]+')


# ── 헬퍼 함수 ─────────────────────────────────────────────────────────────────

def _is_noise(text: str) -> bool:
    if _PURE_FILLER_RE.fullmatch(text):
        return True
    return len(text) < 30 and any(p.search(text) for p in _NOISE_SHORT_PATTERNS)


def _has_aspect_keyword(text: str) -> bool:
    """aspect 키워드 중 하나라도 포함되면 True (1단계 필터)."""
    return any(kw in text for kw in _ALL_KW)


def _is_pure_question(text: str) -> bool:
    """모든 문장이 의문형인 단문이면 True (2단계 필터).

    조건: 전체 길이 ≤ 100자 + 의문형 종결어미 존재 + 모든 문장이 의문형.
    """
    text = text.strip()
    if not text or len(text) > _PURE_QUESTION_MAX_LEN:
        return False
    if not _QUESTION_END.search(text):
        return False
    sentences = [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]
    return bool(sentences) and all(_QUESTION_END.search(s) for s in sentences)


def _filter_basic(raw: list[dict]) -> list[dict]:
    """기본 노이즈 필터: 짧은 댓글·이모지 전용·잡담·중복 제거."""
    seen: set[str] = set()
    kept: list[dict] = []
    for c in raw:
        text = (c.get("text") or "").strip()
        if len(text) < _COMMENT_MIN_CHARS or not _HAS_CONTENT_RE.search(text):
            continue
        if _is_noise(text):
            continue
        key = text[:200]
        if key in seen:
            continue
        seen.add(key)
        kept.append(c)
    return kept


def _prefilter_v2(comments: list[dict]) -> list[dict]:
    """pre-filter v2: 기본 노이즈 제거 → aspect 키워드 → 순수 의문문 제거."""
    result: list[dict] = []
    for c in _filter_basic(comments):
        text = (c.get("text") or "").strip()
        if not _has_aspect_keyword(text):
            continue
        if _is_pure_question(text):
            continue
        result.append(c)
    return result


# ── 메인 노드 ─────────────────────────────────────────────────────────────────

def youtube_reaction_collection_node(
    state: DomainAnalysisState, config: dict | None = None
) -> dict:
    """Phase 3·4 — video_candidate_index 소비, 전량 수집, pre-filter v2, multi-tagging."""
    started_at = datetime.now(timezone.utc).isoformat()
    thread_id = (config or {}).get("configurable", {}).get("thread_id", "")
    if thread_id:
        try:
            set_progress(thread_id, "reaction_collection",
                         detail="YouTube 댓글 수집 (video_candidate_index)")
        except Exception as exc:  # noqa: BLE001
            logger.debug("set_progress 실패: %s", exc)

    video_candidate_index: dict[str, list[str]] = state.get("video_candidate_index") or {}
    if not video_candidate_index:
        logger.info("youtube_reaction_collection_node: video_candidate_index 없음 — skip")
        return {"agent_steps": [_step("skipped", started_at)]}

    # 영상 메타 조회용 lookup (youtube_reactions_urls_by_candidate 활용)
    reactions: dict[str, list[dict]] = state.get("youtube_reactions_urls_by_candidate") or {}
    video_meta: dict[str, dict] = {}
    for cand_items in reactions.values():
        for v in cand_items:
            vid = v.get("video_id", "")
            if vid and vid not in video_meta:
                video_meta[vid] = v

    # 영상 제목·설명 일괄 보강 (videos.list snippet, 50건당 1 unit)
    all_video_ids = list(video_candidate_index.keys())
    snippets: dict[str, dict] = {}
    try:
        snippets = youtube_videos_snippet(all_video_ids)
    except YouTubeApiUnavailable as exc:
        return {"agent_steps": [_step("skipped", started_at, str(exc)[:200])]}
    except YouTubeQuotaExceeded as exc:
        logger.warning("snippet quota 초과 — 메타 없이 진행: %s", exc)

    errors: list[dict[str, Any]] = []
    collected_videos: list[dict] = []
    selected_comments: list[dict] = []
    quota_hit = False

    for vid, cids in sorted(video_candidate_index.items()):
        meta = video_meta.get(vid, {})
        snip = snippets.get(vid, {})

        collected_videos.append({
            "video_id":      vid,
            "url":           meta.get("url", f"https://www.youtube.com/watch?v={vid}"),
            "title":         snip.get("title") or meta.get("title", ""),
            "description":   (snip.get("description") or meta.get("meta_description", ""))[:2000],
            "channel_title": snip.get("channel_title") or meta.get("channel_title", ""),
            "view_count":    int(meta.get("view_count", 0) or 0),
            "like_count":    int(meta.get("like_count", 0) or 0),
            "comment_count": int(meta.get("comment_count", 0) or 0),
            "published_at":  snip.get("published_at") or meta.get("published_at", ""),
        })

        if quota_hit:
            continue   # quota 소진 후에는 댓글 수집 생략 (영상 메타만 유지)

        try:
            raw = youtube_comment_threads(vid)
        except YouTubeQuotaExceeded as exc:
            quota_hit = True
            errors.append(_err(f"댓글 quota 초과 (video={vid}): {exc}"))
            continue
        except YouTubeApiUnavailable as exc:
            errors.append(_err(f"댓글 수집 실패 (video={vid}): {str(exc)[:150]}"))
            continue

        # pre-filter v2 적용
        filtered = _prefilter_v2(raw)

        # multi-tagging: 통과 댓글을 모든 candidate_id에 복제
        for cid in cids:
            for c in filtered:
                selected_comments.append({
                    **c,
                    "video_id":    vid,
                    "candidate_id": cid,
                })

    status = "completed"
    step = _step(status, started_at)
    if errors:
        step["error_message"] = f"{len(errors)}건 부분 실패" + (" (quota)" if quota_hit else "")

    logger.info(
        "youtube_reaction_collection: 영상 %d · 댓글 %d (multi-tag 후) · 부분실패 %d",
        len(collected_videos), len(selected_comments), len(errors),
    )

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
