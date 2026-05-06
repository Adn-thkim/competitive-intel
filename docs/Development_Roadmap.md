# 개발 로드맵 — Domain Analysis 버전

> **적용 환경**: Mac 로컬 전용 (외부 배포 없음)
> **아키텍처 방향**: React(Vite) + Express.js + Python FastAPI + LangGraph StateGraph + JSON 저장 + Multi-Agent Orchestration
> **현재 저장 방식**: JSON 파일 (`data/cache/`, `data/taxonomy/`)
> **향후 저장 방식**: SQLite 연동 예정
> **최종 목표**: 검색어 입력 → 도메인 분류 → 경쟁사 식별 → 공식 홈페이지 기능 비교 → YouTube 사용자 반응 분석 → 인사이트 리포트 생성
> **문서 버전**: v2.0 | 최초 작성: 2026-04-20 | 업데이트: 2026-05-05

---

## 아키텍처 개요

```text
Web Prompt UI (브라우저)
        ↕ REST API (localhost:4000)
Express.js 로컬 서버 (오케스트레이터)
        ↕ HTTP (localhost:8000)
Python FastAPI (uvicorn)
    └── LangGraph StateGraph (compiled_graph)
          ├── [1] query_intake_node           ← QueryIntakeAgent (Claude Code CLI)
          ├── [2] human_review_node           ← interrupt() #1 (폼 검토·수정)
          ├── [3] competitor_discovery_node   ← CompetitorDiscoveryAgent (Claude Code CLI)
          ├── [4] domain_modeling_node        ← DomainTaxonomyAgent (Claude Code CLI)
          ├── [5] normalize_competitor_ids    ← ProductIdResolver (Claude API, temp=0)
          ├── [6] competitor_selection_node   ← interrupt() #2 (경쟁사 선택)
          ├── [7] official_source_resolver    ← OfficialSourceResolverAgent (JS/Python)
          ├── [8] url_retry_node              ← interrupt() #3 (URL 실패 시 수동 입력)
          ├── [9] feature_url_mapper_node     ← FeatureUrlMapperAgent (Claude Code CLI)
          ├── [10] feature_selection_node     ← interrupt() #4 (분석 목적·항목 선택)
          │
          │   ── 이하 미구현 (TODO) ──
          ├── [ ] feature_extraction_node
          ├── [ ] feature_comparison_node
          ├── [ ] youtube_query_planner_node
          ├── [ ] youtube_collection_node
          ├── [ ] reaction_analysis_node
          └── [ ] insight_report_node

    ├── Brave Search API (url_retry Phase 1 재탐색)
    ├── YouTube Data API v3
    └── JSON 파일 저장소 (data/cache/, data/taxonomy/)
```

**Human-in-the-loop 요약 (4개 interrupt 지점)**:

| # | 노드 | 사용자 작업 |
|---|------|------------|
| 1 | `human_review_node` | QueryIntakeAgent 초안 검토·수정 후 파이프라인 재개 |
| 2 | `competitor_selection_node` | 경쟁 후보 목록에서 분석 대상 선택 (1~10개) |
| 3 | `url_retry_node` | URL 실패한 후보에 대해 수동 URL 입력 또는 재탐색 승인 |
| 4 | `feature_selection_node` | 분석 목적(purpose) 단위 선택 + 개별 feature 세부 조정 |

---

## 사전 준비 (Mac)

```bash
# Node.js v18 이상 확인
node --version

# Python 3.11 이상 확인
python3 --version

# npm / pip 버전 확인
npm --version
pip --version
```

**필수 API 키**:

- `ANTHROPIC_API_KEY`: ProductIdResolver (Claude API, temperature=0) 전용
- `YOUTUBE_API_KEY`: YouTubeCollectionAgent (미구현)
- `BRAVE_SEARCH_API_KEY`: url_retry_node Phase 1 URL 재탐색
- Claude Code CLI: 대부분의 LLM 호출 기본 경로 — 로컬 로그인 상태 필요

---

## 1단계: 환경 설정 및 디렉토리 구조 ✅

### 목표

- LangGraph 기반 파이프라인을 수용하는 디렉토리 구조를 구축한다.
- JSON 캐시 경로와 taxonomy 캐시 경로를 분리해 확정한다.

