# `known_domains.json` 역할 정의 및 준자동 학습 파이프라인 결정 기록

> - **상태**: DECIDED — 운영 도구 항목, 구현은 후속 작업으로 보류
> - **작성일**: 2026-05-28
> - **대상 파일**: `agents/official_source_resolver/known_domains.json`
> - **대상 노드**: `server/graph/nodes/official_source_resolver_node.py`
> - **선행 문서**: `docs/design/brave_api_url_discovery_redesign.md` (2026-05-12), `docs/Design_Spec.md`
> - **트리거**: known_domains.json의 생성·갱신 메커니즘이 코드베이스 어디에도 문서화되어 있지 않아 신규 도메인 추가 절차가 암묵지로 남아 있음

---

## 1. 문서 목적과 범위

본 문서는 두 가지를 정리한다.

첫째, `known_domains.json`이 LangGraph 파이프라인 안에서 **무엇을 위해 존재하며**, **어느 단계의 어느 노드에서 어떻게 소비되는지**를 코드 라인 근거와 함께 명시한다.

둘째, "LLM 검증이 `confidence ≥ 0.9`로 확정한 `(brand, domain)` 쌍을 자동으로 후보화하고 PR로 띄우는 준자동 학습(semi-automated learning) 파이프라인" 도입 안건에 대해 본 세션에서 결정된 사항(채택 옵션 / 보류 사항 / 영향 파일)을 운영 기록으로 남긴다.

본 문서가 다루지 않는 범위는 다음과 같다.

- 신규 매핑 추가의 일상적 작업 매뉴얼은 별도 운영 가이드로 분리한다.
- `OfficialSourceStore`(`data/cache/official_sources.json`)의 캐시 정책은 `server/graph/official_source_store.py` 모듈 docstring에서 다루며 본 문서의 주제가 아니다.

---

## 2. `known_domains.json`의 역할

### 2-1. 한 줄 정의

`known_domains.json`은 `official_source_resolver_node`의 **A-2 fast-path 최적화**가 참조하는 **수동 큐레이션(static, hand-curated) 화이트리스트**다. 사전에 신뢰가 확정된 `(브랜드 슬러그 → 공식 도메인 목록)` 매핑을 보유함으로써, 해당 브랜드에 대해서는 Brave 결과 도착 즉시 LLM 검증을 **우회(bypass)**하고 결정론적으로 공식 URL을 확정한다.

### 2-2. 파일 위치와 스키마

위치: `agents/official_source_resolver/known_domains.json`

스키마:

```json
{
  "_meta": {
    "description": "...",
    "version": 1,
    "updated_at": "2026-05-13",
    "match_rules": ["키는 deterministic_normalize(brand) 결과 ...", "..."]
  },
  "entries": {
    "<brand_slug>": ["<domain1>", "<domain2>", ...],
    ...
  }
}
```

`entries` 키는 반드시 `server/utils/slug.py`의 `deterministic_normalize()` 결과(NFC 정규화 + 라틴 소문자화 + 공백·특수문자 제거)와 일치해야 한다. `_load_known_domains()`가 로드 시점에 키를 한 번 더 정규화하기 때문에 불일치 키는 fast-path 매칭에서 영구히 제외된다.

`domains` 리스트의 순서는 우선순위가 높은 도메인을 앞쪽에 배치한다. 첫 항목이 일반적으로 브랜드의 메인 도메인이며, 뒤쪽 항목은 서브 브랜드(예: `tossbank.com`, `tossinvest.com`) 또는 동일 운영 주체의 별도 도메인이다.

### 2-3. 운영상의 정체성

본 파일은 다음 세 가지가 모두 **아니다**.

- `data/cache/agent_outputs/*.json` 같은 **에이전트 출력 캐시**가 아니다. 입력 해시 기반 자동 적재가 발생하지 않는다.
- `data/cache/official_sources.json` 같은 **검증 결과 캐시**가 아니다. candidate_id 단위 TTL 만료가 적용되지 않는다.
- `data/taxonomy/*.json` 같은 **도메인 모델 캐시**가 아니다. 7일 TTL 갱신 사이클을 가지지 않는다.

본 파일은 **개발자가 수동으로 commit하는 정적 시드(static seed)** 이며, 코드베이스 전체에서 쓰기 경로(write path)가 존재하지 않는다. `grep -rn "known_domains"` 결과 호출 지점은 `_load_known_domains()` 하나뿐이며, 모두 **읽기 전용**이다.

---

