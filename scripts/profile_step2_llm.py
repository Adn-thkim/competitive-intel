"""
scripts/profile_step2_llm.py
-----------------------------
Step 2 LLM 추출 비용 실측 (FE-D8 판단 기준).

실데이터(extraction_targets + fetch 캐시)로 ClaudeApiAnalyzer 를 실제 호출하여
candidate별 입력/출력 토큰·비용·소요 시간을 측정한다.

비용 단가 (Claude Sonnet 4.6, 2026-06 기준 — 모델 변경 시 갱신):
  입력 $3 / MTok · 출력 $15 / MTok
  https://platform.claude.com/docs/en/about-claude/pricing

사용법:
  python3 scripts/profile_step2_llm.py                  # 기본: comp_트래블월렛 1건
  python3 scripts/profile_step2_llm.py --all            # 전체 candidate
  python3 scripts/profile_step2_llm.py --candidate ID   # 지정 candidate

주의: 실제 API 과금 발생. 동일 입력 재실행은 agent 캐시 적중(과금 0).
산출물: data/collection/official_content_collection/profile_llm/{candidate_id}.json
"""

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.verify_step0_with_cache import _build_state  # noqa: E402
from server.graph.nodes.official_content_collection_node import (  # noqa: E402
    _LLM_MAX_TOKENS,
    _LLM_TIMEOUT_SEC,
    _load_llm_assets,
    assemble_feature_pool,
    run_llm_extraction,
)

OUT_DIR = ROOT / "data" / "collection" / "official_content_collection" / "profile_llm"

# Sonnet 4.6 단가 (USD / MTok)
PRICE_INPUT_PER_MTOK  = 3.0
PRICE_OUTPUT_PER_MTOK = 15.0
USD_KRW = 1400  # 환산 참고용 개략값


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", default="comp_트래블월렛")
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()

    state, _ = _build_state()
    only = None if args.all else {args.candidate}

    # 실제 analyzer + usage 캡처 래퍼
    system_prompt, _schema = _load_llm_assets()
    from server.llm.claude_api_analyzer import ClaudeApiAnalyzer
    analyzer = ClaudeApiAnalyzer(
        system_prompt=system_prompt,
        max_tokens=_LLM_MAX_TOKENS,
        timeout=_LLM_TIMEOUT_SEC,
    )
    usage_log: list = []
    _orig_create = analyzer._client.messages.create

    def _wrapped(**kwargs):
        resp = _orig_create(**kwargs)
        usage_log.append(resp.usage)
        return resp

    analyzer._client.messages.create = _wrapped
    print(f"모델: {analyzer.model} | 대상: {'전체' if args.all else args.candidate}\n")

    start = time.perf_counter()
    results, errors, stats = run_llm_extraction(state, analyzer=analyzer,
                                                only_candidates=only)
    elapsed = time.perf_counter() - start

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for r in results:
        cid = r["candidate_id"]
        if r["output"] is None:
            print(f"■ {cid}: {'본문 없음 (no_content)' if r['no_content'] else '실패'}")
            continue
        out_path = OUT_DIR / f"{cid}.json"
        out_path.write_text(json.dumps(r["output"], ensure_ascii=False, indent=2),
                            encoding="utf-8")
        feats = r["output"]["extracted_features"]
        by_status: dict[str, int] = {}
        for f in feats:
            by_status[f["extraction_status"]] = by_status.get(f["extraction_status"], 0) + 1
        print(f"■ {cid} — {'캐시 적중' if r['from_cache'] else 'LLM 호출'}"
              f" | 페이지 {len(r['pages_used'])} | feature {len(feats)}건 {by_status}")
        print(f"  → {out_path.relative_to(ROOT)}")

    print(f"\n총 소요 {elapsed:.1f}s | llm_calls {stats['llm_calls']} · "
          f"cache_hits {stats['cache_hits']} | errors {len(errors)}")
    for e in errors:
        print(f"  ✗ {e['error']}")

    # ── Step 3 조립 결과 — feature × candidate 매트릭스 프리뷰 ──────────────
    feature_pool, product_profiles = assemble_feature_pool(results, stats["targets"])
    (OUT_DIR / "feature_pool.json").write_text(
        json.dumps(feature_pool, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT_DIR / "product_profiles.json").write_text(
        json.dumps(product_profiles, ensure_ascii=False, indent=2), encoding="utf-8")

    _STATUS_MARK = {"explicit": "●", "partial": "◐", "inferred": "◔",
                    "unknown": "?", "not_found": "−", "requires_manual_check": "⚠"}
    cids = sorted({cid for cells in feature_pool.values() for cid in cells})
    print(f"\n[feature_pool 매트릭스] (● explicit ◐ partial ◔ inferred − not_found "
          f"⚠ manual_check, * = 기간한정)")
    header = "  " + f"{'feature':38s}" + " ".join(f"{c[:14]:14s}" for c in cids)
    print(header)
    for fid in sorted(feature_pool):
        row = f"  {fid[:38]:38s}"
        for cid in cids:
            cell = feature_pool[fid].get(cid)
            if cell is None:
                row += f"{'·':14s} "
                continue
            mark = _STATUS_MARK.get(cell["extraction_status"], "?")
            promo = "*" if cell.get("is_promotional") else ""
            val = (cell["value"][:10] + "…") if len(cell["value"]) > 10 else cell["value"]
            row += f"{mark}{promo}{val:11s} "
        print(row)
    print(f"  → 전체 조립 결과: {(OUT_DIR / 'feature_pool.json').relative_to(ROOT)}")

    if usage_log:
        in_tok  = sum(u.input_tokens for u in usage_log)
        out_tok = sum(u.output_tokens for u in usage_log)
        cost = (in_tok * PRICE_INPUT_PER_MTOK + out_tok * PRICE_OUTPUT_PER_MTOK) / 1e6
        print(f"\n[비용 실측] API 호출 {len(usage_log)}회 (재시도 포함)")
        print(f"  입력 {in_tok:,} tok · 출력 {out_tok:,} tok")
        print(f"  비용 ${cost:.4f} (≈ {cost * USD_KRW:,.0f}원)"
              f" | candidate당 평균 ${cost / max(1, stats['llm_calls']):.4f}")
        full_run = cost / max(1, stats["llm_calls"]) * 4
        print(f"  전체 1회 분석 추정 (4 candidates): ≈ ${full_run:.4f}"
              f" ({full_run * USD_KRW:,.0f}원)")
    elif stats["llm_calls"] == 0:
        print("\n[비용 실측] API 호출 0회 — 전부 캐시 적중 또는 본문 없음 (과금 없음)")
    else:
        print("\n[비용 실측] usage 미수집 — API 호출이 모두 실패했습니다 (network/key 확인)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
