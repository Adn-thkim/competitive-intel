#!/usr/bin/env python3
"""
scripts/measure_baseline.py
---------------------------
v0.10.17 시점 파일럿 도메인 baseline 측정 — `data/cache/agent_outputs/
feature_url_mapper.json` 캐시 엔트리를 분석하여 B1~B4·B7 정량 지표를 산출하고
`docs/baseline_v0_10_17.md` 로 저장한다.

본 스크립트가 측정하는 지표
---------------------------
- B1: reaction_insight comp_* coverage="not_found" 비율
- B2: reaction_insight comp_* existing_urls 의 외부 host 비율 (공식 도메인 외)
- B3: reaction_insight 전체 additional_urls 중 validated=True 비율
- B4: reaction_insight comp_* candidate_coverage 의 평균 existing_urls 수
- B7: report_type 별 features 분포

본 스크립트가 측정하지 않는 지표 (외부 실행 측정 필요)
-----------------------------------------------------
- B5: LLM 호출 수 (cache miss 첫 실행) — 실제 실행 시 server 로그 카운트
- B6: 4단계 wall-clock cache miss — `agent_steps` 의 started_at~finished_at 차이

본 스크립트 출력의 §2 "외부 측정 필요 지표" 절에 측정 절차가 명시되어 있다.

사용법
------
    # 기본값 사용 (d4a2cba9 prefix 엔트리 → docs/baseline_v0_10_17.md)
    python3 scripts/measure_baseline.py

    # 특정 엔트리 prefix + 출력 경로 지정
    python3 scripts/measure_baseline.py \
        --entry-prefix d4a2cba9 \
        --output docs/baseline_v0_10_17.md

    # 파일 저장 없이 stdout 으로만 확인
    python3 scripts/measure_baseline.py --dry-run

참조
----
- 측정 절차 상세: docs/baseline_measurement_procedure.md
- 비교 기준 검증 게이트: docs/design/feature_url_mapper_redesign.md §11
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

# ── 기본 경로 ────────────────────────────────────────────────────────────────
DEFAULT_CACHE_PATH          = "data/cache/agent_outputs/feature_url_mapper.json"
DEFAULT_OFFICIAL_CACHE_PATH = "data/cache/agent_outputs/official_source_resolver.json"
DEFAULT_ENTRY_PREFIX        = "d4a2cba9"
DEFAULT_OUTPUT_PATH         = "docs/baseline_v0_10_17.md"


# ── 캐시 로드 ────────────────────────────────────────────────────────────────
def load_cache_entry(cache_path: Path, entry_prefix: str) -> tuple[str, dict]:
    """feature_url_mapper.json 캐시에서 prefix 매칭 엔트리 1건을 반환.

    Returns
    -------
    (entry_key, entry_dict)
    """
    if not cache_path.exists():
        raise FileNotFoundError(f"캐시 파일 없음: {cache_path}")
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    entries = cache.get("entries", {}) or {}
    matched = {k: v for k, v in entries.items() if k.startswith(entry_prefix)}
    if not matched:
        available = sorted({k[:12] for k in entries})[:5]
        raise ValueError(
            f"prefix={entry_prefix!r} 매칭 엔트리 없음. 사용 가능한 prefix 예시: {available}"
        )
    if len(matched) > 1:
        keys = sorted(matched)
        print(
            f"⚠ prefix={entry_prefix!r} 매칭 엔트리 {len(matched)}건, "
            f"첫 번째 사용: {keys[0][:16]}...",
            file=sys.stderr,
        )
    key = sorted(matched)[0]
    return key, matched[key]


def load_official_hosts(
    *,
    official_cache_path: Path,
    fum_entry: dict,
) -> set[str]:
    """공식 URL host 집합 추출.

    우선순위:
    1. feature_url_mapper.json 엔트리의 `features[*].candidate_coverage[*].existing_urls`
       중 `origin == "official_source"` 인 항목의 host
       (실제 분석에 사용된 공식 URL 의 정확한 정의)
    2. fallback — official_source_resolver.json 의 `resolutions[*].candidate_urls[*]`
       중 `url_confidence >= 0.5` 항목의 host
       (1번이 비어 있을 때만 사용 — `candidate_urls` 는 LLM 후보일 뿐 검증 전 상태)

    `www.` 접두사 양방향 인식.
    """
    hosts: set[str] = set()

    # 1차: feature_url_mapper.json 엔트리의 existing_urls 에서 origin="official_source" 추출
    features = (fum_entry.get("output", {}) or {}).get("features", []) or []
    for f in features:
        for cov in f.get("candidate_coverage", []) or []:
            for eu in cov.get("existing_urls", []) or []:
                if eu.get("origin") == "official_source":
                    _add_host(hosts, eu.get("url") or "")

    if hosts:
        return hosts

    # 2차 fallback: official_source_resolver.json 의 candidate_urls (confidence ≥ 0.5)
    if not official_cache_path.exists():
        print(
            f"⚠ official_source_resolver.json 없음 ({official_cache_path}) + "
            f"feature_url_mapper.json 에서 official_source origin 미발견. "
            f"외부 host 판정이 보수적이 됨 (모든 host 가 외부로 판정될 수 있음)",
            file=sys.stderr,
        )
        return hosts

    cache = json.loads(official_cache_path.read_text(encoding="utf-8"))
    for entry in (cache.get("entries", {}) or {}).values():
        output = entry.get("output", {}) or {}
        for res in output.get("resolutions", []) or []:
            for cu in res.get("candidate_urls", []) or []:
                if (cu.get("url_confidence") or 0) >= 0.5:
                    _add_host(hosts, cu.get("url") or "")
            for ref in res.get("reference_sources", []) or []:
                _add_host(hosts, ref.get("url") or "")
    return hosts


def _add_host(hosts: set[str], url: str) -> None:
    """URL 의 host 를 hosts 집합에 추가. www. 접두사 양방향 등록."""
    host = urlparse(url).netloc.lower()
    if not host:
        return
    hosts.add(host)
    if host.startswith("www."):
        hosts.add(host[4:])
    else:
        hosts.add(f"www.{host}")


# ── 지표 측정 ────────────────────────────────────────────────────────────────
def measure_b7(features: list[dict]) -> dict[str, int]:
    """B7 — report_type 별 features 분포 (None 키 제외)."""
    counter = Counter(f.get("report_type") for f in features)
    counter.pop(None, None)
    return dict(counter)


def measure_b1_b4(
    features: list[dict],
    official_hosts: set[str],
) -> dict[str, float | int]:
    """B1·B2·B3·B4 — reaction_insight 의 comp_* candidate_coverage 분석."""
    ri = [f for f in features if f.get("report_type") == "reaction_insight"]

    comp_coverage_total  = 0
    not_found_count      = 0
    existing_total       = 0
    existing_external    = 0
    additional_total     = 0
    additional_validated = 0
    existing_sum         = 0

    for f in ri:
        for cov in f.get("candidate_coverage", []) or []:
            cid = cov.get("candidate_id", "") or ""
            if not cid.startswith("comp_"):
                continue
            comp_coverage_total += 1
            if cov.get("coverage") == "not_found":
                not_found_count += 1

            existing_urls = cov.get("existing_urls", []) or []
            existing_sum += len(existing_urls)
            for eu in existing_urls:
                existing_total += 1
                host = urlparse(eu.get("url", "")).netloc.lower()
                if host and host not in official_hosts:
                    existing_external += 1

            for au in cov.get("additional_urls", []) or []:
                additional_total += 1
                if au.get("validated") is True:
                    additional_validated += 1

    return {
        "comp_coverage_total":  comp_coverage_total,
        "not_found_count":      not_found_count,
        "existing_total":       existing_total,
        "existing_external":    existing_external,
        "additional_total":     additional_total,
        "additional_validated": additional_validated,
        "existing_sum":         existing_sum,
        "B1": not_found_count      / max(comp_coverage_total, 1),
        "B2": existing_external    / max(existing_total, 1),
        "B3": additional_validated / max(additional_total, 1),
        "B4": existing_sum         / max(comp_coverage_total, 1),
    }


# ── markdown 렌더 ────────────────────────────────────────────────────────────
def render_markdown(
    *,
    entry_key: str,
    entry_meta: dict,
    domain_name: str | None,
    official_hosts: set[str],
    b7: dict,
    b1_b4: dict,
    feature_count: int,
) -> str:
    """결과를 docs/baseline_v0_10_17.md 양식으로 렌더."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    pct = lambda v: f"{v:.1%}"

    lines: list[str] = [
        "# v0.10.17 시점 파일럿 도메인 Baseline",
        "",
        f"> - 측정일: {now}",
        f"> - 도메인: {domain_name or '핀테크 / 해외여행 특화 카드 (domain_id=3, 추정)'}",
        f"> - 캐시 엔트리: `{entry_key[:16]}...` (`data/cache/agent_outputs/feature_url_mapper.json`)",
        f"> - 캐시 생성: {(entry_meta.get('created_at') or '미상')[:19]}",
        f"> - hit_count: {entry_meta.get('hit_count', 0)}",
        f"> - 분석 features: {feature_count}개",
        "> - 측정 스크립트: `scripts/measure_baseline.py`",
        "> - 측정 절차: `docs/baseline_measurement_procedure.md`",
        "",
        "---",
        "",
        "## 1. 정량 지표 (B1~B4, B7) — 자동 측정",
        "",
        "| ID | 지표 | 값 | 비교 검증 게이트 |",
        "|:-:|---|---|---|",
        (
            f"| B1 | reaction_insight comp_* not_found 비율 "
            f"| **{pct(b1_b4['B1'])}** "
            f"({b1_b4['not_found_count']}/{b1_b4['comp_coverage_total']}) "
            f"| v0.10.23 < 50% |"
        ),
        (
            f"| B2 | reaction_insight comp_* existing_urls 외부 host 비율 "
            f"| **{pct(b1_b4['B2'])}** "
            f"({b1_b4['existing_external']}/{b1_b4['existing_total']}) "
            f"| v0.10.23 ≥ 75% |"
        ),
        (
            f"| B3 | reaction_insight additional_urls validated=True 비율 "
            f"| **{pct(b1_b4['B3'])}** "
            f"({b1_b4['additional_validated']}/{b1_b4['additional_total']}) "
            f"| Phase 3 ≥ 30% |"
        ),
        (
            f"| B4 | reaction_insight comp_* candidate_coverage 평균 existing_urls 수 "
            f"| **{b1_b4['B4']:.2f}** "
            f"(existing_sum={b1_b4['existing_sum']}, coverage_count={b1_b4['comp_coverage_total']}) "
            f"| v0.10.27 비교 baseline |"
        ),
        (
            f"| B7 | report_type 별 features 분포 "
            f"| `{b7}` "
            f"| v0.10.18: positioning_map · executive_summary 0건 |"
        ),
        "",
        "## 2. 외부 측정 필요 지표 (B5, B6)",
        "",
        "B5·B6 은 cache miss 실행 시간을 측정해야 하므로 실제 파이프라인 1회 재실행이 필요합니다.",
        "",
        "### B5 — LLM 호출 수 (cache miss 첫 실행)",
        "",
        "1. 현재 `data/cache/agent_outputs/feature_url_mapper.json` 를 임시 위치로 백업",
        "2. 또는 `prompt_version` 을 임시로 bump 하여 강제 미스 유도",
        "3. React UI(`http://localhost:5173`)에서 파일럿 도메인 분석 재실행 또는 직접 `python3 -m server.graph.api ...`",
        "4. 백엔드 로그(`server` stdout) 에서 다음 패턴 카운트:",
        "    ```",
        "    feature_mapping_llm_node: report_type=<rt> 완료 (features=N)",
        "    ```",
        "5. 카운트 결과(예상 7회, `parallel=4`) 를 본 문서의 B5 항목에 기록",
        "6. 측정 후 백업 캐시 복원 또는 `prompt_version` 원복",
        "",
        "### B6 — 4단계 wall-clock cache miss",
        "",
        "1. B5 측정과 동시에 진행 (단일 실행에서 모두 측정 가능)",
        "2. 분석 완료 후 `state['agent_steps']` 에서 다음 step_name 의 ",
        "   `started_at` ~ `finished_at` 차이를 합산:",
        "    - `UrlDiscoveryBrave`",
        "    - `PageMetaCollect`",
        "    - `FeatureMappingLlm`",
        "    - `AdditionalUrlsValidation`",
        "3. 합산 값(예상 약 30분) 을 본 문서의 B6 항목에 기록",
        "",
        "### B5·B6 측정 결과 기록 (수동 갱신)",
        "",
        "| ID | 지표 | 측정 값 | 비교 검증 게이트 |",
        "|:-:|---|---|---|",
        "| B5 | LLM 호출 수 (cache miss) | _측정 후 기입_ | v0.10.27: 8회 |",
        "| B6 | 4단계 wall-clock cache miss 합산 | _측정 후 기입_ | v0.10.27: ≤ 17분 |",
        "",
        "---",
        "",
        "## 3. 측정 메타데이터",
        "",
        f"- `official_source_resolver.json` 에서 추출한 공식 host: 총 **{len(official_hosts)}개**",
        "- 본 host 집합에 포함되지 않은 도메인은 B2 의 외부 host 비율에 계상됨",
        "",
        "<details><summary>공식 host 전체 목록</summary>",
        "",
        "```",
        *(sorted(official_hosts) if official_hosts else ["(공식 host 캐시 미존재 — 외부 host 판정 보수적)"]),
        "```",
        "",
        "</details>",
        "",
        "---",
        "",
        "## 4. 향후 PR 별 비교 지점",
        "",
        "| PR | B1 목표 | B2 목표 | B3 목표 | B4 비교 | B5 목표 | B6 목표 | B7 목표 |",
        "|---|:-:|:-:|:-:|:-:|:-:|:-:|---|",
        "| v0.10.18 | — | — | — | — | — | — | positioning_map · executive_summary 0건 |",
        "| v0.10.23 | < 50% | ≥ 75% | — | — | — | — | — |",
        "| v0.10.25 | — | — | ≥ 30% | — | — | — | — |",
        "| v0.10.27 | — | — | — | 증가 또는 유지 | 8회 | ≤ 17분 (cache miss) | — |",
        "",
        "---",
        "",
        "## 5. 재측정 방법",
        "",
        "```bash",
        "# 기본 캐시 + 기본 prefix",
        "python3 scripts/measure_baseline.py",
        "",
        "# 다른 엔트리 prefix",
        "python3 scripts/measure_baseline.py --entry-prefix <prefix>",
        "",
        "# stdout 만 (파일 저장 안 함)",
        "python3 scripts/measure_baseline.py --dry-run",
        "```",
        "",
    ]
    return "\n".join(lines)


