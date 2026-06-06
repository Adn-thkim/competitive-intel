"""
ClaudeApiAnalyzer (v0.10.21 신설)
----------------------------------
Anthropic API 직접 호출 어댑터. `temperature=0` 으로 결정론적 출력 보장.

설계 의도
---------
`ClaudeCodeCliAnalyzer` 는 Claude Pro/Max 구독을 사용하나 `--temperature` 플래그를
공식 지원하지 않아 동일 입력에 미세하게 다른 출력이 나올 수 있습니다 (관련 이슈
github.com/anthropics/claude-code/issues/6096). 결정론성이 필요한 단계 — 예: v0.10.21
의 url_discovery_owned_channels_node 의 LLM 검증, ProductIdResolver 의 slug 생성 —
에서는 본 클래스를 사용합니다.

권장 사용 시나리오
-------------------
- url_discovery_owned_channels_node 의 LLM 검증 (D14·D16·D17 결정 항목 처리)
- ProductIdResolver 의 comp_* slug 생성 (결정론 필수)
- 향후 InsightReportAgent 의 최종 리포트 생성 (권장)

ClaudeCodeCliAnalyzer 와의 호환 인터페이스
-------------------------------------------
동일한 `call_with_schema(prompt, output_schema, max_retries)` API 를 제공하여 호출
지점을 변경 없이 교체 가능합니다.

비용 관리
---------
- API 호출은 토큰 단위 과금 (Claude Pro 구독 미사용)
- max_tokens 기본 4096 — 본 에이전트의 verified_handles 최대 5건 + rationale 등 수용
- 호출 실패 시 max_retries 회 재시도
"""
import json
import logging
import os
import time

import anthropic
import jsonschema

from server.config import ANTHROPIC_API_KEY, API_MODEL

logger = logging.getLogger(__name__)

# rate limit(429) 백오프 정책
_RATE_LIMIT_MAX_WAITS    = 6     # 누적 대기 횟수 상한 (초과 시 포기)
_RATE_LIMIT_MAX_SLEEP    = 70    # 단일 대기 상한(초) — ITPM 창은 60초이므로 약간 여유
_RATE_LIMIT_BASE_BACKOFF = 5     # retry-after 헤더 부재 시 지수 백오프 기준(초)


def _retry_after_seconds(exc: Exception, wait_index: int) -> float:
    """429 응답의 retry-after 헤더(초)를 우선 사용, 없으면 지수 백오프.

    wait_index: 0부터 시작하는 누적 대기 횟수.
    """
    try:
        headers = getattr(getattr(exc, "response", None), "headers", None) or {}
        raw = headers.get("retry-after")
        if raw is not None:
            return min(float(raw), _RATE_LIMIT_MAX_SLEEP)
    except (TypeError, ValueError):
        pass
    return min(_RATE_LIMIT_BASE_BACKOFF * (2 ** wait_index), _RATE_LIMIT_MAX_SLEEP)


