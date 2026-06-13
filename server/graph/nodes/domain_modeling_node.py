"""
server/graph/nodes/domain_modeling_node.py
------------------------------------------
DomainTaxonomyAgent LangGraph 노드 (v0.9 단일 호출 + v0.10 report_config 스키마).

역할
----
competitor_discovery_node 직후 실행되어, 7종 분석 리포트(`report_config`)별로
필요한 feature·표준 카테고리·Brave 검색 쿼리 힌트·(reaction_insight 한정)
ABSA aspect codebook을 LLM이 추론한 taxonomy를 생성한다.

taxonomy는 JSON 파일로 캐시되며, feature_url_mapper_node가 Brave 검색으로
URL을 수집할 때 `search_query_hints`를 참조한다.

위치 (파이프라인 — v0.9 CD-fanout 토폴로지)
-------------------------------------------
competitor_discovery_node
  ├─→ normalize_competitor_ids_node → competitor_selection → ... → url_retry  (분기 A)
  └─→ [domain_modeling_node]  ← 이 노드 (분기 B, 병렬)
        └─→ feature_url_mapper_node (분기 A·B fan-in)

도메인 ID 레지스트리
--------------------
domain_name(한글 포함)을 파일명으로 직접 사용하는 대신, 정수 ID를 부여하는
레지스트리를 관리한다.

  data/taxonomy/domains.json  형식: { "id": "domain_name", ... }
  예) { "1": "소비자용 해외송금 앱", "2": "B2B HR SaaS" }

동일 domain_name이 재입력되면 기존 ID를 재사용한다.
신규 domain_name은 순번 ID를 부여하고 레지스트리를 갱신한다.

캐시 파일 네이밍: data/taxonomy/{id}_slug.json

캐시 전략 (v0.9 단일 호출 모드)
-------------------------------
1. domains.json에서 domain_name → ID 조회 (없으면 신규 등록)
2. data/taxonomy/{id}_slug.json 존재 여부 확인
3. 존재 + TTL(7일) 이내 → 캐시 로드, LLM 호출 생략
4. 존재 + TTL 초과 → LLM 재호출하여 전체 taxonomy 재생성 (1차/2차 분리 폐기)
5. 존재하지 않음 → LLM이 taxonomy 최초 생성

D2 재해석(v0.9): `competitor_discovery` 완료 직후 단일 호출. v0.6~v0.8의
1차(axes 없이) + 2차(enrichment) 분리는 폐기되었으며, `competition_axes`는
입력 시점에 항상 확보되어 있다.

출력 키
-------
- domain_taxonomy : 생성·로드된 taxonomy dict (domain_id·report_config 포함)
- agent_steps     : 실행 이력 (Annotated reducer로 누적)
"""

import copy
import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

import jsonschema

from server.config import AGENTS_DIR, BASE_DIR
from server.graph.agent_cache import (
    load_agent_output,
    make_cache_context,
    store_agent_output,
)
from server.graph.progress_store import set_branch_status
from server.graph.state import DomainAnalysisState, AgentStep

# soft TTL 캐시가 동일 입력에 대해 무기한 재사용하므로 CLI로 충분.
# (2026-06-13 사용자 결정 — API(temperature=0) 결정론은 soft TTL이 이미 보장)
_TAXONOMY_CLI_TIMEOUT  = 300
_METADATA_CLI_TIMEOUT  = 60    # Step1 소형 호출 — slug/type/community_sites 만

# Step1 전용 인라인 시스템 프롬프트 (파일 불필요 — 작고 안정적인 단일 임무)
_METADATA_SYSTEM_PROMPT = """\
당신은 DomainTaxonomyAgent의 Step 1 메타데이터 추출기입니다.
입력 JSON을 읽고 다음 세 가지만 결정하여 JSON 객체로만 반환하십시오.

1. domain_slug : 도메인 식별 슬러그 (lowercase snake_case). 예: consumer_travel_card
2. domain_type : 시장 유형 (lowercase snake_case). 예: consumer_remittance
3. community_sites : community_site_candidates 목록에서 분석 도메인과 주제가 직접
   관련된 커뮤니티 0~2개의 domain 값 선정.
   - 목록에 없는 도메인을 절대 생성하지 마십시오.
   - 클리앙·뽐뿌·디시인사이드 등 범용 커뮤니티는 코드가 기본 수집하므로 제외합니다.
   - 적합한 항목이 없으면 빈 배열 []을 반환합니다.

설명문·마크다운 없이 JSON 객체만 반환하십시오.\
"""

