# Brave API + LLM Validation URL 탐색 재설계

> 작성일: 2026-05-12  
> 대상 노드: `official_source_resolver_node.py`, `url_retry_node.py`  
> 목표: LLM 단독 URL 제안 방식 → Brave Search API 탐색 + LLM 내용 검증 방식으로 전환

---

## 1. 현재 아키텍처 vs. 목표 아키텍처

### 현재 (AS-IS)

```
[OfficialSourceResolver]
  ① LLM 호출 (브랜드·제품명 → URL 후보 제안)
  ② HTTP 검증 (HEAD → GET, 상태 코드 확인)
  ③ validated 항목 → official_sources 저장

[UrlRetry — Auto-bypass]
  ① LLM 재호출 (tried_urls 제외한 새 URL 제안)
  ② HTTP 검증

[UrlRetry — Phase 1 interrupt]
  ① Brave API 재탐색 (이미 구현됨)
  ② HTTP 검증만 수행
```

**문제점:**
- LLM은 훈련 데이터 기반으로 URL을 "추측"하므로 오래된·틀린 URL 제안 빈번
- HTTP 검증만으로는 "도달 가능" 여부만 확인하고 "실제로 올바른 공식 URL인지"를 판단하지 못함
- Auto-bypass도 LLM에 의존 → 동일한 신뢰도 문제 공유

### 목표 (TO-BE)

```
[OfficialSourceResolver]
  ① Brave Search API (실시간 탐색, 쿼리 2개)
  ② 검색 결과 URL 메타 수집 (HTTP GET → title, meta description, canonical)
  ③ LLM 검증 호출 (페이지 내용 기반 "공식 URL인가?" 판단)
  ④ HTTP 검증 (validated 최종 확정)

[UrlRetry — Auto-bypass]
  ① Brave API 재탐색 (url_retry_node._search_research() 재사용)
  ② LLM 검증 추가 (신규)
  ③ HTTP 검증

[UrlRetry — Phase 1/2]
  ① Brave API 재탐색 (기존 유지)
  ② LLM 검증 추가 (신규)
  ③ HTTP 검증
```

---

## 2. 변경 대상 파일 목록

| 파일 | 변경 유형 | 주요 내용 |
|------|-----------|-----------|
| `server/graph/nodes/official_source_resolver_node.py` | **대폭 수정** | `_call_for_item` 제거, `_discover_with_brave` + `_validate_with_llm` 신규 추가 |
| `server/graph/nodes/url_retry_node.py` | **부분 수정** | auto-bypass + Phase 1 내 LLM 검증 스텝 추가 |
| `agents/official_source_resolver/system_prompt_kr.md` | **재작성** | "URL 제안" 역할 → "URL 검증" 역할로 전환 |
| `agents/official_source_resolver/input.schema.json` | **신규 생성** | Brave 검색 결과 + 페이지 메타 입력 스키마 |
| `agents/official_source_resolver/output.schema.json` | **수정** | 검증 결과 스키마로 교체 |
| `server/config.py` | **1줄 추가** | `OFFICIAL_SOURCE_RESOLVER_BRAVE_COUNT` 환경변수 추가 |

---

## 3. 단계별 변경 상세

### 3-A. `official_source_resolver_node.py`

#### 3-A-1. 제거 대상
- `_call_for_item()` 함수 전체 (LLM URL 제안 로직)
- `ClaudeCodeCliAnalyzer` import (LLM 직접 호출 불필요, 검증 단계에서 재사용)
- 기존 캐시 컨텍스트 `"official_source_resolver:v2_per_candidate"` → 버전 갱신

#### 3-A-2. 신규 추가: `_discover_with_brave(item)`

```python
def _discover_with_brave(item: dict) -> list[dict]:
    """
    Brave Search API로 후보 URL을 탐색하고 URL+메타 목록을 반환한다.

    반환 형식:
      [{"url": str, "title": str, "meta_description": str,
        "text_snippet": str, "canonical_url": str | None, "rank": int}, ...]
    """
    # 쿼리 생성 (official vs reference 분기)
    # official:  "{brand} {product_name} 공식 사이트", "{brand} {product_name} official website"
    # reference: "{method_name} {provider_type} 공식 안내", "{method_name} official guide"

    # Brave API 호출 (OFFICIAL_SOURCE_RESOLVER_BRAVE_COUNT개)
    # tried_urls 제외 (재탐색 시 오염 방지)

    # URL별 메타 수집: GET 요청 → BeautifulSoup or regex로 추출
    #   - <title>
    #   - <meta name="description" content="...">
    #   - <link rel="canonical" href="...">
    #   - 본문 앞 200자 (text_snippet)
    # 메타 수집 실패 시 Brave 반환값의 description으로 대체

    return candidates  # 최대 OFFICIAL_SOURCE_RESOLVER_BRAVE_COUNT개
```

