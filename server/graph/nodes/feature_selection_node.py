"""
server/graph/nodes/feature_selection_node.py (v0.10)
----------------------------------------------------
Feature 선택 Human-in-the-loop 중단점 노드 (interrupt #4).

역할
----
feature_url_mapper_node 이후 실행된다.
analysis_features를 **report_type 단위**(D4 enum 7종, v0.10)로 그룹핑해 프런트엔드에
전달하고 그래프를 일시 중단한다. 사용자가 분석할 리포트(report)와 feature(항목)를
선택하면 재개(resume)되어 selected_purposes · selected_feature_ids를 state에 저장한다.

※ v0.10 키 이름 호환성
----------------------
- 출력 키 `selected_purposes`의 이름은 그대로 유지하되 **의미는 report_type 목록**으로 변경.
  다른 노드(downstream)가 이 키 이름에 의존할 수 있어 점진적 마이그레이션을 위함이며,
  state.py docstring에 의미 갱신이 명시되어 있다.

interrupt 값 구조 (v0.10.18a — source_flow 별 UI 차별화)
--------------------------------------------------------
{
  "type": "feature_selection",
  "reports": [
    {
      "report_type":          "comparison_matrix",   // D4 enum 7종 중 하나
      "report_label":         "비교 매트릭스",
      "source_flow":          "A" | "B" | "A+B",     // v0.10.18a 신설
      "intro_text":           "이 리포트는 ...",      // v0.10.18a 신설 (정적 dict)
      "url_coverage_visible": true | false,          // v0.10.18a 신설 (B-only 시 false)
      "features": [
        // 흐름 A · A+B (url_coverage_visible=true): coverage_summary + coverage_details 보유
        {
          "feature_id":       "feat_transaction_fee_rate",
          "feature_name":     "거래 수수료율",
          "description":      "...",
          "priority":         "high",
          "coverage_summary": {"sufficient": 3, "partial": 2, "not_found": 1},
          "coverage_details": [...]
        },
        // B-only (url_coverage_visible=false): coverage_summary / coverage_details = null
        {
          "feature_id":       "axis_cost_efficiency_score",
          "feature_name":     "비용 효율성 축 점수",
          "description":      "",
          "priority":         "medium",
          "coverage_summary": null,
          "coverage_details": null
        }
      ]
    },
    ...
  ]
}

resume 값 구조 (프런트엔드 → Express → 여기)
--------------------------------------------
{
  "selected_purposes":    ["comparison_matrix", "battlecard"],   // v0.10: report_type 목록
  "selected_feature_ids": ["feat_transaction_fee_rate", ...]
}

검증 규칙
---------
- selected_feature_ids 최소 1개 이상
- selected_feature_ids의 모든 항목이 analysis_features에 존재해야 함
- selected_purposes는 selected_feature_ids에서 자동 역산하여 검증
"""

import logging
from datetime import datetime, timezone

from langgraph.types import interrupt

from server.graph.state import DomainAnalysisState, AgentStep

logger = logging.getLogger(__name__)

# v0.10 D4 enum 7종 (정렬 순서 = 카드 표시 기본 순서)
REPORT_TYPES = (
    "comparison_matrix",
    "reaction_insight",
    "marketing_social",
    "battlecard",
    "positioning_map",
    "market_context_swot",
    "executive_summary",
)

