"""
server/graph/nodes/cross_reference_node.py (v0.10.26 실 구현)
-------------------------------------------------------------
5중 fan-out 1차 list-fan-in barrier 직후의 결정론적 후처리 필터링 노드.

역할 (turn-7 의도, turn-11 옵션 (e) 별도 노드로 이관)
-----------------------------------------------------
`youtube_reactions_urls_by_candidate` 의 영상 중 `owned_channel_urls_by_candidate`
의 `platform="youtube_official"` 항목의 `channel_id` 와 일치하는 영상을 제외한다.

이를 통해 **자사·경쟁사가 직접 운영하는 공식 YouTube 채널이 자체 상품을 직접
리뷰하는 영상** 이 reaction_insight 분석 풀에 잘못 포함되는 edge case 를 차단한다.

**머지가 아닌 후처리 필터링**이며, owned_channels 결과는 변경 없이 그대로 통과
한다. youtube_reactions 결과만 축소된다. 다른 3종 키(`official`·`blog_community`·
`macro`)는 본 노드의 상태 변경 대상이 아니므로 LangGraph reducer (replace) 로
자동 carry-through 된다.

위치 (v0.10.26 토폴로지)
------------------------
ab_join
  ├─→ url_discovery_official_node           ┐
  ├─→ url_discovery_blog_community_node     │
  ├─→ url_discovery_youtube_reactions_node  │  5중 fan-out (1차)
  ├─→ url_discovery_owned_channels_node     │
  └─→ url_discovery_macro_node              ┘
        ↓  list-fan-in barrier (1차)
      [cross_reference_node]   ← 이 노드 (v0.10.26 신설)
        ↓
      urls_merge_node          (v0.10.19 임시 어댑터 — v0.10.27 도입 시 폐기)
        ↓
      page_meta_collect_node → feature_mapping_llm_node → ...

입력 state 키
-------------
- youtube_reactions_urls_by_candidate : dict[candidate_id, list[video_dict]]
- owned_channel_urls_by_candidate     : dict[candidate_id, list[channel_dict]]
  (다른 3종 키는 본 노드가 읽지 않음 — LangGraph 가 carry)

출력 state 키
-------------
- youtube_reactions_urls_by_candidate : 필터링 후 갱신 (replace)
- video_candidate_index               : {video_id: [candidate_ids]} 역인덱스 (replace)
                                        owned 필터 통과 영상만 포함. multi-tagging 에 사용.
- agent_steps                          : 누적 reducer

캐싱
----
본 노드는 캐시 미사용. 입력 state 가 두 노드 결과의 함수이므로 별도 캐싱 가치 없음.
wall-clock 약 10ms (set 추출 + list comprehension 만).

향후 확장
--------
다른 source-type 간 cross-reference 룰이 추가되면 본 노드에 함수 분기 누적
(예: blog_community × owned_channels(blog_*) 매칭). 별도 cross_reference_<rule>
노드는 추가하지 않는다 (toplogy 폭주 회피).

graceful 종료
-------------
- youtube_reactions 빈 dict: 빈 결과 + status="completed"
- owned_channels 빈 dict 또는 youtube_official channel_id 0건: reactions 그대로 통과
- 모든 정상 경로에서 결정론적 — 실패 가능 분기 없음
"""

import logging
from datetime import datetime, timezone

from server.graph.progress_store import set_progress
from server.graph.state import DomainAnalysisState, AgentStep

logger = logging.getLogger(__name__)


def cross_reference_node(state: DomainAnalysisState, config: dict | None = None) -> dict:
    """v0.10.26 실 구현 — youtube_reactions × owned_channels(youtube_official) cross-reference."""
    started_at = datetime.now(timezone.utc).isoformat()
    thread_id  = (config or {}).get("configurable", {}).get("thread_id", "")

    print(
        f"🔗 [cross_reference_node] ENTRY at {started_at} thread_id={thread_id!r}",
        flush=True,
    )

    if thread_id:
        try:
            set_progress(
                thread_id, "cross_reference",
                detail="owned channels × youtube_reactions cross-reference 필터링",
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("set_progress(cross_reference) 실패: %s", exc)

    reactions = state.get("youtube_reactions_urls_by_candidate") or {}
    owned     = state.get("owned_channel_urls_by_candidate") or {}

    # owned_channel 의 youtube_official channel_id 집합 추출
    owned_yt_channel_ids: set[str] = {
        it["channel_id"]
        for items in owned.values()
        for it in items
        if it.get("platform") == "youtube_official" and it.get("channel_id")
    }

    # reactions 영상 중 owned channel_id 일치 항목 제외 + video_candidate_index 동시 구축
    filtered: dict[str, list[dict]] = {}
    video_candidate_index: dict[str, list[str]] = {}   # video_id → [candidate_ids]
    excluded_count = 0
    for cand_id, items in reactions.items():
        kept: list[dict] = []
        for v in items:
            if v.get("channel_id") in owned_yt_channel_ids:
                excluded_count += 1
                continue
            kept.append(v)
            # video_candidate_index 구축 (kept 영상만 — set으로 중복 방지)
            vid = v.get("video_id", "")
            if vid:
                existing = video_candidate_index.get(vid)
                if existing is None:
                    video_candidate_index[vid] = [cand_id]
                elif cand_id not in existing:
                    existing.append(cand_id)
        if kept:
            filtered[cand_id] = kept

    total_in  = sum(len(v) for v in reactions.values())
    total_out = sum(len(v) for v in filtered.values())
    multi_candidate_videos = sum(1 for cids in video_candidate_index.values() if len(cids) > 1)
    logger.info(
        "cross_reference_node: owned_yt_channels=%d → reactions %d → %d (%d 제외), "
        "video_candidate_index=%d videos (%d multi-candidate)",
        len(owned_yt_channel_ids), total_in, total_out, excluded_count,
        len(video_candidate_index), multi_candidate_videos,
    )

    finished_at = datetime.now(timezone.utc).isoformat()
    step: AgentStep = {
        "step_name":   "CrossReference",
        "status":      "completed",
        "started_at":  started_at,
        "finished_at": finished_at,
    }
    return {
        "youtube_reactions_urls_by_candidate": filtered,
        "video_candidate_index":               video_candidate_index,
        "agent_steps":                          [step],
    }