### 실제 프로젝트 구조

```text
competitive-intel/
├── client/
│   ├── src/
│   │   ├── api/
│   │   ├── components/
│   │   ├── pages/
│   │   └── utils/
│   ├── .env
│   └── vite.config.js
├── server/
│   ├── index.js                    ← Express.js 엔트리
│   ├── api.py                      ← FastAPI / uvicorn 엔트리
│   ├── config.py                   ← 환경변수 로딩, 경로 상수
│   ├── routes/
│   ├── graph/
│   │   ├── graph.py                ← LangGraph 파이프라인 조립
│   │   ├── state.py                ← DomainAnalysisState 스키마
│   │   ├── agent_cache.py          ← 에이전트 출력 캐시 유틸
│   │   └── nodes/                  ← 노드별 구현 파일
│   │       ├── query_intake_node.py
│   │       ├── human_review_node.py
│   │       ├── competitor_discovery_node.py
│   │       ├── domain_modeling_node.py
│   │       ├── normalize_competitor_ids_node.py
│   │       ├── competitor_selection_node.py
│   │       ├── official_source_resolver_node.py
│   │       ├── url_retry_node.py
│   │       ├── feature_url_mapper_node.py
│   │       └── feature_selection_node.py
│   ├── agents/                     ← 에이전트별 프롬프트·스키마
│   │   ├── query_intake/
│   │   │   ├── system_prompt_kr.md
│   │   │   └── output.schema.json
│   │   ├── competitor_discovery/
│   │   ├── domain_taxonomy/
│   │   └── feature_url_mapper/
│   ├── llm/
│   │   ├── claude_cli_analyzer.py  ← Claude Code CLI 호출 (기본 경로)
│   │   └── claude_api_analyzer.py  ← Claude API 호출 (temperature=0 전용)
│   └── utils/
│       └── slug.py                 ← ProductIdResolver (comp_* / func_* 슬러그)
├── data/
│   ├── cache/                      ← 에이전트 출력 캐시 (agent_id/hash.json)
│   ├── taxonomy/                   ← 도메인 taxonomy JSON (7일 TTL)
│   └── domains.json                ← 도메인명 → 정수 ID 레지스트리
├── docs/
│   ├── Design_Spec.md
│   └── Development_Roadmap.md
└── package.json
```

### 환경 변수

```bash
# server/.env
SERVER_PORT=4000
PYTHON_API_PORT=8000
YOUTUBE_API_KEY=...
ANTHROPIC_API_KEY=...         # Claude API (ProductIdResolver 전용)
BRAVE_SEARCH_API_KEY=...      # url_retry Phase 1 재탐색
USE_CLAUDE_CODE_CLI=true       # 기본 LLM 경로: Claude Code CLI

# client/.env
VITE_API_BASE_URL=http://localhost:4000
```

### 의존성 설치

```bash
# Python (서버)
pip install fastapi uvicorn langgraph anthropic

# Node.js (서버)
npm install express cors dotenv axios

# Node.js (클라이언트)
cd client && npm install axios lucide-react
```

---

## 2단계: JSON 캐시 저장 계층 구현 ✅

### 목표

- DB 대신 JSON 파일을 캐시 계층으로 사용한다.
- 에이전트 출력 캐시(`data/cache/`)와 taxonomy 캐시(`data/taxonomy/`)를 분리한다.

### 에이전트 캐시 (`data/cache/`)

- 키: `agent_id` + 입력 해시 + 컨텍스트 해시
- 유효기간: agent별 TTL 설정 가능
- 구현 모듈: `server/graph/agent_cache.py`
  - `make_cache_context()`: system_prompt + schema + 모델 기반 컨텍스트 해시 생성
  - `load_agent_output()`: 캐시 히트 시 JSON 반환
  - `store_agent_output()`: LLM 출력 저장

### Taxonomy 캐시 (`data/taxonomy/`)

- 파일명: `{domain_slug}.json`
- 유효기간: 7일 TTL
- `domain_modeling_node`가 캐시 히트 시 LLM 호출 없이 즉시 반환

### 완료 기준

- 동일 입력에 대해 두 번째 실행부터 LLM 호출 없이 캐시에서 결과가 반환되어야 한다.
- `data/domains.json`에 도메인명 → 정수 ID가 자동 누적되어야 한다.