# v0.10.18a — D27 (b) 옵션 채택. report_type 별 안내 문구 정적 dict.
# 도메인 무관·결정론적. 도메인 다양성 확장 시 옵션 (a) schema 의 intro_text 필드로 이전 가능.
# 변경 시 prompt_version 관리 무관 (server 측 정적 문자열).
_REPORT_INTRO_TEXTS: dict[str, str] = {
    "marketing_social":
        "이 리포트는 자사·경쟁사 운영 SNS(Instagram·X·YouTube 공식 채널)·블로그·"
        "보도자료의 공식 채널 URL 을 식별한 뒤, 아래 채널 활성도·게시물 빈도·콘텐츠 "
        "키워드·광고 정보 등의 feature 값을 수집·분석하여 작성합니다. "
        "(※ feature 값 수집은 v1.0 §6-6a 도입 후 자동 진행)",
    "battlecard":
        "이 리포트는 아래 선택된 feature 데이터를 수집한 뒤, "
        "비교 매트릭스·고객 반응 인사이트·마케팅·소셜 분석 결과를 종합하여 작성합니다.",
    "market_context_swot":
        "이 리포트는 아래 선택된 매크로 feature 데이터를 정부 통계·산업 보고서·"
        "트레이드 미디어에서 수집한 뒤, 비교 매트릭스·고객 반응 인사이트·"
        "마케팅·소셜 분석 결과와 종합하여 작성합니다.",
    "positioning_map":
        "이 리포트는 아래 표시된 feature 를 기반으로 비교 매트릭스 결과로부터 "
        "자동 도출됩니다. URL 수집은 발생하지 않습니다.",
    "executive_summary":
        "이 리포트는 아래 표시된 feature 를 기반으로 6개 분석 리포트 결과를 통합하여 "
        "자동 도출됩니다. URL 수집은 발생하지 않습니다.",
}

# 2026-11 — CM·RI 안내 박스 구조화 (한 줄 요약 + 단계 카드). 담당 에이전트명은 노출하지 않는다.
# phase: "done" = 이 화면 이전에 완료된 단계 / "after" = 선택 완료 후 실행되는 단계.
# 정적 화면 문구 — 어떤 캐시 키에도 들어가지 않는다.
_REPORT_INTRO_STEPS: dict[str, dict] = {
    "comparison_matrix": {
        "summary": "공식 사이트 정보로 상품별 조건을 같은 기준에서 비교합니다.",
        "steps": [
            {"title": "공식 페이지 탐색", "phase": "done",
             "detail": "자사·경쟁사 공식 사이트에서 약관·수수료·한도·혜택 등 하위 페이지를 찾아 feature 별로 연결"},
            {"title": "feature 값 추출", "phase": "after",
             "detail": "아래에서 선택한 feature 의 값·근거·출처를 페이지 본문에서 추출"},
            {"title": "비교표·요약 작성", "phase": "after",
             "detail": "상품 × feature 비교표와 상품별 요약·공통점/차이점·조건부 강점/약점"},
        ],
    },
    "reaction_insight": {
        "summary": "YouTube·커뮤니티의 실사용 반응으로 자사·경쟁사의 강점과 약점을 비교합니다.",
        "steps": [
            {"title": "영상·게시글 탐색", "phase": "done",
             "detail": "상품명으로 최근 2년 YouTube 영상과 커뮤니티 게시글을 탐색 (공식 채널 영상 제외)"},
            {"title": "반응 수집·정제", "phase": "after",
             "detail": "댓글·게시글을 수집하고 상품 리뷰와 무관한 영상·스레드를 제거"},
            {"title": "의견·질문 분석", "phase": "after",
             "detail": "상품별 긍정·부정 의견과 고객 질문을 추출해 반응 축으로 묶음 (축 검토 화면에서 확인)"},
            {"title": "인사이트 작성", "phase": "after",
             "detail": "자사·경쟁사 반응 히트맵, 자사 열위·기회 축, 질문 의도 분석"},
        ],
    },
}


