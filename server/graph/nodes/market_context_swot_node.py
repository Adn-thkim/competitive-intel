"""
server/graph/nodes/market_context_swot_node.py
----------------------------------------------
**§6-4 D1=B 분리형, v0.10 스켈레톤** — `market_context_swot` 리포트 노드.

흐름 분류 (§11-10 표 기준 — 본 프로젝트 유일의 A+B 동시 사용 리포트)
-------------------------------------------------------------------
- 흐름 A: ✓ (market_context — §6-6a market_context_collection_node 산출, D13 캐시 정책)
- 흐름 B: ✓ (3개 상류 리포트 inline 인용 — comparison_matrix + reaction_insight + marketing_social)
- LangGraph 배치: **mid/top** — 4개 의존 모두 완료 대기.

read keys
---------
- `domain_taxonomy.report_config["market_context_swot"]`
    - `categories` (Rubric §2-6 표준 — SWOT 4분면·TOWS·PESTLE 4요소·Porter's 5 Forces·Market Sizing·Seasonality)
- `market_context` (§6-6a market_context_collection_node 산출, 미구현)
    - 구조 추정: TAM/SAM/SOM, CAGR, PESTLE 4요소(P/E/S/T) 정량 데이터
- `report_outputs["comparison_matrix"]` — 내부 S/W 도출 인용
- `report_outputs["reaction_insight"]` — 내부 S/W 도출 인용
- `report_outputs["marketing_social"]` — 내부 S/W 도출 인용

write keys
----------
- `report_outputs["market_context_swot"]` — Rubric §2-6 평가 루브릭 1–5점 기준 envelope.

content 구조 (Rubric §2-6 권장)
-------------------------------
- `swot`: 4분면 — S/W는 흐름 B(3 리포트) 인용, O/T는 흐름 A(market_context) 도출.
- `tows`: SO/WO/ST/WT 4종 액션 + 다중 S-O 또는 W-T 페어 우선순위 (분기당 2–4개).
- `pestle`: P·E·S·T 4요소 (Legal·Environmental은 보조).
- `porter_5_forces`: Rivalry·Substitution 우선, 나머지 3 보조.
- `market_sizing`: TAM/SAM/SOM + CAGR (1차 출처 우선, 매체 추정은 "추정" 명시).
- `seasonality`: 분기별·월별 분해.

캐시 키 (§8-2 D10)
------------------
- upstream_outputs_hash: `frozenset({(comparison_matrix, hash), (reaction_insight, hash),
  (marketing_social, hash)})` + market_context 데이터 해시.

상태
----
**SCAFFOLD ONLY** — 흐름 A·B 양쪽 산출 형식 확정 후 LLM 호출 작성.
"""

from __future__ import annotations

from datetime import datetime, timezone

from server.graph.state import DomainAnalysisState
from server.graph.nodes._report_node_common import (
    is_report_active,
    make_error_result,
    make_skip_result,
)

REPORT_TYPE = "market_context_swot"


def market_context_swot_node(state: DomainAnalysisState) -> dict:
    """v0.10 D1=B 분리형 — market_context_swot 리포트 생성 (스켈레톤, 흐름 A+B 동시)."""
    started_at = datetime.now(timezone.utc).isoformat()

    if not is_report_active(state, REPORT_TYPE):
        return make_skip_result(REPORT_TYPE, started_at)

    # TODO (market_context + 3개 상류 리포트 산출 형식 확정 후 구현):
    # 1. market_context (외부 macro)에서 PESTLE 4요소 + Porter's 5 Forces + 시장 규모 추출
    # 2. 3개 상류 리포트에서 S/W 인용
    # 3. SWOT 4분면 통합
    # 4. TOWS Matrix 4종 액션 도출 + 다중 페어 우선순위
    # 5. 시즌성 보정 (분기별·월별)
    # 6. AP-4·AP-6·AP-7 회피 검증
    # 7. build_report_envelope(...) → report_outputs["market_context_swot"]
    return make_error_result(
        REPORT_TYPE, started_at,
        "market_context_swot_node §6-4 스켈레톤 — market_context + 3개 상류 리포트 확정 후 구현 필요.",
    )
