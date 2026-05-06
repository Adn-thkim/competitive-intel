# FeatureExtractionAgent 스키마 참조

이 문서는 `FeatureExtractionAgent`의 `input.schema.json`, `output.schema.json` 필드를 빠르게 확인하기 위한 참고 문서입니다.

## Input Schema

| 필드 | 타입 | 설명 | 예시값 |
| --- | --- | --- | --- |
| `project_id` | string | 분석 프로젝트 ID | `proj_toss_travelcard` |
| `resolution_targets` | object[] | 이번 추출 대상 상품 목록 | `[{"target_id":"own_product","target_type":"own_product","brand":"토스","product_name":"토스 트래블카드","category":"travel card"}]` |
| `official_sources` | object[] | 공식 출처 목록 | `[{"source_id":"src_001","target_id":"own_product","source_type":"official_product_page","url":"https://toss.im/travel-card",...}]` |
| `source_validation` | object[] | 공식성 판정 결과 | `[{"source_id":"src_001","verdict":"verified","recommended_use":"primary_reference"}]` |
| `locale` | string | 추출 시 참고할 로케일 | `ko-KR` |
| `geography` | string | 시장/국가 기준 힌트 | `KR` |
| `normalization_context` | object | 정규화 정책 | `{ "feature_schema_version": "travel-card-v1", "preferred_unknown_value": "unknown" }` |
| `extraction_preferences` | object | 출처 사용 선호 규칙 | `{ "max_sources_per_target": 3, "include_help_center": true }` |

### Input 예시

```json
{
  "project_id": "proj_toss_travelcard",
  "resolution_targets": [
    {
      "target_id": "own_product",
      "target_type": "own_product",
      "brand": "토스",
      "product_name": "토스 트래블카드",
      "category": "travel card"
    },
    {
      "target_id": "cand_001",
      "target_type": "competitor_candidate",
      "brand": "하나카드",
      "product_name": "트래블로그 체크카드",
      "category": "travel card"
    }
  ],
  "official_sources": [
    {
      "source_id": "src_001",
      "target_id": "own_product",
      "target_type": "own_product",
      "brand": "토스",
      "product_name": "토스 트래블카드",
      "source_type": "official_product_page",
      "url": "https://toss.im/travel-card",
      "domain": "toss.im",
      "page_title": "토스 트래블카드",
      "rationale": "브랜드 공식 도메인의 상품 소개 페이지",
      "confidence": 0.95,
      "needs_validation": false
    },
    {
      "source_id": "src_002",
      "target_id": "cand_001",
      "target_type": "competitor_candidate",
      "brand": "하나카드",
      "product_name": "트래블로그 체크카드",
      "source_type": "official_help_center",
      "url": "https://www.hanacard.co.kr/travelog/help",
      "domain": "hanacard.co.kr",
      "page_title": "트래블로그 체크카드 이용안내",
      "rationale": "공식 도움말 페이지",
      "confidence": 0.82,
      "needs_validation": true
    }
  ],
  "source_validation": [
    {
      "source_id": "src_001",
      "target_id": "own_product",
      "url": "https://toss.im/travel-card",
      "verdict": "verified",
      "positive_signals": ["브랜드 공식 도메인", "상품명 직접 노출"],
      "negative_signals": [],
      "recommended_use": "primary_reference",
      "notes": "주요 추출 기준 URL"
    }
  ],
  "locale": "ko-KR",
  "geography": "KR",
  "normalization_context": {
    "feature_schema_version": "travel-card-v1",
    "preferred_unknown_value": "unknown",
    "preserve_source_evidence": true
  }
}
```

## Output Schema

| 필드 | 타입 | 설명 | 예시값 |
| --- | --- | --- | --- |
| `run_id` | string | 이번 실행 ID | `run_20260424_003` |
| `project_id` | string | 입력과 연결되는 프로젝트 ID | `proj_toss_travelcard` |
| `extraction_targets` | object[] | 실제 추출에 사용한 대상/출처 매핑 | `[{"target_id":"own_product","selected_source_ids":["src_001"]}]` |
| `product_profiles` | object[] | 상품별 구조화 profile | `[{"target_id":"own_product","product_summary":"...","raw_profile":{...}}]` |
| `normalized_feature_schema` | string[] | 공통 비교 schema 키 목록 | `["card_type","supported_currencies","exchange_fee"]` |
| `normalized_features` | object[] | 상품별 정규화 결과 | `[{"target_id":"own_product","schema_version":"travel-card-v1","normalized_features":{...}}]` |
| `unresolved_targets` | object[] | 공식 정보가 부족한 대상 목록 | `[{"target_id":"cand_003","reason":"공식 페이지에서 핵심 기능 확인 불가"}]` |
| `created_at` | string | 결과 생성 시각, ISO 8601 형식 | `2026-04-24T12:00:00+09:00` |

### `product_profiles` 핵심 필드