_METADATA_SCHEMA: dict = {
    "type": "object",
    "required": ["domain_slug", "domain_type", "community_sites"],
    "additionalProperties": False,
    "properties": {
        "domain_slug": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_]*$",
            "minLength": 3,
        },
        "domain_type": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_]*$",
            "minLength": 3,
        },
        "community_sites": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 2,
        },
    },
}

logger = logging.getLogger(__name__)

# ── 설정 상수 ─────────────────────────────────────────────────────────────────
TAXONOMY_DIR          = BASE_DIR / "data" / "taxonomy"
DOMAINS_REGISTRY_PATH = TAXONOMY_DIR / "domains.json"
TAXONOMY_TTL_HOURS    = 168    # 7일

# v0.10 D4 확정 — report_config 7종 enum (output.schema.json과 정합)
REPORT_TYPES = (
    "comparison_matrix",
    "reaction_insight",
    "marketing_social",
    "battlecard",
    "positioning_map",
    "market_context_swot",
    "executive_summary",
)


# ── v0.14 CE-D2 — 2군 커뮤니티 큐레이션 레지스트리 ────────────────────────────

def _load_registry_candidates() -> list[dict]:
    """data/community_registry.json → LLM 입력용 후보 목록 [{domain, label, topics}].

    레지스트리 부재·파싱 실패 시 빈 목록 (community_sites 미선정으로 graceful).
    """
    from server.config import COMMUNITY_REGISTRY_PATH  # 지연 import (순환 방지)
    try:
        data = json.loads(Path(COMMUNITY_REGISTRY_PATH).read_text(encoding="utf-8"))
        return [
            {"domain": s["domain"], "label": s.get("label", ""),
             "topics": s.get("topics", [])}
            for s in data.get("sites", []) if s.get("domain")
        ]
    except Exception as exc:  # noqa: BLE001
        logger.warning("community_registry 로드 실패 — community_sites 후보 없음: %s", exc)
        return []


def _validate_community_sites(out: dict) -> dict:
    """LLM 출력의 community_sites 를 registry 도메인으로 제한 + 최대 2개 (CE-D2).

    v0.15 이후 메인 노드 경로에서는 호출되지 않음.
    Step1(_call_metadata_step)이 동일 검증을 수행하여 main node에 결과를 전달한다.
    """
    sites = out.get("community_sites")
    if not isinstance(sites, list):
        out["community_sites"] = []
        return out
    valid = {c["domain"] for c in _load_registry_candidates()}
    kept = [s for s in sites if isinstance(s, str) and s in valid]
    dropped = [s for s in sites if s not in kept]
    if dropped:
        logger.warning("community_sites registry 외 도메인 제거: %s", dropped)
    out["community_sites"] = kept[:2]
    return out


