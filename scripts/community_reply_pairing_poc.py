#!/usr/bin/env python3
"""
scripts/community_reply_pairing_poc.py
--------------------------------------
커뮤니티 **댓글+대댓글(reply) 구조** 를 Playwright 로 추출해 youtube comments 와 **동일 스키마**
({thread_id, is_reply, parent_id, text}) 로 내보내고, 그대로 youtube 노이즈필터·관련성 파이프라인에
넣을 수 있는지 PoC 한다.

동작:
  1) 도메인별(clien/dcinside/ppomppu/theqoo) fetch-ok URL 을 순회하며 렌더.
  2) 댓글 아이템을 깊이(depth)와 함께 추출 → depth>0 은 대댓글(is_reply=True), 부모=직전의
     더 얕은 댓글(youtube 의 thread_id=최상위 댓글 모델과 동일하게 매핑).
  3) **대댓글이 1개 이상 잡히는 첫 URL** 을 도메인 대표로 선정.
  4) youtube 스키마 item 으로 변환 후 `_filter_basic`(+선택 `_prefilter_v2`) 를 인라인 적용해
     동일 파이프라인 통과를 확인.

⚠ 사이트별 DOM 은 변하므로 `DOM_HINTS` 의 셀렉터/대댓글 마커는 PoC 휴리스틱이다(실측 후 튜닝).

설치/실행:
  pip install playwright && python -m playwright install chromium
  python scripts/community_reply_pairing_poc.py --headed
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# 도메인별: 댓글 '아이템' 컨테이너 셀렉터 + 텍스트 셀렉터 + 대댓글 판정(클래스 키워드).
# depth 는 (a) 대댓글 마커 클래스 또는 (b) 화면상 좌측 들여쓰기(x offset)로 추론.
DOM_HINTS = {
    "clien.net":   {"item": "div.comment_row", "text": ".comment_view, .comment_content",
                    "reply_cls": ("re", "reply"), "id_attr": "data-comment-sn"},
    "dcinside.com": {"item": "li.ub-content, .comment_box li", "text": ".usertxt, p.usertxt",
                     "reply_cls": ("reply", "comment-add"), "id_attr": "data-no"},
    "ppomppu.co.kr": {"item": "tr.reply, table.info_bg tr", "text": "td.han, .comment_memo",
                      "reply_cls": ("re", "reply"), "id_attr": None},
    "theqoo.net":  {"item": "li.fdb_itm", "text": ".comment_content, .xe_content",
                    "reply_cls": ("re", "child"), "id_attr": "data-srl"},
}


def _site(url: str) -> str:
    host = urlparse(url).netloc.lower().replace("www.", "").replace("m.", "")
    for k in DOM_HINTS:
        if k in host:
            return k
    return host


def _desktop(u: str) -> str:
    if "ppomppu.co.kr" in u and "bbs_view.php" in u:
        m = re.search(r"[?&]id=([^&]+).*?[?&]no=(\d+)", u)
        if m:
            return f"https://www.ppomppu.co.kr/zboard/view.php?id={m.group(1)}&no={m.group(2)}"
    return u


def _ok_urls_by_domain() -> dict[str, list[str]]:
    """dump 의 fetch-ok 커뮤니티 URL 을 도메인별로 모은다."""
    d = json.loads((ROOT / "data/debug/reaction_state.json").read_text(encoding="utf-8"))
    seen, by = set(), defaultdict(list)
    for p in d.get("community_posts") or []:
        u = p.get("url", "")
        if u and p.get("fetch_status") == "ok" and u not in seen:
            seen.add(u)
            by[_site(u)].append(u)
    return by


def _extract_threads(page, site: str) -> list[dict]:
    """댓글+대댓글을 youtube 스키마로 추출. depth 추론 → thread_id/parent_id 매핑."""
    h = DOM_HINTS[site]
    js = """(args) => {
      const [itemSel, textSel, replyCls] = args;
      const out = [];
      document.querySelectorAll(itemSel).forEach((el, i) => {
        const tnode = el.querySelector(textSel) || el;
        const text = (tnode.innerText || '').trim();
        if (!text) return;
        const cls = (el.className || '') + ' ' + (el.getAttribute('class') || '');
        const byClass = replyCls.some(c => cls.toLowerCase().includes(c));
        const rect = el.getBoundingClientRect();
        out.push({text, indent: Math.round(rect.left), byClass});
      });
      return out;
    }"""
    rows = page.evaluate(js, [h["item"], h["text"], list(h["reply_cls"])])
    # depth: 대댓글 마커 클래스 OR 들여쓰기(최소 indent 대비 +12px↑)
    if not rows:
        return []
    base = min(r["indent"] for r in rows)
    items, last_top = [], None
    for i, r in enumerate(rows):
        is_reply = bool(r["byClass"]) or (r["indent"] - base) >= 12
        cid = f"{site}#c{i}"
        if not is_reply:
            last_top = cid
        items.append({
            "channel": "community",
            "thread_id": last_top or cid,         # youtube: 최상위 댓글 = thread root
            "is_reply": is_reply and last_top is not None,
            "parent_id": last_top if (is_reply and last_top) else None,
            "text": r["text"], "posted_at": "",
        })
    return items


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-domain-scan", type=int, default=8,
                    help="도메인당 대댓글 보유 URL 을 찾기 위해 시도할 최대 URL 수")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--run-filter", action="store_true", default=True,
                    help="추출 후 _filter_basic/_prefilter_v2 인라인 적용")
    ap.add_argument("--out", type=Path, default=ROOT / "data/debug/community_reply_pairing_poc.json")
    args = ap.parse_args()

    by_domain = _ok_urls_by_domain()
    print("도메인별 fetch-ok URL:", {k: len(v) for k, v in by_domain.items()}, "\n")

    from playwright.sync_api import sync_playwright
    sys.path.insert(0, str(ROOT))
    from server.graph.nodes.youtube_reaction_collection_node import _filter_basic, _prefilter_v2

    selected = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed)
        ctx = browser.new_context(user_agent=UA, locale="ko-KR",
                                  viewport={"width": 1366, "height": 900})
        for site, urls in by_domain.items():
            if site not in DOM_HINTS:
                continue
            for u in urls[:args.per_domain_scan]:
                try:
                    page = ctx.new_page()
                    page.goto(_desktop(u), timeout=25000, wait_until="domcontentloaded")
                    page.wait_for_timeout(1500)
                    items = _extract_threads(page, site)
                    page.close()
                except Exception as exc:  # noqa: BLE001
                    print(f"  [{site}] nav_error {type(exc).__name__}: {u}")
                    continue
                n_reply = sum(1 for it in items if it["is_reply"])
                if n_reply >= 1:
                    selected[site] = {"url": u, "items": items, "n_reply": n_reply}
                    print(f"  [{site}] 선정: {u}  (댓글 {len(items)} · 대댓글 {n_reply})")
                    break
            else:
                print(f"  [{site}] 대댓글 보유 URL 미발견(스캔 {args.per_domain_scan})")
        browser.close()

    # 인라인 파이프라인 검증
    print("\n=== youtube 파이프라인 인라인 검증 ===")
    for site, sel in selected.items():
        raw = sel["items"]
        b = _filter_basic(raw)
        v2 = _prefilter_v2(raw, None)
        rep_kept = sum(1 for c in v2 if c.get("is_reply"))
        print(f"  {site:14} 추출 {len(raw):3} → _filter_basic {len(b):3} → v2 {len(v2):3} "
              f"(대댓글 {rep_kept})")

    args.out.write_text(json.dumps(selected, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n도메인 대표 {len(selected)}개 → {args.out}")


if __name__ == "__main__":
    main()
