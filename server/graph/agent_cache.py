"""
server/graph/agent_cache.py
---------------------------
LLM agent 출력 캐시 유틸리티.

각 agent는 동일한 의미 입력과 동일한 prompt/schema/model 지문에 대해
Claude CLI를 다시 호출하지 않고 로컬 캐시를 재사용한다.

저장 형식 — 키별 샤딩 (cache_storage_sharding_design.md, CONFIRMED 2026-06-19)
---------------------------------------------------------------------------
data/cache/agent_outputs/{agent_id}/{cache_key}.json   # 엔트리 1개 = 파일 1개

각 파일은 엔트리 1건의 dict:
  { "cache_key", "input_summary", "created_at", "updated_at",
    "last_hit_at", "hit_count", "input", "context", "output" }

설계 효과(단일 파일 구조 대비):
- 조회/저장/적중이 해당 엔트리 작은 파일 1개만 읽고 쓴다(O(엔트리), 전체 재기록 없음).
- 서로 다른 키 = 서로 다른 파일 → 병렬 워커 동시 쓰기 충돌 소멸(별도 락 리팩터 불필요).
- 같은 키 동시 쓰기는 per-file 락 + 원자적 교체(temp+os.replace)로 무손상 보장.
- 죽은(구버전·만료) 엔트리는 별개 파일이라 다른 저장에 비용을 더하지 않는다.

외부 인터페이스(load_agent_output / store_agent_output / make_cache_key /
make_cache_context)는 단일 파일 시절과 동일 — 호출부 변경 없음.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jsonschema

from server.config import AGENT_OUTPUT_CACHE_DIR


_SCHEMA_VERSION = 1
_LOCKS: dict[Path, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


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
    agent 출력 캐시(엔트리 파일)를 조회한다.

    output_schema 주어지면 캐시된 output 을 검증하고 실패 시 미스 처리.
    ttl_hours 주어지면 entry 의 updated_at 기준 TTL 검사(초과 시 미스).
    반환값은 호출자가 안전하게 수정하도록 deep copy.
    """
    cache_key = make_cache_key(agent_id, cache_input, context)
    path = _entry_path(agent_id, cache_key)

    with _lock_for(path):
        entry = _read_entry(path)
        if entry is None:
            return None

        # TTL 검사 (옵션). 만료 시 미스.
        if ttl_hours is not None:
            ts = entry.get("updated_at") or entry.get("created_at")
            if ts:
                try:
                    when = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    age_h = (datetime.now(timezone.utc) - when).total_seconds() / 3600
                    if age_h > ttl_hours:
                        if logger:
                            logger.info("%s: cache TTL expired (age=%.1fh > %sh, key=%s)",
                                        agent_id, age_h, ttl_hours, cache_key[:12])
                        return None
                except (ValueError, TypeError):
                    return None  # timestamp 파싱 실패 → 안전하게 미스

        output = copy.deepcopy(entry.get("output"))
        if output_schema is not None:
            try:
                jsonschema.validate(output, output_schema)
            except jsonschema.ValidationError as exc:
                if logger:
                    logger.warning("%s cache invalid — schema validation failed: %s",
                                   agent_id, str(exc)[:200])
                return None

        # 적중 통계 갱신 (CS-D4 — 작은 엔트리 파일 1개 쓰기, 저렴). TTL 은 store 기준 유지:
        # updated_at 은 갱신하지 않는다(적중이 TTL 을 연장하지 않음).
        entry["hit_count"] = int(entry.get("hit_count", 0)) + 1
        entry["last_hit_at"] = _now_iso()
        _write_entry(path, entry)

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
    """agent 출력을 엔트리 파일로 저장하고 cache key 반환."""
    cache_key = make_cache_key(agent_id, cache_input, context)
    path = _entry_path(agent_id, cache_key)
    now = _now_iso()

    with _lock_for(path):
        existing = _read_entry(path) or {}
        entry = {
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
        _write_entry(path, entry)

    if logger:
        logger.info("%s: output cache stored (key=%s)", agent_id, cache_key[:12])
    return cache_key


def make_cache_key(agent_id: str, cache_input: dict, context: dict) -> str:
    """agent_id + cache_input + context를 안정적으로 해시한다."""
    payload = {"agent_id": agent_id, "input": cache_input, "context": context}
    return _sha256_json(payload)


# ── 저장계층 (엔트리당 파일) ────────────────────────────────────────────────

def _safe_name(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in name)


def _entry_path(agent_id: str, cache_key: str) -> Path:
    # cache_key 는 sha256 hex(64자) — 파일명으로 안전. agent_id 는 디렉터리.
    return AGENT_OUTPUT_CACHE_DIR / _safe_name(agent_id) / f"{cache_key}.json"


def _read_entry(path: Path) -> dict | None:
    """엔트리 파일 1개 읽기. 없거나 손상 시 None(미스)."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None  # 손상 → 미스(크래시 대신)


def _write_entry(path: Path, entry: dict) -> None:
    """엔트리 파일 1개를 원자적으로 쓴다(temp + os.replace). 호출자가 락 보유."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.urandom(4).hex()}")
    try:
        tmp.write_text(json.dumps(entry, ensure_ascii=False, indent=2, sort_keys=True),
                       encoding="utf-8")
        os.replace(tmp, path)   # 같은 디렉터리 → 원자적 교체(부분 쓰기 손상 방지)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _lock_for(path: Path) -> threading.Lock:
    """경로별 락. setdefault 로 check-then-set 경쟁 차단(CS-D3)."""
    resolved = path.resolve()
    lock = _LOCKS.get(resolved)
    if lock is None:
        with _LOCKS_GUARD:
            lock = _LOCKS.setdefault(resolved, threading.Lock())
    return lock


# ── 해시·유틸 ────────────────────────────────────────────────────────────────

def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_json(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _sha256_text(text)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _summarize_input(cache_input: dict) -> str:
    preferred = (
        "raw_query", "domain_name", "project_id",
        "candidate_id", "candidate_ids", "mode",
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
