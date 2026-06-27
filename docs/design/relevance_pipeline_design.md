# 관련성 기반 반응 수집·분석 파이프라인 설계 (RP)

> - **상태**: DRAFT — 사용자 결정 반영(2026-06-25). 구현 전 합의·검증 대기.
> - **배경**: 분석 예산의 80~90%가 노이즈(유효율 ~4%). 키워드 게이트가 원시의 58%를
>   버리면서도(통과 41.6%) 광범위어 노이즈는 통과·암묵 유효는 누락. PoC 측정: Haiku/sonnet
>   관련성 분류로 밀도 2~4배·노이즈제거 53~82%, 착시는 "답글만 판정" 프롬프트로 통제됨.
> - **공급 상한 주의**: 현재 수집분의 유효 공급 ≈ 1,200건. 목표(셀당 100, 총 ~3,600)는
>   수집 확대가 동반돼야 하며, 본 설계는 "효율(노이즈 비용↓·밀도↑)" 개선이다.

## 1. 채택 결정

- **RP-D1** 키워드 게이트(`_prefilter_v2`)를 **Haiku 관련성 태깅으로 대체(A)**. 단 하드 드롭이
  아니라 **태그(relevant aspect / none)** 를 부여한다(컷에서 우선순위로 사용 — RP-D3).
- **RP-D2** 대댓글 프롬프트 보강: "**판정 대상은 [답글], [부모]는 맥락**" + 부모 맥락 결합.
  (착시 통제 확인됨 — sonnet·haiku 양쪽.)
- **RP-D3** 컷을 **"관련 우선 정렬 + 잔여 충전"** 으로. 하드컷 금지(오분류 유효 손실 회피).
- **RP-D4** 커뮤니티 **candidate별 캡 제거 → 전량 수집** + 동일 관련성 태깅 + **자체
  MAX_ITEMS=1500**(youtube와 분리 예산).
- **RP-D5** **youtube ABSA ∥ community ABSA 병렬 생성 후 병합**.

## 2. 파이프라인

### 2-1. youtube
1. **수집(전량)**: 영상별 댓글+대댓글, 영상당 캡 해제.
2. **기본 노이즈 필터**(`_filter_basic`, 무료): 길이·이모지·중복 제거. *키워드 게이트는 제거.*
3. **Haiku 관련성 태깅**(배치·최소출력): 댓글별 `relevant aspect_id / none`. 대댓글은
   `[부모]…↳[답글]…` 맥락 결합 + RP-D2 프롬프트.
4. **candidate 귀속**(multi-tagging): 영상→candidate.
5. **컷(RP-D3)**: 관련 태그 우선 + 최신 잔여 충전, **MAX_ITEMS=1500**(스레드 원자).
6. **ABSA**: tuple 추출.

### 2-2. community
캡 제거 → 전량 수집 → 기본필터 → Haiku 관련성 태깅(본문/댓글) → 컷(자체 1500) → ABSA.
(공급이 작아 1500 미충족 예상 — 캡이 아니라 상한일 뿐.)

## 3. 컷 정책 상세 (RP-D3)

스레드 원자 단위 유지. 정렬 키:
1순위 **관련 태그 포함 스레드**(relevant ≥ 1) → 그 안에서 최신순,
2순위 비관련 스레드 → 최신순.
관련 스레드로 예산을 먼저 채우고, 남으면 비관련(최신)으로 **잔여 충전**해 MAX_ITEMS 도달.

- 효과: 관련 우선(밀도↑) + Haiku 오분류 유효를 잔여 충전으로 회수(하드컷 손실 회피).
- 관련이 예산 초과 시: 관련 스레드 중 최신 우선으로 1500 채움(오래된 관련 일부 제외).

## 4. 병렬 ABSA + 병합 (RP-D5)

