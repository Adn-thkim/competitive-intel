# 시장 컨텍스트 + 자사 SWOT 외부 레퍼런스 정리

> - **목적**: 파일럿 도메인 "토스 트래블카드"의 market_context_swot 리포트 설계를 위해, ① SWOT·TOWS·PESTLE·Porter's Five Forces 등 일반 방법론과 ② 한국 트래블카드 시장 규모·성장률·경쟁 구조를 외부 자료로부터 수집·정리합니다.
> - **활용 범위**: market_context_swot 노드의 4분면 분류·TOWS 액션 도출·시장 컨텍스트 통합 양식 설계 시 참조합니다(본 문서는 레퍼런스 정리만 다루며, 노드 구현 가이드는 별도 작성).
> - **흐름 분류**: market_context_swot은 mid/top 노드 (`pipeline_topology_redesign.md` §11-10 기준). 흐름 A 자체 feature(시장 규모·성장률·정책 동향)와 흐름 B(comparison + reaction + marketing_social 다수 인용) 모두 사용.
> - **문서 버전**: v1.0 | 작성일: 2026-05-19

---

## 1. 방법론 레퍼런스

### 1-1. SWOT — 전략 분석의 4분면 기본

SWOT은 1960년대에 등장한 가장 보편적 전략 분석 framework입니다. 두 차원의 결합으로 정의됩니다.

- **내부 vs 외부**: Strengths·Weaknesses는 내부 요인, Opportunities·Threats는 외부 요인.
- **긍정 vs 부정**: Strengths·Opportunities는 활용 대상, Weaknesses·Threats는 대응 대상.

본 프로젝트는 내부 요인(S/W)을 흐름 B(comparison_matrix·reaction_insight·marketing_social 인용)로 도출하고, 외부 요인(O/T)을 흐름 A 자체 수집(시장 규모·정책·기술 동향)으로 도출합니다. 흐름 A·B 양쪽을 동시 활용하는 본 프로젝트 유일한 리포트입니다.