## 3. 어느 단계의 어느 노드에서 어떻게 활용되는가

### 3-1. 노드 파이프라인 내 위치

`official_source_resolver_node`는 선택된 자사·경쟁사·기능 대안 후보별로 다음 6단계를 직렬·병렬 혼합으로 실행한다 (`server/graph/nodes/official_source_resolver_node.py`).

```
0단계  OfficialSourceStore 영구 캐시 조회 (candidate_id 단위)
       └─ hit + HTTP 재검증 통과 → 후속 단계 전부 skip
1단계  Brave Search API 탐색 (한국어/영어 쿼리 병렬, B-1)
2단계  fast-path 분류 (A-2)         ← known_domains.json이 사용되는 단계
       ├─ 적중: 결정론적으로 selected_url 확정 (LLM 우회)
       └─ 미적중: 4단계 LLM 검증 큐로 이동
3단계  HTTP 검증 future 사전 발사 (B-2, B-3)
4단계  batch LLM 검증 (A-1, fast-path 미적중분만)
5단계  candidate별 결과 조립 (_assemble_source)
6단계  validated 항목을 OfficialSourceStore에 영구 저장
```

핵심은 **1단계 Brave 결과 → 2단계 fast-path → 4단계 LLM**의 순서다. fast-path는 LLM 호출 직전의 마지막 결정론적 필터이며, 적중 시 4단계 LLM 호출과 그에 따른 토큰·지연을 모두 회피한다.

### 3-2. 진입점 함수 호출 경로

```
official_source_resolver_node()           ── line 85
  └─ _try_fast_path(item, candidates)     ── line 239 (호출), line 501 (정의)
       └─ _load_known_domains()           ── line 528 (호출), line 471 (정의)
            └─ AGENTS_DIR / "official_source_resolver" / "known_domains.json"
```

`_load_known_domains()`는 모듈 전역 `_KNOWN_DOMAINS_CACHE`에 결과를 메모이즈하므로 파일은 프로세스 수명 동안 **1회만 디스크에서 읽힌다**. 따라서 JSON을 운영 중 수정해도 uvicorn 재시작 전까지 반영되지 않는다.

### 3-3. fast-path 적중 조건 (전부 충족)

`_try_fast_path()`는 다음 3가지 조건을 모두 만족하는 후보가 있을 때만 결과를 반환한다.

1. **브랜드 슬러그 매칭**: `deterministic_normalize(item.brand)` (official) 또는 `deterministic_normalize(item.provider_type)` (reference)가 `entries` 키에 존재한다.
2. **호스트 매칭**: Brave 후보 URL의 host가 매핑 도메인과 정확히 일치하거나 그 서브도메인이다(`_host_matches_domain`).
3. **컨텍스트 매칭**:
   - `official` 타입: `product_name` 토큰이 URL path 또는 page title에 포함되거나, 경로 깊이가 1 이하인 메인 페이지다.
   - `reference` 타입: 도메인 매칭만 충족하면 운영 주체 공식 도메인으로 인정한다.

### 3-4. fast-path가 산출하는 신뢰도

| 적중 시나리오 | confidence | validation_reason |
|---|---|---|
| official + 도메인 일치 + 상품명 토큰 포함 | **0.95** | 도메인+상품명 일치(fast-path) |
| official + 도메인 일치 + 메인/루트 경로(depth ≤ 1) | 0.85 | 브랜드 도메인 메인(fast-path) |
| reference + 도메인 일치 | 0.85 | 운영 주체 공식 도메인(fast-path) |

이 신뢰도는 5단계 `_assemble_source()`에서 `selected_conf`로 그대로 흘러가며, 6단계의 `OfficialSourceStore` 저장 항목에도 `llm_confidence` 필드로 영속화된다. 단, `fast_path=True` 플래그가 함께 부여되어 LLM이 산출한 신뢰도와 출처가 구분 가능하다.

### 3-5. 데이터 흐름 요약

```
known_domains.json (정적 시드)
        │ read-only, 프로세스당 1회
        ▼
_KNOWN_DOMAINS_CACHE (모듈 전역 dict)
        │
        ▼
_try_fast_path(item, Brave 후보)
        │
        ├─(적중)→ {selected_url, is_official=True, confidence∈{0.85,0.95},
        │          validation_reason} → 5단계 조립, 4단계 LLM 우회
        │
        └─(미적중)→ llm_required_items 큐 → 4단계 batch LLM 검증
```

### 3-6. 매칭 정확도가 의존하는 두 가지 외부 함수

