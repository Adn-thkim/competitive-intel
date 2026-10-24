"""server/graph/reaction/extract.py — ⑤ Stage2 의견 units + 질문 questions 추출 코어 (Phase 1c 정본).

이전 원본(verbatim): poc_opinion_units(`_SYS·_SCHEMA·_prompt·_thread_chars·_split_by_chars·
_SVC_*·_profiles_hash·build_service_index·candidate_context·extract`) +
poc_opinion_units_community(`_post_id·_cand_label·_load_comment_cache·load_community_posts·
build_threads`). 로직 불변, 내부 LLM/병렬/한도 참조만 server.graph.reaction.llm 로 교체:
`_cli→cli`, `_pmap→pmap`, `_MODELS→MODELS`, `_ABORT→ABORT`, `_is_quota_exhausted→is_quota_exhausted`.

키워드 유틸(`build_keywords`)과 `_has_aspect_keyword`는 이미 server 노드
(youtube_reaction_collection_node)에 있어 그대로 재사용 — scripts 미참조.

build_threads(§6-3): 커뮤니티 게시글 → 스레드. **제목=t0 멤버**(프레이밍이자 인용 소스)를 포함하며,
제목만 있어도(본문 부재) 유지한다.
"""
from __future__ import annotations

import glob as _glob
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse

from server.graph.reaction.llm import ABORT, MODELS, cli, is_quota_exhausted
from server.graph.nodes.youtube_reaction_collection_node import (  # 키워드 유틸(정본)
    _ALL_KW, _aspect_label_keywords, _has_aspect_keyword,
)

REPO = Path(__file__).resolve().parents[3]

