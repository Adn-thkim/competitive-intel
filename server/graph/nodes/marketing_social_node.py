"""
server/graph/nodes/marketing_social_node.py (v1.0 — 구현)
----------------------------------------------------------
`marketing_social` 리포트 노드 — 운영 채널 수집 2종 집계 → 마케팅·소셜 분석 envelope.
설계: docs/design/marketing_social_node_design.md §5 (MS-D4~D7·D10~D12)

책임 분리 (CM-D1 사상)
----------------------
- 코드 (결정론): 블로그 채널 dedup(MS-D11) · PESO/커버리지 매트릭스 · 월별 게시 빈도
  2계열(전체/상품 관련 — MS-D10) · engagement(분모 2종 병기 — MS-D4) · 4-tuple ·
  상품명 코드 선판정 · 루브릭 채점(MS-D6) · LLM 출력 환각 가드.
- LLM (ClaudeCodeCliAnalyzer, 노드당 1회 — MS-D7): 채널별 키워드 · 카피 톤 ·
  인플루언서 협업 흔적 · 애매 건 상품 관련성 판정 · 서술.

read keys
---------
- youtube_channel_metadata (수집 ①) · blog_rss_posts (수집 ②)
- pr_releases 는 **읽지 않음** — MS-D12 (2026-06-07 사용자: URL 수집 단계부터
  의도 불일치 → 1차 제외, presence-only 만 표기)
- owned_channel_urls_by_candidate (presence 매트릭스용)
- domain_taxonomy.report_config["marketing_social"] · own_product · competitor_candidates

write keys
----------
- report_outputs["marketing_social"] (merge reducer — 자기 키만 반환)

루브릭 코드 채점 (MS-D6 — PR 제외 보정)
----------------------------------------
  2점: 측정 채널 유형(youtube·blog) < 2종
  3점: 측정 2종 + PESO 분류 + engagement 분모 명기 (LLM degrade 시 상한)
  4점: 3점 + 채널 × 키워드 cross-tab (LLM 산출)
  5점: 4점 + 동일 6개월 윈도우 정렬(MS-D5) + 자사 공백 식별 ≥ 1건
"""

from __future__ import annotations

import hashlib
import json
import logging
import statistics
from collections import defaultdict
from datetime import datetime, timezone

from server.config import AGENTS_DIR
from server.graph.agent_cache import (
    load_agent_output,
    make_cache_context,
    store_agent_output,
)
from server.graph.state import DomainAnalysisState
from server.graph.nodes._report_node_common import (
    build_report_envelope,
    get_report_entry,
    is_report_active,
    make_completed_step,
    make_error_result,
    make_skip_result,
)
from server.graph.nodes.feature_url_mapper_node import _load_json, _load_text

logger = logging.getLogger(__name__)

REPORT_TYPE    = "marketing_social"
RUBRIC_VERSION = "report_taxonomy.md §2-3 (2026-05)"

_LLM_TIMEOUT_SEC = 300
_WINDOW_MONTHS   = 12         # MS-D5 — 동일 기간 윈도우 (v1.0.4: 6→12, 사용자 요청)
_LLM_EXCERPT     = 150        # LLM 입력 발췌 상한 (저장은 300자)
_DEDUP_OVERLAP   = 0.5        # MS-D11 — 제목 중복도 임계
_GENERIC_TOKENS  = {"카드", "체크카드", "신용카드"}

# presence 매트릭스 platform 순서 (feature_selection._PLATFORM_ORDER 정합)
_PLATFORMS = ("instagram", "x", "youtube_official",
              "blog_naver", "blog_tistory", "blog_self_hosted", "press_release")
_BLOG_PLATFORMS = ("blog_naver", "blog_tistory", "blog_self_hosted")


# ─── 코드 파트: 순수 함수 (결정론) ───────────────────────────────────────────

def product_tokens(name: str, brand: str) -> list[str]:
    """상품명 → 관련성 선판정 토큰 (소문자). 브랜드·일반어 토큰 제외.

    브랜드=상품 단일 회사(예: 트래블월렛)는 비브랜드 토큰이 없으므로 브랜드
    토큰을 유지한다.
    """
    brand_l = (brand or "").lower()
    toks = [t.lower() for t in (name or "").split() if t.lower() not in _GENERIC_TOKENS]
    non_brand = [t for t in toks if t not in brand_l]
    return non_brand or toks


