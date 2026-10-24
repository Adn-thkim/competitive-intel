# reaction 파이프라인 재설계 — 영상 게이트 · Stage 0/1 · LLM 프리스크린

> - **상태**: DRAFT — 게이트~프리스크린·Stage 2①(추출)·②(군집 Phase A) 검증 완료. Stage 2② Phase B(고정 축 배정)·리포트 반영 대기
> - **작성일**: 2026-07-04 (Stage 2 갱신: 2026-07-18)
> - **배경**: 기존 `reaction_analysis`의 "필터 통과 댓글 → 단일 라벨 관련성 태깅 → per-candidate ABSA(최근접 강제 배치)" 구조에서 발견된 결함(맥락 부재·볼륨 비대칭·이중 aspect 패스·자사 과소집계)을 해소하기 위한 선별·분석 단계 재스케치.
> - **선행 문서**: `docs/design/reaction_insight_node_design.md`, `docs/design/relevance_pipeline_design.md`, `docs/reference/report_taxonomy.md` §2-2
> - **대상 코드(현행)**: `server/graph/nodes/reaction_analysis_node.py`, `server/graph/relevance_tagger.py`, `server/graph/nodes/youtube_reaction_collection_node.py`
> - **검증 스크립트**: `scripts/test_stage0.py` · `test_stage1_v2.py` · `test_gate_prescreen_v2.py` · `test_gate_prescreen_community.py` · `test_cascade_precut.py` · `load_gate_prescreen_df.py`

---

## 1. 재설계 배경 (해소 대상 결함)

| # | 결함 | 근거 |
|---|---|---|
| P1 | **맥락 부재** — 댓글을 영상/본문 없이 고립 해석 | ABSA item = 댓글 text만, relevance = 댓글+부모댓글만 |
| P2 | **볼륨 비대칭** — per-candidate MAX_ITEMS 단일 캡이 자사(11,712→1,500, 87%)를 최다 폐기 | `_channel_cut` |
| P3 | **이중 aspect 패스** — 단일 라벨 관련성(정의 없음, boolean 게이트)과 ABSA(정의·다중·최근접 강제)가 불일치 | `relevance_tagger` vs `agents/reaction_analysis` |
| P4 | **의미 표류** — "가장 가까운 aspect 강제 배치" + 협소 정의 + 기타 버킷 없음 | ABSA 시스템 프롬프트 |
| P5 | **자사 과소집계** — 비교 구문("트래블월렛 아니면 토스")에서 명시 브랜드에만 귀속 | 실데이터 감사 |

핵심 방향: **관련성 판정을 키워드 게이트가 아니라 정의·맥락 인지 LLM으로 이관**하고, **맥락(영상/본문+스레드)을 분석 단위에 부착**한다.

---

## 2. 파이프라인 개요

```
원본 댓글
 └─① 영상 도메인 게이트 (LLM · Haiku)      ← 도메인 밖 영상 통째 제거 (가장 선행)
      └─② Stage 0 (결정론)                 ← 노이즈/필러/무키워드 원댓글 제거, 대댓글 orphan 해제
                                             질문은 fork 없이 **is_question 플래그**만 부착
           └─③ Stage 1 (맥락+스레드 번들)   ← 영상/본문 맥락 헤더 + 스레드(반응·질문 함께), 서브배치
                └─④ 프리스크린 (LLM · Sonnet) ← "카드 관련 **반응 또는 질문**"인지 판정, 무관 컷
                     ├─ is_question=false → ⑤ Stage 2 (LLM · 미구현) 튜플 추출·귀속·출처
                     └─ is_question=true  → ⑥ 질문 분석 (aspect + question_type 분류·집계)
```

