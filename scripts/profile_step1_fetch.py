"""
scripts/profile_step1_fetch.py
-------------------------------
Step 1 `_fetch_content` 실측 프로파일링 — 실제 extraction_targets URL 대상.

측정 항목
---------
1. URL별 추출 소요 시간(순차 측정) · fetch_status · 본문 길이
2. candidate별 병렬(_fetch_all) 소요 시간 — 실제 노드 실행 시 체감 시간
3. 추출 본문 저장: data/collection/official_content_collection/profile/{candidate_id}/
   (파일명 = URL 슬러그.md, gitignore 대상 경로)
4. _build_excerpt 적용 결과 크기 (페이지 예산 = FE-D5 v3 배분)

주의: 실 네트워크 호출 발생. 1회차는 cold(실제 fetch), 같은 날 재실행은 24h 캐시 적중.
실행: python3 scripts/profile_step1_fetch.py
"""

import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.verify_step0_with_cache import _build_state  # noqa: E402
from server.graph.nodes.official_content_collection_node import (  # noqa: E402
    _build_excerpt,
    _fetch_all,
    _fetch_content,
    _page_excerpt_budget,
    build_extraction_targets,
)

OUT_DIR = ROOT / "data" / "collection" / "official_content_collection" / "profile"

# 발췌 측정용 임시 키워드 풀 (Step 2 통합 전 — sub-page 키워드 7종 + 대표 feature 어휘)
_KEYWORDS = ["약관", "수수료", "환율", "한도", "혜택", "공지사항", "이용안내",
             "환전", "재환전", "ATM", "출금", "결제", "충전", "연회비"]


def _slug(url: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "_", url.split("://", 1)[-1])[:80]


def _feature_label_map(cm_features: list[dict]) -> dict[str, str]:
    """feature_id → 한국어 feature_name (파일 헤더 표기용)."""
    return {f["feature_id"]: f.get("feature_name", "") for f in cm_features}


def _file_header(cid: str, url_entry: dict, labels: dict[str, str],
                 content: str, excerpt: str, budget: int) -> str:
    """어떤 feature·리포트용 데이터인지 + 발췌 내 feature 단서 존재 여부를 헤더로 기록."""
    lines = [
        "<!--",
        f"  report_type      : comparison_matrix",
        f"  candidate_id     : {cid}",
        f"  url              : {url_entry['url']}",
        f"  origin           : {url_entry['origin']}"
        + (f" (subpage_category={url_entry['subpage_category']})"
           if url_entry.get("subpage_category") else ""),
        f"  분량             : 전문 {len(content):,}자 / 발췌 {len(excerpt):,}자 (예산 {budget:,})",
        "  연관 feature (이 URL을 게이트 통과시킨 분석 항목):",
    ]
    for fid in url_entry.get("feature_ids", []):
        label = labels.get(fid, "")
        # 간이 단서 점검: feature_name의 2자 이상 토큰이 발췌에 존재하는가
        tokens = [t for t in re.split(r"[\s/·()]+", label) if len(t) >= 2]
        hits = [t for t in tokens if t in excerpt]
        verdict = f"발췌 내 단서 {len(hits)}/{len(tokens)} ({', '.join(hits)})" if tokens else "-"
        lines.append(f"    - {fid} ({label}) — {verdict}")
    lines.append("-->")
    return "\n".join(lines)


def main() -> int:
    state, cm_features = _build_state()
    labels = _feature_label_map(cm_features)
    targets = build_extraction_targets(state)
    print(f"\nextraction_targets: {len(targets)} candidates, "
          f"총 {sum(len(t['urls']) for t in targets)} URLs\n")

    grand_start = time.perf_counter()
    total_ok = total_spa = total_fail = 0

    for t in targets:
        cid, urls = t["candidate_id"], [u["url"] for u in t["urls"]]
        if not urls:
            print(f"■ {cid} — URL 0건 (not_found candidate), 건너뜀")
            continue
        cand_dir = OUT_DIR / cid
        cand_dir.mkdir(parents=True, exist_ok=True)
        budget = _page_excerpt_budget(len(urls))

        # (1) 순차 측정 — URL별 소요 시간
        print(f"■ {cid} — {len(urls)} URLs (페이지 발췌 예산 {budget:,}자)")
        per_url = {}
        for url_entry in t["urls"]:
            url = url_entry["url"]
            start = time.perf_counter()
            result = _fetch_content(url)
            elapsed = time.perf_counter() - start
            per_url[url] = (elapsed, result)

            status = result["fetch_status"]
            content = result["content"]
            excerpt = _build_excerpt(content, _KEYWORDS, budget=budget) if content else ""
            mark = {"ok": "✓", "requires_dynamic_render": "◐", "fetch_failed": "✗"}[status]
            feats = ",".join(f.removeprefix("feat_") for f in url_entry.get("feature_ids", []))
            print(f"  {mark} {elapsed:6.2f}s  {status:24s} 전문 {len(content):6,}자 "
                  f"→ 발췌 {len(excerpt):5,}자  {url[:60]}")
            print(f"      └ features: {feats[:100]}")
            if status == "ok":
                total_ok += 1
                header = _file_header(cid, url_entry, labels, content, excerpt, budget)
                (cand_dir / f"{_slug(url)}.md").write_text(
                    f"{header}\n\n"
                    f"# ===== 발췌 (LLM 입력) =====\n\n{excerpt}\n\n"
                    f"# ===== 전문 =====\n\n{content}\n",
                    encoding="utf-8",
                )
            elif status == "requires_dynamic_render":
                total_spa += 1
                if result.get("error"):
                    print(f"      └ {result['error'][:90]}")
            else:
                total_fail += 1
                print(f"      └ {result.get('error', '')[:90]}")

        # (2) 병렬 재실행 (캐시 적중) — 노드 실행 시 체감 시간 추정용
        start = time.perf_counter()
        _fetch_all(urls)
        cached_elapsed = time.perf_counter() - start
        seq_total = sum(e for e, _ in per_url.values())
        print(f"  → 순차 합계 {seq_total:.2f}s | 캐시 적중 병렬 재실행 {cached_elapsed:.2f}s\n")

    print("=" * 70)
    print(f"전체 소요 {time.perf_counter() - grand_start:.1f}s | "
          f"ok {total_ok} · requires_dynamic_render {total_spa} · fetch_failed {total_fail}")
    print(f"추출 본문 저장 위치: {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
