"""
server/graph/query_intake_overrides.py
--------------------------------------
human_review 에서 사용자가 정정한 query_intake draft 필드를 영속화한다.

설계 (B안 — context 비의존 오버라이드 스토어)
-------------------------------------------------
- LLM 출력 캐시(agent_cache)는 키에 system_prompt/schema/model 지문을 포함하므로,
  거기에 사용자 정정을 덮어쓰면 prompt/schema/model 변경 시 정정이 유실된다.
- 본 스토어는 raw_query 만으로 키를 잡아(컨텍스트 비의존) 그 문제를 피한다.
- 저장은 변경(diff) 필드만 — 사용자가 바꾸지 않은 필드는 향후 LLM 개선이 반영된다.

저장 형식
---------
data/overrides/query_intake/{sha256(raw_query)}.json
  { "raw_query": str, "updated_at": iso, "overrides": { <field>: <value>, ... } }
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

from server.config import QUERY_INTAKE_OVERRIDES_DIR

# draft_competitor_discovery_input 중 사용자가 human_review 폼에서 편집 가능한 필드.
# project_id 는 ProductIdResolver 가 own_product 에서 파생하므로 제외한다.
_DRAFT_FIELDS: tuple[str, ...] = (
    "domain_name",
    "own_product",
    "problem_statement",
    "target_user",
    "core_value_props",
    "known_keywords",
    "usage_context",
    "geography",
    "business_constraints",
)

_LOCK = threading.Lock()


def diff_draft(presented_draft: dict, edited_form: dict) -> dict[str, Any]:
    """제시된 draft 대비 사용자가 실제로 바꾼 필드만 추출한다.

    presented_draft : human_review 에 제시된 draft(= LLM 출력 + 기존 오버라이드 병합본).
    edited_form     : 사용자가 폼에서 수정해 resume 로 돌려준 값(draft 필드 최상위).
    """
    changed: dict[str, Any] = {}
    for field in _DRAFT_FIELDS:
        if field not in edited_form:
            continue
        if edited_form[field] != presented_draft.get(field):
            changed[field] = copy.deepcopy(edited_form[field])
    return changed


def load_overrides(raw_query: str) -> dict[str, Any]:
    """raw_query 에 대한 저장된 오버라이드(필드 dict)를 반환. 없으면 빈 dict."""
    path = _path(raw_query)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return {}  # 손상 → 빈 오버라이드(미적용)
    overrides = data.get("overrides")
    return copy.deepcopy(overrides) if isinstance(overrides, dict) else {}


def store_overrides(
    raw_query: str,
    presented_draft: dict,
    edited_form: dict,
    logger: logging.Logger | None = None,
) -> dict[str, Any]:
    """이번 세션 변경분(diff)을 기존 오버라이드에 누적 병합 후 저장. 병합된 dict 반환.

    변경이 없으면 파일을 건드리지 않는다.
    """
    session_diff = diff_draft(presented_draft, edited_form)
    if not session_diff:
        return load_overrides(raw_query)

    path = _path(raw_query)
    with _LOCK:
        existing = load_overrides(raw_query)
        merged = {**existing, **session_diff}
        entry = {
            "raw_query": raw_query,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "overrides": merged,
        }
        _write_atomic(path, entry)

    if logger:
        logger.info(
            "query_intake override 저장: raw_query=%r, 변경필드=%s",
            raw_query, sorted(session_diff.keys()),
        )
    return merged


def clear_overrides(
    raw_query: str,
    field: str | None = None,
    logger: logging.Logger | None = None,
) -> dict[str, Any]:
    """저장된 정정 오버라이드를 해제한다. 남은 오버라이드 dict 반환.

    field 미지정(None) → 해당 raw_query 의 오버라이드 전체 삭제(파일 제거).
    field 지정          → 그 필드만 제거. 결과가 비면 파일 제거.
    """
    path = _path(raw_query)
    with _LOCK:
        if field is None:
            _unlink(path)
            remaining: dict[str, Any] = {}
        else:
            remaining = load_overrides(raw_query)
            remaining.pop(field, None)
            if remaining:
                _write_atomic(path, {
                    "raw_query": raw_query,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "overrides": remaining,
                })
            else:
                _unlink(path)
    if logger:
        logger.info("query_intake override 해제: raw_query=%r, field=%s, 남음=%s",
                    raw_query, field or "(전체)", sorted(remaining.keys()))
    return remaining


def apply_overrides(draft: dict, overrides: dict) -> dict:
    """draft 에 오버라이드를 적용(override 우선)한 새 dict 반환."""
    if not overrides:
        return draft
    merged = copy.deepcopy(draft)
    for key, value in overrides.items():
        merged[key] = copy.deepcopy(value)
    return merged


# ── 내부 ──────────────────────────────────────────────────────────────────────

def _path(raw_query: str) -> Path:
    key = hashlib.sha256(raw_query.encode("utf-8")).hexdigest()
    return QUERY_INTAKE_OVERRIDES_DIR / f"{key}.json"


def _unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _write_atomic(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.urandom(4).hex()}")
    try:
        tmp.write_text(
            json.dumps(entry, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
