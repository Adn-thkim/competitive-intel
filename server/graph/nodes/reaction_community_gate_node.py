"""server/graph/nodes/reaction_community_gate_node.py — 커뮤니티 도메인 게이트 + 스레드 조립 (신설).

통합계획 §6-2·§8. 기존엔 extract 노드 내부(`_build_community_threads`)에서 미캐시로 매 실행 재수행하던
커뮤니티 게이트를 **독립 노드로 분리**한다. 유튜브 gate/prescreen 과 대칭 — 산출 스키마를
`reaction_prescreened.threads` 와 **동일한 member 스레드**로 통일하고, extract 는 "threads 읽어 추출만" 한다.

입력:  state.community_posts (+ community_comments 캐시)
과정:  gate_videos_graded(community_gate_sys) 로 게시글 relevant(keep)+domain_score(0~3) → build_threads
       (제목 t0 멤버) → score_cut(score>=SCORE_MIN, 상한 MAX_COMMUNITY, §8-5).
출력:  reaction_community_prescreened: {"threads": [thread]}  (각 thread 에 channel/source_url/importance_score 태그)
캐시:  agent_cache(agent_id="reaction_community_gate"), TTL=cache_ttls.yaml.reaction_community_gate_hours(7200h).
       실패(429 등) 시 미캐시 → 재실행 재시도(오염 방지).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from server.cache_ttl import get_ttl_hours
from server.graph.progress_store import set_progress
from server.graph.state import AgentStep, DomainAnalysisState

logger = logging.getLogger(__name__)

_TTL_H = get_ttl_hours("reaction_community_gate_hours", 7200)


def _step(status: str, started_at: str, error: str = "") -> AgentStep:
    s: AgentStep = {"step_name": "ReactionCommunityGate", "status": status,
                    "started_at": started_at,
                    "finished_at": datetime.now(timezone.utc).isoformat()}
    if error:
        s["error_message"] = error[:300]
    return s


def reaction_community_gate_node(state: DomainAnalysisState, config: dict | None = None) -> dict:
    started_at = datetime.now(timezone.utc).isoformat()
    thread_id = (config or {}).get("configurable", {}).get("thread_id", "")
    if thread_id:
        try:
            set_progress(thread_id, "reaction_community_gate", detail="커뮤니티 게이트·스레드 조립")
        except Exception as exc:  # noqa: BLE001
            logger.debug("set_progress 실패: %s", exc)
    try:
        from server.graph.reaction.gate import (
            gate_videos_graded, build_domain_signals, community_gate_sys, score_cut,
            _GATE_GRADED_SCHEMA)
        from server.graph.reaction.extract import load_community_posts, _cand_label, build_threads
        from server.graph.reaction.llm import MODELS
        from server.graph.agent_cache import make_cache_context, load_agent_output, store_agent_output
        from server.config import REACTION_SCORE_MIN, REACTION_MAX_COMMUNITY

        posts = load_community_posts(dict(state), with_comments=True)
        if not posts:
            return {"reaction_community_prescreened": {"threads": []},
                    "agent_steps": [_step("skipped", started_at)]}
        model = MODELS.get("sonnet", "sonnet")
        gate_sys = community_gate_sys(dict(state))
        # 캐시(재현성·비용, TTL 7200h) — 게시글 pid 집합 해시 키(순서 불변). 등급 스키마로 전환되어
        # context 지문이 바뀌므로 구 boolean 캐시는 미적중(재실행 1회 필요, 오류 아님).
        cctx = make_cache_context(agent_id="reaction_community_gate", model=model,
                                  system_prompt=gate_sys, output_schema=_GATE_GRADED_SCHEMA)
        cin = {"pids": sorted(p["pid"] for p in posts)}
        hit = load_agent_output(agent_id="reaction_community_gate", cache_input=cin, context=cctx,
                                ttl_hours=_TTL_H, logger=logger)
        if hit is not None:
            keep = hit["keep"]; score = {k: int(v) for k, v in (hit.get("score") or {}).items()}
        else:
            signals = build_domain_signals(dict(state))
            vids = [(p["pid"], p.get("title", ""), p.get("body", "")) for p in posts]
            graded, ok = gate_videos_graded(vids, "cli", model, signals, 4, 600, 2, gate_sys=gate_sys)
            keep = {pid: rel for pid, (rel, _) in graded.items()}
            score = {pid: sc for pid, (_, sc) in graded.items()}
            if ok:      # 실패 시 캐시 생략 → 재실행 재시도
                store_agent_output(agent_id="reaction_community_gate", cache_input=cin, context=cctx,
                                   output={"keep": keep, "score": score}, logger=logger)
            else:
                logger.warning("reaction_community_gate: 일부 배치 실패 — 캐시 생략(재실행 재시도)")

        kept = [p for p in posts if keep.get(p["pid"], True)]
        pid2url = {p["pid"]: p.get("url", "") for p in kept}
        id2name = _cand_label(dict(state))
        threads = build_threads(kept, id2name)   # 제목 t0 멤버 포함(§6-3)
        # 유튜브 prescreen threads 와 스키마 통일 — 각 thread 에 channel/source_url·importance_score 태그
        # (§8, extract 는 태깅 안 함). importance_score 는 아래 score_cut 및 하류 랭킹에 사용.
        for t in threads:
            t["channel"] = "community"
            t["source_url"] = pid2url.get(t["thread_id"], "")
            t["importance_score"] = int(score.get(t["thread_id"], 0))
        # 점수 컷(결정론): score>=SCORE_MIN 필터 → 상한 초과분 score 내림차순 절단(config).
        n_before = len(threads)
        threads = score_cut(threads, REACTION_MAX_COMMUNITY, REACTION_SCORE_MIN)
        logger.info("reaction_community_gate: 게시글 %d → 통과 %d → 스레드 %d → score>=%d·상한%d 컷 %d",
                    len(posts), len(kept), n_before, REACTION_SCORE_MIN, REACTION_MAX_COMMUNITY, len(threads))
        return {"reaction_community_prescreened": {"threads": threads},
                "agent_steps": [_step("completed", started_at)]}
    except Exception as exc:  # noqa: BLE001
        logger.exception("reaction_community_gate 실패")
        return {"agent_steps": [_step("failed", started_at, f"{type(exc).__name__}: {exc}")],
                "errors": [{"node": "reaction_community_gate_node", "error": str(exc)[:200],
                            "timestamp": datetime.now(timezone.utc).isoformat()}]}