_SYS = (
    "너는 트래블카드 댓글에서 **의견 단위(opinion unit)** 를 추출한다. 사전 정의된 카테고리는 없다.\n"
    "[핵심 판정 원칙] — 개별 예외를 외우지 말고 아래 원칙으로 스스로 판단하라.\n"
    "P1. 각 단위 = (평가 **대상 하나** × 그 대상에 대한 화자의 **태도 하나**).\n"
    "P2. 먼저 **reasoning** 에 '이 의견의 진짜 대상이 무엇이고(특정 기능 / 카드 전반 / 혹은 카드가 아닌 "
    "전략·상황·습관·일반론), 화자의 태도가 무엇인지'를 한 문장으로 적은 뒤 나머지 필드를 채운다.\n"
    "P3. **polarity 는 '그 대상(특히 카드)의 품질·경험에 대한 화자의 태도'만** 반영한다. 단순 사실 전달·"
    "일반 조언·리스크 대비·습관·비교 없는 정보는 카드 품질 평가가 아니므로 **neutral**. 태도가 불명확해도 neutral.\n"
    "P4. 평가 대상이 **카드가 아니라 전략·상황·일반론**이면(예: '카드 한 장은 불안하니 여러 장 챙긴다' = "
    "복수 카드 지참 습관) 특정 카드 호오가 아니므로 target_candidate 를 비운다(none), polarity 는 neutral.\n"
    "P5. **질문은 버리지 말고 `questions` 로 낸다.** 태도(평가)를 담은 부분은 `units` 로, 묻는 부분은 "
    "`questions` 로 낸다. **한 댓글이 둘 다 담으면 양쪽 모두 낸다**(예: '저는 A 씁니다. 출금이 안 되는데 "
    "왜죠?' → units: A의 출금 부정 경험 / questions: 출금이 안 되는 이유). 태도도 질문도 아니면"
    "(잡담·단순 사실 안내) 어느 쪽에도 넣지 않는다.\n\n"
    "[각 단위(units) 필드]\n"
    "- reasoning: P2 의 판단(진짜 대상 + 화자 태도) 한 문장.\n"
    "- target_term: 평가 대상의 **핵심 기능/사물을 나타내는 짧은 명사구**(8자 이내 권장). 장소·기기 브랜드·"
    "국가·카드명·동작·상황 서술은 넣지 말 것. **특정 기능이 아니라 카드 자체에 대한 종합 판단**이면 '카드 전반'.\n"
    "    (예: 'ATM 출금' / '카드 디자인' / '고객센터 응대' / '카드 전반')\n"
    "- opinion: 평가 표현(예: '무료라 좋다','자꾸 막힌다'). 태도가 중립이면 사실 요약.\n"
    "- polarity: positive|negative|neutral(P3 적용),  intensity: 1~3\n"
    "- quote: 근거가 된 **원문 발췌 그대로**(요약·번역 금지)\n"
    "- source_comment_id: 그 의견이 나온 댓글의 id(프롬프트의 (c1),(c2)…)\n"
    "- target_candidate / candidate_source: **아래 규칙을 정확히·보수적으로 따른다. 추측 금지.**\n"
    "    1) 그 단위의 **source 댓글 본문**에 카드명이 있으면 → candidate_source=\"explicit\"\n"
    "    1.5) 카드명은 없지만 **그 카드의 부속 서비스·앱·연동 계좌·결제 네트워크가 그 의견의 주체(평가·질문이 "
    "향하는 대상)일 때** → 해당 candidate, candidate_source=\"service\". 소속 판단은 **세계지식으로**"
    "(프로필에 없어도 어느 카드사 것인지 알면 귀속).\n"
    "        · **핵심은 '언급'이 아니라 '주체'다.** 그 서비스/계좌가 의견의 주체면 귀속한다"
    "(예: '토스 외화통장에서 잘 빠져나가나요?' → 주체가 토스 외화통장 → 토스).\n"
    "        · 그러나 그 서비스/계좌가 **다른 카드의 연결 대상·배경으로만** 등장하면 귀속하지 말고 **주체인 그 "
    "카드**를 따른다(예: '네이버페이 카드를 토스뱅크에 연결하면 차감되나요?' → 주체는 네이버페이 카드 → 그쪽으로. "
    "토스뱅크는 연결 대상일 뿐).\n"
    "        · ★ **전용 부속 vs 독립 결제수단 구분(중요):** 그 서비스가 **그 카드를 써야만 성립하는 전용 요소**"
    "(그 카드로 충전하는 잔액·그 카드의 전용 앱·전용 연동 계좌)이면 그 카드에 귀속한다. 그러나 **여러 카드사·앱이 "
    "공통으로 쓰는 독립 간편결제·결제망**(그 특정 카드 없이도 다른 앱·은행 계좌로 이용 가능한 것)이면 특정 카드가 "
    "아니라 **비트래블카드**로 본다. 이때 **어느 은행 계좌를 연결했는지는 무관**하다(독립 결제수단에 계좌를 연결한 "
    "것일 뿐, 그 은행의 카드를 쓴 게 아니다).\n"
    "        · 그 카드와 무관한 맥락의 은행·회사명('OO은행 창구에서 환전')은 none.\n"
    "    2) 없고, **같은 스레드의 다른 댓글**에 카드명이 있으며 **그 단위가 명백히 같은 카드를 가리킬 때만** "
    "→ \"thread\". 스레드에 여러 카드가 섞였거나 어느 카드인지 불명확하면 → none.\n"
    "    3) 없고, **영상이 특정 카드 1개를 명확히 다루며**(비교·일반 여행 영상이면 해당 없음) 그 단위가 "
    "'이거/이 카드/저거'처럼 **그 카드를 지시**할 때만 → \"video\". 영상이 여러 카드·일반 주제거나 지시가 "
    "아니면 → none.\n"
    "    4) 특정 불가 → target_candidate=\"\", candidate_source=\"none\"\n"
    "    카드명 정규화: 토스 / 신한SOL / 하나트래블로그 / 트래블월렛 / **기타_트래블카드**(위비트래블 등 그 외 "
    "해외여행 특화 카드) / **비트래블카드**(카카오페이·유니온페이·네이버페이·일반 신용·체크카드 등 트래블카드가 "
    "아닌 결제수단). 트래블카드가 아니면 반드시 비트래블카드로.\n"
    "한 댓글에 서로 다른 대상의 평가가 여러 개면 **단위를 나눠** 각각 추출한다. 없으면 units=[].\n"
    "    ★ **복수 카드 병렬 나열**('A, B 같은 …', 'A랑 B 챙긴다')은 두 가지로 갈린다:\n"
    "      · **각 카드를 실제로 쓰거나 평가하는 병용 의견**이면(예: 'A, B 둘 다 잘 쓴다/좋다') → **카드별로 "
    "단위를 나눠** 각 candidate 에 귀속(A 단위 + B 단위).\n"
    "      · 특정 카드 호오가 아니라 **'이런 방식/부류' 예시로 든 정보성 나열**이면(예: 'A, B 같은 미리환전형 "
    "카드로 결제') → 개별 카드 호오가 아니므로 **전반 정보(P3·P4 적용, 대개 neutral)** 로 본다.\n"
    "[질문(questions) 항목] — 화자가 **묻고 있는 것**만. 태도가 없으므로 polarity·intensity 를 붙이지 말 것. "
    "**질문 유형 분류도 하지 말 것**(후단 담당). 질문이 없으면 questions=[].\n"
    "- reasoning: **먼저** '화자가 묻는 진짜 대상이 무엇인지'를 한 문장으로 적은 뒤 나머지를 채운다"
    "(P2 와 동일 취지 — 대상을 먼저 짚어야 target_term 이 정확해진다).\n"
    "- quote: 질문 원문 발췌 **그대로**\n"
    "- target_term: 묻는 **대상**. units 의 target_term 과 **동일 규칙**(핵심 기능 명사구 8자 이내, "
    "카드 자체에 대한 종합적 물음이면 '카드 전반').\n"
    "- source_comment_id: 그 질문이 나온 댓글의 id\n"
    "- target_candidate / candidate_source: units 와 **동일한 귀속 규칙**을 적용한다.\n\n"
    "**JSON 안전 규칙(엄수)**: 출력은 유효한 JSON 하나만. 문자열 값(특히 quote) 안의 큰따옴표(\")는 "
    "**작은따옴표(')로 바꿔** 담아라(원문에 큰따옴표가 있어도). 문자열 안에 줄바꿈·백슬래시(\\)를 넣지 마라. "
    "이 규칙 위반은 파싱 실패로 해당 스레드가 매 실행 재추출되는 원인이 된다.")

