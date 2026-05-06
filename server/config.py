"""
server/config.py
-----------------
환경변수 기반 중앙 설정 모듈.

모든 모듈은 os.environ을 직접 참조하는 대신 이 파일에서 설정값을 가져온다.
실행 환경에 따라 .env 파일 또는 시스템 환경변수를 통해 값을 주입한다.

우선순위: 시스템 환경변수 > .env 파일 > 기본값(코드 하드코딩)

사용 예:
    from server.config import CACHE_DIR, CLI_MODEL
"""

import os
from pathlib import Path

# python-dotenv 가 설치된 경우 .env 파일을 자동으로 로드한다.
# 미설치 시에도 정상 동작하며, 시스템 환경변수가 우선 적용된다.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ── 기준 경로 ────────────────────────────────────────────────────────────────
# 이 파일 위치: competitive-intel/server/config.py
# BASE_DIR  : competitive-intel/
BASE_DIR   = Path(__file__).resolve().parent.parent
SERVER_DIR = Path(__file__).resolve().parent


# ── 에이전트 파일 경로 ───────────────────────────────────────────────────────
AGENTS_DIR = BASE_DIR / "agents"


# ── 캐시 디렉토리 ────────────────────────────────────────────────────────────
# CACHE_DIR 환경변수로 전체 캐시 루트를 교체할 수 있다.
# 기본값: competitive-intel/data/cache/
#
# 예) .env: CACHE_DIR=/tmp/yt_analysis_cache
CACHE_DIR = Path(os.getenv("CACHE_DIR", str(BASE_DIR / "data" / "cache")))

# 캐시 하위 경로 (CACHE_DIR 변경 시 자동으로 따라간다)
PRODUCT_NAME_CACHE_PATH = CACHE_DIR / "product_name_normalization.json"
ANALYSIS_CACHE_PATH     = CACHE_DIR / "analysis_runs.json"
AGENT_OUTPUT_CACHE_DIR  = CACHE_DIR / "agent_outputs"


# ── 캐시 TTL ─────────────────────────────────────────────────────────────────
# 분석 캐시 유효 기간(시간). 초과 시 재실행 및 덮어쓰기.
# 기본값: 48시간
ANALYSIS_CACHE_TTL_HOURS = int(os.getenv("ANALYSIS_CACHE_TTL_HOURS", "48"))


# ── Claude Code CLI 설정 ─────────────────────────────────────────────────────
# ClaudeCodeCliAnalyzer에서 사용하는 기본값.
# ⚠️ CLI는 --temperature 플래그를 지원하지 않는다 (GitHub issue #6096).
#    결정론성이 필요한 단계(ProductIdResolver)는 Claude API를 직접 사용한다.
CLI_MODEL   = os.getenv("CLI_MODEL", "claude-sonnet-4-6")
CLI_TIMEOUT = int(os.getenv("CLI_TIMEOUT", "120"))

# FeatureUrlMapperAgent 병렬 처리 설정.
# 이 노드는 active_purposes를 purpose별로 분리해 병렬 LLM 호출한다.
# (예: 8 purposes → max_workers=2 → 4라운드 × ~45s = 약 3분)
# 값을 높이면 속도가 빨라지지만 Claude 구독 Rate Limit 위험이 증가한다.
# 환경변수로 조정: FEATURE_URL_MAPPER_PARALLEL=2
FEATURE_URL_MAPPER_PARALLEL = int(os.getenv("FEATURE_URL_MAPPER_PARALLEL", "2"))

# OfficialSourceResolverAgent 병렬 처리 설정.
# candidate별로 LLM 호출을 분리해 병렬 실행한다.
#   - 캐시 키가 candidate 단위로 분리되어 재실행 시 캐시 히트율이 높아진다.
#   - 예: 6개 candidate, max_workers=6 → 1라운드 × ~40s = 약 40초 (단일 호출 대비 ~85% 단축)
# 값을 높이면 속도가 빨라지지만 Claude 구독 Rate Limit 위험이 증가한다.
# 기본값 상향(2 → 6, 2026-05): 일반 시나리오의 candidate 수(자사 1 + 경쟁사 3~5)를
# 한 라운드에 처리할 수 있는 수준. 분당 호출 한도 초과 시 환경변수로 하향 조정 권장.
# 환경변수로 조정: OFFICIAL_SOURCE_RESOLVER_PARALLEL=6
OFFICIAL_SOURCE_RESOLVER_PARALLEL = int(os.getenv("OFFICIAL_SOURCE_RESOLVER_PARALLEL", "6"))


# ── Claude API 설정 ──────────────────────────────────────────────────────────
# ProductIdResolver, InsightReportAgent 등 API 직접 호출 시 사용.
# .env 또는 시스템 환경변수에 ANTHROPIC_API_KEY를 설정해야 한다.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")


# ── Brave Search API ─────────────────────────────────────────────────────────
# Phase 1 URL 재탐색(검색 기반)에 사용.
# https://api.search.brave.com 에서 API Key를 발급한다.
# 무료 크레딧: $5/월 자동 충전 → Search 플랜 기준 약 1,000 쿼리/월 무료.
BRAVE_SEARCH_API_KEY = os.getenv("BRAVE_SEARCH_API_KEY", "")

# API 기반 LLM에서 사용할 모델 (temperature=0 결정론적 처리용)
API_MODEL = os.getenv("API_MODEL", "claude-sonnet-4-6")


# ── LangGraph 설정 ───────────────────────────────────────────────────────────
# CompetitorDiscoveryAgent 후보 개수 한도
COMPETITOR_CANDIDATE_MAX = int(os.getenv("COMPETITOR_CANDIDATE_MAX", "15"))
COMPETITOR_CANDIDATE_MIN = int(os.getenv("COMPETITOR_CANDIDATE_MIN", "5"))


# ── 런타임 검증 ──────────────────────────────────────────────────────────────
def validate_config() -> list[str]:
    """
    필수 설정값 누락 여부를 검사하고 경고 목록을 반환한다.
    애플리케이션 시작 시 호출하여 빠른 실패(fail-fast)를 유도한다.

    Returns
    -------
    list[str]
        누락·비정상 설정에 대한 경고 메시지 목록. 비어 있으면 정상.
    """
    warnings: list[str] = []

    if not ANTHROPIC_API_KEY:
        warnings.append(
            "ANTHROPIC_API_KEY가 설정되지 않았습니다. "
            "ProductIdResolver 및 API 기반 LLM 호출이 실패합니다."
        )

    if not BRAVE_SEARCH_API_KEY:
        warnings.append(
            "BRAVE_SEARCH_API_KEY가 설정되지 않았습니다. "
            "Phase 1 URL 재탐색(검색 기반)이 비활성화됩니다. "
            "https://api.search.brave.com 에서 API Key를 발급하세요."
        )

    if not CACHE_DIR.exists():
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            warnings.append(f"CACHE_DIR({CACHE_DIR}) 생성 실패: {e}")

    if CLI_TIMEOUT < 30:
        warnings.append(
            f"CLI_TIMEOUT={CLI_TIMEOUT}초가 너무 짧습니다. "
            "LLM 응답 대기 중 타임아웃이 빈번히 발생할 수 있습니다."
        )

    return warnings
