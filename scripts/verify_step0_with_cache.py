"""
scripts/verify_step0_with_cache.py
-----------------------------------
실데이터 캐시 기반 Step 0 (build_extraction_targets) 검증 스크립트.

합성 fixture(test_official_content_collection_step0.py)와 달리, 실제 파이프라인이
data/cache 에 남긴 url_discovery_official → feature_mapping_official 출력으로
analysis_features 를 재구성하여 게이트의 실데이터 동작을 확인한다.

데이터 출처
-----------
- data/cache/agent_outputs/feature_mapping_official.json : feature × candidate × URL
  (최신 created_at 항목 사용 — feature_selection 화면에 표시된 값과 동일 계열)
- data/cache/agent_outputs/url_validation.json           : URL → http status
  (additional_urls_validation 의 validated 플래그 재현)
- data/cache/official_sources.json                       : candidate → primary_url
  (official_domain 게이트 기준)

시뮬레이션 가정
---------------
- 사용자가 comparison_matrix 의 모든 feature 를 선택 (interrupt #4 전체 선택)
- additional_urls 의 validated 는 url_validation 캐시의 status 2xx·3xx 기준
  (additional_urls_validation_node 의 official_subpage 분기와 동일 규칙)

검증 항목
---------
[V1] 산출 URL 전부 origin ∈ {official_source, official_subpage, additional_validated}
[V2] 산출 URL 전부 해당 candidate official_domain suffix 매칭
[V3] FE-D5 v3 — candidate당 URL 수 ≤ 안전 상한 25 (쌍당 상한 5는 V5와 손실 회계로 확인)
[V4] 손실 회계 — 입력 official URL 중 게이트 차단/상한 탈락 사유별 집계
[V5] coverage-aware 보장 (FE-D5 v2) — 게이트 통과 URL 보유 (feature × candidate)
     쌍은 capped 선택 집합에 최소 1 URL 보유

실행: python3 scripts/verify_step0_with_cache.py
"""

import copy
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.graph.nodes.official_content_collection_node import (  # noqa: E402
    _MAX_URLS_PER_CANDIDATE,
    _OFFICIAL_EXISTING_ORIGINS,
    _official_domain_map,
    build_extraction_targets,
)
from server.graph.nodes.url_discovery_official_node import _host_endswith  # noqa: E402

CACHE = ROOT / "data" / "cache"


def _load_latest_mapping() -> tuple[str, list[dict]]:
    data = json.loads((CACHE / "agent_outputs" / "feature_mapping_official.json").read_text())
    entries = data["entries"]
    h, entry = max(entries.items(), key=lambda kv: kv[1].get("created_at", ""))
    return f"{h[:12]} (created {entry.get('created_at', '')[:19]})", copy.deepcopy(
        entry["output"]["features"]
    )


def _load_url_status() -> dict[str, int]:
    data = json.loads((CACHE / "agent_outputs" / "url_validation.json").read_text())
    status: dict[str, int] = {}
    for e in data["entries"].values():
        url = (e.get("input") or {}).get("url", "")
        st = (e.get("output") or {}).get("status")
        if url and st is not None:
            status[url] = st
    return status


def _load_official_sources() -> list[dict]:
    data = json.loads((CACHE / "official_sources.json").read_text())
    return list(data["entries"].values())


