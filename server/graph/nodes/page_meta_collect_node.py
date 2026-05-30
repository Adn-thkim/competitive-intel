"""
server/graph/nodes/page_meta_collect_node.py (v0.10.9)
------------------------------------------------------
feature_url_mapper 4단계 분리 중 Step 1 — official_sources + Brave URL 의 page meta 수집.

역할
----
official_source_resolver 가 검증한 URL 의 <title> · <meta description> 을 병렬 GET 으로
수집하고, Step 0(url_discovery_brave_node) 가 발견한 Brave URL 과 병합하여 candidate 별
validated_urls 목록(page meta 포함) 을 만든다.

입력 state 키
-------------
- official_sources         : official_source_resolver 산출
- brave_urls_by_candidate  : url_discovery_brave_node 산출 (Step 0)

출력 state 키
-------------
- candidates_with_meta : list[dict] — Step 2 LLM 입력
- agent_steps          : 누적 reducer
"""

import logging
from datetime import datetime, timezone

from server.graph.progress_store import set_progress
from server.graph.state import DomainAnalysisState, AgentStep
from server.graph.nodes.feature_url_mapper_node import (
    _build_candidates_with_meta,
    _error,
)

logger = logging.getLogger(__name__)


def page_meta_collect_node(state: DomainAnalysisState, config: dict | None = None) -> dict:
    """
    official + Brave URL 의 page_title · meta_description 을 병렬 HTTP GET 으로 수집한다.

    Returns
    -------
    dict
        {candidates_with_meta, agent_steps} 또는 {errors, agent_steps}
    """
    started_at = datetime.now(timezone.utc).isoformat()
    thread_id  = (config or {}).get("configurable", {}).get("thread_id", "")

    print(
        f"📄 [page_meta_collect_node] ENTRY at {started_at} thread_id={thread_id!r}",
        flush=True,
    )

    if thread_id:
        try:
            set_progress(thread_id, "feature_mapping_meta",
                         detail="페이지 메타 수집 중")
        except Exception as exc:  # noqa: BLE001
            logger.debug("set_progress(meta) 실패: %s", exc)

    # ── 입력 수집 ───────────────────────────────────────────────────────────
    official_sources: list[dict]              = state.get("official_sources") or []
    brave_urls_by_candidate: dict[str, list]  = state.get("brave_urls_by_candidate") or {}

    if not official_sources:
        return _error(started_at, "official_sources 가 state 에 없습니다.")

    # ── Page Meta 병합 ──────────────────────────────────────────────────────
    candidates_with_meta = _build_candidates_with_meta(
        official_sources=official_sources,
        brave_urls_by_candidate=brave_urls_by_candidate,
    )
    total_urls = sum(len(c["validated_urls"]) for c in candidates_with_meta)
    logger.info(
        "page_meta_collect_node: 완료 (URL=%d, candidates=%d)",
        total_urls, len(candidates_with_meta),
    )

    finished_at = datetime.now(timezone.utc).isoformat()
    step: AgentStep = {
        "step_name":   "PageMetaCollect",
        "status":      "completed",
        "started_at":  started_at,
        "finished_at": finished_at,
    }
    return {
        "candidates_with_meta": candidates_with_meta,
        "agent_steps":          [step],
    }
