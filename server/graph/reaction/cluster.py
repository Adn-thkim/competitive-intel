"""server/graph/reaction/cluster.py — ⑥ 군집(super_aspect) + perspective + question_type 코어 (Phase 1d 정본).

이전 원본(verbatim): poc_bottomup_cluster(`PERSPECTIVES·domain_context·cluster_terms·
normalize_labels·assign_perspective` + 프롬프트/스키마) + poc_cluster_units_questions
(`QUESTION_TYPES·map_questions·assign_question_type·_qmap_sys`) + poc_cluster_uq_v2
(`super_cluster_v2·_super_sys` + main 오케스트레이션→`run_clustering`).

로직 불변, 내부 참조만 server.graph.reaction.llm 로 교체: `_cli→cli`, `_pmap→pmap`, `_MODELS→MODELS`.

**super_cluster(v1)·_cluster_units 는 이식 제외** — poc_cluster_uq_v2 의 `super_cluster_v2`
(조건부 새 축 + '기타' 잔여 + 파편화 상한)로 대체된 구버전이라 정본 오케스트레이션에서 쓰지 않는다.

파이프라인(Phase A 도출):
  A 집계(distinct target_term) → B 하위군집(cluster_terms) → B1.5 정규화(normalize_labels)
  → B2 상위군집(super_cluster_v2) → C perspective(assign_perspective)
  → 질문: super_aspect 매핑(map_questions) + question_type(assign_question_type)
축이 이미 있으면(캐시) super_set 을 그대로 주입해 map_questions/재배정만 하는 Phase B 는 노드가 담당.
"""
from __future__ import annotations

from collections import Counter, defaultdict

from server.graph.reaction.llm import MODELS, cli, pmap


def _llm_cached(agent_id, sys_prompt, schema, model, body, call):
    """배치/호출 단위 **증분 캐시** — 프롬프트 body 문자열을 키로 LLM 결과(res)를 저장한다.

    cluster 는 다수 배치(cluster_terms·perspective·question_type·map 등)로 fan-out 되는데, 종전엔 한
    배치라도 429 나면 노드가 성공분까지 통째로 폐기(all-or-nothing)해 재실행이 매번 처음부터 → 세션
    한도에 계속 막혔다. 본 헬퍼로 **성공한 배치는 개별 캐시**되어, 재실행 시 그 배치는 LLM 미호출로
    replay 되고 **실패했던 배치만 재시도**한다(extract 청크 캐시와 동일 원리). 여러 세션에 걸쳐 실패분이
    줄며 결국 전량 캐시 → 노드 레벨 캐시까지 성사된다.

    실패(429·타임아웃 등 예외)는 **저장하지 않고 전파**한다(호출부의 기존 실패 처리·분할 재시도 유지).
    body(=프롬프트)가 출력을 완전히 결정하므로 body 해시만으로 안전하게 content-address 된다.
    """
    from server.graph.agent_cache import make_cache_context, load_agent_output, store_agent_output
    cctx = make_cache_context(agent_id=agent_id, model=MODELS.get(model, model),
                              system_prompt=sys_prompt, output_schema=schema)
    cin = {"body": body}
    hit = load_agent_output(agent_id=agent_id, cache_input=cin, context=cctx)
    if hit is not None:
        return hit["res"]                      # 캐시 히트 → LLM 미호출
    res = call(body, schema)                    # 실패 시 예외 전파(저장 안 함 → 재실행 재시도)
    store_agent_output(agent_id=agent_id, cache_input=cin, context=cctx, output={"res": res})
    return res


# ── 사전 정의 perspective (③ 사용자 결정: 강제배정 위험 낮음 → 고정 목록 + 기타) ────────────
PERSPECTIVES = [
    ("cost", "비용(수수료)", "수수료·요율·면제·무료 등 비용"),
    ("reliability", "작동 안정성", "오류·실패·거절·먹통·락 등 정상 작동 여부"),
    ("convenience", "편의성", "쉽고 빠름·조작·연동 등 사용 편의"),
    ("coverage", "적용 범위", "지원 국가·가맹점·기기 호환·한도 등 어디까지 되는가"),
    ("exchange_rate", "환율", "적용 환율·우대율"),
    ("benefit", "혜택", "캐시백·적립·라운지·할인 등 부가 혜택"),
    ("overall", "종합 평가", "특정 관점이 아닌 카드 전반 선호·추천·비추천"),
    ("other", "기타", "위 어디에도 명확히 맞지 않는 경우"),
]
_PSP_IDS = {p for p, _, _ in PERSPECTIVES}