`known_domains.json`의 효과는 다음 두 헬퍼의 동작과 강하게 결합되어 있다.

- `deterministic_normalize()` — `server/utils/slug.py`의 슬러그 정규화 함수. 키 정규화 규칙이 바뀌면 모든 기존 매핑이 일괄적으로 무효화될 수 있다.
- `_host_matches_domain()` — 호스트 동일성·서브도메인 판정. 향후 IDN(Internationalized Domain Name) 또는 punycode 도메인을 등재할 경우 본 헬퍼의 처리 정책을 함께 검토해야 한다.

신규 매핑을 추가할 때는 `python3 -c "from server.utils.slug import deterministic_normalize; print(deterministic_normalize('내브랜드'))"`로 키의 정규화 형태를 확인한 뒤 등록한다.

---

## 4. 준자동 학습 파이프라인 — 본 세션 결정 사항

### 4-1. 동기

LLM 검증을 거쳐 `validated=True AND llm_confidence ≥ 0.9`로 확정된 `(brand, primary_url)` 쌍이 `OfficialSourceStore`에 누적되고 있으나, 이 신호가 `known_domains.json`으로 자동 환류(feedback)되는 경로가 없다. 결과적으로 동일 브랜드에 대한 후속 분석에서도 fast-path가 적중하지 못하고 매번 LLM이 재호출되어 토큰·지연이 발생한다.

본 안건은 "LLM이 확정한 매핑을 사람의 검토를 거쳐(human-in-the-loop) `known_domains.json`으로 승격시키는 파이프라인"의 도입 가부를 결정하기 위한 것이다.

### 4-2. 검토된 설계 갈래

**갈래 A — 런타임 즉시 적재**: `_assemble_source` 종료 시 즉시 `known_domains.json`을 수정.
- 단점: hot-path 동시 쓰기, 1회 환각 즉시 화이트리스트 진입, `_KNOWN_DOMAINS_CACHE` 무효화 부담, 사람 검수 게이팅 불가.

**갈래 B — 증거 로그 누적 후 배치 승격**: append-only evidence log(`data/cache/known_domain_evidence.jsonl`)에 매 검증 결과를 기록하고, 별도 스크립트가 임계치(`MIN_EVIDENCE` 회 이상 관측 + 최근성 충족) 도달 시 신규 매핑만 묶어 `known_domains.json` 갱신 + Git 커밋 + PR 생성.
- 장점: hot-path 영향 최소(append 1줄), 일회성 환각 차단, 사람이 PR에서 최종 승인, 멱등성 보장.

### 4-3. Git/PR 자동화 가능성 실측 결과

본 세션에서 sandbox 셸을 통해 직접 검증한 결과는 다음과 같다.

| 항목 | 가능 여부 | 비고 |
|---|---|---|
| git CLI(2.34.1) | 가능 | `/usr/bin/git` |
| 로컬 commit (add/commit/branch) | 가능 | user.name/user.email 모두 설정됨 |
| `git push origin <branch>` | **불가** | credential helper·PAT·SSH 키 전부 부재 |
| `gh pr create` | **불가** | gh CLI 미설치 |
| GitHub REST API 우회 | **불가** | sandbox 프록시가 `api.github.com:443`을 403으로 차단 |

결론: Claude의 sandbox는 **로컬 commit까지만 완결**할 수 있으며, push·PR 생성은 **사용자 로컬 터미널 또는 GitHub Actions에 위임**해야 한다.

### 4-4. 채택 결정 — 구현 보류, 현행 수동 PR 큐레이션 유지

본 세션에서는 다음을 결정한다.

- **채택**: 갈래 B(증거 로그 + 배치 승격)의 **설계**를 운영 로드맵에 보존한다.
- **보류**: 갈래 B의 **구현**(scripts/promote_known_domains.py, evidence emit, GitHub Actions 워크플로)은 본 세션에서 착수하지 않는다.
- **현행 운영 방식**: 신규 매핑은 당분간 개발자가 직접 `known_domains.json`에 PR로 추가한다.
- **재검토 조건**: (가) fast-path 적중률이 운영상 의미 있게 떨어졌다고 판단되는 시점, 또는 (나) Git/PR 자동화 경로(GitHub Actions 또는 PAT 주입)가 확보된 시점에 본 문서를 갱신하고 구현 작업을 착수한다.

### 4-5. 구현이 재개될 경우 영향이 예상되는 파일 (참조용 보존)

