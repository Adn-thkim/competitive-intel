#!/usr/bin/env python3
"""
scripts/community_failure_audit.py
----------------------------------
미수집 커뮤니티 URL을 실제 fetch 해 실패 유형을 분류·집계한다.

분류:
  ok_recoverable : 200 + 본문 추출 성공  → 실패가 아니라 '캡으로 미선정됐던 회수 가능분'
  404_deleted    : HTTP 404            → 삭제(회수 불가)
  bot_blocked    : HTTP 403/401/429 또는 봇 차단 페이지(캡차·Cloudflare) → 우회 전략 대상
  extract_failed : 200인데 본문 추출 실패(동적 렌더링·삭제안내·비정형) → Playwright 등 대상
  http_error     : 기타 4xx/5xx
  network        : 연결·타임아웃 등 예외

대상 URL = (url_discovery_brave 캐시의 커뮤니티 도메인 고유 URL) − (community_posts 수집분).

실행(사용자 머신 — 네트워크 필요):
  pip install trafilatura requests
  python scripts/community_failure_audit.py            # 전량
  python scripts/community_failure_audit.py --limit 50 # 표본 테스트
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import re
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
COMMUNITY_DOMAINS = ("clien.net", "ppomppu", "theqoo", "dcinside", "fmkorea", "ruliweb",
                     "bobaedream", "82cook", "mlbpark", "inven", "instiz", "natepann",
                     "milemoa", "cafe.naver")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
BOT_MARKERS = ("cloudflare", "just a moment", "captcha", "캡차", "verifying you are human",
               "access denied", "잠시 후 다시", "비정상적인 접근", "automated")


def _norm(u: str) -> str:
    u = u.strip().lower()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^m\.", "", u)
    return u.rstrip("/")


def _targets() -> list[str]:
    """발견됐으나 미수집인 커뮤니티 URL 목록(원형 URL 유지)."""
    collected = set()
    state = ROOT / "data/debug/reaction_state.json"
    if state.exists():
        d = json.loads(state.read_text(encoding="utf-8"))
        collected = {_norm(p.get("url", "")) for p in d.get("community_posts") or []}
    seen, out = set(), []
    for f in glob.glob(str(ROOT / "data/cache/agent_outputs/url_discovery_brave/*.json")):
        try:
            res = (json.load(open(f)).get("output") or {}).get("results") or []
        except Exception:
            continue
        for r in res:
            u = r.get("url", "")
            if not u or not any(c in u for c in COMMUNITY_DOMAINS):
                continue
            n = _norm(u)
            if n in collected or n in seen:
                continue
            seen.add(n)
            out.append(u)
    return out


def classify(url: str) -> tuple[str, str]:
    try:
        resp = requests.get(url, timeout=15, headers={"User-Agent": UA}, allow_redirects=True)
    except Exception as exc:  # noqa: BLE001
        return "network", type(exc).__name__
    sc = resp.status_code
    if sc == 404:
        return "404_deleted", ""
    if sc in (401, 403, 429):
        return "bot_blocked", f"http_{sc}"
    if sc >= 400:
        return "http_error", f"http_{sc}"
    low = resp.text[:5000].lower()
    if any(m in low for m in BOT_MARKERS):
        return "bot_blocked", "challenge_page"
    try:
        import trafilatura
        doc = trafilatura.bare_extraction(resp.text, include_comments=True,
                                          url=url, favor_precision=True)
    except Exception as exc:  # noqa: BLE001
        return "extract_failed", type(exc).__name__
    if not doc:
        return "extract_failed", "none"
    text = getattr(doc, "text", "") or (doc.get("text", "") if isinstance(doc, dict) else "")
    if len(text.strip()) < 50:
        return "extract_failed", "empty"
    comments = getattr(doc, "comments", "") or (doc.get("comments", "") if isinstance(doc, dict) else "")
    return "ok_recoverable", f"body={len(text)},comments={len(comments or '')}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="앞에서 N개만 테스트(0=전량)")
    ap.add_argument("--delay", type=float, default=1.0, help="요청 간 대기(초)")
    ap.add_argument("--out", type=Path, default=ROOT / "data/debug/community_failure_audit.json")
    args = ap.parse_args()

    urls = _targets()
    if args.limit:
        urls = urls[:args.limit]
    print(f"대상 미수집 URL: {len(urls)}개\n")

    rows, tally = [], collections.Counter()
    last_host: dict[str, float] = {}
    for i, u in enumerate(urls, 1):
        host = _norm(u).split("/")[0]
        wait = args.delay - (time.time() - last_host.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        cat, detail = classify(u)
        last_host[host] = time.time()
        tally[cat] += 1
        rows.append({"url": u, "category": cat, "detail": detail})
        if i % 25 == 0 or i == len(urls):
            print(f"  [{i}/{len(urls)}] {dict(tally)}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    n = len(urls) or 1
    print("\n=== 분류 비율 ===")
    for cat, c in tally.most_common():
        print(f"  {cat:16} {c:>5} ({c/n*100:.1f}%)")
    print(f"\n상세 → {args.out}")
    print("해석: ok_recoverable=캡으로 빠졌던 회수가능분 · 404_deleted=회수불가 · "
          "bot_blocked/extract_failed=수집전략 대상")


if __name__ == "__main__":
    main()
