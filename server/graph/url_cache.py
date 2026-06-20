"""
server/graph/url_cache.py
--------------------------
URL 탐색·검증 단계의 캐시 유틸리티.

두 가지 캐시를 제공한다 (Phase 3 — 인메모리 → 샤딩 파일 캐시 전환,
cache_storage_sharding_design.md CS-D6).

  ① Brave Search 결과 캐시 (E-1)
       agent_id: "official_source_brave"  · 키: query
       TTL:      BRAVE_RESULT_CACHE_TTL_HOURS (cache_ttls.yaml, 기본 30일)
       목적:     official_source_resolver 의 동일 쿼리 Brave 재호출 제거.

  ② HTTP 검증 결과 캐시 (E-2)
       agent_id: "http_url_validation"    · 키: url
       TTL:      HTTP_VALIDATION_CACHE_TTL_MINUTES (기본 30일 — 개발 완료 후 단축 예정)
       목적:     동일 URL 의 중복 HTTP 검증 제거. 검증 실패(status=None)도 캐시.

변경점 (구설계 대비)
--------------------
- 인메모리 dict(_TTLCache) → `agent_cache`(엔트리당 파일, 샤딩) 백엔드.
  → 서버 재시작·재실행에도 캐시 유지(영속). 서로 다른 키 = 다른 파일 → 동시성 안전.
- 공개 API(get/set 4종)는 그대로라 호출부(official_source_resolver) 무변경.
- agent_id 명명은 생산 맥락을 담는다(CS-D7) — Brave 파일 캐시 `url_discovery_brave`
  (feature_url_mapper 용)와 혼동 방지를 위해 `official_source_brave` 로 분리.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from server.graph.agent_cache import load_agent_output, store_agent_output

_BRAVE_AGENT = "official_source_brave"
_HTTP_AGENT = "http_url_validation"
_BRAVE_CTX = {"agent_id": _BRAVE_AGENT, "v": 1}
_HTTP_CTX = {"agent_id": _HTTP_AGENT, "v": 1}


def _brave_ttl_hours() -> float:
    from server.config import BRAVE_RESULT_CACHE_TTL_HOURS
    return float(BRAVE_RESULT_CACHE_TTL_HOURS)


def _http_ttl_hours() -> float:
    from server.config import HTTP_VALIDATION_CACHE_TTL_MINUTES
    return HTTP_VALIDATION_CACHE_TTL_MINUTES / 60.0


# ── 공개 API: Brave 결과 캐시 ───────────────────────────────────────────────────

def get_brave_results(query: str) -> list[dict] | None:
    """Brave 검색 결과 캐시 조회. 히트 시 결과 리스트, 미스 시 None."""
    out = load_agent_output(
        agent_id=_BRAVE_AGENT, cache_input={"query": _norm_query(query)},
        context=_BRAVE_CTX, ttl_hours=_brave_ttl_hours())
    return out.get("results") if isinstance(out, dict) else None


def set_brave_results(query: str, results: list[dict]) -> None:
    """Brave 검색 결과를 캐시에 저장한다."""
    store_agent_output(
        agent_id=_BRAVE_AGENT, cache_input={"query": _norm_query(query)},
        context=_BRAVE_CTX, output={"results": results})


def _norm_query(query: str) -> str:
    return query.strip().lower()


# ── 공개 API: HTTP 검증 결과 캐시 ───────────────────────────────────────────────

def get_http_validation(
    url: str, ua: str | None = None
) -> tuple[int | None, str | None] | None:
    """URL HTTP 검증 결과 조회. 히트 시 (status, final_url), 미스 시 None.

    status=None(검증 실패)도 캐시된 히트로 (None, final_url) 을 돌려준다(미스 아님).
    ua 를 키에 포함 — UA 변경 시 옛 UA로 캐시된 검증 결과(봇차단 403/타임아웃 위양성
    포함)가 자동 무효화되어 새 UA로 재검증된다.
    """
    out = load_agent_output(
        agent_id=_HTTP_AGENT, cache_input={"url": url.strip(), "ua": ua},
        context=_HTTP_CTX, ttl_hours=_http_ttl_hours())
    if not isinstance(out, dict):
        return None
    return (out.get("status"), out.get("final_url"))


def set_http_validation(
    url: str, status: int | None, final_url: str | None, ua: str | None = None
) -> None:
    """URL HTTP 검증 결과를 캐시에 저장한다(실패 status=None 포함). ua 를 키에 포함."""
    store_agent_output(
        agent_id=_HTTP_AGENT, cache_input={"url": url.strip(), "ua": ua},
        context=_HTTP_CTX, output={"status": status, "final_url": final_url})


# ── 디버깅·관리 API ─────────────────────────────────────────────────────────────

def _agent_dir(agent_id: str) -> Path:
    from server.config import AGENT_OUTPUT_CACHE_DIR
    return AGENT_OUTPUT_CACHE_DIR / agent_id


def get_cache_stats() -> dict:
    """캐시 현황(엔트리 파일 수)을 반환한다."""
    def _count(agent_id: str) -> int:
        d = _agent_dir(agent_id)
        return len(list(d.glob("*.json"))) if d.is_dir() else 0
    return {
        "brave": {"agent_id": _BRAVE_AGENT, "entries": _count(_BRAVE_AGENT)},
        "http":  {"agent_id": _HTTP_AGENT,  "entries": _count(_HTTP_AGENT)},
    }


def clear_all() -> None:
    """양쪽 캐시 디렉터리를 비운다. 테스트·디버깅용(best-effort)."""
    for agent_id in (_BRAVE_AGENT, _HTTP_AGENT):
        d = _agent_dir(agent_id)
        if d.is_dir():
            try:
                shutil.rmtree(d)
            except OSError:
                pass
