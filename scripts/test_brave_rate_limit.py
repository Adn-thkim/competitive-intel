"""
scripts/test_brave_rate_limit.py (v0.13.5)
-------------------------------------------
_brave_search 의 전역 rate limiter + 429 Retry-After 재시도 단위 테스트.

배경 (2026-06-07 회귀):
- v0.13.4 캐시 전면 무효화 + 보조 쿼리 도입으로 실제 Brave 호출 ~46건이
  3-worker burst 로 발사 → free tier 1 req/s 제한에 걸려 전 호출 silent 실패
  (logger.debug) → 빈 결과가 owned_channels 7일 캐시에 박제 → 전 채널 "미발견".
- 증거: owned_channels 캐시 23 entry 가 15초(06:50:13~28) 안에 생성(LLM 호출 시간
  물리적 불가) + Brave 캐시에 당일 entry 0건.

실행: pytest scripts/test_brave_rate_limit.py -q
"""

import time

import pytest

import server.graph.nodes.feature_url_mapper_node as fum
from server.graph.nodes.feature_url_mapper_node import _brave_search, _brave_throttle


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """API key 주입 + 캐시 미스 강제 + throttle 타임스탬프 초기화."""
    monkeypatch.setattr(fum, "BRAVE_SEARCH_API_KEY", "test-key")
    monkeypatch.setattr(fum, "load_agent_output", lambda **kw: None)
    monkeypatch.setattr(fum, "store_agent_output", lambda **kw: None)
    fum._brave_last_call_ts[0] = 0.0
    yield


class _Resp:
    def __init__(self, status_code=200, results=None, retry_after=None):
        self.status_code = status_code
        self._results = results or []
        self.headers = {"Retry-After": str(retry_after)} if retry_after else {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return {"web": {"results": self._results}}


def test_throttle_enforces_min_interval(monkeypatch):
    """연속 호출 시 최소 간격(1.05s)만큼 sleep 이 강제된다."""
    sleeps: list[float] = []
    monkeypatch.setattr(fum.time, "sleep", lambda s: sleeps.append(s))
    _brave_throttle()           # 첫 호출 — sleep 없음
    _brave_throttle()           # 즉시 재호출 — ~1.05s sleep
    assert len(sleeps) == 1
    assert 0.9 < sleeps[0] <= fum._BRAVE_MIN_INTERVAL_S


def test_429_retries_with_retry_after(monkeypatch):
    """429 응답 시 Retry-After 만큼 대기 후 재시도, 성공하면 결과 반환."""
    calls = {"n": 0}
    sleeps: list[float] = []

    def fake_get(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return _Resp(status_code=429, retry_after=2)
        return _Resp(results=[{"url": "https://x.com/toss__official"}])

    monkeypatch.setattr(fum.req_lib, "get", fake_get)
    monkeypatch.setattr(fum.time, "sleep", lambda s: sleeps.append(s))
    out = _brave_search("site:x.com 토스", count=5)
    assert calls["n"] == 2
    assert out == [{"url": "https://x.com/toss__official"}]
    assert 2.0 in sleeps          # Retry-After 존중


def test_429_exhausted_returns_empty(monkeypatch):
    """재시도 한도(2회) 소진 시 빈 리스트 — 호출 총 3회."""
    calls = {"n": 0}
    monkeypatch.setattr(fum.req_lib, "get",
                        lambda *a, **kw: calls.update(n=calls["n"] + 1) or _Resp(429, retry_after=1))
    monkeypatch.setattr(fum.time, "sleep", lambda s: None)
    assert _brave_search("q", count=5) == []
    assert calls["n"] == fum._BRAVE_429_MAX_RETRIES + 1


def test_network_error_returns_empty(monkeypatch):
    def fake_get(*a, **kw):
        raise OSError("connection refused")
    monkeypatch.setattr(fum.req_lib, "get", fake_get)
    monkeypatch.setattr(fum.time, "sleep", lambda s: None)
    assert _brave_search("q", count=5) == []
