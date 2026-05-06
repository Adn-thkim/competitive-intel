# CompetitorDiscoveryAgent 스키마 참조

이 문서는 `input.schema.json`, `output.schema.json`의 각 필드가 어떤 값을 가지는지 빠르게 확인하기 위한 참고 문서입니다.

## Input Schema

| 필드 | 타입 | 설명 | 예시값 |
| --- | --- | --- | --- |
| `project_id` | string | 분석 프로젝트를 식별하는 고유 ID | `proj_toss_travelcard` |
| `domain_name` | string | 분석 대상 시장 또는 상품 도메인명 | `해외 결제/환전 특화 카드` |
| `own_product` | object | 자사 상품 기본 정보 묶음 | `{ "brand": "토스", "name": "토스 트래블카드", "category": "travel payment card" }` |
| `own_product.brand` | string | 자사 상품 브랜드명 | `토스` |
| `own_product.name` | string | 자사 상품명 | `토스 트래블카드` |
| `own_product.category` | string | 자사 상품 카테고리 | `travel payment card` |
| `problem_statement` | string | 사용자가 이 상품으로 해결하려는 핵심 문제 | `해외여행 시 환전과 결제를 간편하고 유리하게 처리하고 싶다` |
| `target_user` | string[] | 핵심 사용자군 목록 | `["해외여행자", "단기 출장자"]` |
| `core_value_props` | string[] | 자사 상품의 핵심 가치 제안 목록 | `["환전 편의성", "해외 결제 편의성", "수수료 절감"]` |
| `known_keywords` | string[] | 연관 검색어 또는 업계 표현 | `["트래블카드", "해외결제", "환율우대"]` |
| `price_or_fee_context` | string | 가격, 수수료, 연회비 등 비용 맥락 설명 | `연회비 없음, 해외 결제 수수료 절감 강조` |
| `usage_context` | string[] | 실제 사용 상황 또는 시점 | `["여행 전 환전", "여행 중 결제", "해외 ATM 출금"]` |
| `geography` | string | 분석 기준 국가 또는 시장 | `KR` |
| `business_constraints` | string[] | 후보 선정 시 지켜야 할 제약 조건 | `["국내 서비스 우선", "공식 상품 기준 우선"]` |

### Input 예시

```json
{
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
  "price_or_fee_context": "연회비 없음, 해외 결제 수수료 절감 강조",
  "usage_context": ["여행 전 환전", "여행 중 결제", "해외 ATM 출금"],
  "geography": "KR",
  "business_constraints": ["국내 서비스 우선", "공식 상품 기준 우선"]
}
```

## Output Schema