def _build_state() -> tuple[dict, list[dict]]:
    entry_label, features = _load_latest_mapping()
    url_status = _load_url_status()
    official_sources = _load_official_sources()

    # additional_urls_validation 의 official_subpage 분기 재현:
    # url_validation 캐시 status 2xx·3xx → validated=True + source_origin 부착
    for feat in features:
        for cov in feat.get("candidate_coverage") or []:
            for au in cov.get("additional_urls") or []:
                st = url_status.get(au.get("url", ""))
                au["source_origin"] = "official_subpage"
                au["http_status"] = st
                au["validated"] = st is not None and 200 <= st <= 399

    cm_features = [f for f in features if f.get("report_type") == "comparison_matrix"]
    coverage_cids = sorted({
        c.get("candidate_id", "")
        for f in cm_features for c in f.get("candidate_coverage") or []
    })

    own_entry = next(
        (s for s in official_sources
         if s["candidate_id"].startswith("own_") and s["candidate_id"] in coverage_cids),
        {"candidate_id": "own", "product_name": ""},
    )
    state = {
        "selected_purposes":    ["comparison_matrix"],
        "selected_feature_ids": [f["feature_id"] for f in cm_features],  # 전체 선택 시뮬레이션
        "analysis_features":    features,
        "official_sources":     official_sources,
        "own_product": {"product_id": own_entry["candidate_id"],
                        "name": own_entry.get("product_name", "")},
        "competitor_candidates": [
            {"candidate_id": s["candidate_id"], "product_name": s.get("product_name", ""),
             "brand": s.get("brand", "")}
            for s in official_sources if s["candidate_id"].startswith("comp_")
        ],
        "selected_competitor_ids": [c for c in coverage_cids if c.startswith("comp_")],
    }
    print(f"캐시 항목: {entry_label}")
    print(f"comparison_matrix features: {len(cm_features)}개 | coverage candidates: {coverage_cids}")
    return state, cm_features


def _loss_accounting(state: dict, cm_features: list[dict], targets: list[dict]) -> bool:
    """[V4] 입력 official URL의 게이트 통과/차단/상한 탈락 사유별 집계."""
    domain_map = _official_domain_map(state["official_sources"])
    gated_urls = {t["candidate_id"]: {u["url"] for u in t["urls"]} for t in targets}
    ok = True

    reasons: dict[str, dict[str, int]] = {}
    blocked_samples: list[str] = []
    for feat in cm_features:
        for cov in feat.get("candidate_coverage") or []:
            cid = cov.get("candidate_id", "")
            r = reasons.setdefault(cid, {
                "통과": 0, "상한 탈락": 0, "origin 비공식/누락": 0,
                "additional 미검증": 0, "additional 도메인 불일치": 0, "not_found 스킵": 0,
            })
            if cov.get("coverage") == "not_found":
                r["not_found 스킵"] += 1
                continue
            for u in cov.get("existing_urls") or []:
                url = (u.get("url") or "").strip()
                if not url:
                    continue
                if u.get("origin") not in _OFFICIAL_EXISTING_ORIGINS:
                    r["origin 비공식/누락"] += 1
                    if len(blocked_samples) < 8:
                        blocked_samples.append(
                            f"  [origin={u.get('origin')!r}] {cid[:28]} ← {url[:72]}")
                elif url in gated_urls.get(cid, set()):
                    r["통과"] += 1
                else:
                    r["상한 탈락"] += 1
            for au in cov.get("additional_urls") or []:
                url = (au.get("url") or "").strip()
                if not url:
                    continue
                if not au.get("validated"):
                    r["additional 미검증"] += 1
                elif not _host_endswith(url, domain_map.get(cid, "")):
                    r["additional 도메인 불일치"] += 1
                elif url in gated_urls.get(cid, set()):
                    r["통과"] += 1
                else:
                    r["상한 탈락"] += 1

    print("\n[V4] 손실 회계 (URL 등장 횟수 기준 — feature 간 중복 포함)")
    for cid in sorted(reasons):
        print(f"  {cid}")
        for k, v in reasons[cid].items():
            if v:
                print(f"      {k}: {v}")
    if blocked_samples:
        print("\n  origin 비공식/누락 샘플 (실데이터에서 게이트가 차단한 항목):")
        print("\n".join(blocked_samples))
    return ok


