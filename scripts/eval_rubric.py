#!/usr/bin/env python3
"""
scripts/eval_rubric.py
======================
Rubric 적용 전/후 DomainTaxonomyAgent 출력 품질을 LLM-as-judge로 paired t-test 평가합니다.

pipeline_topology_redesign.md §6-0 검증 방법 + §11-1 Rubric 기반 품질 평가 명세:
- 트래블카드 도메인을 baseline으로, Rubric 도입 전/후 두 출력을 동일 입력으로 생성.
- Claude API를 judge로 사용하여 7개 리포트 작성 적합도를 1–5점으로 평가.
- 20회 반복 + bootstrap 95% CI + paired t-test로 유의미성 보고.

상태
----
**SCAFFOLD ONLY** — 본 파일은 평가 파이프라인의 구조만 정의합니다. 실제 LLM 호출·
통계 처리는 TODO 마커가 표시된 함수에 단계적으로 채워 넣습니다.

실행 단계 (예상)
----------------
1. baseline 생성: `system_prompt_kr.md` 직전 버전(`git show HEAD~1:...`)으로 호출
2. treatment 생성: 현재 버전(Rubric inline 인용)으로 호출
3. judge 호출: 두 출력을 동일 prompt로 LLM judge에 제출 → 7개 리포트별 1–5점
4. 통계: paired t-test + bootstrap 95% CI + 결과 보고서 작성

사용
----
  python scripts/eval_rubric.py --domain "토스 트래블카드" --iterations 20
  python scripts/eval_rubric.py --dry-run    # LLM 호출 없이 구조 검증만

의존성
------
- `anthropic` SDK (`pip install anthropic`)
- `scipy` (paired t-test, bootstrap CI)
- `server.llm.claude_cli_analyzer.ClaudeCodeCliAnalyzer`

산출물
------
`data/eval/rubric_v{X}.{Y}/report.md` — Rubric 도입 전/후 점수 분포, t-statistic,
                                         p-value, bootstrap CI, 권고 사항.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

ROOT = Path(__file__).resolve().parent.parent
EVAL_OUTPUT_DIR = ROOT / "data" / "eval"
REPORT_TYPES = [
    "comparison_matrix",
    "reaction_insight",
    "marketing_social",
    "battlecard",
    "positioning_map",
    "market_context_swot",
    "executive_summary",
]


@dataclass
class TaxonomyOutput:
    """DomainTaxonomyAgent 단일 호출 결과."""

    domain: str
    rubric_version: str          # "baseline" | "v0.1" 등
    raw_json: dict
    elapsed_ms: int = 0


@dataclass
class JudgeScore:
    """LLM judge가 평가한 점수 1건."""

    report_type: str             # REPORT_TYPES 중 하나
    score: int                   # 1–5
    rationale: str
    judge_response_raw: str = ""


@dataclass
class EvalRun:
    """단일 도메인·단일 iteration의 baseline vs treatment 평가 결과."""

    iteration: int
    domain: str
    baseline_output: TaxonomyOutput
    treatment_output: TaxonomyOutput
    baseline_scores: list[JudgeScore] = field(default_factory=list)
    treatment_scores: list[JudgeScore] = field(default_factory=list)


# ── Step 1: baseline vs treatment 출력 생성 ─────────────────────────────────

def generate_taxonomy(domain: str, prompt_version: Literal["baseline", "treatment"]) -> TaxonomyOutput:
    """DomainTaxonomyAgent를 호출하여 taxonomy를 생성합니다.

    TODO:
    - `prompt_version == "baseline"`: git에서 HEAD~1의 system_prompt_kr.md 로드
    - `prompt_version == "treatment"`: 현재 system_prompt_kr.md 사용 (Rubric inline)
    - `ClaudeCodeCliAnalyzer.call_with_schema(prompt, output_schema)` 호출
    - 결과를 TaxonomyOutput으로 반환
    """
    raise NotImplementedError("generate_taxonomy is a scaffold — implement LLM call here.")


# ── Step 2: LLM judge 호출 ──────────────────────────────────────────────────

JUDGE_PROMPT_TEMPLATE = """다음은 동일한 도메인 '{domain}'에 대해 생성된 두 가지 taxonomy 출력입니다.

[Output A]
{output_a}

[Output B]
{output_b}

각 출력이 다음 7개 분석 리포트를 작성하는 데 얼마나 적합한지 1–5점으로 평가하시기 바랍니다.
- 1점: 부적합 (해당 리포트 작성 불가)
- 3점: 보통 (일부 정보 제공, 보완 필요)
- 5점: 매우 적합 (해당 리포트의 표준 카테고리·평가 루브릭에 정렬)

평가 대상 리포트: {report_types}

`docs/reference_report_taxonomy.md` Rubric의 §2 각 리포트별 평가 루브릭 기준을 적용하시기 바랍니다.