def dedup_blog_feeds(feeds: list[dict]) -> list[dict]:
    """MS-D11 — 같은 candidate 의 ok 피드 중 제목 중복도 ≥ 임계인 쌍을 병합.

    실측 근거(2026-06-07): shinhancard-blog.tistory.com 과 www.shinhancardblog.com 은
    동일 블로그의 커스텀 도메인 — 미병합 시 게시 빈도 2배 왜곡.
    병합 결과는 첫 피드 기준 + `merged_platforms` 에 흡수된 platform 기록.
    """
    out: list[dict] = []
    by_cid: dict[str, list[dict]] = defaultdict(list)
    for f in feeds:
        # MS-D16 — measured_empty(피드 도달, 글 0)도 측정 채널로 포함 (PESO measured)
        if f.get("fetch_status") in ("ok", "measured_empty"):
            by_cid[f["candidate_id"]].append(f)
    for cid in sorted(by_cid):
        kept: list[dict] = []
        for f in by_cid[cid]:
            titles = {p["title"] for p in f.get("posts", [])}
            merged = False
            for k in kept:
                kt = {p["title"] for p in k.get("posts", [])}
                if titles and kt and \
                        len(titles & kt) / min(len(titles), len(kt)) >= _DEDUP_OVERLAP:
                    k.setdefault("merged_platforms", [k["platform"]]).append(f["platform"])
                    merged = True
                    break
            if not merged:
                kept.append(dict(f))
        out.extend(kept)
    return out


def build_channels(meta: dict, ok_feeds: list[dict]) -> dict[str, dict]:
    """channel_key → {candidate_id, channel_type, platforms, audience_size, items}.

    items: [{id, title, excerpt, published_at(, view/like/comment)}]
    channel_key = "{candidate_id}/{youtube|platform}" — LLM 입출력·빈도 표 공용 키.
    """
    channels: dict[str, dict] = {}
    for cid in sorted(meta):
        rec = meta[cid]
        channels[f"{cid}/youtube"] = {
            "candidate_id":  cid,
            "channel_type":  "youtube",
            "platforms":     ["youtube_official"],
            "audience_size": rec.get("subscriber_count", 0),
            "items": [
                {
                    "id":           v["video_id"],
                    "title":        v.get("title", ""),
                    "excerpt":      (v.get("description") or "")[:_LLM_EXCERPT],
                    "published_at": v.get("published_at", ""),
                    "view":         v.get("view_count", 0),
                    "like":         v.get("like_count", 0),
                    "comment":      v.get("comment_count", 0),
                }
                for v in rec.get("recent_videos", [])
            ],
        }
    for f in ok_feeds:
        key = f"{f['candidate_id']}/{f['platform']}"
        channels[key] = {
            "candidate_id":  f["candidate_id"],
            "channel_type":  "blog",
            "platforms":     f.get("merged_platforms") or [f["platform"]],
            "audience_size": None,   # 블로그 — 분모 부재 (§1-3)
            "items": [
                {
                    "id":           p.get("link") or f"{key}#{i}",
                    "title":        p.get("title", ""),
                    "excerpt":      (p.get("summary") or "")[:_LLM_EXCERPT],
                    "published_at": p.get("published_at", ""),
                }
                for i, p in enumerate(f.get("posts", []))
            ],
        }
    return channels


def prejudge_related(channels: dict, tokens_by_cid: dict[str, list[str]]) -> set[str]:
    """MS-D10 하이브리드 1단계 — 상품명 토큰이 제목/발췌에 직접 포함된 item id."""
    related: set[str] = set()
    for ch in channels.values():
        toks = tokens_by_cid.get(ch["candidate_id"], [])
        for it in ch["items"]:
            text = f"{it.get('title', '')} {it.get('excerpt', '')}".lower()
            if any(t in text for t in toks):
                related.add(it["id"])
    return related