---

## 3단계: LLM 호출 계층 구현 ✅

### 목표

- Claude Code CLI와 Claude API를 공통 인터페이스로 래핑한다.
- 두 경로 모두 동일한 JSON schema 강제 응답 방식을 사용한다.

### 실제 구현 결정 사항

> **원래 설계 대비 변경**: v1.0 로드맵은 Claude API를 기본 경로로 권고했으나,
> 실제 구현에서는 Claude Code CLI가 기본 경로로 채택되었다.
>
> | 경로 | 모듈 | 사용 조건 |
> |------|------|---------|
> | Claude Code CLI | `ClaudeCodeCliAnalyzer` | **기본 경로** — 대부분의 에이전트 LLM 호출 |
> | Claude API | `ClaudeApiAnalyzer` | temperature=0 필수 조건 시에만 사용 (ProductIdResolver 등) |

### 구현 모듈

- `server/llm/claude_cli_analyzer.py`: subprocess로 claude CLI 실행, stdout JSON 파싱
- `server/llm/claude_api_analyzer.py`: anthropic SDK 직접 호출, temperature 파라미터 지원
- `call_with_schema(prompt, output_schema)`: 두 경로 공통 인터페이스

### 완료 기준

- 동일한 `output_schema`에 대해 두 경로가 같은 JSON 구조로 결과를 반환해야 한다.
- CLI 타임아웃 및 API 에러는 `RuntimeError`로 정규화해 노드 레이어에 전달해야 한다.

---

## 4단계: QueryIntakeAgent 구현 ✅

### 목표

- 사용자가 입력한 짧은 검색어("토스 트래블카드")를 CompetitorDiscoveryAgent 입력 초안으로 변환한다.
- 이 노드가 없으면 사용자가 project_id, domain_name, problem_statement 등 전체 입력 구조를 직접 작성해야 하므로 UX가 크게 나빠진다.

### 구현 항목

- 입력: `state["raw_query"]`
- 출력: `state["query_intake_output"]` (QueryIntakeAgentOutput schema)
- 에이전트 파일: `server/agents/query_intake/`
  - `system_prompt_kr.md`, `output.schema.json`
- 에이전트 캐시 통합: 동일 raw_query 반복 시 LLM 호출 생략

### 완료 기준

- 짧은 검색어 하나로 CompetitorDiscoveryAgent 입력 초안(domain_name, own_product, problem_statement 등)이 자동 생성되어야 한다.

---

## 5단계: Human Review (interrupt #1) ✅

### 목표

- QueryIntakeAgent가 생성한 초안을 사용자가 검토·수정할 수 있도록 파이프라인을 일시 중단한다.
- 사용자가 승인하면 확정된 값을 state에 flat 필드로 분해해 이후 노드에 전달한다.

### 구현 항목

- `human_review_node.py`: LangGraph `interrupt()` 호출
- 프런트엔드: interrupt 값을 수신해 폼 렌더링, 수정 후 `resume()` 재개
- 확정 필드: `project_id`, `domain_name`, `own_product`, `problem_statement`, `target_user`, `core_value_props`, `geography`, `known_keywords`, `usage_context`, `business_constraints`

### 완료 기준

- 폼 수정 후 파이프라인이 재개되어 확정 필드가 state에 저장되어야 한다.

---

## 6단계: CompetitorDiscoveryAgent 구현 ✅

### 목표

- human_review에서 확정된 도메인 컨텍스트 기반으로 경쟁 후보를 식별한다.
- 브랜드 경쟁사(`competitor_candidates`)와 기능적 대안(`functional_competitors`)을 분리 출력한다.

### 실제 구현 결정 사항

> **원래 설계 대비 변경**: v1.0에는 없던 `FunctionalCompetitor` 타입이 추가되었다.
> 기능적 대안(예: "현지 ATM 현금 인출")은 `func_*` 접두사 candidate_id를 가진다.
> 이들은 competitor_selection_node에서 competitor_candidates와 함께 사용자에게 제시된다.

### 구현 항목

- 입력: `project_id`, `domain_name`, `own_product`, `problem_statement` 등 확정 필드
- 출력: `own_product_summary`, `competition_axes`, `competitor_candidates` (임시 ID), `functional_competitors`, `excluded_or_deferred`
- 에이전트 파일: `server/agents/competitor_discovery/`
- 에이전트 캐시 통합

