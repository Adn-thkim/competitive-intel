"""
server/graph/nodes/domain_modeling_node.py
------------------------------------------
DomainTaxonomyAgent LangGraph 노드.

역할
----
competitor_discovery_node 직후 실행되어, 도메인의 분석 목적(purpose)과
각 목적에 필요한 비교 feature·URL 유형(url_types)을 LLM이 추론한 taxonomy를
생성 또는 보강(enrich)한다.

taxonomy는 JSON 파일로 캐시되며, feature_url_mapper_node가
URL 수집 전략을 결정할 때 참조한다.

위치 (파이프라인 순서)
----
competitor_discovery_node
  → [domain_modeling_node]  ← 이 노드
    → normalize_competitor_ids_node

도메인 ID 레지스트리
--------------------
domain_name(한글 포함)을 파일명으로 직접 사용하는 대신, 정수 ID를 부여하는
레지스트리를 관리한다.

  data/taxonomy/domains.json  형식: { "id": "domain_name", ... }
  예) { "1": "소비자용 해외송금 앱", "2": "B2B HR SaaS" }

동일 domain_name이 재입력되면 기존 ID를 재사용한다.
신규 domain_name은 순번 ID를 부여하고 레지스트리를 갱신한다.

캐시 파일 네이밍: data/taxonomy/{id}_slug.json

캐시 전략
---------
1. domains.json에서 domain_name → ID 조회 (없으면 신규 등록)
2. data/taxonomy/{id}_slug.json 존재 여부 확인
3. 존재 + TTL(7일) 이내 + enrichment 불필요 → 캐시 로드, LLM 호출 생략
4. 존재 + (TTL 초과 또는 enrichment 트리거) → LLM에 기존 taxonomy 전달, add-only 보강
5. 존재하지 않음 → LLM이 taxonomy 최초 생성

enrichment 트리거
-----------------
competition_axes 중 기존 taxonomy의 active_purposes에 대응되지 않는 항목 비율이
ENRICHMENT_TRIGGER_THRESHOLD(30%) 이상이면 enrichment를 실행한다.

출력 키
-------
- domain_taxonomy : 생성·로드된 taxonomy dict (domain_id 필드 포함)
- agent_steps     : 실행 이력 (Annotated reducer로 누적)
"""

import copy
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

import jsonschema

from server.config import AGENTS_DIR, BASE_DIR, CLI_MODEL, CLI_TIMEOUT
from server.graph.agent_cache import (
    load_agent_output,
    make_cache_context,
    store_agent_output,
)
from server.graph.state import DomainAnalysisState, AgentStep
from server.llm.claude_cli_analyzer import ClaudeCodeCliAnalyzer

logger = logging.getLogger(__name__)

# ── 설정 상수 ─────────────────────────────────────────────────────────────────
TAXONOMY_DIR             = BASE_DIR / "data" / "taxonomy"
DOMAINS_REGISTRY_PATH    = TAXONOMY_DIR / "domains.json"
TAXONOMY_TTL_HOURS       = 168    # 7일
ENRICHMENT_TRIGGER_THRESHOLD = 0.30  # axes 중 30% 이상이 미대응이면 enrich 실행


# ── 노드 진입점 ───────────────────────────────────────────────────────────────

