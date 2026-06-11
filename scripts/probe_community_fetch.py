"""
probe_community_fetch.py
------------------------
community_collection 이 수집하지 못한 clien 커뮤니티 URL 2건의 실패 원인을
관측 가능하게 만드는 1회성 진단 스크립트.

배경
----
- 파이프라인 로깅은 stdout 전용(FileHandler 없음), 체크포인터는 MemorySaver(인메모리),
  fetch 캐시는 `ok`·`requires_dynamic_render` 만 저장(`fetch_failed` 미저장) 이라
  clien 의 fetch_status/error 가 디스크 어디에도 남지 않는다.
- 본 스크립트는 (A) 파이프라인과 동일한 `_fetch_content` 결과와,
  (B) 봇 UA vs 브라우저 UA 의 원시 HTTP 응답을 함께 기록해
  "봇 차단(403/429)" 인지 "JS 렌더링 필요" 인지 진단 근거를 남긴다.

실행 (clien 접근 가능한 로컬 네트워크에서)
-------------------------------------------
    cd competitive-intel
    python scripts/probe_community_fetch.py

결과: scripts/out/probe_community_fetch_result.json + 콘솔 요약표.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# 프로젝트 루트(= competitive-intel)를 import 경로에 추가
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import requests  # noqa: E402

from server.graph.nodes.official_content_collection_node import (  # noqa: E402
    _fetch_content,
    _FETCH_USER_AGENT,
    _FETCH_TIMEOUT,
    _SPA_MIN_CHARS,
)

# community_collection 이 선별한 유일한 community(domain_class) URL 2건
COMMUNITY_URLS: list[dict] = [
    {"candidate_id": "comp_트래블월렛카드",
     "url": "https://www.clien.net/service/board/park/19129105"},
    {"candidate_id": "comp_하나트래블로그카드",
     "url": "https://www.clien.net/service/board/lecture/18204643"},
]

# 진단용 브라우저형 UA (실제 Chrome UA 문자열)
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def _raw_probe(url: str, user_agent: str) -> dict:
    """원시 HTTP 응답만 기록 (본문 추출 없이 status/length 만)."""
    headers = {
        "User-Agent": user_agent,
        "Accept-Language": "ko-KR,ko;q=0.9",
        "Referer": "https://www.clien.net/",
    }
    try:
        resp = requests.get(url, timeout=_FETCH_TIMEOUT, headers=headers)
        return {
            "ok": True,
            "status_code": resp.status_code,
            "html_len": len(resp.text),
            "content_type": resp.headers.get("Content-Type", ""),
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def main() -> None:
    rows: list[dict] = []
    for item in COMMUNITY_URLS:
        url = item["url"]
        # (A) 파이프라인과 동일 경로
        fc = _fetch_content(url)
        # (B) UA 비교 진단 (봇 UA vs 브라우저 UA)
        bot_raw = _raw_probe(url, _FETCH_USER_AGENT)
        browser_raw = _raw_probe(url, _BROWSER_UA)
        rows.append({
            "candidate_id": item["candidate_id"],
            "url": url,
            "pipeline_fetch": {
                "fetch_status": fc.get("fetch_status"),
                "error": fc.get("error", ""),
                "content_len": len(fc.get("content", "")),
                "from_cache": fc.get("from_cache", False),
            },
            "raw_bot_ua": bot_raw,
            "raw_browser_ua": browser_raw,
        })

    result = {
        "probed_at": datetime.now(timezone.utc).isoformat(),
        "node_user_agent": _FETCH_USER_AGENT,
        "spa_min_chars": _SPA_MIN_CHARS,
        "results": rows,
    }

    out_dir = _ROOT / "scripts" / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "probe_community_fetch_result.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                        encoding="utf-8")

    # 콘솔 요약
    print(f"\n결과 저장: {out_path}\n")
    print(f"{'candidate':28} {'pipeline':22} {'bot_UA':12} {'browser_UA':12}")
    print("-" * 78)
    for r in rows:
        pf = r["pipeline_fetch"]
        pipe = f"{pf['fetch_status']}({pf['content_len']}자)"
        bot = (str(r["raw_bot_ua"].get("status_code"))
               if r["raw_bot_ua"]["ok"] else "EXC")
        brw = (str(r["raw_browser_ua"].get("status_code"))
               if r["raw_browser_ua"]["ok"] else "EXC")
        print(f"{r['candidate_id']:28} {pipe:22} {bot:12} {brw:12}")
    print("\n진단 가이드:")
    print("  - bot_UA 가 403/429 이고 browser_UA 가 200 → 봇 UA 차단 (헤더 보강으로 해결)")
    print("  - 둘 다 200 인데 pipeline 이 requires_dynamic_render → JS 렌더링 필요")
    print("  - 둘 다 EXC/4xx → 네트워크·차단 정책 (헤드리스 브라우저 폴백 검토)")


if __name__ == "__main__":
    main()
