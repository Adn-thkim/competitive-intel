#!/usr/bin/env python3
"""
scripts/seed_reaction_caches.py
-------------------------------
반응 리포트 경로(B안)가 **기존 캐시 파일을 그대로 히트**하도록 두 가지를 맞춘다. LLM 호출 없음.

1) 서비스 인덱스 고정 (--service-index-from)
   reaction_extract 의 캐시 지문(system_prompt_sha256) = sha(dctx + _DEDUP_ADDENDUM + _SYS) 이고,
   dctx 는 data/debug/_service_index_cli_{hash}.json(LLM 산출 서비스명 목록)으로 만든다.
   9/10 이후 코드는 hash 를 (candidate_id, product_name, model) 만으로 계산해 안정적이지만, 그 hash 의
   파일이 아직 없어 다음 실행이 LLM 으로 **새 서비스 인덱스**를 만들고 → dctx 가 바뀌어 → 추출 전량 미스.
   과거 실행의 서비스 인덱스 파일을 현재 hash 이름으로 복사해 그 실행의 dctx 를 재현한다.
     지문 ↔ 서비스 인덱스 대응(2026-10-23 역산 확인):
       b15002d0 ← 444e6ae98677 (09-05, 정상 리포트) / f73c4ae9 ← c34b8e9df65c (09-09 1차)
       61191af7 ← 3965c22aefb3 (09-09 2차)         / 99e4d5ea ← cae053a94d48 (09-10)

2) 군집 캐시 재키잉 (--rekey-cluster)
   reaction_cluster_node 는 2026-10-23 수정 전까지 cin(units·questions) 을 참조로 둔 채 배정 결과를
   제자리 기록한 뒤 저장해, **변형된 입력으로 키를 만들어 저장** → 조회 키와 영구 불일치(히트 0회).
   저장된 input 에서 군집 단계가 덧붙인 필드를 제거해 원래 입력을 복원하고, 그 키로 사본을 쓴다
   (원본 파일은 보존).

사용
  python scripts/seed_reaction_caches.py --dry-run
  python scripts/seed_reaction_caches.py --service-index-from 444e6ae98677 --rekey-cluster
"""
from __future__ import annotations

import argparse
import copy
import json
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from server.config import AGENT_OUTPUT_CACHE_DIR  # noqa: E402
from server.graph.agent_cache import _entry_path, _write_entry, make_cache_key  # noqa: E402
from server.graph.reaction.extract import _profiles_hash  # noqa: E402
from server.graph.reaction.llm import MODELS  # noqa: E402

DEBUG_DIR = REPO / "data" / "debug"
REPORTS_DIR = REPO / "data" / "reports"
# 군집 단계(Phase A/B)가 units·questions 에 덧붙이는 필드 — 추출 출력 스키마에는 없다.
CLUSTER_ADDED = {"super_aspect", "sub_cluster", "perspective", "question_type"}


def _read(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def _latest_profiles() -> tuple[str, list]:
    snaps = []
    for f in REPORTS_DIR.glob("*.json"):
        d = _read(f)
        if d and d.get("product_profiles"):
            snaps.append((d.get("saved_at", ""), f.name, d["product_profiles"]))
    if not snaps:
        raise SystemExit("product_profiles 가 있는 리포트 스냅샷이 없습니다.")
    saved, name, profiles = sorted(snaps)[-1]
    return name, profiles


def seed_service_index(src_hash: str, dry: bool, force: bool) -> None:
    src = DEBUG_DIR / f"_service_index_cli_{src_hash}.json"
    data = _read(src)
    if data is None:
        raise SystemExit(f"원본 서비스 인덱스를 읽을 수 없습니다: {src.name} (iCloud 미다운로드면 Finder 에서 다운로드)")
    snap, profiles = _latest_profiles()
    model = MODELS.get("sonnet", "sonnet")
    dst = DEBUG_DIR / f"_service_index_cli_{_profiles_hash(profiles, model)}.json"
    print(f"[service_index] 기준 프로필: {snap} → 현재 키 파일 {dst.name}")
    print(f"                원본 {src.name}: {json.dumps(data, ensure_ascii=False)[:160]}")
    if dst.exists() and not force:
        cur = _read(dst)
        print(f"                이미 존재({'동일' if cur == data else '내용 다름 — --force 로 교체'}) — 건너뜀")
        return
    if dry:
        print("                dry-run — 복사하지 않음")
        return
    shutil.copyfile(src, dst)
    print("                복사 완료")


def _strip(items: list) -> list:
    return [{k: v for k, v in it.items() if k not in CLUSTER_ADDED} for it in items]


def rekey_cluster(dry: bool) -> None:
    d = AGENT_OUTPUT_CACHE_DIR / "reaction_cluster"
    n_new = n_skip = n_unread = 0
    for f in sorted(d.glob("*.json")):
        e = _read(f)
        if e is None:
            n_unread += 1
            continue
        inp = e.get("input") or {}
        if not any(CLUSTER_ADDED & set(u) for u in inp.get("units", []) + inp.get("questions", [])):
            n_skip += 1          # 이미 원래 입력으로 저장된 엔트리(수정 후 저장분)
            continue
        fixed = dict(inp, units=_strip(inp.get("units", [])), questions=_strip(inp.get("questions", [])))
        key = make_cache_key("reaction_cluster", fixed, e["context"])
        dst = _entry_path("reaction_cluster", key)
        tag = "있음" if dst.exists() else ("dry-run" if dry else "생성")
        print(f"[cluster] {e['cache_key'][:10]} (updated {e.get('updated_at','')[:16]}, "
              f"units {len(fixed['units'])}·questions {len(fixed['questions'])}) → {key[:10]} [{tag}]")
        if dst.exists() or dry:
            continue
        new = copy.deepcopy(e)
        new.update(cache_key=key, input=fixed, hit_count=0, last_hit_at=None,
                   rekeyed_from=e["cache_key"])
        _write_entry(dst, new)
        n_new += 1
    print(f"[cluster] 재키잉 {n_new}건 · 대상 아님 {n_skip}건 · 읽기 불가 {n_unread}건(iCloud 미다운로드)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--service-index-from", metavar="HASH",
                    help="현재 키로 복사할 과거 서비스 인덱스 hash (예: 444e6ae98677 = 09-05 정상 리포트)")
    ap.add_argument("--rekey-cluster", action="store_true", help="군집 캐시 재키잉")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="서비스 인덱스 파일이 이미 있어도 교체")
    a = ap.parse_args()
    if not (a.service_index_from or a.rekey_cluster):
        a.service_index_from, a.rekey_cluster, a.dry_run = "444e6ae98677", True, True
        print("(인자 없음 → 기본값으로 dry-run)")
    if a.service_index_from:
        seed_service_index(a.service_index_from, a.dry_run, a.force)
    if a.rekey_cluster:
        rekey_cluster(a.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
