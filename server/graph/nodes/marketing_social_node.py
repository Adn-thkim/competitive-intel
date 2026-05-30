"""
server/graph/nodes/marketing_social_node.py
-------------------------------------------
**§6-4 D1=B 분리형, v0.10 스켈레톤** — `marketing_social` 리포트 노드.

흐름 분류 (§11-10 표 기준)
--------------------------
- 흐름 A: ✓ (3채널 fan-in — youtube_channel_metadata + blog_rss_posts + pr_releases)
- 흐름 B: ✗
- LangGraph 배치: **leaf** — 3개 수집 노드 모두 완료 후 실행.

read keys
---------
- `domain_taxonomy.report_config["marketing_social"]`
    - `categories` (Rubric §2-3 표준 7종 중 채택분 — PESO·Channel Operations·Engagement·...)
- `youtube_channel_metadata` (§6-6a youtube_channel_metadata_collection_node 산출, 미구현)
- `blog_rss_posts`            (§6-6a blog_rss_collection_node 산출, 미구현)
- `pr_releases`               (§6-6a pr_release_collection_node 산출, 미구현)

write keys
----------
- `report_outputs["marketing_social"]` — Rubric §2-3 평가 루브릭 1–5점 기준 envelope.

content 구조 (Rubric §2-3 권장)
-------------------------------
- `peso_classification`: Paid/Owned/Shared/Earned 4분면 채널 매핑.
- `channel_matrix`: 4-tuple `(channel, posting_frequency, audience_size, top_keywords)`.
  - `engagement` = `interactions ÷ followers` 표준 (§1-3 결정).
- `channel_keyword_crosstab`: 채널 × 키워드 분포 cross-tab.
- `coverage_gap`: 자사 미점유 채널·메시지 공백 식별.
- `seasonality_correction`: 동일 기간 정렬(예: 5개월 이동 평균).

흐름 B 인용 대상
----------------
본 노드 산출은 battlecard·market_context_swot·executive_summary가 흐름 B로 inline 인용.

상태
----
**SCAFFOLD ONLY** — 3개 수집 노드의 출력 형식이 확정되어야 LLM 호출 로직 작성 가능.
"""

from __future__ import annotations

from datetime import datetime, timezone

from server.graph.state import DomainAnalysisState
from server.graph.nodes._report_node_common import (
    is_report_active,
    make_error_result,
    make_skip_result,
)

REPORT_TYPE = "marketing_social"


def marketing_social_node(state: DomainAnalysisState) -> dict:
    """v0.10 D1=B 분리형 — marketing_social 리포트 생성 (스켈레톤)."""
    started_at = datetime.now(timezone.utc).isoformat()

    if not is_report_active(state, REPORT_TYPE):
        return make_skip_result(REPORT_TYPE, started_at)

    # TODO (§6-6a 3개 수집 노드 산출 형식 확정 후 구현):
    # 1. youtube_channel_metadata + blog_rss_posts + pr_releases 통합
    # 2. PESO 4분면 분류 + 채널별 engagement(interactions ÷ followers) 계산
    # 3. 채널 × 키워드 cross-tab + 시즌성 보정
    # 4. battlecard B-4와 정렬되는 채널 매트릭스 (Rubric §2-3 5점 조건)
    # 5. AP-4·AP-8·AP-9 회피 검증
    # 6. build_report_envelope(...) → report_outputs["marketing_social"]
    return make_error_result(
        REPORT_TYPE, started_at,
        "marketing_social_node §6-4 스켈레톤 — §6-6a 3채널 수집 노드 산출 형식 확정 후 구현 필요.",
    )
