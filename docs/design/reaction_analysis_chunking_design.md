# reaction_analysis chunking 설계 — 스레드 원자 분할 + 평탄화 병렬 호출

> - **상태**: CONFIRMED — 전 결정 항목(Q1~Q5) 확정 (2026-06-18)
> - **작성일**: 2026-06-18 (rev2: 스레드 원자 경계·평탄화 병렬 / rev3: CHUNK_CHARS·TIMEOUT 실측 / rev4: analyzed_size(Q3) / rev5: Q2·Q4·Q5 확정 — 전 항목 확정)
> - **시리즈**: reaction_insight 계열 후속 — 대용량 입력 CLI 타임아웃 해소 + wall-clock 단축
> - **선행 문서**:
>   - `docs/design/reaction_insight_node_design.md` (RI-D5 어댑터=CLI · RI-D7 채널 가중 · RI-D6 루브릭)
>   - `docs/design/youtube_collection_redesign.md` (Phase 3·4 전량 수집 + pre-filter v2)
>   - `docs/design/youtube_reply_collection_design.md` (대댓글+parent 수집 — 본 설계 CH-D3 thread_id 의존)
>   - `docs/design/comparison_matrix_node_design.md` (CM-D1 코드/LLM 분리 · CM-D5 degrade)
> - **대상 파일**: `server/graph/nodes/reaction_analysis_node.py` (수정),
>   `server/config.py` (`REACTION_ABSA_CHUNK_CHARS=60000`·`REACTION_ABSA_CHUNK_CHARS_MIN=20000`·`REACTION_ABSA_CHUNK_TIMEOUT=300`·`REACTION_ABSA_MAX_ITEMS`·`REACTION_ABSA_PARALLEL=4` 추가)
> - **무변경 확인 파일**: `server/graph/nodes/reaction_insight_node.py`, `agents/reaction_analysis/*`

---

## 1. 문서 목적과 범위

`reaction_analysis_node`가 candidate별 사용자 반응(댓글·게시글)을 단일 CLI 호출로
ABSA 분해하는 현재 구조는, 입력이 수백~수천 건으로 커지면 Claude Code CLI 호출이
`_LLM_TIMEOUT_SEC = 600`초를 초과해 실패한다. 본 문서는 이 단일 대용량 호출을
**candidate 입력을 스레드 원자 단위로 분할(chunk)한 다회 호출 → tuples 병합** 구조로
교체하고, 추가로 **candidate×chunk 쌍을 한 풀에 평탄화한 병렬 실행**으로 wall-clock을
단축하기 위한 설계 결정을 정의한다.

범위는 `reaction_analysis_node` 내부 처리 한정이다. 하류 `reaction_insight_node`와 두
노드 사이의 상태 계약(`state["reaction_analysis"]`)은 변경하지 않는 것을 핵심 제약으로 둔다.

### 1-1. 비범위 (Non-goals)

- 콘텐츠 수준 필터링(순수 의문문·노이즈 제거)은 `youtube_reaction_collection_node`의
  `_prefilter_v2` 책임이며 본 설계 대상이 아니다.
- 대댓글·parent 수집은 `youtube_reply_collection_design.md`의 책임이다. 본 설계는 그 산출인
  `thread_id`를 **소비**할 뿐 수집 로직을 정의하지 않는다(thread_id는 YR-D4로 공급 완료).
  구 캐시 등 미공급 시의 폴백은 CH-D3에 정의한다.
- `reaction_insight_node`의 LLM 서술 호출(별도 300초)은 집계된 `aspect_matrix`·
  `top_quotes`만 입력받아 입력 크기가 댓글량과 무관하므로 본 설계에서 다루지 않는다.

---

## 2. 문제 정의 (현행 코드 실사 — 2026-06-18)

### 2-1. 타임아웃 발생 지점

`reaction_analysis_node.py`의 candidate 루프(250~309행)는 candidate당 1회
`analyzer.call_with_schema(prompt, output_schema)`(276행)를 호출한다. `prompt`에는
`payload["items"]`(해당 candidate의 정제 댓글 전량)가 박힌다. 이 단일 호출이 600초를
넘으면 CLI 어댑터가 `subprocess.TimeoutExpired`를 `RuntimeError("Claude CLI timeout
…")`로 변환해 던지고, candidate 단위 `try-except`(303행)가 이를 잡아 `errors`에 적재한 뒤
해당 candidate의 분석 결과는 **누락**된다.