def domain_context(state: dict) -> str:
    """state 에서 **얕은** 도메인 컨텍스트만 추출(slug·type·candidate명). codebook·aspect 목록은 제외.
    하드코딩이 아니라 state 기반이라 다른 도메인에도 그대로 동작한다."""
    dt = state.get("domain_taxonomy") or {}
    cands = [p.get("product_name", "") for p in (state.get("product_profiles") or []) if p.get("product_name")]
    return (f"[도메인 컨텍스트] 이 데이터는 '{dt.get('domain_slug','')}'"
            f"(유형: {dt.get('domain_type','')}) 도메인의 고객 반응이다. "
            f"분석 대상 상품(candidate): {', '.join(cands)}. "
            "이 도메인에서 **당연한 배경**(예: 이 상품군이 기본적으로 다루는 상황·장소·범위)은 구분 기준이 아니라 "
            "**수식어로 간주**하고, candidate 상품명(브랜드)도 기능 구분 기준이 아니다. "
            "단, 평가 '대상(기능)'이 다르면 구분한다.")


# ── B) 하위 군집 (배치 · codebook 미제공 · 도메인 컨텍스트만 주입) ──────────────
_SYS_CLUSTER = (
    "너는 고객 의견의 '대상 어구(target_term)' 목록을 **상향식으로 군집**한다. 미리 정한 카테고리는 없다. "
    "**도메인 무관 원칙**으로 묶어라:\n"
    "1) 장소·기기·브랜드·국가 등 **수식어는 무시하고, 가리키는 핵심 기능/대상이 같으면 반드시 하나의 군집**으로 "
    "묶는다(수식만 다른 것을 다른 군집으로 두지 말 것).\n"
    "2) ★ **평가 척도(관점)로 군집을 만들지 마라.** 비용·수수료·가격·품질·속도·안정성처럼 **여러 기능에 공통으로 "
    "물을 수 있는 척도**는 대상 자체가 아니다(별도 perspective 가 담당). 척도가 **특정 기능에 붙어 있으면**"
    "(예: 'X 수수료','X 속도') **그 기능(X)으로 묶어라**. **특정 기능 없이 대상 전반의 척도만** 말하면"
    "(예: '수수료 무료','전반적으로 저렴') **'종합'으로** 묶어라. **척도 자체('수수료'·'비용' 등)를 군집 이름으로 "
    "삼지 마라.**\n"
    "3) 특정 기능이 아니라 **대상 전체에 대한 종합 평가/선호/추천**은 별도의 '종합' 군집으로 분리한다.\n"
    "4) 각 군집에 **간결한 한국어 라벨**을 스스로 짓되, **라벨은 반드시 그 군집의 '기능/대상'을 나타낸다.** "
    "구성원 다수가 척도 단어(수수료·비용 등)를 포함하더라도 **라벨에는 기능만 쓰고 척도어는 넣지 마라** "
    "(예: 구성원이 대부분 'X 수수료'여도 라벨은 'X 이용', '수수료'가 아님). 지나치게 굵거나 잘게 나누지 말 것.\n"
    "5) 어디에도 안 맞는 소수는 \"uncategorized\".\n"
    "**전체 목록을 한 번에 보고** 판단한다(동의어가 흩어지지 않게). 출력은 각 term 에 cluster_label. "
    "term 은 입력 그대로.")
_SCHEMA_C = {"type": "object", "properties": {"results": {"type": "array", "items": {
    "type": "object", "properties": {"term": {"type": "string"}, "cluster_label": {"type": "string"}},
    "required": ["term", "cluster_label"]}}}, "required": ["results"]}

