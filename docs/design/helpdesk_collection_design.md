# 고객센터/FAQ 수집 설계 — feature_mapping_helpdesk lane + 공식·고객센터 충돌 해소

> - **상태**: DRAFT — 사용자 검토 후 확정 예정
> - **작성일**: 2026-07-19
> - **시리즈**: report generation 시리즈 — feature 근거 소스 확장 (official 계열에 helpdesk lane 추가)
> - **선행 근거**:
>   - `docs/design/feature_extraction_node_design.md` (official_content_collection · 5중 fan-out 토폴로지)
>   - `agents/official_source_resolver/helpdesk_domains.json` (공용 헬프데스크 SaaS 11종 · 현재 미배선)
>   - PoC: `scripts/poc_helpdesk_brave_or.py` (Brave OR 쿼리 타당성·라이브 검증 완료)
> - **대상 파일(예정)**:
>   - 신규: `server/graph/nodes/url_discovery_helpdesk_node.py`, `server/graph/nodes/feature_mapping_helpdesk_node.py`
>   - 신규: `agents/feature_mapping_helpdesk/system_prompt_kr.md` + `output.schema.json`
>   - 수정: `server/graph/state.py`, `server/graph/nodes/_feature_mapping_runner.py`, `server/graph/graph.py`, `server/cache_ttls.yaml`
>   - 배선: `server/graph/nodes/official_source_resolver_node.py`(`_filter_official_urls`가 helpdesk_domains.json 로드)

---

## 1. 문서 목적과 범위

비교 매트릭스 각 feature의 값을 추출할 URL을 탐색하는 과정에, **고객센터/FAQ를 확정적으로 병렬 수집**하는 신규 소스 lane(`feature_mapping_helpdesk`)을 추가하고, **공식 사이트 데이터와 고객센터 데이터가 상충할 때의 채택 규칙**을 정의한다.

**다루는 범위**: helpdesk lane의 토폴로지 배선(§3), URL 탐색 로직의 PoC→프로덕션 이관(§4), 공식 vs 고객센터 충돌 해소 규칙(§5), 캐싱·에러·검증(§6), 미해결 과제(§7).

**다루지 않는 범위**: `feature_comparison`(TODO) 노드의 LLM 프롬프트 상세 — 본 문서는 helpdesk 소스의 산출 계약과 충돌 규칙의 배치 위치까지만 정의한다.

---

## 2. 배경 · 현황 진단

**2-1. 현재 고객센터는 명시적으로 수집되지 않는다.** `official_source_resolver`·`feature_url_mapper` 어디에도 고객센터 전용 경로가 없다. `helpdesk_domains.json`(channel.io·zendesk·intercom 등 11종)은 `_meta`상 `_filter_official_urls`가 쓰도록 설계됐으나, **실제 코드가 이 파일을 로드하지 않는 미배선(dead config)** 상태다.

**2-2. 그럼에도 채널톡 헬프센터는 우연히 유입돼 있다.** 캐시 전수조사 결과 `docs.channel.io/{brand}` URL이 `url_discovery_brave`·`url_validation`에 71개 파일 존재(나머지 10개 SaaS는 0건). 즉 국내는 channel.io가 사실상 독점이며, 일반 Brave 탐색이 부수적으로 긁어온 것이다.

**2-3. PoC 라이브 검증 결과(2026-07 기준).** candidate당 "자사 도메인 + SaaS 2~3개"를 `site: OR`로 묶고 `고객센터 FAQ` 키워드를 실은 단일 쿼리(≈90자)로:

- **정답 확보**: 토스(`support.toss.im/faq`), 하나(`m.hanacard.co.kr/MKFAQ1010M.web`), 트래블월렛(`docs.channel.io/travelwallet` 아티클 다수).
- **실패**: 신한 — `/pconts/html/helpdesk/...` 8건이 `✅`로 잡혔으나 **실제로는 죽은 페이지**(거짓 양성)이고, 진짜 FAQ(`/mob/MOBFM147N/...`)는 경로에 CS 힌트가 없어 누락(거짓 음성).

