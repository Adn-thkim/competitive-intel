"""
server/graph/nodes/reaction_insight_node.py
-------------------------------------------
**§6-4 D1=B 분리형, v0.10 스켈레톤** — `reaction_insight` 리포트 노드.

흐름 분류 (§11-10 표 기준)
--------------------------
- 흐름 A: ✓ (reaction_analysis 산출 — ABSA 7-tuple 분석 결과)
- 흐름 B: ✗
- LangGraph 배치: **leaf** — `reaction_analysis_node` 완료 직후 독립 실행.

read keys
---------
- `domain_taxonomy.report_config["reaction_insight"]`
    - `aspect_codebook` (DomainTaxonomyAgent 자동 생성, 3–12개 ABSA aspect)
    - `categories` (Rubric §2-2 표준 7종 중 채택분)
- `reaction_analysis` (reaction_analysis_node 산출, 미구현)
    - 구조 추정: `[{aspect, polarity, intensity, quote, source_url, channel, posted_at}]` 7-tuple

write keys
----------
- `report_outputs["reaction_insight"]` — Rubric §2-2 평가 루브릭 1–5점 기준 envelope.

content 구조 (Rubric §2-2 권장)
-------------------------------
- `aspect_sentiment_matrix`: aspect × channel cross-tab.
- `representative_quotes`: 각 채널별 1–2개 원문 quote (번역·요약 금지).
- `suggestion_list`: product_dev suggestion 후보 별도 분리 (§1-4 6 카테고리 표준).
- `nps_proxy`: positive 비율 → comparison_matrix와 상관 분석.
- `travel_seasonal_split`: 여행지/시점 분리 뷰 (D11 비활성으로 채널 2종 기준).

D11 확정 영향 (v0.8)
-------------------
- 활성 채널: YouTube + 커뮤니티 (2채널).
- 평가 루브릭 4점 기준: "2채널 cross-validation" 충족.
- 5점은 (4점 요건 + 채널 가중치 + 시점 분리 뷰 + suggestion 분리).

상태
----
**SCAFFOLD ONLY** — `reaction_analysis_node`(§6-6a 신규 수집 + reaction_analysis_node)가
산출하는 7-tuple 형식이 확정되어야 LLM 호출 로직을 작성할 수 있다.
"""

from __future__ import annotations

from datetime import datetime, timezone

from server.graph.state import DomainAnalysisState
from server.graph.nodes._report_node_common import (
    is_report_active,
    make_error_result,
    make_skip_result,
)

REPORT_TYPE = "reaction_insight"


def reaction_insight_node(state: DomainAnalysisState) -> dict:
    """v0.10 D1=B 분리형 — reaction_insight 리포트 생성 (스켈레톤)."""
    started_at = datetime.now(timezone.utc).isoformat()

    if not is_report_active(state, REPORT_TYPE):
        return make_skip_result(REPORT_TYPE, started_at)

    # TODO (§6-6a reaction_analysis 산출 형식 확정 후 구현):
    # 1. domain_taxonomy.report_config["reaction_insight"]에서 aspect_codebook·categories 로드
    # 2. reaction_analysis 7-tuple → aspect × channel cross-tab 작성
    # 3. suggestion 카테고리 별도 분리 (Rubric §2-2 5점 조건)
    # 4. AP-3·AP-5·AP-8 회피 검증
    # 5. build_report_envelope(...) → report_outputs["reaction_insight"]
    return make_error_result(
        REPORT_TYPE, started_at,
        "reaction_insight_node §6-4 스켈레톤 — §6-6a reaction_analysis 산출 형식 확정 후 구현 필요.",
    )