# ── B1.5) 하위 라벨 정규화 (동의 = 동일 의미·기능 병합, 단일 호출·소량) ─────────
_SYS_NORM = (
    "너는 상향식 군집이 만든 '하위 군집 라벨' 목록을 정규화한다. **배치 처리 때문에 같은 것이 여러 라벨로 "
    "갈라진 경우**를 병합하는 게 목적이다.\n"
    "**병합 기준은 단어·표기의 유사성이 아니라 '동일한 의미·동일한 기능/대상'이다.** 표현이 달라도(수식어·"
    "어순·동의어) 가리키는 기능·대상이 같으면 하나의 **대표 라벨(canonical)** 로 통합한다. 반대로 단어가 "
    "비슷해도 기능·대상이 다르면 병합하지 않는다(예: 같은 기기라도 '요금'과 '작동 여부'는 다른 대상).\n"
    "각 입력 라벨에 canonical 라벨을 부여한다(그 그룹의 대표는 가장 일반적·간결한 표현으로, **기능/대상을 "
    "나타내되 척도어(수수료·비용 등)는 라벨에 넣지 마라**). 병합 대상이 없으면 canonical = 입력 라벨 그대로. "
    "전체 목록을 한 번에 보고 판단. 입력 라벨(label) 은 그대로 반환.")
_SCHEMA_N = {"type": "object", "properties": {"results": {"type": "array", "items": {
    "type": "object", "properties": {"label": {"type": "string"}, "canonical": {"type": "string"}},
    "required": ["label", "canonical"]}}}, "required": ["results"]}

# ── C) 관점 배정 (③ 사전 정의 목록 강제 + 기타) ───────────────────────────────
_SYS_PSP = (
    "너는 고객 의견 단위에 **관점(perspective)** 을 아래 **고정 목록 중 하나**로 배정한다. 목록 밖 값 금지. "
    "특정 관점에 명확히 맞지 않으면 \"other\"(기타).\n"
    + "\n".join(f"- {i}: {l} — {d}" for i, l, d in PERSPECTIVES))
_SCHEMA_P = {"type": "object", "properties": {"results": {"type": "array", "items": {
    "type": "object", "properties": {"idx": {"type": "integer"}, "perspective": {"type": "string"}},
    "required": ["idx", "perspective"]}}}, "required": ["results"]}


def cluster_terms(terms_with_q, engine, model, timeout, mr, dctx="", cap=60):
    """[(term, quote)] → {term: cluster_label}. **배치 처리**(전체 데이터 스케일 반영).
    배치 간 동의어 분산은 이후 B1.5(정규화)가 병합한다."""
    if engine == "stub":
        def lab(t):
            tl = t.lower()
            if "카드 전반" in t: return "카드 전반"
            # 배치 분산을 흉내내려 일부러 ATM 을 두 라벨로 흩뜨림(정규화 검증용)
            if "출금" in tl or "인출" in tl: return "해외 ATM 출금"
            if "atm" in tl: return "ATM 현금인출"
            for key, name in [("환전", "환전·충전"), ("충전", "환전·충전"), ("결제", "해외 결제"),
                              ("수수료", "수수료"), ("혜택", "부가 혜택"), ("발급", "발급")]:
                if key in tl: return name
            return "uncategorized"
        return {t: lab(t) for t, _ in terms_with_q}, 0
    sys = (dctx + "\n\n" + _SYS_CLUSTER) if dctx else _SYS_CLUSTER
    call = cli(MODELS.get(model, model), sys, timeout, mr)
    batches = [terms_with_q[i:i + cap] for i in range(0, len(terms_with_q), cap)]
    out, failed = {}, 0

    def run(b, depth=0):
        body = [f"- {t}  (예: {q[:40]})" for t, q in b]
        try:
            res = _llm_cached("reaction_cluster_terms", sys, _SCHEMA_C, model,
                              "아래 term 을 군집하라.\n" + "\n".join(body), call)
            return {r["term"]: (r.get("cluster_label") or "uncategorized").strip()
                    for r in res.get("results", [])}, 0
        except Exception as e:
            msg = str(e)
            quota = "session limit" in msg.lower() or '"api_error_status":429' in msg
            # 실패 배치를 절반씩 쪼개 재시도 → 배치 1건 실패가 전면 붕괴(uncategorized)로 번지는 것 방지.
            # 단 세션 한도(429)면 쪼개도 소용없어 즉시 표면화.
            if not quota and len(b) > 8 and depth < 4:
                mid = len(b) // 2
                a, fa = run(b[:mid], depth + 1)
                c, fc = run(b[mid:], depth + 1)
                a.update(c)
                return a, fa + fc
            # 원인을 삼키지 말고 노출(silent fallback 방지)
            print(f"  ⚠️ [cluster] 배치 실패(크기 {len(b)}): {type(e).__name__}: {msg[:160]}", flush=True)
            return {t: "uncategorized" for t, _ in b}, 1
    for part, f in pmap(lambda b: run(b), batches, 6):
        out.update(part)
        failed += int(f)
    for t, _ in terms_with_q:
        out.setdefault(t, "uncategorized")
    return out, failed


