"""
server/graph/nodes/url_discovery_blog_community_node.py (v0.10.19)
------------------------------------------------------------------
5중 fan-out 중 source-type 2번 — 외부 후기·블로그·커뮤니티 URL 탐색.

역할
----
domain_taxonomy.report_config["reaction_insight"] 의 search_query_hints 만 사용하여
Brave 검색 수행. 발견된 URL 중 외부 도메인(공식 사이트 제외) 위주로 활용되도록
matched_report_types 에 "reaction_insight" 만 부여.

v0.10.19 단계 한계:
- 외부 도메인 화이트리스트/필터링 미적용 — 모든 Brave 결과를 그대로 반환
- v0.10.23 의 system_prompt 분배 + v0.10.27 통합 노드에서 source-type 별 정책으로 정밀화

위치 (v0.10.19 토폴로지 1차 fan-out)
------------------------------------
ab_join
  ├─→ url_discovery_official_node
  ├─→ [url_discovery_blog_community_node]   ← 이 노드
  ├─→ url_discovery_youtube_reactions_node
  ├─→ url_discovery_owned_channels_node
  └─→ url_discovery_macro_node

출력 state 키
-------------
- blog_community_urls_by_candidate : dict[candidate_id, list[dict]]
- agent_steps                      : 누적 reducer
"""

import logging
from datetime import datetime, timezone

from server.graph.progress_store import set_progress
from server.graph.state import DomainAnalysisState, AgentStep
from server.graph.nodes.feature_url_mapper_node import (
    _discover_via_brave_with_hints,
    _extract_active_reports,
    _extract_hints_for_source,
    _error,
)

logger = logging.getLogger(__name__)

# v0.10.19.1 — D18 옵션 (a) 채택. 헬퍼 모듈의 _LEGACY_SOURCE_TO_REPORT_TYPES 가 후방 호환 처리.
_SOURCE_TYPE = "blog_community"


def url_discovery_blog_community_node(
    state: DomainAnalysisState, config: dict | None = None,
) -> dict:
    """
    외부 후기·블로그·커뮤니티 URL 탐색 (Brave Search API).

    Returns
    -------
    dict
        {blog_community_urls_by_candidate, agent_steps} 또는 {errors, agent_steps}
    """
    started_at = datetime.now(timezone.utc).isoformat()
    thread_id  = (config or {}).get("configurable", {}).get("thread_id", "")

    print(
        f"📝 [url_discovery_blog_community_node] ENTRY at {started_at} thread_id={thread_id!r}",
        flush=True,
    )

    if thread_id:
        try:
            set_progress(
                thread_id, "feature_mapping_brave",
                detail="블로그·커뮤니티 후기 URL 탐색 (reaction_insight)",
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("set_progress(blog_community) 실패: %s", exc)

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

    # v0.10.19.1 — source_hint="blog_community" 인 hints 만 추출 (객체 양식 + string 후방 호환)
    all_active = _extract_active_reports(domain_taxonomy)
    hints_with_meta = _extract_hints_for_source(all_active, _SOURCE_TYPE)

    if not hints_with_meta:
        logger.info(
            "url_discovery_blog_community_node: source_hint='blog_community' 인 hint 가 없습니다 — 빈 결과 반환",
        )
        finished_at = datetime.now(timezone.utc).isoformat()
        return {
            "blog_community_urls_by_candidate": {},
            "agent_steps": [{
                "step_name":   "UrlDiscoveryBlogCommunity",
                "status":      "completed",
                "started_at":  started_at,
                "finished_at": finished_at,
            }],
        }

    # ── Brave 검색 (v0.10.19.1 신설 헬퍼) ────────────────────────────────────
    logger.info(
        "url_discovery_blog_community_node: Brave 검색 시작 (hints=%d개)",
        len(hints_with_meta),
    )
    urls_by_candidate = _discover_via_brave_with_hints(
        hints_with_meta=hints_with_meta,
        own_product=own_product,
        competitor_candidates=competitor_candidates,
        selected_ids=selected_ids,
        domain_name=domain_name,
    )
    total = sum(len(v) for v in urls_by_candidate.values())
    logger.info(
        "url_discovery_blog_community_node: 완료 (%d candidate 에 URL %d개 발견)",
        len(urls_by_candidate), total,
    )

    finished_at = datetime.now(timezone.utc).isoformat()
    step: AgentStep = {
        "step_name":   "UrlDiscoveryBlogCommunity",
        "status":      "completed",
        "started_at":  started_at,
        "finished_at": finished_at,
    }
    return {
        "blog_community_urls_by_candidate": urls_by_candidate,
        "agent_steps":                     [step],
    }
