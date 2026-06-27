#!/usr/bin/env python3
"""
scripts/relevance_cache_verify.py
---------------------------------
relevance_tagger per-item 캐시(RP-D1) 검증. LLM 불필요 — 가짜 분석기로 호출 횟수를
세어 캐시 적중/미스/실패-무저장을 확인한다. 실제 캐시는 임시 디렉터리로 격리한다.

확인:
  1) 1차 호출: 전량 미스 → 분류·저장. 분석기 호출 발생.
  2) 2차 호출(동일 입력): 전량 적중 → 분석기 **미호출**, 라벨 동일(_relevance_label 보관).
  3) 실패 배치는 저장 안 됨: 실패분만 2차에서 다시 미스(재분류 시도).
  4) 원시 label 보관: _relevance_label 이 aspect_id/none 으로 남는다.

실행: python scripts/relevance_cache_verify.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.graph import agent_cache                       # noqa: E402
from server.graph import relevance_tagger as rt            # noqa: E402


class _Fake:
    """프롬프트 각 줄에 'atm' 있으면 관련 aspect, 아니면 none. fail_at 배치는 예외."""
    def __init__(self, fail_batches=()):
        self.calls = 0
        self.classified = 0
        self.fail_batches = set(fail_batches)
        self.model = "fake"

    def call_with_schema(self, prompt, schema):
        self.calls += 1
        if self.calls in self.fail_batches:
            raise RuntimeError("boom")
        lines = [l for l in prompt.split("\n") if l[:3].strip().rstrip(".").isdigit()]
        self.classified += len(lines)
        return {"labels": ["atm_withdrawal_ux" if "atm" in l.lower() else "none"
                           for l in lines]}


def main() -> None:
    ok = True
    aspects = [{"aspect_id": "atm_withdrawal_ux", "label": "ATM"}]

    def fresh_items():
        return [{"text": f"atm 수수료 {i}" if i % 2 == 0 else f"여행 좋아요 {i}",
                 "is_reply": False, "thread_id": str(i)} for i in range(40)]

    with tempfile.TemporaryDirectory() as tmp:
        agent_cache.AGENT_OUTPUT_CACHE_DIR = Path(tmp)   # 캐시 격리

        # 1차: 전량 미스 → 분류
        f1 = _Fake()
        rt._build_analyzer = lambda *a, **k: f1
        items1 = fresh_items()
        rt.tag_relevance(items1, aspects, engine="cli", batch=20)
        try:
            assert f1.classified == 40, f"1차 분류 수 {f1.classified} != 40"
            assert all(it["_relevant"] == ("atm" in it["text"]) for it in items1), "라벨 매핑"
            assert all(it.get("_relevance_label") in ("atm_withdrawal_ux", "none")
                       for it in items1), "원시 label 미보관"
        except AssertionError as e:
            ok = False; print(f"  [FAIL 1차] {e}")
        print(f"1) 1차: 분류 {f1.classified}/40 · 원시 label 보관 ✓")

        # 2차: 동일 입력 → 전량 적중, 분석기 미호출
        f2 = _Fake()
        rt._build_analyzer = lambda *a, **k: f2
        items2 = fresh_items()
        rt.tag_relevance(items2, aspects, engine="cli", batch=20)
        try:
            assert f2.calls == 0, f"2차 분석기 호출 {f2.calls} (캐시 미적중)"
            assert all(it["_relevant"] == ("atm" in it["text"]) for it in items2), "적중 라벨 불일치"
        except AssertionError as e:
            ok = False; print(f"  [FAIL 2차 적중] {e}")
        print(f"2) 2차: 분석기 호출 {f2.calls}(=0, 전량 캐시 적중) ✓")

    # 3) 실패 배치 무저장: 별도 임시 캐시
    with tempfile.TemporaryDirectory() as tmp:
        agent_cache.AGENT_OUTPUT_CACHE_DIR = Path(tmp)
        f3 = _Fake(fail_batches={1})        # 1차 호출(첫 배치) 실패
        rt._build_analyzer = lambda *a, **k: f3
        items3 = fresh_items()
        rt.tag_relevance(items3, aspects, engine="cli", batch=20)
        # 첫 배치(0:20)는 실패 → 보수통과 True, 미저장. 2차에서 다시 미스여야 함.
        f4 = _Fake()
        rt._build_analyzer = lambda *a, **k: f4
        items4 = fresh_items()
        rt.tag_relevance(items4, aspects, engine="cli", batch=20)
        try:
            # 실패했던 첫 배치 20건이 2차에서 재분류돼야(저장 안 됐으므로) — 정확히 20건
            assert f4.classified == 20, f"실패배치 미저장 위반: 2차 재분류 {f4.classified} != 20"
        except AssertionError as e:
            ok = False; print(f"  [FAIL 무저장] {e}")
        print(f"3) 실패 배치 무저장: 2차 재분류 {f4.classified}(=20, 실패분만) ✓")

    print("\n검증:", "✅ 전 항목 통과" if ok else "❌ 실패")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