def feature_selection_node(state: DomainAnalysisState) -> dict:
    """
    Feature 선택을 위한 네 번째 Human-in-the-loop 중단점 노드 (v0.10).

    Parameters
    ----------
    state : DomainAnalysisState
        필수 키: analysis_features (각 항목에 report_type 필드)
        선택 키: domain_taxonomy (report_config의 label 조회용)

    Returns
    -------
    dict
        사용자 선택 후: selected_purposes (report_type 목록) · selected_feature_ids · agent_steps
    """
    started_at = datetime.now(timezone.utc).isoformat()

    analysis_features: list[dict] = state.get("analysis_features") or []
    domain_taxonomy: dict          = state.get("domain_taxonomy") or {}

    if not analysis_features:
        return _error(started_at,
                      "analysis_features가 state에 없습니다. "
                      "feature_url_mapper_node가 먼저 실행되어야 합니다.")

    # ── v0.10: report_config에서 라벨 맵 구성 ────────────────────────────────
    report_config: dict = domain_taxonomy.get("report_config", {})
    report_label_map: dict[str, str] = {
        rt: cfg.get("label", rt)
        for rt, cfg in report_config.items()
    }

    # ── analysis_features를 report_type 단위로 그룹핑 ────────────────────────
    # D4 enum 순서 + active=true 우선
    active_in_order = [
        rt for rt in REPORT_TYPES
        if report_config.get(rt, {}).get("active") is True
    ]
    fallback_order = list(dict.fromkeys(
        f.get("report_type", "unknown") for f in analysis_features
    ))
    report_order: list[str] = active_in_order or fallback_order

    grouped: dict[str, list[dict]] = {rt: [] for rt in report_order}
    for feature in analysis_features:
        rt = feature.get("report_type", "unknown")
        if rt not in grouped:
            grouped[rt] = []
        grouped[rt].append(feature)

    # ── interrupt 값 조립 (v0.10.18a — source_flow 별 UI 차별화 + B-only 카드 결합) ──
    # v0.10.28b — marketing_social 카드 재정의 (D45 a):
    #   feature 값(SNS 게시물 빈도 등) 은 v1.0 §6-6a 책임이므로 features 영역을
    #   B-only 형식으로 렌더. (2026-11 — owned_channels_card 는 reaction_insight 의
    #   owned_youtube_card 로 대체, marketing_social 은 화면·그래프에서 비활성)
    reports_payload: list[dict] = []
    for rt in report_order:
        entry = report_config.get(rt, {}) or {}
        source_flow = entry.get("source_flow", "A")    # v0.10.18 후방 호환 기본값
        is_b_only   = source_flow == "B"

        # v0.10.28b — marketing_social 은 features 를 B-only 형식 (URL 영역 숨김)
        is_marketing_social_b_view = (rt == "marketing_social")

        if is_b_only or is_marketing_social_b_view:
            # B-only 또는 marketing_social — domain_taxonomy 의 features 만 (URL 영역 없음)
            feature_items = _build_feature_items_from_taxonomy(entry)
            if not feature_items and not is_marketing_social_b_view:
                continue
            # marketing_social 은 owned_channels 카드도 함께 렌더링 — features 0건이라도 카드 유지
        else:
            # 흐름 A · A+B — analysis_features 의 결과 그대로 사용
            features_in_report = grouped.get(rt, [])
            if not features_in_report:
                # active=true 인데 features 0건이면 카드 자체 미렌더 (옛 동작 유지)
                continue
            feature_items = _build_feature_items_from_analysis(features_in_report)

        report_item: dict = {
            "report_type":          rt,
            "report_label":         report_label_map.get(rt, rt),
            "source_flow":          source_flow,                          # v0.10.18a 신설
            "intro_text":           _REPORT_INTRO_TEXTS.get(rt, ""),      # v0.10.18a 신설
            "intro_steps":          _REPORT_INTRO_STEPS.get(rt),          # 2026-11 — CM·RI 구조화 안내 (없으면 None)
            "url_coverage_visible": not (is_b_only or is_marketing_social_b_view),
            "features":             feature_items,
        }

        # 2026-11 — comparison_matrix 표의 상품 열 (자사 → 경쟁사 ID 순, 표시 이름)
        if rt == "comparison_matrix":
            report_item["candidates"] = [
                {"candidate_id": cid, "label": label, "candidate_type": ctype}
                for cid, label, ctype in _ordered_candidates(state)
            ]

        # reaction_insight — 수집 대상 집계(url_preview) + 제외한 공식 YouTube 채널(owned_youtube_card).
        # 2026-11 — aspect_codebook 화면 표시 제거(taxonomy 는 유지 — 키워드 필터가 소비).
        if rt == "reaction_insight":
            report_item["owned_youtube_card"] = _build_owned_youtube_card(state)
            report_item["url_preview"]        = _build_reaction_preview(state)

        reports_payload.append(report_item)

    total_feature_count = sum(len(r["features"]) for r in reports_payload)
    logger.info(
        "feature_selection_node: interrupt() 호출 (reports=%d개, features=%d개)",
        len(reports_payload), total_feature_count,
    )

    resume_value: dict = interrupt({
        "type":    "feature_selection",
        "reports": reports_payload,
    })

    # ── 재개 후 처리 ─────────────────────────────────────────────────────────
    selected_feature_ids: list[str] = resume_value.get("selected_feature_ids", [])
    selected_purposes_raw: list[str] = resume_value.get("selected_purposes", [])

    # reaction_insight 는 features 없이 aspect_codebook 만 보유하므로,
    # 해당 리포트만 선택된 경우 selected_feature_ids 가 비어 있을 수 있다.
    # selected_purposes_raw 에 값이 있으면 정상 제출로 간주한다.
    if not selected_feature_ids and not selected_purposes_raw:
        return _error(started_at, "selected_feature_ids가 비어 있습니다. "
                                  "최소 1개 이상의 feature를 선택해야 합니다.")

    # 유효 feature_id 집합과 feature→report 매핑은 interrupt payload(reports_payload)
    # 기준으로 구성한다. B-only(positioning_map·executive_summary)·marketing_social
    # 카드의 feature 는 analysis_features 가 아닌 domain_taxonomy 에서 파생되므로
    # analysis_features 만으로 검증하면 정상 선택값이 오탐된다 (프런트는 설계상 이들을
    # selected_feature_ids 에 자동 포함 — feature_url_mapper_redesign.md §5-9).
    feature_report_map: dict[str, str] = {
        f.get("feature_id", ""): report_item["report_type"]
        for report_item in reports_payload
        for f in report_item.get("features", [])
        if f.get("feature_id")
    }
    valid_feature_ids: set[str] = set(feature_report_map)
    invalid = [fid for fid in selected_feature_ids if fid not in valid_feature_ids]
    if invalid:
        return _error(started_at,
                      f"유효하지 않은 feature_id가 포함되어 있습니다: {invalid}")
    derived_reports: list[str] = list(dict.fromkeys(
        feature_report_map[fid]
        for fid in selected_feature_ids
        if fid in feature_report_map
    ))

    # 프런트엔드 전송값 우선, 없으면 역산값
    selected_purposes: list[str] = selected_purposes_raw or derived_reports

    finished_at = datetime.now(timezone.utc).isoformat()
    logger.info(
        "feature_selection_node: 완료 (reports=%d개: %s, features=%d개)",
        len(selected_purposes), selected_purposes, len(selected_feature_ids),
    )

    step: AgentStep = {
        "step_name":   "FeatureSelection",
        "status":      "completed",
        "started_at":  started_at,
        "finished_at": finished_at,
    }

    return {
        "selected_purposes":    selected_purposes,    # v0.10: report_type 목록
        "selected_feature_ids": selected_feature_ids,
        "agent_steps":          [step],
    }