def main() -> int:
    state, cm_features = _build_state()
    targets = build_extraction_targets(state)

    print(f"\n=== extraction_targets: {len(targets)} candidates ===")
    failures: list[str] = []
    domain_map = _official_domain_map(state["official_sources"])

    for t in targets:
        cid, urls = t["candidate_id"], t["urls"]
        domain = domain_map.get(cid, "")
        print(f"\n■ {cid} ({t['candidate_name']}) — official_domain={domain or '미확정'} "
              f"| features {len(t['feature_ids'])} | URLs {len(urls)}")
        for u in urls:
            host = urlparse(u["url"]).hostname or ""
            print(f"    [{u['origin']:21s}] {u['subpage_category'] or '-':6s} {u['url'][:80]}")
            # [V1]
            if u["origin"] not in (*_OFFICIAL_EXISTING_ORIGINS, "additional_validated"):
                failures.append(f"[V1] {cid}: 비공식 origin {u['origin']} ({u['url']})")
            # [V2]
            if domain and not _host_endswith(u["url"], domain):
                failures.append(f"[V2] {cid}: 도메인 불일치 host={host} ≠ {domain} ({u['url']})")
        # [V3]
        if len(urls) > _MAX_URLS_PER_CANDIDATE:
            failures.append(f"[V3] {cid}: 상한 초과 {len(urls)}")

    _loss_accounting(state, cm_features, targets)

    # [V5] coverage-aware 보장 — 게이트 통과 URL이 1개 이상인 (feature × candidate) 쌍은
    #      capped 선택 집합에도 최소 1개 URL을 보유해야 한다 (FE-D5 v2).
    #      단, 상한 5로 전체 feature 커버가 수학적으로 불가능한 candidate는
    #      전수조사(brute force)로 greedy가 최적 커버 수에 도달했는지만 검사한다.
    import itertools

    from server.graph.nodes.official_content_collection_node import _gate_coverage_urls
    gated_sel = {t["candidate_id"]: {u["url"] for u in t["urls"]} for t in targets}

    # candidate별 게이트 통과 URL → feature 집합 재구성
    url_feats: dict[str, dict[str, set]] = {}
    for feat in cm_features:
        ftext = " ".join((feat.get("feature_name", ""), feat.get("description", ""),
                          feat.get("feature_id", "")))
        for cov in feat.get("candidate_coverage") or []:
            cid = cov.get("candidate_id", "")
            for _t, e in _gate_coverage_urls(cov, domain_map.get(cid, ""), ftext):
                url_feats.setdefault(cid, {}).setdefault(e["url"], set()).add(
                    feat["feature_id"])

    v5_fail, v5_warn, v5_pairs, v5_ok = [], [], 0, 0
    for cid, by_url in sorted(url_feats.items()):
        all_feats = set().union(*by_url.values())
        covered = set().union(*(fs for u, fs in by_url.items()
                                if u in gated_sel.get(cid, set())) or [set()])
        v5_pairs += len(all_feats)
        v5_ok += len(covered)
        missing = all_feats - covered
        if not missing:
            continue
        # 전수조사: 상한 내 최적 커버 수 (unique URL ≤ 20 가정 — 실데이터 규모)
        k = min(_MAX_URLS_PER_CANDIDATE, len(by_url))
        optimum = max(
            (len(set().union(*combo)) for combo in
             itertools.combinations(by_url.values(), k)), default=0,
        )
        if len(covered) >= optimum:
            v5_warn.append(
                f"[V5-구조적] {cid}: {sorted(missing)} 미커버 — 상한 {k}로 최대 "
                f"{optimum}/{len(all_feats)} feature만 커버 가능 (greedy=최적 도달)")
        else:
            v5_fail.append(
                f"[V5] {cid}: greedy {len(covered)} < 최적 {optimum} — 선택 알고리즘 결함")
    print(f"\n[V5] feature × candidate 커버리지: {v5_ok}/{v5_pairs} 보장")
    for w in v5_warn:
        print(f"  ⚠ {w}")
    failures.extend(v5_fail)

    print("\n=== 결과 ===")
    if failures:
        print(f"실패 {len(failures)}건:")
        print("\n".join(f"  ✗ {f}" for f in failures))
        return 1
    print(f"✓ V1 (official origin만) · V2 (도메인 매칭) · V3 (안전 상한 "
          f"{_MAX_URLS_PER_CANDIDATE}) 모두 통과 — {len(targets)} candidates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
