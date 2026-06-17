#!/usr/bin/env python3
"""
scripts/check_manual_captions.py
---------------------------------
측정 JSON의 전체 영상에 대해 수동 자막 보유 여부를 확인한다.

비용: youtube-transcript-api는 YouTube Data API v3 quota 미사용 (무료).

실행
----
  cd competitive-intel
  python -m scripts.check_manual_captions
  python -m scripts.check_manual_captions --input data/measurement/youtube_collection_3_slug_20260613T084051.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

MEASUREMENT_GLOB = "data/measurement/youtube_collection_*.json"
REQUEST_DELAY    = 0.3   # YouTube 과부하 방지 (초)


def _latest_measurement(root: Path) -> Path | None:
    files = sorted(root.glob(MEASUREMENT_GLOB))
    return files[-1] if files else None


def _check(video_id: str, api):
    from youtube_transcript_api import (
        NoTranscriptFound, TranscriptsDisabled,
        PoTokenRequired, IpBlocked, RequestBlocked,
        VideoUnavailable, CouldNotRetrieveTranscript,
    )
    try:
        tl = api.list(video_id)
        tracks = list(tl)
        manual = [t for t in tracks if not t.is_generated]
        auto   = [t for t in tracks if t.is_generated]
        has_ko_manual = any(t.language_code == "ko" for t in manual)
        has_ko_auto   = any(t.language_code == "ko" for t in auto)

        if has_ko_manual:
            return "manual_ko"
        elif manual:
            lang_codes = [t.language_code for t in manual]
            return f"manual_other:{','.join(lang_codes[:2])}"
        elif has_ko_auto:
            return "auto_ko"
        elif auto:
            return "auto_other"
        else:
            return "no_caption"

    except TranscriptsDisabled:
        return "disabled"
    except PoTokenRequired:
        return "po_token_required"
    except (IpBlocked, RequestBlocked) as e:
        return f"blocked:{type(e).__name__}"
    except VideoUnavailable:
        return "video_unavailable"
    except CouldNotRetrieveTranscript as e:
        return f"error:{type(e).__name__}"
    except Exception as e:
        return f"error:{type(e).__name__}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=None,
                        help="측정 JSON 경로 (미지정 시 최신 파일 자동 선택)")
    parser.add_argument("--delay", type=float, default=REQUEST_DELAY)
    args = parser.parse_args()

    src = args.input or _latest_measurement(PROJECT_ROOT)
    if not src or not src.exists():
        print("측정 파일을 찾을 수 없습니다.")
        sys.exit(1)

    data    = json.loads(src.read_text(encoding="utf-8"))
    results = data["results"]

    # 전체 video_id 수집
    videos: list[dict] = []   # {candidate_id, video_id, title}
    for cid, r in results.items():
        for v in r.get("videos") or []:
            vid = v.get("video_id")
            if vid:
                videos.append({
                    "cid":   cid,
                    "vid":   vid,
                    "title": v.get("title", ""),
                })

    total = len(videos)
    print(f"총 {total}건 영상 자막 유형 확인 시작 (delay={args.delay}s)\n")

    from youtube_transcript_api import YouTubeTranscriptApi
    api = YouTubeTranscriptApi()

    status_by_cid: dict[str, list[str]] = defaultdict(list)
    manual_videos: list[dict] = []

    for i, v in enumerate(videos, 1):
        status = _check(v["vid"], api)
        status_by_cid[v["cid"]].append(status)

        if status.startswith("manual"):
            manual_videos.append({**v, "status": status})

        if i % 20 == 0 or i == total:
            done = sum(1 for s in sum(status_by_cid.values(), []) if s.startswith("manual"))
            print(f"  진행: {i}/{total}  수동 자막 발견: {done}건", end="\r", flush=True)

        time.sleep(args.delay)

    print(f"\n\n{'═'*65}")

    # ── 전체 집계 ─────────────────────────────────────────────────────────────
    all_statuses = sum(status_by_cid.values(), [])
    from collections import Counter
    counter = Counter(all_statuses)

    print(f"  전체 {total}건 결과:")
    for status, cnt in counter.most_common():
        pct = cnt / total * 100
        bar = "█" * int(pct / 2)
        print(f"    {status:<30} {cnt:>4}건  ({pct:4.1f}%)  {bar}")

    manual_total = sum(v for k, v in counter.items() if k.startswith("manual"))
    print(f"\n  수동 자막 합계: {manual_total}건 ({manual_total/total:.1%})")

    # ── candidate별 집계 ──────────────────────────────────────────────────────
    print(f"\n{'─'*65}")
    print(f"  {'candidate':<32}  {'전체':>4}  {'수동':>4}  {'수동%':>5}  {'auto_ko':>7}")
    print(f"  {'─'*32}  {'─'*4}  {'─'*4}  {'─'*5}  {'─'*7}")
    for cid, statuses in status_by_cid.items():
        n       = len(statuses)
        n_man   = sum(1 for s in statuses if s.startswith("manual"))
        n_autko = sum(1 for s in statuses if s == "auto_ko")
        print(f"  {cid:<32}  {n:>4}  {n_man:>4}  {n_man/n:.0%}  {n_autko:>7}")

    # ── 수동 자막 영상 목록 ────────────────────────────────────────────────────
    if manual_videos:
        print(f"\n  수동 자막 보유 영상 ({len(manual_videos)}건):")
        for v in manual_videos:
            title = v["title"][:55]
            print(f"    [{v['status']}] {v['vid']}  {title}")
    else:
        print("\n  수동 자막 보유 영상: 없음")

    # ── JSON 저장 ─────────────────────────────────────────────────────────────
    out = {
        "source":        str(src),
        "total_videos":  total,
        "status_counts": dict(counter),
        "manual_rate":   round(manual_total / total, 4),
        "by_candidate":  {
            cid: {
                "total":       len(statuses),
                "manual":      sum(1 for s in statuses if s.startswith("manual")),
                "auto_ko":     sum(1 for s in statuses if s == "auto_ko"),
                "disabled":    sum(1 for s in statuses if s == "disabled"),
                "error":       sum(1 for s in statuses if s.startswith("error") or s.startswith("blocked")),
            }
            for cid, statuses in status_by_cid.items()
        },
        "manual_videos": manual_videos,
    }
    out_path = src.parent / (src.stem + "_caption_types.json")
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  저장: {out_path}")


if __name__ == "__main__":
    main()
