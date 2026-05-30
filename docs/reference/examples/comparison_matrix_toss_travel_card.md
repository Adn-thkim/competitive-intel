# Worked Example — `comparison_matrix` × 토스 트래블카드

> - **목적**: Rubric `docs/reference_report_taxonomy.md` §2-1(비교 매트릭스)의 추상 기준이 트래블카드 도메인에서 어떻게 구체화되는지를 시연합니다.
> - **도메인 슬러그**: `consumer_travel_card_kr`
> - **자사 상품**: 토스 트래블카드 (`own_toss_travel_card`)
> - **분석 방향**: `mixed` (marketing + product_dev 양쪽 라벨링)
> - **출처 자료 기준일**: 2026-05-19
> - **작성일**: 2026-05-19

---

## 1. 적용된 Rubric §2-1 항목 (요약 인용)

- **리포트 목적**: 의사결정자에게 자사·경쟁사의 정량·정성 차이를 단일 표에서 식별 가능하게 함.
- **표준 feature 카테고리(5종)**: 가격(수수료) · 핵심 기능 커버리지 · 부가 혜택 · 발급/사용 조건 · 사용자 경험.
- **평가 루브릭**: 카테고리 5종 모두 채움(필수) / 좋은 feature 비율 ≥ 80% / 액션 가능성 라벨 부착 ≥ 90%.
- **좋은 feature 조건**: 정량 측정 가능 + URL에서 직접 추출 가능 + 액션 동사 1개 이상에 매핑.
- **나쁜 feature 조건**: 추상적·심리 척도(`product_quality`, `user_satisfaction`), 외부 검증 불가(`market_share` 자의적 추정), 동일 의미 중복(매수·매도 합산).

---

## 2. 도출된 feature 목록 (10개)

