# 비교 매트릭스 외부 레퍼런스 정리

> - **목적**: 파일럿 도메인 "토스 트래블카드"의 경쟁사 비교 매트릭스 설계를 위해, ① 일반 방법론과 ② 도메인 실제 사례를 외부 자료로부터 수집·정리한다.
> - **활용 범위**: feature_extraction · feature_comparison 노드의 비교 항목·평가 척도·시각 표기 규약 설계 시 참조 (본 문서는 레퍼런스 정리만 다루며, 노드 구현 가이드는 별도 작성).
> - **문서 버전**: v1.0 | 작성일: 2026-05-14

---

## 1. 방법론 레퍼런스

### 1-1. Feature Comparison Matrix (제품관리·CI 표준 양식)

가장 보편적으로 쓰이는 양식은 **행=경쟁사, 열=평가 항목(feature/category)** 의 2차원 매트릭스다. Productside, Crayon, Contify, Coefficient 등 CI(Competitive Intelligence) 솔루션 벤더가 공통적으로 권장하는 구조는 다음과 같다.

- **카테고리화**: 단순 기능 나열을 피하고, "고객 의사결정 기준"에 맞춘 카테고리로 그룹핑한다 (예: Pricing / Core Capability / Integration / Support).
- **차별화 축 강조**: 자사가 우위/열위/동등인 영역을 색상이나 아이콘으로 즉시 식별 가능하게 한다.
- **가중치(weighting) 적용**: 모든 항목을 동등 비교하지 않고, 시장 중요도에 따라 가중치를 부여한다 — 후술하는 Weighted Scoring Model 참조.