# ── 내부 헬퍼 ─────────────────────────────────────────────────────────────────

# v0.10.28a (D47 c) — candidate_id → 한국어 라벨 매핑.
# 기본은 candidate_id 그대로 사용. macro 만 사용자 친화적 라벨로 변환.
# 자사·경쟁사 candidate_id (own_*/comp_*) 는 client 가 own_product / competitor_candidates
# 의 product_name 으로 별도 변환할 수 있도록 raw 값 유지.
_CANDIDATE_LABEL_OVERRIDES: dict[str, str] = {
    "macro": "산업·시장 데이터",
}


def _candidate_label(candidate_id: str) -> str:
    """v0.10.28a (D47 c) — candidate_id 를 사용자 친화적 라벨로 변환.

    overrides 미매칭 시 candidate_id 그대로 반환 — client 가 추가 라벨 매핑 가능.
    """
    return _CANDIDATE_LABEL_OVERRIDES.get(candidate_id, candidate_id)


def _truncate_youtube_reactions(existing_urls: list[dict]) -> list[dict]:
    """v0.10.28c (D48·D49·D50 a) — youtube_reactions origin URL 정렬 + 대표 1건 truncate.

    동일 candidate × feature 의 existing_urls 중 origin="youtube_reactions" URL 이 N건
    있으면 view_count 내림차순 정렬 + 1건만 채택. 나머지 (N-1) 건은 제거 + 채택된 1건에
    `youtube_truncated_count` 메타 부착 (UI 가 "+ N건 추가 영상 식별" pill 표시).

    다른 origin URL 은 영향 없음 (정렬·truncate 미적용) — D49 a 정합.

    Parameters
    ----------
    existing_urls : list[dict]
        v0.10.28a 메타 carry 가 완료된 URL 목록 (view_count·origin 등 보존).

    Returns
    -------
    list[dict]
        youtube_reactions 가 1건으로 축소된 URL 목록 + 비-youtube URL 그대로 유지.
        순서: 비-youtube URL (원래 순서) + youtube 대표 1건 (말단 추가).
    """
    if not existing_urls:
        return existing_urls

    non_yt: list[dict] = []
    yt: list[dict]     = []
    for u in existing_urls:
        if u.get("origin") == "youtube_reactions":
            yt.append(u)
        else:
            non_yt.append(u)

    # youtube 1건 이하면 truncate 불필요
    if len(yt) <= 1:
        return existing_urls

    # view_count 내림차순 정렬 (None 은 0 으로 취급)
    yt_sorted = sorted(yt, key=lambda u: (u.get("view_count") or 0), reverse=True)
    representative = dict(yt_sorted[0])
    representative["youtube_truncated_count"] = len(yt) - 1

    return non_yt + [representative]


