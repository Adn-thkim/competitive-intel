# 배틀카드 외부 레퍼런스 정리

> - **목적**: 파일럿 도메인 "토스 트래블카드"의 배틀카드 리포트 설계를 위해, ① FIA·Winning Zones·Win/Loss 등 일반 방법론과 ② 트래블카드 실제 경쟁 메시징·강약점을 외부 자료로부터 수집·정리합니다.
> - **활용 범위**: `feature_extraction`·`feature_comparison`·`insight_report` 노드의 배틀카드 출력 양식 설계 시 참조합니다(본 문서는 레퍼런스 정리만 다루며, 노드 구현 가이드는 별도 작성).
> - **문서 버전**: v1.0 | 작성일: 2026-05-19

---

## 1. 방법론 레퍼런스

### 1-1. 배틀카드의 정의와 1페이지 원칙

배틀카드는 경쟁 비교 정보를 "한 페이지에서 즉시 판단 가능한 형태로 응축"한 영업·마케팅 보조 자산입니다. Klue, Crayon, Apollo, HubSpot, Federico Presicci, Product Marketing Alliance가 공통적으로 강조하는 원칙은 다음과 같습니다.

- **1페이지 제약**: 영업이 통화 중에도 스캔 가능해야 하므로 한 페이지를 초과하면 사용률이 급락합니다.
- **속도 우선 디자인**: 헤더 + 짧은 bullet + 명확한 섹션 구분. 화려한 디자인·밀집 표는 피합니다.
- **5개 질문 응답**: "이 경쟁사는 누구인가 / 우리가 어디서 이기는가 / 어디서 지는가 / 가격은 어떤가 / 어떻게 응대하는가" 5개 질문에 즉시 답해야 합니다.

본 프로젝트의 배틀카드 리포트는 위 1페이지 제약을 디지털 단위(스크롤 1회)로 재해석합니다.

