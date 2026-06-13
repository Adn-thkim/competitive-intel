"""
scripts/measure_brave_recall.py — CE-D7 Brave 리콜 측정 (일회성, 운영 코드와 분리)
=================================================================================
설계: docs/design/community_collection_expansion_design.md §2 CE-D7 · §6-1

목적
----
broad query(`site:{community_domain} {candidate_alias}`)의 Brave 실제 리콜을 구현
착수 전에 실측한다. 측정 단계에서는 페이지네이션 무제한(offset 최대 9 = 쿼리당
최대 10페이지·200건)으로 수집 가능 총량을 확인하고, 운영 페이지 상한의 근거를 만든다.

산출물 (--out 디렉터리, 기본 data/measurement/brave_recall_{ts}/)
------------------------------------------------------------------
1. recall_table.md / recall_raw.csv
   - 메인 표(발견 기준): 사이트(community domain별 분리) × candidate 결과 수.
     다중 candidate 게시글은 발견된 모든 candidate 셀에 각각 집계 (중복 허용).
   - 보조 행(고유 게시글 기준): 사이트별 고유 URL 수 + 다중 발견 수
     (2개 이상 candidate 쿼리에서 발견된 URL — CE-D5 matched_candidates 대응).
   - 페이지별 결과 수·발행일(page_age) 분포.
2. length_dist.csv — fetch 표본의 본문 글자수 분포 (§3-4 chunk 상한 근거).
3. comment_dist.csv — fetch 표본의 댓글 분량 분포.
   ※ 일반 추출(Trafilatura) 한계로 댓글 "수"는 문단 수 기반 근사치다. 정밀 카운트는
     사이트별 전용 파서(CE-D6 2단계) 영역이므로 본 측정에서는 근사치로 판단한다.
4. false_exclusion_sample.csv — 스니펫 기반 필터 판정 vs 본문 기준 실제 관련성
   표본 20건. 스니펫 필터 제외분 전수 + 통과분 무작위로 채움.
   body_token_hits>0 인데 filter=excluded 면 "잠정 오제외"로 자동 표기,
   최종 판단용 manual_label 컬럼은 빈칸 (수동 기입).
   fetch 표본 한정으로 글당 본문 언급 candidate 수 분포(1/2/3+)도 summary 에 포함.

비용·예의
---------
- Brave 호출 최대: 사이트 6 × candidate 4 × 10페이지 = 240회 ≈ $1.20 (월 $5 무료
  크레딧 내). 결과가 20건 미만이면 다음 페이지를 호출하지 않으므로 실제는 훨씬 적다.
- 본문 fetch: robots.txt 준수(운영과 동일 정책) + host당 1초 대기.

사용
----
  python scripts/measure_brave_recall.py \
      --candidates "토스 트래블카드" "신한 SOL트래블" "트래블월렛" "하나 트래블로그" \
      --fetch-per-site 5
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import sys
import time
import urllib.robotparser
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, urlencode, parse_qsl, urlunparse

import requests

# ── 운영 코드 의존은 config 의 API 키 1개로 최소화 (일회성 스크립트 원칙) ────────
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    from server.config import BRAVE_SEARCH_API_KEY  # noqa: E402
except Exception:  # noqa: BLE001
    BRAVE_SEARCH_API_KEY = os.getenv("BRAVE_SEARCH_API_KEY", "")

BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# CE-D2 1군 고정 화이트리스트
DEFAULT_SITES = ["clien.net", "ppomppu.co.kr", "mlbpark.donga.com",
                 "fmkorea.com", "theqoo.net", "dcinside.com"]

# CE-D4 — 파일럿 aspect_codebook 유래 토큰 (완화 모드: 0개일 때만 제외)
FILTER_TOKENS = ["해외결제", "환율", "환전", "충전", "외화", "ATM", "출금", "수수료",
                 "한도", "분실", "도난", "잠금", "보험", "라운지", "고객센터", "고객 센터",
                 "트래블카드", "트래블"]

_TRACKING_PARAMS = re.compile(r"^(utm_|fbclid|gclid|ref$)")


# ─── 유틸 ────────────────────────────────────────────────────────────────────

def normalize_url(url: str) -> str:
    """CE-D8 — 스킴·www 제거, 모바일 변형 통일, 추적 파라미터 제거."""
    try:
        p = urlparse(url)
    except Exception:  # noqa: BLE001
        return url
    host = (p.hostname or "").lower().removeprefix("www.")
    # 주의(실측 2026-06-12): 모바일 호스트(m.*)를 PC 로 무차별 치환하지 않는다 —
    # ppomppu 는 m.ppomppu.co.kr/new/* 경로가 PC 도메인에 존재하지 않아 404 가 된다.
    # 사이트별 모바일↔PC 매핑은 운영 CE-D8 에서 경로 변환과 함께 처리한다.
    query = urlencode([(k, v) for k, v in parse_qsl(p.query)
                       if not _TRACKING_PARAMS.match(k)])
    return urlunparse(("", host, p.path.rstrip("/"), "", query, "")).lstrip("/")


def snippet_filter_pass(title: str, snippet: str) -> tuple[bool, list[str]]:
    """CE-D4 완화 모드 — 토큰 1개 이상이면 통과. (통과여부, 매칭토큰) 반환."""
    text = f"{title} {snippet}"
    hits = [t for t in FILTER_TOKENS if t in text]
    return bool(hits), hits


def brave_search_all_pages(query: str, max_pages: int, sleep_s: float = 1.0
                           ) -> list[dict]:
    """offset 0~max_pages-1 페이지네이션. 페이지가 20건 미만이면 중단."""
    out: list[dict] = []
    for page in range(max_pages):
        try:
            resp = requests.get(
                BRAVE_ENDPOINT,
                params={"q": query, "count": 20, "offset": page,
                        "country": "KR", "search_lang": "ko"},
                headers={"X-Subscription-Token": BRAVE_SEARCH_API_KEY,
                         "Accept": "application/json"},
                timeout=(3, 10),
            )
            resp.raise_for_status()
            results = (resp.json().get("web") or {}).get("results") or []
        except Exception as exc:  # noqa: BLE001
            print(f"    ! page {page} 실패: {exc}")
            break
        for r in results:
            out.append({
                "url":      r.get("url", ""),
                "title":    r.get("title", ""),
                "snippet":  r.get("description", ""),
                "page_age": r.get("page_age", ""),
                "page":     page,
            })
        time.sleep(sleep_s)
        if len(results) < 20:
            break
    return out


# ─── robots + 본문 fetch (운영 D11 정책과 동일 사상, 의존 없이 재구현) ──────────

_robots_cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}


def robots_allowed(url: str) -> bool:
    p = urlparse(url)
    host = f"{p.scheme}://{p.netloc}"
    if host not in _robots_cache:
        rp = urllib.robotparser.RobotFileParser()
        try:
            r = requests.get(f"{host}/robots.txt", timeout=8,
                             headers={"User-Agent": UA})
            if r.status_code in (401, 403):
                rp.disallow_all = True
            elif r.status_code >= 400:
                rp = None
            else:
                rp.parse(r.text.splitlines())
        except Exception:  # noqa: BLE001
            rp = None
        _robots_cache[host] = rp
    rp = _robots_cache[host]
    return True if rp is None else rp.can_fetch("*", url)


_MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
              "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
              "Mobile/15E148 Safari/604.1")

# dcinside PC(gall) 글 URL → 모바일(m.dcinside) 변환 패턴
_DC_VIEW_RE = re.compile(
    r"gall\.dcinside\.com/(?:mgallery/|mini/)?board/view/?\?id=([^&]+)&no=(\d+)")
_DC_PATH_RE = re.compile(r"gall\.dcinside\.com/board/([^/?]+)/(\d+)")


def _fetch_target(url: str) -> tuple[str, str]:
    """fetch 대상 URL·UA 결정 (m.dcinside 검증 — 2026-06-12).

    실측: gall.dcinside 는 비브라우저 접근에 보일러플레이트/빈 응답을 주고,
    m.dcinside 는 데스크톱 UA 를 gall 로 리다이렉트한다. 따라서 dcinside 글은
    모바일 URL 로 변환 + 모바일 UA 로 요청해 서버 렌더링 본문 수집 가능성을 검증한다.
    """
    m = _DC_VIEW_RE.search(url) or _DC_PATH_RE.search(url)
    if m:
        return f"https://m.dcinside.com/board/{m.group(1)}/{m.group(2)}", _MOBILE_UA
    return url, UA


_ERROR_PAGE_SIGNS = ("찾을 수 없", "없는 게시물", "삭제된 게시물", "삭제되었",
                     "존재하지 않는")
_NAV_SIGNS = ("본문 바로가기", "메뉴 바로가기")   # 추출 실패 → 내비게이션 오염 신호


def fetch_body(url: str) -> dict:
    """requests(브라우저 UA) + trafilatura 추출. 반환: {body, comments, status}.

    실측 결함 수정 (2026-06-12): trafilatura.fetch_url 의 자체 UA 는 clien 등에서
    본문 없는 응답/추출 실패를 일으켜 내비게이션 메뉴만 추출되었다 (잠정 오제외
    6건의 body_excerpt 가 전부 메뉴 텍스트). 운영 D11 과 동일하게 브라우저 UA 의
    requests 로 HTML 을 받고, favor_precision 으로 보일러플레이트를 억제한다.
    오류 페이지·내비 오염은 status 로 표기해 분포 집계에서 구분 가능하게 한다.
    """
    import trafilatura  # 지연 import
    try:
        target, ua = _fetch_target(url)
        resp = requests.get(target, timeout=10, headers={"User-Agent": ua})
        if resp.status_code != 200:
            return {"body": "", "comments": "", "status": f"http_{resp.status_code}"}
        doc = trafilatura.bare_extraction(resp.text, include_comments=True,
                                          url=url, favor_precision=True)
        if doc is None:
            return {"body": "", "comments": "", "status": "extract_failed"}
        body = getattr(doc, "text", "") or (doc.get("text", "") if isinstance(doc, dict) else "")
        comments = getattr(doc, "comments", "") or (doc.get("comments", "") if isinstance(doc, dict) else "")
        status = "ok"
        if any(s in body[:300] for s in _ERROR_PAGE_SIGNS):
            status = "error_page"          # 삭제·부재 글 (HTTP 200 이어도)
        elif any(s in body[:100] for s in _NAV_SIGNS):
            status = "nav_contaminated"    # 본문 대신 메뉴 추출
        return {"body": body or "", "comments": comments or "", "status": status}
    except Exception as exc:  # noqa: BLE001
        return {"body": "", "comments": "", "status": f"error: {str(exc)[:80]}"}


# ─── 메인 ────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="CE-D7 Brave 리콜 측정 (일회성)")
    ap.add_argument("--candidates", nargs="+", required=True,
                    help="candidate alias 목록 (정식 명칭)")
    ap.add_argument("--sites", nargs="+", default=DEFAULT_SITES)
    ap.add_argument("--max-pages", type=int, default=10,
                    help="쿼리당 최대 페이지 (측정 기본 10 = offset 상한)")
    ap.add_argument("--fetch-per-site", type=int, default=5,
                    help="사이트당 본문 fetch 표본 수")
    ap.add_argument("--refetch-from", default="",
                    help="이전 측정 디렉터리 경로 — recall_raw.csv 를 재사용해 "
                         "Brave 검색을 생략하고 fetch 단계만 재실행 (비용 0)")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    if not args.refetch_from and not BRAVE_SEARCH_API_KEY:
        sys.exit("BRAVE_SEARCH_API_KEY 미설정")

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.out or f"data/measurement/brave_recall_{ts}")
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(42)   # 표본 재현성

    # ── 0) refetch 모드 — 이전 recall_raw.csv 재사용 (Brave 검색 생략) ──────
    # 구버전 CSV(url 컬럼 부재) 호환: norm_url 에서 원본 복원. ppomppu 는 구버전
    # normalize 가 m. 을 제거했으므로 /new/ 경로면 모바일 호스트를 복원한다.
    def _reconstruct_url(norm: str) -> str:
        if norm.startswith("ppomppu.co.kr/new/"):
            return "https://m." + norm
        return "https://" + norm

    found: dict[tuple[str, str], list[dict]] = {}
    posts: dict[str, dict] = {}
    page_age_by_page: dict[int, list[str]] = defaultdict(list)

    if args.refetch_from:
        src = Path(args.refetch_from) / "recall_raw.csv"
        print(f"▶ refetch 모드 — {src} 재사용 (Brave 호출 0회)")
        with src.open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                posts[r["norm_url"]] = {
                    "url": r.get("url") or _reconstruct_url(r["norm_url"]),
                    "site": r["site"], "title": r["title"],
                    "snippet": r.get("snippet", ""),
                    "page_age": r.get("page_age", ""),
                    "matched_candidates": {
                        c for c in (r["matched_candidates"] or "").split(";") if c},
                }
        return _run_fetch_phase(args, posts, out_dir, rng)

    est = len(args.sites) * len(args.candidates) * args.max_pages
    print(f"▶ 측정 시작 — 최대 Brave 호출 {est}회 (≈ ${est * 0.005:.2f})")

    # ── 1) 검색 (발견 기준) ────────────────────────────────────────────────
    # found[(site, cand)] = [result, ...] / posts[norm_url] = 고유 게시글 레코드
    for site in args.sites:
        for cand in args.candidates:
            query = f"site:{site} {cand}"
            print(f"  검색: {query}")
            results = brave_search_all_pages(query, args.max_pages)
            found[(site, cand)] = results
            for r in results:
                page_age_by_page[r["page"]].append(r["page_age"])
                key = normalize_url(r["url"])
                rec = posts.setdefault(key, {
                    "url": r["url"], "site": site, "title": r["title"],
                    "snippet": r["snippet"], "page_age": r["page_age"],
                    "matched_candidates": set(),
                })
                rec["matched_candidates"].add(cand)

    # ── 2) 메인 표 + 보조 행 ───────────────────────────────────────────────
    lines = ["# Brave 리콜 측정 — 발견 기준 표 (셀 = candidate 쿼리 결과 수, 중복 허용)\n",
             "| 사이트 | " + " | ".join(args.candidates) + " | 발견 합계 | 고유 URL | 다중 발견 |",
             "|---|" + "---|" * (len(args.candidates) + 3)]
    for site in args.sites:
        row = [str(len(found[(site, c)])) for c in args.candidates]
        site_posts = [p for p in posts.values() if p["site"] == site]
        multi = sum(1 for p in site_posts if len(p["matched_candidates"]) >= 2)
        lines.append(f"| {site} | " + " | ".join(row)
                     + f" | {sum(map(int, row))} | {len(site_posts)} | {multi} |")
    lines.append("\n※ 다중 발견 = 2개 이상 candidate 쿼리에서 발견된 고유 게시글"
                 " (CE-D5 matched_candidates 대응). 발견 합계 − 고유 URL = 중복 발견량.\n")
    lines.append("## 페이지별 결과 수·발행일 분포\n\n| 페이지 | 결과 수 | 발행일 보유율 |")
    lines.append("|---|---|---|")
    for page in sorted(page_age_by_page):
        ages = page_age_by_page[page]
        dated = sum(1 for a in ages if a)
        lines.append(f"| {page + 1} | {len(ages)} | {dated / len(ages):.0%} |")
    (out_dir / "recall_table.md").write_text("\n".join(lines), encoding="utf-8")

    with (out_dir / "recall_raw.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["norm_url", "url", "site", "title", "matched_candidates",
                    "page_age", "snippet"])
        for key, p in sorted(posts.items()):
            w.writerow([key, p["url"], p["site"], p["title"],
                        ";".join(sorted(p["matched_candidates"])),
                        p["page_age"], p["snippet"][:300]])

    return _run_fetch_phase(args, posts, out_dir, rng)


def _run_fetch_phase(args, posts: dict[str, dict], out_dir: Path,
                     rng: random.Random) -> None:
    """필터 판정 + fetch + 분포·오제외 CSV + summary (검색과 분리 — refetch 재사용)."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    # ── 3) 스니펫 필터 판정 + fetch 표본 선정 ──────────────────────────────
    for p in posts.values():
        passed, hits = snippet_filter_pass(p["title"], p["snippet"])
        p["filter"] = "passed" if passed else "excluded"
        p["filter_hits"] = hits

    excluded = [p for p in posts.values() if p["filter"] == "excluded"]
    passed_pool = [p for p in posts.values() if p["filter"] == "passed"]

    # 길이·댓글 분포용: 사이트당 N건 (필터 통과분에서 무작위)
    fetch_sample: list[dict] = []
    for site in args.sites:
        pool = [p for p in passed_pool if p["site"] == site]
        fetch_sample.extend(rng.sample(pool, min(args.fetch_per_site, len(pool))))
    # 오제외 표본 20건: 제외분 전수 우선 + 통과분 무작위로 보충
    fe_sample = excluded[:20]
    fe_fill = [p for p in fetch_sample if p not in fe_sample]
    fe_sample += fe_fill[:max(0, 20 - len(fe_sample))]

    # ── 4) 본문 fetch — 길이·댓글 분포 + 오제외 검사 + 언급 candidate 분포 ──
    to_fetch = {id(p): p for p in fetch_sample + fe_sample}
    print(f"  본문 fetch: {len(to_fetch)}건 (robots 준수, 1s/건)")
    mention_dist: Counter = Counter()
    for p in to_fetch.values():
        if not robots_allowed(p["url"]):
            p["fetch"] = {"body": "", "comments": "", "status": "robots_disallowed"}
            continue
        p["fetch"] = fetch_body(p["url"])
        time.sleep(1.0)
        body = p["fetch"]["body"]
        if body:
            n_mentions = sum(1 for c in args.candidates if c.split()[-1] in body)
            mention_dist[min(n_mentions, 3)] += 1

    with (out_dir / "length_dist.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["url", "site", "body_chars", "needs_chunks(3000자)",
                    "fetch_status"])
        for p in fetch_sample:
            fr = p.get("fetch", {})
            n = len(fr.get("body", ""))
            w.writerow([p["url"], p["site"], n,
                        -(-n // 3000) if n else 0, fr.get("status", "skipped")])

    with (out_dir / "comment_dist.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["url", "site", "comment_chars",
                    "comment_count_approx(문단수)", "fetch_status"])
        for p in fetch_sample:
            fr = p.get("fetch", {})
            cm = fr.get("comments", "")
            approx = len([s for s in cm.splitlines() if s.strip()]) if cm else 0
            w.writerow([p["url"], p["site"], len(cm), approx,
                        fr.get("status", "skipped")])

    auto_fe = 0
    with (out_dir / "false_exclusion_sample.csv").open("w", newline="",
                                                       encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["url", "site", "title", "snippet", "filter_verdict",
                    "snippet_hits", "body_token_hits", "auto_judgement",
                    "body_excerpt_500", "manual_label"])
        for p in fe_sample:
            fr = p.get("fetch", {})
            body = fr.get("body", "") if fr.get("status") == "ok" else ""
            body_hits = [t for t in FILTER_TOKENS if t in body]
            auto = ""
            if p["filter"] == "excluded" and body_hits:
                auto = "잠정 오제외"
                auto_fe += 1
            w.writerow([p["url"], p["site"], p["title"],
                        p["snippet"][:200], p["filter"],
                        ";".join(p["filter_hits"]), ";".join(body_hits), auto,
                        body[:500].replace("\n", " "), ""])

    # ── 5) summary ─────────────────────────────────────────────────────────
    summary = {
        "measured_at": ts,
        "mode": "refetch" if getattr(args, "refetch_from", "") else "full",
        "unique_posts": len(posts),
        "multi_candidate_posts": sum(
            1 for p in posts.values() if len(p["matched_candidates"]) >= 2),
        "snippet_filter_excluded": len(excluded),
        "false_exclusion_auto_provisional": auto_fe,
        "body_mention_candidate_dist (fetch 표본, 3=3개 이상)": dict(mention_dist),
        "gate_check_avg_per_site_x_cand_ge_10": (
            len(posts) / max(1, len(args.sites) * len(args.candidates)) >= 10),
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ 완료 — 산출물: {out_dir}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
