#!/usr/bin/env python3
"""
scripts/build_insight_report.py — 최종 인사이트 리포트(축약판)를 디스크 캐시·스냅샷으로 생성

파이프라인을 다시 돌리지 않고, 이미 있는 산출물로 state 를 조립해 insight_report_node 만 실행한다.
  - reaction_clustered : data/cache/agent_outputs/reaction_cluster/<key>.json   (--cluster, 기본 09-05)
  - comparison_matrix  : data/reports/<thread>.json                             (--matrix, 기본 09-10)
  - reaction_insight   : docs/mockups/reaction_fixture.json 또는 스냅샷          (--reaction)
결과: data/reports/insight_<UTC>.json (리포트 스냅샷 형식 — 뷰어에서 바로 열림)

사용
  python scripts/build_insight_report.py                 # 캐시 히트면 LLM 0회
  python scripts/build_insight_report.py --refresh       # 두 agent 모두 CLI 로 재생성
  python scripts/build_insight_report.py --external data/reports/external_20261024.json   # 외부 조사 결합(전체판)
  python scripts/build_insight_report.py --proxy-dir D   # CLI 없이: D/<agent>.json 을 출력으로 사용,
                                                         #   없으면 D/<agent>.payload.json·prompt.md 를 쓰고 종료(3)
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from server.config import AGENT_OUTPUT_CACHE_DIR  # noqa: E402
import server.graph.nodes.insight_report_node as node  # noqa: E402


class ProxyMissing(Exception):
    pass


def load_state(cluster: str, matrix: str, reaction: str, external: str | None = None) -> dict:
    cl = next((json.loads(p.read_text(encoding="utf-8"))
               for p in (AGENT_OUTPUT_CACHE_DIR / "reaction_cluster").glob(f"{cluster}*.json")), None)
    if cl is None:
        sys.exit(f"군집 캐시 {cluster}* 없음")
    snap = json.loads((REPO / "data/reports" / f"{matrix}").read_text(encoding="utf-8")) \
        if matrix.endswith(".json") else \
        json.loads(next((REPO / "data/reports").glob(f"{matrix}*.json")).read_text(encoding="utf-8"))
    rx = json.loads(Path(reaction).read_text(encoding="utf-8"))
    ri = (rx.get("report_outputs") or {}).get("reaction_insight") or rx.get("reaction_insight")
    taxonomy = json.loads((REPO / "data/taxonomy/3_slug.json").read_text(encoding="utf-8"))
    return {
        "thread_id": f"insight-offline-{cl['cache_key'][:8]}",
        "raw_query": snap.get("raw_query"), "project_id": snap.get("project_id"),
        "domain_taxonomy": taxonomy,
        "product_profiles": snap["product_profiles"], "own_product": snap["own_product"],
        "reaction_clustered": cl["output"]["clustered"], "reaction_axis": cl["output"].get("axis") or {},
        "report_outputs": {"comparison_matrix": snap["report_outputs"]["comparison_matrix"],
                           "reaction_insight": ri},
        "_sources": {"cluster": cl["cache_key"][:16], "cluster_updated_at": cl.get("updated_at"),
                     "matrix_snapshot": snap.get("thread_id"), "matrix_saved_at": snap.get("saved_at"),
                     "reaction": str(Path(reaction).name)} | ({"external": Path(external).name} if external else {}),
        **({"external_research": json.loads(Path(external).read_text(encoding="utf-8"))["external_research"]}
           if external else {}),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cluster", default="b6030dbb8c", help="reaction_cluster 캐시 키 접두 (기본: 09-05 재키잉본)")
    ap.add_argument("--matrix", default="6ec75128", help="비교 매트릭스 스냅샷 thread 접두 또는 파일명")
    ap.add_argument("--reaction", default=str(REPO / "docs/mockups/reaction_fixture.json"),
                    help="reaction_insight 가 든 JSON (fixture 또는 스냅샷)")
    ap.add_argument("--external", help="외부 조사 결과(data/reports/external_*.json — build_external_research.py 산출)")
    ap.add_argument("--refresh", action="store_true", help="캐시 무시하고 agent 재생성")
    ap.add_argument("--proxy-dir", help="CLI 대신 이 폴더의 <agent>.json 을 agent 출력으로 사용")
    ap.add_argument("--out", help="출력 스냅샷 경로 (기본 data/reports/insight_<UTC>.json)")
    a = ap.parse_args()

    state = load_state(a.cluster, a.matrix, a.reaction, a.external)
    proxied = []
    if a.proxy_dir:
        pdir = Path(a.proxy_dir); pdir.mkdir(parents=True, exist_ok=True)

        def proxy(agent_id, system_prompt, schema, prompt):
            f = pdir / f"{agent_id}.json"
            if f.exists():
                proxied.append(agent_id)
                return json.loads(f.read_text(encoding="utf-8"))
            (pdir / f"{agent_id}.prompt.md").write_text(
                f"# system\n\n{system_prompt}\n\n# user\n\n{prompt}\n", encoding="utf-8")
            payload = json.loads(prompt[prompt.index("```json") + 7: prompt.rindex("```")])
            (pdir / f"{agent_id}.payload.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                                                          encoding="utf-8")
            (pdir / f"{agent_id}.schema.json").write_text(json.dumps(schema, ensure_ascii=False, indent=1),
                                                         encoding="utf-8")
            raise ProxyMissing(agent_id)
        node.LLM_OVERRIDE = proxy

    # 노드 내부 예외 처리(make_error_result)를 우회해 ProxyMissing 을 그대로 받기 위해 단계 직접 호출
    try:
        stats = node.build_insight_stats(state)
        raw_ev, src_ev = node.run_agent(node.EVIDENCE_AGENT, node.evidence_payload(stats), a.refresh)
        evidence, warn_ev = node.resolve_evidence(raw_ev, stats)
        ext = node.prepare_external(state, stats)
        report, src_rp = node.run_agent(node.REPORT_AGENT, node.report_payload(stats, evidence, ext), a.refresh)
    except ProxyMissing as m:
        print(f"[proxy] {m} 출력 없음 → {a.proxy_dir}/{m}.payload.json · prompt.md 작성. 출력 JSON 을 "
              f"{a.proxy_dir}/{m}.json 으로 넣고 다시 실행하세요.")
        return 3
    lint = node.lint_prose(report)
    gen = {node.EVIDENCE_AGENT: src_ev, node.REPORT_AGENT: src_rp}
    for ag in (node.EVIDENCE_AGENT, node.REPORT_AGENT):
        if ag in proxied or (a.proxy_dir and (Path(a.proxy_dir) / f"{ag}.json").exists()):
            _mark_cache(ag, stats, evidence, ext)   # CLI 대신 세션에서 같은 프롬프트·스키마로 대행 작성
        who = _authored_by(ag, stats, evidence, ext)
        if who:
            gen[ag] = f"{gen[ag]} · {who}"
    envelope = node.assemble(stats, evidence, report, lint + warn_ev, gen | {"inputs": state["_sources"]}, ext)

    out = Path(a.out).resolve() if a.out else REPO / "data/reports" / \
        f"insight_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    snap = {"saved_at": datetime.now(timezone.utc).isoformat(), "thread_id": state["thread_id"],
            "project_id": state.get("project_id"), "raw_query": state.get("raw_query"),
            "report_outputs": {**state["report_outputs"], node.REPORT_TYPE: envelope},
            "product_profiles": state["product_profiles"], "own_product": state["own_product"]}
    out.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"✓ {out.relative_to(REPO) if out.is_relative_to(REPO) else out} — 분류={gen[node.EVIDENCE_AGENT]} · 종합={gen[node.REPORT_AGENT]} · "
          f"경고 {len(lint + warn_ev)}건 · 점수 {envelope['evaluation_score']}")
    for w in lint + warn_ev:
        print("  ⚠", w)
    return 0


def _entry(agent_id: str, stats: dict, evidence: dict, ext=None):
    from server.graph.agent_cache import _entry_path, _read_entry, make_cache_key
    payload = node.evidence_payload(stats) if agent_id == node.EVIDENCE_AGENT else node.report_payload(stats, evidence, ext)
    cin, ctx, _, _ = node.cache_handles(agent_id, payload)
    p = _entry_path(agent_id, make_cache_key(agent_id, cin, ctx))
    return p, _read_entry(p)


def _authored_by(agent_id: str, stats: dict, evidence: dict, ext=None) -> str:
    _, e = _entry(agent_id, stats, evidence, ext)
    return "session-proxy 작성" if e and str(e.get("authored_by", "")).startswith("session-proxy") else ""


def _mark_cache(agent_id: str, stats: dict, evidence: dict, ext=None) -> None:
    """대행 작성 캐시 엔트리에 출처 표시(authored_by) — 이후 실행도 이 사실을 추적할 수 있게."""
    from server.graph.agent_cache import _entry_path, _read_entry, _write_entry, make_cache_key
    payload = node.evidence_payload(stats) if agent_id == node.EVIDENCE_AGENT else node.report_payload(stats, evidence, ext)
    cin, ctx, _, _ = node.cache_handles(agent_id, payload)
    p = _entry_path(agent_id, make_cache_key(agent_id, cin, ctx))
    e = _read_entry(p)
    if e is not None:
        e["authored_by"] = "session-proxy (Claude CLI 미가용 환경 — 같은 프롬프트·스키마로 대행 작성)"
        _write_entry(p, e)


if __name__ == "__main__":
    raise SystemExit(main())