def normalize_labels(labels, engine, model, timeout, mr, dctx=""):
    """B1.5: 하위 군집 라벨(소량) → {label: canonical}. 배치 분산된 동의(=동일 의미·기능)를 단일 호출로 병합."""
    if engine == "stub":
        can = {}
        for l in labels:
            can[l] = "ATM 현금인출" if ("ATM" in l or "출금" in l or "인출" in l) else l
        return can, 0
    sys = (dctx + "\n\n" + _SYS_NORM) if dctx else _SYS_NORM
    call = cli(MODELS.get(model, model), sys, timeout, mr)
    try:
        res = _llm_cached("reaction_cluster_normalize", sys, _SCHEMA_N, model,
                          "아래 하위 라벨을 정규화(동일 의미·기능 병합)하라.\n" + "\n".join(f"- {l}" for l in labels), call)
        out = {r["label"]: (r.get("canonical") or r["label"]).strip() for r in res.get("results", [])}
        failed = 0
    except Exception as e:
        print(f"  ⚠️ [normalize] 실패: {type(e).__name__}: {str(e)[:160]}", flush=True)
        out, failed = {}, 1
    for l in labels:
        out.setdefault(l, l)
    return out, failed


def assign_perspective(units, engine, model, timeout, mr, cap=30):
    """③ 사전 정의 목록(PERSPECTIVES) 중 하나로 배정. 목록 밖·애매하면 other."""
    if engine == "stub":
        for u in units:
            u["perspective"] = "other"
        return 0
    call = cli(MODELS.get(model, model), _SYS_PSP, timeout, mr)
    idx = {i: u for i, u in enumerate(units)}
    batches = [list(idx.items())[i:i + cap] for i in range(0, len(units), cap)]
    failed = 0

    def run(b):
        body = [f"- idx={i} | term: {u.get('target_term','')} | opinion: {u.get('opinion','')[:30]} | quote: {u.get('quote','')[:40]}" for i, u in b]
        try:
            res = _llm_cached("reaction_cluster_perspective", _SYS_PSP, _SCHEMA_P, model,
                              "아래 의견에 관점을 배정하라.\n" + "\n".join(body), call)
            return {r["idx"]: (r.get("perspective") if r.get("perspective") in _PSP_IDS else "other")
                    for r in res.get("results", [])}, False
        except Exception:
            return {}, True
    for part, f in pmap(run, batches, 8):
        for i, p in part.items():
            idx[i]["perspective"] = p
        failed += int(f)
    for u in units:
        u.setdefault("perspective", "other")
    return failed


