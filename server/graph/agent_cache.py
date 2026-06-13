"""
server/graph/agent_cache.py
---------------------------
LLM agent 출력 JSON 캐시 유틸리티.

각 agent는 동일한 의미 입력과 동일한 prompt/schema/model 지문에 대해
Claude CLI를 다시 호출하지 않고 로컬 JSON 캐시를 재사용한다.

파일 형식
---------
data/cache/agent_outputs/{agent_id}.json

{
  "_meta": {...},
  "entries": {
    "<sha256 cache key>": {
      "cache_key": "...",
      "input_summary": "...",
      "input": {...},
      "context": {...},
      "output": {...},
      "hit_count": 0,
      ...
    }
  }
}
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jsonschema

from server.config import AGENT_OUTPUT_CACHE_DIR


_SCHEMA_VERSION = 1
_LOCKS: dict[Path, threading.Lock] = {}


def make_cache_context(
    *,
    agent_id: str,
    model: str,
    system_prompt: str,
    output_schema: dict,
    prompt_version: str = "v1",
) -> dict[str, Any]:
    """
    캐시 유효성을 결정하는 실행 컨텍스트 지문을 만든다.

    system_prompt나 output_schema가 바뀌면 hash가 달라져 기존 캐시를
    자동으로 사용하지 않는다.
    """
    return {
        "agent_id": agent_id,
        "model": model,
        "prompt_version": prompt_version,
        "system_prompt_sha256": _sha256_text(system_prompt),
        "output_schema_sha256": _sha256_json(output_schema),
        "cache_schema_version": _SCHEMA_VERSION,
    }


def load_agent_output(
    *,
    agent_id: str,
    cache_input: dict,
    context: dict,
    output_schema: dict | None = None,
    logger: logging.Logger | None = None,
    ttl_hours: float | None = None,
) -> dict | None:
    """
    agent 출력 캐시를 조회한다.

    Parameters
    ----------
    output_schema : dict | None
        주어지면 캐시된 output 을 검증하고 실패 시 캐시 미스로 처리한다.
    ttl_hours : float | None (v0.10.12 신설)
        주어지면 entry 의 updated_at 기준 TTL 검사. 초과 시 캐시 미스로 처리한다.
        None(기본값) 이면 TTL 무한(기존 동작 유지).

    반환값은 호출자가 안전하게 수정할 수 있도록 deep copy 한다.
    """
    path = _cache_path(agent_id)
    cache_key = make_cache_key(agent_id, cache_input, context)
    data = _read_cache(path, agent_id)
    entry = data.get("entries", {}).get(cache_key)
    if not entry:
        return None

    # v0.10.12 — TTL 검사 (옵션). 만료 시 캐시 미스 처리.
    if ttl_hours is not None:
        updated_at_str = entry.get("updated_at") or entry.get("created_at")
        if updated_at_str:
            try:
                updated_at = datetime.fromisoformat(updated_at_str.replace("Z", "+00:00"))
                age_hours = (
                    datetime.now(timezone.utc) - updated_at
                ).total_seconds() / 3600
                if age_hours > ttl_hours:
                    if logger:
                        logger.info(
                            "%s: cache TTL expired (age=%.1fh > %sh, key=%s)",
                            agent_id, age_hours, ttl_hours, cache_key[:12],
                        )
                    return None
            except (ValueError, TypeError):
                # timestamp 파싱 실패 → 안전하게 캐시 미스
                return None

    output = copy.deepcopy(entry.get("output"))
    if output_schema is not None:
        try:
            jsonschema.validate(output, output_schema)
        except jsonschema.ValidationError as exc:
            if logger:
                logger.warning(
                    "%s cache invalid — schema validation failed: %s",
                    agent_id,
                    str(exc)[:200],
                )
            return None

    now = _now_iso()
    entry["hit_count"] = int(entry.get("hit_count", 0)) + 1
    entry["last_hit_at"] = now
    data["_meta"]["updated_at"] = now
    _write_cache(path, data)

    if logger:
        logger.info("%s: output cache hit (key=%s)", agent_id, cache_key[:12])
    return output


def store_agent_output(
    *,
    agent_id: str,
    cache_input: dict,
    context: dict,
    output: dict,
    logger: logging.Logger | None = None,
) -> str:
    """agent 출력 JSON을 캐시에 저장하고 cache key를 반환한다."""
    path = _cache_path(agent_id)
    cache_key = make_cache_key(agent_id, cache_input, context)
    data = _read_cache(path, agent_id)
    now = _now_iso()

    existing = data["entries"].get(cache_key, {})
    data["entries"][cache_key] = {
        "cache_key": cache_key,
        "input_summary": _summarize_input(cache_input),
        "created_at": existing.get("created_at", now),
        "updated_at": now,
        "last_hit_at": existing.get("last_hit_at"),
        "hit_count": int(existing.get("hit_count", 0)),
        "input": copy.deepcopy(cache_input),
        "context": copy.deepcopy(context),
        "output": copy.deepcopy(output),
    }
    data["_meta"]["updated_at"] = now
    data["_meta"]["total_entries"] = len(data["entries"])
    _write_cache(path, data)

    if logger:
        logger.info("%s: output cache stored (key=%s)", agent_id, cache_key[:12])
    return cache_key


def make_cache_key(agent_id: str, cache_input: dict, context: dict) -> str:
    """agent_id + cache_input + context를 안정적으로 해시한다."""
    payload = {
        "agent_id": agent_id,
        "input": cache_input,
        "context": context,
    }
    return _sha256_json(payload)


def _cache_path(agent_id: str) -> Path:
    safe_name = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in agent_id)
    return AGENT_OUTPUT_CACHE_DIR / f"{safe_name}.json"


def _read_cache(path: Path, agent_id: str) -> dict:
    lock = _lock_for(path)
    with lock:
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data.get("entries"), dict):
                    data.setdefault("_meta", {})
                    data["_meta"].setdefault("agent_id", agent_id)
                    return data
            except (json.JSONDecodeError, OSError):
                pass
            except UnicodeDecodeError as exc:
                # truncated write (프로세스 중단) 로 파일이 손상된 경우.
                # 크래시 대신 cache miss 로 처리하고 빈 캐시로 초기화한다.
                import logging as _logging
                _logging.getLogger(__name__).warning(
                    "_read_cache: %s 파일 손상(UnicodeDecodeError) — "
                    "cache miss 처리 후 초기화. (%s)", path.name, exc
                )
                try:
                    path.write_bytes(b"{}")
                except OSError:
                    pass
        now = _now_iso()
        return {
            "_meta": {
                "schema_version": _SCHEMA_VERSION,
                "agent_id": agent_id,
                "created_at": now,
                "updated_at": now,
                "total_entries": 0,
                "description": "LLM agent output cache keyed by normalized input and prompt/schema/model fingerprint.",
            },
            "entries": {},
        }


def _write_cache(path: Path, data: dict) -> None:
    lock = _lock_for(path)
    with lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )


def _lock_for(path: Path) -> threading.Lock:
    resolved = path.resolve()
    if resolved not in _LOCKS:
        _LOCKS[resolved] = threading.Lock()
    return _LOCKS[resolved]


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_json(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _sha256_text(text)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _summarize_input(cache_input: dict) -> str:
    preferred = (
        "raw_query",
        "domain_name",
        "project_id",
        "candidate_id",
        "candidate_ids",
        "mode",
    )
    parts = []
    for key in preferred:
        if key in cache_input:
            value = cache_input[key]
            if isinstance(value, list):
                value = ",".join(str(v) for v in value[:8])
            parts.append(f"{key}={value}")
    if parts:
        return " | ".join(parts)[:500]
    return json.dumps(cache_input, ensure_ascii=False, sort_keys=True)[:500]
