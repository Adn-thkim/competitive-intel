#!/usr/bin/env python3
"""
저장된 reaction_cluster 캐시로 고객 반응 인사이트 리포트를 복제한다.
=====================================================================
gate → prescreen → extract → cluster 를 다시 돌리지 않는다.
⑥ cluster 산출물이 이미 디스크에 있으므로, ⑦ reaction_report_node 만 직접 호출한다.

왜 가능한가
-----------
reaction_report_node 가 state 에서 읽는 키는 세 개뿐이다.
    reaction_clustered  ← cluster 캐시의 output.clustered
    product_profiles    ← data/reports/*.json 스냅샷
    own_product         ← 같은 스냅샷
그리고 반환값이 이미 프런트가 쓰는 형태다.
    {"report_outputs": {"reaction_insight": <envelope>}, ...}

LLM 비용
--------
노드는 LLM 을 4회 호출하지만(aspect_analysis · aspect_desc · question_analysis · overview)
모두 load_agent_output 캐시를 먼저 조회하고, 실패 시 해당 서술 필드만 비운다.
  - 캐시 적중 → 호출 0회
  - 캐시 불일치 → Claude CLI 호출 발생 (--no-llm 으로 차단 가능)
  - 호출 실패  → 집계는 그대로, 서술 필드만 빈 값

사용법
------
    cd <repo>/competitive-intel
    python scripts/rebuild_reaction_insight.py                    # 09-05 캐시 (기본)
    python scripts/rebuild_reaction_insight.py --cluster 87b209a  # 08-25 캐시
    python scripts/rebuild_reaction_insight.py --list             # 캐시 목록만
    python scripts/rebuild_reaction_insight.py --no-llm           # LLM 호출 차단 (집계만)

산출
----
    docs/mockups/reaction_fixture.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# ── 리포지토리 루트를 sys.path 에 추가 (server.* import 를 위해) ──────────────
# 이 파일 위치: <repo>/competitive-intel/scripts/rebuild_reaction_insight.py
BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

CLUSTER_DIR = BASE / "data" / "cache" / "agent_outputs" / "reaction_cluster"
REPORTS_DIR = BASE / "data" / "reports"
OUT_PATH    = BASE / "docs" / "mockups" / "reaction_fixture.json"

DEFAULT_CLUSTER = "998c7552"   # 2026-09-05 · units 543 · questions 170 · ATM 출금 113

# 서술 필드 — 채워졌는지로 LLM 캐시 적중을 판정한다.
NARRATIVE_KEYS = ("aspect_analysis", "aspect_descriptions",
                  "question_analysis", "question_overview")


def die(msg: str) -> None:
    print(f"\n✖ {msg}", file=sys.stderr)
    sys.exit(1)


def load_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        die(f"{p} 읽기 실패 — {type(exc).__name__}: {exc}")


def list_clusters() -> list[tuple[Path, dict]]:
    """cluster 캐시를 (경로, 메타) 로 나열한다. 최신순."""
    if not CLUSTER_DIR.is_dir():
        die(f"cluster 캐시 디렉터리가 없습니다: {CLUSTER_DIR}")
    rows = []
    for p in sorted(CLUSTER_DIR.glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        cl = (d.get("output") or {}).get("clustered") or {}
        rows.append((p, {
            "created_at": (d.get("created_at") or "")[:19],
            "units":      len(cl.get("units") or []),
            "questions":  len(cl.get("questions") or []),
            "supers":     len(cl.get("super_aspects") or []),
            "axis_source": cl.get("axis_source", ""),
        }))
    rows.sort(key=lambda r: r[1]["created_at"], reverse=True)
    return rows


def print_clusters(rows) -> None:
    print(f"\n{'해시(앞 8자)':<14} {'생성일시':<20} {'units':>6} {'questions':>10} "
          f"{'supers':>7}  axis")
    print("─" * 74)
    for p, m in rows:
        print(f"{p.stem[:8]:<14} {m['created_at']:<20} {m['units']:>6} "
              f"{m['questions']:>10} {m['supers']:>7}  {m['axis_source']}")


def pick_cluster(prefix: str) -> Path:
    hits = sorted(CLUSTER_DIR.glob(f"{prefix}*.json"))
    if not hits:
        print_clusters(list_clusters())
        die(f"'{prefix}' 로 시작하는 cluster 캐시가 없습니다.")
    if len(hits) > 1:
        die(f"'{prefix}' 가 {len(hits)}개와 일치합니다 — 더 긴 접두사를 주세요: "
            + ", ".join(h.stem[:12] for h in hits))
    return hits[0]


def latest_snapshot() -> Path:
    """product_profiles·own_product 를 가진 가장 최근 리포트 스냅샷."""
    if not REPORTS_DIR.is_dir():
        die(f"스냅샷 디렉터리가 없습니다: {REPORTS_DIR}\n"
            "  서버의 리포트 스냅샷 기능(server/graph/api.py)이 한 번은 실행돼야 합니다.")
    cands = []
    for p in REPORTS_DIR.glob("*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if d.get("product_profiles") and d.get("own_product"):
            cands.append((d.get("saved_at") or "", p))
    if not cands:
        die("product_profiles·own_product 를 가진 스냅샷이 없습니다.")
    cands.sort(reverse=True)
    return cands[0][1]


def main() -> None:
    ap = argparse.ArgumentParser(description="cluster 캐시로 고객 반응 인사이트 리포트 복제")
    ap.add_argument("--cluster", default=DEFAULT_CLUSTER,
                    help=f"cluster 캐시 해시 접두사 (기본 {DEFAULT_CLUSTER})")
    ap.add_argument("--snapshot", default="",
                    help="product_profiles 를 가져올 스냅샷 경로 (기본: 가장 최근)")
    ap.add_argument("--out", default=str(OUT_PATH), help=f"산출 경로 (기본 {OUT_PATH})")
    ap.add_argument("--list", action="store_true", help="cluster 캐시 목록만 출력하고 종료")
    ap.add_argument("--no-llm", action="store_true",
                    help="LLM 호출 차단 — 캐시 미적중 시 서술 필드를 비운 채 집계만 산출")
    args = ap.parse_args()

    if args.list:
        print_clusters(list_clusters())
        return

    # ── 1. 입력 로드 ────────────────────────────────────────────────────────
    cpath = pick_cluster(args.cluster)
    cache = load_json(cpath)
    clustered = (cache.get("output") or {}).get("clustered") or {}
    units, questions = clustered.get("units") or [], clustered.get("questions") or []
    if not units and not questions:
        die(f"{cpath.name} 의 output.clustered 가 비어 있습니다.")

    spath = Path(args.snapshot) if args.snapshot else latest_snapshot()
    snap = load_json(spath)

    print("입력")
    print(f"  cluster  : {cpath.name[:16]}…  ({(cache.get('created_at') or '')[:19]})")
    print(f"             units {len(units)} · questions {len(questions)} · "
          f"super_aspects {len(clustered.get('super_aspects') or [])} · "
          f"axis_source {clustered.get('axis_source','')}")
    print(f"  snapshot : {spath.name}  (saved_at {(snap.get('saved_at') or '')[:19]})")
    print(f"             product_profiles {len(snap.get('product_profiles') or [])} · "
          f"own_product {(snap.get('own_product') or {}).get('product_id','')}")

    # ── 2. LLM 차단 (선택) ──────────────────────────────────────────────────
    # 노드는 각 LLM 호출을 try/except 로 감싸므로, cli() 가 예외를 던지면
    # 해당 서술 필드만 비고 집계는 그대로 산출된다.
    if args.no_llm:
        import server.graph.reaction.llm as _llm

        def _blocked(*_a, **_k):
            def _call(*_a2, **_k2):
                raise RuntimeError("--no-llm 으로 LLM 호출이 차단되었습니다.")
            return _call
        _llm.cli = _blocked
        print("\n  ⚠ --no-llm : 캐시 미적중 시 서술 필드는 비어 나옵니다.")

    # ── 3. 노드 직접 호출 ───────────────────────────────────────────────────
    try:
        from server.graph.nodes.reaction_report_node import reaction_report_node
    except Exception as exc:
        die(f"reaction_report_node import 실패 — {type(exc).__name__}: {exc}\n"
            "  프로젝트 venv 에서 실행했는지 확인하세요 (repo 루트 기준).")

    state = {
        "reaction_clustered": clustered,
        "product_profiles":   snap.get("product_profiles") or [],
        "own_product":        snap.get("own_product") or {},
    }

    print("\n노드 실행 (reaction_report_node) …")
    # config 에 thread_id 를 주지 않아 set_progress·usage 집계 경로를 건너뛴다.
    out = reaction_report_node(state, config={})

    steps = out.get("agent_steps") or []
    status = steps[0].get("status") if steps else "?"
    if out.get("errors"):
        for e in out["errors"]:
            print(f"  ✖ {e.get('node')}: {e.get('error')}")
    env = (out.get("report_outputs") or {}).get("reaction_insight")
    if not env:
        die(f"envelope 이 생성되지 않았습니다 (status={status}). "
            "위 오류 메시지와 노드 로그를 확인하세요.")

    # ── 4. 검수 ─────────────────────────────────────────────────────────────
    content = env.get("content") or {}
    print(f"\n산출 (status={status})")
    print(f"  rubric_version   : {env.get('rubric_version')}")
    print(f"  source_references: {len(env.get('source_references') or [])}")
    print(f"  content 키       : {len(content)}")

    def size(v):
        return len(v) if isinstance(v, (list, dict, str)) else v

    key_rows = [
        ("aspect_matrix",        "항목×상품 집계"),
        ("aspect_labels",        "항목 라벨"),
        ("top_quotes",           "대표 인용"),
        ("selected_aspects",     "상위 항목"),
        ("perspective_by_super_candidate", "항목별 관점"),
        ("question_overview",    "질문 한눈 요약"),
        ("own_only_negative_aspects", "자사만 부정"),
        ("own_only_positive_aspects", "자사만 긍정"),
        ("aspect_analysis",      "항목별 LLM 분석"),
        ("aspect_descriptions",  "항목 설명"),
        ("question_analysis",    "질문 LLM 분석"),
        ("channel_meta",         "채널 표본"),
    ]
    print("\n  주요 필드")
    for k, label in key_rows:
        print(f"    {label:<16} {k:<32} {size(content.get(k)):>5}")

    filled = [k for k in NARRATIVE_KEYS if size(content.get(k))]
    print(f"\n  LLM 서술 필드 {len(filled)}/{len(NARRATIVE_KEYS)} 채워짐"
          f"{' — ' + ', '.join(filled) if filled else ''}")
    if len(filled) == len(NARRATIVE_KEYS):
        print("    → 캐시 전부 적중. 세션 토큰 소모 없이 완전한 리포트입니다.")
    elif filled:
        print("    → 일부만 적중. 빈 필드는 캐시 불일치이거나 LLM 호출이 실패한 것입니다.")
    else:
        print("    → 서술 없음(집계만). 캐시 불일치 또는 --no-llm 입니다.")

    if not content.get("aspect_matrix"):
        print("\n  ⚠ aspect_matrix 가 비었습니다. product_profiles 의 product_name 과 "
              "unit.target_candidate 라벨이 매칭되지 않았을 수 있습니다"
              " (reaction_report_node.build_label_to_cid 참조).")

    # ── 5. 저장 ─────────────────────────────────────────────────────────────
    fixture = {
        "meta": {
            "source":       "rebuild_reaction_insight.py",
            "cluster_hash": cpath.stem,
            "cluster_created_at": (cache.get("created_at") or "")[:19],
            "units":        len(units),
            "questions":    len(questions),
            "snapshot":     spath.name,
            "llm_narrative_filled": f"{len(filled)}/{len(NARRATIVE_KEYS)}",
        },
        "reaction_insight": env,
        "product_profiles": snap.get("product_profiles") or [],
        "own_product":      snap.get("own_product") or {},
    }
    outp = Path(args.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(fixture, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n✅ 저장: {outp}  ({outp.stat().st_size:,} bytes)")
    print("   이 경로를 알려주시면 목업에 이식합니다.")


if __name__ == "__main__":
    main()
