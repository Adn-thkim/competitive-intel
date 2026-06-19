"""
server/graph/nodes/official_content_collection_node.py (Step 0 구현)
---------------------------------------------------------------------
report generation 시리즈 1단계 — official 출처 수집 노드.
설계: docs/design/feature_extraction_node_design.md (FE-D1~D10)

구현 현황
---------
- Step 0 — 입력 게이트 + extraction_targets 구성 (§4 · §5-1). 순수 함수.
- Step 1 — `_fetch_content` (requests + Trafilatura/BS4 폴백 + pypdf, 전문 캐시
  24h TTL, SPA 분류) + `_build_excerpt` (키워드 근접 발췌, FE-D9·FE-D5 v3 예산).
- Step 2 — `run_llm_extraction` (ClaudeApiAnalyzer temperature=0, candidate당
  1회·병렬 4, agent 캐시 키 = candidate + feature 집합 + 발췌 해시. FE-D4·FE-D8).
  키워드 풀 = 보수적 방식 (선택 feature 전체 어휘 + 정적 sub-page 7종).
- Step 3 — `assemble_feature_pool` (feature × candidate 2단계 피벗, §6-1 계약 ·
  누락 셀 0건 보장 · is_promotional/valid_until 보존 FE-D12) + `product_profiles`
  (§6-2) + 관측성 (`data/collection/official_content_collection/{run_id}/` raw 응답
  + dynamic_render_backlog.json — v0.11 Playwright 입력 목록).

출력 state 키 (§6)
------------------
- feature_pool      : {feature_id: {candidate_id: {value, value_numeric, unit, as_of,
                       extraction_status, evidence, source_url, source_origin,
                       confidence, is_promotional, valid_until}}}
- product_profiles  : [{candidate_id, product_name, profile_summary, sources_used,
                       fetch_failures, needs_manual_review}]
- agent_steps / errors (누적 reducer)

위치 (graph.py — §2-3, 아직 미배선)
-----------------------------------
feature_selection (interrupt #4)
  → [official_content_collection_node]   ← 이 노드
  → comparison_matrix

입력 state 키 (§4-1)
--------------------
- analysis_features       : 추출 대상 feature × candidate × URL 원천
- selected_purposes       : "comparison_matrix" 포함 여부 — 활성 게이트
- selected_feature_ids    : interrupt #4 사용자 선택 feature 필터
- official_sources        : candidate별 official_domain 산출 (additional_urls 게이트)
- own_product / competitor_candidates / selected_competitor_ids : 명칭 매핑

출력 state 키 (§6 — Step 3 구현 시)
-----------------------------------
- feature_pool / product_profiles / agent_steps / errors

URL 채택 게이트 (§4-3)
----------------------
- existing_urls   : origin ∈ {official_source, official_subpage} 만 통과
- additional_urls : validated=True AND source_origin="official_subpage"
                    AND host가 candidate official_domain suffix 매칭.
                    official_domain 미확정 candidate(func_* 등)는 보수적으로 전부 차단.
- coverage="not_found" candidate: URL 0건으로 유지 (Step 3에서 extraction_status
  ="not_found" 마킹 대상 — extraction_targets에서 제외하지 않는다).
- 우선순위 tier:
    0 = official_source (carry-through primary)
    1 = official_subpage, subpage_category가 선택 feature 텍스트와 관련
    2 = official_subpage, 무관 카테고리 또는 "hint"/빈 카테고리
    3 = additional (검증 통과)
  동일 URL이 여러 feature에서 등장하면 최저 tier 1건만 유지하되 feature 연관은 union.

URL 상한 — 쌍 단위 (FE-D5 v3, 사용자 확정 2026-06-04)
-----------------------------------------------------
상한은 (feature × candidate) **쌍 단위**로 적용한다:
- 쌍당 최소 1 (게이트 통과 URL이 존재하는 한) · 최대 5 (_MAX_URLS_PER_PAIR).
  쌍별로 (tier, url) 정렬 상위 5건 채택 후 candidate 단위 union (dedup).
- candidate당 안전 상한 25 (_MAX_URLS_PER_CANDIDATE) — 비정상 입력 보호용.
  초과 시 coverage-aware 2단계 trim (_select_urls: greedy set cover → tier 충원)
  으로 쌍별 최소 1 보장을 유지하며 절단.
- Step 2 LLM 입력 비용은 URL 수가 아니라 candidate당 발췌 총예산(§5-2a, 30,000자)
  으로 통제한다.
v2 (candidate당 총 5) 는 실데이터 검증에서 상한 내 전 쌍 커버가 수학적으로 불가한
candidate 가 확인되어 폐기 (설계 문서 FE-D5 이력 참조).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from io import BytesIO

import requests
import trafilatura

from server.config import AGENTS_DIR, API_MODEL
from server.cache_ttl import get_ttl_hours
from server.graph.agent_cache import (
    load_agent_output,
    make_cache_context,
    store_agent_output,
)
from server.graph.state import AgentStep, DomainAnalysisState
from server.graph.nodes.feature_url_mapper_node import (
    _candidate_name_map,
    _load_json,
    _load_text,
)
from server.graph.nodes.url_discovery_official_node import (
    _OFFICIAL_SUBPAGE_KEYWORDS,
    _extract_official_domain,
    _host_endswith_any,
)

logger = logging.getLogger(__name__)

REPORT_TYPE = "comparison_matrix"

_MAX_URLS_PER_PAIR      = 5          # FE-D5 v3 — (feature × candidate) 쌍당 상한
_MAX_URLS_PER_CANDIDATE = 25         # FE-D5 v3 — candidate당 안전 상한 (보조)
_OFFICIAL_EXISTING_ORIGINS = ("official_source", "official_subpage")
_ADDITIONAL_SOURCE_ORIGIN = "official_subpage"

# tier 상수 (모듈 docstring 참조)
_TIER_PRIMARY            = 0
_TIER_SUBPAGE_RELEVANT   = 1
_TIER_SUBPAGE_OTHER      = 2
_TIER_ADDITIONAL         = 3

# ── Step 1: 콘텐츠 수집 상수 (§5-2, FE-D5 v3·FE-D10) ─────────────────────────
_FETCH_AGENT_ID        = "official_content_fetch"
_FETCH_CACHE_TTL_HOURS = get_ttl_hours("official_content_fetch_hours", 720)  # cache_ttls.yaml
_FETCH_TIMEOUT         = (3, 10)      # (connect, read) — 코드베이스 requests 규약
_FETCH_USER_AGENT      = "Mozilla/5.0 (compatible; OfficialContentCollector/1.0)"
_FETCH_MAX_WORKERS     = 5
_FULLTEXT_CAP          = 50_000       # 전문 안전 상한 (LLM 입력 상한과 무관)
_PDF_MAX_PAGES         = 50
_SPA_MIN_CHARS         = 200          # 미만이면 requires_dynamic_render

# 캐시 컨텍스트 — 추출 정책 상수가 바뀌면 캐시 자동 무효화
_FETCH_CACHE_CONTEXT = {
    "agent_id": _FETCH_AGENT_ID,
    "v": 1,
    "fulltext_cap": _FULLTEXT_CAP,
    "pdf_max_pages": _PDF_MAX_PAGES,
    "extractor": "trafilatura-markdown+bs4-fallback",
}

# ── Step 1.5: 발췌 상수 (§5-2a, FE-D9) ───────────────────────────────────────
_PAGE_EXCERPT_BUDGET      = 6_000     # 페이지당 발췌 상한
_CANDIDATE_EXCERPT_BUDGET = 30_000    # candidate당 발췌 총예산 (FE-D5 v3)
_EXCERPT_HEADER_CHARS     = 500       # 항상 포함: 문서 헤더
_EXCERPT_WINDOW_CHARS     = 300       # 키워드 매칭 지점 전후 윈도우
_EXCERPT_MAX_MATCHES_PER_KEYWORD = 10
_EXCERPT_OMIT_MARKER      = "\n[... 본문 일부 생략 ...]\n"

# ── Step 2: LLM 추출 상수 (§5-3, FE-D4·FE-D8) ────────────────────────────────
_LLM_AGENT_ID    = "official_content_collection"
# parallel=1 (FE-D8 v2, 2026-06-06): candidate 1건 입력이 ~22k tok 이고 조직 ITPM 이
# 30k 라, 동시 발사는 즉시 429 를 유발한다. 직렬 처리 + ClaudeApiAnalyzer 의
# retry-after 백오프(429 시 대기 후 재발사)로 한도 내 결정론적 완료를 보장한다.
_LLM_PARALLEL    = 1
_LLM_MAX_TOKENS  = 8_000
_LLM_TIMEOUT_SEC = 180

# 키워드 토큰 분리 (발췌 키워드 풀 — 형태소 분석 없이 결정론적 분리)
_TOKEN_SPLIT_RE = re.compile(r"[\s/·,()\[\]{}<>~%:;'\"!?．。]+")


# ─── Step 0 순수 함수 ────────────────────────────────────────────────────────

def _official_urls_of(src: dict) -> list[str]:
    """official source 의 검증된 공식 URL 목록 (복수 도메인 지원, 하위호환).

    신규 필드 `official_urls`(검증된 공식 URL 전부, primary 포함)를 우선 사용하고,
    부재 시(기존 캐시·데이터) `primary_url` 단일로 폴백한다.
    """
    urls = src.get("official_urls")
    if isinstance(urls, list) and urls:
        return [u for u in urls if u]
    primary = src.get("primary_url")
    return [primary] if primary else []


def _official_domain_map(official_sources: list[dict]) -> dict[str, set[str]]:
    """candidate_id → 허용 공식 도메인 집합 (validated official 기준).

    한 상품이 복수 공식 도메인(예: tossbank.com + toss.im)을 가질 수 있으므로
    집합으로 산출한다. reference source는 공식 도메인이 아니므로 게이트 기준에서 제외.
    """
    domains: dict[str, set[str]] = {}
    for src in official_sources or []:
        if src.get("source_type") == "official" and src.get("validated"):
            cid = src.get("candidate_id", "")
            if not cid:
                continue
            for url in _official_urls_of(src):
                domain = _extract_official_domain(url)
                if domain:
                    domains.setdefault(cid, set()).add(domain)
    return domains


def _subpage_tier(subpage_category: str, feature_text: str) -> int:
    """official_subpage URL의 tier 판정 — 카테고리가 feature 텍스트와 관련이면 1."""
    if subpage_category and subpage_category != "hint" and subpage_category in feature_text:
        return _TIER_SUBPAGE_RELEVANT
    return _TIER_SUBPAGE_OTHER


def _gate_coverage_urls(
    cov: dict, official_domains: set[str], feature_text: str
) -> list[tuple[int, dict]]:
    """단일 candidate_coverage 항목에서 (tier, url_entry) 목록 산출 (§4-3 게이트).

    coverage="not_found" 는 빈 목록 — candidate 자체는 상위에서 유지된다.
    """
    if cov.get("coverage") == "not_found":
        return []

    gated: list[tuple[int, dict]] = []

    for u in cov.get("existing_urls") or []:
        url = (u.get("url") or "").strip()
        origin = u.get("origin", "")
        if not url or origin not in _OFFICIAL_EXISTING_ORIGINS:
            continue
        category = u.get("subpage_category", "")
        tier = (
            _TIER_PRIMARY
            if origin == "official_source"
            else _subpage_tier(category, feature_text)
        )
        gated.append((tier, {
            "url":              url,
            "origin":           origin,
            "subpage_category": category,
            "page_title":       u.get("page_title", ""),
        }))

    for au in cov.get("additional_urls") or []:
        url = (au.get("url") or "").strip()
        if not url or not au.get("validated"):
            continue
        if au.get("source_origin", "") != _ADDITIONAL_SOURCE_ORIGIN:
            continue
        # official_domain 미확정 candidate는 보수적으로 차단 (모듈 docstring)
        if not official_domains or not _host_endswith_any(url, official_domains):
            continue
        gated.append((_TIER_ADDITIONAL, {
            "url":              url,
            "origin":           "additional_validated",
            "subpage_category": "",
            "page_title":       "",
        }))

    return gated


def _select_urls(records: list[dict], cap: int = _MAX_URLS_PER_CANDIDATE) -> list[dict]:
    """coverage-aware trim (FE-D5 v3에서는 안전 상한 25 초과 시에만 사용) — 결정론적 2단계.

    records: [{"tier": int, "entry": dict, "features": set[str]}]
    1단계: greedy set cover — 미커버 feature 최다 커버 URL부터 채택.
    2단계: 잔여 슬롯을 (tier, url) 순으로 충원.
    반환: (tier, url) 정렬된 entry 목록 (상한 cap).
    """
    if not records:
        return []
    remaining = sorted(records, key=lambda r: (r["tier"], r["entry"]["url"]))
    uncovered: set[str] = set().union(*(r["features"] for r in records))
    selected: list[dict] = []

    while uncovered and len(selected) < cap:
        best = min(
            remaining,
            key=lambda r: (-len(r["features"] & uncovered), r["tier"], r["entry"]["url"]),
        )
        if not best["features"] & uncovered:
            break  # 남은 URL이 미커버 feature를 더 줄이지 못함
        selected.append(best)
        remaining.remove(best)
        uncovered -= best["features"]

    for r in remaining:
        if len(selected) >= cap:
            break
        selected.append(r)

    return [
        _entry_with_features(r)
        for r in sorted(selected, key=lambda r: (r["tier"], r["entry"]["url"]))
    ]


def _entry_with_features(record: dict) -> dict:
    """URL entry에 연관 feature_ids 노출 — Step 2 프롬프트·Step 3 출처 추적·검증용."""
    return {**record["entry"], "feature_ids": sorted(record["features"])}


def build_extraction_targets(state: dict) -> list[dict]:
    """§4 필터 3단계 + §5-1 candidate 피벗 — Step 0 진입점 (순수 함수).

    필터:
      [1] report_type == "comparison_matrix" AND comparison_matrix ∈ selected_purposes
      [2] feature_id ∈ selected_feature_ids
      [3] candidate_coverage 내 URL 게이트 (_gate_coverage_urls)

    URL 선택 (FE-D5 v3): 쌍당 (tier, url) 상위 _MAX_URLS_PER_PAIR 건 채택 →
    candidate 단위 union(dedup) → 안전 상한 초과 시에만 coverage-aware trim.

    반환 (candidate_id 정렬, URL은 tier → url 정렬):
      [{candidate_id, candidate_name, feature_ids, urls: [{url, origin,
        subpage_category, page_title, feature_ids}]}]
    URL 항목의 feature_ids = 이 URL을 게이트 통과시킨 feature 목록 (정렬) —
    Step 2 프롬프트의 "이 페이지에서 찾을 feature" 단서 + Step 3 출처 추적에 사용.
    """
    selected_purposes: list[str] = state.get("selected_purposes") or []
    if REPORT_TYPE not in selected_purposes:
        return []

    selected_feature_ids = set(state.get("selected_feature_ids") or [])
    analysis_features: list[dict] = state.get("analysis_features") or []
    if not selected_feature_ids or not analysis_features:
        return []

    domain_map = _official_domain_map(state.get("official_sources") or [])
    name_map = _candidate_name_map(
        state.get("own_product") or {},
        state.get("competitor_candidates") or [],
        state.get("selected_competitor_ids") or [],
    )

    # candidate_id → {"feature_ids": [...], "urls": {url: (tier, entry)}}
    per_candidate: dict[str, dict] = {}

    for feat in analysis_features:
        if feat.get("report_type") != REPORT_TYPE:
            continue
        fid = feat.get("feature_id", "")
        if fid not in selected_feature_ids:
            continue

        feature_text = " ".join((
            feat.get("feature_name", ""),
            feat.get("description", ""),
            fid,
        ))

        for cov in feat.get("candidate_coverage") or []:
            cid = cov.get("candidate_id", "")
            if not cid:
                continue
            target = per_candidate.setdefault(cid, {"feature_ids": [], "urls": {}})
            if fid not in target["feature_ids"]:
                target["feature_ids"].append(fid)

            # 쌍당 상한 5: 이 (fid × cid) 쌍의 게이트 통과 URL 중 (tier, url) 상위만 채택
            gated = sorted(
                _gate_coverage_urls(cov, domain_map.get(cid) or set(), feature_text),
                key=lambda te: (te[0], te[1]["url"]),
            )[:_MAX_URLS_PER_PAIR]

            for tier, entry in gated:
                rec = target["urls"].get(entry["url"])
                if rec is None:
                    target["urls"][entry["url"]] = {
                        "tier": tier, "entry": entry, "features": {fid},
                    }
                else:
                    rec["features"].add(fid)
                    if tier < rec["tier"]:
                        rec["tier"], rec["entry"] = tier, entry

    targets: list[dict] = []
    for cid in sorted(per_candidate):
        records = list(per_candidate[cid]["urls"].values())
        if len(records) > _MAX_URLS_PER_CANDIDATE:
            # 안전 상한 초과 (비정상 입력) — coverage-aware trim으로 쌍별 최소 1 유지
            urls = _select_urls(records)
        else:
            urls = [
                _entry_with_features(r)
                for r in sorted(records, key=lambda r: (r["tier"], r["entry"]["url"]))
            ]
        targets.append({
            "candidate_id":   cid,
            "candidate_name": name_map.get(cid, ""),
            "feature_ids":    per_candidate[cid]["feature_ids"],
            "urls":           urls,
        })
    return targets


# ─── Step 1: 콘텐츠 수집 (§5-2 — Trafilatura/BS4 폴백 + pypdf + 24h 캐시) ────

def _bs4_fallback_text(html: str) -> str:
    """Trafilatura 실패·빈약 추출 시 BeautifulSoup 폴백 — boilerplate 제거 후 본문."""
    from bs4 import BeautifulSoup  # 지연 import — 폴백 경로에서만 필요

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "noscript", "iframe"]):
        tag.decompose()
    return soup.get_text("\n", strip=True)


def _extract_html_text(html: str) -> str:
    """HTML → 본문 텍스트. Trafilatura(markdown, 표 보존) 우선 + BS4 폴백 (FE-D10)."""
    text = ""
    try:
        text = trafilatura.extract(
            html,
            include_tables=True,
            favor_recall=True,
            output_format="markdown",
        ) or ""
    except Exception as exc:  # noqa: BLE001
        logger.debug("trafilatura.extract 실패: %s", exc)

    if len(text) >= _SPA_MIN_CHARS:
        return text
    try:
        fallback = _bs4_fallback_text(html)
    except Exception as exc:  # noqa: BLE001
        logger.debug("BS4 폴백 실패: %s", exc)
        fallback = ""
    # 둘 다 빈약하면 긴 쪽 반환 — SPA 판정은 _fetch_content 의 책임
    return fallback if len(fallback) > len(text) else text


def _extract_pdf_text(content: bytes) -> str:
    """PDF bytes → 텍스트. 안전 상한 _PDF_MAX_PAGES 페이지 (FE-D5 v3 완화 반영)."""
    from pypdf import PdfReader  # 지연 import

    reader = PdfReader(BytesIO(content), strict=False)
    chunks: list[str] = []
    for page in reader.pages[:_PDF_MAX_PAGES]:
        try:
            chunks.append(page.extract_text() or "")
        except Exception as exc:  # noqa: BLE001
            logger.debug("PDF 페이지 추출 실패: %s", exc)
    return "\n".join(c for c in chunks if c)


def _is_pdf_response(url: str, content_type: str) -> bool:
    if "application/pdf" in content_type:
        return True
    return url.lower().split("?", 1)[0].endswith(".pdf")


def _fetch_content(url: str) -> dict:
    """URL → 전문(full text) 수집 (§5-2). 절단은 _build_excerpt 의 책임 (FE-D9).

    반환: {"url", "fetch_status": "ok" | "requires_dynamic_render" | "fetch_failed",
           "content": str (전문, 상한 _FULLTEXT_CAP), "error": str}
    캐시: agent_cache 24h TTL, 키 = URL. ok·requires_dynamic_render 만 저장
          (fetch_failed 는 일시 장애일 수 있어 캐시하지 않음 — 재시도 허용).
    """
    cache_input = {"url": url}
    cached = load_agent_output(
        agent_id=_FETCH_AGENT_ID, cache_input=cache_input,
        context=_FETCH_CACHE_CONTEXT, ttl_hours=_FETCH_CACHE_TTL_HOURS,
        logger=logger,
    )
    if cached is not None:
        # from_cache: 호출자가 네트워크 발생 여부를 알 수 있게 표시
        # (community_collection 의 rate limit 은 실제 네트워크 호출에만 적용 — D11)
        return {**cached, "from_cache": True}

    try:
        resp = requests.get(
            url, timeout=_FETCH_TIMEOUT,
            headers={"User-Agent": _FETCH_USER_AGENT},
        )
    except Exception as exc:  # noqa: BLE001
        return {"url": url, "fetch_status": "fetch_failed", "content": "",
                "error": f"{type(exc).__name__}: {exc}"}

    if resp.status_code >= 400:
        return {"url": url, "fetch_status": "fetch_failed", "content": "",
                "error": f"http_status={resp.status_code}"}

    content_type = (resp.headers.get("Content-Type") or "").lower()
    try:
        if _is_pdf_response(url, content_type):
            text = _extract_pdf_text(resp.content)
        else:
            text = _extract_html_text(resp.text)
    except Exception as exc:  # noqa: BLE001
        return {"url": url, "fetch_status": "fetch_failed", "content": "",
                "error": f"extract: {type(exc).__name__}: {exc}"}

    text = text[:_FULLTEXT_CAP]
    status = "ok" if len(text) >= _SPA_MIN_CHARS else "requires_dynamic_render"
    result = {"url": url, "fetch_status": status, "content": text, "error": ""}
    store_agent_output(
        agent_id=_FETCH_AGENT_ID, cache_input=cache_input,
        context=_FETCH_CACHE_CONTEXT, output=result, logger=logger,
    )
    return result


def _fetch_all(urls: list[str]) -> dict[str, dict]:
    """병렬 fetch (worker 5 — url_discovery_official 과 동일 규약). url → 결과 dict."""
    results: dict[str, dict] = {}
    if not urls:
        return results
    with ThreadPoolExecutor(max_workers=_FETCH_MAX_WORKERS) as pool:
        for result in pool.map(_fetch_content, sorted(set(urls))):
            results[result["url"]] = result
    return results


# ─── Step 1.5: 키워드 근접 발췌 (§5-2a, FE-D9) ───────────────────────────────

def _page_excerpt_budget(n_pages: int) -> int:
    """FE-D5 v3 예산 배분: min(페이지당 6,000자, 30,000자 / 페이지 수)."""
    if n_pages <= 0:
        return _PAGE_EXCERPT_BUDGET
    return min(_PAGE_EXCERPT_BUDGET, max(1, _CANDIDATE_EXCERPT_BUDGET // n_pages))


def _merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _build_excerpt(content: str, keywords: list[str],
                   budget: int = _PAGE_EXCERPT_BUDGET) -> str:
    """결정론적 키워드 근접 발췌 (§5-2a). 전문이 예산 이하면 그대로 통과.

    1. 항상 포함: 문서 헤더(첫 500자) + 헤딩 라인(markdown '#' 시작).
    2. 키워드 매칭 지점 전후 ±300자 윈도우, 중첩 병합.
    3. 예산 초과 시 매칭 키워드 종류가 많은 윈도우 우선. 생략 구간에 마커 삽입.
    4. 결정론 보장 — 발췌 해시가 LLM 캐시 키 구성요소 (§5-3).
    """
    if len(content) <= budget:
        return content

    # 1) mandatory: 헤더 + 헤딩 라인
    mandatory: list[tuple[int, int]] = [(0, min(_EXCERPT_HEADER_CHARS, len(content)))]
    for m in re.finditer(r"^#{1,6}[^\n]*", content, flags=re.MULTILINE):
        mandatory.append((m.start(), m.end()))
    mandatory = _merge_intervals(mandatory)

    # 2) 키워드 윈도우 (키워드 정렬 → 입력 순서 무관 결정론)
    windows: list[tuple[int, int]] = []
    kw_set = sorted({k.strip() for k in keywords if k and k.strip()})
    for kw in kw_set:
        for i, m in enumerate(re.finditer(re.escape(kw), content)):
            if i >= _EXCERPT_MAX_MATCHES_PER_KEYWORD:
                break
            windows.append((max(0, m.start() - _EXCERPT_WINDOW_CHARS),
                            min(len(content), m.end() + _EXCERPT_WINDOW_CHARS)))
    windows = _merge_intervals(windows)

    # 윈도우 점수: 포함된 키워드 종류 수 (내림차순) → 시작 위치 (오름차순)
    def _score(iv: tuple[int, int]) -> int:
        seg = content[iv[0]:iv[1]]
        return sum(1 for kw in kw_set if kw in seg)

    ranked = sorted(windows, key=lambda iv: (-_score(iv), iv[0]))

    # 3) 예산 내 채택 — mandatory 우선, 이후 점수순 윈도우
    marker_len = len(_EXCERPT_OMIT_MARKER)
    selected = list(mandatory)
    used = sum(e - s for s, e in selected) + marker_len * len(selected)
    for iv in ranked:
        size = (iv[1] - iv[0]) + marker_len
        if used + size > budget:
            continue
        selected.append(iv)
        used += size

    # 4) 조립: 위치순 병합 + 생략 마커
    selected = _merge_intervals(selected)
    parts: list[str] = []
    prev_end = 0
    for start, end in selected:
        if start > prev_end:
            parts.append(_EXCERPT_OMIT_MARKER)
        parts.append(content[start:end])
        prev_end = end
    if prev_end < len(content):
        parts.append(_EXCERPT_OMIT_MARKER)
    return "".join(parts)[:budget]


# ─── Step 2: LLM 추출 (§5-3 — ClaudeApiAnalyzer + agent 캐시) ────────────────

def _features_meta(state: dict) -> list[dict]:
    """selected_feature_ids 에 해당하는 comparison_matrix feature 정의 (입력 features 항목)."""
    selected = set(state.get("selected_feature_ids") or [])
    metas: list[dict] = []
    seen: set[str] = set()
    for feat in state.get("analysis_features") or []:
        fid = feat.get("feature_id", "")
        if feat.get("report_type") != REPORT_TYPE or fid not in selected or fid in seen:
            continue
        seen.add(fid)
        metas.append({
            "feature_id":   fid,
            "feature_name": feat.get("feature_name", ""),
            "description":  feat.get("description", ""),
            "priority":     feat.get("priority", "medium"),
        })
    return metas


def _build_keyword_pool(state: dict, features_meta: list[dict]) -> list[str]:
    """발췌 키워드 풀 (§5-2a 2번) — 보수적 방식: 선택 feature 전체 어휘 사용.

    구성: 정적 sub-page 키워드 7종 + report_config feature_labels·categories 토큰
    + feature_name·description 토큰 (2자 이상, 공백·구두점 분리 — 결정론).
    발췌 예산(§5-2a 0번)이 입력 크기를 통제하므로 키워드 과다는 비용에 무해.
    """
    kws: set[str] = set(_OFFICIAL_SUBPAGE_KEYWORDS)
    entry = ((state.get("domain_taxonomy") or {}).get("report_config") or {}) \
        .get(REPORT_TYPE) or {}
    texts: list[str] = [str(v) for v in (entry.get("feature_labels") or {}).values()]
    texts += [str(c) for c in entry.get("categories") or []]
    for f in features_meta:
        texts += [f["feature_name"], f["description"]]
    for text in texts:
        kws.update(t for t in _TOKEN_SPLIT_RE.split(text) if len(t) >= 2)
    return sorted(kws)


def _build_candidate_payload(
    target: dict, features_meta: list[dict], keywords: list[str]
) -> tuple[dict | None, dict]:
    """Step 1 실행 + input.schema.json 페이로드 조립.

    반환: (payload | None, fetch_results). ok 페이지 0건이면 payload=None
    (Step 3 에서 전 feature not_found 처리 — §7 부분 실패 정책).
    """
    fetch_results = _fetch_all([u["url"] for u in target["urls"]])
    ok_entries = [
        u for u in target["urls"]
        if fetch_results.get(u["url"], {}).get("fetch_status") == "ok"
    ]
    if not ok_entries:
        return None, fetch_results

    budget = _page_excerpt_budget(len(ok_entries))
    pages = [
        {
            "url":              u["url"],
            "origin":           u["origin"],
            "subpage_category": u.get("subpage_category", ""),
            "feature_ids":      u.get("feature_ids", []),
            "excerpt":          _build_excerpt(
                fetch_results[u["url"]]["content"], keywords, budget=budget),
        }
        for u in ok_entries
    ]
    target_features = [
        f for f in features_meta if f["feature_id"] in set(target["feature_ids"])
    ]
    payload = {
        "candidate_id":   target["candidate_id"],
        "candidate_name": target["candidate_name"] or target["candidate_id"],
        "report_type":    REPORT_TYPE,
        "features":       target_features,
        "pages":          pages,
    }
    return payload, fetch_results


def _payload_cache_input(payload: dict) -> dict:
    """LLM 캐시 키 입력 (§5-3) — candidate + feature 집합 + URL별 발췌 해시."""
    return {
        "candidate_id": payload["candidate_id"],
        "feature_ids":  sorted(f["feature_id"] for f in payload["features"]),
        "pages": [
            {"url": p["url"],
             "excerpt_sha256": hashlib.sha256(p["excerpt"].encode("utf-8")).hexdigest()}
            for p in sorted(payload["pages"], key=lambda p: p["url"])
        ],
    }


def _wrap_bare_features(candidate_id: str):
    """call_with_schema repair 훅 — 래퍼 객체 누락 복구.

    모델이 최상위 객체(candidate_id·extracted_features·profile_summary·conflicts)를
    생략하고 `extracted_features` 배열만 단독 반환하는 구조 일탈만 정규 객체로 감싼다.
    (출력이 JSON 문자열 한 덩어리로 직렬화된 경우는 어댑터의 _fix_string_encoded_fields
    가 repair 보다 먼저 배열로 디코딩하므로, 이 훅은 배열만 처리하면 된다.)
    배열 항목의 내용은 손대지 않으므로, 항목이 item schema(feat_ 패턴·required 등)를
    위반하면 이후 jsonschema.validate 가 정상적으로 실패시킨다(값 날조 방지).
    """
    def _repair(parsed):
        if isinstance(parsed, list):
            logger.warning(
                "official_content_collection: bare array 응답 구조 복구 "
                "(candidate=%s, items=%d)", candidate_id, len(parsed))
            return {
                "candidate_id":       candidate_id,
                "extracted_features": parsed,
                "profile_summary":    "",
                "conflicts":          [],
            }
        return parsed
    return _repair


def _load_llm_assets() -> tuple[str, dict]:
    agent_dir = AGENTS_DIR / _LLM_AGENT_ID
    system_prompt = _load_text(agent_dir / "system_prompt_kr.md")
    output_schema = _load_json(agent_dir / "output.schema.json")
    if system_prompt is None or output_schema is None:
        raise RuntimeError(f"agents/{_LLM_AGENT_ID}/ 의 prompt·schema 로드 실패")
    return system_prompt, output_schema


def run_llm_extraction(
    state: dict,
    analyzer=None,
    only_candidates: set[str] | None = None,
) -> tuple[list[dict], list[dict], dict]:
    """Step 1+2 실행 — candidate 단위 병렬 LLM 추출 (FE-D4·FE-D8).

    Parameters
    ----------
    analyzer : call_with_schema 인터페이스 객체 | None
        None 이면 ClaudeApiAnalyzer(temperature=0) 생성. 테스트는 fake 주입.
    only_candidates : 대상 candidate_id 제한 (프로파일링용).

    반환: (results, errors, stats)
      results 항목: {"candidate_id", "output": dict|None, "no_content": bool,
                     "pages_used": [url], "fetch_failures": [url], "from_cache": bool}
      stats: {"llm_calls", "cache_hits", "skipped_no_pages"}
    """
    targets = build_extraction_targets(state)
    if only_candidates is not None:
        targets = [t for t in targets if t["candidate_id"] in only_candidates]
    features_meta = _features_meta(state)
    keywords = _build_keyword_pool(state, features_meta)
    system_prompt, output_schema = _load_llm_assets()

    if analyzer is None:
        from server.llm.claude_api_analyzer import ClaudeApiAnalyzer  # 지연 import
        analyzer = ClaudeApiAnalyzer(
            system_prompt=system_prompt,
            max_tokens=_LLM_MAX_TOKENS,
            timeout=_LLM_TIMEOUT_SEC,
        )
    context = make_cache_context(
        agent_id=_LLM_AGENT_ID,
        model=getattr(analyzer, "model", API_MODEL),
        system_prompt=system_prompt,
        output_schema=output_schema,
    )

    errors: list[dict] = []
    stats = {"llm_calls": 0, "cache_hits": 0, "skipped_no_pages": [],
             "targets": targets}

    def _one(target: dict) -> dict:
        cid = target["candidate_id"]
        result = {"candidate_id": cid, "output": None, "no_content": False,
                  "pages_used": [], "fetch_failures": [], "dynamic_render": [],
                  "from_cache": False}
        try:
            payload, fetch_results = _build_candidate_payload(
                target, features_meta, keywords)
            result["fetch_failures"] = sorted(
                url for url, r in fetch_results.items()
                if r["fetch_status"] == "fetch_failed")
            result["dynamic_render"] = sorted(
                url for url, r in fetch_results.items()
                if r["fetch_status"] == "requires_dynamic_render")
            if payload is None or not payload["features"]:
                result["no_content"] = True
                stats["skipped_no_pages"].append(cid)
                return result
            result["pages_used"] = [p["url"] for p in payload["pages"]]

            cache_input = _payload_cache_input(payload)
            cached = load_agent_output(
                agent_id=_LLM_AGENT_ID, cache_input=cache_input,
                context=context, output_schema=output_schema, logger=logger)
            if cached is not None:
                stats["cache_hits"] += 1
                result["output"], result["from_cache"] = cached, True
                return result

            prompt = (
                "다음 입력에서 feature 사실 값을 추출하라.\n\n```json\n"
                + json.dumps(payload, ensure_ascii=False)
                + "\n```"
            )
            stats["llm_calls"] += 1
            output = analyzer.call_with_schema(
                prompt, output_schema, repair=_wrap_bare_features(cid))
            store_agent_output(
                agent_id=_LLM_AGENT_ID, cache_input=cache_input,
                context=context, output=output, logger=logger)
            result["output"] = output
            return result
        except Exception as exc:  # noqa: BLE001 — candidate 단위 부분 실패 허용 (§7)
            errors.append({
                "node":      "official_content_collection_node",
                "error":     f"step2 candidate={cid}: {type(exc).__name__}: {str(exc)[:200]}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            return result

    results: list[dict] = []
    if targets:
        with ThreadPoolExecutor(max_workers=_LLM_PARALLEL) as pool:
            results = list(pool.map(_one, targets))
    return results, errors, stats


# ─── Step 3: feature_pool 조립 + 관측성 (§5-4 · §6) ──────────────────────────

# 관측성 저장 경로 (§6-4 — data/collection/{node_name}/{run_id}/)
_COLLECTION_DIR_NAME = "official_content_collection"


def _not_found_cell() -> dict:
    return {"value": "", "value_numeric": None, "unit": "", "as_of": "",
            "extraction_status": "not_found", "evidence": "", "source_url": "",
            "source_origin": "", "confidence": 0,
            "is_promotional": False, "valid_until": ""}


def assemble_feature_pool(
    results: list[dict], targets: list[dict]
) -> tuple[dict, list[dict]]:
    """Step 3 — candidate별 LLM 출력을 feature 축으로 재피벗 (§6-1·§6-2 계약).

    누락 셀 0건 보장 (§9-4): 모든 (target.feature_ids × candidate) 셀은 값 또는
    not_found 상태값을 가진다. output=None(실패)·no_content candidate 는 전 feature
    not_found 처리 (§7 부분 실패 정책).
    """
    origin_by_cid_url = {
        t["candidate_id"]: {u["url"]: u["origin"] for u in t["urls"]}
        for t in targets
    }
    target_by_cid = {t["candidate_id"]: t for t in targets}

    feature_pool: dict[str, dict[str, dict]] = {}
    product_profiles: list[dict] = []

    for r in sorted(results, key=lambda r: r["candidate_id"]):
        cid = r["candidate_id"]
        target = target_by_cid.get(cid, {"feature_ids": [], "candidate_name": ""})
        extracted = {
            f["feature_id"]: f
            for f in (r["output"] or {}).get("extracted_features", [])
        }

        explicit_n = 0
        for fid in target["feature_ids"]:
            f = extracted.get(fid)
            if f is None:
                cell = _not_found_cell()
            else:
                cell = {
                    "value":             f.get("value", ""),
                    "value_numeric":     f.get("value_numeric"),
                    "unit":              f.get("unit", ""),
                    "as_of":             f.get("as_of", ""),
                    "extraction_status": f.get("extraction_status", "unknown"),
                    "evidence":          f.get("evidence", ""),
                    "source_url":        f.get("source_url", ""),
                    "source_origin":     origin_by_cid_url.get(cid, {})
                                         .get(f.get("source_url", ""), ""),
                    "confidence":        f.get("confidence", 0),
                    "is_promotional":    f.get("is_promotional", False),
                    "valid_until":       f.get("valid_until", ""),
                }
                if cell["extraction_status"] == "explicit":
                    explicit_n += 1
            feature_pool.setdefault(fid, {})[cid] = cell

        conflicts = (r["output"] or {}).get("conflicts", [])
        n_features = len(target["feature_ids"])
        product_profiles.append({
            "candidate_id":   cid,
            "product_name":   target.get("candidate_name", ""),
            "profile_summary": (r["output"] or {}).get("profile_summary", ""),
            "sources_used":   r["pages_used"],
            "fetch_failures": sorted(set(r["fetch_failures"]) | set(r["dynamic_render"])),
            "needs_manual_review": bool(conflicts)
                or r["output"] is None
                or (n_features > 0 and explicit_n / n_features < 0.5),
        })
    return feature_pool, product_profiles


def _write_observability(
    run_id: str, results: list[dict],
    feature_pool: dict | None = None, product_profiles: list[dict] | None = None,
) -> None:
    """§6-4 관측성 저장 (실패는 무시 — 비치명).

    저장 항목 (data/collection/official_content_collection/{run_id}/):
      - {candidate_id}.json          : candidate별 LLM raw 응답
      - feature_pool.json            : Step 3 조립 결과 (feature × candidate 피벗)
      - product_profiles.json        : §6-2 보조 출력
      - dynamic_render_backlog.json  : SPA URL 목록 (v0.11 Playwright 입력)
    """
    try:
        from server.config import BASE_DIR
        out_dir = BASE_DIR / "data" / "collection" / _COLLECTION_DIR_NAME / (run_id or "no_run_id")
        out_dir.mkdir(parents=True, exist_ok=True)
        backlog = sorted({u for r in results for u in r["dynamic_render"]})
        if backlog:
            (out_dir / "dynamic_render_backlog.json").write_text(
                json.dumps({"urls": backlog,
                            "note": "v0.11 Playwright fallback (D19) 입력 목록"},
                           ensure_ascii=False, indent=2), encoding="utf-8")
        for r in results:
            if r["output"] is not None:
                (out_dir / f"{r['candidate_id']}.json").write_text(
                    json.dumps(r["output"], ensure_ascii=False, indent=2),
                    encoding="utf-8")
        if feature_pool is not None:
            (out_dir / "feature_pool.json").write_text(
                json.dumps(feature_pool, ensure_ascii=False, indent=2),
                encoding="utf-8")
        if product_profiles is not None:
            (out_dir / "product_profiles.json").write_text(
                json.dumps(product_profiles, ensure_ascii=False, indent=2),
                encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("관측성 저장 실패 (무시): %s", exc)


# ─── 메인 노드 (Step 0~3 구현 완료) ──────────────────────────────────────────

def official_content_collection_node(
    state: DomainAnalysisState, config: dict | None = None
) -> dict:
    """official 출처 수집 노드 — Step 0(게이트·targets) → 1(수집·발췌) → 2(LLM 추출)
    → 3(feature_pool 조립) 전체 실행."""
    started_at = datetime.now(timezone.utc).isoformat()
    thread_id = (config or {}).get("configurable", {}).get("thread_id", "")
    run_id = state.get("run_id") or thread_id

    selected_purposes = state.get("selected_purposes") or []
    if REPORT_TYPE not in selected_purposes:
        # §4-2: comparison_matrix 미선택 — graceful skip
        return {"agent_steps": [_step("skipped", started_at)]}

    try:
        results, errors, stats = run_llm_extraction(dict(state))
    except Exception as exc:  # noqa: BLE001 — prompt/schema 로드 실패 등
        message = f"step2 실행 불가: {type(exc).__name__}: {str(exc)[:200]}"
        logger.error("official_content_collection_node: %s", message)
        return {
            "errors": [{"node": "official_content_collection_node", "error": message,
                        "timestamp": datetime.now(timezone.utc).isoformat()}],
            "agent_steps": [_step("failed", started_at, message)],
        }

    targets = stats["targets"]
    if not targets:
        logger.info("official_content_collection_node: 추출 대상 0건 — 빈 결과로 종료")
        return {"feature_pool": {}, "product_profiles": [],
                "agent_steps": [_step("completed", started_at)]}

    feature_pool, product_profiles = assemble_feature_pool(results, targets)
    _write_observability(run_id, results, feature_pool, product_profiles)

    failed_n = sum(1 for r in results if r["output"] is None and not r["no_content"])
    logger.info(
        "official_content_collection_node: 완료 — %d candidates "
        "(llm %d · cache %d · no_content %d · 실패 %d), feature_pool %d features",
        len(results), stats["llm_calls"], stats["cache_hits"],
        len(stats["skipped_no_pages"]), failed_n, len(feature_pool),
    )

    step = _step("completed", started_at)
    if errors:
        step["error_message"] = f"{len(errors)}건 부분 실패"
    out: dict = {
        "feature_pool":     feature_pool,
        "product_profiles": product_profiles,
        "agent_steps":      [step],
    }
    if errors:
        out["errors"] = errors
    return out


def _step(status: str, started_at: str, error_message: str = "") -> AgentStep:
    step: AgentStep = {
        "step_name":   "OfficialContentCollection",
        "status":      status,
        "started_at":  started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    if error_message:
        step["error_message"] = error_message
    return step
