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

import json
import logging
from datetime import datetime, timezone

from langgraph.types import interrupt

from server.config import ANTHROPIC_API_KEY, PRODUCT_NAME_CACHE_PATH
from server.graph.state import DomainAnalysisState, AgentStep
from server.graph.nodes.domain_modeling_node import query_fingerprint, TAXONOMY_DIR
from server.graph.query_intake_overrides import load_overrides, store_overrides
from server.utils.slug import ProductIdResolver

logger = logging.getLogger(__name__)


def _find_cached_taxonomy(qfp: str) -> dict:
    """query_fingerprint 가 일치하는 저장된 taxonomy 중 최신 1건을 찾는다.

    "동일 입력"은 query_intake 수준(competition_axes 제외)으로 정의한다 — interrupt#1
    시점에는 competitor_discovery 가 아직 실행되지 않았기 때문.

    Returns
    -------
    dict
        {"exists": bool, "latest_date": "YYYY-MM-DD" | "", "version": int | None}
    """
    best = None
    try:
        for path in TAXONOMY_DIR.glob("*_slug.json"):
            try:
                tax = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if tax.get("query_fingerprint") != qfp:
                continue
            # 재생성은 updated_at 만 갱신하고 created_at(최초 생성일)은 보존하므로,
            # "생성본" 날짜·최신 판정은 updated_at 을 우선한다(없으면 created_at 폴백).
            stamp = tax.get("updated_at") or tax.get("created_at") or ""
            if best is None or stamp > best[0]:
                best = (stamp, tax.get("version"))
    except Exception as exc:  # noqa: BLE001 — 캐시 조회 실패는 비치명적
        logger.debug("taxonomy 캐시 조회 실패: %s", exc)
    if best is None:
        return {"exists": False, "latest_date": "", "version": None}
    return {"exists": True, "latest_date": best[0][:10], "version": best[1]}


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

    # ── taxonomy_choice 계산 (interrupt#1 드롭다운용) ───────────────────────
    # query_intake 수준 지문으로 "동일 입력"의 저장된 taxonomy 존재 여부를 조회한다.
    draft = intake_output.get("draft_competitor_discovery_input", {}) or {}
    taxonomy_choice = _find_cached_taxonomy(query_fingerprint(draft))

    # 현재 raw_query 에 저장된 사용자 정정 필드 목록 — 프런트 '정정 해제' 버튼 tooltip 용.
    raw_query_for_ov = intake_output.get("raw_query") or state.get("raw_query", "")
    try:
        override_fields = sorted(load_overrides(raw_query_for_ov).keys())
    except Exception as exc:  # noqa: BLE001 — 비치명적
        logger.debug("override_fields 조회 실패: %s", exc)
        override_fields = []

    # ── interrupt() ─────────────────────────────────────────────────────────
    # 프런트엔드가 폼을 수정하고 완료를 누를 때까지 여기서 중단한다.
    # interrupt()가 반환한 값 = Express가 Command(resume=...) 로 넘긴 edited_form
    # edited_form 구조: CompetitorDiscoveryAgentInput draft (draft_competitor_discovery_input)
    #   + 드롭다운 선택 결과 force_taxonomy_refresh (bool)
    # taxonomy_choice 는 intake_output 의 형제 키로 전달 — 폼은 기존 필드만 읽고,
    # 드롭다운은 taxonomy_choice 를 읽는다(비파괴적).
    logger.info("human_review_node: interrupt() 호출 — 사용자 검토 대기 (taxonomy_choice=%s)",
                taxonomy_choice)

    edited_form: dict = interrupt({
        **intake_output,
        "taxonomy_choice": taxonomy_choice,
        "override_fields": override_fields,
    })

    # ── 재개 후 처리 ─────────────────────────────────────────────────────────
    logger.info("human_review_node: 사용자 승인 수신, product_id 생성 시작")

    # 사용자가 바꾼 draft 필드를 raw_query 키 오버라이드로 영속화(비치명적).
    # 다음 실행에서 query_intake 가 캐시 히트해도 이 정정이 다시 반영된다.
    try:
        store_overrides(
            raw_query=intake_output.get("raw_query") or state.get("raw_query", ""),
            presented_draft=intake_output.get("draft_competitor_discovery_input") or {},
            edited_form=edited_form,
            logger=logger,
        )
    except Exception as exc:  # noqa: BLE001 — 정정 저장 실패는 파이프라인을 막지 않는다
        logger.warning("query_intake 오버라이드 저장 실패(무시): %s", exc)

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

    # 드롭다운 선택 — "신규 생성" 이면 True(강제 재생성), "생성본 재사용" 이면 False.
    # 키 부재 시 기본 False(= 캐시 재사용, soft TTL 판정에 위임).
    force_taxonomy_refresh = bool(edited_form.get("force_taxonomy_refresh", False))

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
        "force_taxonomy_refresh": force_taxonomy_refresh,
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
