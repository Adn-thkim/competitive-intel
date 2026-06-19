"""
server/cache_ttl.py — 캐시 TTL 일괄 로더 (2026-06-19 신설)
--------------------------------------------------------
`server/cache_ttls.yaml` 의 defaults 를 1회 로드해 캐시 TTL을 단일 출처로 제공한다.
config.py·각 노드는 하드코딩 대신 get_ttl_hours("<key>", fallback) 로 읽는다.

우선순위 (config/노드 호출부에서 ENV 를 감싸는 기존 패턴 유지):
  환경변수(노드별 기존 ENV) > 본 모듈(yaml defaults) > 코드 fallback

yaml 로드 실패(파일 부재·파싱 오류) 시 빈 dict 로 폴백해 fallback 값이 적용되므로,
이 파일이 없어도 시스템은 정상 동작한다(스캐폴드의 안전한 무중단 특성).
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_TTL_FILE = Path(__file__).parent / "cache_ttls.yaml"
_defaults: dict | None = None


def _load() -> dict:
    """yaml defaults 를 1회 로드(메모이즈). 실패 시 빈 dict."""
    global _defaults
    if _defaults is None:
        try:
            data = yaml.safe_load(_TTL_FILE.read_text(encoding="utf-8")) or {}
            _defaults = data.get("defaults", {}) or {}
        except (OSError, yaml.YAMLError) as exc:  # noqa: BLE001 — 폴백 무중단
            logger.warning("cache_ttls.yaml 로드 실패 — fallback 사용: %s", exc)
            _defaults = {}
    return _defaults


def get_ttl_hours(key: str, fallback: int) -> int:
    """yaml defaults[key] (시간) 또는 fallback."""
    val = _load().get(key)
    return int(val) if val is not None else int(fallback)


def get_ttl_minutes(key: str, fallback: int) -> int:
    """yaml defaults[key] (분) 또는 fallback."""
    val = _load().get(key)
    return int(val) if val is not None else int(fallback)
