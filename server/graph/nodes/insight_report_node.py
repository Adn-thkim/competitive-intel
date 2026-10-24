"""
server/graph/nodes/insight_report_node.py
-----------------------------------------
최종 인사이트 리포트(축약판) 노드 — ② 결정론 통계 + ⑤ 종합 agent(분류 패스 → 종합 패스).

위치 (graph 배선 — 아래 WIRING)
  comparison_matrix ─┐
                     ├─→ insight_report → END
  reaction_report  ──┘

입력 state
  reaction_clustered · product_profiles · own_product · domain_taxonomy
  report_outputs.comparison_matrix · report_outputs.reaction_insight
  external_research (선택 — external_research_node. 없으면 외부 블록은 pending)
출력
  report_outputs["insight_report"]  (merge reducer — 자기 키만 반환)
  agent_steps / errors

흐름
  1) build_insight_stats(state)                      — LLM 미사용(server/graph/insight/stats.py)
  2) 분류 패스  agents/insight_evidence              — 원문 → id 분류 (건수는 코드가 셈)
  3) 종합 패스  agents/insight_report                — 판정·기회·권고 서술
  4) lint_prose                                       — 작성 규칙 위반 검사(경고)
  5) 엔벨로프 조립 — 표는 통계에서 결정론 렌더, 서술만 LLM

목업 대비 범위: docs/mockups/domain_analysis_tab.html pane-final2 의 7장. 외부 데이터 블록(시장 흐름·재무·
변경 이력·경쟁사 액션·사업 구조)은 state.external_research 가 있으면 content.external 로 채우고, 없는 블록만
content.pending 으로 남긴다.

WIRING (server/graph/graph.py 에 적용됨)
  from server.graph.nodes.insight_report_node import insight_report_node
  builder.add_node("insight_report", insight_report_node)
  # 기존:  builder.add_edge("comparison_matrix", END) / builder.add_edge("reaction_report", END)
  builder.add_edge(["comparison_matrix", "reaction_report", "external_research_run"], "insight_report")
  # external_research 배선은 server/graph/nodes/external_research_node.py WIRING 참고
  builder.add_edge("insight_report", END)
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from server.config import AGENTS_DIR
from server.graph.agent_cache import load_agent_output, make_cache_context, store_agent_output
from server.graph.insight.external_view import external_digest, external_view
from server.graph.insight.stats import (
    PERSPECTIVE_KO, QUESTION_TYPE_KO, build_insight_stats,
)
from server.graph.nodes._report_node_common import build_report_envelope, make_error_result
from server.graph.state import DomainAnalysisState

logger = logging.getLogger(__name__)

REPORT_TYPE    = "insight_report"
RUBRIC_VERSION = "insight_report v0.1 (축약판 — 반응·매트릭스 기반)"
EVIDENCE_AGENT = "insight_evidence"
REPORT_AGENT   = "insight_report"
MODEL_ALIAS    = "sonnet"
TIMEOUT_S      = 900
MAX_RETRIES    = 2

# 외부 데이터가 필요해 축약판에서 제외되는 목업 블록 (2단계: 시장·이력 수집 + 도메인 분석 생성)
PENDING_BLOCKS = [   # key 가 external_view 의 filled 에 있으면 해당 블록은 외부 조사로 채워진다
    {"key": "market_tiles", "section": "2. 시장 흐름", "block": "시장 규모·집중도·점유율·성장률 타일", "needs": "시장 통계 수집"},
    {"key": "market_axis", "section": "2. 시장 흐름", "block": "경쟁축의 이동 타임라인 · 업계 수익성 압박", "needs": "정책·업계 이력 수집"},
    {"key": "finance", "section": "1·3. 요약·자사 위치", "block": "잔액 유지 지표(외화예수금 시계열) · 획득 지표(좌수)", "needs": "재무·공표 지표 수집"},
    {"key": "history", "section": "3. 자사 위치", "block": "36개월 변경 이력 · 공식 입장 비교", "needs": "보도자료·공지 이력 수집"},
    {"key": "competitor_actions", "section": "4. 경쟁사 비교", "block": "경쟁사 최근 액션", "needs": "경쟁사 뉴스 수집"},
    {"key": "structure", "section": "4. 경쟁사 비교", "block": "사업자 구조 행(국내 락인·예수금 조달·규제 특례·혜택 변동성)", "needs": "매트릭스 항목 확장"},
]

# 테스트·오프라인 실행용 LLM 대체 훅: fn(agent_id, system_prompt, schema, prompt) -> dict
LLM_OVERRIDE: Callable[[str, str, dict, str], dict] | None = None


# ─── agent 호출 (캐시 우선) ──────────────────────────────────────────────────
def _load_agent(agent_id: str) -> tuple[str, dict]:
    d = AGENTS_DIR / agent_id
    return ((d / "system_prompt_kr.md").read_text(encoding="utf-8"),
            json.loads((d / "output.schema.json").read_text(encoding="utf-8")))


def _model() -> str:
    from server.graph.reaction.llm import MODELS
    return MODELS.get(MODEL_ALIAS, MODEL_ALIAS)


def cache_handles(agent_id: str, payload: dict) -> tuple[dict, dict, str, dict]:
    """(cache_input, context, system_prompt, schema) — 오프라인 시드 스크립트와 공유."""
    system_prompt, schema = _load_agent(agent_id)
    ctx = make_cache_context(agent_id=agent_id, model=_model(), system_prompt=system_prompt,
                             output_schema=schema, prompt_version=f"{agent_id}:v0.1")
    return {"payload": payload}, ctx, system_prompt, schema


def _prompt(agent_id: str, payload: dict) -> str:
    head = {"insight_evidence": "아래 원문을 분류하라. id 로만 지정한다.",
            "insight_report":   "아래 통계·분류 결과로 최종 인사이트 리포트(축약판)의 서술을 작성하라."}[agent_id]
    return head + "\n```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"


def run_agent(agent_id: str, payload: dict, refresh: bool = False) -> tuple[dict, str]:
    """캐시 조회 → 미스 시 CLI(또는 LLM_OVERRIDE) 호출 → 저장. 반환 (output, 'cache'|'llm')."""
    cin, ctx, system_prompt, schema = cache_handles(agent_id, payload)
    if not refresh:
        hit = load_agent_output(agent_id=agent_id, cache_input=cin, context=ctx,
                                output_schema=schema, logger=logger)
        if hit is not None:
            return hit, "cache"
    if LLM_OVERRIDE is not None:
        out = LLM_OVERRIDE(agent_id, system_prompt, schema, _prompt(agent_id, payload))
    else:
        from server.graph.reaction.llm import cli
        out = cli(_model(), system_prompt, TIMEOUT_S, MAX_RETRIES)(_prompt(agent_id, payload), schema)
    import jsonschema
    jsonschema.validate(out, schema)
    store_agent_output(agent_id=agent_id, cache_input=cin, context=ctx, output=out, logger=logger)
    return out, "llm"


# ─── 분류 패스 입력·후처리 ──────────────────────────────────────────────────
def evidence_payload(stats: dict) -> dict:
    ev = stats["evidence"]
    return {"own_product": next(c["name"] for c in stats["meta"]["candidates"] if c["is_own"]),
            "focus_aspect": ev["focus_aspect"],
            "own_negatives": ev["own_negatives"], "own_positives": ev["own_positives"],
            "own_reliability": ev["own_reliability"],
            "focus_units": ev["focus_units"], "focus_questions": ev["focus_questions"],
            "questions": ev["questions"]}


def resolve_evidence(raw: dict, stats: dict) -> tuple[dict, list[str]]:
    """LLM 분류(id) → 건수·원문을 코드로 확정. 존재하지 않는 id·중복 배정은 버리고 경고."""
    ev = stats["evidence"]
    umap = {u["id"]: u for k in ("own_negatives", "own_positives", "own_reliability", "focus_units")
            for u in ev[k]}
    qmap = {q["id"]: q for q in ev["questions"]}
    warn: list[str] = []

    def take(ids, pool, used=None):
        out = []
        for i in ids or []:
            if i not in pool:
                warn.append(f"분류 패스: 존재하지 않는 id 무시 ({i})")
                continue
            if used is not None:
                if i in used:
                    warn.append(f"분류 패스: 중복 배정 id 무시 ({i})")
                    continue
                used.add(i)
            out.append(i)
        return out

    neg_ids = {u["id"] for u in ev["own_negatives"]}
    used: set = set()
    causes = []
    for c in raw.get("negative_causes") or []:
        ids = take([i for i in c.get("unit_ids") or [] if i in neg_ids or i not in umap], umap, used)
        if ids:
            causes.append({**c, "unit_ids": ids, "n": len(ids),
                           "quotes": (c.get("quotes") or [umap[i]["quote"] for i in ids])[:3]})
    unassigned = sorted(neg_ids - used)
    if unassigned:
        causes.append({"label": "미분류", "resolution": "대응 불가", "solved_by_benefit": False,
                       "unit_ids": unassigned, "n": len(unassigned),
                       "quotes": [umap[i]["quote"] for i in unassigned][:3]})
    total_neg = len(neg_ids) or 1
    for c in causes:
        c["share"] = round(c["n"] / total_neg * 100)
    causes.sort(key=lambda c: -c["n"])

    def group(items, key, pool, extra=()):
        out = []
        for it in items or []:
            ids = take(it.get(key), pool, set())
            if ids:
                out.append({**{k: it.get(k) for k in it if k != key}, key: ids, "n": len(ids),
                            "quotes": [pool[i]["quote"] for i in ids][:4]})
        return sorted(out, key=lambda x: -x["n"])

    resolved = {
        "negative_causes": causes,
        "focus_aspect": ev["focus_aspect"],
        "focus_negative_causes": group(raw.get("focus_negative_causes"), "unit_ids", umap),
        "focus_positive_contexts": group(raw.get("focus_positive_contexts"), "unit_ids", umap),
        "focus_question_breakdown": group(raw.get("focus_question_breakdown"), "question_ids", qmap),
        "question_shapes": group(raw.get("question_shapes"), "question_ids", qmap),
        "value_language": [v for v in raw.get("value_language") or [] if v.get("unit_id") in umap],
        "combination_use": [c for c in raw.get("combination_use") or []
                            if c.get("id") in umap or c.get("id") in qmap],
        "domain_boundary_flags": [d for d in raw.get("domain_boundary_flags") or []
                                  if d.get("id") in umap or d.get("id") in qmap],
    }
    return resolved, warn


# ─── 종합 패스 입력 (통계 요약 — 원문 묶음 제외) ─────────────────────────────
def prepare_external(state: dict, stats: dict) -> tuple[dict | None, set, list]:
    """state.external_research → (view, filled 블록 key, 데이터 갭). 없으면 (None, ∅, [])."""
    meta = stats["meta"]
    return external_view(state.get("external_research"), meta["own_id"],
                         {c["candidate_id"]: c["name"] for c in meta["candidates"]})


def pending_blocks(filled: set) -> list[dict]:
    return [b for b in PENDING_BLOCKS if b["key"] not in filled]


def report_payload(stats: dict, evidence: dict, ext: tuple | None = None) -> dict:
    view, filled, gaps = ext or (None, set(), [])
    meta = stats["meta"]
    name = {c["candidate_id"]: c["name"] for c in meta["candidates"]}
    own = meta["own_id"]

    def nm(cid):
        return "자사" if cid == own else name.get(cid, cid)

    def cell(s):
        return {"n": s["n"], "pos": s["pos"], "neg": s["neg"], "neu": s["neu"], "sample_level": s["sample_level"]}

    return {
        "meta": {k: meta[k] for k in ("units", "candidate_units", "questions", "own_units", "own_questions",
                                      "excluded", "channels")} | {"own_product": name.get(own)},
        "stats": {
            "aspect_table": [{"aspect": r["aspect"], "own": cell(r["own"]),
                              "best_competitor": nm(r["best_competitor"]) if r["best_competitor"] else None,
                              "best": cell(r["best"]) if r["best"] else None, "verdict": r["verdict"]}
                             for r in stats["aspect_table"]],
            "channel_split": {nm(c): {ch: cell(v) for ch, v in d.items()} for c, d in stats["channel_split"].items()},
            "perspective_table": [{"perspective": p["label"], "own_position": p["own_position"],
                                   "by_candidate": {nm(c): cell(v) for c, v in p["by_candidate"].items()}}
                                  for p in stats["perspective_table"]],
            "questions": {"total": stats["questions"]["total"],
                          "groups": {v["label"]: v["n"] for v in stats["questions"]["groups"].values()},
                          "by_type": {QUESTION_TYPE_KO.get(k, k): v for k, v in stats["questions"]["by_type"].items()},
                          "by_channel": stats["questions"]["by_channel"],
                          "by_candidate": {nm(c): {"n": v["n"], "info": v["info"],
                                                   "types": {QUESTION_TYPE_KO.get(k, k): n for k, n in v["types"].items()},
                                                   "aspects": v["aspects"]}
                                           for c, v in stats["questions"]["by_candidate"].items()},
                          "own_adoption_review": stats["questions"]["own_adoption_review"]},
            "own_negative_points": stats["own_negative_points"],
            "strong_opinions": {nm(c): cell(v) for c, v in stats["strong_opinions"].items()},
            "question_patterns": {k: {kk: vv for kk, vv in v.items() if kk != "examples"}
                                  for k, v in stats["question_patterns"].items()},
        },
        "matrix": {"features": [{"label": f["label"], "identical": f["identical"],
                                 "headline_only_identical": f["headline_only_identical"],
                                 "cells": {nm(c): {"headline": x["headline"], "condition": x["condition"],
                                                   "items": x["items"]} for c, x in f["cells"].items()}}
                                for f in stats["matrix"]["features"]],
                   "product_summary": stats["matrix"]["product_summary"],
                   "promotions": stats["matrix"]["promotions"]},
        "evidence": {
            "negative_causes": [{k: c[k] for k in ("label", "resolution", "solved_by_benefit", "n", "share", "quotes")}
                                for c in evidence["negative_causes"]],
            "focus_aspect": evidence["focus_aspect"],
            "focus_negative_causes": [{"label": c["label"], "solved_by_benefit": c.get("solved_by_benefit"),
                                       "n": c["n"], "quotes": c["quotes"]} for c in evidence["focus_negative_causes"]],
            "focus_positive_contexts": [{"label": c["label"], "n": c["n"], "quotes": c["quotes"]}
                                        for c in evidence["focus_positive_contexts"]],
            "focus_question_breakdown": [{"label": c["label"], "complains_about_level": c.get("complains_about_level"),
                                          "n": c["n"], "quotes": c["quotes"]} for c in evidence["focus_question_breakdown"]],
            "question_shapes": [{"shape": c["shape"], "n": c["n"], "meaning": c.get("meaning"), "quotes": c["quotes"]}
                                for c in evidence["question_shapes"]],
            "value_language": [v["phrase"] for v in evidence["value_language"]],
            "combination_use": evidence["combination_use"],
            "domain_boundary_flags": evidence["domain_boundary_flags"],
            "own_reliability_quotes": [u["quote"] for u in stats["evidence"]["own_reliability"]],
        },
        "cited": stats["cited"],
        **({"external": external_digest(view)} if view else {}),
        "data_gaps": [f"{b['block']} — {b['needs']}" for b in pending_blocks(filled)] + gaps,
    }


# ─── 작성 규칙 검사 (결정론) ────────────────────────────────────────────────
_INDEX_RE = re.compile(r"(?<![\d.])[+−\-±]?\s?0\.\d{2}(?!\d)|[+−\-]\s?1\.00")


def _walk_strings(obj, path=""):
    if isinstance(obj, str):
        yield path, obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk_strings(v, f"{path}.{k}" if path else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk_strings(v, f"{path}[{i}]")


def lint_prose(report: dict) -> list[str]:
    """본문 지수 노출·기회 교집합 누락·액션 비용 정렬 위반을 경고로 반환."""
    warn = []
    for path, s in _walk_strings(report):
        if _INDEX_RE.search(s):
            warn.append(f"규칙 1-1: 본문에 지수 값 노출 — {path}: “{_INDEX_RE.search(s).group(0).strip()}”")
    for o in (report.get("opportunity") or {}).get("items") or []:
        if o.get("kind") == "기회":
            x = o.get("intersection") or {}
            missing = [k for k in ("competitor_gap", "customer_signal", "own_capability") if not (x.get(k) or "").strip()]
            if missing:
                warn.append(f"규칙 4-3: {o.get('id')} '기회'인데 교집합 누락 {missing}")
    order = {"없음": 0, "낮음": 1, "중간": 2, "높음": 3}
    acts = (report.get("recommendation") or {}).get("actions") or []
    hi_first = [a["id"] for i, a in enumerate(acts) if order.get(a.get("cost"), 0) == 3
                and any(order.get(b.get("cost"), 0) <= 1 for b in acts[i + 1:])]
    if hi_first:
        warn.append(f"규칙 4-5: 고비용 액션이 저비용 액션보다 앞에 있음 {hi_first}")
    return warn


def _score(stats: dict, report: dict, lint: list[str], pending: list | None = None) -> int:
    """루브릭: 3=표·판정 / 4=+교집합 기회·비용 정렬 액션·한계 / 5=+외부 데이터 블록 전부(pending 0)."""
    s = 3
    opp_ok = any(o.get("kind") == "기회" for o in report["opportunity"]["items"])
    if opp_ok and report["recommendation"]["actions"] and report["recommendation"]["limitations"] and not lint:
        s = 4
        if pending is not None and not pending:
            s = 5
    return s


# ─── 엔벨로프 조립 ──────────────────────────────────────────────────────────
def assemble(stats: dict, evidence: dict, report: dict, warnings: list[str], sources: dict,
             ext: tuple | None = None) -> dict:
    meta = stats["meta"]
    q = stats["questions"]
    view, filled, _ = ext or (None, set(), [])
    pend = pending_blocks(filled)
    content = {
        "title": "최종 인사이트 리포트" + ("" if not pend else " — 축약판"),
        "scope": ("고객 반응 + 비교 매트릭스" + (" + 외부 조사(이력·재무·시장)" if view else "")
                  + (" 기반. 외부 데이터가 없는 블록은 pending 으로 표시" if pend else " 기반")),
        "meta": meta,
        "sample_policy": stats["sample_policy"],
        "narrative": report,                       # LLM 서술 (요약·위치·기회·권고)
        "tables": {                                # 통계에서 결정론 렌더
            "aspect_table": stats["aspect_table"],
            "channel_split": stats["channel_split"],
            "perspective_table": stats["perspective_table"],
            "questions": {"total": q["total"], "groups": q["groups"], "by_channel": q["by_channel"],
                          "by_candidate": q["by_candidate"], "own_adoption_review": q["own_adoption_review"]},
            "own_negative_points": stats["own_negative_points"],
            "strong_opinions": stats["strong_opinions"],
            "question_patterns": {k: v for k, v in stats["question_patterns"].items()},
            "matrix_features": [f for f in stats["matrix"]["features"]],
            "promotions": stats["matrix"]["promotions"],
            "cited_aspect_analysis": stats["cited"]["aspect_analysis"],
        },
        "evidence": evidence,
        "external": view,                          # 외부 조사 뷰 (server/graph/insight/external_view.py)
        "pending": pend,
        "generation": sources,
        "labels": {"perspective": PERSPECTIVE_KO, "question_type": QUESTION_TYPE_KO},
    }
    return build_report_envelope(
        report_type=REPORT_TYPE, rubric_version=RUBRIC_VERSION,
        categories=["1. 요약"] + (["2. 시장 흐름"] if {"market_tiles", "market_axis"} & filled else [])
                   + ["3. 자사 위치", "4. 경쟁사 비교", "5. 고객 반응", "6. 기회", "7. 권고 및 결정 요청", "부록"],
        content=content, evaluation_score=_score(stats, report, [w for w in warnings if w.startswith("규칙")], pend),
        warnings=warnings,
    )


# ─── 노드 ───────────────────────────────────────────────────────────────────
def insight_report_node(state: DomainAnalysisState, config: dict | None = None) -> dict:
    started_at = datetime.now(timezone.utc).isoformat()
    refresh = bool(((config or {}).get("configurable") or {}).get("insight_refresh"))
    try:
        ro = state.get("report_outputs") or {}
        if not (state.get("reaction_clustered") or {}).get("units"):
            return make_error_result(REPORT_TYPE, started_at, "reaction_clustered 없음 — 반응 파이프라인 선행 필요")
        if not ro.get("comparison_matrix"):
            return make_error_result(REPORT_TYPE, started_at, "report_outputs.comparison_matrix 없음")

        stats = build_insight_stats(dict(state))
        raw_ev, src_ev = run_agent(EVIDENCE_AGENT, evidence_payload(stats), refresh)
        evidence, warn_ev = resolve_evidence(raw_ev, stats)
        ext = prepare_external(dict(state), stats)          # 외부 조사 없으면 축약판(해당 블록 pending)
        report, src_rp = run_agent(REPORT_AGENT, report_payload(stats, evidence, ext), refresh)
        lint = lint_prose(report)
        envelope = assemble(stats, evidence, report, lint + warn_ev,
                            {EVIDENCE_AGENT: src_ev, REPORT_AGENT: src_rp}, ext)
        logger.info("insight_report: 완료 (분류=%s · 종합=%s · 경고 %d)", src_ev, src_rp, len(lint + warn_ev))
        return {
            "report_outputs": {REPORT_TYPE: envelope},
            "agent_steps": [{"step_name": "InsightReport", "status": "completed",
                             "started_at": started_at,
                             "finished_at": datetime.now(timezone.utc).isoformat()}],
        }
    except Exception as exc:  # noqa: BLE001 — 개별 실패가 파이프라인을 중단시키지 않도록
        logger.exception("insight_report 실패")
        return make_error_result(REPORT_TYPE, started_at, f"{type(exc).__name__}: {exc}"[:300])
