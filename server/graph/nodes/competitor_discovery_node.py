"""
server/graph/nodes/competitor_discovery_node.py
------------------------------------------------
CompetitorDiscoveryAgent LangGraph 노드.

역할
----
state의 flat 필드(domain_name, own_product, problem_statement 등)를 읽어
CompetitorDiscoveryAgentInput을 조립하고, Claude CLI를 통해 경쟁사 후보군을
식별한 뒤 결과를 state에 저장한다.

출력 키
-------
- own_product_summary  : 자사 상품 시장 포지션 요약
- competition_axes     : 경쟁 분석 기준 축
- competitor_candidates: 브랜드 경쟁 후보 목록 (candidate_id는 임시값)
- functional_competitors: 전통적·기능적 대안 수단 목록 (func_* 접두사)
- excluded_or_deferred : 제외·보류 항목
- agent_steps          : 실행 이력 (Annotated reducer로 누적)

⚠️ candidate_id 주의
    이 노드의 competitor_candidates[*].candidate_id는 LLM이 임의로 생성한
    임시 플레이스홀더다. normalize_competitor_ids_node가 comp_* 슬러그로 교체한다.
    officialSourceResolverNode.js는 normalize 이후 state를 읽어야 한다.
"""

import copy
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import jsonschema

from server.config import AGENT_OUTPUT_CACHE_DIR, AGENTS_DIR, CLI_MODEL, CLI_TIMEOUT
from server.graph.agent_cache import (
    load_agent_output,
    make_cache_context,
    store_agent_output,
)
from server.graph.state import DomainAnalysisState, AgentStep
from server.llm.claude_cli_analyzer import ClaudeCodeCliAnalyzer

logger = logging.getLogger(__name__)


def competitor_discovery_node(state: DomainAnalysisState) -> dict:
    """
    CompetitorDiscoveryAgent를 실행하는 LangGraph 노드 함수.

    Parameters
    ----------
    state : DomainAnalysisState
        필수 키: domain_name, own_product, problem_statement,
                  target_user, core_value_props, geography, project_id

    Returns
    -------
    dict
        성공 시: own_product_summary, competition_axes,
                  competitor_candidates, excluded_or_deferred, agent_steps
        실패 시: errors, agent_steps
    """
    started_at = datetime.now(timezone.utc).isoformat()

    # ── 전제조건 검사 ────────────────────────────────────────────────────────
    required = ["domain_name", "own_product", "problem_statement",
                "target_user", "core_value_props", "geography"]
    missing = [k for k in required if not state.get(k)]
    if missing:
        return _error(started_at, f"필수 state 키 누락: {missing}. "
                                   "human_review_node가 먼저 실행되어야 합니다.")

    # ── 에이전트 파일 로드 ───────────────────────────────────────────────────
    agent_dir = AGENTS_DIR / "competitor_discovery"

    system_prompt = _load_text(agent_dir / "system_prompt_kr.md")
    if system_prompt is None:
        return _error(started_at, f"시스템 프롬프트를 찾을 수 없음: {agent_dir}")

    output_schema = _load_json(agent_dir / "output.schema.json")
    if output_schema is None:
        return _error(started_at, f"출력 스키마를 찾을 수 없음: {agent_dir}")

    # ── CompetitorDiscoveryAgentInput 조립 ───────────────────────────────────
    cd_input: dict = {
        "project_id":        state.get("project_id", ""),
        "run_id":            state.get("run_id", ""),
        "domain_name":       state["domain_name"],
        "own_product":       state["own_product"],
        "problem_statement": state["problem_statement"],
        "target_user":       state["target_user"],
        "core_value_props":  state["core_value_props"],
        "geography":         state["geography"],
    }
    # 선택 필드는 존재할 때만 포함
    for key in ("known_keywords", "usage_context", "business_constraints"):
        if state.get(key):
            cd_input[key] = state[key]  # type: ignore[literal-required]

    # ── 사용자 프롬프트 구성 ─────────────────────────────────────────────────
    user_prompt = (
        "아래 JSON 입력을 읽고, 경쟁 후보군을 분석하여 "
        "출력 schema를 만족하는 JSON만 반환하라.\n\n"
        f"입력:\n{json.dumps(cd_input, ensure_ascii=False, indent=2)}"
    )

    # ── 캐시 조회 → 미스 시 LLM 호출 ─────────────────────────────────────────
    cache_context = make_cache_context(
        agent_id="competitor_discovery",
        model=CLI_MODEL,
        system_prompt=system_prompt,
        output_schema=output_schema,
        prompt_version="competitor_discovery:v1",
    )
    # 캐시 키는 안정적인 식별 필드만 포함한다.
    #
    # 제외 이유:
    #   - run_id        : 매 실행마다 새로 생성되는 UUID
    #   - problem_statement, core_value_props, target_user, geography :
    #       human_review_node의 LLM 출력으로 채워지는 필드로, 동일 검색어라도
    #       매 실행마다 표현이 미세하게 달라져 불필요한 캐시 미스를 유발한다.
    #
    # 포함 이유:
    #   - project_id  : raw_query에서 결정론적으로 파생되는 식별자
    #   - domain_name : 사용자가 human_review에서 의도적으로 변경할 경우
    #                   경쟁 구도가 달라지므로 재실행이 필요
    #   - own_product : 자사 제품의 핵심 메타데이터(brand/name/category 등);
    #                   구조가 compact하고 제품 변경 시 재실행 필요
    cache_input = {
        "project_id":  cd_input.get("project_id", ""),
        "domain_name": cd_input.get("domain_name", ""),
        "own_product": cd_input.get("own_product", {}),
    }
    raw_output = load_agent_output(
        agent_id="competitor_discovery",
        cache_input=cache_input,
        context=cache_context,
        output_schema=output_schema,
        logger=logger,
    )

    # ── 레거시 폴백: project_id 기반 기존 entry 재활용 ────────────────────────
    # 캐시 키 구조가 변경된 경우(또는 최초 실행 시) 새 키로 miss가 발생한다.
    # 이때 project_id가 동일한 기존 entry를 스캔해 재활용하고,
    # 신규 키로 재저장하여 이후 실행은 빠른 직접 히트가 되도록 한다.
    if raw_output is None:
        raw_output = _find_cached_by_project_id(
            project_id=cache_input["project_id"],
            output_schema=output_schema,
            logger=logger,
        )
        if raw_output is not None:
            store_agent_output(
                agent_id="competitor_discovery",
                cache_input=cache_input,
                context=cache_context,
                output=raw_output,
                logger=logger,
            )
            logger.info(
                "competitor_discovery_node: 레거시 캐시 히트 (project_id=%s) → 신규 키로 재저장",
                cache_input["project_id"],
            )

    if raw_output is None:
        analyzer = ClaudeCodeCliAnalyzer(
            model=CLI_MODEL,
            timeout=CLI_TIMEOUT,
            system_prompt=system_prompt,
        )

        logger.info(
            "competitor_discovery_node: CLI 호출 시작 (project_id=%s)",
            cd_input["project_id"],
        )

        try:
            raw_output = analyzer.call_with_schema(
                prompt=user_prompt,
                output_schema=output_schema,
            )
        except RuntimeError as exc:
            logger.error("competitor_discovery_node: LLM 호출 실패 — %s", exc)
            return _error(started_at, str(exc))

        store_agent_output(
            agent_id="competitor_discovery",
            cache_input=cache_input,
            context=cache_context,
            output=raw_output,
            logger=logger,
        )

    # ── 메타 필드 동기화 ─────────────────────────────────────────────────────
    raw_output["run_id"]     = state.get("run_id", "")
    raw_output["project_id"] = cd_input["project_id"]
    raw_output["created_at"] = datetime.now(timezone.utc).isoformat()

    candidate_count  = len(raw_output.get("competitor_candidates", []))
    functional_count = len(raw_output.get("functional_competitors", []))
    finished_at = datetime.now(timezone.utc).isoformat()
    logger.info(
        "competitor_discovery_node: 완료 (브랜드 후보 %d개, 기능적 대안 %d개, project_id=%s)",
        candidate_count, functional_count, cd_input["project_id"],
    )

    step: AgentStep = {
        "step_name":   "CompetitorDiscoveryAgent",
        "status":      "completed",
        "started_at":  started_at,
        "finished_at": finished_at,
    }

    return {
        "own_product_summary":   raw_output.get("own_product_summary", {}),
        "competition_axes":      raw_output.get("competition_axes", []),
        "competitor_candidates": raw_output.get("competitor_candidates", []),
        "functional_competitors": raw_output.get("functional_competitors", []),
        "excluded_or_deferred":  raw_output.get("excluded_or_deferred", []),
        "agent_steps":           [step],
    }


