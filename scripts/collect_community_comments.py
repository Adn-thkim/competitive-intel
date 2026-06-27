#!/usr/bin/env python3
"""
scripts/collect_community_comments.py
-------------------------------------
커뮤니티 fetch-ok URL 의 **댓글+대댓글** 을 Playwright 로 수집해 youtube 스키마로
agent_cache(`agent_id="community_comments"`, key={url})에 적재하는 **배치 수집기**.

설계(저위협 디커플드):
  - 분석 파이프라인(서버)에는 브라우저를 넣지 않는다. 이 스크립트가 **사전 배치**로 캐시를
    채우고, community_collection_node 는 **캐시 읽기만** 한다.
  - 재실행 시 URL 캐시 적중분은 건너뛴다(`--refresh` 로 강제 재수집).
  - 추출은 community_reply_pairing_poc 의 _extract_threads + DOM_HINTS 재사용.
  - 저장 전 youtube 노이즈 필터(_filter_basic) 적용 → 닉네임·추천수·짧은 잡담 제거.

설치/실행:
  pip install playwright && python -m playwright install chromium
  python scripts/collect_community_comments.py            # 전량(캐시 미적중분)
  python scripts/collect_community_comments.py --limit 50 --headed
  python scripts/collect_community_comments.py --refresh   # 캐시 무시하고 재수집
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.community_reply_pairing_poc import (  # noqa: E402
    DOM_HINTS, UA, _desktop, _extract_threads, _ok_urls_by_domain, _site,
)
from server.graph.agent_cache import load_agent_output, store_agent_output  # noqa: E402
from server.graph.nodes.youtube_reaction_collection_node import _filter_basic  # noqa: E402

_AGENT_ID = "community_comments"
_CTX = {"agent_id": _AGENT_ID, "v": 1}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="처리 URL 상한(0=전량)")
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--refresh", action="store_true", help="캐시 무시하고 재수집")
    ap.add_argument("--site", default="", help="특정 도메인만(예: dcinside.com·ppomppu.co.kr·theqoo.net)")
    args = ap.parse_args()

    by_domain = _ok_urls_by_domain()
    sites = [s for s in sorted(by_domain) if s in DOM_HINTS and (not args.site or s == args.site)]
    urls = [u for site in sites for u in by_domain[site]]
    if args.limit:
        urls = urls[:args.limit]
    print(f"대상 fetch-ok URL: {len(urls)} · 도메인 {sites} · headed={args.headed} "
          f"· refresh={args.refresh}\n")

    from playwright.sync_api import sync_playwright
    tally = Counter()
    site_raw = Counter()      # 추출 원본(수집)
    site_kept = Counter()     # 노이즈 필터 후 저장(kept)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed)
        ctx = browser.new_context(user_agent=UA, locale="ko-KR",
                                  viewport={"width": 1366, "height": 900})
        for i, u in enumerate(urls, 1):
            site = _site(u)
            if not args.refresh and load_agent_output(
                    agent_id=_AGENT_ID, cache_input={"url": u}, context=_CTX) is not None:
                tally["cache_hit"] += 1
                continue
            try:
                page = ctx.new_page()
                page.goto(_desktop(u), timeout=25000, wait_until="domcontentloaded")
                page.wait_for_timeout(1500)
                raw = _extract_threads(page, site)
                page.close()
            except Exception as exc:  # noqa: BLE001
                tally["nav_error"] += 1
                time.sleep(args.delay)
                continue
            # 노이즈 필터(닉네임·추천수·짧은 잡담 제거) — thread_id/is_reply 보존
            kept = _filter_basic(raw)
            store_agent_output(agent_id=_AGENT_ID, cache_input={"url": u}, context=_CTX,
                               output={"items": kept})
            tally["collected"] += 1
            tally["raw"] += len(raw)
            tally["kept"] += len(kept)
            site_raw[site] += len(raw)
            site_kept[site] += len(kept)
            if i % 20 == 0 or i == len(urls):
                rm = tally["raw"] - tally["kept"]
                print(f"  [{i}/{len(urls)}] URL {tally['collected']} · "
                      f"수집(raw) {tally['raw']} → 노이즈제거 {rm} → 저장(kept) {tally['kept']} "
                      f"· cache_hit {tally['cache_hit']} · nav_error {tally['nav_error']}")
            time.sleep(args.delay)
        browser.close()

    raw_t, kept_t = tally["raw"], tally["kept"]
    print("\n=== 결과 ===")
    print(f"  URL: collected {tally['collected']} · cache_hit {tally['cache_hit']} "
          f"· nav_error {tally['nav_error']}")
    print(f"  댓글: 수집(raw) {raw_t} → 노이즈제거 {raw_t - kept_t} → 저장(kept) {kept_t}")
    print("  사이트별 [수집 → 노이즈제거 → 저장]:")
    for s in sorted(site_raw):
        r, k = site_raw[s], site_kept[s]
        print(f"    {s:16} {r:5} → {r - k:5} → {k:5}")
    print(f"\n  ※ 'max_items 포함' 수는 분석 재실행 후 funnel '커뮤 kept[본문·댓글]' "
          f"및 리포트 표본 chip 에서 확인.")
    print(f"캐시 적재 완료 → agent_outputs/{_AGENT_ID}/ (community_collection_node 가 읽음)")


if __name__ == "__main__":
    main()
