"""
server/graph/nodes/official_source_resolver_node.py
-----------------------------------------------------
OfficialSourceResolverAgent LangGraph 노드.

2026-05 리팩토링 — 속도·UX 개편
--------------------------------
A-1 batch LLM 검증     : candidate별 단발 호출 → batch_size 묶음 단일 호출
A-2 fast-path          : known-domain + title/path 매칭 시 LLM 호출 우회
A-3 동적 PARALLEL      : min(N, PARALLEL_MAX) 로 워커 수 자동 조정
B-1 Brave 쿼리 병렬    : 한국어/영어 쿼리 동시 실행
B-2 HTTP 검증 병렬     : candidate 내부 URL 5개 동시 검증
B-3 LLM·HTTP 부분 병렬 : Brave 결과 도착 즉시 HTTP 검증 future 발사
C-1 per-candidate event: progress_store에 candidate별 stage 진행 갱신
C-2 optimistic UI      : LLM 결과 도착 시점에 primary_url을 progress에 즉시 노출
E-1 Brave 결과 캐시    : (query) 24h TTL
E-2 HTTP 검증 캐시     : (url) 1h TTL

처리 분기 (candidate_id 접두사 기준)
--------------------------------------
  own_* / comp_*  →  source_type = "official"
  func_*          →  source_type = "reference"

출력 state 키: official_sources (list[dict])
"""

import json
import logging
import re
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests as req_lib

from server.config import (
    AGENTS_DIR,
    BASE_DIR,
    BRAVE_SEARCH_API_KEY,
    CLI_MODEL,
    CLI_TIMEOUT,
    OFFICIAL_SOURCE_RESOLVER_BRAVE_COUNT,
    OFFICIAL_SOURCE_RESOLVER_LLM_BATCH_SIZE,
    OFFICIAL_SOURCE_RESOLVER_PARALLEL,
    OFFICIAL_SOURCE_RESOLVER_PARALLEL_MAX,
    OFFICIAL_SOURCE_STORE_TTL_DAYS,
)
from server.graph.agent_cache import (
    load_agent_output,
    make_cache_context,
    store_agent_output,
)
from server.graph.official_source_store import get_store
from server.graph.progress_store import (
    init_candidates,
    set_progress,
    update_candidate,
)
from server.graph.state import DomainAnalysisState, AgentStep
from server.graph.url_cache import (
    get_brave_results,
    get_http_validation,
    set_brave_results,
    set_http_validation,
)
from server.llm.claude_cli_analyzer import ClaudeCodeCliAnalyzer
from server.utils.slug import deterministic_normalize

logger = logging.getLogger(__name__)

_HTTP_CONNECT_TIMEOUT = 3
_HTTP_READ_TIMEOUT    = 5
_HTTP_TIMEOUT         = (_HTTP_CONNECT_TIMEOUT, _HTTP_READ_TIMEOUT)
_USER_AGENT = (
    "Mozilla/5.0 (compatible; OfficialSourceResolverBot/1.0)"
)
_MAX_WORKERS = 8   # 병렬 HTTP 검증 스레드 수

_KNOWN_DOMAINS_CACHE: dict | None = None
_BLOCKLIST_CACHE: set[str] | None = None
_BLOCKLIST_QUERY_MAX = 6   # 'NOT site:' 부착 상한 (재현율 보호; 초과분은 host 재검증)
_BASE_QUERY_COUNT    = 2    # _build_brave_queries 의 기본 쿼리 수(한/영) — site: 보조와 구분용
_SITE_BOOST_MAX      = 4    # known_domains 도메인별 site: 보조 쿼리 상한
# official_urls 산출 로직 버전. 로직이 바뀌면 올려 기존 캐시 엔트리를 재해석시킨다.
# v1: LLM official_urls 만. v2: + known_domains 클러스터 보강(fast-path 포함).
_OFFICIAL_URLS_LOGIC_V = 2


# ─────────────────────────────────────────────────── 공개 노드 함수 ──────────

