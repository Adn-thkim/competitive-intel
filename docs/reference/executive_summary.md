# Executive Summary 외부 레퍼런스 정리

> - **목적**: 파일럿 도메인 "토스 트래블카드"의 executive_summary 리포트 설계를 위해, ① Pyramid Principle·SCQA·SCR·BLUF·McKinsey 컨벤션 등 일반 방법론과 ② 7개 리포트 결과의 통합 양식을 외부 자료로부터 수집·정리합니다.
> - **활용 범위**: executive_summary 노드의 BLUF 작성·통합 구조·bold-bullet 표기 규약 설계 시 참조합니다(본 문서는 레퍼런스 정리만 다루며, 노드 구현 가이드는 별도 작성).
> - **흐름 분류**: executive_summary는 top 노드 (`pipeline_topology_redesign.md` §11-10 기준). 흐름 A 자체 feature ✗, 흐름 B로 다른 6개 리포트 결과를 모두 인용. 본 프로젝트 유일의 순수 통합 리포트.
> - **문서 버전**: v1.0 | 작성일: 2026-05-19

---

## 1. 방법론 레퍼런스

### 1-1. Executive Summary의 정의·범위

executive summary는 의사결정자가 **1–2분 안에 전체 분석의 결론과 권고**를 파악할 수 있도록 압축한 1–2 페이지 통합 문서입니다. 다른 6개 리포트의 결론을 단순 나열하지 않고, **의사결정에 필요한 핵심만 추출**해 재배치합니다.

- **분량 제약**: 1–2 페이지 또는 1–2 슬라이드. 이 한계를 넘으면 "executive summary"가 아닙니다.
- **대상 독자**: 시간 자원이 부족한 의사결정자(CEO·CMO·PM). 분석 세부 사항보다 **무엇을 해야 하는가**에 관심.
- **읽기 방식**: 의사결정자는 처음부터 끝까지 읽지 않습니다. **bold 텍스트만 스캔해도 전체 논지를 이해**할 수 있어야 합니다.

본 프로젝트의 executive_summary는 6개 상류 리포트의 결과를 흐름 B로 인용하나 **인용 그 자체는 본문에 노출하지 않습니다**. 결론과 권고만 노출하고, 근거는 출처 링크로 연결합니다.

