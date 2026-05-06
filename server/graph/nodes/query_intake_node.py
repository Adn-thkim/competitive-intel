"""
server/graph/nodes/query_intake_node.py
----------------------------------------
QueryIntakeAgent LangGraph 노드.

역할
----
사용자가 입력한 짧은 검색어(raw_query)를 읽고 Claude CLI를 통해
CompetitorDiscoveryAgent 입력 초안(draft_competitor_discovery_input)을
포함한 구조화 JSON을 생성한 뒤 state["query_intake_output"]에 저장한다.

다음 노드인 human_review_node가 이 값을 interrupt() 인자로 프런트엔드에
전달하고, 사용자가 폼을 수정·승인하면 파이프라인이 재개된다.

이 노드가 필요한 이유
---------------------
- raw_query("토스 트래블카드")는 CompetitorDiscoveryAgent가 요구하는
  project_id, domain_name, own_product, problem_statement 등을 포함하지 않는다.
- 이 노드가 없으면 파이프라인을 시작하려면 사용자가 직접 전체 입력 구조를
  작성해야 하므로 UX가 크게 나빠진다.
- QueryIntakeAgent가 초안을 채우고 사용자가 검토·수정하는 패턴으로
  입력 품질과 사용 편의성을 동시에 확보한다.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from server.config import AGENTS_DIR, CLI_MODEL, CLI_TIMEOUT
from server.graph.agent_cache import (
    load_agent_output,
    make_cache_context,
    store_agent_output,
)
from server.graph.state import DomainAnalysisState, AgentStep
from server.llm.claude_cli_analyzer import ClaudeCodeCliAnalyzer

logger = logging.getLogger(__name__)


def query_intake_node(state: DomainAnalysisState) -> dict:
    """
    QueryIntakeAgent를 실행하는 LangGraph 노드 함수.

    Parameters
    ----------
    state : DomainAnalysisState
        현재 파이프라인 공유 상태.
        필수 키: "raw_query"

    Returns
    -------
    dict
        성공 시: {"query_intake_output": <QueryIntakeAgentOutput>, "agent_steps": [...]}
        실패 시: {"errors": [...], "agent_steps": [...]}
    """
    started_at = datetime.now(timezone.utc).isoformat()

    # ── 전제조건 검사 ────────────────────────────────────────────────────────
    raw_query: str = state.get("raw_query", "").strip()
    if not raw_query:
        return _error(started_at, "raw_query가 state에 없거나 비어 있습니다.")

    # ── 에이전트 파일 로드 ───────────────────────────────────────────────────
    agent_dir = AGENTS_DIR / "query_intake"

    system_prompt = _load_text(agent_dir / "system_prompt_kr.md")
    if system_prompt is None:
        return _error(started_at, f"시스템 프롬프트를 찾을 수 없음: {agent_dir / 'system_prompt_kr.md'}")

    output_schema = _load_json(agent_dir / "output.schema.json")
    if output_schema is None:
        return _error(started_at, f"출력 스키마를 찾을 수 없음: {agent_dir / 'output.schema.json'}")

    # ── 사용자 프롬프트 구성 ─────────────────────────────────────────────────
    run_id    = state.get("run_id", "")
    timestamp = datetime.now(timezone.utc).isoformat()

    user_prompt = (
        f"검색어: {raw_query}\n\n"
        f"run_id: {run_id or 'tmp_' + timestamp[:10]}\n"
        f"request_id: {state.get('request_id', 'req_' + timestamp[:10])}\n\n"
        "위 검색어를 기반으로 CompetitorDiscoveryAgent 입력 초안을 생성하라."
    )

    # ── 캐시 조회 → 미스 시 LLM 호출 ─────────────────────────────────────────
    cache_context = make_cache_context(
        agent_id="query_intake",
        model=CLI_MODEL,
        system_prompt=system_prompt,
        output_schema=output_schema,
        prompt_version="query_intake:v1",
    )
    cache_input = {"raw_query": raw_query}
    output = load_agent_output(
        agent_id="query_intake",
        cache_input=cache_input,
        context=cache_context,
        output_schema=output_schema,
        logger=logger,
    )

    if output is None:
        analyzer = ClaudeCodeCliAnalyzer(
            model=CLI_MODEL,
            timeout=CLI_TIMEOUT,
            system_prompt=system_prompt,
        )

        logger.info("query_intake_node: CLI 호출 시작 (raw_query=%r)", raw_query)

        try:
            output = analyzer.call_with_schema(
                prompt=user_prompt,
                output_schema=output_schema,
            )
        except RuntimeError as exc:
            logger.error("query_intake_node: LLM 호출 실패 — %s", exc)
            return _error(started_at, str(exc))

        store_agent_output(
            agent_id="query_intake",
            cache_input=cache_input,
            context=cache_context,
            output=output,
            logger=logger,
        )

    # ── 메타 필드 동기화 ─────────────────────────────────────────────────────
    output["raw_query"]  = raw_query
    output["run_id"]     = run_id
    output["request_id"] = state.get("request_id", output.get("request_id", ""))
    output["created_at"] = timestamp

    finished_at = datetime.now(timezone.utc).isoformat()
    logger.info(
        "query_intake_node: 완료 (needs_user_confirmation=%s)",
        output.get("needs_user_confirmation"),
    )

    step: AgentStep = {
        "step_name":   "QueryIntakeAgent",
        "status":      "completed",
        "started_at":  started_at,
        "finished_at": finished_at,
    }

    return {
        "query_intake_output": output,
        "agent_steps": [step],
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


def _error(started_at: str, message: str) -> dict:
    logger.error("query_intake_node 오류: %s", message)
    return {
        "errors": [{
            "node":      "query_intake_node",
            "error":     message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }],
        "agent_steps": [{
            "step_name":     "QueryIntakeAgent",
            "status":        "failed",
            "started_at":    started_at,
            "finished_at":   datetime.now(timezone.utc).isoformat(),
            "error_message": message,
        }],
    }
