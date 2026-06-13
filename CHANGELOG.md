본 프로젝트의 모든 주목할 만한 변경 사항은 이 파일에 기록합니다.

형식은 [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/)을 따르며,
버전 관리는 [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html)을 준수합니다.

각 항목은 스코프 태그를 접두하여 영향 영역을 표시합니다:
- `(server)` FastAPI/LangGraph 계층
- `(orchestrator)` Express.js 계층 ·
- `(client)` React(Vite) 계층
- `(agents)` 에이전트 프롬프트/스키마
- `(data)` Taxonomy 등 데이터 자산
- `(docs)` 설계·README 문서
- `(build)` 빌드/리포지토리 설정
- `(test)` 테스트 자산.

> 비고: v0.2.0까지는 `main` 브랜치 직접 commit 방식으로 운용되어 commit 이력을 의미 단위로 재구성하여 기재하였습니다. v0.3.0부터는 PR 단위로 변경을 기록합니다.

## [Unreleased]

### Added
- **(docs)** known_domains.json 역할 정의 및 준자동 학습 파이프라인 결정 기록 문서 추가 — `docs/design/known_domains_role_and_promotion_pipeline.md` (`14566ea`)
- **(docs)** known_domains 문서에 기대 효과(§4-7)와 향후 구현 절차(§4-8) 절 보강 (`9d8edda`)

### Changed
- (해당 사항 없음)

### Fixed
- (해당 사항 없음)

## [0.3.0] - 2026-05-30

`DomainTaxonomyAgent`와 `feature_url_mapper` 등 핵심 노드의 입출력 스키마를 `report_config` 7종 enum 구조로 통일하고, LangGraph 파이프라인을 fan-out + 4단계 분리 구조로 재구성한 v0.10 마이그레이션 릴리스입니다. 입출력 스키마 변경 3건이 **하위 호환을 깨는 변경**에 해당하며, 캐시·timeout·UI 진행 표시 전반의 안정성과 응답성이 향상되었습니다.

### Added
- **(docs)** 7개 분석 리포트(비교 매트릭스·고객 반응 인사이트·마케팅 소셜·배틀카드·포지셔닝 맵·시장 컨텍스트 SWOT·임원 요약) 통합 Rubric 및 7종 참조 문서·7종 worked example 추가 (`740a8e2`)
  - 기존: 후속 리포트 노드 구현 시 표준 카테고리·평가 기준이 부재하여 노드별 프롬프트 작성 시마다 카테고리를 재정의해야 했음
  - 효과: 노드별 프롬프트 작성 시 Rubric §2-x 카테고리를 직접 참조 가능. 노드당 약 3–5시간 절감 가능 (7개 노드 합산 약 21–35시간 절감 전망)
- **(build)** Rubric → system_prompt 자동 inline 인용 빌드 스크립트 + LLM-as-judge 평가 스캐폴드 추가 (`d9d4e26`)
  - 기존: Rubric 문서 변경 시 7개 agents의 system_prompt를 수동 동기화 필요 (변경당 약 30분)
  - 효과: `python scripts/build_prompts.py` 1회 실행으로 자동 갱신. 수동 동기화 인적 시간 0
- **(server)** 7개 분석 리포트 노드 스켈레톤 및 공통 helper(`_report_node_common.py`) + 7개 리포트 에이전트 placeholder 추가 (`642a80a`)
  - 기존: 후속 분석 노드 구현 시 read/write keys·envelope·skip/error 보일러플레이트를 매 노드마다 약 200줄씩 재작성 예정
  - 효과: 공통 helper 도입으로 노드당 약 200줄 × 7 = 약 1,400줄 보일러플레이트 사전 제거. LLM 호출 본체만 추가하면 즉시 동작
- **(server)** `feature_url_mapper`를 `url_discovery_brave` → `page_meta_collect` → `feature_mapping_llm` → `additional_urls_validation` 4단계 노드로 분리 (`b3ffca2`)
  - 기존: 단일 노드에서 Brave 검색·메타 수집·LLM 매핑·HTTP 검증을 모두 처리하여 1개 단계라도 실패하면 전체 재실행 (평균 60–180s 손실)
  - 효과: 단계별 timeout·cache 격리로 실패 단계만 재시도. 평균 30–60s 절감/실패당
