"""server/graph/nodes/reaction_report_node.py — ⑦ 신 반응 인사이트 리포트 (통합계획 §4-3).

입력: reaction_clustered(⑥ 산출 — units/questions 에 super_aspect·sub_cluster·perspective·
question_type·polarity·target_candidate·channel 부여) + product_profiles(자사/경쟁 분류).

산출(reaction_report_output, 결정론적 집계 — LLM 미사용):
- heatmap: super_aspect × {자사, 경쟁} × {pos, neg, neu, net} — 자사 우위/열위/기회 탐색용.
- perspective_by_super / question_type_by_super: super_aspect 세분(관점·질문의도 비율).
- own_weakness_ranked / own_opportunity: 자사 net 기준 열위·기회 축 랭킹.

자사/경쟁 분류(§강화-A): **라벨→candidate_id 고정 테이블**(product_profiles 에서 결정론적 생성) 로
unit.target_candidate 를 candidate_id(own_*/comp_*)에 매핑 → id 네임스페이스로 자사/경쟁 판정.
'기타_트래블카드'·'비트래블카드'·미귀속('')은 특정 candidate 가 아니므로 **분석 제외**(excluded 집계만).
"""
from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone

from server.graph.progress_store import set_progress
from server.graph.state import AgentStep, DomainAnalysisState

logger = logging.getLogger(__name__)

# 특정 경쟁사가 아닌 버킷/미귀속 라벨 — 분석(자사 vs 경쟁 히트맵)에서 제외.
_EXCLUDE_LABELS = {"", "기타_트래블카드", "비트래블카드"}


def _step(status: str, started_at: str, error: str = "") -> AgentStep:
    s: AgentStep = {"step_name": "ReactionReport", "status": status,
                    "started_at": started_at,
                    "finished_at": datetime.now(timezone.utc).isoformat()}
    if error:
        s["error_message"] = error[:300]
    return s


def _norm(s: str) -> str:
    """결정론적 정규화 — 소문자화 + 공백·기호 제거(완성형 한글·영숫자만). LLM 미사용."""
    return re.sub(r"[^0-9a-z가-힣]", "", (s or "").lower())


def build_label_to_cid(state: dict) -> dict:
    """추출 라벨(target_candidate) → candidate_id **고정 테이블**(§강화-A, 결정론적).

    product_profiles 의 (candidate_id, product_name)에서, 각 라벨을 정규화-부분문자열 매칭으로
    candidate 에 귀속한다(예: '신한SOL'→norm '신한sol' ⊂ '신한sol트래블체크카드'→comp_신한sol…).
    문자열 규칙만 사용(LLM 아님) → 동일 state 에서 항상 같은 테이블. 버킷/미귀속은 테이블에 없음(제외)."""
    profiles = [(str(p.get("candidate_id", "")), p.get("product_name", ""))
                for p in (state.get("product_profiles") or []) if p.get("candidate_id")]
    labels = {(u.get("target_candidate") or "")
              for u in ((state.get("reaction_clustered") or {}).get("units") or [])}
    table: dict = {}
    for lab in labels:
        if lab in _EXCLUDE_LABELS:
            continue
        nl = _norm(lab)
        if not nl:
            continue
        for cid, pname in profiles:
            npn = _norm(pname)
            if nl and (nl in npn or npn in nl):   # 라벨 ⊂ 상품명 (또는 역)
                table[lab] = cid
                break
    return table


def _classify(tc: str, label2cid: dict) -> str | None:
    """target_candidate → 'own' | 'competitor' | None(분석 제외). id 네임스페이스 기준."""
    cid = label2cid.get(tc or "")
    if not cid:
        return None
    return "own" if cid.startswith("own") else "competitor"


def _units_to_reaction_analysis(units: list, label2cid: dict) -> dict:
    """신 clustered.units → 구 reaction_insight 결정론 헬퍼 입력 형태로 변환(shim).

    구 헬퍼(build_aspect_matrix·select_top_quotes)는 `{cid: {"tuples":[{aspect,polarity,intensity,
    channel,quote,source_url,posted_at}], "channel_counts", "sample_size"}}` 를 먹는다. 신 파이프라인의
    평면 units 를 이 형태로 매핑해 **구 페이지를 그대로 재활용**한다.
    - super_aspect → aspect (이미 한글 라벨), target_candidate → candidate_id(label2cid; 버킷/미귀속 제외)
    - posted_at 없음(월별 timeline 섹션은 페이지에서 제거) → 빈 문자열.
    """
    ra: dict = {}
    for u in units:
        cid = label2cid.get(u.get("target_candidate") or "")
        if not cid:
            continue                                   # 버킷/미귀속(_EXCLUDE_LABELS) 제외
        entry = ra.setdefault(cid, {"tuples": [], "channel_counts": {}, "sample_size": 0})
        ch = u.get("channel", "") or ""
        entry["tuples"].append({
            "aspect": u.get("super_aspect") or "기타",
            "polarity": u.get("polarity") or "neutral",
            "intensity": int(u.get("intensity", 1) or 1),
            "channel": ch,
            "quote": u.get("quote", "") or "",
            "source_url": u.get("source_url", "") or "",
            "perspective": u.get("perspective") or "other",   # 대표 예문 선정(지배 관점 우선)용
            "posted_at": "",
        })
        entry["channel_counts"][ch] = entry["channel_counts"].get(ch, 0) + 1
        entry["sample_size"] += 1
    return ra


