"""
server/graph/nodes/official_source_resolver_node.py
-----------------------------------------------------
OfficialSourceResolverAgent LangGraph 노드.

Strategy 1 — candidate_id 접두사 기반 처리 분기
-------------------------------------------------
  own_* / comp_*  →  resolution_type = "official"
      LLM이 URL 후보 제안 → ThreadPoolExecutor로 병렬 HTTP 검증 → primary_url 확정

  func_*          →  resolution_type = "reference"
      LLM이 기관 레퍼런스 URL 제안 → ThreadPoolExecutor로 병렬 HTTP 검증
      (reference도 추후 크롤링 대상이므로 동일하게 validated 처리)

HTTP 검증 공통 설계
-------------------
- HEAD 먼저 시도 → 405면 GET 재시도
- 리다이렉트 추적 후 최종 URL을 저장
- CONNECT 3초 / READ 5초 타임아웃
- ThreadPoolExecutor: official URL + reference URL 모두 병렬로 검증
  → CLI 토큰 소모는 LLM 1회 호출로 고정 (병렬화는 HTTP 속도만 개선)

출력 state 키: official_sources (list[dict])

  official 항목:
    candidate_id, source_type="official", brand, product_name,
    primary_url, http_status, validated, fallback_urls, llm_confidence

  reference 항목:
    candidate_id, source_type="reference", method_name, provider_type,
    reference_sources (각 항목에 validated, http_status, final_url 추가),
    note, validated (하나라도 검증 성공이면 True)
"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import requests as req_lib

from server.config import AGENTS_DIR, CLI_MODEL, CLI_TIMEOUT, OFFICIAL_SOURCE_RESOLVER_PARALLEL
from server.graph.agent_cache import (
    load_agent_output,
    make_cache_context,
    store_agent_output,
)
from server.graph.progress_store import set_progress
from server.graph.state import DomainAnalysisState, AgentStep
from server.llm.claude_cli_analyzer import ClaudeCodeCliAnalyzer

logger = logging.getLogger(__name__)

_HTTP_CONNECT_TIMEOUT = 3
_HTTP_READ_TIMEOUT    = 5
_HTTP_TIMEOUT         = (_HTTP_CONNECT_TIMEOUT, _HTTP_READ_TIMEOUT)
_USER_AGENT = (
    "Mozilla/5.0 (compatible; OfficialSourceResolverBot/1.0)"
)
_MAX_WORKERS = 8   # 병렬 HTTP 검증 스레드 수


# ─────────────────────────────────────────────────── 공개 노드 함수 ──────────

def official_source_resolver_node(state: DomainAnalysisState, config: dict | None = None) -> dict:
    started_at = datetime.now(timezone.utc).isoformat()
    thread_id  = (config or {}).get("configurable", {}).get("thread_id", "")

    # ── 에이전트 파일 로드 ───────────────────────────────────────────────────
    agent_dir     = AGENTS_DIR / "official_source_resolver"
    system_prompt = _load_text(agent_dir / "system_prompt_kr.md")
    output_schema = _load_json(agent_dir / "output.schema.json")

    if system_prompt is None:
        return _error(started_at, f"시스템 프롬프트 없음: {agent_dir}")
    if output_schema is None:
        return _error(started_at, f"출력 스키마 없음: {agent_dir}")

    # ── 입력 수집 ────────────────────────────────────────────────────────────
    own_product    = state.get("own_product") or {}
    all_candidates = state.get("competitor_candidates") or []
    all_functional = state.get("functional_competitors") or []
    selected_ids   = set(state.get("selected_competitor_ids") or [])

    if not own_product:
        return _error(started_at, "own_product가 state에 없습니다.")

    sel_comp = [c for c in all_candidates if not selected_ids or c["candidate_id"] in selected_ids]
    sel_func = [f for f in all_functional if not selected_ids or f["candidate_id"] in selected_ids]

    # ── LLM 입력 조립 ────────────────────────────────────────────────────────
    llm_items = [{
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
            "candidate_id": f["candidate_id"],
            "type":         "reference",
            "method_name":  f.get("method_name", ""),
            "provider_type": f.get("provider_type", ""),
            "category":     f.get("category", ""),
        })

    total_candidates = 1 + len(sel_comp) + len(sel_func)
    logger.info(
        "official_source_resolver_node: URL 후보 탐색 준비 "
        "(official=%d, reference=%d)",
        1 + len(sel_comp), len(sel_func),
    )

    # ── 진행 상태: URL 탐색 단계 시작 ───────────────────────────────────────────
    if thread_id:
        set_progress(
            thread_id, "url_discovery",
            detail=f"총 {total_candidates}개 candidate LLM 탐색",
            total=total_candidates,
        )

    # ── LLM 병렬 호출: candidate 1개씩 분리 ──────────────────────────────────
    # candidate별로 LLM을 분리 호출하면 두 가지 이점이 생긴다.
    #   1) 캐시 히트율 향상: candidate 조합이 달라져도 이전에 처리한 candidate는 재사용.
    #   2) 병렬 속도 개선: OFFICIAL_SOURCE_RESOLVER_PARALLEL만큼 동시 처리.
    cache_context = make_cache_context(
        agent_id="official_source_resolver",
        model=CLI_MODEL,
        system_prompt=system_prompt,
        output_schema=output_schema,
        prompt_version="official_source_resolver:v2_per_candidate",
    )

    def _call_for_item(item: dict) -> dict | None:
        """
        단일 candidate 항목에 대해 LLM을 호출하고 resolution dict를 반환한다.
        캐시 히트 시 LLM 호출 없이 즉시 반환.
        """
        cid        = item["candidate_id"]
        per_prompt = (
            "아래 항목 1개를 처리하여 output schema를 만족하는 JSON만 반환하라.\n\n"
            "분기 규칙:\n"
            "  - type == 'official'   → resolution_type = 'official'\n"
            "  - type == 'reference'  → resolution_type = 'reference'\n\n"
            f"처리 항목:\n"
            f"{json.dumps([item], ensure_ascii=False, separators=(',', ':'))}"
        )
        per_cache_input = {"item": item, "candidate_id": cid}

        cached = load_agent_output(
            agent_id="official_source_resolver",
            cache_input=per_cache_input,
            context=cache_context,
            output_schema=output_schema,
            logger=logger,
        )
        if cached is not None:
            resolutions = cached.get("resolutions", [])
            if resolutions:
                logger.info("official_source_resolver[%s]: 캐시 히트", cid)
                return resolutions[0]
            return None

        analyzer = ClaudeCodeCliAnalyzer(
            model=CLI_MODEL, timeout=CLI_TIMEOUT, system_prompt=system_prompt
        )
        try:
            per_output = analyzer.call_with_schema(
                prompt=per_prompt, output_schema=output_schema
            )
        except RuntimeError as exc:
            logger.error(
                "official_source_resolver_node[%s]: LLM 실패 — %s", cid, exc
            )
            return None

        store_agent_output(
            agent_id="official_source_resolver",
            cache_input=per_cache_input,
            context=cache_context,
            output=per_output,
            logger=logger,
        )
        resolutions = per_output.get("resolutions", [])
        return resolutions[0] if resolutions else None

    # 병렬 실행 (candidate 수가 OFFICIAL_SOURCE_RESOLVER_PARALLEL 이하면 전부 동시)
    resolutions: list[dict] = []
    failed_cids: list[str] = []

    with ThreadPoolExecutor(max_workers=OFFICIAL_SOURCE_RESOLVER_PARALLEL) as pool:
        future_map = {
            pool.submit(_call_for_item, item): item for item in llm_items
        }
        resolution_by_cid: dict[str, dict] = {}
        for future in as_completed(future_map):
            item = future_map[future]
            cid  = item["candidate_id"]
            try:
                res = future.result()
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "official_source_resolver_node[%s]: 예외 — %s", cid, exc
                )
                res = None

            if res is not None:
                resolution_by_cid[cid] = res
            else:
                failed_cids.append(cid)
                logger.warning(
                    "official_source_resolver_node[%s]: LLM 응답 없음 — 건너뜀", cid
                )

    # llm_items 원래 순서를 유지하면서 resolutions 조립
    for item in llm_items:
        res = resolution_by_cid.get(item["candidate_id"])
        if res is not None:
            resolutions.append(res)

    if not resolutions:
        return _error(started_at, "모든 candidate의 LLM 호출이 실패했습니다.")

    logger.info(
        "official_source_resolver_node: LLM 완료 (성공 %d / 실패 %d)",
        len(resolutions), len(failed_cids),
    )

    # ── 병렬 HTTP 검증 준비: 모든 URL을 한 번에 제출 ─────────────────────────
    # (진행 상태는 URL 수집 후 업데이트)
    # { future → (res_idx, url_idx_or_ref_idx, kind) }
    url_tasks: list[tuple[int, str, str, int]] = []
    #   (resolution_index, candidate_id, kind="official"/"reference", sub_index)

    for i, res in enumerate(resolutions):
        rtype = res.get("resolution_type")
        if rtype == "official":
            for j, entry in enumerate(res.get("candidate_urls", [])):
                url = entry.get("url", "").strip()
                if url:
                    url_tasks.append((i, url, "official", j))
        elif rtype == "reference":
            for j, src in enumerate(res.get("reference_sources", [])):
                url = src.get("url", "").strip()
                if url:
                    url_tasks.append((i, url, "reference", j))

    # ── 진행 상태: URL 검증 단계 ─────────────────────────────────────────────
    if thread_id and url_tasks:
        set_progress(
            thread_id, "url_validation",
            detail=f"{len(url_tasks)}개 URL 병렬 검증",
            total=len(url_tasks),
        )

    # 병렬 실행
    validation_results: dict[tuple[int, str, int], tuple[int | None, str | None]] = {}

    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        future_map = {
            pool.submit(_validate_url, url): (res_idx, url, kind, sub_idx)
            for res_idx, url, kind, sub_idx in url_tasks
        }
        for future in as_completed(future_map):
            res_idx, url, kind, sub_idx = future_map[future]
            try:
                status, final_url = future.result()
            except Exception as exc:  # noqa: BLE001
                logger.debug("URL 검증 예외 (%s): %s", url, exc)
                status, final_url = None, None
            validation_results[(res_idx, url, sub_idx)] = (status, final_url)

    logger.info(
        "official_source_resolver_node: HTTP 검증 완료 (%d개 URL)", len(url_tasks)
    )

    # ── resolution별 후처리 ──────────────────────────────────────────────────
    official_sources: list[dict] = []
    for i, res in enumerate(resolutions):
        rtype = res.get("resolution_type")
        if rtype == "official":
            official_sources.append(
                _build_official(res, i, validation_results)
            )
        elif rtype == "reference":
            official_sources.append(
                _build_reference(res, i, validation_results)
            )
        else:
            logger.warning("알 수 없는 resolution_type=%r", rtype)

    finished_at = datetime.now(timezone.utc).isoformat()
    logger.info(
        "official_source_resolver_node: 완료 (총 %d개)", len(official_sources)
    )

    step: AgentStep = {
        "step_name":   "OfficialSourceResolver",
        "status":      "completed",
        "started_at":  started_at,
        "finished_at": finished_at,
    }
    return {"official_sources": official_sources, "agent_steps": [step]}


# ──────────────────────────────────────────────── official 후처리 ─────────────

def _build_official(
    res: dict,
    res_idx: int,
    val_results: dict,
) -> dict:
    """
    candidate_urls를 url_confidence 내림차순으로 정렬한 뒤
    병렬 검증 결과를 적용해 primary_url을 확정한다.
    """
    cid      = res.get("candidate_id", "")
    brand    = res.get("brand", "")
    pname    = res.get("product_name", "")
    raw_urls = sorted(
        res.get("candidate_urls", []),
        key=lambda u: u.get("url_confidence", 0),
        reverse=True,
    )

    primary_url         = None
    http_status         = None
    validated           = False
    llm_confidence      = None
    fallback_urls       = []
    initial_fail_status = None   # 첫 번째 URL 검증 실패 시 HTTP 상태 코드

    for j, entry in enumerate(raw_urls):
        url = entry.get("url", "").strip()
        if not url:
            continue
        status, final_url = val_results.get((res_idx, url, j), (None, None))
        if status and 200 <= status < 400:
            primary_url    = final_url or url
            http_status    = status
            validated      = True
            llm_confidence = entry.get("url_confidence")
            # 나머지는 fallback
            fallback_urls  = [
                e.get("url") for k, e in enumerate(raw_urls)
                if k != j and e.get("url")
            ]
            break
        else:
            if initial_fail_status is None:   # 첫 번째 실패 상태만 기록
                initial_fail_status = status
            fallback_urls.append(url)

    # 모두 실패: 최고 신뢰도 URL을 unvalidated로 저장
    if not validated and raw_urls:
        best           = raw_urls[0]
        primary_url    = best.get("url")
        llm_confidence = best.get("url_confidence")
        fallback_urls  = [e.get("url") for e in raw_urls[1:] if e.get("url")]

    logger.debug("official[%s]: validated=%s url=%s", cid, validated, primary_url)
    return {
        "candidate_id":       cid,
        "source_type":        "official",
        "brand":              brand,
        "product_name":       pname,
        "primary_url":        primary_url,
        "http_status":        http_status,
        "validated":          validated,
        "fallback_urls":      fallback_urls,
        "llm_confidence":     llm_confidence,
        "initial_fail_status": initial_fail_status,
    }


# ──────────────────────────────────────────────── reference 후처리 ────────────

def _build_reference(
    res: dict,
    res_idx: int,
    val_results: dict,
) -> dict:
    """
    reference_sources의 각 URL에 병렬 검증 결과를 적용한다.
    추후 크롤링 대상이 되므로 official과 동일하게 validated/http_status를 기록한다.

    source_type="reference" 로 후속 노드가 처리 방식을 구분한다:
      - official: primary_url 단일 크롤링
      - reference: reference_sources[] 중 validated=True인 URL만 크롤링
    """
    cid           = res.get("candidate_id", "")
    method_name   = res.get("method_name", "")
    provider_type = res.get("provider_type", "")
    note          = res.get("note", "")
    raw_refs      = res.get("reference_sources", [])

    validated_refs: list[dict] = []
    any_validated = False

    for j, src in enumerate(raw_refs):
        url = src.get("url", "").strip()
        if not url:
            validated_refs.append({**src, "validated": False, "http_status": None, "final_url": None})
            continue

        status, final_url = val_results.get((res_idx, url, j), (None, None))
        ok = bool(status and 200 <= status < 400)
        if ok:
            any_validated = True

        validated_refs.append({
            **src,
            "final_url":   final_url or url,
            "http_status": status,
            "validated":   ok,
        })
        logger.debug("reference[%s][%d]: validated=%s url=%s", cid, j, ok, url)

    return {
        "candidate_id":      cid,
        "source_type":       "reference",
        "method_name":       method_name,
        "provider_type":     provider_type,
        "reference_sources": validated_refs,
        "note":              note,
        # 하나 이상의 reference URL이 유효하면 True
        "validated":         any_validated,
        # official과의 일관성을 위해 primary_url은 None
        "primary_url":       None,
    }


# ────────────────────────────────────────────── HTTP 검증 헬퍼 ───────────────

def _validate_url(url: str) -> tuple[int | None, str | None]:
    """
    HEAD → GET 순으로 URL을 검증한다.
    Returns (status_code, final_url) or (None, None) on failure.

    HEAD → GET 재시도 조건:
      - 405 Method Not Allowed : HEAD를 지원하지 않는 서버
      - 403 Forbidden          : HEAD만 차단하는 서버 (KT 등 국내 대기업 다수)
        → 실측 결과 KT.com: HEAD=403, GET=200 확인 (2026-05)

    ⚠️  HEAD 403 → GET 404 패턴 (Samsung 등 세션 의존 사이트):
        세션 쿠키 없이 GET하면 지역 라우팅 실패로 404가 반환된다.
        이는 URL이 없는 게 아닌 거짓 음성(false negative)이므로
        GET 404보다 HEAD 403을 최종 상태로 우선 반환한다.
        → UI에서 "Not Found"가 아닌 "접근 제한"으로 표시해 혼동을 방지.

    케이스별 처리 결과 (실측 기준, 2026-05):
      KT.com   : HEAD=403 → GET=200 → return (200, url)  [validated=True]
      Samsung  : HEAD=403 → GET=404 → return (403, url)  [validated=False, "접근 제한"]
      LGU+/SKT : HEAD=200          → return (200, url)  [validated=True]
      일반 서버 : HEAD=405 → GET=2xx → return (2xx, url) [validated=True]
    """
    headers = {"User-Agent": _USER_AGENT}
    head_blocked_status: int | None = None  # HEAD 403 발생 시 저장

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
                    # HEAD 미지원 → GET 재시도, 상태 보존 불필요
                    continue
                if resp.status_code == 403:
                    # HEAD만 차단 → GET 재시도, 403 보존
                    head_blocked_status = 403
                    continue
                # HEAD 성공 또는 기타 오류 → 그대로 반환
                return resp.status_code, str(resp.url)

            # GET 응답 처리
            if 200 <= resp.status_code < 400:
                # GET 성공: HEAD 403 여부와 무관하게 성공으로 처리 (KT 패턴)
                return resp.status_code, str(resp.url)
            else:
                # GET 실패: HEAD가 403이었으면 GET의 오류 코드보다 403을 우선 반환
                # (Samsung 세션 쿠키 패턴 — GET 404는 거짓 음성)
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

    # HEAD/GET 모두 예외로 종료된 경우
    return None, None


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