- **LLM 사용**: ①·④·⑤·⑥. **결정론(무료)**: ②·③.
- **순서 근거**: 게이트를 선행해 무관 영상의 스레드가 비싼 프리스크린에 도달하지 않게 함. 단 게이트/프리스크린은 대상이 다르므로(영상 메타 vs 스레드) **순서를 바꿔도 LLM 콜 수는 동일**하며, 게이트 선행은 결정론 Stage 0의 로컬 연산만 절감한다.
- **질문 처리 위치(변경)**: 질문을 Stage 0에서 분리하지 않고 **프리스크린 이후로 분기**한다. Stage 0의 기계적 질문 필터는 주제 밖·비질문("카메라 집중되나요?", "인출 잘만 되는데???")을 못 걸러, 맥락 인지 LLM 프리스크린이 반응과 함께 관련성을 판정하도록 이관한다. n120 실측에서 질문 364 → Sonnet 통과 268(74%)로 증권·코인·송금·잡담 질문이 걸러지고 트래블카드 기능 질문이 보존됨을 확인.

---

## 3. 단계별 상세 (역할 · 입출력 · 로직)

### ① 영상 도메인 게이트 — LLM(Haiku)
- **역할**: 제목+설명으로 "해외여행용 트래블카드 상품 리뷰/사용기/비교인가" boolean 판정. 도메인 밖(일반 신용카드·여행 브이로그·뉴스·소매치기·호텔예약·간편결제) 영상을 통째 제거.
- **입력**: `[(video_id, title, description)]`
- **출력**: `{video_id: relevant(bool)}` — **메타 부재(제목·설명 공란)는 통과(unknown)**로 유효 데이터 보존, 필터는 후단 위임.
- **모델 선정**: 짧은 이진 분류라 **Haiku** 적절(저비용·저지연). 배치(제목·설명 15개/콜) + 병렬.

### ② Stage 0 — 결정론 필터
- **역할**: 노이즈·필러·중복·무(無)aspect키워드 원댓글 제거. **대댓글은 키워드로 자르지 않고**(맥락 이어가는 의견 보존) 노이즈/필러만 제거 = orphan 해제(`keep_all_replies=True`).
- **입력**: 게이트 통과 영상의 원본 댓글(youtube_comments 캐시 / community_comments)
- **출력**: 라우트 부여 — `content`(원댓글 통과)·`question`(기능질문)·`drop_*`(short/noise/duplicate/keyword_miss)
- **질문 취급(변경)**: question 을 **별도 스트림으로 fork 하지 않는다.** 해당 스레드에 `is_question=True` 플래그만 달고 content 와 함께 Stage 1·프리스크린으로 흘려보낸다. Stage 0 의 `_is_pure_question`(≤100자·전 문장 의문형+키워드)은 **후보 표시용**일 뿐이며, 최종 질문 여부·관련성은 프리스크린/⑥이 판정한다.
- **주요 로직**: `_filter_basic`(길이·이모지·중복) + `_is_filler`(욕설·웃음만 남는 짧은 반응, "아시발ㅋㅋ") + `_is_pure_question`(is_question 플래그 부여). 실제 노드 predicate 재사용.

### ③ Stage 1 — 맥락+스레드 번들
- **역할**: 통과 댓글을 스레드로 묶고 **맥락 헤더**(유튜브=영상 title+description, 커뮤니티=게시글 title+본문)를 부착해 분석 단위 구성. lone 대댓글도 **root_text(부모 원댓글)** 를 함께 부여해 해석 가능하게 함.
- **입력**: Stage 0 라우트 결과
- **출력**: 배치 = `{context(맥락), threads:[{thread_id, root_text, members:[원댓글·대댓글]}]}`
- **주요 로직**: 영상당 **스레드 상한(cap, 기본 25)으로 서브배치** → 거대 LLM 호출·타임아웃 방지. 커뮤니티는 **본문 자체를 VoC 단위**(`url#body`)로 포함, thread_id는 게시글 URL로 네임스페이스(중복 방지).

