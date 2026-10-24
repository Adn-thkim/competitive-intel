"""server/graph/nodes/reaction_extract_node.py — ⑤ Stage2① 의견 units + 질문 questions 추출.

통합계획 §1-B·§6-3. 이식 원본: scripts/poc_opinion_units.py(youtube) +
poc_opinion_units_community.py(community, build_threads + title-member) + test_gate_prescreen_v2.gate_videos.

이행기(병행): state 의 신 키 `reaction_units`·`reaction_questions` **에만** 기록한다. 구
reaction_analysis 경로는 건드리지 않는다. graph 미배선(§4-2) — 배선·실환경 테스트는 §4-4 이후.

2026-08 리팩터(§8): **스레드 조립은 상류 노드가 완료**하고 본 노드는 "threads 읽어 추출만" 한다.
- youtube:   영상 게이트 → prescreen 노드 → `reaction_prescreened.threads`(channel/source_url 태깅 완료)
- community: `reaction_community_gate_node`(게이트+build_threads) → `reaction_community_prescreened.threads`
본 노드: 두 채널 threads 를 읽어 채널별 개별 추출(청크 미혼합, dedup addendum 공통) → unit/question concat(§6-2).
unit/question 은 스레드에 붙은 channel/source_url 을 상속. 추출 청크 캐시는 채널별 분리 기록. 산출 스키마 동일 → ⑥.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from server.graph.progress_store import set_progress
from server.graph.state import AgentStep, DomainAnalysisState

logger = logging.getLogger(__name__)

_DEDUP_ADDENDUM = (
    "[제목 멤버·스레드 내 dedup] '[제목]' 멤버는 게시글 제목(프레이밍이자 인용 소스). 지시어를 제목의 "
    "비교 대상으로 해소하라. dedup 은 같은 스레드 안에서만 — target×aspect×polarity 동일 시 1 unit "
    "으로 병합(quote 본문 우선), 구별·상충은 보존. 게시글 간 병합 금지(집계 단계 몫)."
)


def _step(status: str, started_at: str, error: str = "") -> AgentStep:
    s: AgentStep = {"step_name": "ReactionExtract", "status": status,
                    "started_at": started_at,
                    "finished_at": datetime.now(timezone.utc).isoformat()}
    if error:
        s["error_message"] = error[:300]
    return s


def reaction_extract_node(state: DomainAnalysisState, config: dict | None = None) -> dict:
    started_at = datetime.now(timezone.utc).isoformat()
    thread_id = (config or {}).get("configurable", {}).get("thread_id", "")
    if thread_id:
        try:
            set_progress(thread_id, "reaction_extract", detail="의견 unit·질문 추출")
        except Exception as exc:  # noqa: BLE001
            logger.debug("set_progress 실패: %s", exc)

    try:
        from server.graph.reaction.extract import (
            extract, candidate_context, build_service_index, _split_by_chars,
            build_keywords, _SYS, _SCHEMA)
        from server.graph.reaction.llm import pmap as _pmap, MODELS, ABORT
        from server.graph.agent_cache import (
            make_cache_context, load_agent_output, store_agent_output)

        # 세션 한도(429) 조기중단 플래그를 이 실행 시작 시 초기화 — 지속 프로세스에서 이전 run 의
        # ABORT 가 남아 재실행 추출이 통째로 스킵되는 것을 방지(실패분 재시도 보장).
        ABORT.clear()
        st = dict(state)
        kw = build_keywords(st)
        model_str = MODELS.get("sonnet", "sonnet")
        # 정본 컨텍스트 = **compact**(설계 §49 REACTION_CONTEXT_MODE=compact / resketch: 비용 83%↓·정확도 유지).
        # 카드별 부속 서비스명 인덱스(1회 추출·캐시)를 재료로 카드명+서비스만 주입(프로필 전문 미주입).
        svc_index = build_service_index(st, "cli", model_str, 600, 2)
        base_dctx = candidate_context(st, "compact", svc_index)

        def _extract_channel(threads, dctx, tid2meta, channel):
            """스레드 리스트 → (units, questions). **스레드 단위 캐시**(청크 단위 아님):
            각 스레드를 개별 조회 → 미스분만 청크로 묶어 LLM 추출 → **성공 스레드만 개별 저장**.
            효과: (1) 상한·순서 변화로 청크가 밀려도 스레드 캐시는 그대로 유효(재추출 최소),
            (2) 부분 429/실패 시에도 성공 스레드는 저장돼, 재실행 때 **실패분만** 재시도.
            context 지문은 채널별(dctx)로 분리."""
            u_out, q_out = [], []
            if not threads:
                return u_out, q_out
            agent_id = f"reaction_extract_{channel}"
            cctx = make_cache_context(agent_id=agent_id, model=model_str,
                                      system_prompt=(dctx + "\n\n" + _SYS), output_schema=_SCHEMA)

            def _tkey(t):
                return {"thread": {"thread_id": t["thread_id"], "members": t["members"]}}

            results, misses = {}, []
            for t in threads:
                hit = load_agent_output(agent_id=agent_id, cache_input=_tkey(t), context=cctx,
                                        logger=logger, shard_prefix=2)   # 채널 폴더 아래 해시-접두 세분 샤딩
                if hit is not None:
                    results[t["thread_id"]] = hit.get("out")   # 스레드 캐시 히트 → 재추출 없음
                else:
                    misses.append(t)

            logger.info("[reaction_extract:%s] 스레드 %d — 캐시 히트 %d · 재추출 %d",
                        channel, len(threads), len(threads) - len(misses), len(misses))
            if misses:   # 미스분만 청크로 묶어 LLM(호출 효율) → 성공 스레드 개별 저장
                batches = [{"video_id": ch[0]["video_id"], "threads": ch}
                           for ch in _split_by_chars(misses, 1500, 0)]

                def _one(b):
                    part, _err, _ = extract(b, "cli", "sonnet", kw, 600, 2, dctx)
                    local = {}
                    for t in b["threads"]:
                        r = (part or {}).get(t["thread_id"])
                        if r is not None:            # 성공분만 저장(실패=None → 미저장 → 다음 실행 재시도)
                            store_agent_output(agent_id=agent_id, cache_input=_tkey(t),
                                               context=cctx, output={"out": r}, logger=logger, shard_prefix=2)
                        local[t["thread_id"]] = r
                    return local

                for part in _pmap(_one, batches, 4):
                    for tid, r in (part or {}).items():
                        results[tid] = r

            for t in threads:
                tid = t["thread_id"]
                r = results.get(tid)
                if not r:
                    continue
                meta = tid2meta.get(tid, {"channel": channel, "source_url": ""})
                for u in r.get("units", []):
                    u_out.append(dict(u, thread_id=tid, **meta))
                for q in r.get("questions", []):
                    q_out.append(dict(q, thread_id=tid, **meta))
            return u_out, q_out

        # dedup addendum 은 **양 채널 공통 적용**(옵션 B) — 스레드 내 중복 unit 병합. 채널별로 청크는
        # 여전히 분리 추출되므로 청크에 두 채널이 섞이지 않는다. addendum 의 '[제목] 멤버' 지시는 유튜브엔
        # 해당 멤버(t0)가 없어 자연어상 무효(inert) — 코드가 t0 존재를 가정하지 않아 에러 없음.
        dctx = base_dctx + "\n\n" + _DEDUP_ADDENDUM

        # ── 스레드는 상류 노드가 조립·태깅 완료(§8) → extract 는 **읽어서 추출만** 한다 ──
        # youtube: reaction_prescreened.threads / community: reaction_community_prescreened.threads
        # 각 thread 에 channel·source_url 이 이미 붙어 있음(파이썬 병합 태깅 완료).
        yt_threads = list(((state.get("reaction_prescreened") or {}).get("threads")) or [])
        comm_threads = list(((state.get("reaction_community_prescreened") or {}).get("threads")) or [])

        def _meta_map(threads):
            return {t["thread_id"]: {"channel": t.get("channel", ""),
                                     "source_url": t.get("source_url", "")} for t in threads}

        # 채널별 개별 추출(청크 미혼합 유지). 병합(concat)은 추출 이후(§6-2).
        comm_u, comm_q = _extract_channel(comm_threads, dctx, _meta_map(comm_threads), "community")
        yt_u, yt_q = _extract_channel(yt_threads, dctx, _meta_map(yt_threads), "youtube")

        # ── 병합 지점: 추출 이후 unit/question concat(§6-2) → ⑥ 군집으로 ──
        units = comm_u + yt_u
        questions = comm_q + yt_q

        logger.info("reaction_extract: units %d(comm %d+yt %d) · questions %d — 스레드 comm %d+yt %d",
                    len(units), len(comm_u), len(yt_u), len(questions), len(comm_threads), len(yt_threads))
        return {"reaction_units": units, "reaction_questions": questions,
                "agent_steps": [_step("completed", started_at)]}
    except Exception as exc:  # noqa: BLE001
        logger.exception("reaction_extract 실패")
        return {"agent_steps": [_step("failed", started_at, f"{type(exc).__name__}: {exc}")],
                "errors": [{"node": "reaction_extract_node", "error": str(exc)[:200],
                            "timestamp": datetime.now(timezone.utc).isoformat()}]}
