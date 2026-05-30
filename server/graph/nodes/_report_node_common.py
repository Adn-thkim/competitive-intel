"""
server/graph/nodes/_report_node_common.py
-----------------------------------------
7개 리포트 노드 (§6-4 D1=B 분리형, v0.10)의 공통 helper.

공통 책임
---------
- `domain_taxonomy.report_config[report_type]`을 읽어 본 리포트가 active인지 확인.
- active=false면 즉시 skip (NotImplementedError 미발생).
- active=true면 후속 LLM 호출 로직(아직 미구현)으로 진입.
- 표준화된 출력 컨테이너(`build_report_envelope`)를 통해 모든 리포트가 동일한
  메타데이터(rubric_version·categories·evaluation_score 등)를 갖도록 한다.

설계 의도
---------
- 7개 노드의 중복 로직(스킵 분기·envelope 작성·error 헬퍼·step 작성)을 분리.
- Rubric 버전·평가 점수·source_references 형식을 단일 위치에서 관리.

상태
----
**SCAFFOLD ONLY** — 본 모듈은 §6-4의 인터페이스 정의를 위한 helper.
실제 LLM 호출 로직은 §6-5/§6-6/§6-6a 산출 형식 확정 후 각 노드에 구현된다.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from server.graph.state import AgentStep, DomainAnalysisState

# v0.10 D4 enum 7종 (output.schema.json + domain_modeling_node.REPORT_TYPES와 정합)
REPORT_TYPES = (
    "comparison_matrix",
    "reaction_insight",
    "marketing_social",
    "battlecard",
    "positioning_map",
    "market_context_swot",
    "executive_summary",
)


def get_report_entry(
    state: DomainAnalysisState, report_type: str
) -> dict[str, Any] | None:
    """
    `domain_taxonomy.report_config[report_type]` 항목을 반환한다.

    Returns
    -------
    dict | None
        report_config에 키가 있으면 entry(dict). 없으면 None.
    """
    taxonomy = state.get("domain_taxonomy") or {}
    report_config = taxonomy.get("report_config") or {}
    entry = report_config.get(report_type)
    if not isinstance(entry, dict):
        return None
    return entry


def is_report_active(state: DomainAnalysisState, report_type: str) -> bool:
    """v0.10 report_config[report_type].active 가 true인지 확인."""
    entry = get_report_entry(state, report_type)
    if entry is None:
        return False
    return entry.get("active") is True


def build_report_envelope(
    *,
    report_type: str,
    rubric_version: str,
    categories: list[str],
    content: dict[str, Any],
    evaluation_score: int,
    source_references: list[dict[str, Any]] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """
    리포트 노드 산출의 표준 envelope.

    Returns
    -------
    dict
        state["report_outputs"][report_type]에 그대로 저장 가능한 구조.
    """
    return {
        "rubric_version":    rubric_version,
        "categories":        categories,
        "content":           content,
        "evaluation_score":  evaluation_score,
        "generated_at":      datetime.now(timezone.utc).isoformat(),
        "source_references": source_references or [],
        "warnings":          warnings or [],
    }


def make_skip_result(report_type: str, started_at: str) -> dict[str, Any]:
    """
    `active: false` 또는 entry 누락 시 노드를 정상 종료시키는 결과.
    `report_outputs`에 해당 키를 쓰지 않고, agent_steps에 skipped 기록만 누적한다.
    """
    finished_at = datetime.now(timezone.utc).isoformat()
    return {
        "agent_steps": [{
            "step_name":   f"{report_type}_node",
            "status":      "skipped",
            "started_at":  started_at,
            "finished_at": finished_at,
        }],
    }


def make_error_result(
    report_type: str, started_at: str, message: str
) -> dict[str, Any]:
    """리포트 노드 공통 실패 응답."""
    finished_at = datetime.now(timezone.utc).isoformat()
    return {
        "errors": [{
            "node":      f"{report_type}_node",
            "error":     message,
            "timestamp": finished_at,
        }],
        "agent_steps": [{
            "step_name":     f"{report_type}_node",
            "status":        "failed",
            "started_at":    started_at,
            "finished_at":   finished_at,
            "error_message": message,
        }],
    }


def make_completed_step(report_type: str, started_at: str) -> AgentStep:
    """리포트 노드 정상 완료 시 agent_step."""
    return {
        "step_name":   f"{report_type}_node",
        "status":      "completed",
        "started_at":  started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
