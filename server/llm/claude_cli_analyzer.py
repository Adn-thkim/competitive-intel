"""
ClaudeCodeCliAnalyzer
----------------------
Claude Code CLI(subprocess)를 호출하는 LLM 어댑터.
API 크레딧 대신 Claude Pro/Max 구독 토큰을 소비한다.
개발·실험 단계에서 사용하며, 프로덕션 배포 시 ClaudeApiAnalyzer로 교체한다.

⚠️  temperature 제한 안내
--------------------------------------
Claude Code CLI는 --temperature 플래그를 공식 지원하지 않는다.
(관련 이슈: https://github.com/anthropics/claude-code/issues/6096)

이로 인해 동일한 입력에 대해 출력이 미세하게 달라질 수 있다.

완화 전략:
  1. system_prompt에 일관성 규칙을 명시해 LLM의 자연어 수준 결정론성을 높인다.
     (QueryIntakeAgent system_prompt의 "출력 일관성 요구사항" 섹션 참고)
  2. product_id 슬러그 생성처럼 완전한 결정론성이 필요한 단계에서는
     이 클래스 대신 ClaudeApiAnalyzer(temperature=0)를 사용한다.

권장 사용 시나리오:
  - 개발 단계 모든 agent: CLI 사용 가능
  - product_id 슬러그 생성(ProductIdResolver): API 필수
  - 최종 리포트 생성(InsightReportAgent): API 권장
"""

import json
import logging
import os
import subprocess
import jsonschema
from pathlib import Path

logger = logging.getLogger(__name__)


