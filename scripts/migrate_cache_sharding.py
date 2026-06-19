#!/usr/bin/env python3
"""
scripts/migrate_cache_sharding.py
---------------------------------
단일 파일 agent_cache → 키별 샤딩 마이그레이션 (cache_storage_sharding_design.md Phase 2).

동작:
  1. 백업: data/cache/agent_outputs/{agent_id}.json 전체를
     data/cache/agent_outputs_backup_2026-06-19/ 로 복사(이미 있으면 skip).
  2. 마이그레이션: "히트 가능" 엔트리만 {agent_id}/{cache_key}.json 로 분할 저장.
       제외 기준(CS-D5):
         - 만료: updated_at/created_at < CUTOFF(30일, 2026-05-20)
         - reaction_analysis / reaction_insight: 전량(키 구조·payload 변경)
         - youtube_comments: context.v != 3 (현 코드 v:3)
  3. 정리: 마이그레이션 후 기존 단일 파일 {agent_id}.json 삭제(백업 보존됨).

실행: cd competitive-intel && python scripts/migrate_cache_sharding.py [--apply]
  --apply 없으면 dry-run(요약만 출력, 파일 변경 없음).
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "data" / "cache" / "agent_outputs"
BACKUP_DIR = ROOT / "data" / "cache" / "agent_outputs_backup_2026-06-19"
CUTOFF = "2026-05-20"  # 기준일 2026-06-19 - 30일
DROP_AGENTS = {"reaction_analysis", "reaction_insight"}  # 전량 무효화


def _eligible(agent_id: str, entry: dict) -> tuple[bool, str]:
    ts = (entry.get("updated_at") or entry.get("created_at") or "")[:10]
    if ts and ts < CUTOFF:
        return False, "expired"
    if agent_id in DROP_AGENTS:
        return False, "agent_invalidated"
    if agent_id == "youtube_comments" and (entry.get("context") or {}).get("v") != 3:
        return False, "context_v_bumped"
    return True, "ok"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="실제 적용(없으면 dry-run)")
    args = ap.parse_args()
    apply = args.apply

    files = sorted(p for p in CACHE_DIR.glob("*.json") if p.is_file())
    if not files:
        print("단일 파일 캐시 없음 — 이미 마이그레이션됐거나 캐시 비어 있음.")
        return

    # 1) 백업
    if apply and not BACKUP_DIR.exists():
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        for f in files:
            shutil.copy2(f, BACKUP_DIR / f.name)
        print(f"백업 완료: {len(files)}개 → {BACKUP_DIR}")
    elif apply:
        print(f"백업 skip(이미 존재): {BACKUP_DIR}")

    # 2) 마이그레이션
    tot_mig = tot_skip = 0
    skip_reasons: dict[str, int] = {}
    print(f"\n{'agent':32} {'전체':>6} {'이전':>6} {'제외':>6}")
    print("-" * 56)
    for f in files:
        agent_id = f.stem
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            print(f"{agent_id:32} (읽기 실패 — skip)")
            continue
        entries = data.get("entries") or {}
        mig = skip = 0
        outdir = CACHE_DIR / agent_id
        for key, entry in entries.items():
            ok, reason = _eligible(agent_id, entry)
            if not ok:
                skip += 1
                skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
                continue
            mig += 1
            if apply:
                outdir.mkdir(parents=True, exist_ok=True)
                ck = entry.get("cache_key") or key
                (outdir / f"{ck}.json").write_text(
                    json.dumps(entry, ensure_ascii=False, indent=2, sort_keys=True),
                    encoding="utf-8")
        tot_mig += mig
        tot_skip += skip
        print(f"{agent_id:32} {len(entries):>6} {mig:>6} {skip:>6}")
        # 3) 단일 파일 삭제(적용 시, 백업됨). 삭제 불가 환경이면 남겨둠 — 새 코드는
        #    {agent_id}/ 디렉터리만 읽으므로 구 단일파일은 무해(inert).
        if apply:
            try:
                f.unlink()
            except OSError as exc:
                print(f"  (구 파일 삭제 불가, 무해하게 잔존: {f.name} — {exc})")

    print("-" * 56)
    print(f"{'합계':32} {tot_mig+tot_skip:>6} {tot_mig:>6} {tot_skip:>6}")
    print(f"\n제외 사유: {skip_reasons}")
    if not apply:
        print("\n[dry-run] 변경 없음. 실제 적용하려면 --apply 추가.")
    else:
        print(f"\n적용 완료. 백업: {BACKUP_DIR}")


if __name__ == "__main__":
    main()
