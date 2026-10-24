#!/usr/bin/env python3
"""community_extract_fix.py — 커뮤니티 본문 추출 수리 복제본 (item 1).

원본: server/graph/nodes/community_collection_node.py 의 `_fetch_community_content`
      / `_fetch_target_v14`.
목적: dcinside(모바일)·theqoo 에서 trafilatura 제네릭 추출이 실제 게시글 본문 대신
      목록·공지·갤러리소개 등 보일러플레이트를 뽑는 문제를 고친다. 본 스크립트로
      before/after 를 검증한 뒤, 확인된 변경만 원본 노드에 반영한다(사용자 지시).

수리 2가지
----------
① UA 경로 버그: 원본 `_fetch_target_v14` 의 dcinside 정규식은 `gall.dcinside.com`
   만 매칭한다. 이미 `m.dcinside.com/board/{gall}/{no}` 로 수집된 URL 은 변환을
   건너뛰어 **모바일 페이지에 데스크톱 UA** 로 요청된다(m.dcinside 22% vs
   gall.dcinside 4% 추출실패의 원인 추정). → m.dcinside 도 모바일 UA 로 요청.
② 사이트별 셀렉터 추출: dcinside/theqoo 는 게시글 본문 컨테이너를 CSS 셀렉터로
   직접 추출하고, 실패 시 trafilatura 로 폴백한다.

검증(하니스)
------------
    pip install requests trafilatura beautifulsoup4
    python scripts/community_extract_fix.py            # 내장 미탐 URL 로 before/after
    python scripts/community_extract_fix.py --url <URL>
    python scripts/community_extract_fix.py --inspect <URL>   # 셀렉터 후보 진단

반영(원본 포팅)
---------------
검증 후 community_collection_node.py 에 다음을 반영:
  1) `_fetch_target_v14`: `m.dcinside.com/board/...` 도 모바일 UA 로 (아래 FIXED 참고).
  2) `_fetch_community_content` 의 trafilatura 호출 앞에 `extract_site_specific()` 우선
     시도 → 결과 있으면 채택, 없으면 기존 trafilatura 폴백.
"""
from __future__ import annotations
import argparse, re, sys
from urllib.parse import urlparse

import requests

_TIMEOUT = 15
_BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
              "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")

_DC_VIEW_RE = re.compile(
    r"gall\.dcinside\.com/(?:mgallery/|mini/)?board/view/?\?id=([^&]+)&no=(\d+)")
_DC_PATH_RE = re.compile(r"gall\.dcinside\.com/board/([^/?]+)/(\d+)")
# FIXED — 이미 모바일 URL 로 수집된 dcinside 게시글도 인식.
_DC_MOBILE_RE = re.compile(r"m\.dcinside\.com/board/([^/?]+)/(\d+)")


def fetch_target_fixed(url: str) -> tuple[str, str]:
    """수리판: gall/m 모두 모바일 URL + 모바일 UA 로 정규화."""
    m = _DC_VIEW_RE.search(url) or _DC_PATH_RE.search(url) or _DC_MOBILE_RE.search(url)
    if m:
        return f"https://m.dcinside.com/board/{m.group(1)}/{m.group(2)}", _MOBILE_UA
    if "ppomppu.co.kr" in url:   # www/m 모두 모바일 URL 로 정규화(.viAr 일관)
        mid, mno = re.search(r"id=([^&]+)", url), re.search(r"no=(\d+)", url)
        if mid and mno:
            return (f"https://m.ppomppu.co.kr/new/bbs_view.php?id={mid.group(1)}&no={mno.group(1)}",
                    _BROWSER_UA)
    return url, _BROWSER_UA


# ─── 사이트별 본문 셀렉터 (우선순위 목록; 첫 히트 채택) ───────────────────────
_SELECTORS = {
    "dcinside.com": {
        "body": [".thum-txtin", ".writing_view_box", ".write_div", ".gallview_contents",
                 "div[itemprop='articleBody']"],
        "comments": [".all-comment-lst .txt", ".cmt_txtbox", ".comment_box .usertxt"],
    },
    "theqoo.net": {
        "body": [".rd_body .xe_content", "article .rhymix_content", ".rd_body", ".xe_content"],
        "comments": [".fdb_lst_ul .xe_content", ".comment_content .xe_content", ".fdb_itemwrap"],
    },
    "ppomppu.co.kr": {
        "body": [".viAr"],       # 게시글 본문 컨테이너(실측 --find)
        "comments": [],          # 댓글은 community_comments(Playwright) 정본
    },
}
# 보일러플레이트로 제거할 컨테이너 (폴백 최대텍스트 추출 시)
_STRIP = ["script", "style", "nav", "header", "footer", "aside",
          ".gnb", ".lnb", ".head", ".foot", ".ad", ".banner", ".notice",
          ".gall_list", ".comment", ".sns", ".paging"]