# ── 내부 헬퍼 ─────────────────────────────────────────────────────────────────

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


def _find_cached_by_project_id(
    project_id: str,
    output_schema: dict | None,
    logger: logging.Logger | None,
) -> dict | None:
    """
    project_id가 일치하는 가장 최근 competitor_discovery 캐시 entry를 반환한다.

    캐시 키 포맷 변경 등으로 직접 키 히트가 불가능할 때 폴백으로 호출된다.
    유효한 entry가 발견되면 deep copy를 반환한다(호출자가 안전하게 수정 가능).
    output_schema가 주어지면 entry의 output을 검증하며, 실패 시 None을 반환한다.
    """
    cache_file = AGENT_OUTPUT_CACHE_DIR / "competitor_discovery.json"
    if not cache_file.exists():
        return None

    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    candidates = [
        entry
        for entry in data.get("entries", {}).values()
        if entry.get("input", {}).get("project_id") == project_id
    ]
    if not candidates:
        return None

    # updated_at → created_at 순으로 가장 최근 entry 선택
    best = max(
        candidates,
        key=lambda e: e.get("updated_at", e.get("created_at", "")),
    )
    output = copy.deepcopy(best.get("output"))

    if output_schema is not None:
        try:
            jsonschema.validate(output, output_schema)
        except jsonschema.ValidationError as exc:
            if logger:
                logger.warning(
                    "competitor_discovery _find_cached_by_project_id: "
                    "레거시 entry schema 검증 실패 (project_id=%s): %s",
                    project_id,
                    str(exc)[:200],
                )
            return None

    return output


def _error(started_at: str, message: str) -> dict:
    logger.error("competitor_discovery_node 오류: %s", message)
    return {
        "errors": [{
            "node":      "competitor_discovery_node",
            "error":     message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }],
        "agent_steps": [{
            "step_name":     "CompetitorDiscoveryAgent",
            "status":        "failed",
            "started_at":    started_at,
            "finished_at":   datetime.now(timezone.utc).isoformat(),
            "error_message": message,
        }],
    }