def official_source_resolver_node(state: DomainAnalysisState, config: dict | None = None) -> dict:
    started_at = datetime.now(timezone.utc).isoformat()
    thread_id  = (config or {}).get("configurable", {}).get("thread_id", "")

    # ── 입력 수집 ────────────────────────────────────────────────────────────
    own_product    = state.get("own_product") or {}
    all_candidates = state.get("competitor_candidates") or []
    all_functional = state.get("functional_competitors") or []
    selected_ids   = set(state.get("selected_competitor_ids") or [])

    if not own_product:
        return _error(started_at, "own_product가 state에 없습니다.")

    sel_comp = [c for c in all_candidates if not selected_ids or c["candidate_id"] in selected_ids]
    sel_func = [f for f in all_functional if not selected_ids or f["candidate_id"] in selected_ids]

    # ── candidate item 조립 ─────────────────────────────────────────────────
    llm_items: list[dict] = [{
        "candidate_id": own_product.get("product_id", "own_unknown"),
        "type":         "official",
        "brand":        own_product.get("brand", ""),
        "product_name": own_product.get("name", ""),
        "category":     own_product.get("category", ""),
    }]
    for c in sel_comp:
        llm_items.append({
            "candidate_id": c["candidate_id"],
            "type":         "official",
            "brand":        c.get("brand", ""),
            "product_name": c.get("product_name", ""),
            "category":     c.get("category", ""),
        })
    for f in sel_func:
        llm_items.append({
            "candidate_id":  f["candidate_id"],
            "type":          "reference",
            "method_name":   f.get("method_name", ""),
            "provider_type": f.get("provider_type", ""),
            "category":      f.get("category", ""),
        })

    total_candidates = len(llm_items)
    logger.info(
        "official_source_resolver_node: 시작 (official=%d, reference=%d, total=%d)",
        1 + len(sel_comp), len(sel_func), total_candidates,
    )

    # ── C-1: progress 초기화 (candidate별 슬롯 생성) ────────────────────────
    if thread_id:
        init_candidates(thread_id, [
            {"candidate_id": it["candidate_id"], "label": _item_label(it)}
            for it in llm_items
        ])
        set_progress(
            thread_id, "url_discovery",
            detail=f"총 {total_candidates}개 URL 탐색·검증",
            total=total_candidates,
        )

    # ── 0단계: OfficialSourceStore 영구 캐시 조회 + HTTP 재검증 ─────────────
    # 이전 분석에서 검증된 공식 URL이 있으면 Brave/LLM/HTTP 파이프라인을 건너뛴다.
    # 캐시 hit 시에도 primary_url HTTP 재검증을 1회 수행해 죽은 링크는 자동 폐기.
    store_source_by_cid: dict[str, dict] = {}
    pipeline_items: list[dict] = []

    store = get_store()
    for item in llm_items:
        cid = item["candidate_id"]
        t0 = time.time()
        cached = store.get(cid, ttl_days=OFFICIAL_SOURCE_STORE_TTL_DAYS)
        # 자동 마이그레이션: official_urls 산출 로직 버전이 현재와 다른 official 엔트리는
        # 캐시 미스로 처리해 재해석을 강제한다. official_urls 필드가 아예 없는 구 데이터
        # (official_urls_v 부재)뿐 아니라, v1(클러스터 보강 전, 단일 도메인 고착) 엔트리도
        # 포함한다 — 예: own_토스트래블카드=toss.im 단일 → v2 재해석으로 tossbank.com 회수.
        needs_migration = (
            cached is not None
            and cached.get("source_type") == "official"
            and cached.get("official_urls_v") != _OFFICIAL_URLS_LOGIC_V
        )
        if cached and not needs_migration and _revalidate_cached_source(cached):
            elapsed_ms = int((time.time() - t0) * 1000)
            # 메타 필드 제거 후 정식 source dict로 반환
            cached_clean = {k: v for k, v in cached.items() if not k.startswith("_")}
            cached_clean["from_cache"] = True
            store_source_by_cid[cid] = cached_clean
            if thread_id:
                update_candidate(
                    thread_id, cid,
                    stage="cached", status="done",
                    primary_url=cached_clean.get("primary_url"),
                    validated=True,
                    elapsed_ms=elapsed_ms,
                )
            logger.info(
                "official_source_resolver_node[%s]: store 캐시 hit + 재검증 통과 (%dms)",
                cid, elapsed_ms,
            )
        else:
            if cached is not None:
                # 캐시는 있었으나 재검증 실패 → 무효화 후 정상 파이프라인 진입
                store.invalidate(cid)
            pipeline_items.append(item)

    if not pipeline_items:
        logger.info(
            "official_source_resolver_node: 전체 캐시 hit (%d개) — 파이프라인 생략",
            len(store_source_by_cid),
        )
        return _finalize(
            llm_items=llm_items,
            source_by_cid=store_source_by_cid,
            started_at=started_at,
            thread_id=thread_id,
            store_skipped=True,
        )

    logger.info(
        "official_source_resolver_node: 캐시 hit %d / 파이프라인 처리 %d",
        len(store_source_by_cid), len(pipeline_items),
    )

    # ── A-3 동적 워커 수: min(N, PARALLEL_MAX) ──────────────────────────────
    workers = max(
        OFFICIAL_SOURCE_RESOLVER_PARALLEL,
        min(len(pipeline_items), OFFICIAL_SOURCE_RESOLVER_PARALLEL_MAX),
    )
    logger.info(
        "official_source_resolver_node: workers=%d (PARALLEL=%d, MAX=%d, N=%d)",
        workers, OFFICIAL_SOURCE_RESOLVER_PARALLEL,
        OFFICIAL_SOURCE_RESOLVER_PARALLEL_MAX, len(pipeline_items),
    )

    # ── 1단계: Brave 탐색 (병렬) ─────────────────────────────────────────────
    discoveries: dict[str, list[dict]] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_map: dict[Future, dict] = {}
        for item in pipeline_items:
            if thread_id:
                update_candidate(
                    thread_id, item["candidate_id"],
                    stage="brave", status="in_progress",
                )
            future_map[pool.submit(_discover_with_brave, item)] = item
        for future in as_completed(future_map):
            item = future_map[future]
            cid  = item["candidate_id"]
            try:
                cands = future.result()
            except Exception as exc:  # noqa: BLE001
                logger.error("Brave 탐색 예외 [%s]: %s", cid, exc)
                cands = []
            discoveries[cid] = cands

    # ── 2단계: fast-path 분류 (A-2) ──────────────────────────────────────────
    fast_path_results: dict[str, dict] = {}   # cid → 결정론적 선택 결과
    llm_required_items: list[dict] = []        # LLM 검증이 필요한 항목

    for item in pipeline_items:
        cid   = item["candidate_id"]
        cands = discoveries.get(cid, [])
        if not cands:
            llm_required_items.append(item)
            continue
        fp = _try_fast_path(item, cands)
        if fp:
            fast_path_results[cid] = fp
            if thread_id:
                update_candidate(
                    thread_id, cid,
                    stage="fast_path",
                    primary_url=fp["selected_url"],
                )
            logger.info(
                "official_source_resolver_node[%s]: fast-path 적중 url=%s",
                cid, fp["selected_url"],
            )
        else:
            llm_required_items.append(item)

    logger.info(
        "official_source_resolver_node: fast-path %d / LLM 필요 %d",
        len(fast_path_results), len(llm_required_items),
    )

    # ── 3단계: HTTP 검증을 백그라운드 future로 발사 (B-2, B-3, E-2) ─────────
    http_pool = ThreadPoolExecutor(max_workers=_MAX_WORKERS)
    http_futures: dict[tuple[str, str], Future] = {}

    def _enqueue_http(cid: str, urls: list[str]) -> None:
        for url in urls:
            url = (url or "").strip()
            if not url or (cid, url) in http_futures:
                continue
            http_futures[(cid, url)] = http_pool.submit(_validate_url_cached, url)

    # fast-path 적중분 + LLM 후보 모두 사전 검증 발사 (B-3 부분 병렬)
    for cid, fp in fast_path_results.items():
        urls = [fp["selected_url"]] + [
            c["url"] for c in discoveries.get(cid, [])
            if c["url"] != fp["selected_url"]
        ]
        _enqueue_http(cid, urls)
    for item in llm_required_items:
        cid = item["candidate_id"]
        _enqueue_http(cid, [c["url"] for c in discoveries.get(cid, [])])

    # pipeline_items 처리에 사용할 candidate별 결과 dict
    pipeline_source_by_cid: dict[str, dict] = {}

    # ── 4단계: A-1 batch LLM 검증 ────────────────────────────────────────────
    llm_validations: dict[str, dict] = {}
    if llm_required_items:
        if thread_id:
            for it in llm_required_items:
                update_candidate(thread_id, it["candidate_id"], stage="llm", status="in_progress")
            set_progress(
                thread_id, "url_discovery",
                detail=f"{len(llm_required_items)}개 항목 LLM 검증 중",
                total=total_candidates,
            )
        llm_validations = _batch_validate_with_llm(llm_required_items, discoveries)

        # C-2: LLM 결과 도착 즉시 progress에 primary_url 노출 (HTTP 검증 전)
        if thread_id:
            for it in llm_required_items:
                cid = it["candidate_id"]
                v   = llm_validations.get(cid)
                update_candidate(
                    thread_id, cid,
                    stage="http",
                    primary_url=(v or {}).get("selected_url"),
                )

    # ── 5단계: candidate별 결과 조립 (HTTP future 결과 회수) ────────────────
    cid_start_times = {cid: time.time() for cid in (it["candidate_id"] for it in pipeline_items)}

    for item in pipeline_items:
        cid = item["candidate_id"]
        try:
            src = _assemble_source(
                item=item,
                candidates=discoveries.get(cid, []),
                fast_path=fast_path_results.get(cid),
                llm_val=llm_validations.get(cid),
                http_futures=http_futures,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("결과 조립 예외 [%s]: %s", cid, exc)
            src = None

        if src is not None:
            pipeline_source_by_cid[cid] = src
            # ── 6단계: 검증 성공 항목을 영구 캐시에 저장 ──────────────────────
            if src.get("validated"):
                try:
                    store.set(cid, src)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("OfficialSourceStore.set 실패 [%s]: %s", cid, exc)

            if thread_id:
                update_candidate(
                    thread_id, cid,
                    stage="done", status="done",
                    primary_url=src.get("primary_url"),
                    validated=src.get("validated"),
                    elapsed_ms=int((time.time() - cid_start_times[cid]) * 1000),
                )
        else:
            if thread_id:
                update_candidate(thread_id, cid, stage="failed", status="failed")
            logger.warning("official_source_resolver_node[%s]: 조립 실패 — 건너뜀", cid)

    http_pool.shutdown(wait=False)

    # ── 캐시 hit + 파이프라인 결과를 합쳐 최종 반환 ──────────────────────────
    return _finalize(
        llm_items=llm_items,
        source_by_cid={**store_source_by_cid, **pipeline_source_by_cid},
        started_at=started_at,
        thread_id=thread_id,
        store_skipped=False,
        extra_log=(
            f"fast-path {len(fast_path_results)}, "
            f"LLM batch {len(llm_required_items)} items"
        ),
    )


# ────────────────────────────────────────────── HTTP 검증 헬퍼 ───────────────

def _validate_url(url: str) -> tuple[int | None, str | None]:
    """
    HEAD → GET 순으로 URL을 검증한다. 캐시(E-2)는 _validate_url_cached가 적용.
    Returns (status_code, final_url) or (None, None) on failure.
    """
    headers = {"User-Agent": _USER_AGENT}
    head_blocked_status: int | None = None

    for method in ("HEAD", "GET"):
        try:
            resp = req_lib.request(
                method, url,
                headers=headers,
                timeout=_HTTP_TIMEOUT,
                allow_redirects=True,
                stream=(method == "GET"),
            )

            if method == "HEAD":
                if resp.status_code == 405:
                    continue
                if resp.status_code == 403:
                    head_blocked_status = 403
                    continue
                return resp.status_code, str(resp.url)

            if 200 <= resp.status_code < 400:
                return resp.status_code, str(resp.url)
            if head_blocked_status is not None:
                return head_blocked_status, str(resp.url)
            return resp.status_code, str(resp.url)

        except req_lib.exceptions.SSLError:
            continue
        except (req_lib.exceptions.ConnectionError,
                req_lib.exceptions.Timeout):
            return None, None
        except Exception as exc:  # noqa: BLE001
            logger.debug("URL 검증 예외 (%s): %s", url, exc)
            return None, None

    return None, None


def _validate_url_cached(url: str) -> tuple[int | None, str | None]:
    """
    E-2 캐시 적용 wrapper. URL → (status, final_url) 1h TTL 캐시.
    캐시 미스 시 _validate_url 실행 후 결과를 저장한다.
    """
    cached = get_http_validation(url)
    if cached is not None:
        return cached
    result = _validate_url(url)
    set_http_validation(url, result[0], result[1])
    return result


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
    logger.error("official_source_resolver_node 오류: %s", message)
    return {
        "errors": [{"node": "official_source_resolver_node",
                    "error": message,
                    "timestamp": datetime.now(timezone.utc).isoformat()}],
        "agent_steps": [{"step_name": "OfficialSourceResolver",
                         "status": "failed",
                         "started_at": started_at,
                         "finished_at": datetime.now(timezone.utc).isoformat(),
                         "error_message": message}],
    }


def _item_label(item: dict) -> str:
    """UI 표시용 candidate 라벨."""
    if item.get("type") == "official":
        brand   = (item.get("brand") or "").strip()
        product = (item.get("product_name") or "").strip()
        return f"{brand} {product}".strip() or item.get("candidate_id", "")
    method   = (item.get("method_name") or "").strip()
    provider = (item.get("provider_type") or "").strip()
    return f"{method} ({provider})".strip(" ()") or item.get("candidate_id", "")


# ─────────────────────────────── A-2: fast-path 분류 ────────────────────────

def _load_known_domains() -> dict[str, list[str]]:
    """known_domains.json을 로드해 dict로 반환한다. 1회만 디스크 접근."""
    global _KNOWN_DOMAINS_CACHE
    if _KNOWN_DOMAINS_CACHE is not None:
        return _KNOWN_DOMAINS_CACHE

    path = AGENTS_DIR / "official_source_resolver" / "known_domains.json"
    data = _load_json(path) or {}
    entries = data.get("entries", {}) if isinstance(data, dict) else {}
    _KNOWN_DOMAINS_CACHE = {
        deterministic_normalize(k): list(v) for k, v in entries.items()
    }
    return _KNOWN_DOMAINS_CACHE


def _host_of(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:  # noqa: BLE001
        return ""


def _host_matches_domain(host: str, domain: str) -> bool:
    """host가 domain의 정확한 매칭이거나 서브도메인인지 확인."""
    host   = host.lower()
    domain = domain.lower()
    return host == domain or host.endswith("." + domain)


# ─────────────── 부정 차단 목록(backstop) — 수집 단계 원천 차단 (①②) ──────────

def _load_blocklist() -> set[str]:
    """blocklist.json 의 hosts 를 로드 (1회 캐시). www. 제거·소문자 정규화."""
    global _BLOCKLIST_CACHE
    if _BLOCKLIST_CACHE is not None:
        return _BLOCKLIST_CACHE
    path = AGENTS_DIR / "official_source_resolver" / "blocklist.json"
    data = _load_json(path) or {}
    hosts = data.get("hosts", []) if isinstance(data, dict) else []
    cleaned: set[str] = set()
    for h in hosts:
        if isinstance(h, str) and h.strip():
            v = h.strip().lower()
            cleaned.add(v[4:] if v.startswith("www.") else v)
    _BLOCKLIST_CACHE = cleaned
    return _BLOCKLIST_CACHE


def _host_blocklisted(url: str) -> bool:
    """URL host 가 차단 목록의 어느 항목과 매칭(정확/서브도메인)하는지."""
    host = _host_of(url)
    if not host:
        return False
    return any(_host_matches_domain(host, b) for b in _load_blocklist())


def _blocklist_query_suffix() -> str:
    """Brave 쿼리에 부착할 'NOT site:' 제외 연산자 (재현율 보호 위해 상위 N개만).

    초과분은 _discover_with_brave 의 host 재검증이 받아낸다. 빈 목록이면 빈 문자열.
    """
    hosts = sorted(_load_blocklist())[:_BLOCKLIST_QUERY_MAX]
    return "".join(f" NOT site:{h}" for h in hosts)


# ─────────────── 양성 게이트(공식성 보증) — 조립 단계 (④) ─────────────────────

_BRAND_TOKEN_SPLIT = re.compile(r"[\s/·,()\[\]{}<>~%:;'\"!?.\-_]+")


def _brand_tokens(item: dict) -> set[str]:
    """brand·product_name 에서 2자 이상 토큰 추출 (소문자). 라틴 브랜드 도메인 매칭용."""
    toks: set[str] = set()
    for field in (item.get("brand", ""), item.get("product_name", "")):
        for t in _BRAND_TOKEN_SPLIT.split((field or "").lower()):
            if len(t) >= 2:
                toks.add(t)
    return toks


def _host_token_match(host: str, tokens: set[str]) -> bool:
    """host 또는 등록가능 도메인 첫 레이블에 브랜드 토큰이 포함되는지."""
    if not host:
        return False
    label = host.split(".")[0]
    return any(t in host or t in label for t in tokens)


def _known_domains_for_item(item: dict) -> list[str]:
    """item 의 brand/product_name/토큰을 known_domains 키로 조회한 공식 도메인 목록.

    site: 보조 쿼리 생성용. 브랜드 문자열 표기 차이에 강건하도록 다중 키를 시도한다
    (예: brand='토스' → ['toss.im','tossbank.com','tossinvest.com']).
    """
    kd = _load_known_domains()
    keys = {
        deterministic_normalize(item.get("brand", "") or ""),
        deterministic_normalize(item.get("product_name", "") or ""),
    }
    for t in _brand_tokens(item):
        keys.add(deterministic_normalize(t))
    domains: list[str] = []
    seen: set[str] = set()
    for k in keys:
        if not k:
            continue
        for d in kd.get(k, []):
            if d not in seen:
                seen.add(d)
                domains.append(d)
    return domains


def _known_cluster_for_host(host: str) -> set[str]:
    """host 가 속한 known_domains 클러스터(같은 브랜드의 공식 도메인 목록) 반환.

    primary_url 의 host 가 큐레이션된 브랜드 묶음(예: 토스 → toss.im·tossbank.com·
    tossinvest.com)에 속하면 그 묶음 전체를 허용 근거로 삼는다. 브랜드 문자열
    정규화에 의존하지 않으므로(primary host 기준), 브랜드명-도메인 불일치에 강건하다.
    """
    if not host:
        return set()
    for domains in _load_known_domains().values():
        if any(_host_matches_domain(host, d) for d in domains):
            return set(domains)
    return set()


def _filter_official_urls(
    extra_urls: list[str], item: dict, primary_url: str | None
) -> tuple[list[str], list[str]]:
    """양성 게이트: 추가 공식 후보 URL 중 공식성이 결정적으로 확인되는 것만 통과.

    통과 조건(OR): (1) host 가 primary 의 known_domains 클러스터에 속함,
    (2) host 가 brand 키 known_domains 목록에 속함, (3) 브랜드 토큰 일치.
    반환: (kept, rejected). rejected 는 호출자가 검토용 JSON 에 기록한다.
    """
    allowed: set[str] = _known_cluster_for_host(_host_of(primary_url or ""))
    brand_key = deterministic_normalize(item.get("brand", "") or "")
    if brand_key:
        allowed |= set(_load_known_domains().get(brand_key, []))
    tokens = _brand_tokens(item)

    kept: list[str] = []
    rejected: list[str] = []
    for u in extra_urls:
        host = _host_of(u)
        if any(_host_matches_domain(host, d) for d in allowed) or _host_token_match(host, tokens):
            kept.append(u)
        else:
            rejected.append(u)
    return kept, rejected


def _record_gate_review(item: dict, rejected: list[str], primary_url: str | None) -> None:
    """양성 게이트가 탈락시킨 official 후보를 검토용 JSON 에 누적 기록 (best-effort).

    미등록 브랜드(known_domains 미적중 + 토큰 불일치)로 떨어진 항목을 사용자가
    검토해 known_domains 에 승격할 수 있도록 한다. 실패는 무시(비치명).
    """
    if not rejected:
        return
    try:
        path = BASE_DIR / "data" / "review" / "official_url_gate_review.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        existing: dict = {}
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8")) or {}
            except Exception:  # noqa: BLE001
                existing = {}
        records = existing.get("records", []) if isinstance(existing, dict) else []
        seen = {(r.get("candidate_id"), r.get("url")) for r in records}
        now = datetime.now(timezone.utc).isoformat()
        for u in rejected:
            key = (item.get("candidate_id", ""), u)
            if key in seen:
                continue
            records.append({
                "candidate_id": item.get("candidate_id", ""),
                "brand":        item.get("brand", ""),
                "product_name": item.get("product_name", ""),
                "url":          u,
                "host":         _host_of(u),
                "primary_url":  primary_url or "",
                "reason":       "양성 게이트 미통과(known_domains 미적중 + 브랜드 토큰 불일치) — 검토 후 known_domains 승격 가능",
                "recorded_at":  now,
            })
            seen.add(key)
        path.write_text(
            json.dumps({"records": records}, ensure_ascii=False, indent=2),
            encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("_record_gate_review 실패 (무시): %s", exc)


def _try_fast_path(item: dict, candidates: list[dict]) -> dict | None:
    """
    A-2 fast-path: known-domain + title/path 매칭 시 LLM 호출을 우회한다.

    조건 (모두 충족):
      1. 브랜드 슬러그가 known_domains 테이블에 존재
      2. 후보 URL의 host가 매핑 도메인 중 하나와 일치(또는 서브도메인)
      3. (official만) product_name 토큰이 URL path 또는 title에 포함
         또는 브랜드 메인 도메인의 루트/메인 경로(경로 깊이 ≤ 1)

    Returns
    -------
    dict | None
        {"selected_url", "is_official", "confidence", "validation_reason"}
        조건을 만족하는 후보가 없으면 None.
    """
    stype = item.get("type", "")

    if stype == "official":
        brand_key = deterministic_normalize(item.get("brand", ""))
    else:
        # reference의 provider_type을 brand로 사용 (예: "한국은행")
        brand_key = deterministic_normalize(item.get("provider_type", ""))

    if not brand_key:
        return None

    known = _load_known_domains()
    domains = known.get(brand_key)
    if not domains:
        return None

    product_token = deterministic_normalize(item.get("product_name", ""))

    for c in candidates:
        url   = c.get("url", "")
        host  = _host_of(url)
        if not host:
            continue

        for dom in domains:
            if not _host_matches_domain(host, dom):
                continue

            # 도메인은 일치 — 추가 조건 검사
            path = urlparse(url).path or "/"
            title = c.get("title", "")
            title_norm = deterministic_normalize(title)
            path_norm  = deterministic_normalize(path)

            if stype == "official":
                # 상품명 토큰이 path/title에 포함되면 확정
                if product_token and (product_token in path_norm or product_token in title_norm):
                    return {
                        "selected_url":      url,
                        "is_official":       True,
                        "confidence":        0.95,
                        "validation_reason": "도메인+상품명 일치(fast-path)",
                    }
                # 메인/루트 경로(/, /ko/, /sec/ 등 깊이 ≤ 1)도 인정
                depth = len([p for p in path.split("/") if p])
                if depth <= 1:
                    return {
                        "selected_url":      url,
                        "is_official":       True,
                        "confidence":        0.85,
                        "validation_reason": "브랜드 도메인 메인(fast-path)",
                    }
            else:
                # reference: 도메인만 일치하면 인정 (운영 주체 공식 도메인)
                return {
                    "selected_url":      url,
                    "is_official":       True,
                    "confidence":        0.85,
                    "validation_reason": "운영 주체 공식 도메인(fast-path)",
                }
            break  # 같은 후보에서 다른 도메인 매칭 시도 불필요

    return None


# ─────────────────────────── Brave API 탐색 + 페이지 메타 수집 ──────────────────

_BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"


def _discover_with_brave(item: dict, tried_urls: set | None = None) -> list[dict]:
    """
    Brave Search API로 item에 맞는 후보 URL을 탐색하고
    각 URL의 페이지 메타(title, meta_description, canonical, text_snippet)를 수집한다.

    개편 사항:
      - B-1 한국어/영어 쿼리 ThreadPoolExecutor 병렬 실행
      - E-1 쿼리 결과 24h TTL 캐시 적용

    tried_urls : 이미 시도된 URL 집합 (재탐색 시 결과에서 제외)
    """
    if not BRAVE_SEARCH_API_KEY:
        logger.warning("_discover_with_brave[%s]: BRAVE_SEARCH_API_KEY 미설정 — 탐색 생략",
                       item.get("candidate_id", ""))
        return []

    tried   = tried_urls or set()
    queries = _build_brave_queries(item)
    count   = OFFICIAL_SOURCE_RESOLVER_BRAVE_COUNT

    # ── B-1: 두 쿼리(한/영)를 병렬 실행 ──────────────────────────────────────
    query_results: dict[str, list[dict]] = {}
    with ThreadPoolExecutor(max_workers=len(queries)) as pool:
        future_map = {pool.submit(_brave_query, q, count): q for q in queries}
        for future in as_completed(future_map):
            q = future_map[future]
            try:
                query_results[q] = future.result()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "_discover_with_brave[%s]: 쿼리 실패 (%r) — %s",
                    item.get("candidate_id", ""), q, exc,
                )
                query_results[q] = []

    # ── 결과 병합 (라운드로빈 — 쿼리별 상위부터 번갈아) ──────────────────────
    # site: 보조 쿼리 결과가 일반 쿼리 결과에 상한으로 밀려 누락되지 않도록, 각 쿼리의
    # depth 0 결과부터 번갈아 채택한다. 보조 쿼리 수만큼 상한을 확대해 일반 커버리지 유지.
    seen:           set[str]   = set()
    raw_candidates: list[dict] = []
    per_query = [query_results.get(q, []) for q in queries]
    cap = count + max(0, len(queries) - _BASE_QUERY_COUNT)   # 보조 쿼리 수만큼 확대
    max_depth = max((len(r) for r in per_query), default=0)
    for depth in range(max_depth):
        for results in per_query:
            if depth >= len(results):
                continue
            r = results[depth]
            url = (r.get("url") or "").strip()
            if not url or url in tried or url in seen:
                continue
            # ② host 재검증 — Brave 가 'NOT site:' 를 흘리거나 쿼리 상한 초과분 차단
            if _host_blocklisted(url):
                continue
            seen.add(url)
            raw_candidates.append({
                "url":     url,
                "snippet": r.get("description", ""),
                "rank":    depth,
            })
            if len(raw_candidates) >= cap:
                break
        if len(raw_candidates) >= cap:
            break

    if not raw_candidates:
        logger.info(
            "_discover_with_brave[%s]: 유효 후보 없음",
            item.get("candidate_id", ""),
        )
        return []

    # ── URL별 페이지 메타 병렬 수집 ──────────────────────────────────────────
    meta_results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=min(len(raw_candidates), _MAX_WORKERS)) as pool:
        future_map = {
            pool.submit(_fetch_page_meta, c["url"]): c
            for c in raw_candidates
        }
        for future in as_completed(future_map):
            c = future_map[future]
            try:
                meta = future.result()
            except Exception as exc:  # noqa: BLE001
                logger.debug("_fetch_page_meta[%s]: 예외 — %s", c["url"], exc)
                meta = {}
            meta_results[c["url"]] = meta

    # ── 결과 조립 ─────────────────────────────────────────────────────────────
    candidates: list[dict] = []
    for c in raw_candidates:
        url  = c["url"]
        meta = meta_results.get(url, {})
        candidates.append({
            "url":              url,
            "title":            meta.get("title") or "",
            "meta_description": meta.get("meta_description") or c["snippet"],
            "text_snippet":     meta.get("text_snippet") or c["snippet"],
            "canonical_url":    meta.get("canonical_url"),
            "rank":             c["rank"],
        })

    logger.info(
        "_discover_with_brave[%s]: %d개 후보 수집",
        item.get("candidate_id", ""), len(candidates),
    )
    return candidates


def _brave_query(query: str, count: int) -> list[dict]:
    """
    단일 Brave 쿼리를 실행한다. E-1 캐시 적용.
    Returns: Brave API의 web.results 원본 dict 리스트.
    """
    cached = get_brave_results(query)
    if cached is not None:
        return cached

    headers = {
        "Accept":               "application/json",
        "Accept-Encoding":      "gzip",
        "X-Subscription-Token": BRAVE_SEARCH_API_KEY,
    }
    resp = req_lib.get(
        _BRAVE_ENDPOINT,
        headers=headers,
        params={"q": query, "count": count},
        timeout=(3, 8),
    )
    resp.raise_for_status()
    results = resp.json().get("web", {}).get("results", [])
    set_brave_results(query, results)
    return results


def _build_brave_queries(item: dict) -> list[str]:
    """
    항목 유형(official/reference)에 따라 Brave 검색 쿼리를 최대 2개 생성한다.
    한국어 쿼리를 먼저, 영어 쿼리를 두 번째로 반환한다.
    """
    stype = item.get("type", "")
    if stype == "official":
        brand   = item.get("brand", "").strip()
        product = item.get("product_name", "").strip()
        name    = f"{brand} {product}".strip() if brand else product
        base    = [f"{name} 공식 사이트", f"{name} official website"]
    elif stype == "reference":
        method   = item.get("method_name", "").strip()
        provider = item.get("provider_type", "").strip()
        name     = f"{method} {provider}".strip() if provider else method
        base     = [f"{name} 공식 안내", f"{name} official guide"]
    else:
        return []
    # ① 부정 차단 목록을 'NOT site:' 연산자로 부착 (원천 차단). 빈 목록이면 무영향.
    suffix = _blocklist_query_suffix()
    queries = [f"{q}{suffix}" for q in base]
    # 회수율 보강: known_domains 도메인별 site: 보조 쿼리. 복수 공식 도메인(예: 토스 →
    # toss.im·tossbank.com) 중 일부가 일반 쿼리 상위에 안 떠도 각 도메인을 강제 탐색한다.
    # site: 가 도메인을 한정하므로 차단 목록 suffix 는 불필요.
    if stype == "official":
        boost_name = (item.get("brand", "").strip() or item.get("product_name", "").strip())
        for d in _known_domains_for_item(item)[:_SITE_BOOST_MAX]:
            queries.append(f"{boost_name} site:{d}".strip())
    return queries


def _fetch_page_meta(url: str) -> dict:
    """
    URL을 GET으로 요청해 HTML에서 title, meta description, canonical, text_snippet을 추출한다.
    """
    headers = {"User-Agent": _USER_AGENT}
    try:
        resp = req_lib.get(
            url,
            headers=headers,
            timeout=_HTTP_TIMEOUT,
            allow_redirects=True,
            stream=True,
        )
        ct = resp.headers.get("Content-Type", "")
        if "text/html" not in ct:
            return {}
        raw = b""
        for chunk in resp.iter_content(chunk_size=4096):
            raw += chunk
            if len(raw) >= 32768:
                break
        html = raw.decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        logger.debug("_fetch_page_meta[%s]: 요청 실패 — %s", url, exc)
        return {}

    title_m = re.search(
        r"<title[^>]*>(.*?)</title>",
        html, re.IGNORECASE | re.DOTALL,
    )
    meta_desc_m = re.search(
        r'<meta\s+(?:[^>]*?\s)?name=["\']description["\'][^>]*?\s+content=["\']([^"\']*)["\']',
        html, re.IGNORECASE,
    ) or re.search(
        r'<meta\s+(?:[^>]*?\s)?content=["\']([^"\']*)["\'][^>]*?\s+name=["\']description["\']',
        html, re.IGNORECASE,
    )
    canonical_m = re.search(
        r'<link\s+(?:[^>]*?\s)?rel=["\']canonical["\'][^>]*?\s+href=["\']([^"\']*)["\']',
        html, re.IGNORECASE,
    ) or re.search(
        r'<link\s+(?:[^>]*?\s)?href=["\']([^"\']*)["\'][^>]*?\s+rel=["\']canonical["\']',
        html, re.IGNORECASE,
    )

    body_html = re.sub(
        r"<(?:script|style)[^>]*>.*?</(?:script|style)>",
        " ", html, flags=re.IGNORECASE | re.DOTALL,
    )
    body_text = re.sub(r"<[^>]+>", " ", body_html)
    body_text = re.sub(r"\s+", " ", body_text).strip()[:200]

    return {
        "title": re.sub(r"\s+", " ", title_m.group(1)).strip() if title_m else "",
        "meta_description": meta_desc_m.group(1).strip() if meta_desc_m else "",
        "canonical_url":    canonical_m.group(1).strip()  if canonical_m else None,
        "text_snippet":     body_text,
    }


# ───────────────────────────── A-1: batch LLM 검증 ──────────────────────────────

def _batch_validate_with_llm(
    items: list[dict],
    discoveries: dict[str, list[dict]],
) -> dict[str, dict]:
    """
    A-1: 여러 candidate를 batch로 묶어 LLM 단일 호출로 검증한다.

    items가 BATCH_SIZE를 초과하면 청크로 분할해 각 청크별 호출.
    각 청크 결과는 candidate별로 분리되어 agent_cache에 저장된다(기존 키 체계 호환).

    Returns
    -------
    dict[cid, validation_dict]
        validation_dict: {selected_url, is_official, confidence, validation_reason}
    """
    if not items:
        return {}

    agent_dir     = AGENTS_DIR / "official_source_resolver"
    system_prompt = _load_text(agent_dir / "system_prompt_validation_kr.md")
    output_schema = _load_json(agent_dir / "output.validation.schema.json")

    if system_prompt is None or output_schema is None:
        logger.warning("_batch_validate_with_llm: 검증 에이전트 파일 없음 — 건너뜀")
        return {}

    cache_context = make_cache_context(
        agent_id="official_source_validator",
        model=CLI_MODEL,
        system_prompt=system_prompt,
        output_schema=output_schema,
        prompt_version="official_source_validator:v2_batch",
    )

    # ── 캐시 우선 조회 (candidate 단위) ──────────────────────────────────────
    cached_results: dict[str, dict] = {}
    pending_items: list[dict] = []
    for it in items:
        cid   = it["candidate_id"]
        cands = discoveries.get(cid, [])
        cache_input = {
            "candidate_id":   cid,
            "item":           it,
            "candidate_urls": [c["url"] for c in cands],
        }
        cached = load_agent_output(
            agent_id="official_source_validator",
            cache_input=cache_input,
            context=cache_context,
            output_schema=output_schema,
            logger=logger,
        )
        if cached is not None:
            vals = cached.get("validations", [])
            if vals:
                cached_results[cid] = vals[0]
                logger.info("_batch_validate_with_llm[%s]: 캐시 히트", cid)
                continue
        pending_items.append(it)

    if not pending_items:
        return cached_results

    # ── 청크로 분할 LLM 호출 ────────────────────────────────────────────────
    batch_size = max(1, OFFICIAL_SOURCE_RESOLVER_LLM_BATCH_SIZE)
    new_results: dict[str, dict] = {}

    for chunk_idx in range(0, len(pending_items), batch_size):
        chunk = pending_items[chunk_idx:chunk_idx + batch_size]
        chunk_payload = []
        for it in chunk:
            cid   = it["candidate_id"]
            cands = discoveries.get(cid, [])
            entry = {**it, "candidates": cands}
            chunk_payload.append(entry)

        prompt = (
            "아래 items 배열의 각 항목을 독립적으로 평가하고, "
            "각 항목의 candidates 중 공식 URL로 가장 적합한 것을 1개 선택하라.\n"
            "출력 validations 길이는 입력 items 길이와 같아야 한다.\n"
            "output schema를 만족하는 JSON만 반환하라.\n\n"
            f"입력:\n{json.dumps({'items': chunk_payload}, ensure_ascii=False, separators=(',', ':'))}"
        )

        analyzer = ClaudeCodeCliAnalyzer(
            model=CLI_MODEL, timeout=CLI_TIMEOUT, system_prompt=system_prompt
        )
        try:
            output = analyzer.call_with_schema(prompt=prompt, output_schema=output_schema)
        except RuntimeError as exc:
            logger.error(
                "_batch_validate_with_llm: chunk %d/%d 실패 — %s",
                chunk_idx // batch_size + 1,
                (len(pending_items) + batch_size - 1) // batch_size,
                exc,
            )
            continue

        # cid → validation 매핑
        chunk_map = {
            v.get("candidate_id"): v
            for v in output.get("validations", []) if v.get("candidate_id")
        }
        for it in chunk:
            cid   = it["candidate_id"]
            cands = discoveries.get(cid, [])
            v     = chunk_map.get(cid)
            if v is None:
                logger.warning(
                    "_batch_validate_with_llm[%s]: LLM 응답에 candidate_id 누락",
                    cid,
                )
                continue
            new_results[cid] = v
            # 개별 candidate 키로 캐시 저장 (기존 캐시 체계 호환)
            store_agent_output(
                agent_id="official_source_validator",
                cache_input={
                    "candidate_id":   cid,
                    "item":           it,
                    "candidate_urls": [c["url"] for c in cands],
                },
                context=cache_context,
                output={"validations": [v]},
                logger=logger,
            )

    return {**cached_results, **new_results}


# ───────────────────────────── 결과 조립 ────────────────────────────────────────

def _await_http(http_futures: dict[tuple[str, str], Future],
                cid: str, url: str) -> tuple[int | None, str | None]:
    """발사된 HTTP 검증 future 결과 회수. future 없으면 동기 실행."""
    fut = http_futures.get((cid, url))
    if fut is not None:
        try:
            return fut.result()
        except Exception as exc:  # noqa: BLE001
            logger.debug("_await_http 예외 [%s %s]: %s", cid, url, exc)
            return None, None
    return _validate_url_cached(url)


def _assemble_source(
    *,
    item: dict,
    candidates: list[dict],
    fast_path: dict | None,
    llm_val: dict | None,
    http_futures: dict[tuple[str, str], Future],
) -> dict | None:
    """
    candidate 1건에 대해 official_source dict를 조립한다.

    우선순위: fast_path → llm_val → Brave 1위 fallback
    HTTP 검증 결과는 사전 발사된 http_futures에서 회수.
    """
    cid   = item["candidate_id"]
    itype = item.get("type", "")

    if not candidates and not fast_path:
        return None

    # ── URL 후보 정렬 (fast-path → LLM → Brave fallback) ────────────────────
    selected_url: str | None = None
    selected_conf: float | None = None
    selected_reason: str | None = None
    llm_selected: bool = False

    if fast_path:
        selected_url    = fast_path["selected_url"]
        selected_conf   = fast_path.get("confidence", 0.9)
        selected_reason = fast_path.get("validation_reason", "fast-path")
        llm_selected    = True   # fast-path는 결정론적 선택이므로 신뢰 가능
    elif llm_val and llm_val.get("selected_url"):
        selected_url    = llm_val["selected_url"]
        selected_conf   = llm_val.get("confidence", 0.7)
        selected_reason = llm_val.get("validation_reason", "LLM 검증 통과")
        llm_selected    = True

    if itype == "official":
        if selected_url:
            sorted_urls = [
                {"url": selected_url,
                 "url_confidence": selected_conf,
                 "rationale": selected_reason},
            ] + [
                {"url":            c["url"],
                 "url_confidence": round(max(0.2, 0.35 - c["rank"] * 0.05), 2),
                 "rationale":      "Brave 검색 결과 (fallback)"}
                for c in candidates if c["url"] != selected_url
            ]
        else:
            logger.warning("_assemble_source[%s]: 공식 URL 미선택 — Brave fallback", cid)
            sorted_urls = [
                {"url":            c["url"],
                 "url_confidence": round(max(0.2, 0.4 - c["rank"] * 0.05), 2),
                 "rationale":      "Brave 검색 결과 (LLM 미선택)"}
                for c in candidates
            ]

        primary_url      = None
        http_status      = None
        validated        = False
        llm_confidence   = None
        fallback_urls: list[str] = []
        init_fail_status = None

        for j, entry in enumerate(sorted_urls):
            url = entry.get("url", "").strip()
            if not url:
                continue
            status, final_url = _await_http(http_futures, cid, url)
            if status and 200 <= status < 400:
                primary_url    = final_url or url
                http_status    = status
                validated      = True
                llm_confidence = entry.get("url_confidence")
                fallback_urls  = [
                    e["url"] for k, e in enumerate(sorted_urls)
                    if k != j and e.get("url")
                ]
                break
            else:
                if init_fail_status is None:
                    init_fail_status = status
                if url:
                    fallback_urls.append(url)

        if not validated and sorted_urls:
            best           = sorted_urls[0]
            primary_url    = best.get("url")
            llm_confidence = best.get("url_confidence")
            fallback_urls  = [e["url"] for e in sorted_urls[1:] if e.get("url")]

        # ── 복수 공식 도메인 후보 풀 구성 ───────────────────────────────────────
        # (1) LLM이 공식으로 지목한 official_urls (LLM 경로)
        # (2) 발견 후보(candidates + fallback_urls) 중 known_domains 클러스터에 속하는 URL.
        #     fast-path 처럼 LLM 을 우회하는 경로에서도 복수 공식 도메인을 회수하기 위함.
        #     클러스터는 큐레이션된 허용목록이라 결정적이고 안전하다(토스 → toss.im+tossbank.com).
        cluster = _known_cluster_for_host(_host_of(primary_url or ""))
        multi_candidates: list[str] = list((llm_val or {}).get("official_urls") or [])
        if cluster:
            pool = [c.get("url", "") for c in candidates] + list(fallback_urls)
            for u in pool:
                if u and any(_host_matches_domain(_host_of(u), d) for d in cluster):
                    multi_candidates.append(u)

        # ③ HTTP 검증 통과분만 1차 수집한 뒤, ④ 양성 게이트로 공식성 확인.
        http_ok_extra: list[str] = []
        for u in multi_candidates:
            u = (u or "").strip()
            if not u or u == primary_url or u in http_ok_extra:
                continue
            status, final_url = _await_http(http_futures, cid, u)
            if status and 200 <= status < 400:
                canon = final_url or u
                if canon != primary_url and canon not in http_ok_extra:
                    http_ok_extra.append(canon)

        # ④ 양성 게이트: known_domains 클러스터/브랜드 토큰으로 공식성 확인.
        # 미통과(미등록 브랜드 등)는 검토용 JSON 에 기록 후 제외. primary 는 항상 유지.
        kept_extra, rejected_extra = _filter_official_urls(http_ok_extra, item, primary_url)
        if rejected_extra:
            _record_gate_review(item, rejected_extra, primary_url)

        official_urls: list[str] = ([primary_url] if primary_url else []) + kept_extra

        source: dict = {
            "candidate_id":   cid,
            "source_type":    "official",
            "brand":          item.get("brand", ""),
            "product_name":   item.get("product_name", ""),
            "primary_url":    primary_url,
            "official_urls":  official_urls,
            "official_urls_v": _OFFICIAL_URLS_LOGIC_V,
            "http_status":    http_status,
            "validated":      validated,
            "llm_selected":   llm_selected,
            "llm_confidence": llm_confidence,
            "fallback_urls":  fallback_urls,
        }
        if fast_path:
            source["fast_path"] = True
        if not validated and init_fail_status:
            source["initial_fail_status"] = init_fail_status
        return source

    # ─────────────────────────────────────── reference ────────────────────────
    if selected_url:
        ref_sources = [
            {"url": selected_url, "source_name": selected_reason or "선택", "description": selected_reason or ""}
        ] + [
            {
                "url":         c["url"],
                "source_name": c.get("title") or "Brave 검색 결과",
                "description": c.get("meta_description", ""),
            }
            for c in candidates if c["url"] != selected_url
        ]
    else:
        logger.warning("_assemble_source[%s]: reference URL 미선택 — Brave fallback", cid)
        ref_sources = [
            {
                "url":         c["url"],
                "source_name": c.get("title") or "Brave 검색 결과",
                "description": c.get("meta_description", ""),
            }
            for c in candidates
        ]

    validated_refs: list[dict] = []
    any_validated  = False
    for src in ref_sources:
        url = src.get("url", "").strip()
        if not url:
            validated_refs.append(src)
            continue
        status, final_url = _await_http(http_futures, cid, url)
        ok = bool(status and 200 <= status < 400)
        if ok:
            any_validated = True
        validated_refs.append({
            **src,
            "final_url":   final_url or url,
            "http_status": status,
            "validated":   ok,
        })

    result: dict = {
        "candidate_id":      cid,
        "source_type":       "reference",
        "method_name":       item.get("method_name", ""),
        "provider_type":     item.get("provider_type", ""),
        "reference_sources": validated_refs,
        "validated":         any_validated,
    }
    if fast_path:
        result["fast_path"] = True
    return result


# ───────────────────────────── 영구 캐시 헬퍼 ───────────────────────────────────

def _revalidate_cached_source(cached: dict) -> bool:
    """
    OfficialSourceStore에서 가져온 source dict의 URL이 여전히 도달 가능한지 확인.

    official: primary_url을 HTTP HEAD/GET으로 1회 검증.
    reference: reference_sources 중 validated=True 항목 1개라도 도달 가능하면 OK.

    Returns
    -------
    bool
        True면 캐시를 그대로 사용 가능, False면 무효화 후 정상 파이프라인 진입.
    """
    stype = cached.get("source_type")

    if stype == "official":
        url = cached.get("primary_url") or ""
        if not url:
            return False
        status, _ = _validate_url_cached(url)
        return bool(status and 200 <= status < 400)

    if stype == "reference":
        for ref in cached.get("reference_sources", []):
            if not ref.get("validated"):
                continue
            url = ref.get("final_url") or ref.get("url") or ""
            if not url:
                continue
            status, _ = _validate_url_cached(url)
            if status and 200 <= status < 400:
                return True
        return False

    return False


def _finalize(
    *,
    llm_items: list[dict],
    source_by_cid: dict[str, dict],
    started_at: str,
    thread_id: str,
    store_skipped: bool,
    extra_log: str = "",
) -> dict:
    """
    최종 official_sources를 입력 순서대로 조립하고, stage 전환과 AgentStep을 반환한다.
    캐시 hit만으로 전체가 처리된 경우(store_skipped=True)도 동일하게 처리된다.
    """
    if not source_by_cid:
        return _error(started_at, "모든 candidate의 Brave 탐색·검증이 실패했습니다.")

    official_sources: list[dict] = [
        source_by_cid[it["candidate_id"]]
        for it in llm_items if it["candidate_id"] in source_by_cid
    ]

    finished_at = datetime.now(timezone.utc).isoformat()
    cached_count = sum(1 for s in official_sources if s.get("from_cache"))
    logger.info(
        "official_source_resolver_node: 완료 (총 %d개, 캐시 hit %d, %s)",
        len(official_sources), cached_count,
        extra_log or ("파이프라인 생략" if store_skipped else "정상 파이프라인"),
    )

    if thread_id:
        try:
            set_progress(
                thread_id, "url_resolution_done",
                detail=(
                    f"{len(official_sources)}개 URL 확정 "
                    f"(캐시 hit {cached_count})"
                ),
                current=len(official_sources),
                total=len(official_sources),
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("set_progress(url_resolution_done) 실패 — 무시: %s", exc)

    step: AgentStep = {
        "step_name":   "OfficialSourceResolver",
        "status":      "completed",
        "started_at":  started_at,
        "finished_at": finished_at,
    }
    return {"official_sources": official_sources, "agent_steps": [step]}


# ───────────────────────────── 레거시 호환 wrappers ─────────────────────────────
# url_retry_node.py가 _validate_url 및 _validate_with_llm을 직접 import 한다.
# 새 batch 함수와 기존 단일 함수 양쪽을 모두 노출해 호환을 유지한다.

def _validate_with_llm(item: dict, candidates: list[dict]) -> dict | None:
    """
    레거시 호환: 단일 item을 batch_validate에 위임하고 첫 결과를 반환한다.
    url_retry_node가 후보 재정렬을 위해 호출하는 진입점.
    """
    result_map = _batch_validate_with_llm([item], {item["candidate_id"]: candidates})
    return result_map.get(item["candidate_id"])
