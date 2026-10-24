"""server/graph/nodes/reaction_prescreen_node.py — ②Stage0 + ③Stage1 + ④프리스크린 (통합계획 §1-B).

이식 원본: scripts/test_gate_prescreen_v2 (stage0_route_group·build_batches·prescreen_batch).
**youtube 전용** — 댓글 노이즈/필러 컷·대댓글 orphan 해제 후 스레드 관련성 프리스크린(Sonnet).

§6-2 비대칭: community 는 1 게시글=1 스레드라 Stage0/1 을 **스킵**한다(본 노드 미경유). 따라서
본 노드는 reaction_gate 통과 youtube 영상에만 적용된다.

코어 정본: server.graph.reaction.prescreen (Phase 2, test_gate_prescreen_v2·test_stage0 verbatim 이식).
흐름: 게이트 통과 영상의 수집 캐시 댓글 로드 → build_batches(Stage0/1) → prescreen_batch_graded(Sonnet,
relevant+importance_score) → 통과 스레드 조립 → score_cut(score>=SCORE_MIN, 상한 MAX_YOUTUBE 전역, §8-5)
→ 신 키 `reaction_prescreened.threads`(extract 소비).

이행기(병행): 신 키에만 기록. graph 미배선.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from server.graph.progress_store import set_progress
from server.graph.state import AgentStep, DomainAnalysisState

logger = logging.getLogger(__name__)

from server.cache_ttl import get_ttl_hours

_TTL_H = get_ttl_hours("reaction_prescreen_hours", 7200)   # cache_ttls.yaml
_THREAD_CAP = 40   # 25→40 상향(poc_prescreen_savings: 배치 23→19·비용 −20.8%·판정 일치율 95.7%, 저위험)
_TIMEOUT = 150
_MR = 1
_WORKERS = 8
_MAX_REPLIES = 5
_DROP_LEN = 40     # auto_drop: 키워드 0 + 길이<40 스레드는 LLM 없이 탈락(무손실 절감, 리콜 손실 0)


def _step(status: str, started_at: str, error: str = "") -> AgentStep:
    s: AgentStep = {"step_name": "ReactionPrescreen", "status": status,
                    "started_at": started_at,
                    "finished_at": datetime.now(timezone.utc).isoformat()}
    if error:
        s["error_message"] = error[:300]
    return s


def reaction_prescreen_node(state: DomainAnalysisState, config: dict | None = None) -> dict:
    started_at = datetime.now(timezone.utc).isoformat()
    thread_id = (config or {}).get("configurable", {}).get("thread_id", "")
    if thread_id:
        try:
            set_progress(thread_id, "reaction_prescreen", detail="Stage0/1 + 프리스크린(youtube)")
        except Exception as exc:  # noqa: BLE001
            logger.debug("set_progress 실패: %s", exc)
    try:
        from server.graph.reaction.prescreen import (
            build_batches, prescreen_batch_graded, assemble_youtube_threads, load_youtube_raw,
            auto_drop_thread, _PRE_SYS, _PRE_GRADED_SCHEMA)
        from server.graph.reaction.gate import RUBRIC_BODY, score_cut
        from server.graph.reaction.extract import build_keywords
        from server.graph.reaction.llm import MODELS, pmap
        from server.graph.agent_cache import make_cache_context, load_agent_output, store_agent_output
        from server.config import REACTION_SCORE_MIN, REACTION_MAX_YOUTUBE

        kept_vids = ((state.get("reaction_gate") or {}).get("kept_video_ids")) or []
        if not kept_vids:
            return {"reaction_prescreened": {"threads": []},
                    "agent_steps": [_step("skipped", started_at)]}

        st = dict(state)
        kw = build_keywords(st)
        model = MODELS.get("sonnet", "sonnet")
        vmeta = {v["video_id"]: v for v in (state.get("collected_videos") or []) if v.get("video_id")}
        vmeta_url = {vid: m.get("url", "") for vid, m in vmeta.items()}   # source_url 태깅용(§8)
        raws = load_youtube_raw(kept_vids)          # {vid: [raw comment items]}
        # 캐시(재현성·비용) — 프리스크린 등급 판정을 배치별로 기록. 히트 시 LLM 미호출. 등급 스키마로
        # 전환되어 context 지문이 바뀌므로 구 boolean 캐시는 미적중(재실행 1회 필요, 오류 아님).
        cctx = make_cache_context(agent_id="reaction_prescreen", model=model,
                                  system_prompt=_PRE_SYS + RUBRIC_BODY, output_schema=_PRE_GRADED_SCHEMA)

        def _pre_one(b):
            cin = {"video_id": b["video_id"], "ctx": b["ctx"],
                   "threads": [{"thread_id": t["thread_id"], "root_text": t["root_text"],
                                "replies": t["replies"]} for t in b["threads"]]}
            hit = load_agent_output(agent_id="reaction_prescreen", cache_input=cin,
                                    context=cctx, ttl_hours=_TTL_H, logger=logger)
            if hit is not None:
                return {tid: tuple(v) for tid, v in hit["graded"].items()}
            try:
                res = prescreen_batch_graded(b, "cli", model, kw, _TIMEOUT, _MR)  # {tid: (rel, score)}
            except Exception as exc:  # noqa: BLE001
                # 실패(429 등)는 **캐시하지 않는다** → 재실행에서 재시도. 이번 실행만 보수통과(score 0).
                logger.warning("reaction_prescreen: 배치 실패 — 캐시 생략·이번 실행 보수통과: %s",
                               str(exc)[:150])
                return {th["thread_id"]: (True, 0) for th in b["threads"]}
            store_agent_output(agent_id="reaction_prescreen", cache_input=cin, context=cctx,
                               output={"graded": {tid: [rel, sc] for tid, (rel, sc) in res.items()}},
                               logger=logger)
            return res

        threads: list[dict] = []
        n_dropped = 0
        for vid in kept_vids:
            raw = raws.get(vid) or []
            if not raw:
                continue
            m = vmeta.get(vid, {})
            title, desc = m.get("title", ""), (m.get("description", "") or "")
            # Stage0/1 번들 → auto_drop(결정론, 무손실) → 프리스크린(Sonnet, 배치별 캐시)
            batches = build_batches(vid, {"title": title, "text": desc}, raw, kw, _THREAD_CAP)
            for b in batches:
                keep_th = [t for t in b["threads"]
                           if not auto_drop_thread(t["root_text"], t["replies"], kw, _DROP_LEN)]
                n_dropped += len(b["threads"]) - len(keep_th)
                b["threads"] = keep_th
            batches = [b for b in batches if b["threads"]]   # 빈 배치 제거
            parts = pmap(_pre_one, batches, _WORKERS)
            # relevant=keep, score=랭킹·컷 부가필드(선택지 B)
            kept_tids = {tid for part in parts for tid, (ok, _) in (part or {}).items() if ok}
            score_map = {tid: sc for part in parts for tid, (_, sc) in (part or {}).items()}
            # 통과 스레드만 extract member 포맷으로 조립 + channel/source_url·importance_score 태그(§8).
            src = vmeta_url.get(vid) or f"https://www.youtube.com/watch?v={vid}"
            for t in assemble_youtube_threads(raw, kept_tids, title, desc, vid, kw, _MAX_REPLIES):
                t["channel"] = "youtube"
                t["source_url"] = src
                t["importance_score"] = int(score_map.get(t["thread_id"], 0))
                threads.append(t)

        # 점수 컷(결정론·전역): score>=SCORE_MIN 필터 → 유튜브 상한 초과분 score 내림차순 절단(config).
        n_before = len(threads)
        threads = score_cut(threads, REACTION_MAX_YOUTUBE, REACTION_SCORE_MIN)
        logger.info("reaction_prescreen: youtube %d 영상 → 스레드 %d 조립(auto_drop %d) → score>=%d·상한%d 컷 %d",
                    len(kept_vids), n_before, n_dropped, REACTION_SCORE_MIN, REACTION_MAX_YOUTUBE, len(threads))
        return {"reaction_prescreened": {"threads": threads},
                "agent_steps": [_step("completed", started_at)]}
    except Exception as exc:  # noqa: BLE001
        logger.exception("reaction_prescreen 실패")
        return {"agent_steps": [_step("failed", started_at, f"{type(exc).__name__}: {exc}")],
                "errors": [{"node": "reaction_prescreen_node", "error": str(exc)[:200],
                            "timestamp": datetime.now(timezone.utc).isoformat()}]}
