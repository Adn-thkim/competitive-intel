# OfficialSourceResolverAgent 명세

## 목적

`OfficialSourceResolverAgent`는 자사 상품과 경쟁 후보 상품에 대해 실제 브랜드 공식 도메인을 탐색하고, 실제 페이지 단위 검증을 거쳐 공식 홈페이지, 공식 상품 소개 페이지, 공식 도움말/안내 페이지를 식별한 뒤 구조화된 JSON으로 반환하는 agent이다.

이 agent의 핵심 역할은 "공식 출처 후보 수집"이 아니라 "실제 탐색과 실제 검증을 거쳐 공식성 판단이 가능한 출처 집합 정리"이다. 후속 `FeatureExtractionAgent`가 신뢰할 수 있는 입력 URL만 받도록 만드는 것이 목표다.

## 책임

- 자사 상품과 경쟁 후보를 공식 출처 탐색 대상 목록으로 정리한다.
- 각 대상에 대해 실제 브랜드 공식 도메인 후보를 탐색한다.
- 브랜드 메인 사이트와 상품 상세 페이지를 구분한다.
- 실제 페이지 단위로 최종 URL, 응답 상태, 제목, canonical, 브랜드/상품 매칭 신호를 검증한다.
- 제3자 리뷰, 커뮤니티, 뉴스, 쇼핑몰 페이지를 공식 출처에서 배제한다.
- 도메인 탐색 단계와 페이지 검증 단계를 분리해 기록한다.
- 공식성 판단 근거를 `source_validation`에 구조화해 남긴다.
- 하나의 상품에 대해 여러 공식 출처가 필요하면 함께 반환한다.
- `output.schema.json`에 맞는 정규화된 JSON을 반환한다.

## 비목표

- 상품 기능, 가격, 혜택을 본격 추출하지 않는다.
- 공식 페이지 내용을 비교 리포트 형태로 요약하지 않는다.
- 경쟁 후보 자체를 새로 발굴하지 않는다.
- 비공식 출처를 근거로 사실을 확정하지 않는다.
- 크롤링 구현 상세나 HTML 파싱 전략을 포함하지 않는다.

## 입력 계약

입력 payload는 `input.schema.json`을 만족해야 한다.

필수 입력:

- `project_id`
- `own_product.brand`
- `own_product.name`
- `own_product.category`
- `competitor_candidates`

권장 선택 입력:

- `geography`
- `locale`
- `source_preferences`
- `known_official_domains`
- `search_context`
- `validation_preferences`

## 출력 계약

출력 payload는 `output.schema.json`을 만족해야 한다.

핵심 출력:

- `domain_discovery_results`
- `page_validation_results`
- `resolution_targets`
- `official_sources`
- `source_validation`
- `unresolved_targets`

각 `official_sources` 항목에는 최소한 아래 필드가 포함되어야 한다.

- `source_id`
- `target_id`
- `target_type`
- `brand`
- `product_name`
- `source_type`
- `url`
- `domain`
- `page_title`
- `rationale`
- `confidence`
- `needs_validation`

각 `source_validation` 항목에는 최소한 아래 필드가 포함되어야 한다.

- `source_id`
- `target_id`
- `url`
- `verdict`
- `positive_signals`
- `negative_signals`
- `recommended_use`

## 단계 정의

### 1단계: Brand Official Domain Discovery

목표:

- 각 탐색 대상에 대해 실제 공식 도메인 후보를 찾고 우선순위를 매긴다.

입력 활용:

- 브랜드명
- 상품명
- known official domains
- geography / locale
- source preferences

핵심 작업:

- 브랜드와 상품 기준 탐색 쿼리를 설계한다.
- 브랜드 소유 가능성이 높은 도메인 후보를 수집한다.
- 상품 상세 페이지로 이어질 가능성이 있는 공식 도메인/하위 도메인을 정리한다.
- 리뷰/뉴스/제휴/마켓 도메인을 초기 차단한다.

산출물:

- `domain_discovery_results`

### 2단계: Official Page Validation

목표:

- 실제 후보 URL을 열어 페이지 수준에서 공식성과 활용 가능성을 검증한다.

핵심 작업:

- 최종 URL과 리다이렉트 여부를 확인한다.
- HTTP 응답 상태와 접근 가능성을 확인한다.
- 페이지 제목, canonical, 브랜드/상품 문자열 신호를 확인한다.
- 제품 상세, 도움말, FAQ, 가격 페이지 여부를 판단한다.
- 후속 추출에 실제로 쓸 수 있는지 `recommended_use`로 판정한다.

산출물:

- `page_validation_results`
- `official_sources`
- `source_validation`

## 공식성 판별 원칙

### 공식 출처로 우선 고려할 대상

- 브랜드가 직접 운영하는 메인 도메인
- 브랜드 소유 하위 도메인
- 브랜드 공식 도움말 센터 또는 고객지원 페이지
- 특정 상품명과 직접 연결된 공식 상세 페이지
- 가격, 요금, 수수료, FAQ처럼 상품 설명에 직접 필요한 공식 안내 페이지

### 기본 차단 대상

- 언론 기사
- 블로그 리뷰
- 커뮤니티 게시글
- 비교/추천 사이트
- 오픈마켓, 앱마켓, 제휴몰
- 위키류 문서
- 브랜드 공식 계정이더라도 본 agent의 목적에 맞지 않는 소셜 미디어 페이지

## 다중 출처 규칙

- 한 상품당 공식 출처는 1개 이상, 보통 1~3개 범위로 유지한다.
- 가능하면 `official_product_page`를 1순위로 확보한다.
- 상품 상세 페이지가 없으면 `official_site`와 `official_help_center` 조합으로 보완할 수 있다.
- 도움말 페이지가 실제 기능/조건 설명에 더 유용하면 함께 유지한다.

## 품질 규칙

- 도메인 소유 신호가 약하면 `needs_validation: true`로 둔다.
- 실제 페이지 응답을 확인하지 못한 URL은 높은 신뢰도로 확정하지 않는다.
- 공식성이 불충분한 페이지는 `official_sources`에 넣지 않고 `unresolved_targets` 또는 `source_validation.verdict: rejected` 근거로 남긴다.
- 동일 URL 중복을 피한다.
- URL만 맞고 상품 연결성이 약하면 신뢰도를 낮춘다.
- 입력이 부족하더라도 무응답하지 말고 보수적 결과를 반환한다.

## 실행 흐름

1. 자사 상품과 경쟁 후보를 합쳐 탐색 대상 목록을 만든다.
2. 1단계에서 각 대상별 브랜드/상품 기준 공식 도메인 후보를 실제 탐색한다.
3. `domain_discovery_results`에 도메인 후보, 탐색 쿼리, 도메인 선정 근거를 저장한다.
4. 2단계에서 브랜드 메인, 상품 상세, 도움말/FAQ 후보 URL을 실제 페이지 기준으로 검증한다.
5. `page_validation_results`에 응답 상태, 최종 URL, 제목, canonical, 공식성 신호를 저장한다.
6. 공식성 신호와 비공식 신호를 비교해 `official_sources`, `source_validation`을 작성한다.
7. 공식 출처를 확정하지 못한 대상은 `unresolved_targets`에 남긴다.

## 파일 구성

- `spec.md`: 사람이 읽는 설계 명세
- `system_prompt.md`: 영문 모델 지침 원본
- `system_prompt_kr.md`: 한글 모델 지침 참고본
- `input.schema.json`: 입력 검증 스키마
- `output.schema.json`: 출력 검증 스키마
- `config.yaml`: 실행 설정 예시
- `schema_reference.md`: 필드 설명과 예시
