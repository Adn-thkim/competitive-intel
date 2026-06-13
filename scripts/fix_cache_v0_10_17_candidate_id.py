#!/usr/bin/env python3
"""
scripts/fix_cache_v0_10_17_candidate_id.py
-------------------------------------------
v0.10.17 — comp_토스트래블카드(잘못된 candidate_id) → comp_트래블월렛 정정.

배경
----
competitor_discovery 가 "트래블월렛 카드" 를 정상적으로 `comp_트래블월렛` 로 발견했으나,
normalize_competitor_ids 단계에서 LLM 의 slug 정규화가 자사명 "토스 트래블카드" 와
충돌하여 `comp_토스트래블카드` 로 잘못 생성된 것으로 추정됨. 결과적으로 자사
own_토스트래블카드 와 candidate_id 가 동일한 prefix 만 다른 형태로 공존하여 분석
정확도 저하.

본 스크립트는 다음 캐시 파일들에서 도메인이 `travel-wallet.com` 인 URL 을 갖는
`comp_토스트래블카드` candidate_id 를 `comp_트래블월렛` 으로 정정한다:

  - data/cache/agent_outputs/feature_url_mapper.json (output.features[*].candidate_coverage[*].candidate_id)
  - data/cache/agent_outputs/feature_url_mapper.json (input.candidate_ids 의 리스트)
  - 이미 마이그레이션된 v0.10.13 A-4 cache_input 엔트리 포함

검증 기준
---------
1. candidate_coverage 의 existing_urls / additional_urls 중 1개 이상이 travel-wallet.com 도메인
2. 그리고 candidate_id 가 정확히 'comp_토스트래블카드' (자사 own_토스트래블카드 와 구분)
   → 위 조건을 모두 만족하는 candidate 만 'comp_트래블월렛' 으로 정정

옛 엔트리는 그대로 유지하고 새 엔트리만 신규 cache_key 로 저장하는 마이그레이션 패턴
대신, 본 케이스는 단순 라벨링 오류이므로 직접 수정 후 cache_key 재계산하여 신규 키로 저장한다.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from server.graph.agent_cache import (  # noqa: E402
    _cache_path,
    _read_cache,
    _write_cache,
    make_cache_key,
)
from server.graph.nodes.feature_mapping_llm_node import (  # noqa: E402
    _make_stable_cache_input,
)


_OLD_CID = "comp_토스트래블카드"
_NEW_CID = "comp_트래블월렛"
_DOMAIN_MATCH = "travel-wallet.com"


def _is_travel_wallet(cov: dict) -> bool:
    """candidate_coverage 의 URL 들이 travel-wallet 도메인을 포함하는지 검사."""
    for u in (cov.get("existing_urls") or []) + (cov.get("additional_urls") or []):
        url = u.get("url") or ""
        if _DOMAIN_MATCH in url:
            return True
    return False


def fix_feature_url_mapper_cache() -> int:
    """feature_url_mapper.json 의 모든 v0.10 엔트리에서 잘못된 cid 정정."""
    agent_id = "feature_url_mapper"
    path = _cache_path(agent_id)
    if not path.exists():
        print(f"⚠️  캐시 파일 없음: {path}")
        return 0

    data = _read_cache(path, agent_id)
    entries = data.get("entries", {})

    fixed_entries = 0
    fixed_covs = 0
    new_entries: dict[str, dict] = {}

    for old_key, entry in list(entries.items()):
        ctx = entry.get("context", {}) or {}
        if ctx.get("prompt_version") != "feature_url_mapper:v0.10":
            continue

        new_entry = copy.deepcopy(entry)
        entry_modified = False

        # 1) output.features[*].candidate_coverage[*].candidate_id 정정
        for feat in new_entry.get("output", {}).get("features", []):
            for cov in feat.get("candidate_coverage", []):
                if cov.get("candidate_id") == _OLD_CID and _is_travel_wallet(cov):
                    cov["candidate_id"] = _NEW_CID
                    entry_modified = True
                    fixed_covs += 1

        # 2) input.candidate_ids 의 리스트 정정 (A-4 slim cache_input)
        cids = new_entry.get("input", {}).get("candidate_ids")
        if isinstance(cids, list) and _OLD_CID in cids:
            new_entry["input"]["candidate_ids"] = sorted([
                _NEW_CID if c == _OLD_CID else c for c in cids
            ])
            entry_modified = True

        # 3) input.candidates 의 옛 형식(slim 이전) 도 처리
        cands = new_entry.get("input", {}).get("candidates")
        if isinstance(cands, list):
            for c in cands:
                if c.get("candidate_id") == _OLD_CID:
                    # validated_urls 또는 urls 에서 travel-wallet 확인
                    has_tw = False
                    for u_list_key in ("validated_urls", "urls"):
                        u_list = c.get(u_list_key, [])
                        if isinstance(u_list, list):
                            for u in u_list:
                                url = u if isinstance(u, str) else u.get("url", "")
                                if _DOMAIN_MATCH in (url or ""):
                                    has_tw = True
                                    break
                        if has_tw:
                            break
                    if has_tw:
                        c["candidate_id"] = _NEW_CID
                        entry_modified = True

        if entry_modified:
            fixed_entries += 1
            # 신규 cache_key 산정 (slim cache_input 의 candidate_ids 변경 반영)
            new_input = new_entry.get("input", {})
            # slim 형식 (candidate_ids 키) 인 경우 그대로 사용
            # 옛 형식 (candidates 키) 인 경우 _make_stable_cache_input 으로 변환
            if "candidates" in new_input and "candidate_ids" not in new_input:
                stable = _make_stable_cache_input(new_input)
                new_key = make_cache_key(agent_id, stable, ctx)
                new_entry["input"] = stable
            else:
                new_key = make_cache_key(agent_id, new_input, ctx)
            new_entry["cache_key"] = new_key
            new_entries[new_key] = new_entry
            print(f"  ✅ {old_key[:12]}... → {new_key[:12]}... (cov 정정 {fixed_covs}건 누적)")

    # 신규 엔트리 추가 (옛 엔트리는 보존)
    for nk, ne in new_entries.items():
        if nk not in entries:
            entries[nk] = ne

    data["entries"] = entries
    data["_meta"]["total_entries"] = len(entries)
    _write_cache(path, data)

    print(f"\n정정 결과:")
    print(f"  수정된 엔트리         : {fixed_entries}건 (신규 cache_key 로 추가, 옛 엔트리 보존)")
    print(f"  정정된 candidate_coverage: {fixed_covs}건")
    print(f"  파일 내 총 엔트리      : {len(entries)}건")
    return fixed_entries


def main() -> int:
    print("=" * 60)
    print(f"v0.10.17 candidate_id 정정 — {_OLD_CID} → {_NEW_CID}")
    print(f"(travel-wallet.com 도메인 URL 보유 candidate 만 대상)")
    print("=" * 60)
    print()

    n = fix_feature_url_mapper_cache()
    if n > 0:
        print()
        print("✅ 다음 분석 실행 시 트래블월렛 candidate 가 comp_트래블월렛 로 정상 라벨링됩니다.")
        print("⚠️  selected_competitor_ids 가 comp_토스트래블카드 로 들어오면 캐시 미스 가능 —")
        print("   competitor_discovery 단계의 e0a02225 엔트리는 정상 (comp_트래블월렛) 이므로")
        print("   neue 분석 실행 시에는 LangGraph state 의 candidate_id 가 정상으로 전달됩니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