본 세션에서는 변경하지 않았다. 향후 구현 시 사전 영향도 평가에 사용하기 위해 목록만 남긴다.

| 구분 | 경로 | 변경 성격 |
|---|---|---|
| 신설 | `scripts/promote_known_domains.py` | 증거 집계 → diff → 로컬 commit (push·PR은 CI에 위임) |
| 신설 | `data/cache/known_domain_evidence.jsonl` | 런타임 자동 생성 (append-only SSoT) |
| 신설 | `.github/workflows/promote-known-domains.yml` | weekly cron + `peter-evans/create-pull-request` 액션 |
| 신설 | `tests/test_promote_known_domains.py` | 멱등성·필터·슬러그 고정점 회귀 테스트 |
| 수정 | `server/graph/nodes/official_source_resolver_node.py` | `_emit_evidence()` 헬퍼 ~15줄 추가 |
| 수정 | `server/config.py` | `KNOWN_DOMAIN_EVIDENCE_LOG_PATH`, `KD_MIN_EVIDENCE`, `KD_MAX_AGE_DAYS` 상수 추가 |
| 수정 | `requirements.txt` | `tldextract` 미설치 시 추가 |
| 수정 | `.gitignore` | `data/cache/known_domain_evidence.jsonl` 추가 |
| 갱신 | `agents/official_source_resolver/known_domains.json` | PR 단위로 entries 추가, `_meta.version`/`updated_at` 갱신 |

### 4-6. 본 세션 작업 산출물

- 신규 문서 1건: `docs/design/known_domains_role_and_promotion_pipeline.md` (본 문서)
- 코드 변경: 없음
- 캐시·시드 파일 변경: 없음

### 4-7. 기대 효과 (정량 추정)

본 절은 4-4 절의 보류 결정이 누적시키고 있는 비용을 정량화하고, 재검토 조건 도달 시의 의사결정 근거 자료로 활용한다. 본 절의 모든 수치는 추정값이며 실제 운영 데이터로 보정해야 한다.

#### 4-7-1. 기준선 (Baseline)

- 모델: `claude-sonnet-4-6` (`server/config.py`의 `CLI_MODEL`)
- LLM 배치 크기: 5 candidates / call (`OFFICIAL_SOURCE_RESOLVER_LLM_BATCH_SIZE`)
- Brave 후보 수: 5 URL / query (`OFFICIAL_SOURCE_RESOLVER_BRAVE_COUNT`)
- known_domains 기등재(2026-05-28 기준): 80 entries / 64 unique domains
- 일반 분석 1회 candidate 수: 1 own + 3~5 competitors = 4~6
- Claude Sonnet 4.x 가격(2025년 기준): 입력 $3 / 1M 토큰, 출력 $15 / 1M 토큰 — 참조 `https://www.anthropic.com/pricing`

#### 4-7-2. 배치 1회 LLM 호출 추정 비용·지연

| 항목 | 추정 토큰 | 비용 |
|---|---|---|
| 입력 (system prompt + 5 candidate × 5 URL × 페이지 메타) | ~10,000 | $0.030 |
| 출력 (검증 JSON × 5) | ~1,500 | $0.0225 |
| 배치 1회 합 | | **~$0.05** |
| 지연 (p50) | | 3–6초 |

#### 4-7-3. 시나리오별 절감 효과 추정

| 시나리오 | candidate 수 | LLM 호출 수 | 적용 전 | 적용 후 (전부 known) | 절감 |
|---|---|---|---|---|---|
| 소형 (1 own + 3 comp) | 4 | 1 batch | $0.05 · 4–6초 | $0 · 50ms | -100% |
| 중형 (1 own + 5 comp) | 6 | 2 batch | $0.10 · 8–12초 | $0 · 80ms | -100% |
| 대형 (1 own + 9 comp) | 10 | 2 batch | $0.10 · 8–12초 | $0 · 120ms | -100% |
| 부분 (50% known) | 6 | 1 batch | $0.05 · 5–7초 | $0.025 · 2.5–3.5초 | -50% |

#### 4-7-4. 연간 누적 효과 (가정 기반 시뮬레이션)

가정: 월 100회 분석, 평균 5 candidates, 본 개발 도입 후 일정 시점에 90% known 적중률 도달.

| 항목 | 적용 전 | 적용 후 |
|---|---|---|
| 연간 LLM 배치 호출 수 | ~2,400 | ~240 |
| 연간 LLM 비용 | ~$120 | ~$12 |
| 연간 절감 비용 | — | **~$108** |
| 연간 대기 시간 절감 (분석당 5초 × 12,000회) | — | **~16.7시간** |

