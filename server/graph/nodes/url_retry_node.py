"""
server/graph/nodes/url_retry_node.py
--------------------------------------
URL 검증 실패 항목 재시도 Human-in-the-loop 노드.

Two-Phase Interrupt Design
--------------------------
Phase 1 (is_final=False):
  실패 항목을 사용자에게 제시 → interrupt()
  resume 처리:
    A. manual_url 제공 → 해당 URL 직접 검증
    B. manual_url 빈 문자열 → LLM 재호출로 새 URL 탐색 후 검증
       (tried_urls를 prompt에 포함해 동일 URL 재제안 방지)
  → 재시도 후 여전히 실패 항목 있으면 Phase 2로 진입

Phase 2 (is_final=True):
  실패 항목 + action_case를 포함해 interrupt()
  action_case:
    "case1"   — official comp_*: 경쟁사 전체 제거
    "case2_1" — reference 부분 실패: 특정 URL source 제거
    "case2_2" — reference 전체 실패: func_ 항목 전체 제거
    None      — own_* 항목: 수동 URL 입력만 허용
  resume 구조:
    {
      "manual_urls":    { cid: url },       # cid별 수동 URL (비어 있으면 무시)
      "remove_ids":     [ cid, ... ],       # 분석에서 제거할 candidate_id 목록
      "remove_ref_urls": { cid: [url, ...] } # 제거할 reference URL 목록
    }

출력 state 키
-------------
  official_sources: list[dict]  (갱신된 버전으로 replace)
"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from langgraph.types import interrupt

from server.config import AGENTS_DIR, BRAVE_SEARCH_API_KEY, CLI_MODEL, CLI_TIMEOUT, OFFICIAL_SOURCE_RESOLVER_PARALLEL
from server.graph.agent_cache import (
    load_agent_output,
    make_cache_context,
    store_agent_output,
)
from server.graph.nodes.official_source_resolver_node import (
    _validate_url_cached as _validate_url,
    _validate_with_llm,
)
from server.graph.progress_store import set_progress
from server.graph.state import DomainAnalysisState, AgentStep
from server.llm.claude_cli_analyzer import ClaudeCodeCliAnalyzer

logger = logging.getLogger(__name__)
_MAX_WORKERS = 8


# ──────────────────────────────────────────────────── 공개 노드 함수 ─────────

def url_retry_node(state: DomainAnalysisState, config: dict | None = None) -> dict:
    """
    URL 검증 실패 항목을 Phase 1 → (Phase 2) 순으로 처리하는 노드.

    - 실패 항목 없음 → pass-through (interrupt 없음)
    - Phase 1 재시도 후 성공 → Phase 2 생략
    - Phase 2까지 실패 → 사용자 선택(제거/수동 입력)으로 최종 처리
    """
    started_at = datetime.now(timezone.utc).isoformat()
    thread_id  = (config or {}).get("configurable", {}).get("thread_id", "")

    official_sources: list[dict] = list(state.get("official_sources") or [])
    if not official_sources:
        return _pass(started_at, thread_id)

    # ── Phase 1: 실패 항목 수집 ───────────────────────────────────────────────
    failed_p1 = _collect_failed(official_sources)
    if not failed_p1:
        logger.info("url_retry_node: 실패 항목 없음 — pass-through")
        return _pass(started_at, thread_id)

    # ── 자동 Brave+LLM 재탐색 (interrupt 전 bypass 시도) ───────────────────────────
    # manual_urls 빈 dict → 실패 항목 전체를 Brave 검색 + LLM 검증으로 재탐색
    logger.info("url_retry_node: 자동 Brave+LLM 재탐색 시작 (실패 %d개)", len(failed_p1))
    if thread_id:
        set_progress(
            thread_id, "url_retry_llm",
            detail=f"{len(failed_p1)}개 항목 Brave+LLM 재탐색",
            total=len(failed_p1),
        )
    auto_sources = _retry_phase1(
        official_sources, {}, fail_status_field="_auto_retry_fail_status",
        use_search_api=True, use_llm_validation=True,
        thread_id=thread_id,
    )

    still_failed = _collect_failed(auto_sources)
    if not still_failed:
        logger.info("url_retry_node: 자동 LLM 재탐색 성공 — interrupt 생략")
        return _make_result(auto_sources, started_at, thread_id)

    logger.info(
        "url_retry_node: 자동 재탐색 후에도 실패 %d개 — Phase 1 interrupt 진행",
        len(still_failed),
    )

    # ── Phase 1 interrupt ─────────────────────────────────────────────────────
    resume_p1: dict = interrupt({
        "type":           "url_retry",
        "is_final":       False,
        "failed_sources": still_failed,
        "auto_llm_tried": True,   # UI에 자동 재탐색 완료 사실 전달
    })

    manual_urls_p1: dict[str, str] = resume_p1.get("manual_urls", {})

    # ── Phase 1 재시도: auto_sources 기반으로 수동 URL 검증 OR 검색 API 재탐색 ─
    if thread_id:
        set_progress(
            thread_id, "url_phase1_llm",
            detail=f"{len(still_failed)}개 항목 재탐색",
            total=len(still_failed),
        )
    updated_sources = _retry_phase1(
        auto_sources, manual_urls_p1,
        fail_status_field="_phase1_fail_status",
        use_search_api=True,
        use_llm_validation=True,
        thread_id=thread_id,
    )

    # ── Phase 2 필요 여부 확인 ────────────────────────────────────────────────
    failed_p2 = _collect_failed_with_case(updated_sources)
    if not failed_p2:
        logger.info("url_retry_node: Phase 1 재시도 성공 — Phase 2 생략")
        return _make_result(updated_sources, started_at, thread_id)  # own_* 체크 포함

    logger.info("url_retry_node: Phase 2 interrupt() (실패 %d개)", len(failed_p2))

    # ── Phase 2 interrupt ─────────────────────────────────────────────────────
    resume_p2: dict = interrupt({
        "type":           "url_retry",
        "is_final":       True,
        "failed_sources": failed_p2,
    })

    manual_urls_p2:  dict[str, str]        = resume_p2.get("manual_urls", {})
    remove_ids:      list[str]             = resume_p2.get("remove_ids", [])
    remove_ref_urls: dict[str, list[str]]  = resume_p2.get("remove_ref_urls", {})

    # ── Phase 2 적용 ──────────────────────────────────────────────────────────
    final_sources = _apply_phase2(
        updated_sources, manual_urls_p2, remove_ids, remove_ref_urls
    )

    return _make_result(final_sources, started_at, thread_id)  # own_* 체크 포함


# ──────────────────────────────────────────────── Phase 1 재시도 ─────────────

def _retry_phase1(
    sources: list[dict],
    manual_urls: dict[str, str],
    fail_status_field: str = "_auto_retry_fail_status",
    use_search_api: bool = False,
    use_llm_validation: bool = False,
    thread_id: str = "",
) -> list[dict]:
    """
    Phase 1 resume 결과를 처리한다.

    - manual_urls[cid] 있음 → 해당 URL 직접 검증
    - manual_urls[cid] 없거나 빈 문자열 → 재탐색 후 검증

    fail_status_field
        검증 실패 시 HTTP 상태 코드를 저장할 source dict 키.
        "_auto_retry_fail_status" : 자동 Brave+LLM 재탐색 단계 (interrupt 전)
        "_phase1_fail_status"     : Phase 1 사용자 재시도 단계 (interrupt 후)

    use_search_api
        True  → Brave Search API로 재탐색
        False → LLM 재탐색 (레거시, _llm_research 경로)

    use_llm_validation
        True  → Brave 탐색 결과에 _validate_with_llm을 추가 실행해 URL 선택 품질 향상.
                 use_search_api=True일 때만 효과가 있다.
    """
    # ── LLM 재탐색 대상 수집 ─────────────────────────────────────────────────
    llm_items: list[dict] = []
    for src in sources:
        if src.get("validated"):
            continue
        cid    = src["candidate_id"]
        manual = manual_urls.get(cid, "").strip()
        if manual:
            continue  # 수동 입력 제공 → LLM 재탐색 불필요

        stype = src.get("source_type")
        if stype == "official":
            tried_urls = [src.get("primary_url")] + (src.get("fallback_urls") or [])
            tried_urls = [u for u in tried_urls if u]
            llm_items.append({
                "candidate_id": cid,
                "type":         "official",
                "brand":        src.get("brand", ""),
                "product_name": src.get("product_name", ""),
                "category":     "",
                "tried_urls":   tried_urls,
            })
        elif stype == "reference":
            failed_urls = [
                r.get("final_url") or r.get("url")
                for r in src.get("reference_sources", [])
                if not r.get("validated") and r.get("url")
            ]
            llm_items.append({
                "candidate_id":  cid,
                "type":          "reference",
                "method_name":   src.get("method_name", ""),
                "provider_type": src.get("provider_type", ""),
                "category":      "",
                "tried_urls":    failed_urls,
            })

    # ── 재탐색 실행 (검색 API 또는 LLM) ─────────────────────────────────────
    llm_resolutions: dict[str, dict] = {}
    had_api_error   = False
    llm_item_cids: set[str] = set()
    if llm_items:
        if use_search_api:
            logger.info("_retry_phase1: Brave Search API 재탐색 (%d개)", len(llm_items))
            llm_resolutions, had_api_error = _search_research(llm_items)

            # ── LLM 검증으로 URL 선택 품질 향상 ──────────────────────────────
            if use_llm_validation and llm_resolutions:
                item_by_cid = {it["candidate_id"]: it for it in llm_items}
                for cid, res in list(llm_resolutions.items()):
                    item = item_by_cid.get(cid)
                    if not item:
                        continue
                    stype = item.get("type", "")

                    # resolution에서 URL 목록 추출
                    if stype == "official":
                        urls = [e.get("url") for e in res.get("candidate_urls", []) if e.get("url")]
                    else:
                        urls = [e.get("url") for e in res.get("reference_sources", []) if e.get("url")]
                    if not urls:
                        continue

                    # _validate_with_llm이 기대하는 candidate 형식으로 변환
                    candidates = [
                        {
                            "url": url, "title": "", "meta_description": "",
                            "text_snippet": "", "canonical_url": None, "rank": i,
                        }
                        for i, url in enumerate(urls)
                    ]
                    try:
                        val = _validate_with_llm(item, candidates)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("_retry_phase1 LLM 검증 실패 [%s]: %s", cid, exc)
                        continue

                    if not val or not val.get("selected_url"):
                        continue

                    selected = val["selected_url"]
                    # LLM이 선택한 URL을 첫 번째로 재정렬하고 confidence 갱신
                    if stype == "official":
                        orig = res.get("candidate_urls", [])
                        reordered = (
                            [e for e in orig if e.get("url") == selected] +
                            [e for e in orig if e.get("url") != selected]
                        )
                        if reordered:
                            reordered[0] = {
                                **reordered[0],
                                "url_confidence": val.get("confidence", reordered[0].get("url_confidence", 0.7)),
                            }
                        llm_resolutions[cid] = {**res, "candidate_urls": reordered}
                    else:  # reference
                        orig = res.get("reference_sources", [])
                        reordered = (
                            [e for e in orig if e.get("url") == selected] +
                            [e for e in orig if e.get("url") != selected]
                        )
                        llm_resolutions[cid] = {**res, "reference_sources": reordered}

                    logger.debug(
                        "_retry_phase1 LLM 검증[%s]: selected=%s (confidence=%.2f)",
                        cid, selected, val.get("confidence", 0),
                    )
        else:
            logger.info("_retry_phase1: LLM 재탐색 (%d개)", len(llm_items))
            llm_resolutions, had_api_error = _llm_research(llm_items)
        llm_item_cids = {item["candidate_id"] for item in llm_items}

    # ── 모든 URL 수집 (수동 + LLM) → 병렬 검증 ──────────────────────────────
    url_tasks: list[tuple[str, str]] = []  # (cid, url)

    for src in sources:
        if src.get("validated"):
            continue
        cid    = src["candidate_id"]
        manual = manual_urls.get(cid, "").strip()
        stype  = src.get("source_type")

        if manual:
            url_tasks.append((cid, manual))
        elif cid in llm_resolutions:
            res = llm_resolutions[cid]
            if stype == "official":
                for entry in res.get("candidate_urls", []):
                    url = entry.get("url", "").strip()
                    if url:
                        url_tasks.append((cid, url))
            elif stype == "reference":
                for entry in res.get("reference_sources", []):
                    url = entry.get("url", "").strip()
                    if url:
                        url_tasks.append((cid, url))

    # ── 진행 상태: URL 검증 단계 ─────────────────────────────────────────────
    if thread_id and url_tasks:
        stage = "url_phase1_validation" if use_search_api else "url_retry_validation"
        set_progress(
            thread_id, stage,
            detail=f"{len(url_tasks)}개 URL 병렬 검증",
            total=len(url_tasks),
        )

    # 병렬 HTTP 검증
    val_results: dict[tuple[str, str], tuple] = {}
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        future_map = {
            pool.submit(_validate_url, url): (cid, url)
            for cid, url in url_tasks
        }
        for future in as_completed(future_map):
            cid, url = future_map[future]
            try:
                status, final_url = future.result()
            except Exception as exc:  # noqa: BLE001
                logger.debug("Phase1 URL 검증 예외 (%s): %s", url, exc)
                status, final_url = None, None
            val_results[(cid, url)] = (status, final_url)

    # ── sources 갱신 ──────────────────────────────────────────────────────────
    updated: list[dict] = []
    for src in sources:
        if src.get("validated"):
            updated.append(src)
            continue

        cid    = src["candidate_id"]
        manual = manual_urls.get(cid, "").strip()
        stype  = src.get("source_type")

        if stype == "official":
            src = _apply_official_p1(
                src, cid, manual, llm_resolutions, val_results,
                had_api_error=had_api_error,
                was_llm_item=(cid in llm_item_cids),
                fail_status_field=fail_status_field,
            )
        elif stype == "reference":
            src = _apply_reference_p1(
                src, cid, manual, llm_resolutions, val_results,
                had_api_error=had_api_error,
                was_llm_item=(cid in llm_item_cids),
                fail_status_field=fail_status_field,
            )

        updated.append(src)

    return updated


def _apply_official_p1(
    src: dict,
    cid: str,
    manual: str,
    llm_resolutions: dict,
    val_results: dict,
    had_api_error: bool = False,
    was_llm_item: bool = False,
    fail_status_field: str = "_auto_retry_fail_status",
) -> dict:
    """
    official 항목에 Phase 1 재시도 결과를 적용한다.
    실패 시 _retry_fail_reason 필드와 fail_status_field 필드를 기록한다:
      "manual_failed" — 사용자 입력 URL이 HTTP 검증 실패
      "api_error"     — LLM API 호출 오류
      "no_url_found"  — LLM 재탐색 성공했으나 유효 URL 없음
    fail_status_field: 검증 실패 HTTP 상태 코드를 저장할 키
    """
    updated = dict(src)

    if manual:
        status, final_url = val_results.get((cid, manual), (None, None))
        ok = bool(status and 200 <= status < 400)
        updated["primary_url"]     = final_url or manual
        updated["http_status"]     = status
        updated["validated"]       = ok
        updated["manual_override"] = True
        if not ok:
            updated["_retry_fail_reason"] = "manual_failed"
            updated[fail_status_field]    = status
        logger.debug("official_p1[%s]: manual validated=%s", cid, ok)
        return updated

    # LLM 재탐색 결과 적용
    res = llm_resolutions.get(cid)
    if not res:
        # LLM API 오류이거나 해당 cid가 응답에 포함되지 않음
        if was_llm_item:
            updated["_retry_fail_reason"] = "api_error" if had_api_error else "no_url_found"
        return updated

    candidate_urls = sorted(
        res.get("candidate_urls", []),
        key=lambda u: u.get("url_confidence", 0),
        reverse=True,
    )
    first_fail_status: int | None = None
    for entry in candidate_urls:
        url = entry.get("url", "").strip()
        if not url:
            continue
        status, final_url = val_results.get((cid, url), (None, None))
        if status and 200 <= status < 400:
            updated["primary_url"]    = final_url or url
            updated["http_status"]    = status
            updated["validated"]      = True
            updated["llm_confidence"] = entry.get("url_confidence")
            updated["llm_researched"] = True
            logger.debug("official_p1[%s]: llm re-search validated=True url=%s", cid, url)
            return updated
        if first_fail_status is None:
            first_fail_status = status  # 가장 신뢰도 높은 URL의 실패 상태 코드 기록

    # LLM 재탐색도 모두 실패 — 새 fallback 목록으로 교체 + 실패 원인·상태 기록
    new_fallbacks = [e.get("url") for e in candidate_urls if e.get("url")]
    if new_fallbacks:
        updated["primary_url"]   = new_fallbacks[0]
        updated["fallback_urls"] = new_fallbacks[1:]
    updated["llm_researched"]       = True
    updated["_retry_fail_reason"]   = "no_url_found"
    updated[fail_status_field]      = first_fail_status
    logger.debug("official_p1[%s]: llm re-search still failed (status=%s)", cid, first_fail_status)
    return updated


def _apply_reference_p1(
    src: dict,
    cid: str,
    manual: str,
    llm_resolutions: dict,
    val_results: dict,
    had_api_error: bool = False,
    was_llm_item: bool = False,
    fail_status_field: str = "_auto_retry_fail_status",  # noqa: ARG001 (reference는 UI에 상태코드 미표시)
) -> dict:
    """
    reference 항목에 Phase 1 재시도 결과를 적용한다.
    실패 시 _retry_fail_reason 필드를 기록한다:
      "manual_failed" — 사용자 입력 URL이 HTTP 검증 실패
      "api_error"     — LLM API 호출 오류
      "no_url_found"  — LLM 재탐색 성공했으나 유효 URL 없음
    """
    updated   = dict(src)
    orig_refs = list(src.get("reference_sources", []))

    if manual:
        status, final_url = val_results.get((cid, manual), (None, None))
        ok = bool(status and 200 <= status < 400)
        new_ref = {
            "url":         final_url or manual,
            "source_name": "사용자 직접 입력",
            "description": "사용자가 Phase 1 재시도 단계에서 직접 입력한 URL",
            "final_url":   final_url or manual,
            "http_status": status,
            "validated":   ok,
        }
        all_refs = orig_refs + [new_ref]
        updated["reference_sources"] = all_refs
        updated["validated"]         = any(r.get("validated") for r in all_refs)
        updated["manual_override"]   = True
        if not updated["validated"]:
            updated["_retry_fail_reason"] = "manual_failed"
        logger.debug("reference_p1[%s]: manual validated=%s", cid, ok)
        return updated

    # LLM 재탐색 결과 적용 (새 reference URL 추가)
    res = llm_resolutions.get(cid)
    if not res:
        if was_llm_item:
            updated["_retry_fail_reason"] = "api_error" if had_api_error else "no_url_found"
        return updated

    existing_urls = (
        {r.get("url") for r in orig_refs} |
        {r.get("final_url") for r in orig_refs if r.get("final_url")}
    )
    new_refs = list(orig_refs)
    for entry in res.get("reference_sources", []):
        url = entry.get("url", "").strip()
        if not url or url in existing_urls:
            continue
        status, final_url = val_results.get((cid, url), (None, None))
        ok = bool(status and 200 <= status < 400)
        new_refs.append({
            **entry,
            "final_url":   final_url or url,
            "http_status": status,
            "validated":   ok,
        })

    updated["reference_sources"] = new_refs
    updated["validated"]         = any(r.get("validated") for r in new_refs)
    updated["llm_researched"]    = True
    if not updated["validated"]:
        updated["_retry_fail_reason"] = "no_url_found"
    logger.debug("reference_p1[%s]: llm re-search any_validated=%s", cid, updated["validated"])
    return updated


# ────────────────────────────────────────────────── LLM 재탐색 ────────────────

def _llm_research(items: list[dict]) -> tuple[dict[str, dict], bool]:
    """
    실패 항목에 대해 LLM 재호출 → 새 URL 제안.
    tried_urls를 prompt에 포함해 동일 URL 재제안을 방지한다.

    candidate 1개씩 분리 병렬 호출 (OFFICIAL_SOURCE_RESOLVER_PARALLEL 개수 동시).
    캐시 키도 candidate별로 분리되어 동일 항목의 재실행 시 캐시 히트.

    Returns
    -------
    tuple[dict[str, dict], bool]
        (resolutions_by_cid, had_api_error)
        had_api_error=True  : LLM API 호출 자체가 실패한 경우
        had_api_error=False : LLM 호출 성공 (URL 검증 실패는 별도 처리)
    """
    agent_dir     = AGENTS_DIR / "official_source_resolver"
    system_prompt = _load_text(agent_dir / "system_prompt_kr.md")
    output_schema = _load_json(agent_dir / "output.schema.json")

    if system_prompt is None or output_schema is None:
        logger.warning("_llm_research: agent 파일 없음 — LLM 재탐색 생략")
        return {}, False

    cache_context = make_cache_context(
        agent_id="url_retry_llm_research",
        model=CLI_MODEL,
        system_prompt=system_prompt,
        output_schema=output_schema,
        prompt_version="url_retry_llm_research:v2_per_candidate",
    )

    def _call_for_item(item: dict) -> tuple[str, dict | None, bool]:
        """
        단일 항목에 대해 LLM 재탐색을 수행한다.
        Returns (cid, resolution_or_None, had_api_error).
        """
        cid = item["candidate_id"]
        per_prompt = (
            "아래 항목의 공식 URL 탐색이 실패했습니다.\n"
            "tried_urls에 나열된 URL과 반드시 다른 새로운 URL을 제안하세요.\n"
            "tried_urls에 있는 URL은 절대 재사용하지 마세요.\n\n"
            "분기 규칙:\n"
            "  - type == 'official'   → resolution_type = 'official'\n"
            "  - type == 'reference'  → resolution_type = 'reference'\n\n"
            f"재탐색 항목:\n"
            f"{json.dumps([item], ensure_ascii=False, separators=(',', ':'))}"
        )
        per_cache_input = {"item": item, "candidate_id": cid}

        cached = load_agent_output(
            agent_id="url_retry_llm_research",
            cache_input=per_cache_input,
            context=cache_context,
            output_schema=output_schema,
            logger=logger,
        )
        if cached is not None:
            resolutions = cached.get("resolutions", [])
            if resolutions:
                logger.info("_llm_research[%s]: 캐시 히트", cid)
                return cid, resolutions[0], False
            return cid, None, False

        analyzer = ClaudeCodeCliAnalyzer(
            model=CLI_MODEL, timeout=CLI_TIMEOUT, system_prompt=system_prompt
        )
        try:
            per_output = analyzer.call_with_schema(
                prompt=per_prompt, output_schema=output_schema
            )
        except RuntimeError as exc:
            logger.error("_llm_research[%s]: LLM 실패 — %s", cid, exc)
            return cid, None, True  # had_api_error=True

        store_agent_output(
            agent_id="url_retry_llm_research",
            cache_input=per_cache_input,
            context=cache_context,
            output=per_output,
            logger=logger,
        )
        resolutions = per_output.get("resolutions", [])
        return cid, (resolutions[0] if resolutions else None), False

    resolutions: dict[str, dict] = {}
    had_api_error = False

    with ThreadPoolExecutor(max_workers=min(len(items), OFFICIAL_SOURCE_RESOLVER_PARALLEL)) as pool:
        future_map = {pool.submit(_call_for_item, item): item for item in items}
        for future in as_completed(future_map):
            try:
                cid, res, err = future.result()
            except Exception as exc:  # noqa: BLE001
                logger.error("_llm_research 항목 처리 오류: %s", exc)
                had_api_error = True
                continue
            if err:
                had_api_error = True
            if res is not None:
                resolutions[cid] = res

    logger.info(
        "_llm_research: 완료 (요청 %d개 / URL 수집 성공 %d개)",
        len(items), len(resolutions),
    )
    return resolutions, had_api_error


# ────────────────────────────────────────────── 검색 API 재탐색 ──────────────

def _search_research(items: list[dict]) -> tuple[dict[str, dict], bool]:
    """
    Brave Search API를 이용해 실패 항목의 새 URL을 탐색한다.

    항목당 최대 2개 쿼리(한국어 + 영어)를 순서대로 실행하며,
    tried_urls에 이미 포함된 URL은 결과에서 제외한다.
    각 결과 URL에는 순위 기반 신뢰도(0.85 → 0.65)를 부여한다.

    반환 형식은 _llm_research와 동일해 _retry_phase1이 그대로 소비할 수 있다:
      official  → "candidate_urls":    [{"url": ..., "url_confidence": ...}, ...]
      reference → "reference_sources": [{"url": ..., "url_confidence": ...}, ...]

    Returns
    -------
    tuple[dict[str, dict], bool]
        (resolutions_by_cid, had_api_error)
        had_api_error=True : Brave API 호출 자체가 실패한 경우
                             (결과 없음은 False — no_url_found로 구분)
    """
    if not BRAVE_SEARCH_API_KEY:
        logger.warning("_search_research: BRAVE_SEARCH_API_KEY 미설정 — 검색 생략")
        return {}, True

    _ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
    _HEADERS  = {
        "Accept":               "application/json",
        "Accept-Encoding":      "gzip",
        "X-Subscription-Token": BRAVE_SEARCH_API_KEY,
    }

    # ── 내부 헬퍼 ────────────────────────────────────────────────────────────

    def _brave_search(query: str, count: int = 5) -> tuple[list[dict], bool]:
        """
        단일 쿼리를 Brave Search로 실행한다.
        Returns (results, had_error).
        - had_error=True  : 네트워크·인증 오류 등 실제 API 장애
        - had_error=False : 정상 응답 (결과 0개 포함)
        """
        try:
            resp = req_lib.get(
                _ENDPOINT,
                headers=_HEADERS,
                params={"q": query, "count": count},
                timeout=(3, 8),
            )
            resp.raise_for_status()
            return resp.json().get("web", {}).get("results", []), False
        except Exception as exc:  # noqa: BLE001
            logger.warning("_brave_search 오류 (%r): %s", query, exc)
            return [], True

    def _build_queries(item: dict) -> list[str]:
        """항목 유형에 따라 검색 쿼리 최대 2개를 생성한다 (한국어, 영어 순)."""
        stype = item.get("type", "")
        if stype == "official":
            brand   = item.get("brand", "").strip()
            product = item.get("product_name", "").strip()
            name    = f"{brand} {product}".strip() if brand else product
            return [f"{name} 공식 사이트", f"{name} official website"]
        if stype == "reference":
            method   = item.get("method_name", "").strip()
            provider = item.get("provider_type", "").strip()
            name     = f"{method} {provider}".strip() if provider else method
            return [f"{name} 공식 홈페이지", f"{name} official website"]
        return []

    def _process_item(item: dict) -> tuple[str, dict | None, bool]:
        """
        단일 항목에 대해 검색을 수행하고 resolution dict를 반환한다.

        Returns
        -------
        (cid, resolution_or_None, had_api_error)
        """
        cid     = item["candidate_id"]
        stype   = item.get("type", "")
        tried   = set(item.get("tried_urls", []))
        queries = _build_queries(item)

        if not queries:
            return cid, None, False

        seen: set[str]        = set()
        collected: list[dict] = []
        item_had_error        = False

        for query in queries:
            results, err = _brave_search(query)
            if err:
                item_had_error = True
                continue  # 다음 쿼리 시도

            for rank, r in enumerate(results):
                url = (r.get("url") or "").strip()
                if not url or url in tried or url in seen:
                    continue
                seen.add(url)
                # 1위: 0.85, 이후 0.05씩 감소, 최소 0.50
                confidence = round(max(0.50, 0.85 - rank * 0.05), 2)
                collected.append({"url": url, "url_confidence": confidence})
                if len(collected) >= 5:
                    break

            if len(collected) >= 5:
                break

        if not collected:
            # API 오류로 인한 미수집 vs. 검색 성공이나 유효 URL 없음을 구분
            return cid, None, item_had_error

        if stype == "official":
            resolution: dict = {
                "candidate_id":   cid,
                "resolution_type": "official",
                "candidate_urls": collected,
            }
        else:  # reference
            resolution = {
                "candidate_id":    cid,
                "resolution_type": "reference",
                "reference_sources": [
                    {"url": u["url"], "url_confidence": u["url_confidence"]}
                    for u in collected
                ],
            }
        logger.debug(
            "_search_research[%s]: 결과 %d개 수집 (쿼리: %s)",
            cid, len(collected), queries[0],
        )
        return cid, resolution, False

    # ── 병렬 처리 ─────────────────────────────────────────────────────────────
    resolutions: dict[str, dict] = {}
    had_api_error = False

    with ThreadPoolExecutor(max_workers=min(len(items), 5)) as pool:
        future_map = {pool.submit(_process_item, item): item for item in items}
        for future in as_completed(future_map):
            try:
                cid, resolution, err = future.result()
                if resolution:
                    resolutions[cid] = resolution
                if err:
                    had_api_error = True
            except Exception as exc:  # noqa: BLE001
                logger.error("_search_research 항목 처리 오류: %s", exc)
                had_api_error = True

    logger.info(
        "_search_research: 완료 (요청 %d개 / URL 수집 성공 %d개)",
        len(items), len(resolutions),
    )
    return resolutions, had_api_error


# ──────────────────────────────────────────────── Phase 2 적용 ───────────────

def _apply_phase2(
    sources: list[dict],
    manual_urls: dict[str, str],
    remove_ids: list[str],
    remove_ref_urls: dict[str, list[str]],
) -> list[dict]:
    """Phase 2 resume 결과를 official_sources에 적용한다."""

    # 수동 URL 수집 → 병렬 검증
    url_tasks: list[tuple[str, str]] = [
        (cid, url.strip())
        for cid, url in manual_urls.items()
        if url.strip()
    ]
    val_results: dict[str, tuple[int | None, str | None, str]] = {}

    if url_tasks:
        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
            future_map = {
                pool.submit(_validate_url, url): (cid, url)
                for cid, url in url_tasks
            }
            for future in as_completed(future_map):
                cid, url = future_map[future]
                try:
                    status, final_url = future.result()
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Phase2 URL 검증 예외 (%s): %s", url, exc)
                    status, final_url = None, None
                val_results[cid] = (status, final_url, url)

    remove_set = set(remove_ids)
    result: list[dict] = []

    for src in sources:
        cid   = src["candidate_id"]
        stype = src.get("source_type")

        # ── 항목 전체 제거 (case1 / case2_2) ────────────────────────────────
        if cid in remove_set:
            logger.info("Phase2: 항목 제거 — %s", cid)
            continue

        updated = dict(src)

        # ── 수동 URL 적용 ────────────────────────────────────────────────────
        if cid in val_results:
            status, final_url, tried_url = val_results[cid]
            ok = bool(status and 200 <= status < 400)
            if stype == "official":
                updated["primary_url"]     = final_url or tried_url
                updated["http_status"]     = status
                updated["validated"]       = ok
                updated["manual_override"] = True
                result.append(updated)
                continue
            elif stype == "reference":
                refs = list(src.get("reference_sources", []))
                refs.append({
                    "url":         final_url or tried_url,
                    "source_name": "사용자 직접 입력 (Phase 2)",
                    "description": "Phase 2 재시도에서 사용자가 직접 입력한 URL",
                    "final_url":   final_url or tried_url,
                    "http_status": status,
                    "validated":   ok,
                })
                updated["reference_sources"] = refs
                updated["validated"]         = any(r.get("validated") for r in refs)
                updated["manual_override"]   = True
                result.append(updated)
                continue

        # ── 특정 reference URL 제거 (case2_1) ────────────────────────────────
        if stype == "reference" and cid in remove_ref_urls:
            urls_to_remove = set(remove_ref_urls[cid])
            new_refs = [
                r for r in src.get("reference_sources", [])
                if r.get("url") not in urls_to_remove
                and r.get("final_url") not in urls_to_remove
            ]
            updated["reference_sources"] = new_refs
            updated["validated"]         = any(r.get("validated") for r in new_refs)
            logger.info("Phase2: ref URL %d개 제거 — %s", len(urls_to_remove), cid)
            result.append(updated)
            continue

        result.append(src)

    return result


# ─────────────────────────────────────────── 실패 항목 수집 ──────────────────

def _collect_failed(sources: list[dict]) -> list[dict]:
    """
    Phase 1 interrupt에 포함할 실패 항목 목록을 구성한다.

    포함 조건:
      - 모든 candidate: validated=False (HTTP 검증 실패)
      - own_* candidate 추가 조건: validated=True이더라도 llm_selected=False이면 포함.
          LLM이 공식 URL을 선택하지 못해 Brave fallback이 사용된 경우,
          HTTP 도달 가능성만으로는 "올바른 공식 URL"임을 보장할 수 없으므로
          사람이 직접 확인해야 한다.
    """
    failed = []
    for src in sources:
        cid   = src["candidate_id"]
        stype = src.get("source_type")

        is_validated  = bool(src.get("validated"))
        llm_selected  = bool(src.get("llm_selected", True))  # 기본값 True: 기존 캐시 호환
        is_own        = cid.startswith("own_")

        # 완전히 검증된 항목 스킵:
        #   - HTTP 성공(validated=True) + LLM URL 선택(llm_selected=True) → 신뢰 가능
        #   - comp_* / func_*: HTTP만 통과해도 pass (own_*보다 중요도 낮음)
        if is_validated and (llm_selected or not is_own):
            continue

        if stype == "official":
            entry: dict = {
                "candidate_id":           cid,
                "source_type":            "official",
                "brand":                  src.get("brand", ""),
                "product_name":           src.get("product_name", ""),
                "tried_url":              src.get("primary_url"),
                "llm_confidence":         src.get("llm_confidence"),
                "auto_retry_fail_status": src.get("_auto_retry_fail_status"),
            }
            # HTTP는 통과했으나 LLM 미선택인 own_* — UI에 원인 안내
            if is_own and is_validated and not llm_selected:
                entry["llm_not_selected"] = True
            failed.append(entry)

        elif stype == "reference":
            failed_refs = [
                r.get("final_url") or r.get("url")
                for r in src.get("reference_sources", [])
                if not r.get("validated") and r.get("url")
            ]
            if failed_refs:
                failed.append({
                    "candidate_id":    cid,
                    "source_type":     "reference",
                    "method_name":     src.get("method_name", ""),
                    "provider_type":   src.get("provider_type", ""),
                    "failed_ref_urls": failed_refs,
                })
    return failed


def _collect_failed_with_case(sources: list[dict]) -> list[dict]:
    """
    Phase 2 interrupt에 포함할 실패 항목 목록을 구성한다.
    action_case 결정:
      own_*     → None   (수동 URL 입력만 허용, 제거 불가)
      comp_* official → "case1"  (경쟁사 전체 제거)
      reference 부분 실패 → "case2_1" (특정 URL source 제거)
      reference 전체 실패 → "case2_2" (func_ 항목 전체 제거)
    """
    failed = []
    for src in sources:
        if src.get("validated"):
            continue
        stype = src.get("source_type")
        cid   = src["candidate_id"]

        if stype == "official":
            # own_* 항목은 제거 불가
            action_case = None if cid.startswith("own_") else "case1"
            failed.append({
                "candidate_id":       cid,
                "source_type":        "official",
                "brand":              src.get("brand", ""),
                "product_name":       src.get("product_name", ""),
                "tried_url":          src.get("primary_url"),
                "llm_confidence":     src.get("llm_confidence"),
                "action_case":        action_case,
                "retry_fail_reason":  src.get("_retry_fail_reason"),   # Phase 1 실패 원인
                "phase1_fail_status": src.get("_phase1_fail_status"),  # Phase 1 재시도 HTTP 상태
            })

        elif stype == "reference":
            all_refs    = src.get("reference_sources", [])
            failed_refs = [
                r.get("final_url") or r.get("url")
                for r in all_refs
                if not r.get("validated") and r.get("url")
            ]
            if not failed_refs:
                continue  # 모두 성공

            total  = len(all_refs)
            f_cnt  = len(failed_refs)
            action = "case2_2" if f_cnt >= total else "case2_1"
            failed.append({
                "candidate_id":      cid,
                "source_type":       "reference",
                "method_name":       src.get("method_name", ""),
                "provider_type":     src.get("provider_type", ""),
                "failed_ref_urls":   failed_refs,
                "action_case":       action,
                "retry_fail_reason": src.get("_retry_fail_reason"),  # Phase 1 실패 원인
            })

    return failed


# ──────────────────────────────────────────────────────── 헬퍼 ───────────────

def _make_result(sources: list[dict], started_at: str, thread_id: str = "") -> dict:
    """
    url_retry_node 최종 결과를 조립한다.

    own_* 항목 중 하나라도 validated=False이면 critical_error를 설정한다.
    → graph.py의 conditional edge가 이 플래그를 읽어 후속 노드를 건너뛰고 END로 분기.
    → 프런트엔드는 critical_error 수신 시 분석 중단 에러 화면을 표시한다.

    신뢰할 수 없는 분석 결과는 분석을 하지 않는 것보다 치명적이다.
    """
    finished_at     = datetime.now(timezone.utc).isoformat()
    validated_count = sum(1 for s in sources if s.get("validated"))
    logger.info(
        "url_retry_node: 완료 (검증 성공 %d/%d)",
        validated_count, len(sources),
    )

    # ── 종료 시 명시적 stage 전환 ─────────────────────────────────────────────
    if thread_id:
        try:
            set_progress(
                thread_id, "url_retry_done",
                detail=f"검증 성공 {validated_count}/{len(sources)}",
                current=validated_count, total=len(sources),
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("set_progress(url_retry_done) 실패 — 무시: %s", exc)

    # ── own_* 미검증 체크 — 파이프라인 강제 종료 ─────────────────────────────
    unvalidated_own = [
        s.get("product_name") or s["candidate_id"]
        for s in sources
        if s.get("candidate_id", "").startswith("own_") and not s.get("validated")
    ]
    step_status = "completed"
    critical_error: str | None = None

    if unvalidated_own:
        names = ", ".join(unvalidated_own)
        critical_error = (
            f"자사 상품({names})의 공식 URL을 끝내 확인하지 못했습니다. "
            "자사 데이터 없이 생성된 경쟁 분석은 신뢰할 수 없으므로 분석을 중단합니다. "
            "정확한 URL을 입력한 후 다시 시작해 주세요."
        )
        step_status = "failed"
        logger.error(
            "url_retry_node: 자사 URL 미검증 — 파이프라인 강제 종료 (상품: %s)",
            names,
        )

    step: AgentStep = {
        "step_name":   "UrlRetry",
        "status":      step_status,
        "started_at":  started_at,
        "finished_at": finished_at,
        **({"error_message": critical_error} if critical_error else {}),
    }

    result: dict = {"official_sources": sources, "agent_steps": [step]}
    if critical_error:
        result["critical_error"] = critical_error
    return result


def _pass(started_at: str, thread_id: str = "") -> dict:
    """실패 항목 없음 — interrupt 없이 pass-through."""
    if thread_id:
        try:
            set_progress(thread_id, "url_retry_done", detail="재시도 불필요")
        except Exception as exc:  # noqa: BLE001
            logger.debug("set_progress(url_retry_done, pass-through) 실패 — 무시: %s", exc)

    step: AgentStep = {
        "step_name":   "UrlRetry",
        "status":      "completed",
        "started_at":  started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    return {"agent_steps": [step]}


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