### 완료 기준

- 브랜드 경쟁 후보 목록과 기능적 대안 목록이 분리되어 state에 저장되어야 한다.

---

## 7단계: DomainTaxonomyAgent 구현 ✅

### 목표

- 경쟁 분석 목적(purposes), 분석 항목(features), URL 유형(url_types)을 도메인별로 자동 생성·캐싱한다.
- FeatureUrlMapperAgent가 URL 수집 전략을 결정할 때 이 taxonomy를 참조한다.

### 실제 구현 결정 사항

> **원래 설계에 없던 신규 노드**: v1.0 로드맵에 없던 에이전트이다.
> 기존 설계에서는 분석 항목이 하드코딩되어 있었으나, 도메인 다양성 대응을 위해 동적 taxonomy 생성 방식으로 전환되었다.

### 3가지 실행 모드

| 모드 | 조건 | 동작 |
|------|------|------|
| `cache_hit` | taxonomy 파일 존재 & TTL(7일) 내 | LLM 호출 없이 즉시 반환 |
| `create` | taxonomy 파일 없음 | 전체 taxonomy 신규 생성 |
| `enrich` | competition_axes 미대응 비율 > 30% | 기존 taxonomy에 purpose 추가 보강 |

> **enrich 트리거 설명**: competition_axes(경쟁 구도 축) 중 기존 taxonomy의 active_purposes에 대응되지 않는 항목 비율이 30%를 초과하면 자동 보강 모드로 전환된다. 기존 taxonomy를 폐기하지 않고 부족한 purpose만 추가하는 방식이다.

### 구현 항목

- 입력: `domain_name`, `competition_axes`, `own_product`
- 출력: `domain_taxonomy` (domain_type, active_purposes, purpose_config)
- 캐시: `data/taxonomy/{domain_slug}.json` (7일 TTL)
- `data/domains.json`: 도메인명 → 정수 ID 레지스트리 (자동 누적)

### 완료 기준

- 동일 도메인 재실행 시 7일 이내라면 LLM 호출 없이 캐시에서 taxonomy가 반환되어야 한다.
- competition_axes 미대응 비율 > 30% 시 enrich 모드가 자동 트리거되어야 한다.

---

## 8단계: NormalizeCompetitorIds 노드 구현 ✅

### 목표

- LLM이 임시로 부여한 candidate_id를 결정론적 `comp_*` 슬러그로 교체한다.
- 상품명 표기 편차(예: "토스뱅크", "Toss Bank")를 공식 명칭으로 정규화한다.

### 실제 구현 결정 사항

> **원래 설계에 없던 신규 노드**: v1.0에는 candidate_id 정규화 단계가 없었다.
> LLM이 출력마다 다른 ID를 생성하는 문제를 해결하기 위해 추가되었다.
> `ProductIdResolver`는 Claude API를 temperature=0으로 호출하여 결정론적 출력을 보장한다.

### 구현 항목

- 입력: `state["competitor_candidates"]` (임시 candidate_id)
- 출력: `state["competitor_candidates"]` (comp_* 슬러그로 교체)
- `server/utils/slug.py`: `ProductIdResolver.resolve_comp(product_name)` → `(canonical_name, comp_* id)`
- 개별 후보 실패 시 파이프라인 중단 없이 errors에 누적

### 완료 기준

- 동일 상품명에 대해 반복 실행해도 같은 `comp_*` 슬러그가 반환되어야 한다.

---

## 9단계: CompetitorSelection (interrupt #2) ✅

### 목표

- 정규화된 경쟁 후보 목록(comp_* + func_*)을 사용자에게 제시하고 분석 대상을 선택받는다.

### 구현 항목

- `competitor_selection_node.py`: LangGraph `interrupt()` 호출
- 프런트엔드: 경쟁사 카드 UI, 1~10개 선택 인터페이스, `resume(selected_ids)` 재개
- 출력: `state["selected_competitor_ids"]` (comp_* / func_* 혼합 가능)

### 완료 기준

- 사용자가 선택한 경쟁사 ID 목록이 `selected_competitor_ids`에 저장되어 이후 노드에 전달되어야 한다.

