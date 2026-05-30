"""
server/graph/nodes/battlecard_node.py
-------------------------------------
**§6-4 D1=B 분리형, v0.10 스켈레톤** — `battlecard` 리포트 노드.

흐름 분류 (§11-10 표 기준)
--------------------------
- 흐름 A: ✓ (feature_pool의 dedicated feature 4종)
- 흐름 B: ✓ (3개 상류 리포트 inline 인용)
- LangGraph 배치: **mid** — comparison_matrix · reaction_insight · marketing_social 모두 완료 대기.

read keys
---------
- `domain_taxonomy.report_config["battlecard"]`
    - `features` (dedicated 4종 포함: `competitor_marketing_copy`·`competitor_promo_end_date`·
      `competitor_switch_story_quote`·`competitor_sales_objection`)
    - `categories` (Rubric §2-4 표준 — Winning/Battling/Losing Zone·Persona Variation·Living Battlecard)
- `feature_pool` (dedicated feature 4종의 정형 값)
- `report_outputs["comparison_matrix"]` — 정량 비교 inline 인용
- `report_outputs["reaction_insight"]` — 사용자 quote inline 인용
- `report_outputs["marketing_social"]` — 채널 매트릭스 inline 인용 (B-4)

write keys
----------
- `report_outputs["battlecard"]` — Rubric §2-4 평가 루브릭 1–5점 기준 envelope.

content 구조 (Rubric §2-4 권장 — FIA 3-tuple 의무)
---------------------------------------------------
- `winning_zone`: 자사 명확 우위 영역 + 각 항목 FIA(Fact + Impact + Act) 3-tuple.
- `battling_zone`: 접전 영역 + proof point 인용 + FIA.
- `losing_zone`: 자사 열위 + 명시적 인정 + 우회 전략 + FIA.
- `dedicated_facts`: dedicated feature 4종의 정형 값.
- `persona_variations`: 단기 여행자 / 장기 체류자 / 디지털노마드 변형 3개.
- `living_battlecard_status`: 한시 프로모션 `valid_until` 추적.

캐시 키 (§8-2 D10 — upstream_outputs_hash)
------------------------------------------
- `agent_cache` 키에 `frozenset({(comparison_matrix, hash), (reaction_insight, hash),
  (marketing_social, hash)})` 포함하여 상류 변경 시 자동 무효화.

상태
----
**SCAFFOLD ONLY** — 3개 상류 리포트 산출 형식 + feature_pool dedicated 4종 추출
방식이 확정되어야 LLM 호출 로직 작성 가능.
"""

from __future__ import annotations

from datetime import datetime, timezone

from server.graph.state import DomainAnalysisState
from server.graph.nodes._report_node_common import (
    is_report_active,
    make_error_result,
    make_skip_result,
)

REPORT_TYPE = "battlecard"


def battlecard_node(state: DomainAnalysisState) -> dict:
    """v0.10 D1=B 분리형 — battlecard 리포트 생성 (스켈레톤, 흐름 A+B 의존)."""
    started_at = datetime.now(timezone.utc).isoformat()

    if not is_report_active(state, REPORT_TYPE):
        return make_skip_result(REPORT_TYPE, started_at)

    # TODO (3개 상류 리포트 + feature_pool 산출 형식 확정 후 구현):
    # 1. report_outputs["comparison_matrix"|"reaction_insight"|"marketing_social"] 로드
    # 2. feature_pool에서 dedicated feature 4종 추출
    # 3. Winning/Battling/Losing 3 Zone 분류
    # 4. 각 항목 FIA(Fact+Impact+Act) 3-tuple 작성 (Rubric §2-4 5점 조건)
    # 5. 페르소나 변형 3개 (단기/장기/디지털노마드)
    # 6. valid_until 자동 추적 (한시 프로모션)
    # 7. AP-1·AP-2 회피 검증
    # 8. build_report_envelope(...) → report_outputs["battlecard"]
    return make_error_result(
        REPORT_TYPE, started_at,
        "battlecard_node §6-4 스켈레톤 — 3개 상류 리포트 + feature_pool dedicated 추출 후 구현 필요.",
    )
