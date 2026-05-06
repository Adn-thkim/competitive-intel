# OfficialSourceResolverAgent 스키마 참조

이 문서는 `OfficialSourceResolverAgent`의 `input.schema.json`, `output.schema.json`에 있는 필드의 의미와 예시값을 빠르게 확인하기 위한 참고 문서입니다.

## Input Schema

| 필드 | 타입 | 설명 | 예시값 |
| --- | --- | --- | --- |
| `project_id` | string | 분석 프로젝트를 식별하는 고유 ID | `proj_toss_travelcard` |
| `own_product` | object | 자사 상품 기본 정보 | `{ "brand": "토스", "name": "토스 트래블카드", "category": "travel payment card" }` |
| `own_product.brand` | string | 자사 상품 브랜드명 | `토스` |
| `own_product.name` | string | 자사 상품명 | `토스 트래블카드` |
| `own_product.category` | string | 자사 상품 카테고리 | `travel payment card` |
| `competitor_candidates` | object[] | 경쟁 후보 상품 목록 | `[{"candidate_id":"cand_001","brand":"하나","product_name":"트래블로그 체크카드","category":"travel card"}]` |
| `competitor_candidates[].candidate_id` | string | 경쟁 후보 고유 ID | `cand_001` |
| `competitor_candidates[].brand` | string | 경쟁 후보 브랜드명 | `하나` |
| `competitor_candidates[].product_name` | string | 경쟁 후보 상품명 | `트래블로그 체크카드` |
| `competitor_candidates[].category` | string | 경쟁 후보 카테고리 | `travel card` |
| `competitor_candidates[].competition_type` | string | 이전 단계가 제공한 경쟁 유형 | `direct` |
| `competitor_candidates[].needs_validation` | boolean | 경쟁 후보 자체의 추가 검증 필요 여부 | `true` |
| `geography` | string | 시장/국가 기준 힌트 | `KR` |
| `locale` | string | 탐색 및 판정에 사용할 로케일 힌트 | `ko-KR` |
| `source_preferences` | object | 공식 출처 선택 제약과 선호 규칙 | `{ "preferred_source_types": ["official_product_page", "official_help_center"], "require_https": true }` |
| `source_preferences.preferred_source_types` | string[] | 우선 확보하려는 공식 출처 유형 | `["official_product_page", "official_help_center"]` |
| `source_preferences.reject_domains` | string[] | 제외가 필요한 도메인 목록 | `["namu.wiki", "youtube.com"]` |
| `source_preferences.require_https` | boolean | HTTPS URL만 허용할지 여부 | `true` |
| `known_official_domains` | object[] | 사전 제공된 공식 도메인 힌트 | `[{"brand":"토스","domains":["toss.im"]}]` |
| `known_official_domains[].brand` | string | 브랜드명 | `토스` |
| `known_official_domains[].domains` | string[] | 해당 브랜드의 공식 도메인 후보 목록 | `["toss.im"]` |
| `search_context` | object | 실제 공식 도메인 탐색 단계의 제약 조건 | `{ "max_domain_candidates_per_target": 5, "allow_search_engine_queries": true }` |
| `search_context.max_domain_candidates_per_target` | integer | 대상당 보관할 도메인 후보 최대 개수 | `5` |
| `search_context.max_page_candidates_per_domain` | integer | 도메인당 검증할 페이지 후보 최대 개수 | `3` |
| `search_context.allow_search_engine_queries` | boolean | 검색 엔진 기반 쿼리 사용 허용 여부 | `true` |
| `validation_preferences` | object | 실제 페이지 검증 단계의 동작 제약 | `{ "require_live_page_check": true, "capture_redirect_chain": true }` |
| `validation_preferences.require_live_page_check` | boolean | 실제 응답 확인을 필수로 둘지 여부 | `true` |
| `validation_preferences.capture_redirect_chain` | boolean | 리다이렉트 여부를 기록할지 여부 | `true` |
| `validation_preferences.capture_canonical_url` | boolean | canonical URL 기록 여부 | `true` |
| `validation_preferences.min_confidence_for_verified` | number | `verified` 판정에 필요한 최소 confidence 기준 | `0.85` |