def month_window(n: int = _WINDOW_MONTHS, now: datetime | None = None) -> list[str]:
    """최근 n개월 YYYY-MM 라벨 (오름차순) — MS-D5."""
    now = now or datetime.now(timezone.utc)
    y, m, out = now.year, now.month, []
    for _ in range(n):
        out.append(f"{y}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return sorted(out)


def build_frequency(channels: dict, related_ids: set[str],
                    window: list[str]) -> dict[str, dict]:
    """채널별 월간 게시 수 2계열 (MS-D10) — 동일 윈도우 정렬 (MS-D5)."""
    out: dict[str, dict] = {}
    for key in sorted(channels):
        monthly = {m: {"total": 0, "product_related": 0} for m in window}
        total = rel = 0
        for it in channels[key]["items"]:
            month = (it.get("published_at") or "")[:7]
            if month not in monthly:
                continue
            monthly[month]["total"] += 1
            total += 1
            if it["id"] in related_ids:
                monthly[month]["product_related"] += 1
                rel += 1
        out[key] = {
            "monthly":        monthly,
            "window_total":   total,
            "related_total":  rel,
            "related_ratio":  round(rel / total, 3) if total else 0.0,
        }
    return out


def build_engagement(meta: dict) -> dict[str, dict]:
    """YouTube 한정 engagement — 분모 2종 병기 (MS-D4).

    v1.0.5 — 비율만으로는 해석이 단편적이라는 지적(2026-06-07)에 따라 원수치
    (조회·좋아요·댓글 합계, 구독자)를 함께 산출. 합계 모집단 = 수집된 최근 영상 전체.
    """
    out: dict[str, dict] = {}
    for cid in sorted(meta):
        rec = meta[cid]
        subs = rec.get("subscriber_count", 0)
        videos = rec.get("recent_videos", [])
        per_view, per_sub = [], []
        for v in videos:
            inter = v.get("like_count", 0) + v.get("comment_count", 0)
            if v.get("view_count", 0) > 0:
                per_view.append(inter / v["view_count"])
            if subs > 0:
                per_sub.append(inter / subs)
        out[cid] = {
            "per_view_median":       round(statistics.median(per_view), 5) if per_view else None,
            "per_subscriber_median": round(statistics.median(per_sub), 6) if per_sub else None,
            "total_views":           sum(v.get("view_count", 0) for v in videos),
            "total_likes":           sum(v.get("like_count", 0) for v in videos),
            "total_comments":        sum(v.get("comment_count", 0) for v in videos),
            "subscriber_count":      subs,
            "sample_size":           len(videos),
            "denominators":          ["view_count", "subscriber_count"],
        }
    return out


def build_peso_matrix(owned_urls: dict, meta: dict,
                      ok_feeds: list[dict]) -> dict[str, dict[str, str]]:
    """candidate × platform — measured | measured_empty | presence_only | none.

    MS-D16 — 블로그 피드가 도달했으나 게시물 0건이면 measured_empty (인스타·X 의
    presence_only 와 구분: 후자는 수집 시도 자체 안 함). instagram(MS-D3a) ·
    x(MS-D3b) · press_release(MS-D12)는 URL 존재 시 presence_only.
    """
    # 블로그 platform → 측정 상태 (글 ≥1: measured, 글 0: measured_empty)
    blog_state: dict[tuple[str, str], str] = {}
    for f in ok_feeds:
        st = "measured" if f.get("posts") else "measured_empty"
        for p in f.get("merged_platforms") or [f["platform"]]:
            blog_state[(f["candidate_id"], p)] = st

    matrix: dict[str, dict[str, str]] = {}
    for cid in sorted(owned_urls):
        present = {u.get("platform") for u in owned_urls[cid] or []}
        row = {}
        for p in _PLATFORMS:
            if p == "youtube_official" and cid in meta:
                row[p] = "measured"
            elif p in _BLOG_PLATFORMS and (cid, p) in blog_state:
                row[p] = blog_state[(cid, p)]
            elif p in present:
                row[p] = "presence_only"
            else:
                row[p] = "none"
        matrix[cid] = row
    return matrix


def build_coverage_gaps(matrix: dict, own_id: str) -> list[dict]:
    """자사 미점유(none) platform 중 경쟁사가 보유(≠none)한 공백 (5점 요건)."""
    own_row = matrix.get(own_id) or {}
    gaps = []
    for p in _PLATFORMS:
        if own_row.get(p) != "none":
            continue
        holders = [cid for cid, row in matrix.items()
                   if cid != own_id and row.get(p) != "none"]
        if holders:
            gaps.append({"platform": p, "held_by": sorted(holders)})
    return gaps


def compute_rubric(channels: dict, crosstab_present: bool,
                   gaps: list[dict]) -> tuple[int, str]:
    """MS-D6 — 루브릭 코드 결정론 채점 (모듈 docstring 규칙)."""
    measured_types = {ch["channel_type"] for ch in channels.values()}
    if len(measured_types) < 2:
        got = "·".join(sorted(measured_types)) or "없음"
        return 2, f"측정 채널 유형 부족 ({got}) — youtube·blog 2종 미만"
    if not crosstab_present:
        return 3, ("측정 2종 + PESO + engagement 분모 2종 병기. "
                   "키워드 cross-tab 부재(LLM degrade) — 3점 상한")
    if not gaps:
        return 4, "cross-tab 충족. 5점 미달 — 자사 채널 공백 0건 (식별 결과 없음)"
    return 5, ("측정 2종 + PESO + engagement 분모 병기 + 키워드 cross-tab "
               f"+ 동일 {_WINDOW_MONTHS}개월 윈도우 + 자사 공백 {len(gaps)}건 식별")


def sanitize_llm_output(llm_out: dict, channels: dict) -> tuple[dict, int]:
    """LLM 출력 환각 가드 — 비실존 channel_key·item id·candidate_id 제거."""
    valid_ids = {it["id"] for ch in channels.values() for it in ch["items"]}
    valid_keys = set(channels)
    valid_cids = {ch["candidate_id"] for ch in channels.values()}
    dropped = 0

    kept_kw = []
    for e in llm_out.get("channel_keywords", []):
        if e.get("channel_key") not in valid_keys:
            dropped += 1
            continue
        for kw in e.get("keywords", []):
            before = len(kw.get("example_ids", []))
            kw["example_ids"] = [i for i in kw.get("example_ids", []) if i in valid_ids]
            dropped += before - len(kw["example_ids"])
        kept_kw.append(e)
    llm_out["channel_keywords"] = kept_kw

    before = len(llm_out.get("product_related_ids", []))
    llm_out["product_related_ids"] = [
        i for i in llm_out.get("product_related_ids", []) if i in valid_ids]
    dropped += before - len(llm_out["product_related_ids"])

    kept_inf = []
    for e in llm_out.get("influencer_signals", []):
        if e.get("candidate_id") not in valid_cids:
            dropped += 1
            continue
        before = len(e.get("evidence_ids", []))
        e["evidence_ids"] = [i for i in e.get("evidence_ids", []) if i in valid_ids]
        dropped += before - len(e["evidence_ids"])
        kept_inf.append(e)
    llm_out["influencer_signals"] = kept_inf

    for field, key in (("copy_tones", "candidate_id"), ("channel_insights", "channel_key")):
        valid = valid_cids if key == "candidate_id" else valid_keys
        before = len(llm_out.get(field, []))
        llm_out[field] = [e for e in llm_out.get(field, []) if e.get(key) in valid]
        dropped += before - len(llm_out[field])
    return llm_out, dropped


def _make_analyzer(system_prompt: str):
    from server.llm.claude_cli_analyzer import ClaudeCodeCliAnalyzer  # 지연 import
    return ClaudeCodeCliAnalyzer(system_prompt=system_prompt, timeout=_LLM_TIMEOUT_SEC)


# ─── 메인 노드 ───────────────────────────────────────────────────────────────

def marketing_social_node(
    state: DomainAnalysisState, config: dict | None = None, analyzer=None
) -> dict:
    """운영 채널 집계 → marketing_social envelope (코드 집계 + LLM 판정·서술)."""
    started_at = datetime.now(timezone.utc).isoformat()

    if not is_report_active(state, REPORT_TYPE):
        return make_skip_result(REPORT_TYPE, started_at)
    if REPORT_TYPE not in (state.get("selected_purposes") or []):
        return make_skip_result(REPORT_TYPE, started_at)

    meta: dict = state.get("youtube_channel_metadata") or {}
    feeds: list = state.get("blog_rss_posts") or []
    owned_urls: dict = state.get("owned_channel_urls_by_candidate") or {}
    if not meta and not feeds:
        return make_error_result(
            REPORT_TYPE, started_at,
            "수집 데이터 없음 — youtube_channel_metadata·blog_rss_posts 모두 빈 값.")

    entry = get_report_entry(state, REPORT_TYPE) or {}
    own = state.get("own_product") or {}
    own_id = own.get("product_id", "")

    # 상품명 토큰 (MS-D10 선판정)
    tokens_by_cid = {
        own_id: product_tokens(own.get("name") or own.get("product_name", ""),
                               own.get("brand", "")),
    }
    for c in state.get("competitor_candidates") or []:
        cid = c.get("candidate_id", "")
        if cid:
            tokens_by_cid[cid] = product_tokens(
                c.get("product_name", ""), c.get("brand", ""))

    # ── 코드 파트: 집계 (결정론) ────────────────────────────────────────────
    ok_feeds   = dedup_blog_feeds(feeds)                       # MS-D11
    channels   = build_channels(meta, ok_feeds)
    prejudged  = prejudge_related(channels, tokens_by_cid)     # MS-D10 1단계
    window     = month_window()
    engagement = build_engagement(meta)
    peso       = build_peso_matrix(owned_urls, meta, ok_feeds)
    gaps       = build_coverage_gaps(peso, own_id)

    # ── LLM 파트 (MS-D7 보정, v1.0.3): candidate별 N회 + 종합 1회 ────────────
    # 2026-06-07 실측: 5채널 ~210건 단일 호출이 CLI 300s timeout 초과 → ABSA 전례
    # (reaction_analysis candidate별 순차)대로 분할. candidate 단위 부분 degrade 지원.
    system_prompt    = _load_text(AGENTS_DIR / REPORT_TYPE / "system_prompt_kr.md")
    output_schema    = _load_json(AGENTS_DIR / REPORT_TYPE / "output.schema.json")
    synthesis_schema = _load_json(AGENTS_DIR / REPORT_TYPE / "synthesis.schema.json")
    if system_prompt is None or output_schema is None or synthesis_schema is None:
        return make_error_result(
            REPORT_TYPE, started_at, f"agents/{REPORT_TYPE}/ prompt·schema 로드 실패.")

    if analyzer is None:
        analyzer = _make_analyzer(system_prompt)
    context = make_cache_context(
        agent_id=REPORT_TYPE, model=getattr(analyzer, "model", "claude_cli"),
        system_prompt=system_prompt, output_schema=output_schema)

    warnings: list[str] = []
    failed_cids: list[str] = []
    crosstab, copy_tones, influencer, insights = [], [], [], []
    llm_related: set[str] = set()

    cids = sorted({ch["candidate_id"] for ch in channels.values()})
    for cid in cids:
        cid_channels = {k: ch for k, ch in channels.items() if ch["candidate_id"] == cid}
        cid_item_ids = {it["id"] for ch in cid_channels.values() for it in ch["items"]}
        payload = {
            "mode":                  "per_candidate",
            "own_candidate_id":      own_id,
            "candidate_id":          cid,
            "product_tokens":        tokens_by_cid.get(cid, []),
            "prejudged_related_ids": sorted(prejudged & cid_item_ids),
            "channels": [
                {
                    "channel_key":  key,
                    "candidate_id": cid,
                    "platforms":    ch["platforms"],
                    "items": [
                        {"id": it["id"], "title": it["title"], "excerpt": it["excerpt"]}
                        for it in ch["items"]
                    ],
                }
                for key, ch in sorted(cid_channels.items())
            ],
        }
        cache_input = {
            "report_type": REPORT_TYPE, "candidate_id": cid,
            "payload_sha256": hashlib.sha256(
                json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
        }
        out_c = load_agent_output(
            agent_id=REPORT_TYPE, cache_input=cache_input, context=context,
            output_schema=output_schema, logger=logger)
        if out_c is None:
            prompt = ("다음 단일 candidate 의 운영 채널 게시물 목록에 대해 system_prompt 의 "
                      "per_candidate 산출 규칙을 적용하라 (수치 재계산 금지).\n\n```json\n"
                      + json.dumps(payload, ensure_ascii=False) + "\n```")
            try:
                out_c = analyzer.call_with_schema(prompt, output_schema)
                store_agent_output(agent_id=REPORT_TYPE, cache_input=cache_input,
                                   context=context, output=out_c, logger=logger)
            except Exception as exc:  # noqa: BLE001 — candidate 단위 부분 degrade
                failed_cids.append(cid)
                warnings.append(f"LLM 판정 실패 ({cid}) — 해당 candidate 집계 전용: "
                                f"{type(exc).__name__}: {str(exc)[:120]}")
                logger.error("marketing_social_node: LLM 실패 (%s): %s", cid, exc)
                continue
        out_c, dropped = sanitize_llm_output(out_c, cid_channels)
        if dropped:
            warnings.append(f"환각 가드 ({cid}) — LLM 출력 {dropped}건 제거")
        llm_related |= set(out_c["product_related_ids"])
        crosstab    += out_c["channel_keywords"]
        copy_tones  += out_c["copy_tones"]
        influencer  += out_c["influencer_signals"]
        insights    += out_c["channel_insights"]
        warnings    += out_c.get("warnings", [])

    related_ids = prejudged | llm_related
    degraded_error = ""
    if failed_cids and len(failed_cids) == len(cids):
        degraded_error = "LLM 판정·서술 전체 실패 — 집계 전용 degrade"
        warnings.append("상품 관련 빈도는 코드 선판정 하한치 (LLM 문맥 판정 누락)")

    # 종합 단계 — 후보별 산출 + 코드 집계로 종합 1회 (작은 payload)
    # v1.0.4 — headline + key_points 구조 (가독성). degrade 시 None.
    overall_summary = {"headline": "(degraded — LLM 종합 생략, 코드 집계만 제공)",
                       "key_points": []}
    if not degraded_error:
        frequency_pre = build_frequency(channels, related_ids, window)
        syn_payload = {
            "mode":             "synthesis",
            "own_candidate_id": own_id,
            "copy_tones":       copy_tones,
            "channel_insights": insights,
            "coverage_gaps":    gaps,
            "engagement_table": engagement,
            "frequency_summary": {
                k: {"window_total": v["window_total"], "related_total": v["related_total"]}
                for k, v in frequency_pre.items()
            },
        }
        syn_cache = {
            "report_type": REPORT_TYPE, "phase": "synthesis",
            "payload_sha256": hashlib.sha256(
                json.dumps(syn_payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
        }
        syn_out = load_agent_output(
            agent_id=REPORT_TYPE, cache_input=syn_cache, context=context,
            output_schema=synthesis_schema, logger=logger)
        if syn_out is None:
            prompt = ("다음 후보별 산출과 코드 집계를 바탕으로 system_prompt 의 synthesis "
                      "산출 규칙을 적용해 자사 관점 종합 서술을 작성하라.\n\n```json\n"
                      + json.dumps(syn_payload, ensure_ascii=False) + "\n```")
            try:
                syn_out = analyzer.call_with_schema(prompt, synthesis_schema)
                store_agent_output(agent_id=REPORT_TYPE, cache_input=syn_cache,
                                   context=context, output=syn_out, logger=logger)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"종합 서술 실패 — overall_summary 생략: "
                                f"{type(exc).__name__}: {str(exc)[:120]}")
                syn_out = None
        if syn_out is not None:
            overall_summary = {"headline": syn_out["headline"],
                               "key_points": syn_out["key_points"]}
            warnings += syn_out.get("warnings", [])

    frequency = build_frequency(channels, related_ids, window)
    score, score_rationale = compute_rubric(channels, bool(crosstab), gaps)
    warnings.append(f"score_rationale: {score_rationale}")
    warnings.append("press_release 채널은 1차 측정 제외 (MS-D12) — presence-only 표기")

    # 4-tuple channel_matrix (Rubric §2-3)
    channel_matrix = [
        {
            "channel_key":       key,
            "candidate_id":      ch["candidate_id"],
            "platforms":         ch["platforms"],
            "posting_frequency": frequency[key]["window_total"],
            "product_related":   frequency[key]["related_total"],
            "audience_size":     ch["audience_size"],
            "top_keywords": next(
                (e["keywords"] for e in crosstab if e.get("channel_key") == key), []),
        }
        for key, ch in sorted(channels.items())
    ]

    content = {
        "title":              entry.get("label") or "마케팅·소셜 분석",
        "measurement_window": {"months": window, "policy": "MS-D5 동일 기간 정렬"},
        "peso_matrix":        peso,
        "channel_matrix":     channel_matrix,
        "frequency_table":    frequency,
        "engagement_table":   engagement,
        "keyword_crosstab":   crosstab,
        "coverage_gaps":      gaps,
        "copy_tones":         copy_tones,
        "influencer_signals": influencer,
        "channel_insights":   insights,
        "overall_summary":    overall_summary,       # {headline, key_points} (v1.0.4)
        "related_judgement": {                       # MS-D10 추적 메타
            "prejudged": len(prejudged),
            "llm_added": len(related_ids) - len(prejudged),
            "tokens_by_cid": tokens_by_cid,
        },
    }
    envelope = build_report_envelope(
        report_type=REPORT_TYPE,
        rubric_version=RUBRIC_VERSION,
        categories=entry.get("categories") or [],
        content=content,
        evaluation_score=score,
        source_references=[
            {"url": ch["items"][0]["id"], "channel_key": key}
            for key, ch in sorted(channels.items())
            if ch["channel_type"] == "blog" and ch["items"]
        ],
        warnings=warnings,
    )

    out: dict = {
        "report_outputs": {REPORT_TYPE: envelope},
        "agent_steps":    [make_completed_step(REPORT_TYPE, started_at)],
    }
    if degraded_error:
        out["errors"] = [{"node": f"{REPORT_TYPE}_node", "error": degraded_error,
                          "timestamp": datetime.now(timezone.utc).isoformat()}]
    return out
