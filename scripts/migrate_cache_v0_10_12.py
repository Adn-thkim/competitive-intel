#!/usr/bin/env python3
"""
scripts/migrate_cache_v0_10_12.py
----------------------------------
v0.10.12 의 A-3 (`_make_stable_cache_input`) 적용으로 feature_url_mapper 캐시의
cache_input 구조가 변경되어 cache_key sha256 이 달라졌다. 이전(v0.10.11 이하)
시점에 저장된 캐시 엔트리는 신규 cache_key 로 조회되지 않아 LLM 재호출이 발생한다.

본 스크립트는 1회 실행되어 옛 엔트리의 output 을 신규 cache_key 로 재저장한다.
옛 엔트리는 그대로 유지되며 새 엔트리만 추가되므로 회귀 위험 없음.

대상: feature_url_mapper.json 의 `prompt_version="feature_url_mapper:v0.10"` 엔트리.

사용
----
프로젝트 루트에서:
    python3 scripts/migrate_cache_v0_10_12.py

실행 결과
---------
  마이그레이션: N건 신규 엔트리 추가
  스킵       : M건 (이미 slim 형식 또는 신규 키 존재)
"""

from __future__ import annotations

import sys
from pathlib import Path

# 프로젝트 루트를 sys.path 에 추가
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from server.graph.agent_cache import (  # noqa: E402
    load_agent_output,
    make_cache_key,
    store_agent_output,
    _cache_path,
    _read_cache,
)
from server.graph.nodes.feature_mapping_llm_node import (  # noqa: E402
    _make_stable_cache_input,
)


def main() -> int:
    agent_id = "feature_url_mapper"
    path = _cache_path(agent_id)
    if not path.exists():
        print(f"⚠️  캐시 파일 없음: {path}")
        return 0

    data = _read_cache(path, agent_id)
    entries = data.get("entries", {})
    print(f"=== feature_url_mapper.json 마이그레이션 ===")
    print(f"  파일: {path}")
    print(f"  엔트리 총수: {len(entries)}")
    print()

    migrated = 0
    skipped_already_slim = 0
    skipped_already_exists = 0
    skipped_non_v010 = 0

    for old_key, entry in list(entries.items()):
        ctx = entry.get("context", {}) or {}
        if ctx.get("prompt_version") != "feature_url_mapper:v0.10":
            skipped_non_v010 += 1
            continue

        old_input = entry.get("input", {}) or {}
        cands = old_input.get("candidates") or []
        # 이미 slim 형식인지 (urls 키만 있고 validated_urls 없음) 검사
        if cands and "urls" in cands[0] and "validated_urls" not in cands[0]:
            skipped_already_slim += 1
            continue

        # slim cache_input 으로 변환
        stable = _make_stable_cache_input(old_input)
        new_key = make_cache_key(agent_id, stable, ctx)

        if new_key == old_key:
            # 변환 결과가 동일 (이미 slim 입력이었던 경우)
            skipped_already_slim += 1
            continue

        if new_key in entries:
            skipped_already_exists += 1
            continue

        # 신규 키로 같은 output 저장 (옛 엔트리는 그대로 유지)
        store_agent_output(
            agent_id=agent_id,
            cache_input=stable,
            context=ctx,
            output=entry.get("output", {}),
        )
        migrated += 1
        print(f"  ✅ migrated: {old_key[:12]}... → {new_key[:12]}...")

    print()
    print(f"마이그레이션 결과:")
    print(f"  신규 엔트리 추가: {migrated}건")
    print(f"  스킵 (이미 slim):  {skipped_already_slim}건")
    print(f"  스킵 (이미 존재):  {skipped_already_exists}건")
    print(f"  스킵 (v0.10 아님): {skipped_non_v010}건")
    if migrated > 0:
        print()
        print("✅ 다음 분석 실행 시 feature_mapping_llm_node 가 캐시 hit 으로 즉시 진행됩니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
