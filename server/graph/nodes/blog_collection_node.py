"""
server/graph/nodes/blog_collection_node.py
------------------------------------------
blog 계열(personal_blog / review_site / wiki) 게시글 본문 수집 노드.

2026-06-11 분리: 기존 community_collection_node 가 blog_community 전체(블로그+커뮤니티)를
수집하던 것을, 사용자 요청으로 채널을 분리했다. 본 노드는 blog 계열만 담당하며,
**현재 그래프에 미배선(휴면)** 상태다 — 활성 파이프라인은 커뮤니티만 수집한다.
재활성화하려면 graph.py 의 주석 처리된 배선부를 해제한다.

수집 로직은 community_collection_node 의 `_collect_posts` 코어를 그대로 재사용하고,
domain_class 집합(`_BLOG_CLASSES`)과 출력 키(`blog_posts`)만 다르다.

write keys
----------
- blog_posts : [{url, candidate_id, feature_ids, domain_class, title,
                 body_excerpt, published_at, fetch_status}]
- agent_steps / errors (누적 reducer)
"""

from __future__ import annotations

from server.graph.state import DomainAnalysisState
from server.graph.nodes.community_collection_node import _collect_posts, _BLOG_CLASSES


def blog_collection_node(
    state: DomainAnalysisState, config: dict | None = None
) -> dict:
    """blog 계열(personal_blog/review_site/wiki) 게시글 본문 수집 (미배선/휴면)."""
    return _collect_posts(
        state, config,
        accept_classes=_BLOG_CLASSES, write_key="blog_posts",
        step_name="BlogCollection", node_name="blog_collection_node",
        progress_detail="블로그·리뷰 게시글 수집",
    )