youtube/community 각각 독립 reaction_analysis 패스(독립 MAX_ITEMS) 병렬 실행 후 병합:
- `reaction_analysis[cid].tuples` = youtube ∪ community (중복 제거).
- `channel_counts`·`post_count`·`sample_size`·`analyzed_size` 채널별 합산.
- 효과: 커뮤니티가 youtube에 밀려나던 문제 해소(독립 예산) + 병렬 처리.

## 5. 통합 지점(파일)

- `youtube_reaction_collection_node.py`: `_prefilter_v2`(키워드) 제거 → 관련성 태깅 호출.
- `community_collection_node.py`: `COMMUNITY_URLS_PER_CANDIDATE` 캡 제거 + 관련성 태깅.
- `reaction_analysis_node.py`: 컷을 priority+fill로 교체, youtube/community 분리 패스 + 병합.
- 신규 유틸 `relevance_tagger.py`: 배치 Haiku 분류(프롬프트·스키마·엔진 선택).

## 5-1. 구현 현황 (2026-06-25)

**RP-D4/D5의 "채널 분리 컷 + 병합" 부분(Option A) 구현 완료**:

- `server/config.py`: `REACTION_ABSA_MAX_ITEMS_YOUTUBE`·`REACTION_ABSA_MAX_ITEMS_COMMUNITY`
  추가(기본 1500/1500).
- `reaction_analysis_node.py`: `_channel_cut(cid, items)` 신설 — youtube/그 외(community)를
  분리해 **각자 예산으로 스레드 원자 컷 → chunk 분할**(chunk는 단일 채널만 포함) →
  이어붙여 반환. step 1이 이를 호출. 캐시·평탄화 병렬·병합(candidate 단위 tuple concat+dedup)은
  기존 메커니즘 그대로 → 두 채널 chunk가 같은 풀에서 병렬 ABSA, 결과는 candidate별로 병합.
- funnel 로그에 `YT kept`·`커뮤 kept` 병기.

**검증**(`scripts/channel_cut_verify.py`, LLM 불필요): 단일 풀 컷(변경 전) vs 채널 분리 컷.
- 커뮤니티 분석활용 **OLD 16 → NEW 158**(후보별 6/2/3/5 → 38/44/39/37 = 입력 전량, 예산 내).
- youtube kept ≤ 1500 유지, chunk 단일 채널, 커뮤니티 전량 보존 — **전 항목 통과**.
- 즉 youtube가 커뮤니티를 밀어내던 크라우드아웃이 해소됨.

## 5-2. 구현 현황 — RP-D1·RP-D3 (2026-06-25)

**RP-D1(관련성 태깅) + RP-D3(priority+fill 컷) 구현 완료(플래그 게이트):**

- `server/config.py`: `REACTION_RELEVANCE_ENGINE`(off|cli|api, **기본 off**)·
  `REACTION_RELEVANCE_MODEL`·`REACTION_RELEVANCE_BATCH` 추가.
- `server/graph/relevance_tagger.py` 신설: Haiku 배치 분류로 item `_relevant` 설정.
  최소출력(aspect/none) · **대댓글 "답글만 판정 + 부모 맥락"**(착시 통제 검증분) ·
  엔진 cli(구독)/api(haiku) 선택 · 배치 실패 시 보수적 통과(_relevant=True).
- `reaction_analysis_node.py`:
  - `build_absa_inputs` youtube item 에 `is_reply` 추가(태거 대댓글 맥락용).
  - step 0(신규): `REACTION_RELEVANCE_ENGINE != off` 면 `tag_relevance` 호출(태깅 실패는
    분석을 막지 않음).
  - `_channel_cut`: **관련 태그 포함 스레드를 안정 정렬로 앞에 두어(최신순 보존) 예산을
    관련 우선 채우고 남으면 비관련 최신으로 충전**(RP-D3). `_relevant` 없으면 기존 최신순과 동일.

**배치 정책**: `REACTION_RELEVANCE_ENGINE` 기본 `off` → **현 운영 동작 무변경**. cli/api 로 켜야
태깅·우선컷이 작동(키워드 게이트 `_prefilter_v2` 는 아직 수집 단계에 유지 — 완전 "replace" 는
드롭풀 라벨링 검증 후).