**2-4. 실측된 소스 비대칭.** 트래블월렛(핀테크)은 channel.io FAQ가 수수료·ATM 한도까지 구조화·정적 fetch 가능한 반면, 공식 약관 페이지는 빈 응답(앱 내부 고지). 하나·신한(전통 카드사)은 공식 페이지가 봇 차단/무거운 JS이고 공개 고객센터도 상담·운영시간 위주. **→ "공식이 항상 정본"이 아니며, 소스 권위는 발급 주체 유형에 의존한다.**

---

## 3. lane 배선 설계 — `feature_mapping_helpdesk`

기존 5-source lane(official·blog_community·youtube_reactions·owned_channels·macro)과 **동일 패턴**으로 6번째 소스를 추가한다. 신규 메커니즘이 아니라 `run_source_mapping()` 규약에 소스 하나를 등록하는 방식이라 침습이 작다.

**3-1. state 키 추가** (`server/graph/state.py`):

```
helpdesk_urls_by_candidate: dict[str, list[dict[str, Any]]]   # url_discovery_helpdesk 산출
helpdesk_raw_features:      list[dict[str, Any]]              # feature_mapping_helpdesk 산출
```

**3-2. `_feature_mapping_runner.py` 딕셔너리 등록** — 3개 매핑에 `"helpdesk"` 키를 추가한다:

```
_OUTPUT_STATE_KEY["helpdesk"] = "helpdesk_raw_features"
_INPUT_URLS_KEY["helpdesk"]   = "helpdesk_urls_by_candidate"
_STEP_NAME["helpdesk"]        = "FeatureMappingHelpdesk"
```

그리고 `feature_url_mapper_node._REPORT_TYPES_BY_SOURCE`에 `helpdesk`가 담당할 report_type(우선 `comparison_matrix`)을 추가한다.

**3-3. 신규 노드 2종** (기존 소스와 대칭):

- `url_discovery_helpdesk_node.py` — §4의 Brave OR 탐색 + 콘텐츠 fetch 판정으로 `helpdesk_urls_by_candidate` 산출.
- `feature_mapping_helpdesk_node.py` — 본체는 `run_source_mapping("helpdesk", state, config)` 한 줄. 나머지는 러너가 처리.

**3-4. 에이전트 파일** (`agents/feature_mapping_helpdesk/`): `system_prompt_kr.md`(고객센터 FAQ 본문 → feature 값 매핑 규칙) + `output.schema.json`(official lane 스키마를 기준으로, 각 feature 값에 §5의 provenance 필드 추가).

**3-5. 토폴로지 위치.** `url_discovery_helpdesk` → `feature_mapping_helpdesk` → `helpdesk_raw_features`가 기존 5종과 함께 `additional_urls_validation_node`(또는 후속 `feature_comparison`)의 union 대상이 된다. **병렬성**: 다른 소스와 fan-out으로 동시 실행하므로 전체 지연 증가는 없다(candidate당 Brave 1~2쿼리 + fetch 추가).

---

## 4. URL 탐색 로직 — PoC → 프로덕션 이관

**4-1. 쿼리 구성(PoC 확정, 제안 1·3 반영).** candidate당 단일 Brave 쿼리:

```
{브랜드} 고객센터 FAQ site:{자사도메인} OR site:{SaaS1} OR site:{SaaS2} OR site:{SaaS3}
```

- **자사 도메인은 추측하지 않는다** — `official_sources.json`의 primary host에서 결정론적으로 확보(토스=toss.im 등).
- **SaaS 후보만 좁힌다** — `helpdesk_domains.json` 11종 중 LLM이 2~3개 선별(§7-1의 어댑터 경유). 실패 시 실측 상위(channel.io·zendesk·intercom) 폴백. 12개 전량 OR 대비 쿼리 복잡도·400자 한도 압박을 해소.
- **FAQ 키워드**로 고객센터성 페이지에 랭킹 편향. recall 저하 시 `자주 묻는 질문` 동의어 확장(§7-3).