def _select_representative_quotes(ra: dict, per_bucket: int = 3) -> list:
    """대표 예문 선정 — **버킷(super_aspect×candidate×polarity)의 지배 perspective 우선** 후 품질점수.

    기존 select_top_quotes 는 강도·구체성 점수만 봐서, 군집의 주요 의견이 아닌데 반응이 센 예문이
    대표로 뽑힐 수 있었다(사용자 지적). 여기선 그 버킷에서 **가장 많은 perspective(주요 의견)**에
    해당하는 예문을 먼저 고르고, 그 안에서 품질점수(_score_quote)로 상위 N. 발췌 중복 제거."""
    from server.graph.nodes.reaction_insight_node import _score_quote, _norm_quote
    buckets: dict = defaultdict(list)
    for cid in sorted(ra):
        for t in ra[cid]["tuples"]:
            if t.get("polarity") == "neutral":
                continue
            buckets[(t["aspect"], cid, t["polarity"])].append(t)
    quotes: list = []
    for key in sorted(buckets):
        aspect, cid, pol = key
        ts = buckets[key]
        dom = Counter(t.get("perspective", "other") for t in ts).most_common(1)[0][0]   # 주요 관점
        scored = []
        for t in ts:
            res = _score_quote(t)
            if res is None:
                continue
            score, masked = res
            # 정렬 키: (지배 perspective 아니면 1, 점수 desc). 주요 의견 예문을 앞세운다.
            scored.append(((0 if t.get("perspective", "other") == dom else 1, -score), masked, t))
        scored.sort(key=lambda x: x[0])
        chosen, seen = [], set()
        for _k, masked, t in scored:
            nq = _norm_quote(masked)[:40]
            if nq and nq in seen:
                continue
            seen.add(nq)
            chosen.append((masked, t))
            if len(chosen) >= per_bucket:
                break
        for masked, t in chosen:
            quotes.append({"aspect": aspect, "polarity": pol, "quote": masked, "candidate_id": cid,
                           "channel": t.get("channel", ""), "source_url": t.get("source_url", ""),
                           "posted_at": t.get("posted_at", ""), "intensity": int(t.get("intensity", 1) or 1),
                           "perspective": t.get("perspective", "other")})
    return quotes


_ANALYSIS_SYS = (
    "너는 경쟁 인텔리전스 분석가다. 각 반응 항목에 대해, 자사·경쟁사 고객 반응의 판단 근거(reasoning)와 "
    "집계 수치(stats)를 종합해 **요약 보고 형태**로 구조화한다:\n"
    "- positives: 자사의 주요 긍정 의견 (짧은 문장 bullet 1~3개, 정성)\n"
    "- negatives: 자사의 주요 부정 의견 (bullet 1~3개, 정성)\n"
    "- competition: 자사 vs 경쟁 비교 (bullet 1~3개). **정성 비교와 수치를 한 데 녹여 간결하게** 쓴다. "
    "반드시 다룰 것: ① 자사 부정률 vs 경쟁 평균 부정률, ② 자사 의견이 집중된 관점과 그 긍/부정, ③ 경쟁사는 "
    "어떤 관점에 집중되고 어떻게 다른지. **수치는 제공된 stats 값만 그대로 사용**(재계산·반올림 변경·추측 금지).\n"
    "모든 bullet 은 한 문장, 보고서 톤, 감탄사·수식어 없이 사실 중심. 원문 인용 금지. 근거 없는 항목은 빈 배열([]).\n"
    "**JSON 안전 규칙(엄수)**: 문자열 값 안에 큰따옴표(\") 를 절대 쓰지 마라(강조가 필요하면 작은따옴표). "
    "문자열 안에 줄바꿈을 넣지 마라. 백슬래시(\\) 를 쓰지 마라. 각 bullet 은 한 줄로 끝맺는다."
)
# idx(입력 순서)로 매핑 — LLM 이 super_aspect 문자열을 정확히 echo 못해 검증 실패하던 문제 회피.
_ANALYSIS_SCHEMA = {"type": "object", "properties": {"results": {"type": "array", "items": {
    "type": "object", "properties": {"idx": {"type": "integer"},
                                     "positives": {"type": "array", "items": {"type": "string"}},
                                     "negatives": {"type": "array", "items": {"type": "string"}},
                                     "competition": {"type": "array", "items": {"type": "string"}}},
    "required": ["idx"]}}}, "required": ["results"]}