### Input 예시

```json
{
  "project_id": "proj_toss_travelcard",
  "own_product": {
    "brand": "토스",
    "name": "토스 트래블카드",
    "category": "travel payment card"
  },
  "competitor_candidates": [
    {
      "candidate_id": "cand_001",
      "brand": "하나",
      "product_name": "트래블로그 체크카드",
      "category": "travel card",
      "competition_type": "direct",
      "needs_validation": true
    }
  ],
  "geography": "KR",
  "locale": "ko-KR",
  "source_preferences": {
    "preferred_source_types": [
      "official_product_page",
      "official_help_center"
    ],
    "reject_domains": ["youtube.com", "namu.wiki"],
    "require_https": true
  },
  "known_official_domains": [
    {
      "brand": "토스",
      "domains": ["toss.im"]
    }
  ],
  "search_context": {
    "max_domain_candidates_per_target": 5,
    "max_page_candidates_per_domain": 3,
    "allow_search_engine_queries": true
  },
  "validation_preferences": {
    "require_live_page_check": true,
    "capture_redirect_chain": true,
    "capture_canonical_url": true,
    "min_confidence_for_verified": 0.85
  }
}
```

## Output Schema

| 필드 | 타입 | 설명 | 예시값 |
| --- | --- | --- | --- |
| `run_id` | string | 이번 agent 실행을 식별하는 ID | `run_20260424_002` |
| `project_id` | string | 입력과 연결되는 프로젝트 ID | `proj_toss_travelcard` |
| `domain_discovery_results` | object[] | 1단계 실제 공식 도메인 탐색 결과 | `[{"target_id":"own_product","brand":"토스","product_name":"토스 트래블카드","search_queries":["토스 트래블카드 공식"],"candidate_domains":[{"domain":"toss.im","source":"search_result","is_brand_owned_candidate":true,"selection_reason":"브랜드 공식 도메인으로 일관되게 노출됨"}],"selected_domain_candidates":["toss.im"]}]` |
| `domain_discovery_results[].target_id` | string | 탐색 대상 ID | `own_product` |
| `domain_discovery_results[].brand` | string | 브랜드명 | `토스` |
| `domain_discovery_results[].product_name` | string | 상품명 | `토스 트래블카드` |
| `domain_discovery_results[].search_queries` | string[] | 실제 탐색에 사용한 쿼리 목록 | `["토스 트래블카드 공식", "토스 travel card official"]` |
| `domain_discovery_results[].candidate_domains` | object[] | 평가한 도메인 후보 목록 | `[{"domain":"toss.im","source":"search_result","is_brand_owned_candidate":true,"selection_reason":"브랜드 도메인 일치"}]` |
| `domain_discovery_results[].candidate_domains[].domain` | string | 후보 도메인 | `toss.im` |
| `domain_discovery_results[].candidate_domains[].source` | string | 도메인 후보를 얻은 경로 | `search_result`, `known_domain`, `brand_navigation`, `manual_hint` |
| `domain_discovery_results[].candidate_domains[].is_brand_owned_candidate` | boolean | 브랜드 소유 후보로 판단했는지 여부 | `true` |
| `domain_discovery_results[].candidate_domains[].selection_reason` | string | 후보 유지 이유 | `브랜드명과 도메인 일치` |
| `domain_discovery_results[].candidate_domains[].rejection_reason` | string | 제외 이유가 있으면 기록 | `제3자 리뷰 사이트` |
| `domain_discovery_results[].selected_domain_candidates` | string[] | 2단계 검증으로 넘긴 도메인 목록 | `["toss.im"]` |
| `page_validation_results` | object[] | 2단계 실제 페이지 검증 결과 | `[{"validation_id":"val_001","target_id":"own_product","candidate_url":"https://toss.im/travel-card","final_url":"https://toss.im/travel-card","http_status":200,"status":"ok","page_title":"토스 트래블카드","canonical_url":"https://toss.im/travel-card","page_type_guess":"product_detail","brand_match_signals":["페이지 제목에 토스 포함"],"product_match_signals":["페이지 제목에 트래블카드 포함"],"officiality_signals":["브랜드 소유 도메인","공식 상품 상세 구조"],"blocking_issues":[],"selection_decision":"selected"}]` |
| `page_validation_results[].validation_id` | string | 페이지 검증 ID | `val_001` |
| `page_validation_results[].target_id` | string | 연결된 탐색 대상 ID | `own_product` |
| `page_validation_results[].candidate_url` | string | 검증을 시도한 원래 URL | `https://toss.im/travel-card` |
| `page_validation_results[].final_url` | string | 실제 도달한 최종 URL | `https://toss.im/travel-card` |
| `page_validation_results[].http_status` | integer | HTTP 응답 상태 코드 | `200` |
| `page_validation_results[].status` | string | 접근 결과 상태 | `ok`, `redirected`, `blocked`, `not_found`, `error` |
| `page_validation_results[].page_title` | string | 실제 페이지 제목 | `토스 트래블카드` |
| `page_validation_results[].canonical_url` | string | 확인 가능한 canonical URL | `https://toss.im/travel-card` |
| `page_validation_results[].page_type_guess` | string | 페이지 유형 추정 | `brand_homepage`, `product_detail`, `help_center_article`, `pricing_page`, `faq_page`, `other` |
| `page_validation_results[].brand_match_signals` | string[] | 브랜드 매칭 근거 | `["페이지 제목에 토스 포함"]` |
| `page_validation_results[].product_match_signals` | string[] | 상품 매칭 근거 | `["페이지 제목에 트래블카드 포함"]` |
| `page_validation_results[].officiality_signals` | string[] | 공식성 신호 목록 | `["브랜드 소유 도메인", "공식 상품 상세 구조"]` |
| `page_validation_results[].blocking_issues` | string[] | 검증 과정의 제약/문제 | `["로그인 필요"]` |
| `page_validation_results[].selection_decision` | string | 해당 페이지의 채택 여부 | `selected`, `fallback`, `rejected` |
| `resolution_targets` | object[] | 이번 실행에서 공식 출처 탐색 대상으로 삼은 상품 목록 | `[{"target_id":"own_product","target_type":"own_product","brand":"토스","product_name":"토스 트래블카드","category":"travel payment card"}]` |
| `resolution_targets[].target_id` | string | 탐색 대상 고유 ID | `own_product`, `cand_001` |
| `resolution_targets[].target_type` | string | 대상 유형 | `own_product`, `competitor_candidate` |
| `resolution_targets[].brand` | string | 브랜드명 | `토스` |
| `resolution_targets[].product_name` | string | 상품명 | `토스 트래블카드` |
| `resolution_targets[].category` | string | 상품 카테고리 | `travel payment card` |
| `official_sources` | object[] | 채택된 공식 출처 URL 목록 | `[{"source_id":"src_001","target_id":"own_product","target_type":"own_product","brand":"토스","product_name":"토스 트래블카드","source_type":"official_product_page","url":"https://toss.im/travel-card","domain":"toss.im","page_title":"토스 트래블카드","rationale":"브랜드 도메인에서 상품명을 직접 사용한 상세 페이지","confidence":0.95,"needs_validation":false}]` |
| `official_sources[].source_id` | string | 공식 출처 고유 ID | `src_001` |
| `official_sources[].target_id` | string | 어떤 상품 대상에 연결된 출처인지 식별하는 ID | `own_product` |
| `official_sources[].target_type` | string | 출처가 연결된 대상 유형 | `own_product` |
| `official_sources[].brand` | string | 브랜드명 | `토스` |
| `official_sources[].product_name` | string | 상품명 | `토스 트래블카드` |
| `official_sources[].source_type` | string | 공식 출처 유형 | `official_site`, `official_product_page`, `official_help_center`, `official_pricing_page`, `official_faq` |
| `official_sources[].url` | string | 채택된 공식 URL | `https://toss.im/travel-card` |
| `official_sources[].domain` | string | URL의 주요 도메인 | `toss.im` |
| `official_sources[].page_title` | string | 페이지 제목 또는 제목 요약 | `토스 트래블카드` |
| `official_sources[].selected_from_validation_id` | string | 어떤 페이지 검증 결과에서 채택됐는지 연결하는 ID | `val_001` |
| `official_sources[].rationale` | string | 공식 출처로 채택한 핵심 이유 | `브랜드 소유 도메인에서 상품명을 직접 노출함` |
| `official_sources[].confidence` | number | 출처 공식성 판단 신뢰도, 0~1 범위 | `0.95` |
| `official_sources[].needs_validation` | boolean | 추가 검증 필요 여부 | `false` |
| `source_validation` | object[] | 각 URL에 대한 검증 근거와 판정 | `[{"source_id":"src_001","target_id":"own_product","url":"https://toss.im/travel-card","verdict":"verified","positive_signals":["브랜드 소유 도메인","상품명 직접 노출"],"negative_signals":[],"recommended_use":"primary_reference","notes":"상품 상세 추출의 주 기준 URL로 사용 가능"}]` |
| `source_validation[].source_id` | string | 검증 대상 공식 출처 ID | `src_001` |
| `source_validation[].target_id` | string | 연결된 상품 대상 ID | `own_product` |
| `source_validation[].url` | string | 검증한 URL | `https://toss.im/travel-card` |
| `source_validation[].verdict` | string | 최종 검증 판정 | `verified`, `likely_official`, `ambiguous`, `rejected` |
| `source_validation[].positive_signals` | string[] | 공식성 긍정 신호 목록 | `["브랜드 소유 도메인", "상품명 직접 노출"]` |
| `source_validation[].negative_signals` | string[] | 공식성 부정 신호 목록 | `["상품 상세 정보 부족"]` |
| `source_validation[].recommended_use` | string | 후속 단계에서의 권장 활용 방식 | `primary_reference`, `secondary_reference`, `do_not_use` |
| `source_validation[].validation_evidence` | string[] | 실제 페이지 검증 단계에서 수집한 핵심 증거 | `["HTTP 200", "canonical URL 일치", "페이지 제목에 상품명 포함"]` |
| `source_validation[].notes` | string | 검증 메모 | `상품 상세 추출의 주 기준 URL로 사용 가능` |
| `unresolved_targets` | object[] | 공식 출처를 충분히 확보하지 못한 대상 목록 | `[{"target_id":"cand_003","brand":"OO","product_name":"OO 글로벌 카드","reason":"공식 도메인과 상품 연결성이 약함","suggested_next_step":"브랜드 공식 사이트 내 검색 결과 또는 고객지원 문서 추가 확인"}]` |
| `unresolved_targets[].target_id` | string | 미해결 대상 ID | `cand_003` |
| `unresolved_targets[].brand` | string | 브랜드명 | `OO` |
| `unresolved_targets[].product_name` | string | 상품명 | `OO 글로벌 카드` |
| `unresolved_targets[].reason` | string | 공식 출처 미확정 이유 | `공식 도메인과 상품 연결성이 약함` |
| `unresolved_targets[].suggested_next_step` | string | 다음 검증 액션 제안 | `브랜드 공식 사이트 내 검색 결과 재확인` |
| `created_at` | string | 결과 생성 시각, ISO 8601 형식 | `2026-04-24T11:00:00+09:00` |

