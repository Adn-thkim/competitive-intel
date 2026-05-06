# CompetitorDiscoveryAgent 명세

## 목적

`CompetitorDiscoveryAgent`는 주어진 자사 상품 또는 분석 도메인을 기준으로 경쟁 가능성이 있는 후보를 식별하고, 후속 검증 단계에서 사용할 수 있는 구조화된 후보 집합을 반환합니다.

이 agent는 다음 단계 분석을 지원하기 위한 용도입니다. 최종 진실을 확정하지 않으며, 근거가 있는 경쟁 가설을 기계가 읽을 수 있는 형식으로 정리하는 역할을 맡습니다.

## 책임

- 자사 상품의 시장 포지션을 요약한다.
- 이번 실행에서 사용할 경쟁 판단 축을 정의한다.
- 직접 경쟁, 간접 경쟁, 대체재 후보를 제안한다.
- 각 후보에 경쟁 이유, 근거 요약, 신뢰도를 붙인다.
- 불확실한 후보를 후속 검증 대상으로 표시한다.
- `output.schema.json`에 맞는 정규화된 JSON을 반환한다.

## 비목표

- 공식 URL 최종 확정
- 경쟁 후보별 상세 기능 전수 추출
- 최종 비교 리포트 작성
- 사용자 반응 분석
- 근거 없는 단정적 결론 제시

## 입력 계약

입력 payload는 `input.schema.json`을 만족해야 합니다.

필수 필드:

- `project_id`
- `domain_name`
- `own_product.brand`
- `own_product.name`
- `own_product.category`
- `problem_statement`
- `target_user`
- `core_value_props`

권장 선택 필드:

- `known_keywords`
- `price_or_fee_context`
- `usage_context`
- `geography`
- `business_constraints`

## 출력 계약

출력 payload는 `output.schema.json`을 만족해야 합니다.

필수 출력 섹션:

- `run_id`
- `project_id`
- `own_product_summary`
- `competition_axes`
- `competitor_candidates`
- `excluded_or_deferred`
- `created_at`

각 후보에는 최소한 아래 필드가 포함되어야 합니다.

- `competition_type`: `direct`, `indirect`, `substitute`
- `why_competitor`
- `evidence_summary`
- `confidence`
- `needs_validation`

## 분류 규칙

### 직접 경쟁자

후보가 아래 차원 대부분에서 자사 상품과 강하게 겹칠 때 사용합니다.

- 사용자 문제
- 사용 시나리오
- 가치 제안
- 대체 가능성
- 시장 포지셔닝
- 비교 가능성

운영 규칙:

- 최소 4개 차원 이상에서 강한 정렬이 있으면 `direct`로 분류합니다.

### 간접 경쟁자

후보가 사용자 문제 또는 사용 맥락 일부와는 겹치지만, 일대일 대안으로 보기 어려울 때 사용합니다.

운영 규칙:

- 2~3개 차원에서 의미 있는 정렬이 있으면 `indirect`로 분류합니다.

### 대체재

후보의 상품 형태는 다르지만, 사용자 입장에서 같은 최종 목적을 해결할 수 있을 때 사용합니다.

운영 규칙:

- 상품 구조가 달라도 결과 수준에서 대체가 가능하면 `substitute`로 분류합니다.

## 품질 규칙

- 성급한 제외보다 후보 커버리지를 우선합니다.
- 근거 없는 사실을 만들어내지 않습니다.
- 명확한 경쟁 논리가 없으면 유명 브랜드를 후보로 넣지 않습니다.
- 가능하면 브랜드가 아니라 상품 단위 엔티티로 정규화합니다.
- 근거가 약하면 `needs_validation: true`로 남겨 둡니다.
- 특별한 제약이 없으면 1차 후보 수는 5~15개 범위를 유지합니다.

## 실행 흐름

1. 자사 상품을 읽고 요약합니다.
2. 해당 도메인에 맞는 경쟁 판단 축을 도출합니다.
3. 넓은 후보군을 생성합니다.
4. 중복과 명백한 비관련 후보를 제거합니다.
5. 남은 후보를 분류합니다.
6. 구조화된 출력 JSON을 작성합니다.

## 파일 구성

- `spec.md`: 사람이 읽는 설계 명세
- `system_prompt.md`: 영문 모델 지침 원본
- `system_prompt_kr.md`: 한글 모델 지침 참고본
- `input.schema.json`: 입력 검증 스키마
- `output.schema.json`: 출력 검증 스키마
- `config.yaml`: 실행 설정 예시
