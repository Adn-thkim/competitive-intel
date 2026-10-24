#!/usr/bin/env python3
"""reextract_dcinside_theqoo.py — dcinside·theqoo 캐시 본문 재추출·패치 (item 1 후속).

목적
----
개선된 추출 로직(사이트 셀렉터 + 모바일 UA + bodyless 구분)으로 dcinside·theqoo
게시글을 **강제 재추출**하고, `community_content_fetch` 캐시의 `output.body /
comments / fetch_status` 만 스키마에 맞게 교체한다. 동시에 각 URL의 **현재 수집
가능 여부(삭제 여부)** 를 확인해 보고한다.

- 기존 캐시 로직을 재사용하지 않고(캐시 단락 회피) 직접 재fetch → 재추출한다.
- 저장은 production `store_agent_output` 로 → 동일 cache_key 파일에 created_at 보존,
  output 만 교체(= 외과적 패치). 양성 캐시는 `_fetch_community_content` 에서 음성
  캐시보다 먼저 조회되므로, 여기 기록하면 기존 실패(negative) 판정도 자동 무효화된다.
- bodyless(짤·목록·초단문) 도 동일 처리: 실질 본문 0자면 fetch_status="empty_body",
  body="" 로 저장(제목·댓글이 신호 → 하류에서 살림).

안전
----
- 기본 **dry-run**(미저장). 실제 패치는 `--apply`.
- `--apply` 시 대상 URL의 기존 output 을 백업 JSON 으로 덤프(롤백용).
- 삭제(http_404·error_page) URL 은 패치하지 않고 목록만 보고.

실행 (사용자 머신)
------------------
    pip install requests trafilatura beautifulsoup4
    python scripts/reextract_dcinside_theqoo.py                    # dry-run 진단
    python scripts/reextract_dcinside_theqoo.py --apply            # 캐시 패치
    python scripts/reextract_dcinside_theqoo.py --sites theqoo.net --limit 20
"""
from __future__ import annotations
import argparse, json, shutil, sys, time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

# repo 루트를 sys.path 에 추가 — `python scripts/foo.py` 실행 시 server 패키지 인식.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 개선된 추출 로직(단일 출처) — production 노드에서 그대로 가져온다.
from server.graph.nodes.community_collection_node import (  # noqa: E402
    _fetch_target_v14, _extract_site_specific, _real_body_len, _sub_urls,
    _strip_embed_cards, _ERROR_PAGE_SIGNS, _NAV_SIGNS,
    _FETCH_AGENT_ID_V14, _FETCH_CACHE_CTX_V14,
)
from server.graph.agent_cache import store_agent_output, load_agent_output  # noqa: E402

_TIMEOUT = 15
_RATE = 1.0
_HOST_PARALLEL = 3
_TARGET_HOSTS_DEFAULT = ("dcinside.com", "theqoo.net")


def _load_targets(state_path: str, sites: tuple[str, ...]) -> dict[str, str]:
    """개선 대상 URL → 제목(real_len 판정용) 맵. reaction_state.community_posts 기준."""
    st = json.load(open(state_path, encoding="utf-8"))
    out: dict[str, str] = {}
    for p in st.get("community_posts") or []:
        u = p.get("url") or ""
        if not u:
            continue
        host = urlparse(u).netloc
        if any(s in host for s in sites) and u not in out:
            out[u] = p.get("title") or p.get("page_title") or ""
    return out


