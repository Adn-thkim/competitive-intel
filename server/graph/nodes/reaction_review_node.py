"""server/graph/nodes/reaction_review_node.py — human_review#5 (통합계획 §2-3·§4-4).

⑥ 군집 이후 **고정 축 검토용 interrupt**. 현재 범위(사용자 지시): **출력(확인)만** 수행한다 —
  1) [sub_cluster → super_aspect] 매핑
  2) super_aspect 별 perspective 비율
  3) super_aspect 별 question_type 비율
축 수정(라벨 병합·재배정 등)은 **추후 구현**. 따라서 interrupt 재개값(resume)은 소비하지 않고 통과한다.

비파괴: state 를 변경하지 않고 agent_steps 만 반환한다(검토 페이로드는 interrupt_value 로 프런트에 전달).
"""
from __future__ import annotations

import logging
from collections import Counter, defaultdict
from datetime import datetime, timezone

from langgraph.types import interrupt

from server.graph.progress_store import set_progress
from server.graph.state import AgentStep, DomainAnalysisState

logger = logging.getLogger(__name__)


def _step(status: str, started_at: str, error: str = "") -> AgentStep:
    s: AgentStep = {"step_name": "ReactionReview", "status": status,
                    "started_at": started_at,
                    "finished_at": datetime.now(timezone.utc).isoformat()}
    if error:
        s["error_message"] = error[:300]
    return s


def _ratios(counter: Counter) -> dict:
    """{key: count} → {key: {count, pct}} (내림차순). pct 는 소수 3자리."""
    total = sum(counter.values()) or 1
    return {k: {"count": n, "pct": round(n / total, 3)}
            for k, n in counter.most_common()}


def reaction_review_node(state: DomainAnalysisState, config: dict | None = None) -> dict:
    started_at = datetime.now(timezone.utc).isoformat()
    thread_id = (config or {}).get("configurable", {}).get("thread_id", "")
    if thread_id:
        try:
            set_progress(thread_id, "reaction_review", detail="군집 축 검토(#5)")
        except Exception as exc:  # noqa: BLE001
            logger.debug("set_progress 실패: %s", exc)

    clustered = state.get("reaction_clustered") or {}
    axis = state.get("reaction_axis") or {}
    units = clustered.get("units") or []
    questions = clustered.get("questions") or []

    # 1) sub_cluster → super_aspect 매핑 (축 저장소 우선, 없으면 clustered 에서 역산)
    super_to_sub = axis.get("super_to_sub") or (clustered.get("summary") or {}).get("super_to_sub") or {}
    if not super_to_sub:
        agg: dict = defaultdict(Counter)
        for u in units:
            agg[u.get("super_aspect") or "기타"][u.get("sub_cluster") or "uncategorized"] += 1
        super_to_sub = {sa: dict(c.most_common()) for sa, c in agg.items()}

    # 2) super_aspect 별 perspective 비율
    psp: dict = defaultdict(Counter)
    for u in units:
        psp[u.get("super_aspect") or "기타"][u.get("perspective") or "other"] += 1
    # 3) super_aspect 별 question_type 비율
    qt: dict = defaultdict(Counter)
    for q in questions:
        qt[q.get("super_aspect") or "기타"][q.get("question_type") or "other"] += 1

    payload = {
        "review_type": "reaction_axis_review",
        "editable": False,          # 축 수정 미구현 — 확인 전용
        "axis_source": clustered.get("axis_source", ""),
        "super_to_sub": super_to_sub,
        "perspective_by_super": {sa: _ratios(c) for sa, c in psp.items()},
        "question_type_by_super": {sa: _ratios(c) for sa, c in qt.items()},
        "totals": {"units": len(units), "questions": len(questions),
                   "super_aspects": len(super_to_sub)},
    }
    logger.info("reaction_review: interrupt#5 — super_aspect %d(확인 전용)", len(super_to_sub))

    # interrupt() 로 검토 페이로드를 프런트에 전달하고 재개 대기. 재개값은 (수정 미구현) 무시.
    print("[reaction_review] interrupt#5 진입 — 재개 대기", flush=True)
    _ = interrupt(payload)
    print("[reaction_review] resume 통과 → reaction_report 로 진행", flush=True)

    return {"agent_steps": [_step("completed", started_at)]}