def _analyze_aspects(selected: list, units: list, label2cid: dict, own_id: str,
                     cand_name: dict, model: str, cap: int = 8) -> dict:
    """선정 항목별 자사·경쟁 reasoning 을 LLM 으로 보고서 톤 분석(경쟁 비교 포함) → {super_aspect: text}.

    quote 대신 **reasoning** 을 근거로 요약. agent_cache 캐시(입력 reasoning 해시). 실패 시 빈 dict."""
    if not selected:
        return {}
    import json as _json
    from server.graph.reaction.llm import cli, MODELS, pmap
    from server.graph.agent_cache import make_cache_context, load_agent_output, store_agent_output

    from server.graph.reaction.cluster import PERSPECTIVES
    psp_ko = {pid: label for pid, label, _ in PERSPECTIVES}
    payload = []
    for i, sa in enumerate(selected):
        own = {"positive": [], "negative": []}
        comps: dict = defaultdict(lambda: {"positive": [], "negative": []})
        agg: dict = defaultdict(lambda: {"pos": 0, "neg": 0, "neu": 0,
                                         "psp": defaultdict(lambda: {"pos": 0, "neg": 0, "neu": 0})})
        for u in units:
            if (u.get("super_aspect") or "기타") != sa:
                continue
            cid = label2cid.get(u.get("target_candidate") or "")
            if not cid:
                continue
            name = "자사" if cid == own_id else cand_name.get(cid, cid)
            pol = u.get("polarity")
            k = "pos" if pol == "positive" else "neg" if pol == "negative" else "neu"
            a = agg[name]
            a[k] += 1
            a["psp"][psp_ko.get(u.get("perspective") or "other", u.get("perspective") or "other")][k] += 1
            if pol in ("positive", "negative"):
                r = (u.get("reasoning") or u.get("opinion") or "").strip()
                if r:
                    bucket = own if cid == own_id else comps[name]
                    if len(bucket[pol]) < cap:
                        bucket[pol].append(r)

        # 결정론 집계 → LLM 이 그대로 인용할 수치. 카드별 부정률·주요 관점(긍/부정), 경쟁 평균 부정률.
        def _card_stat(a):
            tot = a["pos"] + a["neg"] + a["neu"]
            tp = max(a["psp"].items(), key=lambda kv: sum(kv[1].values()), default=(None, None))[0]
            tpv = a["psp"].get(tp, {}) if tp else {}
            return {"total": tot, "neg_rate": round(a["neg"] / tot * 100) if tot else 0,
                    "pos_rate": round(a["pos"] / tot * 100) if tot else 0,
                    "top_perspective": tp, "top_perspective_pos": tpv.get("pos", 0), "top_perspective_neg": tpv.get("neg", 0)}
        own_stat = _card_stat(agg["자사"]) if "자사" in agg else {"total": 0, "neg_rate": 0, "pos_rate": 0}
        comp_stats = {n: _card_stat(a) for n, a in agg.items() if n != "자사"}
        comp_avg_neg = round(sum(c["neg_rate"] for c in comp_stats.values()) / len(comp_stats)) if comp_stats else 0
        stats = {"own": own_stat, "competitor_avg_neg_rate": comp_avg_neg, "competitors": comp_stats}
        payload.append({"idx": i, "super_aspect": sa, "own": own, "competitors": dict(comps), "stats": stats})

    m = MODELS.get(model, model)
    cctx = make_cache_context(agent_id="reaction_aspect_analysis", model=m,
                              system_prompt=_ANALYSIS_SYS, output_schema=_ANALYSIS_SCHEMA)
    cin = {"payload": payload}
    hit = load_agent_output(agent_id="reaction_aspect_analysis", cache_input=cin, context=cctx)
    if hit is not None:
        return hit.get("out", {})
    # 항목별 개별 호출(pmap) — 배치 통짜 JSON 이 큰따옴표 1개로 통째 파싱 실패하던 문제 회피.
    # 항목당 작은 JSON → 파싱 실패율↓ + 한 항목 실패가 나머지를 죽이지 않음(격리). 항목별 타이밍·성공 로그.
    import time as _time

    def _one(item):
        t0 = _time.time()
        sa = item.get("super_aspect", "")
        idx = item.get("idx")
        try:
            call = cli(m, _ANALYSIS_SYS, 600, 2)
            res = call("아래 항목([idx])을 분석하라. 결과의 idx 는 입력 idx 를 그대로 쓴다.\n```json\n"
                       + _json.dumps([item], ensure_ascii=False) + "\n```", _ANALYSIS_SCHEMA)
            rs = res.get("results", [])
            nb = sum(len(r.get(k) or []) for r in rs for k in ("positives", "negatives", "competition"))
            print(f"[reaction_aspect_analysis] ✓ idx={idx} '{sa}' {_time.time()-t0:.1f}s · bullets={nb}", flush=True)
            return rs
        except Exception as exc:  # noqa: BLE001
            print(f"[reaction_aspect_analysis] ✗ idx={idx} '{sa}' {_time.time()-t0:.1f}s 실패: "
                  f"{type(exc).__name__}: {str(exc)[:150]}", flush=True)
            return []
    _t_all = _time.time()
    parts = pmap(_one, payload, 4)
    n_fail = sum(1 for p in parts if not p)
    out = {}
    for r in (x for part in parts for x in (part or [])):
        i = r.get("idx")
        if not (isinstance(i, int) and 0 <= i < len(selected)):
            continue
        struct = {"positives": [s for s in (r.get("positives") or []) if s],
                  "negatives": [s for s in (r.get("negatives") or []) if s],
                  "competition": [s for s in (r.get("competition") or []) if s]}
        if any(struct.values()):
            out[selected[i]] = struct
    print(f"[reaction_aspect_analysis] 완료 {len(out)}/{len(selected)} 항목 생성 · 실패 {n_fail} · "
          f"총 {_time.time()-_t_all:.1f}s", flush=True)
    if out:
        store_agent_output(agent_id="reaction_aspect_analysis", cache_input=cin, context=cctx, output={"out": out})
    return out


_DESC_SYS = ("각 상위 반응 항목(super_aspect)이 고객 반응에서 무엇을 다루는 범주인지, 소속 하위 군집"
             "(sub_cluster) 목록을 근거로 **한 문장**으로 설명하라. 보고서 톤, 감탄사·수식어 없이 사실 중심. "
             "항목명을 그대로 반복하지 말고 무엇을 포괄하는지 풀어 설명한다.")
_DESC_SCHEMA = {"type": "object", "properties": {"results": {"type": "array", "items": {
    "type": "object", "properties": {"idx": {"type": "integer"}, "description": {"type": "string"}},
    "required": ["idx"]}}}, "required": ["results"]}