### ④ 프리스크린 — LLM(Sonnet 확정)
- **역할**: 번들 맥락으로 스레드별 관련/무관 boolean 판정, 무관 컷. **반응과 질문을 함께** 판정. 관련성 확정을 정의·맥락 인지 LLM으로 이관(P3 해소).
- **입력**: 배치(맥락 + 스레드들, 각 스레드에 is_question)
- **프롬프트(변경)**: "카드 관련 **반응 또는 기능·사용성 질문**이면 relevant, 주제 밖 질문(카메라·여행 일반·증권·코인)·잡담은 false." 질문까지 유지하도록 broaden.
- **출력**: `{thread_id: relevant(bool)}`. 통과분을 is_question 으로 **분기** — false→⑤, true→⑥. 병렬 호출.
- **모델 확정**: **Sonnet.** Haiku는 진짜 카드 반응·질문을 false-negative로 잘라냄. n120 실측: Sonnet 컷 44% vs Haiku 35%, 일치율 0.829, Sonnet만 컷 348(더 엄격·정확). ⚠️ 실패 배치는 보수적 keep 폴백이라 **토큰 만료·타임아웃 시 "전량 유지"로 위장**될 수 있어(§5) 실패 카운터 필요.

### ⑤ Stage 2① — 의견 단위·질문 추출 (구현·검증 · `scripts/poc_opinion_units.py`)
**변경 요지**: 단일 튜플 추출에서 **원칙 기반 의견 단위(units) + 질문(questions) 동시 산출**로 재설계. is_question 은 Stage 0 플래그가 아니라 **P5 가 의미로 판정**한다.

- **원칙(P1~P5, 도메인 무관)**: P1 단위=대상×태도. P2 `reasoning` 먼저(Chain-of-thought → target_term·candidate 정확도↑). P3 polarity 는 **품질·경험 태도만**(사실·전략·습관은 neutral). P4 대상이 카드 아닌 전략·상황이면 candidate 비움. **P5 질문은 버리지 말고 `questions` 로 산출**(한 댓글이 태도+질문 둘 다면 양쪽 모두).
- **units 필드**: `reasoning, target_term, modifier, opinion, polarity, intensity, quote, source_comment_id, target_candidate, candidate_source`. **questions 필드**: `reasoning, target_term, quote, source_comment_id, target_candidate, candidate_source`(polarity·유형분류 없음 — 후단 담당).
- **candidate 귀속 규칙**: 1)explicit(본문 카드명) · **1.5)service**(부속 서비스·앱·연동 계좌가 **의견의 주체일 때만** 귀속. 단독으로 쓰는 **독립 결제망**(여러 앱·계좌 공용, 그 카드 없이도 이용)은 특정 카드가 아니라 비트래블카드 — GLN 정의 반영) · 2)thread · 3)video · 4)none. **복수 카드 병렬 나열**은 병용 의견이면 카드별 분리, 정보성 예시면 전반 정보.
- **배치=글자 수 청킹**(`_split_by_chars`, 프로덕션 `_split_threads` 이식): **스레드 원자성 보존**, 밀집 영상 timeout 자동 해소. budget=**1,500자**(cap 방식 대비 정확도 동등·안정, 12,000자는 attention dilution·truncation로 부적합 — 실측). timeout 600s(프로덕션 정렬), 세션 한도 감지 시 조기 중단.
- **컨텍스트**: `--context-mode full|compact`. compact 는 카드명+동적 서비스 인덱스만(비용 83%↓, 정확도 유지 — 세계지식 의존). 정본 입력 `poc_chunk1500_compact.json`.
- **실패 처리**: 배치 실패를 units=[] 위장하지 않고 `failed_thread_ids`+`failed_batch_errors`로 노출(silent 마스킹 방지).

### ⑥ Stage 2② — 상향식 군집(반응) + 질문 매핑 (구현·검증 · `scripts/poc_cluster_uq_v2.py`)
**반응(units)으로 aspect 축을 만들고, 질문(questions)은 그 고정 축에 매핑**한다(joint 군집 금지 — 질문의 절차성 대상이 반응 축을 오염). 상세 구조는 **§7 (super_aspect 2단계 처리)** 참조.

