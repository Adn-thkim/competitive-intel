"""server/graph/nodes/reaction_cluster_node.py — ⑥ Stage2② 군집 (통합계획 §1-B·§6-5).

군집 코어 정본: server.graph.reaction.cluster (Phase 1d, poc_cluster_uq_v2 verbatim 이식).

입력: reaction_units + reaction_questions (youtube+community 이미 channel 태그됨 → 단순 concat 상태).
동작:
  - 축 캐시(reaction_axis_store) 미스 → **Phase A**(run_clustering 로 상향식 축 도출) → store 저장.
    (human_review#5 는 별도 interrupt 노드에서 축 수정 → 다음 실행의 정본)
  - 히트 → **Phase B**(고정 축 배정): 저장된 super_aspect 집합에 map_questions 로 units·questions 를
    배정하고, perspective·question_type 는 축과 무관하므로 항상 재계산.
산출: reaction_axis(고정 축) + reaction_clustered(units/questions 에 super_aspect·sub_cluster·
perspective·question_type 부여, channel/source_site 등 입력 필드 **보존**).

이행기(병행): 신 키에만 기록. graph 미배선.
"""
from __future__ import annotations

import copy
import logging
from datetime import datetime, timezone

from server.graph.progress_store import set_progress
from server.graph.reaction_axis_store import axis_key, load_axis, store_axis
from server.graph.state import AgentStep, DomainAnalysisState

_TIMEOUT = 150   # poc_cluster_uq_v2 기본값과 정렬
_MR = 2

logger = logging.getLogger(__name__)


def _step(status: str, started_at: str, error: str = "") -> AgentStep:
    s: AgentStep = {"step_name": "ReactionCluster", "status": status,
                    "started_at": started_at,
                    "finished_at": datetime.now(timezone.utc).isoformat()}
    if error:
        s["error_message"] = error[:300]
    return s