| 필드 | 타입 | 설명 | 예시값 |
| --- | --- | --- | --- |
| `run_id` | string | 이번 실행 결과를 구분하는 ID | `run_20260423_001` |
| `project_id` | string | 입력과 연결되는 프로젝트 ID | `proj_toss_travelcard` |
| `own_product_summary` | object | 자사 상품 포지셔닝 요약 | `{ "market_position": "해외여행자를 위한 결제/환전 특화 상품", "primary_use_cases": ["해외 결제", "여행 전 환전"] }` |
| `own_product_summary.market_position` | string | 자사 상품의 시장 내 위치를 한 줄로 요약한 값 | `해외여행자를 위한 결제/환전 특화 상품` |
| `own_product_summary.primary_use_cases` | string[] | 자사 상품의 대표 사용 사례 | `["해외 결제", "여행 전 환전", "여행 중 자금 사용"]` |
| `competition_axes` | string[] | 이번 실행에서 경쟁 판단에 사용한 비교 축 | `["동일 사용자 문제 해결 여부", "수수료 절감 가치 경쟁 여부"]` |
| `competitor_candidates` | object[] | 경쟁 후보 목록 | `[{"candidate_id":"cand_001","brand":"OO은행","product_name":"OO 트래블 카드","competition_type":"direct","category":"travel card","why_competitor":["해외여행자 대상 상품"],"evidence_summary":"해외 결제 및 환전 특화 포지션이 유사함","confidence":0.87,"needs_validation":true}]` |
| `competitor_candidates[].candidate_id` | string | 후보 고유 ID | `cand_001` |
| `competitor_candidates[].brand` | string | 경쟁 후보 브랜드명 | `OO은행` |
| `competitor_candidates[].product_name` | string | 경쟁 후보 상품명 | `OO 트래블 카드` |
| `competitor_candidates[].competition_type` | string | 경쟁 유형 | `direct` |
| `competitor_candidates[].category` | string | 후보 상품 카테고리 | `travel card` |
| `competitor_candidates[].why_competitor` | string[] | 경쟁 후보로 본 이유 목록 | `["해외 결제와 환전 혜택을 함께 제공", "동일 사용자군을 타깃팅"]` |
| `competitor_candidates[].evidence_summary` | string | 핵심 근거를 한두 문장으로 요약한 값 | `해외 결제 및 환전 특화 포지션이 유사함` |
| `competitor_candidates[].confidence` | number | 경쟁 후보 판단 신뢰도, 0~1 범위 | `0.87` |
| `competitor_candidates[].needs_validation` | boolean | 후속 검증이 필요한지 여부 | `true` |
| `excluded_or_deferred` | object[] | 제외했거나 보류한 후보 목록 | `[{"name":"일반 신용카드 A","reason":"여행 특화 포지션이 약함"}]` |
| `excluded_or_deferred[].name` | string | 제외 또는 보류된 후보명 | `일반 신용카드 A` |
| `excluded_or_deferred[].reason` | string | 제외 또는 보류 이유 | `여행 특화 포지션이 약함` |
| `created_at` | string | 결과 생성 시각, ISO 8601 형식 | `2026-04-23T10:00:00+09:00` |

### `competition_type` 값 설명

| 값 | 의미 | 예시 |
| --- | --- | --- |
| `direct` | 같은 사용자 문제와 사용 맥락에서 직접 비교될 가능성이 높은 경쟁자 | `해외 결제/환전 특화 카드` |
| `indirect` | 일부 문제나 사용 상황은 겹치지만 직접 대체성은 더 낮은 경쟁자 | `일반 해외 결제 특화 체크카드` |
| `substitute` | 형태는 다르지만 사용자 입장에서 같은 목적을 달성할 수 있는 대안 | `현지 환전 서비스`, `여행자용 선불지갑` |

### Output 예시

```json
{
  "run_id": "run_20260423_001",
  "project_id": "proj_toss_travelcard",
  "own_product_summary": {
    "market_position": "해외여행자를 위한 결제/환전 특화 상품",
    "primary_use_cases": ["해외 결제", "여행 전 환전", "여행 중 자금 사용"]
  },
  "competition_axes": [
    "동일 사용자 문제 해결 여부",
    "해외 결제 수단으로의 대체 가능성",
    "환전/수수료 절감 가치 경쟁 여부"
  ],
  "competitor_candidates": [
    {
      "candidate_id": "cand_001",
      "brand": "OO은행",
      "product_name": "OO 트래블 카드",
      "competition_type": "direct",
      "category": "travel card",
      "why_competitor": [
        "해외여행자 대상 상품",
        "해외 결제 및 환전 혜택 제공",
        "동일한 선택 상황에서 비교될 가능성이 높음"
      ],
      "evidence_summary": "해외 결제 및 환전 특화 포지션이 유사함",
      "confidence": 0.87,
      "needs_validation": true
    }
  ],
  "excluded_or_deferred": [
    {
      "name": "일반 신용카드 A",
      "reason": "해외 사용은 가능하지만 여행 특화 포지션이 약함"
    }
  ],
  "created_at": "2026-04-23T10:00:00+09:00"
}
```
