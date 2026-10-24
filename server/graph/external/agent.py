"""추출 agent 호출 — 캐시 우선, output_schema 강제(jsonschema 검증 후 저장)."""
from __future__ import annotations

import json
import logging
from typing import Callable

from server.cache_ttl import get_ttl_hours
from server.config import AGENTS_DIR
from server.graph.agent_cache import load_agent_output, make_cache_context, store_agent_output

logger = logging.getLogger(__name__)

MODEL_ALIAS   = "sonnet"
TIMEOUT_S     = 900
MAX_RETRIES   = 2
PROMPT_V      = "v0.1"
AGENT_TTL     = get_ttl_hours("external_research_hours", 7200)   # 추출 agent 3종
SEARCH_TTL    = get_ttl_hours("external_search_hours", 7200)     # 탐색 agent
# 도구 사용 agent — 비대화형 CLI 에서 명시 허용할 도구·턴 상한·제한시간
TOOLS = {"external_search": {"allowed_tools": ["WebSearch", "WebFetch"], "max_turns": 40, "timeout": 1500}}
# 캐시 키에서 뺄 입력 — 기준일은 실행마다 바뀌지만 수집·추출 결과를 바꾸지 않는다(만료일 계산은 코드가 매번 함).
# 이 키를 넣으면 TTL(7200h)과 무관하게 매일 재탐색·재추출된다.
CACHE_EXCLUDE = ("as_of", "window_start")
HEAD = {
    "external_search":  "아래 조사 목표에 맞는 문서를 직접 검색하고 열어서, 관련 구절을 원문 그대로 모아라.",
    "external_history": "아래 문서에서 상품별 혜택·수수료 변경 이력, 공식 입장, 사업 구조 사실을 추출하라.",
    "external_finance": "아래 문서에서 상품·발급사의 공표 지표(가입자·계좌·발급·잔액·누적액)를 추출하라.",
    "external_market":  "아래 문서에서 시장 규모·점유율·성장률·수익성·혜택 만료 사실과 경쟁축의 이동을 추출하라.",
}

# 테스트·오프라인용 LLM 대체 훅: fn(agent_id, system_prompt, schema, prompt) -> dict
LLM_OVERRIDE: Callable[[str, str, dict, str], dict] | None = None


def load(agent_id: str) -> tuple[str, dict]:
    d = AGENTS_DIR / agent_id
    return ((d / "system_prompt_kr.md").read_text(encoding="utf-8"),
            json.loads((d / "output.schema.json").read_text(encoding="utf-8")))


def model() -> str:
    from server.graph.reaction.llm import MODELS
    return MODELS.get(MODEL_ALIAS, MODEL_ALIAS)


def cache_handles(agent_id: str, payload: dict) -> tuple[dict, dict, str, dict]:
    system_prompt, schema = load(agent_id)
    cache_payload = {k: v for k, v in payload.items() if k not in CACHE_EXCLUDE}
    ctx = make_cache_context(agent_id=agent_id, model=model(), system_prompt=system_prompt,
                             output_schema=schema, prompt_version=f"{agent_id}:{PROMPT_V}")
    return {"payload": cache_payload}, ctx, system_prompt, schema


def prompt(agent_id: str, payload: dict) -> str:
    return HEAD[agent_id] + "\n```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"


def run(agent_id: str, payload: dict, refresh: bool = False) -> tuple[dict, str]:
    cin, ctx, system_prompt, schema = cache_handles(agent_id, payload)
    if not refresh:
        hit = load_agent_output(agent_id=agent_id, cache_input=cin, context=ctx, output_schema=schema,
                                logger=logger, ttl_hours=SEARCH_TTL if agent_id == "external_search" else AGENT_TTL)
        if hit is not None:
            return hit, "cache"
    if LLM_OVERRIDE is not None:
        out = LLM_OVERRIDE(agent_id, system_prompt, schema, prompt(agent_id, payload))
    else:
        from server.llm.claude_cli_analyzer import ClaudeCodeCliAnalyzer
        t = TOOLS.get(agent_id, {})
        an = ClaudeCodeCliAnalyzer(model=model(), system_prompt=system_prompt, timeout=t.get("timeout", TIMEOUT_S),
                                   allowed_tools=t.get("allowed_tools"), max_turns=t.get("max_turns"))
        out = an.call_with_schema(prompt(agent_id, payload), schema, max_retries=MAX_RETRIES)
    import jsonschema
    jsonschema.validate(out, schema)
    store_agent_output(agent_id=agent_id, cache_input=cin, context=ctx, output=out, logger=logger)
    return out, "llm"