def _site_key(url: str) -> str:
    host = urlparse(url).netloc.replace("m.", "").replace("www.", "")
    for k in _SELECTORS:
        if host.endswith(k):
            return k
    return ""


def extract_site_specific(html: str, url: str) -> tuple[str, str] | None:
    """사이트 셀렉터로 (본문, 댓글) 추출. 미지원/미검출 시 None → trafilatura 폴백.
    원본 노드에 그대로 이식 가능한 순수 함수.
    """
    key = _site_key(url)
    if not key:
        return None
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        print("  [경고] beautifulsoup4 미설치 → 셀렉터 추출 생략", file=sys.stderr)
        return None
    soup = BeautifulSoup(html, "html.parser")

    # 메타데이터만 있는 텍스트(날짜·IP·조회·추천·고정닉 등) 판별 — 본문 아님.
    _META = re.compile(r"^[\d\s.\-:()추천수조회고정닉ㆍ·|]+$")

    def first_text(selectors: list[str], join_all: bool = False) -> str:
        for sel in selectors:
            nodes = soup.select(sel)
            if not nodes:
                continue          # 셀렉터 미매칭 → 다음 후보
            if join_all:
                txt = "\n".join(n.get_text(" ", strip=True) for n in nodes)
            else:
                txt = nodes[0].get_text("\n", strip=True)
            txt = txt.strip()
            # 컨테이너는 잡혔으니 길이 하한 없이 채택. 단, 메타데이터만이면 다음 후보.
            if txt and not _META.match(txt):
                return txt
        return ""

    body = first_text(_SELECTORS[key]["body"])
    comments = first_text(_SELECTORS[key]["comments"], join_all=True)
    if not body:
        return None    # 본문 컨테이너 자체를 못 찾음 → trafilatura 폴백
    return body, comments


def extract_trafilatura(html: str, target: str) -> tuple[str, str]:
    """원본과 동일한 제네릭 추출 (비교 기준)."""
    import trafilatura
    try:
        doc = trafilatura.bare_extraction(html, include_comments=True, url=target,
                                          favor_precision=True)
    except Exception:  # noqa: BLE001
        return "", ""
    if doc is None:
        return "", ""
    body = getattr(doc, "text", "") or (doc.get("text", "") if isinstance(doc, dict) else "")
    cmts = getattr(doc, "comments", "") or (doc.get("comments", "") if isinstance(doc, dict) else "")
    return body or "", cmts or ""


def fetch_and_extract(url: str, use_fix: bool = True) -> dict:
    """수리판 fetch+추출. use_fix=False 면 원본(trafilatura only) 재현."""
    if use_fix:
        target, ua = fetch_target_fixed(url)
    else:
        # 원본 재현: gall 만 모바일 변환, 그 외 브라우저 UA
        m = _DC_VIEW_RE.search(url) or _DC_PATH_RE.search(url)
        target, ua = ((f"https://m.dcinside.com/board/{m.group(1)}/{m.group(2)}", _MOBILE_UA)
                      if m else (url, _BROWSER_UA))
    try:
        r = requests.get(target, timeout=_TIMEOUT, headers={"User-Agent": ua})
    except Exception as exc:  # noqa: BLE001
        return {"status": f"fetch_failed:{type(exc).__name__}", "body": "", "comments": ""}
    if r.status_code >= 400:
        return {"status": f"http_{r.status_code}", "body": "", "comments": ""}

    if use_fix:
        ss = extract_site_specific(r.text, url)
        if ss is not None:
            return {"status": "ok(selector)", "body": ss[0], "comments": ss[1], "target": target}
    body, cmts = extract_trafilatura(r.text, target)
    return {"status": "ok(trafilatura)" if body else "empty",
            "body": body, "comments": cmts, "target": target}


# ─── 검증 하니스 ──────────────────────────────────────────────────────────────
# 앞선 분석에서 확인된 '본문 추출 실패' 미탐 URL (제목엔 카드 신호 있음).
_MISS_URLS = [
    "https://m.dcinside.com/board/nokanto/643563",
    "https://m.dcinside.com/board/nokanto/482957",
    "https://m.dcinside.com/board/nokanto/180878",
    "https://m.dcinside.com/board/checkcard/24399",
    "https://m.dcinside.com/board/creditcard/288019",
    "https://theqoo.net/japan/2788962309",
    "https://theqoo.net/travel/3082074180",
]


