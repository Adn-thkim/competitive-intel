"""
server/graph/nodes/human_review_node.py
-----------------------------------------
Human-in-the-loop 중단점 노드.

역할
----
1. interrupt(query_intake_output) 로 파이프라인을 일시 중단한다.
   - LangGraph가 이 값을 api.py /invoke 응답의 interrupt_value로 전달한다.
   - 프런트엔드는 이 값으로 검토 폼을 렌더링한다.

2. 사용자가 폼을 수정하고 완료 버튼을 누르면 Express가
   Command(resume=edited_form) 으로 그래프를 재개한다.
   - edited_form = 사용자가 수정한 draft_competitor_discovery_input dict

3. 재개 후 이 노드는:
   - ProductIdResolver.resolve_own() 으로 own_product.product_id(own_* 슬러그) 생성
   - project_id = "proj_" + slug (own_ 접두사 제거)
   - edited_form 필드를 state의 flat 최상위 키로 분해해 반환

인터럽트 흐름 요약
------------------
  /api/intake POST
    → graph.invoke({raw_query, run_id})
    → query_intake_node 실행
    → human_review_node: interrupt() 호출 → 그래프 일시 중단
    → /invoke 응답: is_interrupted=True, interrupt_value=<query_intake_output>

  /api/approve POST
    → graph.invoke(Command(resume=edited_form), config={thread_id: run_id})
    → human_review_node: interrupt() 재개, edited_form 수신
    → own_product.product_id 생성
    → flat 필드 반환 → competitor_discovery_node 실행 → ...
"""

import logging
from datetime import datetime, timezone

from langgraph.types import interrupt

from server.config import ANTHROPIC_API_KEY, PRODUCT_NAME_CACHE_PATH
from server.graph.state import DomainAnalysisState, AgentStep
from server.utils.slug import ProductIdResolver

logger = logging.getLogger(__name__)


def human_review_node(state: DomainAnalysisState) -> dict:
    """
    사용자 폼 검토를 위한 중단점 노드.

    Parameters
    ----------
    state : DomainAnalysisState
        필수 키: "query_intake_output"

    Returns
    -------
    dict
        사용자 승인 후: 분석 컨텍스트 flat 필드 + agent_steps
    """
    started_at = datetime.now(timezone.utc).isoformat()

    # ── 전제조건 검사 ────────────────────────────────────────────────────────
    intake_output: dict = state.get("query_intake_output")  # type: ignore[assignment]
    if not intake_output:
        return _error(started_at, "query_intake_output이 state에 없습니다.")

    # ── interrupt() ─────────────────────────────────────────────────────────
    # 프런트엔드가 폼을 수정하고 완료를 누를 때까지 여기서 중단한다.
    # interrupt()가 반환한 값 = Express가 Command(resume=...) 로 넘긴 edited_form
    # edited_form 구조: CompetitorDiscoveryAgentInput draft (draft_competitor_discovery_input)
    logger.info("human_review_node: interrupt() 호출 — 사용자 검토 대기")

    edited_form: dict = interrupt(intake_output)

    # ── 재개 후 처리 ─────────────────────────────────────────────────────────
    logger.info("human_review_node: 사용자 승인 수신, product_id 생성 시작")

    # own_product.product_id 생성 (temperature=0 API 호출)
    resolver = ProductIdResolver(
        api_key=ANTHROPIC_API_KEY,
        cache_path=str(PRODUCT_NAME_CACHE_PATH),
    )
    raw_own_name: str = edited_form.get("own_product", {}).get("name", "")
    if not raw_own_name:
        return _error(started_at, "edited_form.own_product.name이 비어 있습니다.")

    try:
        canonical_name, product_id = resolver.resolve_own(raw_own_name)
    except Exception as exc:  # noqa: BLE001
        return _error(started_at, f"own_product.product_id 생성 실패: {exc}")

    # project_id: "own_토스트래블카드" → "proj_토스트래블카드"
    project_id = "proj_" + product_id[len("own_"):]

    finished_at = datetime.now(timezone.utc).isoformat()
    logger.info(
        "human_review_node: 완료 (product_id=%s, project_id=%s)",
        product_id, project_id,
    )

    step: AgentStep = {
        "step_name":   "HumanReview",
        "status":      "completed",
        "started_at":  started_at,
        "finished_at": finished_at,
    }

    # ── flat 필드 분해 및 반환 ────────────────────────────────────────────────
    # own_product에 확정된 product_id와 canonical_name을 주입한다.
    own_product = {
        **edited_form.get("own_product", {}),
        "name":       canonical_name,
        "product_id": product_id,
    }

    return {
        "project_id":          project_id,
        "domain_name":         edited_form.get("domain_name", ""),
        "own_product":         own_product,
        "problem_statement":   edited_form.get("problem_statement", ""),
        "target_user":         edited_form.get("target_user", []),
        "core_value_props":    edited_form.get("core_value_props", []),
        "geography":           edited_form.get("geography", "대한민국"),
        "known_keywords":      edited_form.get("known_keywords", []),
        "usage_context":       edited_form.get("usage_context", []),
        "business_constraints": edited_form.get("business_constraints", []),
        "agent_steps":         [step],
    }


# ── 내부 헬퍼 ─────────────────────────────────────────────────────────────────

def _error(started_at: str, message: str) -> dict:
    logger.error("human_review_node 오류: %s", message)
    return {
        "errors": [{
            "node":      "human_review_node",
            "error":     message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }],
        "agent_steps": [{
            "step_name":     "HumanReview",
            "status":        "failed",
            "started_at":    started_at,
            "finished_at":   datetime.now(timezone.utc).isoformat(),
            "error_message": message,
        }],
    }