**4-2. 분류기를 "경로 판정"에서 "콘텐츠 fetch 판정"으로 승격 (신한 유형 해결).**

현행 PoC 분류기는 URL 경로 토큰(`helpdesk`·`faq` 등)만 본다. 이 때문에 (a) 죽은 페이지를 걸러내지 못하고(신한 거짓 양성 8건), (b) 정답이 불투명 코드 URL이면 놓친다(신한 거짓 음성). **경로만으로는 원리적으로 해결 불가**하므로, Brave가 반환한 URL을 실제로 fetch해 유효성을 판정한다. 3단 게이트:

1. **HTTP 게이트** — `url_cache`의 HTTP 검증(`http_validation_minutes` 캐시) 재사용. 200번대만 통과, 3xx→최종 URL 추적, 4xx/5xx·삭제 페이지 탈락. (신한 거짓 양성 제거)
2. **콘텐츠 게이트** — `official_content_fetch` 패턴으로 본문을 가져와 "FAQ/Q&A 구조 신호"(질문-답변 반복, `자주 묻는 질문`·`FAQ` 헤딩, 아티클 목록) 존재 여부 확인. 본문이 비었거나(핀테크 SPA) 신호가 없으면 강등.
3. **브랜드 귀속 게이트** — 공용 SaaS는 host/path에 브랜드 토큰(별칭 포함) 일치 요구(타 브랜드 고객센터 오인 방지). `helpdesk_domains.json._meta.match_rule`과 동일.

경로 토큰 매칭은 게이트가 아니라 **랭킹 가점**으로만 강등한다. 이로써 신한 정답(`/mob/MOBFM147N`)이 경로 힌트가 없어도 콘텐츠 게이트로 살아남는다.

**4-3. `official_source_resolver` 배선(부수 작업).** helpdesk lane과 별개로, `_filter_official_urls`가 `helpdesk_domains.json`을 로드해 `docs.channel.io/{brand}` 유형을 "보조 공식 출처"로 정식 인정하도록 배선한다(현 dead config 해소). 이미 캐시에 유입된 채널톡 URL을 추가 quota 없이 활용할 수 있다.

---

## 5. 공식 vs 고객센터 충돌 해소 규칙

**소스 타입으로 우선순위를 고정하지 않는다.** §2-4에서 확인했듯 "공식이 항상 정본"은 핀테크에서 깨진다. 대신 **신선도를 1차 축, feature 카테고리 권위를 2차 축**으로 둔다. 배치 위치는 5종 raw_features를 병합하는 지점(`additional_urls_validation` union → 후속 `feature_comparison` 노드).

**5-1. provenance 필수 부착.** 모든 추출 값에 `{source_type, source_url, effective_date}`를 담는다. 공식 약관·수수료는 개정일/시행일, FAQ·공지는 게시일/최종수정일을 파싱. 미추출 시 `effective_date=null`.

**5-2. 카테고리 권위 사전.** feature를 두 부류로 분류한다.

- **구속형(정형)**: 수수료·환율·한도·약관 조건 → 공식 페이지가 정본, FAQ는 보조.
- **설명형**: 발급 방법·사용 흐름·UX·팁 → FAQ 동급 이상 허용.

**5-3. 값 단위 채택 규칙**(우선순위 순):

1. 값 **일치** → 채택 + 교차검증으로 confidence 상향.
2. 값 **상충 & 양측 날짜 존재** → **더 최신 effective_date 채택**(잘 관리된 FAQ가 더 최신인 케이스 포착). 단 구속형에서 FAQ가 더 최신이어도 공식이 "시행 예정" 명시거나 격차가 크면 자동 채택 금지 → `conflicts` 승격.
3. 값 **상충 & 일방만 날짜** → 근거 있는(날짜 있는) 쪽 우선.
4. 값 **상충 & 양측 날짜 없음** → 카테고리 권위 사전으로 결정(구속형→공식, 설명형→FAQ) + confidence 하향 + `conflicts` 기록.

