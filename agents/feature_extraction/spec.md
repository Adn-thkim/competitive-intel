# FeatureExtractionAgent 명세

## 목적

`FeatureExtractionAgent`는 검증된 공식 출처를 기반으로 상품 설명, 기능, 수수료, 혜택, 제약 조건을 구조화하고, 후속 `FeatureComparisonAgent`가 바로 사용할 수 있는 정규화 feature 세트를 반환하는 agent이다.

이 agent의 핵심 역할은 "공식 문구를 근거로 추출 가능한 사실을 정리하는 것"이다. 추정은 최소화하고, 공식 출처에 없는 값은 `unknown`, `not_found`, `requires_manual_check` 같은 상태값으로 남긴다.

## 책임

- `resolution_targets`와 `official_sources`를 결합해 상품별 추출 대상을 구성한다.
- 공식 상품 페이지, 도움말, 가격/FAQ 페이지에서 핵심 설명을 구조화한다.
- 추출 결과를 `product_profiles` 형태로 남긴다.
- 도메인별 공통 비교를 위해 `travel-card-v1` 기준으로 정규화한다.
- 출처 추적 가능성을 유지하기 위해 source id / URL 연결을 보존한다.
- 출처가 부족하거나 내용이 불충분한 상품은 `unresolved_targets`에 남긴다.
- `output.schema.json`에 맞는 JSON만 반환한다.

## 비목표

- 새로운 공식 URL을 탐색하지 않는다.
- 제3자 리뷰나 커뮤니티 내용을 근거로 기능을 보강하지 않는다.
- 상품 간 비교 결론을 내리지 않는다.
- YouTube 검색어를 설계하지 않는다.
- 공식 문서에 없는 세부 수치를 추정으로 채우지 않는다.

## 입력 계약

입력 payload는 `input.schema.json`을 만족해야 한다.

필수 입력:

- `project_id`
- `resolution_targets`
- `official_sources`

권장 선택 입력:

- `source_validation`
- `locale`
- `geography`
- `normalization_context`
- `extraction_preferences`

## 출력 계약

출력 payload는 `output.schema.json`을 만족해야 한다.

핵심 출력:

- `extraction_targets`
- `product_profiles`
- `normalized_feature_schema`
- `normalized_features`
- `unresolved_targets`

## 추출 원칙

### 허용 근거

- `OfficialSourceResolverAgent`가 통과시킨 공식 출처
- 상품 상세 설명 페이지
- 공식 FAQ / 도움말 / 이용안내 페이지
- 공식 가격, 수수료, 정책 안내 페이지

### 기본 금지

- 제3자 블로그, 기사, 커뮤니티, 마켓플레이스
- 공식성 판정이 `rejected`인 출처
- 입력에 없는 URL

### 사실성 규칙

- 페이지에 명시된 내용은 `explicit` 근거로 취급한다.
- 일부 문맥만 확인 가능한 경우 `partial`로 남긴다.
- 정규화 과정에서 최소한의 분류 해석이 필요하면 `inferred`로 표시하되, profile 본문에는 사실처럼 과장하지 않는다.
- 확정할 수 없는 값은 비워서 숨기지 말고 상태값으로 둔다.

## 정규화 원칙

현재 초안은 `travel-card-v1` 스키마를 기본으로 한다.

권장 정규화 필드:

- `card_type`
- `supported_currencies`
- `exchange_fee`
- `overseas_payment_fee`
- `atm_withdrawal`
- `recharge_method`
- `app_linkage`
- `travel_benefits`
- `eligibility`
- `major_constraints`
- `source_coverage`

정규화 값 규칙:

- 비교 가능한 값이면 간단한 문자열 또는 enum 성격의 문자열로 축약한다.
- 배열형 필드는 최소 1개 이상 채운다. 값이 없으면 `["unknown"]` 같은 placeholder를 사용한다.
- 공식 출처에서 충분히 확인되지 않은 항목은 `unknown`, `not_found`, `requires_manual_check` 중 하나로 남긴다.

## 품질 규칙

- 한 상품당 보통 1~3개의 공식 출처를 사용한다.
- `official_product_page`를 우선 사용하고, 부족한 정보는 `official_help_center`, `official_pricing_page`, `official_faq`로 보완한다.
- source coverage가 부족하면 `needs_manual_review: true`로 둔다.
- 서로 충돌하는 공식 문구가 있으면 profile에는 충돌 사실을 남기고 수동 검토 대상으로 올린다.
- 긴 원문 복사는 피하고 구조화 요약을 우선한다.

## 실행 흐름

1. `resolution_targets`별로 연결 가능한 공식 출처를 모은다.
2. `source_validation`이 있으면 `verified`, `likely_official` 출처를 우선 채택한다.
3. 상품 요약, 기능, 수수료, 혜택, 제약 조건을 추출한다.
4. `product_profiles`를 만든다.
5. `travel-card-v1` 기준으로 `normalized_features`를 만든다.
6. 정보가 부족한 상품은 `unresolved_targets`에 남긴다.

## 파일 구성

- `spec.md`: 사람이 읽는 설계 명세
- `system_prompt.md`: 영문 모델 지침 원본
- `system_prompt_kr.md`: 한글 모델 지침 참고본
- `input.schema.json`: 입력 검증 스키마
- `output.schema.json`: 출력 검증 스키마
- `config.yaml`: 실행 설정 예시
- `schema_reference.md`: 필드 설명과 예시
