"""
server/graph/nodes/url_discovery_official_node.py (v0.10.19)
------------------------------------------------------------
5중 fan-out 중 source-type 1번 — 공식 사이트 + 매체 비교 URL 탐색.

역할
----
domain_taxonomy.report_config 의 다음 report_type 의 search_query_hints 만 사용하여
Brave Search API 호출:
  - comparison_matrix
  - battlecard       (A Fact 부분)
  - market_context_swot  (규제 부분)

기존 `_discover_via_brave` 헬퍼(feature_url_mapper_node 모듈) 재사용. 단순히
active_reports 필터링만 본 노드가 책임.

위치 (v0.10.19 토폴로지 1차 fan-out)
------------------------------------
ab_join
  ├─→ [url_discovery_official_node]   ← 이 노드
  ├─→ url_discovery_blog_community_node
  ├─→ url_discovery_youtube_reactions_node
  ├─→ url_discovery_owned_channels_node
  └─→ url_discovery_macro_node
        ↓  list-fan-in barrier
      urls_merge_node               (v0.10.19 임시 어댑터)
        ↓
      page_meta_collect_node        (기존 v0.10.9 그대로)
        ↓ ...

입력 state 키
-------------
- domain_taxonomy   : DomainTaxonomyAgent 산출
- own_product
- competitor_candidates / selected_competitor_ids
- domain_name

출력 state 키
-------------
- official_urls_by_candidate : dict[candidate_id, list[dict]]
- agent_steps                : 누적 reducer
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


# v0.10.19 — source-type 별 담당 report_type 매핑 (D18 임시 처리, 본 PR 의사 결정 §4-1)
_OFFICIAL_REPORT_TYPES: tuple[str, ...] = (
    "comparison_matrix",
    "battlecard",
    "market_context_swot",
)


def url_discovery_official_node(state: DomainAnalysisState, config: dict | None = None) -> dict:
    """
    공식 사이트 + 매체 비교 URL 탐색 (Brave Search API).

    Returns
    -------
    dict
        {official_urls_by_candidate, agent_steps} 또는 {errors, agent_steps}
    """
    started_at = datetime.now(timezone.utc).isoformat()
    thread_id  = (config or {}).get("configurable", {}).get("thread_id", "")

    print(
        f"🏛️  [url_discovery_official_node] ENTRY at {started_at} thread_id={thread_id!r}",
        flush=True,
    )

    if thread_id:
        try:
            set_progress(
                thread_id, "feature_mapping_brave",
                detail="공식 사이트 URL 탐색 (comparison_matrix · battlecard · market_context_swot)",
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("set_progress(official) 실패: %s", exc)

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

    # active=true + source_flow ∈ {A, A+B} 인 7종(현재 5종) 중 본 source 담당 3종만 필터
    all_active = _extract_active_reports(domain_taxonomy)
    active_official = {
        rt: entry for rt, entry in all_active.items()
        if rt in _OFFICIAL_REPORT_TYPES
    }

    if not active_official:
        logger.info(
            "url_discovery_official_node: 담당 report_type %s 가 모두 비활성/B-only — 빈 결과 반환",
            _OFFICIAL_REPORT_TYPES,
        )
        finished_at = datetime.now(timezone.utc).isoformat()
        return {
            "official_urls_by_candidate": {},
            "agent_steps": [{
                "step_name":   "UrlDiscoveryOfficial",
                "status":      "completed",
                "started_at":  started_at,
                "finished_at": finished_at,
            }],
        }

    # ── Brave 검색 실행 (기존 헬퍼 재사용) ───────────────────────────────────
    logger.info(
        "url_discovery_official_node: Brave 검색 시작 "
        "(담당 report_type=%d개 — %s)",
        len(active_official), sorted(active_official.keys()),
    )
    urls_by_candidate = _discover_via_brave(
        active_reports=active_official,
        own_product=own_product,
        competitor_candidates=competitor_candidates,
        selected_ids=selected_ids,
        domain_name=domain_name,
    )
    total = sum(len(v) for v in urls_by_candidate.values())
    logger.info(
        "url_discovery_official_node: 완료 (%d candidate 에 URL %d개 발견)",
        len(urls_by_candidate), total,
    )

    finished_at = datetime.now(timezone.utc).isoformat()
    step: AgentStep = {
        "step_name":   "UrlDiscoveryOfficial",
        "status":      "completed",
        "started_at":  started_at,
        "finished_at": finished_at,
    }
    return {
        "official_urls_by_candidate": urls_by_candidate,
        "agent_steps":                [step],
    }
