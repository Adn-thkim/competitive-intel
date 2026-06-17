"""
test_claude_api_rate_limit.py
------------------------------
ClaudeApiAnalyzer 의 429(rate limit) retry-after 백오프 회귀 테스트.

검증: 429 시 retry-after 만큼 대기 후 동일 시도를 재발사하며, schema 재시도 예산
(max_retries)을 소모하지 않는다. time.sleep 은 monkeypatch 로 즉시 통과시킨다.

실행: python -m pytest scripts/test_claude_api_rate_limit.py -q
"""

import os

import anthropic
import httpx
import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

import server.llm.claude_api_analyzer as mod
from server.llm.claude_api_analyzer import ClaudeApiAnalyzer, _retry_after_seconds

_SCHEMA = {"type": "object", "required": ["ok"],
           "properties": {"ok": {"type": "boolean"}}, "additionalProperties": False}


def _rate_limit_error(retry_after=None):
    headers = {} if retry_after is None else {"retry-after": str(retry_after)}
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(429, headers=headers, request=request)
    return anthropic.RateLimitError(message="429", response=response, body=None)


@pytest.fixture
def _no_sleep(monkeypatch):
    sleeps = []
    monkeypatch.setattr(mod.time, "sleep", lambda s: sleeps.append(s))
    return sleeps


def _analyzer(monkeypatch):
    # __init__ 의 anthropic.Anthropic 클라이언트 생성을 우회
    monkeypatch.setattr(mod.anthropic, "Anthropic", lambda **k: object())
    return ClaudeApiAnalyzer(model="claude-sonnet-4-6")


class TestRetryAfter:
    def test_respects_retry_after_header(self):
        assert _retry_after_seconds(_rate_limit_error(retry_after=12), 0) == 12.0

    def test_caps_long_retry_after(self):
        assert _retry_after_seconds(_rate_limit_error(retry_after=9999), 0) == 70

    def test_exponential_backoff_without_header(self):
        assert _retry_after_seconds(_rate_limit_error(), 0) == 5      # 5 * 2^0
        assert _retry_after_seconds(_rate_limit_error(), 2) == 20     # 5 * 2^2


class TestBackoffLoop:
    def test_429_then_success_does_not_consume_schema_budget(self, monkeypatch, _no_sleep):
        analyzer = _analyzer(monkeypatch)
        calls = {"n": 0}

        def _fake_invoke(prompt, output_schema):
            calls["n"] += 1
            if calls["n"] <= 2:          # 첫 2회는 429
                raise _rate_limit_error(retry_after=3)
            return {"ok": True}           # 3회차 성공 (tool_use 는 dict 반환)

        monkeypatch.setattr(analyzer, "_invoke_api_with_tool", _fake_invoke)
        result = analyzer.call_with_schema("p", _SCHEMA, max_retries=3)
        assert result == {"ok": True}
        assert calls["n"] == 3
        assert _no_sleep == [3, 3]        # retry-after 두 번 대기

    def test_gives_up_after_max_waits(self, monkeypatch, _no_sleep):
        analyzer = _analyzer(monkeypatch)

        def _always_429(prompt, output_schema):
            raise _rate_limit_error(retry_after=1)

        monkeypatch.setattr(analyzer, "_invoke_api_with_tool", _always_429)
        with pytest.raises(RuntimeError):
            analyzer.call_with_schema("p", _SCHEMA, max_retries=3)
        assert len(_no_sleep) == mod._RATE_LIMIT_MAX_WAITS   # 대기 상한까지만 시도
