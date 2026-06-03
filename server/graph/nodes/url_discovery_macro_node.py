"""
server/graph/nodes/url_discovery_macro_node.py (v0.10.19)
---------------------------------------------------------
5중 fan-out 중 source-type 5번 — 매크로 데이터 URL 탐색.

역할
----
domain_taxonomy.report_config["market_context_swot"] 의 search_query_hints 만 사용하여
Brave 검색 수행. 정부 통계 · 산업 보고서 · 트레이드 미디어 URL 발견.

v0.10.19 단계 한계:
- 도메인 화이트리스트(kosis.kr · bok.or.kr · nia.or.kr · statista.com 등) 미적용
- 기존 _discover_via_brave 헬퍼 그대로 재사용
- v0.10.22 에서 도메인 화이트리스트 + 발행일 ≤ 24개월 필터 보강

위치 (v0.10.19 토폴로지 1차 fan-out)
------------------------------------
ab_join
  ├─→ url_discovery_official_node
  ├─→ url_discovery_blog_community_node
  ├─→ url_discovery_youtube_reactions_node
  ├─→ url_discovery_owned_channels_node
  └─→ [url_discovery_macro_node]   ← 이 노드

출력 state 키
-------------
- macro_urls_by_candidate : dict[candidate_id, list[dict]]
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

_MACRO_REPORT_TYPES: tuple[str, ...] = (
    "market_context_swot",
)


def url_discovery_macro_node(state: DomainAnalysisState, config: dict | None = None) -> dict:
    """
    매크로 데이터 URL 탐색 (Brave Search API).

    Returns
    -------
    dict
        {macro_urls_by_candidate, agent_steps} 또는 {errors, agent_steps}
    """
    started_at = datetime.now(timezone.utc).isoformat()
    thread_id  = (config or {}).get("configurable", {}).get("thread_id", "")

    print(
        f"📊 [url_discovery_macro_node] ENTRY at {started_at} thread_id={thread_id!r}",
        flush=True,
    )

    if thread_id:
        try:
            set_progress(
                thread_id, "feature_mapping_brave",
                detail="매크로 데이터 URL 탐색 (market_context_swot)",
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("set_progress(macro) 실패: %s", exc)

    # ── 입력 수집 ───────────────────────────────────────────────────────────
    domain_taxonomy: dict       = state.get("domain_taxonomy") or {}
    domain_name: str            = state.get("domain_name") or ""
    own_product: dict           = state.get("own_product") or {}
    competitor_candidates: list = state.get("competitor_candidates") or []
    selected_ids: list[str]     = state.get("selected_competitor_ids") or []

    if not domain_taxonomy:
        return _error(started_at, "domain_taxonomy 가 state 에 없습니다.")
    if not domain_name:
        return _error(started_at, "domain_name 이 state 에 없습니다.")

    all_active = _extract_active_reports(domain_taxonomy)
    active_macro = {
        rt: entry for rt, entry in all_active.items()
        if rt in _MACRO_REPORT_TYPES
    }

    if not active_macro:
        logger.info(
            "url_discovery_macro_node: market_context_swot 비활성/B-only — 빈 결과 반환",
        )
        finished_at = datetime.now(timezone.utc).isoformat()
        return {
            "macro_urls_by_candidate": {},
            "agent_steps": [{
                "step_name":   "UrlDiscoveryMacro",
                "status":      "completed",
                "started_at":  started_at,
                "finished_at": finished_at,
            }],
        }

    # ── Brave 검색 (기존 헬퍼 재사용) ────────────────────────────────────────
    logger.info("url_discovery_macro_node: Brave 검색 시작 (market_context_swot)")
    urls_by_candidate = _discover_via_brave(
        active_reports=active_macro,
        own_product=own_product,
        competitor_candidates=competitor_candidates,
        selected_ids=selected_ids,
        domain_name=domain_name,
    )
    total = sum(len(v) for v in urls_by_candidate.values())
    logger.info(
        "url_discovery_macro_node: 완료 (%d candidate 에 URL %d개 발견)",
        len(urls_by_candidate), total,
    )

    finished_at = datetime.now(timezone.utc).isoformat()
    step: AgentStep = {
        "step_name":   "UrlDiscoveryMacro",
        "status":      "completed",
        "started_at":  started_at,
        "finished_at": finished_at,
    }
    return {
        "macro_urls_by_candidate": urls_by_candidate,
        "agent_steps":             [step],
    }
