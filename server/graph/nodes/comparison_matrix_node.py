"""
server/graph/nodes/comparison_matrix_node.py
--------------------------------------------
**§6-4 D1=B 분리형, v0.10 스켈레톤** — `comparison_matrix` 리포트 노드.

흐름 분류 (§11-10 표 기준)
--------------------------
- 흐름 A: ✓ (feature_pool에서 자체 features 직접 선택)
- 흐름 B: ✗
- LangGraph 배치: **leaf** — `feature_extraction` 완료 직후 독립 실행.

read keys
---------
- `domain_taxonomy.report_config["comparison_matrix"]`
    - `features` · `feature_labels` · `categories` · `search_query_hints`
    - `categories`는 Rubric §2-1 표준 6종(Pricing·Core Capability·Integration·
      Additional Benefit·Onboarding/Eligibility·UX/Support) 중 채택분
- `feature_pool` (feature_extraction_node 산출, §6-6 D3 옵션 C — 미구현)
- `selected_feature_ids` (feature_selection_node interrupt #4 산출)
- `selected_competitor_ids`

write keys
----------
- `report_outputs["comparison_matrix"]` — Rubric §2-1 평가 루브릭 1–5점 기준 envelope.

content 구조 (Rubric §2-1 권장)
-------------------------------
- `feature_table`: 행=경쟁사, 열=feature. 정량 수치 + 단위 + 시점 + 공식 출처 URL.
- `use_case_weights`: 페르소나별 가중치 (D7 `mixed` 채택 시 action_lens 부여).
- `harvey_balls`: 정성 feature의 5단계 시각 표기.
- `zone_summary`: Winning/Battling/Losing Zone 색상 코딩 요약 (battlecard로 흐름 B 인용 대상).
- `traps_footnote`: AP-1·AP-2·AP-3 함정 항목 4종 명시.

상태
----
**SCAFFOLD ONLY** — `feature_extraction_node`(§6-6)가 산출하는 `feature_pool` 형식이
확정되어야 LLM 호출 로직을 작성할 수 있다.
"""

from __future__ import annotations

from datetime import datetime, timezone

from server.graph.state import DomainAnalysisState
from server.graph.nodes._report_node_common import (
    is_report_active,
    make_error_result,
    make_skip_result,
)

REPORT_TYPE = "comparison_matrix"


def comparison_matrix_node(state: DomainAnalysisState) -> dict:
    """v0.10 D1=B 분리형 — comparison_matrix 리포트 생성 (스켈레톤)."""
    started_at = datetime.now(timezone.utc).isoformat()

    if not is_report_active(state, REPORT_TYPE):
        return make_skip_result(REPORT_TYPE, started_at)

    # TODO (§6-6 산출 형식 확정 후 구현):
    # 1. domain_taxonomy.report_config["comparison_matrix"] 로드
    # 2. feature_pool에서 features ID 목록의 값 추출
    # 3. selected_competitor_ids별 × features 매트릭스 구성
    # 4. Rubric §2-1 평가 루브릭(1–5점) 자체 평가
    # 5. AP-1·AP-2·AP-3 함정 footnote
    # 6. build_report_envelope(...)로 envelope 작성 후 report_outputs["comparison_matrix"]에 write
    return make_error_result(
        REPORT_TYPE, started_at,
        "comparison_matrix_node §6-4 스켈레톤 — §6-6 feature_extraction 산출 형식 확정 후 구현 필요.",
    )
