# 경쟁 인텔리전스 플랫폼 (Competitive Intelligence Platform)

검색어 한 줄을 입력하면 경쟁사 식별 → 공식 기능 비교 → YouTube 사용자 반응 분석 →
인사이트 리포트까지 자동으로 생성하는 **에이전트 기반 경쟁 인텔리전스 플랫폼**입니다.

> 현재 파일럿 도메인: `토스 트래블카드`

---

## 1. 해결하고자 하는 문제

기획자·마케터·전략 담당자가 신규 상품을 설계하거나 포지셔닝을 점검할 때 반복적으로 부딪히는
경쟁 분석은 다음과 같은 구조적 한계를 가지고 있습니다.

- **경쟁사 식별의 비일관성**: 누가 진짜 경쟁자인지(브랜드 vs 기능적 대안)에 대한 기준이 사람마다
  달라, 분석 시작점부터 결과가 흔들립니다.
- **공식 정보 수집의 비효율**: 경쟁사 홈페이지 URL을 수동으로 찾아 기능을 정리하는 데
  분석 시간의 절반 이상이 소비됩니다.
- **사용자 반응 데이터의 단절**: 공식 스펙 비교는 가능해도, 실제 사용자가 어떻게 느끼는지
  (불만, 만족, 대안 선호)는 별도 채널에서 별도 작업으로 수집해야 합니다.
- **재현 가능성 부족**: 동일한 검색어로 다시 분석해도 결과 구성이 달라져, 기간별 비교나
  팀 간 공유가 어렵습니다.

이 프로젝트는 위 네 가지 문제를 **단일 입력(검색어) → 단일 산출물(인사이트 리포트)** 의
파이프라인으로 자동화하는 것을 목표로 합니다.

---

## 2. 왜 이 방법을 선택했는지

### 2-1. LangGraph 기반 멀티 에이전트 오케스트레이션

분석 워크플로는 단일 LLM 호출로 끝나지 않고, **순차 의존성**(경쟁사 확정 → URL 탐색 →
기능 추출)과 **재시도 분기**(URL 검증 실패 → 사용자 개입 또는 Brave 재탐색)를 포함합니다.
LangGraph의 StateGraph는 이러한 흐름을 노드/엣지/조건부 분기로 명시적으로 표현할 수 있어,
파이프라인의 어느 단계에서 실패했는지 추적하고 부분 재실행하기에 적합합니다.

### 2-2. Human-in-the-loop interrupt 패턴

완전 자동화는 잘못된 경쟁사 후보가 끼어들었을 때 이후 단계 전체를 오염시킵니다. 본 시스템은
네 개의 핵심 지점(분석 입력 검토, 경쟁사 선택, URL 재시도, 분석 항목 선택)에서
LangGraph `interrupt()` 를 호출해 사용자의 최종 판단을 받습니다. 이는 자동화의 속도와
도메인 전문가의 판단력을 결합하기 위한 의도적 선택입니다.

### 2-3. 캐시 우선 LLM 호출 계층

도메인 taxonomy와 에이전트 출력은 입력 해시 기반으로 JSON 캐시(`data/cache/`,
`data/taxonomy/`)에 저장됩니다. 동일 도메인을 반복 분석할 때 LLM 비용과 지연을 줄이고,
결과의 재현 가능성을 확보하기 위함입니다.

### 2-4. 결정론이 필요한 곳만 Claude API, 나머지는 Claude Code CLI

대부분의 에이전트는 Claude Code CLI(`ClaudeCodeCliAnalyzer`)를 통해 호출하지만,
경쟁사 ID 정규화처럼 동일 입력에 동일 출력이 보장되어야 하는 경로는 `temperature=0` 의
Claude API(`ClaudeApiAnalyzer`)를 사용합니다. 비용과 결정성 사이의 균형을 의도적으로
설계했습니다.

---

## 3. 이 프로젝트가 제공하는 가치

