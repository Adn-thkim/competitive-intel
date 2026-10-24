#!/usr/bin/env python3
"""
scripts/seed_url_discovery_official_cache.py
--------------------------------------------
url_discovery_official 노드 결과 캐시(2026-10-23 신설)를 **기존 캐시만으로** 시드한다.

배경
  노드 캐시 신설 전에는 공식 URL 선정이 실행마다 흔들려(as_completed 순서 → 상한 컷)
  feature_mapping_official 의 url_fingerprint 가 매번 달라졌다. 노드 캐시만 추가하면
  첫 실행이 또 새로운 선정을 만들어 기존 하류 캐시(feature_mapping_official →
  official_content_collection → comparison_matrix)를 놓친다.
  이 스크립트는 "특정 과거 실행"의 feature_mapping_official 캐시 엔트리에 기록된
  url_fingerprint 를 정답 URL 집합으로 삼아, 노드 출력 형태로 복원해 노드 캐시에 심는다.
  → 다음 실행에서 url_discovery_official 이 그 집합을 그대로 내놓아 하류 캐시가 히트한다.

복원 재료 (LLM·네트워크 호출 없음)
  - url_fingerprint          : data/cache/agent_outputs/feature_mapping_official/{key}.json
  - Brave site: 검색 결과     : data/cache/agent_outputs/url_discovery_brave/  (title·description·키워드)
  - carry(공식 URL 영구 캐시) : data/cache/official_sources.json

사용
  python scripts/seed_url_discovery_official_cache.py --list
  python scripts/seed_url_discovery_official_cache.py --from-key 5e8b2f80d1 --dry-run
  python scripts/seed_url_discovery_official_cache.py --from-key 5e8b2f80d1
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from server.config import AGENT_OUTPUT_CACHE_DIR, CACHE_DIR  # noqa: E402
from server.graph.agent_cache import (  # noqa: E402
    load_agent_output, make_cache_key, store_agent_output)
from server.graph.nodes.url_discovery_official_node import (  # noqa: E402
    _NODE_CACHE_AGENT_ID, _NODE_CACHE_CONTEXT, _OFFICIAL_SUBPAGE_KEYWORDS, node_cache_input)

_MATCHED = ["comparison_matrix", "battlecard", "market_context_swot"]
_SITE_RE = re.compile(r"^(?P<prefix>.*)\s+site:(?P<domain>\S+)$")


def _read(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None   # iCloud 미다운로드(EDEADLK) 등


def _fm_entries() -> list[dict]:
    out = []
    for f in (AGENT_OUTPUT_CACHE_DIR / "feature_mapping_official").glob("*.json"):
        e = _read(f)
        if e and "url_fingerprint" in (e.get("input") or {}):
            out.append(e)
    return sorted(out, key=lambda e: e.get("updated_at", ""))


def _host_ok(url: str, domain: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    domain = domain.lower().lstrip(".")
    return host == domain or host.endswith("." + domain)


def _brave_index() -> tuple[dict[str, list[dict]], int]:
    """url → [{query, keyword, title, description, domain}] (site: 한정 쿼리만)."""
    idx: dict[str, list[dict]] = defaultdict(list)
    unread = 0
    for f in (AGENT_OUTPUT_CACHE_DIR / "url_discovery_brave").rglob("*.json"):
        e = _read(f)
        if e is None:
            unread += 1
            continue
        q = (e.get("input") or {}).get("query", "")
        m = _SITE_RE.match(q)
        if not m:
            continue
        prefix, domain = m.group("prefix"), m.group("domain")
        keyword = next((kw for kw in _OFFICIAL_SUBPAGE_KEYWORDS if prefix.endswith(" " + kw)), "hint")
        for r in (e.get("output") or {}).get("results", []) or []:
            url = (r.get("url") or "").strip()
            if url and _host_ok(url, domain):
                idx[url].append({
                    "query": q, "keyword": keyword, "domain": domain,
                    "title": (r.get("title") or "").strip(),
                    "description": (r.get("description") or "").strip(),
                })
    return idx, unread


def _carry_index() -> dict[str, set[str]]:
    store = _read(CACHE_DIR / "official_sources.json") or {}
    carry: dict[str, set[str]] = defaultdict(set)
    for cid, e in (store.get("entries") or {}).items():
        for u in (e.get("official_urls") or []) + ([e["primary_url"]] if e.get("primary_url") else []):
            if u:
                carry[cid].add(u)
        for ref in e.get("reference_sources") or []:
            if ref.get("validated"):
                u = ref.get("final_url") or ref.get("url")
                if u:
                    carry[cid].add(u)
    return carry


def _rebuild(fp: dict[str, list[str]], brave, carry) -> tuple[dict, list]:
    result: dict[str, list[dict]] = {}
    unresolved: list[tuple[str, str]] = []
    static_rank = {kw: i for i, kw in enumerate(_OFFICIAL_SUBPAGE_KEYWORDS)}
    for cid, urls in sorted(fp.items()):
        carried, subs = [], []
        for url in sorted(urls):
            if url in carry.get(cid, ()):
                carried.append({"url": url, "page_title": "", "meta_description": "",
                                "origin": "official_source", "subpage_category": "",
                                "matched_report_types": list(_MATCHED)})
            elif url in brave:
                hit = sorted(brave[url], key=lambda h: (static_rank.get(h["keyword"], 99), h["query"]))[0]
                subs.append({"url": url, "page_title": hit["title"],
                             "meta_description": hit["description"],
                             "origin": "official_subpage", "subpage_category": hit["keyword"],
                             "matched_report_types": list(_MATCHED)})
            else:
                unresolved.append((cid, url))
        result[cid] = carried + subs
    return result, unresolved


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="시드 가능한 feature_mapping_official 엔트리 목록")
    ap.add_argument("--from-key", help="feature_mapping_official cache_key 접두(8자 이상 권장)")
    ap.add_argument("--dry-run", action="store_true", help="복원 결과만 출력하고 저장하지 않음")
    ap.add_argument("--force", action="store_true", help="이미 노드 캐시가 있어도 덮어씀")
    a = ap.parse_args()

    entries = _fm_entries()
    if a.list or not a.from_key:
        print("feature_mapping_official 엔트리 (updated_at UTC | key | candidate별 URL 수)")
        for e in entries:
            fp = e["input"]["url_fingerprint"]
            print(f"  {e['updated_at'][:19]} | {e['cache_key'][:10]} | "
                  + ", ".join(f"{k}={len(v)}" for k, v in sorted(fp.items())))
        return 0

    matches = [e for e in entries if e["cache_key"].startswith(a.from_key)]
    if len(matches) != 1:
        print(f"--from-key {a.from_key!r} 일치 {len(matches)}건 — 정확히 1건이어야 합니다.")
        return 2
    src = matches[0]
    inp = src["input"]
    fp = inp["url_fingerprint"]

    brave, unread = _brave_index()
    carry = _carry_index()
    rebuilt, unresolved = _rebuild(fp, brave, carry)
    if unread:
        print(f"[참고] 읽지 못한 Brave 캐시 {unread}건(iCloud 미다운로드 등) — 복원에 필요한 URL 이 빠지면 아래 unresolved 로 표시")
    if unresolved:
        print(f"[중단] 복원 불가 URL {len(unresolved)}건 — 시드하지 않습니다:")
        for cid, u in unresolved[:20]:
            print("   ", cid, u)
        return 3

    # 검증: 복원 결과의 URL 집합 == url_fingerprint
    for cid, urls in fp.items():
        got = sorted(u["url"] for u in rebuilt.get(cid, []))
        assert got == sorted(urls), f"{cid}: URL 집합 불일치"

    cache_input = node_cache_input(inp["domain"], {"brand": inp["own_product"]["brand"],
                                                   "name": inp["own_product"]["product_name"]},
                                   inp["candidate_ids"])
    node_key = make_cache_key(_NODE_CACHE_AGENT_ID, cache_input, _NODE_CACHE_CONTEXT)
    print(f"source  : feature_mapping_official {src['cache_key'][:10]} (updated_at {src['updated_at'][:19]})")
    print(f"node key: {node_key[:10]}  input={json.dumps(cache_input, ensure_ascii=False)}")
    for cid, items in rebuilt.items():
        n_carry = sum(1 for u in items if u["origin"] == "official_source")
        print(f"  {cid}: {len(items)} URL (carry {n_carry} + subpage {len(items) - n_carry})")

    if a.dry_run:
        print("dry-run — 저장하지 않음")
        return 0
    existing = load_agent_output(agent_id=_NODE_CACHE_AGENT_ID, cache_input=cache_input,
                                 context=_NODE_CACHE_CONTEXT)
    if existing and not a.force:
        print(f"이미 노드 캐시 존재(selected_at={existing.get('selected_at')}, origin={existing.get('origin')}) — --force 로 덮어쓰기")
        return 4
    store_agent_output(
        agent_id=_NODE_CACHE_AGENT_ID, cache_input=cache_input, context=_NODE_CACHE_CONTEXT,
        output={"official_urls_by_candidate": rebuilt,
                "selected_at": src["updated_at"],
                "origin": f"seed:feature_mapping_official/{src['cache_key'][:16]}"},
    )
    print(f"저장 완료 → {AGENT_OUTPUT_CACHE_DIR / _NODE_CACHE_AGENT_ID / (node_key + '.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