def _describe_aspects(super_to_sub: dict, model: str) -> dict:
    """super_aspect 별 sub_cluster 목록 → LLM 한 문장 설명 {super_aspect: desc}. 캐시. 실패 시 {}."""
    if not super_to_sub:
        return {}
    import json as _json
    from server.graph.reaction.llm import cli, MODELS
    from server.graph.agent_cache import make_cache_context, load_agent_output, store_agent_output
    items = sorted(super_to_sub.items())
    payload = [{"idx": i, "super_aspect": sa,
                "sub_clusters": [s for s, _ in Counter(subs).most_common(10)]}
               for i, (sa, subs) in enumerate(items)]
    m = MODELS.get(model, model)
    cctx = make_cache_context(agent_id="reaction_aspect_desc", model=m,
                              system_prompt=_DESC_SYS, output_schema=_DESC_SCHEMA)
    cin = {"payload": payload}
    hit = load_agent_output(agent_id="reaction_aspect_desc", cache_input=cin, context=cctx)
    if hit is not None:
        return hit.get("out", {})
    try:
        call = cli(m, _DESC_SYS, 600, 2)
        res = call("아래 각 항목([idx])을 설명하라. 결과의 idx 는 입력 idx 를 그대로 쓴다.\n```json\n"
                   + _json.dumps(payload, ensure_ascii=False) + "\n```", _DESC_SCHEMA)
        out = {}
        for r in res.get("results", []):
            i = r.get("idx")
            d = (r.get("description") or "").strip()
            if isinstance(i, int) and 0 <= i < len(items) and d:
                out[items[i][0]] = d
        print(f"[reaction_aspect_desc] {len(out)}/{len(items)} 항목 설명 생성", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[reaction_aspect_desc] 실패: {type(exc).__name__}: {str(exc)[:200]}", flush=True)
        out = {}
    if out:
        store_agent_output(agent_id="reaction_aspect_desc", cache_input=cin, context=cctx, output={"out": out})
    return out


_QUESTION_SYS = (
    "너는 경쟁 인텔리전스 분석가다. 자사 상품에 대해 고객이 던진 **질문**들을 유형(question_type)별로 보고, "
    "자사 고객이 주로 무엇을 궁금해하거나 혼란스러워하는지, 그리고 경쟁사 질문 패턴과 어떻게 다른지 요약한다:\n"
    "- focus: 자사 질문이 몰린 유형과 그 구체 내용 (짧은 문장 bullet 1~3개, 정성)\n"
    "- competition: 경쟁사 질문 패턴과의 비교 (bullet 1~3개). 어떤 유형이 자사에 더/덜 집중되는지 stats 수치를 "
    "녹여 간결하게. **수치는 제공된 stats 값만 그대로 사용**(재계산·추측 금지).\n"
    "모든 bullet 은 한 문장, 보고서 톤, 감탄사·수식어 없이 사실 중심. 원문 인용 금지. 근거 없는 항목은 빈 배열([]).\n"
    "**JSON 안전 규칙(엄수)**: 문자열 값 안에 큰따옴표(\") 를 절대 쓰지 마라. 줄바꿈·백슬래시 금지. bullet 은 한 줄로 끝맺는다."
)
_QUESTION_SCHEMA = {"type": "object", "properties": {
    "focus": {"type": "array", "items": {"type": "string"}},
    "competition": {"type": "array", "items": {"type": "string"}}}, "required": []}


def _analyze_questions(questions: list, label2cid: dict, own_id: str, cand_name: dict,
                       model: str, cap: int = 12) -> dict:
    """자사 질문(유형별)을 LLM 으로 보고서 톤 분석(경쟁 비교 포함) → {focus:[...], competition:[...]}.

    quote(질문 발췌)를 근거로 요약. agent_cache 캐시. 실패 시 {}."""
    import json as _json
    from server.graph.reaction.llm import cli, MODELS
    from server.graph.agent_cache import make_cache_context, load_agent_output, store_agent_output
    from server.graph.reaction.cluster import QUESTION_TYPES
    qt_ko = {qid: label for qid, label, _ in QUESTION_TYPES}

    own_q: dict = defaultdict(list)
    agg: dict = defaultdict(lambda: defaultdict(int))   # name → qtype_ko → count
    for q in questions:
        cid = label2cid.get(q.get("target_candidate") or "")
        if not cid:
            continue
        name = "자사" if cid == own_id else cand_name.get(cid, cid)
        qt = qt_ko.get(q.get("question_type") or "other", q.get("question_type") or "기타")
        agg[name][qt] += 1
        if cid == own_id:
            txt = (q.get("quote") or q.get("opinion") or "").strip()
            if txt and len(own_q[qt]) < cap:
                own_q[qt].append(txt)
    if not own_q:
        return {}
    own_tot = sum(sum(v.values()) for n, v in agg.items() if n == "자사")
    own_dist = {qt: {"count": c, "pct": round(c / own_tot * 100) if own_tot else 0}
                for qt, c in agg.get("자사", {}).items()}
    comp_dist = {n: dict(v) for n, v in agg.items() if n != "자사"}
    stats = {"own_total": own_tot, "own_by_type": own_dist, "competitors_by_type": comp_dist}
    payload = {"own_questions": dict(own_q), "stats": stats}

    m = MODELS.get(model, model)
    cctx = make_cache_context(agent_id="reaction_question_analysis", model=m,
                              system_prompt=_QUESTION_SYS, output_schema=_QUESTION_SCHEMA)
    cin = {"payload": payload}
    hit = load_agent_output(agent_id="reaction_question_analysis", cache_input=cin, context=cctx)
    if hit is not None:
        return hit.get("out", {})
    import time as _time
    t0 = _time.time()
    try:
        call = cli(m, _QUESTION_SYS, 600, 2)
        res = call("아래 자사 질문 데이터를 분석하라.\n```json\n"
                   + _json.dumps(payload, ensure_ascii=False) + "\n```", _QUESTION_SCHEMA)
        out = {"focus": [s for s in (res.get("focus") or []) if s],
               "competition": [s for s in (res.get("competition") or []) if s]}
        if not any(out.values()):
            out = {}
        print(f"[reaction_question_analysis] {'생성' if out else '빈 결과'} · {_time.time()-t0:.1f}s", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[reaction_question_analysis] 실패: {type(exc).__name__}: {str(exc)[:150]} · {_time.time()-t0:.1f}s", flush=True)
        out = {}
    if out:
        store_agent_output(agent_id="reaction_question_analysis", cache_input=cin, context=cctx, output={"out": out})
    return out


_OVERVIEW_SYS = (
    "너는 경쟁 인텔리전스 분석가다. 전체 candidate에 대한 고객 **질문** 분포를 보고, 고객이 선택 전에 무엇에 "
    "집중하고 무엇 때문에 망설이는지 한눈 요약을 만든다. 각 항목은 **한 문장, 40자 이내, 수식어 없이 사실만**:\n"
    "- focus: 질문이 몰린 기능/불안 top 2~3을 한 문장으로 (무엇에 관심·궁금·불안이 집중되나)\n"
    "- common: 모든 카드가 공통으로 많이 받는 질문 유형/주제 한 문장\n"
    "- diff: 자사에 유독 몰리거나 덜한 질문 한 문장(stats 로 확인될 때만, 없으면 빈 배열)\n"
    "질문량은 '중요도 × 불확실성'이라는 점을 감안해 단정하지 말 것. 제공된 stats 수치만 사용(추측 금지).\n"
    "**JSON 안전**: 문자열에 큰따옴표(\")·줄바꿈·백슬래시 금지."
)
_OVERVIEW_SCHEMA = {"type": "object", "properties": {
    "focus": {"type": "array", "items": {"type": "string"}},
    "common": {"type": "array", "items": {"type": "string"}},
    "diff": {"type": "array", "items": {"type": "string"}}}, "required": []}


def _summarize_questions_overview(questions: list, label2cid: dict, own_id: str,
                                  cand_name: dict, model: str) -> dict:
    """전체 질문 분포 → 한눈 요약 A블록(focus·common·diff) LLM 생성. 캐시. 실패 시 {}."""
    import json as _json
    from server.graph.reaction.llm import cli, MODELS
    from server.graph.agent_cache import make_cache_context, load_agent_output, store_agent_output
    from server.graph.reaction.cluster import QUESTION_TYPES
    qt_ko = {qid: label for qid, label, _ in QUESTION_TYPES}

    overall_type, overall_aspect = Counter(), Counter()
    by_cand_type: dict = defaultdict(Counter)
    for q in questions:
        cid = label2cid.get(q.get("target_candidate") or "")
        if not cid:
            continue
        name = "자사" if cid == own_id else cand_name.get(cid, cid)
        qt = qt_ko.get(q.get("question_type") or "other", q.get("question_type") or "기타")
        overall_type[qt] += 1
        overall_aspect[q.get("super_aspect") or "기타"] += 1
        by_cand_type[name][qt] += 1
    if sum(overall_type.values()) == 0:
        return {}
    payload = {"overall_by_type": dict(overall_type.most_common()),
               "overall_by_aspect": dict(overall_aspect.most_common(6)),
               "by_candidate_type": {n: dict(c.most_common()) for n, c in by_cand_type.items()}}

    m = MODELS.get(model, model)
    cctx = make_cache_context(agent_id="reaction_overview", model=m,
                              system_prompt=_OVERVIEW_SYS, output_schema=_OVERVIEW_SCHEMA)
    cin = {"payload": payload}
    hit = load_agent_output(agent_id="reaction_overview", cache_input=cin, context=cctx)
    if hit is not None:
        return hit.get("out", {})
    try:
        call = cli(m, _OVERVIEW_SYS, 600, 2)
        res = call("아래 전체 질문 분포를 요약하라.\n```json\n"
                   + _json.dumps(payload, ensure_ascii=False) + "\n```", _OVERVIEW_SCHEMA)
        out = {k: [s for s in (res.get(k) or []) if s] for k in ("focus", "common", "diff")}
        if not any(out.values()):
            out = {}
        print(f"[reaction_overview] {'생성' if out else '빈 결과'}", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[reaction_overview] 실패: {type(exc).__name__}: {str(exc)[:150]}", flush=True)
        out = {}
    if out:
        store_agent_output(agent_id="reaction_overview", cache_input=cin, context=cctx, output={"out": out})
    return out


def reaction_report_node(state: DomainAnalysisState, config: dict | None = None) -> dict:
    started_at = datetime.now(timezone.utc).isoformat()
    thread_id = (config or {}).get("configurable", {}).get("thread_id", "")
    if thread_id:
        try:
            set_progress(thread_id, "reaction_report", detail="반응 인사이트(자사 vs 경쟁 히트맵)")
        except Exception as exc:  # noqa: BLE001
            logger.debug("set_progress 실패: %s", exc)

    try:
        clustered = state.get("reaction_clustered") or {}
        units = clustered.get("units") or []
        questions = clustered.get("questions") or []
        print(f"[reaction_report] ENTRY units={len(units)} questions={len(questions)}", flush=True)
        if not units and not questions:
            return {"reaction_report_output": {"status": "empty"},
                    "agent_steps": [_step("skipped", started_at)]}

        label2cid = build_label_to_cid(dict(state))   # 라벨→id 고정 테이블(결정론)

        # ── heatmap: super_aspect × {own, competitor} × {pos,neg,neu} ──
        heat: dict = defaultdict(lambda: {"own": Counter(), "competitor": Counter()})
        psp_by_super: dict = defaultdict(Counter)
        excluded = Counter()               # 버킷/미귀속(기타_트래블카드·비트래블카드·'') — 분석 제외
        for u in units:
            sa = u.get("super_aspect") or "기타"
            pol = u.get("polarity") or "neutral"
            psp_by_super[sa][u.get("perspective") or "other"] += 1
            cls = _classify(u.get("target_candidate") or "", label2cid)
            if cls is None:
                excluded[sa] += 1
                continue
            heat[sa][cls][pol] += 1

        qt_by_super: dict = defaultdict(Counter)
        for q in questions:
            qt_by_super[q.get("super_aspect") or "기타"][q.get("question_type") or "other"] += 1

        def _cell(c: Counter) -> dict:
            pos, neg, neu = c["positive"], c["negative"], c["neutral"]
            return {"pos": pos, "neg": neg, "neu": neu, "total": pos + neg + neu, "net": pos - neg}

        heatmap = {sa: {"own": _cell(v["own"]), "competitor": _cell(v["competitor"]),
                        "excluded": excluded.get(sa, 0)}
                   for sa, v in heat.items()}

        # ── 자사 열위·기회 랭킹 (net = pos−neg) ──
        rows = []
        for sa, v in heatmap.items():
            o, c = v["own"], v["competitor"]
            rows.append({"super_aspect": sa, "own_net": o["net"], "own_total": o["total"],
                         "comp_net": c["net"], "comp_total": c["total"],
                         "gap": o["net"] - c["net"]})   # gap<0 = 경쟁 대비 열위
        own_weakness_ranked = sorted([r for r in rows if r["own_total"] >= 1],
                                     key=lambda r: r["own_net"])          # net 낮은 순(열위 먼저)
        own_opportunity = sorted([r for r in rows if r["gap"] < 0 and r["own_total"] >= 1],
                                 key=lambda r: r["gap"])                  # 경쟁 대비 열위 큰 순

        report = {
            "status": "ok",
            "generated_at": started_at,
            "axis_source": clustered.get("axis_source", ""),
            "totals": {
                "units": len(units), "questions": len(questions),
                "own_units": sum(v["own"]["total"] for v in heatmap.values()),
                "competitor_units": sum(v["competitor"]["total"] for v in heatmap.values()),
                "excluded_units": sum(excluded.values()),   # 버킷/미귀속(분석 제외)
                "by_channel": dict(Counter(u.get("channel") or "?" for u in units)),
            },
            "label_to_candidate_id": label2cid,   # 강화-A 매핑 근거(감사용)
            "heatmap": heatmap,
            "perspective_by_super": {sa: dict(c.most_common()) for sa, c in psp_by_super.items()},
            "question_type_by_super": {sa: dict(c.most_common()) for sa, c in qt_by_super.items()},
            "own_weakness_ranked": own_weakness_ranked,
            "own_opportunity": own_opportunity,
        }
        logger.info("reaction_report: super_aspect %d · 자사 unit %d · 경쟁 unit %d",
                    len(heatmap), report["totals"]["own_units"], report["totals"]["competitor_units"])

        # ── 세션 토큰 총계: gate 진입 스냅샷 대비 누적 diff(gate→report) ──
        try:
            from server.llm.claude_cli_analyzer import get_usage, format_usage
            u0 = state.get("reaction_usage_start") or {}
            now = get_usage()
            total = {k: now.get(k, 0) - u0.get(k, 0) for k in now}
            report["session_usage"] = total
            print(f"[session total] gate→report: {format_usage(total)} "
                  f"(cache_creation={total.get('cache_creation_tokens', 0):,})", flush=True)
        except Exception as exc:  # noqa: BLE001
            logger.debug("세션 usage 집계 실패: %s", exc)

        # ── 구 reaction_insight 페이지 재활용: 신 데이터 → envelope(결정론, LLM 미사용) ──
        # 구 결정론 헬퍼를 units→reaction_analysis shim 으로 재활용. timeline·suggestions·LLM 서술은
        # 제외(페이지에서도 제거). aspect=super_aspect(라벨 그대로). report_outputs merge reducer 로 기록.
        print("[reaction_report] reaction_insight envelope 생성 시작", flush=True)
        from server.graph.nodes.reaction_insight_node import (
            build_aspect_matrix, select_top_quotes, CHANNEL_WEIGHTS)
        from server.graph.nodes._report_node_common import build_report_envelope

        ra = _units_to_reaction_analysis(units, label2cid)
        matrix = build_aspect_matrix(ra)
        top_quotes = _select_representative_quotes(ra)   # 지배 perspective 우선(§대표 예문 개선)
        aspect_labels = {a: a for a in matrix}          # super_aspect 자체가 표시 라벨

        # super_aspect × candidate × perspective × {pos,neg,neu} — 항목별 관점 분포/긍부정 시각화용. 귀속 units 만.
        psp_sc: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: {"pos": 0, "neg": 0, "neu": 0})))
        for u in units:
            cid = label2cid.get(u.get("target_candidate") or "")
            if not cid:
                continue
            pol = u.get("polarity") or "neutral"
            sa = u.get("super_aspect") or "기타"
            psp = u.get("perspective") or "other"
            k = "pos" if pol == "positive" else "neg" if pol == "negative" else "neu"
            psp_sc[sa][cid][psp][k] += 1
        perspective_by_super_candidate = {
            sa: {cid: {p: dict(v) for p, v in pm.items()} for cid, pm in cids.items()}
            for sa, cids in psp_sc.items()}

        # 선정 항목: 항목별 총 의견량(귀속 전체) 내림차순 누적 50%까지, 종합·기타 제외(프런트 파이/분석 공통).
        # 적응형 floor(REACTION_ASPECT_FLOOR_ALPHA): 평균 점유율(1/k)의 α배 미만 저빈도 항목은 제외해
        # 경계 아티팩트(임계선 채우려 1%짜리를 끌어오는 문제) 방지. 내림차순이라 floor 미만이면 이후도 컷.
        from server.config import REACTION_ASPECT_FLOOR_ALPHA
        vol_sa = {sa: sum((v.get("pos", 0) + v.get("neg", 0) + v.get("neu", 0))
                          for pm in cids.values() for v in pm.values())
                  for sa, cids in perspective_by_super_candidate.items() if sa not in ("종합", "기타")}
        grand_sa = sum(vol_sa.values())
        floor_sa = grand_sa * (REACTION_ASPECT_FLOOR_ALPHA / (len(vol_sa) or 1))
        selected_aspects: list = []
        acc = 0
        for sa, v in sorted(vol_sa.items(), key=lambda x: -x[1]):
            if v < floor_sa:
                break
            selected_aspects.append(sa)
            acc += v
            if grand_sa and acc >= grand_sa * 0.5:
                break

        own_id = (state.get("own_product") or {}).get("product_id", "")
        cand_name = {p.get("candidate_id", ""): p.get("product_name", "")
                     for p in (state.get("product_profiles") or [])}

        # 전략적 관심(저빈도) 예외 경로 — 임시 휴리스틱. 상위 N에서 빠졌어도 "자사만 순부정/순긍정" 항목은
        # 리스크·차별화 신호라 별도 노출. 판정은 **히트맵 cellPolarity 와 동일한 net 극성**(weighted_sentiment
        # 부호: null/0=중립, <0=부정, >0=긍정)으로 맞춰 화면 일관성 확보. 표시용 pos/neg/neu 는 별도 합산.
        def _sa_cid_totals(sa: str) -> dict:
            out: dict = {}
            for cid, pm in (perspective_by_super_candidate.get(sa) or {}).items():
                t = {"pos": 0, "neg": 0, "neu": 0}
                for v in pm.values():
                    t["pos"] += v.get("pos", 0); t["neg"] += v.get("neg", 0); t["neu"] += v.get("neu", 0)
                out[cid] = t
            return out

        # rate-margin 판정(net 부호 대체) — 히트맵 flag·selected 태그와 동일 산식(config 공유).
        from server.config import (REACTION_OWNONLY_MIN_RATE as _OO_MIN,
                                   REACTION_OWNONLY_MARGIN as _OO_MARGIN,
                                   REACTION_OWNONLY_MIN_SAMPLES as _OO_MINS)

        def _rates(cell: dict):   # (neg_rate, pos_rate, tot) — 분모=tuple_count(중립 포함). 없으면 None.
            tot = cell.get("tuple_count", 0) if cell else 0
            if not tot:
                return None
            return (cell.get("negative", 0) / tot, cell.get("positive", 0) / tot, tot)
        sel_set = set(selected_aspects)
        own_only_neg, own_only_pos, exc_polarity = [], [], {}
        for sa, cells in matrix.items():
            if sa in ("종합", "기타") or sa in sel_set:   # 이미 상위 N 이면 중복 생략
                continue
            ow = _rates(cells.get(own_id))
            if not ow or ow[2] < _OO_MINS:               # 최소 표본 미만 → 저표본 과대 방지
                continue
            # 경쟁 '최고'값과 비교(평균은 강한 경쟁사를 희석). 표본 MIN_SAMPLES 미만 경쟁사는 제외(왜곡 방지).
            comp = [c for c in (_rates(v) for cid, v in cells.items() if cid != own_id)
                    if c and c[2] >= _OO_MINS]
            if not comp:
                continue
            comp_neg = max(c[0] for c in comp)   # 경쟁 최고 부정률
            comp_pos = max(c[1] for c in comp)   # 경쟁 최고 긍정률
            is_neg = ow[0] >= _OO_MIN and (ow[0] - comp_neg) >= _OO_MARGIN
            is_pos = ow[1] >= _OO_MIN and (ow[1] - comp_pos) >= _OO_MARGIN
            if is_neg:
                own_only_neg.append(sa)
            if is_pos:
                own_only_pos.append(sa)
            if is_neg or is_pos:
                exc_polarity[sa] = _sa_cid_totals(sa)
        own_only_neg.sort(key=lambda s: -exc_polarity[s].get(own_id, {}).get("neg", 0))
        own_only_pos.sort(key=lambda s: -exc_polarity[s].get(own_id, {}).get("pos", 0))

        # 선정 항목 + 예외 항목 모두 LLM 분석(reasoning 기반, 보고서 톤, 경쟁 비교) — 캐시. 실패해도 집계 유지.
        analysis_targets = list(dict.fromkeys([*selected_aspects, *own_only_neg, *own_only_pos]))
        aspect_analysis = _analyze_aspects(analysis_targets, units, label2cid, own_id, cand_name, "sonnet")

        # 반응 항목(super_aspect) 한 문장 설명 — sub_cluster 근거 LLM. 히트맵 상단 노출용. 전 항목.
        _s2s: dict = defaultdict(Counter)
        for u in units:
            _s2s[u.get("super_aspect") or "기타"][u.get("sub_cluster") or "기타"] += 1
        aspect_descriptions = _describe_aspects(dict(_s2s), "sonnet")

        # 자사 질문 유형 분석(LLM, 경쟁 비교) — 질문 유형 분포 차트 하단 카드용. 캐시. 실패해도 집계 유지.
        question_analysis = _analyze_questions(questions, label2cid, own_id, cand_name, "sonnet")
        # 한눈 요약 A블록(질문 종합: focus·common·diff) — 최상단 요약. LLM. B블록(강점/약점/기회)은 프런트 결정론.
        question_overview = _summarize_questions_overview(questions, label2cid, own_id, cand_name, "sonnet")

        # candidate별 채널 상세 표본(표본 카드용) — 유튜브 영상수·의견·질문 / 커뮤니티 게시글수·의견·질문.
        _detail = defaultdict(lambda: {"youtube": {"videos": set(), "units": 0, "questions": 0},
                                       "community": {"posts": set(), "units": 0, "questions": 0}})
        for u in units:
            cid = label2cid.get(u.get("target_candidate") or "")
            if not cid:
                continue
            ch = u.get("channel") or ""
            if ch == "youtube":
                d = _detail[cid]["youtube"]; d["units"] += 1
                if u.get("source_url"):
                    d["videos"].add(u["source_url"])         # 유튜브 source_url=영상 URL → distinct 영상 수
            elif ch == "community":
                d = _detail[cid]["community"]; d["units"] += 1
                d["posts"].add(u.get("thread_id") or u.get("source_url") or "")   # 게시글=스레드
        for q in questions:
            cid = label2cid.get(q.get("target_candidate") or "")
            if not cid:
                continue
            ch = q.get("channel") or ""
            if ch in ("youtube", "community"):
                _detail[cid][ch]["questions"] += 1
        channel_meta = {}
        for cid, e in ra.items():
            d = _detail.get(cid) or {"youtube": {"videos": set(), "units": 0, "questions": 0},
                                     "community": {"posts": set(), "units": 0, "questions": 0}}
            channel_meta[cid] = {
                "channel_counts": e["channel_counts"],   # 구 페이지 호환 유지
                "sample_size": e["sample_size"],
                "youtube": {"videos": len(d["youtube"]["videos"]),
                            "units": d["youtube"]["units"], "questions": d["youtube"]["questions"]},
                "community": {"posts": len(d["community"]["posts"]),
                              "units": d["community"]["units"], "questions": d["community"]["questions"]},
            }

        attributed = sum(e["sample_size"] for e in ra.values())   # 특정 candidate 귀속 units
        # 전체(분석 대상 candidate 귀속) perspective·question_type 분포 — 상단 요약 차트용. most_common 정렬.
        psp_overall, qt_overall, qt_own, qa_own = Counter(), Counter(), Counter(), Counter()
        q_aspect_cand: dict = defaultdict(lambda: defaultdict(int))   # super_aspect → cid → 질문 수(산점도용)
        for u in units:
            if label2cid.get(u.get("target_candidate") or ""):
                psp_overall[u.get("perspective") or "other"] += 1
        for q in questions:
            cid = label2cid.get(q.get("target_candidate") or "")
            if cid:
                qt_overall[q.get("question_type") or "other"] += 1
                q_aspect_cand[q.get("super_aspect") or "기타"][cid] += 1
                if cid == own_id:
                    qt_own[q.get("question_type") or "other"] += 1
                    qa_own[q.get("super_aspect") or "기타"] += 1   # 자사 질문의 반응 항목(super_aspect) 분포
        source_refs = [{"url": q["source_url"], "aspect": q["aspect"], "candidate_id": q["candidate_id"]}
                       for q in top_quotes if q.get("source_url")]
        insight_content = {
            "title": "고객 반응 인사이트",
            "aspect_labels": aspect_labels,
            "aspect_matrix": matrix,
            "top_quotes": top_quotes,
            "channel_meta": channel_meta,
            "channel_weights": CHANNEL_WEIGHTS,
            # 전체 vs 분석대상(자사+경쟁) vs 제외(비트래블카드·기타_트래블카드·미귀속) — 히트맵 반영 범위 표시용.
            "totals": {"units": len(units), "questions": len(questions),
                       "candidate_units": attributed, "excluded_units": len(units) - attributed},
            "perspective_overall": dict(psp_overall.most_common()),      # 관점 분포(의견 기준)
            "question_type_overall": dict(qt_overall.most_common()),     # 질문 유형 분포(전체, 질문 기준)
            "question_type_own": dict(qt_own.most_common()),             # 질문 유형 분포(자사, 질문 기준)
            "question_aspect_own": dict(qa_own.most_common()),           # 자사 질문의 반응 항목(super_aspect) 분포
            "question_aspect_by_candidate": {sa: dict(c) for sa, c in q_aspect_cand.items()},  # 산점도용(항목×cid 질문수)
            "question_analysis": question_analysis,                       # 자사 질문 유형 LLM 분석(focus·competition)
            "question_overview": question_overview,                       # 한눈 요약 A블록(질문 focus·common·diff)
            "perspective_by_super_candidate": perspective_by_super_candidate,   # 항목별 관점 분포
            "selected_aspects": selected_aspects,        # 의견 상위 누적 50%(적응형 floor·종합·기타 제외)
            "own_only_negative_aspects": own_only_neg,   # 전략 예외: 자사만 부정 반응(상위 N 밖, 저빈도 리스크)
            "own_only_positive_aspects": own_only_pos,   # 전략 예외: 자사만 긍정 반응(상위 N 밖, 차별화)
            "aspect_candidate_polarity": exc_polarity,   # 예외 항목별 cid→{pos,neg,neu}(자사·경쟁 위치 렌더용)
            "aspect_analysis": aspect_analysis,          # 선정+예외 항목별 LLM 분석(positives/negatives/competition · 정성+수치 병합)
            "aspect_descriptions": aspect_descriptions,  # super_aspect 한 문장 설명(sub_cluster 근거)
            "aspect_insights": [],                       # 구 LLM 서술 미사용
            "overall_summary": "",
        }
        insight_envelope = build_report_envelope(
            report_type="reaction_insight",
            rubric_version="deterministic (신 파이프라인 집계)",
            categories=[],
            content=insight_content,
            evaluation_score=0,
            source_references=source_refs,
            warnings=[],
        )

        print("[reaction_report] 완료 → report_outputs.reaction_insight 기록", flush=True)
        return {"reaction_report_output": report,
                "report_outputs": {"reaction_insight": insight_envelope},   # merge reducer — 자기 키만
                "agent_steps": [_step("completed", started_at)]}
    except Exception as exc:  # noqa: BLE001
        logger.exception("reaction_report 실패")
        return {"agent_steps": [_step("failed", started_at, f"{type(exc).__name__}: {exc}")],
                "errors": [{"node": "reaction_report_node", "error": str(exc)[:200],
                            "timestamp": datetime.now(timezone.utc).isoformat()}]}
