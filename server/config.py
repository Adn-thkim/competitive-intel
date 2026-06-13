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
PRODUCT_NAME_CACHE_PATH    = CACHE_DIR / "product_name_normalization.json"
ANALYSIS_CACHE_PATH        = CACHE_DIR / "analysis_runs.json"
AGENT_OUTPUT_CACHE_DIR     = CACHE_DIR / "agent_outputs"
OFFICIAL_SOURCE_STORE_PATH = CACHE_DIR / "official_sources.json"

# OfficialSourceStore TTL (일 단위).
# 한 번 검증된 상품 공식 URL은 이 기간 동안 재탐색 없이 재사용된다.
# 캐시 hit 시에도 HTTP 재검증 1회를 수행하므로 죽은 링크는 자동으로 폐기된다.
OFFICIAL_SOURCE_STORE_TTL_DAYS = int(os.getenv("OFFICIAL_SOURCE_STORE_TTL_DAYS", "30"))


# ── 캐시 TTL ─────────────────────────────────────────────────────────────────
# 분석 캐시 유효 기간(시간). 초과 시 재실행 및 덮어쓰기.
# 기본값: 48시간
ANALYSIS_CACHE_TTL_HOURS = int(os.getenv("ANALYSIS_CACHE_TTL_HOURS", "48"))


# ── Claude Code CLI 설정 ─────────────────────────────────────────────────────
# ClaudeCodeCliAnalyzer에서 사용하는 기본값.
# ⚠️ CLI는 --temperature 플래그를 지원하지 않는다 (GitHub issue #6096).
#    결정론성이 필요한 단계(ProductIdResolver)는 Claude API를 직접 사용한다.
CLI_MODEL   = os.getenv("CLI_MODEL", "claude-sonnet-4-6")
# v0.10.10: 일반 LLM 호출 timeout 기본값을 120s → 300s 로 상향.
# 본 값은 query_intake / competitor_discovery / domain_modeling / official_source_resolver /
# url_retry 등 모든 단일 LLM 호출에 공통 적용된다. feature_mapping_llm_node 는 토큰
# 규모가 더 커서 아래의 FEATURE_MAPPING_LLM_TIMEOUT 으로 별도 분리·관리한다.
CLI_TIMEOUT = int(os.getenv("CLI_TIMEOUT", "300"))

# feature_mapping_llm_node 전용 timeout (v0.10.10 분리).
# 본 노드의 단일 LLM 호출은 (a) system_prompt(Rubric 인라인 약 5K 토큰) +
# (b) active_reports[report_type] (features 최대 12개 + categories + hints) +
# (c) candidates_with_meta (슬림화 후 평균 3K–4K 토큰) +
# (d) 출력 JSON(features × candidate_coverage × additional_urls, 약 6K–10K 토큰)
# 의 총 입출력 약 13K–24K 토큰을 처리하므로 일반 노드보다 더 큰 안전 마진이 필요하다.
# 본 변수는 CLI_TIMEOUT 과 독립이며, 환경변수 FEATURE_MAPPING_LLM_TIMEOUT 으로 별도 override 가능.
# 기본값은 CLI_TIMEOUT 과 동일(300s) 이지만, 실측 결과에 따라 운영자가 본 값만 더 늘릴 수 있다.
FEATURE_MAPPING_LLM_TIMEOUT = int(
    os.getenv("FEATURE_MAPPING_LLM_TIMEOUT", str(CLI_TIMEOUT))
)

# FeatureUrlMapperAgent 병렬 처리 설정.
# v0.10 이후: 이 노드는 report_config의 active 리포트 단위로 병렬 LLM 호출한다.
# (예: 7개 active 리포트 → max_workers=4 → ~2라운드 × ~60s = 약 2분)
# 값을 높이면 속도가 빨라지지만 Claude 구독 Rate Limit 위험이 증가한다.
# v0.10.9: 4배치 → 2배치로 절감 위해 기본값 2→4 상향.
# 환경변수로 조정: FEATURE_URL_MAPPER_PARALLEL=4
FEATURE_URL_MAPPER_PARALLEL = int(os.getenv("FEATURE_URL_MAPPER_PARALLEL", "4"))