# ── 질문: 고정 축 매핑(map_questions) + 질문 전용 2차 축(question_type) ───────────
def _qmap_sys(super_labels) -> str:
    """super_labels: [str] 또는 [(label, definition)]. 정의가 있으면 함께 제시(매핑 정확도↑)."""
    lines = []
    for x in super_labels:
        if isinstance(x, (list, tuple)):
            lines.append(f"- {x[0]}" + (f" — {x[1]}" if len(x) > 1 and x[1] else ""))
        else:
            lines.append(f"- {x}")
    return ("너는 고객 **질문**을 아래 **고정 aspect 목록 중 하나**로 배정한다(반응 units 에서 만들어진 축). "
            "**새 군집을 만들지 말고 매핑만** 한다.\n"
            "원칙:\n"
            "1) **질문이 실제로 묻는 핵심 기능/대상**으로 배정하라. 질문에 **스쳐 지나가듯 언급된 다른 기능·수단의 "
            "이름이나 표면 단어**(배경으로 등장하는 명칭)에 끌리지 말고, **질문의 초점 — 무엇의 무엇을 궁금해하는가**"
            "를 보라.\n"
            "2) 축은 **기능/대상 기준**이다. 비용·수수료·가격 같은 **관점**만 보고 배정하지 말고, 그 관점이 걸린 "
            "**기능**으로 배정하라.\n"
            "3) 목록 밖 값 금지. **정말로 어느 기능에도 명확히 맞지 않을 때만 \"기타\"**(억지 배정 금지).\n"
            "[고정 aspect 목록]\n" + "\n".join(lines) + "\n- 기타")


_QMAP_SCHEMA = {"type": "object", "properties": {"results": {"type": "array", "items": {
    "type": "object", "properties": {"idx": {"type": "integer"}, "aspect": {"type": "string"}},
    "required": ["idx", "aspect"]}}}, "required": ["results"]}


QUESTION_TYPES = [
    ("feasibility", "가능여부", "되는지·가능한지 여부를 물음(아직 문제를 겪지 않은 일반적 가능성)"),
    ("method", "방법", "어떻게 하는지 방법·절차를 물음"),
    ("cost", "비용", "비용·수수료·가격이 얼마인지·드는지를 물음"),
    ("condition", "조건·자격", "조건·자격·요건·한도 등 무엇이 필요하거나 제한되는지를 물음"),
    ("comparison", "비교", "둘 이상을 **명시적으로 비교**해 무엇이 나은지 물음"),
    ("recommendation", "추천요청", "무엇을 쓸지·추천을 요청"),
    ("troubleshooting", "원인·해결", "**이미 시도했으나 안 되는 상황**에서 왜 안 되는지·무엇이 문제인지·"
                                    "해결 방법을 물음('왜 안됨','~인데 안돼요')"),
    ("safety_anxiety", "안전·불안", "손실·위험·문제가 생길까 **불안해서** 괜찮은지 확인"),
    ("other", "기타", "위 어디에도 명확히 맞지 않을 때만(최후의 수단)"),
]
_QT_IDS = {i for i, _, _ in QUESTION_TYPES}
_QT_SYS = ("너는 고객 **질문**을 아래 **고정 question_type 중 하나**로 분류한다. 질문이 **'무엇을 알아내려 하는가'"
           "(묻는 의도)** 로 배정하라 — 질문이 향하는 기능(super_aspect)이 아니라 **묻는 성격**을 본다.\n"
           "원칙:\n"
           "1) 여러 유형에 걸치면 **주된 의도 하나**를 고른다(예: **이미 안 되는 상황의 원인**이면 feasibility 가 "
           "아니라 troubleshooting).\n"
           "2) **other 로 도피하지 말 것** — 위 8개 유형 중 실제 의도에 맞는 것이 있으면 반드시 그것을 쓴다. "
           "other 는 정말 어디에도 안 맞을 때만.\n"
           "목록 밖 값 금지.\n[question_type]\n"
           + "\n".join(f"- {i}: {l} — {d}" for i, l, d in QUESTION_TYPES))
_QT_SCHEMA = {"type": "object", "properties": {"results": {"type": "array", "items": {
    "type": "object", "properties": {"idx": {"type": "integer"}, "question_type": {"type": "string"}},
    "required": ["idx", "question_type"]}}}, "required": ["results"]}


