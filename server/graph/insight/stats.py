"""server/graph/insight/stats.py — ② 인사이트 리포트용 결정론 통계 모듈 (LLM 미사용).

입력 state 키
  reaction_clustered          : reaction_cluster_node 산출 {units, questions}
  product_profiles·own_product: candidate 명칭·자사 식별
  report_outputs.comparison_matrix / reaction_insight : 기존 리포트 엔벨로프(인용용)

출력 `build_insight_stats(state)` → dict (JSON 직렬화 가능, 동일 입력 → 동일 출력)
  meta · sample_policy · channel_split · aspect_table · perspective_table · questions ·
  own_negative_points · strong_opinions · question_patterns · matrix · cited · evidence(근거 원문 묶음)

수치 규칙 (docs/mockups/insight_report_authoring_rules.md §1)
  - 지수 = 강도 가중 (Σ sign×intensity) / Σ intensity  (긍정 +1·부정 −1·중립 0, intensity 결측=1)
  - 채널 가중 미적용(유튜브·커뮤니티 동일 1.0) — 대신 channel_split 으로 분리 제시
  - 표본 하한 4단계: ≥20 비율 서술 / 10~19 방향만 / 5~9 관측 적음 / <5 건수만 (sample_level)
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any

# ── 고정 분류 라벨 (정본: server/graph/reaction/cluster.py PERSPECTIVES·QUESTION_TYPES) ──
PERSPECTIVE_KO = {
    "cost": "비용", "reliability": "작동 안정성", "convenience": "편의성", "coverage": "적용 범위",
    "exchange_rate": "환율", "benefit": "혜택", "overall": "종합 평가", "other": "기타",
}
QUESTION_TYPE_KO = {
    "feasibility": "가능 여부", "method": "방법", "cost": "비용", "condition": "조건·자격",
    "comparison": "비교", "recommendation": "추천 요청", "troubleshooting": "원인·해결",
    "safety_anxiety": "안전·불안", "other": "기타",
}
# 질문 성격 그룹 — "정보형"=무엇이 되는지·조건·방법·비교를 묻는 질문 (목업 1·5·6장 기준)
QUESTION_GROUPS = {
    "info":            ("정보형", ("feasibility", "condition", "comparison", "method")),
    "recommendation":  ("추천 요청", ("recommendation",)),
    "cost":            ("비용", ("cost",)),
    "troubleshooting": ("원인·해결", ("troubleshooting",)),
    "other":           ("기타", ("safety_anxiety", "other")),
}
CHANNEL_KO = {"youtube": "유튜브", "community": "커뮤니티", "blog": "블로그"}
EXCLUDED_ASPECTS = {"기타"}
SIGN = {"positive": 1, "negative": -1, "neutral": 0}

# 판정 임계 (지수 격차) — 목업 5장 판정(접전 −0.04·−0.03 / 열위 −0.48·−0.56)과 정합
GAP_TIE = 0.10
# 확인 요청형 어법 — "~아닌가요/맞나요/건가요" 계열. 패턴에 걸린 것만 세므로 하한값(rules §5-4)
CONFIRM_PATTERN = re.compile(r"(아닌가요|아닌가\?|아닌가…|아닌가\.\.|맞나요|맞죠|맞는지요|건가요|아니었음|아니에요\?|아닌거죠)")
EVIDENCE_CAP = 60            # 근거 묶음 상한(유형별) — LLM 입력 비대화 방지


def sample_level(n: int) -> str:
    """표본 하한 4단계 — ratio(≥20) / direction(10~19) / thin(5~9) / count_only(<5)."""
    if n >= 20:
        return "ratio"
    if n >= 10:
        return "direction"
    if n >= 5:
        return "thin"
    return "count_only"


def _index(units: list[dict]) -> float | None:
    if not units:
        return None
    den = sum(int(u.get("intensity") or 1) for u in units)
    num = sum(SIGN.get(u.get("polarity"), 0) * int(u.get("intensity") or 1) for u in units)
    return round(num / den, 2) if den else None


def _pnn(units: list[dict]) -> dict:
    c = Counter(u.get("polarity") or "neutral" for u in units)
    n = len(units)
    return {"n": n, "pos": c.get("positive", 0), "neg": c.get("negative", 0),
            "neu": c.get("neutral", 0), "index": _index(units), "sample_level": sample_level(n)}


def _label_to_cid(state: dict) -> dict:
    from server.graph.nodes.reaction_report_node import build_label_to_cid
    return build_label_to_cid(state)


def _names(state: dict) -> tuple[str, dict, list]:
    own = state.get("own_product") or {}
    own_id = own.get("product_id") or ""
    names = {p["candidate_id"]: p.get("product_name") or p["candidate_id"]
             for p in (state.get("product_profiles") or []) if p.get("candidate_id")}
    order = ([own_id] if own_id in names else []) + sorted(c for c in names if c != own_id)
    return own_id, names, order


# ═════════════════════════════════════════════════════════════════════════════
def build_insight_stats(state: dict) -> dict[str, Any]:
    clustered = state.get("reaction_clustered") or {}
    units_all = list(clustered.get("units") or [])
    questions_all = list(clustered.get("questions") or [])
    own_id, names, order = _names(state)
    l2c = _label_to_cid(state)

    def cid_of(x):
        return l2c.get(x.get("target_candidate") or "")

    # 안정 ID 부여(입력 순서 기준) — LLM 분류는 ID 만 돌려주고 건수는 코드가 센다
    units = []
    for i, u in enumerate(units_all):
        units.append(dict(u, _id=f"u{i:03d}", _cid=cid_of(u)))
    questions = []
    for i, q in enumerate(questions_all):
        questions.append(dict(q, _id=f"q{i:03d}", _cid=cid_of(q)))
    cu = [u for u in units if u["_cid"]]                       # 분석 대상(자사+경쟁)
    excluded = Counter((u.get("target_candidate") or "미귀속") for u in units if not u["_cid"])

    by_cid = defaultdict(list)
    for u in cu:
        by_cid[u["_cid"]].append(u)
    comps = [c for c in order if c != own_id]

    # ── 채널 분리 ──────────────────────────────────────────────────────────
    channels = sorted({u.get("channel") or "" for u in cu if u.get("channel")})
    channel_split = {
        cid: {"all": _pnn(by_cid[cid]),
              **{ch: _pnn([u for u in by_cid[cid] if u.get("channel") == ch]) for ch in channels}}
        for cid in order
    }

    # ── 항목(super_aspect)별 자사 vs 경쟁 최고 ─────────────────────────────
    aspects = sorted({u.get("super_aspect") or "기타" for u in cu} - EXCLUDED_ASPECTS,
                     key=lambda a: -sum(1 for u in cu if u.get("super_aspect") == a))
    aspect_table = []
    for a in aspects:
        row = {cid: _pnn([u for u in by_cid[cid] if u.get("super_aspect") == a]) for cid in order}
        own_s = row.get(own_id) or _pnn([])
        best = max(((c, row[c]) for c in comps if row[c]["n"] > 0 and row[c]["index"] is not None),
                   key=lambda kv: (kv[1]["index"], kv[1]["n"]), default=(None, None))
        gap = (round(own_s["index"] - best[1]["index"], 2)
               if own_s["index"] is not None and best[1] else None)
        if own_s["n"] == 0:
            verdict = "측정 불가"
        elif own_s["n"] < 5:
            verdict = "표본 부족"
        elif gap is None:
            verdict = "판단 불가"
        elif gap == 0:
            verdict = "동률"
        elif abs(gap) < GAP_TIE:
            verdict = "접전"
        else:
            verdict = "우위" if gap > 0 else "열위"
        aspect_table.append({"aspect": a, "own": own_s, "best_competitor": best[0],
                             "best": best[1], "gap": gap, "verdict": verdict, "by_candidate": row})

    # ── 관점(perspective)별 상품 지수 + 자사 순위 ─────────────────────────
    perspective_table = []
    for p, label in PERSPECTIVE_KO.items():
        if p == "other":
            continue
        row = {cid: _pnn([u for u in by_cid[cid] if u.get("perspective") == p]) for cid in order}
        own_s = row.get(own_id) or _pnn([])
        ranked = sorted((c for c in order if row[c]["index"] is not None),
                        key=lambda c: -row[c]["index"])
        if own_s["index"] is None:
            pos = "측정 불가"
        else:
            higher = sum(1 for c in comps if row[c]["index"] is not None and row[c]["index"] > own_s["index"])
            ties = sum(1 for c in comps if row[c]["index"] is not None and row[c]["index"] == own_s["index"])
            rank = higher + 1
            if rank == 1 and ties == 0:
                pos = "단독 1위"
            elif ties:
                pos = f"{rank}위 동률"
            elif rank == len(ranked):
                pos = "최하"
            else:
                pos = f"{rank}위"
        perspective_table.append({"perspective": p, "label": label, "by_candidate": row,
                                  "own_position": pos, "own_sample_level": own_s["sample_level"]})

    # ── 질문 ───────────────────────────────────────────────────────────────
    def qtype(q):
        return q.get("question_type") or "other"
    q_types = Counter(qtype(q) for q in questions)
    q_groups = {g: {"label": lab, "n": sum(q_types.get(t, 0) for t in ts),
                    "types": {t: q_types.get(t, 0) for t in ts}}
                for g, (lab, ts) in QUESTION_GROUPS.items()}
    q_by_cid = defaultdict(list)
    for q in questions:
        if q["_cid"]:
            q_by_cid[q["_cid"]].append(q)
    q_cand = {cid: {"n": len(q_by_cid[cid]),
                    "types": dict(Counter(qtype(q) for q in q_by_cid[cid])),
                    "info": sum(1 for q in q_by_cid[cid] if qtype(q) in QUESTION_GROUPS["info"][1]),
                    "aspects": dict(Counter(q.get("super_aspect") or "기타" for q in q_by_cid[cid]).most_common())}
              for cid in order}
    own_q = q_by_cid.get(own_id, [])
    adoption = [q for q in own_q if qtype(q) in QUESTION_GROUPS["info"][1] + ("recommendation",)]
    questions_stats = {
        "total": len(questions), "by_type": dict(q_types.most_common()), "groups": q_groups,
        "by_channel": dict(Counter(q.get("channel") or "" for q in questions)),
        "by_candidate": q_cand,
        "own_adoption_review": {"n": len(adoption), "of": len(own_q),
                                "troubleshooting": sum(1 for q in own_q if qtype(q) == "troubleshooting")},
    }

    # ── 자사 부정 발생 지점·관점 분포 ─────────────────────────────────────
    own_units = by_cid.get(own_id, [])
    own_neg = [u for u in own_units if u.get("polarity") == "negative"]
    own_pos = [u for u in own_units if u.get("polarity") == "positive"]
    own_negative_points = {
        "n": len(own_neg),
        "by_aspect": dict(Counter(u.get("super_aspect") or "기타" for u in own_neg).most_common()),
        "perspective_mix": dict(Counter(PERSPECTIVE_KO.get(u.get("perspective") or "other", "기타")
                                        for u in own_units).most_common()),
    }

    # ── 강한 의견(강도 3) ─────────────────────────────────────────────────
    strong = {cid: _pnn([u for u in by_cid[cid] if int(u.get("intensity") or 0) >= 3]) for cid in order}

    # ── 질문 어법 패턴(확인 요청형) — 하한값 ───────────────────────────────
    confirm = [q for q in questions if CONFIRM_PATTERN.search(q.get("quote") or "")]
    question_patterns = {
        "confirm_request": {"n": len(confirm), "lower_bound": True,
                            "own_feasibility_confirm": sum(1 for q in own_q if qtype(q) == "feasibility"
                                                           and CONFIRM_PATTERN.search(q.get("quote") or "")),
                            "own_feasibility": sum(1 for q in own_q if qtype(q) == "feasibility"),
                            "examples": [q["quote"][:120] for q in confirm[:5]]},
    }

    # ── 매트릭스 요약 (comparison_matrix 엔벨로프) ─────────────────────────
    matrix = _matrix_summary(state, own_id, order, names)

    # ── 기존 리포트 서술 인용 (reaction_insight 엔벨로프) ─────────────────
    ri = ((state.get("report_outputs") or {}).get("reaction_insight") or {}).get("content") or {}
    cited = {"aspect_analysis": ri.get("aspect_analysis") or {},
             "question_overview": ri.get("question_overview") or {},
             "selected_aspects": ri.get("selected_aspects") or []}

    # ── 근거 원문 묶음 (분류 패스 입력) ───────────────────────────────────
    worst = [r for r in aspect_table if r["verdict"] == "열위"]
    focus = max(worst, key=lambda r: r["own"]["n"], default=None)
    focus_aspect = focus["aspect"] if focus else None

    def ev_u(u):
        return {"id": u["_id"], "aspect": u.get("super_aspect"), "perspective": u.get("perspective"),
                "channel": u.get("channel"), "intensity": u.get("intensity"),
                "opinion": (u.get("opinion") or "")[:160], "quote": (u.get("quote") or "")[:200]}

    def ev_q(q):
        return {"id": q["_id"], "candidate": names.get(q["_cid"], q.get("target_candidate") or "미귀속"),
                "type": qtype(q), "aspect": q.get("super_aspect"), "channel": q.get("channel"),
                "quote": (q.get("quote") or "")[:200]}

    evidence = {
        "own_negatives": [ev_u(u) for u in own_neg][:EVIDENCE_CAP],
        "own_positives": [ev_u(u) for u in own_pos][:EVIDENCE_CAP],
        "own_reliability": [ev_u(u) for u in own_units if u.get("perspective") == "reliability"],
        "focus_aspect": focus_aspect,
        "focus_units": [ev_u(u) for u in own_units if u.get("super_aspect") == focus_aspect
                        and u.get("polarity") in ("positive", "negative")] if focus_aspect else [],
        "focus_questions": [ev_q(q) for q in own_q if q.get("super_aspect") == focus_aspect] if focus_aspect else [],
        "questions": [ev_q(q) for q in questions],   # 질문 형태 분류용 전량
    }

    return {
        "meta": {
            "own_id": own_id, "candidates": [{"candidate_id": c, "name": names[c], "is_own": c == own_id}
                                             for c in order],
            "units": len(units), "candidate_units": len(cu), "questions": len(questions),
            "own_units": len(own_units), "own_questions": len(own_q),
            "excluded": dict(excluded.most_common()),
            "channels": {CHANNEL_KO.get(ch, ch): sum(1 for u in units if u.get("channel") == ch)
                         for ch in sorted({u.get("channel") or "" for u in units})},
        },
        "sample_policy": {"ratio": 20, "direction": 10, "thin": 5,
                          "index": "강도 가중 (Σsign×intensity)/Σintensity, 채널 무가중"},
        "channel_split": channel_split,
        "aspect_table": aspect_table,
        "perspective_table": perspective_table,
        "questions": questions_stats,
        "own_negative_points": own_negative_points,
        "strong_opinions": strong,
        "question_patterns": question_patterns,
        "matrix": matrix,
        "cited": cited,
        "evidence": evidence,
    }


def _matrix_summary(state: dict, own_id: str, order: list, names: dict) -> dict:
    cm = (state.get("report_outputs") or {}).get("comparison_matrix") or {}
    content = cm.get("content") or {}
    table = content.get("feature_table") or {}
    rows = {r.get("candidate_id"): r for r in table.get("rows") or []}
    features = []
    for col in table.get("columns") or []:
        fid = col.get("feature_id")
        cells = {}
        for cid in order:
            c = ((rows.get(cid) or {}).get("cells") or {}).get(fid) or {}
            cells[cid] = {"headline": c.get("headline") or c.get("display") or "",
                          "value": (c.get("value") or "")[:200], "condition": (c.get("condition") or "")[:200],
                          "items": c.get("items") or [], "status": c.get("extraction_status") or "",
                          "valid_until": c.get("valid_until") or "", "source_url": c.get("source_url") or ""}
        keyv = lambda x: "|".join(x["items"]) if x["items"] else (x["headline"] or "").strip()
        norm = lambda s: re.sub(r"\s+", "", s or "")
        vals = [keyv(cells[c]) for c in order if keyv(cells[c]) and keyv(cells[c]) != "미확인"]
        same_head = len(vals) == len(order) and len(set(vals)) == 1
        same_cond = len({norm(cells[c]["condition"]) for c in order}) == 1
        # headline 이 같아도 조건이 다르면 변별력이 있다(예: ATM "면제" 4사 동일 + 한도 조건 상이)
        identical = same_head and same_cond
        features.append({"feature_id": fid, "label": col.get("label") or fid, "identical": identical,
                         "headline_only_identical": same_head and not same_cond, "cells": cells})
    promos = [{"candidate_id": cid, "feature_id": f["feature_id"], "label": f["label"],
               "valid_until": f["cells"][cid]["valid_until"]}
              for f in features for cid in order if f["cells"][cid]["valid_until"]]
    return {"features": features, "product_summary": content.get("product_summary") or {},
            "promotions": promos + [p for p in content.get("promotional_footnotes") or []
                                    if p.get("valid_until")],
            "traps": content.get("traps_footnote") or []}