### `verdict` 값 설명

| 값 | 의미 | 예시 |
| --- | --- | --- |
| `verified` | 공식 도메인과 상품 연관성이 모두 강하게 확인됨 | 브랜드 도메인의 상품 상세 페이지 |
| `likely_official` | 전반적으로 공식성이 높지만 상품 연결이 일부 간접적임 | 브랜드 메인 사이트의 관련 섹션 |
| `ambiguous` | 일부 공식 신호가 있으나 확정하기 어려움 | 브랜드와 유사한 별도 프로모션 도메인 |
| `rejected` | 공식 출처로 사용하면 안 됨 | 리뷰 블로그, 언론 기사, 오픈마켓 |

### Output 예시

```json
{
  "run_id": "run_20260424_002",
  "project_id": "proj_toss_travelcard",
  "domain_discovery_results": [
    {
      "target_id": "own_product",
      "brand": "토스",
      "product_name": "토스 트래블카드",
      "search_queries": [
        "토스 트래블카드 공식",
        "토스 travel card official"
      ],
      "candidate_domains": [
        {
          "domain": "toss.im",
          "source": "search_result",
          "is_brand_owned_candidate": true,
          "selection_reason": "브랜드 공식 도메인으로 일관되게 노출됨"
        },
        {
          "domain": "blog.example.com",
          "source": "search_result",
          "is_brand_owned_candidate": false,
          "selection_reason": "검색 결과에 등장",
          "rejection_reason": "브랜드 비소유 블로그 도메인"
        }
      ],
      "selected_domain_candidates": ["toss.im"]
    }
  ],
  "page_validation_results": [
    {
      "validation_id": "val_001",
      "target_id": "own_product",
      "candidate_url": "https://toss.im/travel-card",
      "final_url": "https://toss.im/travel-card",
      "http_status": 200,
      "status": "ok",
      "page_title": "토스 트래블카드",
      "canonical_url": "https://toss.im/travel-card",
      "page_type_guess": "product_detail",
      "brand_match_signals": [
        "페이지 제목에 토스 포함"
      ],
      "product_match_signals": [
        "페이지 제목에 트래블카드 포함"
      ],
      "officiality_signals": [
        "브랜드 소유 도메인",
        "공식 상품 상세 구조"
      ],
      "blocking_issues": [],
      "selection_decision": "selected"
    }
  ],
  "resolution_targets": [
    {
      "target_id": "own_product",
      "target_type": "own_product",
      "brand": "토스",
      "product_name": "토스 트래블카드",
      "category": "travel payment card"
    },
    {
      "target_id": "cand_001",
      "target_type": "competitor_candidate",
      "brand": "하나",
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
      "selected_from_validation_id": "val_001",
      "rationale": "브랜드 소유 도메인에서 상품명을 직접 사용한 상세 페이지",
      "confidence": 0.95,
      "needs_validation": false
    },
    {
      "source_id": "src_002",
      "target_id": "cand_001",
      "target_type": "competitor_candidate",
      "brand": "하나",
      "product_name": "트래블로그 체크카드",
      "source_type": "official_help_center",
      "url": "https://www.kebhana.com/help/travelog",
      "domain": "kebhana.com",
      "page_title": "트래블로그 체크카드 이용안내",
      "rationale": "브랜드 공식 도메인 내 상품 이용안내 페이지",
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
      "positive_signals": [
        "브랜드 소유 도메인",
        "상품명 직접 노출",
        "공식 상품 설명 페이지 구조"
      ],
      "negative_signals": [],
      "recommended_use": "primary_reference",
      "validation_evidence": [
        "HTTP 200",
        "canonical URL 일치",
        "페이지 제목에 상품명 포함"
      ],
      "notes": "후속 기능 추출의 우선 기준 URL로 사용 가능"
    },
    {
      "source_id": "src_002",
      "target_id": "cand_001",
      "url": "https://www.kebhana.com/help/travelog",
      "verdict": "likely_official",
      "positive_signals": [
        "브랜드 공식 도메인",
        "상품 이용안내 성격"
      ],
      "negative_signals": [
        "상품 메인 소개 페이지 여부는 추가 확인 필요"
      ],
      "recommended_use": "secondary_reference",
      "notes": "상세 혜택보다 이용 조건 확인에 적합"
    }
  ],
  "unresolved_targets": [],
  "created_at": "2026-04-24T11:00:00+09:00"
}
```