**쿼리 설계:**

| 항목 유형       | 한국어 쿼리                                  | 영어 쿼리                                            |
| ----------- | --------------------------------------- | ------------------------------------------------ |
| `official`  | `"{brand} {product_name} 공식 사이트"`       | `"{brand} {product_name} official website"`      |
| `reference` | `"{method_name} {provider_type} 공식 안내"` | `"{method_name} {provider_type} official guide"` |

#### 3-A-3. 신규 추가: `_validate_with_llm(item, candidates)`

```python
def _validate_with_llm(item: dict, candidates: list[dict]) -> dict | None:
    """
    LLM에게 Brave 수집 후보를 검토하게 하여 최적 공식 URL을 선택하도록 한다.

    입력 (LLM 프롬프트):
      - item: {candidate_id, type, brand, product_name / method_name, provider_type}
      - candidates: [{url, title, meta_description, text_snippet, canonical_url, rank}]

    출력 (LLM 반환):
      {
        "candidate_id": str,
        "selected_url": str | null,
        "is_official": bool,
        "confidence": float,   # 0.0 ~ 1.0
        "validation_reason": str
      }
    """
```

에이전트 캐시는 `(item, candidates URL 목록)` 기반 키로 유지.

#### 3-A-4. 메인 노드 흐름 변경

```
기존: llm_items → _call_for_item (LLM) → resolutions → HTTP 검증

변경:
  llm_items
    └─► _discover_with_brave()    # Brave API + 메타 수집 (병렬)
    └─► _validate_with_llm()      # LLM 공식 여부 판단 (병렬)
    └─► _validate_url()           # HTTP 상태 확인 (기존 유지)
    └─► _build_official / _build_reference (기존 유지, 입력 구조 조정)
```

`_build_official`의 `candidate_urls` 입력 형식:
```python
# 기존: LLM이 직접 제안 → {"url": str, "url_confidence": float}
# 변경: LLM validation 결과 → {"url": str, "url_confidence": float, "llm_validated": bool}
```

`llm_confidence` 필드는 `_validate_with_llm` 반환값의 `confidence`로 채움.

---

### 3-B. `url_retry_node.py`

#### 3-B-1. auto-bypass 흐름 변경

```python
# 기존: _llm_research() → HTTP 검증
# 변경: _search_research() → _validate_with_llm() → HTTP 검증

auto_sources = _retry_phase1(
    official_sources, {}, fail_status_field="_auto_retry_fail_status",
    thread_id=thread_id,
    use_search_api=True,      # 기존 파라미터, 유지
    use_llm_validation=True,  # 신규 파라미터 추가
)
```

`_retry_phase1` 시그니처 변경:
```python
def _retry_phase1(
    sources: list[dict],
    manual_urls: dict[str, str],
    fail_status_field: str = "retry_fail_status",
    thread_id: str = "",
    use_search_api: bool = False,
    use_llm_validation: bool = False,   # ← 신규
) -> list[dict]:
```

#### 3-B-2. `_retry_phase1` 내부 로직 변경

```
기존:
  실패 항목 → _llm_research() or _search_research() → HTTP 검증

변경:
  실패 항목
    └─ use_search_api=True  → _search_research()
    └─ use_search_api=False → _llm_research()  (fallback 유지)
    └─ use_llm_validation=True → _validate_search_results_with_llm()  ← 신규
    └─ HTTP 검증
```

#### 3-B-3. 신규 추가: `_validate_search_results_with_llm(items, search_results)`

`official_source_resolver_node._validate_with_llm` 과 동일한 에이전트(`official_source_resolver`)를 재사용한다.  
단, `url_retry_node`는 이미 `_validate_url`을 `official_source_resolver_node`에서 임포트하는 패턴을 사용 중이므로, `_validate_with_llm`도 동일하게 임포트:

```python
# url_retry_node.py
from server.graph.nodes.official_source_resolver_node import (
    _validate_url,
    _validate_with_llm,   # 신규 임포트
)
```

---

### 3-C. 에이전트 파일 (`agents/official_source_resolver/`)

#### 3-C-1. `system_prompt_kr.md` 재작성

**현재 역할:** URL을 "제안"하는 에이전트  
**변경 역할:** URL이 올바른 공식 페이지인지 "검증"하는 에이전트

