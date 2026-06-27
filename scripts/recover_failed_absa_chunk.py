#!/usr/bin/env python3
"""
scripts/recover_failed_absa_chunk.py
------------------------------------
600s 타임아웃으로 실패해 캐시에 없는 ABSA 청크를, **잘게 나눠 처리한 뒤 전체 청크 키로
저장**한다. → 다음 분석 재실행 때 그 청크가 **캐시 히트**한다.
(CHUNK_CHARS 를 전역 수정하면 전 청크 items_sha 가 바뀌어 캐시 전량 무효화되므로, 그 대신
실패 청크 1개만 sub-chunk 로 분해 처리하고 결과를 합쳐 원래 키에 적재한다.)

원리:
  - ABSA 캐시 키 = (candidate_id, aspect_ids, items_sha[청크 전체]) + context(prompt·schema·model).
  - sub-chunk 별 tuple 을 합쳐 **원래(전체) 청크 키**로 store → 노드는 전체 키로 조회하므로 적중.
  - 노드와 동일한 prompt·context·model 을 사용해 키·검증이 일치하도록 한다.

대상 = reaction_state.json + community_comments 캐시로 재구성한 청크 중 **캐시 미스**.

실행(사용자 머신 — 구독 CLI 필요):
  python scripts/recover_failed_absa_chunk.py --dry-run        # 대상·분할만 확인(LLM 미호출)
  python scripts/recover_failed_absa_chunk.py --sub-chars 4000 --timeout 1200
"""
from __future__ import annotations

import argparse
import json
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# community_collection_node import 체인의 trafilatura 회피(노드 로더는 trafilatura 미사용)
sys.modules.setdefault("trafilatura", types.ModuleType("trafilatura"))

import logging  # noqa: E402

from server.graph.agent_cache import (  # noqa: E402
    load_agent_output, make_cache_context, make_cache_key, store_agent_output,
)
import server.graph.nodes.community_collection_node as CC          # noqa: E402
from server.graph.nodes import reaction_analysis_node as R          # noqa: E402
import server.graph.relevance_tagger as rt                          # noqa: E402

log = logging.getLogger("recover")
logging.basicConfig(level=logging.WARNING)


def _disp(it, parent):
    t = it.get("text", "")
    if it.get("is_reply"):
        p = parent.get(it.get("thread_id", ""), "")
        return f"[부모] {p[:150]} ↳ [답글] {t}" if p else t
    return t


def _split_by_chars(chunk, sub_chars):
    out, cur, c = [], [], 0
    for it in chunk:
        n = len(it.get("text", ""))
        if cur and c + n > sub_chars:
            out.append(cur); cur, c = [], 0
        cur.append(it); c += n
    if cur:
        out.append(cur)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sub-chars", type=int, default=4000, help="sub-chunk 문자 예산")
    ap.add_argument("--timeout", type=int, default=1200, help="sub-chunk CLI 타임아웃(초)")
    ap.add_argument("--dry-run", action="store_true", help="대상·분할만 출력(LLM 미호출)")
    args = ap.parse_args()

    d = json.loads((ROOT / "data/debug/reaction_state.json").read_text(encoding="utf-8"))
    comments = CC._load_cached_comments(d.get("community_posts") or [], log)
    state = dict(d); state["community_comments"] = comments
    inputs = R.build_absa_inputs(state)
    aspects = R._aspect_ids(dict(d)); valid = {a["aspect_id"] for a in aspects}
    name_by_cid = {p.get("candidate_id", ""): p.get("product_name", "")
                   for p in d.get("product_profiles") or []}

    # _relevant 적용(밤 실행과 동일 컷 재현) — tagger 캐시 라벨
    tl = {}
    for p in (ROOT / "data/cache/agent_outputs/relevance_tagger").glob("*.json"):
        try:
            tl[p.stem] = (json.loads(p.read_text()).get("output") or {}).get("label")
        except Exception:
            pass
    tctx = make_cache_context(agent_id="relevance_tagger", model="claude-haiku-4-5-20251001",
                              system_prompt=rt._sys_prompt(aspects), output_schema=rt._LABEL_SCHEMA)
    for cid, items in inputs.items():
        parent = {it.get("thread_id", ""): it.get("text", "")
                  for it in items if not it.get("is_reply")}
        for it in items:
            lab = tl.get(make_cache_key("relevance_tagger", {"text": _disp(it, parent)[:300]}, tctx))
            it["_relevant"] = lab in valid if lab else False

    # ABSA context (노드와 동일: agent_id·prompt·schema·model=sonnet)
    sysp = R._load_text(R.AGENTS_DIR / R._LLM_AGENT_ID / "system_prompt_kr.md")
    osch = R._load_json(R.AGENTS_DIR / R._LLM_AGENT_ID / "output.schema.json")
    analyzer = None
    if not args.dry_run:
        from server.llm.claude_cli_analyzer import ClaudeCodeCliAnalyzer
        analyzer = ClaudeCodeCliAnalyzer(system_prompt=sysp, timeout=args.timeout)
    model = getattr(analyzer, "model", "claude-sonnet-4-6")
    actx = make_cache_context(agent_id=R._LLM_AGENT_ID, model=model,
                              system_prompt=sysp, output_schema=osch)

    # 미스 청크 탐색
    misses = []
    for cid, items in inputs.items():
        _, chunks, _ = R._channel_cut(cid, items)
        for i, ch in enumerate(chunks):
            ci = {"candidate_id": cid, "aspect_ids": sorted(valid),
                  "items_sha": [R._norm(it["text"])[:64] for it in ch]}
            if load_agent_output(agent_id=R._LLM_AGENT_ID, cache_input=ci,
                                 context=actx, output_schema=osch) is None:
                misses.append((cid, i, ch, ci))

    print(f"캐시 미스 청크: {len(misses)}개\n")
    for cid, i, ch, ci in misses:
        subs = _split_by_chars(ch, args.sub_chars)
        chan = ch[0].get("channel")
        print(f"  {cid} chunk[{i}] {chan} · items {len(ch)} · "
              f"chars {sum(len(it['text']) for it in ch)} → sub-chunk {len(subs)}개")
        if args.dry_run:
            continue
        # sub-chunk 별 ABSA → tuple 병합
        merged = []
        for s, sub in enumerate(subs, 1):
            payload = {"candidate_id": cid, "candidate_name": name_by_cid.get(cid, cid),
                       "aspects": aspects, "items": sub}
            prompt = ("다음 사용자 반응에서 aspect 별 의견을 추출하라.\n\n```json\n"
                      + json.dumps(payload, ensure_ascii=False) + "\n```")
            out = analyzer.call_with_schema(prompt, osch)
            t = out.get("tuples", [])
            merged.extend(t)
            print(f"    sub[{s}/{len(subs)}] items {len(sub)} → tuple {len(t)}")
        # 전체 청크 키로 저장 → 노드 재실행 시 적중.
        # 스키마 required=[candidate_id, tuples] · additionalProperties=false → candidate_id 필수.
        store_agent_output(agent_id=R._LLM_AGENT_ID, cache_input=ci, context=actx,
                           output={"candidate_id": cid, "tuples": merged})
        # 적중 검증
        hit = load_agent_output(agent_id=R._LLM_AGENT_ID, cache_input=ci,
                                context=actx, output_schema=osch)
        ok = hit is not None and len(hit.get("tuples", [])) == len(merged)
        print(f"    → 저장 tuple {len(merged)} · 재조회 {'적중 ✅' if ok else '실패 ❌'}")

    if not misses:
        print("미스 없음 — 전 청크 캐시 적중(복구 불필요).")


if __name__ == "__main__":
    main()
