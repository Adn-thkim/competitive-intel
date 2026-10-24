#!/usr/bin/env python3
"""probe_community_fetchability.py — 수집 URL의 fetch 가능비율 측정 (item 1-1).

배경
----
커뮤니티 게시글은 작성 후 짧은 기간에 삭제/잠김되는 경우가 잦다. 본 프로브는 수집
대상 URL이 지금 시점에 **실제로 fetch 가능한지**를 사이트별로 집계한다.
(라이브 네트워크가 필요하므로 사용자 머신에서 실행 — 이 스크립트는 서버 노드를
건드리지 않는 읽기 전용 진단이다.)

분류
----
- ok             : HTTP 200 + 본문 페이지 (정상)
- deleted        : HTTP 404, 또는 200이지만 '삭제된 게시물/없는 게시물' 안내 (삭제)
- blocked        : HTTP 401/403 (차단/로그인 요구)
- http_5xx / 기타 : 서버 오류·일시 장애
- timeout/error  : 네트워크 실패

입력
----
--from-state <reaction_state.json>  : community_posts 의 고유 URL (기본)
--urls <파일>                        : 줄단위 URL 목록
--limit N / --sites clien.net,...   : 표본 제한

실행
----
    pip install requests
    python scripts/probe_community_fetchability.py --from-state data/debug/reaction_state.json
    python scripts/probe_community_fetchability.py --urls my_urls.txt --limit 200
"""
from __future__ import annotations
import argparse, json, re, sys, time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import requests

_TIMEOUT = 15
_BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
              "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
# dcinside 는 데스크톱/모바일 URL 모두 모바일 UA 로 요청해야 정상 페이지를 받는다.
_DC_HOST = re.compile(r"(?:^|\.)dcinside\.com$")
_DELETED_SIGNS = ("삭제된 게시물", "없는 게시물", "존재하지 않는", "찾을 수 없",
                  "삭제되었", "이동되었거나 삭제")

_HOST_PARALLEL = 4


def _ua_for(url: str) -> str:
    host = urlparse(url).netloc
    return _MOBILE_UA if _DC_HOST.search(host) else _BROWSER_UA


def classify(url: str) -> str:
    try:
        r = requests.get(url, timeout=_TIMEOUT, headers={"User-Agent": _ua_for(url)})
    except requests.Timeout:
        return "timeout"
    except Exception as exc:  # noqa: BLE001
        return f"error:{type(exc).__name__}"
    if r.status_code == 404:
        return "deleted"
    if r.status_code in (401, 403):
        return "blocked"
    if r.status_code >= 500:
        return "http_5xx"
    if r.status_code >= 400:
        return f"http_{r.status_code}"
    if any(s in r.text[:4000] for s in _DELETED_SIGNS):
        return "deleted"
    return "ok"


def load_urls(args) -> list[str]:
    if args.urls:
        return [l.strip() for l in open(args.urls, encoding="utf-8") if l.strip()]
    st = json.load(open(args.from_state, encoding="utf-8"))
    seen, out = set(), []
    for p in st.get("community_posts") or []:
        u = p.get("url") or ""
        if u and u not in seen:
            seen.add(u); out.append(u)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-state", default="data/debug/reaction_state.json")
    ap.add_argument("--urls")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sites", help="쉼표구분 사이트 필터 (netloc 부분일치)")
    ap.add_argument("--out", default="data/debug/fetchability_probe.json")
    args = ap.parse_args()

    urls = load_urls(args)
    if args.sites:
        keys = [s.strip() for s in args.sites.split(",")]
        urls = [u for u in urls if any(k in urlparse(u).netloc for k in keys)]
    if args.limit:
        urls = urls[:args.limit]
    print(f"대상 URL {len(urls)}개 · fetch 진단 시작 (host별 rate-limit 1s)", flush=True)

    # host 단위 그룹 → host 간 병렬, host 내 직렬 + 1s
    by_host: dict[str, list[str]] = defaultdict(list)
    for u in urls:
        by_host[urlparse(u).netloc].append(u)

    results: dict[str, str] = {}

    def run_host(host_urls: list[str]) -> dict[str, str]:
        out = {}
        for u in host_urls:
            out[u] = classify(u)
            time.sleep(1.0)
        return out

    with ThreadPoolExecutor(max_workers=_HOST_PARALLEL) as pool:
        for part in pool.map(run_host, by_host.values()):
            results.update(part)

    # 집계
    site_stat: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for u, c in results.items():
        site = urlparse(u).netloc.replace("m.", "").replace("www.", "")
        site_stat[site][c] += 1
        site_stat[site]["_total"] += 1

    def fetchable(d):  # ok 만 fetch 가능으로 집계
        return d.get("ok", 0)

    print("\n=== 사이트별 fetch 가능비율 ===")
    print(f"{'사이트':22}{'total':>6}{'ok':>6}{'삭제':>6}{'차단':>6}{'기타':>6}  fetch가능%")
    g_tot = g_ok = g_del = 0
    for site in sorted(site_stat, key=lambda s: -site_stat[s]["_total"]):
        d = site_stat[site]; t = d["_total"]; ok = fetchable(d)
        dele = d.get("deleted", 0); blk = d.get("blocked", 0)
        etc = t - ok - dele - blk
        g_tot += t; g_ok += ok; g_del += dele
        print(f"{site:22}{t:6}{ok:6}{dele:6}{blk:6}{etc:6}  {100*ok/t:6.1f}%")
    print(f"\n전체 {g_ok}/{g_tot} fetch 가능 ({100*g_ok/g_tot:.1f}%) · 삭제 추정 {g_del} "
          f"({100*g_del/g_tot:.1f}%)")

    deleted = [u for u, c in results.items() if c == "deleted"]
    print(f"\n삭제 추정 URL {len(deleted)}건 (상위 15):")
    for u in deleted[:15]:
        print("  ", u)

    json.dump({"summary": {s: dict(v) for s, v in site_stat.items()},
               "results": results}, open(args.out, "w"), ensure_ascii=False, indent=1)
    print(f"\n저장: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
