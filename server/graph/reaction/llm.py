"""server/graph/reaction/llm.py — 반응 파이프라인 LLM 호출·병렬 인프라 (Phase 1a 정본).

이전 원본: scripts/test_gate_prescreen_v2.py(_cli·_pmap·_MODELS) + poc_opinion_units.py(_ABORT·
_is_quota_exhausted). gate·extract·cluster 모두가 의존하는 뿌리 인프라.

- `cli(model, system, timeout, max_retries)` → `call(prompt, schema)` 클로저(스키마 강제 호출).
  기존 프로덕션 LLM 어댑터(server.llm.claude_cli_analyzer.ClaudeCodeCliAnalyzer)를 그대로 사용.
- `pmap` : 순서 보존 병렬 map.
- `MODELS` : 모델 별칭 → 모델 문자열. (향후 server.config 로 이관 검토)
- 세션 한도(429/usage limit) 조기중단: `ABORT` 이벤트 + `is_quota_exhausted`.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

# 모델 별칭 (PoC와 동일; 값 변경 시 여기만 수정). TODO: server.config 상수로 이관 검토.
HAIKU = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-4-6"
MODELS = {"haiku": HAIKU, "sonnet": SONNET}

# 세션 사용량 한도 소진 → 전체 실행 조기 중단(이후 배치는 호출 없이 즉시 반환).
ABORT = threading.Event()


def is_quota_exhausted(msg: str) -> bool:
    """구독 세션 사용량 한도 소진인가? (일시적 rate-limit 과 구분 — 재시도로 풀리지 않는다)"""
    m = (msg or "").lower()
    return "session limit" in m or "usage limit" in m or '"api_error_status":429' in m


def pmap(fn, items, workers):
    """items 각각에 fn 적용(순서 보존, 병렬)."""
    out = [None] * len(items)
    if not items:
        return out
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(items)))) as ex:
        futs = {ex.submit(fn, it): i for i, it in enumerate(items)}
        for f in as_completed(futs):
            out[futs[f]] = f.result()
    return out


def cli(model, system, timeout, max_retries):
    """스키마 강제 LLM 호출 클로저. 반환: call(prompt, schema) → dict."""
    from server.llm.claude_cli_analyzer import ClaudeCodeCliAnalyzer
    an = ClaudeCodeCliAnalyzer(model=model, system_prompt=system, timeout=timeout)
    return lambda prompt, schema: an.call_with_schema(prompt, schema, max_retries=max_retries)