| 사용자 | 제공 가치 |
|--------|-----------|
| 상품 기획자 | 검색어 입력 한 번으로 경쟁사 후보, 공식 기능 비교 매트릭스, 사용자 반응 요약을 동시에 확보 |
| 마케터 | YouTube 댓글 기반의 실제 사용자 어휘와 페인 포인트를 카피·메시지에 반영 가능 |
| 전략·리서치 담당 | 동일 검색어 기준의 재현 가능한 분석으로 분기별 추적과 의사결정 근거 확보 |
| 개발 조직 | LangGraph 노드 단위로 모듈화되어 도메인 확장(카드 → 보험 → 통신) 시 재사용 가능 |

핵심 가치는 다음 세 가지로 요약됩니다.

1. **시간 단축**: 수작업 경쟁 분석 1\~2일을 단일 파이프라인 실행으로 압축합니다.
2. **데이터 통합**: 공식 스펙과 사용자 반응을 동일한 ID 네임스페이스(`comp_*`, `feat_*`)로
   연결해 단절을 제거합니다.
3. **재현 가능성**: 캐시·스키마 강제·결정론적 ID 정규화로 동일 입력 시 동일 결과를 보장합니다.

---

## 4. 스케치 및 와이어프레임

> 와이어프레임 작성이 완료되면 이 섹션에 이미지를 배치합니다.

### 4-1. 사용자 플로우 와이어프레임

<!-- TODO: docs/wireframes/user-flow.png 추가 -->
_(작성 예정)_

### 4-2. 결과 리포트 화면 스케치

<!-- TODO: docs/wireframes/report-screen.png 추가 -->
_(작성 예정)_

### 4-3. Human-in-the-loop 검토 UI 스케치

<!-- TODO: docs/wireframes/hitl-review.png 추가 -->
_(작성 예정)_

---

## 5. 시스템 아키텍처

```text
React (Vite) Client
        ↕ REST (localhost:4000)
Express.js Orchestrator
        ↕ HTTP (localhost:8000)
Python FastAPI (uvicorn)
        └── LangGraph StateGraph
            ├─ 1. query_intake
            ├─ 2. human_review              ← interrupt #1
            ├─ 3. competitor_discovery
            ├─ 4. domain_modeling
            ├─ 5. normalize_competitor_ids
            ├─ 6. competitor_selection      ← interrupt #2
            ├─ 7. official_source_resolver
            ├─ 8. url_retry                 ← interrupt #3
            ├─ 9. feature_url_mapper
            ├─10. feature_selection         ← interrupt #4
            ├─11. feature_extraction        (TODO)
            ├─12. feature_comparison        (TODO)
            ├─13. youtube_query_planner     (TODO)
            ├─14. youtube_collection        (TODO)
            ├─15. reaction_analysis         (TODO)
            └─16. insight_report            (TODO)
```

상세 다이어그램은 [`docs/diagrams/pipeline.mmd`](../docs/diagrams/pipeline.mmd) 를 참고하십시오.

---

## 6. 기술 스택

| 레이어 | 기술 |
|--------|------|
| Frontend | React 18 + Vite + Tailwind CSS |
| Orchestrator | Node.js 20+ / Express.js (포트 4000) |
| Agent Runtime | Python 3.11+ / FastAPI / uvicorn (포트 8000) |
| Workflow Engine | LangGraph (StateGraph + MemorySaver) |
| LLM | Claude (Code CLI 기본 / API `temperature=0` 결정론 경로) |
| External APIs | YouTube Data API v3, Brave Search API |
| Caching | JSON 파일 캐시 (`data/cache/`, `data/taxonomy/` · TTL 7일) |
| Future Storage | SQLite (영구 저장 단계 도입 예정) |

---

## 7. 프로젝트 구조

```
competitive-intel/
├── client/                          ← React (Vite) 클라이언트
├── server/
│   ├── index.js                     ← Express 오케스트레이터 진입점
│   ├── agents/                      ← 에이전트별 system_prompt + output.schema
│   ├── graph/
│   │   ├── state.py                 ← DomainAnalysisState (TypedDict)
│   │   └── nodes/
│   │       ├── query_intake_node.py
│   │       ├── competitor_discovery_node.py
│   │       └── ...
│   └── llm/
│       ├── claude_cli_analyzer.py   ← 기본 경로
│       └── claude_api_analyzer.py   ← 결정론 경로
├── data/
│   ├── cache/                       ← 에이전트 출력 캐시 (입력 해시 키)
│   └── taxonomy/                    ← 도메인 taxonomy 캐시
├── docs/
│   ├── Design_Spec.md               ← 설계 전체
│   └── Development_Roadmap.md       ← 단계별 구현 순서
├── package.json
└── requirements.txt
```

