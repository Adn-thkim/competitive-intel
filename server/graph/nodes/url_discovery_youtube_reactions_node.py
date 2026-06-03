"""
server/graph/nodes/url_discovery_youtube_reactions_node.py (v0.10.19 스켈레톤)
-----------------------------------------------------------------------------
5중 fan-out 중 source-type 3번 — reaction_insight YouTube 영상 탐색 (스켈레톤).

v0.10.19 단계 상태
------------------
**스켈레톤만 — 빈 dict 반환**. 실 구현은 v0.10.20 에서 YouTube Data API v3 통합 후 진행.

본 노드의 v0.10.20 이후 책임:
  - YouTube Data API v3 `search.list?q="{candidate_name} 후기"&type=video` 호출
  - 영상 metadata 만 저장 (댓글은 §6-6a `youtube_collection_node` 가 후속 수집)
  - quota 관리 + cross_reference_node(v0.10.26) 가 owned channel 영상 자동 제외

v0.10.19 단계 동작:
  - 빈 `youtube_reactions_urls_by_candidate` 반환
  - `agent_steps` 에 status="skipped" + step_name="UrlDiscoveryYoutubeReactions"
  - error_message="not_implemented: v0.10.20 YouTube Data API v3 통합 대기"

위치 (v0.10.19 토폴로지 1차 fan-out)
------------------------------------
ab_join
  ├─→ url_discovery_official_node
  ├─→ url_discovery_blog_community_node
  ├─→ [url_discovery_youtube_reactions_node]   ← 이 노드 (스켈레톤)
  ├─→ url_discovery_owned_channels_node
  └─→ url_discovery_macro_node
"""

import logging
from datetime import datetime, timezone

from server.graph.progress_store import set_progress
from server.graph.state import DomainAnalysisState, AgentStep

logger = logging.getLogger(__name__)


def url_discovery_youtube_reactions_node(
    state: DomainAnalysisState, config: dict | None = None,
) -> dict:
    """
    v0.10.19 스켈레톤 — YouTube Data API v3 미통합 상태로 빈 결과 반환.

    Returns
    -------
    dict
        {youtube_reactions_urls_by_candidate: {}, agent_steps: [skipped]}
    """
    started_at = datetime.now(timezone.utc).isoformat()
    thread_id  = (config or {}).get("configurable", {}).get("thread_id", "")

    print(
        f"▶️  [url_discovery_youtube_reactions_node] SKELETON ENTRY at {started_at} "
        f"thread_id={thread_id!r}",
        flush=True,
    )

    if thread_id:
        try:
            set_progress(
                thread_id, "feature_mapping_brave",
                detail="YouTube reactions 영상 탐색 (v0.10.20 대기 — 빈 결과 반환)",
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("set_progress(youtube_reactions) 실패: %s", exc)

    logger.info(
        "url_discovery_youtube_reactions_node: SKELETON — YouTube Data API v3 미통합. "
        "빈 결과 반환 (v0.10.20 에서 실 구현 예정)",
    )

    finished_at = datetime.now(timezone.utc).isoformat()
    step: AgentStep = {
        "step_name":     "UrlDiscoveryYoutubeReactions",
        "status":        "skipped",
        "started_at":    started_at,
        "finished_at":   finished_at,
        "error_message": "not_implemented: v0.10.20 YouTube Data API v3 통합 대기",
    }
    return {
        "youtube_reactions_urls_by_candidate": {},
        "agent_steps":                          [step],
    }