**검증**(`scripts/relevance_cut_verify.py`, LLM 불필요 — 합성 태그):
- 관련 우선·잔여 충전: 관련 304 전량 보존 + 비관련으로 예산 충전(유효 손실 0) ✓
- 태깅 off → 기존 최신순 컷 동일(하위호환) ✓
- 태거 라벨 매핑·실패 배치 보수 통과 ✓ — **전 항목 통과**.

**충전 정책 플래그**(`REACTION_RELEVANCE_FILL`, 기본 true): true=관련 우선 + 비관련 최신으로
예산까지 충전(같은 비용·유효 손실 0). false=`relevant_only`(관련만 분석 → ABSA 입력·비용 절감,
단 태깅 false-negative 유효 손실). 검증: relevant_only 에서 youtube kept=관련수(충전 안 함) ✓.

**커뮤니티 태깅**: 통합 태거가 `reaction_analysis` step 0 에서 **youtube·community 모두** 태깅하고
`_channel_cut` 가 채널별로 우선/충전 적용 → 커뮤니티도 동일 관련성 처리됨(별도 수집-단계 태깅 불요).
검증: community item 이 태거를 거쳐 `_relevant` 설정됨 ✓.

**미구현(후속)**: 키워드 게이트 완전 제거(replace, 드롭풀 라벨링 후) · 운영 엔진(haiku api)
기본 활성화.

## 5-3. 구현 현황 — 커뮤니티 캡 제거 (2026-06-25)

**RP-D4 "candidate별 캡 제거 → 전량 수집" 구현 완료:**

- `server/config.py`: `COMMUNITY_URLS_PER_CANDIDATE`·`COMMUNITY_URLS_PER_SITE` **기본 0(무제한)**.
- `community_collection_node.py` `select_community_pool`: 캡 `0` 을 `10**9` 로 치환 →
  round-robin 이 사이트 소진까지 진행(`progressed=False` 에서 종료) → **발견 URL 전량 선별**.
  사이트 다양성 round-robin 순서는 유지(특정 사이트 편중 방지).
- 채널 분리 예산(§5-1)과 결합: 커뮤니티는 youtube 와 무관하게 자체 `MAX_ITEMS_COMMUNITY=1500`.

### 수집 불가(공급 산정 제외) — fmkorea / still_fail

Playwright PoC(`scripts/community_playwright_poc.py`, 데스크톱-URL 치환·`--headed` 포함) 결과
**still_fail 16건(fmkorea 12 + ppomppu 4)은 헤드리스/비헤드리스·데스크톱 폼 모두 회복 0건**.
- `fmkorea.com`: `snippet_only`(robots/봇차단) — 본문 수집 불가. **집계 전용**(top_quotes 제외).
- ppomppu still_fail: 모바일 동적 페이지로 데스크톱 치환에도 본문 추출 실패.
- **결론**: 이 URL군은 **수집 불가로 확정, 공급 산정에서 제외**한다. 캡 제거의 실효 회수
  대상은 `both_ok`(정적 추출 가능) URL이며, 이들이 캡 바인딩으로 누락되던 분이다.
- **후속 권장**: still_fail/snippet_only dead URL **네거티브 캐시** — 캡 제거로 매 실행
  재시도되는 비용을 막는다(현재 미구현, 캡 제거의 유일한 부작용).

## 5-4. 영향 점검 — 캡 제거 + 채널 분리 MAX_ITEMS (2026-06-25)

dump(`data/debug/reaction_state.json`) 실측 기준:

| | item 수 | item당 평균 문자 | 채널 char-sum | ABSA chunk(12k) |
|---|---|---|---|---|
| youtube (1500 예산 충전 시) | 1,500 | 79 | ~613k | ~52 |
| community (현재, 캡 era 158) | 158 | 555 | ~88k | ~8 |
| community **1500 예산 충전 가정** | 1,500 | 555 | ~833k | **~70** |

**판단:**

