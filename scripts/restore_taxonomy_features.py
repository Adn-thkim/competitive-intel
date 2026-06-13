#!/usr/bin/env python3
"""
scripts/restore_taxonomy_features.py
--------------------------------------
domain_modeling 2-step 분리(v0.15) 이후, 현재 taxonomy 파일의 7종 리포트별
features · feature_labels · search_query_hints · aspect_codebook 를
백업 파일 값으로 강제 치환한다.

배경
----
- v0.14 system_prompt 변경(§3-1 추가 등)이 input_fingerprint 를 바꿔 강제 재생성 발생.
- 재생성된 taxonomy 에서 positioning_map 등 B-only 리포트의 feature 품질이 저하됨.
- 백업(v0.14 이전)의 feature 값이 더 우수하므로 이를 복원한다.
- v0.15 2-step 분리로 community_site_candidates 가 fingerprint 에서 제외되어
  향후 registry 변경으로 인한 불필요한 재생성을 방지한다.

soft-TTL 보호 기간
------------------
치환 후 updated_at 을 현재 시각으로 갱신하므로 7일(168h) TTL 이 리셋된다.
이 기간 동안 파이프라인은 항상 캐시를 재사용한다 (LLM 재호출 없음).
TTL 만료 후 첫 실행 시 2-step 분리된 새 프롬프트로 재생성되며, 이후 fingerprint 가
정상적으로 기록되어 soft-TTL 이 영구 작동한다.

사용법
------
  cd competitive-intel
  python scripts/restore_taxonomy_features.py [--dry-run]

  --dry-run : 변경 내용만 출력하고 파일을 저장하지 않는다.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR    = Path(__file__).parent.parent
NEW_FILE    = BASE_DIR / "data" / "taxonomy" / "3_slug.json"
BACKUP_FILE = (
    BASE_DIR / "data" / "taxonomy" / "backups"
    / "3_slug__pre_today_20260603T1155.json"
)

REPORT_TYPES = (
    "comparison_matrix", "reaction_insight", "marketing_social",
    "battlecard", "positioning_map", "market_context_swot", "executive_summary",
)
# aspect_codebook 는 reaction_insight 에만 존재 — 조건부로 처리됨
RESTORE_KEYS = ("features", "feature_labels", "search_query_hints", "aspect_codebook")


def _summary(val: object) -> str:
    if isinstance(val, list):
        return f"list[{len(val)}]"
    if isinstance(val, dict):
        return f"dict[{len(val)}]"
    return repr(val)[:60]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true",
                        help="저장하지 않고 변경 내용만 출력")
    args = parser.parse_args()

    # ── 파일 로드 ─────────────────────────────────────────────────────────────
    for path in (NEW_FILE, BACKUP_FILE):
        if not path.exists():
            print(f"[ERROR] 파일 없음: {path}", file=sys.stderr)
            sys.exit(1)

    new_tax: dict = json.loads(NEW_FILE.read_text(encoding="utf-8"))
    bak_tax: dict = json.loads(BACKUP_FILE.read_text(encoding="utf-8"))

    # ── input_fingerprint 확인 ────────────────────────────────────────────────
    fp = new_tax.get("input_fingerprint")
    if not fp:
        print(
            "[WARN] 현재 taxonomy 에 input_fingerprint 없음.\n"
            "       soft-TTL 은 updated_at TTL 신선도(7일)에만 의존합니다.",
        )
    else:
        print(f"input_fingerprint (유지): {fp[:20]}...")

    # ── 7종 리포트 치환 ───────────────────────────────────────────────────────
    new_rc: dict = new_tax.get("report_config", {})
    bak_rc: dict = bak_tax.get("report_config", {})
    changed = 0

    for rt in REPORT_TYPES:
        bak_entry = bak_rc.get(rt)
        new_entry = new_rc.get(rt)
        if bak_entry is None:
            print(f"  [SKIP] {rt}: 백업에 없음")
            continue
        if new_entry is None:
            print(f"  [SKIP] {rt}: 현재 taxonomy 에 없음")
            continue

        for key in RESTORE_KEYS:
            bak_val = bak_entry.get(key)
            if bak_val is None:
                continue   # 백업에 해당 키 없으면 건드리지 않음
            old_val = new_entry.get(key)
            if old_val == bak_val:
                print(f"  [=] {rt}.{key}: 동일, 스킵")
            else:
                print(
                    f"  [→] {rt}.{key}: "
                    f"{_summary(old_val)} → {_summary(bak_val)}"
                )
                new_rc[rt][key] = bak_val
                changed += 1

    if changed == 0:
        print("\n변경 항목 없음 — 현재 taxonomy 가 이미 백업과 동일합니다.")
        return

    # ── updated_at 갱신 (TTL 7일 리셋) ───────────────────────────────────────
    now_iso = datetime.now(timezone.utc).isoformat()
    old_updated = new_tax.get("updated_at", "(없음)")
    new_tax["updated_at"] = now_iso
    print(f"\nupdated_at: {old_updated} → {now_iso}  (TTL 7일 리셋)")

    # version 증가
    new_tax["version"] = new_tax.get("version", 1) + 1
    print(f"version: {new_tax['version']}")

    if args.dry_run:
        print(f"\n[dry-run] {changed}건 변경 예정. 저장 생략.")
        return

    # ── 저장 ──────────────────────────────────────────────────────────────────
    NEW_FILE.write_text(
        json.dumps(new_tax, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n[OK] {changed}건 치환 완료: {NEW_FILE}")
    print(
        "\nsoft-TTL 확인 방법:\n"
        "  python scripts/verify_soft_ttl.py\n"
        "\n다음 파이프라인 실행 시 기대 로그:\n"
        "  domain_modeling_node: 캐시 재사용(신선), LLM 생략"
    )


if __name__ == "__main__":
    main()