def _preview(s: str, n: int = 140) -> str:
    return (s or "").replace("\n", " ")[:n]


# 크롤 노이즈(목록·공지·갤러리소개·이벤트) 마커 — 추출 품질 판정용.
_NOISE_RE = re.compile(
    r"(마이너 갤러리|최근방문|개념글 리스트|이벤트 공지|갤러리 소개|갤러리 이슈박스|"
    r"체험단|대문 :|로그인 보안|필독|공지\s*\||설문\s*\||\|\s*\d{2,}\s*\||운영자)")


def _noise_hits(s: str) -> int:
    return len(_NOISE_RE.findall(s or ""))


def _css_of(node) -> str:
    cls = node.get("class")
    return (node.name + "." + ".".join(cls)) if cls else node.name


def inspect(url: str, find: str | None = None):
    """셀렉터 후보별 텍스트 길이를 덤프 — 셀렉터 조정용 진단.
    find 주어지면 그 텍스트를 담은 **가장 작은(구체적인) 요소**와 조상 체인을 출력 →
    짧은 본문 컨테이너를 정확히 역추적한다."""
    from bs4 import BeautifulSoup
    target, ua = fetch_target_fixed(url)
    r = requests.get(target, timeout=_TIMEOUT, headers={"User-Agent": ua})
    soup = BeautifulSoup(r.text, "html.parser")
    key = _site_key(url)
    print(f"target={target}  http={r.status_code}  site_key={key or '(미지원)'}")

    if find:
        # find 텍스트를 포함하는 요소 중 텍스트가 가장 짧은(=본문에 가장 근접) 것
        cands = [n for n in soup.find_all(True) if find in n.get_text()]
        if not cands:
            print(f"'{find}' 를 포함한 요소 없음 — 다른 본문 문구로 재시도하세요.")
            return
        best = min(cands, key=lambda n: len(n.get_text(strip=True)))
        print(f"'{find}' 포함 최소 요소: <{_css_of(best)}> (텍스트 {len(best.get_text(strip=True))}자)")
        print(f"  텍스트: {best.get_text(' ', strip=True)[:120]!r}")
        print("  조상 체인(가까운 순):")
        p = best.parent
        for _ in range(6):
            if p is None or p.name in ("body", "html"):
                break
            print(f"    <{_css_of(p)}> (텍스트 {len(p.get_text(strip=True))}자)")
            p = p.parent
        return
    cand = _SELECTORS.get(key, {}).get("body", []) if key else []
    print("본문 셀렉터 후보:")
    for sel in cand:
        nodes = soup.select(sel)
        tl = max((len(n.get_text(strip=True)) for n in nodes), default=0)
        print(f"  {sel:30} 노드 {len(nodes)}개 · 최대텍스트 {tl}자")
    # 텍스트 상위 블록(폴백 진단)
    print("텍스트 최다 div (상위 5):")
    blocks = [(len(d.get_text(strip=True)),
               (d.get('class') and '.'.join(d.get('class')) or d.name))
              for d in soup.find_all(["div", "article", "section"])]
    for tl, cls in sorted(blocks, reverse=True)[:5]:
        print(f"  {tl:6}자  {cls[:50]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", help="단일 URL before/after")
    ap.add_argument("--inspect", help="셀렉터 후보 진단")
    ap.add_argument("--find", help="본문 문구로 컨테이너 역추적(--inspect 와 함께)")
    args = ap.parse_args()

    if args.inspect:
        inspect(args.inspect, args.find); return 0

    urls = [args.url] if args.url else _MISS_URLS
    print("=== before(원본 trafilatura) vs after(수리판) 본문 추출 비교 ===\n")
    win = 0
    for u in urls:
        old = fetch_and_extract(u, use_fix=False)
        new = fetch_and_extract(u, use_fix=True)
        ob, nb = old["body"], new["body"]
        # 품질 지표: 길이가 아니라 '노이즈 감소'로 판정(짧고 깨끗한 본문이 정답 — "뭐가 남" 등).
        n_ob, n_nb = _noise_hits(ob), _noise_hits(nb)
        better = (n_nb < n_ob) and bool(nb.strip())
        win += better
        print(f"URL {u}")
        print(f"  before[{old['status']:16}] {len(ob):5}자 노이즈{n_ob:2} | {_preview(ob)}")
        print(f"  after [{new['status']:16}] {len(nb):5}자 노이즈{n_nb:2} | {_preview(nb)}")
        print(f"  → {'✅ 개선(노이즈 제거)' if better else '· 변화없음/폴백'}\n")
    print(f"개선 {win}/{len(urls)}건")
    return 0


if __name__ == "__main__":
    sys.exit(main())
