"""
scripts/profile_owned_channels.py (v0.13.5)
--------------------------------------------
url_discovery_owned_channels_node 의 candidate 별 공식 운영 채널 탐지를
실데이터로 단독 검증한다 (전체 파이프라인 실행 불필요).

배경
----
- 2026-06-07 사용자 실사로 실존 채널 7건(토스 X, 하나은행 X·블로그,
  하나카드 YouTube, 신한카드 YouTube·티스토리, 트래블월렛 네이버 블로그)이
  미탐지였음을 확인 → v0.13.4 브랜드 site: 보조 쿼리 + blog_self_hosted 도입.
- 본 스크립트는 그 보완의 실효성(회수율)을 채점한다.

특징
----
- 실제 Brave API(~46쿼리, 전역 1.05s throttle 로 직렬) + CLI LLM 검증 호출.
  Brave 크레딧 약 $0.25 소모, 소요 약 5~10분.
- cache_input 은 파이프라인 실행과 동일(candidate_id·platform·query·brand_query)
  → 본 스크립트가 채운 캐시를 이후 UI 전체 실행이 그대로 히트 (이중 과금 없음).
- 네트워크·CLI 토큰이 필요하므로 로컬에서 실행한다 (샌드박스 불가).

실행: python scripts/profile_owned_channels.py
출력: 콘솔 표 + scripts/out/profile_owned_channels_result.json
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.graph.nodes.url_discovery_owned_channels_node import (  # noqa: E402
    url_discovery_owned_channels_node,
)

OUT_PATH = Path(__file__).parent / "out" / "profile_owned_channels_result.json"

# 실제 파이프라인과 동일한 candidate 명세 (캐시 키 정합 — 06-07 실행 기준)
STATE = {
    "domain_name": "핀테크 / 해외여행 특화 카드 (consumer_travel_card)",
    "own_product": {
        "product_id": "own_토스트래블카드",
        "name":       "토스 트래블카드",
        "brand":      "토스",
    },
    "competitor_candidates": [
        {"candidate_id": "comp_하나트래블로그카드",     "product_name": "하나 트래블로그 카드",      "brand": "하나카드"},
        {"candidate_id": "comp_신한sol트래블체크카드",  "product_name": "신한 SOL트래블 체크카드",   "brand": "신한카드"},
        {"candidate_id": "comp_트래블월렛카드",         "product_name": "트래블월렛 카드",           "brand": "트래블월렛"},
    ],
    "selected_competitor_ids": [
        "comp_하나트래블로그카드", "comp_신한sol트래블체크카드", "comp_트래블월렛카드",
    ],
}

# 사용자 실사 확인 실존 채널 (2026-06-07) — 회수율 채점 기준
# (candidate_id, platform, url 부분 문자열)
EXPECTED = [
    ("own_토스트래블카드",        "x",                "x.com/toss__official"),
    ("comp_하나트래블로그카드",   "x",                "x.com/hanabank_kr"),
    ("comp_하나트래블로그카드",   "blog_self_hosted", "blog.hanabank.com"),
    ("comp_하나트래블로그카드",   "youtube_official", "ucsnlgvmpylledhxpqvucmca"),
    ("comp_신한sol트래블체크카드", "youtube_official", "youtube.com/user/eshinhancard"),
    ("comp_신한sol트래블체크카드", "blog_tistory",     "shinhancard-blog.tistory.com"),
    ("comp_신한sol트래블체크카드", "blog_self_hosted", "shinhancardblog.com"),
    ("comp_트래블월렛카드",       "blog_naver",       "blog.naver.com/travelwallet"),
]


def _norm(u: str) -> str:
    return u.lower().replace("https://", "").replace("http://", "").replace("www.", "")


def main() -> None:
    print("=== url_discovery_owned_channels 단독 실측 ===")
    print("candidate 4 × platform 7 — Brave throttle 직렬, 약 5~10분 소요\n")

    out = url_discovery_owned_channels_node(STATE)  # type: ignore[arg-type]
    results = out.get("owned_channel_urls_by_candidate", {})
    errors = out.get("errors", [])

    # ── 1) 발견 핸들 전체 ────────────────────────────────────────────────────
    print("\n--- 발견된 채널 ---")
    for cid, handles in results.items():
        print(f"\n[{cid}] {len(handles)}건")
        for h in handles:
            print(f"  {h['platform']:<17} {h['url']}"
                  f"  (handle={h.get('handle','')}, scope={h.get('account_scope','')},"
                  f" conf={h.get('confidence')})")

    # ── 2) 실존 채널 회수율 채점 ─────────────────────────────────────────────
    print("\n--- 실존 채널 회수율 (2026-06-07 사용자 실사 기준) ---")
    hit = 0
    for cid, platform, frag in EXPECTED:
        found = any(
            h.get("platform") == platform and frag in _norm(h.get("url", ""))
            for h in results.get(cid, [])
        )
        hit += found
        print(f"  {'✅' if found else '❌'} [{cid}] {platform}: {frag}")
    print(f"\n회수율: {hit}/{len(EXPECTED)}")

    # ── 3) 실패 기록 (v0.13.5 — Brave 후보 0건은 errors 로 가시화) ──────────
    if errors:
        print(f"\n--- errors ({len(errors)}건) ---")
        for e in errors:
            print(f"  {e.get('error','')}")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps({"results": results, "errors": errors}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n결과 저장: {OUT_PATH}")


if __name__ == "__main__":
    main()