class ClaudeApiAnalyzer:
    """Anthropic API 직접 호출 어댑터 (temperature=0 결정론적).

    Parameters
    ----------
    model : str
        Anthropic model 식별자 (예: 'claude-sonnet-4-6'). default = server.config.API_MODEL
    timeout : int
        API 호출 timeout (초). default 120.
    system_prompt : str | None
        시스템 프롬프트. 제공 시 사용자 프롬프트 앞에 주입.
    max_tokens : int
        응답 최대 토큰 수. default 4096.
    """

    def __init__(
        self,
        model: str | None     = None,
        timeout: int          = 120,
        system_prompt: str | None = None,
        max_tokens: int       = 4096,
    ) -> None:
        if not ANTHROPIC_API_KEY:
            raise RuntimeError(
                "ANTHROPIC_API_KEY 가 설정되지 않아 ClaudeApiAnalyzer 를 사용할 수 없습니다. "
                ".env 또는 시스템 환경변수에 ANTHROPIC_API_KEY 를 설정하십시오."
            )
        self.model         = model or API_MODEL
        self.timeout       = timeout
        self.system_prompt = system_prompt
        self.max_tokens    = max_tokens
        self._client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, timeout=timeout)

    # ── public API ────────────────────────────────────────────────────────────
    def call_with_schema(
        self,
        prompt: str,
        output_schema: dict,
        max_retries: int = 3,
    ) -> dict:
        """프롬프트를 실행하고 output_schema 를 만족하는 dict 를 반환한다.

        ClaudeCodeCliAnalyzer 와 동일한 인터페이스. native JSON 모드 + temperature=0
        + jsonschema validate + 실패 시 재시도.

        Parameters
        ----------
        prompt : str
            LLM 에 전달할 사용자 프롬프트.
        output_schema : dict
            LLM 출력이 만족해야 하는 JSON Schema.
        max_retries : int
            schema 검증 실패 시 최대 재시도 횟수.

        Returns
        -------
        dict
            output_schema 를 만족하는 파싱된 응답.

        Raises
        ------
        RuntimeError
            API 호출 오류 또는 max_retries 초과 시.
        """
        last_error: Exception | None = None

        # schema/일반 오류용 재시도 예산(attempt)과 429 대기(rate_limit_waits)는 분리한다.
        # rate limit 은 일시적 용량 문제이므로, retry-after 만큼 대기 후 같은 시도를
        # 재발사하며 schema 재시도 예산을 소모하지 않는다.
        attempt = 1
        rate_limit_waits = 0
        while attempt <= max_retries:
            try:
                # [v0.12.3 수정] 재시도에도 schema 재주입 — API 호출은 무상태(매 호출
                # 독립 메시지)라 "앞서 제시된 schema" 가 재시도 컨텍스트에 존재하지
                # 않는다. schema + error feedback 을 함께 전달한다.
                if attempt == 1:
                    full_prompt = self._build_schema_prompt(prompt, output_schema)
                else:
                    full_prompt = (
                        self._build_schema_prompt(prompt, output_schema)
                        + f"\n\n[이전 시도 {attempt - 1}회 오류: {str(last_error)[:300]}]\n"
                        "위 오류를 수정해, 위 JSON Schema 를 정확히 만족하는 JSON 만 "
                        "다시 반환하라."
                    )

                raw_output = self._invoke_api(full_prompt)
                parsed     = self._extract_json(raw_output)
                jsonschema.validate(parsed, output_schema)
                if attempt > 1 or rate_limit_waits > 0:
                    logger.info("ClaudeApiAnalyzer: 성공 (schema 시도 %d, 429 대기 %d회)",
                                attempt, rate_limit_waits)
                return parsed

            except anthropic.RateLimitError as exc:
                # 429 — retry-after 만큼 대기 후 동일 시도 재발사 (attempt 미소모)
                last_error = exc
                if rate_limit_waits >= _RATE_LIMIT_MAX_WAITS:
                    logger.error(
                        "ClaudeApiAnalyzer: rate limit 대기 %d회 초과 — 포기",
                        rate_limit_waits)
                    break
                sleep_s = _retry_after_seconds(exc, rate_limit_waits)
                rate_limit_waits += 1
                logger.warning(
                    "ClaudeApiAnalyzer: rate limit(429) — %.1f초 대기 후 재시도 "
                    "(대기 %d/%d)", sleep_s, rate_limit_waits, _RATE_LIMIT_MAX_WAITS)
                time.sleep(sleep_s)
                continue   # attempt 유지

            except (json.JSONDecodeError, jsonschema.ValidationError) as exc:
                last_error = exc
                logger.warning(
                    "ClaudeApiAnalyzer: schema 검증 실패 (시도 %d/%d) — %s",
                    attempt, max_retries, str(exc)[:200],
                )
                attempt += 1
            except anthropic.APIError as exc:
                last_error = exc
                logger.error(
                    "ClaudeApiAnalyzer: API 호출 오류 (시도 %d/%d) — %s",
                    attempt, max_retries, str(exc)[:200],
                )
                attempt += 1

        logger.error(
            "ClaudeApiAnalyzer: 재시도 모두 실패 — %s", str(last_error)[:300],
        )
        raise RuntimeError(
            f"Anthropic API 재시도 후 실패 (schema 시도 {attempt - 1}, "
            f"429 대기 {rate_limit_waits}회): {last_error}"
        )

    # ── 내부 헬퍼 ─────────────────────────────────────────────────────────────
    def _invoke_api(self, prompt: str) -> str:
        """anthropic.messages.create 호출. temperature=0 결정론적."""
        kwargs = {
            "model":       self.model,
            "max_tokens":  self.max_tokens,
            "temperature": 0,
            "messages":    [{"role": "user", "content": prompt}],
        }
        if self.system_prompt:
            kwargs["system"] = self.system_prompt

        response = self._client.messages.create(**kwargs)
        # response.content 는 ContentBlock 리스트. text 블록만 concat.
        text_parts = [
            block.text for block in response.content
            if hasattr(block, "text") and isinstance(block.text, str)
        ]
        return "".join(text_parts).strip()

    @staticmethod
    def _build_schema_prompt(prompt: str, output_schema: dict) -> str:
        """LLM 입력에 schema 지시를 주입 (CLI 와 동일 패턴)."""
        schema_str = json.dumps(output_schema, ensure_ascii=False, indent=2)
        return (
            f"{prompt}\n\n"
            f"위 요청에 대해 다음 JSON Schema 를 정확히 만족하는 JSON 만 반환하라. "
            f"JSON 외 어떤 문자(설명·마크다운·코드 블록 표시)도 포함하지 말 것.\n\n"
            f"```json\n{schema_str}\n```"
        )

    @staticmethod
    def _extract_json(raw_output: str) -> dict:
        """LLM 응답에서 JSON dict 추출.

        anthropic native JSON 모드는 응답을 순수 JSON 으로 반환하나, 안전을 위해
        ```json ... ``` 코드 블록도 처리.
        """
        s = raw_output.strip()
        # 코드 블록 제거
        if s.startswith("```"):
            lines = s.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            s = "\n".join(lines).strip()
        return json.loads(s)
