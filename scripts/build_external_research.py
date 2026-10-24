#!/usr/bin/env python3
"""
scripts/build_external_research.py — 외부 조사(이력·재무·시장)를 스냅샷 후보로 실행해 저장

  python scripts/build_external_research.py                     # 캐시 히트면 검색·LLM 0회
  python scripts/build_external_research.py --refresh           # LLM 재탐색(WebSearch·WebFetch) + 추출 agent 재생성
  python scripts/build_external_research.py --proxy-dir D       # CLI 없이: D/docs_<track>.json 을 문서 묶음으로,
                                                                #   D/<agent>.json 을 agent 출력으로 사용. 없으면
                                                                #   D/<agent>.payload.json·prompt.md 를 쓰고 종료(3)
결과: data/reports/external_<as_of>.json  →  build_insight_report.py --external 로 최종 리포트에 결합
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from server.graph.agent_cache import _entry_path, _read_entry, _write_entry, make_cache_key  # noqa: E402
from server.graph.external import agent as ext_agent, collect as ext_collect  # noqa: E402
from server.graph.external.queries import TRACKS, build_queries, candidates  # noqa: E402
import server.graph.nodes.external_research_node as node  # noqa: E402

PROXY_TAG = "session-proxy (WebSearch/WebFetch 수집 · 같은 프롬프트·스키마로 세션 작성)"


class ProxyMissing(Exception):
    pass


def load_state(matrix: str) -> dict:
    p = REPO / "data/reports" / matrix if matrix.endswith(".json") else next((REPO / "data/reports").glob(f"{matrix}*.json"))
    snap = json.loads(p.read_text(encoding="utf-8"))
    return {"product_profiles": snap["product_profiles"], "own_product": snap["own_product"],
            "raw_query": snap.get("raw_query"), "_snapshot": p.name}


def _mark(agent_id: str, cin: dict, ctx: dict) -> None:
    path = _entry_path(agent_id, make_cache_key(agent_id, cin, ctx))
    e = _read_entry(path)
    if e is not None and not str(e.get("authored_by", "")).startswith("session-proxy"):
        e["authored_by"] = PROXY_TAG
        _write_entry(path, e)


def _who(agent_id: str, cin: dict, ctx: dict) -> str:
    e = _read_entry(_entry_path(agent_id, make_cache_key(agent_id, cin, ctx)))
    return "session-proxy 작성" if e and str(e.get("authored_by", "")).startswith("session-proxy") else ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--matrix", default="6ec75128", help="후보 목록을 가져올 스냅샷(thread 접두 또는 파일명)")
    ap.add_argument("--as-of", default=None, help="기준일 YYYY-MM-DD (기본 오늘 UTC)")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--proxy-dir")
    ap.add_argument("--out")
    a = ap.parse_args()

    state = load_state(a.matrix)
    as_of = date.fromisoformat(a.as_of) if a.as_of else datetime.now(timezone.utc).date()
    pdir = Path(a.proxy_dir) if a.proxy_dir else None
    if pdir:
        pdir.mkdir(parents=True, exist_ok=True)

        def docs_override(track, queries):
            f = pdir / f"docs_{track}.json"
            if not f.exists():
                raise ProxyMissing(f"docs_{track}")
            return json.loads(f.read_text(encoding="utf-8"))

        def llm_override(agent_id, system_prompt, schema, prompt):
            f = pdir / f"{agent_id}.json"
            if f.exists():
                return json.loads(f.read_text(encoding="utf-8"))
            (pdir / f"{agent_id}.prompt.md").write_text(f"# system\n\n{system_prompt}\n\n# user\n\n{prompt}\n", encoding="utf-8")
            (pdir / f"{agent_id}.payload.json").write_text(
                json.dumps(json.loads(prompt[prompt.index("```json") + 7: prompt.rindex("```")]), ensure_ascii=False, indent=1),
                encoding="utf-8")
            raise ProxyMissing(agent_id)
        ext_collect.COLLECT_OVERRIDE, ext_agent.LLM_OVERRIDE = docs_override, llm_override

    cands, qs = candidates(state), build_queries(state, as_of)
    tracks, missing = {}, []
    for t in TRACKS:                       # 순차 실행 — ProxyMissing 을 트랙별로 받기 위해 노드 내부 격리 우회
        try:
            tracks[t] = node.run_track(t, qs[t], cands, as_of, a.refresh)
        except ProxyMissing as m:
            missing.append(str(m)); continue
        ws = node.window_start(as_of).isoformat()
        if pdir:                                          # 대행 수집본: 탐색 agent 대신 문서 파일을 그대로 사용
            docs = json.loads((pdir / f"docs_{t}.json").read_text(encoding="utf-8"))
            tracks[t]["generation"]["collect"] += " · session-proxy 작성"
        else:                                             # 탐색 agent 캐시 히트 — 추출 agent 캐시 키 재구성용
            docs, _, _ = ext_collect.collect(t, qs[t], cands, as_of.isoformat(), ws)
            scin, sctx, _, _ = ext_agent.cache_handles(ext_collect.SEARCH_AGENT,
                                                       ext_collect.search_payload(t, qs[t], cands, as_of.isoformat(), ws))
            if _who(ext_collect.SEARCH_AGENT, scin, sctx):
                tracks[t]["generation"]["collect"] += " · session-proxy 작성"
        ag = node.AGENT_OF[t]
        acin, actx, _, _ = ext_agent.cache_handles(ag, node.agent_payload(t, cands, docs, as_of))
        if pdir and (pdir / f"{ag}.json").exists():
            _mark(ag, acin, actx)
        who = _who(ag, acin, actx)
        if who:
            tracks[t]["generation"][ag] += f" · {who}"
    if missing:
        print(f"[proxy] 출력 없음: {', '.join(missing)} → {pdir}/ 에 payload·prompt 작성. JSON 을 넣고 다시 실행하세요.")
        return 3
    er = {"version": node.VERSION, "as_of": as_of.isoformat(), "window_start": node.window_start(as_of).isoformat(),
          "candidates": cands, "tracks": tracks, "generated_at": datetime.now(timezone.utc).isoformat(),
          "inputs": {"candidates_from": state["_snapshot"]}}
    out = Path(a.out).resolve() if a.out else REPO / "data/reports" / f"external_{as_of:%Y%m%d}.json"
    out.write_text(json.dumps({"saved_at": er["generated_at"], "external_research": er}, ensure_ascii=False, indent=1), encoding="utf-8")
    for t, r in tracks.items():
        print(f"  {t:8s} {r['status']:9s} 문서 {len(r['docs'])} · 경고 {len(r['warnings'])} · {r['generation']}")
        for w in r["warnings"]:
            print("     ⚠", w)
    print(f"✓ {out.relative_to(REPO) if out.is_relative_to(REPO) else out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
