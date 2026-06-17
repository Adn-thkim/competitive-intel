"""
server/graph/nodes/additional_urls_validation_node.py (v0.10.25 정식화)
----------------------------------------------------------------------
feature_url_mapper 흐름의 최종 단계 — 5종 *_raw_features 통합 + source-type 별
additional_urls 검증 분기 + analysis_features 산출.

v0.10.25 정식화 (D23 적용 + 5 source 별 검증 분기)
--------------------------------------------------
v0.10.27.1 의 임시 `_union_raw_features` (feature_url_mapper_node.py) 폐기 + 본
모듈 내부에 정식 헬퍼 신설. v0.10.27 의 임시 호환 어댑터 (state["raw_features"]
fallback) 도 폐기.

핵심 변경 (turn-67)
-------------------
1. **D23 정식 `_union_raw_features`** — feature_url_mapper_node → 본 모듈 이전
   + `source_origin` 메타 부착 (additional_urls 의 검증 분기 키).
2. **5 source 별 검증 분기**:
   - `_validate_youtube_video`     : videos.list API 호출 (1 unit/URL) +
                                     view_count·like·comment 메타 보강
   - `_validate_owned_channel`     : HEAD/GET + is_brand_match 검증
   - `_validate_official_subpage`  : HEAD/GET (기존)
   - `_validate_blog_community`    : HEAD/GET + 발행일 36개월 재검증 (D37)
   - `_validate_macro_subpage`     : HEAD/GET + 화이트리스트 매칭
3. **임시 fallback 폐기** — state["raw_features"] 옛 키 사용 제거 (v0.10.27 정식화
   완료로 옛 흐름 무관).

입력 state 키
-------------
- official_raw_features          : feature_mapping_official_node 산출
- blog_community_raw_features    : feature_mapping_blog_community_node 산출
- youtube_reactions_raw_features : feature_mapping_youtube_reactions_node 산출
- owned_channel_raw_features     : feature_mapping_owned_channels_node 산출
- macro_raw_features             : feature_mapping_macro_node 산출
- own_product / competitor_candidates : owned_channels brand 매칭용

출력 state 키
-------------
- analysis_features : list[AnalysisFeature] — feature_selection_node 가 사용
- agent_steps       : 누적 reducer

graceful 종료
-------------
- 5종 *_raw_features 모두 빈 결과 → 빈 analysis_features + status="completed"
- YouTube quota 초과 → HEAD/GET fallback (validated 결과 보수적 채택)
"""

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import urlparse

from server.graph.progress_store import set_progress
from server.graph.state import AnalysisFeature, AgentStep, DomainAnalysisState
from server.graph.nodes.feature_url_mapper_node import (
    _check_url_status,
    _is_recent_enough,
    _normalize_feature,
    _error,
)

logger = logging.getLogger(__name__)


# v0.10.25 — 검증 정책 상수
_BRAND_MATCH_THRESHOLD     = 0.7    # is_brand_match Levenshtein 임계
_BLOG_MAX_MONTHS           = 36     # blog_community additional_urls 발행일 검증
_VALIDATION_MAX_WORKERS    = 8      # 병렬 검증 worker 수

# v0.10.25 — macro 화이트리스트 (v0.10.22 의 4 분류 17 도메인 그대로 참조)
_MACRO_WHITELIST: tuple[str, ...] = (
    "kosis.kr", "ecos.bok.or.kr", "index.go.kr",
    "fsc.go.kr", "mosf.go.kr", "fss.or.kr", "bok.or.kr",
    "kdi.re.kr", "kiet.re.kr", "nia.or.kr", "kotra.or.kr",
    "yna.co.kr", "hankyung.com", "mk.co.kr", "mt.co.kr",
    "etnews.com", "dt.co.kr",
)

# YouTube watch URL → video_id 추출 정규식
_YOUTUBE_VIDEO_ID_RE = re.compile(
    r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([A-Za-z0-9_-]{11})"
)


# ─── D23 정식 _union_raw_features ────────────────────────────────────────────

