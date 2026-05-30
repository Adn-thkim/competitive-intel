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

interrupt 값 구조 (v0.10)
-------------------------
{
  "type": "feature_selection",
  "reports": [
    {
      "report_type":  "comparison_matrix",   // D4 enum 7종 중 하나
      "report_label": "비교 매트릭스",
      "features": [
        {
          "feature_id":   "feat_transaction_fee_rate",
          "feature_name": "거래 수수료율",
          "description":  "...",
          "priority":     "high",
          "coverage_summary": {"sufficient": 3, "partial": 2, "not_found": 1}
        },
        ...
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

    # ── interrupt 값 조립 ────────────────────────────────────────────────────
    reports_payload: list[dict] = []
    for rt in report_order:
        features_in_report = grouped.get(rt, [])
        if not features_in_report:
            continue

        feature_items = []
        for feat in features_in_report:
            coverage_summary: dict[str, int] = {"sufficient": 0, "partial": 0, "not_found": 0}
            # v0.10.16 — UI 확장 표시용 candidate 별 coverage 상세
            coverage_details: list[dict] = []
            for cov in feat.get("candidate_coverage", []):
                key = cov.get("coverage", "not_found")
                coverage_summary[key] = coverage_summary.get(key, 0) + 1

                # 각 URL 항목에서 UI 노출에 필요한 최소 필드만 추출
                existing_urls = [
                    {
                        "url":            (u.get("url") or "").strip(),
                        "relevance_note": (u.get("relevance_note") or "").strip(),
                        "origin":         u.get("origin", "official_source"),
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
                coverage_details.append({
                    "candidate_id":   cov.get("candidate_id", ""),
                    "coverage":       key,
                    "existing_urls":  existing_urls,
                    "additional_urls": additional_urls,
                })

            feature_items.append({
                "feature_id":       feat.get("feature_id", ""),
                "feature_name":     feat.get("feature_name", ""),
                "description":      feat.get("description", ""),
                "priority":         feat.get("priority", "medium"),
                "coverage_summary": coverage_summary,
                # v0.10.16 신설 — client FeatureCard 확장 시 candidate × URL 상세 표시용
                "coverage_details": coverage_details,
            })

        reports_payload.append({
            "report_type":  rt,
            "report_label": report_label_map.get(rt, rt),
            "features":     feature_items,
        })

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