**5-4. FAQ 신선도 오염 방어.** FAQ 수정일이 공식 개정일보다 **과거이면서 값이 상충**하면 FAQ 자동 강등. FAQ에 날짜가 아예 없으면 구속형 값의 근거로는 채택 불가(설명 보강용으로만). "FAQ가 최신을 유지한다"는 낙관을 코드가 신뢰하지 않게 한다.

**5-5. 핀테크 예외.** 공식 약관 fetch가 빈 응답(SPA/앱 내부 고지)인 candidate는, 구속형 항목도 FAQ를 정본으로 승격한다(트래블월렛 유형). 판별 신호: 공식 fetch 본문 공백 + FAQ 콘텐츠 게이트 통과.

**5-6. 무음(silent) 덮어쓰기 금지.** 채택하지 않은 값도 provenance와 함께 보존하고, 구속형 상충은 `official_content_collection`의 기존 `conflicts` 필드에 적재 → `needs_manual_review=True`로 흐르게 한다(신규 메커니즘 아님, 기존 경로 확장).

---

## 6. 캐싱 · 에러 · 검증

**캐싱.** `cache_ttls.yaml`에 `helpdesk_hours: 7200` 신설(현 300일 정책 일관). Brave 검색은 기존 brave 캐시, fetch는 `official_content_fetch` 캐시/HTTP 검증 캐시 재사용.

**에러.** lane은 기존 노드와 동일하게 try-except로 감싸 개별 실패가 파이프라인을 중단시키지 않게 한다. 비치명 오류는 `errors` 누적 필드에 `{node, error, timestamp}`로 적재. helpdesk 소스 전량 실패해도 나머지 5종 소스로 리포트는 생성된다(graceful degradation).

**검증.** (1) PoC 4개 candidate 회귀 — 토스·하나·트래블월렛 정답 유지 + 신한 거짓 양성 0·정답 포착. (2) 콘텐츠 게이트 단위 테스트 — 죽은 URL/빈 본문/타 브랜드 SaaS를 각각 탈락시키는지. (3) 충돌 규칙 테스트 — 구속형·설명형 × 날짜 유무 4케이스가 §5-3대로 분기하는지.

---

## 7. 미해결 과제

**7-1. LLM `BadRequestError` (SaaS 선별 경로 복구).** PoC는 `anthropic` SDK를 직접 호출하며 `model="claude-sonnet-5"`를 썼으나, 본 프로젝트 계정의 모델은 `config.API_MODEL="claude-sonnet-4-6"`다. 존재하지 않는 모델명이라 API가 400으로 거부 → 매 호출 폴백. **프로덕션에서는 raw SDK 호출을 버리고 프로젝트 규약대로 `ClaudeApiAnalyzer.call_with_schema(prompt, output_schema)`(결정론 경로, model 기본값=API_MODEL)를 경유**한다. 이러면 모델명·재시도·스키마 강제가 일원화된다.

**7-2. SPA/봇 차단 fetch.** 신한·하나 공식 페이지는 정적 fetch 불가. 콘텐츠 게이트(§4-2)가 빈 본문을 SPA로 판정해 강등하되, `feature_extraction` 설계의 Playwright SPA fallback(v0.11 위임)과 정합적으로 후속 처리한다.

**7-3. FAQ 키워드 recall.** `FAQ` 단일어가 "자주 묻는 질문"만 쓰는 페이지를 놓칠 수 있어, 라이브 recall 측정 후 동의어 확장 여부를 결정한다.

**7-4. 대표 URL 축약 정책.** 트래블월렛처럼 정답이 다수 아티클인 경우, 대표 고객센터 URL 1건으로 축약할지 아티클 다수를 feature 근거로 넘길지 결정(후자가 feature 근거 관점 유리).