### 2-2. 캐시 실측 근거

`data/cache/agent_outputs/reaction_analysis.json` 분석 결과, 상한 해제 후 전량 실행
(2026-06-13 15:07)에서 `comp_신한sol트래블체크카드`만 1016건으로 캐시되고, 나머지 세
candidate는 전량 실행 엔트리가 없다. 신한SOL 분석 직후 다음 candidate의 전량 호출에서
타임아웃이 발생했음을 보여준다. 현행 회피책 `_MAX_ITEMS_PER_CANDIDATE = 250`
(commit `f961b42`)은 입력을 250건으로 샘플링해 호출 시간을 줄이지만 **수집 데이터를
버리는** 트레이드오프가 있어 "전량 활용" 요구와 충돌한다.

### 2-3. 인과 정리

타임아웃 유발 변수는 산출물 `{candidate_id, tuples}` 크기가 아니라 **입력 items 양**
(프롬프트 토큰량)이다. wall-clock 문제는 candidate·chunk를 순차 처리할 때 호출 시간이
누적되는 데서 온다. 따라서 처방은 (가) 입력 분할 + (나) 분할 단위의 병렬 실행이다.

---

## 3. 핵심 제약 — 상태 계약 불변 (CH-D1)

**결정 CH-D1**: chunking·병렬화는 `reaction_analysis_node` 내부에 전적으로 격리하고,
산출물 `reaction_analysis[cid]` 딕셔너리 형태는 현행과 동일하게 유지한다.

근거: `reaction_insight_node`의 모든 집계 함수는 `reaction_analysis[cid]["tuples"]`와
메타 5종(`channel_counts`, `post_count`, `sample_size`, `collected_at`,
`dropped_by_guard`)만 소비한다. tuples가 1회 호출에서 왔는지 N회 병렬에서 왔는지는
하류가 알지 못한다. 계약 형태만 보존하면 하류는 무수정으로 전량 데이터를 흡수한다.

계약 형태 (기존 키 불변 + `analyzed_size` 추가 — CH-D9, 하류 무시 가능):

```
reaction_analysis[cid] = {
    "tuples":           [ {aspect, polarity, intensity, channel,
                           quote, source_url, posted_at, is_suggestion}, ... ],
    "channel_counts":   {"youtube": int, "community": int, "blog": int},
    "post_count":       {"youtube": int, "community": int, "blog": int},
    "sample_size":      int,        # AP-3 — 수집 전량
    "analyzed_size":    int,        # CH-D9 — 분석 성공 chunk 의 item 합 (부분실패 시 < sample_size)
    "collected_at":     ISO8601,    # AP-3
    "dropped_by_guard": int,
    "failed_chunks":    int,        # CH-D10 Q4 — 실패한 chunk 수 (전 chunk 성공 시 0)
    "missing_items":    int,        # CH-D10 Q4 — 누락 댓글 수 (= sample_size - analyzed_size)
}
```

---

## 4. 분할 설계

### 4-1. 분할 위치와 상한 폐기 (CH-D2)

**결정 CH-D2**: `_sample_items(items_raw, _MAX_ITEMS_PER_CANDIDATE)`로 250건 절단하던
부분(252행)을 제거하고 candidate 전량을 분할 대상으로 삼는다. 외부 참조가 없음을
확인했으므로(`grep` 결과 reaction_analysis_node 한정), `_sample_items`와
`_MAX_ITEMS_PER_CANDIDATE`는 고아 코드로 함께 제거한다(CLAUDE.md §3).

안전장치: 비정상 폭주 방지용 하드 상한 `REACTION_ABSA_MAX_ITEMS`(예: 5000)를 두되 정상
운영에서는 도달하지 않는다.

### 4-2. 스레드 원자 chunk 경계 (CH-D3)