def assign_question_type(questions, engine, model, timeout, mr, cap=30):
    """각 질문에 question_type(고정 목록) 배정. 반응의 assign_perspective 에 대응하는 질문 전용 축."""
    if engine == "stub":
        for q in questions:
            t = (q.get("quote") or "")
            q["question_type"] = ("troubleshooting" if "왜" in t or "안돼" in t or "안되" in t or "문제" in t else
                                  "cost" if "수수료" in t or "얼마" in t else
                                  "method" if "어떻게" in t or "방법" in t else
                                  "feasibility" if "되나" in t or "가능" in t or "되는" in t else "other")
        return 0
    call = cli(MODELS.get(model, model), _QT_SYS, timeout, mr)
    idx = {i: q for i, q in enumerate(questions)}
    batches = [list(idx.items())[i:i + cap] for i in range(0, len(questions), cap)]
    failed = 0

    def run(b):
        body = [f"- idx={i} | 대상: {q.get('target_term','')} | 질문: {q.get('quote','')[:60]}" for i, q in b]
        try:
            res = _llm_cached("reaction_cluster_qtype", _QT_SYS, _QT_SCHEMA, model,
                              "아래 질문을 question_type 으로 분류하라.\n" + "\n".join(body), call)
            return {r["idx"]: (r.get("question_type") if r.get("question_type") in _QT_IDS else "other")
                    for r in res.get("results", [])}, False
        except Exception:
            return {}, True
    for part, f in pmap(run, batches, 8):
        for i, t in part.items():
            idx[i]["question_type"] = t
        failed += int(f)
    for q in questions:
        q.setdefault("question_type", "other")
    return failed


def map_questions(questions, super_labels, engine, model, timeout, mr, dctx="", cap=30):
    """questions → 각 질문에 super_aspect(고정 목록 + '기타'). 배치 분류.
    super_labels: [str] 또는 [(label, definition)]."""
    names = [x[0] if isinstance(x, (list, tuple)) else x for x in super_labels]
    allowed = set(names) | {"기타"}
    if engine == "stub":                       # 휴리스틱: term 키워드로 대충 매핑
        for q in questions:
            t = (q.get("target_term") or "").lower()
            q["super_aspect"] = next((s for s in names
                                      if any(w in t for w in s.lower().split())), "기타")
        return 0
    sys = (dctx + "\n\n" + _qmap_sys(super_labels)) if dctx else _qmap_sys(super_labels)
    call = cli(MODELS.get(model, model), sys, timeout, mr)
    idx = {i: q for i, q in enumerate(questions)}
    batches = [list(idx.items())[i:i + cap] for i in range(0, len(questions), cap)]
    failed = 0

    def run(b):
        body = [f"- idx={i} | 대상: {q.get('target_term','')} | 질문: {q.get('quote','')[:50]}" for i, q in b]
        try:
            res = _llm_cached("reaction_cluster_qmap", sys, _QMAP_SCHEMA, model,
                              "아래 질문을 고정 aspect 축에 배정하라.\n" + "\n".join(body), call)
            return {r["idx"]: (r.get("aspect") if r.get("aspect") in allowed else "기타")
                    for r in res.get("results", [])}, False
        except Exception:
            return {}, True
    for part, f in pmap(run, batches, 8):
        for i, a in part.items():
            idx[i]["super_aspect"] = a
        failed += int(f)
    for q in questions:
        q.setdefault("super_aspect", "기타")
    return failed