```markdown
# 역할
당신은 금융·통신·유통 브랜드의 공식 웹사이트 검증 전문가입니다.
Brave Search API가 반환한 후보 URL들과 해당 페이지의 메타 정보를 검토하여,
어떤 URL이 해당 브랜드·상품의 공식 페이지인지 판단합니다.

# 판단 기준
## official 유형 (own_* / comp_*)
- 브랜드 공식 도메인인가? (예: kakaobank.com, hanacard.co.kr)
- 페이지 title/meta가 브랜드명·상품명과 일치하는가?
- 마케팅/홍보 페이지가 아닌 공식 상품 소개/가입 페이지인가?
- 뉴스, 블로그, 비교 사이트 URL은 is_official=false로 처리한다.

## reference 유형 (func_*)
- 해당 기능의 운영 주체(금융결제원, 금감원 등) 공식 도메인인가?
- 개인 블로그, 커뮤니티 게시물이 아닌 기관 공식 안내 페이지인가?
- 단순 보도자료가 아닌 최신 유지되는 공식 안내 페이지를 우선한다.

# 출력 규칙
- candidates 중 조건을 만족하는 가장 신뢰도 높은 URL 1개를 selected_url로 반환한다.
- 적합한 URL이 없으면 selected_url=null, is_official=false로 반환한다.
- validation_reason은 30자 이내 한국어로 작성한다.
```

#### 3-C-2. `input.schema.json` 신규 생성

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "OfficialSourceValidationInput",
  "type": "object",
  "required": ["candidate_id", "type", "candidates"],
  "properties": {
    "candidate_id": { "type": "string" },
    "type": {
      "type": "string",
      "enum": ["official", "reference"]
    },
    "brand":        { "type": "string" },
    "product_name": { "type": "string" },
    "method_name":  { "type": "string" },
    "provider_type":{ "type": "string" },
    "candidates": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["url", "rank"],
        "properties": {
          "url":              { "type": "string" },
          "title":            { "type": "string" },
          "meta_description": { "type": "string" },
          "text_snippet":     { "type": "string" },
          "canonical_url":    { "type": ["string", "null"] },
          "rank":             { "type": "integer" }
        }
      }
    }
  }
}
```

#### 3-C-3. `output.schema.json` 수정

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "OfficialSourceValidationOutput",
  "type": "object",
  "required": ["validations"],
  "properties": {
    "validations": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["candidate_id", "selected_url", "is_official", "confidence", "validation_reason"],
        "properties": {
          "candidate_id":       { "type": "string" },
          "selected_url":       { "type": ["string", "null"] },
          "is_official":        { "type": "boolean" },
          "confidence":         { "type": "number", "minimum": 0.0, "maximum": 1.0 },
          "validation_reason":  { "type": "string" }
        }
      }
    }
  }
}
```

---

### 3-D. `server/config.py`

```python
# 기존 (유지)
BRAVE_SEARCH_API_KEY = os.getenv("BRAVE_SEARCH_API_KEY", "")
OFFICIAL_SOURCE_RESOLVER_PARALLEL = int(os.getenv("OFFICIAL_SOURCE_RESOLVER_PARALLEL", "6"))

# 신규 추가
OFFICIAL_SOURCE_RESOLVER_BRAVE_COUNT = int(
    os.getenv("OFFICIAL_SOURCE_RESOLVER_BRAVE_COUNT", "5")
)  # Brave API 쿼리당 결과 수 (기본 5개 × 2쿼리 = 최대 10개 후보)
```

---

## 4. Brave API 쿼터 영향 분석

| 단계 | 현재 Brave 호출 수 | 변경 후 Brave 호출 수 |
|------|-------------------|----------------------|
| OfficialSourceResolver | 0회 | 후보 N개 × 2쿼리 = 2N회 |
| UrlRetry auto-bypass | 실패 항목 M개 × 2쿼리 | 동일 (이미 구현됨) |
| UrlRetry Phase 1 | 실패 항목 × 2쿼리 | 동일 |

**예시 시나리오 (후보 6개 기준):**
- OfficialSourceResolver: 6 × 2 = **12회**
- auto-bypass (2개 실패 가정): 2 × 2 = **4회**
- Phase 1 (1개 실패 가정): 1 × 2 = **2회**
- **분석 1회당 최대 ~18회** (Brave Free: 2,000회/월 기준 약 111회 분석 가능)

