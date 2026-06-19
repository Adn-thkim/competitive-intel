# 캐시 저장계층 개편 — 키별 샤딩 + 백업·마이그레이션 + 인메모리→파일 전환

> - **상태**: CONFIRMED — 전 결정(CS-D1~D7, Q1~Q6) 채택 (2026-06-19)
> - **작성일**: 2026-06-19
> - **시리즈**: agent_cache 저장계층 리팩터링 (조회·저장·hit·동시성·누적 일괄 해소)
> - **선행/관련 문서**:
>   - `docs/design/Future_Improvements.md` 5번 (Brave 인메모리→파일 캐시 전환)
>   - `docs/design/reaction_analysis_chunking_design.md` CH-D11 §7-2 (메인 스레드 I/O 우회 — 본 개편으로 불필요해짐)
>   - `server/cache_ttls.yaml` (TTL 단일 관리 — 본 개편과 연동)
> - **대상 파일**: `server/graph/agent_cache.py`(저장계층 교체), `server/graph/url_cache.py`(인메모리→파일),
>   `server/graph/nodes/official_source_resolver_node.py`·`url_retry_node.py`(호출부),
>   신규 마이그레이션 스크립트 `scripts/migrate_cache_sharding.py`

---

## 1. 문서 목적과 범위

현재 agent 출력 캐시는 "agent당 단일 JSON 파일"에 모든 엔트리를 담는 구조다. 이 구조에서
파생되는 일군의 문제(전체 파일 재기록·전체 파일 읽기·동시성 경쟁·구버전 누적)를 **키별
샤딩**으로 일괄 해소하고, 그 위에서 기존 인메모리 캐시(Brave 결과·HTTP 검증)를 파일 캐시로
전환한다. 또한 기존 캐시를 백업하고 "재실행 시 히트 가능한" 엔트리만 샤딩 포맷으로
마이그레이션한다.

범위는 agent_cache 저장계층 + url_cache 두 인메모리 캐시 + 마이그레이션이다. 캐시 키 산정
규칙(입력+컨텍스트 해시)·TTL 값(`cache_ttls.yaml`)은 변경하지 않는다.

---

## 2. 문제 정의 (현행 코드 실사·실측 — 2026-06-19)

현 구조: `data/cache/agent_outputs/{agent_id}.json` 1개에 그 agent의 모든 엔트리가
`entries` dict로 들어 있다. `store_agent_output`은 전체 파일을 읽어(`_read_cache`) 키 1개를
추가/교체한 뒤 전체를 다시 쓴다(`_write_cache`). `load_agent_output`도 전체 파일을 읽어 dict
조회하고, 적중 시 hit_count 갱신을 위해 다시 전체를 쓴다.

이로 인한 문제와 실측:

| 문제 | 내용 | 실측 |
|---|---|---|
| 조회=전체 읽기 | 키 1건 조회도 파일 전체 read+parse | 11MB Brave ≈ 152ms/조회 |
| 적중도 전체 쓰기 | hit_count 갱신 위해 전체 재기록 | 11MB ≈ 321ms/적중 |
| 저장=전체 쓰기 | 신규 1건 저장에 전체 재직렬화 | 9MB youtube_comments 383ms, 11MB brave 321ms |
| 구버전 누적 | prune 없음 — 만료·버전무효 엔트리도 파일에 남아 매번 재기록 | youtube_comments v2 잔존 등 |
| 동시성 경쟁 | 비원자 read-modify-write — 병렬 워커 동시 store 시 엔트리 유실 | chunking이 메인 스레드 I/O로 우회 중 |
| 인메모리 휘발 | url_cache(Brave 결과·HTTP 검증)는 재시작 시 소멸 → 재실행 미스 | — |

규모(현재): 전체 30개 agent, 엔트리 1,954건. 대용량 파일 = url_discovery_brave 11MB(331)·
youtube_comments 9MB(379)·youtube_search 4MB(74).

---

## 3. 핵심 설계 — 키별 샤딩 (CS-D1)

**결정 CS-D1**: agent당 단일 파일을 **엔트리당 1파일**로 분리한다. 레이아웃:

```
data/cache/agent_outputs/{agent_id}/{cache_key}.json   # 엔트리 1개 = 파일 1개
data/cache/agent_outputs/{agent_id}/_meta.json         # 집계 메타(선택)
```

cache_key가 곧 파일명이므로 조회는 해당 파일을 **이름으로 직행**해 읽고(있으면 히트), 저장은
그 작은 파일만 쓴다. 이로써 §2의 문제가 한꺼번에 해소된다:

- **조회**: 전체 읽기 → 단일 작은 파일 읽기(O(엔트리)). 디렉터리 스캔 불필요(점 조회).
- **저장·적중**: 전체 재기록 → 해당 엔트리 파일만 쓰기(O(엔트리)).
- **구버전 누적**: 죽은 엔트리는 별개 파일이라 신규 저장 시 재직렬화 대상이 아님(정리는 파일 1개 삭제).
- **동시성**: 서로 다른 키 = 서로 다른 파일 → 병렬 워커 충돌 소멸(상세 CS-D3).