_SCHEMA = {"type": "object", "properties": {"results": {"type": "array", "items": {
    "type": "object", "properties": {
        "thread_id": {"type": "string"},
        "units": {"type": "array", "items": {"type": "object", "properties": {
            "reasoning": {"type": "string"},
            "target_term": {"type": "string"},
            "opinion": {"type": "string"},
            "polarity": {"type": "string"}, "intensity": {"type": "integer"},
            "quote": {"type": "string"}, "source_comment_id": {"type": "string"},
            "target_candidate": {"type": "string"}, "candidate_source": {"type": "string"}},
            # required 는 **핵심만**. reasoning·candidate 등을 required 로 두면 항목 1개의 누락이
            # 스키마 검증 실패 → 재시도 소진 → **배치 전체 소실**로 번진다(프롬프트로는 계속 요구).
            "required": ["target_term", "polarity", "quote", "source_comment_id"]}},
        "questions": {"type": "array", "items": {"type": "object", "properties": {
            "reasoning": {"type": "string"},
            "target_term": {"type": "string"}, "quote": {"type": "string"},
            "source_comment_id": {"type": "string"},
            "target_candidate": {"type": "string"}, "candidate_source": {"type": "string"}},
            "required": ["target_term", "quote", "source_comment_id"]}}},
    "required": ["thread_id", "units", "questions"]}}}, "required": ["results"]}


