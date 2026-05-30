"""
server/graph/nodes/executive_summary_node.py
--------------------------------------------
**§6-4 D1=B 분리형, v0.10 스켈레톤** — `executive_summary` 리포트 노드 (top 노드, 본 프로젝트 유일).

흐름 분류 (§11-10 표 기준)
--------------------------
- 흐름 A: ✗
- 흐름 B: ✓ (6개 상류 리포트 모두 inline 인용)
- LangGraph 배치: **top** — 6개 리포트 fan-in 모두 완료 대기. 본 노드 직후 END.

read keys
---------
- `domain_taxonomy.report_config["executive_summary"]`
    - `categories` (Rubric §2-7 표준 — BLUF·SCR/SCQA·Pyramid Principle·Bold-Bullet·So What? Test·Persona Branching·Cross-link)
- `report_outputs["comparison_matrix"]` — 정량 우위/열위 1줄 결론
- `report_outputs["reaction_insight"]` — 인식 1줄 결론
- `report_outputs["marketing_social"]` — 채널·메시지 공백 1줄 결론
- `report_outputs["battlecard"]` — FIA Zones 1줄 결론
- `report_outputs["positioning_map"]` — 좌표 1줄 결론
- `report_outputs["market_context_swot"]` — 시장·전략 1줄 결론

write keys
----------
- `report_outputs["executive_summary"]` — Rubric §2-7 평가 루브릭 1–5점 기준 envelope.
- `final_report` (state 별도 키) — 동일 내용을 프런트엔드 즉시 접근용으로 노출.

content 구조 (Rubric §2-7 권장 — SCR + Pyramid + BLUF + Bold-Bullet)
--------------------------------------------------------------------
- `bluf`: 단일 문장 결론 + 둘째 문장 핵심 근거 요약.
- `situation`: 시장 컨텍스트 (10–15% 분량).
- `complication`: 자사 위치 문제 (15–20%).
- `question`: 1줄 의사결정 초점.
- `resolution`: 페르소나별 권장 + 액션 우선순위 (60–70% 분량).
- `persona_recommendations`: 3개 페르소나(CEO/CMO/PM) 분기 권장.
- `cross_links`: 6개 상류 리포트로의 짧은 출처 참조 (인용 본문 노출 최소).

핵심 원칙 (Rubric §2-7)
-----------------------
- 1–2 페이지 분량 제약 (초과 시 정의상 executive summary 아님).
- Bold 문장만 스캔해도 전체 논지 파악 가능.
- 각 bullet은 "so what?" 답 포함 (단순 사실 진술 금지).

캐시 키 (§8-2 D10)
------------------
- upstream_outputs_hash: 6개 상류 리포트의 출력 해시 frozenset.
- 6개 중 하나라도 변경되면 본 노드 캐시 자동 무효화.

상태
----
**SCAFFOLD ONLY** — 6개 상류 리포트의 산출 1줄 결론 추출 패턴이 확정되어야 LLM 작성.
"""

from __future__ import annotations

from datetime import datetime, timezone

from server.graph.state import DomainAnalysisState
from server.graph.nodes._report_node_common import (
    is_report_active,
    make_error_result,
    make_skip_result,
)

REPORT_TYPE = "executive_summary"


def executive_summary_node(state: DomainAnalysisState) -> dict:
    """v0.10 D1=B 분리형 — executive_summary 리포트 생성 (스켈레톤, top, 6개 fan-in 의존)."""
    started_at = datetime.now(timezone.utc).isoformat()

    if not is_report_active(state, REPORT_TYPE):
        return make_skip_result(REPORT_TYPE, started_at)

    # TODO (6개 상류 리포트 산출 형식 확정 후 구현):
    # 1. report_outputs["comparison_matrix"|"reaction_insight"|"marketing_social"|
    #    "battlecard"|"positioning_map"|"market_context_swot"]의 1줄 결론 추출
    # 2. SCR 분량 비중 강제 (Resolution 60–70%)
    # 3. BLUF 첫 문장 = 단일 결론
    # 4. Pyramid 3–5 논거 (MECE)
    # 5. 3개 페르소나(CEO/CMO/PM) 분기 권장
    # 6. bold-bullet 일관 적용 + "so what?" 답 명시
    # 7. AP-5·AP-6·AP-10 회피 검증
    # 8. 1–2 페이지 분량 제약 검증
    # 9. build_report_envelope(...) → report_outputs["executive_summary"] + final_report
    return make_error_result(
        REPORT_TYPE, started_at,
        "executive_summary_node §6-4 스켈레톤 — 6개 상류 리포트 산출 확정 후 구현 필요.",
    )