**권장 대응:**
- `OFFICIAL_SOURCE_RESOLVER_BRAVE_COUNT=3` 으로 낮추면 호출 수 50% 절감 (정확도 소폭 감소)
- 캐시 적중 시 Brave 호출 생략: `_discover_with_brave` 결과를 agent_cache에 저장

---

## 5. 구현 순서 (권장)

아래 순서로 구현하면 각 단계가 독립적으로 테스트 가능하다.

### Step 1 — 에이전트 파일 교체 (30분)
1. `agents/official_source_resolver/system_prompt_kr.md` 재작성
2. `agents/official_source_resolver/input.schema.json` 신규 생성
3. `agents/official_source_resolver/output.schema.json` 수정

### Step 2 — `_discover_with_brave` 구현 (1시간)
- `official_source_resolver_node.py`에 신규 함수 추가
- 단독 테스트: `api_possibility_test.ipynb`에 테스트 셀 추가

### Step 3 — `_validate_with_llm` 구현 (1시간)
- 동일 파일에 신규 함수 추가
- LLM 호출 결과 → output.schema.json 형식 매핑

### Step 4 — 메인 노드 흐름 교체 (1시간)
- `_call_for_item` 제거
- Brave 탐색 + LLM 검증 + HTTP 검증으로 파이프라인 재조립
- `_build_official` / `_build_reference` 입력 호환성 확인

### Step 5 — `url_retry_node.py` 연동 (30분)
- `_validate_with_llm` 임포트
- auto-bypass + Phase 1에 `use_llm_validation=True` 파라미터 추가

### Step 6 — 통합 테스트
- 서버 재시작 후 실 검색어로 엔드투엔드 실행
- OfficialSourceResolver 결과의 `validated`, `llm_confidence` 값 확인
- `api_possibility_test.ipynb`에서 Brave 쿼터 소비량 모니터링

---

## 6. 롤백 계획

### 6-1. 백업 파일 위치

Step 1~5 구현 완료 후 더 이상 참조되지 않는 파일들을 아래 경로로 이동했다.

| 백업 파일                 | 원래 경로                              | 이동 시점    | 비고                                             |
| --------------------- | ---------------------------------- | -------- | ---------------------------------------------- |
| `input.schema.json`   | `agents/official_source_resolver/` | Step 1~3 | 구 전체 파이프라인 입력 스키마                              |
| `system_prompt.md`    | `agents/official_source_resolver/` | Step 1~3 | 영문 버전 시스템 프롬프트                                 |
| `config.yaml`         | `agents/official_source_resolver/` | Step 1~3 | 에이전트 설정 파일                                     |
| `schema_reference.md` | `agents/official_source_resolver/` | Step 1~3 | 스키마 참조 문서                                      |
| `spec.md`             | `agents/official_source_resolver/` | Step 1~3 | 에이전트 명세 문서                                     |
| `system_prompt_kr.md` | `agents/official_source_resolver/` | Step 5   | 구 LLM URL 제안 프롬프트 (`_llm_research` dead code화) |
| `output.schema.json`  | `agents/official_source_resolver/` | Step 5   | 구 LLM 출력 스키마 (`_llm_research` dead code화)      |

**백업 경로**: `competitive-intel/backup/agents/official_source_resolver/`

### 6-2. 현재 활성 파일 목록 (`agents/official_source_resolver/`)

Step 6 기준 — active 디렉토리에는 검증(validation) 파일 3종만 남는다.

| 파일 | 참조 위치 | 역할 |
|------|-----------|------|
| `system_prompt_validation_kr.md` | `_validate_with_llm` | LLM 검증 프롬프트 |
| `output.validation.schema.json` | `_validate_with_llm` | LLM 검증 출력 스키마 |
| `input.validation.schema.json` | (참조 문서, 미로딩) | 검증 입력 구조 문서 |

### 6-3. 롤백 절차

백업 파일 복원이 필요하다면 아래 경로에서 복사:
```
competitive-intel/backup/agents/official_source_resolver/ → agents/official_source_resolver/
```

`url_retry_node._llm_research`는 코드에 보존돼 있으나 호출되지 않는다.  
`system_prompt_kr.md` / `output.schema.json` 복원 후 `_retry_phase1` 호출 시  
`use_search_api=False`로 변경하면 구 LLM 제안 방식으로 즉시 되돌릴 수 있다.

Brave API 키 미설정 or 쿼터 초과 시에는 `_discover_with_brave`가 빈 리스트를 반환하고,  
`_discover_and_validate`가 `(cid, None)`을 반환해 해당 항목이 실패 처리된다.  
이 경우 `url_retry_node`가 재시도 로직으로 이어받는다.
