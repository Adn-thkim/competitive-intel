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
    _is_recent_enough,
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

    # ── owned_channels 분기 (v0.10.28b D45 a — LLM 호출 생략) ────────────────
    # marketing_social 의 feature 값(SNS 게시물 빈도·콘텐츠 키워드 등) 은 v1.0 §6-6a
    # 의 수집 노드 책임. 본 노드는 candidate 별 공식 채널 URL 식별까지만 담당.
    # LLM 호출 없이 owned_channel_urls_by_candidate 의 candidate × platform 구조를
    # 그대로 carry-through. feature_selection_node 가 owned_channels_card payload 로
    # 변환하여 marketing_social 카드 UI 에 별도 렌더링.
    if source == "owned_channels":
        return _carry_owned_channels(state, started_at, _STEP_NAME[source], thread_id)

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

    # v0.10.24 (D37 적용) — blog_community 한정 발행일 ≤ 36개월 검증.
    # 후기 신선도 보장 — 옛 후기 (3년 초과) 제외로 LLM 입력 슬림화 + 최신 의견 우선.
    # published_at 부재 또는 파싱 실패 시 안전 통과 (보수적).
    if source == "blog_community":
        before_total = sum(len(c.get("validated_urls", []) or []) for c in candidates_with_meta)
        for cand in candidates_with_meta:
            cand["validated_urls"] = [
                u for u in (cand.get("validated_urls") or [])
                if _is_recent_enough(u.get("published_at", ""), max_months=36)
            ]
        after_total = sum(len(c.get("validated_urls", []) or []) for c in candidates_with_meta)
        excluded = before_total - after_total
        if excluded > 0:
            logger.info(
                "feature_mapping_blog_community_node: 발행일 36개월 초과 URL %d건 제외 (%d → %d)",
                excluded, before_total, after_total,
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

        # 캐시 저장 — LLM 호출 실패(errors_list)가 있으면 저장하지 않는다.
        # feature_mapping 캐시는 TTL 이 없어, 불완전/빈 결과가 한 번 저장되면
        # 동일 입력 재실행에도 영구히 hit 되어 회복 불가(cache-poisoning).
        # 따라서 모든 report 가 성공한 경우에만 캐시한다 (재시도 가능성 보존).
        if errors_list:
            logger.warning(
                "feature_mapping_%s_node: %d report 실패 — 캐시 저장 생략 (재시도 허용)",
                source, len(errors_list),
            )
        else:
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


# ─── v0.10.28b D45 a — owned_channels LLM 호출 생략 헬퍼 ─────────────────────

def _carry_owned_channels(
    state: DomainAnalysisState,
    started_at: str,
    step_name: str,
    thread_id: str,
) -> dict:
    """v0.10.28b D45 a — owned_channels LLM 호출 생략 + URL carry-through.

    `owned_channel_urls_by_candidate` 를 그대로 `owned_channel_raw_features` 로
    변환. feature_selection_node 가 본 결과를 `owned_channels_card` payload 로
    재구성하여 marketing_social 카드의 candidate × platform 매트릭스로 렌더링.

    LLM 호출을 생략하는 이유:
    - marketing_social 의 feature (SNS 게시물 빈도·콘텐츠 키워드 등) 은 채널
      페이지 URL 한 개만 보고 LLM 이 판정 불가
    - 실제 feature 값 계산은 v1.0 §6-6a 의 수집 노드 (youtube_channel_metadata_
      collection·blog_rss_collection·pr_release_collection) 가 채널 방문 후 수행
    - 본 노드는 URL 발견만 책임

    출력 구조 (owned_channel_raw_features)
    --------------------------------------
    feature_mapping_owned_channels 의 옛 schema (feature × candidate × URL 매트릭스)
    가 아닌, **candidate × platform 그룹화 placeholder** 로 산출:

    [
      {
        "report_type":  "marketing_social",
        "feature_id":   "feat_owned_channels_summary",
        "feature_name": "공식 운영 채널",
        "description":  "candidate 별 공식 SNS·블로그·보도자료·YouTube 채널 식별 결과",
        "priority":     "high",
        "candidate_coverage": [
          {
            "candidate_id":   "own_xxx" | "comp_xxx",
            "coverage":       "sufficient" | "partial" | "not_found",
            "existing_urls":  [{url, origin="owned_channel_search", platform,
                                account_scope, channel_id, ...}, ...],
            "additional_urls": [],
          }, ...
        ],
      }
    ]

    이 placeholder 는 feature_selection_node 가 owned_channels_card 로 재구성 시
    원본 입력으로 사용. coverage 는 platform 수 기반 단순 임계 (≥ 3 platforms →
    sufficient, ≥ 1 → partial, 0 → not_found).
    """
    if thread_id:
        try:
            set_progress(
                thread_id, "feature_mapping_owned_channels",
                detail="공식 채널 URL carry-through (D45 a — LLM 호출 생략)",
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("set_progress(owned_channels) 실패: %s", exc)

    urls_by_candidate: dict = state.get("owned_channel_urls_by_candidate") or {}

    # candidate × platform 그룹화 → candidate_coverage 항목으로 변환
    candidate_coverage: list[dict] = []
    for cid, urls in urls_by_candidate.items():
        if not urls:
            continue
        platforms_found = {u.get("platform") for u in urls if u.get("platform")}
        platform_count = len(platforms_found)
        if platform_count >= 3:
            coverage = "sufficient"
        elif platform_count >= 1:
            coverage = "partial"
        else:
            coverage = "not_found"

        candidate_coverage.append({
            "candidate_id":    cid,
            "coverage":        coverage,
            "existing_urls":   list(urls),   # url_discovery_owned_channels 의 결과 그대로
            "additional_urls": [],
        })

    # 단일 placeholder feature 로 marketing_social 에 carry
    raw_features: list[dict] = []
    if candidate_coverage:
        raw_features.append({
            "report_type":  "marketing_social",
            "feature_id":   "feat_owned_channels_summary",
            "feature_name": "공식 운영 채널",
            "description":  "candidate 별 공식 SNS·블로그·보도자료·YouTube 채널 식별 결과 (LLM 미사용, v1.0 §6-6a 수집 전 단계)",
            "priority":     "high",
            "candidate_coverage": candidate_coverage,
        })

    total_urls = sum(len(c["existing_urls"]) for c in candidate_coverage)
    logger.info(
        "feature_mapping_owned_channels_node (D45 a carry): %d candidate · %d URL "
        "(LLM 호출 생략)", len(candidate_coverage), total_urls,
    )

    finished_at = datetime.now(timezone.utc).isoformat()
    return {
        "owned_channel_raw_features": raw_features,
        "agent_steps": [{
            "step_name":   step_name,
            "status":      "completed",
            "started_at":  started_at,
            "finished_at": finished_at,
        }],
    }
