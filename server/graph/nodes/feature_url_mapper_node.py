"""
server/graph/nodes/feature_url_mapper_node.py (v0.10.9 — 헬퍼 모듈)
------------------------------------------------------------------
v0.10.9 부터 본 파일은 노드 진입 함수가 아닌 **헬퍼 모듈**이다.
옛 단일 노드 `feature_url_mapper_node()` 가 4개 노드로 분리됨에 따라, 본 모듈은 4개
신규 노드가 공유하는 헬퍼 함수와 상수의 공통 위치 역할만 한다.

4단계 분리 (graph.py v0.10.9 토폴로지)
--------------------------------------
  ab_join
    → url_discovery_brave_node            (Step 0 — Brave 검색)
    → page_meta_collect_node              (Step 1 — page meta 수집)
    → feature_mapping_llm_node            (Step 2 — LLM 호출, 가장 무거움)
    → additional_urls_validation_node     (Step 3 — HTTP 검증)
    → feature_selection (#4)

본 모듈이 제공하는 헬퍼·상수
----------------------------
- 상수: REPORT_TYPES (D4 enum 7종)
- Brave: _brave_search, _discover_via_brave, _candidate_name_map, _substitute_tokens
- Page Meta: _build_candidates_with_meta, _collect_page_meta, _fetch_meta, _MetaExtractor
- LLM 입력 슬림화: _filter_candidates_for_report   (A안 v0.10.8)
- 추출/필터: _extract_active_reports
- 추가 URL 검증: _validate_additional_urls, _check_url_status
- 출력 정규화: _normalize_feature, _strip_schema_patterns
- 파일 IO: _load_text, _load_json
- 오류 응답: _error

v0.10 → v0.10.9 변경 흐름
-------------------------
v0.10:   report_config 단위 + Brave Search 패턴 도입 (단일 노드)
v0.10.8: _filter_candidates_for_report 신설 (A안 — candidates 슬림화)
v0.10.9: 4개 노드 분리 (옵션 A) + parallel 2→4 + UI 4단계 stage 분리 (옵션 2)
         본 모듈은 노드 진입 함수를 폐기하고 헬퍼 모듈로 전환.
"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

import requests as req_lib

from server.config import BRAVE_SEARCH_API_KEY
from server.graph.agent_cache import (
    load_agent_output,
    store_agent_output,
)
from server.graph.state import AnalysisFeature

logger = logging.getLogger(__name__)

# v0.10.12 — 4개 노드 캐시 공통 TTL (시간 단위)
_NODE_CACHE_TTL_HOURS = 24

# ── HTTP·Brave 설정 ───────────────────────────────────────────────────────────
_HTTP_CONNECT_TIMEOUT = 3
_HTTP_READ_TIMEOUT    = 7
_HTTP_TIMEOUT         = (_HTTP_CONNECT_TIMEOUT, _HTTP_READ_TIMEOUT)
_USER_AGENT           = "Mozilla/5.0 (compatible; FeatureUrlMapperBot/1.0)"
_MAX_WORKERS          = 10
_META_BODY_LIMIT      = 8_000

_BRAVE_ENDPOINT       = "https://api.search.brave.com/res/v1/web/search"
_BRAVE_COUNT          = 5    # 쿼리당 결과 수
_BRAVE_MAX_QUERIES    = 3    # 리포트당 최대 쿼리 수 (rate limit 고려)

# v0.10 D4 enum 7종 (output.schema.json + domain_modeling_node와 정합)
REPORT_TYPES = (
    "comparison_matrix",
    "reaction_insight",
    "marketing_social",
    "battlecard",
    "positioning_map",
    "market_context_swot",
    "executive_summary",
)


# ─────────────────────────────────── 헬퍼 모듈 (v0.10.9) ────────────────────
#
# v0.10.9 부터 본 모듈의 공개 노드 함수 `feature_url_mapper_node` 는 제거되었다.
# 4개 신규 노드(url_discovery_brave / page_meta_collect / feature_mapping_llm /
# additional_urls_validation)가 본 모듈의 헬퍼들을 import 하여 사용한다.
#
# 본 모듈은 다음 헬퍼들의 공유 위치다:
#   - _extract_active_reports
#   - _candidate_name_map / _substitute_tokens
#   - _brave_search / _discover_via_brave
#   - _build_candidates_with_meta / _collect_page_meta / _fetch_meta / _MetaExtractor
#   - _filter_candidates_for_report          (A안 v0.10.8)
#   - _validate_additional_urls / _check_url_status
#   - _normalize_feature / _strip_schema_patterns
#   - _load_text / _load_json / _error
#   - REPORT_TYPES 상수
# ────────────────────────────────────────────────────────────────────────────

# v0.10.9 노드 분리 이후, 본 모듈은 노드 진입 함수를 제공하지 않는다.
# 옛 `feature_url_mapper_node` 함수 본문은 4개 신규 노드 파일로 분리·이전되었다.


# ────────────────── Step 0: Brave 검색으로 URL 후보 발견 (v0.10) ─────────────

def _extract_active_reports(domain_taxonomy: dict) -> dict[str, dict]:
    """v0.10 report_config 에서 active=true + source_flow ∈ {A, A+B} 리포트만 추출.

    v0.10.18: source_flow="B" (positioning_map · executive_summary) 인 리포트는
    `feature_url_mapper` 의 URL 수집 영역에서 자동 제외된다. B-only 리포트의
    features 자체는 `domain_taxonomy.report_config` 에 보존되어 후속 리포트 노드
    (`positioning_map_node`·`executive_summary_node`, v1.0) 가 derived 추출에 사용.

    source_flow 누락 시 기본값 "A" 로 간주하여 옛 도메인 taxonomy 후방 호환 유지.
    """
    report_config = domain_taxonomy.get("report_config") or {}
    return {
        rt: entry
        for rt, entry in report_config.items()
        if isinstance(entry, dict)
        and entry.get("active") is True
        and entry.get("source_flow", "A") in ("A", "A+B")   # v0.10.18: B-only 제외
    }


def _candidate_name_map(
    own_product: dict, competitor_candidates: list[dict], selected_ids: list[str]
) -> dict[str, str]:
    """candidate_id → 검색용 한국어 명칭 매핑."""
    name_map: dict[str, str] = {}
    own_id = own_product.get("product_id") or "own"
    own_name = own_product.get("name") or own_product.get("product_name") or ""
    if own_name:
        name_map[own_id] = own_name
        name_map["own"] = own_name  # fallback

    for cand in competitor_candidates:
        cid = cand.get("candidate_id", "")
        if cid and (not selected_ids or cid in selected_ids):
            name = cand.get("product_name") or cand.get("brand", "")
            if name:
                name_map[cid] = name
    return name_map


def _substitute_tokens(query_template: str, name_map: dict[str, str],
                       candidate_name: str, own_product_name: str,
                       domain_name: str) -> str:
    """search_query_hints 의 토큰 치환.

    v0.10.19.1 — 토큰 표준화:
    - `{candidate_name}` : 자사·경쟁사 모두에 적용되는 중립 토큰 (권장). 처리 중인
      candidate(own 이든 comp 이든) 의 product_name 으로 치환.
    - `{competitor_name}` : 옛 양식. `{candidate_name}` 의 alias 로 후방 호환 (동일 치환).
    - `{own_product}` : 자사 컨텍스트 명시가 필요한 경우 (예: 비교 쿼리).
    - `{domain_name}` : 도메인 일반 검색.
    """
    q = query_template
    q = q.replace("{candidate_name}",  candidate_name)   # v0.10.19.1 신설 (권장 토큰)
    q = q.replace("{competitor_name}", candidate_name)   # 후방 호환 alias
    q = q.replace("{own_product}",     own_product_name)
    q = q.replace("{domain_name}",     domain_name)
    return q.strip()


# v0.10.19.1 — D18 옵션 a 후방 호환 매핑 (옛 string hints 처리용)
# 옛 양식: report_type 단위 hints 에 source-type 메타 없음 → 본 표로 임시 라우팅
# v1.0 시점에 옛 양식 후방 호환 폐기 검토.
_LEGACY_SOURCE_TO_REPORT_TYPES: dict[str, tuple[str, ...]] = {
    "official":          ("comparison_matrix", "battlecard", "market_context_swot"),
    "blog_community":    ("reaction_insight",),
    "youtube_reactions": ("reaction_insight",),
    "owned_channels":    ("marketing_social", "battlecard"),
    "macro":             ("market_context_swot",),
}


def _extract_hints_for_source(
    active_reports: dict[str, dict],
    source_type: str,
) -> list[tuple[str, str, str]]:
    """source_type 에 해당하는 (query, feature_id, report_type) 튜플 목록 반환.

    v0.10.19.1 — 두 양식 처리:
    1. 신규 객체 양식 `{feature_id, query, source_hint}` — source_hint 일치만 채택.
       feature_id 메타가 보존되어 후속 LLM 매핑 정확도 향상에 활용.
    2. 옛 string 양식 — `_LEGACY_SOURCE_TO_REPORT_TYPES` 기반 report_type 매칭으로 임시 라우팅.
       feature_id 부재(빈 문자열).

    Parameters
    ----------
    active_reports : dict[str, dict]
        _extract_active_reports() 결과. report_type → reportEntry 매핑.
    source_type : str
        "official" | "blog_community" | "youtube_reactions" | "owned_channels" | "macro"

    Returns
    -------
    list[tuple[query: str, feature_id: str, report_type: str]]
        feature_id 는 옛 string 양식 시 빈 문자열 "".
    """
    legacy_rts = _LEGACY_SOURCE_TO_REPORT_TYPES.get(source_type, ())
    out: list[tuple[str, str, str]] = []

    for rt, entry in active_reports.items():
        for h in entry.get("search_query_hints") or []:
            if isinstance(h, dict):
                # 신규 객체 양식 — source_hint 일치만 채택
                if h.get("source_hint") == source_type:
                    q   = (h.get("query") or "").strip()
                    fid = (h.get("feature_id") or "").strip()
                    if q:
                        out.append((q, fid, rt))
            elif isinstance(h, str):
                # 옛 string 양식 — _LEGACY_SOURCE_TO_REPORT_TYPES 기반 후방 호환
                if rt in legacy_rts:
                    q = h.strip()
                    if q:
                        out.append((q, "", rt))
    return out


def _brave_search(query: str, count: int = _BRAVE_COUNT) -> list[dict]:
    """
    Brave Search API 호출 (v0.10.12 B-1 24h TTL 캐시 적용).

    캐시 조회 → 미스 시 실제 API 호출 → 결과 저장. 동일 쿼리 + count 조합에 대해
    24시간 이내에는 항상 같은 결과를 반환하여 Brave rate limit 부담을 완화하고
    feature_mapping_llm 의 cache_input(URL list) 안정성을 보장한다.

    실패 시 빈 리스트를 반환하며, 빈 결과는 캐시하지 않는다(일시적 실패 재시도 가능).
    """
    if not BRAVE_SEARCH_API_KEY:
        logger.warning("BRAVE_SEARCH_API_KEY 미설정 — Brave 검색 생략")
        return []

    # ── 캐시 조회 (v0.10.12) ────────────────────────────────────────────────
    cache_input   = {"query": query, "count": count}
    cache_context = {"agent_id": "url_discovery_brave", "v": 1}
    cached = load_agent_output(
        agent_id="url_discovery_brave",
        cache_input=cache_input,
        context=cache_context,
        logger=logger,
        ttl_hours=_NODE_CACHE_TTL_HOURS,
    )
    if cached is not None:
        return cached.get("results", [])

    # ── 캐시 미스 — 실제 Brave API 호출 ──────────────────────────────────────
    headers = {
        "Accept":               "application/json",
        "Accept-Encoding":      "gzip",
        "X-Subscription-Token": BRAVE_SEARCH_API_KEY,
    }
    try:
        resp = req_lib.get(
            _BRAVE_ENDPOINT, headers=headers,
            params={"q": query, "count": count}, timeout=(3, 8),
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("web", {}).get("results", [])
    except Exception as exc:  # noqa: BLE001
        logger.debug("Brave 검색 실패 (%s): %s", query, exc)
        return []

    # ── 캐시 저장 (빈 결과는 캐시 안 함 — 일시 실패 재시도 보존) ─────────────
    if results:
        try:
            store_agent_output(
                agent_id="url_discovery_brave",
                cache_input=cache_input,
                context=cache_context,
                output={"results": results},
                logger=logger,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Brave 검색 캐시 저장 실패 (%s): %s", query, exc)
    return results


def _discover_via_brave(
    *,
    active_reports: dict[str, dict],
    own_product: dict,
    competitor_candidates: list[dict],
    selected_ids: list[str],
    domain_name: str,
) -> dict[str, list[dict]]:
    """
    각 active 리포트의 search_query_hints를 candidate별로 토큰 치환 후 Brave 호출.

    Returns
    -------
    dict[candidate_id, list[{url, page_title, meta_description, origin, matched_report_types}]]
    """
    name_map = _candidate_name_map(own_product, competitor_candidates, selected_ids)
    own_product_name = own_product.get("name") or own_product.get("product_name") or ""

    # (candidate_id, query, [report_types]) 작업 목록 생성
    tasks: list[tuple[str, str, list[str]]] = []
    query_to_reports: dict[str, list[str]] = {}

    for rt, entry in active_reports.items():
        hints = entry.get("search_query_hints") or []
        # 리포트당 최대 _BRAVE_MAX_QUERIES개 hint 사용
        for hint in hints[:_BRAVE_MAX_QUERIES]:
            for cand_id, cand_name in name_map.items():
                if cand_id == "own":
                    continue  # fallback 중복 회피
                query = _substitute_tokens(
                    hint, name_map, cand_name, own_product_name, domain_name,
                )
                if not query or "{" in query:
                    # 치환 실패 (토큰 미치환) — 스킵
                    continue
                tasks.append((cand_id, query, [rt]))
                query_to_reports.setdefault(query, []).append(rt)

    # 동일 쿼리 dedup
    deduped: dict[str, str] = {}  # query → candidate_id (첫 등장 기준)
    for cand_id, query, _ in tasks:
        if query not in deduped:
            deduped[query] = cand_id

    # Brave 호출 (병렬)
    results_by_candidate: dict[str, list[dict]] = {}
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        future_map = {
            pool.submit(_brave_search, q): (q, deduped[q]) for q in deduped
        }
        for future in as_completed(future_map):
            query, cand_id = future_map[future]
            try:
                results = future.result()
            except Exception as exc:  # noqa: BLE001
                logger.debug("Brave 결과 처리 실패 (%s): %s", query, exc)
                results = []
            for r in results:
                url = (r.get("url") or "").strip()
                if not url:
                    continue
                results_by_candidate.setdefault(cand_id, []).append({
                    "url":              url,
                    "page_title":       (r.get("title") or "").strip(),
                    "meta_description": (r.get("description") or "").strip(),
                    "origin":           "brave_search",
                    "matched_report_types": query_to_reports.get(query, []),
                })
    return results_by_candidate


def _discover_via_brave_with_hints(
    *,
    hints_with_meta: list[tuple[str, str, str]],
    own_product: dict,
    competitor_candidates: list[dict],
    selected_ids: list[str],
    domain_name: str,
) -> dict[str, list[dict]]:
    """v0.10.19.1 신설 — `_extract_hints_for_source` 산출 튜플로 Brave 검색.

    각 hint 튜플 (query_template, feature_id, report_type) 을 own + selected comp
    모든 candidate 에 토큰 치환 후 Brave 호출. 발견된 각 URL 에 feature_id 메타 부착.

    `_discover_via_brave` 와의 차이:
    - 입력이 active_reports dict 가 아닌 hints_with_meta 튜플 목록
    - 출력 URL 항목에 `feature_id` 필드 추가 (LLM 매핑 정확도 향상에 활용)
    - 토큰 치환은 동일하게 `_substitute_tokens` 사용 — {candidate_name} · {competitor_name} 모두 처리

    Parameters
    ----------
    hints_with_meta : list[tuple[query, feature_id, report_type]]
        `_extract_hints_for_source(active_reports, source_type)` 산출.

    Returns
    -------
    dict[candidate_id, list[{url, page_title, meta_description, origin, feature_id,
                              matched_report_types}]]
    """
    name_map = _candidate_name_map(own_product, competitor_candidates, selected_ids)
    own_product_name = own_product.get("name") or own_product.get("product_name") or ""

    # (candidate_id, query, feature_id, report_type) 작업 목록 생성
    tasks: list[tuple[str, str, str, str]] = []
    # query → [(feature_id, report_type), ...] (동일 query 중복 시 메타 누적)
    query_to_meta: dict[str, list[tuple[str, str]]] = {}

    for query_template, feature_id, rt in hints_with_meta:
        for cand_id, cand_name in name_map.items():
            if cand_id == "own":
                continue  # fallback 중복 회피
            query = _substitute_tokens(
                query_template, name_map, cand_name, own_product_name, domain_name,
            )
            if not query or "{" in query:
                continue  # 치환 실패 스킵
            tasks.append((cand_id, query, feature_id, rt))
            query_to_meta.setdefault(query, []).append((feature_id, rt))

    # 동일 쿼리 dedup (query → first candidate_id)
    deduped: dict[str, str] = {}
    for cand_id, query, _, _ in tasks:
        if query not in deduped:
            deduped[query] = cand_id

    # Brave 호출 (병렬)
    results_by_candidate: dict[str, list[dict]] = {}
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        future_map = {pool.submit(_brave_search, q): (q, deduped[q]) for q in deduped}
        for future in as_completed(future_map):
            query, cand_id = future_map[future]
            try:
                results = future.result()
            except Exception as exc:  # noqa: BLE001
                logger.debug("Brave 결과 처리 실패 (%s): %s", query, exc)
                results = []
            metas      = query_to_meta.get(query, [])
            feature_ids = sorted({fid for fid, _ in metas if fid})  # 빈 fid 제외
            report_types = sorted({rt  for _, rt  in metas if rt})
            for r in results:
                url = (r.get("url") or "").strip()
                if not url:
                    continue
                results_by_candidate.setdefault(cand_id, []).append({
                    "url":              url,
                    "page_title":       (r.get("title") or "").strip(),
                    "meta_description": (r.get("description") or "").strip(),
                    "origin":           "brave_search",
                    "feature_ids":      feature_ids,   # v0.10.19.1 신설 — 매핑 정확도 향상
                    "matched_report_types": report_types,
                })
    return results_by_candidate


# ─────────────────── Step 1: Page Meta 수집 (병합) ───────────────────────────

def _build_candidates_with_meta(
    *,
    official_sources: list[dict],
    brave_urls_by_candidate: dict[str, list[dict]],
) -> list[dict]:
    """
    official_sources + Brave 발견 URL을 candidate별로 병합하고, 누락된 URL은 page meta 수집.

    official_source URL은 이미 검증되어 있으나 page meta가 없을 수 있어 별도 수집.
    Brave URL은 검색 결과에 title·description이 포함되어 있어 추가 fetch 불필요(빠른 경로).
    """
    candidates_out: list[dict] = []

    # 1) official_sources에서 candidate별 validated URL 수집 (origin=official_source)
    official_by_candidate: dict[str, list[dict]] = {}
    urls_to_fetch_meta: set[str] = set()

    for src in official_sources:
        cid   = src.get("candidate_id", "")
        stype = src.get("source_type")

        if stype == "official":
            if src.get("validated") and src.get("primary_url"):
                url = src["primary_url"]
                official_by_candidate.setdefault(cid, []).append({
                    "url":    url,
                    "origin": "official_source",
                })
                urls_to_fetch_meta.add(url)
        elif stype == "reference":
            for ref in src.get("reference_sources", []):
                if not ref.get("validated"):
                    continue
                url = ref.get("final_url") or ref.get("url", "")
                if url:
                    official_by_candidate.setdefault(cid, []).append({
                        "url":    url,
                        "origin": "official_source",
                    })
                    urls_to_fetch_meta.add(url)

    # 2) Brave 검색 URL은 이미 title·description 포함 — meta fetch 불필요
    #    (단 추가로 validated 여부는 LLM 판단에 맡김)

    # 3) official URL의 page meta 병렬 수집
    meta_by_url = _collect_page_meta(urls_to_fetch_meta)

    # 4) candidate별로 URL 병합
    all_candidate_ids = set(official_by_candidate.keys()) | set(brave_urls_by_candidate.keys())
    for cid in sorted(all_candidate_ids):
        validated_urls: list[dict] = []
        # 4-a) official_source URL + meta
        for item in official_by_candidate.get(cid, []):
            url  = item["url"]
            meta = meta_by_url.get(url, {})
            validated_urls.append({
                "url":              url,
                "page_title":       meta.get("page_title", ""),
                "meta_description": meta.get("meta_description", ""),
                "origin":           "official_source",
            })
        # 4-b) Brave 발견 URL (title·description 포함, dedup)
        seen_urls = {u["url"] for u in validated_urls}
        for item in brave_urls_by_candidate.get(cid, []):
            if item["url"] in seen_urls:
                continue
            validated_urls.append({
                "url":                  item["url"],
                "page_title":           item.get("page_title", ""),
                "meta_description":     item.get("meta_description", ""),
                "origin":               "brave_search",
                "matched_report_types": item.get("matched_report_types", []),
            })
            seen_urls.add(item["url"])

        # source_type 추정: official URL이 있으면 official, 아니면 reference
        source_type = "official" if cid in official_by_candidate else "reference"
        candidates_out.append({
            "candidate_id":   cid,
            "source_type":    source_type,
            "validated_urls": validated_urls,
        })
    return candidates_out


def _collect_page_meta(urls: set[str]) -> dict[str, dict]:
    """병렬 HTTP GET으로 page_title + meta_description 수집."""
    if not urls:
        return {}
    meta_by_url: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        future_map = {pool.submit(_fetch_meta, url): url for url in urls}
        for future in as_completed(future_map):
            url = future_map[future]
            try:
                meta = future.result()
            except Exception as exc:  # noqa: BLE001
                logger.debug("page meta 수집 예외 (%s): %s", url, exc)
                meta = {"page_title": "", "meta_description": ""}
            meta_by_url[url] = meta
    return meta_by_url


def _fetch_meta(url: str) -> dict:
    """
    URL 의 <title> + <meta description> 수집 (v0.10.12 B-2 24h TTL 캐시 적용).

    캐시 조회 → 미스 시 HTTP GET → 결과 저장. 동일 URL 에 대해 24시간 이내에는
    동일한 page_title·meta_description 반환. page meta 의 페이지 운영자 미세 수정
    영향을 흡수하고, page_meta_collect_node 의 wall-clock 을 최소화한다.
    """
    # ── 캐시 조회 (v0.10.12) ────────────────────────────────────────────────
    cache_input   = {"url": url}
    cache_context = {"agent_id": "page_meta_collect", "v": 1}
    cached = load_agent_output(
        agent_id="page_meta_collect",
        cache_input=cache_input,
        context=cache_context,
        logger=logger,
        ttl_hours=_NODE_CACHE_TTL_HOURS,
    )
    if cached is not None:
        return {
            "page_title":       cached.get("page_title", ""),
            "meta_description": cached.get("meta_description", ""),
        }

    # ── 캐시 미스 — 실제 HTTP GET ───────────────────────────────────────────
    headers = {"User-Agent": _USER_AGENT, "Accept": "text/html"}
    meta: dict = {"page_title": "", "meta_description": ""}
    try:
        resp = req_lib.get(
            url, headers=headers, timeout=_HTTP_TIMEOUT,
            allow_redirects=True, stream=True,
        )
        if resp.status_code < 200 or resp.status_code >= 400:
            meta = {"page_title": "", "meta_description": ""}
        else:
            ctype = resp.headers.get("Content-Type", "").lower()
            if "html" not in ctype:
                meta = {"page_title": "", "meta_description": ""}
            else:
                raw = b""
                for chunk in resp.iter_content(chunk_size=2048):
                    raw += chunk
                    if len(raw) >= _META_BODY_LIMIT:
                        break
                html_text = raw.decode("utf-8", errors="replace")
                parser = _MetaExtractor()
                parser.feed(html_text)
                meta = {
                    "page_title":       (parser.title or "").strip(),
                    "meta_description": (parser.meta_desc or "").strip(),
                }
    except Exception as exc:  # noqa: BLE001
        logger.debug("_fetch_meta 예외 (%s): %s", url, exc)

    # ── 캐시 저장 (빈 결과도 캐시 — 동일 URL 의 반복 fetch 방지) ─────────────
    try:
        store_agent_output(
            agent_id="page_meta_collect",
            cache_input=cache_input,
            context=cache_context,
            output=meta,
            logger=logger,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("_fetch_meta 캐시 저장 실패 (%s): %s", url, exc)
    return meta


class _MetaExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title: str | None     = None
        self.meta_desc: str | None = None
        self._in_title: bool       = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            attrs_dict = {k.lower(): (v or "") for k, v in attrs}
            name = attrs_dict.get("name", "").lower()
            prop = attrs_dict.get("property", "").lower()
            if (name == "description" or prop == "og:description") and self.meta_desc is None:
                self.meta_desc = attrs_dict.get("content", "")

    def handle_data(self, data: str) -> None:
        if self._in_title and self.title is None:
            s = data.strip()
            if s:
                self.title = s

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False


# ──────────────────────── Step 2: 입력 슬림화 (A안 v0.10.8) ──────────────────

def _filter_candidates_for_report(
    candidates_with_meta: list[dict],
    report_type: str,
) -> list[dict]:
    """
    report_type 단위 LLM 호출 입력용으로 candidates_with_meta 를 슬림화한다.

    설계 의도 (v0.10.8 A안)
    -----------------------
    v0.10.7 까지는 _call_for_report 가 매 report_type 호출에 candidates_with_meta
    전체(자사 + 모든 선택 경쟁사 × validated_urls 전체) 를 통째로 LLM 입력에 포함하여
    activate 리포트 7개 호출 시 동일 brave_search URL 이 최대 6회 중복 전송됨으로써
    토큰을 낭비하고 단일 LLM 호출이 CLI_TIMEOUT(120s) 을 초과할 가능성을 높였다.

    A안: 본 헬퍼가 다음 규칙으로 report_type 별 입력을 슬림화한다.

      - origin == "official_source" URL: 모든 report_type 에 공통이므로 그대로 유지.
        (official_source_resolver 가 검증한 자사·경쟁사 공식 페이지)
      - origin == "brave_search" URL: 본 URL 의 matched_report_types 에 본 report_type 이
        포함된 경우에만 유지. 다른 report_type 에서 발견된 brave URL 은 제외.
      - 유지되는 URL 이 하나도 없는 candidate 는 결과에서 제외하여 LLM 이 빈 candidate 로
        잘못된 매핑(특히 'no_url_available' 잡음) 을 생성하는 것을 방지.

    예상 효과
    ---------
    candidate 4명 × 평균 8 URL → 슬림화 후 평균 3–4 URL. report_type 별 입력에서
    candidates 영역이 약 50–60% 감소. 활성 리포트 7개 합산 시 LLM 호출 총 토큰 약
    30–40% 절감, Step 2 wall-clock 비례 감소 예상.

    Parameters
    ----------
    candidates_with_meta : list[dict]
        Step 1 _build_candidates_with_meta 가 생성한 후보 목록. 각 항목은
        {"candidate_id", "source_type", "validated_urls": [{url, page_title,
        meta_description, origin, [matched_report_types]}]} 구조.
    report_type : str
        REPORT_TYPES (v0.10 D4 enum 7종) 중 하나.

    Returns
    -------
    list[dict]
        슬림화된 candidates 목록. LLM 입력 구조는 candidates_with_meta 와 동일하지만
        validated_urls 길이만 축소됨.
    """
    out: list[dict] = []
    for cand in candidates_with_meta:
        kept_urls: list[dict] = []
        for u in cand.get("validated_urls", []):
            origin = u.get("origin") or ""
            if origin == "official_source":
                # official_source 는 official_source_resolver_node 가 검증한 자사·경쟁사
                # 공식 페이지 — 모든 report_type 에 공통으로 유지.
                kept_urls.append(u)
            else:
                # v0.10.20.1 — 비-official 모든 origin 을 matched_report_types 매칭으로 통과.
                # 옛 v0.10.8 A안은 origin="brave_search" 만 처리했으나, v0.10.20 이후 다음
                # 5종 origin 이 추가되어 matched_report_types 안전망으로 일관 처리:
                #   - "brave_search"            (옛 url_discovery_brave_node · v0.10.19 의 official/blog_community/macro)
                #   - "youtube_reactions"       (v0.10.20 url_discovery_youtube_reactions_node)
                #   - "owned_channel_search"    (v0.10.21 url_discovery_owned_channels_node, 예정)
                #   - "macro_search"            (v0.10.22 url_discovery_macro_node 보강, 예정)
                #   - "official_subpage"·기타   (turn-3 §4-4 system_prompt report_type 별 정책)
                # 옛 주석 "알 수 없는 origin 은 보수적으로 제외" 는 v0.10.20 신규 origin 들이
                # report_type 호출에서 누락되는 회귀를 유발하여 v0.10.20.1 에서 제거.
                if report_type in (u.get("matched_report_types") or []):
                    kept_urls.append(u)
        if kept_urls:
            out.append({
                "candidate_id":   cand["candidate_id"],
                "source_type":    cand.get("source_type", ""),
                "validated_urls": kept_urls,
            })
    return out


# ──────────────────────── Step 3: additional_urls 검증 ───────────────────────

def _validate_additional_urls(raw_features: list[dict]) -> list[AnalysisFeature]:
    tasks: list[tuple[int, int, int, str]] = []
    for fi, feat in enumerate(raw_features):
        for ci, cov in enumerate(feat.get("candidate_coverage", [])):
            for ui, au in enumerate(cov.get("additional_urls", [])):
                url = (au.get("url") or "").strip()
                if url:
                    tasks.append((fi, ci, ui, url))

    if not tasks:
        return [_normalize_feature(f) for f in raw_features]

    val_results: dict[tuple[int, int, int], int | None] = {}
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        future_map = {
            pool.submit(_check_url_status, url): (fi, ci, ui)
            for fi, ci, ui, url in tasks
        }
        for future in as_completed(future_map):
            key = future_map[future]
            try:
                status = future.result()
            except Exception:  # noqa: BLE001
                status = None
            val_results[key] = status

    logger.info("feature_url_mapper_node: additional_urls 검증 완료 (%d개)", len(tasks))

    for fi, ci, ui, _ in tasks:
        status = val_results.get((fi, ci, ui))
        au = raw_features[fi]["candidate_coverage"][ci]["additional_urls"][ui]
        au["validated"]   = bool(status and 200 <= status < 400)
        au["http_status"] = status
    return [_normalize_feature(f) for f in raw_features]


def _check_url_status(url: str) -> int | None:
    """
    URL 도달성 검증 (v0.10.12 B-3 24h TTL 캐시 적용).

    캐시 조회 → 미스 시 HEAD→GET 순차 시도 → 결과 저장. 동일 URL 의 24시간 이내
    검증 결과를 재사용하여 additional_urls_validation_node 의 wall-clock 을 단축한다.
    실패(None) 도 캐시하여 죽은 링크의 반복 검증을 방지한다.
    """
    # ── 캐시 조회 (v0.10.12) ────────────────────────────────────────────────
    cache_input   = {"url": url}
    cache_context = {"agent_id": "url_validation", "v": 1}
    cached = load_agent_output(
        agent_id="url_validation",
        cache_input=cache_input,
        context=cache_context,
        logger=logger,
        ttl_hours=_NODE_CACHE_TTL_HOURS,
    )
    if cached is not None:
        return cached.get("status")

    # ── 캐시 미스 — 실제 HEAD→GET 시도 ──────────────────────────────────────
    headers = {"User-Agent": _USER_AGENT}
    status: int | None = None
    for method in ("HEAD", "GET"):
        try:
            resp = req_lib.request(
                method, url, headers=headers, timeout=_HTTP_TIMEOUT,
                allow_redirects=True, stream=(method == "GET"),
            )
            if method == "HEAD" and resp.status_code == 405:
                continue
            status = resp.status_code
            break
        except req_lib.exceptions.SSLError:
            continue
        except (req_lib.exceptions.ConnectionError, req_lib.exceptions.Timeout):
            status = None
            break
        except Exception as exc:  # noqa: BLE001
            logger.debug("_check_url_status 예외 (%s): %s", url, exc)
            status = None
            break

    # ── 캐시 저장 (None 도 저장 — 죽은 링크 재시도 방지) ─────────────────────
    try:
        store_agent_output(
            agent_id="url_validation",
            cache_input=cache_input,
            context=cache_context,
            output={"status": status},
            logger=logger,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("_check_url_status 캐시 저장 실패 (%s): %s", url, exc)
    return status


def _normalize_feature(raw: dict) -> AnalysisFeature:
    """LLM 출력 정규화: report_type enum 검증, additional_urls 기본값 채움."""
    # report_type enum 검증 (잘못된 값은 'comparison_matrix'로 fallback — 안전판)
    rt = raw.get("report_type", "")
    if rt not in REPORT_TYPES:
        logger.warning(
            "_normalize_feature: 잘못된 report_type '%s' → 'comparison_matrix'로 fallback", rt,
        )
        raw["report_type"] = "comparison_matrix"

    for cov in raw.get("candidate_coverage", []):
        for au in cov.get("additional_urls", []):
            au.setdefault("validated", False)
            au.setdefault("http_status", None)
    return raw  # type: ignore[return-value]


def _strip_schema_patterns(schema: object) -> object:
    if isinstance(schema, dict):
        return {k: _strip_schema_patterns(v) for k, v in schema.items() if k != "pattern"}
    if isinstance(schema, list):
        return [_strip_schema_patterns(item) for item in schema]
    return schema


# ─────────────────────────────────────────────────────── 내부 헬퍼 ───────────

def _load_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("파일 없음: %s", path)
        return None


def _load_json(path: Path) -> dict | None:
    text = _load_text(path)
    if text is None:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("JSON 파싱 실패 (%s): %s", path, exc)
        return None


def _error(started_at: str, message: str) -> dict:
    logger.error("feature_url_mapper_node 오류: %s", message)
    return {
        "errors": [{
            "node":      "feature_url_mapper_node",
            "error":     message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }],
        "agent_steps": [{
            "step_name":     "FeatureUrlMapper",
            "status":        "failed",
            "started_at":    started_at,
            "finished_at":   datetime.now(timezone.utc).isoformat(),
            "error_message": message,
        }],
    }
