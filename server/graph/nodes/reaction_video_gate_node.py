"""server/graph/nodes/reaction_video_gate_node.py — ① 영상 도메인 게이트 (통합계획 §1-B).

이식 원본: scripts/test_gate_prescreen_v2.gate_videos. youtube 영상(제목+설명)이 '트래블카드
상품 리뷰/사용기'인지 boolean 판정. community 게이트는 reaction_extract_node 내부(도메인 게이트,
§6-2 비대칭)에서 처리하므로 본 노드는 youtube 전용.

이행기(병행): 통과 video_id 집합을 신 키 `reaction_gate`(비파괴 신설)에 기록. prescreen·extract 가 소비.
graph 미배선. TODO(실환경): reaction_gate 키를 state.py 에 정식 추가할지 확정(현재 임시 통과키).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from server.cache_ttl import get_ttl_hours
from server.graph.progress_store import set_progress
from server.graph.state import AgentStep, DomainAnalysisState

logger = logging.getLogger(__name__)

_TTL_H = get_ttl_hours("reaction_video_gate_hours", 7200)   # cache_ttls.yaml


def _step(status: str, started_at: str, error: str = "") -> AgentStep:
    s: AgentStep = {"step_name": "ReactionVideoGate", "status": status,
                    "started_at": started_at,
                    "finished_at": datetime.now(timezone.utc).isoformat()}
    if error:
        s["error_message"] = error[:300]
    return s


def reaction_video_gate_node(state: DomainAnalysisState, config: dict | None = None) -> dict:
    started_at = datetime.now(timezone.utc).isoformat()
    thread_id = (config or {}).get("configurable", {}).get("thread_id", "")
    if thread_id:
        try:
            set_progress(thread_id, "reaction_gate", detail="영상 도메인 게이트")
        except Exception as exc:  # noqa: BLE001
            logger.debug("set_progress 실패: %s", exc)
    try:
        from server.graph.reaction.gate import gate_videos, build_domain_signals, _GATE_SYS, _GATE_SCHEMA
        from server.graph.reaction.llm import MODELS
        from server.graph.agent_cache import make_cache_context, load_agent_output, store_agent_output
        from server.llm.claude_cli_analyzer import get_usage

        # 세션 토큰 추적 시작점 — gate 진입 시 usage 스냅샷을 state 에 저장(report 가 여기서부터 누적 diff).
        usage_start = get_usage()

        vids = [(v.get("video_id", ""), v.get("title", ""), v.get("description", "") or "")
                for v in (state.get("collected_videos") or []) if v.get("video_id")]
        if not vids:
            return {"reaction_gate": {"kept_video_ids": []}, "reaction_usage_start": usage_start,
                    "agent_steps": [_step("skipped", started_at)]}
        model = MODELS.get("sonnet", "sonnet")
        # 캐시(재현성·비용) — 영상 집합 해시 키. 히트 시 LLM 미호출·동일 판정 재사용.
        cctx = make_cache_context(agent_id="reaction_gate", model=model,
                                  system_prompt=_GATE_SYS, output_schema=_GATE_SCHEMA)
        # 캐시 키를 **순서·잡음 불변**으로: 상류가 만드는 collected_videos 의 순서/설명 공백이 실행마다
        # 달라져도 같은 영상 집합이면 히트하도록 video_id 집합(정렬)만으로 키 구성. (제목·설명은 영상당
        # 안정적이므로 판정 재현성에 영향 없음)
        cin = {"video_ids": sorted(v for v, t, d in vids)}
        hit = load_agent_output(agent_id="reaction_gate", cache_input=cin, context=cctx,
                                ttl_hours=_TTL_H, logger=logger)
        if hit is not None:
            keep = hit["keep"]
        else:
            signals = build_domain_signals(dict(state))
            keep, ok = gate_videos(vids, "cli", model, signals, 4, 600, 2)
            if ok:      # 실패(429 등) 시 캐시 생략 → 재실행에서 재시도(오염 방지)
                store_agent_output(agent_id="reaction_gate", cache_input=cin, context=cctx,
                                   output={"keep": keep}, logger=logger)
            else:
                logger.warning("reaction_video_gate: 일부 배치 실패 — 캐시 생략(재실행 재시도)")
        kept = [vid for vid, _, _ in vids if keep.get(vid, True)]
        logger.info("reaction_video_gate: %d → %d 영상 통과", len(vids), len(kept))
        return {"reaction_gate": {"kept_video_ids": kept}, "reaction_usage_start": usage_start,
                "agent_steps": [_step("completed", started_at)]}
    except Exception as exc:  # noqa: BLE001
        logger.exception("reaction_video_gate 실패")
        return {"agent_steps": [_step("failed", started_at, f"{type(exc).__name__}: {exc}")],
                "errors": [{"node": "reaction_video_gate_node", "error": str(exc)[:200],
                            "timestamp": datetime.now(timezone.utc).isoformat()}]}
