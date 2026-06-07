"""
scripts/profile_marketing_collection.py (v1.0 §6-6a)
-----------------------------------------------------
marketing_social 수집 3노드의 실데이터 실측.
설계: docs/design/marketing_social_node_design.md §6 (검증 6)

측정 항목
---------
① YouTube 3-call 체인 성공률 + quota 실소모
② RSS 발견율 (platform 별 — 특히 self_hosted 순차 시도 적중률)
③ PR 날짜 추출 성공률 (50% 미만 시 일괄 강등 검토 — §6)
④ 게시일 분포 — 최근 6개월 윈도우(MS-D5) 월별 카운트
⑤ 발췌 가용률 — YouTube description · RSS summary 비어있지 않은 비율 (MS-D10)
⑥ 상품명 직접 포함률 — 코드 선판정(하이브리드 1단계) 예상 적중률 (MS-D10)

특징
----
- owned_channel_urls_by_candidate 는 url_discovery_owned_channels_node 를 거쳐
  복원 (직전 실행 캐시 히트 — Brave·LLM 비용 0).
- YouTube quota ~3 units/candidate, RSS·PR 은 실 HTTP (1초 rate limit).
- 네트워크 필요 — 로컬에서 실행 (샌드박스 불가).

실행: python scripts/profile_marketing_collection.py
출력: 콘솔 + scripts/out/profile_marketing_collection_result.json
"""

import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.graph.nodes.url_discovery_owned_channels_node import (  # noqa: E402
    url_discovery_owned_channels_node,
)
from server.graph.nodes.youtube_channel_metadata_collection_node import (  # noqa: E402
    youtube_channel_metadata_collection_node,
)
from server.graph.nodes.blog_rss_collection_node import blog_rss_collection_node  # noqa: E402
from server.graph.nodes.pr_release_collection_node import pr_release_collection_node  # noqa: E402
from server.llm.youtube_client import current_quota_used  # noqa: E402

OUT_PATH = Path(__file__).parent / "out" / "profile_marketing_collection_result.json"

# 실제 파이프라인과 동일한 candidate 명세 (profile_owned_channels.py 와 동일 — 캐시 정합)
BASE_STATE = {
    "domain_name": "핀테크 / 해외여행 특화 카드 (consumer_travel_card)",
    "own_product": {
        "product_id": "own_토스트래블카드",
        "name":       "토스 트래블카드",
        "brand":      "토스",
    },
    "competitor_candidates": [
        {"candidate_id": "comp_하나트래블로그카드",    "product_name": "하나 트래블로그 카드",    "brand": "하나카드"},
        {"candidate_id": "comp_신한sol트래블체크카드", "product_name": "신한 SOL트래블 체크카드", "brand": "신한카드"},
        {"candidate_id": "comp_트래블월렛카드",        "product_name": "트래블월렛 카드",         "brand": "트래블월렛"},
    ],
    "selected_competitor_ids": [
        "comp_하나트래블로그카드", "comp_신한sol트래블체크카드", "comp_트래블월렛카드",
    ],
    "selected_purposes": ["marketing_social"],
}

# ⑥ 코드 선판정용 상품명 토큰 (소문자 비교)
PRODUCT_TOKENS = {
    "own_토스트래블카드":        ["트래블카드"],
    "comp_하나트래블로그카드":   ["트래블로그"],
    "comp_신한sol트래블체크카드": ["sol트래블", "쏠트래블"],
    "comp_트래블월렛카드":       ["트래블월렛"],
}