**결정 CH-D3**: chunk 경계는 **스레드 원자 단위**로 끊는다. 하나의 스레드(최상위 댓글 +
그 대댓글)는 같은 chunk 안에 통째로 담기며 chunk 사이로 절대 분할되지 않는다. 정렬은
스레드 대표(최상위 댓글)의 `posted_at` 내림차순(최신 우선), 동률 시 `thread_id`,
`source_url` 순으로 결정론적으로 둔다.

근거: 서로 참조하는 댓글은 대부분 같은 스레드(대댓글이 최상위에 답하는 구조) 안에서
발생한다. 스레드를 원자 단위로 유지하면 이 상호 참조 맥락이 한 호출 안에 보존되어
cross-comment 맥락 손실이 최소화된다. 최상위 댓글 간(스레드 밖) 참조는 빈도가 낮아
보존 대상에서 제외한다.

**폴백 (구 캐시 데이터)**: `thread_id`는 `youtube_reply_collection_design.md`(Phase A·YR-D4)
로 이미 공급된다(**구현 완료** — `build_absa_inputs`가 모든 item에 부여). 단 Phase A 이전
캐시 등으로 items에 `thread_id`가 없으면 각 item을 1개 스레드로 간주한다(= 댓글 원자성).
경계 로직은 두 경우 동일하다.

**불변식**: 단일 스레드가 chunk 크기 한도(CH-D4)를 초과하면 그 스레드만으로 1 chunk를
구성한다. 스레드는 어떤 경우에도 분할하지 않는다.

### 4-3. chunk 크기 — 글자/토큰 기준 (CH-D4)

**결정 CH-D4**: chunk 크기는 건수가 아니라 **누적 문자량** 기준으로 자른다.
`REACTION_ABSA_CHUNK_CHARS = 60,000자` 한도까지 스레드를 담고, 다음 스레드를 담으면 한도를
넘을 때 경계를 끊는다. 경계는 항상 스레드 단위에서만 끊으므로 댓글·스레드는 중간에서
쪼개지지 않는다. **하한 `REACTION_ABSA_CHUNK_CHARS_MIN = 20,000자`** — 마지막 잔여 chunk를
제외하고 이보다 작게 쪼개지 않는다(고정 오버헤드 분할 손실 방지).

근거 (실측, 2026-06-18): 가장 큰 candidate(신한SOL)의 prefilter 통과 댓글은 평균 107.8자
(median 74·p90 217)이며, 1016건 ABSA 코퍼스는 item JSON 구조 포함 약 201,000자다. chunk마다
재전송되는 고정 오버헤드 O = 3,903자(system_prompt 2,174 + slim schema 694 + aspect codebook
1,035). 06-13 실측에서 이 ~201,000자(1016건)가 600초 안에 성공했고 직후 candidate에서 초과해
타임아웃이 났다. 이 처리량을 캘리브레이션으로 `CHUNK_TIMEOUT=300초`·safety 0.6을 적용하면
`CHUNK_CHARS ≈ 60,000자`(최대 candidate 기준 chunk 수 ~3, 오버헤드 비중 O/60,000 ≈ 6.5%)다.
하한 20,000자는 데이터:오버헤드 ≥ 4:1(≈5×O)을 유지하는 값이다. 캘리브레이션은 "1016건이
정확히 600초 걸렸다"는 보수적 가정이므로(실제론 그 이하 성공) 60,000자는 안전측 값이다.

문자 기준을 쓰는 이유: 타임아웃 제약은 컨텍스트 윈도우가 아니라 **처리 시간**이며, 문자량이
그 시간의 안정적 대리 지표다(60,000자는 약 3~4만 토큰으로 컨텍스트엔 여유). 글자수 기준은 각
chunk를 안전 한도까지 꽉 채워 chunk 수를 최소화하므로 같은 타임아웃 안전성에서 chunk 간 맥락
분할이 가장 적다(맥락 보존에 유리). Phase A 대댓글·YR-D3로 candidate 코퍼스가 1016보다 커지면
chunk 수만 늘 뿐 per-chunk 예산·타임아웃 안전성은 유지된다. 구현 후 최대 candidate 1회 타이밍
측정으로 상수를 미세 조정한다.

### 4-4. chunk별 캐싱 (CH-D5)

**결정 CH-D5**: chunk마다 결과를 chunk 단위로 캐싱한다. 캐시 입력에 chunk 식별을 둔다:

