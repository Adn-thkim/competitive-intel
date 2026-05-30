"""
server/graph/nodes/positioning_map_node.py
------------------------------------------
**§6-4 D1=B 분리형, v0.10 스켈레톤** — `positioning_map` 리포트 노드.

흐름 분류 (§11-10 표 기준)
--------------------------
- 흐름 A: ✗
- 흐름 B: ✓ (comparison_matrix·reaction_insight inline 인용)
- LangGraph 배치: **mid** — comparison_matrix 완료 대기. reaction_insight는 perceptual map용.

read keys
---------
- `domain_taxonomy.report_config["positioning_map"]`
    - `categories` (Rubric §2-5 표준 — Axis Selection·Coordinate Calculation·Quadrant·
      White Space·Persona Multi-view·Aaker 3 Conditions·Reframing Candidate)
- `report_outputs["comparison_matrix"]` — Coordinate X 축 (실제 우위)
- `report_outputs["reaction_insight"]` — Coordinate Y 축 (인식 우위)

write keys
----------
- `report_outputs["positioning_map"]` — Rubric §2-5 평가 루브릭 1–5점 기준 envelope.

content 구조 (Rubric §2-5 권장)
-------------------------------
- `axis_selection`: 고객 의사결정 기준 빈도·중요도 가중으로 선정한 2개 축 + 근거.
- `positioning_map`: 정량 좌표(comparison_matrix 기반).
- `perceptual_map`: 인식 좌표(reaction_insight ABSA 기반).
- `gap_markers`: 두 맵의 격차가 큰 카드 식별 → 마케팅 액션 기회.
- `quadrant_interpretation`: 4사분면 해석 (Porter Generic Strategies 매핑).
- `white_space`: 시장 빈 공간 + 진입 가능성 평가.
- `persona_views`: 최소 2개 페르소나(단기 여행자 + 장기 체류자) 뷰.
- `aaker_scores`: Resonate / Differentiate / Reflect 3조건 점수.
- `reframing_candidates`: 후발 브랜드용 재정의 카테고리 1–2개.

캐시 키 (§8-2 D10)
------------------
- upstream_outputs_hash: `frozenset({(comparison_matrix, hash), (reaction_insight, hash)})`

상태
----
**SCAFFOLD ONLY** — 두 상류 리포트의 산출 좌표값 형식이 확정되어야 LLM 호출 작성 가능.
"""

from __future__ import annotations

from datetime import datetime, timezone

from server.graph.state import DomainAnalysisState
from server.graph.nodes._report_node_common import (
    is_report_active,
    make_error_result,
    make_skip_result,
)

REPORT_TYPE = "positioning_map"


def positioning_map_node(state: DomainAnalysisState) -> dict:
    """v0.10 D1=B 분리형 — positioning_map 리포트 생성 (스켈레톤, 흐름 B 전용)."""
    started_at = datetime.now(timezone.utc).isoformat()

    if not is_report_active(state, REPORT_TYPE):
        return make_skip_result(REPORT_TYPE, started_at)

    # TODO (comparison_matrix·reaction_insight 산출 형식 확정 후 구현):
    # 1. 두 상류 리포트의 좌표화 가능 데이터 추출
    # 2. 축 선정 (§1-4 4단계 절차 — 고객 의사결정 기준 빈도·중요도 가중)
    # 3. positioning + perceptual 동시 산출 → 격차 markers
    # 4. 4사분면 해석 + white space 식별
    # 5. 페르소나 다중 뷰 (최소 2개)
    # 6. Aaker 3조건 점수 + Reframing 후보
    # 7. AP-5 회피 검증 (페르소나 평균화 금지)
    # 8. build_report_envelope(...) → report_outputs["positioning_map"]
    return make_error_result(
        REPORT_TYPE, started_at,
        "positioning_map_node §6-4 스켈레톤 — 상류 2개 리포트 산출 형식 확정 후 구현 필요.",
    )