# ── B2 개선: 조건부 새 축 + 기타 잔여 + 상한 (super_cluster v1 대체) ──────────────
def _super_sys(max_supers: int) -> str:
    return ("너는 상향식으로 만들어진 '하위 군집 라벨'들을 **더 큰 상위 군집(super_aspect)**으로 묶는다. "
            "미리 정한 목록은 없다. 각 라벨 옆의 (n=…)은 그 라벨의 빈도다.\n"
            "규칙:\n"
            "1) **묶는 기준은 오직 '같은 기능/대상(무엇을 하는가·무엇에 대한 것인가)'이다.**\n"
            "2) ★ **관점(perspective)으로 묶지 마라.** 비용·가격·수수료·품질·속도·안정성처럼 **여러 기능에 "
            "공통으로 물을 수 있는 평가 척도**는 상위 축의 기준이 아니다(그건 별도 perspective 필드가 담당). "
            "예: 서로 다른 기능 A·B 각각의 '비용'을 하나의 '비용' 축으로 묶지 말고, **A의 비용은 A 로, B의 비용은 "
            "B 로** 그 기능에 귀속시켜라. 공통 척도(비용 등)를 이유로 다른 기능을 합치는 것은 금지.\n"
            "3) **억지 배정 금지.** 기존 상위 어디에도 명확히 맞지 않으면: 그 라벨이 규모·독립성이 충분하면(n이 "
            "유의미하고 독자적 대상) **새 상위 축**을 만들고, 규모·독립성이 부족한 소수 잔여(n이 작고 애매)는 "
            "**\"기타\"**로 둔다.\n"
            "4) **의심스러우면 병합하지 말고 분리를 유지**한다(서로 다른 기능/대상을 하나로 뭉치는 것이 더 큰 오류).\n"
            "   ★ 상위 라벨도 **기능/대상**을 나타낸다. 하위 구성원이 척도어(수수료·비용 등)를 포함해도 "
            "**상위 라벨에 척도어를 넣지 마라**(예: 'ATM 수수료'가 아니라 'ATM 이용').\n"
            f"5) **파편화 방지**: 상위 축은 대략 {max_supers}개를 넘기지 말 것(1~2건짜리 미세 축 남발 금지).\n"
            "6) 특정 기능이 아닌 대상 전반에 대한 종합 평가는 '종합'으로 둔다.\n"
            "전체 목록을 한 번에 보고 판단. 출력은 각 하위 라벨에 super_label(하위 라벨은 입력 그대로).")


_SCHEMA_S = {"type": "object", "properties": {"results": {"type": "array", "items": {
    "type": "object", "properties": {"cluster_label": {"type": "string"}, "super_label": {"type": "string"}},
    "required": ["cluster_label", "super_label"]}}}, "required": ["results"]}


def super_cluster_v2(labels_with_n, engine, model, timeout, mr, dctx="", max_supers=12):
    """[(label, n)] → {label: super_label}. 조건부 새 축 + 기타 잔여."""
    if engine == "stub":                       # 검증용: 발급→새 축, 지참전략→기타
        def sup(l):
            ll = l.lower()
            if "발급" in ll or "자격" in ll: return "발급·자격"          # 새 축
            if "지참" in ll or "전략" in ll: return "기타"               # 소수 잔여
            for key, name in [("atm", "ATM 이용"), ("출금", "ATM 이용"), ("인출", "ATM 이용"),
                              ("결제", "결제 환경"), ("환전", "환전·충전"), ("충전", "환전·충전"),
                              ("환율", "환전·충전"), ("수수료", "수수료·비용"), ("앱", "앱·디지털 채널"),
                              ("혜택", "혜택·적립"), ("적립", "혜택·적립"), ("종합", "종합")]:
                if key in ll: return name
            return "기타"
        return {l: sup(l) for l, _ in labels_with_n}, 0
    sys = (dctx + "\n\n" + _super_sys(max_supers)) if dctx else _super_sys(max_supers)
    call = cli(MODELS.get(model, model), sys, timeout, mr)
    body = "\n".join(f"- {l}  (n={n})" for l, n in labels_with_n)
    try:
        res = _llm_cached("reaction_cluster_super", sys, _SCHEMA_S, model,
                          "아래 하위 군집 라벨을 상위 군집으로 묶어라.\n" + body, call)
        out = {r["cluster_label"]: (r.get("super_label") or r["cluster_label"]).strip()
               for r in res.get("results", [])}
        failed = 0
    except Exception as e:
        print(f"  ⚠️ [super_v2] 실패: {type(e).__name__}: {str(e)[:160]}", flush=True)
        out, failed = {}, 1
    for l, _ in labels_with_n:
        out.setdefault(l, "기타")
    return out, failed


