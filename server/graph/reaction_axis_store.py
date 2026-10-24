"""server/graph/reaction_axis_store.py — 고정 super_aspect 축 저장/로드/버전 (통합계획 §2-3·§6-5).

축(super_aspect 라벨+정의+버전+생성일)을 **입력 도메인 해시** 키로 캐시한다. 히트 시 Phase A(도출)·
human_review 를 생략하고 배정(Phase B)만 수행하도록 한다(재실행 시 택소노미 비결정성 방지).

정본은 이 스토어. human_review(interrupt#5)로 수정된 축이 다음 실행의 정본이 된다.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_STORE_DIR = Path(__file__).resolve().parents[2] / "data" / "reaction_axis"


def axis_key(domain_slug: str, candidate_ids: list[str]) -> str:
    """도메인 + candidate 목록으로 축 캐시 키. (수집 URL 집합까지 넣을지는 §꼬리질문 확정 필요)"""
    raw = domain_slug + "|" + "|".join(sorted(candidate_ids or []))
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def load_axis(key: str) -> dict | None:
    p = _STORE_DIR / f"{key}.json"
    if not p.exists():
        return None
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.debug("axis load 실패(%s): %s", key, exc)
        return None


def store_axis(key: str, super_to_sub: dict, definitions: dict | None = None,
               version: int = 1) -> dict:
    _STORE_DIR.mkdir(parents=True, exist_ok=True)
    axis = {"key": key, "version": version,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "super_to_sub": super_to_sub, "definitions": definitions or {}}
    json.dump(axis, open(_STORE_DIR / f"{key}.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    return axis
