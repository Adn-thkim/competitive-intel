"""
server/graph/nodes/competitor_selection_node.py
------------------------------------------------
경쟁사 선택 Human-in-the-loop 중단점 노드.

역할
----
1. normalize_competitor_ids_node 이후 실행된다.
2. competitor_candidates (conf 기준 내림차순 정렬된 브랜드 경쟁사) 와
   functional_competitors (conf 기준 내림차순 정렬된 기능적 대안) 를
   interrupt() 값으로 프런트엔드에 전달해 그래프를 일시 중단한다.
3. 사용자가 최소 1개, 최대 10개를 선택하고 확인 버튼을 누르면
   Express가 Command(resume={"selected_ids": [...]}) 로 재개한다.
4. 재개 후 selected_competitor_ids를 state에 저장하고 다음 노드로 진행한다.

interrupt 값 구조 (프런트엔드가 'type' 필드로 1차/2차 인터럽트를 구분)
-----------------------------------------------------------------------
  {
    "type": "competitor_selection",
    "competitor_candidates": [...],   # confidence 내림차순 정렬
    "functional_competitors": [...]   # confidence 내림차순 정렬
  }

resume 값 구조 (프런트엔드 → Express → 여기)
--------------------------------------------
  {
    "selected_ids": ["comp_abc", "func_local_atm", ...]   # 1~10개
  }
"""

import logging
from datetime import datetime, timezone

from langgraph.types import interrupt

from server.graph.state import DomainAnalysisState, AgentStep

logger = logging.getLogger(__name__)


def competitor_selection_node(state: DomainAnalysisState) -> dict:
    """
    경쟁사 선택을 위한 두 번째 Human-in-the-loop 중단점 노드.

    Parameters
    ----------
    state : DomainAnalysisState
        필수 키: competitor_candidates (comp_* 슬러그 확정 후)
        선택 키: functional_competitors

    Returns
    -------
    dict
        사용자 선택 후: selected_competitor_ids, agent_steps
    """
    started_at = datetime.now(timezone.utc).isoformat()

    candidates        = state.get("competitor_candidates") or []
    functional        = state.get("functional_competitors") or []

    if not candidates and not functional:
        return _error(started_at, "competitor_candidates와 functional_competitors가 모두 비어 있습니다.")

    # ── confidence 내림차순 정렬 ─────────────────────────────────────────────
    sorted_candidates = sorted(
        candidates, key=lambda c: c.get("confidence", 0), reverse=True
    )
    sorted_functional = sorted(
        functional, key=lambda f: f.get("confidence", 0), reverse=True
    )

    # ── interrupt() ─────────────────────────────────────────────────────────
    # 프런트엔드가 'type' 필드로 이 interrupt가 competitor_selection임을 감지한다.
    # (1차 interrupt인 human_review는 'display_fields' 키를 가지므로 충돌 없음)
    logger.info(
        "competitor_selection_node: interrupt() 호출 "
        "(브랜드 후보 %d개, 기능적 대안 %d개)",
        len(sorted_candidates), len(sorted_functional),
    )

    resume_value: dict = interrupt({
        "type":                   "competitor_selection",
        "competitor_candidates":  sorted_candidates,
        "functional_competitors": sorted_functional,
    })

    # ── 재개 후 처리 ─────────────────────────────────────────────────────────
    selected_ids: list[str] = resume_value.get("selected_ids", [])

    if not selected_ids:
        return _error(started_at, "selected_ids가 비어 있습니다. 최소 1개를 선택해야 합니다.")

    if len(selected_ids) > 10:
        return _error(
            started_at,
            f"선택 개수({len(selected_ids)}개)가 최대 허용치(10개)를 초과합니다.",
        )

    finished_at = datetime.now(timezone.utc).isoformat()
    logger.info(
        "competitor_selection_node: 완료 (선택 %d개: %s)",
        len(selected_ids), selected_ids,
    )

    step: AgentStep = {
        "step_name":   "CompetitorSelection",
        "status":      "completed",
        "started_at":  started_at,
        "finished_at": finished_at,
    }

    return {
        "selected_competitor_ids": selected_ids,
        "agent_steps":             [step],
    }


# ── 내부 헬퍼 ─────────────────────────────────────────────────────────────────

def _error(started_at: str, message: str) -> dict:
    logger.error("competitor_selection_node 오류: %s", message)
    return {
        "errors": [{
            "node":      "competitor_selection_node",
            "error":     message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }],
        "agent_steps": [{
            "step_name":     "CompetitorSelection",
            "status":        "failed",
            "started_at":    started_at,
            "finished_at":   datetime.now(timezone.utc).isoformat(),
            "error_message": message,
        }],
    }