def domain_modeling_node(state: DomainAnalysisState) -> dict:
    """
    DomainTaxonomyAgent를 실행하는 LangGraph 노드 함수.

    Parameters
    ----------
    state : DomainAnalysisState
        필수 키: domain_name, own_product, problem_statement,
                  target_user, core_value_props, competition_axes
        선택 키: own_product_summary, project_id, run_id

    Returns
    -------
    dict
        성공 시: domain_taxonomy, agent_steps
        실패 시: errors, agent_steps
    """
    started_at = datetime.now(timezone.utc).isoformat()

    # ── 전제조건 검사 ────────────────────────────────────────────────────────
    required = ["domain_name", "own_product", "problem_statement",
                "target_user", "core_value_props", "competition_axes"]
    missing = [k for k in required if not state.get(k)]
    if missing:
        return _error(started_at,
                      f"필수 state 키 누락: {missing}. "
                      "competitor_discovery_node가 먼저 실행되어야 합니다.")

    domain_name      = state["domain_name"]
    competition_axes: list[str] = state.get("competition_axes", [])  # type: ignore[assignment]

    # ── 도메인 ID 조회 / 신규 등록 ──────────────────────────────────────────
    domain_id = _get_domain_id(domain_name)

    # ── 캐시 판정 ────────────────────────────────────────────────────────────
    cached = _load_cache(domain_id)
    mode, existing_taxonomy = _decide_mode(cached, competition_axes)

    if mode == "cache_hit":
        logger.info(
            "domain_modeling_node: 캐시 히트, LLM 생략 (id=%s, domain='%s')",
            domain_id, domain_name,
        )
        finished_at = datetime.now(timezone.utc).isoformat()
        step: AgentStep = {
            "step_name":   "DomainTaxonomyAgent",
            "status":      "completed",
            "started_at":  started_at,
            "finished_at": finished_at,
        }
        return {
            "domain_taxonomy": cached,
            "agent_steps":     [step],
        }

    # ── 에이전트 파일 로드 ───────────────────────────────────────────────────
    agent_dir = AGENTS_DIR / "domain_modeling"

    system_prompt = _load_text(agent_dir / "system_prompt_kr.md")
    if system_prompt is None:
        return _error(started_at, f"시스템 프롬프트를 찾을 수 없음: {agent_dir}")

    output_schema = _load_json(agent_dir / "output.schema.json")
    if output_schema is None:
        return _error(started_at, f"출력 스키마를 찾을 수 없음: {agent_dir}")

    # ── LLM 입력 조립 ────────────────────────────────────────────────────────
    taxonomy_input: dict = {
        "project_id":        state.get("project_id", ""),
        "domain_name":       domain_name,
        "own_product":       state["own_product"],
        "problem_statement": state["problem_statement"],
        "target_user":       state.get("target_user", []),
        "core_value_props":  state.get("core_value_props", []),
        "competition_axes":  competition_axes,
        "mode":              mode,  # "create" | "enrich"
    }
    if state.get("own_product_summary"):
        taxonomy_input["own_product_summary"] = state["own_product_summary"]
    if existing_taxonomy:
        taxonomy_input["existing_taxonomy"] = existing_taxonomy

    mode_label = "최초 생성" if mode == "create" else "보강(enrich)"
    user_prompt = (
        f"아래 JSON 입력을 읽고, 도메인 taxonomy를 {mode_label}하여 "
        "출력 schema를 만족하는 JSON만 반환하라.\n\n"
        f"입력:\n{json.dumps(taxonomy_input, ensure_ascii=False, indent=2)}"
    )

    # ── 캐시 조회 → 미스 시 LLM 호출 ─────────────────────────────────────────
    cache_context = make_cache_context(
        agent_id="domain_modeling",
        model=CLI_MODEL,
        system_prompt=system_prompt,
        output_schema=output_schema,
        prompt_version="domain_modeling:v1",
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
        analyzer = ClaudeCodeCliAnalyzer(
            model=CLI_MODEL,
            timeout=CLI_TIMEOUT,
            system_prompt=system_prompt,
        )

        logger.info(
            "domain_modeling_node: CLI 호출 시작 (id=%s, domain='%s', mode=%s)",
            domain_id, domain_name, mode,
        )

        # ── 패턴 제약을 완화한 스키마로 LLM 호출 ────────────────────────────
        # LLM이 대문자('SK_telecom')나 한글 혼용 값을 생성하더라도 호출이
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
            return _error(started_at, str(exc))

        # ── snake_case 정규화 + 엄격 스키마 재검증 ──────────────────────────
        raw_output = _normalize_taxonomy_output(raw_output)
        try:
            jsonschema.validate(raw_output, output_schema)
        except jsonschema.ValidationError as exc:
            msg = f"taxonomy 정규화 후 schema 검증 실패: {str(exc)[:300]}"
            logger.error("domain_modeling_node: %s", msg)
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
    if mode == "create":
        raw_output["created_at"] = now_iso
        raw_output["updated_at"] = now_iso
        raw_output["version"]    = 1
    else:
        # enrich: created_at 보존, updated_at·version 갱신
        raw_output["created_at"] = existing_taxonomy.get("created_at", now_iso)
        raw_output["updated_at"] = now_iso
        raw_output["version"]    = existing_taxonomy.get("version", 1) + 1

    # ── 캐시 저장 ────────────────────────────────────────────────────────────
    _save_cache(domain_id, raw_output)
    logger.info(
        "domain_modeling_node: taxonomy 저장 완료 (id=%s, domain='%s', purposes=%d개)",
        domain_id, domain_name, len(raw_output.get("active_purposes", [])),
    )

    finished_at = datetime.now(timezone.utc).isoformat()
    step_result: AgentStep = {
        "step_name":   "DomainTaxonomyAgent",
        "status":      "completed",
        "started_at":  started_at,
        "finished_at": finished_at,
    }

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


def _needs_enrichment(cached: dict, competition_axes: list[str]) -> bool:
    """
    competition_axes 중 기존 taxonomy의 active_purposes에 대응되지 않는
    축 비율이 ENRICHMENT_TRIGGER_THRESHOLD 이상이면 True를 반환한다.

    대응 여부 판단: axis 문자열이 purpose ID 또는 purpose label에 부분 일치하면 대응으로 간주.
    """
    if not competition_axes:
        return False

    active_purposes: list[str] = cached.get("active_purposes", [])
    purpose_config: dict = cached.get("purpose_config", {})

    # 검색 대상: purpose ID + label (소문자)
    purpose_tokens: set[str] = set()
    for pid in active_purposes:
        purpose_tokens.add(pid.lower())
        label: str = purpose_config.get(pid, {}).get("label", "")
        if label:
            purpose_tokens.add(label.lower())

    unmatched = 0
    for axis in competition_axes:
        axis_lower = axis.lower()
        if not any(
            token in axis_lower or axis_lower in token
            for token in purpose_tokens
        ):
            unmatched += 1

    ratio = unmatched / len(competition_axes)
    logger.debug(
        "taxonomy enrichment 검사: 미대응 %d/%d (%.0f%%, 임계값 %.0f%%)",
        unmatched, len(competition_axes),
        ratio * 100, ENRICHMENT_TRIGGER_THRESHOLD * 100,
    )
    return ratio >= ENRICHMENT_TRIGGER_THRESHOLD


def _decide_mode(
    cached: dict | None,
    competition_axes: list[str],
) -> tuple[str, dict]:
    """
    캐시 상태와 enrichment 필요 여부에 따라 실행 모드를 결정한다.

    Returns
    -------
    mode : str
        "cache_hit" | "create" | "enrich"
    existing_taxonomy : dict
        enrich 모드일 때 LLM에 전달할 기존 taxonomy. 그 외에는 빈 dict.
    """
    if cached is None:
        return "create", {}

    expired = _is_cache_expired(cached)
    enrich  = _needs_enrichment(cached, competition_axes)

    if not expired and not enrich:
        return "cache_hit", {}

    reason = []
    if expired:
        reason.append("TTL 초과")
    if enrich:
        reason.append("competition_axes 미대응 비율 초과")
    logger.info("taxonomy enrichment 트리거: %s", ", ".join(reason))
    return "enrich", cached


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


# ── taxonomy 정규화 유틸리티 ──────────────────────────────────────────────────

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
        'SK텔레콤 통신' → 'sk__'  → 'sk'  (한글 제거)
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
    _to_snake_id()로 정규화한다.

    정규화 대상:
    - domain_slug, domain_type
    - active_purposes 항목
    - purpose_config 키 + 내부 features/url_types 항목
    - feature_labels 키 동기화
    - url_type_priority 키 동기화
    """
    out = copy.deepcopy(raw)

    # ── domain_slug / domain_type ─────────────────────────────────────────
    for key in ("domain_slug", "domain_type"):
        if key in out and isinstance(out[key], str):
            out[key] = _to_snake_id(out[key])

    # ── active_purposes + 매핑 테이블 생성 ────────────────────────────────
    # 중복 발생 시 _2, _3 접미사를 붙여 uniqueItems 제약을 만족시킨다.
    old_purposes: list[str] = out.get("active_purposes") or []
    purpose_map: dict[str, str] = {}      # old_id → normalized_id
    new_purposes: list[str] = []
    seen_purposes: dict[str, int] = {}    # normalized → 발생 횟수
    for p in old_purposes:
        norm_p = _to_snake_id(str(p))
        if norm_p in seen_purposes:
            seen_purposes[norm_p] += 1
            norm_p = f"{norm_p}_{seen_purposes[norm_p]}"
        else:
            seen_purposes[norm_p] = 1
        purpose_map[p] = norm_p
        new_purposes.append(norm_p)
    out["active_purposes"] = new_purposes

    # ── purpose_config 키·내부 필드 정규화 ────────────────────────────────
    old_config: dict = out.get("purpose_config") or {}
    new_config: dict = {}

    for old_pid, cfg in old_config.items():
        new_pid = purpose_map.get(old_pid, _to_snake_id(str(old_pid)))
        if not isinstance(cfg, dict):
            new_config[new_pid] = cfg
            continue

        cfg = dict(cfg)

        # features + feature_labels 키 동기화
        old_feats: list[str] = cfg.get("features") or []
        feat_map: dict[str, str] = {}
        new_feats: list[str] = []
        for f in old_feats:
            nf = _to_snake_id(str(f))
            feat_map[f] = nf
            new_feats.append(nf)
        cfg["features"] = new_feats

        old_labels: dict = cfg.get("feature_labels") or {}
        new_labels: dict = {}
        for old_fid, label in old_labels.items():
            new_fid = feat_map.get(old_fid, _to_snake_id(str(old_fid)))
            new_labels[new_fid] = label
        cfg["feature_labels"] = new_labels

        # url_types + url_type_priority 키 동기화
        old_utypes: list[str] = cfg.get("url_types") or []
        utype_map: dict[str, str] = {}
        new_utypes: list[str] = []
        for u in old_utypes:
            nu = _to_snake_id(str(u))
            utype_map[u] = nu
            new_utypes.append(nu)
        cfg["url_types"] = new_utypes

        old_priority: dict = cfg.get("url_type_priority") or {}
        new_priority: dict = {}
        for old_ut, pri in old_priority.items():
            new_ut = utype_map.get(old_ut, _to_snake_id(str(old_ut)))
            new_priority[new_ut] = pri
        cfg["url_type_priority"] = new_priority

        new_config[new_pid] = cfg

    out["purpose_config"] = new_config
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