def _union_raw_features(state: dict) -> list[dict]:
    """v0.10.25 D23 정식 — 5종 *_raw_features 를 candidate_coverage union 으로 통합.

    v0.10.27.1 hotfix 의 candidate_coverage union 로직 + source_origin 메타 부착.
    동일 (report_type, feature_id) 가 여러 source 산출 시:

    1. 첫 등장 source 의 feature 메타 (report_type · feature_name · description ·
       priority) 유지 (priority: official > blog_community > youtube_reactions >
       owned_channels > macro)
    2. candidate_coverage union — 동일 candidate_id 의 existing_urls / additional_urls
       URL union (dedup)
    3. coverage 갱신 — sufficient > partial > not_found
    4. **`source_origin` 메타 부착** (D23 정식화 핵심) — 각 existing_url 의 origin
       을 그대로 candidate_id 별 추적하여 additional_urls 검증 분기 시 활용
    """
    # youtube_reactions 제거 — Phase 3 폐기 (youtube_collection_redesign.md)
    priority = ("official", "blog_community", "owned_channels", "macro")
    coverage_rank = {"sufficient": 3, "partial": 2, "not_found": 1}
    by_key: dict[tuple[str, str], dict] = {}

    for src in priority:
        key_name = (
            f"{src}_raw_features" if src != "owned_channels"
            else "owned_channel_raw_features"
        )
        for feat in state.get(key_name) or []:
            rt  = feat.get("report_type", "")
            fid = feat.get("feature_id", "")
            if not rt or not fid:
                continue

            existing = by_key.get((rt, fid))
            if existing is None:
                # 첫 등장 — deep copy + 모든 candidate_coverage 의 additional_urls 에
                # source_origin 메타 부착 (현재 source 의 첫 등장이므로 그대로 사용)
                cands = []
                for c in (feat.get("candidate_coverage") or []):
                    cand_copy = dict(c)
                    # additional_urls 에 source_origin 부착
                    cand_copy["additional_urls"] = [
                        {**au, "source_origin": _SOURCE_ORIGIN_BY_SRC[src]}
                        for au in (c.get("additional_urls") or [])
                    ]
                    cands.append(cand_copy)
                by_key[(rt, fid)] = {**feat, "candidate_coverage": cands}
                continue

            # 이미 있는 feature — candidate_coverage union
            existing_covs: dict[str, dict] = {
                c.get("candidate_id", ""): c for c in existing["candidate_coverage"]
            }
            for new_cov in feat.get("candidate_coverage") or []:
                cid = new_cov.get("candidate_id", "")
                if not cid:
                    continue

                if cid not in existing_covs:
                    # 새 candidate — additional_urls 에 source_origin 부착하여 append
                    new_cov_copy = dict(new_cov)
                    new_cov_copy["additional_urls"] = [
                        {**au, "source_origin": _SOURCE_ORIGIN_BY_SRC[src]}
                        for au in (new_cov.get("additional_urls") or [])
                    ]
                    existing["candidate_coverage"].append(new_cov_copy)
                    existing_covs[cid] = new_cov_copy
                else:
                    # 동일 candidate — URL union (dedup) + source_origin 부착
                    base = existing_covs[cid]
                    base_existing  = base.setdefault("existing_urls", [])
                    base_seen_e    = {u.get("url", "") for u in base_existing}
                    for u in new_cov.get("existing_urls") or []:
                        url = u.get("url", "")
                        if url and url not in base_seen_e:
                            base_existing.append(u)
                            base_seen_e.add(url)

                    base_additional = base.setdefault("additional_urls", [])
                    base_seen_a     = {u.get("url", "") for u in base_additional}
                    for au in new_cov.get("additional_urls") or []:
                        url = au.get("url", "")
                        if url and url not in base_seen_a:
                            base_additional.append({**au, "source_origin": _SOURCE_ORIGIN_BY_SRC[src]})
                            base_seen_a.add(url)

                    # coverage 갱신
                    new_rank  = coverage_rank.get(new_cov.get("coverage", ""), 0)
                    base_rank = coverage_rank.get(base.get("coverage", ""), 0)
                    if new_rank > base_rank:
                        base["coverage"] = new_cov["coverage"]

    return list(by_key.values())


# source-type → additional_urls.source_origin 값 매핑 (v0.10.25 신설)
_SOURCE_ORIGIN_BY_SRC: dict[str, str] = {
    "official":       "official_subpage",
    "blog_community": "blog_community",
    "owned_channels": "owned_channel_search",
    "macro":          "macro_search",
}


# ─── source-type 별 검증 분기 (v0.10.25) ─────────────────────────────────────

def _extract_youtube_id(url: str) -> str:
    """YouTube watch URL → video_id 추출 (11자 base64-url)."""
    if not url:
        return ""
    m = _YOUTUBE_VIDEO_ID_RE.search(url)
    return m.group(1) if m else ""


def _extract_host(url: str) -> str:
    """URL → host (lowercase + www. strip)."""
    if not url:
        return ""
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:  # noqa: BLE001
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def _fuzzy_match(a: str, b: str, threshold: float = _BRAND_MATCH_THRESHOLD) -> bool:
    """간단한 substring + 길이 기반 fuzzy match (외부 라이브러리 없이).

    a 의 정규화된 문자열이 b 의 substring 또는 그 역인 경우 매치.
    threshold 는 향후 Levenshtein 도입 시 확장 여지로 보존.
    """
    if not a or not b:
        return False
    a_l = a.lower().strip().replace(" ", "")
    b_l = b.lower().strip().replace(" ", "")
    if not a_l or not b_l:
        return False
    if a_l in b_l or b_l in a_l:
        return True
    # 길이 70% 이상이 공통 prefix 인 경우
    common = 0
    for ca, cb in zip(a_l, b_l):
        if ca == cb:
            common += 1
        else:
            break
    return common / max(len(a_l), len(b_l)) >= threshold