---

## 4. 샤딩 범위 — 전체 적용 (CS-D2)

**결정 CS-D2**: 일부가 아니라 **전체 agent 캐시**를 샤딩한다.

근거: 전체 샤딩 시 "다른 키=다른 파일"이 전역 성립해 동시성 경쟁이 구조적으로 사라지므로,
별도의 `agent_cache` 원자 락 리팩터링(이전 논의의 Option B)이 **불필요**해진다. 또한
reaction_analysis chunking의 "메인 스레드 I/O 우회"(CH-D11 §7-2)도 정확성상 무해하게 유지하되
더 이상 필수가 아니게 된다. 선별 샤딩은 비샤딩 agent에 경쟁이 남아 Option B/우회가 계속
필요하므로 채택하지 않는다.

트레이드오프: 파일 수가 엔트리 수만큼 증가(대용량 brave 331·youtube_comments 379·
url_validation 622 등, 마이그레이션 대상 합 ≈ 1,468개 파일). 일괄 작업(TTL sweep·전체 집계)은
디렉터리 스캔이 되나, 점 조회 위주의 평상시 실행에는 영향이 없다(§12 트레이드오프 참조).

---

## 5. 동시성·내구성 (CS-D3)

**결정 CS-D3**: 쓰기는 **원자적 교체(temp 파일 + `os.replace`)**로 하고, 같은 키 동시 쓰기는
**per-file 락**으로 직렬화한다.

- 서로 다른 키(대부분의 병렬 케이스): 다른 파일이라 락 없이 안전.
- 같은 키 동시 쓰기(드묾): per-file 락 + temp+rename으로 last-writer-wins(파일 손상·유실 없음).
- `_lock_for`의 check-then-set 경쟁은 `setdefault`로 보강(엔트리 파일 단위 락 생성 시).
- temp+rename은 크래시 중 부분 쓰기(파일 손상)도 방지 → 현행 단일 파일의 손상 위험까지 개선.

이로써 이전에 검토한 Option B(공유 단일 파일 원자 락)는 본 샤딩으로 대체된다.

---

## 6. hit_count·last_hit_at 처리 (CS-D4)

**결정 CS-D4**: 샤딩 후에는 적중 시 통계 갱신이 **작은 엔트리 파일 1개 쓰기**로 저렴해지므로
(11MB 재기록 문제 소멸), 현행대로 적중 시 갱신을 유지한다. 단 TTL은 여전히 마지막 store
시각(`updated_at`) 기준이며 적중은 `updated_at`을 갱신하지 않는다(현행 동작 보존). 자주 쓰는
키의 TTL 연장이 필요하면 별도 정책으로 다룬다(§11 결정 항목).

대안(인메모리 누적 + 종료 flush)은 샤딩으로 비용 문제가 사라져 불필요하다.

---

## 7. 백업·마이그레이션 (CS-D5)

**결정 CS-D5**: 전환 전 기존 단일 파일 캐시를 백업하고, "히트 가능" 엔트리만 샤딩 포맷으로
이전한다.

- **백업**: `data/cache/agent_outputs/`의 기존 `{agent_id}.json` 전체를
  `data/cache/agent_outputs_backup_2026-06-19/`로 복사 보존(롤백·감사용).
- **마이그레이션 기준 — "히트 가능" = TTL(30일) 이내 ∧ 키-컨텍스트 안정**:
  - 제외 1) 30일 만료: 86건.
  - 제외 2) 키 무효화 확정분: youtube_comments 비-v3 378건(현 코드 v:3) · reaction_analysis
    17건(chunking이 chunk 단위 items_sha로 키 변경) · reaction_insight 5건(상류 tuples·Q2로
    payload 변경).
  - **대상 ≈ 1,468건** (= 1,954 − 86 − 400 근사)을 `{agent_id}/{cache_key}.json`로 분할 저장.
- **마이그레이션 스크립트** `scripts/migrate_cache_sharding.py`: 각 `{agent_id}.json`을 읽어
  기준 통과 엔트리만 샤딩 파일로 쓰고, 통계 요약을 출력. 멱등(재실행 안전).
- 주: 정확한 "실제 히트 수"는 실행 입력에 좌우되므로(같은 키 재요청 여부), 본 기준은 상한측
  보수 기준이다.

---

## 8. 인메모리 → 파일 전환 (CS-D6)

**결정 CS-D6**: 샤딩 저장계층이 선 적용된 뒤, 마지막 단계로 `url_cache.py`의 두 인메모리
캐시를 샤딩 파일 캐시 사용으로 전환한다.

