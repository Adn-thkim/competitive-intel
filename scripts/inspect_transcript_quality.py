#!/usr/bin/env python3
"""
scripts/inspect_transcript_quality.py
--------------------------------------
자막 원문 품질 점검 스크립트.

- 실제 텍스트 출력 (video별 앞 500자)
- 품질 지표: 길이 / 문장 완성도 / 반복 비율 / ABSA 관련 키워드 히트율
- [ko 자동생성] 자막의 일반적 노이즈 패턴 탐지

실행
----
  cd competitive-intel
  python -m scripts.inspect_transcript_quality
"""
from __future__ import annotations
import json, re, sys
from pathlib import Path
from collections import Counter

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

TEST_IDS = [
    "b_LmOdeKA7Q",
    "ku0HsHsIdQ8",
    "tgmO91q-KLk",
    "wV5de5kmc_k",
    "MiU4AMsW2uQ",
    "KyL7GUMp_rw",
]

ASPECT_KW: dict[str, list[str]] = {
    "overseas_payment":  ["해외결제", "해외 결제", "결제", "페이", "비자", "마스터"],
    "exchange_rate":     ["환율", "수수료", "환전", "달러", "엔화", "원화"],
    "atm":               ["ATM", "atm", "현금인출", "출금", "현금"],
    "app_ux":            ["앱", "어플", "토스", "UI", "로그인"],
    "pricing":           ["수수료", "무료", "유료", "요금", "연회비"],
    "travel_benefit":    ["혜택", "포인트", "캐시백", "마일", "라운지"],
    "fx_reload":         ["충전", "환전하기", "잔액", "이체", "입금"],
}

# 노이즈 패턴: 자동생성 자막에 자주 등장하는 무의미 세그먼트
_NOISE_RE = re.compile(
    r'^[♪\s]+$|'                        # 음악 기호만
    r'^\[.*?\]$|'                        # [음악], [박수] 등 메타태그
    r'^[ㅋㅎㅠㅜ\s]+$|'                  # 웃음·울음 의성어만
    r'^\s*$'
)

def _fetch(video_id: str):
    from youtube_transcript_api import (
        YouTubeTranscriptApi, NoTranscriptFound, TranscriptsDisabled,
        PoTokenRequired, IpBlocked, RequestBlocked,
    )
    api = YouTubeTranscriptApi()
    try:
        tl = api.list(video_id)
        for finder, label in (
            (tl.find_manually_created_transcript, "ok_manual"),
            (tl.find_generated_transcript,        "ok_generated"),
        ):
            try:
                tr = finder(["ko"])
                entries = tr.fetch().to_raw_data()
                return entries, label
            except NoTranscriptFound:
                continue
        return [], "no_korean"
    except TranscriptsDisabled:
        return [], "disabled"
    except PoTokenRequired:
        return [], "po_token_required"
    except (IpBlocked, RequestBlocked) as e:
        return [], f"blocked:{type(e).__name__}"
    except Exception as e:
        return [], f"error:{type(e).__name__}"


def _analyze(entries: list[dict]) -> dict:
    texts = [e["text"] for e in entries if e.get("text")]
    clean = [t for t in texts if not _NOISE_RE.match(t)]
    full_text = " ".join(clean)

    # 반복 비율: 동일 구절(3-gram) 중복 비율
    words = full_text.split()
    trigrams = [" ".join(words[i:i+3]) for i in range(len(words)-2)]
    counter = Counter(trigrams)
    repeat_ratio = (
        sum(v - 1 for v in counter.values() if v > 1) / max(len(trigrams), 1)
    )

    # ABSA 키워드 히트
    aspect_hits = {
        aid: sum(1 for kw in kws if kw in full_text)
        for aid, kws in ASPECT_KW.items()
    }

    return {
        "total_segments":  len(entries),
        "clean_segments":  len(clean),
        "noise_segments":  len(entries) - len(clean),
        "total_chars":     len(full_text),
        "repeat_ratio":    round(repeat_ratio, 3),
        "aspect_hits":     aspect_hits,
        "any_aspect_hit":  any(v > 0 for v in aspect_hits.values()),
        "full_text":       full_text,
    }


def main():
    for vid in TEST_IDS:
        entries, status = _fetch(vid)
        print(f"\n{'═'*65}")
        print(f"  {vid}  [{status}]")

        if not entries:
            print(f"  자막 없음 또는 실패")
            continue

        a = _analyze(entries)
        print(f"  세그먼트: {a['total_segments']}개  노이즈 제거 후: {a['clean_segments']}개"
              f"  총 {a['total_chars']}자  반복률: {a['repeat_ratio']:.1%}")

        # aspect 히트
        hits = [(k, v) for k, v in a["aspect_hits"].items() if v > 0]
        if hits:
            print(f"  aspect 히트: {', '.join(f'{k}({v})' for k, v in hits)}")
        else:
            print(f"  aspect 히트: 없음 (ABSA 입력 가치 낮음)")

        # 원문 앞 600자
        preview = a["full_text"][:600]
        print(f"\n  ── 원문 미리보기 (600자) ──")
        # 80자 단위로 출력
        for i in range(0, len(preview), 80):
            print(f"  {preview[i:i+80]}")

        # 노이즈 샘플
        noise = [e["text"] for e in entries if _NOISE_RE.match(e.get("text",""))]
        if noise:
            print(f"\n  ── 노이즈 샘플 ({len(noise)}건 중 최대 5건) ──")
            for n in noise[:5]:
                print(f"    • {repr(n)}")

    print(f"\n{'═'*65}")


if __name__ == "__main__":
    main()
