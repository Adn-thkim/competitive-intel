#!/usr/bin/env python3
"""
scripts/community_comment_playwright_poc.py
-------------------------------------------
커뮤니티 게시글 URL에서 **댓글(comment)** 을 Playwright(JS 렌더)로 수집할 수 있는지 측정한다.
(기존 community_playwright_poc.py 는 '본문' 회복 측정. 본 스크립트는 '댓글' 추출 PoC.)

대상 선정(우선순위) — 이번 분석 실행 캐시 기준:
  fetch 성공(fetch_status="ok") **그리고** 관련성 태깅 성공(relevant) 한 커뮤니티 URL.
  → scripts 없이도 재현되도록 `data/debug/comment_poc_targets.txt`(사전 산출)를 읽는다.
     파일이 없으면 --rebuild 로 dump+캐시에서 다시 만든다.

방법(각 URL):
  1) Playwright(headless 기본)로 페이지 렌더 → page.content().
  2) 사이트별 댓글 CSS 셀렉터로 댓글 텍스트 노드 추출(+ 제네릭 폴백).
  3) 노이즈 필터(닉네임·추천수·타임스탬프·길이) 후 유효 댓글 수 집계.
  분류: ok(≥1 댓글) · no_comment(0) · nav_error(렌더 실패).

설치(사용자 머신):
  pip install playwright
  python -m playwright install chromium
실행:
  python scripts/community_comment_playwright_poc.py --limit 40
  python scripts/community_comment_playwright_poc.py --limit 40 --headed   # 봇차단 회피 시도
"""
from __future__ import annotations

import argparse
import json
import re
import time
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
TARGETS_FILE = ROOT / "data/debug/comment_poc_targets.txt"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# 사이트별 댓글 텍스트 셀렉터(PoC 휴리스틱 — 운영 시 사이트별 파서 경화 필요).
SITE_SELECTORS = {
    "clien.net":   [".comment_view", ".comment_content", "div.comment_row .comment_view"],
    "dcinside.com": ["p.usertxt", ".usertxt", ".cmt_txtbox", ".comment_box .usertxt"],
    "ppomppu.co.kr": ["td.han", ".comment_memo", ".cmt_contents", ".reply_contents"],
    "theqoo.net":  ["li.fdb_itm .comment_content", ".fdb_lst_ul .xe_content", ".comment_content"],
}
GENERIC_SELECTORS = ["[class*=comment] [class*=content]", "[class*=cmt] [class*=txt]"]

MIN_CHARS = 5
_NOISE_RE = re.compile(
    r"^(추천|비추|신고|답글|대댓글|삭제|수정|\d+\s*분\s*전|\d{4}[-./]\d{1,2}[-./]\d{1,2}"
    r"|\d{1,2}:\d{2}|[\d,]+)$"
)


def _site(url: str) -> str:
    host = urlparse(url).netloc.lower().lstrip("m.").replace("www.", "")
    for key in SITE_SELECTORS:
        if key in host:
            return key
    return host


def _desktop(u: str) -> str:
    """ppomppu 모바일/리다이렉트 URL → 정적 데스크톱 view (댓글 DOM 노출 ↑)."""
    if "ppomppu.co.kr" in u and "bbs_view.php" in u:
        m = re.search(r"[?&]id=([^&]+).*?[?&]no=(\d+)", u)
        if m:
            return f"https://www.ppomppu.co.kr/zboard/view.php?id={m.group(1)}&no={m.group(2)}"
    return u


