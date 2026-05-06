# FeatureUrlMapperAgent 명세

## 목적

`FeatureUrlMapperAgent`는 URL 검증이 완료된 시점에서 도메인과 상품 구성을 분석하여 경쟁 분석에 필요한 비교 항목(feature)을 동적으로 도출하고, 각 feature × candidate 조합마다 기존 URL의 커버리지를 평가한 뒤 부족한 경우 추가 탐색 후보 URL을 제안하는 agent이다.

이 agent는 url_retry_node 이후에 실행되므로 자사·경쟁사의 공식 URL과 기능적 대안의 레퍼런스 URL이 이미 검증된 상태에서 작동한다. 후속 `feature_extraction_node`가 "어떤 URL에서 무슨 정보를 긁을지" 결정할 수 있도록 구조화된 매핑 결과를 제공한다.

## 책임

- 도메인 특성(해외여행 카드, 로보어드바이저, 여행자 보험 등)을 바탕으로 5~10개의 비교 feature를 동적으로 정의한다.
- 각 feature의 소비자 의사결정 영향도를 `high / medium / low`로 분류한다.
- 각 feature × candidate 조합마다 기존 검증 URL로 정보를 수집할 수 있는지 평가한다 (`sufficient / partial / not_found`).
- 커버리지가 부족한 경우 기존 URL 도메인 내 전용 하위 페이지를 우선 제안한다.
- 제안 URL의 실제 존재 가능성을 `url_confidence`로 정량화하여 불확실성을 명시한다.
- `output.schema.json`을 만족하는 정규화된 JSON을 반환한다.

## 비목표

- 실제 URL에서 상품 정보를 크롤링하거나 내용을 추출하지 않는다.
- feature 값을 직접 채우거나 비교표를 작성하지 않는다.
- 새로운 경쟁사를 발굴하거나 추가하지 않는다.
- 이미 검증된 URL 외에 별도 검색을 수행하지 않는다.

## 파이프라인 위치

```
query_intake
  → human_review (interrupt #1)
    → competitor_discovery
      → normalize_competitor_ids
        → competitor_selection (interrupt #2)
          → official_source_resolver
            → url_retry (interrupt #3, 선택적)
              → feature_url_mapper   ← 이 agent
                → feature_selection (interrupt #4)
                  → feature_extraction
                    → ...
```

## 입력 계약

입력 payload는 `input.schema.json`을 만족해야 한다.

필수 입력:

- `domain` : 분석 도메인 설명. LLM이 feature를 도출하는 핵심 컨텍스트.
- `own_product.brand`, `own_product.product_name` : 자사 상품 식별
- `candidates[]` : 각 후보의 `candidate_id`, `source_type`, `validated_urls`

`validated_urls`의 `page_title`, `meta_description`은 노드가 HTTP GET으로 수집해 주입한다.
LLM은 이 메타데이터를 커버리지 판단의 주요 근거로 활용한다.

## 출력 계약

출력 payload는 `output.schema.json`을 만족해야 한다.

핵심 출력:

- `features[]` : 도출된 비교 항목 목록

각 feature 항목에는 최소한 아래 필드가 포함되어야 한다:

- `feature_id` (`feat_` 접두사, snake_case)
- `feature_name`
- `description`
- `priority` (`high / medium / low`)
- `candidate_coverage[]` : 모든 입력 candidate에 대해 coverage 평가

각 candidate_coverage 항목:

- `candidate_id`
- `coverage` (`sufficient / partial / not_found`)
- `existing_urls[]` : 관련 있는 기존 URL + `relevance_note`
- `additional_urls[]` : 추가 탐색 후보 URL (coverage가 sufficient이면 빈 배열)

## 실행 흐름

노드(`feature_url_mapper_node.py`)의 처리 단계:

### Step 1 — Page Meta 수집 (HTTP)

- `official_sources` state에서 validated URL을 추출한다.
  - official 항목 : `primary_url` (validated=True인 경우만)
  - reference 항목 : `reference_sources[]` 중 `validated=True`인 URL
- ThreadPoolExecutor로 각 URL에 GET 요청을 보내 `<title>`과 `<meta name="description">` content를 추출한다.
- 메타 수집 실패 시 공백 문자열로 처리하고 계속 진행한다.

### Step 2 — LLM 호출 (1회)

- Step 1 결과를 `input.schema.json` 형식으로 조립한다.
- `system_prompt_kr.md`와 `output.schema.json`을 사용해 LLM을 호출한다.
- LLM은 feature 정의 + 전체 coverage 매핑을 1회 호출로 반환한다.

### Step 3 — Additional URL HTTP 검증

- LLM이 제안한 `additional_urls`를 ThreadPoolExecutor로 병렬 검증한다.
- 각 URL에 `validated`, `http_status` 필드를 추가한다.
- coverage가 `sufficient`인 항목은 additional_urls가 빈 배열이므로 검증 대상 없음.

## Feature 정의 원칙

### 개수 및 범위

- 5개 이상 10개 이하로 제한한다.
- 도메인 특성을 반영한 항목만 포함한다 (예: 해외여행 카드라면 해외결제 수수료, ATM 인출 수수료 등).
- 모든 도메인에 공통 적용되는 범용 항목(예: "고객 서비스 품질")은 낮은 우선순위로 배치하거나 생략한다.

### Priority 기준

| 값 | 기준 |
|---|---|
| `high` | 소비자가 상품 선택 시 거의 반드시 비교하는 항목 |
| `medium` | 특정 사용자군에게 중요하거나, 기본 비교 후 고려하는 항목 |
| `low` | 참고 정보로 활용되는 부가 항목 |

## Coverage 판단 원칙

### Sufficient

기존 URL의 `page_title` 또는 `meta_description`에 해당 feature 관련 키워드가 명확히 포함되거나, 상품 메인 페이지로서 feature 정보가 포함될 가능성이 매우 높은 경우.

### Partial

상품 메인 페이지이긴 하나, 수수료·약관·혜택 등 특정 feature는 전용 하위 페이지에 분리되어 있을 가능성이 높은 경우.

### Not Found

브랜드 메인 도메인 수준 URL이거나, `page_title`/`meta_description`에 해당 feature와 무관한 내용만 있는 경우.

## Additional URL 제안 원칙

- `coverage`가 `sufficient`이면 `additional_urls`는 반드시 빈 배열.
- 기존 URL의 도메인 내 전용 하위 페이지를 우선 제안한다.
  - 경로 힌트: `/fee`, `/benefit`, `/guide`, `/info`, `/faq`, `/rate`, `/charge`
- feature-candidate 쌍마다 최대 2개로 제한한다.
- 확신할 수 없는 URL은 `url_confidence`를 0.3 이하로 설정한다.
- 알지 못하는 URL은 지어내지 않는다.

## 파일 구성

- `spec.md` : 사람이 읽는 설계 명세 (이 파일)
- `system_prompt_kr.md` : 한글 LLM 지침
- `input.schema.json` : 입력 검증 스키마
- `output.schema.json` : 출력 검증 스키마