규모는 단독 개발 환경 기준으로는 모더릿하나, 본 효과는 **복리적**이다. known_domains이 성장할수록 fast-path 적중률이 단조 증가하므로 절감이 누적된다.

#### 4-7-5. 정성적 부가 효과

비용·지연 외의 효과는 다음과 같다.

1. **결정론성 회복** — fast-path는 동일 입력에 동일 출력. LLM은 confidence 0.85~0.92 구간에서 동일 prompt에 다른 URL을 산출할 수 있어 분석 재현성을 해친다. 본 개발은 동일 brand 분석 결과의 변동을 0에 수렴시킨다.
2. **외부 API 의존도 감소** — Claude API 율 제한·다운타임 영향이 known 브랜드 분석에는 미치지 않는다.
3. **운영 telemetry 확보** — evidence.jsonl이 부수적으로 "어떤 brand가 어떤 빈도로 분석되는지"의 사용 패턴 데이터를 제공한다.
4. **사용자 체감 응답성** — interactive UI 흐름에서 5–10초 단축은 "느림 → 빠름" 인지 임계를 넘는다.
5. **회귀 안전망** — promote 스크립트의 diff 출력이 PR 리뷰 단위로 사람 검수를 강제해 LLM 환각의 화이트리스트 침투를 차단한다.

#### 4-7-6. 추정의 한계와 보정 방법

본 절 수치는 다음 가정에 의존한다.

- 월 분석 횟수 100회 — 실측되지 않음.
- known_domains 적중률 90% — 운영 데이터로만 측정 가능.
- 배치당 입출력 토큰 10,000 / 1,500 — system prompt와 페이지 메타 길이에 따라 변동.

4-8 절 구현 후 첫 30일 운영 결과로 다음을 측정해 본 절을 보정한다.

- `wc -l data/cache/known_domain_evidence.jsonl` × 평균 candidate 수
- `data/cache/agent_outputs/official_source_resolver.json` 캐시 항목 수
- promote 스크립트 dry-run 결과의 신규 후보 수

### 4-8. 갈래 B 부분 채택 — 향후 구현 절차

본 절은 4-4 절의 **재검토 조건이 도달**해 갈래 B를 부분 채택하기로 결정될 경우의 개발 절차를 사전에 박제한다. 본 절을 작성한 시점에는 구현이 착수되지 않았으며, 본 절차는 **향후 구현 세션의 단일 참조점** 역할을 한다.

#### 4-8-1. 부분 채택의 범위

**포함**:

- evidence emit (`_emit_evidence` 헬퍼 추가)
- promote 스크립트 (`--dry-run` / `--apply` 두 모드)
- 회귀 테스트

**제외**:

- Git commit·push 자동화 (4-3 절 sandbox 실측 결과 — 본 환경에서 영구 불가)
- `peter-evans/create-pull-request` GitHub Actions 워크플로
- `scripts/promote_known_domains.py`의 `--open-pr` 플래그 또는 `git` 호출
- 신규 매핑의 PR 생성·머지 — 사용자가 별도 호스트 터미널에서 수동으로 처리

#### 4-8-2. 사전 결정이 필요한 3가지 갈래

본 절차 착수 전 다음 세 결정이 확정되어야 Phase 2·3에서 막히지 않는다.

**갈래 ① — evidence emit 시점의 thread_id 가용성**:
- Option a) `_assemble_source` 시그니처에 thread_id 추가 (외과적 변경 ~3줄).
- Option b) `_emit_evidence`를 메인 함수 안에서 직접 호출 (시그니처 미변경).
- **권장 b**: 더 외과적이다.

**갈래 ② — emit 실행 위치 (메인 함수 vs hot loop)**:
- Option a) 후보별 호출 (총 N회 disk write).
- Option b) 루프 종료 후 batch write 1회.
- **권장 a**: 분석당 candidate 수가 보통 10개 미만이므로 단순화해도 무해.

**갈래 ③ — promote 스크립트의 JSON 직렬화 순서**:
- Option a) `sort_keys=False`, 신규 매핑을 dict 끝에 append (Python 3.7+ dict 순서 보존).
- Option b) 처음부터 정렬된 순서로 통일 (1회 큰 diff 후 안정).
- **권장 a**: 기존 entries 순서를 보존해 PR 리뷰 부담을 최소화한다.

#### 4-8-3. Phase별 개발 절차

##### Phase 0 — 의사결정 명문화 (코드 0줄, 문서 1건 수정)