def _month_window(n: int = 6) -> list[str]:
    """최근 n개월 YYYY-MM 라벨 (오름차순) — MS-D5 동일 윈도우."""
    now = datetime.now(timezone.utc)
    months = []
    y, m = now.year, now.month
    for _ in range(n):
        months.append(f"{y}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return sorted(months)


def _related(cid: str, *texts: str) -> bool:
    joined = " ".join(t.lower() for t in texts if t)
    return any(tok in joined for tok in PRODUCT_TOKENS.get(cid, []))


def main() -> None:
    print("=== marketing_social 수집 3노드 실측 ===\n")

    # 0) owned channels 복원 (직전 실행 캐시 히트 — 비용 0)
    owned = url_discovery_owned_channels_node(dict(BASE_STATE))  # type: ignore[arg-type]
    state = {**BASE_STATE, "owned_channel_urls_by_candidate":
             owned.get("owned_channel_urls_by_candidate", {})}
    n_ch = sum(len(v) for v in state["owned_channel_urls_by_candidate"].values())
    print(f"[0] 공식 채널 복원: {n_ch}건 (캐시)")

    # 1) 3노드 실행 + quota 측정
    q0 = current_quota_used()
    yt   = youtube_channel_metadata_collection_node(state)   # type: ignore[arg-type]
    q_yt = current_quota_used() - q0
    blog = blog_rss_collection_node(state)                    # type: ignore[arg-type]
    pr   = pr_release_collection_node(state)                  # type: ignore[arg-type]

    meta  = yt.get("youtube_channel_metadata", {})
    feeds = blog.get("blog_rss_posts", [])
    prs   = pr.get("pr_releases", [])
    window = _month_window()
    report: dict = {"measured_at": datetime.now(timezone.utc).isoformat(),
                    "window": window}

    # ① YouTube 체인
    print(f"\n[①] YouTube: {len(meta)}건 성공 | quota {q_yt} units")
    for cid, rec in sorted(meta.items()):
        print(f"  {cid}: {rec['title']} 구독 {rec['subscriber_count']:,} · "
              f"최근 영상 {len(rec['recent_videos'])}건")
    report["youtube"] = {"success": len(meta), "quota_units": q_yt}

    # ② RSS 발견율
    print(f"\n[②] RSS 발견율 (platform 별)")
    by_status = Counter((f["platform"], f["fetch_status"]) for f in feeds)
    for (p, s), n in sorted(by_status.items()):
        print(f"  {p:<17} {s:<16} {n}건")
    ok_feeds = [f for f in feeds if f["fetch_status"] == "ok"]
    report["rss"] = {"total": len(feeds), "ok": len(ok_feeds),
                     "by_status": {f"{p}/{s}": n for (p, s), n in by_status.items()}}

    # ③ PR 추출 성공률
    ok_pr = [p for p in prs if p["fetch_status"] == "ok"]
    rate = (len(ok_pr) / len(prs) * 100) if prs else 0
    print(f"\n[③] PR 추출: {len(ok_pr)}/{len(prs)} ({rate:.0f}%)"
          f"{' — 50% 미만: 일괄 강등 검토(§6)' if prs and rate < 50 else ''}")
    for p in prs:
        print(f"  {p['candidate_id']}: {p['fetch_status']} · {len(p['releases'])}건")
    report["pr"] = {"total": len(prs), "ok": len(ok_pr)}

    # ④ 월별 분포 (최근 6개월 윈도우)
    print(f"\n[④] 게시일 분포 — 윈도우 {window[0]} ~ {window[-1]}")
    dist: dict[str, dict[str, int]] = {}
    for cid, rec in meta.items():
        c = Counter(v["published_at"][:7] for v in rec["recent_videos"]
                    if v.get("published_at", "")[:7] in window)
        dist[f"{cid}/youtube"] = dict(c)
    for f in ok_feeds:
        c = Counter(p["published_at"][:7] for p in f["posts"]
                    if p.get("published_at", "")[:7] in window)
        dist[f"{f['candidate_id']}/{f['platform']}"] = dict(c)
    for ch, c in sorted(dist.items()):
        total = sum(c.values())
        print(f"  {ch:<45} 윈도우 내 {total:>3}건  {dict(sorted(c.items()))}")
    report["monthly"] = dist

    # ⑤ 발췌 가용률 + ⑥ 상품명 직접 포함률 (MS-D10)
    print(f"\n[⑤⑥] 발췌 가용률 / 상품명 직접 포함률 (코드 선판정 예상)")
    rows = []
    for cid, rec in meta.items():
        vids = rec["recent_videos"]
        if vids:
            desc = sum(1 for v in vids if v.get("description"))
            rel  = sum(1 for v in vids if _related(cid, v.get("title", ""),
                                                   v.get("description", "")))
            rows.append((f"{cid}/youtube", len(vids), desc, rel))
    for f in ok_feeds:
        posts = f["posts"]
        summ = sum(1 for p in posts if p.get("summary"))
        rel  = sum(1 for p in posts if _related(f["candidate_id"], p.get("title", ""),
                                                p.get("summary", "")))
        rows.append((f"{f['candidate_id']}/{f['platform']}", len(posts), summ, rel))
    for ch, n, excerpt_n, rel_n in sorted(rows):
        print(f"  {ch:<45} 게시물 {n:>3} · 발췌 {excerpt_n / n * 100 if n else 0:>4.0f}% · "
              f"상품명 포함 {rel_n / n * 100 if n else 0:>4.0f}% ({rel_n}건)")
    report["excerpt_and_relevance"] = [
        {"channel": ch, "posts": n, "excerpt": e, "product_token_hits": r}
        for ch, n, e, r in rows]

    # 에러 합산
    errors = (yt.get("errors", []) + blog.get("errors", []) + pr.get("errors", []))
    if errors:
        print(f"\n--- errors ({len(errors)}건) ---")
        for e in errors:
            print(f"  {e.get('error','')}")
    report["errors"] = errors

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n결과 저장: {OUT_PATH}")


if __name__ == "__main__":
    main()
