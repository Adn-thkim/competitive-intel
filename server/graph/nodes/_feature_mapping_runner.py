"""
server/graph/nodes/_feature_mapping_runner.py (v0.10.27)
--------------------------------------------------------
5 통합 노드(`feature_mapping_<source>_node`)의 공통 실행 헬퍼.

각 통합 노드는 본 모듈의 `run_source_mapping(source, state, config)` 만 호출하는
thin wrapper. 본 헬퍼가:
  1. page meta 수집 (자기 source 의 *_urls_by_candidate 입력)
  2. report_type 별 병렬 LLM 호출 (자기 source 의 system_prompt + schema)
  3. *_raw_features 산출

D44 a 채택 — 설계 §5-6a 의 2단계 캐시 정책:
  - 단계 1 page meta: _fetch_meta 의 agent_id="page_meta_collect" 24h TTL 캐시 그대로
    (5 통합 노드 공유 — source 별 분리는 v1.0 시점 검토)
  - 단계 2 LLM 매핑: agent_id=f"feature_mapping_{source}",
    prompt_version=f"feature_mapping_{source}:v0.10.27"

LLM 호출 정책:
  - 자기 source 가 담당하는 report_type 만 처리 (_REPORT_TYPES_BY_SOURCE)
  - ClaudeCodeCliAnalyzer (turn-49 일관 패턴)
  - parallel=FEATURE_URL_MAPPER_PARALLEL (v0.10.9 정책 유지)
  - agents/feature_mapping_<source>/system_prompt_kr.md + output.schema.json 로드
"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

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
    _REPORT_TYPES_BY_SOURCE,
    _build_candidates_with_meta,
    _extract_active_reports,
    _filter_candidates_for_report,
    _load_json,
    _load_text,
    _strip_schema_patterns,
    _error,
)

logger = logging.getLogger(__name__)


# state 키 매핑 (owned_channels 만 owned_channel_* 단수형)
_OUTPUT_STATE_KEY: dict[str, str] = {
    "official":          "official_raw_features",
    "blog_community":    "blog_community_raw_features",
    "youtube_reactions": "youtube_reactions_raw_features",
    "owned_channels":    "owned_channel_raw_features",
    "macro":             "macro_raw_features",
}

_INPUT_URLS_KEY: dict[str, str] = {
    "official":          "official_urls_by_candidate",
    "blog_community":    "blog_community_urls_by_candidate",
    "youtube_reactions": "youtube_reactions_urls_by_candidate",
    "owned_channels":    "owned_channel_urls_by_candidate",
    "macro":             "macro_urls_by_candidate",
}

_STEP_NAME: dict[str, str] = {
    "official":          "FeatureMappingOfficial",
    "blog_community":    "FeatureMappingBlogCommunity",
    "youtube_reactions": "FeatureMappingYoutubeReactions",
    "owned_channels":    "FeatureMappingOwnedChannels",
    "macro":             "FeatureMappingMacro",
}


def run_source_mapping(
    *,
    source: str,
    state: DomainAnalysisState,
    config: dict | None,
) -> dict:
    """v0.10.27 — source-type 단위 통합 매핑 실행 (page meta + LLM 매핑 직렬).

    Returns
    -------
    dict
        {<output_state_key>: list[dict], agent_steps: list[AgentStep][+ errors]}
    """
    started_at = datetime.now(timezone.utc).isoformat()
    thread_id  = (config or {}).get("configurable", {}).get("thread_id", "")
    output_key = _OUTPUT_STATE_KEY[source]
    input_key  = _INPUT_URLS_KEY[source]
    step_name  = _STEP_NAME[source]

    print(
        f"🧠 [feature_mapping_{source}_node] ENTRY at {started_at} thread_id={thread_id!r}",
        flush=True,
    )

    # ── 에이전트 파일 로드 ───────────────────────────────────────────────────
    agent_dir     = AGENTS_DIR / f"feature_mapping_{source}"
    system_prompt = _load_text(agent_dir / "system_prompt_kr.md")
    output_schema = _load_json(agent_dir / "output.schema.json")
    if system_prompt is None:
        return _error(started_at, f"시스템 프롬프트 없음: {agent_dir}")
    if output_schema is None:
        return _error(started_at, f"출력 스키마 없음: {agent_dir}")

    # ── 입력 수집 ───────────────────────────────────────────────────────────
    urls_by_candidate: dict      = state.get(input_key) or {}
    domain_taxonomy: dict        = state.get("domain_taxonomy") or {}
    domain_name: str             = state.get("domain_name") or ""
    own_product: dict            = state.get("own_product") or {}
    official_sources: list       = state.get("official_sources") or []

    if not domain_taxonomy:
        return _error(started_at, "domain_taxonomy 가 state 에 없습니다.")

    # 자기 source 가 담당하는 report_type 중 active 만 필터
    all_active = _extract_active_reports(domain_taxonomy)
    source_report_types = _REPORT_TYPES_BY_SOURCE.get(source, ())
    active_reports_for_source = {
        rt: all_active[rt] for rt in source_report_types if rt in all_active
    }
    if not active_reports_for_source:
        logger.info(
            "feature_mapping_%s_node: 담당 report_type 중 active 0건 — 빈 결과 반환",
            source,
        )
        finished_at = datetime.now(timezone.utc).isoformat()
        return {
            output_key: [],
            "agent_steps": [{
                "step_name":   step_name,
                "status":      "completed",
                "started_at":  started_at,
                "finished_at": finished_at,
            }],
        }

    if not urls_by_candidate:
        logger.info(
            "feature_mapping_%s_node: %s 가 빈 dict — 빈 결과 반환",
            source, input_key,
        )
        finished_at = datetime.now(timezone.utc).isoformat()
        return {
            output_key: [],
            "agent_steps": [{
                "step_name":   step_name,
                "status":      "completed",
                "started_at":  started_at,
                "finished_at": finished_at,
            }],
        }

    # ── 단계 1: page meta 수집 (자기 source 의 URL 만) ──────────────────────
    if thread_id:
        try:
            set_progress(
                thread_id, f"feature_mapping_{source}_meta",
                detail=f"{source} 페이지 메타 수집 중",
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("set_progress(%s_meta) 실패: %s", source, exc)

    # official source 만 official_sources carry-through (다른 source 는 빈 list)
    candidates_with_meta = _build_candidates_with_meta(
        official_sources=official_sources if source == "official" else [],
        brave_urls_by_candidate=urls_by_candidate,
    )

    if not candidates_with_meta:
        logger.info(
            "feature_mapping_%s_node: candidates_with_meta 가 빈 list — 빈 결과 반환",
            source,
        )
        finished_at = datetime.now(timezone.utc).isoformat()
        return {
            output_key: [],
            "agent_steps": [{
                "step_name":   step_name,
                "status":      "completed",
                "started_at":  started_at,
                "finished_at": finished_at,
            }],
        }

    # ── 단계 2: report_type 별 병렬 LLM 호출 ───────────────────────────────
    cache_context = make_cache_context(
        agent_id=f"feature_mapping_{source}",
        model=CLI_MODEL,
        system_prompt=system_prompt,
        output_schema=output_schema,
        prompt_version=f"feature_mapping_{source}:v0.10.27",
    )

    # 캐시 조회 — cache_input 은 source-type 한정 active_reports + sorted candidate_ids
    cache_input = {
        "domain":         domain_name,
        "own_product":    {
            "brand":        own_product.get("brand", ""),
            "product_name": own_product.get("name") or own_product.get("product_name", ""),
        },
        "source":         source,
        "report_types":   sorted(active_reports_for_source.keys()),
        "candidate_ids":  sorted(c.get("candidate_id", "") for c in candidates_with_meta),
        "active_reports": active_reports_for_source,
    }
    cached = load_agent_output(
        agent_id=f"feature_mapping_{source}",
        cache_input=cache_input,
        context=cache_context,
        output_schema=output_schema,
        logger=logger,
    )

    if cached is not None:
        raw_features = cached.get("features", []) or []
        logger.info(
            "feature_mapping_%s_node: 캐시 hit (%d features)",
            source, len(raw_features),
        )
    else:
        if thread_id:
            try:
                set_progress(
                    thread_id, f"feature_mapping_{source}_llm",
                    detail=f"{source} AI 매핑 ({len(active_reports_for_source)}개 리포트)",
                    total=len(active_reports_for_source),
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("set_progress(%s_llm) 실패: %s", source, exc)

        analyzer       = ClaudeCodeCliAnalyzer(
            model=CLI_MODEL,
            timeout=FEATURE_MAPPING_LLM_TIMEOUT,
            system_prompt=system_prompt,
        )
        relaxed_schema = _strip_schema_patterns(output_schema)

        def _call_for_report(report_type: str) -> list[dict]:
            """단일 report_type 에 대한 LLM 호출 (자기 source 한정)."""
            filtered_candidates = _filter_candidates_for_report(
                candidates_with_meta, report_type,
            )
            r_input = {
                "domain":         domain_name,
                "own_product":    cache_input["own_product"],
                "report_type":    report_type,
                "active_reports": {report_type: active_reports_for_source[report_type]},
                "candidates":     filtered_candidates,
            }
            r_prompt = (
                "아래 입력을 분석하여 output schema를 만족하는 JSON만 반환하라.\n\n"
                "규칙:\n"
                f"1. active_reports의 '{report_type}' features만 처리한다. 임의 추가·삭제 금지.\n"
                "2. 각 feature_id는 taxonomy feature ID 앞에 feat_ 접두사를 붙인다.\n"
                f"3. report_type은 입력의 '{report_type}'을 그대로 사용한다.\n"
                "4. coverage='sufficient'이면 additional_urls는 반드시 빈 배열 []을 반환한다.\n"
                "5. additional_urls 도메인 제약은 system_prompt 의 source-type 별 정책을 따른다.\n"
                "6. 출력 features 순서는 active_reports[report_type].features 순서를 따른다.\n\n"
                f"입력:\n{json.dumps(r_input, ensure_ascii=False, separators=(',', ':'))}"
            )
            result = analyzer.call_with_schema(
                prompt=r_prompt, output_schema=relaxed_schema,
            )
            return result.get("features", [])

        results_by_report: dict[str, list[dict]] = {}
        errors_list: list[str] = []
        with ThreadPoolExecutor(max_workers=FEATURE_URL_MAPPER_PARALLEL) as pool:
            future_map = {
                pool.submit(_call_for_report, rt): rt
                for rt in active_reports_for_source
            }
            for future in as_completed(future_map):
                rt = future_map[future]
                try:
                    feats = future.result()
                    results_by_report[rt] = feats
                    logger.info(
                        "feature_mapping_%s_node: %s 완료 (%d features)",
                        source, rt, len(feats),
                    )
                except Exception as exc:  # noqa: BLE001
                    errors_list.append(f"{rt}: {exc}")
                    logger.error(
                        "feature_mapping_%s_node: %s 실패 — %s",
                        source, rt, exc,
                    )

        # 결과 집계 (source 가 담당하는 report_type 순서대로)
        raw_features = []
        for rt in source_report_types:
            raw_features.extend(results_by_report.get(rt, []))

        # 캐시 저장 (부분 성공도 저장 — 동일 입력에 재실행 시 캐시 hit)
        try:
            store_agent_output(
                agent_id=f"feature_mapping_{source}",
                cache_input=cache_input,
                context=cache_context,
                output={"features": raw_features},
                logger=logger,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("feature_mapping_%s 캐시 저장 실패: %s", source, exc)

        if errors_list:
            logger.warning(
                "feature_mapping_%s_node: %d report 부분 실패",
                source, len(errors_list),
            )

    finished_at = datetime.now(timezone.utc).isoformat()
    step: AgentStep = {
        "step_name":   step_name,
        "status":      "completed",
        "started_at":  started_at,
        "finished_at": finished_at,
    }
    logger.info(
        "feature_mapping_%s_node: 종료 (%d features)", source, len(raw_features),
    )
    return {
        output_key:    raw_features,
        "agent_steps": [step],
    }