- **(server)** `agent_cache.load_agent_output`에 `ttl_hours` 옵션 추가 및 `FEATURE_MAPPING_LLM_TIMEOUT` 환경변수 신설 (`6e5380a`)
  - 기존: `CLI_TIMEOUT=120s`가 모든 LLM 호출에 일률 적용되어 입출력 13–24K 토큰 규모의 `feature_mapping_llm` 호출이 빈번히 timeout
  - 효과: 무거운 LLM 호출 노드만 별도 timeout 마진 부여 가능 (`CLI_TIMEOUT` 300s 상향 + `FEATURE_MAPPING_LLM_TIMEOUT` 독립 override). URL 검증 캐시는 24h TTL로 자연 만료되어 신선도·비용 균형 확보
- **(server, client)** 분기·4단계 진행 이벤트 emit + `CompetitorSelectionPage`의 진행 UI 4단계 확장 (`835aecc`)
  - 기존: 단일 stage만 emit되어 `feature_mapping` 단계에서 60–120s 동안 진행 상황 불투명
  - 효과: `branches` dict + 4종 stage(`feature_mapping_brave`/`meta`/`llm`/`validate`) 분리 emit으로 각 단계 실시간 가시화, 체감 응답성 향상

### Changed
- **(server)** `domain_modeling_node` 단일 호출 모드로 재작성 — `_decide_mode`·`_needs_enrichment`·`ENRICHMENT_TRIGGER_THRESHOLD` 폐기, `analysis_direction` 입력 수신, 노드 코드 623줄 → 442줄 (-29%) (`7bcfb0c`)
  - 기존: 1차(`competition_axes` 없이) + 2차(enrichment) 분리 호출로 도메인당 LLM 2회 호출. 캐시 키가 phase·threshold에 의존하여 동일 도메인 재실행 시에도 부분 미스
  - 효과: 단일 호출로 전환하여 도메인당 wall-clock 8–15s + 입력 토큰 약 3–4K 절감. 캐시 키가 도메인 단위로 안정화되어 사용자가 다른 경쟁사 부분집합을 선택해도 캐시 100% 적중
- **(agents)** `domain_modeling` `output.schema.json`을 `report_config` 7종 enum 구조로 재작성 — `active_purposes`·`purpose_config`·`url_types`·`url_type_priority` 4개 필드 폐기, `reportEntry $defs` 신설(`label`·`active`·`features`·`feature_labels`·`categories`·`search_query_hints`·`aspect_codebook`·`action_lens`), `additionalProperties: false` 강제. `system_prompt_kr.md` 전면 재작성 (`10338f9`) — **BREAKING**: DomainTaxonomyAgent 출력 스키마가 v0.6 → v0.10으로 비호환 변경. 기존 캐시(`data/cache/domain_modeling/*.json`)는 input 해시 변경으로 자연 미스 처리됨
  - 기존: `active_purposes` + `purpose_config` 중간 추상화 layer + `url_types` 사전 추측으로 LLM 출력 토큰이 도메인당 약 800–1,300 토큰 추가 발생
  - 효과: 중간 layer 폐기로 출력 토큰 절감 + `search_query_hints` 신설로 `feature_url_mapper`가 Brave 검색 쿼리를 결정론적으로 구성하여 동일 도메인 재실행 시 검색 결과 변동 흡수
- **(agents)** `feature_url_mapper` 에이전트 자산을 `report_config` 단위로 갱신 — `input.schema`에 `active_reports`·`origin`·`matched_report_types` 필드 신설, `output.schema`의 `report_type` 필드를 7종 enum으로 명시 (`829b45c`) — **BREAKING**: 에이전트 입출력 스키마 비호환. 기존 캐시(`data/cache/feature_url_mapper/*.json`)는 자연 미스 처리됨
  - 기존: `active_purposes` 입력 구조로는 `report_type`별 LLM 입력 분리가 불가능하여 7개 리포트 호출 시 동일 candidate URL이 최대 6회 중복 전송
  - 효과: `origin`·`matched_report_types` 메타데이터로 report_type별 입력 슬림화 가능 (후속 노드에서 candidate 영역 토큰 50–60% 절감 활용)