```
cache_input = {
    "candidate_id": cid,
    "aspect_ids":   sorted(valid_aspects),
    "items_sha":    [_norm(it["text"])[:64] for it in chunk_items],  # 이 chunk 한정
}
```

근거: chunk 단위 캐싱은 (가) 일부 chunk 실패 시 성공분·이전 실행분 재사용으로 재실행
비용을 낮추고, (나) timeout 노출 면적을 chunk 단위로 줄인다. **주의(병렬과의 상호작용)**:
캐시 조회/저장은 워커 스레드가 아니라 메인 스레드에서만 수행한다(CH-D11 참조).

### 4-5. chunk별 timeout (CH-D6)

**결정 CH-D6**: chunk 1회 호출 timeout은 candidate 전량용 600초가 아니라 chunk 크기에 맞춘
`REACTION_ABSA_CHUNK_TIMEOUT = 300초`로 낮춘다(CH-D4 60,000자 캘리브레이션과 정합). 병렬화는
이 per-chunk timeout을 바꾸지 않고 호출을 겹쳐 실행할 뿐이며, wall-clock은 평탄화 병렬
(CH-D11)이 흡수하므로 300초가 부담이 아니다.

구현 주의: `ClaudeCodeCliAnalyzer`는 timeout을 **생성자에서 1회 고정**한다(현 233~234행
`timeout=_LLM_TIMEOUT_SEC`). 따라서 analyzer를 `REACTION_ABSA_CHUNK_TIMEOUT`으로 생성해야
이 값이 실제 호출에 적용된다. 주입(injected) analyzer를 쓰는 테스트에서는 호출자가 timeout을
지정한다.

---

## 5. 병합 설계 (메인 스레드 수행)

### 5-1. tuples 병합 (CH-D7)

**결정 CH-D7**: chunk별 LLM 산출 tuples를 concat으로 병합한다. chunk 간 items가
서로소이므로 동일 `(quote, source_url)` 중복이 구조적으로 없으나, 방어적으로
`(aspect, source_url, _norm(quote))` 키 기준 dedup을 1회 적용해 경계 중복 환각을 차단한다.
병렬 실행 시 병합 순서는 완료 순서에 의존하나, 최종 리포트 산출물은 순서 불변이다(§9).

### 5-2. 가드 적용 범위 (CH-D8)

**결정 CH-D8**: `sanitize_tuples`는 병합 후 tuples 전체에 대해 candidate **전량 items**를
기준 corpus·valid_urls로 1회 적용한다. 각 chunk quote는 전량 corpus의 부분집합이므로
정상 통과한다. `dropped_by_guard`는 이 1회 적용의 제거 합계다.

### 5-3. 메타 집계 — 전량 기준 (CH-D9)

**결정 CH-D9**: `channel_counts`·`post_count`는 candidate **전량 items**를 직접 세어
산출한다. `collected_at`은 candidate 처리 시작 시각 1개로 유지한다. `sample_size`는
**수집 전량**(`len(items_all)`)으로 두고, 분석에 실제 반영된 양은 `analyzed_size`(성공
chunk의 item 합)로 별도 표기한다(Q3 확정). 전 chunk 성공 시 둘은 같고, 부분 실패 시
`analyzed_size < sample_size`가 되어 표본 신뢰도 저하를 하류가 감지할 수 있다.
`analyzed_size`는 하류가 무시 가능한 추가 필드다.

---

## 6. 에러 핸들링 (CH-D10)

**결정 CH-D10**: 2단계 try-except 위계를 둔다. 병렬에서는 각 future 단위로 적용한다.

- **chunk(future) 단위**: 각 chunk 호출(future)을 try-except로 감싼다. 실패 시 `errors`에
  `{node, error: "candidate=…/chunk=i: …", timestamp}`를 적재하고 결과를 비운다. 성공
  chunk의 tuples는 보존한다(부분 수집).
- **candidate 단위**: candidate의 **모든** chunk가 실패한 경우에만 해당 candidate가
  `reaction_analysis`에서 누락된다. 일부 chunk만 성공하면 그 부분으로 구성한다.