---

## 10단계: OfficialSourceResolverAgent 구현 ✅

### 목표

- 자사 및 선택된 경쟁 후보의 공식 URL과 기능별 참조 URL을 탐색·검증한다.

### 구현 항목

- 입력: `own_product`, `selected_competitor_ids`, `domain_taxonomy`
- 출력: `domain_discovery_results`, `page_validation_results`, `official_sources`, `source_validation`
- 내부 단계: 공식 도메인 탐색 → 실제 페이지 검증 → URL 채택

### 완료 기준

- 자사 URL과 선택된 경쟁사별 공식 출처 URL이 검증 근거와 함께 저장되어야 한다.

---

## 11단계: URL Retry (Two-Phase interrupt #3) ✅

### 목표

- OfficialSourceResolverAgent 이후 미검증 URL에 대해 Two-Phase 재처리를 수행한다.
- 자사(own_*) URL이 끝내 검증 불가능하면 파이프라인을 강제 종료한다.

### Two-Phase 설계

**Phase 1 — 수동 URL 입력 또는 Brave Search 재탐색** (`interrupt #3`):
- 미검증 후보별 action_case 분기:
  - `None` (own_*): 사용자가 직접 URL 입력 필수
  - `case1` (comp_* 제거): 사용자 승인 시 분석 대상에서 제외
  - `case2_1` (func_* 일부 제거): 부분 제거 승인
  - `case2_2` (func_* 전체 제거): 전체 제거 승인
- Brave Search API로 URL 재탐색 후 HTTP 검증

**Phase 2 — critical_error 판정**:
- own_* URL이 Phase 2 종료 후에도 `validated=False`이면 `state["critical_error"]` 설정
- `_route_after_url_retry()` conditional edge: `critical_error` 있으면 → END, 없으면 → feature_url_mapper

### 완료 기준

- 모든 own_* URL이 검증되거나, 검증 불가 시 파이프라인이 명확한 오류 메시지와 함께 종료되어야 한다.

---

## 12단계: FeatureUrlMapperAgent 구현 ✅

### 목표

- domain_taxonomy 기반 purpose × feature × candidate URL 커버리지를 매핑한다.
- 각 분석 항목(feature)에 대해 후보별 URL 충족도를 `sufficient` / `partial` / `not_found`로 분류한다.

### 실제 구현 결정 사항

> **원래 설계에 없던 신규 노드**: v1.0에는 FeatureExtractionAgent가 URL 수집과 추출을 함께 담당했다.
> 실제 구현에서는 URL 수집·검증(FeatureUrlMapperAgent)과 내용 추출(FeatureExtractionAgent)을 분리했다.
> FeatureExtractionAgent는 FeatureUrlMapperAgent가 충분히 검증한 URL만 크롤링하므로 불필요한 HTTP 요청이 줄어든다.

### 3-Step 파이프라인

| Step | 작업 |
|------|------|
| Step 1 | HTTP 메타 수집: 기존 official_sources URL에 대해 HEAD/GET으로 title·status 수집 |
| Step 2 | LLM 매핑: feature × candidate 조합별 coverage 판정 + 추가 URL 후보 생성 |
| Step 3 | HTTP 검증: Step 2에서 생성된 additional_urls를 HTTP로 검증 (validated/http_status 업데이트) |

### 구현 항목

- 입력: `domain_taxonomy`, `official_sources`, `selected_competitor_ids`, `own_product`
- 출력: `analysis_features` (purpose_id, feature_id, feature_name, description, priority, candidate_coverage)
- `FEATURE_URL_MAPPER_PARALLEL`: ThreadPoolExecutor 동시 처리 수 설정 가능

### 완료 기준

- purpose × feature × candidate 조합별 URL 커버리지 매핑 결과가 `analysis_features`에 저장되어야 한다.

---

## 13단계: FeatureSelection (interrupt #4) ✅

### 목표

- 매핑된 analysis_features를 purpose 단위로 사용자에게 제시하고 이번 분석에 포함할 항목을 선택받는다.

### 구현 항목

- `feature_selection_node.py`: LangGraph `interrupt()` 호출
- 프런트엔드: purpose 그룹 토글 + 개별 feature 세부 조정 UI, `resume()` 재개
- 출력: `selected_purposes` (purpose_id 목록), `selected_feature_ids` (feat_* 목록)