def _validate_default(au: dict) -> None:
    """기본 HEAD/GET 검증 (옛 동작)."""
    status = _check_url_status(au.get("url", ""))
    au["validated"]   = status is not None and 200 <= status < 400
    au["http_status"] = status


def _validate_official_subpage(au: dict) -> None:
    """official_subpage — HEAD/GET (기본과 동일)."""
    _validate_default(au)


def _validate_blog_community(au: dict) -> None:
    """blog_community — HEAD/GET + 발행일 36개월 재검증 (D37).

    LLM 이 제안한 additional_urls 에 published_at 메타가 있으면 v0.10.24 의
    `_is_recent_enough` 로 재검증. 미통과 시 validated=False.
    """
    _validate_default(au)
    published_at = au.get("published_at", "")
    if published_at and not _is_recent_enough(published_at, max_months=_BLOG_MAX_MONTHS):
        au["validated"] = False
        au["recency_check"] = False
    else:
        au["recency_check"] = True


def _validate_youtube_video(au: dict) -> None:
    """youtube_reactions — videos.list API 재호출로 영상 메타 보강.

    YouTube watch 페이지는 영상 삭제·비공개 시에도 200 OK 반환하므로 HEAD/GET 만
    으로는 검증 불가. videos.list (1 unit) 호출하여 실 메타 확인 + view_count·
    like·comment_count 채움.

    quota 초과 시 HEAD/GET fallback (validated 보수적 채택).
    """
    # local import to avoid hard dependency on youtube module (테스트 환경 호환)
    try:
        from server.llm.youtube_client import (
            youtube_videos_list,
            YouTubeQuotaExceeded,
            YouTubeApiUnavailable,
        )
    except ImportError:
        _validate_default(au)
        return

    video_id = _extract_youtube_id(au.get("url", ""))
    if not video_id:
        au["validated"]   = False
        au["http_status"] = None
        return
    try:
        meta = youtube_videos_list(video_id)
    except (YouTubeQuotaExceeded, YouTubeApiUnavailable):
        # quota 초과 또는 API 사용 불가 → HEAD/GET fallback
        _validate_default(au)
        return

    if meta is None:
        # 영상 삭제·private
        au["validated"]   = False
        au["http_status"] = 404
        return

    au["validated"]     = True
    au["http_status"]   = 200
    au["view_count"]    = meta.get("view_count")
    au["like_count"]    = meta.get("like_count")
    au["comment_count"] = meta.get("comment_count")


def _validate_owned_channel(au: dict, candidate_brand_keywords: list[str]) -> None:
    """owned_channels — HEAD/GET + is_brand_match 검증.

    Instagram·X·블로그 URL 은 누구나 만들 수 있으므로 HEAD/GET 만으로는 공식 계정
    여부 검증 불가. URL 의 host + path 첫 segment 가 candidate 의 brand·
    product_name 과 매칭하는지 fuzzy check.
    """
    _validate_default(au)
    url = au.get("url", "")
    host = _extract_host(url)
    # path 첫 segment (handle) 추출
    try:
        path = (urlparse(url).path or "").lstrip("/")
    except Exception:  # noqa: BLE001
        path = ""
    handle = path.split("/", 1)[0] if path else ""
    handle = handle.lstrip("@")

    is_match = any(
        _fuzzy_match(handle, kw) or _fuzzy_match(host.split(".")[0], kw)
        for kw in candidate_brand_keywords if kw
    )
    au["is_brand_match"] = is_match


def _validate_macro_subpage(au: dict) -> None:
    """macro — HEAD/GET + 화이트리스트 매칭.

    macro additional_urls 가 화이트리스트 (Tier 1·2 정적 + 뉴스) 외 도메인이면
    validated=False (LLM 이 도메인 정책 위반 제안 차단).
    """
    _validate_default(au)
    host = _extract_host(au.get("url", ""))
    is_match = any(
        host == d or host.endswith("." + d) for d in _MACRO_WHITELIST
    )
    au["whitelist_match"] = is_match
    if not is_match:
        au["validated"] = False


def _get_brand_keywords_by_candidate(state: dict) -> dict[str, list[str]]:
    """candidate_id → brand·product_name 키워드 list 매핑 산출."""
    out: dict[str, list[str]] = {}
    own = state.get("own_product") or {}
    own_id = own.get("product_id") or "own"
    out[own_id] = [
        s for s in (
            own.get("name", ""), own.get("product_name", ""), own.get("brand", "")
        ) if s
    ]
    for cand in state.get("competitor_candidates") or []:
        cid = cand.get("candidate_id", "")
        if cid:
            out[cid] = [
                s for s in (
                    cand.get("product_name", ""), cand.get("brand", "")
                ) if s
            ]
    return out