def _build_feature_items_from_analysis(features_in_report: list[dict]) -> list[dict]:
    """analysis_features (흐름 A·A+B) 의 feature 를 UI 카드용 item 으로 변환.

    각 item 에 coverage_summary + coverage_details 포함 — v0.10.16 UI 사양 유지.
    v0.10.28a (D46·D47) — existing_urls 에 source 특수 메타 carry + candidate_label
    부착으로 UI 가 origin chip 6종 분기 + 사용자 친화적 candidate 표시 가능.
    """
    items: list[dict] = []
    for feat in features_in_report:
        coverage_summary: dict[str, int] = {"sufficient": 0, "partial": 0, "not_found": 0}
        coverage_details: list[dict] = []
        for cov in feat.get("candidate_coverage", []) or []:
            key = cov.get("coverage", "not_found")
            coverage_summary[key] = coverage_summary.get(key, 0) + 1

            existing_urls = [
                {
                    "url":              (u.get("url") or "").strip(),
                    "relevance_note":   (u.get("relevance_note") or "").strip(),
                    "origin":           u.get("origin", "official_source"),
                    # v0.10.28a — source 특수 메타 carry (D46)
                    # 5 source-type 각자 의미 있는 메타만 채움 (LLM 출력 schema 정합)
                    "source_tier":      u.get("source_tier"),       # macro 한정
                    "tier_group":       u.get("tier_group"),        # macro 한정
                    "subpage_category": u.get("subpage_category"),  # official 한정
                    "domain_class":     u.get("domain_class"),      # blog_community 한정
                    "view_count":       u.get("view_count"),        # youtube_reactions 한정
                    "platform":         u.get("platform"),          # owned_channels 한정
                }
                for u in (cov.get("existing_urls") or [])
                if u.get("url")
            ]
            # v0.10.28c (D48·D49·D50 a) — youtube_reactions URL 다수 시 대표 1건만 채택
            existing_urls = _truncate_youtube_reactions(existing_urls)
            additional_urls = [
                {
                    "url":         (u.get("url") or "").strip(),
                    "rationale":   (u.get("rationale") or "").strip(),
                    "validated":   bool(u.get("validated", False)),
                    "http_status": u.get("http_status"),
                }
                for u in (cov.get("additional_urls") or [])
                if u.get("url")
            ]
            candidate_id = cov.get("candidate_id", "")
            coverage_details.append({
                "candidate_id":    candidate_id,
                "candidate_label": _candidate_label(candidate_id),   # v0.10.28a (D47 c)
                "coverage":        key,
                "existing_urls":   existing_urls,
                "additional_urls": additional_urls,
            })

        items.append({
            "feature_id":       feat.get("feature_id", ""),
            "feature_name":     feat.get("feature_name", ""),
            "description":      feat.get("description", ""),
            "priority":         feat.get("priority", "medium"),
            "coverage_summary": coverage_summary,
            "coverage_details": coverage_details,
        })
    return items


