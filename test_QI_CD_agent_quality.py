"""
test_agent_quality.py
─────────────────────
웹 서버 없이 QueryIntakeAgent / CompetitorDiscoveryAgent의
출력 품질을 직접 검증하는 CLI 테스트 스크립트.

주요 검증 항목
--------------
1. Schema 슬림화 (description/examples 제거) 이후에도 유효한 JSON이 반환되는지
2. 핵심 필드(own_product.name, brand, category 등)가 올바르게 채워지는지
3. 브랜드 명칭 정규화 규칙(영어→한글, 오타 보정, 약어 복원)이 적용되는지
4. 슬림/원본 schema 호출 시 토큰 추정량 비교

사용 방법
---------
  # 기본 실행 (QueryIntakeAgent, 슬림 schema)
  python test_agent_quality.py

  # 전체 옵션
  python test_agent_quality.py \\
    --agent    query_intake             # query_intake | competitor_discovery
    --schema   slim                     # slim | full
    --query    "토스 트래블카드"          # 검색어 (쉼표로 여러 개)
    --compare                           # slim vs full schema 결과 비교
    --out      results/test_result.json # 결과 저장 경로 (선택)

실행 전제 조건
--------------
  - claude CLI 설치 및 로그인 완료 (which claude)
  - pip install jsonschema python-dotenv  (이미 설치됨)
  - 프로젝트 루트에서 실행: python test_agent_quality.py
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ── 경로 설정 ─────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

# .env 로드 (ANTHROPIC_API_KEY 등)
try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass

from server.llm.claude_cli_analyzer import ClaudeCodeCliAnalyzer  # noqa: E402

# ── 상수 ─────────────────────────────────────────────────────────────────────
AGENTS_DIR = PROJECT_ROOT / "agents"

# 에이전트별 기본 테스트 검색어
DEFAULT_QUERIES = {
    "query_intake": [
        "토스 트래블카드",          # 정상 입력
        "toss travel card",         # 영어 입력 → 한글 변환 검증
        "토트카",                   # 약어 복원 검증
        "하나 트레블로그",           # 오타 보정 검증
        "카카오페이",               # 단순 브랜드명
    ],
    "competitor_discovery": [
        # competitor_discovery는 human_review 이후 state를 받으므로
        # 테스트용 minimal input을 직접 구성함
    ],
}

# ── Schema 슬림화 ─────────────────────────────────────────────────────────────
_VERBOSE_KEYS = frozenset({
    "description", "examples", "$schema", "$id",
    "title", "$comment", "default",
})


def strip_schema(obj):
    """description, examples 등 메타 필드를 재귀적으로 제거한다."""
    if isinstance(obj, dict):
        return {k: strip_schema(v) for k, v in obj.items() if k not in _VERBOSE_KEYS}
    if isinstance(obj, list):
        return [strip_schema(i) for i in obj]
    return obj


def token_estimate(text: str) -> int:
    """바이트 기준 토큰 추정 (한국어 혼합: ≈ 3 bytes/token)."""
    return len(text.encode("utf-8")) // 3


# ── 에이전트 파일 로더 ────────────────────────────────────────────────────────
def load_agent_files(agent_name: str) -> tuple[str, dict]:
    """system_prompt_kr.md와 output.schema.json을 로드한다."""
    agent_dir = AGENTS_DIR / agent_name
    prompt_path = agent_dir / "system_prompt_kr.md"
    schema_path = agent_dir / "output.schema.json"

    if not prompt_path.exists():
        raise FileNotFoundError(f"system_prompt_kr.md 없음: {prompt_path}")
    if not schema_path.exists():
        raise FileNotFoundError(f"output.schema.json 없음: {schema_path}")

    system_prompt = prompt_path.read_text(encoding="utf-8")
    output_schema = json.loads(schema_path.read_text(encoding="utf-8"))
    return system_prompt, output_schema


# ── 사용자 프롬프트 구성 ──────────────────────────────────────────────────────
def build_user_prompt_query_intake(query: str) -> str:
    now = datetime.now(timezone.utc).isoformat()
    return (
        f"검색어: {query}\n\n"
        f"run_id: test_run_{now[:10]}\n"
        f"request_id: test_req_{now[:10]}\n\n"
        "위 검색어를 기반으로 CompetitorDiscoveryAgent 입력 초안을 생성하라."
    )


def build_user_prompt_competitor_discovery(query: str) -> str:
    """테스트용 minimal CompetitorDiscoveryAgent 입력을 구성한다."""
    cd_input = {
        "project_id":        "test_proj_001",
        "run_id":            "test_run_001",
        "domain_name":       "해외 결제/환전 특화 카드",
        "own_product": {
            "brand":    "토스",
            "name":     query,
            "category": "여행 특화 카드 상품",
        },
        "problem_statement": "해외여행 시 환전과 결제를 간편하고 유리하게 처리하고 싶다",
        "target_user":       ["해외여행자", "단기 출장자"],
        "core_value_props":  ["환전 편의성", "해외 결제 편의성", "수수료 절감"],
        "geography":         "대한민국",
    }
    return (
        "아래 JSON 입력을 읽고, 경쟁 후보군을 분석하여 "
        "출력 schema를 만족하는 JSON만 반환하라.\n\n"
        f"입력:\n{json.dumps(cd_input, ensure_ascii=False, indent=2)}"
    )


PROMPT_BUILDERS = {
    "query_intake":         build_user_prompt_query_intake,
    "competitor_discovery": build_user_prompt_competitor_discovery,
}

# ── 핵심 필드 검증 ────────────────────────────────────────────────────────────
def validate_key_fields(agent_name: str, output: dict, query: str) -> list[str]:
    """출력 dict에서 핵심 필드의 존재와 비어 있지 않음을 확인한다."""
    issues = []

    if agent_name == "query_intake":
        draft = output.get("draft_competitor_discovery_input", {})
        own   = draft.get("own_product", {})

        checks = [
            (own.get("brand"),           "own_product.brand 누락 또는 빈 값"),
            (own.get("name"),            "own_product.name 누락 또는 빈 값"),
            (own.get("category"),        "own_product.category 누락 또는 빈 값"),
            (draft.get("problem_statement"), "problem_statement 누락 또는 빈 값"),
            (draft.get("target_user"),   "target_user 누락 또는 빈 값"),
            (draft.get("core_value_props"), "core_value_props 누락 또는 빈 값"),
            (draft.get("geography"),     "geography 누락 또는 빈 값"),
            (output.get("display_fields"), "display_fields 누락"),
            (output.get("assumptions"),  "assumptions 누락"),
        ]
        for value, msg in checks:
            if not value:
                issues.append(msg)

    elif agent_name == "competitor_discovery":
        checks = [
            (output.get("own_product_summary"), "own_product_summary 누락"),
            (output.get("competition_axes"),    "competition_axes 누락"),
            (output.get("competitor_candidates"), "competitor_candidates 누락"),
        ]
        for value, msg in checks:
            if not value:
                issues.append(msg)

        for i, c in enumerate(output.get("competitor_candidates", [])):
            if not c.get("brand"):
                issues.append(f"competitor_candidates[{i}].brand 누락")
            if not c.get("product_name"):
                issues.append(f"competitor_candidates[{i}].product_name 누락")
            if c.get("competition_type") not in ("direct", "indirect", "substitute"):
                issues.append(f"competitor_candidates[{i}].competition_type 값 오류")

    return issues


# ── 단일 테스트 실행 ──────────────────────────────────────────────────────────
def run_single_test(
    agent_name: str,
    query: str,
    system_prompt: str,
    output_schema: dict,
    use_slim: bool,
) -> dict:
    """에이전트를 1회 호출하고 결과를 반환한다."""
    import jsonschema

    schema_label = "slim" if use_slim else "full"
    effective_schema = strip_schema(output_schema) if use_slim else output_schema

    user_prompt  = PROMPT_BUILDERS[agent_name](query)
    schema_str   = json.dumps(effective_schema, ensure_ascii=False, separators=(",", ":"))
    prompt_total = system_prompt + user_prompt + schema_str

    result = {
        "agent":        agent_name,
        "query":        query,
        "schema_mode":  schema_label,
        "schema_tokens": token_estimate(schema_str),
        "total_input_tokens_estimate": token_estimate(prompt_total),
        "timestamp":    datetime.now(timezone.utc).isoformat(),
        "success":      False,
        "attempts":     0,
        "output":       None,
        "key_field_issues": [],
        "error":        None,
        "elapsed_sec":  0.0,
    }

    analyzer = ClaudeCodeCliAnalyzer(
        model=os.getenv("CLI_MODEL", "claude-sonnet-4-6"),
        timeout=int(os.getenv("CLI_TIMEOUT", "120")),
        system_prompt=system_prompt,
    )

    # attempt 횟수 추적을 위해 내부 루프를 직접 실행
    import jsonschema as _js

    last_error = None
    max_retries = 3
    start = time.time()

    for attempt in range(1, max_retries + 1):
        result["attempts"] = attempt
        try:
            if attempt == 1:
                full_prompt = analyzer._build_schema_prompt(user_prompt, effective_schema)
            else:
                full_prompt = (
                    user_prompt
                    + f"\n\n[이전 시도 {attempt - 1}회 오류: {str(last_error)[:300]}]\n"
                    "위 오류를 수정해 올바른 JSON을 다시 반환하라. "
                    "앞서 제시된 JSON Schema를 그대로 준수할 것."
                )

            raw    = analyzer._invoke_cli(full_prompt)
            parsed = analyzer._extract_json(raw)
            _js.validate(parsed, output_schema)   # 원본 schema로 검증 (슬림 여부 무관)

            result["success"] = True
            result["output"]  = parsed
            result["key_field_issues"] = validate_key_fields(agent_name, parsed, query)
            break

        except (_js.ValidationError, json.JSONDecodeError) as e:
            last_error = e
        except RuntimeError as e:
            last_error = e
            result["error"] = str(e)
            break

    result["elapsed_sec"] = round(time.time() - start, 1)
    if not result["success"] and last_error:
        result["error"] = str(last_error)[:500]

    return result


# ── 결과 출력 ─────────────────────────────────────────────────────────────────
RESET  = "\033[0m"
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"


def print_result(r: dict, verbose: bool = True) -> None:
    status = f"{GREEN}✅ PASS{RESET}" if (r["success"] and not r["key_field_issues"]) \
             else f"{YELLOW}⚠ PASS(경고){RESET}" if r["success"] \
             else f"{RED}❌ FAIL{RESET}"

    print(f"\n{'─'*60}")
    print(f"{BOLD}에이전트{RESET}  : {r['agent']}  [{r['schema_mode']} schema]")
    print(f"{BOLD}검색어{RESET}    : {r['query']}")
    print(f"{BOLD}결과{RESET}      : {status}  ({r['attempts']}회 시도, {r['elapsed_sec']}s)")
    print(f"{BOLD}토큰 추정{RESET} : schema {r['schema_tokens']:,} tokens  /  전체 입력 {r['total_input_tokens_estimate']:,} tokens")

    if r["key_field_issues"]:
        print(f"{YELLOW}핵심 필드 경고:{RESET}")
        for issue in r["key_field_issues"]:
            print(f"  • {issue}")

    if r["error"]:
        print(f"{RED}오류:{RESET} {r['error'][:300]}")

    if r["success"] and verbose and r["output"]:
        out = r["output"]
        if r["agent"] == "query_intake":
            draft = out.get("draft_competitor_discovery_input", {})
            own   = draft.get("own_product", {})
            print(f"\n{CYAN}[핵심 출력 요약]{RESET}")
            print(f"  own_product.brand   : {own.get('brand')}")
            print(f"  own_product.name    : {own.get('name')}")
            print(f"  own_product.category: {own.get('category')}")
            print(f"  geography           : {draft.get('geography')}")
            print(f"  problem_statement   : {str(draft.get('problem_statement',''))[:80]}")
            print(f"  needs_user_confirm  : {out.get('needs_user_confirmation')}")
            print(f"  uncertain_fields    : {out.get('uncertain_fields')}")

        elif r["agent"] == "competitor_discovery":
            candidates = out.get("competitor_candidates", [])
            print(f"\n{CYAN}[핵심 출력 요약]{RESET}")
            print(f"  경쟁사 후보 수  : {len(candidates)}")
            print(f"  기능적 대안 수  : {len(out.get('functional_competitors', []))}")
            print(f"  competition_axes: {out.get('competition_axes', [])[:3]}")
            for c in candidates[:5]:
                print(f"  └ [{c.get('competition_type','?')}] {c.get('brand')} / {c.get('product_name')}  (confidence={c.get('confidence')})")


def print_compare_summary(slim_results: list[dict], full_results: list[dict]) -> None:
    print(f"\n{'═'*60}")
    print(f"{BOLD}Schema 슬림/원본 비교 요약{RESET}")
    print(f"{'─'*60}")
    print(f"{'검색어':<20}  {'slim 토큰':>10}  {'full 토큰':>10}  {'절감':>8}  {'slim 결과':<10}  {'full 결과'}")
    print(f"{'─'*60}")

    for s, f in zip(slim_results, full_results):
        saved = f["schema_tokens"] - s["schema_tokens"]
        pct   = saved / f["schema_tokens"] * 100 if f["schema_tokens"] else 0
        slim_ok = "✅" if s["success"] and not s["key_field_issues"] else ("⚠" if s["success"] else "❌")
        full_ok = "✅" if f["success"] and not f["key_field_issues"] else ("⚠" if f["success"] else "❌")
        print(f"{s['query']:<20}  {s['schema_tokens']:>10,}  {f['schema_tokens']:>10,}  {saved:>6,}({pct:.0f}%)  {slim_ok:<10}  {full_ok}")


# ── 메인 ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="QueryIntakeAgent / CompetitorDiscoveryAgent 품질 테스트"
    )
    parser.add_argument(
        "--agent", default="query_intake",
        choices=["query_intake", "competitor_discovery"],
        help="테스트할 에이전트 (기본: query_intake)",
    )
    parser.add_argument(
        "--schema", default="slim",
        choices=["slim", "full"],
        help="사용할 schema 모드 (기본: slim)",
    )
    parser.add_argument(
        "--query", default=None,
        help="테스트 검색어. 쉼표로 여러 개 지정 가능. 미지정 시 기본 세트 사용.",
    )
    parser.add_argument(
        "--compare", action="store_true",
        help="slim / full schema 결과를 나란히 비교",
    )
    parser.add_argument(
        "--out", default=None,
        help="결과를 저장할 JSON 파일 경로 (선택)",
    )
    parser.add_argument(
        "--no-verbose", action="store_true",
        help="핵심 출력 요약 생략",
    )
    args = parser.parse_args()

    # 검색어 목록 결정
    if args.query:
        queries = [q.strip() for q in args.query.split(",") if q.strip()]
    else:
        queries = DEFAULT_QUERIES.get(args.agent, ["토스 트래블카드"])

    if not queries:
        # competitor_discovery 기본값
        queries = ["토스 트래블카드"]

    # 에이전트 파일 로드
    try:
        system_prompt, output_schema = load_agent_files(args.agent)
    except FileNotFoundError as e:
        print(f"{RED}파일 로드 실패: {e}{RESET}")
        sys.exit(1)

    print(f"\n{BOLD}{'='*60}")
    print(f"Agent Quality Test  —  {args.agent}")
    print(f"Schema mode : {args.schema}{'  +  full (비교 모드)' if args.compare else ''}")
    print(f"검색어 수   : {len(queries)}개")
    print(f"{'='*60}{RESET}")

    slim_results = []
    full_results = []

    for query in queries:
        if args.compare or args.schema == "slim":
            print(f"\n{CYAN}▶ [slim]  '{query}'{RESET}")
            r = run_single_test(args.agent, query, system_prompt, output_schema, use_slim=True)
            slim_results.append(r)
            print_result(r, verbose=not args.no_verbose)

        if args.compare or args.schema == "full":
            print(f"\n{CYAN}▶ [full]  '{query}'{RESET}")
            r = run_single_test(args.agent, query, system_prompt, output_schema, use_slim=False)
            full_results.append(r)
            print_result(r, verbose=not args.no_verbose)

    # 비교 요약
    if args.compare and slim_results and full_results:
        print_compare_summary(slim_results, full_results)

    # 최종 통계
    all_results = slim_results + full_results
    passed  = sum(1 for r in all_results if r["success"] and not r["key_field_issues"])
    warned  = sum(1 for r in all_results if r["success"] and r["key_field_issues"])
    failed  = sum(1 for r in all_results if not r["success"])
    retried = sum(1 for r in all_results if r["attempts"] > 1)

    print(f"\n{BOLD}{'─'*60}")
    print(f"최종 결과  ✅ {passed}  ⚠ {warned}  ❌ {failed}  (재시도 발생: {retried}건)")
    print(f"{'─'*60}{RESET}")

    # 결과 저장
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        save_data = {
            "run_at":      datetime.now(timezone.utc).isoformat(),
            "agent":       args.agent,
            "slim_results": slim_results,
            "full_results": full_results,
        }
        # output 필드에서 순환참조 방지 (직렬화 가능 여부 확인)
        out_path.write_text(
            json.dumps(save_data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"결과 저장 완료: {out_path}")


if __name__ == "__main__":
    main()