def _validate_additional_urls_v2(
    raw_features: list[dict],
    state: dict,
) -> list[dict]:
    """v0.10.25 정식 — source-type 별 검증 분기 적용.

    옛 `_validate_additional_urls` (feature_url_mapper_node 의 단일 분기) 를 폐기
    하고, 본 함수가 source_origin 메타 기반으로 5 분기 적용.
    """
    brand_keywords = _get_brand_keywords_by_candidate(state)

    # 1) (fi, ci, ui, source_origin, url, candidate_id) 작업 목록 생성
    tasks: list[tuple[int, int, int, str, str, str]] = []
    for fi, feat in enumerate(raw_features):
        for ci, cov in enumerate(feat.get("candidate_coverage", []) or []):
            cand_id = cov.get("candidate_id", "")
            for ui, au in enumerate(cov.get("additional_urls", []) or []):
                url = (au.get("url") or "").strip()
                if not url:
                    continue
                source_origin = au.get("source_origin", "")
                tasks.append((fi, ci, ui, source_origin, url, cand_id))

    if not tasks:
        return [_normalize_feature(f) for f in raw_features]

    # 2) 병렬 검증 — source_origin 별 분기
    def _validate_one(task):
        fi, ci, ui, source_origin, url, cand_id = task
        au = raw_features[fi]["candidate_coverage"][ci]["additional_urls"][ui]
        if source_origin == "youtube_reactions":
            _validate_youtube_video(au)
        elif source_origin == "owned_channel_search":
            _validate_owned_channel(au, brand_keywords.get(cand_id, []))
        elif source_origin == "official_subpage":
            _validate_official_subpage(au)
        elif source_origin == "blog_community":
            _validate_blog_community(au)
        elif source_origin == "macro_search":
            _validate_macro_subpage(au)
        else:
            _validate_default(au)

    with ThreadPoolExecutor(max_workers=_VALIDATION_MAX_WORKERS) as pool:
        futures = [pool.submit(_validate_one, t) for t in tasks]
        for f in as_completed(futures):
            try:
                f.result()
            except Exception as exc:  # noqa: BLE001
                logger.debug("validate_additional_urls 예외: %s", exc)

    return [_normalize_feature(f) for f in raw_features]


# ─── 메인 노드 ───────────────────────────────────────────────────────────────

def additional_urls_validation_node(
    state: DomainAnalysisState, config: dict | None = None,
) -> dict:
    """v0.10.25 정식 — 5종 *_raw_features 통합 + source-type 별 검증.

    Returns
    -------
    dict
        {analysis_features, agent_steps} 또는 {errors, agent_steps}
    """
    started_at = datetime.now(timezone.utc).isoformat()
    thread_id  = (config or {}).get("configurable", {}).get("thread_id", "")

    print(
        f"🌐 [additional_urls_validation_node] ENTRY at {started_at} thread_id={thread_id!r}",
        flush=True,
    )

    if thread_id:
        try:
            set_progress(thread_id, "feature_mapping_validate",
                         detail="추가 URL source-type 별 검증")
        except Exception as exc:  # noqa: BLE001
            logger.debug("set_progress(validate) 실패: %s", exc)

    # v0.10.25 정식 — 5종 *_raw_features 통합 (D23 candidate_coverage union)
    raw_features: list[dict] = _union_raw_features(state)

    if not raw_features:
        # 모두 빈 결과 — 빈 analysis_features 반환 (feature_selection 이 빈 결과 처리)
        logger.warning(
            "additional_urls_validation_node: 5종 *_raw_features 모두 빈 결과",
        )
        analysis_features: list = []
    else:
        # v0.10.25 정식 — source-type 별 검증 분기
        analysis_features = _validate_additional_urls_v2(raw_features, state)

    logger.info(
        "additional_urls_validation_node: 완료 (analysis_features=%d)",
        len(analysis_features),
    )

    finished_at = datetime.now(timezone.utc).isoformat()

    if thread_id:
        try:
            set_progress(
                thread_id, "feature_mapping_done",
                detail=f"{len(analysis_features)}개 분석 항목 매핑 완료",
                current=len(analysis_features),
                total=len(analysis_features),
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("set_progress(done) 실패: %s", exc)

    step: AgentStep = {
        "step_name":   "AdditionalUrlsValidation",
        "status":      "completed",
        "started_at":  started_at,
        "finished_at": finished_at,
    }
    return {
        "analysis_features": analysis_features,
        "agent_steps":       [step],
    }
