#!/usr/bin/env python3
"""postclean_community_bodies.py — dcinside/theqoo 외 5개 사이트 body 노이즈 제거.

clien·ppomppu·fmkorea·mlbpark·milemoa 는 추출 자체는 정상(실패율 clien/ppomppu 0%)이고
남은 건 텍스트 레벨 노이즈뿐이라, **재fetch 없이** 기존 state body 를 정제한다.
(dcinside/theqoo 는 엉뚱한 DOM 추출 → reextract_dcinside_theqoo.py 로 재fetch 필요.)

정제 파이프라인
--------------
1) 스테일 '[댓글]…' 병합 꼬리 제거(댓글 정본은 community_comments 로 별도 로드)
2) HTML 태그 제거(<strong> 등)
3) 제목 접두 제거(body 가 제목으로 시작하면 — fmkorea/mlbpark/milemoa)
4) 사이트 크롬 제거(_CHROME_RE: 에펨 브레드크럼 · MLBPARK 푸터 · 마일모아/affiliate · 클리앙)
5) URL → [링크] + OG 링크/영상 미리보기 카드 제거(노드와 동일)
6) bodyless 판별 → 실질 본문 <1자면 empty_body(body="")

산출
----
postclean_report.json (reextract_report.json 과 동일 스키마) → patch_state_bodies.py 가 소비.
--apply 시 content_fetch 캐시 body 도 패치(+백업).

실행
----
    python scripts/postclean_community_bodies.py            # dry-run(리포트만 생성)
    python scripts/postclean_community_bodies.py --apply    # 캐시 패치 + 백업
"""
from __future__ import annotations
import argparse
import json
import re
import shutil
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.graph.nodes.community_collection_node import (  # noqa: E402
    _sub_urls, _strip_embed_cards, _real_body_len, _CHROME_RE,
    _FETCH_AGENT_ID_V14, _FETCH_CACHE_CTX_V14,
)
from server.graph.agent_cache import store_agent_output, load_agent_output  # noqa: E402

# ppomppu 는 짧은 본문 오추출(favor_precision) 문제로 재fetch 대상 → reextract 로 이관.
# 여기(text-only 정제)는 추출이 정상인 4개 사이트만.
_SITES = ("clien.net", "fmkorea.com", "mlbpark.donga.com", "milemoa.com")


def _clean(raw: str, title: str) -> str:
    b = raw or ""
    b = re.split(r"\n*\[댓글\]", b)[0]              # ① 스테일 댓글 병합 제거
    b = re.sub(r"<[^>]+>", "", b).strip()           # ② HTML 태그
    if title and b.startswith(title):               # ③ 제목 접두 제거
        b = b[len(title):]
    b = _CHROME_RE.sub("", b)                        # ④ 사이트 크롬
    b = _strip_embed_cards(_sub_urls(b))            # ⑤ URL→[링크] + OG카드
    return re.sub(r"[ \t]*\n[ \t]*", "\n", b).strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=str(ROOT / "data/debug/reaction_state.json"))
    ap.add_argument("--sites", default=",".join(_SITES))
    ap.add_argument("--report", default=str(ROOT / "data/debug/postclean_report.json"))
    ap.add_argument("--backup-dir", default=str(ROOT / "backup"))
    ap.add_argument("--apply", action="store_true",
                    help="content_fetch 캐시 body 패치(+백업). 미지정=리포트만 생성")
    args = ap.parse_args()
    sites = tuple(s.strip() for s in args.sites.split(","))

    st = json.load(open(args.state, encoding="utf-8"))
    by = {}
    for p in st.get("community_posts") or []:
        u = p.get("url") or ""
        if not any(s in urlparse(u).netloc for s in sites):
            continue
        by.setdefault(u, {"title": p.get("title") or "", "chunks": {}})
        by[u]["chunks"][p.get("chunk_index") or 0] = (p.get("body_excerpt") or "")

    def raw(u):
        c = by[u]["chunks"]
        return "\n".join(c[k] for k in sorted(c))

    results, n_empty, n_thin = {}, 0, 0
    for u, p in by.items():
        cleaned = _clean(raw(u), p["title"])
        rl = _real_body_len(cleaned, p["title"])
        if rl < 1:
            results[u] = {"status": "empty_body", "body": "", "comments": "",
                          "deleted": False, "note": "bodyless"}
            n_empty += 1
        else:
            note = "thin" if rl < 15 else ""
            results[u] = {"status": "ok", "body": cleaned, "comments": "",
                          "deleted": False, "note": note}
            if note:
                n_thin += 1

    sc = Counter(urlparse(u).netloc.replace("m.", "").replace("www.", "") for u in results)
    print(f"정제 대상 {len(results)} URL · empty_body {n_empty} · thin {n_thin}")
    print(f"  사이트: {dict(sc)}")
    print("표본(before→after):")
    for u in list(results)[:5]:
        print(f"  [{urlparse(u).netloc[:17]:17}] OLD {raw(u).replace(chr(10),' ')[:66]!r}")
        print(f"  {'':19}   NEW[{results[u]['status']}] {results[u]['body'].replace(chr(10),' ')[:60]!r}")

    json.dump({"results": results}, open(args.report, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"[리포트] {args.report}")

    if args.apply:
        cache_dir = ROOT / "data/cache/agent_outputs" / _FETCH_AGENT_ID_V14
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        bdir = Path(args.backup_dir) / f"{_FETCH_AGENT_ID_V14}_postclean_{ts}"
        bdir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(cache_dir, bdir)
        print(f"[백업] 기존 캐시 복제 → {bdir}")
        patched = 0
        for u, r in results.items():
            old = load_agent_output(agent_id=_FETCH_AGENT_ID_V14, cache_input={"url": u},
                                    context=_FETCH_CACHE_CTX_V14, ttl_hours=10**9) or {}
            store_agent_output(agent_id=_FETCH_AGENT_ID_V14, cache_input={"url": u},
                               context=_FETCH_CACHE_CTX_V14,
                               output={"body": r["body"], "comments": old.get("comments", ""),
                                       "fetch_status": r["status"]})
            patched += 1
        print(f"[적용] content_fetch 캐시 {patched}건 body 패치")
    else:
        print("DRY-RUN(캐시 미패치) — 리포트는 생성됨. 적용: --apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