| 필드 | 타입 | 설명 | 예시값 |
| --- | --- | --- | --- |
| `target_id` | string | 상품 대상 ID | `own_product` |
| `target_type` | string | 대상 유형 | `own_product` |
| `brand` | string | 브랜드명 | `토스` |
| `product_name` | string | 상품명 | `토스 트래블카드` |
| `category` | string | 상품 카테고리 | `travel card` |
| `source_ids` | string[] | 사용한 공식 출처 ID 목록 | `["src_001"]` |
| `source_urls` | string[] | 사용한 공식 URL 목록 | `["https://toss.im/travel-card"]` |
| `product_summary` | string | 상품 설명 요약 | `해외 결제와 외화 사용을 지원하는 여행 특화 카드` |
| `raw_profile.features` | string[] | 핵심 기능 목록 | `["외화 충전 및 보유", "해외 결제"]` |
| `raw_profile.fees` | string[] | 수수료 관련 공식 설명 | `["해외 결제 수수료 관련 안내 존재"]` |
| `raw_profile.benefits` | string[] | 주요 혜택 | `["여행 특화 사용성"]` |
| `raw_profile.constraints` | string[] | 제약 조건 | `["지원 통화/국가 제한은 추가 확인 필요"]` |
| `coverage_status` | string | 공식 정보 커버리지 수준 | `partial` |
| `confidence` | number | 추출 결과 신뢰도 | `0.84` |
| `needs_manual_review` | boolean | 수동 검토 필요 여부 | `true` |

### `normalized_features` 핵심 필드

| 필드 | 타입 | 설명 | 예시값 |
| --- | --- | --- | --- |
| `schema_version` | string | 정규화 schema 버전 | `travel-card-v1` |
| `normalized_features.card_type` | string | 카드 유형 | `travel_card` |
| `normalized_features.supported_currencies` | string[] | 지원 통화 | `["unknown"]` |
| `normalized_features.exchange_fee` | string | 환전 수수료 상태 또는 값 | `requires_manual_check` |
| `normalized_features.overseas_payment_fee` | string | 해외 결제 수수료 상태 또는 값 | `not_found` |
| `normalized_features.atm_withdrawal` | string | ATM 관련 조건 | `requires_manual_check` |
| `normalized_features.recharge_method` | string | 충전 방식 | `app_based` |
| `normalized_features.app_linkage` | string | 앱 연동성 | `toss_app` |
| `normalized_features.travel_benefits` | string[] | 여행 특화 혜택 | `["travel_focused"]` |
| `normalized_features.eligibility` | string | 가입/사용 자격 | `unknown` |
| `normalized_features.major_constraints` | string[] | 핵심 제약 조건 | `["지원 통화 상세는 공식 추가 문서 확인 필요"]` |
| `normalized_features.source_coverage` | string | 정규화 근거 커버리지 | `partial` |

### Output 예시

```json
{
  "run_id": "run_20260424_003",
  "project_id": "proj_toss_travelcard",
  "extraction_targets": [
    {
      "target_id": "own_product",
      "target_type": "own_product",
      "brand": "토스",
      "product_name": "토스 트래블카드",
      "category": "travel card",
      "selected_source_ids": ["src_001"]
    }
  ],
  "product_profiles": [
    {
      "target_id": "own_product",
      "target_type": "own_product",
      "brand": "토스",
      "product_name": "토스 트래블카드",
      "category": "travel card",
      "source_ids": ["src_001"],
      "source_urls": ["https://toss.im/travel-card"],
      "product_summary": "해외 결제와 외화 사용에 초점을 둔 여행 특화 카드 상품",
      "raw_profile": {
        "features": ["외화 충전 및 보유", "해외 결제"],
        "fees": ["수수료 정보는 별도 공식 안내 페이지 확인 필요"],
        "benefits": ["여행 사용성 중심"],
        "constraints": ["세부 지원 통화는 추가 확인 필요"],
        "usage_scope": ["해외 여행 중 결제"]
      },
      "coverage_status": "partial",
      "confidence": 0.84,
      "needs_manual_review": true
    }
  ],
  "normalized_feature_schema": [
    "card_type",
    "supported_currencies",
    "exchange_fee",
    "overseas_payment_fee",
    "atm_withdrawal",
    "recharge_method",
    "app_linkage",
    "travel_benefits",
    "eligibility",
    "major_constraints",
    "source_coverage"
  ],
  "normalized_features": [
    {
      "target_id": "own_product",
      "target_type": "own_product",
      "product_name": "토스 트래블카드",
      "schema_version": "travel-card-v1",
      "normalized_features": {
        "card_type": "travel_card",
        "supported_currencies": ["unknown"],
        "exchange_fee": "requires_manual_check",
        "overseas_payment_fee": "not_found",
        "atm_withdrawal": "requires_manual_check",
        "recharge_method": "app_based",
        "app_linkage": "toss_app",
        "travel_benefits": ["travel_focused"],
        "eligibility": "unknown",
        "major_constraints": ["세부 수수료와 지원 통화는 추가 확인 필요"],
        "source_coverage": "partial"
      },
      "normalization_notes": [
        "공식 상품 소개 페이지 기준으로 기본 기능만 확정함"
      ]
    }
  ],
  "unresolved_targets": [],
  "created_at": "2026-04-24T12:00:00+09:00"
}
```