---

## 8. 시작하기

### 8-1. 사전 요구사항

```bash
node --version          # v20 이상
python --version        # 3.11 이상
```

### 8-2. 의존성 설치

```bash
# Node 측 (오케스트레이터 + 클라이언트)
npm install
cd client && npm install && cd ..

# Python 측 (LangGraph 파이프라인)
pip install -r requirements.txt
```

### 8-3. 환경 변수

`.env.example` 을 복사한 뒤 필요한 키를 채워 넣습니다.

```bash
cp .env.example .env
```

| 키 | 용도 |
|-----|------|
| `ANTHROPIC_API_KEY` | Claude API (결정론 경로) |
| `YOUTUBE_API_KEY` | YouTube Data API v3 |
| `BRAVE_SEARCH_API_KEY` | URL 재탐색 fallback |

> API 키는 코드에 직접 노출하지 말고, 반드시 `config.py` 의 상수를 통해 접근하십시오.

### 8-4. 실행

```bash
# Python LangGraph 서버
uvicorn server.api:app --reload --port 8000

# Express 오케스트레이터
npm run dev

# (별도 터미널) React 클라이언트
cd client && npm run dev
```

---

## 9. 핵심 설계 원칙 요약

상세 내용은 [`docs/Design_Spec.md`](docs/Design_Spec.md) 를 참고하시고, 본 README에서는
구현 시 반드시 지켜야 할 규칙만 요약합니다.

- 공유 상태는 `DomainAnalysisState` TypedDict 한 곳에서만 관리합니다.
- 누적 필드(`agent_steps`, `errors`)는 `Annotated[list, operator.add]` 리듀서를 사용합니다.
- 모든 LLM 호출은 `output_schema` 를 강제하고 `call_with_schema(prompt, schema)` 를 사용합니다.
- 노드 단위 `try-except` 로 개별 실패가 파이프라인 전체를 중단시키지 않도록 합니다.
- 비치명적 오류는 `errors` 누적 필드에 `{node, error, timestamp}` 형식으로 기록합니다.
- ID 네임스페이스: 자사 `own_*`, 브랜드 경쟁사 `comp_*`, 기능적 대안 `func_*`, 분석 항목 `feat_*`.

---

## 10. 로드맵

| 단계 | 상태 |
|------|:----:|
| 검색어 입력 → 분석 입력 초안 자동 생성 | 완료 |
| 도메인 taxonomy 자동 생성·캐시 | 완료 |
| 경쟁사·기능적 대안 식별 + ID 정규화 | 완료 |
| 공식 홈페이지 URL 탐색·검증 + 재시도 | 완료 |
| feature × URL 커버리지 매핑 + 사용자 선택 | 완료 |
| 기능 추출 / 비교 매트릭스 생성 | 진행 예정 |
| YouTube 검색어 설계 / 영상·댓글 수집 | 진행 예정 |
| 사용자 반응 분석 + 통합 인사이트 리포트 | 진행 예정 |
| SQLite 영구 저장 + Excel/PDF 리포트 출력 | 장기 |

---

## 11. 문서

- 설계 전체: [`docs/Design_Spec.md`](docs/Design_Spec.md)
- 단계별 구현 순서: [`docs/Development_Roadmap.md`](docs/Development_Roadmap.md)
- 파이프라인 다이어그램: [`docs/diagrams/pipeline.mmd`](../docs/diagrams/pipeline.mmd)

---

## 12. 라이선스 및 사용 범위

본 저장소는 내부 파일럿 단계의 코드입니다. 외부 배포 전, 프롬프트·스키마·캐시 데이터의
민감 정보 여부를 확인한 뒤 라이선스를 확정합니다.