def _reextract(url: str, title: str) -> dict:
    """강제 재fetch+재추출 → {status, body, comments, deleted, note}."""
    target, ua = _fetch_target_v14(url)
    try:
        r = requests.get(target, timeout=_TIMEOUT, headers={"User-Agent": ua})
    except requests.Timeout:
        return {"status": "timeout", "deleted": False, "body": "", "comments": ""}
    except Exception as exc:  # noqa: BLE001
        return {"status": f"error:{type(exc).__name__}", "deleted": False, "body": "", "comments": ""}
    if r.status_code == 404:
        return {"status": "http_404", "deleted": True, "body": "", "comments": ""}
    if r.status_code >= 400:
        return {"status": f"http_{r.status_code}", "deleted": r.status_code in (410,),
                "body": "", "comments": ""}

    # 사이트 셀렉터 → trafilatura 폴백 (노드와 동일 순서)
    body, comments = _extract_site_specific(r.text, url)
    if not body:
        import trafilatura
        try:
            doc = trafilatura.bare_extraction(r.text, include_comments=True,
                                              url=target, favor_precision=True)
        except Exception:  # noqa: BLE001
            doc = None
        if doc is not None:
            body = getattr(doc, "text", "") or (doc.get("text", "") if isinstance(doc, dict) else "")
            comments = getattr(doc, "comments", "") or (doc.get("comments", "") if isinstance(doc, dict) else "")

    # bare URL → [링크] 치환 → OG 미리보기 카드 제거 (노드와 동일 정규화).
    body = _strip_embed_cards(_sub_urls(body or ""))
    comments = _sub_urls(comments or "")

    if any(s in body[:300] for s in _ERROR_PAGE_SIGNS):
        return {"status": "error_page", "deleted": True, "body": "", "comments": ""}
    if any(s in body[:100] for s in _NAV_SIGNS):
        return {"status": "nav_contaminated", "deleted": False, "body": "", "comments": comments}

    # bodyless 구분 — 노드 _collect_one 과 동일 기준(실질 본문 <1자 = empty_body).
    real_len = _real_body_len(body or "", title)
    if real_len < 1:
        return {"status": "empty_body", "deleted": False, "body": "", "comments": comments or "",
                "note": "bodyless"}
    return {"status": "ok", "deleted": False, "body": body or "", "comments": comments or "",
            "note": ("thin" if real_len < 15 else "")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="data/debug/reaction_state.json")
    ap.add_argument("--sites", default=",".join(_TARGET_HOSTS_DEFAULT),
                    help="쉼표구분 대상 사이트 host 부분일치")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--apply", action="store_true", help="실제 캐시 패치(미지정=dry-run)")
    ap.add_argument("--backup", default="data/debug/reextract_backup.json")
    ap.add_argument("--backup-dir", default=str(ROOT / "backup"),
                    help="--apply 전 기존 캐시 폴더 전체를 복제 백업할 위치 (기본 ./backup)")
    ap.add_argument("--report", default="data/debug/reextract_report.json")
    args = ap.parse_args()

    sites = tuple(s.strip() for s in args.sites.split(","))
    targets = _load_targets(args.state, sites)
    urls = list(targets)
    if args.limit:
        urls = urls[:args.limit]
    print(f"개선 대상 {len(urls)} URL (sites={sites}) · {'APPLY' if args.apply else 'DRY-RUN'}", flush=True)

    # host 병렬 + host내 직렬 rate limit
    by_host: dict[str, list[str]] = defaultdict(list)
    for u in urls:
        by_host[urlparse(u).netloc].append(u)

    results: dict[str, dict] = {}
    from concurrent.futures import ThreadPoolExecutor

    def run_host(hurls):
        o = {}
        for u in hurls:
            o[u] = _reextract(u, targets[u])
            time.sleep(_RATE)
        return o
    with ThreadPoolExecutor(max_workers=_HOST_PARALLEL) as pool:
        for part in pool.map(run_host, by_host.values()):
            results.update(part)

    # 분류
    deleted = [u for u, r in results.items() if r["deleted"]]
    ok = [u for u, r in results.items() if r["status"] == "ok"]
    empty = [u for u, r in results.items() if r["status"] == "empty_body"]
    failed = [u for u, r in results.items()
              if r["status"] not in ("ok", "empty_body") and not r["deleted"]]

    print(f"\n=== fetch 가능성 ===")
    print(f"  수집가능(ok+empty_body) {len(ok)+len(empty)} · 삭제/부재 {len(deleted)} · 기타실패 {len(failed)}")
    st = defaultdict(lambda: [0, 0])
    for u, r in results.items():
        s = urlparse(u).netloc.replace("m.", "").replace("www.", "")
        st[s][0] += 1
        if r["deleted"]:
            st[s][1] += 1
    for s, (t, d) in sorted(st.items()):
        print(f"    {s:20} 총 {t:3} · 삭제 {d:3} · 수집가능 {100*(t-d)/t:5.1f}%")
    if deleted:
        print(f"\n  삭제 추정 URL {len(deleted)}건 (패치 제외):")
        for u in deleted[:15]:
            print("    ", results[u]["status"], u)

    # 패치 대상 = 수집가능(ok/empty_body). 기존 캐시 output 과 비교.
    patch = ok + empty
    print(f"\n=== 캐시 패치 대상 {len(patch)}건 (ok {len(ok)} · empty_body {len(empty)}) ===")

    # --apply 전 기존 캐시 폴더 전체를 ./backup 에 복제(롤백용). 어떤 write 보다 먼저.
    if args.apply:
        _cache_dir = ROOT / "data/cache/agent_outputs" / _FETCH_AGENT_ID_V14
        _ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        _bdir = Path(args.backup_dir) / f"{_FETCH_AGENT_ID_V14}_{_ts}"
        _bdir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(_cache_dir, _bdir)
        print(f"[백업] 기존 캐시 복제 → {_bdir} ({len(list(_bdir.glob('*.json')))} 파일)")

    backup = {}
    changed = 0
    for u in patch:
        old = load_agent_output(agent_id=_FETCH_AGENT_ID_V14, cache_input={"url": u},
                                context=_FETCH_CACHE_CTX_V14, ttl_hours=10**9) or {}
        old_body = (old.get("body") or "")
        new = results[u]
        if old_body.strip() == new["body"].strip() and old.get("fetch_status") == new["status"]:
            continue    # 변화 없음 → 스킵
        changed += 1
        backup[u] = {"body": old_body, "comments": old.get("comments", ""),
                     "fetch_status": old.get("fetch_status", "")}
        if len(backup) <= 5:
            print(f"  [{new['status']:11}] {u}")
            print(f"      old {len(old_body):5}자 | {old_body[:70].replace(chr(10),' ')!r}")
            print(f"      new {len(new['body']):5}자 | {new['body'][:70].replace(chr(10),' ')!r}")
        if args.apply:
            store_agent_output(
                agent_id=_FETCH_AGENT_ID_V14, cache_input={"url": u},
                context=_FETCH_CACHE_CTX_V14,
                output={"body": new["body"], "comments": new["comments"],
                        "fetch_status": new["status"]})

    print(f"\n변경 필요 {changed}건 · {'패치 완료' if args.apply else 'DRY-RUN(미저장)'}")
    if args.apply and backup:
        json.dump(backup, open(args.backup, "w"), ensure_ascii=False, indent=1)
        print(f"백업(롤백용): {args.backup}")
    json.dump({"results": results, "deleted": deleted, "changed": changed},
              open(args.report, "w"), ensure_ascii=False, indent=1)
    print(f"리포트: {args.report}")
    if not args.apply:
        print("\n→ 확인 후 실제 패치: python scripts/reextract_dcinside_theqoo.py --apply")
    return 0


if __name__ == "__main__":
    sys.exit(main())