- **축 도출(units)**: A 집계(distinct target_term) → B 하위군집(배치, 실패 시 절반 쪼개 재시도) → B1.5 정규화(동의=동일 의미·기능 병합) → **B2 상위군집(조건부 새 축 + 기타 잔여 + 파편화 상한)** → C perspective(사전정의 8종 고정 배정).
  - **B2 견고한 원칙(도메인 무관)**: 묶는 기준은 **'같은 기능/대상'만**. **비용·수수료·품질 같은 '관점'으로 서로 다른 기능을 묶지 마라**(관점은 perspective 필드 담당). 의심스러우면 **병합보다 분리 유지**.
- **질문 매핑**: 고정 super_aspect 축에 **분류(재군집 아님)**, 안 맞으면 '기타'(반응 축 밖 질문 = 유용 신호). **원칙 기반 매핑**: 질문의 **핵심 초점(무엇의 무엇)**으로 배정, 스쳐 지나가는 표면 단어에 끌리지 말 것.
- **질문 2차 축 = question_type (확정, perspective 대체)**: perspective 는 **평가 척도**라 평가가 없는 질문에선 방법·조건·가능여부 유형이 갈 곳 없어 other 로 샌다(실측 other 18%). question_type 은 **"무엇을 알아내려 하는가"**를 분류해 질문 공간을 온전히 덮는다(실측 other 12%로 감소). **반응(unit)=perspective, 질문(question)=question_type** 으로 채널별 2차 축 분리.
  - **taxonomy(도메인 무관)**: `feasibility`(가능여부)·`method`(방법)·`cost`(비용)·`safety_anxiety`(안전·불안)·`condition`(조건·자격)·`comparison`(명시적 2개+ 비교)·`recommendation`(추천 요청)·`troubleshooting`(왜 안 되나·원인/해결)·`other`. troubleshooting 은 "왜 안 됨" 질문이 feasibility·safety_anxiety·other 로 흩어지던 것을 통합(실측 ~11%).
- **산출**: `super_aspect × (반응 sentiment / 질문 수)` 교차표 + `super_aspect × question_type` 교차 = "반응은 양호한데 질문(방법·원인) 몰리는 지점 = 사용법 혼란·고장" 도출.

---

## 4. 실측 퍼널 (유튜브 n120, seed 42, CLI · Haiku 게이트)

| 단계 | 입력 | 남은 것 | 비고 |
|---|--:|--:|---|
| ① 영상 게이트 | 120영상 | **64영상** | 56 제외(신용카드·뉴스·소매치기·호텔·알리페이 등), 2,117 스레드 상류 제거 |
| ② Stage 0 | 6,698 댓글 | 4,357 댓글 | 제외 2,171: keyword_miss 1,542 · short 606 · dup 17 · noise 6 · **orphan 0** |
| ③ Stage 1 | 4,357 댓글 | **2,775 스레드** | 148배치(cap25, 평균 18.8), member=원댓글2,019+대댓글1,952+질문386, **orphan 회복 574** |
| ④ 프리스크린(Sonnet) | 2,775 스레드 | **1,512 스레드** | 컷 1,263(46%). Haiku 1,498(Haiku FN 60) |

**엔진 비교(프리스크린)**: none↔haiku·sonnet 일치 ~0.6, **haiku↔sonnet 0.841**. 불일치 90건 중 **sonnet keep·haiku cut 60건이 진짜 카드 반응**(ATM 인출 실패·하나머니 앱 먹통 등) = Haiku false-negative. → 프리스크린은 Sonnet.

---

## 5. 결정·검증 대기 항목

1. **Stage 2 구현·테스트** — 튜플 추출·귀속·출처 보유. 오탐 번들이 실제 0 튜플로 걸러지는지 확인.
2. **캐싱·병렬(프로덕션)** — 게이트/프리스크린을 `relevance_tagger`식 항목 캐시(TTL) + `ThreadPoolExecutor`로. 전 영상(323개) 첫 실행 ~338콜(캐시 후 재실행 ≈ 0). API/배치는 비용 이유로 현재 보류.
3. **Haiku FN 하이브리드** — Haiku 1차 + 경계군 Sonnet 재판정으로 비용·recall 절충.
4. **골드 라벨 평가** — 소표본 손라벨로 Haiku·Sonnet·none의 precision/recall 대칭 측정(both_cut의 공통 FN 포함).
5. **커뮤니티 CLI 실행** — 현재 stub 검증만 완료. `--llm cli`로 실측 후 두 채널 통합 퍼널 확정.
6. **영상 게이트 메타 부재 폴백** — unknown 통과 정책의 오통과율 점검.