def _build_feature_items_from_taxonomy(report_entry: dict) -> list[dict]:
    """B-only 리포트(positioning_map · executive_summary)의 features 를 UI item 으로 변환.

    v0.10.18 의 _extract_active_reports 필터로 인해 analysis_features 에 미포함되므로
    domain_taxonomy.report_config[<rt>] 에서 직접 읽어 features 만 노출. URL coverage 부재.
    """
    feature_labels = (report_entry.get("feature_labels") or {})
    items: list[dict] = []
    for fid in (report_entry.get("features") or []):
        items.append({
            "feature_id":       fid,
            # feature_id 에 feat_ 접두사가 없으면 그대로 사용 (taxonomy 단계 명세는 접두사 없음)
            "feature_name":     feature_labels.get(fid, fid),
            "description":      "",                       # B-only 는 description 부재 (taxonomy 보유 안 함)
            "priority":         "medium",                 # B-only 기본 우선순위
            "coverage_summary": None,                     # B-only 표식 — client 가 URL 영역 미렌더
            "coverage_details": None,                     # 동일
        })
    return items


# 2026-11 — reaction_insight 카드의 공식 YouTube 채널 payload 빌더 ─────────────
# (구 marketing_social owned_channels_card 의 6 platform 매트릭스를 대체)
# cross_reference_node 는 owned_channel_urls_by_candidate 중 platform=youtube_official
# 항목의 channel_id 를 **전 candidate 합집합**으로 모아, 그 채널이 올린 영상을 반응 수집
# 대상에서 제외한다. 따라서 reaction_insight 에서 활용되는 정보는 youtube_official 의
# url·handle·channel_id 뿐이며, channel_id 가 없으면 제외 필터가 적용되지 않는다.

def _build_reaction_preview(state: dict) -> dict:
    """2026-11 — 고객 반응 수집 대상 집계 (feature_selection 시점 = 탐색 완료, 수집 전).

    - youtube: 공식 채널 영상 제외(cross_reference) 후. total 은 고유 영상 수
      (video_candidate_index), by_candidate 는 상품별 영상 수 — 한 영상이 여러 상품에
      해당할 수 있어 합계 ≥ total.
    - community: total 은 고유 게시글 URL 수(탐색 단계 dedup). by_candidate 는
      matched_candidates 기준(다중 상품 게시글은 각 상품에 1회씩), by_site 는 사이트별.
    """
    yt_by_cand: dict = state.get("youtube_reactions_urls_by_candidate") or {}
    video_index: dict = state.get("video_candidate_index") or {}
    yt_total = len(video_index) or len({
        v.get("video_id") for items in yt_by_cand.values() for v in items if v.get("video_id")
    })

    cm_by_cand: dict = state.get("blog_community_urls_by_candidate") or {}
    cm_count: dict[str, int] = {}
    site_rows: dict[str, dict] = {}
    cm_total = 0
    for primary, entries in cm_by_cand.items():
        for e in entries or []:
            cm_total += 1
            cids = e.get("matched_candidates") or [primary]
            site = e.get("site") or ""
            row = site_rows.setdefault(site, {"site": site, "total": 0, "by_candidate": {}})
            row["total"] += 1
            for cid in cids:
                cm_count[cid] = cm_count.get(cid, 0) + 1
                row["by_candidate"][cid] = row["by_candidate"].get(cid, 0) + 1

    return {
        "youtube": {
            "total":        yt_total,
            "by_candidate": {cid: len(items or []) for cid, items in yt_by_cand.items()},
        },
        "community": {
            "total":        cm_total,
            "by_candidate": cm_count,
            "by_site":      sorted(site_rows.values(), key=lambda r: (-r["total"], r["site"])),
        },
    }