- **(server)** LangGraph 파이프라인을 `competitor_discovery → {normalize_competitor_ids, domain_modeling}` fan-out + `add_edge(["url_retry", "domain_modeling"], "ab_join")` list-fan-in barrier 구조로 재구성. `state.py`에 `report_outputs`·`feature_pool` + 4단계 노드 브릿지 키 3종 신설, 폐기 키(`query_insights`·`report_brief`) 제거 (`3c72010`)
  - 기존: `domain_modeling`이 `normalize_competitor_ids` 이후 직렬 실행되어 interrupt #2·#3 대기 시간(평균 30–90s)이 누적
  - 효과: fan-out으로 `domain_modeling`이 interrupt 대기 시간 + `official_source_resolver`·`url_retry` 진행 시간을 흡수. 전체 wall-clock 약 30–90s 단축/도메인당
- **(server, client)** `feature_selection`을 `report_config` 키 체계로 정합 갱신 — 노드 측 `purpose_id` → `report_type`, `purpose_config` → `report_config` 전환, interrupt 페이로드를 `reports: [{report_type, report_label, features: [...]}]`로 변경. UI 측 `PurposeSection` → `ReportSection` 개명 동기화 (`c706b34`) — **BREAKING**: feature_selection 노드의 interrupt 페이로드 구조 비호환 변경
  - 기존: v0.10 마이그레이션에서 server는 `reports`로 전환했으나 client는 옛 `purposes` 키 참조로 "분석 항목 0/0개"의 빈 표시 회귀 발생
  - 효과: server ↔ client 키 통일로 dead-link 제거, 정상 렌더링 회복. `selected_purposes`는 호환 키로 보존되어 진행 중 thread 일부 보호
- **(data)** 도메인 taxonomy 데이터를 `report_config` 구조로 마이그레이션 — 토스 트래블카드(domain_id=3) 재작성, domain_id=4 신규 도메인 추가, `domains.json` 레지스트리 갱신 (`b7db59b`)
  - 기존: 옛 `active_purposes` 구조 taxonomy 캐시는 v0.10 schema와 호환 불가로 매 실행마다 LLM 재호출 발생
  - 효과: 신규 구조로 미리 채워 캐시 hit 보장, 도메인당 LLM 호출 1회(약 4K 토큰) 절감
- **(docs)** `Design_Spec.md`·`Development_Roadmap.md`를 현행 파이프라인 구조에 맞춰 갱신 (`fc67aa9`)

### Fixed
- **(orchestrator)** Express `analysisRouter`에 undici `Agent` 장기 타임아웃 dispatcher 적용 — `PYTHON_INVOKE_TIMEOUT_MS` 기본 30분, `/invoke` 호출에만 dispatcher 부착 (`681f884`)
  - 기존: Node.js native fetch(undici 기반)의 기본 `headersTimeout`·`bodyTimeout` 300s가 Python `invoke` 동기 호출 wall-clock(평균 5분 이상)을 초과하여 `fetch failed` race 발생
  - 효과: dispatcher 분리로 30분 wall-clock 흡수, race 차단. 분석 1회당 race 재실행 시간(평균 2–5분) 제거

## [0.2.0] - 2026-05-14

LLM 단독 추론에 의존하던 공식 URL 탐색 흐름을 Brave Search API + LLM 검증의
4단계 파이프라인으로 재설계하였습니다. URL 탐색·검증의 신뢰도와 재현성을
크게 끌어올린 릴리스이며, `OfficialSourceResolver` 에이전트의 역할 정의가
바뀌므로 **하위 호환을 깨는 변경**을 포함합니다.

### Added
- **(server)** URL 탐색·검증 전용 캐시 모듈 신규 추가 — Brave 호출과 LLM 검증
  결과를 입력 해시 기반으로 캐싱하여 반복 호출 비용 절감 (`008e39b`)
- **(server, client, orchestrator)** Candidate별 실시간 진행 이벤트(C-1) 도입 —
  서버 SSE 송신, 오케스트레이터 패스스루, UI 단계 흐름 시각화 (`d1dff41`)
- **(docs)** OfficialSourceResolver 재설계 문서 추가 (`9d3ad5c`)

### Changed
- **(server)** `official_source_resolver_node`를 Brave + LLM 검증 4단계
  파이프라인으로 전면 재작성 (`036f224`) — **BREAKING**: 노드 출력 스키마와
  중간 상태 키 변경