# OfficialSourceResolverAgent 병렬 처리 설정.
# candidate별로 Brave 탐색·HTTP 검증을 병렬 실행한다.
#   - 캐시 키가 candidate 단위로 분리되어 재실행 시 캐시 히트율이 높아진다.
# ⚠️ (2026-05 개편) LLM 호출은 candidate 단위가 아니라 batch 단위(A-1)로 묶이므로
#    PARALLEL은 주로 Brave 탐색·페이지 메타·HTTP 검증의 I/O 병렬도를 결정한다.
#    LLM Rate Limit 위험은 batch 호출 1~2회로 한정되어 크게 완화되었다.
# 환경변수로 조정: OFFICIAL_SOURCE_RESOLVER_PARALLEL=2 (기존 호환 기본값)
OFFICIAL_SOURCE_RESOLVER_PARALLEL = int(os.getenv("OFFICIAL_SOURCE_RESOLVER_PARALLEL", "2"))

# A-3 동적 PARALLEL 상한.
# candidate 수 N에 대해 실제 워커 수 = min(N, OFFICIAL_SOURCE_RESOLVER_PARALLEL_MAX).
# LLM 호출이 batch로 통합되었으므로 I/O 위주 단계에는 상한을 넉넉히 둔다.
OFFICIAL_SOURCE_RESOLVER_PARALLEL_MAX = int(
    os.getenv("OFFICIAL_SOURCE_RESOLVER_PARALLEL_MAX", "6")
)

# A-1 batch LLM 검증 설정.
# 한 번의 LLM 호출에 묶을 candidate 수 상한. 5 이하 권장(프롬프트 토큰 폭증 방지).
OFFICIAL_SOURCE_RESOLVER_LLM_BATCH_SIZE = int(
    os.getenv("OFFICIAL_SOURCE_RESOLVER_LLM_BATCH_SIZE", "5")
)

# E-1 Brave 검색 결과 캐시 TTL(시간). 동일 (브랜드, 상품명) 쿼리는 이 기간 동안 재사용.
# (official_source_resolver 의 인메모리 캐시 전용 — 파일 캐시 전환은 Future_Improvements 5번)
BRAVE_RESULT_CACHE_TTL_HOURS = int(os.getenv("BRAVE_RESULT_CACHE_TTL_HOURS", "24"))

# E-1b v0.13.5 — _brave_search 파일 캐시(url_discovery_brave) TTL(시간).
# 2026-06-07 월 크레딧 소진 사고의 구조 원인 = 전체 실행마다 5개 url_discovery 노드의
# 쿼리 ~150건이 24h 만료로 전량 재호출. URL 탐색 결과는 일 단위로 변하지 않으므로
# 7일로 연장해 월 소모량을 ~1/7 로 줄인다. page_meta_collect·url_validation 은
# 대상이 아님 (url_validation 은 죽은 링크 오판 박제 방지를 위해 24h 유지).
BRAVE_SEARCH_CACHE_TTL_HOURS = int(os.getenv("BRAVE_SEARCH_CACHE_TTL_HOURS", "168"))

# E-2 HTTP 검증 결과 캐시 TTL(분). 동일 URL은 이 기간 동안 재검증을 생략.
HTTP_VALIDATION_CACHE_TTL_MINUTES = int(os.getenv("HTTP_VALIDATION_CACHE_TTL_MINUTES", "60"))


# ── v0.14 커뮤니티 수집 확장 (docs/design/community_collection_expansion_design.md) ──
# CE-D2 1군 고정 화이트리스트 — {domain: collection_mode}. 실측 근거: CE-D7 표 (2026-06-12).
#   full        : 본문 + 댓글 (일반 추출로 충분)
#   body_only   : 본문만 (댓글 동적 로딩 또는 휴리스틱 실패 — ppomppu 는 CE-D6 2단계
#                 전용 파서 구현 시 full 승격)
#   snippet_only: robots 차단 — 검색 스니펫만 사용 (CE-D10. 집계 전용, top_quotes 제외)
COMMUNITY_SITES_FIXED: dict[str, str] = {
    "clien.net":         "full",
    "dcinside.com":      "full",          # 모바일 URL 변환 + 모바일 UA 필수 (CE-D7)
    "ppomppu.co.kr":     "body_only",
    "theqoo.net":        "body_only",
    "fmkorea.com":       "snippet_only",
    "mlbpark.donga.com": "snippet_only",
}
# CE-D1·D7 — broad query 페이지네이션 상한 (실측: Brave 깊이 한계 = 6페이지 포화)
COMMUNITY_BRAVE_MAX_PAGES   = int(os.getenv("COMMUNITY_BRAVE_MAX_PAGES", "6"))
# §3 선별 상한 — 사이트당/candidate당 (round-robin 사이트 다양성 우선)
COMMUNITY_URLS_PER_SITE      = int(os.getenv("COMMUNITY_URLS_PER_SITE", "10"))
COMMUNITY_URLS_PER_CANDIDATE = int(os.getenv("COMMUNITY_URLS_PER_CANDIDATE", "40"))
# §3-4 — 문장 경계 chunking (요약·단순 절단 금지. 게시글 1건 = ABSA item 1~3건)
COMMUNITY_CHUNK_CHARS = int(os.getenv("COMMUNITY_CHUNK_CHARS", "3000"))
COMMUNITY_MAX_CHUNKS  = int(os.getenv("COMMUNITY_MAX_CHUNKS", "3"))
# CE-D2 2군 큐레이션 레지스트리 경로
COMMUNITY_REGISTRY_PATH = BASE_DIR / "data" / "community_registry.json"