- Brave 결과(E-1, official_source_resolver): 키=쿼리. HTTP 검증(E-2, url_retry): 키=URL.
- 병렬 워커가 서로 다른 키(쿼리·URL)를 쓰므로 CS-D3로 안전.
- HTTP 검증 TTL은 개발 완료 시까지 다른 캐시와 동일하게 30일 유지(추후 dead-link 박제 방지를
  위해 단축 예정 — `cache_ttls.yaml`에서 조정).
- 인메모리 L1 없이 파일(L2) 단독(앞선 결정).

---

## 9. 명명 규칙·매핑 문서 (CS-D7)

**결정 CS-D7**: 파일 캐시 agent_id는 데이터명만이 아니라 **생산 맥락**을 담는 규칙을 쓰고,
`agent_id ↔ 생산/소비 노드` 매핑을 한 곳에 문서화한다.

- Brave 결과는 기존 파일 캐시 `url_discovery_brave`와 혼동되지 않게 `official_source_brave`,
  공유되는 검증은 `http_url_validation` 등 목적+맥락 명명.
- 매핑은 `cache_ttls.yaml` 옆 주석 섹션 또는 별도 `cache_registry.md`에 기록.

---

## 10. 단계별 구현 계획

1. **Phase 1 — 샤딩 저장계층**: `agent_cache.py`의 저장 함수를 키별 파일(읽기/쓰기/TTL/
   원자 교체/per-file 락)로 교체. 외부 인터페이스(`load_agent_output`/`store_agent_output`)는
   유지 → 호출부 무변경. → 검증: 단위 테스트(왕복·동시성 무유실·원자성·TTL).
2. **Phase 2 — 백업 + 마이그레이션**: 백업 복사 후 `migrate_cache_sharding.py`로 히트 가능분
   이전. → 검증: 마이그레이션 전후 엔트리 수·키 일치, 샘플 조회 히트.
3. **Phase 3 — 인메모리 전환**: `url_cache.py` + 호출부를 샤딩 캐시로 전환. → 검증: Brave·
   검증 캐시 round-trip, 재시작 후 히트.

각 단계 후 기존 노드 테스트(test_reaction_*, test_official_content_* 등) 회귀.

---

## 11. 결정 항목 (✅ 전부 채택 — 2026-06-19)

| ID | 항목 | 채택 |
|---|---|---|
| Q1 | 샤딩 범위 | ✅ 전체 agent(=Option B 불필요) |
| Q2 | 마이그레이션 기준 | ✅ TTL 이내 ∧ 키 안정(무효화분 제외, ≈1,468) |
| Q3 | 백업 경로 | ✅ `agent_outputs_backup_2026-06-19/` 복사 보존 |
| Q4 | 적중 시 TTL 연장 | ✅ 미적용(현행 store 기준) |
| Q5 | `_meta`/total_entries | ✅ 엔트리당 파일에 `_meta` 미보관(드롭) — 집계는 디렉터리 파일 수 |
| Q6 | chunking 메인 스레드 I/O 우회 | ✅ 유지(무해) |

---

## 12. 위험·트레이드오프

- **파일 폭증**: 마이그레이션 대상 ≈ 1,468개 + 이후 증가. inode·디렉터리 스캔(일괄 작업만)
  부담 → 대용량 디렉터리(brave·youtube_comments·url_validation)에서 TTL sweep 비용 점검 필요.
- **일괄 작업 비용 전가**: 점 조회는 빨라지나(직행), "전체 로드/정리"는 디렉터리 스캔으로
  느려짐. 평상시 실행엔 점 조회만 쓰여 영향 없음.
- **GIL 의존**: per-file 락의 원자성은 CPython GIL 기준. free-threaded(3.13+) 시 명시 락 필요.
- **다중 프로세스**: 샤딩+원자 교체로 같은 키 충돌은 last-writer-wins로 무손상이나, 같은 키
  동시 store가 잦은 환경이면 파일 락(flock)까지 고려(현재 단일 프로세스라 범위 밖).
- **마이그레이션 정확도**: "히트 가능"은 상한 기준 — 일부는 실제 재요청 안 돼 미사용으로 남을
  수 있음(무해, 디스크만 점유).

---

## 13. 테스트·검증 계획 (목표 주도)

1. **단위(저장계층)**: store→load 왕복, 동시 store(다른 키) 무유실, 같은 키 동시 store 원자성
   (손상 0), TTL 만료 시 미스, `setdefault` 락 단일성.
2. **단위(마이그레이션)**: 기준 통과 엔트리만 이전, 키·output 동일, 멱등(2회 실행 동일 결과).
3. **통합(호환)**: 샤딩 후 임의 키 조회 히트, 기존 노드 테스트 회귀 그린.
4. **전환**: Brave·HTTP 검증 키 round-trip, (모의) 재시작 후 파일 히트.
5. **성능**: 11MB급 agent의 조회·저장·적중 비용이 단일 파일 대비 감소함을 측정 비교.
