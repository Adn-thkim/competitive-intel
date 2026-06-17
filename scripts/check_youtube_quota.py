"""
YouTube Data API v3 quota 사용량 조회 스크립트.

실행
----
  cd competitive-intel
  python -m scripts.check_youtube_quota          # 서버 실행 중일 때
  python -m scripts.check_youtube_quota --port 8001

출력 예시
---------
  ┌─────────────────────────────────────────┐
  │  YouTube API Quota (2026-06-13 UTC)     │
  ├──────────────────────┬──────────────────┤
  │  일일 한도           │  10,000 units    │
  │  사용                │     420 units    │
  │  잔량                │   9,580 units    │
  │  실사용 가능 (여유)  │   8,580 units    │
  │  safety_margin       │   1,000 units    │
  └──────────────────────┴──────────────────┘
  * 서버 프로세스 재시작 시 카운터 초기화됨
  * 정확한 Google 측 잔량: https://console.cloud.google.com/...
"""

from __future__ import annotations

import argparse
import sys

try:
    import requests as _req
except ImportError:
    _req = None  # type: ignore[assignment]


def _bar(used: int, limit: int, width: int = 30) -> str:
    filled = int(width * used / limit) if limit else 0
    pct = used / limit * 100 if limit else 0
    return f"[{'█' * filled}{'░' * (width - filled)}] {pct:.1f}%"


def main() -> None:
    parser = argparse.ArgumentParser(description="YouTube quota 잔량 조회")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    url = f"http://{args.host}:{args.port}/quota/youtube"

    if _req is None:
        print("ERROR: 'requests' 패키지가 없습니다. pip install requests 후 재실행하세요.")
        sys.exit(1)

    try:
        resp = _req.get(url, timeout=5)
        resp.raise_for_status()
    except _req.exceptions.ConnectionError:
        print(f"ERROR: 서버에 연결할 수 없습니다 ({url})")
        print("  → FastAPI 서버가 실행 중인지 확인하세요: uvicorn server.graph.api:app")
        sys.exit(1)
    except _req.exceptions.RequestException as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    d = resp.json()
    used      = d["used"]
    remaining = d["remaining"]
    usable    = d["usable"]
    limit     = d["daily_limit"]
    margin    = d["safety_margin"]
    date      = d.get("reset_date_utc", "unknown")

    print(f"\n  YouTube API Quota ({date} UTC)")
    print(f"  {_bar(used, limit)}")
    print()
    print(f"  {'일일 한도':<18} {limit:>8,} units")
    print(f"  {'사용':<18} {used:>8,} units")
    print(f"  {'잔량':<18} {remaining:>8,} units")
    print(f"  {'실사용 가능':<18} {usable:>8,} units  (safety_margin {margin:,} 제외)")
    print()

    # 주요 작업별 예상 소비량 안내
    search_pages = usable // 100
    print(f"  └ search.list 잔여 호출 가능: 약 {search_pages}회 (페이지당 100 units)")
    print()
    print(f"  * 서버 프로세스 재시작 시 카운터 초기화됨 (Google 실측치와 괴리 발생 가능)")
    print(f"  * 정확한 Google 측 잔량: {d['console_url']}")
    print()


if __name__ == "__main__":
    main()
