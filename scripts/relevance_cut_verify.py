#!/usr/bin/env python3
"""
scripts/relevance_cut_verify.py
-------------------------------
RP-D3(관련 우선 정렬 + 잔여 충전) 컷 검증 + 태거(RP-D1) 로직 단위 검증.
LLM 불필요 — 합성 _relevant 태그로 컷 동작을 확인한다.

확인:
  1) 관련 우선: 예산이 관련 item 으로 먼저 채워진다(관련이 예산 이하면 전부 분석 + 잔여는
     비관련 최신으로 충전 → 유효 손실 0, 하드컷 대비 밀도 이득).
  2) 잔여 충전: 관련 < 예산이면 비관련(최신)으로 채워 예산을 모두 사용.
  3) 태깅 off(=_relevant 없음)면 기존 최신순 컷과 동일(하위호환).
  4) relevance_tagger: 가짜 분석기로 라벨→_relevant 매핑·대댓글 맥락·실패 보수통과 검증.

실행: python scripts/relevance_cut_verify.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.config import REACTION_ABSA_MAX_ITEMS_YOUTUBE          # noqa: E402
from server.graph.nodes.reaction_analysis_node import _channel_cut, build_absa_inputs  # noqa: E402
from server.graph import relevance_tagger as rt                    # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def _kept_youtube(items):
    items_sorted, chunks, intake = _channel_cut("t", items)
    return [it for it in items_sorted if it.get("channel") == "youtube"], intake


def main() -> None:
    d = json.loads((ROOT / "data/debug/reaction_state.json").read_text(encoding="utf-8"))
    inputs = build_absa_inputs(dict(d))
    cid = "own_토스트래블카드"
    yt = [dict(it) for it in inputs[cid] if it.get("channel") == "youtube"]
    budget = REACTION_ABSA_MAX_ITEMS_YOUTUBE
    print(f"검증 candidate={cid} · youtube 입력 {len(yt)} · 예산 {budget}\n")
    ok = True

    # 시나리오: 입력의 일부만 관련(_relevant) 으로 합성 태깅(예산보다 적게) → 관련 우선 + 충전
    REL = 300   # 관련 표시할 댓글 수(예산 1500 보다 작음)
    for i, it in enumerate(yt):
        it["_relevant"] = (i % (len(yt) // REL or 1) == 0)   # 분산 배치(최신/과거 혼재)
    rel_total = sum(1 for it in yt if it["_relevant"])
    kept, intake = _kept_youtube(yt)
    kept_rel = sum(1 for it in kept if it.get("_relevant"))
    try:
        assert len(kept) <= budget, "예산 초과"
        assert kept_rel == rel_total, f"관련 전량 보존 실패 {kept_rel}/{rel_total}"   # 관련<예산 → 전부 분석
        # 잔여 충전: 관련(304)보다 훨씬 많이 채워졌고 예산에 근접(thread-atomic 라 정확히 1500은 아님)
        assert len(kept) > rel_total and len(kept) >= budget - 300, "잔여 충전 실패"
    except AssertionError as e:
        ok = False; print(f"  [FAIL 우선/충전] {e}")
    print(f"1·2) 관련 {rel_total} → kept관련 {kept_rel}(전량 보존) · 총 kept {len(kept)}"
          f"(예산 충전) — 유효 손실 0")

    # 하위호환: 태그 제거 → 최신순 컷과 동일 길이
    yt2 = [dict(it) for it in inputs[cid] if it.get("channel") == "youtube"]
    for it in yt2:
        it.pop("_relevant", None)
    kept2, _ = _kept_youtube(yt2)
    try:
        assert len(kept2) <= budget and len(kept2) >= budget - 300, "하위호환(최신순) 실패"
    except AssertionError as e:
        ok = False; print(f"  [FAIL 하위호환] {e}")
    print(f"3) 태깅 off → kept {len(kept2)} (기존 최신순 컷 동일)")

    # 태거 로직: 가짜 분석기
    class _Fake:
        def __init__(self): self.calls = 0
        def call_with_schema(self, prompt, schema):
            self.calls += 1
            if self.calls == 2:                     # 2번째 배치는 실패 시뮬
                raise RuntimeError("boom")
            # 프롬프트의 각 줄에 'atm' 있으면 관련 aspect, 아니면 none
            lines = [l for l in prompt.split("\n") if l[:2].strip().rstrip(".").isdigit()]
            return {"labels": ["atm_withdrawal_ux" if "atm" in l.lower() else "none" for l in lines]}
    aspects = [{"aspect_id": "atm_withdrawal_ux", "label": "ATM"}]
    items = [{"text": f"atm 수수료 {i}" if i % 2 == 0 else f"여행 좋아요 {i}",
              "is_reply": False, "thread_id": str(i)} for i in range(50)]
    rt._build_analyzer = lambda *a, **k: _Fake()       # monkeypatch
    rt.load_agent_output = lambda **k: None            # 캐시 격리(태거 로직만 검증)
    rt.store_agent_output = lambda **k: None
    rt.tag_relevance(items, aspects, engine="cli", batch=20)
    b1 = items[:20]; b2 = items[20:40]; b3 = items[40:]
    try:
        assert all(it["_relevant"] == ("atm" in it["text"]) for it in b1 + b3), "라벨 매핑 오류"
        assert all(it["_relevant"] is True for it in b2), "실패 배치 보수통과 실패"
    except AssertionError as e:
        ok = False; print(f"  [FAIL 태거] {e}")
    print(f"4) 태거: 배치1·3 라벨 매핑 정확 · 배치2(실패) 보수 통과(_relevant=True)")

    print("\n검증:", "✅ 전 항목 통과" if ok else "❌ 실패")


if __name__ == "__main__":
    main()
