"""
server/graph/nodes/additional_urls_validation_node.py (v0.10.9 → v0.10.27 임시 호환)
-----------------------------------------------------------------------------------
feature_url_mapper 흐름의 최종 단계 — LLM 이 제안한 additional_urls 의 HTTP 도달성 검증.

역할
----
5 통합 노드 (`feature_mapping_<source>_node`, v0.10.27) 가 산출한 5종 `*_raw_features`
를 임시 어댑터로 단일 raw_features 로 union 한 뒤, 각 candidate_coverage[*].
additional_urls 의 URL 에 대해 HEAD→GET 순차 시도로 http_status 를 수집하고
200–399 인 경우 validated=True 로 마킹한다. 최종적으로 raw_features → analysis_features
변환.

v0.10.27 임시 호환 (D42 a)
-------------------------
본 노드는 v0.10.25 의 정식 _union_raw_features 헬퍼 도입까지 임시 어댑터로 동작.
v0.10.25 에서:
  - candidate_coverage union 처리 (D23) — 동일 feature_id 의 여러 source 결과 통합
  - source-type 별 검증 분기 (`videos.list`·`owned_channel` 검증)
이 신설되면 본 노드의 입력 흐름이 정식화됨.

입력 state 키 (v0.10.27)
------------------------
- official_raw_features
- blog_community_raw_features
- youtube_reactions_raw_features
- owned_channel_raw_features
- macro_raw_features
- raw_features (v0.10.27 임시 호환 — 옛 흐름 fallback)

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
    _union_raw_features,
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

    # v0.10.27 임시 호환 — 5종 *_raw_features 를 단일 raw_features 로 union (D42 a)
    # v0.10.25 정식 _union_raw_features (D23 union 처리) 도입 시 본 어댑터 폐기.
    raw_features: list[dict] = _union_raw_features(state)

    # v0.10.27 도입 전 옛 흐름과의 fallback — state["raw_features"] 가 있고 union 결과가 비면 옛 키 사용
    if not raw_features and state.get("raw_features"):
        logger.info(
            "additional_urls_validation_node: 5종 *_raw_features 모두 빈 결과 — "
            "옛 raw_features fallback 사용 (v0.10.27 도입 전 그래프)",
        )
        raw_features = state.get("raw_features") or []

    if not raw_features:
        # 모두 빈 결과 — 빈 analysis_features 반환 (feature_selection 이 빈 결과 처리)
        logger.warning(
            "additional_urls_validation_node: 5종 *_raw_features + raw_features 모두 빈 결과",
        )
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
