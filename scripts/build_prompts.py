#!/usr/bin/env python3
"""
scripts/build_prompts.py
========================
D9 방식 1 채택(pipeline_topology_redesign.md v0.8 §6-0)에 따른 system_prompt 자동 빌드.

`docs/reference/report_taxonomy.md`의 §1·§2·§3을 발췌하여
`agents/domain_modeling/system_prompt_kr.md`의 RUBRIC marker 영역에 inline 인용합니다.

사용
----
  python scripts/build_prompts.py             # Rubric 변경 후 1회 실행
  python scripts/build_prompts.py --check     # CI용: 빌드 차이만 확인하고 종료 코드로 보고

흐름
----
1. Rubric md 파일에서 `## 1. ` ~ `## 4. ` 직전 구간(=§1·§2·§3)을 그대로 발췌.
2. system_prompt_kr.md의 `<!-- RUBRIC_BEGIN -->` ... `<!-- RUBRIC_END -->` 사이를 발췌로 교체.
3. footer에 `<!-- Rubric: vX.Y -->` 주석 부착(갱신). Rubric의 "문서 버전" 라인에서 vX.Y 추출.

설계 원칙
---------
- 외부 의존성 없음 (표준 라이브러리만 사용 — re, sys, argparse, pathlib).
- Rubric 변경 시 본 스크립트만 1회 실행하면 system_prompt와 캐시 키(Rubric 버전 포함)가
  자동 동기화됩니다.
- 빌드 실패는 종료 코드 1로 보고. CI에서 `--check` 모드로 차이만 검증 가능.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# ── 경로 상수 ──────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
RUBRIC_PATH = ROOT / "docs" / "reference" / "report_taxonomy.md"
PROMPT_PATH = ROOT / "agents" / "domain_modeling" / "system_prompt_kr.md"

# ── Marker 상수 ───────────────────────────────────────────────────────────
RUBRIC_BEGIN = "<!-- RUBRIC_BEGIN -->"
RUBRIC_END = "<!-- RUBRIC_END -->"
FOOTER_PATTERN = re.compile(r"<!-- Rubric: v\d+\.\d+ -->")


def extract_rubric_excerpt(rubric_text: str) -> str:
    """Rubric의 §1·§2·§3을 발췌. §4(anti-pattern) 직전까지 포함."""
    start = rubric_text.find("## 1. ")
    end = rubric_text.find("## 4. ")
    if start == -1:
        raise ValueError(
            f"Rubric에서 '## 1. ' 헤더를 찾을 수 없음 — {RUBRIC_PATH.relative_to(ROOT)}"
        )
    if end == -1:
        raise ValueError(
            f"Rubric에서 '## 4. ' 헤더를 찾을 수 없음 — {RUBRIC_PATH.relative_to(ROOT)}"
        )
    return rubric_text[start:end].rstrip()


def extract_rubric_version(rubric_text: str) -> str:
    """Rubric '문서 버전' 라인에서 vX.Y 추출. 미발견 시 'v?.?'."""
    match = re.search(r"문서\s*버전.*?(v\d+\.\d+)", rubric_text)
    return match.group(1) if match else "v?.?"


def build_system_prompt(prompt_text: str, excerpt: str, version: str) -> str:
    """Marker 영역을 excerpt로 교체하고 footer에 Rubric 버전 주석 부착."""
    marker_pattern = re.compile(
        re.escape(RUBRIC_BEGIN) + r".*?" + re.escape(RUBRIC_END),
        re.DOTALL,
    )
    new_block = f"{RUBRIC_BEGIN}\n{excerpt}\n{RUBRIC_END}"

    if not marker_pattern.search(prompt_text):
        raise ValueError(
            f"{PROMPT_PATH.relative_to(ROOT)}에 `{RUBRIC_BEGIN}` ... `{RUBRIC_END}` "
            "marker가 없습니다. 수동으로 marker를 1회 삽입한 뒤 본 스크립트를 재실행하세요."
        )

    new_prompt = marker_pattern.sub(new_block, prompt_text)
    footer_block = f"<!-- Rubric: {version} -->"
    if FOOTER_PATTERN.search(new_prompt):
        new_prompt = FOOTER_PATTERN.sub(footer_block, new_prompt)
    else:
        new_prompt = new_prompt.rstrip() + f"\n\n{footer_block}\n"
    return new_prompt


def run_build(check_only: bool = False) -> int:
    if not RUBRIC_PATH.exists():
        print(f"ERROR: Rubric 파일 없음 — {RUBRIC_PATH}", file=sys.stderr)
        return 1
    if not PROMPT_PATH.exists():
        print(f"ERROR: system_prompt 파일 없음 — {PROMPT_PATH}", file=sys.stderr)
        return 1

    rubric_text = RUBRIC_PATH.read_text(encoding="utf-8")
    prompt_text = PROMPT_PATH.read_text(encoding="utf-8")

    excerpt = extract_rubric_excerpt(rubric_text)
    version = extract_rubric_version(rubric_text)
    new_prompt = build_system_prompt(prompt_text, excerpt, version)

    if new_prompt == prompt_text:
        print(f"✅ 변경 없음 — Rubric {version} 이미 적용됨")
        return 0

    if check_only:
        print(
            f"⚠ 빌드 차이 발견 — Rubric {version}이 system_prompt에 반영되지 않았습니다. "
            "`python scripts/build_prompts.py`를 실행하세요.",
            file=sys.stderr,
        )
        return 2

    PROMPT_PATH.write_text(new_prompt, encoding="utf-8")
    print(f"✅ system_prompt 갱신 완료 — Rubric {version} 적용")
    print(f"   - 입력: {RUBRIC_PATH.relative_to(ROOT)}")
    print(f"   - 출력: {PROMPT_PATH.relative_to(ROOT)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rubric §1·§2·§3을 system_prompt_kr.md에 inline 인용 빌드."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="CI용: 파일을 변경하지 않고 빌드 차이만 검증 (종료 코드 2 = 차이 있음).",
    )
    args = parser.parse_args()
    return run_build(check_only=args.check)


if __name__ == "__main__":
    sys.exit(main())