# ── main ─────────────────────────────────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--cache",
        default=DEFAULT_CACHE_PATH,
        help=f"feature_url_mapper.json 경로 (기본: {DEFAULT_CACHE_PATH})",
    )
    parser.add_argument(
        "--official-cache",
        default=DEFAULT_OFFICIAL_CACHE_PATH,
        help=f"official_source_resolver.json 경로 (기본: {DEFAULT_OFFICIAL_CACHE_PATH})",
    )
    parser.add_argument(
        "--entry-prefix",
        default=DEFAULT_ENTRY_PREFIX,
        help=f"분석할 엔트리 키 prefix (기본: {DEFAULT_ENTRY_PREFIX})",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT_PATH,
        help=f"결과 markdown 경로 (기본: {DEFAULT_OUTPUT_PATH})",
    )
    parser.add_argument(
        "--domain-name",
        default=None,
        help="도메인 명 (없으면 entry 의 cache_input 에서 추출 시도)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="markdown 출력만 하고 파일 저장 안 함",
    )
    args = parser.parse_args()

    cache_path          = Path(args.cache)
    official_cache_path = Path(args.official_cache)
    output_path         = Path(args.output)

    # 1. 캐시 엔트리 로드
    print(f"📂 캐시 로드: {cache_path}", file=sys.stderr)
    entry_key, entry = load_cache_entry(cache_path, args.entry_prefix)
    print(f"   → 엔트리: {entry_key[:16]}... (hit_count={entry.get('hit_count', 0)})", file=sys.stderr)

    # 2. 공식 host 집합 로드 (1차: feature_url_mapper.json 의 origin="official_source" / 2차 fallback: official_source_resolver.json)
    print(f"📂 공식 host 추출 (1차: feature_url_mapper / 2차 fallback: {official_cache_path})", file=sys.stderr)
    official_hosts = load_official_hosts(
        official_cache_path=official_cache_path,
        fum_entry=entry,
    )
    print(f"   → {len(official_hosts)}개 host", file=sys.stderr)

    # 3. domain_name 추출
    domain_name = args.domain_name
    if not domain_name:
        cache_input = entry.get("cache_input", {}) or {}
        domain_name = cache_input.get("domain") or None

    # 4. 측정
    features = (entry.get("output", {}) or {}).get("features", []) or []
    print(f"🔬 features 분석: {len(features)}개", file=sys.stderr)

    if not features:
        print("⚠ features 가 비어 있음 — 잘못된 엔트리이거나 빈 캐시", file=sys.stderr)
        return 1

    b7    = measure_b7(features)
    b1_b4 = measure_b1_b4(features, official_hosts)

    # 5. stderr 요약 출력 (CI/redirect 친화)
    print("", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print("Baseline 측정 결과", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print(
        f"B1 reaction_insight comp_* not_found 비율:           "
        f"{b1_b4['B1']:>6.1%}  ({b1_b4['not_found_count']}/{b1_b4['comp_coverage_total']})",
        file=sys.stderr,
    )
    print(
        f"B2 reaction_insight comp_* 외부 host 비율:           "
        f"{b1_b4['B2']:>6.1%}  ({b1_b4['existing_external']}/{b1_b4['existing_total']})",
        file=sys.stderr,
    )
    print(
        f"B3 reaction_insight additional_urls validated 비율:  "
        f"{b1_b4['B3']:>6.1%}  ({b1_b4['additional_validated']}/{b1_b4['additional_total']})",
        file=sys.stderr,
    )
    print(
        f"B4 candidate_coverage 평균 existing_urls 수:         "
        f"{b1_b4['B4']:>6.2f}  (sum={b1_b4['existing_sum']})",
        file=sys.stderr,
    )
    print(f"B7 report_type 분포:                                 {b7}", file=sys.stderr)
    print(f"B5·B6: 외부 측정 필요 — docs/baseline_v0_10_17.md §2 참조", file=sys.stderr)
    print("", file=sys.stderr)

    # 6. markdown 렌더
    md = render_markdown(
        entry_key=entry_key,
        entry_meta=entry,
        domain_name=domain_name,
        official_hosts=official_hosts,
        b7=b7,
        b1_b4=b1_b4,
        feature_count=len(features),
    )

    # 7. 저장
    if args.dry_run:
        print(md)
        print("✋ --dry-run: 파일 저장 건너뜀", file=sys.stderr)
    else:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(md, encoding="utf-8")
        print(f"📝 결과 저장: {output_path}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
