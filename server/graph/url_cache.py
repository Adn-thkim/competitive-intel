"""
server/graph/url_cache.py
--------------------------
URL 탐색·검증 단계의 단기 캐시 유틸리티.

두 가지 캐시를 제공한다.

  ① Brave Search 결과 캐시 (E-1)
       키:    (query) 또는 (brand, product_name)
       TTL:   BRAVE_RESULT_CACHE_TTL_HOURS (기본 24시간)
       목적:  동일 쿼리를 재실행 시 Brave API 호출과 페이지 메타 수집을 건너뛴다.

  ② HTTP 검증 결과 캐시 (E-2)
       키:    URL
       TTL:   HTTP_VALIDATION_CACHE_TTL_MINUTES (기본 60분)
       목적:  동일 URL이 official_source_resolver → url_retry 자동 재탐색 → Phase 1까지
              세 번 검증되는 중복을 제거한다.

설계 원칙
---------
- 인메모리 dict + threading.Lock. 서버 재시작 시 초기화(영속성 불필요).
- 캐시 미스 시 caller가 결과를 직접 set() 호출해 채운다.
- TTL 만료 항목은 lazy하게 get() 시점에 제거된다(별도 GC 스레드 없음).
"""

from __future__ import annotations

import threading
import time
from typing import Any


# ── Brave 결과 캐시 ─────────────────────────────────────────────────────────────

class _TTLCache:
    """
    범용 TTL 캐시. set 시 expires_at을 기록하고 get 시 만료 여부를 검사한다.
    """

    def __init__(self, ttl_seconds: int):
        self._ttl_seconds = ttl_seconds
        self._lock        = threading.Lock()
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        now = time.time()
        with self._lock:
            entry = self._store.get(key)
            if not entry:
                return None
            expires_at, value = entry
            if expires_at < now:
                self._store.pop(key, None)
                return None
            return value

    def set(self, key: str, value: Any) -> None:
        expires_at = time.time() + self._ttl_seconds
        with self._lock:
            self._store[key] = (expires_at, value)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def stats(self) -> dict:
        with self._lock:
            return {
                "ttl_seconds": self._ttl_seconds,
                "size":        len(self._store),
            }


# ── 모듈 레벨 싱글톤 ────────────────────────────────────────────────────────────
# config 임포트는 함수 내부에서 lazy하게 수행해 순환 의존을 회피한다.

_brave_cache: _TTLCache | None = None
_http_cache:  _TTLCache | None = None
_init_lock = threading.Lock()


def _get_brave_cache() -> _TTLCache:
    global _brave_cache
    if _brave_cache is None:
        with _init_lock:
            if _brave_cache is None:
                from server.config import BRAVE_RESULT_CACHE_TTL_HOURS
                _brave_cache = _TTLCache(ttl_seconds=BRAVE_RESULT_CACHE_TTL_HOURS * 3600)
    return _brave_cache


def _get_http_cache() -> _TTLCache:
    global _http_cache
    if _http_cache is None:
        with _init_lock:
            if _http_cache is None:
                from server.config import HTTP_VALIDATION_CACHE_TTL_MINUTES
                _http_cache = _TTLCache(ttl_seconds=HTTP_VALIDATION_CACHE_TTL_MINUTES * 60)
    return _http_cache


# ── 공개 API: Brave 결과 캐시 ───────────────────────────────────────────────────

def get_brave_results(query: str) -> list[dict] | None:
    """
    Brave 검색 결과 캐시를 조회한다.
    Returns
    -------
    list[dict] | None
        캐시 히트 시 raw 결과(딕셔너리 리스트), 미스 시 None.
    """
    return _get_brave_cache().get(_brave_key(query))


def set_brave_results(query: str, results: list[dict]) -> None:
    """Brave 검색 결과를 캐시에 저장한다."""
    _get_brave_cache().set(_brave_key(query), results)


def _brave_key(query: str) -> str:
    return f"brave::{query.strip().lower()}"


# ── 공개 API: HTTP 검증 결과 캐시 ───────────────────────────────────────────────

def get_http_validation(url: str) -> tuple[int | None, str | None] | None:
    """
    URL의 HTTP 검증 결과 캐시를 조회한다.
    Returns
    -------
    (status_code, final_url) | None
        캐시 히트 시 검증 결과, 미스 시 None.
        status_code=None은 검증 실패(타임아웃·연결 오류)를 의미하며 이 또한 캐시된다.
    """
    return _get_http_cache().get(_http_key(url))


def set_http_validation(url: str, status: int | None, final_url: str | None) -> None:
    """URL의 HTTP 검증 결과를 캐시에 저장한다."""
    _get_http_cache().set(_http_key(url), (status, final_url))


def _http_key(url: str) -> str:
    return f"http::{url.strip()}"


# ── 디버깅·관리 API ─────────────────────────────────────────────────────────────

def get_cache_stats() -> dict:
    """캐시 현황(크기·TTL)을 반환한다."""
    return {
        "brave": _get_brave_cache().stats(),
        "http":  _get_http_cache().stats(),
    }


def clear_all() -> None:
    """양쪽 캐시를 모두 비운다. 테스트·디버깅용."""
    _get_brave_cache().clear()
    _get_http_cache().clear()
