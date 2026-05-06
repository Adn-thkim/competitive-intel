# QueryIntakeAgent 명세

## 목적

`QueryIntakeAgent`는 웹 검색창에서 입력된 짧은 검색어를 해석해, 사용자가 검토하고 수정할 수 있는 `CompetitorDiscoveryAgent`용 초안 입력(draft input)으로 변환하는 agent이다.

이 agent의 핵심 역할은 "최종 입력 확정"이 아니라 "사용자 확인이 가능한 구조화 초안 생성"이다.

## 역할 정의

- 사용자의 짧은 검색어를 도메인 분석 관점에서 해석한다.
- 검색어에서 자사 상품, 브랜드, 카테고리, 문제 정의를 가능한 범위에서 추론한다.
- `CompetitorDiscoveryAgent`에 전달할 입력 초안을 구조화한다.
- 추론에 기반한 필드는 `assumptions`, `uncertain_fields`, `needs_user_confirmation`로 함께 표시한다.
- 웹 UI가 그대로 렌더링할 수 있는 검토용 메타데이터를 함께 반환한다.

## 비목표

- 경쟁 후보를 직접 식별하지 않는다.
- 공식 출처를 탐색하지 않는다.
- 기능 비교를 수행하지 않는다.
- 최종 리포트를 생성하지 않는다.
- 사용자가 확인하기 전 draft를 확정 입력으로 간주하지 않는다.

## 권장 웹 흐름

1. 사용자가 검색창에 간단한 검색어를 입력한다.
2. `QueryIntakeAgent`가 검색어를 해석해 draft input을 생성한다.
3. 웹 UI가 draft를 폼 형태로 보여준다.
4. 사용자가 필드를 확인하고 수정한다.
5. 수정된 결과를 `CompetitorDiscoveryAgent`의 실제 입력으로 전달한다.

## 입력 계약

입력 payload는 `input.schema.json`을 만족해야 한다.

필수 입력:

- `request_id`
- `raw_query`

권장 선택 입력:

- `geography_hint`
- `locale`
- `ui_context`
- `known_context`

## 출력 계약

출력 payload는 `output.schema.json`을 만족해야 한다.

핵심 출력:

- `draft_competitor_discovery_input`
- `display_fields`
- `assumptions`
- `uncertain_fields`
- `needs_user_confirmation`

## 설계 원칙

- 사용자 입력이 짧을수록 추론은 보수적으로 한다.
- 불확실한 값은 숨기지 않고 명시적으로 표시한다.
- downstream agent가 기대하는 필드 구조에 최대한 맞춘다.
- 웹 폼에서 그대로 편집 가능하도록 평탄하고 설명 가능한 필드를 반환한다.

## 예시 시나리오

사용자 입력:

```text
토스 트래블카드
```

예상 해석:

- 자사 상품명: `토스 트래블카드`
- 브랜드: `토스`
- 카테고리: `travel payment card`
- 도메인명: `해외 결제/환전 특화 카드`
- 문제 정의: `해외여행 시 환전과 결제를 간편하고 유리하게 처리하고 싶다`

이 값들은 자동 확정이 아니라, 사용자가 수정 가능한 초안으로 반환되어야 한다.