def _call_metadata_step(
    taxonomy_input_base: dict,
    community_candidates: list[dict],
) -> dict:
    """Step 1 — domain_slug·domain_type·community_sites 사전 결정 (v0.15).

    taxonomy_input_base: community_site_candidates 미포함 기본 입력.
    community_candidates: _load_registry_candidates() 결과.

    역할 분리 이유:
    - community_sites 선정 지시를 main system_prompt에서 분리 →
      Step2(report_config 생성)가 B-only 리포트 feature 설계에 온전히 집중.
    - community_site_candidates가 Step1 전용이 됨 →
      registry 변경이 taxonomy fingerprint(= feature/codebook cache)에 영향 없음.

    실패 시 fallback 반환 (Step2 중단 방지). fallback domain_slug는 domain_name 변환값.
    """
    from server.llm.claude_cli_analyzer import ClaudeCodeCliAnalyzer  # 지연 import

    meta_input = {
        **taxonomy_input_base,
        "community_site_candidates": community_candidates,
    }
    prompt = (
        "아래 JSON을 읽고 domain_slug, domain_type, community_sites 만 결정하라.\n\n"
        f"```json\n{json.dumps(meta_input, ensure_ascii=False, indent=2)}\n```"
    )
    try:
        analyzer = ClaudeCodeCliAnalyzer(
            system_prompt=_METADATA_SYSTEM_PROMPT,
            timeout=_METADATA_CLI_TIMEOUT,
        )
        result: dict = analyzer.call_with_schema(prompt, _METADATA_SCHEMA)
        # registry 외 도메인 제거 + 최대 2개
        valid = {c["domain"] for c in community_candidates}
        result["community_sites"] = [
            s for s in result.get("community_sites", [])
            if isinstance(s, str) and s in valid
        ][:2]
        # snake_case 정규화
        result["domain_slug"] = _to_snake_id(result.get("domain_slug") or "")
        result["domain_type"] = _to_snake_id(result.get("domain_type") or "")
        logger.info(
            "_call_metadata_step 완료: slug=%s, type=%s, community_sites=%s",
            result["domain_slug"], result["domain_type"], result["community_sites"],
        )
        return result
    except Exception as exc:  # noqa: BLE001
        logger.warning("_call_metadata_step 실패, fallback 사용: %s", exc)
        fallback_slug = _to_snake_id(
            taxonomy_input_base.get("domain_name", "unknown_domain")
        ) or "unknown_domain"
        return {
            "domain_slug":     fallback_slug,
            "domain_type":     "unknown_domain_type",
            "community_sites": [],
        }


# ── 노드 진입점 ───────────────────────────────────────────────────────────────