1. **예산 단위 불일치가 핵심 리스크.** `MAX_ITEMS` 는 *item 수* 기준인데, 커뮤니티 item
   (본문 청크 ≤3000자, 평균 555자)은 youtube 댓글(평균 79자)보다 **item당 ~7배 무겁다**.
   따라서 동일한 1500-item 예산이라도 ABSA chunk 부하는 커뮤니티가 youtube의 ~1.3배(70 vs 52).
   *item 수로는 공평해 보여도 char/chunk 비용은 커뮤니티가 과대*하다.
2. **현재는 예산 비바인딩 → 캡 제거 안전.** 커뮤니티 실공급(158, 캡 제거+still_fail 제외 후
   both_ok 회수해도 수백 건대)은 1500 예산을 한참 밑돈다. 즉 **MAX_ITEMS_COMMUNITY=1500 은
   현재 상한일 뿐 바인딩되지 않으며, 캡 제거 = 전량 분석**(크라우드아웃 없음). 순효과는
   커버리지 확대(긍정).
3. **스케일 시 재점검 필요.** 향후 커뮤니티 수집이 1500 item 에 근접하면 char-sum ~833k →
   ~70 chunk 로 폭증한다. 그 시점에는 **(a) 커뮤니티 예산을 char 기준으로 전환**하거나
   **(b) item 예산을 youtube보다 낮게**(예: 커뮤니티 500) 두는 편이 채널 간 비용을 균형화한다.
4. **처리량은 비동기 `/invoke` 로 흡수.** 현재 합산 ~60 chunk(youtube 52 + community 8)는
   `PARALLEL=2` 에서도 동기 30분 천장을 위협하나, **이미 구현된 비동기 `/invoke`(즉시 반환 +
   `/state` 폴링)** 가 이 천장을 제거 → 캡 제거로 인한 chunk 증가가 타임아웃 회귀를 일으키지 않음.

**결론**: 캡 제거는 현 공급 구간에서 안전(커버리지↑, 바인딩 없음). 잠재 위험은 *예산 단위
불일치*이며 — 커뮤니티가 1500 item 에 근접할 때만 발현 — char 기준 예산 또는 낮은 item 예산으로
선제 대응 가능. 처리량 회귀는 비동기 `/invoke` 가 차단한다.

## 6. 의존성·리스크

- **(하드 의존) 처리량/타임아웃**: youtube 1500 + community 1500 = 후보당 3000 = 현재 2배.
  동기 `/invoke` 30분 천장·CLI 속도가 다시 병목 → **비동기 `/invoke`(즉시 반환+`/state`
  폴링) + ABSA 엔진을 API(또는 빠른 모델)로 전환**이 선행·동반 필수. (이게 최대 리스크)
- **A 회수 이득 미검증**: 키워드-드롭 풀(21,525) 유효밀도 미측정 → CLI ABSA 표본 라벨링으로
  확정 권장(밀도 ≥ ~1%면 A 이득 실재).
- **Haiku 장애 폴백**: 관련성 태깅 실패 시 전량 통과=폭주. 폴백 정책 필요(키워드 게이트 임시
  복귀 or 해당 배치 skip).
- **공급 상한 불변**: 목표 100/셀은 수집 확대(영상·앱리뷰) 동반 필요.

## 7. 권장 순서

1. **드롭풀 라벨링**으로 A 이득 확인(병행, CLI=과금 없음).
2. **비동기 `/invoke` + ABSA 엔진(API/빠른모델)** — 처리량 인에이블러(2배 부하 대비).
3. **youtube 관련성 태깅 + priority+fill 컷** 구현.
4. **커뮤니티 동일 적용 + 병렬·병합**.

## 8. 미결정

- 운영 관련성 엔진: haiku(API) 확정 여부(회당 ~$2~4).
- 병합 시 중복 tuple 기준(동일 quote+aspect 제거?).
- Haiku 실패 폴백 정책 확정.
- youtube/community 예산 비율(현 1500/1500 고정 vs 공급 비례).
