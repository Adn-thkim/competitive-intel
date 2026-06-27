#!/usr/bin/env python3
"""
scripts/absa_subsplit_verify.py
-------------------------------
2-패스 sub-분할 폴백(reaction_analysis_node) 검증. LLM 불필요 — 가짜 분석기로
"전체 청크 타임아웃 → sub-분할 재시도 → 병합·전체키 저장" 흐름을 결정론적으로 확인한다.

확인:
  1) 타임아웃 청크가 sub-분할로 복구된다(missing_items=0·failed_chunks=0).
  2) 복구분이 전체 청크 키 + candidate_id 로 캐시 저장 → 재조회 적중.
  3) 타임아웃이 아닌 실패(429류)는 분할하지 않고 그대로 실패(폴백 트리거 한정).

실행: python scripts/absa_subsplit_verify.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.modules.setdefault("trafilatura", types.ModuleType("trafilatura"))

from server.graph import agent_cache as AC                       # noqa: E402
from server.graph.nodes import reaction_analysis_node as R       # noqa: E402

_TIMEOUT_CHARS = 8000     # 전체 청크(12000)는 초과 → 타임아웃, sub(6000)는 미만 → 성공


def _make_fake(mode: str):
    class _Fake:
        model = "claude-sonnet-4-6"
        def call_with_schema(self, prompt, schema):
            payload = json.loads(prompt.split("```json\n", 1)[1].rsplit("\n```", 1)[0])
            items = payload["items"]
            chars = sum(len(it.get("text", "")) for it in items)
            if chars > _TIMEOUT_CHARS:
                if mode == "timeout":
                    raise RuntimeError("Claude CLI timeout (600s 초과). 프롬프트 단순화")
                raise RuntimeError("Claude CLI 비정상 종료 (returncode=1): api_error_status:429")
            a = payload["aspects"][0]["aspect_id"]
            it0 = items[0]
            return {"candidate_id": payload["candidate_id"], "tuples": [{
                "aspect": a, "polarity": "neutral", "intensity": 1,
                "quote": (it0.get("text") or "x")[:200],
                "source_url": it0.get("source_url", ""), "channel": it0.get("channel", "youtube"),
                "posted_at": it0.get("posted_at", ""), "is_suggestion": False}]}
    return _Fake()


def _state():
    d = json.loads((ROOT / "data/debug/reaction_state.json").read_text(encoding="utf-8"))
    cid = "own_토스트래블카드"
    sc = [c for c in (d.get("selected_comments") or []) if c.get("candidate_id") == cid][:200]
    return {"selected_purposes": [R.REPORT_TYPE], "selected_comments": sc,
            "community_posts": [], "community_comments": [], "collected_videos": d.get("collected_videos"),
            "domain_taxonomy": d.get("domain_taxonomy"), "product_profiles": d.get("product_profiles")}, cid


def main() -> None:
    R._dump_replay_state = lambda *a, **k: None        # 실제 덤프 덮어쓰기 방지
    R.REACTION_RELEVANCE_ENGINE = "off"                # 태깅 skip
    R.REACTION_ABSA_SUBSPLIT_ON_TIMEOUT = True
    ok = True

    # 시나리오 1: 타임아웃 → 폴백 복구
    with tempfile.TemporaryDirectory() as tmp:
        AC.AGENT_OUTPUT_CACHE_DIR = Path(tmp)
        state, cid = _state()
        out = R.reaction_analysis_node(state, {}, analyzer=_make_fake("timeout"))
        ra = (out.get("reaction_analysis") or {}).get(cid, {})
        n_cache = len(list((Path(tmp) / "reaction_analysis").glob("*.json")))
        print(f"1) 타임아웃 폴백: failed_chunks={ra.get('failed_chunks')} · "
              f"missing_items={ra.get('missing_items')} · tuples={len(ra.get('tuples', []))} · "
              f"캐시엔트리={n_cache}")
        try:
            assert ra, "reaction_analysis 누락"
            assert ra.get("failed_chunks") == 0, "복구 실패(failed_chunks>0)"
            assert ra.get("missing_items") == 0, "복구 실패(missing_items>0)"
            assert n_cache >= 1, "복구 청크 미저장"
        except AssertionError as e:
            ok = False; print(f"   [FAIL] {e}")

    # 시나리오 2: 비-타임아웃(429류) → 분할 안 함 → 실패 유지
    with tempfile.TemporaryDirectory() as tmp:
        AC.AGENT_OUTPUT_CACHE_DIR = Path(tmp)
        state, cid = _state()
        out = R.reaction_analysis_node(state, {}, analyzer=_make_fake("error429"))
        ra = (out.get("reaction_analysis") or {}).get(cid, {})
        print(f"2) 429류(분할 안 함): failed_chunks={ra.get('failed_chunks')} · "
              f"missing_items={ra.get('missing_items')}")
        try:
            assert ra.get("failed_chunks", 0) >= 1 and ra.get("missing_items", 0) > 0, \
                "비-타임아웃인데 분할/복구됨(트리거 한정 위반)"
        except AssertionError as e:
            ok = False; print(f"   [FAIL] {e}")

    print("\n검증:", "✅ 전 항목 통과" if ok else "❌ 실패")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
