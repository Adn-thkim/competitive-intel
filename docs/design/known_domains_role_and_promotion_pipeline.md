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