부분 누락 표식(Q4 확정): `reaction_analysis[cid]`에 `failed_chunks`(실패한 chunk 수)와
`missing_items`(실패 chunk의 누락 댓글 수 = `sample_size - analyzed_size`)를 추가한다. 전
chunk 성공 시 둘 다 0이다. 하류 무시 가능 필드이며 표본 신뢰도 경고에 활용한다.

---

## 7. 병렬 실행 — candidate×chunk 평탄화 (CH-D11)

**결정 CH-D11**: 모든 candidate의 모든 chunk를 하나의 `(cid, chunk)` 작업 목록으로
**평탄화(flatten)** 한 뒤 상한 풀로 병렬 실행한다.

### 7-1. 정확도 불변 (스케줄링 ≠ 프롬프트)

각 chunk 호출은 `claude --print`로 매번 새 프로세스를 띄우는 **무상태 단발 실행**이며,
프롬프트에는 단일 candidate의 단일 chunk만 담긴다(`_invoke_cli`). 평탄화는 "어떤 호출을
동시에 돌릴지"를 정하는 스케줄링일 뿐 프롬프트를 합치지 않으므로, candidate가 섞여도 각
호출이 보는 입력은 순차 실행과 완전히 동일하다. **병렬 단위는 LLM 정확도에 영향이 없다.**
맥락 보존 변수는 오직 chunk 크기(CH-D4)·스레드 원자성(CH-D3)이다.

### 7-2. 캐시 I/O는 메인 스레드 전용 (경쟁 조건 회피)

`agent_cache`의 `store_agent_output`·`load_agent_output`은 단일 JSON 파일을
read-modify-write 하며, 읽기·쓰기 사이에 락이 풀려 **원자적이지 않다**(load도 hit_count를
증가시키며 파일을 다시 씀). 워커가 동시에 같은 `reaction_analysis.json`을 쓰면 엔트리가
유실된다. 따라서:

1. **메인 스레드**: chunk별 cache_key 계산 → `load_agent_output` 순차 조회 → (적중분)과
   (미스분)으로 분리.
2. **워커(병렬)**: 미스 chunk만 `analyzer.call_with_schema()`만 수행(캐시 I/O·종합 금지).
3. **메인 스레드**: 완료된 미스 결과를 순차로 `store_agent_output` 저장 + candidate별로
   tuples 병합·가드·메타 집계.

이로써 공용 `agent_cache.py`를 수정하지 않고(외과적) 경쟁 조건을 제거한다.

### 7-3. 병렬도 상한 (무제한 금지)

**결정**: 풀 크기는 `max_workers = min(len(tasks), REACTION_ABSA_PARALLEL)`로 제한한다
(`REACTION_ABSA_PARALLEL = 4` — Q5 확정, 기존 LLM 병렬 상수 정합). "chunk 수만큼" 무제한
병렬은 금지한다. 계정 동시 요청 한도가 확인되면 그에 맞춰 조정한다.

근거: 하우스 패턴은 전부 상한 병렬이다(`OFFICIAL_SOURCE_RESOLVER_PARALLEL=2`,
`FEATURE_URL_MAPPER_PARALLEL=4`, `max_workers=3`). `url_retry_node`는 이미
`ClaudeCodeCliAnalyzer`를 상한 풀에서 호출하는 검증된 선례다. 무제한은 하부 API 동시
요청·RPM/TPM 한도(429) 및 `claude` 프로세스 다중 생성으로 인한 메모리·CPU 피크를 유발한다.
상한 풀이 두 위험을 함께 통제하며, 실패 chunk는 CH-D10으로 격리된다.

### 7-4. 진행률·에러 수집

`set_progress`·`errors.append`는 메인 스레드에서만 호출해 공유 상태 동시 변경을 피한다.
워커는 결과/예외만 future로 반환하고, 메인 스레드가 `as_completed`로 수거한다.

---

## 8. 수정 후 처리 구조 의사코드

