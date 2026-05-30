"""
server/graph/nodes/feature_mapping_llm_node.py (v0.10.9)
--------------------------------------------------------
feature_url_mapper 4단계 분리 중 Step 2 — report_type 별 LLM 호출 (가장 무거운 단계).

역할
----
report_config 의 active 리포트 각각에 대해 Claude CLI 를 병렬 호출하여 feature × candidate
URL 매핑·coverage 평가·additional_urls 제안을 산출한다. A안(v0.10.8) `_filter_candidates_
for_report` 슬림화 + parallel=4(v0.10.9) 가 그대로 적용된다.

본 노드가 단일 노드로 분리된 이유:
  - 4단계 중 유일하게 LLM 호출이 발생하는 단계 (timeout 위험 격리)
  - report_type 별 분기 처리 및 캐시 적중률 관리 단위
  - UI 진행 상태에서 가장 길게 노출되는 stage 의 단독 표시

입력 state 키
-------------
- candidates_with_meta : page_meta_collect_node 산출
- domain_taxonomy      : report_config 추출용
- domain_name / own_product : LLM 입력 메타데이터

출력 state 키
-------------
- raw_features : list[dict] — Step 3 가 additional_urls 를 검증한 뒤 analysis_features 로 변환
- agent_steps  : 누적 reducer
"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from server.config import (
    AGENTS_DIR,
    CLI_MODEL,
    FEATURE_MAPPING_LLM_TIMEOUT,
    FEATURE_URL_MAPPER_PARALLEL,
)
from server.graph.agent_cache import (
    load_agent_output,
    make_cache_context,
    store_agent_output,
)
from server.graph.progress_store import set_progress
from server.graph.state import DomainAnalysisState, AgentStep
from server.llm.claude_cli_analyzer import ClaudeCodeCliAnalyzer
from server.graph.nodes.feature_url_mapper_node import (
    REPORT_TYPES,
    _extract_active_reports,
    _filter_candidates_for_report,
    _load_text,
    _load_json,
    _strip_schema_patterns,
    _error,
)

logger = logging.getLogger(__name__)


def feature_mapping_llm_node(state: DomainAnalysisState, config: dict | None = None) -> dict:
    """
    report_type 별 LLM 호출 (ThreadPoolExecutor, max_workers=FEATURE_URL_MAPPER_PARALLEL).

    Returns
    -------
    dict
        {raw_features, agent_steps} 또는 {errors, agent_steps}
    """
    started_at = datetime.now(timezone.utc).isoformat()
    thread_id  = (config or {}).get("configurable", {}).get("thread_id", "")

    print(
        f"🧠 [feature_mapping_llm_node] ENTRY at {started_at} thread_id={thread_id!r}",
        flush=True,
    )

    # ── 에이전트 파일 로드 ───────────────────────────────────────────────────
    agent_dir     = AGENTS_DIR / "feature_url_mapper"
    system_prompt = _load_text(agent_dir / "system_prompt_kr.md")
    output_schema = _load_json(agent_dir / "output.schema.json")
    if system_prompt is None:
        return _error(started_at, f"시스템 프롬프트 없음: {agent_dir}")
    if output_schema is None:
        return _error(started_at, f"출력 스키마 없음: {agent_dir}")

    # ── 입력 수집 ───────────────────────────────────────────────────────────
    candidates_with_meta: list[dict] = state.get("candidates_with_meta") or []
    domain_taxonomy: dict            = state.get("domain_taxonomy") or {}
    domain_name: str                 = state.get("domain_name") or ""
    own_product: dict                = state.get("own_product") or {}

    if not candidates_with_meta:
        return _error(started_at, "candidates_with_meta 가 state 에 없습니다.")
    if not domain_taxonomy:
        return _error(started_at, "domain_taxonomy 가 state 에 없습니다.")

    active_reports = _extract_active_reports(domain_taxonomy)
    if not active_reports:
        return _error(started_at,
                      "domain_taxonomy.report_config 에 active=true 인 리포트가 없습니다.")

    # ── LLM 입력 조립 ───────────────────────────────────────────────────────
    llm_input = {
        "domain":        domain_name,
        "own_product": {
            "brand":        own_product.get("brand", ""),
            "product_name": own_product.get("name", own_product.get("product_name", "")),
        },
        "active_reports": active_reports,
        "candidates":     candidates_with_meta,
    }
    total_features = sum(len(r.get("features", [])) for r in active_reports.values())
    logger.info(
        "feature_mapping_llm_node: 준비 "
        "(active 리포트=%d, features=%d, candidates=%d, parallel=%d)",
        len(active_reports), total_features, len(candidates_with_meta),
        FEATURE_URL_MAPPER_PARALLEL,
    )

    # ── 캐시 조회 ───────────────────────────────────────────────────────────
    cache_context = make_cache_context(
        agent_id="feature_url_mapper",
        model=CLI_MODEL,
        system_prompt=system_prompt,
        output_schema=output_schema,
        prompt_version="feature_url_mapper:v0.10",
    )
    # v0.10.12 A-3 — cache_key 안정화를 위해 cache_input 슬림화.
    # LLM 실제 입력(llm_input)은 그대로 사용하지만, 캐시 키 산정에는 변동 위험이 큰
    # page_title/meta_description/matched_report_types 등을 제외하고 URL list 만 사용한다.
    # Brave 결과의 미세 변동(snippet/title 변동) 이 캐시 미스를 유발하지 않도록 한다.
    cache_input = _make_stable_cache_input(llm_input)
    llm_output = load_agent_output(
        agent_id="feature_url_mapper",
        cache_input=cache_input,
        context=cache_context,
        output_schema=output_schema,
        logger=logger,
    )

    if llm_output is None:
        if thread_id:
            try:
                set_progress(
                    thread_id, "feature_mapping_llm",
                    detail=f"AI 매핑 ({len(active_reports)}개 리포트 분석, parallel={FEATURE_URL_MAPPER_PARALLEL})",
                    total=len(active_reports),
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("set_progress(llm) 실패: %s", exc)

        # v0.10.10 — feature_mapping_llm_node 전용 timeout (FEATURE_MAPPING_LLM_TIMEOUT).
        # 다른 노드(CLI_TIMEOUT 사용) 와 독립으로 환경변수 override 가능.
        analyzer       = ClaudeCodeCliAnalyzer(
            model=CLI_MODEL, timeout=FEATURE_MAPPING_LLM_TIMEOUT, system_prompt=system_prompt,
        )
        relaxed_schema = _strip_schema_patterns(output_schema)

        def _call_for_report(report_type: str) -> list[dict]:
            """단일 report_type 에 대한 LLM 호출 (A안 candidates 슬림화 적용)."""
            filtered_candidates = _filter_candidates_for_report(
                candidates_with_meta, report_type,
            )
            r_input = {
                "domain":         domain_name,
                "own_product":    llm_input["own_product"],
                "report_type":    report_type,
                "active_reports": {report_type: active_reports[report_type]},
                "candidates":     filtered_candidates,
            }
            r_prompt = (
                "아래 입력을 분석하여 output schema를 만족하는 JSON만 반환하라.\n\n"
                "규칙:\n"
                "1. active_reports의 해당 report_type features만 처리한다. 임의로 추가·삭제 금지.\n"
                "2. 각 feature_id는 taxonomy feature ID 앞에 feat_ 접두사를 붙인다.\n"
                "   예) 'transaction_fee_rate' → 'feat_transaction_fee_rate'\n"
                f"3. report_type은 입력의 '{report_type}'을 그대로 사용한다.\n"
                "4. coverage='sufficient'이면 additional_urls는 반드시 빈 배열 []을 반환한다.\n"
                "5. additional_urls는 existing_url의 sub-path 또는 동일 도메인 내 전용 페이지만.\n"
                "6. 출력 features 순서는 active_reports[report_type].features 순서를 따른다.\n\n"
                f"입력:\n{json.dumps(r_input, ensure_ascii=False, separators=(',', ':'))}"
            )
            result = analyzer.call_with_schema(
                prompt=r_prompt, output_schema=relaxed_schema,
            )
            return result.get("features", [])

        results_by_report: dict[str, list[dict]] = {}
        errors: list[str] = []

        with ThreadPoolExecutor(max_workers=FEATURE_URL_MAPPER_PARALLEL) as pool:
            future_map = {pool.submit(_call_for_report, rt): rt for rt in active_reports}
            for future in as_completed(future_map):
                rt = future_map[future]
                try:
                    feats = future.result()
                    results_by_report[rt] = feats
                    logger.info(
                        "feature_mapping_llm_node: report_type=%s 완료 (features=%d)",
                        rt, len(feats),
                    )
                except RuntimeError as exc:
                    logger.error(
                        "feature_mapping_llm_node: report_type=%s 실패 — %s", rt, exc,
                    )
                    errors.append(f"{rt}: {str(exc)[:120]}")

        if errors and not results_by_report:
            return _error(started_at,
                          "모든 report_type LLM 호출 실패:\n" + "\n".join(errors))
        elif errors:
            logger.warning(
                "feature_mapping_llm_node: %d개 리포트 실패, %d개 성공 결과로 부분 진행\n%s",
                len(errors), len(results_by_report), "\n".join(errors),
            )

        # active_reports 키 순서 유지
        all_features: list[dict] = []
        for rt in REPORT_TYPES:
            if rt in active_reports:
                all_features.extend(results_by_report.get(rt, []))

        llm_output = {"features": all_features}
        store_agent_output(
            agent_id="feature_url_mapper",
            cache_input=cache_input,
            context=cache_context,
            output=llm_output,
            logger=logger,
        )

    raw_features: list[dict] = llm_output.get("features", [])
    logger.info("feature_mapping_llm_node: 완료 (raw_features=%d)", len(raw_features))

    finished_at = datetime.now(timezone.utc).isoformat()
    step: AgentStep = {
        "step_name":   "FeatureMappingLlm",
        "status":      "completed",
        "started_at":  started_at,
        "finished_at": finished_at,
    }
    return {
        "raw_features": raw_features,
        "agent_steps":  [step],
    }


# ───────────── A-4: cache_input 거시 축약 헬퍼 (v0.10.13) ──────────────────

def _make_stable_cache_input(llm_input: dict) -> dict:
    """
    cache_key 결정론적 안정화를 위해 변동 위험 필드를 모두 제거한 cache_input 을 만든다.

    설계 의도 (v0.10.13 A-4)
    ------------------------
    v0.10.12 의 A-3 슬림화(URL list 정렬 보존) 는 candidates 의 URL list 가 매번
    미세 변동하는 경우(Brave 결과 순위 변동·새 URL 추가) 여전히 cache_key 가 변경되어
    캐시 미스를 유발하였다. 진단 결과 candidate 당 URL 수가 평균 23개·최대 64개로
    매우 커서 단 1개 URL 변동만으로도 cache_key 가 달라졌다.

    A-4 (본 헬퍼) 는 URL list 자체를 cache_input 에서 제외하고 **`sorted(candidate_ids)`
    만 사용**하여 결정론적 보장을 달성한다. LLM 실제 입력(llm_input) 은 그대로
    유지되므로 매핑 품질에는 영향 없다.

    안정성 분석
    -----------
    - domain         : 동일 검색어이면 동일 (안정)
    - own_product    : human_review 산출 (안정)
    - active_reports : domain_taxonomy 캐시 hit 시 동일 (안정)
    - candidate_ids  : normalize_competitor_ids 결과 (안정)
    - URL list / page_title / meta_description / matched_report_types : 모두 **제외** (변동 위험)

    캐시 적중 보장 조건
    -------------------
    동일 domain + own_product + active_reports keys + selected_competitor_ids 조합이면
    cache_key 가 결정론적으로 동일. URL list 변동·Brave 결과 변동 모두 흡수.

    trade-off
    ---------
    매우 거시적 키. LLM 입력의 URL meta 가 크게 다른데도 같은 캐시 사용. 그러나 동일
    candidate set 이면 URL 도 결국 같은 brand 의 페이지들이므로 LLM 결과 의미상 동일.
    동일 도메인 재실행 시 캐시 hit 가 사용자 요구사항(v0.10.13 의 명시 결정).
    """
    candidate_ids = sorted([
        cand.get("candidate_id", "")
        for cand in llm_input.get("candidates", [])
        if cand.get("candidate_id")
    ])
    return {
        "domain":         llm_input.get("domain", ""),
        "own_product":    llm_input.get("own_product", {}),
        "active_reports": llm_input.get("active_reports", {}),
        "candidate_ids":  candidate_ids,
    }