|  #  | feature_id                        | 카테고리   | action_lens | 근거 URL                                                                                                                                                                                                                                                                                                                                                                       | 근거 인용                                     |
| :-: | --------------------------------- | ------ | :---------: | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------- |
|  1  | `supported_currency_count`        | 핵심 기능  |    both     | [Travel Wallet 공식](https://www.travel-wallet.com/en)                                                                                                                                                                                                                                                                                                                         | "One card, 46 currencies"                 |
|  2  | `exchange_fee_primary_currencies` | 가격     |  marketing  | [카드고릴라 — 환전 서비스 비교 2026](https://m.card-gorilla.com/contents/detail/2867)                                                                                                                                                                                                                                                                                                    | "달러·유로·엔화 환전 수수료 0%"                      |
|  3  | `re_exchange_fee_rate`            | 가격     | product_dev | [Holafly — 트래블월렛·트래블로그 비교 2026](https://esim.holafly.com/ko/travel-tips/travel-log-travel-wallet-comparison/)                                                                                                                                                                                                                                                                | "재환전 시 0.5%~1% 수수료"                       |
|  4  | `overseas_payment_fee_exempt`     | 가격     |  marketing  | [토스피드 — 해외 카드 수수료 안내](https://toss.im/tossfeed/article/traveling-budget-4)                                                                                                                                                                                                                                                                                                   | "국제 브랜드 수수료 1% 면제"                        |
|  5  | `atm_withdrawal_limit_daily_usd`  | 핵심 기능  | product_dev | [Holafly — 트래블월렛·트래블로그 비교 2026](https://esim.holafly.com/ko/travel-tips/travel-log-travel-wallet-comparison/)                                                                                                                                                                                                                                                                | "트래블월렛 1일 USD 1,000 / 트래블로그 1일 USD 6,000" |
|  6  | `atm_fee_free_count_monthly`      | 핵심 기능  | product_dev | [카드고릴라 — 해외 수수료 면제 체크카드 6선](https://m.card-gorilla.com/contents/detail/848)                                                                                                                                                                                                                                                                                                  | "월 N회 ATM 수수료 면제"                         |
|  7  | `lounge_benefit_scope`            | 부가 혜택  |  marketing  | [뱅크샐러드 — 해외결제 카드 BEST 5](https://www.banksalad.com/articles/2023-%ED%95%B4%EC%99%B8%EA%B2%B0%EC%A0%9C-%EC%B9%B4%EB%93%9C-BEST-3)                                                                                                                                                                                                                                             | "하나 트래블로그 라운지 분기 N회"                      |
|  8  | `cashback_or_mileage_rate`        | 부가 혜택  |    both     | [KKday — 트래블로그·트래블월렛 비교](https://www.kkday.com/ko/blog/31760/world-travelwallet)                                                                                                                                                                                                                                                                                             | "트래블로그 결제 3% 적립"                          |
|  9  | `issuance_channel_app_only`       | 발급/조건  | product_dev | [Weolbu — 환전 수수료 없는 트래블카드 추천](https://weolbu.com/community/3287510/%ED%99%98%EC%A0%84-%EC%88%98%EC%88%98%EB%A3%8C-%EC%97%86%EB%8A%94-%ED%8A%B8%EB%9E%98%EB%B8%94%EC%B9%B4%EB%93%9C-%EC%B6%94%EC%B2%9C-2025%EB%85%84-8%EC%9B%94-%EC%B5%9C%EC%8B%A0-%ED%95%98%EB%82%98-%EC%8B%A0%ED%95%9C-%EC%9A%B0%EB%A6%AC-%ED%86%A0%EC%8A%A4-%EB%84%A4%EC%9D%B4%EB%B2%84%ED%8E%98%EC%9D%B4) | "토스 트래블카드 앱 단독 발급"                        |
| 10  | `recharge_other_bank_linkage`     | 사용자 경험 |    both     | [KKday — 트래블로그·트래블월렛 비교](https://www.kkday.com/ko/blog/31760/world-travelwallet)                                                                                                                                                                                                                                                                                             | "타은행 계좌 연동 충전 가능"                         |

---

## 3. 평가 루브릭 점수 산정

- **표준 카테고리 커버리지**: 5/5 — 가격(2, 3, 4) · 핵심 기능(1, 5, 6) · 부가 혜택(7, 8) · 발급/조건(9) · 사용자 경험(10) 모두 포함.
- **좋은 feature 비율**: 10/10 — 모든 feature가 정량 측정 가능, URL에서 직접 추출 가능, 1개 이상의 액션 동사에 매핑됨.
- **액션 가능성 라벨 부착**: 10/10 — 모든 feature에 `action_lens`(marketing / product_dev / both) 부착.
- **종합 점수**: **5.0 / 5.0** — Rubric §2-1 기준 통과.

---

## 4. Anti-pattern 회피 사례

본 도메인에서 매체들이 반복적으로 지적하는 4가지 함정과 본 worked example의 회피 방식은 다음과 같습니다.

- **"환전 수수료 무료"의 비대칭**: 매수와 매도를 단일 항목으로 묶지 않고, `exchange_fee_primary_currencies`(매수, marketing lens)와 `re_exchange_fee_rate`(매도, product_dev lens) 두 feature로 분리했습니다. 대부분의 카드가 매수는 0%여도 매도에서 0.5~1%를 부과합니다.
- **프로모션성 한시 혜택의 영구 혜택 혼합**: 한시 혜택(예: KB 트래블러스 2025-12-31 면제, 우리 위비트래블 2024-12-31 면제)은 feature 값의 `notes` 필드에 종료일을 명시하고, 본 점수 산정에서 영구 혜택과 분리 가중합니다. 본 예시는 영구 혜택 기준 점수만 표기했습니다.
- **ATM 한도 단일 수치 함정**: 한도(`atm_withdrawal_limit_daily_usd`)와 면제 횟수(`atm_fee_free_count_monthly`)를 두 feature로 분리해 "한도가 낮으면 인출 횟수 증가 → 실효 수수료 상승"이라는 상호작용을 추론 가능하게 했습니다.
- **지원 통화 수 ≠ 실효 유용성**: 정량값(`supported_currency_count`)만으로 평가하지 않습니다. Use case 다중 뷰(`pipeline_topology_redesign.md` §6-0 Rubric §2-1 참조) 단계에서 "여행 빈도 상위 5개 통화 우대율"을 보조 지표로 활용해 가중 합산합니다.

---

## 5. 본 worked example 사용 시 주의 (메타 지시)

본 예시는 트래블카드 도메인의 사례이며, 본인 도메인에 맹목적으로 복제하지 마십시오. Rubric §2-1의 추상 기준(5개 표준 카테고리, 평가 기준)이 우선이며, 본 예시는 "트래블카드에서는 카테고리가 이렇게 매핑되었다"라는 구체화 사례로만 활용하십시오. 다른 도메인은 동일 추상 기준에서 다른 feature 집합이 도출됩니다.

---

## 6. 관련 문서

- 방법론 reference: `docs/reference_comparison_matrix.md` (Crayon · Klue · Gartner Critical Capabilities · Weighted Scoring · Harvey Balls 일반 framework)
- Rubric 본체: `docs/reference_report_taxonomy.md` §2-1(비교 매트릭스 정의), §3(액션 가능성 동사 집합)
- 파이프라인 설계: `docs/design/pipeline_topology_redesign.md` §6-0(P0-Rubric), §11-10(worked example 작성·제공 방식)