def reaction_cluster_node(state: DomainAnalysisState, config: dict | None = None) -> dict:
    started_at = datetime.now(timezone.utc).isoformat()
    thread_id = (config or {}).get("configurable", {}).get("thread_id", "")
    if thread_id:
        try:
            set_progress(thread_id, "reaction_cluster", detail="super_aspect 군집(축 도출/배정)")
        except Exception as exc:  # noqa: BLE001
            logger.debug("set_progress 실패: %s", exc)
    try:
        from server.graph.reaction.cluster import (
            run_clustering, map_questions, assign_perspective, assign_question_type,
            _SYS_CLUSTER, _super_sys, _QT_SYS, _SYS_PSP, _SCHEMA_C)
        from server.graph.reaction.llm import MODELS
        from server.graph.agent_cache import make_cache_context, load_agent_output, store_agent_output

        # 입력 필드 전량 보존 위해 dict 복사(제자리 변형이 원본 state 를 오염시키지 않도록).
        units = [dict(u) for u in (state.get("reaction_units") or [])]
        questions = [dict(q) for q in (state.get("reaction_questions") or [])]
        if not units and not questions:
            return {"agent_steps": [_step("skipped", started_at)]}

        st = dict(state)
        model = MODELS.get("sonnet", "sonnet")
        dt = state.get("domain_taxonomy") or {}
        cand_ids = [p.get("candidate_id", "") for p in (state.get("product_profiles") or [])]
        key = axis_key(dt.get("domain_slug", ""), cand_ids)
        axis = load_axis(key)

        # 캐시(재현성·비용) — 노드 전체 출력(축 도출/배정)을 units·questions·축 해시로 기록.
        # Phase A/B 의 LLM 배정 결과를 통째로 재사용 → 재실행 시 동일 군집(비결정성 제거).
        _fp = _SYS_CLUSTER + _super_sys(12) + _QT_SYS + _SYS_PSP
        cctx = make_cache_context(agent_id="reaction_cluster", model=model,
                                  system_prompt=_fp, output_schema=_SCHEMA_C)
        # cin 은 **배정 전 입력의 스냅샷(deepcopy)** 이어야 한다. 아래 Phase A/B 가 units·questions 를
        # 제자리 변형(super_aspect·sub_cluster·perspective·question_type 부여)하므로, 참조를 그대로 두면
        # store 시점 키(변형 후 입력) ≠ load 시점 키(변형 전 입력) 가 되어 캐시가 영구 미적중이었다
        # (2026-10-23 수정 — 기존 엔트리는 scripts/seed_reaction_caches.py 로 재키잉).
        cin = copy.deepcopy({"units": units, "questions": questions, "axis_key": key,
                             "axis": (axis or {}).get("super_to_sub") if axis else None})
        hit = load_agent_output(agent_id="reaction_cluster", cache_input=cin, context=cctx, logger=logger)
        if hit is not None:
            logger.info("reaction_cluster: 출력 캐시 히트 → LLM 미호출")
            return {"reaction_axis": hit.get("axis") or axis or {},
                    "reaction_clustered": hit["clustered"],
                    "agent_steps": [_step("completed", started_at)]}

        if axis is not None:
            # 축 캐시 히트 → Phase B(고정 축 배정). map_questions 를 고정 축 배정기로 units 에도 재사용
            # (target_term/quote 만 읽어 super_aspect 를 부여 — 입력 스키마 호환). 축은 캐시 유지(재저장 X).
            logger.info("reaction_cluster: 축 캐시 히트(%s) → Phase B 배정", key)
            super_set = sorted((axis.get("super_to_sub") or {}).keys())
            f1 = map_questions(units, super_set, "cli", model, _TIMEOUT, _MR)
            f2 = map_questions(questions, super_set, "cli", model, _TIMEOUT, _MR)
            for u in units:
                u.setdefault("sub_cluster", u.get("super_aspect", "기타"))
            f3 = assign_perspective(units, "cli", model, _TIMEOUT, _MR)
            f4 = assign_question_type(questions, "cli", model, _TIMEOUT, _MR)
            ok = (f1 or 0) + (f2 or 0) + (f3 or 0) + (f4 or 0) == 0   # 배정 LLM 실패 없음?
            clustered = {"units": units, "questions": questions, "axis_source": "cache",
                         "super_aspects": super_set}
        else:
            # 축 캐시 미스 → Phase A(상향식 도출). LLM 실패 없을 때만 축·캐시 저장(오염 방지).
            logger.info("reaction_cluster: 축 미스 → Phase A 도출(%s)", key)
            doc = run_clustering(units, questions, st, "cli", model, _TIMEOUT, _MR)
            units, questions = doc["units"], doc["questions"]
            super_to_sub = doc["summary"]["super_to_sub"]
            ok = all(v == 0 for v in (doc["meta"].get("failed") or {}).values())
            if ok:
                axis = store_axis(key, super_to_sub)      # 성공 시에만 정본 축 저장
            else:
                axis = {"super_to_sub": super_to_sub}     # 임시(미저장) — 이번 실행 한정
                logger.warning("reaction_cluster: 군집 일부 실패(%s) — 축·캐시 저장 생략(재실행 재시도)",
                               doc["meta"].get("failed"))
            clustered = {"units": units, "questions": questions, "axis_source": "derived",
                         "summary": doc["summary"], "meta": doc["meta"]}

        if ok:   # 실패분(429 등)은 캐시하지 않는다 → 재실행에서 재시도(오염 방지)
            store_agent_output(agent_id="reaction_cluster", cache_input=cin, context=cctx,
                               output={"axis": axis, "clustered": clustered}, logger=logger)
        else:
            logger.warning("reaction_cluster: LLM 실패 포함 — reaction_cluster 캐시 생략")
        return {"reaction_axis": axis, "reaction_clustered": clustered,
                "agent_steps": [_step("completed", started_at)]}
    except Exception as exc:  # noqa: BLE001
        logger.exception("reaction_cluster 실패")
        return {"agent_steps": [_step("failed", started_at, f"{type(exc).__name__}: {exc}")],
                "errors": [{"node": "reaction_cluster_node", "error": str(exc)[:200],
                            "timestamp": datetime.now(timezone.utc).isoformat()}]}
