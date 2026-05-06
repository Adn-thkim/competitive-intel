# QueryIntakeAgent 스키마 참조

이 문서는 `QueryIntakeAgent`의 `input.schema.json`, `output.schema.json`에 있는 필드의 의미와 예시값을 빠르게 확인하기 위한 참고 문서입니다.

## Input Schema

| 필드 | 타입 | 설명 | 예시값 |
| --- | --- | --- | --- |
| `request_id` | string | 웹 요청 또는 세션 내 입력 요청을 식별하는 ID | `req_20260424_001` |
| `raw_query` | string | 사용자가 검색창에 직접 입력한 원문 검색어 | `토스 트래블카드` |
| `geography_hint` | string | 기본 시장 또는 국가 추론에 사용할 힌트 | `KR` |
| `locale` | string | UI 언어 또는 사용자 로케일 정보 | `ko-KR` |
| `ui_context` | object | 어떤 화면/진입점에서 검색이 발생했는지 나타내는 정보 | `{ "entrypoint": "main_search", "page": "home" }` |
| `ui_context.entrypoint` | string | 검색이 시작된 UI 진입점 식별자 | `main_search` |
| `ui_context.page` | string | 현재 페이지 또는 화면 이름 | `home` |
| `known_context` | object | 서버나 UI가 이미 알고 있는 보조 힌트 | `{ "brand_hint": "토스", "category_hint": "travel card" }` |
| `known_context.brand_hint` | string | 브랜드 추론 보조 힌트 | `토스` |
| `known_context.product_hint` | string | 상품명 추론 보조 힌트 | `토스 트래블카드` |
| `known_context.category_hint` | string | 카테고리 추론 보조 힌트 | `travel payment card` |

### Input 예시

```json
{
  "request_id": "req_20260424_001",
  "raw_query": "토스 트래블카드",
  "geography_hint": "KR",
  "locale": "ko-KR",
  "ui_context": {
    "entrypoint": "main_search",
    "page": "home"
  },
  "known_context": {
    "brand_hint": "토스",
    "category_hint": "travel payment card"
  }
}
```

## Output Schema