- **(agents)** OfficialSourceResolver 에이전트 자산을 Validator 역할로 교체 —
  `system_prompt_kr.md`, `output.schema.json` 갱신 (`9d3ad5c`) — **BREAKING**:
  기존 Resolver 프롬프트와 출력 형식과 호환되지 않음
- **(server)** `url_retry_node`의 auto-bypass 경로를 Brave + LLM 검증 흐름으로
  통합 — 재시도 시에도 동일 검증 게이트 통과 보장 (`550e668`)
- **(server)** `competitor_discovery` 캐시 키 안정화 — `system_prompt + schema +
  model` 해시를 키에 포함시켜 프롬프트·스키마·모델 변경 시 자동 무효화
  (`d0ceb06`)
- **(data)** 도메인 ID 3(토스 트래블카드) Taxonomy를 v17로 갱신 (`eecf774`)
- **(build)** `.gitignore`에 리팩터링 백업 디렉터리 및 작업 메모 패턴 추가
  (`ecb21d0`)

## [0.1.1] - 2026-05-07

초기 릴리스 직후의 문서·메타데이터 정비 릴리스입니다. 기능 변경은 없습니다.

### Changed
- **(docs)** README를 경쟁 인텔리전스 플랫폼 정체성에 맞춰 전면 개편 — 아키텍처
  개요, 노드 흐름, 실행 가이드를 신규 작성 (`03b1c57`)
- **(docs)** 설계 문서 머리말 인용문을 bullet list 형식으로 통일 (`555cb88`)
- **(build)** `.gitignore`에 `CLAUDE.md` 추가 — 개발자 로컬 지침이 원격에
  커밋되지 않도록 차단 (`5094f21`)

### Removed
- **(docs)** 설계 문서 머리말 아래 수평 구분선(`---`) 제거 — 가독성 정리
  (`bb6e247`)

## [0.1.0] - 2026-05-06

경쟁 인텔리전스 플랫폼의 최초 동작 가능 버전입니다. React 클라이언트 → Express
오케스트레이터 → FastAPI/LangGraph 백엔드의 3계층 구조와, `query_intake`부터
`feature_selection`까지 10개 노드(4개 interrupt 포함)의 파이프라인이
구축되었습니다.

### Added
- **(docs)** 프로젝트 초기 셋업 — `Design_Spec.md`, `Development_Roadmap.md`
  등 설계 문서 추가 (`836e0fa`)
- **(server)** 백엔드 공통 인프라 구축 — `config.py`, `DomainAnalysisState`
  TypedDict, Claude Code CLI/Claude API 듀얼 LLM 어댑터, 캐시 유틸리티 등
  (`b2047a0`)
- **(agents, data)** 에이전트 프롬프트·스키마 및 도메인 Taxonomy 자산 초기 구축
  — `agents/<agent_id>/system_prompt_kr.md` + `output.schema.json` 표준 적용
  (`fb86325`)
- **(server)** LangGraph `StateGraph` 구성 및 10개 노드 구현 —
  `query_intake` → `human_review`(interrupt#1) → `competitor_discovery` →
  `domain_modeling` → `normalize_competitor_ids` →
  `competitor_selection`(interrupt#2) → `official_source_resolver` →
  `url_retry`(interrupt#3) → `feature_url_mapper` →
  `feature_selection`(interrupt#4) (`f9f8263`)
- **(orchestrator)** Express.js 오케스트레이터 계층 구현 — `localhost:4000`에서
  React 클라이언트와 FastAPI(`localhost:8000`)를 중계 (`0785336`)
- **(client)** React(Vite) 프런트엔드 클라이언트 구현 — 4개 interrupt에 대응하는
  Human-in-the-loop UI 포함 (`bdb8d18`)
- **(test)** 에이전트 품질 및 URL 검증 통합 테스트 자산 추가 —
  `test_QI_CD_agent_quality.py`, `test_url_validator.py` (`9880946`)

[Unreleased]: https://github.com/Adn-thkim/competitive-intel/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/Adn-thkim/competitive-intel/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Adn-thkim/competitive-intel/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/Adn-thkim/competitive-intel/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/Adn-thkim/competitive-intel/releases/tag/v0.1.0