def _prompt(batch):
    """배치의 스레드를 **영상별로 묶어** 맥락 헤더를 영상당 1회만 낸다.
    (cap 배치는 단일 영상 → 헤더 1회. chunk-chars 배치는 여러 영상 가능 → 영상 바뀔 때마다 헤더.)"""
    body = []
    last_v = None
    # thread_id 단축: 프롬프트엔 짧은 인덱스(t1..)만 표기(긴 원본 id 반복 제거). 결과는 extract() 가
    # 동일 enumerate 로 역매핑한다(§ 프롬프트 비용 최적화). 순서가 매핑의 근거이므로 threads 순회 고정.
    for i, th in enumerate(batch["threads"], 1):
        if th["video_id"] != last_v:
            c = th["ctx"]
            body += [f"[영상] 제목: {c['title']}", f"[영상] 설명: {c['desc']}", ""]
            last_v = th["video_id"]
        body.append(f"[t{i}]")
        for lid, _, role, txt in th["members"]:
            tag = "원댓글" if role == "root" else "↳대댓글"
            body.append(f"  ({lid}) {tag}: {txt[:180]}")
    return "아래 영상 맥락과 스레드에서 의견 단위를 추출하라.\n" + "\n".join(body)


def _thread_chars(th) -> int:
    """스레드(원댓글+대댓글) 누적 원문 글자 수 — 프로덕션 _thread_chars 와 동일 기준."""
    return sum(len(txt) for _, _, _, txt in th["members"])


def _split_by_chars(threads, budget, min_chars):
    """프로덕션 reaction_analysis_node._split_threads 이식 — **스레드는 절대 분할하지 않는다.**
    누적 글자수 budget 까지 담되 다음 스레드가 넘기면 청크를 닫는다(단 min_chars 미만이면 계속 채움).
    단일 스레드가 budget 초과 시 그 스레드만으로 1 청크."""
    chunks, cur, cc = [], [], 0
    for th in threads:
        t = _thread_chars(th)
        if cur and cc + t > budget and cc >= min_chars:
            chunks.append(cur)
            cur, cc = [], 0
        cur.append(th)
        cc += t
    if cur:
        chunks.append(cur)
    return chunks


_SVC_GUIDE = (
    "\n댓글에 위 서비스/앱/계좌가 언급되면(카드명이 없어도) **너의 세계지식으로 소속 카드를 판단해** 그 "
    "candidate 로 귀속하라(source=\"service\").\n"
    "**위 목록은 참고일 뿐 폐쇄형이 아니다.** 목록에 없는 서비스라도, 그것이 어느 카드사·브랜드의 것인지 네가 "
    "알고 있다면 귀속하라. 목록에 없다는 이유로 none 으로 두지 말 것.")

_SVC_SYS = ("너는 각 상품(카드)의 프로필을 읽고, 그 카드에 **부속된 서비스·앱·연동 계좌·결제 네트워크의 고유 "
            "명칭만** 뽑는다. 일반 명사(체크카드·수수료·환율 등)나 기능 설명이 아니라 **고유 서비스명**만"
            "(예: 전용 앱 이름, 계좌 상품명, 제휴 결제망 이름). 없으면 빈 배열. 프로필에 근거해서만.")
_SVC_SCHEMA = {"type": "object", "properties": {"results": {"type": "array", "items": {
    "type": "object", "properties": {"product_name": {"type": "string"},
                                     "services": {"type": "array", "items": {"type": "string"}}},
    "required": ["product_name", "services"]}}}, "required": ["results"]}