| 필드 | 타입 | 설명 | 예시값 |
| --- | --- | --- | --- |
| `run_id` | string | 이번 agent 실행을 식별하는 ID | `run_20260424_001` |
| `request_id` | string | 입력 요청 ID | `req_20260424_001` |
| `raw_query` | string | 원본 검색어 | `토스 트래블카드` |
| `draft_competitor_discovery_input` | object | `CompetitorDiscoveryAgent`에 넘길 검토용 초안 입력 | `{ "project_id": "proj_toss_travelcard", "domain_name": "해외 결제/환전 특화 카드", ... }` |
| `draft_competitor_discovery_input.project_id` | string | 후속 분석에 사용할 프로젝트 ID 초안 | `proj_toss_travelcard` |
| `draft_competitor_discovery_input.domain_name` | string | 분석 도메인명 초안 | `해외 결제/환전 특화 카드` |
| `draft_competitor_discovery_input.own_product` | object | 자사 상품 기본 정보 초안 | `{ "brand": "토스", "name": "토스 트래블카드", "category": "travel payment card" }` |
| `draft_competitor_discovery_input.own_product.brand` | string | 자사 상품 브랜드명 초안 | `토스` |
| `draft_competitor_discovery_input.own_product.name` | string | 자사 상품명 초안 | `토스 트래블카드` |
| `draft_competitor_discovery_input.own_product.category` | string | 자사 상품 카테고리 초안 | `travel payment card` |
| `draft_competitor_discovery_input.problem_statement` | string | 사용자가 해결하려는 문제에 대한 초안 문장 | `해외여행 시 환전과 결제를 간편하고 유리하게 처리하고 싶다` |
| `draft_competitor_discovery_input.target_user` | string[] | 핵심 사용자군 초안 | `["해외여행자", "단기 출장자"]` |
| `draft_competitor_discovery_input.core_value_props` | string[] | 핵심 가치 제안 초안 | `["환전 편의성", "해외 결제 편의성", "수수료 절감"]` |
| `draft_competitor_discovery_input.known_keywords` | string[] | 후속 agent에 넘길 연관 키워드 초안 | `["트래블카드", "해외결제", "환율우대"]` |
| `draft_competitor_discovery_input.price_or_fee_context` | string | 가격 또는 수수료 관련 초안 설명 | `연회비와 해외 결제 수수료가 주요 비교 포인트일 가능성이 높음` |
| `draft_competitor_discovery_input.usage_context` | string[] | 사용 시나리오 초안 | `["여행 전 환전", "여행 중 결제"]` |
| `draft_competitor_discovery_input.geography` | string | 분석 기준 시장 초안 | `KR` |
| `draft_competitor_discovery_input.business_constraints` | string[] | 후속 분석 제약 조건 초안 | `["국내 서비스 우선"]` |
| `display_fields` | object[] | 웹 UI가 편집 폼으로 바로 사용할 필드 목록 | `[{"field_path":"own_product.name","label":"상품명","value":"토스 트래블카드","editable":true,"confidence":0.95}]` |
| `display_fields[].field_path` | string | 대응하는 draft field 경로 | `own_product.name` |
| `display_fields[].label` | string | UI에 표시할 라벨 | `상품명` |
| `display_fields[].value` | string or string[] | 현재 추정값 | `토스 트래블카드` 또는 `["해외여행자", "단기 출장자"]` |
| `display_fields[].editable` | boolean | 사용자가 수정 가능한 필드인지 여부 | `true` |
| `display_fields[].confidence` | number | 필드 단위 추정 신뢰도, 0~1 범위 | `0.92` |
| `display_fields[].reason` | string | 낮은 신뢰도 또는 추론 근거 설명 | `검색어에 카테고리가 명시되지 않아 일반적인 travel card로 추정함` |
| `assumptions` | string[] | 이번 초안 생성에서 사용한 주요 가정 목록 | `["검색어가 자사 상품명이라고 가정함", "한국 시장 기준으로 해석함"]` |
| `uncertain_fields` | string[] | 사용자 확인이 필요한 필드 경로 목록 | `["domain_name", "own_product.category"]` |
| `needs_user_confirmation` | boolean | 후속 agent 실행 전 사용자 검토가 필요한지 여부 | `true` |
| `created_at` | string | 결과 생성 시각, ISO 8601 형식 | `2026-04-24T10:30:00+09:00` |

### Output 예시

```json
{
  "run_id": "run_20260424_001",
  "request_id": "req_20260424_001",
  "raw_query": "토스 트래블카드",
  "draft_competitor_discovery_input": {
    "project_id": "proj_toss_travelcard",
    "domain_name": "해외 결제/환전 특화 카드",
    "own_product": {
      "brand": "토스",
      "name": "토스 트래블카드",
      "category": "travel payment card"
    },
    "problem_statement": "해외여행 시 환전과 결제를 간편하고 유리하게 처리하고 싶다",
    "target_user": ["해외여행자", "단기 출장자"],
    "core_value_props": [
      "환전 편의성",
      "해외 결제 편의성",
      "수수료 절감"
    ],
    "known_keywords": ["트래블카드", "해외결제", "환율우대"],
    "usage_context": ["여행 전 환전", "여행 중 결제"],
    "geography": "KR",
    "business_constraints": ["국내 서비스 우선"]
  },
  "display_fields": [
    {
      "field_path": "own_product.name",
      "label": "상품명",
      "value": "토스 트래블카드",
      "editable": true,
      "confidence": 0.98
    },
    {
      "field_path": "own_product.category",
      "label": "상품 카테고리",
      "value": "travel payment card",
      "editable": true,
      "confidence": 0.62,
      "reason": "검색어에 카테고리가 직접 명시되지 않아 추정값으로 채움"
    }
  ],
  "assumptions": [
    "검색어가 자사 상품명을 의미한다고 가정함",
    "한국 시장 기준으로 해석함"
  ],
  "uncertain_fields": [
    "domain_name",
    "own_product.category"
  ],
  "needs_user_confirmation": true,
  "created_at": "2026-04-24T10:30:00+09:00"
}
```
