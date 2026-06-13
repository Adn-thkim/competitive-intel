"""
scripts/probe_comments.py — 댓글 수집 가능성 진단 (일회성, CE-D6 2단계 근거)
============================================================================
질문: theqoo·ppomppu 의 댓글이 trafilatura 로 안 잡히는 이유가
  (A) 초기 HTML 에 댓글이 없음 (XHR 동적 로딩) → 어떤 정적 파서로도 불가
  (B) HTML 에는 있으나 trafilatura 댓글 휴리스틱이 못 잡음 → 전용 파서로 해결 가능
중 무엇인지 원본 HTML 로 확정한다.

진단 방법
---------
각 URL 의 원본 HTML 을 받아(브라우저/모바일 UA — measure_brave_recall 과 동일 정책):
1. html 길이, "댓글" 문자열 등장 횟수
2. 댓글 컨테이너 후보 (class/id 에 comment·cmt·reply·fdb 포함) 노드 수
3. 후보 노드의 텍스트 표본 출력 → 실제 댓글 문장이 보이면 (B), 비어 있으면 (A)
4. trafilatura include_comments 추출 결과와 대조

사용:  python scripts/probe_comments.py <URL> [<URL> ...]
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from measure_brave_recall import UA, _fetch_target  # noqa: E402 — UA 정책 재사용

_MARKER_RE = re.compile(r"댓글")
_CONTAINER_RE = re.compile(
    r"""(?:class|id)\s*=\s*["'][^"']*(comment|cmt|reply|fdb|memo)[^"']*["']""",
    re.IGNORECASE)


def probe(url: str) -> None:
    target, ua = _fetch_target(url)
    print(f"\n{'=' * 70}\nURL    : {url}")
    if target != url:
        print(f"변환    : {target}")
    try:
        resp = requests.get(target, timeout=10, headers={"User-Agent": ua})
    except Exception as exc:  # noqa: BLE001
        print(f"fetch 실패: {exc}")
        return
    html = resp.text
    print(f"HTTP   : {resp.status_code} · html {len(html):,}자")
    print(f"'댓글' 등장: {len(_MARKER_RE.findall(html))}회 · "
          f"댓글 컨테이너 후보 속성: {len(_CONTAINER_RE.findall(html))}개")

    # 후보 노드 텍스트 표본 (BeautifulSoup 가능 시)
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        nodes = soup.select(
            "[class*=comment], [id*=comment], [class*=cmt], [class*=reply], "
            "[class*=fdb], [class*=memo]")
        texts = []
        for n in nodes:
            t = n.get_text(" ", strip=True)
            if len(t) >= 10:
                texts.append(t)
        print(f"텍스트 보유 후보 노드: {len(texts)}개")
        for t in texts[:3]:
            print(f"  표본: {t[:120]}")
        if not texts:
            print("  → 후보 노드에 텍스트 없음 (A: 동적 로딩 가능성 높음)")
        else:
            print("  → 댓글 텍스트가 HTML 에 존재 (B: 전용 파서로 수집 가능)")
    except ImportError:
        print("(bs4 미설치 — 컨테이너 속성 수만 표시)")

    # trafilatura 대조
    try:
        import trafilatura
        doc = trafilatura.bare_extraction(html, include_comments=True, url=target)
        cm = (getattr(doc, "comments", "") or
              (doc.get("comments", "") if isinstance(doc, dict) else "")) if doc else ""
        print(f"trafilatura comments: {len(cm)}자")
    except Exception as exc:  # noqa: BLE001
        print(f"trafilatura 오류: {exc}")
    time.sleep(1.0)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("사용: python scripts/probe_comments.py <URL> [<URL> ...]")
    for u in sys.argv[1:]:
        probe(u)
