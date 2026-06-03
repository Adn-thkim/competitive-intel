"""
server/graph/nodes/urls_merge_node.py (v0.10.19 임시 어댑터)
-----------------------------------------------------------
5중 fan-out 직후 list-fan-in barrier 에 위치한 임시 머지 노드.

역할
----
5개 source-type URL 탐색 노드의 결과를 단일 `brave_urls_by_candidate` 로 union 머지
하여 기존 `page_meta_collect_node` → `feature_mapping_llm_node` → `additional_urls_
validation_node` 3단계가 변경 없이 동작하도록 호환 어댑터를 제공한다.

머지 정책 (v0.10.19 임시)
-------------------------
- 동일 candidate_id 의 URL 들을 concatenate
- URL 단위 dedup (동일 URL 발견 시 첫 등장 source-type 유지)
- `matched_report_types` 는 모든 source-type 의 union 으로 보존
- cross-reference / 우선순위 정책은 적용하지 않음 (v0.10.26 cross_reference_node 에서 처리)

폐기 시점
---------
v0.10.26 PR 진입 시 본 노드는 `cross_reference_node` 로 교체되며 파일 삭제.
그 후 v0.10.27 의 5개 통합 노드(`feature_mapping_<source>_node`)가 5개 source-type
키를 **직접 read** 하므로 단일 `brave_urls_by_candidate` 어댑터 자체가 불필요.

위치 (v0.10.19 토폴로지)
-------------------------
  url_discovery_official_node          ┐
  url_discovery_blog_community_node    │
  url_discovery_youtube_reactions_node │  5중 fan-in
  url_discovery_owned_channels_node    │  (list-edge barrier)
  url_discovery_macro_node             ┘
                  ↓
       [urls_merge_node]   ← 이 노드 (임시)
                  ↓
       page_meta_collect_node  (기존 v0.10.9 그대로)

입력 state 키
-------------
- official_urls_by_candidate
- blog_community_urls_by_candidate
- youtube_reactions_urls_by_candidate
- owned_channel_urls_by_candidate
- macro_urls_by_candidate

출력 state 키
-------------
- brave_urls_by_candidate : dict[candidate_id, list[dict]]  (기존 키로 union)
- agent_steps             : 누적 reducer
"""

import logging
from datetime import datetime, timezone

from server.graph.progress_store import set_progress
from server.graph.state import DomainAnalysisState, AgentStep

logger = logging.getLogger(__name__)


# 머지 순서 (동일 URL 의 origin 충돌 시 앞순위 우선)
_SOURCE_KEYS_IN_ORDER: tuple[str, ...] = (
    "official_urls_by_candidate",
    "blog_community_urls_by_candidate",
    "youtube_reactions_urls_by_candidate",
    "owned_channel_urls_by_candidate",
    "macro_urls_by_candidate",
)


def urls_merge_node(state: DomainAnalysisState, config: dict | None = None) -> dict:
    """
    5개 source-type URL 결과를 단일 brave_urls_by_candidate 로 union 머지.

    Returns
    -------
    dict
        {brave_urls_by_candidate, agent_steps}
    """
    started_at = datetime.now(timezone.utc).isoformat()
    thread_id  = (config or {}).get("configurable", {}).get("thread_id", "")

    print(
        f"🔗 [urls_merge_node] ENTRY at {started_at} thread_id={thread_id!r}",
        flush=True,
    )

    if thread_id:
        try:
            set_progress(
                thread_id, "feature_mapping_brave",
                detail="5 source-type URL 머지 (v0.10.19 임시 어댑터)",
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("set_progress(urls_merge) 실패: %s", exc)

    # ── union 머지 + URL 단위 dedup ──────────────────────────────────────────
    merged: dict[str, list[dict]] = {}
    seen_urls_per_candidate: dict[str, set[str]] = {}

    per_source_counts: dict[str, int] = {}
    for source_key in _SOURCE_KEYS_IN_ORDER:
        source_dict = state.get(source_key) or {}
        count = 0
        for cand_id, items in source_dict.items():
            if not isinstance(items, list):
                continue
            seen = seen_urls_per_candidate.setdefault(cand_id, set())
            merged_list = merged.setdefault(cand_id, [])
            for item in items:
                url = (item.get("url") or "").strip()
                if not url:
                    continue
                if url in seen:
                    # 동일 URL 이 다른 source-type 에서 또 발견된 경우 — matched_report_types 만 union
                    for existing in merged_list:
                        if existing.get("url") == url:
                            existing_types = set(existing.get("matched_report_types") or [])
                            new_types      = set(item.get("matched_report_types") or [])
                            existing["matched_report_types"] = sorted(existing_types | new_types)
                            break
                    continue
                seen.add(url)
                merged_list.append(dict(item))
                count += 1
        per_source_counts[source_key] = count

    total = sum(len(v) for v in merged.values())
    logger.info(
        "urls_merge_node: 완료 (총 %d candidate, URL %d개) — per source: %s",
        len(merged), total, per_source_counts,
    )

    finished_at = datetime.now(timezone.utc).isoformat()
    step: AgentStep = {
        "step_name":   "UrlsMerge",
        "status":      "completed",
        "started_at":  started_at,
        "finished_at": finished_at,
    }
    return {
        "brave_urls_by_candidate": merged,
        "agent_steps":             [step],
    }
