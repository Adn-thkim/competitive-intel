"""
server/graph/nodes/url_discovery_brave_node.py (v0.10.9)
--------------------------------------------------------
feature_url_mapper 4단계 분리 중 Step 0 — Brave Search API 로 URL 후보 발견.

역할
----
domain_taxonomy.report_config 의 active=true 리포트별 search_query_hints 를 candidate
(자사 + 선택 경쟁사) 에 토큰 치환하여 Brave Search API 를 호출하고, 발견된 URL 을
candidate 별로 집계하여 state 에 저장한다.

위치 (v0.10.9 토폴로지)
-----------------------
ab_join
  → [url_discovery_brave_node]   ← 이 노드 (Step 0)
  → page_meta_collect_node       (Step 1)
  → feature_mapping_llm_node     (Step 2)
  → additional_urls_validation_node (Step 3)
  → feature_selection (#4)

입력 state 키
-------------
- domain_taxonomy   : DomainTaxonomyAgent 산출. report_config 의 active 리포트 추출.
- own_product       : 자사 상품 (name·product_name·product_id)
- competitor_candidates / selected_competitor_ids : 검색 대상 candidate 결정
- domain_name       : 검색 토큰 치환용

출력 state 키
-------------
- brave_urls_by_candidate : dict[candidate_id, list[dict]] — Step 1 이 사용
- agent_steps             : 누적 reducer
"""

import logging
from datetime import datetime, timezone

from server.graph.progress_store import set_progress
from server.graph.state import DomainAnalysisState, AgentStep
from server.graph.nodes.feature_url_mapper_node import (
    _discover_via_brave,
    _extract_active_reports,
    _error,
)

logger = logging.getLogger(__name__)


def url_discovery_brave_node(state: DomainAnalysisState, config: dict | None = None) -> dict:
    """
    Brave Search API 호출로 active 리포트별 candidate URL 후보를 발견한다.

    Returns
    -------
    dict
        {brave_urls_by_candidate, agent_steps} 또는 {errors, agent_steps}
    """
    started_at = datetime.now(timezone.utc).isoformat()
    thread_id  = (config or {}).get("configurable", {}).get("thread_id", "")

    print(
        f"🔎 [url_discovery_brave_node] ENTRY at {started_at} thread_id={thread_id!r}",
        flush=True,
    )

    if thread_id:
        try:
            set_progress(thread_id, "feature_mapping_brave",
                         detail="Brave 검색으로 URL 후보 발견")
        except Exception as exc:  # noqa: BLE001
            logger.debug("set_progress(brave) 실패: %s", exc)

    # ── 입력 수집 + 전제조건 검사 ────────────────────────────────────────────
    domain_taxonomy: dict       = state.get("domain_taxonomy") or {}
    domain_name: str            = state.get("domain_name") or ""
    own_product: dict           = state.get("own_product") or {}
    competitor_candidates: list = state.get("competitor_candidates") or []
    selected_ids: list[str]     = state.get("selected_competitor_ids") or []

    if not domain_taxonomy:
        return _error(started_at,
                      "domain_taxonomy 가 state 에 없습니다. "
                      "domain_modeling_node 가 먼저 실행되어야 합니다.")
    if not domain_name:
        return _error(started_at, "domain_name 이 state 에 없습니다.")

    active_reports = _extract_active_reports(domain_taxonomy)
    if not active_reports:
        return _error(started_at,
                      "domain_taxonomy.report_config 에 active=true 인 리포트가 없습니다.")

    # ── Brave 검색 실행 ──────────────────────────────────────────────────────
    logger.info(
        "url_discovery_brave_node: Brave 검색 시작 (active 리포트=%d개)",
        len(active_reports),
    )
    brave_urls_by_candidate = _discover_via_brave(
        active_reports=active_reports,
        own_product=own_product,
        competitor_candidates=competitor_candidates,
        selected_ids=selected_ids,
        domain_name=domain_name,
    )
    logger.info(
        "url_discovery_brave_node: 완료 (%d candidate 에 URL 발견)",
        len(brave_urls_by_candidate),
    )

    finished_at = datetime.now(timezone.utc).isoformat()
    step: AgentStep = {
        "step_name":   "UrlDiscoveryBrave",
        "status":      "completed",
        "started_at":  started_at,
        "finished_at": finished_at,
    }
    return {
        "brave_urls_by_candidate": brave_urls_by_candidate,
        "agent_steps":             [step],
    }
