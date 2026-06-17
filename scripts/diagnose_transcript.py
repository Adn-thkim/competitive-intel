#!/usr/bin/env python3
"""
scripts/diagnose_transcript.py
-------------------------------
youtube-transcript-api 실패 원인 진단 스크립트.
로컬 머신에서 실행해서 실제 예외 유형을 확인한다.

실행
----
  cd competitive-intel
  python -m scripts.diagnose_transcript
"""
from __future__ import annotations
import json, sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

MEASUREMENT_GLOB = "data/measurement/youtube_collection_*.json"

def main():
    # 최신 측정 파일에서 video_id 추출
    files = sorted(Path(PROJECT_ROOT / "competitive-intel").glob(MEASUREMENT_GLOB)
                   if (PROJECT_ROOT / "competitive-intel").exists()
                   else Path(PROJECT_ROOT).glob(MEASUREMENT_GLOB))
    if not files:
        print("측정 파일 없음. video_id를 직접 지정합니다.")
        test_ids = ["o8LdRE3Jh-Q", "kstpZ0IfDnM", "dQw4w9WgXcQ"]
    else:
        data = json.loads(files[-1].read_text())
        test_ids = []
        for r in data["results"].values():
            for v in (r.get("videos") or [])[:2]:
                if v.get("video_id"):
                    test_ids.append(v["video_id"])
            if len(test_ids) >= 6:
                break

    print(f"테스트 video_id: {test_ids[:6]}\n")

    try:
        from youtube_transcript_api import (
            YouTubeTranscriptApi,
            NoTranscriptFound,
            TranscriptsDisabled,
            PoTokenRequired,
            IpBlocked,
            RequestBlocked,
            VideoUnavailable,
            VideoUnplayable,
            CouldNotRetrieveTranscript,
            YouTubeDataUnparsable,
        )
    except ImportError as e:
        print(f"ImportError: {e}")
        print("→ pip install youtube-transcript-api 실행 필요")
        return

    api = YouTubeTranscriptApi()

    for vid in test_ids[:6]:
        print(f"── {vid} ──────────────────────")
        try:
            tl = api.list(vid)
            print(f"  list() 성공")
            langs = [(t.language_code, t.is_generated) for t in tl]
            print(f"  자막 목록: {langs[:5]}")

            # ko 자막 fetch 시도
            try:
                tr = tl.find_manually_created_transcript(["ko"])
                fetched = tr.fetch()
                print(f"  수동 ko 자막 fetch 성공: {len(fetched.to_raw_data())}개 세그먼트")
            except NoTranscriptFound:
                try:
                    tr = tl.find_generated_transcript(["ko"])
                    fetched = tr.fetch()
                    print(f"  자동 ko 자막 fetch 성공: {len(fetched.to_raw_data())}개 세그먼트")
                except NoTranscriptFound:
                    print(f"  ko 자막 없음 (no_korean)")
            except PoTokenRequired as e:
                print(f"  PoTokenRequired: {e}")
            except Exception as e:
                print(f"  fetch() 실패: {type(e).__name__}: {str(e)[:120]}")

        except PoTokenRequired as e:
            print(f"  PoTokenRequired ← 이게 원인")
            print(f"  → YouTube가 bot 감지 토큰 요구. yt-dlp 전환 필요.")
        except IpBlocked as e:
            print(f"  IpBlocked ← 이게 원인")
            print(f"  → 현재 IP가 YouTube에 의해 차단됨. VPN 또는 다른 네트워크 시도.")
        except RequestBlocked as e:
            print(f"  RequestBlocked ← 이게 원인")
            print(f"  → YouTube가 자동화 요청을 차단. yt-dlp 전환 또는 쿨다운 필요.")
        except TranscriptsDisabled as e:
            print(f"  TranscriptsDisabled (영상이 자막 비활성화)")
        except VideoUnavailable as e:
            print(f"  VideoUnavailable")
        except VideoUnplayable as e:
            print(f"  VideoUnplayable")
        except YouTubeDataUnparsable as e:
            print(f"  YouTubeDataUnparsable: {str(e)[:120]}")
        except CouldNotRetrieveTranscript as e:
            print(f"  CouldNotRetrieveTranscript: {type(e).__name__}: {str(e)[:120]}")
        except Exception as e:
            print(f"  기타 예외: {type(e).__name__}: {str(e)[:120]}")
        print()

if __name__ == "__main__":
    main()
