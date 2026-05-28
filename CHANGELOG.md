# Changelog

본 프로젝트의 모든 주목할 만한 변경 사항은 이 파일에 기록합니다.

형식은 [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/)을 따르며,
버전 관리는 [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html)을 준수합니다.

각 항목은 스코프 태그를 접두하여 영향 영역을 표시합니다:
`(server)` FastAPI/LangGraph 계층 · `(orchestrator)` Express.js 계층 ·
`(client)` React(Vite) 계층 · `(agents)` 에이전트 프롬프트/스키마 ·
`(data)` Taxonomy 등 데이터 자산 · `(docs)` 설계·README 문서 ·
`(build)` 빌드/리포지토리 설정 · `(test)` 테스트 자산.

> 비고: 본 저장소는 `main` 브랜치 직접 commit 방식으로 운용되며, commit 이력을 의미 단위로 재구성하여 기재하였습니다.

## [Unreleased]

### Added
- (해당 사항 없음)

### Changed
- (해당 사항 없음)

### Fixed
- (해당 사항 없음)

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

[Unreleased]: https://github.com/Adn-thkim/competitive-intel/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/Adn-thkim/competitive-intel/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/Adn-thkim/competitive-intel/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/Adn-thkim/competitive-intel/releases/tag/v0.1.0