- **목표**: 본 절차 착수 사실을 본 문서 4-4 절에 부분 번복으로 갱신.
- **작업**:
  - 4-4 절에 새 하위 항목 추가: "**[일자] 갱신**: 본 결정 중 evidence emit과 promote 스크립트 로컬 적용까지는 착수한다. Git/PR 자동화는 여전히 보류."
  - 6장 변경 이력 표에 한 줄 추가.
- **영향 파일**: `docs/design/known_domains_role_and_promotion_pipeline.md`.
- **성공 기준**: `grep "구현 보류"` 결과가 4-4 절의 부분 채택 영역과 잔여 보류 영역을 명확히 구분.
- **Commit 메시지 예**: `known_domains 승격 파이프라인 부분 채택 결정 갱신`.

##### Phase 1 — 인프라 정비 (코드 ~10줄)

- **목표**: 후속 단계가 의존할 config·gitignore·의존성을 먼저 정착.
- **작업**:
  - `server/config.py` 추가: `KNOWN_DOMAIN_EVIDENCE_LOG_PATH = CACHE_DIR / "known_domain_evidence.jsonl"`, `KD_MIN_EVIDENCE = int(os.getenv("KD_MIN_EVIDENCE", "2"))`, `KD_MAX_AGE_DAYS = int(os.getenv("KD_MAX_AGE_DAYS", "30"))`.
  - `.gitignore`: `data/cache/known_domain_evidence.jsonl` 추가.
  - `requirements.txt`: `tldextract>=5.1.0` (미설치 시).
- **영향 파일**: `server/config.py`, `.gitignore`, `requirements.txt`.
- **성공 기준**: `python3 -c "from server.config import KNOWN_DOMAIN_EVIDENCE_LOG_PATH, KD_MIN_EVIDENCE, KD_MAX_AGE_DAYS; print('ok')"` → `ok` 출력.
- **Commit 메시지 예**: `known_domains 승격 파이프라인 인프라 상수·gitignore·의존성 정비`.

##### Phase 2 — Producer: evidence emit (코드 ~30줄)

- **목표**: validated 결과를 append-only로 1줄씩 적재하는 fire-and-forget 헬퍼.
- **작업**:
  - `_emit_evidence(item, src, thread_id)` 헬퍼를 `official_source_resolver_node.py`에 추가.
  - `_etld1_of(url)` 헬퍼 추가 (tldextract 기반, urlparse fallback).
  - 호출 위치: 메인 함수의 `if src.get("validated"):` 블록 안, `store.set(cid, src)` 직후 1줄.
- **본문 의사 코드**:

  ```python
  def _emit_evidence(item: dict, src: dict, thread_id: str) -> None:
      """append-only로 1줄 기록. 실패는 swallow하여 파이프라인 영향 차단."""
      try:
          if not src.get("validated") or src.get("source_type") != "official":
              return
          record = {
              "ts": datetime.now(timezone.utc).isoformat(),
              "thread_id": thread_id,
              "brand": item.get("brand", ""),
              "brand_slug": deterministic_normalize(item.get("brand", "")),
              "candidate_id": item.get("candidate_id"),
              "source_type": src.get("source_type"),
              "primary_url": src.get("primary_url"),
              "etld1": _etld1_of(src.get("primary_url", "")),
              "llm_confidence": src.get("llm_confidence"),
              "fast_path": bool(src.get("fast_path", False)),
              "validated": True,
          }
          KNOWN_DOMAIN_EVIDENCE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
          with open(KNOWN_DOMAIN_EVIDENCE_LOG_PATH, "a", encoding="utf-8") as f:
              f.write(json.dumps(record, ensure_ascii=False) + "\n")
      except Exception as exc:
          logger.debug("evidence emit 실패(swallow): %s", exc)
  ```

- **영향 파일**: `server/graph/nodes/official_source_resolver_node.py`.
- **성공 기준**:
  - dummy item·src로 1회 호출 → evidence.jsonl에 1줄 추가.
  - 쓰기 권한 없는 경로 주입 시 예외 없이 종료, debug 로그만 남음.
  - `_assemble_source` 반환 dict 키 셋 미변경.
- **Commit 메시지 예**: `official_source_resolver_node에 evidence emit fire-and-forget 헬퍼 추가`.

##### Phase 3 — Consumer: promote 스크립트 (코드 ~150줄)