def run_clustering(units, questions, state, engine, model, timeout, mr, max_supers=12):
    """Phase A 도출 오케스트레이션(poc_cluster_uq_v2.main 의 계산부 이식 — 파일 IO/argparse 제외).

    입력 units/questions 를 **제자리 변형**(sub_cluster·super_aspect·perspective·question_type 부여)하고
    집계 doc(meta·summary·units·questions)을 반환한다. 원본과 동일하게 units/questions 는 호출부에서
    dict 복사해 넘길 것(입력 필드 전량 보존).

    반환 doc.summary.super_to_sub 가 **고정 축(super_aspect→하위군집)** — 축 저장소에 저장해 Phase B 재사용.
    """
    try:
        dctx = domain_context(state)
    except Exception:
        dctx = ""

    # A) 집계
    term_q, term_n = {}, Counter()
    for u in units:
        t = (u.get("target_term") or "").strip()
        if not t:
            continue
        term_n[t] += 1
        term_q.setdefault(t, u.get("quote", ""))
    pairs = list(term_q.items())

    # B) 하위군집 · B1.5) 정규화
    tmap, cf = cluster_terms(pairs, engine, model, timeout, mr, dctx)
    raw = sorted(set(tmap.values()))
    nmap, nf = normalize_labels(raw, engine, model, timeout, mr, dctx)
    for t in list(tmap):
        tmap[t] = nmap.get(tmap[t], tmap[t])

    # 하위 라벨별 빈도(units) — B2 규모 판정용
    sub_n = Counter()
    for u in units:
        t = (u.get("target_term") or "").strip()
        sub_n[tmap.get(t, "uncategorized")] += 1
    labels_with_n = sorted(sub_n.items(), key=lambda x: -x[1])

    # B2) 개선된 상위군집(조건부 새 축 + 기타 + 상한)
    smap, sf = super_cluster_v2(labels_with_n, engine, model, timeout, mr, dctx, max_supers)
    super_set = sorted(set(smap.values()))

    # C) perspective
    pf = assign_perspective(units, engine, model, timeout, mr)
    for u in units:
        t = (u.get("target_term") or "").strip()
        u["sub_cluster"] = tmap.get(t, "uncategorized")
        u["super_aspect"] = smap.get(u["sub_cluster"], "기타")

    # 질문 매핑(고정 축) + 질문 전용 2차 축 question_type
    qf = map_questions(questions, super_set, engine, model, timeout, mr, dctx)
    qtf = assign_question_type(questions, engine, model, timeout, mr)

    # 재집계 + 교차표
    sub_by_sup = defaultdict(Counter)
    for u in units:
        sub_by_sup[u["super_aspect"]][u["sub_cluster"]] += 1
    sup_u = Counter(u["super_aspect"] for u in units)
    sup_q = Counter(q["super_aspect"] for q in questions)
    pol_by_sup = defaultdict(Counter)
    for u in units:
        pol_by_sup[u["super_aspect"]][u.get("polarity") or "?"] += 1
    all_sup = sorted(set(sup_u) | set(sup_q), key=lambda s: -(sup_u[s] + sup_q[s]))
    crosstab = [{"super_aspect": s, "reactions": sup_u[s], "pos": pol_by_sup[s]["positive"],
                 "neg": pol_by_sup[s]["negative"], "neu": pol_by_sup[s]["neutral"],
                 "questions": sup_q[s]} for s in all_sup]
    qt_dist = Counter(q.get("question_type") for q in questions)
    qt_by_sup = defaultdict(Counter)
    for q in questions:
        qt_by_sup[q["super_aspect"]][q.get("question_type") or "other"] += 1

    doc = {"meta": {"engine": engine, "model": model, "max_supers": max_supers,
                    "failed": {"cluster": cf, "normalize": nf, "super": sf, "perspective": pf,
                               "question_map": qf, "question_type": qtf}},
           "summary": {"units": len(units), "questions": len(questions),
                       "super_aspects": len(super_set),
                       "super_aspect_reactions": dict(sup_u.most_common()),
                       "super_aspect_questions": dict(sup_q.most_common()),
                       "units_기타": sup_u.get("기타", 0), "questions_기타": sup_q.get("기타", 0),
                       "super_to_sub": {s: dict(c.most_common()) for s, c in
                                        sorted(sub_by_sup.items(), key=lambda x: -sum(x[1].values()))},
                       "label_normalization": {l: c for l, c in nmap.items() if l != c},
                       "crosstab_aspect_x_reaction_question": crosstab,
                       "question_type_dist": dict(qt_dist.most_common()),
                       "crosstab_aspect_x_question_type": {s: dict(c.most_common())
                                                           for s, c in qt_by_sup.items()}},
           "units": units,
           "questions": questions}
    return doc