참조: [Crayon — How to Create a Competitive Matrix](https://www.crayon.co/blog/competitive-matrix-examples), [Productside — Free Competitive Matrix](https://productside.com/free-competitive-matrix-feature-comparison-chart/), [Contify — Competitive Matrix Guide](https://www.contify.com/resources/blog/competitive-matrix/), [Coefficient — Competitive Matrix Template](https://coefficient.io/templates/competitive-matrix-template)

### 1-2. Battlecard 양식 (영업 인에이블먼트 관점)

Battlecard는 비교 매트릭스를 "한 페이지에서 읽고 판단 가능한" 형태로 응축한 양식이다. Klue, Apollo, HubSpot, Crayon, Federico Presicci, Product Marketing Alliance 등이 공유하는 공통 구성 요소는 다음과 같다.

- **Overview** — 경쟁사 배경, 메시징, 가격대, 핵심 win/switch 스토리.
- **Winning / Battling / Losing Zones** — 자사 우위/접전/열위 영역의 명시적 구분. 매트릭스의 색상 코딩과 동일한 사상.
- **Objection Handling** — 경쟁사가 자주 제기하는 반박과 대응 멘트.
- **Proof Points** — 비교 주장의 근거(데이터·고객 사례·공식 페이지 인용).
- **Living Battlecard** — 정적 PDF 대신 갱신 가능한 모듈형 자산으로 운용 (Crayon 2024 트렌드).

본 프로젝트의 인사이트 리포트는 battlecard의 "Winning/Battling/Losing Zone" 구분과 "Proof Points 인용" 패턴을 차용하기에 적합하다.

참조: [Klue — Sales Battlecards 101 (2025)](https://klue.com/blog/competitive-battlecards-101), [Apollo — Sales Battlecard Template](https://www.apollo.io/insights/sales-battlecard-template), [HubSpot — Battle Cards in Sales](https://blog.hubspot.com/sales/battle-cards), [Crayon — Modern Battlecard Blueprint](https://www.crayon.co/blog/modern-battlecard-blueprint), [Product Marketing Alliance — Battlecard Template](https://www.productmarketingalliance.com/sales-battlecard-template-framework/)

### 1-3. Gartner Critical Capabilities — Use case 기반 가중치 모델

Gartner는 Magic Quadrant(2축 포지셔닝)와 Critical Capabilities(다항목 점수화)를 보완 관계로 운용한다. Critical Capabilities는 본 프로젝트의 feature 매트릭스 설계에 직접적인 시사점을 준다.

- **Use case 정의 우선**: 시장의 핵심 use case를 먼저 정의하고, 각 use case별 critical capability를 도출한다.
- **5점 Likert 척도**: 1(poor) ~ 5(outstanding) 척도로 capability를 평가한다.
- **Use case별 가중치**: 동일한 capability라도 use case에 따라 다른 가중치를 적용해 점수를 재계산할 수 있도록 한다 (대화형 조정).

이 구조는 "토스 트래블카드" 비교에서도 use case별 (예: 단기 여행자 / 장기 체류자 / 디지털노마드) 가중치 차별화가 의미 있음을 시사한다.

참조: [Gartner — Critical Capabilities Methodology](https://www.gartner.com/en/research/methodologies/research-methodologies-gartner-critical-capabilities), [Gartner — Magic Quadrant Methodology](https://www.gartner.com/en/research/methodologies/magic-quadrants-research), [Magic Quadrant FAQ (PDF)](https://www.gartner.com/imagesrv/pdf/magic_quad_faq.pdf)

### 1-4. Weighted Scoring Model — 점수 산정 규약

각 feature 점수에 가중치를 곱해 합산하는 모델이다. ProductPlan, Product School, Userpilot, Savio 등이 공통적으로 제시하는 운영 규칙은 다음과 같다.

- **일관된 척도**: 1–5 또는 1–10 중 하나를 선택해 전 항목에 동일하게 적용한다.
- **명시적 루브릭**: "1점 / 3점 / 5점에 해당하는 예시 답안"을 사전 정의해 평가자 편향을 줄인다 (rubric-as-rewards 연구도 동일 결론).
- **Criterion 독립성**: 항목 간 의미 중복(double-counting)을 피한다 — 예컨대 "환전 수수료"와 "총 결제 비용"을 별도 항목으로 두지 않는다.
- **신뢰 구간 보고**: 단일 평균값 대신 변동 폭(bootstrap CI 등)을 함께 제시한다 — LLM-as-a-judge 평가에서도 권장되는 패턴.

참조: [ProductPlan — Weighted Scoring](https://www.productplan.com/glossary/weighted-scoring), [Product School — Weighted Scoring Model Guide](https://productschool.com/blog/product-fundamentals/weighted-scoring-model), [Savio — Weighted Scoring Model Calculator](https://www.savio.io/product-roadmap/weighted-scoring-model/), [Userpilot — Weighted Scoring Model](https://userpilot.com/blog/weighted-scoring-model/)

### 1-5. Harvey Balls — 정성 비교의 시각 표기 규약

Harvey Balls는 1970년대 Booz Allen Hamilton의 Harvey Poppel이 고안한 원형 아이콘 체계로, "정량화하기 애매한 정성 평가"를 5단계(공백·1/4·반·3/4·완전 채움)로 표현한다. 본 프로젝트에서 "지원함/일부 지원/제한적 지원"처럼 이분법으로 표현하기 어려운 항목(예: 라운지 혜택의 폭, 앱 UX 완성도)에 적합하다.

- **장점**: 정량 점수의 단정성을 피하면서 직관적 우열 비교 가능.
- **주의**: 어느 단계가 어떤 의미인지(예: 3/4 = "주요 통화만 지원") 범례를 반드시 함께 명시해야 함.

참조: [Wikipedia — Harvey Balls](https://en.wikipedia.org/wiki/Harvey_balls), [Minitab — Harvey Balls Visual Comparisons](https://blog.minitab.com/en/blog/harvey-balls-some-of-the-best-presenting-visual-comparisons-you-might-not-have-even-heard-of), [Compint — Competitive Matrix with Harvey Balls](https://compint.co/templates/free-competitive-matrix-with-harvey-balls-template)

---

## 2. 도메인 레퍼런스 — 트래블카드/외화 선불카드 실제 비교 사례

### 2-1. 업계·언론·핀테크 매체의 공통 비교 축

카드고릴라, 뱅크샐러드, 토스피드, KKday, Holafly, AJD, 클리앙, 나무위키 등 다수 매체가 트래블카드를 비교할 때 반복적으로 사용하는 축은 다음 9가지로 수렴한다.

1. **지원 통화 수** — 토스뱅크 17종, 트래블월렛 46종(주요 3종 무료), 하나 트래블로그 58종, KB 트래블러스 33종, 우리 위비트래블 30종 등.
2. **환전 수수료(매수)** — "주요 N개 통화 무료" / "그 외 0.5%~2.5%" 구조가 일반적.
3. **재환전 수수료(매도)** — 매수 100% 우대여도 재환전 시 0.5~1% 부과되는 카드가 다수.
4. **해외 결제 수수료** — VISA/Mastercard 국제 브랜드 수수료(1%) + 해외 서비스 수수료(건당 USD 0.5) 면제 여부.
5. **ATM 출금 한도·수수료** — 건당/일별/월별 한도와 면제 ATM 네트워크.
6. **충전·환급 방식** — 앱 내 즉시 충전, 다른 은행 계좌 연동 가능 여부, 잔액 환급 방식.
7. **적립·캐시백** — 트래블로그의 3% 적립 같은 부가 혜택.
8. **연계 부가 혜택** — 공항 라운지, 여행자보험, 해외 이심(eSIM) 할인.
9. **발급·실적 조건** — 연회비, 전월 실적, 발급 채널(앱 단독/은행 영업점 병행).

이 9개 축은 본 프로젝트 taxonomy의 `feat_*` 후보로 그대로 매핑 가능하다.

참조: [카드고릴라 — 여행 특화 카드별 환전서비스 비교 2026](https://m.card-gorilla.com/contents/detail/2867), [뱅크샐러드 — 2026 해외결제 카드 추천 TOP 5](https://www.banksalad.com/articles/2023-%ED%95%B4%EC%99%B8%EA%B2%B0%EC%A0%9C-%EC%B9%B4%EB%93%9C-BEST-3), [토스피드 — 해외 카드·ATM 수수료 안내](https://toss.im/tossfeed/article/traveling-budget-4), [KKday — 트래블로그·트래블월렛 비교 최신판](https://www.kkday.com/ko/blog/31760/world-travelwallet), [Holafly — 트래블로그·트래블월렛 비교 2026](https://esim.holafly.com/ko/travel-tips/travel-log-travel-wallet-comparison/), [AJD — 트래블카드 체크카드 7종 비교](https://www.ajd.co.kr/contents/basic-tip/detail/%ED%8A%B8%EB%9E%98%EB%B8%94%EC%B9%B4%EB%93%9C_%EB%B9%84%EA%B5%90_%EC%B6%94%EC%B2%9C_|_%EC%97%AC%ED%96%89_%EA%B0%80%EA%B8%B0_%EC%A0%84_%EA%BC%AD_%EC%95%8C%EC%95%84%EC%95%BC_%ED%95%A0_%ED%8A%B8%EB%9E%98%EB%B8%94%EC%B9%B4%EB%93%9C_%EC%B2%B4%ED%81%AC%EC%B9%B4%EB%93%9C_7%EC%A2%85_%EB%AA%A8%EB%91%90_%EB%B9%84%EA%B5%90!-70597), [클리앙 — 외화 선불카드 간단 비교](https://www.clien.net/service/board/lecture/18806850), [나무위키 — 외화 선불카드](https://namu.wiki/w/%EC%99%B8%ED%99%94%20%EC%84%A0%EB%B6%88%EC%B9%B4%EB%93%9C)

### 2-2. 매체별 표현 양식의 차이

- **카드고릴라·AJD**: 행=카드, 열=feature 의 전통적 표 양식. 수치는 그대로 노출하되, 핵심 차별점만 굵게.
- **뱅크샐러드**: "단점 보완 추천" 식으로 자사 추천을 위해 시나리오(여행자 페르소나)별 best-fit 카드를 추천하는 narrative 양식.
- **Holafly·KKday**: 2개 카드(트래블월렛 vs 트래블로그)만 1:1로 비교하는 head-to-head 양식. 짧고 의사결정에 직결.
- **나무위키**: "지원 통화", "수수료 구조" 등 정합성 검증된 사실 위주의 reference 양식. 출처 각주 풍부.

본 프로젝트는 LangGraph 인사이트 리포트의 가독성 측면에서 **카드고릴라형 표 양식 + 뱅크샐러드형 페르소나 추천 + battlecard의 Winning/Losing Zone** 을 혼합하는 것이 자연스럽다.

참조: [뱅크샐러드 — 트래블월렛 단점 보완 BEST 3](https://www.banksalad.com/articles/%ED%8A%B8%EB%9E%98%EB%B8%94%EC%9B%94%EB%A0%9B-%ED%95%B4%EC%99%B8%EA%B2%B0%EC%A0%9C%EC%B9%B4%EB%93%9C-%ED%8A%B8%EB%9E%98%EB%B8%94%EC%B9%B4%EB%93%9C-%ED%99%98%EC%A0%84%EC%88%98%EC%88%98%EB%A3%8C-%EB%AC%B4%EB%A3%8C), [Holafly — 비교 양식 예시](https://esim.holafly.com/ko/travel-tips/travel-log-travel-wallet-comparison/), [Tourtoctoc — 사용 꿀팁 형식](https://www.tourtoctoc.com/news/articleView.html?idxno=1774)

### 2-3. 도메인 특유의 비교 함정

레퍼런스를 종합하면, 트래블카드 비교에서 매체들이 반복적으로 지적하는 "오해를 부르는 비교 항목"은 다음과 같다.

- **"환전 수수료 무료"의 비대칭성** — 매수 시 무료여도 매도(재환전)에서 수수료가 발생하는 경우가 많아, "수수료 무료" 단일 항목으로 비교하면 왜곡이 생긴다.
- **프로모션성 면제 기간 혼동** — KB 트래블러스의 2025-12-31 한정, 우리 위비트래블의 2024-12-31 한정 등 시점 의존적 혜택은 영구 혜택과 분리 표기해야 한다.
- **ATM 한도의 실효성** — 건당 한도가 낮으면 인출 횟수 증가로 실효 수수료가 늘어나므로, 한도 단일 수치보다 "한도 × 면제 횟수"를 함께 봐야 한다.
- **지원 통화 수 ≠ 실효 유용성** — 58종 지원이라도 실제 여행 빈도가 높은 통화에서 우대율 차이가 있을 수 있다.

이 4가지는 본 프로젝트의 비교 매트릭스에서 **각주(footnote) 또는 보조 컬럼**으로 명시해야 할 항목이다.

참조: [Holafly — 트래블월렛/트래블로그 한도 비교](https://esim.holafly.com/ko/travel-tips/travel-log-travel-wallet-comparison/), [Weolbu — 환전 수수료 없는 트래블카드 추천(2025년 8월)](https://weolbu.com/community/3287510/%ED%99%98%EC%A0%84-%EC%88%98%EC%88%98%EB%A3%8C-%EC%97%86%EB%8A%94-%ED%8A%B8%EB%9E%98%EB%B8%94%EC%B9%B4%EB%93%9C-%EC%B6%94%EC%B2%9C-2025%EB%85%84-8%EC%9B%94-%EC%B5%9C%EC%8B%A0-%ED%95%98%EB%82%98-%EC%8B%A0%ED%95%9C-%EC%9A%B0%EB%A6%AC-%ED%86%A0%EC%8A%A4-%EB%84%A4%EC%9D%B4%EB%B2%84%ED%8E%98%EC%9D%B4), [카드고릴라 — 해외 수수료 면제 체크카드 6선](https://m.card-gorilla.com/contents/detail/848)

---

## 3. 종합 — 비교 매트릭스 권장안 (요약)

방법론과 도메인 레퍼런스를 종합한 권장 양식은 다음과 같다 (본 문서는 권장안 요약까지만 다루며, 실제 스키마·노드 구현은 별도 작업).

- **포함 요소**: ① 비교 카테고리(가격/핵심 기능/부가 혜택/발급 조건/사용자 경험), ② feature별 정량값·정성 등급, ③ Use case별 가중치(단기/장기/디지털노마드), ④ Winning·Battling·Losing Zone 색상 코딩, ⑤ Proof Point 인용(공식 홈페이지 URL), ⑥ 도메인 함정에 대한 각주.
- **표기 규약**: 정량 값은 원본 수치 그대로 + 단위 명기, 정성 등급은 Harvey Balls 5단계 + 범례 동봉, 점수는 1–5 Likert 척도, Proof Point는 공식 출처 링크.
- **양식 혼합**: 카드고릴라형 표 양식 + 뱅크샐러드형 페르소나 추천 + Battlecard식 Winning/Losing Zone 요약 카드.

---

## 4. 사용자가 추가로 검토할 만한 꼬리 질문

1. 본 프로젝트의 매트릭스를 **단일 표 양식**으로 유지할지, **use case별로 가중치를 달리한 다중 뷰**(예: 단기 여행자 뷰 / 장기 체류 뷰)로 구성할지 — Gartner Critical Capabilities는 후자를 권장하는데, 사용자 인터페이스 복잡도와 트레이드오프가 존재한다.
2. 정량 데이터(수수료·한도)와 정성 데이터(앱 UX·라운지 분위기)를 **하나의 점수**로 합산할지, **이원 표기**(수치 + Harvey Balls)로 유지할지 — 합산은 직관적이지만 평가자 편향 위험이 크다.
3. 프로모션성 혜택(예: "2025-12-31 한도 면제")을 **별도 컬럼**으로 분리할지, **본 항목의 각주**로 처리할지 — 매트릭스 수명(refresh 주기)에 영향을 준다.
