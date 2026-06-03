"""
server/graph/nodes/url_discovery_macro_node.py (v0.10.22 실 구현)
------------------------------------------------------------------
5중 fan-out 중 source-type 5번 — 매크로 데이터 URL 탐색.

v0.10.19 까지의 스켈레톤(`_discover_via_brave_with_hints` 그대로 재사용) 을 폐기하고
본 PR(v0.10.22) 에서 도메인 화이트리스트 + Tier 그룹 site: 검색 + 2단계 fallback
실 구현을 도입합니다.

핵심 변경 (turn-50)
-------------------
1. **candidate 분기 폐기 (D32 옵션 a)** — macro feature 는 산업·시장 수준 데이터로
   candidate 비종속. `_substitute_tokens` 의 `{candidate_name}` 치환 폐기 →
   `_substitute_domain_only` 로 `{domain_name}` 만 치환. 결과는
   `candidate_id="macro"` 단일 키에 집계.

2. **도메인 화이트리스트 2-layer (D29 옵션 c)**:
   - Tier 1 (통계 핵심): kosis.kr·ecos.bok.or.kr·index.go.kr — 정적 코어
   - Tier 2 (정책·규제·연구): fsc.go.kr·mosf.go.kr·fss.or.kr·bok.or.kr·
     kdi.re.kr·kiet.re.kr·nia.or.kr·kotra.or.kr — 정적 코어
   - Tier 3 (도메인 의존): domain_taxonomy.report_config.market_context_swot.
     macro_data_sources — domain_modeling 의 LLM 이 도메인별 출처 3-5건 추천.
     TLD 화이트리스트(*.go.kr · *.or.kr · *.re.kr · *.ac.kr · *.kr) 강제는
     domain_modeling 의 output.schema.json 패턴으로 처리.

3. **Tier 그룹화 3쿼리 site: 검색 (D30 옵션 c)** — hint 1건당 Tier1/Tier2/Tier3 각
   1쿼리 = 3쿼리. `(site:a OR site:b OR ...)` 형식. Tier 3 도메인 없으면 스킵.

4. **Stage 2 뉴스 보강 (D31 옵션 b)** — Stage 1 완료 후 feature 별 결과 < 2건이면
   뉴스 화이트리스트(연합뉴스·한국경제·매경·머니투데이·전자신문·디지털타임스) 대상
   재검색. 결과에 `source_tier="news_supplement"` 부착.

5. **화이트리스트 매칭 검증** — Brave 의 site: 연산자가 누락될 수 있으므로 host
   suffix 매칭으로 재검증. 화이트리스트 미매칭 URL 제외.

위치 (v0.10.19 토폴로지 1차 fan-out)
------------------------------------
ab_join
  ├─→ url_discovery_official_node
  ├─→ url_discovery_blog_community_node
  ├─→ url_discovery_youtube_reactions_node
  ├─→ url_discovery_owned_channels_node
  └─→ [url_discovery_macro_node]   ← 이 노드 (v0.10.22 실 구현)

출력 state 키
-------------
- macro_urls_by_candidate : dict {"macro": [{url, source_tier, tier_group,
                                              feature_ids, matched_report_types, ...}]}
- agent_steps             : 누적 reducer

graceful 종료
-------------
- BRAVE_SEARCH_API_KEY 미설정: _brave_search 가 빈 리스트 반환 → 본 노드 status="completed"
  with empty 결과 (다른 4개 source-type 노드 정상 진행)
- 부분 실패 (일부 쿼리 timeout): status="completed" + errors 누적
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import urlparse

from server.graph.progress_store import set_progress
from server.graph.state import DomainAnalysisState, AgentStep
from server.graph.nodes.feature_url_mapper_node import (
    _brave_search,
    _extract_active_reports,
    _extract_hints_for_source,
    _error,
)

logger = logging.getLogger(__name__)

_SOURCE_TYPE = "macro"

# ─── 정적 화이트리스트 (D29 옵션 c — Tier 1·2 코어) ───────────────────────────

# Tier 1: 한국 통계 핵심 (3건)
_TIER1_STATISTICS: tuple[str, ...] = (
    "kosis.kr",          # 통계청 국가통계포털
    "ecos.bok.or.kr",    # 한국은행 ECOS (통화·외환·금융)
    "index.go.kr",       # e-나라지표
)

# Tier 2: 정책·규제·연구 (8건)
_TIER2_POLICY: tuple[str, ...] = (
    "fsc.go.kr",         # 금융위원회 (인가·규제)
    "mosf.go.kr",        # 기획재정부 (외환·경제)
    "fss.or.kr",         # 금융감독원
    "bok.or.kr",         # 한국은행 (ECOS 외 자료실)
    "kdi.re.kr",         # 한국개발연구원
    "kiet.re.kr",        # 산업연구원
    "nia.or.kr",         # 한국지능정보사회진흥원
    "kotra.or.kr",       # KOTRA
)

# 뉴스 보강 화이트리스트 (6건) — Stage 2 fallback 전용
_NEWS_SUPPLEMENT: tuple[str, ...] = (
    "yna.co.kr",         # 연합뉴스 (통신사)
    "hankyung.com",      # 한국경제
    "mk.co.kr",          # 매일경제
    "mt.co.kr",          # 머니투데이
    "etnews.com",        # 전자신문 (디지털·핀테크)
    "dt.co.kr",          # 디지털타임스
)

_MIN_RESULTS_PER_FEATURE = 2          # Stage 2 진입 임계 (D31 옵션 b)
_BRAVE_COUNT_PER_QUERY   = 5
_MAX_WORKERS             = 3
_MACRO_CANDIDATE_ID      = "macro"    # D32 옵션 a — 단일 키 집계


# ─── 헬퍼 ────────────────────────────────────────────────────────────────────

def _build_site_filter(domains: tuple[str, ...]) -> str:
    """site:a OR site:b OR ... 형식의 Brave 필터 문자열 생성.

    예: ('kosis.kr', 'bok.or.kr') → '(site:kosis.kr OR site:bok.or.kr)'
    빈 입력 시 빈 문자열.
    """
    if not domains:
        return ""
    return "(" + " OR ".join(f"site:{d}" for d in domains) + ")"


def _host_matches(url: str, whitelist: tuple[str, ...]) -> bool:
    """URL 의 host 가 화이트리스트 도메인의 suffix 인지 확인.

    Brave 의 site: 연산자가 가끔 무시되거나 누락될 수 있어 결과 차원에서 재검증.
    예: 'https://www.kosis.kr/foo' + ('kosis.kr',) → True
        'https://blog.naver.com/x' + ('kosis.kr',) → False
    """
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:  # noqa: BLE001
        return False
    if not host:
        return False
    if host.startswith("www."):
        host = host[4:]
    return any(host == d or host.endswith("." + d) for d in whitelist)


def _substitute_domain_only(query_template: str, domain_name: str) -> str:
    """macro 전용 토큰 치환 — `{domain_name}` 만 치환.

    `{candidate_name}` · `{competitor_name}` · `{own_product}` 등 candidate 차원
    토큰이 잔존하면 치환 실패로 처리 (호출부에서 '{' 잔존 검사로 스킵).
    """
    return query_template.replace("{domain_name}", domain_name).strip()


# ─── 메인 노드 ───────────────────────────────────────────────────────────────

def url_discovery_macro_node(state: DomainAnalysisState, config: dict | None = None) -> dict:
    """v0.10.22 실 구현 — 도메인 화이트리스트 Tier + 2단계 fallback.

    Returns
    -------
    dict
        {macro_urls_by_candidate, agent_steps[+ errors]}
    """
    started_at = datetime.now(timezone.utc).isoformat()
    thread_id  = (config or {}).get("configurable", {}).get("thread_id", "")

    print(
        f"📊 [url_discovery_macro_node] ENTRY at {started_at} thread_id={thread_id!r}",
        flush=True,
    )

    if thread_id:
        try:
            set_progress(
                thread_id, "feature_mapping_brave",
                detail="매크로 데이터 URL 탐색 (Tier+화이트리스트)",
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("set_progress(macro) 실패: %s", exc)

    # ── 입력 수집 ───────────────────────────────────────────────────────────
    domain_taxonomy: dict = state.get("domain_taxonomy") or {}
    domain_name: str      = state.get("domain_name") or ""

    if not domain_taxonomy:
        return _error(started_at, "domain_taxonomy 가 state 에 없습니다.")
    if not domain_name:
        return _error(started_at, "domain_name 이 state 에 없습니다.")

    all_active      = _extract_active_reports(domain_taxonomy)
    hints_with_meta = _extract_hints_for_source(all_active, _SOURCE_TYPE)

    if not hints_with_meta:
        logger.info(
            "url_discovery_macro_node: source_hint='macro' 인 hint 가 없습니다 — 빈 결과 반환",
        )
        finished_at = datetime.now(timezone.utc).isoformat()
        return {
            "macro_urls_by_candidate": {},
            "agent_steps": [{
                "step_name":   "UrlDiscoveryMacro",
                "status":      "completed",
                "started_at":  started_at,
                "finished_at": finished_at,
            }],
        }

    # ── 동적 화이트리스트 (Tier 3) ──────────────────────────────────────────
    market_swot = (domain_taxonomy.get("report_config") or {}).get("market_context_swot") or {}
    raw_tier3   = market_swot.get("macro_data_sources") or []
    tier3_dynamic: tuple[str, ...] = tuple(d for d in raw_tier3 if isinstance(d, str) and d)

    # ── Tier 그룹별 site: 필터 ──────────────────────────────────────────────
    tier_specs: list[tuple[str, tuple[str, ...]]] = [
        ("tier1_statistics", _TIER1_STATISTICS),
        ("tier2_policy",     _TIER2_POLICY),
    ]
    if tier3_dynamic:
        tier_specs.append(("tier3_dynamic", tier3_dynamic))

    # ── Stage 1 — 공식 출처 (Tier 1·2·3 병렬) ───────────────────────────────
    logger.info(
        "url_discovery_macro_node: Stage 1 (공식) — hints=%d, tier3=%d개",
        len(hints_with_meta), len(tier3_dynamic),
    )

    errors: list[dict[str, str]] = []
    stage1_results: list[dict] = []

    # (full_query, feature_id, report_type, tier_name) 작업 목록
    stage1_tasks: list[tuple[str, str, str, str]] = []
    for query_template, feature_id, rt in hints_with_meta:
        base_query = _substitute_domain_only(query_template, domain_name)
        if not base_query or "{" in base_query:
            # candidate 토큰 잔존 등 치환 실패 — domain_modeling 의 LLM 작성 오류
            continue
        for tier_name, tier_domains in tier_specs:
            site_filter = _build_site_filter(tier_domains)
            if not site_filter:
                continue
            full_query = f"{base_query} {site_filter}"
            stage1_tasks.append((full_query, feature_id, rt, tier_name))

    # 동일 쿼리 dedup — _brave_search 의 24h TTL 캐시와 무관하게 중복 호출 방지
    stage1_dedup: dict[str, tuple[str, str, str, str]] = {}
    for task in stage1_tasks:
        stage1_dedup.setdefault(task[0], task)

    def _search_stage1(full_query: str, feature_id: str, rt: str, tier_name: str):
        try:
            return _brave_search(full_query, count=_BRAVE_COUNT_PER_QUERY), feature_id, rt, tier_name
        except Exception as exc:  # noqa: BLE001
            errors.append({
                "node":      "url_discovery_macro_node",
                "error":     f"stage1 ({tier_name}) query={full_query[:60]}: {exc}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            return [], feature_id, rt, tier_name

    tier_whitelist_map = {
        "tier1_statistics": _TIER1_STATISTICS,
        "tier2_policy":     _TIER2_POLICY,
        "tier3_dynamic":    tier3_dynamic,
    }

    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        futures = [pool.submit(_search_stage1, *t) for t in stage1_dedup.values()]
        for fut in as_completed(futures):
            results, fid, rt, tier_name = fut.result()
            whitelist = tier_whitelist_map.get(tier_name, ())
            for r in results:
                url = (r.get("url") or "").strip()
                if not url:
                    continue
                # 화이트리스트 매칭 검증 — Brave site: 누락 대비 결과 재검증
                if not _host_matches(url, whitelist):
                    continue
                stage1_results.append({
                    "url":              url,
                    "page_title":       (r.get("title") or "").strip(),
                    "meta_description": (r.get("description") or "").strip(),
                    "origin":           "macro_search",
                    "source_tier":      "official_statistics",
                    "tier_group":       tier_name,
                    "feature_ids":      [fid] if fid else [],
                    "matched_report_types": [rt] if rt else ["market_context_swot"],
                })

    # ── Stage 2 — 뉴스 보강 진입 판정 (feature 별 < 2건) ────────────────────
    feature_counts: dict[str, int] = {}
    for u in stage1_results:
        for fid in u.get("feature_ids", []):
            feature_counts[fid] = feature_counts.get(fid, 0) + 1

    all_features = sorted({fid for _, fid, _ in hints_with_meta if fid})
    deficit_features = [
        fid for fid in all_features
        if feature_counts.get(fid, 0) < _MIN_RESULTS_PER_FEATURE
    ]

    stage2_results: list[dict] = []
    if deficit_features:
        logger.info(
            "url_discovery_macro_node: Stage 2 (뉴스 보강) 진입 — 결손 features=%d/%d",
            len(deficit_features), len(all_features),
        )
        news_filter = _build_site_filter(_NEWS_SUPPLEMENT)

        stage2_tasks: list[tuple[str, str, str]] = []
        for query_template, feature_id, rt in hints_with_meta:
            # 결손 feature 만 보강 호출
            if feature_id and feature_id not in deficit_features:
                continue
            base_query = _substitute_domain_only(query_template, domain_name)
            if not base_query or "{" in base_query:
                continue
            full_query = f"{base_query} {news_filter}"
            stage2_tasks.append((full_query, feature_id, rt))

        stage2_dedup: dict[str, tuple[str, str, str]] = {}
        for task in stage2_tasks:
            stage2_dedup.setdefault(task[0], task)

        def _search_stage2(full_query: str, feature_id: str, rt: str):
            try:
                return _brave_search(full_query, count=_BRAVE_COUNT_PER_QUERY), feature_id, rt
            except Exception as exc:  # noqa: BLE001
                errors.append({
                    "node":      "url_discovery_macro_node",
                    "error":     f"stage2 query={full_query[:60]}: {exc}",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                return [], feature_id, rt

        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
            futures = [pool.submit(_search_stage2, *t) for t in stage2_dedup.values()]
            for fut in as_completed(futures):
                results, fid, rt = fut.result()
                for r in results:
                    url = (r.get("url") or "").strip()
                    if not url:
                        continue
                    if not _host_matches(url, _NEWS_SUPPLEMENT):
                        continue
                    stage2_results.append({
                        "url":              url,
                        "page_title":       (r.get("title") or "").strip(),
                        "meta_description": (r.get("description") or "").strip(),
                        "origin":           "macro_search",
                        "source_tier":      "news_supplement",
                        "tier_group":       "news",
                        "feature_ids":      [fid] if fid else [],
                        "matched_report_types": [rt] if rt else ["market_context_swot"],
                    })

    # ── 결과 집계 — candidate_id = "macro" 단일 키 ──────────────────────────
    all_results = stage1_results + stage2_results
    urls_by_candidate = {_MACRO_CANDIDATE_ID: all_results} if all_results else {}

    logger.info(
        "url_discovery_macro_node: 완료 — Stage 1=%d, Stage 2=%d "
        "(deficit %d/%d features), errors=%d",
        len(stage1_results), len(stage2_results),
        len(deficit_features), len(all_features), len(errors),
    )

    finished_at = datetime.now(timezone.utc).isoformat()
    step: AgentStep = {
        "step_name":   "UrlDiscoveryMacro",
        "status":      "completed",
        "started_at":  started_at,
        "finished_at": finished_at,
    }
    if errors:
        step["error_message"] = f"{len(errors)}건 부분 실패"

    out: dict = {
        "macro_urls_by_candidate": urls_by_candidate,
        "agent_steps":             [step],
    }
    if errors:
        out["errors"] = errors
    return out
