"""
server/graph/nodes/url_discovery_owned_channels_node.py (v0.10.19 스켈레톤)
--------------------------------------------------------------------------
5중 fan-out 중 source-type 4번 — marketing_social 자사·경쟁사 운영 채널 탐색 (스켈레톤).

v0.10.19 단계 상태
------------------
**스켈레톤만 — 빈 dict 반환**. 실 구현은 v0.10.21 에서 Brave 검색 + LLM 검증
(`official_source_resolver` 패턴 재사용) + YouTube `channels.list` 호출 통합.

본 노드의 v0.10.21 이후 책임:
  - candidate 별 platform 5종(Instagram · X · 블로그 · 보도자료 · YouTube 공식 채널)
    Brave 쿼리 + LLM 검증 + verified 시그널 확인
  - LLM 검증 결과의 confidence 0.7 미만은 needs_validation=True
  - account_scope (parent_company · sub_brand · product_specific · regional) 분류

v0.10.19 단계 동작:
  - 빈 `owned_channel_urls_by_candidate` 반환
  - `agent_steps` 에 status="skipped" + step_name="UrlDiscoveryOwnedChannels"
  - error_message="not_implemented: v0.10.21 Brave + LLM 검증 + youtube_official 대기"

위치 (v0.10.19 토폴로지 1차 fan-out)
------------------------------------
ab_join
  ├─→ url_discovery_official_node
  ├─→ url_discovery_blog_community_node
  ├─→ url_discovery_youtube_reactions_node
  ├─→ [url_discovery_owned_channels_node]   ← 이 노드 (스켈레톤)
  └─→ url_discovery_macro_node
"""

import logging
from datetime import datetime, timezone

from server.graph.progress_store import set_progress
from server.graph.state import DomainAnalysisState, AgentStep

logger = logging.getLogger(__name__)


def url_discovery_owned_channels_node(
    state: DomainAnalysisState, config: dict | None = None,
) -> dict:
    """
    v0.10.19 스켈레톤 — Brave + LLM 검증 + YouTube channels.list 미통합으로 빈 결과 반환.

    Returns
    -------
    dict
        {owned_channel_urls_by_candidate: {}, agent_steps: [skipped]}
    """
    started_at = datetime.now(timezone.utc).isoformat()
    thread_id  = (config or {}).get("configurable", {}).get("thread_id", "")

    print(
        f"📱 [url_discovery_owned_channels_node] SKELETON ENTRY at {started_at} "
        f"thread_id={thread_id!r}",
        flush=True,
    )

    if thread_id:
        try:
            set_progress(
                thread_id, "feature_mapping_brave",
                detail="Owned channels 탐색 (v0.10.21 대기 — 빈 결과 반환)",
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("set_progress(owned_channels) 실패: %s", exc)

    logger.info(
        "url_discovery_owned_channels_node: SKELETON — Brave + LLM 검증 미통합. "
        "빈 결과 반환 (v0.10.21 에서 5 platforms 실 구현 예정)",
    )

    finished_at = datetime.now(timezone.utc).isoformat()
    step: AgentStep = {
        "step_name":     "UrlDiscoveryOwnedChannels",
        "status":        "skipped",
        "started_at":    started_at,
        "finished_at":   finished_at,
        "error_message": "not_implemented: v0.10.21 Brave + LLM 검증 + youtube_official 대기",
    }
    return {
        "owned_channel_urls_by_candidate": {},
        "agent_steps":                     [step],
    }
