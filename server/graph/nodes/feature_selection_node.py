"""
server/graph/nodes/feature_selection_node.py
---------------------------------------------
Feature 선택 Human-in-the-loop 중단점 노드 (interrupt #4).

역할
----
feature_url_mapper_node 이후 실행된다.
analysis_features를 purpose 단위로 그룹핑해 프런트엔드에 전달하고 그래프를 일시 중단한다.
사용자가 분석할 purpose(목적)와 feature(항목)를 선택하면 재개(resume)되어
selected_purposes · selected_feature_ids를 state에 저장한다.

interrupt 값 구조
-----------------
{
  "type": "feature_selection",
  "purposes": [
    {
      "purpose_id":    "fee_comparison",
      "purpose_label": "수수료 비교",
      "features": [
        {
          "feature_id":   "feat_transaction_fee_rate",
          "feature_name": "거래 수수료율",
          "description":  "...",
          "priority":     "high",
          "coverage_summary": {
            "sufficient": 3,
            "partial":    2,
            "not_found":  1
          }
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
  "selected_purposes":    ["fee_comparison", "speed_comparison"],
  "selected_feature_ids": ["feat_transaction_fee_rate", "feat_transfer_time_standard"]
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


def feature_selection_node(state: DomainAnalysisState) -> dict:
    """
    Feature 선택을 위한 네 번째 Human-in-the-loop 중단점 노드.

    Parameters
    ----------
    state : DomainAnalysisState
        필수 키: analysis_features
        선택 키: domain_taxonomy (purpose 레이블 조회용)

    Returns
    -------
    dict
        사용자 선택 후: selected_purposes, selected_feature_ids, agent_steps
    """
    started_at = datetime.now(timezone.utc).isoformat()

    analysis_features: list[dict] = state.get("analysis_features") or []
    domain_taxonomy: dict         = state.get("domain_taxonomy") or {}

    if not analysis_features:
        return _error(started_at, "analysis_features가 state에 없습니다. "
                                  "feature_url_mapper_node가 먼저 실행되어야 합니다.")

    # ── purpose 레이블 맵 구성 ───────────────────────────────────────────────
    # domain_taxonomy.purpose_config[purpose_id].label
    purpose_config: dict = domain_taxonomy.get("purpose_config", {})
    purpose_label_map: dict[str, str] = {
        pid: cfg.get("label", pid)
        for pid, cfg in purpose_config.items()
    }

    # ── analysis_features를 purpose 단위로 그룹핑 ──────────────────────────
    # active_purposes 순서를 유지하기 위해 OrderedDict 방식으로 수집
    active_purposes: list[str] = domain_taxonomy.get("active_purposes", [])
    purpose_order: list[str] = active_purposes if active_purposes else list(
        dict.fromkeys(f.get("purpose_id", "unknown") for f in analysis_features)
    )

    grouped: dict[str, list[dict]] = {pid: [] for pid in purpose_order}
    for feature in analysis_features:
        pid = feature.get("purpose_id", "unknown")
        if pid not in grouped:
            grouped[pid] = []
        grouped[pid].append(feature)

    # ── interrupt 값 조립 ────────────────────────────────────────────────────
    purposes_payload: list[dict] = []
    for pid in purpose_order:
        features_in_purpose = grouped.get(pid, [])
        if not features_in_purpose:
            continue

        feature_items = []
        for feat in features_in_purpose:
            # coverage 요약: candidate_coverage의 coverage 값 집계
            coverage_summary: dict[str, int] = {"sufficient": 0, "partial": 0, "not_found": 0}
            for cov in feat.get("candidate_coverage", []):
                key = cov.get("coverage", "not_found")
                coverage_summary[key] = coverage_summary.get(key, 0) + 1

            feature_items.append({
                "feature_id":       feat.get("feature_id", ""),
                "feature_name":     feat.get("feature_name", ""),
                "description":      feat.get("description", ""),
                "priority":         feat.get("priority", "medium"),
                "coverage_summary": coverage_summary,
            })

        purposes_payload.append({
            "purpose_id":    pid,
            "purpose_label": purpose_label_map.get(pid, pid),
            "features":      feature_items,
        })

    total_feature_count = sum(len(p["features"]) for p in purposes_payload)
    logger.info(
        "feature_selection_node: interrupt() 호출 "
        "(purposes=%d개, features=%d개)",
        len(purposes_payload), total_feature_count,
    )

    resume_value: dict = interrupt({
        "type":     "feature_selection",
        "purposes": purposes_payload,
    })

    # ── 재개 후 처리 ─────────────────────────────────────────────────────────
    selected_feature_ids: list[str] = resume_value.get("selected_feature_ids", [])
    selected_purposes_raw: list[str] = resume_value.get("selected_purposes", [])

    # 최소 1개 검증
    if not selected_feature_ids:
        return _error(started_at, "selected_feature_ids가 비어 있습니다. "
                                  "최소 1개 이상의 feature를 선택해야 합니다.")

    # 유효 feature_id 집합 구성
    valid_feature_ids: set[str] = {
        f.get("feature_id", "") for f in analysis_features
    }
    invalid = [fid for fid in selected_feature_ids if fid not in valid_feature_ids]
    if invalid:
        return _error(started_at,
                      f"유효하지 않은 feature_id가 포함되어 있습니다: {invalid}")

    # selected_purposes 역산 검증:
    # 프런트엔드가 보낸 selected_purposes를 기준으로 하되,
    # selected_feature_ids에서 실제로 사용된 purpose_id와 교차 검증한다.
    feature_purpose_map: dict[str, str] = {
        f.get("feature_id", ""): f.get("purpose_id", "")
        for f in analysis_features
    }
    derived_purposes: list[str] = list(dict.fromkeys(
        feature_purpose_map[fid]
        for fid in selected_feature_ids
        if fid in feature_purpose_map
    ))

    # 프런트엔드 전송값이 있으면 우선 사용, 없으면 역산값 사용
    selected_purposes: list[str] = selected_purposes_raw if selected_purposes_raw else derived_purposes

    finished_at = datetime.now(timezone.utc).isoformat()
    logger.info(
        "feature_selection_node: 완료 "
        "(purposes=%d개: %s, features=%d개)",
        len(selected_purposes), selected_purposes, len(selected_feature_ids),
    )

    step: AgentStep = {
        "step_name":   "FeatureSelection",
        "status":      "completed",
        "started_at":  started_at,
        "finished_at": finished_at,
    }

    return {
        "selected_purposes":    selected_purposes,
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