```python
# 1) 평탄화된 작업 목록 (메인 스레드)
tasks = []  # [(cid, chunk_index, chunk_items)]
chunks_by_cid = {}
for cid in sorted(inputs):
    items_all = _sort_threads(inputs[cid])              # CH-D3 (thread_id 없으면 item=thread)
    chunks    = _split_threads(items_all, CHUNK_CHARS)  # CH-D4 스레드 원자
    chunks_by_cid[cid] = (items_all, chunks)
    for i, chunk in enumerate(chunks):
        tasks.append((cid, i, chunk))

# 2) 캐시 조회 (메인 스레드 순차) → 적중/미스 분리
results = {}                                            # (cid,i) -> tuples
misses  = []
for (cid, i, chunk) in tasks:
    cached = load_agent_output(... items_sha(chunk) ...)  # CH-D5
    if cached is not None:
        results[(cid, i)] = cached.get("tuples", [])
    else:
        misses.append((cid, i, chunk))

# 3) 미스만 병렬 CLI 호출 (워커: call_with_schema 만)   # CH-D11
with ThreadPoolExecutor(max_workers=min(len(misses), REACTION_ABSA_PARALLEL)) as pool:
    fut = {pool.submit(analyzer.call_with_schema, prompt(cid, chunk), schema): (cid, i, chunk)
           for (cid, i, chunk) in misses}
    for f in as_completed(fut):
        cid, i, chunk = fut[f]
        try:
            out = f.result()                            # CH-D6 per-chunk timeout
            results[(cid, i)] = out.get("tuples", [])
            store_agent_output(... )                    # 메인 스레드(컨텍스트), CH-D5
        except Exception as exc:                        # CH-D10 future 단위
            errors.append({"node": "reaction_analysis_node",
                           "error": f"candidate={cid}/chunk={i}: {exc}", ...})

# 4) candidate별 종합 (메인 스레드)
for cid, (items_all, chunks) in chunks_by_cid.items():
    merged = [t for i in range(len(chunks)) for t in results.get((cid, i), [])]
    if not merged and _all_chunks_failed(cid):          # CH-D10 전 chunk 실패
        continue
    merged = _dedup_tuples(merged)                      # CH-D7
    tuples, dropped = sanitize_tuples(merged, items_all, valid_aspects)  # CH-D8
    reaction_analysis[cid] = {
        "tuples": tuples,
        "channel_counts":   _count_by_channel(items_all),   # CH-D9 전량
        "post_count":       _unique_urls_by_channel(items_all),
        "sample_size":      len(items_all),                 # 수집 전량
        "analyzed_size":    sum(len(chunks[i]) for i in range(len(chunks))
                                if (cid, i) in results),    # CH-D9 분석 성공분
        "collected_at":     started_at,
        "dropped_by_guard": dropped,
        "failed_chunks":    sum(1 for i in range(len(chunks))
                                if (cid, i) not in results),        # CH-D10 Q4
        "missing_items":    sum(len(chunks[i]) for i in range(len(chunks))
                                if (cid, i) not in results),        # CH-D10 Q4
    }
```

---

## 9. 하류 불변성 검증 (CH-D12)

**결정 CH-D12**: `reaction_insight_node`는 무수정을 유지하며 다음을 회귀 기준으로 둔다.

- `build_aspect_matrix`의 `weighted_sentiment = num/den`은 비율이라 tuple 수 증가에 불변.
- `select_top_quotes`는 aspect×극성당 `per_bucket=1`로 출력 크기가 입력 증가와 무관.
- `compute_rubric`의 2채널 교차·`posted_at` 보유율 50% 판정은 구조적으로 동일.
- 병렬 완료 순서는 결과에 무관: top_quotes는 안정 정렬 키, 극성 count는 순서 무관,
  timeline은 `posted_at` 기준. 따라서 병합 순서가 달라도 envelope는 결정론적이다.
- `build_suggestions`(Q2 확정): aspect별로 묶어 intensity·채널 가중·좋아요 상위
  **top-N(N=3)**만 노출한다. 인사이트 LLM 입력이 데이터량과 무관하게 상한되어 하류 타임아웃
  위험이 제거되고, 리포트도 "항목별 대표 제안" 형태로 가독성이 오른다. **단 이 변경은
  `reaction_insight_node.build_suggestions` 수정을 수반하므로 CH-D1/CH-D12 '무수정' 원칙의
  명시적 예외이며, 구현은 chunking이 아니라 reaction_insight 측에서 수행한다.**

---

## 10. 결정 필요 항목 (사용자 확정 요망)