### 완료 기준

- 사용자가 선택한 purpose와 feature_id 목록이 state에 저장되어 FeatureExtractionAgent의 필터로 사용되어야 한다.

---

## 14단계: FeatureExtractionAgent 구현 🔲

### 목표

- `selected_feature_ids`에 해당하는 feature만 대상으로 공식 URL을 크롤링해 기능·조건·혜택을 구조화한다.

### 구현 항목

- 입력: `analysis_features`, `selected_feature_ids`, `official_sources`
- 출력: `product_profiles`
- FeatureUrlMapperAgent가 검증한 URL만 크롤링 (불필요한 HTTP 요청 최소화)
- HTML 수집 또는 텍스트 추출 모듈
- 추출 프롬프트 설계

### 완료 기준

- 선택된 feature 범위 내에서 자사 1개 + 경쟁 상품 1개 이상의 구조화된 profile JSON이 생성되어야 한다.

---

## 15단계: FeatureComparisonAgent 구현 🔲

### 목표

- 추출된 product_profiles를 공통 schema로 정규화하고 기능 차이를 비교한다.

### 구현 항목

- 입력: `product_profiles`
- 출력: `normalized_features`, `feature_matrix`
- 도메인별 비교 schema 설계
- 값 정규화 규칙 (숫자/텍스트 혼합 처리)
- `official_gap_summary` 생성

### 완료 기준

- 자사 상품과 경쟁 상품의 공통 비교표와 gap summary가 생성되어야 한다.
- 알 수 없는 값은 `unknown` / `not_found` / `requires_manual_check`로 명확히 구분되어야 한다.

---

## 16단계: YouTubeQueryPlannerAgent 구현 🔲

### 목표

- 기능 비교 결과를 바탕으로 YouTube 검색어를 설계한다.

### 구현 항목

- 입력: `own_product`, `selected_competitor_ids`, `feature_matrix`
- 출력: `query_plan`
- 검색어 유형: 후기형 / 비교형 / 기능 쟁점형
- query_id와 query_slug 생성

### 완료 기준

- 자사 상품과 주요 경쟁 상품에 대한 검색어 계획이 JSON으로 저장되어야 한다.

---

## 17단계: YouTubeCollectionAgent 구현 🔲

### 목표

- 검색어별 상위 영상을 수집하고 댓글을 저장한다.

### 구현 항목

- 입력: `query_plan`
- 출력: `search_results`, `collected_videos`, `selected_comments`
- YouTube Data API v3 (`search.list`, `commentThreads.list`)
- 댓글 품질 선별 기준 적용

### 댓글 선별 기본 설정

```python
MAX_PAGES             = 5
MIN_VALID_COMMENTS    = 150
MAX_SELECTED_COMMENTS = 200
MIN_TEXT_LENGTH       = 15
```

### 완료 기준

- 검색어 1개에 대해 상위 9개 영상 목록과 영상별 선별 댓글 JSON이 생성되어야 한다.

---

## 18단계: ReactionAnalysisAgent 구현 🔲

### 목표

- 영상별 사용자 반응을 분석하고 검색어별 종합 인사이트를 만든다.

### 구현 항목

- 입력: `selected_comments`, `collected_videos`
- 출력: `query_insights`
- 긍정/부정 감성 분석 (합 100)
- 영상 1줄 요약, 댓글 반응 2~3줄 요약
- 검색어별 cross-video insight 생성

### 완료 기준

- 영상별 반응 분석과 검색어별 종합 인사이트가 JSON으로 저장되어야 한다.

---

## 19단계: InsightReportAgent 구현 🔲

### 목표

- 공식 기능 비교 결과와 YouTube 사용자 반응 결과를 통합해 최종 리포트를 생성한다.

### 구현 항목

- 입력: `feature_matrix`, `query_insights`, `selected_competitor_ids`
- 출력: `report_brief`, `final_report`
- 권장 리포트 섹션:
  1. 도메인 및 경쟁 구도 요약
  2. 공식 홈페이지 기준 기능 비교
  3. YouTube 사용자 반응 요약
  4. 통합 인사이트 및 개선 포인트

### 완료 기준

