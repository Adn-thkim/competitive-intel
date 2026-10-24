"""server/graph/reaction/gate.py — 영상/도메인 게이트 (Phase 1b 정본).

이전 원본(verbatim): test_gate_prescreen_v2(`_GATE_SYS·_GATE_SCHEMA·gate_videos`) +
test_stage1_v2(`build_domain_signals·video_gate`) + poc_opinion_units_community(`community_gate_sys`).
로직 불변, 내부 LLM/병렬 호출만 server.graph.reaction.llm 의 `cli`·`pmap` 으로 교체.

- youtube: `gate_videos(..., gate_sys=_GATE_SYS)`  — 영상 제목+설명 기반.
- community: `gate_videos(..., gate_sys=community_gate_sys(state))` — 게시글 제목+본문 기반(§6-2).
- stub(휴리스틱): `video_gate` — 도메인 시드 어휘 매칭(오프라인 검증용).
"""
from __future__ import annotations

from server.graph.reaction.llm import cli, pmap

_GATE_SYS = ("너는 유튜브 영상이 '해외여행용 트래블 체크/선불카드'(환전·해외결제·해외 ATM·재환전 등) "
             "상품의 리뷰·사용기·비교인지 판정한다. 제목·설명만 보고, 일반 신용카드·여행 브이로그·"
             "소매치기 주의 등 상품이 주제가 아니면 false.")

_GATE_SCHEMA = {"type": "object", "properties": {"results": {"type": "array", "items": {
    "type": "object", "properties": {"video_id": {"type": "string"}, "relevant": {"type": "boolean"}},
    "required": ["video_id", "relevant"]}}}, "required": ["results"]}

# ── 등급 점수(0~3) 루브릭 — 커뮤니티 gate·유튜브 prescreen 공통(prescreen.py 가 import) ──
# 점수화 지점은 채널마다 다르지만(community=gate, youtube=prescreen), 평가 기준(루브릭)은 동일하게
# 둔다. 카드 브랜드를 하드코딩하지 않고 '분석 대상 상품(candidate)'으로 일반화 — 도메인은 프롬프트가
# state 에서 주입(설계 원칙: 도메인 주입, 하드코딩 금지).
RUBRIC_BODY = (
    " 평가 기준: (a)도메인 관련성, (b)구체적 비교 유무, (c)실제 사용 경험의 구체성, "
    "(d)분석 대상 상품(candidate)에 대한 언급 정도. 이 넷을 종합해 매긴다.\n"
    "0=도메인 무관(대상 상품군과 무관한 잡담·진행자 언급 등)\n"
    "1=도메인은 관련되나 특정 상품 언급 없음(일반론·질문만)\n"
    "2=특정 상품을 언급하며 사용 경험이나 평가가 있음\n"
    "3=특정 상품에 대한 구체적 비교·수치·상세 사용경험(상품 간 비교 포함)")

_GATE_GRADED_SCHEMA = {"type": "object", "properties": {"results": {"type": "array", "items": {
    "type": "object", "properties": {"video_id": {"type": "string"}, "relevant": {"type": "boolean"},
                                     "domain_score": {"type": "integer"}},
    "required": ["video_id", "relevant", "domain_score"]}}}, "required": ["results"]}


def gate_videos_graded(vids, llm, model, signals, workers, timeout, max_retries,
                       gate_batch=15, gate_sys=_GATE_SYS):
    """gate_videos 의 등급판 → ({id: (relevant, score)}, ok).

    boolean 판정(relevant, keep 용)에 더해 importance/domain score(0~3, 랭킹·컷 용)를 한 호출로 함께
    산출한다(선택지 B: keep 은 relevant 로 유지, score 는 부가 필드). 실패 처리·보수통과·ok 규칙은
    gate_videos 와 동일. **커뮤니티 gate 전용** — 유튜브 영상 게이트는 boolean gate_videos 를 그대로 쓴다."""
    out = {v: (True, 0) for v, t, d in vids if not (t or d)}
    todo = [(v, t, d) for v, t, d in vids if (t or d)]
    if llm == "stub":
        for v, t, d in todo:
            keep = video_gate(t, d, signals) != "irrelevant"
            out[v] = (keep, 2 if keep else 0)
        return out, True
    call = cli(model, gate_sys + RUBRIC_BODY, timeout, max_retries)
    groups = [todo[i:i + gate_batch] for i in range(0, len(todo), gate_batch)]

    def run(group):
        lines = [f"- video_id={v} / 제목: {t} / 설명: {(d or '')[:300]}" for v, t, d in group]
        try:
            res = call("아래 항목을 판정하고 domain_score(0~3)를 매겨라.\n" + "\n".join(lines),
                       _GATE_GRADED_SCHEMA)
            return {r["video_id"]: (bool(r["relevant"]), int(r.get("domain_score", 0)))
                    for r in res.get("results", [])}, True
        except Exception:  # noqa: BLE001
            return {v: (True, 0) for v, t, d in group}, False   # 이번 실행만 보수통과, ok=False
    ok = True
    for part, part_ok in pmap(run, groups, workers):
        out.update(part or {})
        ok = ok and part_ok
    return out, ok