| ID | 항목 | 선택지 | 권장 |
|---|---|---|---|
| ~~Q1~~ ✅확정 | chunk 크기·timeout·하한 | — | **CHUNK_CHARS=60,000 · CHUNK_TIMEOUT=300s · 하한=20,000** (신한SOL 실측 캘리브레이션, §4-3 근거) |
| ~~Q2~~ ✅확정 | suggestions 노출/상한 | — | **aspect별 top-N(N=3)** — `reaction_insight_node.build_suggestions`에서 구현(무수정 예외, §9) |
| ~~Q3~~ ✅확정 | `sample_size` 부분 실패 의미 | — | **sample_size=수집 전량 + `analyzed_size`(성공 chunk item 합) 추가** (§5-3) |
| ~~Q4~~ ✅확정 | 부분 누락 표식 | — | **`failed_chunks`·`missing_items` 필드 추가** (§6 CH-D10) |
| ~~Q5~~ ✅확정 | `REACTION_ABSA_PARALLEL` 기본값 | — | **4** (기존 LLM 병렬 상수 정합; 계정 동시한도 확인 시 조정) |

> **모든 결정 항목(Q1~Q5) 확정 완료.** 구현 착수 가능.

---

## 11. 영향 파일·상태 키

- **수정**: `server/graph/nodes/reaction_analysis_node.py` (평탄화·병렬·스레드 분할 헬퍼),
  `server/config.py`(`REACTION_ABSA_CHUNK_CHARS=60000`·`CHUNK_CHARS_MIN=20000`·
  `CHUNK_TIMEOUT=300`·`MAX_ITEMS`·`PARALLEL=4`, `CLI_TIMEOUT<30` 류 sanity 가드 동반).
- **제거(고아)**: `_sample_items`·`_MAX_ITEMS_PER_CANDIDATE`.
- **상태 키**: `state["reaction_analysis"]` 기존 키 불변(CH-D1) + `analyzed_size`·
  `failed_chunks`·`missing_items` 추가(CH-D9·CH-D10, 하류 무시 가능 필드). 신규 상태 키 없음.
- **하류 예외**: Q2(aspect별 top-N) 구현은 `reaction_insight_node.build_suggestions` 수정을
  수반 — 무수정 원칙의 명시적 예외(§9). reaction_insight 측 작업으로 분리한다.
- **의존**: CH-D3 스레드 원자성은 ABSA items의 `thread_id`에 의존하며,
  `youtube_reply_collection_design.md`(Phase A·YR-D4)로 **공급됨(구현 완료)**. 구 캐시 등
  미공급 시 폴백(item=thread).
- **캐시**: `reaction_analysis` agent_output 캐시 키가 chunk 입도로 세분화됨.

---

## 12. 테스트·검증 계획 (목표 주도)

1. **단위**: `_split_threads` — 스레드 비분할 보장(한 thread_id가 두 chunk에 걸치지 않음),
   한도 초과 단일 스레드의 단독 chunk화, chunk 합 = 전량.
2. **단위**: `_dedup_tuples` — 동일 `(aspect, source_url, quote)` 중복 0.
3. **통합(계약)**: (구) 단일 호출과 (신) 병렬 chunk 병합의 `reaction_analysis[cid]` 키
   집합·메타 의미 일치, `sample_size`=전량.
4. **동시성**: 미스 다수를 병렬 실행 후 `reaction_analysis.json` 엔트리 유실 0 검증
   (캐시 I/O 메인 스레드 직렬화 확인).
5. **하류 회귀**: 병렬 산출을 `reaction_insight_node`에 투입 → envelope 생성,
   `weighted_sentiment` ∈ [-1,1], 루브릭 점수 산출.
6. **타임아웃 회귀**: 1016건 candidate를 chunk 병렬 처리 → 개별 chunk < timeout, 전량 분석
   완료, wall-clock이 순차 대비 단축.
7. **부분 실패**: 1개 future 강제 실패 주입 → 나머지 보존, candidate 비누락, `errors` 1건,
   `analyzed_size < sample_size` (CH-D9 부분실패 표기).
8. **결정성**: 병렬 완료 순서를 뒤섞어도 envelope 동일(top_quotes·matrix·timeline 불변).