- 하나의 도메인에 대해 공식 비교와 YouTube 반응을 함께 반영한 리포트가 생성되어야 한다.

---

## 20단계: Web Prompt UI 구현 🔲

### 목표

- 사용자가 검색어를 입력하고 4개 interrupt 지점에서 상호작용하며 결과 리포트를 확인할 수 있는 UI를 구현한다.

### 구현 항목

- 검색어 입력 폼
- 파이프라인 진행 상태 표시 (10개 노드 step 로그)
- **interrupt #1**: QueryIntake 검토 폼
- **interrupt #2**: 경쟁사 선택 카드 UI
- **interrupt #3**: URL 실패 알림 + 수동 입력 필드
- **interrupt #4**: purpose 그룹 토글 + feature 세부 조정
- 공식 기능 비교 패널
- YouTube 반응 요약 패널
- 통합 리포트 패널

### 완료 기준

- 브라우저에서 검색어를 입력해 실행하고, 4개 interrupt 지점에서 상호작용한 뒤 최종 리포트를 확인할 수 있어야 한다.

---

## 21단계: 검증 및 다음 단계 🔲

### 검증 체크리스트

**구현 완료 (1~13단계)**:
- [x] 검색어 입력 시 QueryIntakeAgent 초안 자동 생성
- [x] interrupt #1: 폼 검토·수정 후 파이프라인 재개
- [x] 경쟁 후보 목록 (브랜드 + 기능적 대안) 분리 생성
- [x] 도메인 taxonomy 자동 생성 및 7일 캐시
- [x] comp_* 슬러그 결정론적 정규화
- [x] interrupt #2: 경쟁사 선택 후 재개
- [x] 자사/경쟁사 공식 URL 탐색·검증
- [x] interrupt #3: URL 실패 Two-Phase 재처리 및 critical_error 판정
- [x] purpose × feature × candidate URL 커버리지 매핑
- [x] interrupt #4: 분석 목적·항목 선택 후 재개

**미구현 (14~20단계)**:
- [ ] FeatureExtractionAgent
- [ ] FeatureComparisonAgent
- [ ] YouTubeQueryPlannerAgent
- [ ] YouTubeCollectionAgent
- [ ] ReactionAnalysisAgent
- [ ] InsightReportAgent
- [ ] Web Prompt UI (interrupt 포함 전체 인터랙션)

### 다음 단계

- FeatureExtractionAgent 구현 시작 (14단계)
- LangGraph graph.py에서 주석 처리된 노드 순서대로 활성화
- JSON → SQLite 마이그레이션 설계 (장기)
- 리포트 품질 평가 기준 수립

---

## 단계별 우선순위 요약

| 단계 | 내용 | 상태 |
|------|------|------|
| 1 | 환경 설정 및 디렉토리 구조 | ✅ 완료 |
| 2 | JSON 캐시 저장 계층 | ✅ 완료 |
| 3 | LLM 호출 계층 (CLI 기본 / API 보조) | ✅ 완료 |
| 4 | QueryIntakeAgent | ✅ 완료 |
| 5 | Human Review (interrupt #1) | ✅ 완료 |
| 6 | CompetitorDiscoveryAgent | ✅ 완료 |
| 7 | DomainTaxonomyAgent | ✅ 완료 |
| 8 | NormalizeCompetitorIds | ✅ 완료 |
| 9 | CompetitorSelection (interrupt #2) | ✅ 완료 |
| 10 | OfficialSourceResolverAgent | ✅ 완료 |
| 11 | URL Retry Two-Phase (interrupt #3) | ✅ 완료 |
| 12 | FeatureUrlMapperAgent | ✅ 완료 |
| 13 | FeatureSelection (interrupt #4) | ✅ 완료 |
| 14 | FeatureExtractionAgent | 🔲 미구현 |
| 15 | FeatureComparisonAgent | 🔲 미구현 |
| 16 | YouTubeQueryPlannerAgent | 🔲 미구현 |
| 17 | YouTubeCollectionAgent | 🔲 미구현 |
| 18 | ReactionAnalysisAgent | 🔲 미구현 |
| 19 | InsightReportAgent | 🔲 미구현 |
| 20 | Web Prompt UI (전체 인터랙션) | 🔲 미구현 |
| 21 | 검증 및 확장 준비 | 🔲 미구현 |