def domain_modeling_node(state: DomainAnalysisState, config: dict | None = None) -> dict:
    """
    DomainTaxonomyAgent를 실행하는 LangGraph 노드 함수.

    v0.9 토폴로지에서 본 노드는 `competitor_discovery_node` 직후 분기 B로
    `normalize_competitor_ids_node`와 병렬 실행되며, `feature_url_mapper_node`에서
    fan-in 된다.

    v0.10.4: 진입·완료 시점에 `set_branch_status(thread_id, "domain_modeling", ...)`을
    emit하여 UI(`CompetitorSelectionPage.jsx`)가 분기 B 진행을 별도 단계로 표시할 수 있게 한다.
    이는 progress_store의 stage 단일 트랙(분기 A)과 별개로 운영된다.

    Parameters
    ----------
    state : DomainAnalysisState
        필수 키: domain_name, own_product, problem_statement,
                  target_user, core_value_props, competition_axes
        선택 키: own_product_summary, project_id, analysis_direction
                  (D7 옵션 b 확정 v0.7, default "mixed")
    config : dict | None
        LangGraph가 전달하는 RunnableConfig. configurable.thread_id 추출용.

    Returns
    -------
    dict
        성공 시: domain_taxonomy, agent_steps
        실패 시: errors, agent_steps
    """
    started_at = datetime.now(timezone.utc).isoformat()
    thread_id  = (config or {}).get("configurable", {}).get("thread_id", "")

    # ── 진단 print (v0.9 분기 B 진입 검증용, 본 블록은 디버깅 후 제거 가능) ──
    print(
        f"⚡⚡⚡ [domain_modeling_node] ENTRY at {started_at} "
        f"thread_id={thread_id!r} state_keys={sorted(state.keys())[:6]}...",
        flush=True,
    )

    # ── 진입 시 분기 B 상태 emit (v0.10.4) ───────────────────────────────────
    if thread_id:
        try:
            set_branch_status(thread_id, "domain_modeling", "running")
        except Exception as exc:  # noqa: BLE001
            logger.debug("set_branch_status(running) 실패: %s", exc)

    # ── 전제조건 검사 ────────────────────────────────────────────────────────
    required = ["domain_name", "own_product", "problem_statement",
                "target_user", "core_value_props", "competition_axes"]
    missing = [k for k in required if not state.get(k)]
    if missing:
        if thread_id:
            try:
                set_branch_status(thread_id, "domain_modeling", "failed")
            except Exception as exc:  # noqa: BLE001
                logger.debug("set_branch_status(failed) 실패: %s", exc)
        return _error(started_at,
                      f"필수 state 키 누락: {missing}. "
                      "competitor_discovery_node가 먼저 실행되어야 합니다.")

    domain_name = state["domain_name"]
    competition_axes: list[str] = state.get("competition_axes", [])  # type: ignore[assignment]
    analysis_direction = state.get("analysis_direction") or "mixed"

    # ── LLM 입력 조립 (캐시 지문 비교를 위해 캐시 판정 이전에 구성) ──────────────
    # v0.15: community_site_candidates 제거 → fingerprint 안정화.
    # registry 변경이 feature/codebook taxonomy를 재생성하지 않도록 Step1로 분리.
    taxonomy_input: dict = {
        "project_id":         state.get("project_id", ""),
        "domain_name":        domain_name,
        "own_product":        state["own_product"],
        "problem_statement":  state["problem_statement"],
        "target_user":        state.get("target_user", []),
        "core_value_props":   state.get("core_value_props", []),
        "competition_axes":   competition_axes,
        "analysis_direction": analysis_direction,
    }
    if state.get("own_product_summary"):
        taxonomy_input["own_product_summary"] = state["own_product_summary"]
    input_fp = _fingerprint(taxonomy_input)
    # 강제 재생성 플래그 (human_review 등 UI 에서 설정 가능; 기본 False)
    force_refresh = bool(state.get("force_taxonomy_refresh"))

    # ── 도메인 ID 조회 / 신규 등록 ──────────────────────────────────────────
    domain_id = _get_domain_id(domain_name)

    # ── 캐시 판정 (soft TTL — 2026-06-11) ───────────────────────────────────
    # 재사용 조건: force 아님 + (신선 OR 만료됐어도 입력 지문 동일).
    # 7일 TTL 이 지나도 입력(taxonomy_input)이 같으면 재생성하지 않고 재사용한다 →
    # 비결정 재생성으로 feature ID 가 통째로 바뀌는 회귀를 방지.
    cached = _load_cache(domain_id)
    if cached is not None and not force_refresh and (
        not _is_cache_expired(cached) or cached.get("input_fingerprint") == input_fp
    ):
        reuse_reason = ("신선" if not _is_cache_expired(cached)
                        else "만료·입력동일(soft TTL)")
        logger.info(
            "domain_modeling_node: 캐시 재사용(%s), LLM 생략 (id=%s, domain='%s')",
            reuse_reason, domain_id, domain_name,
        )
        finished_at = datetime.now(timezone.utc).isoformat()
        step: AgentStep = {
            "step_name":   "DomainTaxonomyAgent",
            "status":      "completed",
            "started_at":  started_at,
            "finished_at": finished_at,
        }
        if thread_id:
            try:
                set_branch_status(thread_id, "domain_modeling", "done")
            except Exception as exc:  # noqa: BLE001
                logger.debug("set_branch_status(done, cache) 실패: %s", exc)
        return {
            "domain_taxonomy": cached,
            "agent_steps":     [step],
        }

    # ── 에이전트 파일 로드 ───────────────────────────────────────────────────
    agent_dir = AGENTS_DIR / "domain_modeling"

    system_prompt = _load_text(agent_dir / "system_prompt_kr.md")
    if system_prompt is None:
        if thread_id:
            try: set_branch_status(thread_id, "domain_modeling", "failed")
            except Exception: pass  # noqa: BLE001
        return _error(started_at, f"시스템 프롬프트를 찾을 수 없음: {agent_dir}")

    output_schema = _load_json(agent_dir / "output.schema.json")
    if output_schema is None:
        if thread_id:
            try: set_branch_status(thread_id, "domain_modeling", "failed")
            except Exception: pass  # noqa: BLE001
        return _error(started_at, f"출력 스키마를 찾을 수 없음: {agent_dir}")

    # ── 캐시 조회 → 미스 시 LLM 호출 ─────────────────────────────────────────
    cache_context = make_cache_context(
        agent_id="domain_modeling",
        model="claude_cli",
        system_prompt=system_prompt,
        output_schema=output_schema,
        prompt_version="domain_modeling:v0.12-cli",
    )
    cache_input = {
        **taxonomy_input,
        "domain_id": domain_id,
    }
    raw_output = load_agent_output(
        agent_id="domain_modeling",
        cache_input=cache_input,
        context=cache_context,
        output_schema=output_schema,
        logger=logger,
    )

    if raw_output is None:
        # ── Step 1: domain_slug · domain_type · community_sites (소형 호출) ──
        # community_site_candidates 는 fingerprint 제외 → Step1 전용 입력.
        community_candidates = _load_registry_candidates()
        step1 = _call_metadata_step(taxonomy_input, community_candidates)

        # ── Step 2: report_config 7종 (메인 호출) ─────────────────────────────
        # Step1 결과를 user_prompt에 명시 → system_prompt의 §3-1 선정 지시가
        # report_config feature 생성 집중을 방해하지 않도록 한다.
        from server.llm.claude_cli_analyzer import ClaudeCodeCliAnalyzer  # 지연 import
        analyzer = ClaudeCodeCliAnalyzer(
            system_prompt=system_prompt,
            timeout=_TAXONOMY_CLI_TIMEOUT,
        )
        user_prompt = (
            "아래 JSON 입력을 읽고, 7종 리포트(`report_config`) 단위의 도메인 "
            "taxonomy를 생성하여 출력 schema를 만족하는 JSON만 반환하라.\n\n"
            "※ 다음 값은 Step 1에서 사전 결정됨 — 그대로 출력할 것 (재추론·변경 금지):\n"
            f"  domain_slug: \"{step1['domain_slug']}\"\n"
            f"  domain_type: \"{step1['domain_type']}\"\n"
            f"  community_sites: "
            f"{json.dumps(step1['community_sites'], ensure_ascii=False)}\n\n"
            f"입력:\n{json.dumps(taxonomy_input, ensure_ascii=False, indent=2)}"
        )

        logger.info(
            "domain_modeling_node: CLI Step2 호출 시작 (id=%s, domain='%s', direction=%s)",
            domain_id, domain_name, analysis_direction,
        )

        # ── 패턴 제약을 완화한 스키마로 LLM 호출 ────────────────────────────
        # LLM이 대문자('SK_telecom')나 한글 혼용 식별자를 생성하더라도 호출이
        # 실패하지 않도록 pattern 제약을 제거한 relaxed schema를 사용한다.
        # 이후 _normalize_taxonomy_output()으로 snake_case 정규화 후
        # 엄격한 스키마로 재검증한다.
        relaxed_schema = _strip_patterns(output_schema)

        try:
            raw_output = analyzer.call_with_schema(
                prompt=user_prompt,
                output_schema=relaxed_schema,
            )
        except RuntimeError as exc:
            logger.error("domain_modeling_node: LLM 호출 실패 — %s", exc)
            if thread_id:
                try: set_branch_status(thread_id, "domain_modeling", "failed")
                except Exception: pass  # noqa: BLE001
            return _error(started_at, str(exc))

        # ── snake_case 정규화 + community_sites Step1 값 강제 적용 ──────────
        raw_output = _normalize_taxonomy_output(raw_output)
        raw_output["community_sites"] = step1["community_sites"]  # v0.15 Step1 확정값
        try:
            jsonschema.validate(raw_output, output_schema)
        except jsonschema.ValidationError as exc:
            msg = f"taxonomy 정규화 후 schema 검증 실패: {str(exc)[:300]}"
            logger.error("domain_modeling_node: %s", msg)
            if thread_id:
                try: set_branch_status(thread_id, "domain_modeling", "failed")
                except Exception: pass  # noqa: BLE001
            return _error(started_at, msg)

        store_agent_output(
            agent_id="domain_modeling",
            cache_input=cache_input,
            context=cache_context,
            output=raw_output,
            logger=logger,
        )

    # ── taxonomy 후처리 ──────────────────────────────────────────────────────
    # domain_id 추가: 레지스트리 기반 파일명 식별자를 taxonomy에 기록
    raw_output["domain_id"] = domain_id

    now_iso = datetime.now(timezone.utc).isoformat()
    # soft TTL — 다음 실행에서 입력 동일성 비교에 사용할 지문 기록
    raw_output["input_fingerprint"] = input_fp
    # human_review(interrupt#1) 드롭다운의 "동일 입력" 매칭용 query 수준 지문
    raw_output["query_fingerprint"] = query_fingerprint(taxonomy_input)
    if cached is None:
        # 신규 생성
        raw_output["created_at"] = now_iso
        raw_output["updated_at"] = now_iso
        raw_output["version"]    = 1
    else:
        # 재생성(force 또는 입력 변경): created_at 보존, updated_at·version 갱신
        raw_output["created_at"] = cached.get("created_at", now_iso)
        raw_output["updated_at"] = now_iso
        raw_output["version"]    = cached.get("version", 1) + 1

    # ── 캐시 저장 ────────────────────────────────────────────────────────────
    _save_cache(domain_id, raw_output)

    # active 리포트 수 카운트 (v0.10 — report_config 7종 중 active=true)
    active_count = sum(
        1
        for r in REPORT_TYPES
        if raw_output.get("report_config", {}).get(r, {}).get("active") is True
    )
    logger.info(
        "domain_modeling_node: taxonomy 저장 완료 "
        "(id=%s, domain='%s', active 리포트=%d/7)",
        domain_id, domain_name, active_count,
    )

    finished_at = datetime.now(timezone.utc).isoformat()
    step_result: AgentStep = {
        "step_name":   "DomainTaxonomyAgent",
        "status":      "completed",
        "started_at":  started_at,
        "finished_at": finished_at,
    }

    # ── 완료 시 분기 B 상태 emit (v0.10.4) ───────────────────────────────────
    if thread_id:
        try:
            set_branch_status(thread_id, "domain_modeling", "done")
        except Exception as exc:  # noqa: BLE001
            logger.debug("set_branch_status(done, llm) 실패: %s", exc)

    return {
        "domain_taxonomy": raw_output,
        "agent_steps":     [step_result],
    }