def _profiles_hash(profiles: list, model: str) -> str:
    # 안정 키: (candidate_id, product_name) 만 사용. **변동성 큰 profile_summary 는 제외** —
    # full 재실행마다 profile_summary 가 미세하게 바뀌면(CLI 비결정성) service_index·dctx 지문이 흔들려
    # extract 캐시가 통째 orphan 되던 문제를 차단. 서비스명은 카드의 안정 속성이라 정확도 손실 없음.
    raw = json.dumps([[p.get("candidate_id"), p.get("product_name")] for p in profiles],
                     ensure_ascii=False, sort_keys=True) + "|" + model
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def build_service_index(state: dict, engine: str, model: str, timeout: int, mr: int) -> dict:
    """카드별 부속 서비스·계좌·앱 **고유명칭**을 프로필에서 1회 추출(도메인 무관). 입력 해시로 캐시.
    → compact 컨텍스트를 하드코딩 없이 동적으로 구성하는 재료."""
    profiles = state.get("product_profiles") or []
    if not profiles:
        return {}
    # §90 버그 수정: 캐시 키에 **engine 반영** — stub(빈 서비스) 결과가 cli 캐시를 오염시키지 않도록.
    cache = REPO / f"data/debug/_service_index_{engine}_{_profiles_hash(profiles, model)}.json"
    if cache.exists():
        return json.load(open(cache, encoding="utf-8"))
    if engine == "stub":          # 배선 검증용 — 실제 추출은 cli
        idx = {p.get("product_name", ""): [] for p in profiles if p.get("product_name")}
    else:
        body = "\n".join(f"- {p.get('product_name','')}: {(p.get('profile_summary') or '')[:600]}"
                         for p in profiles)
        res = cli(MODELS.get(model, model), _SVC_SYS, timeout, mr)(
            "아래 각 카드의 부속 서비스·앱·계좌 고유명칭을 뽑아라.\n" + body, _SVC_SCHEMA)
        idx = {r["product_name"]: list(dict.fromkeys(r.get("services", []))) for r in res.get("results", [])}
    cache.parent.mkdir(parents=True, exist_ok=True)
    json.dump(idx, open(cache, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return idx


def candidate_context(state: dict, mode: str = "full", service_index: dict | None = None) -> str:
    """candidate 목록 + 부속 서비스 귀속 지침. mode:
       full    : profile_summary 전문(최대 600자)까지 주입(정확·무거움).
       compact : 카드명 + service_index(동적 추출한 서비스 고유명칭)만(가벼움). 하드코딩 아님."""
    dt = state.get("domain_taxonomy") or {}
    profiles = state.get("product_profiles") or []
    lines = []
    for p in profiles:
        nm = p.get("product_name", "")
        if not nm:
            continue
        if mode == "compact":
            svcs = (service_index or {}).get(nm, [])
            lines.append(f"  · {nm}: {', '.join(svcs) if svcs else '(부속 서비스 미상)'}")
        else:
            summ = (p.get("profile_summary") or "").replace("\n", " ")[:600]
            lines.append(f"  · {nm}: {summ}")
    if not lines:
        return ""
    head = ("[도메인 컨텍스트] 도메인 '" + dt.get("domain_slug", "") +
            "'. 분석 대상 카드(candidate) ↔ 부속 서비스·앱·연동 계좌:\n")
    return head + "\n".join(lines) + _SVC_GUIDE


def extract(batch, engine, model, kw, timeout, max_retries, dctx=""):
    """1 배치(영상 1개) → {thread_id: {"units": [...], "questions": [...]}}, err
    실패 스레드는 None(진짜 0건과 구분). err 은 None 또는 원인 문자열."""
    ths = batch["threads"]
    nsent = len(ths)
    # 세션 한도 소진 시 즉시 중단 — 남은 배치가 재시도까지 소진하며 한도를 더 태우는 것을 막는다.
    if ABORT.is_set():
        return ({th["thread_id"]: None for th in ths},
                "ABORTED: 세션 한도 소진으로 실행 중단(호출 안 함)", (0, nsent))
    if engine == "stub":   # 휴리스틱: 스레드당 키워드 있으면 단위 1개, '?' 있으면 질문 1개
        out = {}
        for th in ths:
            txt = " ".join(t for _, _, _, t in th["members"])
            hit = _has_aspect_keyword(txt, kw)
            out[th["thread_id"]] = {
                "units": ([{"reasoning": "(stub)", "target_term": "(stub)", "opinion": "",
                            "polarity": "neutral", "intensity": 1, "quote": txt[:40],
                            "source_comment_id": th["members"][0][1],
                            "target_candidate": "", "candidate_source": "none"}] if hit else []),
                "questions": ([{"reasoning": "(stub)", "target_term": "(stub-q)", "quote": txt[:40],
                                "source_comment_id": th["members"][0][1],
                                "target_candidate": "", "candidate_source": "none"}]
                              if hit and "?" in txt else [])}
        return out, None, (nsent, nsent)
    call = cli(MODELS.get(model, model), (dctx + "\n\n" + _SYS) if dctx else _SYS, timeout, max_retries)
    lid2cid = {th["thread_id"]: {lid: cid for lid, cid, _, _ in th["members"]} for th in ths}
    # thread_id 단축: 프롬프트는 t1.. 로 보내므로 결과의 짧은 id 를 실제 thread_id 로 역매핑(_prompt 와 동일 enumerate).
    sid2tid = {f"t{i}": th["thread_id"] for i, th in enumerate(ths, 1)}
    try:
        res = call(_prompt(batch), _SCHEMA)
    except Exception as e:
        # 실패분은 빈 결과 위장 대신 None → failed_thread_ids 로 기록(진짜 0건과 구분).
        # 원인(타임아웃/스키마/429)을 삼키지 말고 반환 — 추측 대신 근거로 진단한다.
        msg = str(e)
        if is_quota_exhausted(msg):
            ABORT.set()          # 이후 배치는 호출 없이 즉시 반환
        return ({th["thread_id"]: None for th in ths},
                f"{type(e).__name__}: {msg[:300]}", (0, nsent))
    # 모델이 실제로 응답한 thread 수 — cap 을 키웠을 때 뒤쪽 스레드가 잘리는지(truncation) 감지용
    returned = {sid2tid.get(r.get("thread_id", ""), r.get("thread_id", "")) for r in res.get("results", [])}
    nret = sum(1 for th in ths if th["thread_id"] in returned)
    out = {}
    for r in res.get("results", []):
        tid = sid2tid.get(r.get("thread_id", ""), r.get("thread_id", ""))   # 짧은 id → 실제 thread_id
        rec = {}
        for key in ("units", "questions"):
            items = []
            for x in r.get(key, []) or []:
                x = dict(x)
                x["source_comment_id"] = lid2cid.get(tid, {}).get(x.get("source_comment_id", ""),
                                                                  x.get("source_comment_id", ""))
                # 선택 필드 누락은 배치를 죽이지 않고 공란으로 — 누락 자체는 summary 에서 집계
                for k, dv in (("reasoning", ""), ("target_candidate", ""), ("candidate_source", "none")):
                    x.setdefault(k, dv)
                items.append(x)
            rec[key] = items
        out[tid] = rec
    for th in ths:
        out.setdefault(th["thread_id"], {"units": [], "questions": []})
    return out, None, (nret, nsent)


def build_keywords(state: dict) -> set[str]:
    """youtube_reaction_collection_node 와 동일: _ALL_KW ∪ aspect_label 키워드."""
    cb = (((state.get("domain_taxonomy") or {}).get("report_config") or {})
          .get("reaction_insight") or {}).get("aspect_codebook")
    return set(_ALL_KW) | _aspect_label_keywords(cb)


# ── community(게시글) 스레드 조립 ─────────────────────────────────────────────

_COMMENT_CACHE = REPO / "data/cache/agent_outputs/community_comments"


def _post_id(url: str, body: str) -> str:
    return "cm_" + hashlib.sha1((url or body[:40]).encode()).hexdigest()[:12]


def _cand_label(state: dict) -> dict:
    """candidate_id → product_name (게시글 카드 컨텍스트용)."""
    return {p.get("candidate_id"): p.get("product_name", "")
            for p in (state.get("product_profiles") or []) if p.get("candidate_id")}


def _load_comment_cache() -> dict:
    """community_comments(Playwright) 캐시(url → 댓글 텍스트 리스트) — **프로덕션 노드와 동일 정본**.
    구조화 댓글 output.items[].text 를 추출(기존 content_fetch 문자열 blob 에서 전환 —
    이중 경로 불일치 해소)."""
    out = {}
    for f in _glob.glob(str(_COMMENT_CACHE / "*.json")):
        if f.endswith(" 2.json"):
            continue
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        u = (d.get("input") or {}).get("url", "")
        items = (d.get("output") or {}).get("items") or []
        lines = [(it.get("text") or "").strip() for it in items]
        lines = [x for x in lines if x and x not in ("댓글 영역", "댓글")]
        if u and lines:
            out[u] = lines
    return out


def load_community_posts(state: dict, with_comments: bool) -> list:
    """state.community_posts 를 **고유 url 로 병합**(chunk_index 순 본문 이어붙임) → 게시글 리스트.
    with_comments 면 캐시 댓글을 replies 로 붙인다."""
    rows = state.get("community_posts") or []
    by_url = {}
    for p in rows:
        u = p.get("url") or ""
        if u not in by_url:
            by_url[u] = {"url": u, "candidate_id": p.get("candidate_id"),
                         "site": urlparse(u).netloc.replace("m.", "").replace("www.", ""),
                         "title": p.get("title") or "", "chunks": {}}
        ci = p.get("chunk_index") or 0
        by_url[u]["chunks"][ci] = (p.get("body_excerpt") or p.get("content") or "")
    cmts = _load_comment_cache() if with_comments else {}
    posts = []
    for u, p in by_url.items():
        body = "\n".join(p["chunks"][k] for k in sorted(p["chunks"])).strip()
        if not body:
            continue
        posts.append({"url": u, "pid": _post_id(u, body), "candidate_id": p["candidate_id"],
                      "site": p["site"], "title": p["title"], "body": body,
                      "comments": cmts.get(u, [])})
    return posts


def build_threads(posts, id2name):
    """게시글 → 스레드. **제목=t0 멤버**(프레이밍이자 인용 소스, §6-3), 본문=c1, 댓글=c2….
    본문이 없어도 제목·댓글이 있으면 유지(본문 부재 ≠ 컨텐츠 부재). ctx.desc 에 '이 게시글은 X 카드 글' 을
    실어 지시성 귀속(rule 3)이 게시글 카드로 향하게 한다."""
    threads = []
    for p in posts:
        body = (p.get("body") or "").strip()
        title = (p.get("title") or "").strip()
        comments = [c for c in (p.get("comments") or [])[:20] if (c or "").strip()]
        if not (body or title or comments):
            continue
        pid = p.get("pid") or _post_id(p.get("url", ""), body)
        members = []
        if title:
            members.append(("t0", "title", "root", f"[제목] {title}"))   # 제목 멤버(§6-3)
        if body:
            members.append(("c1", "body", "root", body))
        members += [(f"c{i}", f"cmt{i}", "reply", c) for i, c in enumerate(comments, 2)]
        if not members:
            continue
        card = id2name.get(p.get("candidate_id"), "")
        ctx = {"title": title[:120],
               "desc": (f"이 게시글은 '{card}' 카드에 대한 커뮤니티 글이다. 카드명이 명시되지 않은 평가는 이 카드로 본다."
                        if card else "카드 관련 커뮤니티 글이다.")}
        threads.append({"thread_id": pid, "video_id": pid, "ctx": ctx, "members": members})
    return threads