class ClaudeCodeCliAnalyzer:
    """
    Claude Code CLI를 subprocess로 호출하는 LLM 어댑터.

    Parameters
    ----------
    model : str
        사용할 Claude 모델. CLI에서 --model 플래그로 전달된다.
    timeout : int
        subprocess 타임아웃(초). 기본값 120초.
    system_prompt : str | None
        시스템 프롬프트. 제공 시 사용자 프롬프트 앞에 주입된다.
        CLI는 --system-prompt 플래그를 지원한다.
    """

    def __init__(
        self,
        model: str = "claude-sonnet-4-6",
        timeout: int = 120,
        system_prompt: str | None = None,
    ):
        self.model = model
        self.timeout = timeout
        self.system_prompt = system_prompt

    def call_with_schema(
        self,
        prompt: str,
        output_schema: dict,
        max_retries: int = 3,
    ) -> dict:
        """
        프롬프트를 실행하고 output_schema를 만족하는 dict를 반환한다.

        CLI는 native schema 강제 기능이 없으므로:
          - 프롬프트에 schema 지시를 주입해 LLM이 schema를 따르도록 유도
          - jsonschema.validate()로 출력 검증
          - 실패 시 오류 피드백과 함께 최대 max_retries 회 재시도

        Parameters
        ----------
        prompt : str
            LLM에 전달할 사용자 프롬프트.
        output_schema : dict
            LLM 출력이 만족해야 하는 JSON Schema.
        max_retries : int
            schema 검증 실패 시 최대 재시도 횟수.

        Returns
        -------
        dict
            output_schema를 만족하는 파싱된 응답.

        Raises
        ------
        RuntimeError
            CLI 실행 오류 또는 max_retries 초과 시.
        """
        last_error = None

        for attempt in range(1, max_retries + 1):
            try:
                # ── [SLIM 변경 #2] 재시도 시 schema 재주입 방지 ──────────────
                # 첫 시도에만 schema를 주입하고, 재시도는 error feedback만 전달한다.
                # (schema는 이미 첫 시도에서 LLM이 수신했으므로 재전송 불필요)
                # 효과: 재시도 1회당 ~2,700 tokens 절감
                #
                # ✏️  FULL로 롤백하려면 아래 if/else 블록 전체를 다음 한 줄로 교체:
                #     full_prompt = self._build_schema_prompt(prompt, output_schema)
                #     그리고 아래 else 블록과 이 주석 블록을 삭제한다.
                # ─────────────────────────────────────────────────────────────
                if attempt == 1:
                    # 첫 시도: schema 전체 주입 (슬림화 적용)
                    full_prompt = self._build_schema_prompt(prompt, output_schema)
                else:
                    # 재시도: schema 재주입 없이 error feedback만 추가
                    full_prompt = (
                        prompt
                        + f"\n\n[이전 시도 {attempt - 1}회 오류: {str(last_error)[:300]}]\n"
                        "위 오류를 수정해 올바른 JSON을 다시 반환하라. "
                        "앞서 제시된 JSON Schema를 그대로 준수할 것."
                    )

                raw_output = self._invoke_cli(full_prompt)
                parsed     = self._extract_json(raw_output)
                jsonschema.validate(parsed, output_schema)
                if attempt > 1:
                    logger.info("call_with_schema: %d회 시도에 성공", attempt)
                return parsed

            except (json.JSONDecodeError, jsonschema.ValidationError) as e:
                last_error = e
                logger.warning(
                    "call_with_schema: schema 검증 실패 (시도 %d/%d) — %s",
                    attempt, max_retries, str(e)[:200],
                )

        logger.error(
            "call_with_schema: %d회 재시도 모두 실패 — %s",
            max_retries, str(last_error)[:300],
        )
        raise RuntimeError(
            f"CLI {max_retries}회 재시도 후 schema 검증 실패: {last_error}"
        )

    # ── 내부 메서드 ───────────────────────────────────────────────────────

    # ── [SLIM 변경 #1] 스키마 슬림화 메서드 ───────────────────────────────
    # description, examples 등 메타 필드를 제거해 주입 토큰을 절감한다.
    # 효과: query_intake 67% / competitor_discovery 26% 토큰 감소
    #
    # ✏️  FULL로 롤백하려면:
    #     1. 아래 _strip_schema_verbosity() 메서드 전체를 삭제한다.
    #     2. _build_schema_prompt() 안의 slim_schema 관련 두 줄을 삭제하고
    #        schema를 직접 사용하도록 수정한다. (아래 #3 주석 참고)
    # ──────────────────────────────────────────────────────────────────────
    @staticmethod
    def _strip_schema_verbosity(obj: object) -> object:
        """
        JSON Schema에서 LLM 출력 생성에 불필요한 메타 필드를 재귀적으로 제거한다.

        제거 대상: description, examples, $schema, $id, title, $comment, default
        유지 대상: type, properties, required, additionalProperties,
                   enum, format, pattern, minimum, maximum,
                   minLength, maxLength, minItems, maxItems, items, oneOf, anyOf

        jsonschema.validate()는 description/examples 없이도 정상 동작하므로
        유효성 검사 정확도에는 영향을 주지 않는다.
        """
        _VERBOSE_KEYS = frozenset({
            "description", "examples", "$schema", "$id",
            "title", "$comment", "default",
        })
        if isinstance(obj, dict):
            return {
                k: ClaudeCodeCliAnalyzer._strip_schema_verbosity(v)
                for k, v in obj.items()
                if k not in _VERBOSE_KEYS
            }
        if isinstance(obj, list):
            return [ClaudeCodeCliAnalyzer._strip_schema_verbosity(i) for i in obj]
        return obj

    def _build_schema_prompt(self, prompt: str, schema: dict) -> str:
        """
        사용자 프롬프트에 schema 강제 지시를 추가한다.

        [SLIM 변경 #3] schema 주입 전 _strip_schema_verbosity()로 슬림화한다.
        ✏️  FULL로 롤백하려면 아래 두 줄을 삭제하고,
            json.dumps() 의 인자를 slim_schema → schema 로 되돌린다.
            또한 separators=(',', ':') 를 indent=2 로 바꾸면 원본 가독성 복원.
        """
        # ↓ SLIM: 슬림화 적용 — FULL 롤백 시 이 줄 삭제
        slim_schema = self._strip_schema_verbosity(schema)
        return (
            f"{prompt}\n\n"
            "---\n"
            "[출력 형식 지시]\n"
            "반드시 아래 JSON Schema를 완전히 만족하는 유효한 JSON만 반환하라.\n"
            "설명 텍스트, 마크다운 코드 블록(```), 주석을 절대 포함하지 마라.\n"
            "JSON 외의 어떤 문자도 출력하지 마라.\n\n"
            # ↓ SLIM: slim_schema + separators — FULL 롤백 시 → schema, indent=2
            f"Schema:\n{json.dumps(slim_schema, ensure_ascii=False, separators=(',', ':'))}"
        )

    def _invoke_cli(self, prompt: str) -> str:
        """
        Claude Code CLI를 subprocess로 실행하고 stdout을 반환한다.

        사용되는 플래그:
          --print (-p)          : 비대화형 단일 실행 모드
          --output-format json  : CLI 래퍼 출력을 JSON으로
          --model               : 모델 지정
          --system-prompt       : 시스템 프롬프트 주입 (지원 시)

        ⚠️  --temperature 는 CLI에서 지원되지 않는다.
            결정론성이 필요한 경우 ClaudeApiAnalyzer를 사용할 것.

        ⚠️  --no-env-file 플래그는 설치된 Claude CLI 버전에 따라
            지원되지 않을 수 있어 제거하였다.
            대신 env= 파라미터 필터링(1단계 격리)만으로 ANTHROPIC_API_KEY 격리를 유지한다.

        ⚠️  ANTHROPIC_API_KEY 격리 (env= 필터링)
            env= 파라미터로 ANTHROPIC_API_KEY를 subprocess 환경변수에서 제외.
            → CLI가 구독 대신 API Key로 인증하는 경로를 차단한다.
            FastAPI 프로세스 자체는 ANTHROPIC_API_KEY를 보유해야 하므로
            os.environ을 직접 수정하지 않고 자식 프로세스 전달분만 필터링한다.
            normalize_competitor_ids_node(slug.py)는 FastAPI 프로세스 내에서
            anthropic SDK를 직접 호출하므로 이 격리의 영향을 받지 않는다.
        """
        cmd = [
            "claude",
            "--print",
            "--output-format", "json",
            "--model", self.model,
        ]

        # 시스템 프롬프트가 있으면 추가
        # CLI가 --system-prompt 플래그를 지원하는 버전인지 실행 환경에서 확인 필요
        if self.system_prompt:
            cmd += ["--system-prompt", self.system_prompt]

        cmd.append(prompt)

        # ANTHROPIC_API_KEY를 제외한 환경변수만 CLI subprocess에 전달한다.
        # ∵ ANTHROPIC_API_KEY가 있으면 CLI가 구독 대신 API Key로 인증하기 때문.
        env_for_cli = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=env_for_cli,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"Claude CLI timeout ({self.timeout}s 초과). "
                "timeout 값을 늘리거나 프롬프트를 단순화하라."
            )
        except FileNotFoundError:
            raise RuntimeError(
                "claude CLI를 찾을 수 없습니다.\n"
                "설치 확인: npm install -g @anthropic-ai/claude-code\n"
                "경로 확인: which claude"
            )

        if result.returncode != 0:
            raise RuntimeError(
                f"Claude CLI 비정상 종료 (returncode={result.returncode}):\n"
                f"{result.stderr.strip()}"
            )

        return result.stdout

    def _extract_json(self, cli_stdout: str) -> dict:
        """
        CLI 래퍼 JSON에서 LLM 응답 텍스트를 꺼내 dict로 파싱한다.

        CLI 출력 구조:
          {"type": "result", "subtype": "success", "result": "<LLM 응답>", ...}

        LLM이 마크다운 코드 블록으로 감쌌을 경우 제거 후 파싱한다.
        """
        try:
            wrapper = json.loads(cli_stdout)
        except json.JSONDecodeError as e:
            raise json.JSONDecodeError(
                f"CLI stdout이 유효한 JSON이 아닙니다: {cli_stdout[:200]}",
                e.doc, e.pos,
            )

        if wrapper.get("type") != "result" or wrapper.get("subtype") != "success":
            raise RuntimeError(
                f"CLI 비정상 응답 구조: type={wrapper.get('type')}, "
                f"subtype={wrapper.get('subtype')}"
            )

        llm_text = wrapper["result"].strip()

        # 마크다운 코드 블록 제거
        if llm_text.startswith("```"):
            lines = llm_text.split("\n")
            # 첫 줄(```json 또는 ```) 과 마지막 줄(```) 제거
            inner = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
            llm_text = "\n".join(inner).strip()

        return json.loads(llm_text)