def _clean(texts: list[str]) -> list[str]:
    out, seen = [], set()
    for t in texts:
        t = re.sub(r"\s+", " ", (t or "")).strip()
        if len(t) < MIN_CHARS or _NOISE_RE.match(t) or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def _rebuild_targets() -> list[str]:
    """dump + 캐시에서 (fetch ok + relevant) 커뮤니티 URL 재산출."""
    import sys
    sys.path.insert(0, str(ROOT))
    import server.graph.relevance_tagger as rt
    from server.graph.agent_cache import make_cache_key, make_cache_context
    from server.graph.nodes import reaction_analysis_node as R

    d = json.loads((ROOT / "data/debug/reaction_state.json").read_text(encoding="utf-8"))
    aspects = R._aspect_ids(dict(d))
    valid = {a["aspect_id"] for a in aspects}
    tctx = make_cache_context(agent_id="relevance_tagger", model="claude-haiku-4-5-20251001",
                              system_prompt=rt._sys_prompt(aspects), output_schema=rt._LABEL_SCHEMA)
    tl = {}
    for p in (ROOT / "data/cache/agent_outputs/relevance_tagger").glob("*.json"):
        try:
            tl[p.stem] = (json.loads(p.read_text()).get("output") or {}).get("label")
        except Exception:
            pass
    rec = defaultdict(lambda: {"ok": False, "rel": False})
    for p in d.get("community_posts") or []:
        url = p.get("url", "")
        if not url:
            continue
        if p.get("fetch_status") == "ok":
            rec[url]["ok"] = True
        body = (p.get("body_excerpt") or "").strip()
        if not body:
            continue
        text = f"{p.get('title', '')}\n{body}".strip()
        lab = tl.get(make_cache_key("relevance_tagger", {"text": text[:300]}, tctx))
        if lab in valid:
            rec[url]["rel"] = True
    return [u for u, r in rec.items() if r["ok"] and r["rel"]]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--rebuild", action="store_true", help="대상 URL 파일을 dump+캐시에서 재생성")
    ap.add_argument("--urls-file", type=Path, default=TARGETS_FILE)
    ap.add_argument("--out", type=Path, default=ROOT / "data/debug/community_comment_poc.json")
    args = ap.parse_args()

    if args.rebuild or not args.urls_file.exists():
        urls = _rebuild_targets()
        TARGETS_FILE.write_text("\n".join(urls), encoding="utf-8")
        print(f"대상 재생성: {len(urls)} → {TARGETS_FILE}")
    else:
        urls = [ln.strip() for ln in args.urls_file.read_text(encoding="utf-8").splitlines()
                if ln.strip()]
    urls = urls[:args.limit]
    print(f"대상 URL: {len(urls)}개 · headed={args.headed} (fetch ok + relevant)\n")

    from playwright.sync_api import sync_playwright
    rows, tally = [], Counter()
    site_ok, site_tot = Counter(), Counter()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed)
        ctx = browser.new_context(user_agent=UA, locale="ko-KR",
                                  viewport={"width": 1366, "height": 900})
        for i, u in enumerate(urls, 1):
            site = _site(u)
            site_tot[site] += 1
            target = _desktop(u)
            try:
                page = ctx.new_page()
                page.goto(target, timeout=25000, wait_until="networkidle")
                texts = []
                for sel in SITE_SELECTORS.get(site, []) + GENERIC_SELECTORS:
                    if texts:
                        break
                    try:
                        texts = [el.inner_text() for el in page.query_selector_all(sel)]
                    except Exception:
                        texts = []
                page.close()
            except Exception as exc:  # noqa: BLE001
                tally["nav_error"] += 1
                rows.append({"url": u, "site": site, "cat": "nav_error",
                             "detail": type(exc).__name__})
                time.sleep(args.delay)
                continue
            comments = _clean(texts)
            cat = "ok" if comments else "no_comment"
            tally[cat] += 1
            if cat == "ok":
                site_ok[site] += 1
            rows.append({"url": u, "site": site, "cat": cat, "n_comment": len(comments),
                         "sample": comments[:3]})
            if i % 10 == 0 or i == len(urls):
                print(f"  [{i}/{len(urls)}] {dict(tally)}")
            time.sleep(args.delay)
        browser.close()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    n = len(urls) or 1
    print("\n=== 결과 ===")
    for k in ("ok", "no_comment", "nav_error"):
        print(f"  {k:11} {tally[k]:>4} ({tally[k] / n * 100:.1f}%)")
    print("\n사이트별 댓글 추출 성공률:")
    for s in sorted(site_tot):
        print(f"  {s:16} {site_ok[s]:>3}/{site_tot[s]:<3} ({site_ok[s] / site_tot[s] * 100:.0f}%)")
    tot_c = sum(r.get("n_comment", 0) for r in rows)
    print(f"\n추출 댓글 총합: {tot_c} · 상세 → {args.out}")


if __name__ == "__main__":
    main()
