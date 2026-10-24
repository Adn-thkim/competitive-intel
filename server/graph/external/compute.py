"""외부 조사 결정론 처리 — 출처 검증, 단위 정규화, 이력·지표·시장 집계. LLM 미사용.

agent 는 '문서에 적힌 값'만 추출하고, 아래가 맡는다.
  verify        doc_id 실재 확인 · quote 가 문서 본문에 실제로 있는지(verified 플래그)
  to_base       단위 정규화(금액→억원, 건수→개수, 비율→%)
  history_block 36개월 창 필터 · 중복 병합 · 유형별 횟수 · 만료 예정 · 사업 구조 행(변동성 행은 계산)
  finance_block 지표별 시계열 · 구간 증감과 월평균 속도 · 같은 기준일 값 충돌
  market_block  최신 규모 타일 · 점유율 그룹과 상위 n사 합(CR) · 성장률 재계산 대조 · 집계 범위 충돌 · 만료 군집
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import date

CHANGE_ORDER = ["신설", "확대", "축소", "원복", "연장", "종료", "제휴", "기타"]
UPCOMING_DAYS = 120          # 기준일 이후 이 기간 안의 만료 = 임박
ACTION_WINDOW_DAYS = 365     # 경쟁사 최근 액션 창

_NORM_RE = re.compile(r"[\s\"'“”‘’·.,!?()\[\]~\-–—…:;/]")


def _norm(s: str) -> str:
    return _NORM_RE.sub("", (s or "")).lower()


def parse_date(s: str | None) -> tuple[date | None, str]:
    """'2026-04-01'|'2026-04'|'2026' → (date, 'day'|'month'|'year'). 월·연 정밀도는 해당 기간의 첫날."""
    if not s:
        return None, ""
    m = re.match(r"^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?", s)
    if not m:
        return None, ""
    y, mo, d = int(m[1]), int(m[2] or 1), int(m[3] or 1)
    try:
        return date(y, mo, d), ("day" if m[3] else "month" if m[2] else "year")
    except ValueError:
        return None, ""


def _period_end(s: str | None) -> date | None:
    """만료일 비교용 — 월·연 정밀도는 기간의 마지막 날로 본다."""
    d, p = parse_date(s)
    if not d:
        return None
    if p == "year":
        return date(d.year, 12, 31)
    if p == "month":
        nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
        return date.fromordinal(nxt.toordinal() - 1)
    return d


# ─── 출처 검증 ───────────────────────────────────────────────────────────────
def verify(items: list[dict], docs: dict[str, dict], label: str, *, ids_key: str = "doc_ids",
           quote_key: str | None = "quote") -> tuple[list[dict], list[str]]:
    """존재하지 않는 doc_id 는 제거, 근거가 하나도 없으면 항목 제외. quote 가 본문에 없으면 verified=False."""
    kept, warn = [], []
    for i, it in enumerate(items):
        it = dict(it)
        ids = it.get(ids_key)
        single = isinstance(ids, str) or ids is None
        ids = [ids] if isinstance(ids, str) else list(ids or [])
        valid = [x for x in ids if x in docs]
        if ids and not valid:
            warn.append(f"{label}[{i}] 근거 문서 없음({','.join(ids)}) — 제외")
            continue
        if not single:
            it[ids_key] = valid
        elif valid:
            it[ids_key] = valid[0]
        q = it.get(quote_key) if quote_key else None
        if valid and all(docs[x].get("source_check") == "불일치" for x in valid):
            it["verified"] = False                    # 원문 대조 실패 문서만 근거 → 미검증
            warn.append(f"{label}[{i}] 근거 문서가 원문 대조에서 불일치")
        elif q:
            nq = _norm(q)
            it["verified"] = any(nq and nq in _norm(" ".join([docs[x].get("title", ""), docs[x].get("snippet", ""),
                                                             docs[x].get("text", "")])) for x in valid)
            if not it["verified"]:
                warn.append(f"{label}[{i}] 인용문이 근거 문서 본문에서 확인되지 않음")
        else:
            it["verified"] = bool(valid) if quote_key else True
        it["_i"] = i
        kept.append(it)
    return kept, warn


# ─── 단위 정규화 ────────────────────────────────────────────────────────────
_MONEY = {"조원": 1e4, "조 원": 1e4, "조": 1e4, "억원": 1, "억 원": 1, "억": 1, "만원": 1e-4, "만 원": 1e-4, "원": 1e-8}
_COUNT_MULT = {"백만": 1e6, "천만": 1e7, "만": 1e4, "천": 1e3, "": 1}
_COUNT_BASE = ("명", "좌", "장", "건", "개", "곳", "개국")


def to_base(value: float, unit: str) -> tuple[float | None, str, str]:
    """(값, 기준단위, 종류). 종류: money(억원) / count(명·좌·장…) / pct / usd / unknown."""
    u = (unit or "").replace(" ", "")
    if u in ("%", "퍼센트"):
        return value, "%", "pct"
    if u in ("%p", "%포인트", "퍼센트포인트"):
        return value, "%p", "pct"
    if u.endswith("달러") or u.lower().endswith("usd") or u.startswith("$"):
        mult = 1e8 if u.startswith("억") else 1e4 if u.startswith("만") else 1
        return value * mult, "달러", "usd"
    for k, m in _MONEY.items():
        if u == k.replace(" ", ""):
            return round(value * m, 4), "억원", "money"
    for base in _COUNT_BASE:
        if u.endswith(base):
            pre = u[: -len(base)]
            if pre in _COUNT_MULT:
                return value * _COUNT_MULT[pre], base, "count"
    return None, unit, "unknown"


def fmt_base(v: float | None, unit: str) -> str:
    if v is None:
        return "—"
    if unit == "억원":
        if v >= 1e4:
            jo, eok = divmod(round(v), 10000)
            return f"{jo}조 {eok:,}억원" if eok else f"{jo}조원"
        return f"{v:,.0f}억원"
    if unit in ("%", "%p"):
        return f"{v:.2f}{unit}"
    if unit in _COUNT_BASE:
        return f"{v / 1e4:,.0f}만{unit}" if v >= 1e4 else f"{v:,.0f}{unit}"
    return f"{v:,.0f}{unit}"


# ─── 이력 ───────────────────────────────────────────────────────────────────
def history_block(out: dict, docs: dict, cands: list[dict], as_of: date, window_start: date) -> tuple[dict, list[str]]:
    warn: list[str] = []
    cids = {c["cid"] for c in cands}
    ev, w = verify(out.get("events", []), docs, "events"); warn += w
    st, w = verify(out.get("statements", []), docs, "statements", ids_key="doc_id"); warn += w
    sr, w = verify(out.get("structure", []), docs, "structure"); warn += w

    events, seen = [], {}
    for e in ev:
        if e["cid"] not in cids:
            warn.append(f"events[{e['_i']}] 후보 외 cid {e['cid']} — 제외"); continue
        d, prec = parse_date(e["date"])
        if not d:
            warn.append(f"events[{e['_i']}] 날짜 해석 불가 — 제외"); continue
        if d > as_of:
            e["scheduled"] = True                    # 예고된 변경
        if d < window_start and e["change_type"] != "신설":
            continue
        key = (e["cid"], _norm(e["feature"]), e["change_type"], e["date"][:7])
        if key in seen:                              # 같은 변경의 다른 보도 — 근거만 합친다
            tgt = seen[key]; tgt["doc_ids"] = sorted(set(tgt["doc_ids"]) | set(e["doc_ids"]))
            tgt["verified"] = tgt["verified"] or e["verified"]; continue
        e["_d"], e["precision"] = d, prec
        seen[key] = e; events.append(e)
    events.sort(key=lambda e: (e["_d"], e["cid"]))
    idx_map = {e["_i"]: n for n, e in enumerate(events)}

    by_cid: dict[str, dict] = {}
    for c in cands:
        mine = [e for e in events if e["cid"] == c["cid"]]
        cnt = Counter(e["change_type"] for e in mine if e["_d"] >= window_start)
        by_cid[c["cid"]] = {"timeline": [_ev_out(e) for e in mine],
                            "counts": {k: cnt.get(k, 0) for k in CHANGE_ORDER if cnt.get(k)},
                            "reductions": cnt.get("축소", 0), "restorations": cnt.get("원복", 0),
                            "extensions": cnt.get("연장", 0), "n_events": len(mine)}

    upcoming = []
    for e in events:
        end = _period_end(e.get("ends_at"))
        if end and end >= as_of:
            upcoming.append({"cid": e["cid"], "item": e["feature"], "ends_at": e["ends_at"],
                             "days_left": (end - as_of).days, "doc_ids": e["doc_ids"]})
    upcoming.sort(key=lambda x: x["days_left"])

    actions = []
    for a in out.get("action_summaries", []):
        if a["cid"] not in cids or next(c for c in cands if c["cid"] == a["cid"])["is_own"]:
            continue
        refs = [idx_map[r] for r in a.get("event_refs", []) if r in idx_map]
        recent = [r for r in refs if (as_of - events[r]["_d"]).days <= ACTION_WINDOW_DAYS]
        actions.append({**{k: a[k] for k in ("cid", "headline", "detail", "is_inference")},
                        "events": [_ev_out(events[r]) for r in refs], "recent_n": len(recent)})
        if refs and not recent:
            warn.append(f"action_summaries {a['cid']}: 근거 사건이 모두 최근 12개월 밖")

    st_map = {s["_i"]: n for n, s in enumerate(st)}
    contra = [{"cid": c["cid"], "note": c["note"], "is_inference": True,
               "statements": [_strip(st[st_map[r]]) for r in c["statement_refs"] if r in st_map]}
              for c in out.get("contradictions", [])]
    contra = [c for c in contra if len(c["statements"]) >= 2]

    fields = ["국내 락인", "예수금 조달", "규제 특례", "발급 구조"]
    rows = []
    for f in fields:
        cells = {}
        for c in cands:
            hit = [s for s in sr if s["cid"] == c["cid"] and s["field"] == f]
            cells[c["cid"]] = ({"value": hit[0]["value"], "status": hit[0]["status"], "doc_ids": hit[0]["doc_ids"],
                                "verified": hit[0]["verified"]} if hit else {"value": "미확인", "status": "미확인", "doc_ids": []})
        if any(v["status"] == "확인" for v in cells.values()):
            rows.append({"field": f, "cells": cells, "computed": False})
    rows.append({"field": "혜택 변동성", "computed": True, "cells": {
        c["cid"]: {"value": _volatility(by_cid[c["cid"]]), "status": "확인" if by_cid[c["cid"]]["n_events"] else "미확인",
                   "doc_ids": sorted({d for e in by_cid[c["cid"]]["timeline"] for d in e["doc_ids"]})} for c in cands}})

    return {"window": [window_start.isoformat(), as_of.isoformat()], "by_cid": by_cid, "upcoming": upcoming,
            "competitor_actions": actions, "statements": [_strip(s) for s in st], "contradictions": contra,
            "structure_rows": rows, "notes": out.get("notes", [])}, warn


def _volatility(b: dict) -> str:
    if not b["n_events"]:
        return "수집 문서에 변경 이력 없음"
    parts = [f"{k} {b['counts'][k]}" for k in ("축소", "원복", "연장", "확대") if b["counts"].get(k)]
    return " · ".join(parts) or "축소·원복·연장 없음"


def _ev_out(e: dict) -> dict:
    return {k: e.get(k) for k in ("cid", "date", "precision", "feature", "change_type", "before", "after", "ends_at",
                                  "stated_reason", "doc_ids", "quote", "verified", "scheduled")}


def _strip(d: dict) -> dict:
    return {k: v for k, v in d.items() if not k.startswith("_")}


# ─── 재무 지표 ──────────────────────────────────────────────────────────────
def finance_block(out: dict, docs: dict, cands: list[dict]) -> tuple[dict, list[str]]:
    ms, warn = verify(out.get("metrics", []), docs, "metrics", ids_key="doc_id")
    pts = []
    for m in ms:
        v, bu, kind = to_base(m["value"], m["unit"])
        d, _ = parse_date(m["as_of"])
        if v is None or not d:
            warn.append(f"metrics[{m['_i']}] 단위/기준일 해석 불가({m['unit']}, {m['as_of']}) — 표에서 제외"); continue
        pts.append({**_strip(m), "base_value": v, "base_unit": bu, "display": fmt_base(v, bu), "_d": d})
    groups = defaultdict(list)
    for p in pts:
        groups[(p.get("cid") or p["entity"], p["metric"], p["base_unit"], p["basis"], _norm(p["scope"]))].append(p)
    series, conflicts = [], []
    for (ent, metric, bu, basis, _), g in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        g.sort(key=lambda p: p["_d"])
        by_day = defaultdict(list)
        for p in g:
            by_day[p["as_of"]].append(p)
        for day, same in by_day.items():
            vals = {round(p["base_value"], 2) for p in same}
            if len(vals) > 1 and max(vals) > min(vals) * 1.01:
                conflicts.append({"entity": ent, "metric": metric, "as_of": day,
                                  "values": [{"display": p["display"], "doc_id": p["doc_id"]} for p in same]})
        uniq = [same[0] for _, same in sorted(by_day.items(), key=lambda kv: kv[1][0]["_d"])]
        steps = []
        for a, b in zip(uniq, uniq[1:]):
            days = (b["_d"] - a["_d"]).days
            delta = b["base_value"] - a["base_value"]
            steps.append({"from": a["as_of"], "to": b["as_of"], "days": days, "delta": delta,
                          "delta_display": ("+" if delta >= 0 else "−") + fmt_base(abs(delta), bu),
                          "per_month_display": (("+" if delta >= 0 else "−") + fmt_base(abs(delta) / days * 30.4, bu))
                          if days >= 28 and basis == "누적" else None})
        series.append({"entity": ent, "metric": metric, "label": g[-1]["label"], "basis": basis, "scope": g[-1]["scope"],
                       "unit": bu, "points": [{k: p[k] for k in ("as_of", "as_of_kind", "display", "base_value", "qualifier",
                                                                 "doc_id", "quote", "verified")} for p in uniq],
                       "steps": steps, "latest": uniq[-1]["display"], "latest_as_of": uniq[-1]["as_of"]})
    return {"series": series, "conflicts": conflicts, "unavailable": out.get("unavailable", []),
            "notes": out.get("notes", [])}, warn


# ─── 시장 ───────────────────────────────────────────────────────────────────
def market_block(out: dict, docs: dict, as_of: date) -> tuple[dict, list[str]]:
    ms, warn = verify(out.get("metrics", []), docs, "metrics", ids_key="doc_id")
    ph, w = verify(out.get("axis_phases", []), docs, "axis_phases", quote_key=None); warn += w
    pr, w = verify(out.get("profitability", []), docs, "profitability", ids_key="doc_id"); warn += w
    ex, w = verify(out.get("expirations", []), docs, "expirations", ids_key="doc_id"); warn += w

    rows = []
    for m in ms:
        v, bu, kind = to_base(m["value"], m["unit"])
        if v is None:
            warn.append(f"metrics[{m['_i']}] 단위 해석 불가({m['unit']})"); continue
        pv = to_base(m["prior_value"], m["unit"])[0] if m.get("prior_value") is not None else None
        rows.append({**_strip(m), "base_value": v, "base_unit": bu, "display": fmt_base(v, bu), "prior_base": pv})

    # 성장률 재계산 — 이용액 항목에 prior 값이 있으면 코드가 계산하고, 문서 기재 성장률과 대조
    growth = []
    for r in rows:
        if r["metric"] in ("시장 이용액", "사업자 이용액") and r["prior_base"]:
            calc = (r["base_value"] - r["prior_base"]) / r["prior_base"] * 100
            stated = next((s for s in rows if s["metric"] == "성장률" and s["entity"] == r["entity"]
                           and s["period"] == r["period"] and _norm(s["scope"]) == _norm(r["scope"])), None)
            growth.append({"entity": r["entity"], "period": r["period"], "prior_period": r["prior_period"], "scope": r["scope"],
                           "computed_pct": round(calc, 2), "stated_pct": stated["base_value"] if stated else None,
                           "mismatch": bool(stated and abs(stated["base_value"] - calc) > 0.5), "doc_id": r["doc_id"]})

    # 점유율 그룹(같은 기간·같은 집계 범위) → 상위 n사 합
    share_groups = defaultdict(list)
    for r in rows:
        if r["metric"] == "점유율" and r["entity"] != "업계":
            share_groups[(r["period"], _norm(r["scope"]))].append(r)
    shares = []
    for (period, _), g in share_groups.items():
        g = sorted({x["entity"]: x for x in g}.values(), key=lambda x: -x["base_value"])
        total = sum(x["base_value"] for x in g)
        shares.append({"period": period, "scope": g[0]["scope"],
                       "rows": [{"entity": x["entity"], "share": x["base_value"], "doc_id": x["doc_id"], "verified": x["verified"]} for x in g],
                       "cr": {f"CR{n}": round(sum(x["base_value"] for x in g[:n]), 2) for n in (2, 3) if len(g) >= n},
                       "coverage_pct": round(total, 2)})
    shares.sort(key=lambda s: s["period"], reverse=True)

    # 사업자 이용액 ÷ 시장 이용액 (같은 기간·범위) → 점유율 교차 확인
    share_check = []
    for r in rows:
        if r["metric"] != "사업자 이용액":
            continue
        mk = next((m for m in rows if m["metric"] == "시장 이용액" and m["period"] == r["period"]
                   and _norm(m["scope"]) == _norm(r["scope"])), None)
        st = next((s for s in rows if s["metric"] == "점유율" and s["entity"] == r["entity"] and s["period"] == r["period"]
                   and _norm(s["scope"]) == _norm(r["scope"])), None)
        if mk:
            calc = r["base_value"] / mk["base_value"] * 100
            share_check.append({"entity": r["entity"], "period": r["period"], "computed_pct": round(calc, 2),
                                "stated_pct": st["base_value"] if st else None,
                                "mismatch": bool(st and abs(st["base_value"] - calc) > 1.0)})

    # 같은 지표·주체·기간인데 값이 다른 경우 — 대개 집계 범위 차이
    conflicts = []
    bykey = defaultdict(list)
    for r in rows:
        if r["metric"] in ("시장 이용액", "사업자 이용액", "점유율"):
            bykey[(r["metric"], r["entity"], r["period"])].append(r)
    for (metric, ent, period), g in bykey.items():
        vals = {round(x["base_value"], 2) for x in g}
        if len(vals) > 1:
            conflicts.append({"metric": metric, "entity": ent, "period": period,
                              "values": [{"display": x["display"], "scope": x["scope"], "doc_id": x["doc_id"]} for x in g],
                              "same_scope": len({_norm(x["scope"]) for x in g}) < len(g)})

    # 타일: 업계 시장 이용액 최신(기간 문자열 내림차순) + 1위 점유율
    market_rows = sorted([r for r in rows if r["metric"] == "시장 이용액"], key=lambda r: r["period"], reverse=True)
    tiles = []
    if market_rows:
        r = market_rows[0]
        tiles.append({"label": "시장 이용액", "value": r["display"], "note": f"{r['period']} · {r['scope']}", "doc_id": r["doc_id"]})
    g = next((g for g in growth if g["entity"] == "업계" and market_rows and g["period"] == market_rows[0]["period"]
              and _norm(g["scope"]) == _norm(market_rows[0]["scope"])), None) or next((g for g in growth if g["entity"] == "업계"), None)
    if g:
        tiles.append({"label": "시장 성장률", "value": f"{g['computed_pct']:+.1f}%", "note": f"{g['prior_period']} → {g['period']} · 코드 계산",
                      "doc_id": g["doc_id"]})
    if shares:
        s = shares[0]
        top = s["rows"][0]
        tiles.append({"label": "1위 점유율", "value": f"{top['share']:.1f}%", "note": f"{s['period']} · {top['entity']}", "doc_id": top["doc_id"]})
        if "CR2" in s["cr"]:
            tiles.append({"label": "상위 2사 합 (CR2)", "value": f"{s['cr']['CR2']:.1f}%",
                          "note": f"{s['period']} · " + "+".join(x["entity"] for x in s["rows"][:2]), "doc_id": None})

    exps = []
    for e in ex:
        end = _period_end(e["ends_at"])
        if end and end >= as_of:
            exps.append({**_strip(e), "days_left": (end - as_of).days})
    exps.sort(key=lambda e: e["days_left"])
    by_month = Counter(e["ends_at"][:7] for e in exps)
    clusters = [{"month": m, "n": n, "items": [f"{e['entity']} {e['item']}" for e in exps if e["ends_at"][:7] == m]}
                for m, n in sorted(by_month.items()) if n >= 2]

    return {"tiles": tiles, "metrics": [{k: v for k, v in r.items() if k != "prior_base"} for r in rows],
            "shares": shares, "growth": growth, "share_check": share_check, "conflicts": conflicts,
            "axis_phases": [_strip(p) for p in ph], "profitability": [_strip(p) for p in pr],
            "expirations": exps, "expiry_clusters": clusters, "notes": out.get("notes", [])}, warn
