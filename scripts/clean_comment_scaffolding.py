#!/usr/bin/env python3
"""clean_comment_scaffolding.py — community_comments 댓글 텍스트의 스캐폴딩 정제.

ppomppu Playwright 댓글은 text 필드에 '좋아요수 \\n 싫어요수 \\n 닉네임 \\n 내용 \\n 날짜'가
통째로 담겨 있다(예: "0\\n 0\\ndtddtd\\n신용카드를…\\n2023-08-11"). 투표수·닉네임·날짜
스캐폴딩을 제거하고 **댓글 내용만** 남긴다. 스캐폴딩이 아닌 댓글(dcinside/theqoo/clien 등)은
그대로 통과한다.

- community_comments 캐시를 **외과적 in-place 패치**(items[].text 만 정제, 다른 필드 불변).
- 적용 전 캐시 폴더를 ./backup 에 복제 백업.
- 기본 dry-run(미저장), --apply 로 반영.

실행
----
    python scripts/clean_comment_scaffolding.py            # dry-run
    python scripts/clean_comment_scaffolding.py --apply    # 백업 후 캐시 패치
"""
from __future__ import annotations
import argparse
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
_CACHE = ROOT / "data/cache/agent_outputs/community_comments"
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def clean_comment(text: str) -> str:
    """'투표수 \\n 투표수 \\n 닉네임 \\n 내용 \\n 날짜' → 내용만. 스캐폴딩 아니면 원문 유지."""
    toks = [l.strip() for l in (text or "").split("\n")]
    # 스캐폴딩 판별: 첫 줄이 순수 숫자(좋아요 수) — 아니면 정제 대상 아님
    if not (len(toks) >= 3 and re.fullmatch(r"\d+", toks[0] or "")):
        return (text or "").strip()
    i = 0
    while i < len(toks) and re.fullmatch(r"\d+", toks[i] or ""):  # 투표수 줄들 skip
        i += 1
    if i < len(toks):                                            # 닉네임 줄 skip
        i += 1
    rest = toks[i:]
    while rest and _DATE.fullmatch(rest[-1] or ""):             # 끝 날짜 줄 제거
        rest.pop()
    return " ".join(x for x in rest if x).strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sites", default="ppomppu.co.kr",
                    help="정제 대상 사이트(쉼표). 기본 ppomppu(유일한 스캐폴딩 도메인)")
    ap.add_argument("--backup-dir", default=str(ROOT / "backup"))
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    sites = tuple(s.strip() for s in args.sites.split(","))

    files = list(_CACHE.glob("*.json"))
    n_url = n_item = n_changed = 0
    samples = []
    to_write = []
    for f in files:
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        u = (d.get("input") or {}).get("url") or ""
        if not any(s in urlparse(u).netloc for s in sites):
            continue
        items = (d.get("output") or {}).get("items") or []
        if not items:
            continue
        n_url += 1
        changed_here = False
        for it in items:
            old = it.get("text") or ""
            new = clean_comment(old)
            n_item += 1
            if new != old.strip():
                n_changed += 1
                changed_here = True
                if len(samples) < 6:
                    samples.append((old, new))
            it["text"] = new
        if changed_here:
            to_write.append((f, d))

    print(f"대상 사이트 {sites} · 댓글보유 URL {n_url} · 총 댓글 {n_item} · 정제된 댓글 {n_changed}")
    print("표본(before → after):")
    for old, new in samples:
        print(f"  OLD: {old.replace(chr(10),' / ')[:75]!r}")
        print(f"  NEW: {new[:75]!r}")

    if args.apply:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        bdir = Path(args.backup_dir) / f"community_comments_{ts}"
        bdir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(_CACHE, bdir)
        print(f"[백업] 기존 댓글 캐시 복제 → {bdir}")
        for f, d in to_write:
            json.dump(d, open(f, "w", encoding="utf-8"), ensure_ascii=False)
        print(f"[적용] community_comments 캐시 {len(to_write)}개 파일 패치")
    else:
        print(f"DRY-RUN(미저장) — 적용: --apply (패치 대상 파일 {len(to_write)}개)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
