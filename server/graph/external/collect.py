"""외부 자료 수집 — LLM 탐색(Claude CLI + WebSearch·WebFetch) → 트랙 문서 묶음 → 원문 대조.

흐름
  1) external_search agent: 트랙 목표·후보·출발 검색어를 받아 직접 검색하고, 연 페이지의 구절을
     원문 그대로(excerpts) 반환한다. (캐시: agent_outputs/external_search, external_search_hours)
  2) doc_id 부여(h001·f001·m001 …) — 같은 agent 출력이면 같은 id → 추출 agent 캐시도 히트.
  3) 원문 대조: 서버가 URL 본문을 직접 받아(external_fetch 캐시) excerpt 가 실제로 있는지 확인.
     source_check = 확인 | 부분 | 불일치 | 미확인(본문 수집 실패). 불일치 문서 근거는 리포트에서 '미검증'.
LLM 이 쓴 구절을 LLM 이 다시 인용하는 순환을 3)이 끊는다 — 최종 근거는 서버가 받은 원문이다.
"""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

import requests

from server.cache_ttl import get_ttl_hours
from server.graph.external import agent as ext_agent

logger = logging.getLogger(__name__)

SEARCH_AGENT, FETCH_AGENT = "external_search", "external_fetch"
FETCH_TTL  = get_ttl_hours("external_fetch_hours", 7200)
TEXT_LIMIT = 3500          # 추출 agent 에 넘기는 문서당 글자 수
MAX_DOCS   = {"history": 24, "finance": 14, "market": 14}
GOALS = {
    "history": "후보 상품별로 기준일 36개월 이내의 혜택·수수료 변경(신설·확대·축소·원복·연장·종료·제휴), 회사의 공식 발언, "
               "국내 결제 혜택의 상시/한시 여부, 외화 잔액이 예수금인지, 혁신금융서비스 지정 여부를 보여주는 문서",
    "finance": "후보 상품·발급사가 공표한 가입자·계좌·카드 발급·이용 고객 수, 누적 환전·이용액, 외화예금 잔액 — 기준일이 있는 문서",
    "market":  "해외 체크카드(트래블카드) 시장 이용액·사업자별 점유율·성장률(집계 기관·범위 명시), 업계 수익성 압박, "
               "주요 사업자 상시 혜택의 종료 예정일, 경쟁 방식의 변화를 보여주는 문서",
}

# 오프라인·테스트용 대체 훅: fn(track, queries) -> docs (doc_id 포함 완성 문서)
COLLECT_OVERRIDE: Callable[[str, list[dict]], list[dict]] | None = None

_WS = re.compile(r"\s+")
_NORM = re.compile(r"[\s\"'“”‘’·.,!?()\[\]~\-–—…:;/]")


def _norm(s: str) -> str:
    return _NORM.sub("", s or "").lower()


def fetch_text(url: str) -> str:
    """서버가 직접 받은 본문 텍스트(스크립트·내비 제거). 실패는 빈 문자열."""
    from server.graph.agent_cache import load_agent_output, store_agent_output
    cin, ctx = {"url": url}, {"agent_id": FETCH_AGENT, "v": 1}
    hit = load_agent_output(agent_id=FETCH_AGENT, cache_input=cin, context=ctx, logger=logger, ttl_hours=FETCH_TTL)
    if hit is not None:
        return hit.get("text", "")
    try:
        r = requests.get(url, timeout=(3, 10), headers={"User-Agent": "Mozilla/5.0 (compatible; CI-ExternalResearch/0.2)"})
        r.raise_for_status()
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(r.content, "html.parser")
        for t in soup(["script", "style", "nav", "header", "footer", "aside", "form", "noscript"]):
            t.decompose()
        text = _WS.sub(" ", (soup.body or soup).get_text(" ")).strip()[:60000]
    except Exception as exc:  # noqa: BLE001
        logger.info("원문 수집 실패 %s: %s", url, exc)
        return ""
    if text:
        store_agent_output(agent_id=FETCH_AGENT, cache_input=cin, context=ctx, output={"text": text}, logger=logger)
    return text


def source_check(doc: dict) -> dict:
    page = _norm(fetch_text(doc["url"]))
    if not page:
        return {"source_check": "미확인", "excerpts_found": None}
    found = sum(1 for e in doc["excerpts"] if _norm(e) and _norm(e) in page)
    n = len(doc["excerpts"])
    return {"source_check": "확인" if found == n else "부분" if found else "불일치", "excerpts_found": f"{found}/{n}"}


def search_payload(track: str, queries: list[dict], cands: list[dict], as_of: str, window_start: str) -> dict:
    return {"track": track, "goal": GOALS[track], "as_of": as_of, "window_start": window_start,
            "candidates": [{k: c[k] for k in ("cid", "name", "provider", "is_own")} for c in cands],
            "seed_queries": [q["query"] for q in queries], "max_docs": MAX_DOCS[track]}


def collect(track: str, queries: list[dict], cands: list[dict], as_of: str, window_start: str,
            refresh: bool = False) -> tuple[list[dict], str, dict]:
    """트랙 문서 묶음. 반환 (docs, 생성 경로, 탐색 메타{searches, gaps})."""
    if COLLECT_OVERRIDE is not None:
        docs = COLLECT_OVERRIDE(track, queries)
        for d in docs:
            d.setdefault("source_check", "대행 작성")
        return docs, "override", {"searches": [q["query"] for q in queries], "gaps": []}
    out, how = ext_agent.run(SEARCH_AGENT, search_payload(track, queries, cands, as_of, window_start), refresh)
    seen, raw = set(), []
    for d in out.get("docs", [])[:MAX_DOCS[track]]:
        if d["url"] in seen:
            continue
        seen.add(d["url"]); raw.append(d)
    with ThreadPoolExecutor(max_workers=8) as ex:
        checks = list(ex.map(source_check, raw))
    docs = [{"doc_id": f"{track[0]}{i:03d}", "url": d["url"], "title": d["title"], "publisher": d.get("publisher"),
             "published_at": d.get("published_at"), "cid_hint": d.get("cid_hint"), "excerpts": d["excerpts"],
             "snippet": "", "text": "\n".join(d["excerpts"]), **chk}
            for i, (d, chk) in enumerate(zip(raw, checks), 1)]
    return docs, how, {"searches": out.get("searches", []), "gaps": out.get("gaps", [])}


def agent_view(docs: list[dict]) -> list[dict]:
    """추출 agent 입력 — 원문 대조 결과는 넘기지 않는다(추출은 문서 내용만 보고 한다)."""
    return [{"doc_id": d["doc_id"], "url": d["url"], "title": d.get("title", ""), "published_at": d.get("published_at"),
             "text": (" ".join(filter(None, [d.get("snippet"), d.get("text")])))[:TEXT_LIMIT]} for d in docs]