참조: [Klue — Sales Battlecards 101 (2025)](https://klue.com/blog/competitive-battlecards-101), [Apollo — Sales Battlecard Template](https://www.apollo.io/insights/sales-battlecard-template), [HubSpot — Battle Cards in Sales](https://blog.hubspot.com/sales/battle-cards), [Federico Presicci — Competitive Battlecards Framework](https://federicopresicci.com/blog/sales-enablement-content/competitive-battlecards/)

### 1-2. FIA Framework — 항목 구성의 표준 단위

Klue가 제안하고 업계가 표준으로 채택한 FIA(Fact / Impact / Act) 프레임워크는 배틀카드의 모든 항목이 다음 3-tuple로 구성되어야 한다고 정의합니다.

- **Fact** — 검증 가능한 사실. 출처 URL과 인용이 필수입니다. 예: "트래블월렛은 USD/EUR/JPY 3개 통화만 매수 수수료 0%, 그 외 0.5~2.5% 부과."
- **Impact** — "그래서 어쩌라고(so what)"의 명시적 답. 고객이 경쟁사를 선택했을 때의 부정적 결과 또는 자사를 선택했을 때의 긍정적 결과를 명확히 진술합니다. 예: "동남아·중남미 여행객은 트래블월렛에서 환전 수수료 부담을 인지하지 못하고 추가 비용 발생."
- **Act** — 영업 담당자가 즉시 사용 가능한 행동 지시. 멘트(talk track), 질문(discovery question), 후속 자료(follow-up) 중 하나 이상이어야 합니다. 예: "발견 질문: '주로 어느 국가로 여행하시나요? 일본 외 다른 지역도 자주 가십니까?'"

본 프로젝트는 FIA 3-tuple을 배틀카드의 **최소 출력 단위**로 채택하며, 단순 비교 표만 출력하는 배틀카드는 anti-pattern으로 간주합니다.

참조: [Klue — Fact, Impact, Act Framework](https://klue.com/blog/fact-impact-act-the-battlecard-framework-you-need-to-be-using), [LinkedIn — Why Vandelay should focus on FIA (Klue)](https://www.linkedin.com/pulse/why-vandelay-industries-should-focus-importing-exporting-klue), [Klue — FIA YouTube 영상](https://www.youtube.com/watch?v=G1qrcNfKJWk)

### 1-3. Winning / Battling / Losing Zones — 우열 구분

비교 매트릭스가 셀 단위 비교에 머무는 반면, 배틀카드는 영역(zone) 단위로 우열을 한눈에 식별합니다.

- **Winning Zone**: 자사가 명확히 우위인 영역. 마케팅 메시지·영업 talk track의 핵심.
- **Battling Zone**: 접전 영역. 사례·proof point 인용으로 차별화 시도.
- **Losing Zone**: 자사가 열위인 영역. 객관적 인정 + 우회 전략(다른 강점으로 의제 전환) 동반 필요.

Losing Zone을 숨기면 영업이 현장에서 의외 질문에 대응하지 못합니다. **명시적 인정 + 우회 전략**이 표준입니다.

참조: [Crayon — Modern Battlecard Blueprint](https://www.crayon.co/blog/modern-battlecard-blueprint), [Gong — Sales Battle Card Template](https://www.gong.io/resources/templates/sales-battle-card-template), [Dock — 24 Best Sales Battlecard Examples](https://www.dock.us/library/sales-battlecard-examples)

### 1-4. Win/Loss Analysis — 배틀카드의 데이터 입력

배틀카드 품질은 입력 데이터의 품질에 종속됩니다. Win/Loss Analysis(WLA)는 거래 종료 직후 구매자 인터뷰로 결정 요인 4–6개를 직접 수집하는 방법론입니다.

- **타이밍 결정성**: 거래 후 14일 내 인터뷰는 다요인 상세 진술. 30일 후에는 서사가 압축되고, 60일 후에는 단순화된 회상으로 정확도가 급락합니다.
- **표본 균형**: Win·Loss·No-decision을 균형 있게 수집해야 편향이 통제됩니다. Growth Velocity 권장 baseline은 20건 이상 인터뷰.
- **WLA → 배틀카드 흐름**: 결정 요인 4–6개가 FIA의 Fact로 직접 변환되고, 인터뷰 quote가 Impact의 근거로 인용됩니다.

본 프로젝트는 거래 단위 WLA 인터뷰를 직접 수행하지 않으나, YouTube·커뮤니티·앱스토어의 사용자 텍스트(reaction_insight 결과)가 WLA의 quote-level 입력을 대체합니다. "왜 토스 대신 트래블월렛을 선택했는가" 같은 비교 댓글은 WLA 인터뷰의 약식 대체로 활용됩니다.

참조: [Klue — Ultimate 7-Step Guide to Win-Loss Analysis (2025)](https://klue.com/blog/win-loss-analysis-guide), [Anova — Win/Loss in CI Strategy (2024)](https://www.theanovagroup.com/2024/10/win-loss-analysis-competitive-intelligence/), [Elevated Signal — Win/Loss Methodology & ROI](https://elevatedsignal.com/insights/win-loss-analysis/), [Infomineo — Competitive Analysis Framework (2025)](https://infomineo.com/services/business-research/market-intelligence/competitive-analysis-framework-a-practitioners-guide-for-enterprise-strategy-teams/)

### 1-5. Role-based Variations — 역할별 차별 카드

현장 사용자가 다를 경우 동일 정보라도 압축 방식이 달라야 합니다. Klue·Crayon이 권장하는 4개 역할별 변형은 다음과 같습니다.

- **BDR(Business Development Rep) 배틀카드**: cold outreach 단계. 30초 안에 차별점을 전달하는 hook 멘트.
- **AE(Account Executive) 배틀카드**: 본 협상 단계. 가격·기능 비교 정밀 데이터.
- **SE(Sales Engineer) 배틀카드**: 기술 평가 단계. integration·API·security 비교.
- **CS(Customer Success) 배틀카드**: 갱신·이탈 방어 단계. switch 비용·migration 부담 강조.

본 프로젝트는 B2C 트래블카드 도메인이므로 위 4종 분류가 그대로 적용되지는 않습니다. 대신 사용자 페르소나별(단기 여행자 / 장기 체류자 / 디지털노마드) 변형이 자연스럽습니다.

참조: [Klue — Sales Battlecards 101](https://klue.com/blog/competitive-battlecards-101), [Content Camel — Battlecard Examples (2026)](https://www.contentcamel.io/blog/sales-battlecard-examples/)

### 1-6. Living Battlecard — 갱신 자동화

2025년 Crayon 데이터에 따르면 **배틀카드의 중위 유효 수명은 45일이며 자동화가 없는 평균 갱신 주기는 90–120일**입니다. 이 격차가 정적 PDF 배틀카드의 한계입니다.

- **자동 갱신 트리거**: 경쟁사 가격 페이지 변경, 신제품 출시 보도자료, 신규 사용자 후기 폭증.
- **AI 보조**: Klue·Crayon 등의 AI 배틀카드 생성기는 raw 출처에서 Fact를 자동 추출하나 Impact·Act는 PMM(Product Marketing Manager)의 큐레이션이 여전히 필요합니다.
- **벤치마크**: 자동화 도입 시 콘텐츠 제작 시간이 60–70% 감소(Klue 2025 State of CI Report).

본 프로젝트의 LangGraph 파이프라인은 본질적으로 Living Battlecard 패턴이며, `insight_report_node`가 매 실행마다 최신 Fact로 배틀카드를 재생성합니다. 단, Impact·Act 부분은 사람 검토(interrupt #4 단계)에서 조정 가능하게 둡니다.

참조: [Crayon — Dynamic Battlecard Updates AI Toolkit](https://www.crayon.co/ai-toolkit/dynamic-battlecard-updates), [Unleash — Competitive Intelligence Tools 2025](https://www.unleash.so/post/competitive-intelligence-tools-in-2025-building-ai-powered-battlecards-that-actually-win-deals), [HireSteve — Automated Battlecard Systems for B2B SaaS PMMs 2026](https://hiresteve.ai/articles/automated-battlecard-systems-saas-pmms-2026), [Klue — AI Battlecard Generator](https://klue.com/blog/ai-battlecard)

### 1-7. Battlecard dedicated features — 단순 재가공만으로는 부족한 이유

배틀카드는 comparison_matrix(정량 비교)와 reaction_insight(사용자 quote)의 **재가공·인용**만으로 작성되어서는 안 됩니다. 두 상류 리포트는 자사·경쟁사의 **현재 사실**을 정리하나, 배틀카드의 핵심 가치인 "경쟁사 메시지에 어떻게 대응할 것인가"는 다음과 같은 **배틀카드 고유(dedicated) feature**를 추가 수집해야 도달 가능합니다.

- **`competitor_marketing_copy`**: 경쟁사 공식 홈페이지·광고에 노출되는 핵심 카피. comparison_matrix의 정량값이 아닌 "어떻게 말하는가"를 포착합니다. 예: 트래블월렛의 "One card, 46 currencies".
- **`competitor_promo_end_date`**: 경쟁사 한시 프로모션의 `valid_until` 만료일. comparison_matrix가 단일 시점 비교에 집중하는 동안 배틀카드가 시점 의존성을 명시 추적합니다.
- **`competitor_switch_story_quote`**: "왜 X에서 Y로 바꿨는가" 형식의 전환 narrative. reaction_insight의 aspect 분류와 다른 차원으로, 카드 간 전환 이벤트를 묘사하는 quote를 수집합니다.
- **`competitor_sales_objection`**: 경쟁사 영업·고객지원·커뮤니티에서 자주 등장하는 반박 멘트. "그렇긴 한데, X 카드에서는…"으로 시작하는 사용자 응답이 이에 해당합니다.
- **`competitor_advertising_channel_mix`**: 경쟁사가 광고·콘텐츠를 노출하는 채널 조합. **marketing_social 리포트가 채널 운영 데이터·콘텐츠 주기·소비 규모·핵심 키워드를 산출하면, battlecard는 그 결과를 흐름 B(Output 인용)로 직접 인용**하여 "어디서 만나면 경쟁이 격렬한가" + "어느 채널·메시지 공백을 활용할 것인가"를 한 페이지에서 보여줍니다. 이는 forward reference("marketing_social 리포트 참조") 없이 inline으로 의사결정 근거를 완결시키는 §11-10 원칙의 직접 적용입니다.

위 5종 중 앞의 4종(`competitor_marketing_copy`·`competitor_promo_end_date`·`competitor_switch_story_quote`·`competitor_sales_objection`)은 §11-10(pipeline_topology_redesign) 흐름 A의 **배틀카드 dedicated feature pool**로 분류되어 Feature Selection UI(§10 D6)에서 배틀카드 카드 헤더 아래에 단독 표시됩니다. 마지막 `competitor_advertising_channel_mix`는 흐름 B로 marketing_social 리포트의 산출물을 인용하므로 dedicated feature가 아닌 **상류 리포트 인용 항목**으로 분류됩니다. 5종 중 2종 이상 누락된 배틀카드는 "comparison_matrix·reaction_insight의 단순 재가공" anti-pattern에 해당합니다.

참조: [Klue — Sales Battlecards 101 (Fact 항목 정의)](https://klue.com/blog/competitive-battlecards-101), [Federico Presicci — Battlecard 5가지 질문](https://federicopresicci.com/blog/sales-enablement-content/competitive-battlecards/), [Dock — 24 Sales Battlecard Examples (Switch Story 활용)](https://www.dock.us/library/sales-battlecard-examples)

---

## 2. 도메인 레퍼런스 — 트래블카드 배틀카드 입력

### 2-1. 토스 트래블카드의 알려진 강점 (Winning Zone 후보)

매체 자료가 공통적으로 인용하는 토스 트래블카드의 차별점은 다음과 같습니다.

- **재환전 수수료 완전 무료** — 트래블카드 11종 중 유일. 매수·매도 모두 우대. 환테크 용도로도 활용 가능. ([bdoginfo](https://bdoginfo.com/entry/%ED%8A%B8%EB%9E%98%EB%B8%94%EC%B9%B4%EB%93%9C-11%EC%A2%85-%EB%B9%84%EA%B5%90-%EC%B4%9D%EC%A0%95%EB%A6%AC-%ED%86%A0%EC%8A%A4%EB%B1%85%ED%81%AC-%ED%98%9C%ED%83%9D%EC%9D%B4-%EC%A0%9C%EC%9D%BC-%EC%A2%8B%EC%9D%84%EA%B9%8C))
- **결제 한도가 가장 큼** — 1일 5천만원, 월 1억원.
- **앱 UX 직관성** — 토스 앱이 별도 설치 없이 트래블카드 기능 통합.
- **17개 통화 외화통장 자동 인출** — 외화 잔액 결제 시 별도 환전 단계 없음.

참조: [bdoginfo — 트래블카드 11종 비교 (토스뱅크 최고?)](https://bdoginfo.com/entry/%ED%8A%B8%EB%9E%98%EB%B8%94%EC%B9%B4%EB%93%9C-11%EC%A2%85-%EB%B9%84%EA%B5%90-%EC%B4%9D%EC%A0%95%EB%A6%AC-%ED%86%A0%EC%8A%A4%EB%B1%85%ED%81%AC-%ED%98%9C%ED%83%9D%EC%9D%B4-%EC%A0%9C%EC%9D%BC-%EC%A2%8B%EC%9D%84%EA%B9%8C), [Bizhankook — 토스 vs 신한 직접 사용기](https://www.bizhankook.com/bk/article/27202), [나무위키 — 토스뱅크카드](https://namu.wiki/w/%ED%86%A0%EC%8A%A4%EB%B1%85%ED%81%AC%EC%B9%B4%EB%93%9C)

### 2-2. 토스 트래블카드의 알려진 약점 (Losing Zone 후보)

매체와 사용자 후기가 공통적으로 지적하는 약점은 다음과 같습니다.

- **공항 라운지 혜택 부재** — 신한 SOL트래블·하나 트래블로그는 라운지 무료.
- **삼성페이·자체페이 미등록** — 해외에서 실물카드 필수.
- **해외 계좌로 무료 송금 불가** — 자행 외화통장으로만 가능.
- **타행 계좌 연결 불가** — 외화통장 별도 개설 필요.
- **앱스토어 만족도 4위(69.8%)** — 트래블월렛·트래블로그(약 82%) 대비 격차.

참조: [millionaire24 — 신한 SOL vs 토스 vs 트래블월렛](https://millionairefrom24.thinkmblog.com/entry/%ED%95%B4%EC%99%B8%EC%97%AC%ED%96%89-%ED%95%84%EC%88%98-%EC%B9%B4%EB%93%9C-%EC%8B%A0%ED%95%9C-sol-vs-%ED%86%A0%EC%8A%A4%EB%B1%85%ED%81%AC-vs-%ED%8A%B8%EB%9E%98%EB%B8%94%EC%9B%94%EB%A0%9B), [헤어트래블 — 해외여행 카드 비교 추천](https://info.heretravel.co.kr/board-post/2201), [뱅크샐러드 — 트래블월렛 단점 보완 BEST 3](https://www.banksalad.com/articles/%ED%8A%B8%EB%9E%98%EB%B8%94%EC%9B%94%EB%A0%9B-%ED%95%B4%EC%99%B8%EA%B2%B0%EC%A0%9C%EC%B9%B4%EB%93%9C-%ED%8A%B8%EB%9E%98%EB%B8%94%EC%B9%B4%EB%93%9C-%ED%99%98%EC%A0%84%EC%88%98%EC%88%98%EB%A3%8C-%EB%AC%B4%EB%A3%8C)

### 2-3. 경쟁사의 핵심 메시징 (Fact 소스)

배틀카드의 Fact 항목은 경쟁사 공식 카피·광고·매체 인용에서 직접 수집합니다.

- **트래블월렛**: "One card, 46 currencies" — 통화 다양성 메시지. 매수 수수료 0%는 USD/EUR/JPY 3종으로 한정.
- **하나 트래블로그**: "58개국 통화 + 3% 결제 적립" — 통화 폭과 적립 결합. 라운지 분기 N회.
- **신한 SOL트래블**: "체크카드 + 공항라운지 연 2회 + 해외 컨택리스 1% 할인" — 부가 혜택 강조.
- **KB국민 트래블러스**: "33개 통화 + 2025-12-31까지 환전 수수료 면제 프로모션" — 한시 혜택.
- **우리카드 위비트래블**: "30개 통화 + 2024-12-31까지 ATM 수수료 면제 프로모션".

참조: [카드고릴라 — 환전 서비스 비교 2026](https://m.card-gorilla.com/contents/detail/2867), [홀라플라이 — 트래블로그·트래블월렛 비교 2026](https://esim.holafly.com/ko/travel-tips/travel-log-travel-wallet-comparison/), [Travel Wallet 공식](https://www.travel-wallet.com/en)

### 2-4. 도메인 특유의 분석 함정

- **한시 프로모션을 영구 강점으로 오인**: KB·우리의 한시 면제 혜택을 자사 정시 혜택과 동일 선상에서 비교하면 잘못된 Impact가 도출됩니다. Fact에 `valid_until` 필드를 의무 기록.
- **"수수료 0%"의 통화 범위 누락**: 트래블월렛 0%는 3종 통화에 한정. "전 통화 무료"로 일반화하면 동남아 여행객을 잘못 안내합니다.
- **라운지 부재 = 가치 없음 오류**: 라운지를 가치 있게 여기지 않는 사용자에게 "수수료 0원"이 더 강력한 메시지일 수 있음. 페르소나별 가치 가중치를 분리해야 합니다.
- **출시 시기 편향**: 토스가 후발 출시로 누적 사용자 후기 수가 적을 뿐, 만족도 절대값이 낮은 것은 아닐 수 있음. Win/Loss 비율 산출 시 표본 크기 정규화 필수.

---

## 3. 종합 — 배틀카드 권장안 요약

방법론과 도메인 레퍼런스를 종합한 권장 양식은 다음과 같습니다(본 문서는 권장안 요약까지만 다루며, 실제 스키마·노드 구현은 별도 작업).

- **포함 요소**: ① 경쟁사 1줄 개요 + 핵심 메시징 인용, ② Winning/Battling/Losing 3개 Zone, ③ Zone 항목마다 FIA 3-tuple(Fact·Impact·Act), ④ 페르소나별(단기 여행자 / 장기 체류자 / 디지털노마드) 변형 3개, ⑤ 한시 혜택 만료일 명시(`valid_until`).
- **표기 규약**: Fact는 출처 URL + 인용 문장 보존. Impact는 1–2 문장 narrative. Act는 talk track / discovery question / follow-up 중 1개 이상 명시. Zone은 색상 코딩(녹·황·적).
- **양식**: 페르소나당 1페이지(스크롤 1회) 분량. comparison_matrix와 cross-link(상세 비교), reaction_insight와 cross-link(사용자 quote 근거).

---

## 4. 사용자가 추가로 검토할 만한 꼬리 질문

1. **페르소나별 변형 개수**를 단일 통합 배틀카드 + 사용 시나리오 토글 / 페르소나당 별도 카드 3개 / 사용자 선택 시 동적 생성 중 어느 형태로 두시겠습니까? 통합형은 일관성이 높고, 분리형은 적합도가 높으나 노드 호출 비용 증가.
2. **한시 프로모션(`valid_until` 도래) 자동 갱신** 시 배틀카드가 어떻게 동작하길 원하십니까? (a) 만료된 Fact를 자동 회색 처리 / (b) 다음 LangGraph 실행 시점에서만 갱신 / (c) 만료 30일 전 알림 후 사람 검토 강제.
3. Losing Zone의 **우회 전략 멘트**를 LLM이 자동 생성하게 할지, **사람이 검토·승인한 후에만 출력**하게 할지 결정이 필요합니다. 자동 생성은 속도가 빠르나 잘못된 우회 멘트가 자사 신뢰도를 훼손할 위험이 있습니다.