JSON 형식으로 응답하시기 바랍니다:
{{
  "output_a": {{"comparison_matrix": {{"score": <1-5>, "rationale": "..."}}, ...}},
  "output_b": {{"comparison_matrix": {{"score": <1-5>, "rationale": "..."}}, ...}}
}}
"""


def call_judge(run: EvalRun) -> EvalRun:
    """LLM judge에 baseline·treatment 출력을 제출하고 점수를 받습니다.

    TODO:
    - JUDGE_PROMPT_TEMPLATE에 baseline·treatment를 A/B로 (랜덤 swap으로 순서 편향 제거)
    - `ClaudeApiAnalyzer(temperature=0)` 호출
    - JSON 응답 파싱 → run.baseline_scores · run.treatment_scores 채움
    - judge 응답 raw text도 보존 (재현성)
    """
    raise NotImplementedError("call_judge is a scaffold — implement Claude API judge call.")


# ── Step 3: 통계 분석 ───────────────────────────────────────────────────────

def paired_t_test(
    baseline_scores: list[int], treatment_scores: list[int]
) -> dict:
    """리포트별 점수 list에 대한 paired t-test 결과.

    TODO:
    - `scipy.stats.ttest_rel(treatment_scores, baseline_scores)` 호출
    - t-statistic, p-value 반환
    - 양측 검정 (treatment > baseline 또는 < baseline 모두 보고)
    """
    raise NotImplementedError("paired_t_test is a scaffold — use scipy.stats.ttest_rel.")


def bootstrap_ci(
    baseline_scores: list[int],
    treatment_scores: list[int],
    n_bootstrap: int = 1000,
    confidence: float = 0.95,
) -> dict:
    """리포트별 평균 차이의 bootstrap 95% CI.

    TODO:
    - n_bootstrap회 resampling + 차이 평균 계산
    - 2.5 / 97.5 percentile로 95% CI 산출
    """
    raise NotImplementedError("bootstrap_ci is a scaffold — implement resampling here.")


# ── Step 4: 보고서 작성 ─────────────────────────────────────────────────────

def write_report(runs: list[EvalRun], output_path: Path, rubric_version: str) -> None:
    """리포트별 평균 + t-test + CI + 권고를 markdown 파일로 저장.

    TODO:
    - 7개 리포트별 baseline·treatment 평균 + t-statistic + p-value + 95% CI 표
    - p < 0.05인 리포트만 강조
    - Rubric 도입 권고 / 보류 / 추가 개선 영역 명시
    """
    raise NotImplementedError("write_report is a scaffold — implement markdown rendering.")


# ── 실행 진입점 ──────────────────────────────────────────────────────────────

def run_evaluation(
    domain: str,
    iterations: int,
    rubric_version: str,
    dry_run: bool = False,
) -> int:
    output_dir = EVAL_OUTPUT_DIR / f"rubric_{rubric_version}"
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"📊 Rubric eval 시작 — 도메인='{domain}', 반복={iterations}회, Rubric={rubric_version}")
    if dry_run:
        print("⚠ DRY RUN — LLM 호출 없이 구조만 검증합니다.")
        print(f"   출력 디렉터리: {output_dir.relative_to(ROOT)}")
        print(f"   평가 리포트 유형 {len(REPORT_TYPES)}종: {', '.join(REPORT_TYPES)}")
        return 0

    runs: list[EvalRun] = []
    for i in range(iterations):
        print(f"  [{i + 1}/{iterations}] baseline·treatment 생성 + judge 호출 중...")
        baseline = generate_taxonomy(domain, "baseline")
        treatment = generate_taxonomy(domain, "treatment")
        run = EvalRun(
            iteration=i,
            domain=domain,
            baseline_output=baseline,
            treatment_output=treatment,
        )
        run = call_judge(run)
        runs.append(run)

    report_path = output_dir / "report.md"
    write_report(runs, report_path, rubric_version)
    print(f"✅ 평가 완료 — 보고서: {report_path.relative_to(ROOT)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rubric 적용 전/후 DomainTaxonomyAgent 출력 LLM-as-judge 평가."
    )
    parser.add_argument(
        "--domain", default="토스 트래블카드", help="평가 대상 도메인 (default: 토스 트래블카드)"
    )
    parser.add_argument(
        "--iterations", type=int, default=20, help="반복 횟수 (default: 20)"
    )
    parser.add_argument(
        "--rubric-version", default="v0.1", help="Rubric 버전 라벨 (default: v0.1)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="LLM 호출 없이 구조만 검증.",
    )
    args = parser.parse_args()
    return run_evaluation(
        domain=args.domain,
        iterations=args.iterations,
        rubric_version=args.rubric_version,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    sys.exit(main())