def score_cut(threads, cap, min_score):
    """점수 기반 물량 컷(결정론 후처리, 캐시 무효화 없음). threads 각 원소에 int 'importance_score' 부착 가정.

    절차: ① score>=min_score 필터 → ② 남은 수가 cap 이하면 전량, 초과면 score 내림차순 top-cap 절단.
    안정정렬(동점 시 입력 순서 유지 — build 순서=최신/수집순 보존)."""
    filt = [t for t in threads if int(t.get("importance_score", 0)) >= min_score]
    if len(filt) <= cap:
        return filt
    return sorted(filt, key=lambda t: -int(t.get("importance_score", 0)))[:cap]


def build_domain_signals(state: dict) -> set[str]:
    """candidate 브랜드명 + 트래블카드 도메인 시드 어휘. (최종본은 LLM 게이트로 대체)"""
    sig = {
        "트래블", "여행", "환전", "해외결제", "해외 결제", "해외atm", "해외 atm", "atm",
        "외화", "해외여행", "트래블카드", "해외카드", "해외 카드", "환율", "면세",
        "해외 사용", "해외결제 수수료", "체크카드", "선불카드",
    }
    for p in state.get("product_profiles") or []:
        name = (p.get("product_name") or "").lower()
        for tok in name.replace("카드", " ").split():
            if len(tok) >= 2:
                sig.add(tok)
    sig |= {"토스", "트래블월렛", "신한", "하나", "트래블로그", "sol", "쏠"}
    return {s.lower() for s in sig if s}


def video_gate(title: str, desc: str, signals: set[str]) -> str:
    """영상 도메인 게이트 판정: relevant | irrelevant | unknown.

    메타데이터(제목·설명) 부재 시 negative-false 를 피하려 'unknown' 으로 두고 통과(유효 데이터 보존).
    """
    if not (title or "").strip() and not (desc or "").strip():
        return "unknown"
    blob = f"{title} {desc}".lower()
    return "relevant" if any(s in blob for s in signals) else "irrelevant"


def gate_videos(vids, llm, model, signals, workers, timeout, max_retries,
                gate_batch=15, gate_sys=_GATE_SYS):
    """[(id,title,text)] → ({id: bool}, ok). 메타부재=True. (영상·게시글 공용)

    ok: 모든 배치가 정상 판정됐는가. **한 배치라도 실패하면 ok=False** — 이때 그 배치는 보수적
    통과(keep-all)로 채우되, **호출부는 ok=False 면 결과를 캐시하지 말아야 한다**(429 등 실패를
    성공 판정처럼 캐시해 영구 재사용·필터 무력화되는 것을 방지)."""
    out = {v: True for v, t, d in vids if not (t or d)}
    todo = [(v, t, d) for v, t, d in vids if (t or d)]
    if llm == "stub":
        for v, t, d in todo:
            out[v] = video_gate(t, d, signals) != "irrelevant"
        return out, True
    call = cli(model, gate_sys, timeout, max_retries)
    groups = [todo[i:i + gate_batch] for i in range(0, len(todo), gate_batch)]

    def run(group):
        lines = [f"- video_id={v} / 제목: {t} / 설명: {(d or '')[:300]}" for v, t, d in group]
        try:
            res = call("아래 영상들을 판정하라.\n" + "\n".join(lines), _GATE_SCHEMA)
            return {r["video_id"]: bool(r["relevant"]) for r in res.get("results", [])}, True
        except Exception:  # noqa: BLE001
            return {v: True for v, t, d in group}, False   # 이번 실행만 보수통과, ok=False
    ok = True
    for part, part_ok in pmap(run, groups, workers):
        out.update(part or {})
        ok = ok and part_ok
    return out, ok


def community_gate_sys(state: dict) -> str:
    """**커뮤니티 전용** 도메인 게이트 프롬프트. 유튜브 게이트(_GATE_SYS)와 분리 —
    데이터 특성이 다른 두 채널을 한 프롬프트로 판정하면 상호 오염되므로 별도 프롬프트로 둔다.
    출력 스키마는 유튜브 게이트와 동일(gate_videos 재사용). 도메인은 state 에서 주입(하드코딩 아님)."""
    dt = state.get("domain_taxonomy") or {}
    cands = [p.get("product_name", "") for p in (state.get("product_profiles") or []) if p.get("product_name")]
    dom = dt.get("domain_slug", "")
    return ("너는 커뮤니티 게시글(블로그·포럼 글)이 **분석 대상 상품군의 실제 리뷰·사용기·비교·질문**인지 "
            "판정한다. 제목과 본문을 함께 본다.\n"
            f"[분석 대상] 도메인 '{dom}'. 대상 상품(candidate): {', '.join(cands)}.\n"
            "다음이면 relevant=false:\n"
            "1) 그 상품군이 **주제가 아닌 글** — 다른 상품·일반 잡담·핫딜/이벤트 홍보·무관 주제(연예·스포츠 등).\n"
            "2) **게시글 목록·네비게이션·메뉴·갤러리 소개**만 있고 실제 내용이 없는 페이지(수집 과정에서 목록이 "
            "본문으로 들어온 경우 — 날짜·조회수·글제목 나열, '최근방문 갤러리' 등).\n"
            "3) **이미지·GIF·시스템 안내 텍스트**만 있고 사람의 의견·경험이 없는 글('GIF 다운로드', 인증번호 안내 등).\n"
            "반대로 **짧더라도 그 상품군을 실제로 쓰거나 평가·질문**하는 내용이면 relevant=true "
            "(본문이 짧다는 이유만으로 버리지 말 것).\n"
            "제목이 상품군 관련이어도 **본문이 1~3 에 해당하면 false** 다.")
