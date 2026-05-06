"""
server/graph/nodes/normalize_competitor_ids_node.py
-----------------------------------------------------
경쟁 후보 candidate_id 확정 노드.

역할
----
state["competitor_candidates"]의 각 후보에 대해
ProductIdResolver.resolve_comp() 를 호출하여:
  1. candidate["candidate_id"]  → comp_* 슬러그로 교체
  2. candidate["product_name"]  → LLM 정규화된 공식 명칭으로 교체

결과를 state["competitor_candidates"]에 덮어쓴다.
officialSourceResolverNode.js 는 이 노드 실행 이후의 state를 읽는다.

설계 결정
---------
- Annotated[list, operator.add] 리듀서를 우회해
  competitor_candidates 전체를 직접 replace한다.
  LangGraph는 Annotated가 없는 필드를 반환값으로 대체하므로 의도된 동작이다.
- 개별 후보 실패는 파이프라인을 중단하지 않는다.
  실패한 후보는 LLM이 채운 임시 candidate_id를 유지하고 errors에 누적된다.
"""

import copy
import logging
from datetime import datetime, timezone

from server.config import ANTHROPIC_API_KEY, PRODUCT_NAME_CACHE_PATH
from server.graph.state import DomainAnalysisState, AgentStep
from server.utils.slug import ProductIdResolver

logger = logging.getLogger(__name__)


def normalize_competitor_ids_node(state: DomainAnalysisState) -> dict:
    """
    경쟁 후보의 candidate_id를 comp_* 슬러그로 확정하는 노드.

    Parameters
    ----------
    state : DomainAnalysisState
        필수 키: "competitor_candidates"

    Returns
    -------
    dict
        {"competitor_candidates": <확정 목록>, "agent_steps": [...]}
        개별 실패 시 "errors"도 포함.
    """
    started_at = datetime.now(timezone.utc).isoformat()

    # ── 전제조건 검사 ────────────────────────────────────────────────────────
    raw_candidates: list = state.get("competitor_candidates") or []
    if not raw_candidates:
        return _error(started_at, "competitor_candidates가 state에 없거나 비어 있습니다.")

    # ── 딥카피: 원본 불변 보장 ────────────────────────────────────────────────
    candidates = copy.deepcopy(raw_candidates)
    accumulated_errors: list[dict] = []

    # ── ProductIdResolver 초기화 ──────────────────────────────────────────────
    resolver = ProductIdResolver(
        api_key=ANTHROPIC_API_KEY,
        cache_path=str(PRODUCT_NAME_CACHE_PATH),
    )

    logger.info(
        "normalize_competitor_ids_node: 시작 (후보 %d개)", len(candidates)
    )

    # ── 후보별 ID 정규화 ─────────────────────────────────────────────────────
    for idx, candidate in enumerate(candidates):
        raw_name: str = candidate.get("product_name", "").strip()
        if not raw_name:
            logger.warning("후보[%d].product_name 비어 있어 건너뜁니다.", idx)
            continue

        try:
            canonical_name, product_id = resolver.resolve_comp(raw_name)
            candidate["candidate_id"] = product_id
            candidate["product_name"] = canonical_name
            logger.debug("후보[%d] '%s' → '%s' / '%s'",
                         idx, raw_name, canonical_name, product_id)
        except Exception as exc:  # noqa: BLE001
            msg = f"후보[{idx}] '{raw_name}' ID 정규화 실패: {exc}"
            logger.error("normalize_competitor_ids_node: %s", msg)
            accumulated_errors.append({
                "node":      "normalize_competitor_ids_node",
                "error":     msg,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

    finished_at = datetime.now(timezone.utc).isoformat()
    logger.info(
        "normalize_competitor_ids_node: 완료 (캐시 항목=%d)",
        resolver.cache_stats().get("total_entries", 0),
    )

    step: AgentStep = {
        "step_name":   "NormalizeCompetitorIds",
        "status":      "completed",
        "started_at":  started_at,
        "finished_at": finished_at,
    }

    result: dict = {
        "competitor_candidates": candidates,
        "agent_steps": [step],
    }
    if accumulated_errors:
        result["errors"] = accumulated_errors

    return result


# ── 내부 헬퍼 ─────────────────────────────────────────────────────────────────

def _error(started_at: str, message: str) -> dict:
    logger.error("normalize_competitor_ids_node 오류: %s", message)
    return {
        "errors": [{
            "node":      "normalize_competitor_ids_node",
            "error":     message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }],
        "agent_steps": [{
            "step_name":     "NormalizeCompetitorIds",
            "status":        "failed",
            "started_at":    started_at,
            "finished_at":   datetime.now(timezone.utc).isoformat(),
            "error_message": message,
        }],
    }