---

## 6. 관련 산출물

- 결과 데이터: `data/debug/gate_prescreen_v2_n120.json`(유튜브 CLI), `gate_prescreen_community.json`(커뮤니티 stub), `stage1_v2_test.json`
- Stage 2 산출물: `poc_chunk1500_compact.json`(①의견·질문 추출 정본), `poc_cluster_uq_v2.json`(②군집)
- df 로더: `scripts/load_gate_prescreen_df.py`(선별), `scripts/load_units_df.py`(units+questions)
- 본 재설계는 리포트/노드에 **아직 반영하지 않음** — 테스트 스크립트로만 검증 중.

---

## 7. super_aspect 2단계 처리 (축 도출 → 고정 축 배정)

**문제**: 상향식 군집을 매 실행 재생성하면 축이 흔들린다(동일 데이터 3회 실행: 축 9/9/11개, 수수료·보안·교통의 소속이 실행마다 변동). 히트맵은 **안정적 축**이 필요하므로, 매번 재군집하면 시계열 비교가 불가능하고 변동성이 리포트에 노출된다.

**해결**: 축을 **한 번 도출해 고정**하고, 이후에는 그 고정 축에 **배정만** 한다. codebook 강제배정과 다른 점 — 축이 임의 사전이 아니라 **데이터에서 귀납**되고, **human_review + '기타' 잔여 + 주기적 갱신**이 있어 새 주제도 포착한다.

### Phase A — super_aspect 축 도출 (가끔 · 사람 검토)
- **입력**: 대표 표본(또는 전체 1회)의 units.
- **처리**: §⑥의 상향식 파이프라인(B → B1.5 → **B2 조건부 새 축**)으로 super_aspect 축을 귀납한다.
- **캐시 우선**: 히트 가능한 축 캐시가 있으면 **도출을 생략**하고 캐시에서 로드한다(입력 프로필/도메인 해시 기반).
- **human_review**: 도출(또는 캐시 로드)된 축을 **사람이 검토·수정**하는 칸을 둔다(라벨명·병합·분리·정의 보정). 검토 완료된 축 = **고정 축**(각 super_aspect 에 짧은 정의 포함).
- **산출**: `frozen_axis = [{super_aspect, definition}]` + 버전·생성일.

### Phase B — 고정 축 배정 (매 실행 · 전체 데이터)
- **입력**: 전체 units·questions + Phase A 의 **고정 축**.
- **처리**: 재군집하지 않고, 각 unit(또는 sub_cluster)·question 을 **고정 축에 분류(classification)**. 정의를 함께 제시해 매핑 정확도↑. 안 맞으면 **'기타'** 잔여.
- **모니터링**: '기타' 비율이 임계 초과 시 → 새 주제 누적 신호 → Phase A 재도출 트리거.
- **효과**: 축 변동성 0(시계열 비교 가능), 배치 분류라 대규모 확장, 강제배정 없음(귀납+잔여+갱신).

```
[Phase A]  units 표본 ─(캐시 히트?)─┬─ 캐시 로드 ──┐
                                    └─ 상향식 도출 ─┴→ human_review(수정) → frozen_axis(+정의)
[Phase B]  전체 units·questions + frozen_axis → 고정 축 분류(+기타) → 히트맵/교차표
                                                     └ '기타' 비율 모니터 → 임계 초과 시 Phase A 재실행
```

**현재 상태**: ⑥의 `poc_cluster_uq_v2.py` 는 **Phase A(축 도출)** 에 해당(질문 매핑은 이미 고정 축 분류 방식). **Phase B(전체 데이터 고정 축 배정) + human_review 칸 + 축 캐시**는 다음 구현 대상.