# ── 도메인 ID 레지스트리 ──────────────────────────────────────────────────────

def _get_domain_id(domain_name: str) -> str:
    """
    domains.json 레지스트리에서 domain_name에 대응하는 ID를 반환한다.
    신규 domain_name이면 순번 ID를 부여하고 레지스트리를 갱신한다.

    레지스트리 형식
    ---------------
    { "id": "domain_name", ... }
    예) { "1": "소비자용 해외송금 앱", "2": "B2B HR SaaS" }

    파일 경로: data/taxonomy/domains.json

    Parameters
    ----------
    domain_name : str
        human_review_node가 확정한 domain_name. 한글 포함 가능.

    Returns
    -------
    str
        정수 문자열 ID. 예: "1", "2", "10"
    """
    TAXONOMY_DIR.mkdir(parents=True, exist_ok=True)

    # 레지스트리 로드
    registry: dict[str, str] = {}
    if DOMAINS_REGISTRY_PATH.exists():
        try:
            registry = json.loads(
                DOMAINS_REGISTRY_PATH.read_text(encoding="utf-8")
            )
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("domains.json 로드 실패, 빈 레지스트리로 시작: %s", exc)
            registry = {}

    # domain_name으로 기존 ID 검색 (value 기준 역방향 조회)
    for id_, name in registry.items():
        if name == domain_name:
            logger.debug("domain ID 재사용: id=%s, domain='%s'", id_, domain_name)
            return id_

    # 신규: 기존 최대 ID + 1 부여 (레지스트리가 비어 있으면 1부터 시작)
    existing_ids = [int(k) for k in registry if k.isdigit()]
    next_id = str(max(existing_ids) + 1 if existing_ids else 1)

    registry[next_id] = domain_name

    try:
        DOMAINS_REGISTRY_PATH.write_text(
            json.dumps(registry, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(
            "domains.json 갱신: 신규 등록 id=%s, domain='%s'", next_id, domain_name
        )
    except OSError as exc:
        logger.warning("domains.json 저장 실패: %s", exc)

    return next_id


# ── 캐시 유틸리티 ─────────────────────────────────────────────────────────────

def _fingerprint(taxonomy_input: dict) -> str:
    """taxonomy_input 의 안정적 SHA-256 해시 — soft TTL 의 입력 동일성 판정용."""
    blob = json.dumps(taxonomy_input, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def query_fingerprint(fields: dict) -> str:
    """query_intake 수준 입력의 지문 — human_review(interrupt#1) 의 "동일 입력" 판정용.

    competition_axes 는 competitor_discovery(human_review 이후) 산출이라 interrupt#1
    시점에 알 수 없다. 따라서 그 시점에 확정된 query_intake 수준 필드만으로 지문을
    만든다(도메인·문제정의·타깃·핵심가치·자사상품명). full input_fingerprint 와 별개.
    """
    payload = {
        "domain_name":       fields.get("domain_name", ""),
        "problem_statement": fields.get("problem_statement", ""),
        "target_user":       fields.get("target_user", []),
        "core_value_props":  fields.get("core_value_props", []),
        "own_product_name":  (fields.get("own_product") or {}).get("name", ""),
    }
    return _fingerprint(payload)


def _load_cache(domain_id: str) -> dict | None:
    """
    캐시 파일 data/taxonomy/{domain_id}_slug.json 을 로드한다.
    파일이 없거나 파싱 실패 시 None을 반환한다.
    """
    cache_path = TAXONOMY_DIR / f"{domain_id}_slug.json"
    if not cache_path.exists():
        return None
    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("taxonomy 캐시 로드 실패 (%s): %s", cache_path, exc)
        return None


def _is_cache_expired(cached: dict) -> bool:
    """캐시 updated_at 기준으로 TTL 초과 여부를 반환한다."""
    updated_at_str = cached.get("updated_at") or cached.get("created_at")
    if not updated_at_str:
        return True
    try:
        updated_at = datetime.fromisoformat(updated_at_str.replace("Z", "+00:00"))
        age_hours = (
            datetime.now(timezone.utc) - updated_at
        ).total_seconds() / 3600
        return age_hours > TAXONOMY_TTL_HOURS
    except (ValueError, TypeError):
        return True


def _save_cache(domain_id: str, taxonomy: dict) -> None:
    """taxonomy를 data/taxonomy/{domain_id}_slug.json에 저장한다."""
    try:
        TAXONOMY_DIR.mkdir(parents=True, exist_ok=True)
        cache_path = TAXONOMY_DIR / f"{domain_id}_slug.json"
        cache_path.write_text(
            json.dumps(taxonomy, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning(
            "taxonomy 캐시 저장 실패 (id=%s): %s", domain_id, exc
        )


# ── 공통 헬퍼 ─────────────────────────────────────────────────────────────────

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


# ── taxonomy 정규화 유틸리티 (v0.10 — report_config 스키마 기준) ──────────────

def _to_snake_id(text: str) -> str:
    """
    임의의 문자열을 소문자 snake_case 식별자로 변환한다.

    - 알파벳/숫자 이외의 모든 문자(한글, 공백, 특수문자 등)를 _ 로 교체
    - 연속된 _ 를 하나로 압축
    - 소문자 변환
    - 앞뒤 _ 제거
    - 첫 문자가 숫자이면 'x_' 접두사 추가
    - 결과가 비면 'unknown' 반환

    예) 'SK_telecom' → 'sk_telecom'
        'mobile_carrier_Korea' → 'mobile_carrier_korea'
        'SK텔레콤 통신' → 'sk__' → 'sk' (한글 제거)
    """
    result = re.sub(r"[^a-zA-Z0-9]+", "_", text)
    result = result.lower().strip("_")
    result = re.sub(r"_+", "_", result)
    if not result:
        return "unknown"
    if not result[0].isalpha():
        result = "x_" + result
    return result


def _strip_patterns(schema: object) -> object:
    """
    JSON Schema에서 모든 'pattern' 제약을 재귀적으로 제거한다.

    LLM 호출 시 패턴 검증 없이 구조만 요구하기 위해 사용한다.
    call_with_schema 이후 _normalize_taxonomy_output()으로 정규화하고
    원본 schema로 재검증한다.
    """
    if isinstance(schema, dict):
        return {
            k: _strip_patterns(v)
            for k, v in schema.items()
            if k != "pattern"
        }
    if isinstance(schema, list):
        return [_strip_patterns(item) for item in schema]
    return schema


def _normalize_taxonomy_output(raw: dict) -> dict:
    """
    LLM 출력 중 ^[a-z][a-z0-9_]*$ 패턴 제약을 위반하는 식별자 필드를
    _to_snake_id()로 정규화한다. (v0.10 — report_config 스키마 기준)

    정규화 대상:
    - domain_slug, domain_type
    - report_config[*].features 항목 + feature_labels 키 동기화
    - report_config[*].action_lens 키(feature_id) 동기화
    - report_config["reaction_insight"].aspect_codebook[*].aspect_id

    `report_config`의 키 자체(7종 enum)는 고정이므로 정규화하지 않는다.
    `categories`는 한국어/영문 자유 텍스트이므로 정규화 대상 아님.
    `search_query_hints`는 한국어 자연어 쿼리이므로 정규화 대상 아님.
    """
    out = copy.deepcopy(raw)

    # ── domain_slug / domain_type ─────────────────────────────────────────
    for key in ("domain_slug", "domain_type"):
        if key in out and isinstance(out[key], str):
            out[key] = _to_snake_id(out[key])

    # ── report_config 내부 정규화 ─────────────────────────────────────────
    report_config = out.get("report_config")
    if not isinstance(report_config, dict):
        # 스키마 위반이지만 다음 단계의 jsonschema.validate가 보고하도록 그대로 반환
        return out

    for report_type, entry in report_config.items():
        if not isinstance(entry, dict):
            continue

        # features + feature_labels 키 동기화
        old_feats: list[str] = entry.get("features") or []
        feat_map: dict[str, str] = {}
        new_feats: list[str] = []
        for f in old_feats:
            nf = _to_snake_id(str(f))
            feat_map[f] = nf
            new_feats.append(nf)
        entry["features"] = new_feats

        old_labels: dict = entry.get("feature_labels") or {}
        new_labels: dict = {}
        for old_fid, label in old_labels.items():
            new_fid = feat_map.get(old_fid, _to_snake_id(str(old_fid)))
            new_labels[new_fid] = label
        entry["feature_labels"] = new_labels

        # action_lens 키 동기화 (D7 mixed 옵셔널)
        old_lens: dict = entry.get("action_lens") or {}
        if old_lens:
            new_lens: dict = {}
            for old_fid, lens_val in old_lens.items():
                new_fid = feat_map.get(old_fid, _to_snake_id(str(old_fid)))
                new_lens[new_fid] = lens_val
            entry["action_lens"] = new_lens

        # aspect_codebook의 aspect_id 정규화 (reaction_insight 한정)
        codebook = entry.get("aspect_codebook")
        if isinstance(codebook, list):
            for aspect in codebook:
                if isinstance(aspect, dict) and "aspect_id" in aspect:
                    aspect["aspect_id"] = _to_snake_id(str(aspect["aspect_id"]))

    out["report_config"] = report_config
    return out


def _error(started_at: str, message: str) -> dict:
    logger.error("domain_modeling_node 오류: %s", message)
    return {
        "errors": [{
            "node":      "domain_modeling_node",
            "error":     message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }],
        "agent_steps": [{
            "step_name":     "DomainTaxonomyAgent",
            "status":        "failed",
            "started_at":    started_at,
            "finished_at":   datetime.now(timezone.utc).isoformat(),
            "error_message": message,
        }],
    }