# ── Claude API 설정 ──────────────────────────────────────────────────────────
# ProductIdResolver, InsightReportAgent 등 API 직접 호출 시 사용.
# .env 또는 시스템 환경변수에 ANTHROPIC_API_KEY를 설정해야 한다.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")


# ── Brave Search API ─────────────────────────────────────────────────────────
# Phase 1 URL 재탐색(검색 기반) 및 OfficialSourceResolver 초기 탐색에 사용.
# https://api.search.brave.com 에서 API Key를 발급한다.
# 무료 크레딧: $5/월 자동 충전 → Search 플랜 기준 약 1,000 쿼리/월 무료.
BRAVE_SEARCH_API_KEY = os.getenv("BRAVE_SEARCH_API_KEY", "")

# ── YouTube Data API v3 (v0.10.20 신설) ─────────────────────────────────────
# url_discovery_youtube_reactions_node 가 reaction_insight 의 3rd-party 영상 검색에 사용.
# Google Cloud Console 에서 YouTube Data API v3 활성화 후 API key 발급.
# 무료 quota = 일일 10,000 units.  search.list = 100 units/call · videos.list = 1 unit/call.
# 예상 호출(cache miss 첫 실행): candidate 4명 × 3 쿼리 = 12 × 100u = 1,200 units.
# 동일 도메인 재실행 시 24h TTL agent_cache hit 으로 0 units.
YOUTUBE_API_KEY             = os.getenv("YOUTUBE_API_KEY", "")
YOUTUBE_DAILY_QUOTA         = int(os.getenv("YOUTUBE_DAILY_QUOTA", "10000"))
YOUTUBE_QUOTA_SAFETY_MARGIN = int(os.getenv("YOUTUBE_QUOTA_SAFETY_MARGIN", "1000"))
YOUTUBE_REGION_CODE         = os.getenv("YOUTUBE_REGION_CODE", "KR")
YOUTUBE_MAX_RESULTS         = int(os.getenv("YOUTUBE_MAX_RESULTS", "10"))    # search.list 호출당
YOUTUBE_MIN_VIEW_COUNT      = int(os.getenv("YOUTUBE_MIN_VIEW_COUNT", "1000"))
YOUTUBE_MIN_COMMENT_COUNT   = int(os.getenv("YOUTUBE_MIN_COMMENT_COUNT", "10"))
YOUTUBE_CACHE_TTL_HOURS     = int(os.getenv("YOUTUBE_CACHE_TTL_HOURS", "24"))

# OfficialSourceResolver Brave 탐색 결과 수 (쿼리당).
# 후보 N개 × 2쿼리(한국어+영어) 기준 쿼터 소비:
#   count=5 → 최대 10개 후보  (기본, 정확도·쿼터 균형)
#   count=3 → 최대 6개 후보   (쿼터 절감, 정확도 소폭 감소)
# 환경변수로 조정: OFFICIAL_SOURCE_RESOLVER_BRAVE_COUNT=3
OFFICIAL_SOURCE_RESOLVER_BRAVE_COUNT = int(
    os.getenv("OFFICIAL_SOURCE_RESOLVER_BRAVE_COUNT", "5")
)

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

    # v0.10.20 — YouTube API key 미설정 시 reaction_insight 의 youtube_reactions
    # source-type 노드가 빈 결과를 반환하고 skipped 상태로 진행됨 (치명적 오류 아님).
    if not YOUTUBE_API_KEY:
        warnings.append(
            "YOUTUBE_API_KEY 가 설정되지 않았습니다. "
            "url_discovery_youtube_reactions_node 가 빈 결과를 반환하여 reaction_insight 의 "
            "YouTube 영상 수집이 비활성화됩니다. "
            "https://console.cloud.google.com 에서 YouTube Data API v3 활성화 후 API Key 를 발급하세요."
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