- **목표**: evidence log를 읽어 임계치 충족 신규 매핑만 `known_domains.json`에 머지.
- **작업**: 신규 파일 `scripts/promote_known_domains.py`. CLI: `--dry-run` (기본) / `--apply`.
- **내부 단계**:
  1. **읽기**: evidence.jsonl line-by-line 파싱. JSON 파싱 실패 라인은 skip + warning.
  2. **필터**: `validated AND source_type == "official" AND llm_confidence >= 0.9 AND fast_path != True AND etld1 비어있지 않음 AND ts가 KD_MAX_AGE_DAYS 이내`.
  3. **집계**: `(brand_slug, etld1)` 그룹핑. 같은 `thread_id`는 1관측으로 dedupe.
  4. **임계치**: `count(distinct thread_id) >= KD_MIN_EVIDENCE` 그룹만 승격 후보.
  5. **diff**: 기존 `known_domains.json` 로드. `(brand_slug, etld1)`가 이미 entries에 있으면 skip. 신규 매핑만 entries에 머지 (기존 도메인 리스트 끝에 append, 기존 항목 순서 보존).
  6. **출력**: dry-run은 표 형식 출력만, apply는 파일 갱신 + `_meta.version` +1 + `_meta.updated_at` 갱신 + 갱신 후 표 출력.
- **종료 코드**: 신규 0건 → 0, dry-run N건 → 0, apply N건 → 0, 오류 → 1.
- **영향 파일**: `scripts/promote_known_domains.py` (신설).
- **성공 기준**:
  - 1차 `--dry-run`: 표 출력, 파일 미변경 (`git diff` 빈 출력).
  - 2차 `--apply`: known_domains.json 변경, `_meta.version` +1, 정확히 신규 매핑만 추가.
  - 3차 `--apply` 재실행 (동일 evidence): "신규 0건" 메시지, 파일 미변경 (멱등성).
- **Commit 메시지 예**: `known_domains 승격 스크립트 신규 추가 (로컬 적용 모드 단독)`.

##### Phase 4 — 회귀 테스트 (코드 ~120줄)

- **목표**: Phase 2·3 회귀 방지.
- **작업**: 신규 파일 `tests/test_promote_known_domains.py`. fixture로 임시 known_domains.json + evidence.jsonl 생성.
- **케이스 (최소 9건)**:
  1. `test_filter_fast_path_excluded` — fast_path=True 라인 제외.
  2. `test_filter_low_confidence_excluded` — llm_confidence < 0.9 제외.
  3. `test_filter_low_evidence_excluded` — 1회 관측은 임계 미달.
  4. `test_dedupe_within_thread` — 같은 thread_id 3회 → 1관측.
  5. `test_idempotent_apply` — 두 번 apply 시 두 번째는 미변경.
  6. `test_slug_fixed_point` — 신규 키가 `deterministic_normalize`의 fixed point.
  7. `test_existing_entries_order_preserved` — 기존 순서 보존 + 신규만 끝에 append.
  8. `test_emit_failure_swallowed` — 쓰기 불가 경로 주입 시 예외 없이 종료.
  9. `test_dry_run_no_file_change` — dry-run 후 파일 mtime 미변경.
- **영향 파일**: `tests/test_promote_known_domains.py` (신설).
- **성공 기준**: `pytest tests/test_promote_known_domains.py -v` 9개 PASS. 핵심 함수(filter, aggregate, merge) 라인 커버리지 90%↑.
- **Commit 메시지 예**: `promote 스크립트·evidence emit 회귀 테스트 9건 추가`.

##### Phase 5 — 운영 검증 + CHANGELOG (코드 0줄, 검증 명령 + 문서 1건)

- **목표**: 실제 분석 1회 흐름에서 의도대로 동작함을 확인 + CHANGELOG 갱신 + 본 문서 4-7-6 절 실측 보정.
- **작업**:
  1. 분석 1회 실행 (Express + uvicorn 기동, 1 own + 2 competitors 시나리오).
  2. `wc -l data/cache/known_domain_evidence.jsonl` 새 라인 확인.
  3. `tail -1 data/cache/known_domain_evidence.jsonl | jq .` 필수 필드 모두 존재.
  4. `python3 scripts/promote_known_domains.py --dry-run` 정상 출력. 임계 미달 시 "신규 0건".
  5. 다른 brand로 2차 분석 → 임계 충족 시 `--apply` 효과 확인.
  6. `CHANGELOG.md`의 `[Unreleased] / Added`에 Phase 0~4의 4건 commit 항목 추가.
  7. 본 문서 4-7-6 절(추정의 한계)에 실측 보정 결과 박제.
