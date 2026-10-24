"""server/graph/reaction/prescreen.py — ②Stage0 + ③Stage1 + ④프리스크린 코어 (Phase 2 정본, youtube 전용).

이전 원본(verbatim): test_stage0(`_EXPLETIVE·_is_filler·_matched_kw·_basic_drop·stage0_route_group`) +
test_gate_prescreen_v2(`_PRE_SYS·_PRE_SCHEMA·_KEEP_ROOT·build_batches·prescreen_batch`) +
poc_opinion_units.main 의 youtube 스레드 조립부(`assemble_youtube_threads`).

로직 불변, 내부 참조만 server.graph.reaction.llm 로 교체(`_cli→cli`, `_pmap→pmap`). Stage0 판정 헬퍼
(`_is_noise·_is_pure_question·_has_aspect_keyword·_COMMENT_MIN_CHARS·_HAS_CONTENT_RE`)는 이미
youtube_reaction_collection_node(server 정본)에 있어 재사용 — scripts 미참조.

§6-2 비대칭: **youtube 전용**. community 는 1 게시글=1 스레드라 Stage0/1 을 스킵(extract 가 직접 조립).
"""
from __future__ import annotations

import glob as _glob
import json
import math
import re
from collections import defaultdict
from pathlib import Path

from server.graph.reaction.llm import cli, pmap
from server.graph.nodes.youtube_reaction_collection_node import (  # Stage0 판정 헬퍼(정본)
    _COMMENT_MIN_CHARS, _HAS_CONTENT_RE, _has_aspect_keyword, _is_noise, _is_pure_question,
)

REPO = Path(__file__).resolve().parents[3]
_YT_CACHE = REPO / "data/cache/agent_outputs/youtube_comments"

_KEEP_ROOT = {"content", "question"}

# 필러(웃음·이모지·감탄사·욕설만 남는 짧은 반응) 탐지 — _is_noise 가 못 잡는 "아시발ㅋㅋ" 류.
_EXPLETIVE = re.compile(r"아?\s*시\s*발|씨\s*발|시\s*바|ㅅㅂ|존나|ㅈㄴ|병신|ㅄ|미친|ㅁㅊ|지랄|ㅈㄹ")


def _is_filler(text: str) -> bool:
    t = _EXPLETIVE.sub("", text or "")
    t = re.sub(r"[ㅋㅎㅜㅠㅗㅡ]", "", t)          # 웃음·자모
    t = re.sub(r"[^0-9A-Za-z가-힣]", "", t)      # 기호·이모지·공백 제거(완성형 한글·영숫자만)
    return len(t) < 2


def _matched_kw(text: str, kw: set[str]) -> str:
    """text 에 포함된 aspect 키워드(첫 매칭) — 질문이 어떤 기능에 관한지 파악용."""
    for k in kw:
        if k in text:
            return k
    return ""


def _basic_drop(t: str) -> str | None:
    """길이·내용·노이즈·필러 기본 컷(최상위·대댓글 공통)."""
    if len(t) < _COMMENT_MIN_CHARS or not _HAS_CONTENT_RE.search(t):
        return "drop_short"
    if _is_noise(t) or _is_filler(t):
        return "drop_noise"
    return None


def stage0_route_group(items: list[dict], kw: set[str],
                       keep_all_replies: bool = False) -> list[tuple[dict, str, str]]:
    """한 컨텍스트(영상/게시글 스레드) 내 댓글들을 2-pass 라우팅.

    v2 변경(재설계):
    - **대댓글은 키워드로 자르지 않는다.** 앞 맥락에 이어 주어·목적어 없이 기능 의견을
      다는 대댓글을 보존하기 위함. 노이즈·필러·중복만 제거한다.
    - 필러(_is_filler)를 노이즈 컷에 포함 → "아시발ㅋㅋ" 류 제거.
    - keep_all_replies=False: 관련 스레드(최상위 content/question)의 대댓글만 content,
      아니면 drop_orphan.
    - keep_all_replies=True: **부모 관련성과 무관하게** 노이즈 통과 대댓글을 모두 content.
      (관련성 판정을 Stage 2/프리스크린에 위임 — orphan 손실 방지)

    반환: [(item, route, matched_keyword)]
    """
    seen: set[str] = set()
    out: list[tuple[dict, str, str]] = []
    relevant_threads: set[str] = set()

    # pass 1 — 최상위 댓글(스레드 관련성 확정)
    for c in items:
        if c.get("is_reply"):
            continue
        t = (c.get("text") or "").strip()
        mk = _matched_kw(t, kw)
        route = _basic_drop(t)
        if route is None:
            if t[:200] in seen:
                route = "drop_duplicate"
            else:
                seen.add(t[:200])
                has_kw = _has_aspect_keyword(t, kw)
                if _is_pure_question(t):
                    route = "question" if has_kw else "drop_question_noise"
                elif has_kw:
                    route = "content"
                else:
                    route = "drop_keyword_miss"
        if route in ("content", "question"):
            relevant_threads.add(c.get("thread_id", ""))
        out.append((c, route, mk))

    # pass 2 — 대댓글(키워드 미적용, 관련 스레드 소속만 유지)
    for c in items:
        if not c.get("is_reply"):
            continue
        t = (c.get("text") or "").strip()
        mk = _matched_kw(t, kw)
        route = _basic_drop(t)
        if route is None:
            if t[:200] in seen:
                route = "drop_duplicate"
            else:
                seen.add(t[:200])
                if keep_all_replies or c.get("thread_id", "") in relevant_threads:
                    route = "content"
                else:
                    route = "drop_orphan"
        out.append((c, route, mk))
    return out