def _ordered_candidates(state: dict) -> list[tuple[str, str, str]]:
    """(candidate_id, 표시 이름, "own"|"competitor") — 자사 → 경쟁사 candidate_id 사전순.

    comparison_matrix_node._candidate_rows_order 와 같은 순서 (리포트 표와 선택 화면 일치).
    """
    own_product: dict           = state.get("own_product") or {}
    competitor_candidates: list = state.get("competitor_candidates") or []
    selected_ids: list[str]     = state.get("selected_competitor_ids") or []

    own_id = own_product.get("product_id") or "own"
    own_name = (
        own_product.get("name") or own_product.get("product_name") or "자사 상품"
    )
    competitors: list[tuple[str, str, str]] = []
    for cand in competitor_candidates:
        cid = cand.get("candidate_id", "")
        if cid and (not selected_ids or cid in selected_ids):
            name = cand.get("product_name") or cand.get("brand", "") or cid
            competitors.append((cid, name, "competitor"))
    return [(own_id, own_name, "own")] + sorted(competitors)


def _build_owned_youtube_card(state: dict) -> dict:
    """candidate 별 공식 YouTube 채널 (cross_reference 제외 필터 입력) payload.

    Returns
    -------
    dict
        {"candidates": [{"candidate_id", "candidate_label", "candidate_type": "own"|"competitor",
                         "channels": [{"url", "handle", "channel_id"}]}]}
        channels 는 youtube_official 항목 전부 (다중 공식 계정 D17 — cross_reference 와 동일 범위),
        channel_id 기준 중복 제거.
    """
    urls_by_candidate: dict = state.get("owned_channel_urls_by_candidate") or {}

    candidates_out: list[dict] = []
    for cid, label, ctype in _ordered_candidates(state):
        # 2026-11 — 같은 채널이 @핸들 URL 과 /channel/UC… URL 로 중복 발견되는 경우가 있어
        # channel_id 기준으로 1건만 표시한다(@핸들 URL 우선). cross_reference 는 channel_id
        # 집합을 쓰므로 표시 중복 제거가 제외 필터 결과에 영향을 주지 않는다.
        # 정렬: @핸들 URL 우선 → URL 사전순 (탐색 노드의 병렬 완료 순서와 무관하게 고정).
        yt = sorted(
            (u for u in (urls_by_candidate.get(cid) or [])
             if u.get("platform") == "youtube_official"),
            key=lambda u: (not (u.get("handle") or ""), u.get("url", "")),
        )
        channels: list[dict] = []
        seen_keys: set[str] = set()
        for u in yt:
            key = u.get("channel_id") or u.get("url", "")
            if not key or key in seen_keys:
                continue
            seen_keys.add(key)
            channels.append({
                "url":           u.get("url", ""),
                "handle":        u.get("handle", "") or "",
                "channel_id":    u.get("channel_id", "") or "",
                "channel_title": u.get("channel_title", "") or "",   # 2026-11 — 핸들 부재 시 표시용
            })
        candidates_out.append({
            "candidate_id":    cid,
            "candidate_label": label,
            "candidate_type":  ctype,
            "channels":        channels,
        })
    return {"candidates": candidates_out}


def _error(started_at: str, message: str) -> dict:
    logger.error("feature_selection_node 오류: %s", message)
    return {
        "errors": [{
            "node":      "feature_selection_node",
            "error":     message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }],
        "agent_steps": [{
            "step_name":     "FeatureSelection",
            "status":        "failed",
            "started_at":    started_at,
            "finished_at":   datetime.now(timezone.utc).isoformat(),
            "error_message": message,
        }],
    }
