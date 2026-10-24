"""외부 조사(state.external_research) → 최종 리포트 렌더용 뷰 · 종합 패스 요약 · 미충족 블록 판정. LLM 미사용."""
from __future__ import annotations

def _named(cid: str, own_id: str, names: dict) -> str:
    return "자사" if cid == own_id else names.get(cid, cid)


def external_view(ext: dict | None, own_id: str, names: dict) -> tuple[dict | None, set[str], list[str]]:
    """반환 (view, 채워진 블록 key, 데이터 갭 문장). ext 가 없으면 (None, ∅, [])."""
    if not ext or not ext.get("tracks"):
        return None, set(), []
    T = ext["tracks"]
    hist = (T.get("history") or {}).get("block") or {}
    fin = (T.get("finance") or {}).get("block") or {}
    mkt = (T.get("market") or {}).get("block") or {}
    filled: set[str] = set()
    gaps: list[str] = []

    sources = []
    for t, r in T.items():
        for d in r.get("docs") or []:
            if d.get("used"):
                sources.append({"track": t, **{k: d.get(k) for k in ("doc_id", "url", "title", "publisher", "published_at",
                                                                     "source_check", "excerpts_found")}})
    cand_order = [c["cid"] for c in ext.get("candidates") or []]

    by_cid = hist.get("by_cid") or {}
    history = {
        "window": hist.get("window"),
        "own_timeline": (by_cid.get(own_id) or {}).get("timeline", []),
        "counts": [{"cid": c, "name": _named(c, own_id, names), **{k: v for k, v in (by_cid.get(c) or {}).items() if k != "timeline"}}
                   for c in cand_order if c in by_cid],
        "statements": [s | {"name": _named(s["cid"], own_id, names)} for s in hist.get("statements", [])],
        "contradictions": hist.get("contradictions", []),
        "competitor_actions": [a | {"name": names.get(a["cid"], a["cid"])} for a in hist.get("competitor_actions", [])],
        "structure_rows": hist.get("structure_rows", []),
        "upcoming": [u | {"name": _named(u["cid"], own_id, names)} for u in hist.get("upcoming", [])],
        "notes": hist.get("notes", []),
    }
    if history["own_timeline"]:
        filled.add("history")
    if history["competitor_actions"]:
        filled.add("competitor_actions")
    if any(not r["computed"] for r in history["structure_rows"]):
        filled.add("structure")

    series = fin.get("series", [])
    finance = {"own": [s for s in series if s["entity"] == own_id],
               "competitors": [s | {"name": names.get(s["entity"], s["entity"])} for s in series if s["entity"] != own_id],
               "conflicts": fin.get("conflicts", []), "unavailable": fin.get("unavailable", []), "notes": fin.get("notes", [])}
    if finance["own"]:
        filled.add("finance")
    for u in finance["unavailable"]:
        gaps.append(f"{_named(u['cid'], own_id, names) if u.get('cid') else '시장'} {u['metric']} — {u['note']}")

    market = {k: mkt.get(k, []) for k in ("tiles", "shares", "growth", "share_check", "conflicts", "axis_phases",
                                           "profitability", "expirations", "expiry_clusters", "notes")}
    if market["tiles"]:
        filled.add("market_tiles")
    if market["axis_phases"] or market["profitability"]:
        filled.add("market_axis")

    for t, r in T.items():
        if r.get("status") != "completed":
            gaps.append(f"외부 조사 {t} 트랙 {r.get('status')} — {'; '.join(r.get('warnings') or [])[:120]}")
    view = {"as_of": ext.get("as_of"), "window_start": ext.get("window_start"), "history": history, "finance": finance,
            "market": market, "sources": sources,
            "generation": {t: r.get("generation") for t, r in T.items()},
            "warnings": {t: r.get("warnings", []) for t, r in T.items() if r.get("warnings")}}
    return view, filled, gaps


def external_digest(view: dict | None) -> dict | None:
    """종합 패스 입력용 요약(원문·URL 제외, 근거 doc_id 유지)."""
    if not view:
        return None
    h, f, m = view["history"], view["finance"], view["market"]
    cname = {c["cid"]: c["name"] for c in h["counts"]}
    ev = lambda e: {k: e.get(k) for k in ("date", "feature", "change_type", "before", "after", "ends_at", "stated_reason", "doc_ids")}
    return {
        "as_of": view["as_of"], "history_window": h["window"],
        "history": {"own_timeline": [ev(e) for e in h["own_timeline"]],
                    "change_counts": [{"name": c["name"], "counts": c["counts"]} for c in h["counts"]],
                    "own_statements": [{"date": s["date"], "topic": s["topic"], "quote": s["quote"]} for s in h["statements"] if s["name"] == "자사"],
                    "competitor_actions": [{"name": a["name"], "headline": a["headline"], "detail": a["detail"]} for a in h["competitor_actions"]],
                    "structure": [{"field": r["field"], "cells": {cname.get(c, c): v["value"] for c, v in r["cells"].items()}} for r in h["structure_rows"]],
                    "upcoming": [{"name": u["name"], "item": u["item"], "ends_at": u["ends_at"], "days_left": u["days_left"]} for u in h["upcoming"]],
                    "notes": h["notes"]},
        "finance": {"own": [{"label": s["label"], "latest": s["latest"], "as_of": s["latest_as_of"],
                             "steps": [{"from": x["from"], "to": x["to"], "delta": x["delta_display"], "per_month": x["per_month_display"]} for x in s["steps"]]}
                            for s in f["own"]],
                    "competitors": [{"name": s["name"], "label": s["label"], "latest": s["latest"], "as_of": s["latest_as_of"]} for s in f["competitors"]],
                    "unavailable": f["unavailable"], "notes": f["notes"]},
        "market": {"tiles": m["tiles"], "shares": [{k: s[k] for k in ("period", "scope", "rows", "cr")} for s in m["shares"]],
                   "axis_phases": [{k: p[k] for k in ("period", "axis", "summary", "status")} for p in m["axis_phases"]],
                   "profitability": [p["statement"] for p in m["profitability"]],
                   "expirations": [{k: e[k] for k in ("entity", "item", "ends_at", "days_left")} for e in m["expirations"]],
                   "conflicts": [{k: c[k] for k in ("metric", "entity", "period", "values")} for c in m["conflicts"]],
                   "notes": m["notes"]},
    }
