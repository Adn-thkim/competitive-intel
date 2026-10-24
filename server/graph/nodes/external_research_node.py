"""
server/graph/nodes/external_research_node.py
--------------------------------------------
외부 조사 노드 — 최종 인사이트 리포트의 외부 데이터 블록(시장 흐름·변경 이력·재무 지표·경쟁사 액션·사업 구조)을 공급.

위치 (graph 배선 — 미적용, 아래 WIRING 참고)
  official_content_collection(product_profiles) ─→ external_research_run ─┐
  comparison_matrix ─────────────────────────────────────┼─→ insight_report → END
  reaction_report ───────────────────────────────────────┘
  매트릭스·반응 파이프라인과 입력이 겹치지 않아 병렬 실행된다(후보 목록만 필요).

입력 state : product_profiles · own_product   (선택: raw_query)
출력       : external_research (replace) · agent_steps · errors

트랙 3개(병렬) — 각 트랙은 같은 4단계
  1) 검색어 생성   server/graph/external/queries.py        결정론
  2) 문서 수집     server/graph/external/collect.py        LLM 탐색(CLI + WebSearch·WebFetch) → 원문 대조
  3) 추출 agent    agents/external_{history,finance,market} 문서에 적힌 사실만, doc_id·원문 인용 필수
  4) 검증·집계     server/graph/external/compute.py        인용 실재 확인 · 단위 정규화 · 횟수/시계열/점유율 계산
캐시 TTL: server/cache_ttls.yaml 의 external_search_hours(탐색 agent) / external_fetch_hours(원문) /
          external_research_hours(추출 agent) — 모두 7200h. 캐시 키에 기준일은 넣지 않는다(agent.CACHE_EXCLUDE).
한 트랙이 실패해도 나머지는 반환한다(트랙별 status). 결과 없는 트랙은 리포트에서 pending 으로 남는다.

WIRING (server/graph/graph.py 에 적용 시)
  from server.graph.nodes.external_research_node import external_research_node
  # 노드명은 state 키(external_research)와 같을 수 없다(LangGraph 제약) → external_research_run
  builder.add_node("external_research_run", external_research_node)
  builder.add_edge("official_content_collection", "external_research_run")   # product_profiles 산출 직후
  builder.add_edge(["comparison_matrix", "reaction_report", "external_research_run"], "insight_report")
  (server/graph/graph.py 에 적용됨)
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import Any

from server.graph.external import agent as ext_agent
from server.graph.external import collect as ext_collect
from server.graph.external.compute import finance_block, history_block, market_block
from server.graph.external.queries import TRACKS, build_queries, candidates
from server.graph.state import DomainAnalysisState

logger = logging.getLogger(__name__)

NODE = "external_research"
VERSION = "external_research v0.1"
WINDOW_MONTHS = 36
AGENT_OF = {"history": "external_history", "finance": "external_finance", "market": "external_market"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def window_start(as_of: date, months: int = WINDOW_MONTHS) -> date:
    y, m = divmod(as_of.year * 12 + as_of.month - 1 - months, 12)
    return date(y, m + 1, 1)


def agent_payload(track: str, cands: list[dict], docs: list[dict], as_of: date) -> dict:
    return {"as_of": as_of.isoformat(), "window_start": window_start(as_of).isoformat(),
            "candidates": [{"cid": c["cid"], "name": c["name"], "provider": c["provider"], "is_own": c["is_own"]}
                           for c in cands],
            "docs": ext_collect.agent_view(docs)}


def run_track(track: str, queries: list[dict], cands: list[dict], as_of: date, refresh: bool = False) -> dict:
    """한 트랙 전 단계. 예외는 호출자가 받는다(노드는 트랙 단위로 격리)."""
    docs, how_docs, meta = ext_collect.collect(track, queries, cands, as_of.isoformat(),
                                               window_start(as_of).isoformat(), refresh)
    if not docs:
        return {"status": "empty", "queries": queries, "docs": [], "warnings": ["수집 문서 없음"] + meta["gaps"],
                "generation": {"collect": how_docs}, "search": meta}
    out, how_llm = ext_agent.run(AGENT_OF[track], agent_payload(track, cands, docs, as_of), refresh)
    dmap = {d["doc_id"]: d for d in docs}
    if track == "history":
        block, warn = history_block(out, dmap, cands, as_of, window_start(as_of))
    elif track == "finance":
        block, warn = finance_block(out, dmap, cands)
    else:
        block, warn = market_block(out, dmap, as_of)
    used = _used_doc_ids(block)
    bad = [d["doc_id"] for d in docs if d.get("source_check") == "불일치"]
    if bad:
        warn.append(f"원문 대조 불일치 문서 {len(bad)}건({', '.join(bad)}) — 해당 근거는 미검증")
    return {"status": "completed", "queries": queries, "block": block, "warnings": warn, "search": meta,
            "docs": [{k: d.get(k) for k in ("doc_id", "url", "title", "publisher", "published_at", "source_check",
                                             "excerpts_found")} | {"used": d["doc_id"] in used} for d in docs],
            "generation": {"collect": how_docs, AGENT_OF[track]: how_llm}}


def _used_doc_ids(obj: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("doc_id",) and isinstance(v, str):
                found.add(v)
            elif k == "doc_ids" and isinstance(v, list):
                found.update(x for x in v if isinstance(x, str))
            else:
                found |= _used_doc_ids(v)
    elif isinstance(obj, list):
        for v in obj:
            found |= _used_doc_ids(v)
    return found


def build_external_research(state: dict, as_of: date | None = None, refresh: bool = False,
                            tracks: tuple[str, ...] = TRACKS) -> dict:
    as_of = as_of or datetime.now(timezone.utc).date()
    cands = candidates(state)
    qs = build_queries(state, as_of)

    def one(t):
        try:
            return t, run_track(t, qs[t], cands, as_of, refresh)
        except Exception as exc:  # noqa: BLE001 — 트랙 격리
            logger.exception("external_research %s 실패", t)
            return t, {"status": "failed", "queries": qs[t], "docs": [], "warnings": [f"{type(exc).__name__}: {exc}"],
                       "generation": {}}
    with ThreadPoolExecutor(max_workers=len(tracks)) as ex:
        res = dict(ex.map(one, tracks))
    return {"version": VERSION, "as_of": as_of.isoformat(), "window_start": window_start(as_of).isoformat(),
            "candidates": cands, "tracks": res, "generated_at": _now()}


def external_research_node(state: DomainAnalysisState, config: dict | None = None) -> dict:
    started = _now()
    refresh = bool(((config or {}).get("configurable") or {}).get("external_refresh"))
    try:
        if not state.get("product_profiles"):
            raise ValueError("product_profiles 없음 — 후보 확정 이후에 실행해야 합니다")
        er = build_external_research(dict(state), refresh=refresh)
    except Exception as exc:  # noqa: BLE001
        msg = f"{type(exc).__name__}: {exc}"
        return {"agent_steps": [{"node": NODE, "status": "failed", "started_at": started, "finished_at": _now(),
                                 "error_message": msg}],
                "errors": [{"node": NODE, "error": msg, "timestamp": _now()}]}
    st = {t: r["status"] for t, r in er["tracks"].items()}
    step = {"node": NODE, "status": "completed" if all(v == "completed" for v in st.values()) else "partial",
            "started_at": started, "finished_at": _now(), "tracks": st,
            "docs": {t: len(r["docs"]) for t, r in er["tracks"].items()},
            "warnings": sum(len(r["warnings"]) for r in er["tracks"].values())}
    out: dict = {"external_research": er, "agent_steps": [step]}
    bad = [t for t, v in st.items() if v != "completed"]
    if bad:
        out["errors"] = [{"node": NODE, "error": f"트랙 {', '.join(bad)} 미완료", "timestamp": _now()}]
    return out