# ── 프리스크린(스레드 관련성 판정) ──────────────────────────────────────────────
_PRE_SYS = ("너는 댓글 스레드가 '트래블카드 상품의 기능·사용경험 반응'을 담는지 판정한다. 영상 맥락과 "
            "스레드(원댓글+대댓글)를 보고 스레드별 relevant(boolean). 잡담·진행자 언급·여행 안전 수다는 false.")
_PRE_SCHEMA = {"type": "object", "properties": {"results": {"type": "array", "items": {
    "type": "object", "properties": {"thread_id": {"type": "string"}, "relevant": {"type": "boolean"}},
    "required": ["thread_id", "relevant"]}}}, "required": ["results"]}


def build_batches(vid, ctx, raw, kw, thread_cap):
    """영상 원본 댓글 → 프리스크린용 서브배치([{video_id, ctx, threads:[{thread_id,root_text,replies}]}])."""
    routed = stage0_route_group(raw, kw, keep_all_replies=True)
    th = defaultdict(lambda: {"root": None, "rr": None, "reps": []})
    for c, r, _ in routed:
        k = c.get("thread_id", "")
        if not c.get("is_reply"):
            th[k]["root"] = c
            th[k]["rr"] = r
        else:
            th[k]["reps"].append((c, r))
    threads = []
    for k, g in th.items():
        replies = [c.get("text", "") for c, r in g["reps"] if r == "content"]
        if g["rr"] not in _KEEP_ROOT and not replies:
            continue
        threads.append({"thread_id": k, "root_text": (g["root"] or {}).get("text", ""),
                        "replies": replies})
    n = math.ceil(len(threads) / thread_cap) if threads else 0
    return [{"video_id": vid, "ctx": ctx, "threads": threads[i * thread_cap:(i + 1) * thread_cap]}
            for i in range(n)]


def auto_drop_thread(root_text: str, replies, kw, drop_len: int = 40) -> bool:
    """②-보수: **결정론 auto_drop** — aspect 키워드 0개 + 짧은 스레드는 무관으로 보고 LLM 없이 탈락.
    poc_prescreen_savings 측정상 이 컷의 **리콜 손실 0**(Sonnet도 탈락시키는 것만 제거) → 무손실 절감.
    auto_keep(과통과 위험)은 도입하지 않는다."""
    blob = ((root_text or "") + " " + " ".join(replies or [])).strip()
    return len(blob) < drop_len and not _has_aspect_keyword(blob, kw)


def prescreen_batch(batch, llm, model, kw, timeout, max_retries):
    """1 서브배치 → {thread_id: bool}.

    **실패(429·타임아웃 등) 시 예외를 전파한다**(과거엔 keep-all 로 삼켰음). 호출부(노드)가
    실패를 인지해 **캐시하지 않고 재시도**하도록 — 실패 판정을 성공처럼 캐시해 영구 재사용·필터
    무력화되던 문제(429-삼킴)를 근절한다."""
    if llm == "stub":
        return {th["thread_id"]: _has_aspect_keyword(th["root_text"] + " " + " ".join(th["replies"]), kw)
                for th in batch["threads"]}
    call = cli(model, _PRE_SYS, timeout, max_retries)
    # thread_id 단축: 프롬프트엔 짧은 인덱스(t1..tN)만 싣고, 결과는 실제 id 로 역매핑한다.
    # (유튜브 댓글 ID 26자가 스레드마다 반복 → 프롬프트의 40%+ 를 차지하던 것을 제거. 내용·품질 무손실)
    sid2tid = {f"t{i}": th["thread_id"] for i, th in enumerate(batch["threads"], 1)}
    body = [f"[영상] {batch['ctx']['title']}\n설명: {batch['ctx']['text'][:300]}"]
    for i, th in enumerate(batch["threads"], 1):
        body.append(f"[t{i}] 원댓글: {th['root_text'][:120]}")
        for r in th["replies"][:8]:
            body.append(f"   ↳ {r[:120]}")
    res = call("\n".join(body), _PRE_SCHEMA)   # 실패 시 예외 전파(삼키지 않음)
    return {sid2tid.get(r["thread_id"], r["thread_id"]): bool(r["relevant"])
            for r in res.get("results", [])}


