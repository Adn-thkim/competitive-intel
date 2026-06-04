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
    "comparison_matrix":
        "이 리포트는 아래 선택된 feature 데이터를 자사·경쟁사 공식 사이트에서 "
        "수집하여 작성합니다.",
    "reaction_insight":
        "이 리포트는 아래 선택된 feature 데이터를 외부 후기·블로그·YouTube 영상·"
        "커뮤니티 게시글에서 수집하여 작성합니다.",
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
    #   B-only 형식으로 렌더 + 별도 owned_channels_card 로 candidate × platform 매트릭스 표시.
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
            "url_coverage_visible": not (is_b_only or is_marketing_social_b_view),
            "features":             feature_items,
        }

        # v0.10.28b D45 a — marketing_social 에 owned_channels_card payload 부착
        if is_marketing_social_b_view:
            report_item["owned_channels_card"] = _build_owned_channels_card(state)

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

    if not selected_feature_ids:
        return _error(started_at, "selected_feature_ids가 비어 있습니다. "
                                  "최소 1개 이상의 feature를 선택해야 합니다.")

    valid_feature_ids: set[str] = {
        f.get("feature_id", "") for f in analysis_features
    }
    invalid = [fid for fid in selected_feature_ids if fid not in valid_feature_ids]
    if invalid:
        return _error(started_at,
                      f"유효하지 않은 feature_id가 포함되어 있습니다: {invalid}")

    # selected_purposes 역산 검증 (v0.10 — report_type 목록)
    feature_report_map: dict[str, str] = {
        f.get("feature_id", ""): f.get("report_type", "")
        for f in analysis_features
    }
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


# v0.10.28b D45 a — owned_channels_card payload 빌더 ────────────────────────

_PLATFORM_LABELS: dict[str, str] = {
    "instagram":       "Instagram",
    "x":               "X (Twitter)",
    "blog_naver":      "네이버 블로그",
    "blog_tistory":    "티스토리 블로그",
    "press_release":   "보도자료",
    "youtube_official": "YouTube 공식 채널",
}

_PLATFORM_ORDER: tuple[str, ...] = (
    "instagram", "x", "youtube_official",
    "blog_naver", "blog_tistory", "press_release",
)


def _build_owned_channels_card(state: dict) -> dict:
    """v0.10.28b D45 a — marketing_social 카드의 candidate × platform 매트릭스 payload.

    `owned_channel_urls_by_candidate` (url_discovery_owned_channels_node 산출) 를
    candidate × platform 그리드로 재구성. 각 셀은 platform 별 URL + handle +
    account_scope + channel_id (youtube_official) + confidence 메타.

    own_product / competitor_candidates 의 product_name 으로 candidate 라벨 변환.

    Returns
    -------
    dict
        {
          "candidates": [
            {
              "candidate_id":   "own_xxx" | "comp_xxx",
              "candidate_label": "트래블월렛" | "토스뱅크" | candidate_id,
              "candidate_type": "own" | "competitor",
              "platforms": [
                {
                  "platform":      "instagram",
                  "platform_label": "Instagram",
                  "found":         true,
                  "url":           str,
                  "handle":        str,
                  "account_scope": "parent_company" | ... | "" ,
                  "channel_id":    str | "",     # youtube_official 한정
                  "subscriber_count": int | null, # youtube_official 한정
                  "confidence":    float,
                },
                ...
              ],
            },
            ...
          ]
        }
    """
    urls_by_candidate: dict = state.get("owned_channel_urls_by_candidate") or {}
    own_product: dict        = state.get("own_product") or {}
    competitor_candidates: list = state.get("competitor_candidates") or []
    selected_ids: list[str]   = state.get("selected_competitor_ids") or []

    own_id = own_product.get("product_id") or "own"
    own_name = (
        own_product.get("name") or own_product.get("product_name") or "자사 상품"
    )

    # candidate_id → (label, type) 매핑
    label_by_id: dict[str, tuple[str, str]] = {own_id: (own_name, "own")}
    for cand in competitor_candidates:
        cid = cand.get("candidate_id", "")
        if cid and (not selected_ids or cid in selected_ids):
            name = cand.get("product_name") or cand.get("brand", "") or cid
            label_by_id[cid] = (name, "competitor")

    candidates_out: list[dict] = []
    # 결과는 own → competitor 순서로 정렬 (label_by_id 의 own_id 우선 + 선택 경쟁사 순서)
    ordered_ids = [own_id] + [
        c.get("candidate_id", "") for c in competitor_candidates
        if c.get("candidate_id") and (not selected_ids or c.get("candidate_id") in selected_ids)
    ]
    for cid in ordered_ids:
        if cid not in label_by_id:
            continue
        candidate_label, candidate_type = label_by_id[cid]
        # platform → url 매핑 (첫 매칭 URL 채택)
        urls = urls_by_candidate.get(cid, []) or []
        urls_by_platform: dict[str, dict] = {}
        for u in urls:
            p = u.get("platform", "")
            if p and p not in urls_by_platform:
                urls_by_platform[p] = u

        platforms_out: list[dict] = []
        for p in _PLATFORM_ORDER:
            label = _PLATFORM_LABELS.get(p, p)
            if p in urls_by_platform:
                u = urls_by_platform[p]
                platforms_out.append({
                    "platform":         p,
                    "platform_label":   label,
                    "found":            True,
                    "url":              u.get("url", ""),
                    "handle":           u.get("handle", ""),
                    "account_scope":    u.get("account_scope", ""),
                    "channel_id":       u.get("channel_id", "") or "",
                    "subscriber_count": u.get("follower_count"),
                    "confidence":       float(u.get("confidence", 0)),
                })
            else:
                platforms_out.append({
                    "platform":       p,
                    "platform_label": label,
                    "found":          False,
                })
        candidates_out.append({
            "candidate_id":    cid,
            "candidate_label": candidate_label,
            "candidate_type":  candidate_type,
            "platforms":       platforms_out,
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