참조: [Vanderbilt — SWOT, Five Forces & PEST Analysis](https://researchguides.library.vanderbilt.edu/c.php?g=68805&p=449215), [AUT — PEST/Porters/SWOT Analysis Guide](https://aut.ac.nz.libguides.com/c.php?g=205007&p=6861991)

### 1-2. TOWS Matrix — SWOT을 액션으로 변환

전통적 SWOT의 약점은 4분면 항목을 나열만 하고 구체 액션을 제시하지 못한다는 것입니다. Heinz Weihrich가 1980년대 제안한 TOWS Matrix는 SWOT 4분면을 교차시켜 4종 액션 전략을 도출합니다.

| 전략 | 조합 | 의도 |
|---|---|---|
| **SO** (Maxi-Maxi) | Strengths × Opportunities | 강점으로 기회 가속 |
| **WO** (Mini-Maxi) | Weaknesses × Opportunities | 약점 개선해 기회 포착 |
| **ST** (Maxi-Mini) | Strengths × Threats | 강점으로 위협 방어 |
| **WT** (Mini-Mini) | Weaknesses × Threats | 생존 — 약점·위협 동시 완화 |

다수의 S-O 또는 W-T 페어로 뒷받침되는 전략이 우선순위가 높습니다. 일반 조직이 분기당 실행 가능한 전략은 2–4개이며, 그 이상은 초점 분산을 유발합니다. 본 프로젝트는 TOWS 4분면별로 액션 2개씩 도출하고 우선순위를 명시합니다.

참조: [Foundor.ai — TOWS Matrix Extended SWOT Guide](https://foundor.ai/en/blog/tows-matrix-extended-swot-guide), [Bitesize Learning — TOWS Matrix Guide](https://www.bitesizelearning.co.uk/resources/tows-matrix-explained-example), [Kyle Murphy — TOWS Matrix for Strategy 2024](https://kylemurphy.com/2024/02/15/using-the-tows-matrix-for-strategy-formulation/), [Creately — TOWS Matrix Definition](https://creately.com/guides/tows-matrix-guide/), [Professional Academy — Introduction to TOWS](https://www.professionalacademy.com/blogs/an-introduction-to-the-tows-matrix-putting-swot-into-action/)

### 1-3. PESTLE — Macro 환경 6요소

PESTLE은 산업 외부의 macro 환경을 6개 요소로 분석합니다.

- **Political**: 정부 정책·규제 동향
- **Economic**: 경기·환율·소비 지출
- **Social**: 인구·세대·라이프스타일 변화
- **Technological**: 기술 발전·디지털 전환
- **Legal**: 법규·약관·소비자 보호
- **Environmental**: 환경·지속가능성

본 프로젝트는 트래블카드 도메인에 직접 영향이 큰 4요소(Political·Economic·Social·Technological)를 우선 분석하고, Legal·Environmental은 보조로 둡니다.

참조: [AnalystPrep — Porter's Five Forces & PESTLE for CFA](https://analystprep.com/cfa-level-1-exam/equity/porters-five-forces-and-pestle-frameworks/), [Visual Paradigm — Strategic Analysis with PESTLE & Porter](https://online.visual-paradigm.com/knowledge/strategic-tools/pestle-and-five-forces-analysis/), [Al-Kindi Publishers — Holistic Strategic Analysis with PESTEL & Porter (2024)](https://al-kindipublishers.org/index.php/jbms/article/download/9135/7819)

### 1-4. Porter's Five Forces — 산업 경쟁 구조

Michael Porter가 1979년 제안한 산업 구조 분석 framework는 5개 압력을 측정합니다.

1. **Competitive Rivalry** — 기존 경쟁사 간 경쟁 강도
2. **Threat of New Entry** — 신규 진입 위협
3. **Threat of Substitution** — 대체재 위협
4. **Bargaining Power of Suppliers** — 공급자 협상력
5. **Bargaining Power of Buyers** — 구매자 협상력

트래블카드 도메인에서는 **Competitive Rivalry**(11개 카드 격전)와 **Threat of Substitution**(현지 환전소·전통 환전·디지털 지갑)이 핵심 압력이며, 나머지 3개는 영향이 상대적으로 작습니다.

참조: [Business News Daily — Porter's Five Forces](https://www.businessnewsdaily.com/5446-porters-five-forces.html), [B2B International — Competitive Landscape Analysis with Porter (2024)](https://www.b2binternational.com/2024/04/04/competitive-landscape-analysis-porters-five-forces/), [Uncovered — What Are Porter's Five Forces](https://uncovered.so/blog/what-are-porters-five-forces-competitive-analysis-explained)

### 1-5. SWOT + TOWS + PESTLE + Porter 통합 운용

네 framework는 독립적으로 사용하면 빈틈이 생깁니다. 본 프로젝트는 다음 순서로 통합합니다.

1. **PESTLE** → 외부 macro 환경 도출 → SWOT의 O/T 후보
2. **Porter** → 산업 경쟁 구조 도출 → SWOT의 O/T 보강
3. **흐름 B** → 내부 분석(comparison_matrix·reaction_insight·marketing_social) → SWOT의 S/W
4. **SWOT** → 위 셋을 4분면 단일 표로 통합
5. **TOWS** → SWOT 결과를 SO·WO·ST·WT 4종 액션 전략으로 변환

이 5단계 흐름은 단일 framework만 쓸 때보다 빈틈이 작고, 액션 도출까지 일관됩니다.

참조: [Determ — Strategic Frameworks Integration](https://determ.com/blog/track-measure-paid-earned-shared-owned-media/), [Lean Wisdom — SWOT and TOWS for Portfolio Planning](https://www.leanwisdom.com/blog/swot-and-tows-analysis/)

### 1-6. 정량 데이터 통합 — 시장 규모·성장률·점유율

SWOT의 O(Opportunities) 항목은 시장 규모·성장률로 정량화될 때 신뢰도가 가장 높습니다. T(Threats)도 마찬가지입니다.

- **TAM(Total Addressable Market)**: 전체 잠재 시장 규모
- **SAM(Serviceable Addressable Market)**: 자사가 진입 가능한 부분
- **SOM(Serviceable Obtainable Market)**: 자사가 단기 점유 가능한 부분
- **CAGR**: 연평균 성장률

본 프로젝트는 정부·금융감독원·금융결제원·카드사 협회 공식 통계를 1차 소스로 채택합니다. 매체 추정치는 1차 소스가 없을 때만 활용합니다.

---

## 2. 도메인 레퍼런스 — 한국 트래블카드 시장 컨텍스트

### 2-1. 시장 규모·성장률 (2025년 기준)

매체·정부 통계가 보고한 2025년 한국 해외 카드 결제·트래블카드 시장 데이터는 다음과 같습니다.

- **2025년 1분기 해외 체크카드 결제**: 1조 5,742억원 (전년 동기 +48.4%)
- **2025년 1분기 전체 해외 개인 카드 결제**: 4조 9,321억원 (전년 동기 +11.68%)
- **2025년 연간 카드 해외 사용 금액**: 약 33조원 (229.1억 달러, +5.5%)
- **체크카드 비중 증가**: 2030세대 중심으로 환전 수수료 무료 + 연회비 없음 매력으로 폭발적 성장.
- **경쟁 구조**: 매체는 "하나·신한 양강 체제" 표현. 핀테크(트래블월렛·토스)는 후발 도전자.

이 데이터는 SWOT의 O(시장 성장) 항목 정량 근거로 직접 활용됩니다.

참조: [이코노믹데일리 — 1분기 해외 체크카드 강세 (하나·신한 양강 체제)](https://www.economidaily.com/view/20250421145718465), [econmingle — 한국 해외 카드 33조원 사용 2025](https://econmingle.com/news/korea-overseas-card-spending-record-2025/), [전자신문 — 트래블카드 시장 합종연횡](https://www.etnews.com/20250116000185), [Trendmonitor — 환전·트래블카드 U&A 조사](https://trendmonitor.co.kr/tmweb/trend/allTrend/detail.do?bIdx=2967&code=0103&trendType=CKOREA)

### 2-2. 시장 구조 동향 — 합종연횡·신규 진입

2025년 트래블카드 시장의 주요 구조 변화는 다음과 같습니다.

- **합종연횡 가속**: 카드사 + 은행 + 핀테크 간 제휴 확산. 단일 카드 독자 운영보다 협력 모델 증가.
- **체크카드 강세**: 신용카드 대비 진입 장벽 낮음. 2030세대 선호.
- **여행 회복**: COVID 이후 해외여행 정상화로 시장 절대 규모가 빠르게 회복.
- **수수료 제로 평탄화**: 환전·결제 수수료 무료가 차별점이 아닌 기본 요건으로 전환.

이는 SWOT의 T(Threats) 항목 — "수수료 차별성 소실"과 "경쟁사 다수 진입" — 의 근거입니다.

참조: [전자신문 — 트래블카드 시장 합종연횡](https://www.etnews.com/20250116000185), [코리아비즈리뷰 — 2025년 신용카드 시장분석](https://koreabizreview.com/detail.php?number=7059), [카드고릴라 — 2025 신용카드 트렌드 10](https://m.card-gorilla.com/contents/detail/4081)

### 2-3. 사회·기술·정책 동향 (PESTLE 4요소)

- **Political/Legal**: 금융감독원의 외화 선불카드 약관 표준화 움직임. 한시 프로모션 표시 의무 강화 논의.
- **Economic**: 원화 약세 시 해외 결제 부담 증가, 환전 시점 의사결정 중요도 상승.
- **Social**: 2030세대 + 디지털노마드 인구 증가. 일본·동남아 단기 여행 패턴 정착.
- **Technological**: BNPL(Buy Now Pay Later) 글로벌 성장(2024년 95억 달러 → 2033년 801억 달러 추정). 핀테크 융합 확산.

### 2-4. 도메인 특유의 분석 함정

- **시장 규모 ≠ 자사 기회**: 33조원 시장이라도 점유 가능한 SAM은 훨씬 작음. TAM·SAM·SOM 분리 필수.
- **양강 체제 vs 후발 도전**: "하나·신한 양강"이라는 매체 표현이 토스의 시장 진입 어려움을 시사하나, 토스는 본사 핀테크 자산을 활용한 reframing 전략으로 별도 카테고리 진입 가능.
- **시즌성 누락**: 시장 규모를 연간 단일 수치로 보면 시즌 변동(여름 휴가철 2–3배)을 놓침. 분기별·월별 분해 필요.
- **거시 변수의 자사 영향 과대 추정**: PESTLE 6요소 중 일부(예: 환경)는 트래블카드 도메인에 직접 영향이 거의 없음. 4요소만 우선 분석.

---

## 3. 종합 — market_context_swot 권장안 요약

방법론과 도메인 레퍼런스를 종합한 권장 양식은 다음과 같습니다(본 문서는 권장안 요약까지만 다루며, 실제 스키마·노드 구현은 별도 작업).

- **포함 요소**: ① SWOT 4분면(S/W는 흐름 B 인용, O/T는 흐름 A 외부 수집), ② TOWS 4종 액션 전략 + 우선순위, ③ PESTLE 4요소(P·E·S·T) 요약, ④ Porter's 5 Forces 평가, ⑤ 시장 규모·성장률 정량 데이터(TAM/SAM/SOM), ⑥ 시즌성 보정.
- **표기 규약**: 모든 정량 수치는 공식 출처(정부·협회) 1차 소스 우선. 매체 추정치는 1차 소스 부재 시만 사용하고 "추정"으로 명시. SWOT 항목당 흐름 A 또는 흐름 B 출처 명시.
- **양식**: SWOT 4분면 표 + TOWS 4분면 액션 표 + PESTLE 요약 + Porter 5 Forces 도식의 4단 구성. executive_summary로 흐름 B inline 인용.

---

## 4. 결정된 사항 (사용자 확정)

- **결정 1**: market_context_swot은 흐름 A(외부 시장 컨텍스트)와 흐름 B(내부 분석 인용)를 모두 사용하는 mid/top 노드입니다(§11-10 모델). 본 프로젝트 유일.
- **결정 2**: 외부 데이터 1차 소스는 정부·금융감독원·금융결제원·카드사 협회 공식 통계. 매체 추정치는 보조.
- **결정 3**: PESTLE 6요소 중 P·E·S·T 4요소만 우선 분석. L·E는 트래블카드 도메인 영향 적어 보조 처리.
- **결정 4**: TOWS 4분면 액션은 분기당 2–4개로 제한. 우선순위는 다중 S-O 또는 W-T 페어로 뒷받침되는 전략 우선.

---

## 5. 사용자가 추가로 검토할 만한 꼬리 질문

1. SWOT의 O/T 항목 정량화 시 TAM·SAM·SOM을 명시 산출할지, 단순 "시장 성장 중" 같은 정성 표현으로 둘지 결정이 필요합니다. 정량화는 신뢰도 높으나 점유율 데이터 부재로 SOM 추정이 어려울 수 있습니다.
2. TOWS 4분면 액션 우선순위 산정 규칙을 (a) 다중 S-O/W-T 페어 뒷받침 / (b) 액션 실행 비용·기간 / (c) 잠재 임팩트의 정성 점수 중 무엇으로 잡으시겠습니까? 본 worked example은 (a)를 1차 채택하나 사용자가 다른 기준을 선호할 수 있습니다.
3. Porter's 5 Forces 5개 압력 중 트래블카드 도메인 영향이 적은 3개(공급자 협상력·구매자 협상력·신규 진입)를 보조로 둘지, 모두 동등 분석할지 결정이 필요합니다. 보조 처리는 worked example 분량 절감 효과가 있으나 framework 일관성이 약해집니다.