# ── 등급 프리스크린(관련성 + 중요도 점수) — 선택지 B: keep=relevant, score=랭킹·컷 부가필드 ──
_PRE_GRADED_SCHEMA = {"type": "object", "properties": {"results": {"type": "array", "items": {
    "type": "object", "properties": {"thread_id": {"type": "string"}, "relevant": {"type": "boolean"},
                                     "importance_score": {"type": "integer"}},
    "required": ["thread_id", "relevant", "importance_score"]}}}, "required": ["results"]}


def prescreen_batch_graded(batch, llm, model, kw, timeout, max_retries):
    """prescreen_batch 의 등급판 → {thread_id: (relevant, score)}.

    관련성(relevant, keep 용)에 더해 importance_score(0~3, 랭킹·컷 용)를 한 호출로 산출. 프롬프트
    형식·thread_id 단축·실패 전파 규칙은 prescreen_batch 와 동일하고 루브릭(RUBRIC_BODY)만 덧붙인다."""
    from server.graph.reaction.gate import RUBRIC_BODY
    if llm == "stub":
        out = {}
        for th in batch["threads"]:
            keep = _has_aspect_keyword(th["root_text"] + " " + " ".join(th["replies"]), kw)
            out[th["thread_id"]] = (keep, 2 if keep else 0)
        return out
    call = cli(model, _PRE_SYS + RUBRIC_BODY, timeout, max_retries)
    sid2tid = {f"t{i}": th["thread_id"] for i, th in enumerate(batch["threads"], 1)}
    body = [f"[영상] {batch['ctx']['title']}\n설명: {batch['ctx']['text'][:300]}"]
    for i, th in enumerate(batch["threads"], 1):
        body.append(f"[t{i}] 원댓글: {th['root_text'][:120]}")
        for r in th["replies"][:8]:
            body.append(f"   ↳ {r[:120]}")
    res = call("\n".join(body), _PRE_GRADED_SCHEMA)   # 실패 시 예외 전파(삼키지 않음)
    return {sid2tid.get(r["thread_id"], r["thread_id"]):
            (bool(r["relevant"]), int(r.get("importance_score", 0)))
            for r in res.get("results", [])}


# ── youtube 추출용 스레드 조립(프리스크린 통과분만, member 포맷) ──────────────────
def assemble_youtube_threads(raw, kept_tids, title, desc, video_id, kw, max_replies=5):
    """poc_opinion_units.main 의 youtube 조립부 이식. stage0 라우팅 → 프리스크린 통과 스레드만
    extract member 포맷([(lid, comment_id, role, text)])으로 조립. ctx 는 extract._prompt 호환
    ({title, desc})."""
    routed = stage0_route_group(raw, kw, keep_all_replies=True)
    th = defaultdict(lambda: {"root": None, "reps": []})
    for c, r, _ in routed:
        k = c.get("thread_id", "")
        if not c.get("is_reply"):
            th[k]["root"] = c
        else:
            th[k]["reps"].append(c)
    ctx = {"title": title, "desc": (desc or "")[:500]}
    threads = []
    for k, g in th.items():
        if k not in kept_tids or not g["root"]:
            continue
        members = [("c1", g["root"].get("comment_id", ""), "root", g["root"].get("text", ""))]
        for i, c in enumerate(g["reps"][:max_replies], start=2):
            members.append((f"c{i}", c.get("comment_id", ""), "reply", c.get("text", "")))
        threads.append({"thread_id": k, "video_id": video_id, "ctx": ctx, "members": members})
    return threads


def load_youtube_raw(video_ids) -> dict:
    """youtube_comments(수집) 캐시에서 대상 영상의 원본 댓글 items 로드 → {video_id: [items]}.
    (프로덕션 노드와 동일 캐시 경로. ' 2.json' 중복본 제외.)"""
    want = set(video_ids or [])
    out: dict = {}
    for f in _glob.glob(str(_YT_CACHE / "*.json")):
        if f.endswith(" 2.json"):
            continue
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        v = (d.get("input") or {}).get("video_id", "")
        if v and (not want or v in want):
            out[v] = (d.get("output") or {}).get("items") or []
    return out
