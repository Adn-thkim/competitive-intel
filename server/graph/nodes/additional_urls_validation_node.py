"""
server/graph/nodes/additional_urls_validation_node.py (v0.10.9)
---------------------------------------------------------------
feature_url_mapper 4단계 분리 중 Step 3 — LLM 이 제안한 additional_urls 의 HTTP 도달성 검증.

역할
----
feature_mapping_llm_node 가 제안한 각 candidate_coverage[*].additional_urls 의 URL 에 대해
HEAD→GET 순차 시도로 http_status 를 수집하고, 200–399 인 경우 validated=True 로 마킹한다.
최종적으로 raw_features → analysis_features 변환을 완료한다.

입력 state 키
-------------
- raw_features : feature_mapping_llm_node 산출

출력 state 키
-------------
- analysis_features : list[AnalysisFeature] — feature_selection_node 가 사용
- agent_steps       : 누적 reducer
"""

import logging
from datetime import datetime, timezone

from server.graph.progress_store import set_progress
from server.graph.state import DomainAnalysisState, AgentStep
from server.graph.nodes.feature_url_mapper_node import (
    _validate_additional_urls,
    _error,
)

logger = logging.getLogger(__name__)


def additional_urls_validation_node(
    state: DomainAnalysisState, config: dict | None = None,
) -> dict:
    """
    additional_urls 의 HTTP 도달성 검증 후 analysis_features 산출.

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
                         detail="추가 URL 도달성 검증")
        except Exception as exc:  # noqa: BLE001
            logger.debug("set_progress(validate) 실패: %s", exc)

    raw_features: list[dict] = state.get("raw_features") or []
    if not raw_features:
        # raw_features 가 비어도 빈 analysis_features 반환 (feature_selection 이 빈 결과 처리)
        logger.warning("additional_urls_validation_node: raw_features 가 비어 있음")
        analysis_features: list = []
    else:
        analysis_features = _validate_additional_urls(raw_features)

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