참조: [Slideworks — How to Write Executive Summary like McKinsey](https://slideworks.io/resources/how-to-write-executive-summary), [Slidemodel — McKinsey Presentation Structure](https://slidemodel.com/mckinsey-presentation-structure/), [Slidescience — Executive Summary Templates](https://slidescience.co/executive-summary/)

### 1-2. Pyramid Principle (Barbara Minto, 1970s McKinsey)

Barbara Minto가 1970년대 McKinsey에서 정립한 Pyramid Principle은 의사결정자 커뮤니케이션의 표준 구조입니다.

- **Top-Down 구조**: 가장 위에 핵심 주장(assertion), 그 아래에 그를 뒷받침하는 논거(arguments), 가장 아래에 데이터(data).
- **One-Sentence Top**: 피라미드 꼭대기는 단 한 문장. 모든 하위 논거가 이 한 문장을 뒷받침.
- **MECE 논거**: Mutually Exclusive, Collectively Exhaustive — 논거 간 중복 없고 전체를 포괄.
- **3–5 논거 룰**: 인간의 단기 기억 한계로 논거는 3–5개가 최적. 7개를 넘으면 기억 효율이 급락.

본 프로젝트는 executive_summary 본문 첫 줄에 단일 문장 주장(BLUF)을 두고, 그 아래에 3–5개 논거를 bold-bullet로 배치합니다.

참조: [BetterUp — Minto Pyramid Principle Explained](https://www.betterup.com/blog/minto-pyramid), [Management Consulted — Pyramid Principle Applied](https://managementconsulted.com/pyramid-principle/), [StrategyU — Pyramid Principle Book Review](https://strategyu.co/pyramid-principle-partone/), [Strategypunk — Minto Pyramid Principle PDF](https://www.strategypunk.com/the-minto-pyramid-principle-how-to-communicate-like-a-mckinsey-consultant-pdf/)

### 1-3. SCQA / SCR Framework — 스토리 구조

Pyramid Principle의 본문 구조는 SCQA(Situation·Complication·Question·Answer) 또는 SCR(Situation·Complication·Resolution) 4단계 스토리로 풀어쓰여집니다.

| 요소 | 역할 | 분량 비중 |
|---|---|:-:|
| **Situation** | 도메인·시장 컨텍스트, 사실·수치 | 10–15% |
| **Complication** | 문제·도전·긴장. 의사결정이 필요한 이유 | 15–20% |
| **Question** | Complication에서 도출되는 핵심 질문 (생략 가능) | 5% |
| **Answer / Resolution** | 권고·다음 단계 | **60–70%** |

핵심은 **Answer 비중이 60–70%**라는 점입니다. Situation·Complication에 분량을 과도 배정하면 의사결정자가 "그래서 어떻게 하란 말인가"의 답을 빠르게 찾지 못합니다.

본 프로젝트는 SCR(Resolution 중심) 표준을 채택하되, Question은 명시적으로 1줄 두어 의사결정 초점을 강조합니다.

참조: [Management Consulted — SCQA Framework](https://managementconsulted.com/scqa-framework/), [Slidemodel — SCQA Framework Guide](https://slidemodel.com/scqa-framework-guide/), [Antonov — SCQA Framework Explained](https://antonov.com.au/scqa-framework), [Indeed — What Is SCQA](https://www.indeed.com/career-advice/career-development/scqa)

### 1-4. BLUF — Bottom Line Up Front

BLUF는 군대·정부 보고서에서 유래한 작성 규약으로, 의사결정자가 첫 문장만 읽어도 본질을 파악할 수 있게 합니다.

- **첫 문장 = 결론**: "토스 트래블카드는 단기 여행자 시장에서 1순위 추천이며, 다음 분기 마케팅 액션 4가지를 권고합니다" 같은 단일 문장.
- **두 번째 문장 = 핵심 근거 요약**: "재환전 0원 단독 + 앱 UX 만족도 94% + 메시지 공백 발견."
- **나머지 = 상세 논거**: 근거의 디테일, 페르소나별 변형, 위험 요인.

본 프로젝트의 worked example은 BLUF를 §0 또는 §1 첫 줄에 명시 배치합니다.

참조: [BetterUp — Minto Pyramid Principle](https://www.betterup.com/blog/minto-pyramid), [Mental-Models — Minto Pyramid Principle](https://mental-models.com/minto-pyramid/)

### 1-5. Bold-Bullet 표기 표준

McKinsey·BCG·Bain의 executive summary는 일관된 시각 규약을 따릅니다.

- **Bold 문장**: 핵심 주장(claim). 의사결정자가 bold만 스캔해도 전체 논지 파악 가능.
- **Bullet 항목**: 각 claim을 뒷받침하는 데이터·근거. 일반 텍스트.
- **숫자 우선**: bullet 안에 정량 수치(%, 금액, 횟수)를 노출.
- **출처 inline**: 매 claim마다 흐름 B 출처를 짧게 표기 (예: "comparison_matrix §3").

본 프로젝트는 이 4개 규약을 worked example에 그대로 적용합니다.

참조: [Slideworks — How McKinsey Consultants Make Presentations](https://slideworks.io/resources/how-mckinsey-consultants-make-presentations), [Deckary — Executive Summary Slides: How to Write Like McKinsey](https://deckary.com/blog/executive-summary-slides), [JeffSu — Presentation Techniques from McKinsey, Bain, BCG](https://www.jeffsu.org/presentation-techniques-from-mckinsey-bain-and-bcg/)

### 1-6. Decision-maker 중심 작성 — "So What?" 테스트

executive_summary의 각 문장은 의사결정자의 "그래서?"(so what?) 질문에 답해야 합니다.

- **나쁜 문장**: "5개 카드 중 토스의 만족도는 4위(69.8%)입니다."
- **좋은 문장**: "토스 만족도 4위는 라운지 부재의 인식 편향에서 비롯되며, 라운지 점유 없이도 만족도를 끌어올릴 메시지 공백("여행 후 잔액 환급 0원")이 존재합니다."

차이는 단순 사실 진술(나쁜)과 액션 시사점이 명시된 진술(좋은)입니다. 본 프로젝트는 각 bullet에 "so what?"의 답을 1개 이상 포함합니다.

참조: [Slideworks — Pyramid Principle Toolbox](https://slideworks.io/resources/the-pyramid-principle-mckinsey-toolbox-with-examples), [Think Insights — SCQA Logic](https://thinkinsights.net/strategy/scqa-logic)

---

## 2. 도메인 레퍼런스 — 트래블카드 executive summary 통합 양식

### 2-1. 의사결정자 페르소나 — executive_summary의 대상 독자

본 프로젝트는 분석을 마케터·PM가 활용한다는 전제이나, executive_summary는 더 좁은 의사결정자 페르소나를 대상으로 합니다.

- **CEO/COO**: 사업 전체 우선순위. 본 분석은 "토스 트래블카드를 본사 자원의 어느 우선순위에 둘 것인가" 답을 제공.
- **CMO/마케팅 헤드**: 분기 캠페인 의사결정. 본 분석은 "어느 메시지·채널에 예산을 배정할 것인가" 답을 제공.
- **PM/Product 헤드**: 제품 로드맵 우선순위. 본 분석은 "어떤 기능을 다음 분기 추가할 것인가" 답을 제공.

executive_summary 한 페이지에 위 3개 페르소나가 모두 답을 찾을 수 있어야 하며, 답의 출처(흐름 B 상류 리포트)는 링크로만 노출합니다.

### 2-2. 6개 리포트 결론의 통합 양식

본 프로젝트의 6개 상류 리포트 결론을 다음과 같이 압축합니다.

| 상류 리포트 | 핵심 결론 1줄 |
|---|---|
| comparison_matrix | "재환전 0원·결제 한도·앱 UX 강점, 부가 혜택 부재" (정량) |
| reaction_insight | "라운지 부재의 인식 영향이 가장 크고, 앱 UX 만족도는 1위" (인식) |
| marketing_social | "외부 노출 채널 부재 + '여행 후 잔액 환급' 메시지 공백" (채널·메시지) |
| battlecard | "Winning 3·Battling 4·Losing 4 — 페르소나 단기 여행자에 최강" (FIA Zones) |
| positioning_map | "페르소나 1에서 좌측 극단 단독, 페르소나 2에서 좌측 하단으로 후퇴" (좌표) |
| market_context_swot | "시장 +48.4% 성장, SO1·SO2 분기 1순위 액션" (시장·전략) |

executive_summary는 이 6줄을 단순 나열하지 않고, **공통 결론 + 페르소나별 권장 + 액션 우선순위**로 재구성합니다.

### 2-3. 도메인 특유의 분석 함정

- **6개 리포트 단순 나열 오류**: 페르소나·시점·우선순위를 통합하지 않고 6개 결론을 그대로 붙이면 의사결정자가 "그래서 무엇을 해야 하는가" 답을 찾지 못함.
- **세부 데이터 과다 노출**: 상류 리포트의 정량 수치(예: aspect별 sentiment 비율)를 모두 본문에 옮기면 분량이 폭증. 출처 링크로 처리.
- **페르소나 평균화**: 단기·장기·디지털노마드 페르소나를 평균낸 단일 권장은 누구에게도 적합하지 않음. 페르소나별 분기 권장 필수.
- **시점 표기 누락**: executive_summary는 스냅샷이므로 시점을 명시하지 않으면 다음 분기 재실행 시 어느 버전의 결론인지 추적 불가.

---

## 3. 종합 — executive_summary 권장안 요약

방법론과 도메인 레퍼런스를 종합한 권장 양식은 다음과 같습니다(본 문서는 권장안 요약까지만 다루며, 실제 스키마·노드 구현은 별도 작업).

- **포함 요소**: ① BLUF (1–2 문장 결론), ② Situation (시장 컨텍스트, 3–4 bullet), ③ Complication (자사 위치 문제, 2–3 bullet), ④ Question (1줄 의사결정 초점), ⑤ Answer/Resolution (60–70% 분량, 페르소나별 권장 + 액션 우선순위), ⑥ 6개 상류 리포트 cross-link.
- **표기 규약**: bold-bullet 구조 일관 적용. 매 claim에 "so what?" 답 포함. 출처는 본문에 짧게 표기 후 §관련 문서에서 링크. 시점 명시.
- **양식**: 1–2 페이지 한정. SCR 분량 비중(Resolution 60–70%) 준수. 의사결정자 3개 페르소나(CEO/CMO/PM) 모두 답을 찾을 수 있는 구성.

---

## 4. 결정된 사항 (사용자 확정)

- **결정 1**: executive_summary는 흐름 A 자체 feature 없이 흐름 B로만 동작하는 top 노드입니다(§11-10 모델). 본 프로젝트 유일.
- **결정 2**: SCR Framework(Situation·Complication·Resolution) 채택. Resolution 분량 60–70%.
- **결정 3**: Pyramid Principle + BLUF 적용. 첫 문장은 단일 결론, bold-bullet 표기 일관.
- **결정 4**: 페르소나 3개(단기 여행자 / 장기 체류자 / 디지털노마드) 권장을 모두 포함하되, 분량 차등(단기 1순위 더 상세).

---

## 5. 사용자가 추가로 검토할 만한 꼬리 질문

1. executive_summary의 BLUF는 단일 페르소나 기준일지(단기 여행자), 페르소나 가중 통합일지 결정이 필요합니다. 단일 페르소나 BLUF는 명료하나 다른 페르소나 의사결정자에게 적합도가 떨어지고, 가중 통합 BLUF는 추상적이 됩니다.
2. 6개 상류 리포트의 결론이 충돌하는 경우(예: positioning_map은 토스 1위, marketing_social은 외부 노출 약점) 어느 쪽을 executive_summary의 결론으로 채택하시겠습니까? 충돌 자체를 본문에 노출할지, 가중 통합 후 단일 결론으로 표기할지의 선택입니다.
3. executive_summary가 다른 6개 리포트로의 link만 두고 본문 분량을 최소화할지, 본문에 핵심 데이터(예: 만족도 수치·시장 규모)를 inline 인용하여 link 없이도 의사결정 가능하게 할지 결정이 필요합니다. 후자는 가독성 높으나 분량이 증가합니다.