- **영향 파일**: `CHANGELOG.md`, `docs/design/known_domains_role_and_promotion_pipeline.md`.
- **성공 기준**:
  - evidence.jsonl이 분석 1회당 1줄 누적.
  - promote 스크립트 dry-run·apply가 예상 결과와 일치.
  - CHANGELOG에 4건 commit이 `(<hash>)` 형식으로 인용됨.
- **Commit 메시지 예**: `CHANGELOG Unreleased 갱신 + 4-7-6 절 실측 보정`.

#### 4-8-4. 영향 파일·예상 라인 수 종합

| Phase | 파일 | 변경 성격 | 예상 라인 |
|---|---|---|---|
| 0 | `docs/design/known_domains_role_and_promotion_pipeline.md` | 갱신 | +10 |
| 1 | `server/config.py` | 추가 (3 상수) | +5 |
| 1 | `.gitignore` | 추가 | +1 |
| 1 | `requirements.txt` | 조건부 추가 | +1 |
| 2 | `server/graph/nodes/official_source_resolver_node.py` | 헬퍼 2종 + 호출 1줄 | +30 |
| 3 | `scripts/promote_known_domains.py` | 신설 | ~150 |
| 4 | `tests/test_promote_known_domains.py` | 신설 | ~120 |
| 5 | `CHANGELOG.md` + 본 문서 4-7-6 절 | 추가/갱신 | +10 |

**총 6 commit 시리즈** (Phase별 단독 commit). 본 시리즈는 순차 의존하므로 단일 PR(`feat/known-domains-promotion`)로 묶어 머지하는 것을 권장한다.

#### 4-8-5. 본 절차의 신뢰성 한계

본 절의 영향 파일 표와 의사 코드는 **작성 시점(2026-05-30)의 코드베이스 구조**에 기반한다. 구현 재개 시점에 다음 항목이 변경되었을 수 있으므로 사전 영향도 평가가 필요하다.

- `server/config.py`의 분할 리팩토링 (가능성 중)
- `_assemble_source`의 시그니처 변경 (가능성 저)
- `data/cache/` 캐시 정책 갱신 (가능성 중)
- `requirements.txt`에 `tldextract` 사전 도입 여부 (확인 필요)
- `official_source_resolver_node.py`의 line 번호 변동 (가능성 고 — 절대 라인이 아닌 함수명으로 navigate 권장)

영향도 평가 산출물은 Phase 0 commit 본문에 1단락으로 보고한다.

---

## 5. 위험과 후속 과제

첫째, `_KNOWN_DOMAINS_CACHE`는 프로세스 전역 1회 로드이므로, 향후 PR 머지 후 신규 매핑을 반영하려면 uvicorn 재시작이 강제된다. 무중단 반영이 필요한 시점이 도래하면 (가) 파일 mtime 기반 lazy reload 또는 (나) `/admin/reload-known-domains` 비공개 엔드포인트 추가를 검토한다.

둘째, 현재 매칭 정책은 brand·product_name이 **공백·특수문자만 다른 표기**까지만 흡수한다. 약어·동의어(예: "토스뱅크" ↔ "Toss Bank" ↔ "tossbnk")는 entries에 중복 키를 명시적으로 등록해 대응 중이며(현재 파일에서 토스뱅크 계열 3개 키가 동일 도메인을 가리킴), 향후 키 수가 일정 임계를 넘으면 별도의 별칭(alias) 인덱스 도입을 검토한다.

셋째, 본 파일은 **국내 금융·핀테크·플랫폼 브랜드에 강하게 편중**되어 있다. 향후 글로벌 카테고리(예: 글로벌 SaaS, 게이밍, 미디어)로 분석 도메인이 확장될 때는 도메인 정책 차이(예: `.io`, `.dev`, country-code TLD)에 맞춰 `_host_matches_domain` 정책을 재검토해야 한다.

---

## 6. 변경 이력

| 일자 | 변경 | 작성자 |
|---|---|---|
| 2026-05-28 | 최초 작성. `known_domains.json` 역할 정의 + 준자동 학습 파이프라인 구현 보류 결정 기록. | Claude(에이전트 세션) |
| 2026-05-30 | 4-7 절(기대 효과 정량 추정) 및 4-8 절(갈래 B 부분 채택 향후 구현 절차) 신설. 구현 자체는 여전히 보류 — 재검토 시 4-8 절을 단일 참조점으로 사용. | Claude(에이전트 세션) |
